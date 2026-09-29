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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from services import http_client, redact
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from crypto import decrypt_token, encrypt_token
from models import PlatformCredential
from services.platforms import credentials

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
        # Masked at birth: this text is logged, stored as error_message and shown in
        # Settings, and it can quote a request or a platform's echo of one.
        super().__init__(redact.redact(message))
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
    return http_client.client(timeout=60.0)


def begin_connect(db: Session, *, redirect_uri: str) -> tuple[str, str]:
    """Start the OAuth flow. Persists a half-connected credential keyed by state nonce."""
    app_id, _ = _require_app_keys()
    state = uuid.uuid4().hex

    # In-flight state goes beside the live connection, not in place of it. Deleting the
    # row here (as this used to) killed a working connection the moment the user clicked
    # Connect — before Pinterest had even asked them — and, pre-0033, cascaded through
    # every Pinterest post_platforms row. Nothing live changes until the callback works.
    cred = credentials.upsert(db, PLATFORM)
    if not cred.access_token:
        cred.key_version = KEY_VERSION
    credentials.set_pending_oauth(cred, {"redirect_uri": redirect_uri, "state": state})
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
    redact.remember(code)   # single-use, but the exchange's error text can quote it
    row = credentials.get(db, PLATFORM)
    pending = credentials.pending_oauth(row)
    if not pending:
        raise PinterestError("No pending Pinterest connection — start over from Settings.", permanent=True)
    if pending.get("state") != state:
        raise PinterestError("OAuth state mismatch — possible CSRF, please retry.", permanent=True)

    redirect_uri = pending["redirect_uri"]
    try:
        previous = json.loads(row.extra_json or "{}")
    except ValueError:
        previous = {}

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
            f"Token exchange failed (HTTP {r.status_code}): {redact.clip(r.text, 300)}",
            permanent=(r.status_code in (400, 401, 403)),
        )
    token_body = r.json()
    access_token = token_body["access_token"]
    refresh_token = token_body.get("refresh_token")
    # Fresh from the exchange: redact() must know them before user_account is asked —
    # crypto only sees them at encryption, after that call (and its error text).
    redact.remember(access_token)
    redact.remember(refresh_token)
    expires_in = token_body.get("expires_in")

    # Fetch user account.
    with _client() as c:
        r = c.get(
            f"{API_BASE}/user_account",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code >= 400:
        raise PinterestError(f"user_account fetch failed (HTTP {r.status_code}): {redact.clip(r.text, 200)}")
    account = r.json()
    username = account.get("username") or ""

    # Same row, same id: pins already made stay attached to this connection.
    row.access_token = encrypt_token(access_token)
    row.refresh_token = encrypt_token(refresh_token) if refresh_token else None
    if expires_in:
        row.token_expires = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).replace(tzinfo=None)
    row.key_version = KEY_VERSION
    credentials.mark_connected(row, account_name=username)
    row.last_success_at = datetime.now(timezone.utc)
    # A reconnect of the same account keeps its default board — re-picking it after
    # every token renewal is busywork, and until it is re-picked every pin fails.
    same_account = previous.get("user_id") in (None, account.get("id"))
    row.extra_json = json.dumps({
        "user_id": account.get("id"),
        "profile_url": f"https://www.pinterest.com/{username}/" if username else None,
        "account_type": account.get("account_type"),
        "default_board_id": previous.get("default_board_id") if same_account else None,
        "default_board_name": previous.get("default_board_name") if same_account else None,
    })
    db.commit()
    db.refresh(row)
    log.info("pinterest connected: account=%s", username)
    return row


def disconnect(db: Session) -> bool:
    return credentials.disconnect(db, PLATFORM, keep_extra=(
        "user_id", "profile_url", "default_board_id", "default_board_name"))


