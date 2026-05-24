"""Admin CLI — first-run setup and operational commands.

Run inside the backend container:
    docker compose exec backend python -m admin <command>

Commands (brief: Security & Operational → CLI admin commands):
    create-admin              Create the single admin account interactively. Idempotent.
    reset-password            Reset the admin's password.
    generate-encryption-key   Print a fresh Fernet key for TOKEN_ENCRYPTION_KEY.
    backup                    Run an ad-hoc SQLite hot backup.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from passlib.hash import argon2

from config import settings
from database import SessionLocal
from models import User


def _db_path() -> Path:
    url = settings.database_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise RuntimeError(f"Expected sqlite:/// URL, got {url}")
    return Path("/" + url[len(prefix):]) if url[len(prefix):].startswith("/") else Path(url[len(prefix):])


def cmd_create_admin(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        existing = db.query(User).first()
        if existing and not args.force:
            print(f"Admin already exists ({existing.username}). Use --force to override.", file=sys.stderr)
            return 2
        username = input("Admin username: ").strip()
        if not username:
            print("Username required.", file=sys.stderr)
            return 1
        pw1 = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2 or not pw1:
            print("Passwords do not match or empty.", file=sys.stderr)
            return 1
        h = argon2.hash(pw1)
        if existing:
            existing.username = username
            existing.password_hash = h
        else:
            db.add(User(username=username, password_hash=h))
        db.commit()
        print(f"Admin '{username}' saved.")
        return 0
    finally:
        db.close()


def cmd_reset_password(_args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).first()
        if not user:
            print("No admin user exists. Run create-admin first.", file=sys.stderr)
            return 2
        pw1 = getpass.getpass(f"New password for {user.username}: ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 != pw2 or not pw1:
            print("Passwords do not match or empty.", file=sys.stderr)
            return 1
        user.password_hash = argon2.hash(pw1)
        db.commit()
        print(f"Password updated for {user.username}.")
        return 0
    finally:
        db.close()


def cmd_generate_encryption_key(_args: argparse.Namespace) -> int:
    print(Fernet.generate_key().decode())
    print(
        "\nAdd this to TOKEN_ENCRYPTION_KEY in .env. To rotate later, prepend a new key "
        "(comma-separated) — MultiFernet will decrypt with any listed key, encrypt with the first.",
        file=sys.stderr,
    )
    return 0


def cmd_backup(_args: argparse.Namespace) -> int:
    src_path = _db_path()
    if not src_path.exists():
        print(f"Database not found at {src_path}", file=sys.stderr)
        return 1
    backup_dir = Path(settings.photo_root) / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst_path = backup_dir / f"framepost-{stamp}.sqlite"
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print(f"Backup written to {dst_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-admin", help="Create the single admin user (idempotent).")
    p.add_argument("--force", action="store_true", help="Overwrite an existing admin.")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("reset-password", help="Reset the admin password.")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("generate-encryption-key", help="Print a fresh Fernet key for TOKEN_ENCRYPTION_KEY.")
    p.set_defaults(func=cmd_generate_encryption_key)

    p = sub.add_parser("backup", help="Run an ad-hoc SQLite hot backup.")
    p.set_defaults(func=cmd_backup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
