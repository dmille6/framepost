"""Auth service — password hashing, session token signing, CSRF helpers.

Sessions are stateless signed cookies (itsdangerous URLSafeTimedSerializer + SECRET_KEY).
For a single-user LAN app this is simpler than a server-side sessions table; the cost
of stateless sessions (can't force-logout) is a non-issue here.
"""
from __future__ import annotations

import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import argon2
from sqlalchemy import select

from config import settings
from database import SessionLocal
from models import AppConfig, User

SESSION_COOKIE = "framepost_session"
CSRF_COOKIE = "framepost_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SESSION_SALT = "framepost.session.v1"


def _serializer() -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY is not set — cannot sign sessions.")
    return URLSafeTimedSerializer(settings.secret_key, salt=_SESSION_SALT)


def hash_password(plaintext: str) -> str:
    return argon2.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return argon2.verify(plaintext, hashed)
    except Exception:
        return False


def session_timeout_seconds() -> int:
    """Read session_timeout_minutes from app_config; fall back to settings default."""
    db = SessionLocal()
    try:
        row = db.execute(
            select(AppConfig).where(AppConfig.key == "session_timeout_minutes")
        ).scalar_one_or_none()
        minutes = int(row.value) if row and row.value else settings.session_timeout_minutes
    finally:
        db.close()
    return minutes * 60


def sign_session(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def verify_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=session_timeout_seconds())
    except (BadSignature, SignatureExpired):
        return None


def lookup_user(user_id: int) -> User | None:
    db = SessionLocal()
    try:
        return db.get(User, user_id)
    finally:
        db.close()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
