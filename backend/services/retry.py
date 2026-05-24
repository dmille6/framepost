"""Bounded exponential backoff. Brief: 5 attempts at 1m / 5m / 15m / 1h / 4h, then `failed`.
Permanent validation errors skip retry. Both knobs read from app_config so the operator can tune.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AppConfig

DEFAULT_BACKOFF = (1, 5, 15, 60, 240)
DEFAULT_MAX_ATTEMPTS = 5


def _read(db: Session, key: str) -> str | None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    return row.value if row else None


def max_attempts(db: Session) -> int:
    raw = _read(db, "retry_max_attempts")
    try:
        return int(raw) if raw else DEFAULT_MAX_ATTEMPTS
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def backoff_schedule(db: Session) -> tuple[int, ...]:
    raw = _read(db, "retry_backoff_minutes")
    if not raw:
        return DEFAULT_BACKOFF
    try:
        parsed = tuple(int(p.strip()) for p in raw.split(",") if p.strip())
        return parsed or DEFAULT_BACKOFF
    except ValueError:
        return DEFAULT_BACKOFF


def next_retry_at(db: Session, attempt_number: int) -> datetime | None:
    """Return the wall-clock time of the next retry, or None if attempts are exhausted."""
    schedule = backoff_schedule(db)
    if attempt_number < 1 or attempt_number > len(schedule):
        return None
    minutes = schedule[attempt_number - 1]
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(tzinfo=None)
