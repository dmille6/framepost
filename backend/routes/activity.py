"""Activity feed — unified comments + engagement deltas across all connected platforms.

Endpoints:
  GET  /api/activity                 chronological feed (most recent first)
  GET  /api/activity/unread-count    unread comments count, for the nav badge
  POST /api/activity/mark-all-seen   bulk-mark comments as seen
  POST /api/activity/sync-now        trigger an on-demand sync (no need to wait for daily cron)
  GET  /api/posts/{post_id}/comments  per-post comment list (for the Published modal)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from database import get_session
from models import EngagementSnapshot, Post, PostComment, PostLike, User
from routes.auth import current_user
from services import comments as comments_sync

log = logging.getLogger("framepost.activity")
router = APIRouter()


class ActivityItem(BaseModel):
    # Composite key: kind + numeric id is unique across the union, since comments and likes
    # both have their own auto-increment ids and we never need to dedup across the two.
    kind: str  # "comment" | "like"
    id: int
    post_id: str
    post_title: str | None
    platform: str
    author_handle: str | None
    author_display_name: str | None
    author_url: str | None
    body: str  # comment text, or empty for likes
    posted_at: datetime | None
    fetched_at: datetime
    seen_at: datetime | None


def _sort_key(item: ActivityItem) -> datetime:
    """Newest first by posted_at, falling back to fetched_at when posted_at is missing
    (Pixelfed favourited_by doesn't carry a per-fave timestamp)."""
    return item.posted_at or item.fetched_at


@router.get("", response_model=list[ActivityItem])
def list_activity(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    only_unread: bool = Query(False),
):
    """Unified comments + likes feed across platforms. We over-fetch from each table by the
    full limit, merge in Python, then page — for the scale we're at (low thousands of items
    per user) this is much simpler than a SQL UNION ALL with platform-specific ORDER BY."""
    over = limit + offset

    cs_stmt = (
        select(PostComment, Post.title)
        .join(Post, Post.id == PostComment.post_id)
        .order_by(
            desc(PostComment.posted_at.is_(None)),
            desc(PostComment.posted_at),
            desc(PostComment.fetched_at),
        )
        .limit(over)
    )
    ls_stmt = (
        select(PostLike, Post.title)
        .join(Post, Post.id == PostLike.post_id)
        .order_by(
            desc(PostLike.liked_at.is_(None)),
            desc(PostLike.liked_at),
            desc(PostLike.fetched_at),
        )
        .limit(over)
    )
    if only_unread:
        cs_stmt = cs_stmt.where(PostComment.seen_at.is_(None))
        ls_stmt = ls_stmt.where(PostLike.seen_at.is_(None))

    items: list[ActivityItem] = []
    for c, title in db.execute(cs_stmt).all():
        items.append(ActivityItem(
            kind="comment",
            id=c.id,
            post_id=c.post_id,
            post_title=title,
            platform=c.platform,
            author_handle=c.author_handle,
            author_display_name=c.author_display_name,
            author_url=c.author_url,
            body=c.body,
            posted_at=c.posted_at,
            fetched_at=c.fetched_at,
            seen_at=c.seen_at,
        ))
    for li, title in db.execute(ls_stmt).all():
        items.append(ActivityItem(
            kind="like",
            id=li.id,
            post_id=li.post_id,
            post_title=title,
            platform=li.platform,
            author_handle=li.actor_handle,
            author_display_name=li.actor_display_name,
            author_url=li.actor_url,
            body="",
            posted_at=li.liked_at,
            fetched_at=li.fetched_at,
            seen_at=li.seen_at,
        ))

    items.sort(key=_sort_key, reverse=True)
    return items[offset : offset + limit]


class PlatformBreakdown(BaseModel):
    likes: int
    comments: int
    unread: int


class PostActivitySummary(BaseModel):
    post_id: str
    post_title: str | None
    flickr_url: str | None
    posted_at: datetime | None
    newest_activity_at: datetime | None
    total_likes: int
    total_comments: int
    unread: int
    platforms: dict[str, PlatformBreakdown]


@router.get("/by-post", response_model=list[PostActivitySummary])
def by_post(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    limit: int = Query(100, ge=1, le=500),
):
    """Aggregated view: every post with at least one like or comment, with per-platform
    engagement breakdown. Sorted by most-recent activity first."""
    # Pull all activity rows, group in Python. For our scale (low thousands of rows) this
    # is much simpler than a multi-table SQL aggregation with platform pivots.
    comment_rows = db.execute(
        select(PostComment, Post.title, Post.flickr_url, Post.posted_at)
        .join(Post, Post.id == PostComment.post_id)
    ).all()
    like_rows = db.execute(
        select(PostLike, Post.title, Post.flickr_url, Post.posted_at)
        .join(Post, Post.id == PostLike.post_id)
    ).all()

    # Bucket by post_id.
    by_post: dict[str, dict] = {}

    def _bucket(post_id: str, title: str | None, flickr_url: str | None, posted_at: datetime | None) -> dict:
        if post_id not in by_post:
            by_post[post_id] = {
                "post_id": post_id,
                "post_title": title,
                "flickr_url": flickr_url,
                "posted_at": posted_at,
                "newest_activity_at": None,
                "platforms": {},  # platform -> {likes, comments, unread}
            }
        return by_post[post_id]

    def _platform(b: dict, platform: str) -> dict:
        if platform not in b["platforms"]:
            b["platforms"][platform] = {"likes": 0, "comments": 0, "unread": 0}
        return b["platforms"][platform]

    for c, title, flickr_url, posted_at in comment_rows:
        b = _bucket(c.post_id, title, flickr_url, posted_at)
        p = _platform(b, c.platform)
        p["comments"] += 1
        if c.seen_at is None:
            p["unread"] += 1
        ts = c.posted_at or c.fetched_at
        if ts and (b["newest_activity_at"] is None or ts > b["newest_activity_at"]):
            b["newest_activity_at"] = ts

    for li, title, flickr_url, posted_at in like_rows:
        b = _bucket(li.post_id, title, flickr_url, posted_at)
        p = _platform(b, li.platform)
        p["likes"] += 1
        if li.seen_at is None:
            p["unread"] += 1
        ts = li.liked_at or li.fetched_at
        if ts and (b["newest_activity_at"] is None or ts > b["newest_activity_at"]):
            b["newest_activity_at"] = ts

    # Snapshot fallback for platforms that don't have per-user like records (Instagram is
    # manually-tracked aggregate counts only). For each (post, platform), if there are no
    # PostLike rows but there IS a snapshot, surface the snapshot's count instead. This is
    # how IG ♥ counts appear in the by-post breakdown alongside auto-synced platforms.
    snapshot_subq = (
        select(
            EngagementSnapshot.post_id,
            EngagementSnapshot.platform,
            func.max(EngagementSnapshot.sampled_at).label("max_sampled"),
        )
        .group_by(EngagementSnapshot.post_id, EngagementSnapshot.platform)
        .subquery()
    )
    latest_snaps = db.execute(
        select(EngagementSnapshot)
        .join(
            snapshot_subq,
            (EngagementSnapshot.post_id == snapshot_subq.c.post_id)
            & (EngagementSnapshot.platform == snapshot_subq.c.platform)
            & (EngagementSnapshot.sampled_at == snapshot_subq.c.max_sampled),
        )
    ).scalars().all()
    for snap in latest_snaps:
        post_obj = db.get(Post, snap.post_id)
        if not post_obj:
            continue
        b = _bucket(snap.post_id, post_obj.title, post_obj.flickr_url, post_obj.posted_at)
        p = _platform(b, snap.platform)
        # Only fill from snapshot when we have no per-user data for this platform.
        if p["likes"] == 0 and snap.likes > 0:
            p["likes"] = int(snap.likes)
            ts = snap.sampled_at
            if ts and (b["newest_activity_at"] is None or ts > b["newest_activity_at"]):
                b["newest_activity_at"] = ts

    out = [
        PostActivitySummary(
            post_id=b["post_id"],
            post_title=b["post_title"],
            flickr_url=b["flickr_url"],
            posted_at=b["posted_at"],
            newest_activity_at=b["newest_activity_at"],
            total_likes=sum(p["likes"] for p in b["platforms"].values()),
            total_comments=sum(p["comments"] for p in b["platforms"].values()),
            unread=sum(p["unread"] for p in b["platforms"].values()),
            platforms={k: PlatformBreakdown(**v) for k, v in b["platforms"].items()},
        )
        for b in by_post.values()
    ]
    out.sort(
        key=lambda s: s.newest_activity_at or datetime.min,
        reverse=True,
    )
    return out[:limit]


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, int]:
    """Sum of unread comments + unread likes."""
    n_comments = db.execute(
        select(func.count(PostComment.id)).where(PostComment.seen_at.is_(None))
    ).scalar() or 0
    n_likes = db.execute(
        select(func.count(PostLike.id)).where(PostLike.seen_at.is_(None))
    ).scalar() or 0
    return {"unread": int(n_comments) + int(n_likes)}


@router.post("/mark-all-seen")
def mark_all_seen(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, int]:
    """Mark every unread comment AND like as seen."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cr = db.execute(
        update(PostComment).where(PostComment.seen_at.is_(None)).values(seen_at=now)
    )
    lr = db.execute(
        update(PostLike).where(PostLike.seen_at.is_(None)).values(seen_at=now)
    )
    db.commit()
    return {"marked": int((cr.rowcount or 0) + (lr.rowcount or 0))}


@router.post("/sync-now")
def sync_now(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    """Trigger an on-demand sync — useful when the user wants fresh data without waiting
    for the daily 04:00 UTC cron."""
    try:
        return comments_sync.sync_all(db)
    except Exception as e:
        log.exception("on-demand sync failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"sync failed: {e}")
