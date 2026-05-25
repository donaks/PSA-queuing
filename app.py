from flask import Flask, render_template, jsonify, request
from threading import Lock
from dotenv import load_dotenv
from ipaddress import ip_address, ip_network
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import time

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

if os.getenv("TRUST_PROXY", "true").lower() in {"1", "true", "yes", "on"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

lock = Lock()

QUEUES = ["reg-pri", "reg-reg", "iss-pri", "iss-reg"]
ADMIN_TOKEN = os.getenv("QUEUE_ADMIN_TOKEN", "").strip()
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

queue_state = {
    "reg-pri": {"called": 0, "serving": 0, "max": None},
    "reg-reg": {"called": 0, "serving": 0, "max": None},
    "iss-pri": {"called": 0, "serving": 0, "max": None},
    "iss-reg": {"called": 0, "serving": 0, "max": None},
}

announcement_events = []
latest_announcement_id = 0

WINDOW_MAPPING = {
    1: {"queue": "reg-pri", "title": "REGISTRATION", "type": "PRIORITY"},
    2: {"queue": "reg-reg", "title": "REGISTRATION", "type": "REGULAR"},
    3: {"queue": "iss-pri", "title": "ISSUANCE", "type": "PRIORITY"},
    4: {"queue": "iss-reg", "title": "ISSUANCE", "type": "REGULAR"},
}


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
    if ADMIN_TOKEN and request_admin_token() == ADMIN_TOKEN:
        return True

    return is_lan_request()


def require_control_access():
    if control_allowed():
        return None

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


def save_announcement(queue, message):
    global latest_announcement_id

    if not message:
        return None

    latest_announcement_id += 1

    event = {
        "id": latest_announcement_id,
        "message": message,
        "queue": queue,
        "timestamp": time.time()
    }

    announcement_events.append(event)

    if len(announcement_events) > 300:
        del announcement_events[:150]

    return event


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/controller")
def controller():
    return render_template("index.html")


@app.route("/display")
def display():
    return render_template("display.html")


@app.route("/window/<int:window_id>")
def window_controller(window_id):
    if window_id not in WINDOW_MAPPING:
        return "Invalid window", 404

    return render_template(
        "window.html",
        window_id=window_id,
        config=WINDOW_MAPPING[window_id]
    )


@app.route("/api/state")
def get_state():
    with lock:
        return jsonify(queue_state)

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

    with lock:
        events = [event for event in announcement_events if event["id"] > since]
        events.sort(key=lambda event: event["id"])

        return jsonify({
            "latest_id": latest_announcement_id,
            "events": events
        })


@app.route("/api/next/<queue>", methods=["POST"])
def next_queue(queue):
    denied = require_control_access()
    if denied:
        return denied

    if queue not in QUEUES:
        return jsonify({"error": "Invalid queue"}), 400

    with lock:
        q = queue_state[queue]

        called = q["called"]
        serving = q["serving"]
        max_slot = q["max"]

        if called == "Cut Off":
            return jsonify({"state": queue_state, "event": None})

        if called == 0 and serving == 0:
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

        event = save_announcement(queue, announcement)

        return jsonify({"state": queue_state, "event": event})


@app.route("/api/reset/<queue>", methods=["POST"])
def reset_queue(queue):
    denied = require_control_access()
    if denied:
        return denied

    if queue not in QUEUES:
        return jsonify({"error": "Invalid queue"}), 400

    with lock:
        queue_state[queue]["called"] = 0
        queue_state[queue]["serving"] = 0
        queue_state[queue]["max"] = None

        return jsonify({"state": queue_state})


@app.route("/api/max/<queue>", methods=["POST"])
def update_max(queue):
    denied = require_control_access()
    if denied:
        return denied

    if queue not in QUEUES:
        return jsonify({"error": "Invalid queue"}), 400

    data = request.get_json() or {}
    raw_value = str(data.get("max", "")).strip()

    with lock:
        q = queue_state[queue]

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

        return jsonify({"state": queue_state})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
