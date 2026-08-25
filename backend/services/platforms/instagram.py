"""Instagram integration via the Instagram API with Instagram Login (graph.instagram.com).

Meta's July-2024 "Instagram API with Instagram Login" removed the two blockers that kept
v1 on copy-paste assist (see services/instagram.py): no Facebook Page link is needed and a
single-user app runs fine in Development mode with no app review — the account just needs
a role on the Meta app.

Auth model (no OAuth dance in FramePost):
  1. User creates a Business-type app at developers.facebook.com, adds the Instagram
     product ("API setup with Instagram business login"), adds their IG account, and
     clicks Generate token in the dashboard. That token is already long-lived (~60 days).
  2. They paste it into Settings → Platforms → Instagram. We verify it with GET /me,
     store it encrypted, and stamp token_expires = now + 60 days.
  3. We refresh via GET /refresh_access_token (grant_type=ig_refresh_token) whenever a
     post fires within REFRESH_LEEWAY of expiry, plus a daily scheduler job so a quiet
     account doesn't let the token lapse. Refresh needs no app secret — just the token.

Publishing (two-step "container" flow):
  1. POST /{ig_id}/media with image_url + caption + alt_text  →  container id.
     Meta fetches image_url server-side — it must be a public JPEG. FramePost passes the
     photo's Flickr rendition (the canonical public copy; static URLs are secret-guarded
     and work regardless of Flickr privacy).
  2. Poll GET /{container}?fields=status_code until FINISHED (images are usually
     immediate; we poll briefly to be safe).
  3. POST /{ig_id}/media_publish with creation_id  →  media id, then fetch permalink.

Hard API constraints enforced here / upstream:
  - JPEG only, aspect ratio 4:5 … 1.91:1 (scheduler pre-checks via aspect_ok and fails
    permanently with a pointer at the assist tab — the private-staging-album variant
    generator is the planned fix for portraits).
  - 100 API-published posts per rolling 24h (a non-issue at FramePost's cadence; the
    test endpoint surfaces quota usage anyway).
"""
from __future__ import annotations

import json
import re
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token, encrypt_token
from models import PlatformCredential

log = logging.getLogger("framepost.instagram")

PLATFORM = "instagram"
KEY_VERSION = 1
GRAPH = "https://graph.instagram.com"
API_VERSION = "v23.0"

# Feed-image aspect limits (width/height). Outside this range Meta rejects the container.
MIN_ASPECT = 4 / 5
MAX_ASPECT = 1.91

# Long-lived tokens last ~60 days. Dashboard-generated tokens don't tell us their exact
# expiry, so we assume the full window on connect and let refresh correct it.
TOKEN_LIFETIME = timedelta(days=60)
REFRESH_LEEWAY = timedelta(days=7)

# Meta accepts at most 3 co-authors per media.
MAX_COLLABORATORS = 3

# Instagram caps: caption 2200 chars, alt text 1000.
MAX_CAPTION = 2200
MAX_ALT_TEXT = 1000

# Container status polling. Image containers are typically ready immediately; Meta's
# docs say poll once per minute for videos — for stills a few short beats suffice.
STATUS_POLL_TRIES = 10
STATUS_POLL_INTERVAL = 3.0


class InstagramError(Exception):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def aspect_ok(width: int, height: int) -> bool:
    if not width or not height:
        return True  # unknown dims — let Meta be the judge rather than block the post
    ratio = width / height
    # Small epsilon: Meta accepts exactly-4:5 crops that integer dims land a hair under.
    return (MIN_ASPECT - 0.005) <= ratio <= (MAX_ASPECT + 0.005)


def _client() -> httpx.Client:
    return httpx.Client(base_url=f"{GRAPH}/{API_VERSION}", timeout=60.0)


def _error_text(r: httpx.Response) -> str:
    """Extract Meta's error message; fall back to raw body."""
    try:
        err = r.json().get("error") or {}
        msg = err.get("error_user_msg") or err.get("message") or ""
        if err.get("error_user_title"):
            msg = f"{err['error_user_title']}: {msg}"
        if msg:
            return msg
    except Exception:
        pass
    return r.text[:300]


