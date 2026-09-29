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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token
from models import AppConfig, PlatformCredential, Post, Reel
from services import channel_health, events, preflight, publish_errors, redact
from services.platforms import bluesky, credentials, flickr, instagram, pinterest, pixelfed

log = logging.getLogger("framepost.connection_check")

LOOKAHEAD = timedelta(minutes=90)
RECHECK_AFTER = timedelta(hours=1)
STATE_KEY = "connection_check_state"


# Meta error codes (Graph API error handling reference). Only these two mean the grant
# itself is gone: 190 "Invalid OAuth 2.0 access token" (all its subcodes — expired,
# password changed, app removed, session invalidated) and 102 "API session". Codes 4,
# 17, 32 and 613 are rate limits, and many other codes arrive typed OAuthException
# without saying anything about the token, so neither the type nor a bare 401/403 is
# evidence on its own.
IG_REAUTH_CODES = frozenset({190, 102})
IG_RATE_LIMIT_CODES = frozenset({4, 17, 32, 613})


class CheckFailed(Exception):
    """A verification call got an answer, and the answer was no."""

    def __init__(self, message: str, *, http_status: int | None = None,
                 code: int | None = None, subcode: int | None = None,
                 error_type: str | None = None):
        super().__init__(redact.redact(message))   # quotes the response body
        self.http_status = http_status
        self.code = code
        self.subcode = subcode
        self.error_type = error_type


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _raise_for(platform: str, r) -> None:
    if r.status_code < 400:
        return
    code = subcode = error_type = None
    try:
        err = r.json().get("error")
        if isinstance(err, dict):   # Meta's shape; other platforms answer differently
            code, subcode, error_type = err.get("code"), err.get("error_subcode"), err.get("type")
            code = int(code) if code is not None else None
    except Exception:  # noqa: BLE001 — an unparseable body just means "no code"
        pass
    raise CheckFailed(f"{platform} check HTTP {r.status_code}: {redact.clip(r.text, 300)}",
                      http_status=r.status_code, code=code, subcode=subcode,
                      error_type=error_type)


# --- the cheapest authenticated call per platform ------------------------------------

def _verify_instagram(db: Session, cred: PlatformCredential) -> None:
    with instagram._client() as c:
        r = c.get("/me", params={"fields": "user_id,username"},
                  headers=instagram._auth(decrypt_token(cred.access_token)))
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

    Deliberately stingy with True — a false "reconnect" banner trains the user to ignore
    the real one:

      * transport errors, 429 and 5xx are never reauth, whatever their text says;
      * Instagram: only error.code 190 or 102 is reauth. Rate-limit codes, other
        OAuthException-typed errors and code-less 401/403s are transient;
      * other platforms' own HTTP answers: 401 is reauth; a 403 is ambiguous
        (suspended, blocked, scope, rate-limited) and transient;
      * adapter exceptions (Flickr's "error 98", Bluesky refusing the stored app
        password, Pinterest with no refresh token) go through publish_errors, whose
        rules match the adapters' own messages rather than arbitrary response bodies.
    """
    cause = err.__cause__ or err.__context__
    if isinstance(err, httpx.TransportError) or isinstance(cause, httpx.TransportError):
        return False, f"{platform.title()} was unreachable during the connection check."
    status = getattr(err, "http_status", None)
    if status is not None and (status >= 500 or status == 429):
        return False, f"{platform.title()} returned HTTP {status} during the connection check."

    if isinstance(err, CheckFailed):
        if platform == "instagram":
            if err.code in IG_REAUTH_CODES:
                return True, ("Instagram needs reconnecting — the access token is no "
                              "longer valid.")
            kind = "rate limit" if err.code in IG_RATE_LIMIT_CODES else "error"
            return False, (f"Instagram connection check hit a {kind} (code {err.code}, "
                           f"{err.error_type or 'no type'}, HTTP {status}).")
        if status == 401:
            return True, f"{platform.title()} needs reconnecting — it refused the saved login."
        return False, f"{platform.title()} connection check failed with HTTP {status}."

    # Adapter messages embed the status ("refresh failed (HTTP 503): <body>"), and the
    # body can say anything — "expired", "scope" — so a 5xx/429 is settled before the
    # text rules get a look at it.
    if re.search(r"\bHTTP (5\d\d|429)\b", str(err)):
        return False, f"{platform.title()} connection check failed: {redact.clip(str(err), 200)}"
    failure = publish_errors.classify(platform, err)
    if failure.requires_reauth:
        return True, failure.user_message
    # An adapter that says "permanent" on a read-only identity call is refusing the
    # grant (e.g. Bluesky rejecting the stored app password) — unless it also looked
    # like a transient failure above.
    if getattr(err, "permanent", False) and failure.category is not publish_errors.FailureCategory.RETRY:
        return True, f"{platform.title()} needs reconnecting: {redact.clip(str(err), 200)}"
    return False, f"{platform.title()} connection check failed: {redact.clip(str(err), 200)}"


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
            st["error"] = redact.clip(str(e), 300)
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
                    details={"message": message, "error": redact.clip(str(e), 300),
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
