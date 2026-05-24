"""Platform integrations API. Connect/disconnect/status for Flickr, Bluesky, Pixelfed."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from database import get_session
from models import PlatformCredential, Post, User
from routes.auth import current_user
from services.platforms import bluesky, flickr, pixelfed

log = logging.getLogger("framepost.platforms")
router = APIRouter()

OAUTH_COOKIE = "framepost_oauth_state"
OAUTH_COOKIE_TTL = 600  # 10 minutes — generous for the user to authorize
_OAUTH_SALT = "framepost.oauth.flickr.v1"


def _signer() -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY is not set.")
    return URLSafeTimedSerializer(settings.secret_key, salt=_OAUTH_SALT)


def _absolute_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"


@router.get("/flickr/status")
def flickr_status(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    return flickr.current_status(db)


@router.get("/flickr/connect")
def flickr_connect(
    request: Request,
    _user: User = Depends(current_user),
):
    """Start the OAuth dance. Top-level GET because the browser navigates here directly —
    fetch can't follow a redirect to flickr.com cleanly, and OAuth requires a real navigation.
    """
    callback = _absolute_url(request, "/api/platforms/flickr/callback")
    try:
        result = flickr.begin_authorize(callback)
    except flickr.FlickrCredentialsMissing as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:  # pragma: no cover — surface Flickr errors to the user
        log.exception("flickr request_token failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Flickr request_token failed: {e}")

    state_cookie = _signer().dumps(
        {"req_token": result.oauth_token, "req_secret": result.oauth_token_secret}
    )
    response = RedirectResponse(url=result.authorize_url, status_code=status.HTTP_303_SEE_OTHER)
    # Hand back the URL in JSON form too — but RedirectResponse takes precedence
    # since browsers follow it. We still set the cookie before redirecting.
    response.set_cookie(
        OAUTH_COOKIE,
        state_cookie,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=OAUTH_COOKIE_TTL,
        path="/api/platforms/flickr",
    )
    return response


@router.get("/flickr/callback")
def flickr_callback(
    request: Request,
    oauth_token: str,
    oauth_verifier: str,
    db: Session = Depends(get_session),
):
    """User comes back here from Flickr with oauth_verifier. Exchange + persist + redirect to UI.

    No `current_user` dependency: the user's session cookie may or may not be present (this is
    a top-level browser navigation from flickr.com). If they were logged in when they hit
    connect, they'll still be logged in when they land here.
    """
    cookie = request.cookies.get(OAUTH_COOKIE)
    if not cookie:
        return _redirect_back(request, "missing OAuth state")
    try:
        state = _signer().loads(cookie, max_age=OAUTH_COOKIE_TTL)
    except (BadSignature, SignatureExpired):
        return _redirect_back(request, "OAuth state expired or invalid")

    if state.get("req_token") != oauth_token:
        return _redirect_back(request, "OAuth token mismatch")

    try:
        flickr.complete_authorize(
            oauth_token=oauth_token,
            oauth_token_secret=state["req_secret"],
            oauth_verifier=oauth_verifier,
            db=db,
        )
    except Exception as e:
        log.exception("flickr callback exchange failed")
        return _redirect_back(request, f"Flickr exchange failed: {e}")

    response = RedirectResponse(url="/settings/flickr?connected=1", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(OAUTH_COOKIE, path="/api/platforms/flickr")
    return response


@router.post("/flickr/disconnect")
def flickr_disconnect(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    removed = flickr.disconnect(db)
    return {"ok": True, "removed": removed}


def _redirect_back(_request: Request, reason: str) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/settings/flickr?error={quote(reason)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# -----------------------------------------------------------------------------
# Bluesky — handle + app password (no OAuth)
# -----------------------------------------------------------------------------

class BlueskyConnectBody(BaseModel):
    handle: str = Field(min_length=1, max_length=253)
    app_password: str = Field(min_length=1, max_length=200)


class BlueskyTestBody(BaseModel):
    text: str = Field(default="FramePost connection test 🟢", max_length=300)


@router.get("/bluesky/status")
def bluesky_status(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    return bluesky.current_status(db)


@router.post("/bluesky/connect")
def bluesky_connect(
    body: BlueskyConnectBody,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    try:
        cred = bluesky.connect(db, handle=body.handle, app_password=body.app_password)
    except bluesky.BlueskyError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST if e.permanent else status.HTTP_502_BAD_GATEWAY,
            str(e),
        )
    return {
        "ok": True,
        "handle": cred.account_name,
        "connected_at": cred.connected_at.isoformat() if cred.connected_at else None,
    }


@router.post("/bluesky/disconnect")
def bluesky_disconnect(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    return {"ok": True, "removed": bluesky.disconnect(db)}


@router.post("/bluesky/test")
def bluesky_test(
    body: BlueskyTestBody,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Verify the session is alive by calling getProfile against our handle. Doesn't post."""
    row, session = bluesky._load_session(db)
    r = bluesky._post_with_retry(
        db, row, session, "GET",
        "/xrpc/app.bsky.actor.getProfile",
        params={"actor": session.handle or row.account_name or ""},
    )
    if r.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Bluesky test failed: {r.text[:200]}")
    body_json = r.json()
    return {
        "ok": True,
        "handle": body_json.get("handle"),
        "display_name": body_json.get("displayName"),
        "followers": body_json.get("followersCount"),
    }


