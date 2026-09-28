"""Keep a drafts-built reel's frames aimed at Instagram, or not, to match the reel.

An ordinary reel carries its photographs to Instagram, so its frames stop targeting
Instagram individually -- otherwise the same five photos go out as a reel AND as five
posts. A Trial Reel is shown to non-followers first; the photographer's rule is that a
trial is extra reach, so its frames keep Instagram and followers still get them as feed
posts.

Only frames that were true drafts (the Draft Queue's own definition: pending, not
scheduled) aimed at Instagram when the reel was built are ever managed -- they carry
reel_photos.ig_follows_reel. A scheduled photo already has a plan someone made, and a
posted one is history; neither is touched.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.util import identity_key

from models import PlatformCredential, Post, PostPlatform, Reel, ReelPhoto
from services import events, preflight


def is_draft(post: Post) -> bool:
    """What the Draft Queue lists (routes/posts.list_drafts): pending and unscheduled."""
    return post.status == "pending" and post.scheduled_at is None and post.posted_at is None


def is_posted(db: Session, post: Post) -> bool:
    """Has this photograph gone out anywhere? Then its targeting is history."""
    if post.posted_at is not None or post.status in ("posted", "late"):
        return True
    return db.execute(
        select(PostPlatform.post_id).where(
            PostPlatform.post_id == post.id, PostPlatform.remote_id.is_not(None))
    ).first() is not None


def should_follow(db: Session, post: Post | None, creds: list[PlatformCredential]) -> bool:
    """At build time: may this frame's Instagram targeting follow the reel? Decided here,
    server-side, whatever the client claims about where the frames came from."""
    return (post is not None and is_draft(post) and not is_posted(db, post)
            and "instagram" in preflight.targets_for(post, creds))


def sync_frame_instagram(db: Session, reel: Reel) -> list[str]:
    """Make each managed frame's Instagram targeting match reel.trial_graduation.
    Returns the post ids changed. Caller commits.

    A frame is managed only while it is still a true draft — the same test as at build
    time. Once the photographer schedules it (or it posts), its targeting is theirs: the
    flag is cleared for good and the frame is never touched again, by a trial toggle or
    by the reconcile after the reel publishes.

    Every change is one compare-and-swap UPDATE (see _swap_targets): eligibility and
    the value this decision was based on are re-asserted in the WHERE clause, so a
    photographer scheduling the frame, or editing its targets, between the read and the
    write wins — the frame is left alone rather than overwritten.
    """
    want_ig = reel.trial_graduation is not None
    creds = list(db.execute(select(PlatformCredential)).scalars())
    changed: list[str] = []
    frames = db.execute(
        select(ReelPhoto).where(ReelPhoto.reel_id == reel.id, ReelPhoto.ig_follows_reel.is_(True))
    ).scalars().all()
    for frame in frames:
        # Read the row itself, not the session's copy of the Post: with autoflush off the
        # identity map can be older than the database, and the swap compares against it.
        row = db.execute(
            select(Post.status, Post.scheduled_at, Post.posted_at, Post.target_platforms)
            .where(Post.id == frame.post_id)
        ).one_or_none()
        if row is None:
            continue
        if not _still_a_draft(db, frame.post_id, row):
            frame.ig_follows_reel = False
            continue
        read = row.target_platforms
        targets = preflight.targets_for(SimpleNamespace(target_platforms=read), creds)
        if want_ig == ("instagram" in targets):
            continue
        targets = targets + ["instagram"] if want_ig else [t for t in targets if t != "instagram"]
        if not _swap_targets(db, frame.post_id, read, json.dumps(targets)):
            # Someone else wrote first. If it is no longer a draft it has left the
            # reel's hands for good; if it is, their targeting stands for now.
            again = db.execute(
                select(Post.status, Post.scheduled_at, Post.posted_at)
                .where(Post.id == frame.post_id)
            ).one_or_none()
            if again is None or not _still_a_draft(db, frame.post_id, again):
                frame.ig_follows_reel = False
            continue
        events.log_event(db, post_id=frame.post_id, event_type="edited", actor="system", details={
            "fields": ["target_platforms"], "reel_id": reel.id,
            "reason": "trial reel: frame also posts to Instagram" if want_ig
            else "reel carries this photo to Instagram",
        })
        changed.append(frame.post_id)
    return changed


def _still_a_draft(db: Session, post_id: str, row) -> bool:
    if not (row.status == "pending" and row.scheduled_at is None and row.posted_at is None):
        return False
    return db.execute(
        select(PostPlatform.post_id).where(
            PostPlatform.post_id == post_id, PostPlatform.remote_id.is_not(None))
    ).first() is None


def _swap_targets(db: Session, post_id: str, read: str | None, new: str) -> bool:
    """Set target_platforms only if the post is still a true draft AND its targeting is
    still exactly what was read (NULL-safe). One statement: SQLite serialises writers,
    so nothing lands between the check and the write. True if it was written."""
    won = db.execute(
        update(Post)
        .where(
            Post.id == post_id,
            Post.status == "pending",
            Post.scheduled_at.is_(None),
            Post.posted_at.is_(None),
            Post.target_platforms.is_not_distinct_from(read),
            ~exists().where(PostPlatform.post_id == Post.id, PostPlatform.remote_id.is_not(None)),
        )
        .values(target_platforms=new)
        .execution_options(synchronize_session=False)
    ).rowcount
    if won:
        # The session's copy (if loaded) is now stale; reload it on next access rather
        # than ever flushing the old value back.
        cached = db.identity_map.get(identity_key(Post, post_id))
        if cached is not None:
            db.expire(cached, ["target_platforms"])
    return bool(won)