def current_status(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        return {"connected": False, "account": None}
    extra = json.loads(row.extra_json or "{}")
    return {
        "connected": bool(row.access_token),
        "pending": credentials.pending_oauth(row) is not None,
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
            f"Pinterest token refresh failed (HTTP {r.status_code}): {redact.clip(r.text, 300)}",
            permanent=(r.status_code in (400, 401, 403)),
        )
    body = r.json()
    access_token = body["access_token"]
    redact.remember(body.get("refresh_token"))
    row.access_token = encrypt_token(access_token)
    # A freshly stored token means the channel is authorised again — drop any
    # "needs reconnecting" flag so the health banner clears immediately rather
    # than waiting for the next scheduled post to prove it.
    row.auth_status, row.auth_error, row.auth_flagged_at = "ok", None, None
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
            raise PinterestError(f"list boards failed (HTTP {r.status_code}): {redact.clip(r.text, 200)}")
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
            f"pin create failed (HTTP {r.status_code}): {redact.clip(r.text, 400)}",
            permanent=(r.status_code in (400, 401, 403, 422)),
        )
    pin = r.json()
    pin_id = pin["id"]
    return {
        "remote_id": pin_id,
        "url": f"https://www.pinterest.com/pin/{pin_id}/",
    }


# -----------------------------------------------------------------------------
# Pin analytics (GET /v5/pins/{pin_id}/analytics) -- read by comments.sync_all
# -----------------------------------------------------------------------------

# All eight are StandardPinMetricTypes in Pinterest's published v5 OpenAPI spec
# (pinterest/api-description, v5.28.0). A metric the API refuses for a given pin fails
# the whole request, so a refusal drops to the four core metrics before giving up --
# the same two-tier ask comments.py makes of Instagram insights.
ANALYTICS_METRICS = (
    "IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK",
    "TOTAL_COMMENTS", "TOTAL_REACTIONS", "PROFILE_VISIT", "USER_FOLLOW",
)
ANALYTICS_METRICS_MIN = ("IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK")
# start_date "cannot be more than 90 days back from today". One day of margin so a
# request built just before UTC midnight isn't refused just after it.
ANALYTICS_MAX_DAYS_BACK = 89


def analytics_token(db: Session) -> str:
    """A usable access token for the analytics pass, refreshed if it is about to lapse.

    Raises PinterestError when Pinterest isn't connected; the caller only asks when a
    credential with a token exists, so that means the token itself has gone bad.
    """
    return _refresh_if_needed(db, _load_credential(db))


def fetch_pin_analytics(
    access_token: str, pin_id: str, *, since: date, today: date
) -> dict[str, int] | None:
    """Totals for one pin from `since` (its posting day) to `today`, keyed by metric.

    Pinterest reports daily buckets, not running counts, so the pin's totals are the
    summary over every day it has existed. That is only possible inside the 90-day
    look-back the endpoint allows; an older pin gets its last 89 days, which is why the
    sync never asks about pins older than that (comments.DEFAULT_LOOKBACK_DAYS).

    Days still PROCESSING are included as Pinterest currently has them. The totals are
    stored as a snapshot each day, so a lagging day is corrected by the next reading
    rather than lost.

    Returns None when the pin no longer exists (deleted on Pinterest); raises
    PinterestError on any other failure.
    """
    start = max(since, today - timedelta(days=ANALYTICS_MAX_DAYS_BACK))
    start = min(start, today)
    last: httpx.Response | None = None
    for metrics in (ANALYTICS_METRICS, ANALYTICS_METRICS_MIN):
        with _client() as c:
            r = c.get(
                f"{API_BASE}/pins/{pin_id}/analytics",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "start_date": start.isoformat(),
                    "end_date": today.isoformat(),
                    "metric_types": ",".join(metrics),
                },
            )
        if r.status_code == 404:
            return None
        if r.status_code < 400:
            return _pin_totals(r.json())
        last = r
        if r.status_code != 400:
            break
    assert last is not None
    raise PinterestError(
        f"pin analytics failed (HTTP {last.status_code}): {redact.clip(last.text, 200)}",
        permanent=(last.status_code in (401, 403)),
    )


def _pin_totals(body: Any) -> dict[str, int]:
    """Flatten the analytics response to {metric: int}.

    The body is keyed by app type -- "all" when the request is not split, which is
    the only way this module asks. The spec types it as a free-form map, so anything
    else is read from its first entry rather than trusted to a key name. summary_metrics
    is the period total; lifetime_metrics (comments/reactions on newer pins) fills in
    what the summary lacks.
    """
    if not isinstance(body, dict) or not body:
        return {}
    block = body.get("all")
    if not isinstance(block, dict):
        block = next((v for v in body.values() if isinstance(v, dict)), {})
    out: dict[str, int] = {}
    for source in (block.get("lifetime_metrics"), block.get("summary_metrics")):
        for name, value in (source or {}).items():
            try:
                out[name] = int(round(float(value)))
            except (TypeError, ValueError):
                continue
    return out
