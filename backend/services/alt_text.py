"""Background alt-text generation — fills posts.alt_text where it's missing.

Every photo should carry alt text (accessibility + image SEO; Bluesky/Pixelfed/
Pinterest/Instagram all receive it on auto-post) without the user clicking AI
Suggest per photo. A worker job sweeps for posts with NULL alt_text — new imports
and the back catalog alike — and generates ONLY the alt text. Title, description,
and tags are never touched: those are the user's words.

Cost control: BATCH_PER_RUN posts per 15-minute tick — the archive backfills over
a day or two for pennies, and fresh imports get alt text minutes after landing,
long before their scheduled post fires. Uses the 1600px preview (alt text doesn't
need 60MP) and the user's configured provider/tone. Idle when AI tagging is
disabled or unconfigured.

A run that SUCCEEDS but yields no alt text writes "" so the post isn't retried
forever; transient API errors leave NULL for the next sweep and abort the batch
(no point burning quota against a down provider).
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import AppConfig, Performer, Post, PostPerformer, Venue
from services import ai_tagging, events, storage

log = logging.getLogger("framepost.alt_text")

BATCH_PER_RUN = 6


def _cfg(db: Session, key: str) -> str | None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    return row.value if row else None


def fill_missing_alt_text(db: Session) -> int:
    """Generate alt text for up to BATCH_PER_RUN posts missing it. Returns count filled."""
    if (_cfg(db, "ai_tagging_enabled") or "").lower() != "true":
        return 0
    provider = _cfg(db, "ai_tagging_provider") or ai_tagging.ANTHROPIC
    if provider not in ai_tagging.SELECTABLE_PROVIDERS:
        provider = ai_tagging.ANTHROPIC
    suggester = ai_tagging.for_provider(provider)
    if not suggester.is_configured():
        return 0
    tone = (_cfg(db, "ai_tone") or "concise").lower()

    candidates = db.execute(
        select(Post)
        .where(Post.alt_text.is_(None), Post.status.in_(("pending", "posted", "late")))
        .order_by(Post.created_at.desc())  # newest first — drafts about to be scheduled win
        .limit(BATCH_PER_RUN)
    ).scalars().all()

    filled = 0
    for post in candidates:
        src: Path | None = None
        preview = storage.preview_path(post.id)
        if preview.exists():
            src = preview
        elif post.original_path and Path(post.original_path).exists():
            src = Path(post.original_path)
        if src is None:
            post.alt_text = ""  # no pixels left to describe — stop retrying
            continue

        venue_name = None
        if post.venue_id:
            v = db.get(Venue, post.venue_id)
            venue_name = v.display_name if v else None
        performers = db.execute(
            select(Performer.display_name)
            .join(PostPerformer, PostPerformer.performer_id == Performer.id)
            .where(PostPerformer.post_id == post.id)
            .order_by(PostPerformer.position)
        ).scalars().all()

        try:
            result = suggester.suggest(
                image_path=src,
                max_tags=5,  # we only want alt_text; keep the cheap path
                full_resolution=False,
                hint_title=post.title,
                hint_tags=post.tags,
                hint_description=post.description,
                hint_venue=venue_name,
                hint_show=post.show,
                hint_city=post.city,
                hint_performers=list(performers),
                tone=tone,
            )
        except Exception as e:  # noqa: BLE001 — provider down: leave NULLs, retry next sweep
            log.warning("alt-text sweep aborted at post %s: %s", post.id[:8], e)
            break

        post.alt_text = (result.alt_text or "").strip()
        if post.alt_text:
            filled += 1
            events.log_event(
                db,
                post_id=post.id,
                event_type="alt_text_generated",
                actor="worker",
                details={"provider": provider, "chars": len(post.alt_text)},
            )
    db.commit()
    if filled:
        log.info("alt-text sweep filled %d post(s)", filled)
    return filled
