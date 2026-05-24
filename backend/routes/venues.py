"""Venues API — list, create, edit, delete.

Mirrors routes/performers.py exactly. A venue is a lightweight entity (display_name +
optional instagram_handle) that the caption builder uses to auto-insert an @-mention
on platforms that resolve them (IG, etc.) and a #hashtag everywhere. Venues drive
audience growth via the venue-repost dynamic — NOLA venues like Hi-Ho Lounge regularly
reshare performance photos from their nights when tagged.

Photos reference a venue via posts.venue_id (nullable FK with ON DELETE SET NULL),
not a junction table — a photo is never at more than one venue at a time.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_session
from models import Post, User, Venue
from routes.auth import current_user

log = logging.getLogger("framepost.venues")
router = APIRouter()


_HANDLE_OK = re.compile(r"^[a-z0-9._]+$")


def _normalize_handle(raw: str | None) -> str | None:
    if not raw:
        return None
    h = raw.strip().lstrip("@").lower()
    if not h:
        return None
    if not _HANDLE_OK.match(h):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid Instagram handle '{raw}' — letters, numbers, dots, and underscores only",
        )
    return h


class VenueIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    instagram_handle: str | None = Field(None, max_length=100)

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("display_name cannot be empty")
        return v


class VenuePatch(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=200)
    instagram_handle: str | None = Field(None, max_length=100)


class VenueOut(BaseModel):
    id: str
    display_name: str
    instagram_handle: str | None
    usage_count: int
    created_at: datetime
    updated_at: datetime


def _to_out(v: Venue, usage_count: int = 0) -> VenueOut:
    return VenueOut(
        id=v.id,
        display_name=v.display_name,
        instagram_handle=v.instagram_handle,
        usage_count=usage_count,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


@router.get("", response_model=list[VenueOut])
def list_venues(
    q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    stmt = select(Venue)
    if q:
        like = f"%{q.strip().lower().lstrip('@')}%"
        stmt = stmt.where(
            (func.lower(Venue.display_name).like(like))
            | (func.lower(Venue.instagram_handle).like(like))
        )
    stmt = stmt.order_by(func.lower(Venue.display_name))
    rows = list(db.execute(stmt).scalars())

    counts: dict[str, int] = {}
    if rows:
        count_rows = db.execute(
            select(Post.venue_id, func.count())
            .where(Post.venue_id.in_([r.id for r in rows]))
            .group_by(Post.venue_id)
        ).all()
        counts = {vid: n for vid, n in count_rows}

    return [_to_out(v, usage_count=counts.get(v.id, 0)) for v in rows]


@router.post("", response_model=VenueOut)
def create_venue(
    body: VenueIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    name = body.display_name.strip()
    handle = _normalize_handle(body.instagram_handle)

    existing = db.execute(
        select(Venue).where(func.lower(Venue.display_name) == name.lower())
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a venue named '{existing.display_name}' already exists",
        )

    v = Venue(
        id=uuid.uuid4().hex,
        display_name=name,
        instagram_handle=handle,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return _to_out(v, usage_count=0)


@router.patch("/{venue_id}", response_model=VenueOut)
def update_venue(
    venue_id: str,
    body: VenuePatch,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    v = db.get(Venue, venue_id)
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "venue not found")

    if body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "display_name cannot be empty")
        conflict = db.execute(
            select(Venue)
            .where(func.lower(Venue.display_name) == name.lower())
            .where(Venue.id != venue_id)
        ).scalar_one_or_none()
        if conflict:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"a venue named '{conflict.display_name}' already exists",
            )
        v.display_name = name

    if body.instagram_handle is not None:
        v.instagram_handle = _normalize_handle(body.instagram_handle) if body.instagram_handle.strip() else None

    v.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(v)

    usage = db.execute(
        select(func.count()).where(Post.venue_id == venue_id)
    ).scalar_one()
    return _to_out(v, usage_count=int(usage or 0))


@router.delete("/{venue_id}")
def delete_venue(
    venue_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    v = db.get(Venue, venue_id)
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "venue not found")
    db.delete(v)  # SET NULL cascade on posts.venue_id
    db.commit()
    return {"ok": True}
