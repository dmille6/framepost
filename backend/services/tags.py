"""Tag merging helpers for tag profiles + machine tags.

Profiles stack: user tags ∪ default-profile tags ∪ assigned-profile tags, deduplicated
case-insensitively but preserving the first-seen casing.
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Post, PostProfile, TagProfile


def parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def normalize_tag(s: str) -> str:
    """Collapse internal whitespace and trim. Keeps casing for readability.

    Why no spaces: Flickr accepts multi-word tags but URL-encodes them awkwardly; IG /
    Bluesky / Pixelfed hashtags don't support spaces at all and need concatenation. So we
    store tags space-free across the board — 'New Orleans nightlife' becomes
    'NewOrleansnightlife'. The user-typed casing is preserved so 'NouvelleFollies' still
    reads cleanly.
    """
    # Strip then collapse all internal whitespace (spaces, tabs, multiple) to nothing.
    cleaned = " ".join(s.split()).strip()
    return cleaned.replace(" ", "")


def _explode_social_blob(item: str) -> list[str]:
    """A pasted Instagram-style block ("#tag1 #tag2 @handle ...") arrives as ONE comma
    item; the space-collapse in normalize_tag would weld it into a single unusable
    mega-tag. When an item contains # or @, treat those symbols as token starts, split
    there, and strip the symbols — tags are stored bare and each platform's caption
    builder adds its own # form."""
    import re
    if "#" not in item and "@" not in item:
        return [item]
    out: list[str] = []
    for tok in re.split(r"(?=[#@])", item):
        tok = tok.strip().lstrip("#@").strip()
        if tok:
            out.append(tok)
    return out


def normalize_tag_csv(s: str | None) -> str | None:
    """Normalize a comma-separated tag string: each tag is space-collapsed, dupes removed
    case-insensitively. Pasted #hashtag/@mention runs are exploded into individual tags.
    Returns None for empty input so the DB column stays NULL rather than empty-string."""
    if not s or not s.strip():
        return None
    parts: list[str] = []
    for raw in parse_csv(s):
        parts.extend(_explode_social_blob(raw))
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        cleaned = normalize_tag(p)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return ", ".join(out) if out else None


def merge_unique(*sources: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for src in sources:
        for t in src:
            norm = t.lower().strip()
            if norm and norm not in seen:
                seen.add(norm)
                out.append(t.strip())
    return out


def merged_tags_for_post(db: Session, post: Post) -> str:
    """Comma-joined merged tags ready to ship. Excludes the framepost:sha256= machine tag —
    that's appended separately in the upload path."""
    profile_rows = db.execute(
        select(TagProfile).where(
            (TagProfile.is_default == 1)
            | (
                TagProfile.id.in_(
                    select(PostProfile.profile_id).where(PostProfile.post_id == post.id)
                )
            )
        )
    ).scalars().all()
    user = parse_csv(post.tags)
    from_profiles: list[str] = []
    for p in profile_rows:
        from_profiles.extend(parse_csv(p.tags))
    return ", ".join(merge_unique(user, from_profiles))


def ensure_default_profile(db: Session) -> TagProfile:
    """First-run bootstrap: a global-default profile that's always applied. Empty by default."""
    existing = db.execute(
        select(TagProfile).where(TagProfile.is_default == 1)
    ).scalar_one_or_none()
    if existing:
        return existing
    import uuid

    p = TagProfile(
        id=uuid.uuid4().hex,
        name="Global default",
        tags="",
        is_default=1,
        sort_order=0,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p
