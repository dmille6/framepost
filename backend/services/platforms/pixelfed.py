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
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token, encrypt_token
from models import PlatformCredential

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
    return httpx.Client(base_url=instance_url.rstrip("/"), timeout=30.0)


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

    # Persist a half-connected credential. We delete any prior pixelfed connection so there's
    # only ever one in flight at a time (single-account model for v1).
    existing = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    cred = PlatformCredential(
        id=str(uuid.uuid4()),
        platform=PLATFORM,
        access_token=None,  # filled in by callback
        instance_url=instance_url,
        extra_json=json.dumps({
            "client_id": client_id,
            "client_secret": encrypt_token(client_secret),
            "redirect_uri": redirect_uri,
            "state": state,
            "pending": True,
        }),
        connected_at=datetime.now(timezone.utc),
        key_version=KEY_VERSION,
    )
    db.add(cred)
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
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        raise PixelfedError("No pending Pixelfed connection — start over from Settings.", permanent=True)

    extra = json.loads(row.extra_json or "{}")
    if not extra.get("pending"):
        raise PixelfedError("This Pixelfed connection has already been completed.", permanent=True)
    if extra.get("state") != state:
        raise PixelfedError("OAuth state mismatch — possible CSRF, please retry.", permanent=True)

    client_id = extra["client_id"]
    client_secret = decrypt_token(extra["client_secret"])
    redirect_uri = extra["redirect_uri"]
    instance_url = row.instance_url or ""

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

    row.access_token = encrypt_token(access_token)
    row.account_name = account.get("acct") or account.get("username") or ""
    row.last_success_at = datetime.now(timezone.utc)
    row.last_error = None
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
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def current_status(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        return {"connected": False, "account": None, "instance_url": None}
    extra = json.loads(row.extra_json or "{}")
    return {
        "connected": bool(row.access_token),
        "pending": bool(extra.get("pending")),
        "account": row.account_name,
        "instance_url": row.instance_url,
        "profile_url": extra.get("url"),
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "default_target": bool(row.default_target),
    }


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
    row = _load_credential(db)
    access_token = decrypt_token(row.access_token)
    instance_url = row.instance_url or ""
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: media upload.
    with open(src, "rb") as f:
        files = {"file": (src.name, f, "image/jpeg")}
        data = {"description": (alt_text or "")[:1500]}  # Mastodon caps alt around 1500
        with _client(instance_url) as c:
            r = c.post("/api/v1/media", headers=headers, files=files, data=data)
    if r.status_code >= 400:
        raise PixelfedError(
            f"media upload failed (HTTP {r.status_code}): {r.text[:300]}",
            permanent=(r.status_code in (400, 401, 403, 422)),
        )
    media = r.json()
    media_id = media["id"]

    # Step 2: status post.
    payload = {
        "status": text or "",
        "media_ids[]": media_id,
        "visibility": visibility,
    }
    with _client(instance_url) as c:
        r = c.post("/api/v1/statuses", headers=headers, data=payload)
    if r.status_code >= 400:
        raise PixelfedError(
            f"status post failed (HTTP {r.status_code}): {r.text[:300]}",
            permanent=(r.status_code in (400, 401, 403, 422)),
        )
    status = r.json()
    return {
        "remote_id": status["id"],
        "url": status.get("url") or status.get("uri"),
    }
