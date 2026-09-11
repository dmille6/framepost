"""Carousels: several posts publishing as one Instagram post.

A carousel is a grouping over posts, not a new kind of post. Every frame keeps its own
Flickr upload — Flickr has no carousel and the archive there should stay one photo per
photo — so members remain ordinary posts and the one at position 0 owns the Instagram
post for the group.

That choice is what keeps this small: the scheduler, the retry policy, the failure
taxonomy and the engagement sync are untouched, because it is still exactly one
post_platforms row doing the publishing.

The rules below are validation rather than resolution. A carousel is a set of frames of
the same performer at the same show, so caption, performers and platforms already agree
by construction; the checks catch a mis-grouping instead of silently reconciling one.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Post, PostPerformer
from services import ig_variant
from services.platforms import instagram

# Platforms where a carousel collapses to one post. Flickr never does — one photo per
# photo is the point of the archive.
#
# Bluesky and Pixelfed cap at four images each, so a bigger set is threaded rather than
# truncated: the caption and hashtags on the root, continuations replying to it. Losing
# frames silently is worse than an extra post, and three top-level posts carrying the
# same caption would read as bot output on a chronological feed.
CAROUSEL_PLATFORMS = frozenset({"instagram", "bluesky", "pixelfed"})

# post_platforms.status for a member's Instagram row. The row exists only to hold that
# frame's staging id so ensure_staged() works unchanged; it is never published on its
# own and must never read as pending.
MEMBER_STATUS = "carousel_member"

MIN_MEMBERS = 2
MAX_MEMBERS = instagram.MAX_CAROUSEL


def is_in_carousel(post: Post) -> bool:
    return bool(post.carousel_id)


def is_lead(post: Post) -> bool:
    return bool(post.carousel_id) and (post.carousel_position or 0) == 0


def is_member(post: Post) -> bool:
    """In a carousel but not the one that publishes it."""
    return bool(post.carousel_id) and (post.carousel_position or 0) != 0


def members(db: Session, carousel_id: str) -> list[Post]:
    """Every frame in the carousel, lead first."""
    return list(db.execute(
        select(Post)
        .where(Post.carousel_id == carousel_id)
        .order_by(Post.carousel_position)
    ).scalars().all())


def lead_for(db: Session, carousel_id: str) -> Post | None:
    return db.execute(
        select(Post).where(Post.carousel_id == carousel_id, Post.carousel_position == 0)
    ).scalar_one_or_none()


def target_ratio(db: Session, post: Post) -> float | None:
    """The output ratio this photo would publish at — the same rule the worker applies.

    Two frames agree when this matches. A panorama caps at 1.91 while everything else
    takes the learned floor, so a landscape and a portrait in one carousel would have
    Instagram cropping the rest to match the first.
    """
    if not post.width or not post.height:
        return None
    floor, _key, _tested = ig_variant.supported_floor(db)
    ratio = post.width / post.height
    return ig_variant.MAX_ASPECT if ratio > ig_variant.MAX_ASPECT else floor


def _performer_ids(db: Session, post_id: str) -> tuple[str, ...]:
    rows = db.execute(
        select(PostPerformer.performer_id)
        .where(PostPerformer.post_id == post_id)
        .order_by(PostPerformer.position)
    ).scalars().all()
    return tuple(sorted(rows))


def validate(db: Session, posts: list[Post], *, lead_id: str) -> list[str]:
    """Everything wrong with grouping these, in the order a human would want to fix it.

    Empty list means it is safe to group.
    """
    errors: list[str] = []
    ids = {p.id for p in posts}

    if not MIN_MEMBERS <= len(posts) <= MAX_MEMBERS:
        errors.append(
            f"A carousel holds {MIN_MEMBERS}–{MAX_MEMBERS} photos; this selection has {len(posts)}."
        )
    if lead_id not in ids:
        errors.append("The cover photo has to be one of the selected photos.")

    already = [p for p in posts if p.carousel_id]
    if already:
        errors.append(
            f"{len(already)} of these are already in a carousel — ungroup them first."
        )

    ratios = {}
    for p in posts:
        r = target_ratio(db, p)
        ratios.setdefault(r, []).append(p)
    if None in ratios:
        errors.append(
            f"{len(ratios[None])} photo(s) have no stored dimensions, so their crop can't be checked."
        )
    real = {r: v for r, v in ratios.items() if r is not None}
    if len(real) > 1:
        shape = ", ".join(f"{len(v)} at {r:.2f}" for r, v in sorted(real.items()))
        errors.append(
            f"Instagram crops every frame to match the first, so they must share a ratio ({shape}). "
            "Use Crop for IG on the selection first."
        )

    performers = {_performer_ids(db, p.id) for p in posts}
    if len(performers) > 1:
        errors.append(
            "These don't all tag the same performers. A carousel has one caption and one "
            "set of co-authors, so mixed performers would lose credits."
        )

    platforms = {(p.target_platforms or "") for p in posts}
    if len(platforms) > 1:
        errors.append("These don't all target the same platforms.")

    return errors


def group(db: Session, posts: list[Post], *, lead_id: str) -> str:
    """Make these posts a carousel. Returns the new carousel id.

    Members inherit the lead's scheduled_at — a carousel that fired across three
    different days would be a genuinely confusing bug, and Smart Fill's scatter is built
    to spread a show's photos apart.

    Caller validates first; this trusts its input.
    """
    carousel_id = uuid.uuid4().hex
    lead = next(p for p in posts if p.id == lead_id)
    rest = [p for p in posts if p.id != lead_id]

    lead.carousel_id = carousel_id
    lead.carousel_position = 0
    for i, p in enumerate(rest, start=1):
        p.carousel_id = carousel_id
        p.carousel_position = i
        p.scheduled_at = lead.scheduled_at
    db.commit()
    return carousel_id


def reorder(db: Session, carousel_id: str, ordered_ids: list[str]) -> None:
    """Set positions from an explicit order. First id becomes the lead."""
    by_id = {p.id: p for p in members(db, carousel_id)}
    for i, pid in enumerate(ordered_ids):
        if pid in by_id:
            by_id[pid].carousel_position = i
    db.commit()


def ungroup(db: Session, carousel_id: str) -> int:
    """Break the carousel back into ordinary posts. Returns how many were freed.

    Scheduled times are left as they are: they were deliberately aligned, and silently
    re-scattering someone's queue on an ungroup would be a worse surprise than a few
    posts sharing a slot.
    """
    freed = members(db, carousel_id)
    for p in freed:
        p.carousel_id = None
        p.carousel_position = None
    db.commit()
    return len(freed)
