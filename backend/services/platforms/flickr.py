"""Flickr OAuth 1.0a flow + token storage. Phase 3A — connect/disconnect/status only.

Phase 3B fills in upload, album/group ops, machine-tag stamping.

OAuth 1.0a via Authlib's httpx OAuth1Client (sync). Flickr access tokens don't expire,
so token_expires is left null. We store the OAuth1 token-secret in the `refresh_token`
column slot (the schema's name is generic — Flickr just happens to use a token+secret pair).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from authlib.integrations.httpx_client import OAuth1Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from crypto import decrypt_token, encrypt_token
from models import PlatformCredential

log = logging.getLogger("framepost.flickr")

import base64
import hashlib
import hmac
import secrets as _secrets
import time
from pathlib import Path
from urllib.parse import quote

import httpx

REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
AUTHORIZE_URL = "https://www.flickr.com/services/oauth/authorize"
ACCESS_TOKEN_URL = "https://www.flickr.com/services/oauth/access_token"
UPLOAD_URL = "https://up.flickr.com/services/upload/"
REST_URL = "https://api.flickr.com/services/rest/"

PLATFORM = "flickr"
KEY_VERSION = 1


class FlickrError(Exception):
    """Raised on a Flickr-side failure. .permanent=True means don't retry (HTTP 4xx, validation)."""
    def __init__(self, message: str, *, code: int | None = None, permanent: bool = False):
        super().__init__(message)
        self.code = code
        self.permanent = permanent


@dataclass
class RequestTokenResult:
    authorize_url: str
    oauth_token: str
    oauth_token_secret: str


class FlickrCredentialsMissing(Exception):
    pass


def _require_app_keys() -> tuple[str, str]:
    if not settings.flickr_api_key or not settings.flickr_api_secret:
        raise FlickrCredentialsMissing(
            "FLICKR_API_KEY / FLICKR_API_SECRET are not set. Add them to .env."
        )
    return settings.flickr_api_key, settings.flickr_api_secret


def begin_authorize(callback_url: str, *, perms: str = "write") -> RequestTokenResult:
    """Step 1: get a request token from Flickr and build the authorize URL.

    `perms` ∈ {read, write, delete}. We need write for upload; delete is overkill for v1.
    """
    api_key, api_secret = _require_app_keys()
    client = OAuth1Client(
        client_id=api_key,
        client_secret=api_secret,
        redirect_uri=callback_url,
    )
    token = client.fetch_request_token(REQUEST_TOKEN_URL)
    auth_url = client.create_authorization_url(AUTHORIZE_URL, perms=perms)
    return RequestTokenResult(
        authorize_url=auth_url,
        oauth_token=token["oauth_token"],
        oauth_token_secret=token["oauth_token_secret"],
    )


def complete_authorize(
    *,
    oauth_token: str,
    oauth_token_secret: str,
    oauth_verifier: str,
    db: Session,
) -> PlatformCredential:
    """Step 2: exchange request_token + verifier for an access token. Persists encrypted."""
    api_key, api_secret = _require_app_keys()
    client = OAuth1Client(
        client_id=api_key,
        client_secret=api_secret,
        token=oauth_token,
        token_secret=oauth_token_secret,
    )
    token = client.fetch_access_token(ACCESS_TOKEN_URL, verifier=oauth_verifier)
    access_token = token["oauth_token"]
    access_secret = token["oauth_token_secret"]
    account_name = (
        token.get("fullname")
        or token.get("username")
        or token.get("user_nsid")
        or "Flickr account"
    )

    # Single-platform v1: replace any existing row.
    existing = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    cred = PlatformCredential(
        id=uuid.uuid4().hex,
        platform=PLATFORM,
        access_token=encrypt_token(access_token),
        refresh_token=encrypt_token(access_secret),
        token_expires=None,
        account_name=account_name,
        key_version=KEY_VERSION,
        connected_at=datetime.now(timezone.utc),
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    log.info("flickr connected: account=%s", account_name)
    return cred


def disconnect(db: Session) -> bool:
    existing = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not existing:
        return False
    db.delete(existing)
    db.commit()
    log.info("flickr disconnected")
    return True


def current_status(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        return {
            "connected": False,
            "account_name": None,
            "connected_at": None,
            "key_version": None,
        }
    return {
        "connected": True,
        "account_name": row.account_name,
        "connected_at": row.connected_at.isoformat() if row.connected_at else None,
        "key_version": row.key_version,
    }


def load_oauth_session(db: Session) -> OAuth1Client:
    """Build an authenticated OAuth1Client for REST API calls (NOT for upload — see upload_photo)."""
    api_key, api_secret = _require_app_keys()
    cred = _load_credential(db)
    return OAuth1Client(
        client_id=api_key,
        client_secret=api_secret,
        token=decrypt_token(cred.access_token),
        token_secret=decrypt_token(cred.refresh_token),
    )


def _load_credential(db: Session) -> PlatformCredential:
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == PLATFORM)
    ).scalar_one_or_none()
    if not row:
        raise RuntimeError("Flickr is not connected.")
    return row


