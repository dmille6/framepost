"""Check that the connections an upcoming post needs still work — before it fires.

A revoked or expired grant is discovered today by the publish that needed it: the post
fails at its scheduled minute, lands in the retry queue, and the "reconnect" banner
appears after the fact. Posting is time-sensitive (the reach experiment is literally
about the hour a post goes out), so learning about a dead token an hour early is worth
one cheap authenticated call.

What this does, every ~15 minutes:
  1. Find what fires in the next LOOKAHEAD (posts and reels).
  2. Work out which platform credentials those will use (preflight.targets_for, the same
     answer the worker uses).
  3. Verify each once, with the cheapest authenticated read the platform has.

What it deliberately does not do:
  * Block, delay or alter any publish. The worker validates on its own; this only warns.
  * Hammer APIs. One call per platform per run, and none at all when the connection was
    proven in the last RECHECK_AFTER — by this check or by a successful publish.
  * Cry wolf. A timeout or 5xx says the network blipped, not that the grant is gone, so
    only an answer that means "this token is refused" parks a channel as
    reauth_required. That flag is the existing channel_health mechanism: it drives the
    /health banner, the "needs reconnecting" list and the preflight blocker, so nothing
    new has to be looked at.
  * Clear flags. A passing identity check doesn't prove every permission — the Flickr
    delete-scope incident passed flickr.test.login the whole time — so a flag raised by
    a real publish failure stays until a reconnect or a real publish clears it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token
from models import AppConfig, PlatformCredential, Post, Reel
from services import channel_health, events, preflight, publish_errors
from services.platforms import bluesky, credentials, flickr, instagram, pinterest, pixelfed

log = logging.getLogger("framepost.connection_check")

LOOKAHEAD = timedelta(minutes=90)
RECHECK_AFTER = timedelta(hours=1)
STATE_KEY = "connection_check_state"


class CheckFailed(Exception):
    """A verification call got an answer, and the answer was no."""

    def __init__(self, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _raise_for(platform: str, r) -> None:
    if r.status_code >= 400:
        raise CheckFailed(f"{platform} check HTTP {r.status_code}: {r.text[:300]}",
                          http_status=r.status_code)


# --- the cheapest authenticated call per platform ------------------------------------

def _verify_instagram(db: Session, cred: PlatformCredential) -> None:
    with instagram._client() as c:
        r = c.get("/me", params={"fields": "user_id,username",
                                 "access_token": decrypt_token(cred.access_token)})
    _raise_for("instagram", r)


def _verify_flickr(db: Session, cred: PlatformCredential) -> None:
    flickr.rest_call(db, "flickr.test.login")


def _verify_bluesky(db: Session, cred: PlatformCredential) -> None:
    # _post_with_retry refreshes an expired access JWT the way a publish would, so a
    # merely-stale session reads as healthy and only a dead grant fails.
    row, session = bluesky._load_session(db)
    r = bluesky._post_with_retry(
        db, row, session, "GET", "/xrpc/app.bsky.actor.getProfile",
        params={"actor": session.did or session.handle or row.account_name or ""},
    )
    _raise_for("bluesky", r)


def _verify_pixelfed(db: Session, cred: PlatformCredential) -> None:
    with pixelfed._client(cred.instance_url or "") as c:
        r = c.get("/api/v1/accounts/verify_credentials",
                  headers={"Authorization": f"Bearer {decrypt_token(cred.access_token)}"})
    _raise_for("pixelfed", r)


def _verify_pinterest(db: Session, cred: PlatformCredential) -> None:
    token = pinterest._refresh_if_needed(db, cred)
    with pinterest._client() as c:
        r = c.get(f"{pinterest.API_BASE}/user_account",
                  headers={"Authorization": f"Bearer {token}"})
    _raise_for("pinterest", r)


VERIFIERS: dict[str, Callable[[Session, PlatformCredential], None]] = {
    "instagram": _verify_instagram,
    "flickr": _verify_flickr,
    "bluesky": _verify_bluesky,
    "pixelfed": _verify_pixelfed,
    "pinterest": _verify_pinterest,
}


def is_reauth(platform: str, err: BaseException) -> tuple[bool, str]:
    """Does this failure mean the grant is gone (True), or only that the call failed?

    Transport errors, 429 and 5xx are never reauth, whatever their text says: a
    connection reset is not evidence about a token. After that, a 401/403 is, and the
    shared publish_errors rules get a say on the rest (Flickr answers 200 with "error 98",
    Instagram 400 with code 190).
    """
    cause = err.__cause__ or err.__context__
    if isinstance(err, httpx.TransportError) or isinstance(cause, httpx.TransportError):
        return False, f"{platform.title()} was unreachable during the connection check."
    status = getattr(err, "http_status", None)
    if status is not None and (status >= 500 or status == 429):
        return False, f"{platform.title()} returned HTTP {status} during the connection check."
    failure = publish_errors.classify(platform, err)
    if failure.requires_reauth:
        return True, failure.user_message
    if status in (401, 403):
        return True, f"{platform.title()} needs reconnecting — it refused the saved login."
    # An adapter that says "permanent" on a read-only identity call is refusing the
    # grant (e.g. Bluesky rejecting the stored app password) — unless it also looked
    # like a transient failure above.
    if getattr(err, "permanent", False) and failure.category is not publish_errors.FailureCategory.RETRY:
        return True, f"{platform.title()} needs reconnecting: {str(err)[:200]}"
    return False, f"{platform.title()} connection check failed: {str(err)[:200]}"


# --- what is about to need which connection ------------------------------------------

def upcoming_needs(db: Session, now: datetime) -> dict[str, list[Post]]:
    """Platform -> the posts in the lookahead that will use it, soonest first."""
    horizon = now + LOOKAHEAD
    creds = preflight.load_credentials(db)
    posts = db.execute(
        select(Post).where(
            Post.status == "pending",
            Post.scheduled_at.is_not(None),
            Post.scheduled_at <= horizon,
            Post.scheduled_at >= now - timedelta(minutes=5),
        ).order_by(Post.scheduled_at)
    ).scalars().all()
    needs: dict[str, list[Post]] = {}
    for post in posts:
        for platform in preflight.targets_for(post, creds):
            needs.setdefault(platform, []).append(post)
    reels_due = db.execute(
        select(Reel.id).where(
            Reel.status == "ready", Reel.posted_at.is_(None),
            Reel.scheduled_at.is_not(None), Reel.scheduled_at <= horizon,
        ).limit(1)
    ).first()
    if reels_due:
        needs.setdefault("instagram", [])
    return needs


# --- state: when each platform last passed, and when we last told anyone -------------

def _load_state(db: Session) -> dict[str, dict[str, Any]]:
    row = db.get(AppConfig, STATE_KEY)
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def _save_state(db: Session, state: dict) -> None:
    row = db.get(AppConfig, STATE_KEY)
    if row is None:
        db.add(AppConfig(key=STATE_KEY, value=json.dumps(state)))
    else:
        row.value = json.dumps(state)
    db.commit()


def _recently_proven(cred: PlatformCredential, st: dict, now: datetime) -> bool:
    ok_at = st.get("ok_at")
    if ok_at and now - datetime.fromisoformat(ok_at) < RECHECK_AFTER:
        return True
    last = cred.last_success_at
    if last is not None:
        last = last.replace(tzinfo=None) if last.tzinfo is None else \
            last.astimezone(timezone.utc).replace(tzinfo=None)
        if now - last < RECHECK_AFTER:
            return True
    return False


def run(db: Session, *, now: datetime | None = None) -> dict[str, str]:
    """One pass. Returns platform -> outcome, for logging and tests."""
    now = now or _now()
    needs = upcoming_needs(db, now)
    if not needs:
        return {}
    state = _load_state(db)
    out: dict[str, str] = {}

    for platform, posts in needs.items():
        verify = VERIFIERS.get(platform)
        cred = credentials.get(db, platform)
        if verify is None or not credentials.is_connected(cred):
            continue  # preflight already reports "not connected" as a blocker
        if cred.auth_status == "reauth_required":
            out[platform] = "already_flagged"
            continue
        st = state.setdefault(platform, {})
        if _recently_proven(cred, st, now):
            out[platform] = "skipped"
            continue

        try:
            verify(db, cred)
        except Exception as e:  # noqa: BLE001 — a check must never take the worker down
            db.rollback()
            reauth, message = is_reauth(platform, e)
            st["failed_at"] = now.isoformat()
            st["error"] = str(e)[:300]
            if not reauth:
                log.warning("connection check %s: transient (%s) — not flagging", platform, e)
                out[platform] = "transient"
                continue
            channel_health.flag_reauth(db, platform, message)
            out[platform] = "reauth"
            today = now.date().isoformat()
            if st.get("alerted_on") != today and posts:
                # One timeline entry per platform per day, on the soonest affected post,
                # so the failure is on record where the photographer looks — not only in
                # a log on a box nobody reads.
                st["alerted_on"] = today
                events.log_event(
                    db, post_id=posts[0].id,
                    event_type=f"{platform}_connection_check_failed", actor="worker",
                    details={"message": message, "error": str(e)[:300],
                             "posts_in_next_90m": len(posts)},
                )
                db.commit()
            log.error("connection check %s: needs reconnecting before %d upcoming post(s): %s",
                      platform, len(posts), message)
            continue

        st["ok_at"] = now.isoformat()
        st.pop("error", None)
        out[platform] = "ok"

    _save_state(db, state)
    return out
