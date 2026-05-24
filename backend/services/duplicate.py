"""Duplicate detection.

Layer 1: local SHA256 in `posts` (catches re-imports).
Layer 2: cross-check against `flickr_photos` cache populated by the daily sync — looks for
         the `framepost:sha256=<hash>` machine tag we stamped on prior uploads.
Layer 2-soft: title + date_taken + dimensions match for older Flickr photos uploaded
              outside FramePost. This is a *warning*, not a block — surfaces the chance of
              duplicate, but proceeds with the post.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import FlickrPhoto, Post


def find_by_hash(db: Session, sha256: str) -> Post | None:
    return db.execute(select(Post).where(Post.sha256 == sha256)).scalar_one_or_none()


def find_in_flickr_cache(db: Session, sha256: str) -> FlickrPhoto | None:
    pattern = f"%framepost:sha256={sha256}%"
    return db.execute(
        select(FlickrPhoto).where(FlickrPhoto.machine_tags.like(pattern))
    ).scalar_one_or_none()


def find_soft_match(
    db: Session,
    *,
    title: str | None,
    captured_at: datetime | None,
    width: int | None,
    height: int | None,
) -> FlickrPhoto | None:
    if not title or not captured_at or not width or not height:
        return None
    return db.execute(
        select(FlickrPhoto).where(
            FlickrPhoto.title == title,
            FlickrPhoto.date_taken == captured_at,
            FlickrPhoto.width == width,
            FlickrPhoto.height == height,
        )
    ).scalar_one_or_none()
