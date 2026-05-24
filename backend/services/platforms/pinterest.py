"""Pinterest integration via API v5 (OAuth 2.0).

Auth flow:
  1. User clicks Connect → we redirect to https://www.pinterest.com/oauth/ with our
     PINTEREST_APP_ID, the requested scopes, our callback URL, and a random state.
  2. User approves on Pinterest → Pinterest redirects to our callback with ?code=...&state=...
  3. We POST to /v5/oauth/token (Basic auth with app id + secret) to exchange the code
     for an access_token + refresh_token + expires_in.
  4. We fetch /v5/user_account to display the username.

Posting:
  1. Caller sets a default board once in Settings → Platforms (post_pin reads it from
     extra_json.default_board_id).
  2. POST /v5/pins with title + description + link (the photo's Flickr URL — drives the
     killer per-pin referral traffic Pinterest gives you) + media_source.image_base64.

Pinterest access tokens expire ~30 days; refresh tokens last 1 year. We auto-refresh
inside post_pin when the access token is within 5 minutes of expiry.
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from crypto import decrypt_token, encrypt_token
from models import PlatformCredential

log = logging.getLogger("framepost.pinterest")

PLATFORM = "pinterest"
KEY_VERSION = 1
AUTH_URL = "https://www.pinterest.com/oauth/"
API_BASE = "https://api.pinterest.com/v5"
# Comma-separated per Pinterest docs (space-separated also works; comma is what the dev
# portal shows in examples).
SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"
REFRESH_LEEWAY = timedelta(minutes=5)


class PinterestError(Exception):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _require_app_keys() -> tuple[str, str]:
    if not settings.pinterest_app_id or not settings.pinterest_app_secret:
        raise PinterestError(
            "Pinterest app keys not configured. Set PINTEREST_APP_ID and "
            "PINTEREST_APP_SECRET in .env (register at developers.pinterest.com).",
            permanent=True,
        )
    return settings.pinterest_app_id, settings.pinterest_app_secret


def _basic_auth_header() -> str:
    app_id, app_secret = _require_app_keys()
    return "Basic " + base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()


def _client() -> httpx.Client:
    return httpx.Client(timeout=60.0)


def begin_connect(db: Session, *, redirect_uri: str) -> tuple[str, str]:
    """Start the OAuth flow. Persists a half-connected credential keyed by state nonce."""
    app_id, _ = _require_app_keys()
    state = uuid.uuid4().hex

    # Replace any prior pinterest credential — single-account model for v1.
    existing = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    cred = PlatformCredential(
        id=str(uuid.uuid4()),
        platform=PLATFORM,
        access_token=None,
        extra_json=json.dumps({
            "redirect_uri": redirect_uri,
            "state": state,
            "pending": True,
        }),
        connected_at=datetime.now(timezone.utc),
        key_version=KEY_VERSION,
    )
    db.add(cred)
    db.commit()

    qs = urlencode({
        "response_type": "code",
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    })
    return f"{AUTH_URL}?{qs}", state


def complete_connect(db: Session, *, code: str, state: str) -> PlatformCredential:
    """Exchange the authorization code for an access token, persist it, fetch account info."""
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        raise PinterestError("No pending Pinterest connection — start over from Settings.", permanent=True)

    extra = json.loads(row.extra_json or "{}")
    if not extra.get("pending"):
        raise PinterestError("This Pinterest connection has already been completed.", permanent=True)
    if extra.get("state") != state:
        raise PinterestError("OAuth state mismatch — possible CSRF, please retry.", permanent=True)

    redirect_uri = extra["redirect_uri"]

    with _client() as c:
        r = c.post(
            f"{API_BASE}/oauth/token",
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    if r.status_code >= 400:
        raise PinterestError(
            f"Token exchange failed (HTTP {r.status_code}): {r.text[:300]}",
            permanent=(r.status_code in (400, 401, 403)),
        )
    token_body = r.json()
    access_token = token_body["access_token"]
    refresh_token = token_body.get("refresh_token")
    expires_in = token_body.get("expires_in")

    # Fetch user account.
    with _client() as c:
        r = c.get(
            f"{API_BASE}/user_account",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code >= 400:
        raise PinterestError(f"user_account fetch failed (HTTP {r.status_code}): {r.text[:200]}")
    account = r.json()
    username = account.get("username") or ""

    row.access_token = encrypt_token(access_token)
    row.refresh_token = encrypt_token(refresh_token) if refresh_token else None
    if expires_in:
        row.token_expires = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).replace(tzinfo=None)
    row.account_name = username
    row.last_success_at = datetime.now(timezone.utc)
    row.last_error = None
    row.extra_json = json.dumps({
        "user_id": account.get("id"),
        "profile_url": f"https://www.pinterest.com/{username}/" if username else None,
        "account_type": account.get("account_type"),
        "default_board_id": None,
        "default_board_name": None,
    })
    db.commit()
    db.refresh(row)
    log.info("pinterest connected: account=%s", username)
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
        return {"connected": False, "account": None}
    extra = json.loads(row.extra_json or "{}")
    return {
        "connected": bool(row.access_token),
        "pending": bool(extra.get("pending")),
        "account": row.account_name,
        "profile_url": extra.get("profile_url"),
        "default_board_id": extra.get("default_board_id"),
        "default_board_name": extra.get("default_board_name"),
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "default_target": bool(row.default_target),
        "token_expires": row.token_expires.isoformat() if row.token_expires else None,
    }


def _load_credential(db: Session) -> PlatformCredential:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row or not row.access_token:
        raise PinterestError("Pinterest is not connected.", permanent=True)
    return row


def _refresh_if_needed(db: Session, row: PlatformCredential) -> str:
    """Return a valid access_token, refreshing in-place if it's expired or expiring soon."""
    if row.token_expires:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if (row.token_expires - now) > REFRESH_LEEWAY:
            return decrypt_token(row.access_token)

    if not row.refresh_token:
        raise PinterestError(
            "Pinterest access token has expired and no refresh token is available — reconnect from Settings.",
            permanent=True,
        )

    refresh = decrypt_token(row.refresh_token)
    with _client() as c:
        r = c.post(
            f"{API_BASE}/oauth/token",
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "scope": SCOPES,
            },
        )
    if r.status_code >= 400:
        raise PinterestError(
            f"Pinterest token refresh failed (HTTP {r.status_code}): {r.text[:300]}",
            permanent=(r.status_code in (400, 401, 403)),
        )
    body = r.json()
    access_token = body["access_token"]
    row.access_token = encrypt_token(access_token)
    if body.get("refresh_token"):
        row.refresh_token = encrypt_token(body["refresh_token"])
    if body.get("expires_in"):
        row.token_expires = (
            datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))
        ).replace(tzinfo=None)
    db.commit()
    log.info("pinterest token refreshed (account=%s)", row.account_name)
    return access_token


