"""Can this post actually be delivered, and where would it fail?

The queue's "ready" badge was computed in the browser from title, tags and alt text:
metadata completeness, not delivery readiness. It answered a question nobody asked.
A post could be green while Instagram was disconnected, the original file had been
moved off disk, or a carousel had lost a frame — none of which is visible until the
worker tries and fails, hours later and out of sight.

Two levels, because they mean different things:

  blocker  this destination cannot publish as things stand
  warning  it will publish, but worse than it should

Blockers are not predictions of certain failure — a token can be revoked between this
check and delivery, which is why publishing validates again on its own. They are the
failures knowable in advance, said out loud while the photographer can still act.

Deliberately has no scheduler import: it is read-only, called per row on list
endpoints, and must stay cheap. Pass `creds` when checking many posts so the
credential lookup happens once rather than per post.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PlatformCredential, Post
from services import carousel as carousel_svc, r2, storage
from services.platforms import instagram as ig

BLOCKER = "blocker"
WARNING = "warning"

FANOUT_PLATFORMS = ("bluesky", "pixelfed", "pinterest", "instagram")


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str
    destination: str | None = None

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code,
                "message": self.message, "destination": self.destination}


def load_credentials(db: Session) -> list[PlatformCredential]:
    return list(db.execute(select(PlatformCredential)).scalars().all())


def targets_for(post: Post, creds: list[PlatformCredential]) -> list[str]:
    """The destinations this post is aimed at.

    Mirrors the worker: an explicit target_platforms list wins; otherwise Flickr plus
    every connected default_target platform.
    """
    raw = (post.target_platforms or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(p) for p in parsed]
        except (ValueError, TypeError):
            pass
    return ["flickr"] + [
        c.platform for c in creds
        if c.platform in FANOUT_PLATFORMS and c.default_target == 1 and c.access_token
    ]


def _image_available(post: Post) -> bool:
    """The worker falls back to the cached preview when the original is gone."""
    if post.original_path and Path(post.original_path).exists():
        return True
    try:
        return storage.preview_path(post.id).exists()
    except Exception:  # noqa: BLE001 — a storage misconfiguration is not this call's problem
        return False


def check(db: Session, post: Post, *, creds: list[PlatformCredential] | None = None) -> list[Finding]:
    """Everything known to be wrong with delivering this post, worst first."""
    creds = load_credentials(db) if creds is None else creds
    by_platform = {c.platform: c for c in creds}
    targets = targets_for(post, creds)
    out: list[Finding] = []

    # --- the photo itself ----------------------------------------------------
    if not _image_available(post):
        out.append(Finding(
            BLOCKER, "image_missing",
            "The image file is missing — the original has moved and no preview is cached.",
        ))

    # --- carousel shape ------------------------------------------------------
    if carousel_svc.is_lead(post):
        n = len(carousel_svc.members(db, post.carousel_id))
        if n < 2:
            out.append(Finding(
                BLOCKER, "carousel_too_small",
                f"A carousel needs at least 2 frames; this one has {n}.",
            ))
        elif n > ig.MAX_CAROUSEL:
            out.append(Finding(
                BLOCKER, "carousel_too_large",
                f"Instagram takes at most {ig.MAX_CAROUSEL} frames; this carousel has {n}.",
                destination="instagram",
            ))

    # --- per destination -----------------------------------------------------
    for name in targets:
        cred = by_platform.get(name)
        if cred is None or not cred.access_token:
            out.append(Finding(
                BLOCKER, "not_connected",
                f"{name.title()} isn't connected — Settings → Platforms.",
                destination=name,
            ))
            continue
        if cred.auth_status == "reauth_required":
            out.append(Finding(
                BLOCKER, "reauth_required",
                f"{name.title()} needs reconnecting: {cred.auth_error or 'authorisation expired'}",
                destination=name,
            ))
            continue

        if name == "instagram" and not post.flickr_photo_id and not r2.configured():
            # Meta ingests from a public URL. Without R2 staging that URL is the Flickr
            # rendition, so a post not going to Flickr has nowhere for Meta to fetch from.
            out.append(Finding(
                BLOCKER, "no_public_image_url",
                "Instagram needs the image on a public URL — configure R2 staging, or "
                "include Flickr in this post's destinations.",
                destination="instagram",
            ))
        if name == "pinterest" and not post.flickr_url and "flickr" not in targets:
            out.append(Finding(
                WARNING, "pin_without_link",
                "This pin will have no link back to the photo, which is most of why "
                "pinning is worth doing.",
                destination="pinterest",
            ))

    # --- content quality -----------------------------------------------------
    if not (post.title or "").strip():
        out.append(Finding(WARNING, "no_title", "No title."))
    if not (post.tags or "").strip():
        out.append(Finding(WARNING, "no_tags", "No tags — nothing will find this in search."))
    if not (post.alt_text or "").strip():
        # Empty is not done. The old check treated "" as complete because the AI sweep
        # had run, which conflated "we tried" with "there is alt text".
        out.append(Finding(
            WARNING, "no_alt_text",
            "No alt text — the photo is unreadable to anyone using a screen reader.",
        ))

    out.sort(key=lambda f: 0 if f.level == BLOCKER else 1)
    return out


def summarize(findings: list[Finding]) -> dict:
    blockers = [f for f in findings if f.level == BLOCKER]
    warnings = [f for f in findings if f.level == WARNING]
    return {
        "ready": not blockers and not warnings,
        "deliverable": not blockers,
        "blockers": [f.as_dict() for f in blockers],
        "warnings": [f.as_dict() for f in warnings],
    }
