"""Pixelfed integration via the Mastodon-compatible OAuth 2.0 + REST API.

Auth flow (the broken-PAT-page workaround):
  1. POST {instance}/api/v1/apps  →  client_id, client_secret, this is FramePost registering itself
  2. Redirect user to {instance}/oauth/authorize?response_type=code&...   →  user approves
  3. Callback to FramePost with ?code=...
  4. POST {instance}/oauth/token with the code  →  access_token (long-lived, no expiry by default)
  5. GET {instance}/api/v1/accounts/verify_credentials  →  pull username/avatar to display

Posting:
  1. POST /api/v1/media (multipart) with the JPEG  →  media_id
  2. POST /api/v1/statuses with status text + media_ids[]  →  status object

Same code path will work for Mastodon when we wire it up — Pixelfed implements the Mastodon
client API, so platform_kind="mastodon" with a different instance_url is the only delta.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from services import http_client
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token, encrypt_token
from models import PlatformCredential
from services.platforms import credentials

log = logging.getLogger("framepost.pixelfed")

PLATFORM = "pixelfed"
KEY_VERSION = 1
APP_NAME = "FramePost"
APP_WEBSITE = "https://framepost.local"  # informational; Pixelfed shows this on the consent screen
SCOPES = "read write"


class PixelfedError(Exception):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class PendingApp:
    """Held in the DB row temporarily between /connect and /callback. We persist the partial
    PlatformCredential with extra_json carrying client_id/secret + state nonce; access_token
    stays empty until the OAuth dance completes."""
    instance_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    state: str


def _client(instance_url: str) -> httpx.Client:
    return http_client.client(base_url=instance_url.rstrip("/"), timeout=30.0)


def _normalize_instance(instance_url: str) -> str:
    instance_url = instance_url.strip().rstrip("/")
    if not instance_url.startswith(("http://", "https://")):
        instance_url = "https://" + instance_url
    return instance_url


def begin_connect(
    db: Session,
    *,
    instance_url: str,
    redirect_uri: str,
) -> tuple[str, str]:
    """Register FramePost on the user's instance and produce the authorize URL.

    Returns (authorize_url, state). The caller should redirect the browser to authorize_url;
    the callback will arrive at redirect_uri with ?code=... and ?state=... matching what we
    return here. We persist the partial credential so the callback can look it up by state.
    """
    instance_url = _normalize_instance(instance_url)

    # Step 1: register the app on this instance.
    with _client(instance_url) as c:
        r = c.post(
            "/api/v1/apps",
            data={
                "client_name": APP_NAME,
                "redirect_uris": redirect_uri,
                "scopes": SCOPES,
                "website": APP_WEBSITE,
            },
        )
    if r.status_code >= 400:
        raise PixelfedError(
            f"Couldn't register app on {instance_url} (HTTP {r.status_code}): {r.text[:200]}",
            permanent=(r.status_code == 404),
        )
    app = r.json()
    client_id = app["client_id"]
    client_secret = app["client_secret"]
    state = uuid.uuid4().hex

    # Park the in-flight OAuth state beside the live connection, never in place of it.
    # This used to delete the existing row before the user had even seen the consent
    # screen — so an abandoned reconnect killed a working connection, and (pre-0033) the
    # delete cascaded through every Pixelfed post_platforms row. The tokens and the row
    # id are untouched until complete_connect succeeds.
    cred = credentials.upsert(db, PLATFORM)
    if not cred.access_token:
        cred.key_version = KEY_VERSION
    credentials.set_pending_oauth(cred, {
        "instance_url": instance_url,
        "client_id": client_id,
        "client_secret": encrypt_token(client_secret),
        "redirect_uri": redirect_uri,
        "state": state,
    })
    db.commit()

    # Step 2: build the authorize URL the browser will be redirected to.
    from urllib.parse import urlencode
    qs = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    })
    authorize_url = f"{instance_url}/oauth/authorize?{qs}"
    return authorize_url, state


def complete_connect(db: Session, *, code: str, state: str) -> PlatformCredential:
    """Exchange the authorization code for an access token, persist it, fetch account info."""
    row = credentials.get(db, PLATFORM)
    pending = credentials.pending_oauth(row)
    if not pending:
        raise PixelfedError("No pending Pixelfed connection — start over from Settings.", permanent=True)
    if pending.get("state") != state:
        raise PixelfedError("OAuth state mismatch — possible CSRF, please retry.", permanent=True)

    client_id = pending["client_id"]
    client_secret = decrypt_token(pending["client_secret"])
    redirect_uri = pending["redirect_uri"]
    # Pre-0033 pending rows kept the instance on the row itself.
    instance_url = pending.get("instance_url") or row.instance_url or ""

    with _client(instance_url) as c:
        r = c.post(
            "/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code": code,
                "scope": SCOPES,
            },
        )
    if r.status_code >= 400:
        raise PixelfedError(
            f"Token exchange failed (HTTP {r.status_code}): {r.text[:300]}",
            permanent=(r.status_code in (400, 401, 403)),
        )
    token_body = r.json()
    access_token = token_body["access_token"]

    # Verify by fetching the account.
    with _client(instance_url) as c:
        r = c.get(
            "/api/v1/accounts/verify_credentials",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code >= 400:
        raise PixelfedError(f"verify_credentials failed (HTTP {r.status_code}): {r.text[:200]}")
    account = r.json()

    # Same row, same id: whatever this account already published stays attached to it.
    row.access_token = encrypt_token(access_token)
    row.instance_url = instance_url
    row.key_version = KEY_VERSION
    credentials.mark_connected(
        row, account_name=account.get("acct") or account.get("username") or "")
    row.last_success_at = datetime.now(timezone.utc)
    row.extra_json = json.dumps({
        "client_id": client_id,
        "client_secret": encrypt_token(client_secret),
        "redirect_uri": redirect_uri,
        "account_id": account.get("id"),
        "display_name": account.get("display_name"),
        "url": account.get("url"),
    })
    db.commit()
    db.refresh(row)
    log.info("pixelfed connected: account=%s instance=%s", row.account_name, instance_url)
    return row


def disconnect(db: Session) -> bool:
    return credentials.disconnect(
        db, PLATFORM, keep_extra=("account_id", "display_name", "url"))


def current_status(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        return {"connected": False, "account": None, "instance_url": None}
    extra = json.loads(row.extra_json or "{}")
    return {
        "connected": bool(row.access_token),
        "pending": credentials.pending_oauth(row) is not None,
        "account": row.account_name,
        "instance_url": row.instance_url,
        "profile_url": extra.get("url"),
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "default_target": bool(row.default_target),
    }


# Mastodon-compatible attachment ceiling, which Pixelfed follows. A carousel bigger than
# this is threaded rather than truncated.
MAX_IMAGES = 4


def _decode(r, what: str) -> dict:
    """Parse a Pixelfed response body, or say something useful about why it won't.

    The status-code guards at each call site catch 4xx and 5xx. This catches the other
    failure: a 2xx carrying a body that is not JSON at all. pixelfed.social sits behind
    a proxy that has served HTML maintenance and rate-limit pages with a 200, and
    `r.json()` then raises `Expecting value: line 1 column 1 (char 0)` -- a parser
    complaining about byte zero, which says nothing about Pixelfed, the request, or what
    to do next. Four posts failed exactly this way in May and June 2026, retried six
    times each, and the only reason ever recorded was that sentence.

    Transient: a proxy serving the wrong page is precisely the case worth retrying.
    """
    try:
        return r.json()
    except ValueError:
        body = (r.text or "").strip()
        looks_html = body[:1] == "<"
        raise PixelfedError(
            f"{what}: Pixelfed returned HTTP {r.status_code} with "
            f"{'an HTML page' if looks_html else 'a non-JSON body'} instead of JSON "
            f"({len(body)} bytes): {body[:200]!r}"
        ) from None


def _load_credential(db: Session) -> PlatformCredential:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row or not row.access_token:
        raise PixelfedError("Pixelfed is not connected.", permanent=True)
    return row


def post_photo(
    db: Session,
    *,
    src: Path,
    text: str,
    alt_text: str | None = None,
    visibility: str = "public",
) -> dict:
    """Upload media + create status. Returns {remote_id, url}."""
    return post_photos(db, frames=[(src, alt_text)], text=text, visibility=visibility)


def post_thread(
    db: Session,
    *,
    frames: list[tuple[Path, str | None]],
    text: str,
    visibility: str = "public",
) -> dict:
    """Post frames as one status, or as a reply chain when there are more than
    MAX_IMAGES.

    The caption and its hashtags go on the root only. Repeating them on every
    continuation is the thing that reads as bot output, and a reply is already attached
    to the root, so restating it gains nothing.

    Returns the ROOT's {remote_id, url} — the status engagement accrues to.
    """
    if not frames:
        raise PixelfedError("nothing to post", permanent=True)

    chunks = [frames[i:i + MAX_IMAGES] for i in range(0, len(frames), MAX_IMAGES)]
    root = post_photos(db, frames=chunks[0], text=text, visibility=visibility)
    parent_id = root["remote_id"]
    for chunk in chunks[1:]:
        parent = post_photos(
            db, frames=chunk, text="", visibility=visibility, in_reply_to=parent_id,
        )
        parent_id = parent["remote_id"]
    if len(chunks) > 1:
        log.info("pixelfed: %d frames posted as a thread of %d", len(frames), len(chunks))
    return root


def post_photos(
    db: Session,
    *,
    frames: list[tuple[Path, str | None]],
    text: str,
    visibility: str = "public",
    in_reply_to: str | None = None,
) -> dict:
    """One status carrying up to MAX_IMAGES images. Returns {remote_id, url}."""
    if not 1 <= len(frames) <= MAX_IMAGES:
        raise PixelfedError(
            f"a status takes 1..{MAX_IMAGES} images, got {len(frames)}",
            permanent=True,
        )
    row = _load_credential(db)
    access_token = decrypt_token(row.access_token)
    instance_url = row.instance_url or ""
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: media upload, one call per image.
    media_ids: list[str] = []
    for s, alt in frames:
        with open(s, "rb") as f:
            files = {"file": (s.name, f, "image/jpeg")}
            data = {"description": (alt or "")[:1500]}  # Mastodon caps alt around 1500
            with _client(instance_url) as c:
                r = c.post("/api/v1/media", headers=headers, files=files, data=data)
        if r.status_code >= 400:
            raise PixelfedError(
                f"media upload failed (HTTP {r.status_code}): {r.text[:300]}",
                permanent=(r.status_code in (400, 401, 403, 422)),
            )
        media_ids.append(_decode(r, "media upload")["id"])

    # Step 2: status post. A list value is how httpx repeats a form key, which is what
    # media_ids[] needs for more than one attachment — a list of (key, value) TUPLES is
    # not accepted by httpx's form encoder and blows up at send time, not at call time.
    payload: dict[str, object] = {
        "status": text or "",
        "visibility": visibility,
        "media_ids[]": media_ids,
    }
    if in_reply_to:
        payload["in_reply_to_id"] = in_reply_to
    with _client(instance_url) as c:
        r = c.post("/api/v1/statuses", headers=headers, data=payload)
    if r.status_code >= 400:
        raise PixelfedError(
            f"status post failed (HTTP {r.status_code}): {r.text[:300]}",
            permanent=(r.status_code in (400, 401, 403, 422)),
        )
    status = _decode(r, "status post")
    return {
        "remote_id": status["id"],
        "url": status.get("url") or status.get("uri"),
    }
