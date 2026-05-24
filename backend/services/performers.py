"""Performer caption helpers — shared across scheduler, IG-format route, Pinterest, Reels.

The lightweight model: each performer has display_name + optional instagram_handle.
When tagged on a post:
- Mentions: '@handle1 @handle2 ...' for performers who have a handle. Skipped for those who don't.
- Hashtags: '#handle' if handle present, else '#NameLikeThis' from the display name.

Both blocks render in performer insertion order (PostPerformer.position).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Performer, Post, PostPerformer, Venue


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


# -----------------------------------------------------------------------------
# Unified caption-context helper — performers + venue + show + city
# -----------------------------------------------------------------------------
#
# As of migration 0014, posts can also reference a Venue (lightweight entity like
# Performer) plus free-text show + city. Caption builders across scheduler / IG-format
# route / Pinterest all need the same composed mention block and hashtag list, so
# centralizing here keeps platforms consistent.


def _candidate_keys(name_or_handle: str | None, display_name: str) -> list[str]:
    """Lowercased keys we'd consider 'already mentioned' for an entity. Used for both
    dedup-against-existing-text and dedup-across-the-mention/hashtag-blocks."""
    out: list[str] = []
    if name_or_handle:
        out.append(name_or_handle.lower())
    fallback = "".join(ch for ch in display_name if ch.isalnum()).lower()
    if fallback and fallback not in out:
        out.append(fallback)
    return out


def _entity_already_in(text_lower: str, tags_lower: str, candidates: list[str]) -> bool:
    for cand in candidates:
        if (
            f"@{cand}" in text_lower
            or f"#{cand}" in text_lower
            or _has_token(tags_lower, cand)
        ):
            return True
    return False


def _camel_token(text: str) -> str:
    """Strip whitespace/punctuation to make a single hashtag token from free text.
    'Slow Burn Burlesque' -> 'SlowBurnBurlesque'. 'New Orleans, LA' -> 'NewOrleansLA'."""
    return "".join(ch for ch in (text or "") if ch.isalnum())


@dataclass
class CaptionContext:
    """All the auto-insertable context for a post's caption. Mentions are deduped against
    the post's existing description/title/tags; hashtags are deduped among themselves and
    against manual tags. The platform-specific builder decides where to place each block.
    """
    mention_block: str         # "@h1 @h2" — performers + venue (those with handles)
    hashtag_tokens: list[str]  # ["#h1", "#VenueName", "#City", "#ShowName"] — display order


def caption_context_for_post(db: Session, post: Post) -> CaptionContext:
    """Build the unified mention + hashtag context for a post, deduping against any
    @-mentions / #hashtags the user typed manually in the description/title/tags.

    Mentions and hashtags are tracked separately — a performer gets both `@handle` AND
    `#handle` (different platforms surface them differently). The seen sets prevent
    duplicates within each kind, not across kinds.

    Order matters because Bluesky has a 300-char budget — performers come first as
    attribution-critical, then venue (relevant for repost dynamics), then show/city."""
    text_lower = ((post.description or "") + " " + (post.title or "")).lower()
    tags_lower = (post.tags or "").lower()

    mention_handles: list[str] = []
    seen_mention_keys: set[str] = set()
    hashtag_tokens: list[str] = []
    seen_hashtag_keys: set[str] = set()

    def _add_entity(handle: str | None, display_name: str) -> None:
        """Add @-mention and #-hashtag for an entity, skipping if either is already
        present (in user-typed text/tags or already added by an earlier entity)."""
        candidates = _candidate_keys(handle, display_name)
        if _entity_already_in(text_lower, tags_lower, candidates):
            return
        if handle:
            mk = handle.lower()
            if mk not in seen_mention_keys:
                mention_handles.append(handle)
                seen_mention_keys.add(mk)
        hashtag_token = handle if handle else _camel_token(display_name)
        if hashtag_token:
            hk = hashtag_token.lower()
            if hk not in seen_hashtag_keys:
                hashtag_tokens.append(f"#{hashtag_token}")
                seen_hashtag_keys.add(hk)

    # Performers in insertion order.
    for p in get_post_performers(db, post.id):
        _add_entity(p.instagram_handle, p.display_name)

    # Venue — at most one per post.
    if post.venue_id:
        v = db.get(Venue, post.venue_id)
        if v:
            _add_entity(v.instagram_handle, v.display_name)

    # Show + City — hashtag only, no @-mention (text fields, no IG handles).
    for text_val in (post.show, post.city):
        if not text_val or not text_val.strip():
            continue
        token = _camel_token(text_val)
        if not token:
            continue
        key = token.lower()
        if key in seen_hashtag_keys:
            continue
        if (
            f"#{key}" in text_lower
            or _has_token(tags_lower, key)
        ):
            continue
        hashtag_tokens.append(f"#{token}")
        seen_hashtag_keys.add(key)

    mention = " ".join(f"@{h}" for h in mention_handles)
    return CaptionContext(mention_block=mention, hashtag_tokens=hashtag_tokens)
