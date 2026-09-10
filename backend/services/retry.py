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
    """Effective attempt budget — never more attempts than the schedule can time.

    The two knobs are separate fields in Settings and were validated separately, so
    max_attempts could outrun the backoff schedule. The extra attempts didn't fail
    loudly; next_retry_at() returned None for them while the count was still under the
    max, which left the row 'pending' with no retry timer. The scanner only picks up
    rows with a timer, so the post sat there indefinitely: on Flickr, missing from
    Instagram, and never marked failed for the health banner to notice.

    Capping here means the count always reaches the max while a retry is still
    schedulable, so exhaustion is reported instead of vanishing.
    """
    raw = _read(db, "retry_max_attempts")
    try:
        configured = int(raw) if raw else DEFAULT_MAX_ATTEMPTS
    except ValueError:
        configured = DEFAULT_MAX_ATTEMPTS
    return max(1, min(configured, len(backoff_schedule(db))))


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
