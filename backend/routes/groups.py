"""Groups CRUD + per-post group selection.

Groups are user-curated (manual entry); we don't auto-discover them from Flickr because
the brief explicitly treats group submission as deliberate, not a filing system.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import Group, Post, PostGroup, User
from routes.auth import current_user
from services.platforms import flickr

log = logging.getLogger("framepost.groups")
router = APIRouter()


def _normalize_group_id(db: Session, raw: str | None) -> str | None:
    """Accept either a bare NSID (512395@N21), a full Flickr group URL, or a vanity slug,
    and store the resolved NSID. Lookup goes through Flickr's API for vanity URLs, so the
    user must be connected to Flickr before adding groups by URL/slug.

    Returns None if the input is None/empty so callers can preserve the no-id state.
    """
    if not raw:
        return None
    try:
        return flickr.resolve_group_id(db, raw)
    except flickr.FlickrError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


class GroupOut(BaseModel):
    id: str
    flickr_group_id: str | None
    name: str
    category: str | None
    daily_limit: int | None
    content_notes: str | None
    no_watermark: bool
    default_enabled: bool

    class Config:
        from_attributes = True

    @classmethod
    def from_row(cls, g: Group) -> "GroupOut":
        return cls(
            id=g.id,
            flickr_group_id=g.flickr_group_id,
            name=g.name,
            category=g.category,
            daily_limit=g.daily_limit,
            content_notes=g.content_notes,
            no_watermark=bool(g.no_watermark),
            default_enabled=bool(g.default_enabled),
        )


class GroupIn(BaseModel):
    flickr_group_id: str | None = None
    name: str
    category: str | None = None
    daily_limit: int | None = None
    content_notes: str | None = None
    no_watermark: bool = False
    default_enabled: bool = False


class PostGroupsUpdate(BaseModel):
    group_ids: list[str]


@router.get("", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_session), _user: User = Depends(current_user)):
    rows = db.execute(select(Group).order_by(Group.category.asc().nulls_last(), Group.name.asc())).scalars().all()
    return [GroupOut.from_row(r) for r in rows]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    nsid = _normalize_group_id(db, body.flickr_group_id)
    g = Group(
        id=uuid.uuid4().hex,
        flickr_group_id=nsid,
        name=body.name,
        category=body.category,
        daily_limit=body.daily_limit,
        content_notes=body.content_notes,
        no_watermark=1 if body.no_watermark else 0,
        default_enabled=1 if body.default_enabled else 0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return GroupOut.from_row(g)


@router.put("/{group_id}", response_model=GroupOut)
def update_group(
    group_id: str,
    body: GroupIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")
    g.flickr_group_id = _normalize_group_id(db, body.flickr_group_id)
    g.name = body.name
    g.category = body.category
    g.daily_limit = body.daily_limit
    g.content_notes = body.content_notes
    g.no_watermark = 1 if body.no_watermark else 0
    g.default_enabled = 1 if body.default_enabled else 0
    db.commit()
    db.refresh(g)
    return GroupOut.from_row(g)


@router.delete("/{group_id}")
def delete_group(
    group_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")
    db.delete(g)
    db.commit()
    return {"ok": True}


@router.get("/post/{post_id}", response_model=list[str])
def get_post_groups(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    rows = db.execute(
        select(PostGroup.group_id).where(PostGroup.post_id == post_id)
    ).scalars().all()
    return list(rows)


@router.put("/post/{post_id}", response_model=list[str])
def set_post_groups(
    post_id: str,
    body: PostGroupsUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    valid_ids = set(db.execute(select(Group.id)).scalars().all())
    requested = [g for g in body.group_ids if g in valid_ids]

    # Replace pending submissions only — preserve any that already submitted/failed.
    db.execute(
        PostGroup.__table__.delete().where(
            PostGroup.post_id == post_id,
            PostGroup.status == "pending",
        )
    )
    existing_after = set(
        db.execute(select(PostGroup.group_id).where(PostGroup.post_id == post_id)).scalars().all()
    )
    for gid in requested:
        if gid in existing_after:
            continue
        db.add(
            PostGroup(
                id=uuid.uuid4().hex,
                post_id=post_id,
                group_id=gid,
                status="pending",
            )
        )
    db.commit()
    return requested