def _raise_api_error(r: httpx.Response, doing: str) -> None:
    raise InstagramError(
        f"{doing} failed (HTTP {r.status_code}): {_error_text(r)}",
        permanent=(r.status_code in (400, 401, 403)),
    )


# -----------------------------------------------------------------------------
# Connection lifecycle
# -----------------------------------------------------------------------------

def connect(db: Session, *, access_token: str) -> PlatformCredential:
    """Validate a pasted long-lived token via GET /me and persist it encrypted."""
    access_token = access_token.strip()
    try:
        with _client() as c:
            r = c.get("/me", params={
                "fields": "user_id,username,account_type",
                "access_token": access_token,
            })
    except Exception as e:
        raise InstagramError(f"Couldn't reach graph.instagram.com: {e}") from e
    if r.status_code >= 400:
        _raise_api_error(r, "Token validation")
    me = r.json()
    # user_id is the professional-account id the publishing endpoints want; `id` is the
    # app-scoped id. Either works against graph.instagram.com but prefer user_id.
    ig_user_id = str(me.get("user_id") or me.get("id") or "")
    if not ig_user_id:
        raise InstagramError("Token validated but no user id in the response.", permanent=True)
    account_type = me.get("account_type") or ""
    if account_type not in ("", "BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        raise InstagramError(
            f"Account type {account_type} can't publish via API — switch the Instagram "
            "account to a professional (Business or Creator) account first.",
            permanent=True,
        )

    now = datetime.now(timezone.utc)
    existing = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    cred = PlatformCredential(
        id=str(uuid.uuid4()),
        platform=PLATFORM,
        access_token=encrypt_token(access_token),
        token_expires=now + TOKEN_LIFETIME,
        account_name=me.get("username") or "",
        extra_json=json.dumps({
            "ig_user_id": ig_user_id,
            "account_type": account_type,
        }),
        connected_at=now,
        last_success_at=now,
        key_version=KEY_VERSION,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    log.info("instagram connected: @%s (ig_user_id=%s, type=%s)",
             cred.account_name, ig_user_id, account_type or "?")
    return cred


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
        "account": row.account_name,
        "account_type": extra.get("account_type"),
        "profile_url": f"https://www.instagram.com/{row.account_name}/" if row.account_name else None,
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        "token_expires": row.token_expires.isoformat() if row.token_expires else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "default_target": bool(row.default_target),
    }


def _load_credential(db: Session) -> PlatformCredential:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row or not row.access_token:
        raise InstagramError("Instagram is not connected.", permanent=True)
    return row


# -----------------------------------------------------------------------------
# Token refresh
# -----------------------------------------------------------------------------

def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; our writes are always UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _refresh(db: Session, row: PlatformCredential) -> None:
    """Swap the current token for a fresh 60-day one. Token must be ≥24h old — Meta
    rejects refreshing brand-new tokens, which is why connect() doesn't refresh."""
    token = decrypt_token(row.access_token)
    # Note: refresh_access_token is unversioned (no /vXX.X prefix).
    with httpx.Client(base_url=GRAPH, timeout=30.0) as c:
        r = c.get("/refresh_access_token", params={
            "grant_type": "ig_refresh_token",
            "access_token": token,
        })
    if r.status_code >= 400:
        _raise_api_error(r, "Token refresh")
    body = r.json()
    row.access_token = encrypt_token(body["access_token"])
    expires_in = int(body.get("expires_in") or TOKEN_LIFETIME.total_seconds())
    row.token_expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    db.commit()
    log.info("instagram token refreshed; next expiry %s", row.token_expires)


def _maybe_refresh(db: Session, row: PlatformCredential) -> None:
    """Refresh when inside the leeway window. A failed refresh on a still-valid token is
    logged but doesn't block posting; on an expired token it's terminal."""
    now = datetime.now(timezone.utc)
    expires = _as_utc(row.token_expires)
    if expires is None or expires - now > REFRESH_LEEWAY:
        return
    try:
        _refresh(db, row)
    except InstagramError:
        if expires <= now:
            raise InstagramError(
                "Instagram token has expired and refresh failed — generate a new token in "
                "the Meta app dashboard and reconnect in Settings → Platforms.",
                permanent=True,
            )
        log.warning("instagram token refresh failed; token still valid until %s", expires)


