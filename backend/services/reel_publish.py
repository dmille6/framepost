"""Put a finished reel on Instagram without anyone touching a phone.

The reel builder has produced a correct MP4 since May and stopped there: the
photographer downloaded it, moved it to a phone, and uploaded it by hand. One reel
has ever been made. A workflow with a manual step in the middle is a workflow that
does not get run, which is the whole argument for this module.

The shape mirrors the photo path deliberately. Meta does not accept an upload; it
fetches a URL, so the MP4 goes to R2 exactly as staged JPEGs do, and the staged
object is deleted once Meta has ingested it. What differs is time: Meta transcodes
a reel server-side and that can take minutes, so `instagram.post_reel` polls far
longer than the image path and a timeout here is a retry rather than a failure.

Nothing in here raises past the caller. A reel that fails records why and waits for
the next pass; the worker must not die because Meta was slow.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Performer, PostPerformer, Reel, ReelPhoto
from services import r2
from services.platforms import instagram as ig

log = logging.getLogger("framepost.reel_publish")

# Meta fetches once, during container creation, but transcoding runs afterwards and the
# URL must survive it. r2.DEFAULT_EXPIRY (2h) is ample; named here so the reason is
# attached to the decision rather than inherited silently.
STAGE_EXPIRY = r2.DEFAULT_EXPIRY

# A reel that has failed this many times is not going to succeed by being tried again,
# and a permanently broken one retried forever is how a worker queue fills with noise.
MAX_ATTEMPTS = 4


class ReelPublishError(Exception):
    """Something stopped this reel going out. Carries whether retrying is worthwhile."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def due_reels(db: Session, *, now: datetime | None = None) -> list[Reel]:
    """Reels whose time has come: rendered, scheduled, not yet posted, not exhausted.

    `status == "ready"` matters -- a reel still rendering has no file to stage, and one
    that failed to render has nothing worth sending.
    """
    now = now or _utcnow()
    return list(db.execute(
        select(Reel).where(
            Reel.status == "ready",
            Reel.scheduled_at.is_not(None),
            Reel.scheduled_at <= now,
            Reel.posted_at.is_(None),
            Reel.publish_attempts < MAX_ATTEMPTS,
        ).order_by(Reel.scheduled_at)
    ).scalars().all())


def collaborators_for(db: Session, reel: Reel) -> list[str]:
    """Instagram handles of every performer appearing anywhere in the reel.

    Not just the cover. A reel is a set of photographs of several performers, and the
    ones whose frames are in it have as much claim to a credit as whoever happens to be
    on the thumbnail -- and each accepted invite puts the reel in front of that
    performer's followers, which is the reach this whole exercise is chasing.

    Order follows the reel, so the cover's performers come first and Instagram's cap of
    three lands on the people most prominent in it.
    """
    positions = db.execute(
        select(ReelPhoto.post_id)
        .where(ReelPhoto.reel_id == reel.id)
        .order_by(ReelPhoto.position)
    ).scalars().all()
    ordered = [reel.cover_post_id] + [p for p in positions if p != reel.cover_post_id]

    seen: set[str] = set()
    handles: list[str] = []
    for post_id in ordered:
        rows = db.execute(
            select(Performer.instagram_handle)
            .join(PostPerformer, PostPerformer.performer_id == Performer.id)
            .where(PostPerformer.post_id == post_id,
                   Performer.instagram_handle.is_not(None))
        ).scalars().all()
        for h in rows:
            key = (h or "").lstrip("@").strip().lower()
            if key and key not in seen:
                seen.add(key)
                handles.append(key)
    return handles


def stage(reel: Reel) -> tuple[str, str]:
    """Upload the MP4 to R2. Returns (key, publicly fetchable URL).

    R2 is not optional for this the way it is for a photo: a photo can fall back to its
    Flickr rendition, and a reel has no equivalent -- the MP4 exists nowhere but this
    disk until it is staged.
    """
    if not r2.configured():
        raise ReelPublishError(
            "Instagram fetches the video from a public URL, and R2 staging isn't "
            "configured — there is nowhere to serve the MP4 from.",
            permanent=True,
        )
    if not reel.mp4_path:
        raise ReelPublishError("This reel has no rendered MP4.", permanent=True)
    path = Path(reel.mp4_path)
    if not path.exists():
        raise ReelPublishError(
            f"The rendered MP4 is gone from disk ({reel.mp4_path}) — regenerate the reel.",
            permanent=True,
        )

    key = f"reels/{reel.id}.mp4"
    r2.put(key, path.read_bytes(), content_type="video/mp4")
    return key, r2.presign_get(key, expires=STAGE_EXPIRY)


def unstage(reel: Reel) -> None:
    """Drop the staged MP4. Best-effort: a leaked object costs pennies, and failing the
    publish over cleanup would trade a real success for a bookkeeping problem."""
    if not reel.staged_key:
        return
    try:
        r2.delete(reel.staged_key)
    except Exception:  # noqa: BLE001 — cleanup must never mask a completed publish
        log.warning("reel %s: couldn't remove staged object %s", reel.id[:8], reel.staged_key)
    reel.staged_key = None


def publish(db: Session, reel: Reel) -> dict:
    """Stage, publish, record. Raises ReelPublishError; the caller decides about retries."""
    reel.publish_attempts = (reel.publish_attempts or 0) + 1
    key, url = stage(reel)
    reel.staged_key = key
    db.commit()

    try:
        result = ig.post_reel(
            db,
            video_url=url,
            caption=reel.caption or "",
            collaborators=collaborators_for(db, reel),
        )
    except ig.InstagramError as e:
        reel.publish_error = str(e)
        db.commit()
        raise ReelPublishError(str(e), permanent=getattr(e, "permanent", False)) from e

    reel.remote_id = result.get("remote_id")
    reel.remote_url = result.get("url")
    reel.posted_at = _utcnow()
    reel.publish_error = None
    unstage(reel)
    db.commit()
    log.info("reel %s published as %s", reel.id[:8], reel.remote_id)
    return result


def run_due(db: Session, *, now: datetime | None = None) -> int:
    """Publish every due reel. Returns how many went out.

    One reel's failure never stops the next: they are independent pieces of work, and a
    permanent failure on one is not evidence about another.
    """
    published = 0
    for reel in due_reels(db, now=now):
        try:
            publish(db, reel)
            published += 1
        except ReelPublishError as e:
            log.warning("reel %s not published: %s", reel.id[:8], e)
            if e.permanent:
                # Stop it being picked up again; the message is already on the row.
                reel.publish_attempts = MAX_ATTEMPTS
            db.commit()
        except Exception:  # noqa: BLE001 — a worker pass must survive one bad reel
            log.exception("reel %s: unexpected failure", reel.id[:8])
            db.rollback()
    return published
