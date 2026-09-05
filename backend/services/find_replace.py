"""Find & replace across the free-text fields of unpublished posts.

A typo that got copied into twenty descriptions is otherwise twenty manual edits, and
Bulk Edit can't help: its Description field REPLACES the whole value, which would
flatten twenty different descriptions into one. This rewrites only the matched
substring and leaves the rest of each post's text alone.

Deliberately limited to status == "pending". A published post already exists on Flickr
and Instagram with its old text; rewriting the local copy would just create a silent
mismatch between what FramePost shows and what the world sees. Published hits are
counted and reported so the caller can say so, never edited.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Post
from services.tags import normalize_tag_csv

# Free text the photographer authored. Everything else on Post is either derived
# (EXIF, paths) or structured (status, ids) and has no business being bulk-rewritten.
FIELDS = ("description", "title", "tags")

# Characters of surrounding context shown either side of a hit in the preview.
CONTEXT = 45


@dataclass
class Match:
    post_id: str
    title: str | None
    status: str
    scheduled_at: str | None
    occurrences: int
    before: str
    after: str


def _pattern(find: str, case_sensitive: bool) -> re.Pattern[str]:
    # Escaped: the photographer is typing a literal typo, not a regex. "C++" or "a.b"
    # must match themselves rather than blowing up or matching everything.
    return re.compile(re.escape(find), 0 if case_sensitive else re.IGNORECASE)


def _replace_all(pat: re.Pattern[str], text: str, replace: str) -> str:
    # A function replacement, so backslashes and \1 in the user's text stay literal.
    # pat.sub(replace, ...) would interpret those as group references and either raise
    # or silently mangle the result.
    return pat.sub(lambda _m: replace, text)


def _snippet(text: str, pat: re.Pattern[str]) -> str:
    """The first hit with a little context, so the preview shows WHERE it matched."""
    m = pat.search(text)
    if not m:
        return text[: CONTEXT * 2]
    start, end = max(0, m.start() - CONTEXT), min(len(text), m.end() + CONTEXT)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _normalized(field: str, value: str) -> str:
    """Tags carry an invariant (comma-separated, no stray #/@ blobs) that the rest of
    the app relies on, so a replacement inside them gets re-normalized."""
    if field != "tags":
        return value
    return normalize_tag_csv(value) or ""


def scan(
    db: Session,
    *,
    find: str,
    replace: str = "",
    field: str = "description",
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Dry run. Returns every unpublished post that contains `find`, with a before/after
    snippet, plus a count of published posts that match but will not be touched."""
    if field not in FIELDS:
        raise ValueError(f"field must be one of {FIELDS}")
    if not find:
        raise ValueError("find must not be empty")

    pat = _pattern(find, case_sensitive)
    matches: list[Match] = []
    occurrences = 0
    published_hits = 0

    for p in db.execute(select(Post)).scalars():
        text = getattr(p, field) or ""
        if not text:
            continue
        n = len(pat.findall(text))
        if not n:
            continue
        if p.status != "pending":
            published_hits += 1
            continue
        occurrences += n
        new_text = _normalized(field, _replace_all(pat, text, replace))
        matches.append(Match(
            post_id=p.id,
            title=p.title,
            status=p.status,
            scheduled_at=p.scheduled_at.isoformat() if p.scheduled_at else None,
            occurrences=n,
            before=_snippet(text, pat),
            after=_snippet(new_text, _pattern(replace, case_sensitive)) if replace
            else new_text[: CONTEXT * 2],
        ))

    matches.sort(key=lambda m: (m.scheduled_at or "", m.title or ""))
    return {
        "field": field,
        "find": find,
        "replace": replace,
        "case_sensitive": case_sensitive,
        "post_count": len(matches),
        "occurrence_count": occurrences,
        "published_skipped": published_hits,
        "matches": [asdict(m) for m in matches],
    }


def apply(
    db: Session,
    *,
    find: str,
    replace: str,
    post_ids: list[str],
    field: str = "description",
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Rewrite `find` → `replace` in the named posts only.

    Takes explicit ids rather than re-running the search, so what the caller previewed
    and ticked is exactly what changes — a post edited between preview and apply can't
    be silently swept in.
    """
    if field not in FIELDS:
        raise ValueError(f"field must be one of {FIELDS}")
    if not find:
        raise ValueError("find must not be empty")

    pat = _pattern(find, case_sensitive)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    changed = 0
    occurrences = 0
    skipped: list[str] = []

    for pid in post_ids:
        p = db.get(Post, pid)
        if p is None or p.status != "pending":
            skipped.append(pid)
            continue
        text = getattr(p, field) or ""
        n = len(pat.findall(text))
        if not n:
            skipped.append(pid)   # no longer matches — edited since the preview
            continue
        setattr(p, field, _normalized(field, _replace_all(pat, text, replace)))
        p.updated_at = now
        changed += 1
        occurrences += n

    return {"changed": changed, "occurrence_count": occurrences, "skipped": skipped}