def refresh_stale_token(db: Session) -> None:
    """Daily worker job. No-op when not connected."""
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if row and row.access_token:
        _maybe_refresh(db, row)


# -----------------------------------------------------------------------------
# Publishing
# -----------------------------------------------------------------------------

_COLLAB_REJECT_SUBCODE = 2207018


def _bad_collaborator_handles(resp: httpx.Response, candidates: list[str]) -> list[str]:
    """Which of `candidates` did Meta refuse? It names them in error_user_msg
    ("The following user(s) cannot be accessed: foo, bar"); we intersect on that rather
    than trusting the order, and fall back to "all of them" when the message is opaque.
    """
    try:
        err = resp.json().get("error") or {}
    except Exception:
        return []
    subcode = err.get("error_subcode")
    blob = f"{err.get('error_user_msg') or ''} {err.get('message') or ''}".lower()
    looks_like_collab = subcode == _COLLAB_REJECT_SUBCODE or "cannot be accessed" in blob
    if not looks_like_collab:
        return []
    # Match whole tokens, not substrings: a handle like "eli" would otherwise be
    # "found" inside a word like "delivery" and get dropped for no reason.
    words = set(re.findall(r"[a-z0-9._]+", blob))
    named = [h for h in candidates if h.lower().lstrip("@") in words]
    return named or list(candidates)


def _create_container(
    ig_user_id: str, data: dict, collaborators: list[str]
) -> tuple[str, list[str], list[str]]:
    """Create the media container, degrading gracefully on bad collaborators.

    Returns (container_id, collaborators_actually_sent, collaborators_dropped).
    """
    attempt_collabs = list(collaborators)
    rejected: list[str] = []

    # One pass per removable collaborator, plus a final pass with none.
    for _ in range(len(collaborators) + 1):
        body = dict(data)
        if attempt_collabs:
            body["collaborators"] = json.dumps(attempt_collabs)

        # "Media download has failed" is Meta's fetcher racing CDN propagation of
        # image_url (bites freshly-uploaded staging variants). Transient despite the
        # 400 — retry in-line before handing off to the retry queue.
        for fetch_attempt in range(3):
            with _client() as c:
                r = c.post(f"/{ig_user_id}/media", data=body)
            if r.status_code < 400:
                break
            if "download" in _error_text(r).lower() and fetch_attempt < 2:
                log.info("Meta couldn't fetch image_url (attempt %d) — waiting for CDN",
                         fetch_attempt + 1)
                time.sleep(10.0)
                continue
            break

        if r.status_code < 400:
            container_id = r.json().get("id")
            if not container_id:
                raise InstagramError(f"container creation returned no id: {r.text[:200]}")
            return container_id, attempt_collabs, rejected

        bad = _bad_collaborator_handles(r, attempt_collabs)
        if bad:
            rejected.extend(bad)
            attempt_collabs = [h for h in attempt_collabs if h not in bad]
            continue  # retry without the handles Meta refused

        if "download" in _error_text(r).lower():
            raise InstagramError(
                f"Meta couldn't fetch the image URL after retries: {_error_text(r)}",
                permanent=False,
            )
        _raise_api_error(r, "media container creation")

    raise InstagramError("media container creation failed after dropping all collaborators")


