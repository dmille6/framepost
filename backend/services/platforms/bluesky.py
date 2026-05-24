"""Bluesky / atproto integration.

The atproto auth model is simpler than OAuth: user generates an app-specific password
in Bluesky settings → we exchange handle + app_password for a session JWT (createSession),
which gives us an access token (~30 min TTL) and a refresh token (~60 days). We persist
both encrypted and call refreshSession when the access token expires.

For posting:
1. POST com.atproto.repo.uploadBlob with the JPEG bytes → blob ref
2. POST com.atproto.repo.createRecord with collection=app.bsky.feed.post + the blob embed

Bluesky enforces a 1MB blob ceiling so we resize aggressively. Captions are capped at
300 graphemes; we trim if needed (rare for photo posts but possible if a long Flickr
description got pulled in).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token, encrypt_token
from models import PlatformCredential

log = logging.getLogger("framepost.bluesky")

PLATFORM = "bluesky"
DEFAULT_PDS = "https://bsky.social"
MAX_BLOB_BYTES = 976_000  # ~1MB Bluesky cap; leave headroom for atproto envelope
MAX_TEXT_GRAPHEMES = 300
KEY_VERSION = 1


class BlueskyError(Exception):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class _Session:
    pds: str
    did: str
    handle: str
    access_jwt: str
    refresh_jwt: str


def _client(pds: str = DEFAULT_PDS) -> httpx.Client:
    return httpx.Client(base_url=pds.rstrip("/"), timeout=30.0)


def _create_session(handle: str, app_password: str, pds: str = DEFAULT_PDS) -> _Session:
    """Exchange handle+app_password → session JWTs. Bluesky returns 401 on bad creds; we map
    those to permanent errors so the operator gets a clear "wrong password" rather than a
    background retry storm."""
    with _client(pds) as c:
        r = c.post(
            "/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
        )
    if r.status_code == 401:
        raise BlueskyError("Bluesky rejected the credentials (handle or app password is wrong).", permanent=True)
    if r.status_code >= 400:
        raise BlueskyError(f"createSession failed (HTTP {r.status_code}): {r.text[:200]}")
    body = r.json()
    return _Session(
        pds=pds,
        did=body["did"],
        handle=body["handle"],
        access_jwt=body["accessJwt"],
        refresh_jwt=body["refreshJwt"],
    )


def _refresh_session(refresh_jwt: str, pds: str = DEFAULT_PDS) -> _Session:
    with _client(pds) as c:
        r = c.post(
            "/xrpc/com.atproto.server.refreshSession",
            headers={"Authorization": f"Bearer {refresh_jwt}"},
        )
    if r.status_code >= 400:
        raise BlueskyError(
            f"refreshSession failed (HTTP {r.status_code}): {r.text[:200]}",
            permanent=(r.status_code == 401),
        )
    body = r.json()
    return _Session(
        pds=pds,
        did=body["did"],
        handle=body["handle"],
        access_jwt=body["accessJwt"],
        refresh_jwt=body["refreshJwt"],
    )


def connect(db: Session, *, handle: str, app_password: str) -> PlatformCredential:
    """Verify the handle/app-password pair against bsky.social and persist the session."""
    session = _create_session(handle, app_password)
    # Drop any existing connection for this platform — single-account model for v1.
    existing = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    cred = PlatformCredential(
        id=str(uuid.uuid4()),
        platform=PLATFORM,
        access_token=encrypt_token(session.access_jwt),
        refresh_token=encrypt_token(session.refresh_jwt),
        account_name=session.handle,
        instance_url=session.pds,
        extra_json=json.dumps(
            {"did": session.did, "app_password": encrypt_token(app_password)}
        ),
        connected_at=datetime.now(timezone.utc),
        last_success_at=datetime.now(timezone.utc),
        key_version=KEY_VERSION,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    log.info("bluesky connected: handle=%s did=%s", session.handle, session.did)
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


def _load_session(db: Session) -> tuple[PlatformCredential, _Session]:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        raise BlueskyError("Bluesky is not connected.", permanent=True)
    extra = json.loads(row.extra_json or "{}")
    pds = row.instance_url or DEFAULT_PDS

    access = decrypt_token(row.access_token) if row.access_token else None
    refresh = decrypt_token(row.refresh_token) if row.refresh_token else None
    if not refresh:
        raise BlueskyError("Bluesky session is missing — reconnect.", permanent=True)

    # Try the access token first; on 401 refresh, on refresh failure fall back to a fresh
    # createSession with the stored app password.
    session = _Session(
        pds=pds, did=extra.get("did", ""), handle=row.account_name or "",
        access_jwt=access or "", refresh_jwt=refresh,
    )
    return row, session


def _save_session(db: Session, row: PlatformCredential, session: _Session) -> None:
    row.access_token = encrypt_token(session.access_jwt)
    row.refresh_token = encrypt_token(session.refresh_jwt)
    row.last_success_at = datetime.now(timezone.utc)
    db.commit()


def _ensure_fresh(db: Session, row: PlatformCredential, session: _Session) -> _Session:
    """Refresh the access token if needed. We're optimistic — try once, refresh on 401."""
    return session  # callers below call _post_with_retry which handles refresh on 401


def _needs_refresh(r: httpx.Response) -> bool:
    """Detect token-expiry responses. atproto signals expiry via HTTP 400 with body
    `{"error": "ExpiredToken"}` (not 401 like most APIs), so we have to peek at the body
    on 4xx to decide whether to refresh."""
    if r.status_code == 401:
        return True
    if r.status_code == 400:
        try:
            body = r.json()
            return body.get("error") in ("ExpiredToken", "InvalidToken", "AuthMissing")
        except Exception:
            return False
    return False


