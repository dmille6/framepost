"""Reels API — create, list, download, edit, regenerate, delete.

Reels are generated asynchronously via FastAPI BackgroundTasks. The create endpoint persists
the Reel + ReelPhoto rows with status='pending', schedules the ffmpeg render in the
background, and returns immediately. Client polls GET /reels/{id} until status='ready' (or
'failed'), then fetches the MP4 via /reels/{id}/mp4.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from database import SessionLocal, get_session
from models import Post, Reel, ReelPhoto, User
from routes.auth import current_user
from services import storage
from services.reel import CropRect, PhotoSegment, ReelGenerationError, generate

log = logging.getLogger("framepost.reels")
router = APIRouter()


# --- schemas ---------------------------------------------------------------

class CropIn(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ReelPhotoIn(BaseModel):
    post_id: str
    position: int = Field(ge=0)
    crop_start: CropIn
    crop_end: CropIn | None = None


class ReelCreate(BaseModel):
    cover_post_id: str
    total_duration_seconds: float = Field(60.0, ge=10.0, le=90.0)
    caption: str | None = None
    photos: list[ReelPhotoIn] = Field(min_length=1, max_length=10)


class ReelPhotoOut(BaseModel):
    post_id: str
    position: int
    crop_start: CropIn
    crop_end: CropIn | None


class ReelOut(BaseModel):
    id: str
    cover_post_id: str
    total_duration_seconds: float
    caption: str | None
    status: str
    error_message: str | None
    mp4_available: bool
    photos: list[ReelPhotoOut]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_reel(cls, reel: Reel, photos: list[ReelPhoto]) -> "ReelOut":
        return cls(
            id=reel.id,
            cover_post_id=reel.cover_post_id,
            total_duration_seconds=reel.total_duration_seconds,
            caption=reel.caption,
            status=reel.status,
            error_message=reel.error_message,
            mp4_available=bool(reel.mp4_path) and Path(reel.mp4_path).exists(),
            photos=[
                ReelPhotoOut(
                    post_id=p.post_id,
                    position=p.position,
                    crop_start=CropIn(**json.loads(p.crop_start_json)),
                    crop_end=CropIn(**json.loads(p.crop_end_json)) if p.crop_end_json else None,
                )
                for p in sorted(photos, key=lambda x: x.position)
            ],
            created_at=reel.created_at,
            updated_at=reel.updated_at,
        )


class ReelPatch(BaseModel):
    caption: str | None = None
    total_duration_seconds: float | None = Field(None, ge=10.0, le=90.0)
    photos: list[ReelPhotoIn] | None = Field(None, min_length=1, max_length=10)
    cover_post_id: str | None = None


# --- helpers ---------------------------------------------------------------

def _load_photos(db: Session, reel_id: str) -> list[ReelPhoto]:
    return list(db.execute(
        select(ReelPhoto).where(ReelPhoto.reel_id == reel_id)
    ).scalars())


def _validate_posts_exist(db: Session, post_ids: set[str]) -> None:
    found = set(db.execute(
        select(Post.id).where(Post.id.in_(post_ids))
    ).scalars())
    missing = post_ids - found
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown post_id(s): {', '.join(sorted(missing))}",
        )


def _build_segments(db: Session, reel: Reel, photos: list[ReelPhoto]) -> list[PhotoSegment]:
    per_photo_duration = reel.total_duration_seconds / max(1, len(photos))
    segments: list[PhotoSegment] = []
    for p in sorted(photos, key=lambda x: x.position):
        post = db.get(Post, p.post_id)
        if not post or not post.original_path:
            raise ReelGenerationError(f"post {p.post_id} has no source path")
        src = Path(post.original_path)
        if not src.exists():
            raise ReelGenerationError(f"source file missing: {src}")
        segments.append(PhotoSegment(
            source_path=src,
            duration_s=per_photo_duration,
            crop_start=CropRect(**json.loads(p.crop_start_json)),
            crop_end=CropRect(**json.loads(p.crop_end_json)) if p.crop_end_json else None,
        ))
    return segments


def _run_generation(reel_id: str) -> None:
    """Background task — opens its own DB session, runs ffmpeg, updates status."""
    db = SessionLocal()
    try:
        reel = db.get(Reel, reel_id)
        if not reel:
            log.error("reel %s vanished before generation started", reel_id)
            return
        photos = _load_photos(db, reel_id)
        try:
            segments = _build_segments(db, reel, photos)
            out_path = storage.reel_path(reel_id)
            generate(segments, out_path)
            reel.status = "ready"
            reel.mp4_path = str(out_path)
            reel.error_message = None
            reel.updated_at = datetime.now(timezone.utc)
            db.commit()
            log.info("reel %s generated (%d bytes)", reel_id, out_path.stat().st_size)
        except (ReelGenerationError, Exception) as e:
            log.exception("reel %s generation failed", reel_id)
            reel.status = "failed"
            reel.error_message = str(e)[:1000]
            reel.updated_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


# --- routes ----------------------------------------------------------------

@router.post("", response_model=ReelOut)
def create_reel(
    body: ReelCreate,
    bg: BackgroundTasks,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post_ids = {p.post_id for p in body.photos} | {body.cover_post_id}
    _validate_posts_exist(db, post_ids)

    reel_id = uuid.uuid4().hex
    reel = Reel(
        id=reel_id,
        cover_post_id=body.cover_post_id,
        total_duration_seconds=body.total_duration_seconds,
        caption=body.caption,
        status="pending",
    )
    db.add(reel)
    for p in body.photos:
        db.add(ReelPhoto(
            reel_id=reel_id,
            position=p.position,
            post_id=p.post_id,
            crop_start_json=json.dumps(p.crop_start.model_dump()),
            crop_end_json=json.dumps(p.crop_end.model_dump()) if p.crop_end else None,
        ))
    db.commit()
    db.refresh(reel)
    photos = _load_photos(db, reel_id)

    bg.add_task(_run_generation, reel_id)
    return ReelOut.from_reel(reel, photos)


@router.get("", response_model=list[ReelOut])
def list_reels(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    reels = list(db.execute(
        select(Reel).order_by(Reel.created_at.desc())
    ).scalars())
    out: list[ReelOut] = []
    for r in reels:
        out.append(ReelOut.from_reel(r, _load_photos(db, r.id)))
    return out


@router.get("/{reel_id}", response_model=ReelOut)
def get_reel(
    reel_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    reel = db.get(Reel, reel_id)
    if not reel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reel not found")
    return ReelOut.from_reel(reel, _load_photos(db, reel_id))


@router.get("/{reel_id}/mp4")
def download_reel(
    reel_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    reel = db.get(Reel, reel_id)
    if not reel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reel not found")
    if reel.status != "ready" or not reel.mp4_path:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"reel not ready (status: {reel.status})",
        )
    path = Path(reel.mp4_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mp4 file missing on disk")
    filename = f"reel-{reel.created_at.strftime('%Y%m%d-%H%M')}.mp4"
    return FileResponse(path, media_type="video/mp4", filename=filename)


@router.post("/{reel_id}/regenerate", response_model=ReelOut)
def regenerate_reel(
    reel_id: str,
    bg: BackgroundTasks,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    reel = db.get(Reel, reel_id)
    if not reel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reel not found")
    if reel.status == "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "reel is already being generated")
    reel.status = "pending"
    reel.error_message = None
    reel.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reel)
    bg.add_task(_run_generation, reel_id)
    return ReelOut.from_reel(reel, _load_photos(db, reel_id))


@router.patch("/{reel_id}", response_model=ReelOut)
def update_reel(
    reel_id: str,
    body: ReelPatch,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    reel = db.get(Reel, reel_id)
    if not reel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reel not found")

    if body.caption is not None:
        reel.caption = body.caption
    if body.total_duration_seconds is not None:
        reel.total_duration_seconds = body.total_duration_seconds
    if body.cover_post_id is not None:
        _validate_posts_exist(db, {body.cover_post_id})
        reel.cover_post_id = body.cover_post_id
    if body.photos is not None:
        _validate_posts_exist(db, {p.post_id for p in body.photos})
        db.execute(delete(ReelPhoto).where(ReelPhoto.reel_id == reel_id))
        for p in body.photos:
            db.add(ReelPhoto(
                reel_id=reel_id,
                position=p.position,
                post_id=p.post_id,
                crop_start_json=json.dumps(p.crop_start.model_dump()),
                crop_end_json=json.dumps(p.crop_end.model_dump()) if p.crop_end else None,
            ))

    reel.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reel)
    return ReelOut.from_reel(reel, _load_photos(db, reel_id))


@router.delete("/{reel_id}")
def delete_reel(
    reel_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    reel = db.get(Reel, reel_id)
    if not reel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reel not found")
    if reel.mp4_path:
        path = Path(reel.mp4_path)
        if path.exists():
            path.unlink()
    db.execute(delete(ReelPhoto).where(ReelPhoto.reel_id == reel_id))
    db.delete(reel)
    db.commit()
    return {"ok": True}