def post_photo(
    db: Session,
    *,
    image_url: str,
    caption: str,
    alt_text: str | None = None,
    collaborators: list[str] | None = None,
) -> dict:
    """Container → poll → publish. Returns {remote_id, url, collaborators}.

    collaborators: up to MAX_COLLABORATORS Instagram usernames invited as co-authors.
    Accepted invitations put the post on THEIR profile and in their followers' feeds —
    roughly double the impressions of a solo post, which is why this is worth the extra
    failure handling below. Feed images, carousels and Reels only (never Stories).
    """
    row = _load_credential(db)
    _maybe_refresh(db, row)
    token = decrypt_token(row.access_token)
    ig_user_id = json.loads(row.extra_json or "{}").get("ig_user_id")
    if not ig_user_id:
        raise InstagramError("Credential is missing ig_user_id — reconnect Instagram.", permanent=True)

    # Step 1: create the media container. Meta fetches image_url during this call, so a
    # dead/non-JPEG/oversized URL or bad aspect ratio surfaces here as a 400.
    data = {
        "image_url": image_url,
        "caption": (caption or "")[:MAX_CAPTION],
        "access_token": token,
    }
    alt = (alt_text or "").strip()
    if alt:
        data["alt_text"] = alt[:MAX_ALT_TEXT]

    # Co-authors. Meta validates every handle and rejects the WHOLE container if any one
    # is private, misspelled, or gone — so a stale handle in the performer roster would
    # otherwise cost us the entire post. _create_container drops the named offenders and
    # retries, so the photo always ships even if a credit doesn't.
    wanted_collabs = [
        h.lstrip("@").strip()
        for h in (collaborators or [])
        if h and h.strip()
    ][:MAX_COLLABORATORS]

    container_id, used_collabs, rejected = _create_container(
        ig_user_id, data, wanted_collabs
    )
    if rejected:
        log.warning("instagram: dropped un-taggable collaborator(s) %s and retried", rejected)
    # Step 2: wait for the container to be ready. Image containers usually come back
    # FINISHED on the first check.
    for attempt in range(STATUS_POLL_TRIES):
        with _client() as c:
            r = c.get(f"/{container_id}", params={
                "fields": "status_code",
                "access_token": token,
            })
        if r.status_code >= 400:
            _raise_api_error(r, "container status check")
        status_code = r.json().get("status_code")
        if status_code == "FINISHED":
            break
        if status_code in ("ERROR", "EXPIRED"):
            raise InstagramError(
                f"media container ended in {status_code} — Meta couldn't ingest the image "
                f"(url={image_url})",
                permanent=True,
            )
        time.sleep(STATUS_POLL_INTERVAL)
    else:
        raise InstagramError(
            f"media container still {status_code!r} after "
            f"{STATUS_POLL_TRIES * STATUS_POLL_INTERVAL:.0f}s — will retry"
        )

    # Step 3: publish.
    with _client() as c:
        r = c.post(f"/{ig_user_id}/media_publish", data={
            "creation_id": container_id,
            "access_token": token,
        })
    if r.status_code >= 400:
        _raise_api_error(r, "media publish")
    media_id = r.json().get("id")
    if not media_id:
        raise InstagramError(f"media_publish returned no id: {r.text[:200]}")

    # Permalink is cosmetic — the post is live even if this lookup fails.
    permalink = None
    try:
        with _client() as c:
            r = c.get(f"/{media_id}", params={"fields": "permalink", "access_token": token})
        if r.status_code < 400:
            permalink = r.json().get("permalink")
    except Exception:
        log.warning("instagram permalink lookup failed for media %s", media_id)

    return {
        "remote_id": str(media_id),
        "url": permalink,
        "collaborators": used_collabs,
        "collaborators_rejected": rejected,
    }


def publishing_quota(db: Session) -> dict:
    """Current rolling-24h API publish usage — surfaced by the Settings test button."""
    row = _load_credential(db)
    token = decrypt_token(row.access_token)
    ig_user_id = json.loads(row.extra_json or "{}").get("ig_user_id")
    with _client() as c:
        r = c.get(f"/{ig_user_id}/content_publishing_limit", params={
            "fields": "quota_usage,config",
            "access_token": token,
        })
    if r.status_code >= 400:
        _raise_api_error(r, "quota lookup")
    entries = r.json().get("data") or [{}]
    entry = entries[0]
    return {
        "quota_usage": entry.get("quota_usage"),
        "quota_total": (entry.get("config") or {}).get("quota_total"),
    }