# --- Hand-rolled OAuth1.0a signer for multipart uploads ---


def _percent_encode(s: str) -> str:
    return quote(s, safe="-._~")


def _oauth1_authorization_header(
    *,
    method: str,
    url: str,
    form_params: dict[str, str],
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
) -> str:
    """Build a fully-signed OAuth 1.0a Authorization header. Form-field text values are
    included in the signature base string per RFC 5849; the photo binary (multipart file
    part) must be excluded — caller passes only the text fields here.
    """
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": _secrets.token_urlsafe(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    all_params = [(_percent_encode(str(k)), _percent_encode(str(v)))
                  for k, v in {**oauth_params, **form_params}.items()]
    all_params.sort()
    param_str = "&".join(f"{k}={v}" for k, v in all_params)
    base_string = f"{method.upper()}&{_percent_encode(url)}&{_percent_encode(param_str)}"
    signing_key = f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )


# --- Flickr upload + REST helpers (Phase 3B) ---

_PRIVACY = {
    "private": ("0", "0", "0"),
    "friends_family": ("0", "1", "1"),
    "public": ("1", "0", "0"),
}
_SAFETY = {"safe": "1", "moderate": "2", "restricted": "3"}
_CONTENT = {"photo": "1", "screenshot": "2", "other": "3"}


def format_tags(comma_separated: str | None, *, machine_tags: list[str] | None = None) -> str:
    """Flickr expects space-separated tags, multi-word tags in quotes. Add machine tags raw."""
    out: list[str] = []
    if comma_separated:
        for raw in comma_separated.split(","):
            t = raw.strip()
            if not t:
                continue
            out.append(f'"{t}"' if " " in t else t)
    if machine_tags:
        out.extend(machine_tags)  # machine tags are namespace:key=value, never quoted
    return " ".join(out)


def _parse_rsp(text: str):
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise FlickrError(f"unparseable Flickr response: {e}") from e
    if root.get("stat") != "ok":
        err = root.find("err")
        msg = err.get("msg") if err is not None else "unknown"
        code_str = err.get("code") if err is not None else None
        try:
            code = int(code_str) if code_str else None
        except ValueError:
            code = None
        # Treat 4xx-ish Flickr error codes as permanent so retry logic doesn't waste attempts.
        permanent_codes = {1, 2, 3, 4, 5, 6, 7, 8, 9}
        permanent = code is not None and code in permanent_codes
        raise FlickrError(f"flickr error {code}: {msg}", code=code, permanent=permanent)
    return root


