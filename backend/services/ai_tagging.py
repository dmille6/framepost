"""AI tagging — provider-agnostic photo tag + caption suggester.

Concrete adapters: AnthropicSuggester (claude-haiku-4-5), OpenAISuggester (gpt-4o-mini).
The selected provider is read from app_config.ai_tagging_provider.

Privacy disclosure (brief): the user's image leaves the local server and goes to the
chosen AI provider. We never send the original full-res image unless `ai_send_full_resolution`
is explicitly enabled — defaults to a downscaled preview at AI_PREVIEW_LONG_EDGE.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from config import settings

log = logging.getLogger("framepost.ai_tagging")

ANTHROPIC = "anthropic"
OPENAI = "openai"
BOTH = "both"
PROVIDERS = (ANTHROPIC, OPENAI)               # underlying single-key providers
SELECTABLE_PROVIDERS = (ANTHROPIC, OPENAI, BOTH)  # what the user can pick in Settings

AI_PREVIEW_LONG_EDGE = 1024
AI_PREVIEW_QUALITY = 82

ANTHROPIC_MODEL = "claude-haiku-4-5"
OPENAI_MODEL = "gpt-4o-mini"

PROMPT = (
    "You are a photographer's assistant. Look at this photograph and suggest tags useful "
    "for Flickr search and discovery. Cover: subject/people, setting/location, genre, "
    "lighting/mood, and salient technical details (e.g. black & white, long exposure) "
    "where visible. Avoid duplicates and avoid generic words like 'photo' or 'image'. "
    "Optionally draft a short 1–2 sentence description suitable as a Flickr caption.\n\n"
    "Reply with JSON only, in this exact shape:\n"
    '{{"tags": ["tag1", "tag2"], "description": "short caption or null"}}\n\n'
    'Cap the tag list at {max_tags}. Tags should be lowercase unless they\'re proper nouns.'
)


def build_prompt(
    *,
    max_tags: int,
    hint_title: str | None,
    hint_tags: str | None,
    hint_description: str | None = None,
    tone: str = "concise",
) -> str:
    """Inject the photographer's current title/tags/description as context. Two modes:

    - **Polish mode** (description already present): refine the existing draft — tighten
      language, weave in title proper-nouns if missing — but don't rewrite from scratch.
    - **Draft mode** (no description): generate a 1–2 sentence caption from the image.

    `tone` controls description style:
    - "concise" (default): factual, journalistic. State the subject, action, location, and
      relevant technical details only when visible. No flowery adjectives, no atmospheric
      mood-setting. Reads like a Reuters caption.
    - "descriptive": evocative, atmospheric. Allowed to use mood/lighting language and
      paint a fuller picture. Closer to art-blog voice.
    """
    has_title = bool(hint_title and hint_title.strip())
    has_tags = bool(hint_tags and hint_tags.strip())
    has_description = bool(hint_description and hint_description.strip())
    is_concise = (tone or "concise").lower() != "descriptive"

    context_lines = []
    if has_title:
        context_lines.append(f'• Title: "{hint_title.strip()}"')
    if has_tags:
        context_lines.append(f"• Existing tags: {hint_tags.strip()}")
    if has_description:
        context_lines.append(f'• Existing description draft: "{hint_description.strip()}"')
    context = (
        "Context (the photographer's current state):\n" + "\n".join(context_lines) + "\n\n"
        if context_lines
        else ""
    )

    if has_description:
        if is_concise:
            description_instruction = (
                "Tighten the existing description above to be FACTUAL and JOURNALISTIC. "
                "Remove flowery adjectives, mood-setting, and atmospheric language. State who "
                "is in the frame, what they're doing, and where (using proper-noun details "
                "from the title if available). One or two short sentences. No 'a dreamlike "
                "moment captured', no 'shrouded in mystery', etc."
            )
        else:
            description_instruction = (
                "Polish the existing description above. Tighten language, fix awkward phrasing, "
                "and weave in proper-noun details from the title if any are missing. Keep the "
                "photographer's voice — don't rewrite it from scratch and don't lengthen "
                "significantly."
            )
    else:
        if is_concise:
            description_instruction = (
                "Draft a SHORT, FACTUAL caption (1–2 short sentences max). State who/what is "
                "in the frame and where, using proper-noun details from the title (performer "
                "names, event names, venue, location) if relevant. No flowery adjectives, no "
                "mood/atmosphere language. Think Reuters or AP wire-photo caption — describe "
                "what's literally visible, nothing more."
            )
        else:
            description_instruction = (
                "Draft a short 1–2 sentence caption suitable for Flickr. Use proper-noun "
                "details from the title (event names, performers) if relevant. Evocative is "
                "fine — convey mood and lighting where it adds to the photo."
            )

    tag_instruction = (
        "Suggest tags useful for Flickr search and discovery. Cover: subject/people, "
        "setting/location, genre, lighting/mood, and salient technical details (e.g. "
        "black & white, long exposure) where visible. Avoid generic words like 'photo' "
        "or 'image'."
    )
    if has_tags:
        tag_instruction += " Don't propose near-duplicates of the existing tags."

    return (
        "You are a photographer's assistant.\n\n"
        + context
        + "Look at this photograph and:\n"
        + f"1. {tag_instruction}\n"
        + f"2. {description_instruction}\n\n"
        + 'Reply with JSON only, in this exact shape:\n'
        + '{"tags": ["tag1", "tag2"], "description": "..."}\n\n'
        + f"Cap the tag list at {max_tags}. Tags lowercase unless they're proper nouns."
    )


@dataclass
class TagSuggestion:
    tags: list[str]
    description: str | None = None
    raw: str | None = None
    # Parallel to `tags`. None for single-provider; populated by EnsembleSuggester so the UI
    # can badge each tag with which provider(s) supplied it.
    sources: list[list[str]] | None = None


class TagSuggesterError(Exception):
    pass


def _encode_preview(image_path: Path, *, full_resolution: bool) -> tuple[str, str]:
    """Return (mime, base64). Downscales to AI_PREVIEW_LONG_EDGE unless full_resolution."""
    Image.MAX_IMAGE_PIXELS = 200_000_000
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        if not full_resolution and max(img.size) > AI_PREVIEW_LONG_EDGE:
            img.thumbnail((AI_PREVIEW_LONG_EDGE, AI_PREVIEW_LONG_EDGE), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=AI_PREVIEW_QUALITY, optimize=True)
        return "image/jpeg", base64.b64encode(buf.getvalue()).decode()


def _parse_json_blob(text: str, *, max_tags: int) -> TagSuggestion:
    """Tolerant parse — strips ```json fences, salvages the largest {...} block if needed."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Last-ditch: find the first { and the matching }
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                raise TagSuggesterError(f"could not parse JSON from response: {text[:200]}")
        else:
            raise TagSuggesterError(f"no JSON in response: {text[:200]}")

    tags_raw = data.get("tags") or []
    tags: list[str] = []
    seen: set[str] = set()
    for t in tags_raw:
        s = str(t).strip()
        if not s:
            continue
        norm = s.lower()
        if norm in seen:
            continue
        seen.add(norm)
        tags.append(s)
        if len(tags) >= max_tags:
            break

    desc = data.get("description")
    if isinstance(desc, str):
        desc = desc.strip() or None
    else:
        desc = None
    return TagSuggestion(tags=tags, description=desc, raw=text)


# --- Provider interface ---


class TagSuggester(ABC):
    name: str = ""

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def test(self) -> dict[str, Any]: ...

    @abstractmethod
    def suggest(
        self,
        *,
        image_path: Path,
        max_tags: int,
        full_resolution: bool,
        hint_title: str | None = None,
        hint_tags: str | None = None,
        hint_description: str | None = None,
        tone: str = "concise",
    ) -> TagSuggestion: ...


# --- Anthropic ---


class AnthropicSuggester(TagSuggester):
    name = ANTHROPIC

    def is_configured(self) -> bool:
        return bool(settings.anthropic_api_key)

    def _client(self):
        if not self.is_configured():
            raise TagSuggesterError("ANTHROPIC_API_KEY not set")
        import anthropic
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def test(self) -> dict[str, Any]:
        try:
            client = self._client()
            r = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=8,
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            )
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            return {"ok": True, "model": r.model, "echo": text.strip()[:32]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def suggest(
        self,
        *,
        image_path: Path,
        max_tags: int,
        full_resolution: bool,
        hint_title: str | None = None,
        hint_tags: str | None = None,
        hint_description: str | None = None,
        tone: str = "concise",
    ) -> TagSuggestion:
        client = self._client()
        mime, b64 = _encode_preview(image_path, full_resolution=full_resolution)
        prompt = build_prompt(
            max_tags=max_tags,
            hint_title=hint_title,
            hint_tags=hint_tags,
            hint_description=hint_description,
            tone=tone,
        )
        r = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return _parse_json_blob(text, max_tags=max_tags)


# --- OpenAI ---


class OpenAISuggester(TagSuggester):
    name = OPENAI

    def is_configured(self) -> bool:
        return bool(settings.openai_api_key)

    def _client(self):
        if not self.is_configured():
            raise TagSuggesterError("OPENAI_API_KEY not set")
        from openai import OpenAI
        return OpenAI(api_key=settings.openai_api_key)

    def test(self) -> dict[str, Any]:
        try:
            client = self._client()
            r = client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=8,
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            )
            text = (r.choices[0].message.content or "").strip()
            return {"ok": True, "model": r.model, "echo": text[:32]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def suggest(
        self,
        *,
        image_path: Path,
        max_tags: int,
        full_resolution: bool,
        hint_title: str | None = None,
        hint_tags: str | None = None,
        hint_description: str | None = None,
        tone: str = "concise",
    ) -> TagSuggestion:
        client = self._client()
        mime, b64 = _encode_preview(image_path, full_resolution=full_resolution)
        prompt = build_prompt(
            max_tags=max_tags,
            hint_title=hint_title,
            hint_tags=hint_tags,
            hint_description=hint_description,
            tone=tone,
        )
        r = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        text = r.choices[0].message.content or ""
        return _parse_json_blob(text, max_tags=max_tags)


# --- Ensemble (calls both providers in parallel, merges with source attribution) ---


class EnsembleSuggester(TagSuggester):
    name = BOTH

    def __init__(self) -> None:
        self._anth = AnthropicSuggester()
        self._oai = OpenAISuggester()

    def is_configured(self) -> bool:
        return self._anth.is_configured() and self._oai.is_configured()

    def test(self) -> dict[str, Any]:
        anth = self._anth.test() if self._anth.is_configured() else {"ok": False, "error": "Anthropic key not set"}
        oai = self._oai.test() if self._oai.is_configured() else {"ok": False, "error": "OpenAI key not set"}
        ok = bool(anth.get("ok") and oai.get("ok"))
        return {
            "ok": ok,
            "model": f"{anth.get('model') or '—'}  +  {oai.get('model') or '—'}",
            "echo": "ensemble",
            "error": None if ok else "; ".join(filter(None, [anth.get("error"), oai.get("error")])),
        }

    def suggest(
        self,
        *,
        image_path: Path,
        max_tags: int,
        full_resolution: bool,
        hint_title: str | None = None,
        hint_tags: str | None = None,
        hint_description: str | None = None,
        tone: str = "concise",
    ) -> TagSuggestion:
        from concurrent.futures import ThreadPoolExecutor

        results: dict[str, TagSuggestion | None] = {ANTHROPIC: None, OPENAI: None}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(
                    s.suggest,
                    image_path=image_path,
                    max_tags=max_tags,
                    full_resolution=full_resolution,
                    hint_title=hint_title,
                    hint_tags=hint_tags,
                    hint_description=hint_description,
                    tone=tone,
                ): s.name
                for s in (self._anth, self._oai)
                if s.is_configured()
            }
            for f, provider_name in futures.items():
                try:
                    results[provider_name] = f.result()
                except Exception as e:
                    errors[provider_name] = str(e)
                    log.warning("ensemble: %s failed: %s", provider_name, e)

        if not any(results.values()):
            details = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "no providers configured"
            raise TagSuggesterError(f"ensemble: both providers failed — {details}")

        # Merge tags case-insensitively, preserving first-seen casing.
        seen: dict[str, list[str]] = {}
        anth_order: list[str] = []
        oai_order: list[str] = []
        for provider, bucket in ((ANTHROPIC, anth_order), (OPENAI, oai_order)):
            res = results[provider]
            if not res:
                continue
            for t in res.tags:
                key = t.lower().strip()
                if not key:
                    continue
                if key not in seen:
                    seen[key] = [provider]
                    bucket.append(t.strip())
                elif provider not in seen[key]:
                    seen[key].append(provider)

        # Build the final list: agreed-by-both first, then interleave the remaining
        # provider-unique tags so each provider gets fair representation under the cap.
        agreed = [t for t in anth_order + oai_order if len(seen[t.lower().strip()]) >= 2]
        # De-dupe agreed (it's already case-keyed but `t` casing may differ).
        deduped_agreed: list[str] = []
        agreed_seen: set[str] = set()
        for t in agreed:
            k = t.lower().strip()
            if k not in agreed_seen:
                agreed_seen.add(k)
                deduped_agreed.append(t)
        anth_unique = [t for t in anth_order if seen[t.lower().strip()] == [ANTHROPIC]]
        oai_unique = [t for t in oai_order if seen[t.lower().strip()] == [OPENAI]]
        interleaved: list[str] = []
        for i in range(max(len(anth_unique), len(oai_unique))):
            if i < len(anth_unique):
                interleaved.append(anth_unique[i])
            if i < len(oai_unique):
                interleaved.append(oai_unique[i])

        ranked = (deduped_agreed + interleaved)[:max_tags]
        sources = [seen[t.lower().strip()] for t in ranked]

        # Description: prefer Anthropic (richer in our test); fall back to OpenAI.
        description = None
        for provider in (ANTHROPIC, OPENAI):
            r = results[provider]
            if r and r.description:
                description = r.description
                break

        return TagSuggestion(tags=ranked, description=description, sources=sources)


# --- Factory ---


_REGISTRY = {ANTHROPIC: AnthropicSuggester, OPENAI: OpenAISuggester, BOTH: EnsembleSuggester}


def for_provider(name: str) -> TagSuggester:
    cls = _REGISTRY.get(name)
    if not cls:
        raise TagSuggesterError(f"unknown AI provider: {name}")
    return cls()


def all_status() -> dict[str, dict[str, bool]]:
    """Used by the Settings → AI Tagging tab to render per-provider key presence."""
    return {p: {"configured": for_provider(p).is_configured()} for p in PROVIDERS}


# --- Auto-apply on import (Phase 5C) ---


def apply_to_post(post_id: str) -> None:
    """Run the configured suggester and merge into the post — best-effort.

    No-op unless ai_tagging_enabled + ai_auto_apply are both on. Adds suggested tags
    (deduped with existing). Sets description only when post.description is empty.
    Never raises — auto-apply must not break the import path.
    """
    from sqlalchemy import select
    from database import SessionLocal
    from models import AppConfig, Post
    from services import events, tags as tag_helpers

    db = SessionLocal()
    try:
        rows = {
            r.key: r.value
            for r in db.execute(
                select(AppConfig).where(
                    AppConfig.key.in_(
                        [
                            "ai_tagging_enabled",
                            "ai_auto_apply",
                            "ai_tagging_provider",
                            "ai_max_suggestions",
                            "ai_send_full_resolution",
                            "ai_suggest_description",
                        ]
                    )
                )
            )
            .scalars()
            .all()
        }
        if (rows.get("ai_tagging_enabled") or "false").lower() != "true":
            return
        if (rows.get("ai_auto_apply") or "false").lower() != "true":
            return

        provider = rows.get("ai_tagging_provider") or ANTHROPIC
        if provider not in SELECTABLE_PROVIDERS:
            provider = ANTHROPIC
        try:
            max_tags = int(rows.get("ai_max_suggestions") or "10")
        except ValueError:
            max_tags = 10
        full_res = (rows.get("ai_send_full_resolution") or "false").lower() == "true"
        suggest_desc = (rows.get("ai_suggest_description") or "true").lower() == "true"

        post = db.get(Post, post_id)
        if not post or not post.original_path:
            return
        src = Path(post.original_path)
        if not src.exists():
            return

        try:
            suggester = for_provider(provider)
            if not suggester.is_configured():
                log.info("ai auto-apply: %s not configured for %s, skipping", provider, post_id[:8])
                return
            result = suggester.suggest(
                image_path=src, max_tags=max_tags, full_resolution=full_res
            )
        except Exception as e:
            log.warning("ai auto-apply: suggester failed on %s: %s", post_id[:8], e)
            return

        existing = tag_helpers.parse_csv(post.tags)
        merged = tag_helpers.merge_unique(existing, result.tags)
        added = [t for t in result.tags if t.lower() not in {e.lower() for e in existing}]
        post.tags = tag_helpers.normalize_tag_csv(", ".join(merged)) if merged else None

        wrote_description = False
        if suggest_desc and not (post.description or "").strip() and result.description:
            post.description = result.description
            wrote_description = True

        events.log_event(
            db,
            post_id=post_id,
            event_type="edited",
            actor="ai",
            details={
                "action": "auto_apply",
                "provider": provider,
                "added_tags": added,
                "set_description": wrote_description,
            },
        )
        db.commit()
        log.info(
            "ai auto-apply: post %s +%d tag(s)%s via %s",
            post_id[:8],
            len(added),
            " + description" if wrote_description else "",
            provider,
        )
    except Exception:
        log.exception("ai auto-apply crashed unexpectedly for %s", post_id[:8])
        db.rollback()
    finally:
        db.close()
