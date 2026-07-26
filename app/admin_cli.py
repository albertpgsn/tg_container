import argparse
from getpass import getpass

from argon2 import PasswordHasher

from .database import Base, SessionLocal, engine
from .models import AdminUser


def add_admin(username: str) -> None:
    password = getpass("New admin password (16+ characters): ")
    repeated = getpass("Repeat password: ")
    if len(password) < 16 or password != repeated:
        raise SystemExit("Passwords do not match or are shorter than 16 characters")
    normalized = username.strip().lower()
    with SessionLocal() as db:
        existing = db.query(AdminUser).filter_by(username=normalized).first()
        if existing:
            raise SystemExit("Admin already exists")
        db.add(AdminUser(username=normalized, password_hash=PasswordHasher().hash(password)))
        db.commit()
    print(f"Admin {normalized} created")


def set_enabled(username: str, enabled: bool) -> None:
    normalized = username.strip().lower()
    with SessionLocal() as db:
        admin = db.query(AdminUser).filter_by(username=normalized).first()
        if not admin:
            raise SystemExit("Admin not found")
        admin.enabled = enabled
        db.commit()
    print(f"Admin {normalized} {'enabled' if enabled else 'disabled'}")


def list_admins() -> None:
    with SessionLocal() as db:
        for admin in db.query(AdminUser).order_by(AdminUser.username).all():
            print(f"{admin.username}\t{'enabled' if admin.enabled else 'disabled'}")


def main() -> None:
    Base.metadata.create_all(bind=engine)
    parser = argparse.ArgumentParser(description="Manage Telegram CRM administrators")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("username")
    disable_parser = subparsers.add_parser("disable")
    disable_parser.add_argument("username")
    enable_parser = subparsers.add_parser("enable")
    enable_parser.add_argument("username")
    subparsers.add_parser("list")
    args = parser.parse_args()
    if args.command == "add":
        add_admin(args.username)
    elif args.command == "disable":
        set_enabled(args.username, False)
    elif args.command == "enable":
        set_enabled(args.username, True)
    else:
        list_admins()


if __name__ == "__main__":
    main()
