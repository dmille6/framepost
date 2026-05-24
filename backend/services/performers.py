"""Performer caption helpers — shared across scheduler, IG-format route, Pinterest, Reels.

The lightweight model: each performer has display_name + optional instagram_handle.
When tagged on a post:
- Mentions: '@handle1 @handle2 ...' for performers who have a handle. Skipped for those who don't.
- Hashtags: '#handle' if handle present, else '#NameLikeThis' from the display name.

Both blocks render in performer insertion order (PostPerformer.position).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Performer, PostPerformer


def get_post_performers(db: Session, post_id: str) -> list[Performer]:
    """Return performers tagged on a post, in insertion order (position ASC)."""
    return list(db.execute(
        select(Performer)
        .join(PostPerformer, PostPerformer.performer_id == Performer.id)
        .where(PostPerformer.post_id == post_id)
        .order_by(PostPerformer.position)
    ).scalars())


def mention_block(performers: list[Performer]) -> str:
    """Return '@h1 @h2' for performers with handles. Empty string if nobody has one."""
    handles = [p.instagram_handle for p in performers if p.instagram_handle]
    if not handles:
        return ""
    return " ".join(f"@{h}" for h in handles)


def hashtag_tokens(performers: list[Performer]) -> list[str]:
    """Return ['#handle', '#NameLikeThis', ...]. Uses handle when present, falls back to
    display_name with non-alphanumeric chars stripped."""
    out: list[str] = []
    seen: set[str] = set()
    for p in performers:
        if p.instagram_handle:
            token = p.instagram_handle.lower()
        else:
            token = "".join(ch for ch in p.display_name if ch.isalnum())
            if not token:
                continue
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        out.append(f"#{token}")
    return out


def for_post(db: Session, post_id: str) -> tuple[str, list[str]]:
    """Convenience: load performers for post_id and return (mention_block, hashtag_tokens)."""
    performers = get_post_performers(db, post_id)
    return mention_block(performers), hashtag_tokens(performers)


def dedupe_against_text(
    performers: list[Performer],
    *,
    existing_text: str = "",
    existing_tags: str = "",
) -> list[Performer]:
    """Filter out performers whose handle (or display_name fallback token) is already present.

    If the user manually typed '@roxielarouge' in the description, we don't want the
    auto-mention to add it again. Same for '#roxielarouge' in the tag input. We check both
    forms — handles AND fallback tokens (CamelCased display names) — case-insensitively.
    """
    text_lower = (existing_text or "").lower()
    tags_lower = (existing_tags or "").lower()

    out: list[Performer] = []
    for p in performers:
        candidates: list[str] = []
        if p.instagram_handle:
            candidates.append(p.instagram_handle.lower())
        # Fallback token (CamelCased display name) — lowercased for matching.
        fallback = "".join(ch for ch in p.display_name if ch.isalnum()).lower()
        if fallback and fallback not in candidates:
            candidates.append(fallback)

        # Skip if ANY representation of this performer is already in the text or tags.
        # We look for @form and #form in description (mentions/hashtags can appear either
        # place when the user types manually) and bare-token in tags (since tag input is
        # comma/space-separated, prefixes don't matter — we just check whole-token presence).
        present = False
        for cand in candidates:
            if (
                f"@{cand}" in text_lower
                or f"#{cand}" in text_lower
                or _has_token(tags_lower, cand)
            ):
                present = True
                break
        if not present:
            out.append(p)
    return out


def _has_token(tags_lower: str, token: str) -> bool:
    """Whole-token match within the tags string. Splits on common separators."""
    if not tags_lower or not token:
        return False
    for raw in tags_lower.replace(",", " ").split():
        cleaned = raw.lstrip("#").lstrip("@")
        if cleaned == token:
            return True
    return False
