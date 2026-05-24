"""AI tagging API — settings, key test, on-demand suggestions for a post."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import AppConfig, Performer, Post, PostPerformer, User, Venue
from routes.auth import current_user
from services import ai_tagging

log = logging.getLogger("framepost.ai")
router = APIRouter()


class AIStatus(BaseModel):
    enabled: bool
    provider: str
    auto_apply: bool
    suggest_description: bool
    send_full_resolution: bool
    max_suggestions: int
    tone: str  # "concise" or "descriptive"
    providers: dict[str, dict[str, Any]]


class AISettingsUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    auto_apply: bool | None = None
    suggest_description: bool | None = None
    send_full_resolution: bool | None = None
    max_suggestions: int | None = None
    tone: str | None = None


class TestResult(BaseModel):
    ok: bool
    model: str | None = None
    echo: str | None = None
    error: str | None = None


class Suggestion(BaseModel):
    tags: list[str]
    description: str | None
    alt_text: str | None = None
    provider: str
    full_resolution: bool
    sources: list[list[str]] | None = None  # populated only for the "both" provider


class SuggestHints(BaseModel):
    """Optional context the editor passes — current (unsaved) field values.

    If `hint_description` is non-empty the suggester switches to *polish mode*: refine the
    existing draft instead of writing from scratch. Venue/show/city/performers come from
    the structured fields the user has filled in and are surfaced as ground truth to the
    AI — output quality improves significantly when these are populated.
    """
    hint_title: str | None = None
    hint_tags: str | None = None
    hint_description: str | None = None
    # Structured context. The route falls back to DB values when None.
    hint_venue: str | None = None
    hint_show: str | None = None
    hint_city: str | None = None
    hint_performers: list[str] | None = None


def _get(db: Session, key: str) -> str | None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppConfig(key=key, value=value))


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.lower() == "true"


def _int(raw: str | None, default: int) -> int:
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _read_settings(db: Session) -> AIStatus:
    provider = _get(db, "ai_tagging_provider") or ai_tagging.ANTHROPIC
    if provider not in ai_tagging.SELECTABLE_PROVIDERS:
        provider = ai_tagging.ANTHROPIC
    tone = (_get(db, "ai_tone") or "concise").lower()
    if tone not in ("concise", "descriptive"):
        tone = "concise"
    return AIStatus(
        enabled=_bool(_get(db, "ai_tagging_enabled"), False),
        provider=provider,
        auto_apply=_bool(_get(db, "ai_auto_apply"), False),
        suggest_description=_bool(_get(db, "ai_suggest_description"), True),
        send_full_resolution=_bool(_get(db, "ai_send_full_resolution"), False),
        max_suggestions=_int(_get(db, "ai_max_suggestions"), 10),
        tone=tone,
        providers=ai_tagging.all_status(),
    )


@router.get("/status", response_model=AIStatus)
def get_status(db: Session = Depends(get_session), _user: User = Depends(current_user)):
    return _read_settings(db)


@router.put("/settings", response_model=AIStatus)
def put_settings(
    body: AISettingsUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if body.provider is not None and body.provider not in ai_tagging.SELECTABLE_PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown provider: {body.provider}")
    if body.max_suggestions is not None and not (1 <= body.max_suggestions <= 50):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "max_suggestions must be 1..50")
    if body.tone is not None and body.tone not in ("concise", "descriptive"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tone must be 'concise' or 'descriptive'")

    if body.enabled is not None:
        _set(db, "ai_tagging_enabled", "true" if body.enabled else "false")
    if body.provider is not None:
        _set(db, "ai_tagging_provider", body.provider)
    if body.auto_apply is not None:
        _set(db, "ai_auto_apply", "true" if body.auto_apply else "false")
    if body.suggest_description is not None:
        _set(db, "ai_suggest_description", "true" if body.suggest_description else "false")
    if body.send_full_resolution is not None:
        _set(db, "ai_send_full_resolution", "true" if body.send_full_resolution else "false")
    if body.max_suggestions is not None:
        _set(db, "ai_max_suggestions", str(body.max_suggestions))
    if body.tone is not None:
        _set(db, "ai_tone", body.tone)
    db.commit()
    return _read_settings(db)


@router.post("/test/{provider}", response_model=TestResult)
def test_provider(
    provider: str,
    _user: User = Depends(current_user),
):
    if provider not in ai_tagging.SELECTABLE_PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown provider: {provider}")
    suggester = ai_tagging.for_provider(provider)
    if not suggester.is_configured():
        return TestResult(ok=False, error="API key not set in .env")
    result = suggester.test()
    return TestResult(**result)


@router.post("/suggest/{post_id}", response_model=Suggestion)
def suggest(
    post_id: str,
    hints: SuggestHints | None = None,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    s = _read_settings(db)
    suggester = ai_tagging.for_provider(s.provider)
    if not suggester.is_configured():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{s.provider} key not configured — add it to .env",
        )
    post = db.get(Post, post_id)
    if not post or not post.original_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found or has no source file")
    src = Path(post.original_path)
    if not src.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source file missing on disk")

    # Fall back to whatever the post has saved if the caller didn't pass live edits.
    hint_title = (hints.hint_title if hints else None) or post.title
    hint_tags = (hints.hint_tags if hints else None) or post.tags
    hint_description = (hints.hint_description if hints else None) or post.description

    # Structured context: prefer hints (live edits in the editor), fall back to the saved
    # post fields. Venue + performers require a DB lookup since they're FK-linked.
    hint_venue = hints.hint_venue if hints else None
    if hint_venue is None and post.venue_id:
        v = db.get(Venue, post.venue_id)
        if v:
            hint_venue = v.display_name
    hint_show = (hints.hint_show if hints else None) or post.show
    hint_city = (hints.hint_city if hints else None) or post.city
    hint_performers = hints.hint_performers if hints else None
    if hint_performers is None:
        perf_names = db.execute(
            select(Performer.display_name)
            .join(PostPerformer, PostPerformer.performer_id == Performer.id)
            .where(PostPerformer.post_id == post_id)
            .order_by(PostPerformer.position)
        ).scalars().all()
        hint_performers = list(perf_names)

    try:
        result = suggester.suggest(
            image_path=src,
            max_tags=s.max_suggestions,
            full_resolution=s.send_full_resolution,
            hint_title=hint_title,
            hint_tags=hint_tags,
            hint_description=hint_description,
            hint_venue=hint_venue,
            hint_show=hint_show,
            hint_city=hint_city,
            hint_performers=hint_performers,
            tone=s.tone,
        )
    except ai_tagging.TagSuggesterError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    except Exception as e:
        log.exception("AI suggest failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return Suggestion(
        tags=result.tags,
        description=result.description,
        alt_text=result.alt_text,
        provider=s.provider,
        full_resolution=s.send_full_resolution,
        sources=result.sources,
    )
