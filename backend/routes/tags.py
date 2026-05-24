"""Trending tags API + previously-used tag history. Read aggregated trending tags +
manual refresh, plus tag autocomplete data drawn from user's existing posts.tags."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import AppConfig, Post, TagProfile, User
from routes.auth import current_user
from services import trending
from services.platforms import flickr

log = logging.getLogger("framepost.tags_api")
router = APIRouter()


class TrendingTagOut(BaseModel):
    tag: str
    score: float
    seeds: list[str]


class TrendingResponse(BaseModel):
    tags: list[TrendingTagOut]
    seeds: list[str]
    last_refresh: str | None


class SeedsUpdate(BaseModel):
    seeds: list[str]


@router.get("/trending", response_model=TrendingResponse)
def get_trending(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    seeds = trending.get_seed_tags(db)
    last = db.execute(
        select(AppConfig).where(AppConfig.key == "trending_last_refresh")
    ).scalar_one_or_none()
    return TrendingResponse(
        tags=[TrendingTagOut(**t) for t in trending.list_trending(db, limit=60)],
        seeds=seeds,
        last_refresh=last.value if last and last.value else None,
    )


@router.put("/trending/seeds", response_model=TrendingResponse)
def put_seeds(
    body: SeedsUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if len(body.seeds) > 30:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "max 30 seed tags (Flickr API limits + signal-to-noise)"
        )
    trending.set_seed_tags(db, body.seeds)
    return get_trending(db, _user)  # type: ignore[arg-type]


@router.post("/trending/refresh", response_model=dict[str, Any])
def refresh_now(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    try:
        return trending.refresh(db)
    except flickr.FlickrError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Flickr error: {e}")
    except RuntimeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# --- Tag autocomplete (Phase 7D) ---


class TagUsage(BaseModel):
    tag: str
    count: int


@router.get("/used", response_model=list[TagUsage])
def list_used_tags(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    limit: int = Query(default=300, ge=1, le=1000),
):
    """Return the user's previously-used tags (from posts + profiles), sorted by frequency.
    Powers the autocomplete dropdown in the metadata editor."""
    counter: Counter[str] = Counter()
    for raw in db.execute(select(Post.tags)).scalars().all():
        if not raw:
            continue
        for t in raw.split(","):
            t = t.strip()
            if t:
                counter[t.lower()] += 1
    for raw in db.execute(select(TagProfile.tags)).scalars().all():
        if not raw:
            continue
        for t in raw.split(","):
            t = t.strip()
            if t:
                counter[t.lower()] += 1
    return [TagUsage(tag=t, count=c) for t, c in counter.most_common(limit)]
