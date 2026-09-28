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

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    """
    want_ig = reel.trial_graduation is not None
    creds = list(db.execute(select(PlatformCredential)).scalars())
    changed: list[str] = []
    frames = db.execute(
        select(ReelPhoto).where(ReelPhoto.reel_id == reel.id, ReelPhoto.ig_follows_reel.is_(True))
    ).scalars().all()
    for frame in frames:
        post = db.get(Post, frame.post_id)
        if post is None:
            continue
        if not is_draft(post) or is_posted(db, post):
            frame.ig_follows_reel = False
            continue
        targets = preflight.targets_for(post, creds)
        if want_ig == ("instagram" in targets):
            continue
        targets = targets + ["instagram"] if want_ig else [t for t in targets if t != "instagram"]
        post.target_platforms = json.dumps(targets)
        events.log_event(db, post_id=post.id, event_type="edited", actor="system", details={
            "fields": ["target_platforms"], "reel_id": reel.id,
            "reason": "trial reel: frame also posts to Instagram" if want_ig
            else "reel carries this photo to Instagram",
        })
        changed.append(post.id)
    return changed