def list_boards(db: Session) -> list[dict[str, Any]]:
    """Return all boards owned by the connected user (paginated, all pages)."""
    row = _load_credential(db)
    access_token = _refresh_if_needed(db, row)
    out: list[dict[str, Any]] = []
    bookmark: str | None = None
    while True:
        params: dict[str, str] = {"page_size": "100"}
        if bookmark:
            params["bookmark"] = bookmark
        with _client() as c:
            r = c.get(
                f"{API_BASE}/boards",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
        if r.status_code >= 400:
            raise PinterestError(f"list boards failed (HTTP {r.status_code}): {r.text[:200]}")
        body = r.json()
        for b in body.get("items", []):
            out.append({
                "id": b["id"],
                "name": b.get("name", ""),
                "privacy": b.get("privacy"),
                "pin_count": b.get("pin_count"),
            })
        bookmark = body.get("bookmark")
        if not bookmark:
            break
    return out


def set_default_board(db: Session, *, board_id: str, board_name: str) -> None:
    row = _load_credential(db)
    extra = json.loads(row.extra_json or "{}")
    extra["default_board_id"] = board_id
    extra["default_board_name"] = board_name
    row.extra_json = json.dumps(extra)
    db.commit()
    log.info("pinterest default board set: %s (%s)", board_name, board_id)


def post_pin(
    db: Session,
    *,
    src: Path,
    title: str | None,
    description: str | None,
    tags: str | None,
    link: str | None,
    alt_text: str | None = None,
) -> dict[str, str]:
    """Create a pin on the user's default board.

    Image is sent inline as base64. The link field is the killer Pinterest feature — every
    pin perpetually links back to the source (we pass the photo's Flickr URL), so Pinterest
    traffic compounds back to the canonical photo page.
    """
    row = _load_credential(db)
    extra = json.loads(row.extra_json or "{}")
    board_id = extra.get("default_board_id")
    if not board_id:
        raise PinterestError(
            "No default Pinterest board selected — pick one in Settings → Platforms first.",
            permanent=True,
        )

    access_token = _refresh_if_needed(db, row)

    with open(src, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    desc = (description or "").strip()
    tag_str = (tags or "").strip()
    if tag_str:
        # Pinterest doesn't have an official hashtag concept (search uses keywords), but
        # hashtags in description are tolerated and don't hurt. Append a hashtag block at
        # the bottom of the description, capped to keep us under the 800-char total.
        hashtags: list[str] = []
        seen: set[str] = set()
        for raw in tag_str.split():
            cleaned = "".join(ch for ch in raw.lower() if ch.isalnum() or ch == "_")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                hashtags.append(f"#{cleaned}")
        if hashtags:
            tag_block = " ".join(hashtags)
            joined = f"{desc}\n\n{tag_block}".strip() if desc else tag_block
            desc = joined

    payload: dict[str, Any] = {
        "board_id": board_id,
        "title": (title or "")[:100],
        "description": desc[:800],
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/jpeg",
            "data": img_b64,
        },
    }
    if link:
        payload["link"] = link
    if alt_text:
        payload["alt_text"] = alt_text[:500]

    with _client() as c:
        r = c.post(
            f"{API_BASE}/pins",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if r.status_code >= 400:
        raise PinterestError(
            f"pin create failed (HTTP {r.status_code}): {r.text[:400]}",
            permanent=(r.status_code in (400, 401, 403, 422)),
        )
    pin = r.json()
    pin_id = pin["id"]
    return {
        "remote_id": pin_id,
        "url": f"https://www.pinterest.com/pin/{pin_id}/",
    }
