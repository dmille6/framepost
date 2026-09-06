"""Which platform connections are broken, and what it's costing.

Split from the publish path deliberately: a channel can be discovered dead by something
other than a post (a nightly sweep, a token refresh), and the answer the operator needs
is always the same — *what is broken, and how much of my queue does it affect?*

"231 scheduled posts affected" is the part that makes a warning actionable. A banner
saying "Flickr error" is easy to ignore for a fortnight. One that says how many photos
are about to fail is not.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import PlatformCredential, Post


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def flag_reauth(db: Session, platform: str, message: str) -> bool:
    """Park a channel as needing the user to reconnect it. Idempotent.

    Returns True if this changed anything, so callers can avoid re-logging the same
    problem on every nightly tick.
    """
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == platform)
    ).scalars().first()
    if row is None:
        return False
    if row.auth_status == "reauth_required" and row.auth_error == message:
        return False
    row.auth_status = "reauth_required"
    row.auth_error = message
    row.auth_flagged_at = _now()
    db.commit()
    return True


def clear(db: Session, platform: str) -> None:
    """Called after a reconnect, or any proof the channel works again."""
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == platform)
    ).scalars().first()
    if row is None:
        return
    row.auth_status, row.auth_error, row.auth_flagged_at = "ok", None, None
    db.commit()


def _pending_count(db: Session) -> int:
    """Scheduled posts that haven't fired yet — the queue a dead channel will fail."""
    return db.execute(
        select(func.count()).select_from(Post).where(
            Post.status == "pending", Post.scheduled_at.is_not(None)
        )
    ).scalar_one()


def broken_channels(db: Session) -> list[dict[str, Any]]:
    """Every connection currently needing a reconnect, with its blast radius."""
    rows = db.execute(
        select(PlatformCredential).where(
            PlatformCredential.auth_status == "reauth_required"
        )
    ).scalars().all()
    if not rows:
        return []

    affected = _pending_count(db)
    out = []
    for r in rows:
        out.append({
            "platform": r.platform,
            "account_name": r.account_name,
            "message": r.auth_error or f"{r.platform.title()} needs reconnecting.",
            "flagged_at": r.auth_flagged_at.isoformat() if r.auth_flagged_at else None,
            # Every scheduled post fans out to the enabled platforms, so a broken
            # default target puts the whole upcoming queue at risk. Reported only for
            # channels that actually receive new posts.
            "scheduled_posts_affected": affected if r.default_target else 0,
        })
    return out


def summary(db: Session) -> dict[str, Any]:
    channels = broken_channels(db)
    return {
        "ok": not channels,
        "needs_reconnect": channels,
        "scheduled_pending": _pending_count(db),
    }