def _post_with_retry(
    db: Session,
    row: PlatformCredential,
    session: _Session,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """Call an authenticated XRPC endpoint; if the access token is expired, refresh + retry once."""
    headers = kwargs.pop("headers", {}) or {}

    def _call(token: str) -> httpx.Response:
        with _client(session.pds) as c:
            return c.request(
                method,
                path,
                headers={**headers, "Authorization": f"Bearer {token}"},
                **kwargs,
            )

    r = _call(session.access_jwt)
    if not _needs_refresh(r):
        return r

    # Refresh path. Try refreshSession first; if the refresh JWT is also dead (60d expiry,
    # or revoked), fall back to a fresh createSession using the stored app_password.
    try:
        new_session = _refresh_session(session.refresh_jwt, session.pds)
    except BlueskyError as e:
        if e.permanent:
            extra = json.loads(row.extra_json or "{}")
            ap_enc = extra.get("app_password")
            if not ap_enc:
                raise
            app_password = decrypt_token(ap_enc)
            new_session = _create_session(row.account_name or "", app_password, session.pds)
        else:
            raise
    session.access_jwt = new_session.access_jwt
    session.refresh_jwt = new_session.refresh_jwt
    _save_session(db, row, session)
    return _call(session.access_jwt)


def _shrink_image(src: Path) -> tuple[bytes, str]:
    """Re-encode JPEG to fit Bluesky's 1MB blob cap. Step quality down until we fit."""
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # First, cap dimensions. Bluesky displays max ~1200px wide on the web client; 2000 is plenty.
        if max(img.size) > 2000:
            img.thumbnail((2000, 2000), Image.LANCZOS)

        for quality in (90, 85, 80, 75, 70, 65, 60):
            buf = BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
            data = buf.getvalue()
            if len(data) <= MAX_BLOB_BYTES:
                return data, "image/jpeg"

        # Last resort: shrink dimensions further
        img.thumbnail((1200, 1200), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=70, optimize=True, progressive=True)
        data = buf.getvalue()
        if len(data) > MAX_BLOB_BYTES:
            raise BlueskyError("Image refused to fit under 1MB even after aggressive resize.")
        return data, "image/jpeg"


def _upload_blob(db: Session, row: PlatformCredential, session: _Session, src: Path) -> dict:
    data, mime = _shrink_image(src)
    r = _post_with_retry(
        db, row, session, "POST",
        "/xrpc/com.atproto.repo.uploadBlob",
        content=data,
        headers={"Content-Type": mime},
    )
    if r.status_code >= 400:
        raise BlueskyError(f"uploadBlob failed (HTTP {r.status_code}): {r.text[:300]}")
    return r.json()["blob"]


def _trim_text(text: str) -> str:
    """Bluesky caps post text at 300 graphemes. We approximate with characters (close enough
    for English captions) and trim with an ellipsis if needed."""
    if len(text) <= MAX_TEXT_GRAPHEMES:
        return text
    return text[: MAX_TEXT_GRAPHEMES - 1].rstrip() + "…"


def _detect_facets(text: str) -> list[dict]:
    """Detect #hashtags and URLs and emit atproto 'facets' so they render as links/tags.
    Uses byte offsets, not character offsets — atproto requirement."""
    text_bytes = text.encode("utf-8")
    facets: list[dict] = []

    # Hashtags
    for m in re.finditer(r"(?:^|\s)(#[\w]+)", text):
        # match offset within the original string; convert to bytes
        start_char = m.start(1)
        end_char = m.end(1)
        byte_start = len(text[:start_char].encode("utf-8"))
        byte_end = len(text[:end_char].encode("utf-8"))
        facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)[1:]}],
        })

    # Links
    for m in re.finditer(r"https?://[^\s]+", text):
        byte_start = len(text[: m.start()].encode("utf-8"))
        byte_end = len(text[: m.end()].encode("utf-8"))
        facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}],
        })

    return facets


def _public_post_url(handle: str, at_uri: str) -> str:
    """Convert an at:// URI to a public bsky.app permalink."""
    # at://did:plc:.../app.bsky.feed.post/3krx...  →  https://bsky.app/profile/{handle}/post/{rkey}
    rkey = at_uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def post_photo(
    db: Session,
    *,
    src: Path,
    text: str,
    alt_text: str | None = None,
) -> dict:
    """Post a photo to Bluesky. Returns {at_uri, cid, url}."""
    row, session = _load_session(db)
    blob = _upload_blob(db, row, session, src)

    body_text = _trim_text(text or "")
    record = {
        "$type": "app.bsky.feed.post",
        "text": body_text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "embed": {
            "$type": "app.bsky.embed.images",
            "images": [{
                "alt": (alt_text or "")[:1000],  # atproto allows long alt text but be sane
                "image": blob,
            }],
        },
    }
    facets = _detect_facets(body_text)
    if facets:
        record["facets"] = facets

    r = _post_with_retry(
        db, row, session, "POST",
        "/xrpc/com.atproto.repo.createRecord",
        json={
            "repo": session.did,
            "collection": "app.bsky.feed.post",
            "record": record,
        },
    )
    if r.status_code >= 400:
        body = r.text[:400]
        # Map common permanent failures so the retry layer doesn't loop on them.
        permanent = r.status_code in (400, 401, 403)
        raise BlueskyError(f"createRecord failed (HTTP {r.status_code}): {body}", permanent=permanent)

    body = r.json()
    at_uri = body["uri"]
    return {
        "at_uri": at_uri,
        "cid": body.get("cid"),
        "url": _public_post_url(session.handle, at_uri),
    }


def current_status(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        return {"connected": False, "handle": None, "connected_at": None}
    return {
        "connected": True,
        "handle": row.account_name,
        "instance_url": row.instance_url,
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "last_error": row.last_error,
        "default_target": bool(row.default_target),
    }
