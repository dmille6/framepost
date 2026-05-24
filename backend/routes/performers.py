"""Performers API — list, create, edit, delete + per-post tag/untag.

Performers are people you photograph repeatedly. They store one display_name and one
instagram_handle. When tagged on a post, caption builders auto-insert @-mentions and
hashtags at publish time.

Endpoints:
  GET    /api/performers              — list (with optional q= prefix search)
  POST   /api/performers              — create
  PATCH  /api/performers/{id}         — update display_name and/or instagram_handle
  DELETE /api/performers/{id}         — delete (cascades to post_performers)
  GET    /api/posts/{post_id}/performers   — list tagged performers for a post
  PUT    /api/posts/{post_id}/performers   — replace the tagged-performers list for a post
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from database import get_session
from models import Performer, Post, PostPerformer, User
from routes.auth import current_user

log = logging.getLogger("framepost.performers")
router = APIRouter()


# Normalize handles: strip leading @, strip whitespace, lowercase. IG handles are
# case-insensitive and only allow [a-z0-9._].
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


class PerformerIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    instagram_handle: str | None = Field(None, max_length=100)

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("display_name cannot be empty")
        return v


class PerformerPatch(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=200)
    instagram_handle: str | None = Field(None, max_length=100)


class PerformerOut(BaseModel):
    id: str
    display_name: str
    instagram_handle: str | None
    usage_count: int
    created_at: datetime
    updated_at: datetime


def _to_out(p: Performer, usage_count: int = 0) -> PerformerOut:
    return PerformerOut(
        id=p.id,
        display_name=p.display_name,
        instagram_handle=p.instagram_handle,
        usage_count=usage_count,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=list[PerformerOut])
def list_performers(
    q: str | None = Query(None, max_length=200),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """List all performers. Optional q= filters by case-insensitive substring on
    display_name OR instagram_handle. Always returns usage_count (number of posts
    tagged with each performer) so the UI can show 'remove safely?' warnings."""
    stmt = select(Performer)
    if q:
        like = f"%{q.strip().lower().lstrip('@')}%"
        stmt = stmt.where(
            (func.lower(Performer.display_name).like(like))
            | (func.lower(Performer.instagram_handle).like(like))
        )
    stmt = stmt.order_by(func.lower(Performer.display_name))
    rows = list(db.execute(stmt).scalars())

    # One small query for usage counts. Keeps the list endpoint cheap up to ~thousands
    # of performers — well above realistic ceilings.
    counts: dict[str, int] = {}
    if rows:
        count_rows = db.execute(
            select(PostPerformer.performer_id, func.count())
            .where(PostPerformer.performer_id.in_([r.id for r in rows]))
            .group_by(PostPerformer.performer_id)
        ).all()
        counts = {pid: n for pid, n in count_rows}

    return [_to_out(p, usage_count=counts.get(p.id, 0)) for p in rows]


@router.post("", response_model=PerformerOut)
def create_performer(
    body: PerformerIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    name = body.display_name.strip()
    handle = _normalize_handle(body.instagram_handle)

    # Case-insensitive uniqueness check (the UNIQUE constraint is case-sensitive in SQLite).
    existing = db.execute(
        select(Performer).where(func.lower(Performer.display_name) == name.lower())
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a performer named '{existing.display_name}' already exists",
        )

    p = Performer(
        id=uuid.uuid4().hex,
        display_name=name,
        instagram_handle=handle,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(p, usage_count=0)


@router.patch("/{performer_id}", response_model=PerformerOut)
def update_performer(
    performer_id: str,
    body: PerformerPatch,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    p = db.get(Performer, performer_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "performer not found")

    if body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "display_name cannot be empty")
        # Case-insensitive uniqueness, excluding self.
        conflict = db.execute(
            select(Performer)
            .where(func.lower(Performer.display_name) == name.lower())
            .where(Performer.id != performer_id)
        ).scalar_one_or_none()
        if conflict:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"a performer named '{conflict.display_name}' already exists",
            )
        p.display_name = name

    if body.instagram_handle is not None:
        # Empty string clears the handle; otherwise normalize.
        p.instagram_handle = _normalize_handle(body.instagram_handle) if body.instagram_handle.strip() else None

    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(p)

    usage = db.execute(
        select(func.count()).where(PostPerformer.performer_id == performer_id)
    ).scalar_one()
    return _to_out(p, usage_count=int(usage or 0))


@router.delete("/{performer_id}")
def delete_performer(
    performer_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    p = db.get(Performer, performer_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "performer not found")
    db.delete(p)  # cascades to post_performers via FK
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-post tagging
# ---------------------------------------------------------------------------

class TagPerformersBody(BaseModel):
    performer_ids: list[str] = Field(default_factory=list, max_length=50)


@router.get("/by-post/{post_id}", response_model=list[PerformerOut])
def list_post_performers(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Return the performers tagged on a post in insertion order (position ASC)."""
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    rows = db.execute(
        select(Performer, PostPerformer.position)
        .join(PostPerformer, PostPerformer.performer_id == Performer.id)
        .where(PostPerformer.post_id == post_id)
        .order_by(PostPerformer.position)
    ).all()
    return [_to_out(p, usage_count=0) for p, _ in rows]


@router.put("/by-post/{post_id}", response_model=list[PerformerOut])
def set_post_performers(
    post_id: str,
    body: TagPerformersBody,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Replace the entire performer-tag list for a post in one call. Order matters —
    captions render performers in this order."""
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    # Validate referenced performers exist.
    if body.performer_ids:
        found = {
            pid for (pid,) in db.execute(
                select(Performer.id).where(Performer.id.in_(body.performer_ids))
            ).all()
        }
        missing = [pid for pid in body.performer_ids if pid not in found]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown performer_id(s): {', '.join(missing)}",
            )

    db.execute(delete(PostPerformer).where(PostPerformer.post_id == post_id))
    for i, pid in enumerate(body.performer_ids):
        db.add(PostPerformer(post_id=post_id, performer_id=pid, position=i))
    db.commit()

    # Return the new tag list (in position order) so the caller has the canonical state.
    rows = db.execute(
        select(Performer)
        .join(PostPerformer, PostPerformer.performer_id == Performer.id)
        .where(PostPerformer.post_id == post_id)
        .order_by(PostPerformer.position)
    ).scalars().all()
    return [_to_out(p, usage_count=0) for p in rows]
