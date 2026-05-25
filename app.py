from flask import Flask, render_template, jsonify, redirect, request, session, url_for
from threading import Lock
from dotenv import load_dotenv
from ipaddress import ip_address, ip_network
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
import json
import os
import sqlite3
import time

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
app.config["DATABASE"] = os.getenv(
    "QUEUE_DB_PATH",
    os.path.join(app.instance_path, "psa_queue.db")
)

if os.getenv("TRUST_PROXY", "true").lower() in {"1", "true", "yes", "on"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

lock = Lock()

QUEUES = ["reg-pri", "reg-reg", "iss-pri", "iss-reg"]
ADMIN_TOKEN = os.getenv("QUEUE_ADMIN_TOKEN", "").strip()
DEFAULT_BRANCH = os.getenv("DEFAULT_BRANCH", "main").strip() or "main"
LAN_NETWORKS = tuple(
    ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

WINDOW_MAPPING = {
    1: {"queue": "reg-pri", "title": "REGISTRATION", "type": "PRIORITY"},
    2: {"queue": "reg-reg", "title": "REGISTRATION", "type": "REGULAR"},
    3: {"queue": "iss-pri", "title": "ISSUANCE", "type": "PRIORITY"},
    4: {"queue": "iss-reg", "title": "ISSUANCE", "type": "REGULAR"},
}


def normalize_branch(branch):
    branch = (branch or DEFAULT_BRANCH).strip().lower()
    allowed = []
    for character in branch:
        if character.isalnum() or character in {"-", "_"}:
            allowed.append(character)

    return "".join(allowed) or DEFAULT_BRANCH


def get_db():
    database_dir = os.path.dirname(app.config["DATABASE"])
    if database_dir:
        os.makedirs(database_dir, exist_ok=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                branch TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'branch_admin',
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                username TEXT,
                branch TEXT,
                ip_address TEXT,
                details TEXT,
                created_at REAL NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS queue_state (
                branch TEXT NOT NULL,
                queue TEXT NOT NULL,
                called TEXT NOT NULL DEFAULT '0',
                serving INTEGER NOT NULL DEFAULT 0,
                max_value INTEGER,
                updated_at REAL NOT NULL,
                PRIMARY KEY (branch, queue)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch TEXT NOT NULL,
                queue TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_branch_time ON audit_logs(branch, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_logs(username, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_announcements_branch_id ON announcements(branch, id)")


def decode_called(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def encode_called(value):
    return str(value)


def ensure_branch_state(db, branch):
    branch = normalize_branch(branch)
    now = time.time()
    for queue in QUEUES:
        db.execute(
            """
            INSERT OR IGNORE INTO queue_state (branch, queue, called, serving, max_value, updated_at)
            VALUES (?, ?, '0', 0, NULL, ?)
            """,
            (branch, queue, now)
        )


def row_to_queue_state(row):
    return {
        "called": decode_called(row["called"]),
        "serving": row["serving"],
        "max": row["max_value"],
    }


def get_branch_state(branch):
    branch = normalize_branch(branch)
    with get_db() as db:
        ensure_branch_state(db, branch)
        rows = db.execute(
            "SELECT queue, called, serving, max_value FROM queue_state WHERE branch = ?",
            (branch,)
        ).fetchall()

    state = {row["queue"]: row_to_queue_state(row) for row in rows}
    for queue in QUEUES:
        state.setdefault(queue, {"called": 0, "serving": 0, "max": None})

    return state


def set_queue_state(db, branch, queue, q):
    db.execute(
        """
        UPDATE queue_state
        SET called = ?, serving = ?, max_value = ?, updated_at = ?
        WHERE branch = ? AND queue = ?
        """,
        (
            encode_called(q["called"]),
            int(q["serving"]),
            q["max"],
            time.time(),
            normalize_branch(branch),
            queue,
        )
    )


def create_user(username, password, branch, role="branch_admin", active=True):
    now = time.time()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO users (username, password_hash, branch, role, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username.strip(),
                generate_password_hash(password),
                normalize_branch(branch),
                role.strip() or "branch_admin",
                1 if active else 0,
                now,
                now,
            )
        )


def update_user_password(username, password):
    with get_db() as db:
        db.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
            (generate_password_hash(password), time.time(), username)
        )


def set_user_active(username, active):
    with get_db() as db:
        db.execute(
            "UPDATE users SET active = ?, updated_at = ? WHERE username = ?",
            (1 if active else 0, time.time(), username)
        )


def get_user(username):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()


def user_count():
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return row["count"]


def list_users():
    with get_db() as db:
        return db.execute(
            "SELECT username, branch, role, active, created_at, updated_at FROM users ORDER BY branch, username"
        ).fetchall()


def seed_users_from_env():
    if user_count() > 0:
        return

    raw_accounts = os.getenv("QUEUE_ADMIN_USERS", "").strip()
    for item in raw_accounts.split(";"):
        if not item.strip():
            continue

        parts = item.split(":", 2)
        if len(parts) != 3:
            continue

        branch, username, password = [part.strip() for part in parts]
        if branch and username and password:
            create_user(username, password, branch)


def record_audit(event, username=None, branch=None, details=None):
    if details is None:
        details = {}

    with get_db() as db:
        db.execute(
            """
            INSERT INTO audit_logs (event, username, branch, ip_address, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event,
                username or session.get("username"),
                normalize_branch(branch or session.get("branch") or DEFAULT_BRANCH),
                get_client_ip(),
                json.dumps(details, sort_keys=True),
                time.time(),
            )
        )


def get_audit_logs(branch, include_all=False, limit=100):
    with get_db() as db:
        if include_all:
            return db.execute(
                "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()

        return db.execute(
            "SELECT * FROM audit_logs WHERE branch = ? ORDER BY created_at DESC LIMIT ?",
            (normalize_branch(branch), limit)
        ).fetchall()


init_db()
seed_users_from_env()


def public_branch():
    return normalize_branch(request.args.get("branch") or DEFAULT_BRANCH)


def active_branch():
    return normalize_branch(session.get("branch") or DEFAULT_BRANCH)


def logged_in():
    return bool(session.get("username") and session.get("branch"))


def is_super_admin():
    return session.get("role") == "super_admin"


def safe_next_url(next_url):
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url

    return url_for("controller")


def login_required_response():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Login required"}), 401

    return redirect(url_for("login", next=request.full_path.rstrip("?")))


def get_queue_name(queue):
    service = "Registration" if queue.startswith("reg") else "Issuance"
    queue_type = "Priority" if queue.endswith("pri") else "Regular"
    return service, queue_type


def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.remote_addr or ""


def is_lan_request():
    try:
        client_ip = ip_address(get_client_ip())
    except ValueError:
        return False

    return any(client_ip in network for network in LAN_NETWORKS)


def request_admin_token():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return (
        request.headers.get("X-Admin-Token", "")
        or request.args.get("token", "")
        or request.cookies.get("queue_admin_token", "")
    ).strip()


def control_allowed():
    if logged_in():
        return True

    if ADMIN_TOKEN and request_admin_token() == ADMIN_TOKEN:
        return True

    return is_lan_request()


def require_control_access():
    if control_allowed():
        return None

    if not logged_in() and not request_admin_token():
        return jsonify({"error": "Login required"}), 401

    return jsonify({"error": "Controller access denied"}), 403


def make_announcement(queue, mode, serving_number=None, called_number=None):
    service, queue_type = get_queue_name(queue)

    if mode == "called":
        return f"{service} {queue_type}, number {called_number}, called."

    if mode == "serving_called":
        return (
            f"{service} {queue_type}, number {serving_number}, now serving. "
            f"{service} {queue_type}, number {called_number}, called."
        )

    if mode == "serving_cutoff":
        return (
            f"{service} {queue_type}, number {serving_number}, now serving. "
            f"{service} {queue_type} queue is now cut off."
        )

    return ""


def save_announcement(branch, queue, message):
    if not message:
        return None

    branch = normalize_branch(branch)
    timestamp = time.time()

    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO announcements (branch, queue, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (branch, queue, message, timestamp)
        )
        event_id = cursor.lastrowid
        old_rows = db.execute(
            """
            SELECT id FROM announcements
            WHERE branch = ?
            ORDER BY id DESC
            LIMIT 1 OFFSET 300
            """,
            (branch,)
        ).fetchone()
        if old_rows:
            db.execute(
                "DELETE FROM announcements WHERE branch = ? AND id <= ?",
                (branch, old_rows["id"])
            )

    return {
        "id": event_id,
        "message": message,
        "queue": queue,
        "branch": branch,
        "timestamp": timestamp,
    }


@app.route("/")
def home():
    return render_template("home.html", branch=DEFAULT_BRANCH, user=session.get("username"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    next_url = safe_next_url(request.args.get("next") or url_for("controller"))

    if request.method == "POST":
        next_url = safe_next_url(request.form.get("next") or next_url)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        account = get_user(username)

        if account and account["active"] and check_password_hash(account["password_hash"], password):
            session.clear()
            session["username"] = username
            session["branch"] = normalize_branch(account["branch"])
            session["role"] = account["role"]
            record_audit("login_success", username=username, branch=account["branch"])
            return redirect(next_url)

        record_audit("login_failed", username=username, branch=DEFAULT_BRANCH)
        error = "Invalid username or password."

    return render_template(
        "login.html",
        error=error,
        next_url=next_url,
        has_accounts=user_count() > 0
    )


@app.route("/logout")
def logout():
    if logged_in():
        record_audit("logout")
    session.clear()
    return redirect(url_for("login"))


@app.route("/audit")
def audit():
    if not logged_in():
        return login_required_response()

    logs = get_audit_logs(active_branch(), include_all=is_super_admin())
    return render_template(
        "audit.html",
        logs=logs,
        branch=active_branch(),
        username=session.get("username"),
        is_super_admin=is_super_admin()
    )


@app.route("/controller")
def controller():
    if not logged_in():
        return login_required_response()

    branch = active_branch()
    return render_template("index.html", branch=branch, username=session.get("username"))


@app.route("/display")
def display():
    return render_template("display.html", branch=DEFAULT_BRANCH)


@app.route("/branch/<branch>/display")
def branch_display(branch):
    return render_template("display.html", branch=normalize_branch(branch))


@app.route("/window/<int:window_id>")
def window_controller(window_id):
    if not logged_in():
        return login_required_response()

    if window_id not in WINDOW_MAPPING:
        return "Invalid window", 404

    return render_template(
        "window.html",
        window_id=window_id,
        config=WINDOW_MAPPING[window_id],
        branch=active_branch(),
        username=session.get("username")
    )


@app.route("/api/state")
def get_state():
    branch = public_branch()
    with lock:
        return jsonify(get_branch_state(branch))

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "psa-flask", "time": time.time()})


@app.route("/api/debug-client")
def debug_client():
    return jsonify({
        "client_ip": get_client_ip(),
        "remote_addr": request.remote_addr,
        "scheme": request.scheme,
        "host": request.host,
        "is_lan": is_lan_request(),
        "control_allowed": control_allowed(),
        "session_user": session.get("username"),
        "session_branch": session.get("branch"),
        "trusted_proxy": os.getenv("TRUST_PROXY", "true"),
        "headers": {
            "Forwarded": request.headers.get("Forwarded"),
            "X-Forwarded-For": request.headers.get("X-Forwarded-For"),
            "X-Forwarded-Host": request.headers.get("X-Forwarded-Host"),
            "X-Forwarded-Proto": request.headers.get("X-Forwarded-Proto"),
        },
    })


@app.route("/api/announcements")
def get_announcements():
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0

    branch = public_branch()

    with lock:
        with get_db() as db:
            latest_row = db.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest_id FROM announcements WHERE branch = ?",
                (branch,)
            ).fetchone()
            rows = db.execute(
                """
                SELECT id, message, queue, branch, created_at
                FROM announcements
                WHERE branch = ? AND id > ?
                ORDER BY id
                """,
                (branch, since)
            ).fetchall()

        events = [
            {
                "id": row["id"],
                "message": row["message"],
                "queue": row["queue"],
                "branch": row["branch"],
                "timestamp": row["created_at"],
            }
            for row in rows
        ]

        return jsonify({
            "latest_id": latest_row["latest_id"],
            "events": events
        })


@app.route("/api/next/<queue>", methods=["POST"])
def next_queue(queue):
    denied = require_control_access()
    if denied:
        return denied

    if queue not in QUEUES:
        return jsonify({"error": "Invalid queue"}), 400

    branch = active_branch()
    with lock:
        with get_db() as db:
            ensure_branch_state(db, branch)
            row = db.execute(
                """
                SELECT called, serving, max_value
                FROM queue_state
                WHERE branch = ? AND queue = ?
                """,
                (branch, queue)
            ).fetchone()
            q = row_to_queue_state(row)

            called = q["called"]
            max_slot = q["max"]

            if called == "Cut Off":
                record_audit("queue_next_ignored_cutoff", branch=branch, details={"queue": queue})
                return jsonify({"state": get_branch_state(branch), "event": None})

            if called == 0 and q["serving"] == 0:
                q["called"] = 1
                announcement = make_announcement(queue, "called", called_number=1)

            elif max_slot is not None and called >= max_slot:
                q["serving"] = called
                q["called"] = "Cut Off"
                announcement = make_announcement(queue, "serving_cutoff", serving_number=called)

            else:
                new_serving = called
                new_called = called + 1

                q["serving"] = new_serving
                q["called"] = new_called

                announcement = make_announcement(
                    queue,
                    "serving_called",
                    serving_number=new_serving,
                    called_number=new_called
                )

            set_queue_state(db, branch, queue, q)

        queue_state = get_branch_state(branch)
        event = save_announcement(branch, queue, announcement)
        record_audit(
            "queue_next",
            branch=branch,
            details={
                "queue": queue,
                "called": q["called"],
                "serving": q["serving"],
                "max": q["max"],
            }
        )

        return jsonify({"state": queue_state, "event": event})


@app.route("/api/reset/<queue>", methods=["POST"])
def reset_queue(queue):
    denied = require_control_access()
    if denied:
        return denied

    if queue not in QUEUES:
        return jsonify({"error": "Invalid queue"}), 400

    branch = active_branch()
    with lock:
        with get_db() as db:
            ensure_branch_state(db, branch)
            q = {"called": 0, "serving": 0, "max": None}
            set_queue_state(db, branch, queue, q)

        record_audit("queue_reset", branch=branch, details={"queue": queue})

        return jsonify({"state": get_branch_state(branch)})


@app.route("/api/max/<queue>", methods=["POST"])
def update_max(queue):
    denied = require_control_access()
    if denied:
        return denied

    if queue not in QUEUES:
        return jsonify({"error": "Invalid queue"}), 400

    branch = active_branch()
    data = request.get_json() or {}
    raw_value = str(data.get("max", "")).strip()

    with lock:
        with get_db() as db:
            ensure_branch_state(db, branch)
            row = db.execute(
                """
                SELECT called, serving, max_value
                FROM queue_state
                WHERE branch = ? AND queue = ?
                """,
                (branch, queue)
            ).fetchone()
            q = row_to_queue_state(row)

            if raw_value == "":
                q["max"] = None
            else:
                try:
                    max_value = int(raw_value)
                    q["max"] = max_value if max_value > 0 else None
                except ValueError:
                    q["max"] = None

            if q["called"] == "Cut Off" and q["max"] is not None and q["max"] > q["serving"]:
                q["called"] = q["serving"] + 1

            set_queue_state(db, branch, queue, q)

        record_audit(
            "queue_set_max",
            branch=branch,
            details={"queue": queue, "max": q["max"]}
        )

        return jsonify({"state": get_branch_state(branch)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