def upload_photo(
    *,
    db: Session,
    image_path,  # Path
    title: str | None,
    description: str | None,
    tags: str,                          # already formatted via format_tags()
    privacy: str = "private",
    safety_level: str = "safe",
    content_type: str = "photo",
) -> str:
    """POST the image to Flickr's upload endpoint. Returns the flickr_photo_id."""
    is_public, is_friend, is_family = _PRIVACY.get(privacy, _PRIVACY["private"])
    data = {
        "title": title or "",
        "description": description or "",
        "tags": tags or "",
        "is_public": is_public,
        "is_friend": is_friend,
        "is_family": is_family,
        "safety_level": _SAFETY.get(safety_level, "1"),
        "content_type": _CONTENT.get(content_type, "1"),
        "hidden": "2",  # 1=public, 2=hidden from search by default; aligns with private-by-default brief
    }
    # NOTE: we don't use Authlib's OAuth1Client for uploads. Authlib's auth_flow consumes
    # the multipart body when signing and the body never makes it onto the wire — Flickr
    # responds with a misleading "POST size too large" error 93 on a 0-byte upload.
    # Hand-rolled OAuth1 signer below uses the form fields (excluding the photo binary)
    # in the signature base string, which is what the OAuth1 spec calls for on multipart.
    api_key, api_secret = _require_app_keys()
    cred = _load_credential(db)
    photo_bytes = Path(image_path).read_bytes()
    auth_header = _oauth1_authorization_header(
        method="POST",
        url=UPLOAD_URL,
        form_params=data,
        consumer_key=api_key,
        consumer_secret=api_secret,
        token=decrypt_token(cred.access_token),
        token_secret=decrypt_token(cred.refresh_token),
    )
    headers = {"Authorization": auth_header}
    files = {"photo": (Path(image_path).name, photo_bytes, "image/jpeg")}
    try:
        with httpx.Client(timeout=300.0) as client:
            response = client.post(UPLOAD_URL, data=data, files=files, headers=headers)
    except Exception as e:
        raise FlickrError(f"upload transport failed: {e}") from e

    if response.status_code >= 500:
        raise FlickrError(f"flickr 5xx: {response.status_code}")
    root = _parse_rsp(response.text)
    photoid = root.findtext("photoid")
    if not photoid:
        raise FlickrError("upload succeeded but Flickr returned no photoid", permanent=True)
    return photoid


def photo_url(photo_id: str, account_nsid: str | None = None) -> str:
    """Best-effort photo URL. Without the user nsid we point at the canonical short URL."""
    return f"https://www.flickr.com/photos/{account_nsid or 'me'}/{photo_id}/"


def rest_call(db: Session, method: str, **params) -> "ET.Element":
    """Generic REST API call. Returns the parsed XML root (with stat=ok already verified).

    Uses the same hand-rolled OAuth1 signer as upload (authlib's body-consumption issue isn't
    isolated to multipart — has bitten REST calls too with empty-body symptoms).
    """
    api_key, api_secret = _require_app_keys()
    cred = _load_credential(db)
    payload = {
        "method": method,
        "format": "rest",
        **{k: v for k, v in params.items() if v is not None},
    }
    auth_header = _oauth1_authorization_header(
        method="POST",
        url=REST_URL,
        form_params=payload,
        consumer_key=api_key,
        consumer_secret=api_secret,
        token=decrypt_token(cred.access_token),
        token_secret=decrypt_token(cred.refresh_token),
    )
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(REST_URL, data=payload, headers={"Authorization": auth_header})
    except Exception as e:
        raise FlickrError(f"REST transport failed for {method}: {e}") from e
    if response.status_code >= 500:
        raise FlickrError(f"flickr 5xx on {method}: {response.status_code}")
    if not response.text.strip():
        raise FlickrError(f"flickr returned empty response on {method}")
    return _parse_rsp(response.text)


import re as _re
_NSID_RE = _re.compile(r"\b(\d+@N\d+)\b")


def resolve_group_id(db: Session, url_or_id: str) -> str:
    """Return a Flickr group NSID from any of:
    - bare NSID like '512395@N21'
    - URL with NSID embedded
    - vanity URL like 'https://www.flickr.com/groups/burlesquebeauties/' — resolved via API
    """
    s = (url_or_id or "").strip()
    if not s:
        raise FlickrError("group id is empty", permanent=True)

    m = _NSID_RE.search(s)
    if m:
        return m.group(1)

    if not s.startswith("http"):
        s = f"https://www.flickr.com/groups/{s.strip('/')}/"

    root = rest_call(db, "flickr.urls.lookupGroup", url=s)
    group_el = root.find("group")
    if group_el is None:
        raise FlickrError(
            f"flickr.urls.lookupGroup returned no group for {s}", permanent=True,
        )
    nsid = group_el.get("id")
    if not nsid:
        raise FlickrError(
            f"flickr.urls.lookupGroup returned group with no id for {s}", permanent=True,
        )
    return nsid

