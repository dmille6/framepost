"""Published history API. Returns posts that have left the queue (posted/late/missed/failed)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database import get_session
from models import PlatformCredential, Post, PostComment, PostEvent, PostPlatform, User
from routes.auth import current_user
from routes.posts import PostOut

router = APIRouter()

TERMINAL_STATUSES = ("posted", "late", "missed", "failed")


class HistoryPost(BaseModel):
    id: str
    title: str | None
    description: str | None
    tags: str | None
    original_filename: str | None
    width: int | None
    height: int | None
    captured_at: datetime | None
    camera_make: str | None
    camera_model: str | None
    lens: str | None
    iso: int | None
    shutter_speed: str | None
    aperture: float | None
    status: str
    scheduled_at: datetime | None
    posted_at: datetime | None
    flickr_photo_id: str | None
    flickr_url: str | None
    error_message: str | None
    retry_count: int
    posted_to_instagram_at: datetime | None = None
    reddit_posted_at: datetime | None = None

    class Config:
        from_attributes = True


class TimelineEvent(BaseModel):
    id: int
    event_type: str
    actor: str
    details: dict[str, Any] | None
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[HistoryPost])
def list_history(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    limit: int = Query(200, le=500),
    offset: int = Query(0, ge=0),
):
    statuses = [s for s in (status_filter or list(TERMINAL_STATUSES)) if s in TERMINAL_STATUSES]
    if not statuses:
        statuses = list(TERMINAL_STATUSES)
    stmt = select(Post).where(Post.status.in_(statuses))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Post.title.ilike(like),
                Post.tags.ilike(like),
                Post.camera_model.ilike(like),
                Post.lens.ilike(like),
            )
        )
    stmt = stmt.order_by(Post.posted_at.desc().nulls_last(), Post.scheduled_at.desc().nulls_last())
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [HistoryPost.model_validate(r) for r in rows]


@router.get("/{post_id}", response_model=PostOut)
def history_detail(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    return PostOut.from_post(post)


class PostPlatformOut(BaseModel):
    platform: str
    account_name: str | None
    instance_url: str | None
    status: str
    remote_id: str | None
    remote_url: str | None
    posted_at: datetime | None
    error_message: str | None
    retry_count: int


@router.get("/{post_id}/platforms", response_model=list[PostPlatformOut])
def post_platforms(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Per-platform status for a single post. Includes Flickr (synthesized from Post columns)
    so the UI can present a unified 'where did this go' view."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    out: list[PostPlatformOut] = []

    # Synthesize Flickr from Post columns since it predates the post_platforms table.
    if post.flickr_photo_id or post.status in ("posted", "late", "failed", "missed"):
        out.append(
            PostPlatformOut(
                platform="flickr",
                account_name=None,
                instance_url=None,
                status=post.status if post.flickr_photo_id else "failed" if post.status == "failed" else post.status,
                remote_id=post.flickr_photo_id,
                remote_url=post.flickr_url,
                posted_at=post.posted_at,
                error_message=post.error_message if not post.flickr_photo_id else None,
                retry_count=post.retry_count or 0,
            )
        )

    rows = db.execute(
        select(PostPlatform, PlatformCredential)
        .join(PlatformCredential, PlatformCredential.id == PostPlatform.platform_id)
        .where(PostPlatform.post_id == post_id)
    ).all()
    for pp, cred in rows:
        out.append(
            PostPlatformOut(
                platform=cred.platform,
                account_name=cred.account_name,
                instance_url=cred.instance_url,
                status=pp.status,
                remote_id=pp.remote_id,
                remote_url=pp.remote_url,
                posted_at=pp.posted_at,
                error_message=pp.error_message,
                retry_count=pp.retry_count or 0,
            )
        )

    # Manual-tracking platforms (no API integration). When the user clicks "Mark posted" on
    # the IG or Reddit copy-paste tab, we record a timestamp on Post — surface those as chips
    # so the unified 'where did this go' view is complete.
    if post.posted_to_instagram_at:
        out.append(
            PostPlatformOut(
                platform="instagram",
                account_name=None,
                instance_url=None,
                status="posted",
                remote_id=None,
                remote_url=None,
                posted_at=post.posted_to_instagram_at,
                error_message=None,
                retry_count=0,
            )
        )
    if post.reddit_posted_at:
        out.append(
            PostPlatformOut(
                platform="reddit",
                account_name=None,
                instance_url=None,
                status="posted",
                remote_id=None,
                remote_url=None,
                posted_at=post.reddit_posted_at,
                error_message=None,
                retry_count=0,
            )
        )
    return out


class CommentOut(BaseModel):
    id: int
    platform: str
    author_handle: str | None
    author_display_name: str | None
    author_url: str | None
    body: str
    posted_at: datetime | None
    fetched_at: datetime
    seen_at: datetime | None


@router.get("/{post_id}/comments", response_model=list[CommentOut])
def post_comments_list(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Comments fetched from Flickr/Bluesky/Pixelfed for one post. Newest first."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    rows = db.execute(
        select(PostComment)
        .where(PostComment.post_id == post_id)
        .order_by(PostComment.posted_at.desc().nulls_last(), PostComment.fetched_at.desc())
    ).scalars().all()
    return [
        CommentOut(
            id=r.id,
            platform=r.platform,
            author_handle=r.author_handle,
            author_display_name=r.author_display_name,
            author_url=r.author_url,
            body=r.body,
            posted_at=r.posted_at,
            fetched_at=r.fetched_at,
            seen_at=r.seen_at,
        )
        for r in rows
    ]


@router.get("/{post_id}/events", response_model=list[TimelineEvent])
def post_events(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    events = db.execute(
        select(PostEvent)
        .where(PostEvent.post_id == post_id)
        .order_by(PostEvent.created_at.asc(), PostEvent.id.asc())
    ).scalars().all()
    out: list[TimelineEvent] = []
    for ev in events:
        details: dict[str, Any] | None = None
        if ev.details:
            try:
                details = json.loads(ev.details)
            except (TypeError, ValueError):
                details = {"raw": ev.details}
        out.append(
            TimelineEvent(
                id=ev.id,
                event_type=ev.event_type,
                actor=ev.actor,
                details=details,
                created_at=ev.created_at,
            )
        )
    return out
