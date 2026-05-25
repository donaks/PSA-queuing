import argparse
import getpass
import os
import sys


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import create_user, get_user, list_users, set_user_active, update_user_password


def read_password(args):
    if args.password:
        return args.password

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")

    return password


def create(args):
    if get_user(args.username):
        raise SystemExit(f"User already exists: {args.username}")

    create_user(
        username=args.username,
        password=read_password(args),
        branch=args.branch,
        role=args.role,
        active=True,
    )
    print(f"Created user {args.username} for branch {args.branch}.")


def password(args):
    if not get_user(args.username):
        raise SystemExit(f"User not found: {args.username}")

    update_user_password(args.username, read_password(args))
    print(f"Updated password for {args.username}.")


def activate(args):
    if not get_user(args.username):
        raise SystemExit(f"User not found: {args.username}")

    set_user_active(args.username, True)
    print(f"Activated {args.username}.")


def deactivate(args):
    if not get_user(args.username):
        raise SystemExit(f"User not found: {args.username}")

    set_user_active(args.username, False)
    print(f"Deactivated {args.username}.")


def users(args):
    rows = list_users()
    if not rows:
        print("No users found.")
        return

    for row in rows:
        status = "active" if row["active"] else "inactive"
        print(f"{row['username']}\t{row['branch']}\t{row['role']}\t{status}")


def main():
    parser = argparse.ArgumentParser(description="Manage PSA Queue staff users.")
    subparsers = parser.add_subparsers(required=True)

    create_parser = subparsers.add_parser("create", help="Create a staff user")
    create_parser.add_argument("username")
    create_parser.add_argument("--branch", required=True)
    create_parser.add_argument("--role", default="branch_admin", choices=["branch_admin", "super_admin"])
    create_parser.add_argument("--password")
    create_parser.set_defaults(func=create)

    password_parser = subparsers.add_parser("password", help="Change a user's password")
    password_parser.add_argument("username")
    password_parser.add_argument("--password")
    password_parser.set_defaults(func=password)

    activate_parser = subparsers.add_parser("activate", help="Activate a user")
    activate_parser.add_argument("username")
    activate_parser.set_defaults(func=activate)

    deactivate_parser = subparsers.add_parser("deactivate", help="Deactivate a user")
    deactivate_parser.add_argument("username")
    deactivate_parser.set_defaults(func=deactivate)

    list_parser = subparsers.add_parser("list", help="List users")
    list_parser.set_defaults(func=users)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
