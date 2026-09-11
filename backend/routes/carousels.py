"""Carousel grouping — turn several posts into one Instagram post.

Mounted at /api/carousels rather than under /api/posts so it can never be swallowed by
/api/posts/{post_id}. See tests/test_route_shadowing.py for why that matters.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import Post, User
from routes.auth import current_user
from routes.posts import PostOut
from services import carousel as carousel_svc, events

log = logging.getLogger("framepost.carousels")

router = APIRouter()


class CarouselFrame(BaseModel):
    post_id: str
    position: int
    title: str | None = None
    original_filename: str | None = None
    width: int | None = None
    height: int | None = None


class CarouselOut(BaseModel):
    """A grouping result. `errors` non-empty means nothing was changed."""
    carousel_id: str | None = None
    frames: list[CarouselFrame] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CarouselCreate(BaseModel):
    post_ids: list[str]
    lead_id: str
    # The dialog validates as you pick, before anything is committed.
    dry_run: bool = False


class CarouselReorder(BaseModel):
    ordered_ids: list[str]


def _frames(db: Session, carousel_id: str) -> list[CarouselFrame]:
    return [
        CarouselFrame(
            post_id=p.id,
            position=p.carousel_position or 0,
            title=p.title,
            original_filename=p.original_filename,
            width=p.width,
            height=p.height,
        )
        for p in carousel_svc.members(db, carousel_id)
    ]


@router.post("", response_model=CarouselOut)
def create_carousel(
    body: CarouselCreate,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Group posts into a carousel, or (dry_run) report what's wrong with grouping them.

    Errors come back in the body rather than as a 4xx: the dialog shows them as you
    build the selection, and a 400 per keystroke is not a validation strategy.
    """
    posts = list(db.execute(
        select(Post).where(Post.id.in_(body.post_ids))
    ).scalars().all())
    missing = set(body.post_ids) - {p.id for p in posts}
    if missing:
        return CarouselOut(errors=[f"{len(missing)} selected photo(s) no longer exist."])

    # Preserve the caller's order — the dialog's arrangement is the carousel's order.
    by_id = {p.id: p for p in posts}
    ordered = [by_id[pid] for pid in body.post_ids if pid in by_id]

    errors = carousel_svc.validate(db, ordered, lead_id=body.lead_id)
    if errors or body.dry_run:
        return CarouselOut(errors=errors)

    carousel_id = carousel_svc.group(db, ordered, lead_id=body.lead_id)
    for p in carousel_svc.members(db, carousel_id):
        events.log_event(
            db,
            post_id=p.id,
            event_type="carousel_grouped",
            actor=user.username,
            details={
                "carousel_id": carousel_id,
                "position": p.carousel_position,
                "frames": len(ordered),
            },
        )
    db.commit()
    log.info("carousel %s grouped from %d posts", carousel_id[:8], len(ordered))
    return CarouselOut(carousel_id=carousel_id, frames=_frames(db, carousel_id))


@router.get("/{carousel_id}", response_model=CarouselOut)
def get_carousel(
    carousel_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    frames = _frames(db, carousel_id)
    if not frames:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "carousel not found")
    return CarouselOut(carousel_id=carousel_id, frames=frames)


@router.get("/{carousel_id}/posts", response_model=list[PostOut])
def get_carousel_posts(
    carousel_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Every frame in full, in slide order.

    CarouselOut carries enough to draw a list; the crop editor needs the whole post —
    ig_fit, the crop rect, the focal point. Deliberately indifferent to post status: a
    carousel's frames go `posted` when it publishes and then vanish from the draft
    queue, which used to leave no way to fix a crop after the fact.
    """
    frames = carousel_svc.members(db, carousel_id)
    if not frames:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "carousel not found")
    return [PostOut.model_validate(p) for p in frames]


@router.patch("/{carousel_id}", response_model=CarouselOut)
def reorder_carousel(
    carousel_id: str,
    body: CarouselReorder,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Reorder the frames. The first id becomes the cover, and Instagram crops the whole
    set to the cover's ratio — which is why grouping refuses mixed ratios up front."""
    if not carousel_svc.members(db, carousel_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "carousel not found")
    carousel_svc.reorder(db, carousel_id, body.ordered_ids)
    return CarouselOut(carousel_id=carousel_id, frames=_frames(db, carousel_id))


@router.delete("/{carousel_id}")
def ungroup_carousel(
    carousel_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Break it back into ordinary posts.

    Scheduled times are left aligned rather than re-scattered — silently rearranging
    someone's queue on an ungroup is a worse surprise than a few posts sharing a slot.
    """
    members = carousel_svc.members(db, carousel_id)
    if not members:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "carousel not found")
    ids = [p.id for p in members]
    freed = carousel_svc.ungroup(db, carousel_id)
    for pid in ids:
        events.log_event(
            db,
            post_id=pid,
            event_type="carousel_ungrouped",
            actor=user.username,
            details={"carousel_id": carousel_id},
        )
    db.commit()
    return {"ok": True, "freed": freed}
