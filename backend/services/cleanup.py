"""Daily cleanup. Purges originals older than retention (default 30d) ONLY when
posts.status='posted' AND thumbnail file exists. Logs every deletion to post_events
with event_type='original_purged'.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AppConfig, Post, Reel
from services import events, storage

log = logging.getLogger("framepost.cleanup")


def _retention_days(db: Session) -> int:
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "original_retention_days")
    ).scalar_one_or_none()
    try:
        return int(row.value) if row and row.value else 30
    except ValueError:
        return 30


def purge_expired_originals(db: Session) -> int:
    """Delete original files for posts that are safely on Flickr and past the retention window.

    Safety predicates (all required):
      - status == 'posted' (not 'late' / 'missed' / 'failed' / 'pending')
      - posted_at is set AND older than retention_days
      - original_path is set on disk
      - thumbnail file still exists on disk (so we don't lose the only visual record)

    Returns the number of files purged. Each deletion logs an `original_purged` event.
    """
    days = _retention_days(db)
    cutoff = datetime.utcnow() - timedelta(days=days)

    candidates = db.execute(
        select(Post).where(
            Post.status == "posted",
            Post.posted_at.is_not(None),
            Post.posted_at < cutoff,
            Post.original_path.is_not(None),
        )
    ).scalars().all()

    purged = 0
    for post in candidates:
        if not post.thumbnail_path:
            continue
        thumb = Path(post.thumbnail_path)
        if not thumb.exists():
            log.warning("post %s thumbnail missing — refusing to purge original", post.id[:8])
            continue
        original = Path(post.original_path)
        if original.exists():
            try:
                size = original.stat().st_size
                original.unlink()
            except OSError as e:
                log.warning("post %s: could not delete original: %s", post.id[:8], e)
                continue
            events.log_event(
                db,
                post_id=post.id,
                event_type="original_purged",
                actor="worker",
                details={
                    "retention_days": days,
                    "posted_at": post.posted_at.isoformat(),
                    "freed_bytes": size,
                },
            )
            log.info("purged original for post %s (%d bytes freed)", post.id[:8], size)
        # Even if the file was already gone, clear the path on the row so we stop trying.
        post.original_path = None
        purged += 1

    if purged:
        db.commit()
    return purged


def _reel_retention_days(db: Session) -> int:
    """Reel MP4s expire faster than originals — they're easy to regenerate and bulky.
    Default 30 days; configurable via app_config.reel_retention_days."""
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "reel_retention_days")
    ).scalar_one_or_none()
    try:
        return int(row.value) if row and row.value else 30
    except ValueError:
        return 30


def purge_expired_reels(db: Session) -> int:
    """Delete Reel MP4 files older than reel_retention_days.

    Keeps the `reels` DB row (so the user still sees the Reel in history with metadata
    + photo sequence) but clears mp4_path so the UI can show 'Expired — regenerate to
    download.' The reel can be regenerated via POST /api/reels/{id}/regenerate without
    losing any photo selections, crops, or caption.

    Returns the number of MP4s purged.
    """
    days = _reel_retention_days(db)
    cutoff = datetime.utcnow() - timedelta(days=days)

    candidates = db.execute(
        select(Reel).where(
            Reel.created_at < cutoff,
            Reel.mp4_path.is_not(None),
        )
    ).scalars().all()

    purged = 0
    for reel in candidates:
        path = Path(reel.mp4_path)
        if path.exists():
            try:
                size = path.stat().st_size
                path.unlink()
                log.info("purged reel mp4 %s (%d bytes freed)", reel.id[:8], size)
            except OSError as e:
                log.warning("reel %s: could not delete mp4: %s", reel.id[:8], e)
                continue
        # Clear path even if the file was already gone — keeps DB consistent with disk.
        reel.mp4_path = None
        purged += 1

    if purged:
        db.commit()
    return purged