# -----------------------------------------------------------------------------
# Pixelfed — OAuth 2.0
# -----------------------------------------------------------------------------

PIXELFED_OAUTH_COOKIE = "framepost_pixelfed_oauth"
_PIXELFED_OAUTH_SALT = "framepost.oauth.pixelfed.v1"


def _pixelfed_signer() -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY is not set.")
    return URLSafeTimedSerializer(settings.secret_key, salt=_PIXELFED_OAUTH_SALT)


class PixelfedConnectQuery(BaseModel):
    instance: str = Field(min_length=4, max_length=300)


@router.get("/pixelfed/status")
def pixelfed_status(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    return pixelfed.current_status(db)


@router.get("/pixelfed/connect")
def pixelfed_connect(
    request: Request,
    instance: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Register FramePost on the user's Pixelfed instance and redirect them to /oauth/authorize.

    Top-level GET because the browser navigates here (same reason as Flickr).
    """
    callback = _absolute_url(request, "/api/platforms/pixelfed/callback")
    try:
        authorize_url, state = pixelfed.begin_connect(
            db, instance_url=instance, redirect_uri=callback
        )
    except pixelfed.PixelfedError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST if e.permanent else status.HTTP_502_BAD_GATEWAY,
            str(e),
        )

    state_cookie = _pixelfed_signer().dumps({"state": state})
    response = RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        PIXELFED_OAUTH_COOKIE,
        state_cookie,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=OAUTH_COOKIE_TTL,
        path="/api/platforms/pixelfed",
    )
    return response


@router.get("/pixelfed/callback")
def pixelfed_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_session),
):
    """Pixelfed redirects here after user approval. Validate state, exchange code, persist."""
    if error:
        return _pixelfed_redirect_back(f"Pixelfed authorization denied: {error}")
    if not code or not state:
        return _pixelfed_redirect_back("Missing authorization code or state.")

    cookie = request.cookies.get(PIXELFED_OAUTH_COOKIE)
    if not cookie:
        return _pixelfed_redirect_back("Missing OAuth state cookie.")
    try:
        cookie_state = _pixelfed_signer().loads(cookie, max_age=OAUTH_COOKIE_TTL)
    except (BadSignature, SignatureExpired):
        return _pixelfed_redirect_back("OAuth state expired or invalid.")
    if cookie_state.get("state") != state:
        return _pixelfed_redirect_back("OAuth state mismatch.")

    try:
        pixelfed.complete_connect(db, code=code, state=state)
    except pixelfed.PixelfedError as e:
        return _pixelfed_redirect_back(f"Pixelfed exchange failed: {e}")
    except Exception as e:
        log.exception("pixelfed callback failed")
        return _pixelfed_redirect_back(f"Pixelfed exchange failed: {e}")

    response = RedirectResponse(
        url="/settings/platforms?connected=pixelfed", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie(PIXELFED_OAUTH_COOKIE, path="/api/platforms/pixelfed")
    return response


@router.post("/pixelfed/disconnect")
def pixelfed_disconnect(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    return {"ok": True, "removed": pixelfed.disconnect(db)}


def _pixelfed_redirect_back(reason: str) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/settings/platforms?error={quote(reason)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# -----------------------------------------------------------------------------
# Cross-platform per-post default-target toggle
# -----------------------------------------------------------------------------

class DefaultTargetBody(BaseModel):
    default_target: bool


@router.patch("/{platform}/default-target")
def set_default_target(
    platform: str,
    body: DefaultTargetBody,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Toggle whether new scheduled posts default to including this platform."""
    row = db.execute(
        select(PlatformCredential).where(PlatformCredential.platform == platform)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "platform not connected")
    row.default_target = 1 if body.default_target else 0
    db.commit()
    return {"ok": True, "default_target": bool(row.default_target)}


@router.get("")
def list_connected_platforms(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    """Unified list of connected platforms for per-post targeting UI.

    Flickr is special-cased: its credential lives in platform_credentials with platform='flickr'
    but the actual post fields are on Post (predates this table). We expose it as 'always
    available' if a Flickr connection exists.
    """
    out: list[dict[str, Any]] = []

    flickr_status = flickr.current_status(db)
    if flickr_status.get("connected"):
        out.append({
            "platform": "flickr",
            "label": "Flickr",
            "account_name": flickr_status.get("account_name"),
            "default_target": True,  # Flickr is always a default target when connected.
        })

    rows = db.execute(
        select(PlatformCredential).where(
            PlatformCredential.platform.in_(("bluesky", "pixelfed", "mastodon"))
        )
    ).scalars().all()
    for row in rows:
        if not row.access_token:
            continue  # connection still pending (e.g. Pixelfed mid-OAuth)
        out.append({
            "platform": row.platform,
            "label": row.platform.capitalize(),
            "account_name": row.account_name,
            "instance_url": row.instance_url,
            "default_target": bool(row.default_target),
        })
    return out
