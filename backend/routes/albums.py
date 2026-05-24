"""Albums API. List synced albums, manual sync trigger, set albums on a post."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import Album, Post, PostAlbum, User
from routes.auth import current_user
from services import flickr_sync
from services.platforms import flickr

router = APIRouter()


class AlbumOut(BaseModel):
    id: str
    flickr_album_id: str | None
    name: str
    description: str | None
    photo_count: int
    last_synced_at: datetime | None

    class Config:
        from_attributes = True


class PostAlbumsUpdate(BaseModel):
    album_ids: list[str]


@router.get("", response_model=list[AlbumOut])
def list_albums(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    rows = db.execute(select(Album).order_by(Album.name.asc())).scalars().all()
    return [AlbumOut.model_validate(r) for r in rows]


@router.post("/sync")
def trigger_sync(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    try:
        count = flickr_sync.sync_albums(db)
    except flickr.FlickrError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Flickr sync failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"synced": count}


@router.get("/post/{post_id}", response_model=list[str])
def get_post_albums(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    rows = db.execute(select(PostAlbum.album_id).where(PostAlbum.post_id == post_id)).scalars().all()
    return list(rows)


@router.put("/post/{post_id}", response_model=list[str])
def set_post_albums(
    post_id: str,
    body: PostAlbumsUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if not db.get(Post, post_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    valid_ids = {
        r for r in db.execute(select(Album.id)).scalars().all()
    }
    requested = [a for a in body.album_ids if a in valid_ids]
    db.execute(
        PostAlbum.__table__.delete().where(PostAlbum.post_id == post_id)
    )
    for aid in requested:
        db.add(PostAlbum(post_id=post_id, album_id=aid))
    db.commit()
    return requested
