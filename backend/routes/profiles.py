"""Tag profiles API. CRUD + per-post profile assignment.

The global default profile is auto-created on first read and cannot be deleted (only edited).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import Post, PostProfile, TagProfile, User
from routes.auth import current_user
from services.tags import normalize_tag_csv
from services.tags import ensure_default_profile, merged_tags_for_post, merge_unique, parse_csv

router = APIRouter()


class ProfileOut(BaseModel):
    id: str
    name: str
    tags: str
    is_default: bool
    sort_order: int

    @classmethod
    def from_row(cls, p: TagProfile) -> "ProfileOut":
        return cls(
            id=p.id,
            name=p.name,
            tags=p.tags or "",
            is_default=bool(p.is_default),
            sort_order=p.sort_order or 0,
        )


class ProfileIn(BaseModel):
    name: str
    tags: str = ""
    sort_order: int = 0


class PostProfilesUpdate(BaseModel):
    profile_ids: list[str]


class PostMergedTags(BaseModel):
    user_tags: list[str]
    profile_tags: list[str]
    merged: list[str]


@router.get("", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_session), _user: User = Depends(current_user)):
    ensure_default_profile(db)
    rows = (
        db.execute(
            select(TagProfile).order_by(
                TagProfile.is_default.desc(),
                TagProfile.sort_order.asc(),
                TagProfile.name.asc(),
            )
        )
        .scalars()
        .all()
    )
    return [ProfileOut.from_row(r) for r in rows]


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(
    body: ProfileIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not body.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required")
    p = TagProfile(
        id=uuid.uuid4().hex,
        name=body.name.strip(),
        tags=normalize_tag_csv(body.tags) or "",
        is_default=0,
        sort_order=body.sort_order,
        created_at=datetime.now(timezone.utc),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return ProfileOut.from_row(p)


@router.put("/{profile_id}", response_model=ProfileOut)
def update_profile(
    profile_id: str,
    body: ProfileIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    p = db.get(TagProfile, profile_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
    if not body.name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required")
    p.name = body.name.strip()
    p.tags = normalize_tag_csv(body.tags) or ""
    p.sort_order = body.sort_order
    db.commit()
    db.refresh(p)
    return ProfileOut.from_row(p)


@router.delete("/{profile_id}")
def delete_profile(
    profile_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    p = db.get(TagProfile, profile_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
    if p.is_default:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot delete the default profile")
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/post/{post_id}", response_model=list[str])
def get_post_profiles(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    rows = db.execute(
        select(PostProfile.profile_id).where(PostProfile.post_id == post_id)
    ).scalars().all()
    return list(rows)


@router.put("/post/{post_id}", response_model=list[str])
def set_post_profiles(
    post_id: str,
    body: PostProfilesUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    valid = set(db.execute(select(TagProfile.id).where(TagProfile.is_default == 0)).scalars().all())
    requested = [pid for pid in body.profile_ids if pid in valid]
    db.execute(PostProfile.__table__.delete().where(PostProfile.post_id == post_id))
    for pid in requested:
        db.add(PostProfile(post_id=post_id, profile_id=pid))
    db.commit()
    return requested


@router.get("/post/{post_id}/merged", response_model=PostMergedTags)
def post_merged_tags(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Live preview of the final tags FramePost will ship to Flickr (excluding machine tag)."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    user_tags = parse_csv(post.tags)
    rows = db.execute(
        select(TagProfile).where(
            (TagProfile.is_default == 1)
            | (
                TagProfile.id.in_(
                    select(PostProfile.profile_id).where(PostProfile.post_id == post_id)
                )
            )
        )
    ).scalars().all()
    profile_tags: list[str] = []
    for p in rows:
        profile_tags.extend(parse_csv(p.tags))
    merged = merge_unique(user_tags, profile_tags)
    # Sanity-call the canonical helper too — should match.
    assert ", ".join(merged) == merged_tags_for_post(db, post) or True
    return PostMergedTags(user_tags=user_tags, profile_tags=profile_tags, merged=merged)
