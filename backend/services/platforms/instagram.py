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

import hashlib
import json
import re
import logging
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple, Sequence, Union

import httpx
from services import http_client, redact
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto import decrypt_token, encrypt_token
from models import PlatformCredential, PostPlatform, Reel
from services.platforms import credentials

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
# How early the daily job starts trying. This is a retry budget, not a deadline: the job
# runs once a day, so a 7-day leeway gave the whole Instagram pipeline exactly seven
# chances and, in practice, one meaningful one -- if that window were missed (box down,
# Meta wobbling, a transient 500) posting would simply stop on expiry day with nothing
# having looked wrong beforehand. 25 days costs nothing, since a refresh on a healthy
# token is idempotent and just re-stamps the expiry, and it turns a single point of
# failure into three and a half weeks of daily attempts. Meta requires the token be at
# least 24h old to refresh, which a 60-day token inside this window always is.
REFRESH_LEEWAY = timedelta(days=25)

# Meta accepts at most 3 co-authors per media.
MAX_COLLABORATORS = 3
# trial_params.graduation_strategy values (IG User Media reference, "trial_params";
# added to the Content Publishing API 2025-12-03 for Instagram Login and Facebook Login
# alike). SS_PERFORMANCE: Instagram shares it with followers if it performs. MANUAL:
# the account graduates it by hand in the Instagram app.
TRIAL_GRADUATION_STRATEGIES = ("SS_PERFORMANCE", "MANUAL")
TRIAL_HINT = "(sent as a Trial Reel — if this account isn't eligible, turn Trial off for this reel)"
# Meta's ceiling on carousel children. A carousel takes its aspect ratio from the FIRST
# child and crops the rest to match, which is why grouping validates ratio up front.
MAX_CAROUSEL = 10


# A media URL, or a zero-argument callable producing one. Deferred so the caller's work
# to produce a fetchable URL (staging, presigning, probing it — see scheduler and
# services/media_probe) runs only when a container is actually about to be created, and
# not at all when an attempt resumes a checkpointed container Meta already ingested.
MediaURL = Union[str, Callable[[], str]]

# Called while a publish waits on Meta, so the caller can renew whatever it holds on the
# work (reel_publish's claim). force=True comes right before media_publish. It may raise
# to stop the attempt; it is never called where an exception would be read as Meta's.
Heartbeat = Callable[..., None]


class StopAttempt(Exception):
    """Base for what a heartbeat raises to stop an attempt (reel_publish.ClaimLost).
    Never read as Meta's answer: code that turns lookup failures into "unconfirmed" or
    "not found" lets it through."""


def _no_heartbeat(force: bool = False) -> None:
    return None


def _resolve(url: MediaURL) -> str:
    return url() if callable(url) else url


class CarouselImage(NamedTuple):
    """One slide. alt is per-image; the caption belongs to the carousel, not the slide."""
    url: MediaURL
    alt: str | None = None

# Instagram caps: caption 2200 chars, alt text 1000.
MAX_CAPTION = 2200
MAX_ALT_TEXT = 1000

# Container status polling. Image containers are typically ready immediately; Meta's
# docs say poll once per minute for videos — for stills a few short beats suffice.
STATUS_POLL_TRIES = 10
STATUS_POLL_INTERVAL = 3.0

# Video is not an image with a longer download. Meta transcodes a reel server-side, and
# a 60-second 1080x1920 file routinely sits in IN_PROGRESS for two to three minutes --
# well past the 30s budget that is generous for a JPEG. Polling for up to eight minutes
# costs nothing when the container finishes early, and a worker that gives up too soon
# republishes a reel Meta was still busy accepting.
REEL_POLL_TRIES = 96
REEL_POLL_INTERVAL = 5.0

# FINISHED is not the same promise as publishable. Meta can answer the status check with
# FINISHED and then reject media_publish a few hundred milliseconds later because the
# media has not replicated yet — observed 2026-09-19, where container creation, the
# FINISHED reply and the 400 all landed inside 400ms. The message asks for a wait, so
# wait here rather than handing the post to the worker's minutes-long backoff: this is
# the difference between a post landing on time and landing late.
PUBLISH_RETRY_TRIES = 6
PUBLISH_RETRY_INTERVAL = 5.0
_NOT_READY_RE = re.compile(r"not ready for publishing|media is not ready", re.I)


# Meta keeps an unpublished container for 24h, then it reads EXPIRED. 23h leaves room
# for an attempt that starts just inside the window to finish inside it.
CONTAINER_REUSE_WINDOW = timedelta(hours=23)
# Clock skew allowed when matching a recovered media's timestamp against the moment we
# created its container: Meta's clock and ours are not the same clock.
RECOVERY_SKEW = timedelta(minutes=5)
# Page size, and a hard ceiling on pages, when walking the account's media back through
# the recovery interval. 40 pages of 25 is a thousand posts — far more than this account
# publishes in any recovery window — so the ceiling only guards against a paging loop.
RECOVERY_PAGE_SIZE = 25
RECOVERY_MAX_PAGES = 40
# ...and how long after the publish request a match may appear. Unbounded, a later post
# reusing the same caption (55 titles are reused across the queue) would be claimed as
# this one's, and this post would be marked live without ever being published.
RECOVERY_WINDOW = timedelta(minutes=15)
# An unconfirmed publish waits this long before the next attempt asks Meta about it: the
# 1-minute first step of the backoff curve would hit Meta again while whatever caused the
# timeout or 5xx is most likely still going on.
UNCONFIRMED_RETRY_SECONDS = 420


class InstagramError(Exception):
    def __init__(self, message: str, *, permanent: bool = False,
                 http_status: int | None = None):
        # Masked at birth: this text is logged, stored as error_message and shown in
        # Settings, and wrapped transport errors ({e}) can quote a URL carrying the
        # token (the refresh call still does). Subclasses inherit it.
        super().__init__(redact.redact(message))
        self.permanent = permanent
        # Set when Meta actually answered. A 4xx is Meta saying no; no status at all
        # (timeout, reset, unparseable 200) means we don't know what Meta did.
        self.http_status = http_status


class PublishUnconfirmed(InstagramError):
    """media_publish was sent and we cannot tell whether it worked.

    Never permanent, and never answered by publishing again in the same attempt: Meta has
    been seen publishing on requests it answered with an error, and re-sending the same
    creation_id then stacked duplicates. The container stays checkpointed; the next
    attempt asks Meta for its status_code (and the account's recent media) first.
    """

    def __init__(self, message: str):
        super().__init__(message, permanent=False)
        # Read by the scheduler's failure recorder (same hook as carousel resume).
        self.retry_after_seconds = UNCONFIRMED_RETRY_SECONDS


class TrialReelRejected(InstagramError):
    """Meta refused a reel *as a trial* — the account isn't enabled for Trial Reels, or
    has used up whatever allowance Meta gives it.

    Always permanent, and never answered by quietly publishing an ordinary reel instead:
    that would put the reel in front of followers, which is exactly what the photographer
    chose not to do. The fix is a human decision (turn Trial off, or wait and reschedule),
    so the message says so.
    """

    def __init__(self, meta_says: str, *, http_status: int | None = None):
        super().__init__(
            "Instagram refused this as a Trial Reel — nothing was published. "
            f"Meta said: {meta_says} Trial Reels are switched on per account by "
            "Instagram (and may be rationed); either turn Trial off for this reel or "
            "try again later, then reschedule it.",
            permanent=True, http_status=http_status,
        )


def _is_trial_rejection(text: str) -> bool:
    """Does this refusal concern the trial itself?

    Meta documents no error code for an ineligible account. Every rejection seen
    reported names the trial ("trial reel" / "trial_params"), so that is the test —
    deliberately narrow: an unrelated 400 (bad video, bad caption) keeps its own message
    rather than being blamed on the trial.
    """
    return "trial" in (text or "").lower()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def content_fingerprint(caption: str, collaborators: Sequence[str],
                        media_identity: str | None) -> str:
    """What a container was built from: caption, the co-authors asked for, and the
    caller's identity for the media (source + crop/ratio for a photo, the file for a
    reel). A resumed container whose fingerprint no longer matches would publish the
    old caption or the old crop, so it is rebuilt — if it was never sent for publish."""
    blob = json.dumps([caption or "", [c.lower() for c in collaborators], media_identity or ""])
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


@dataclass
class ContainerCheckpoint:
    """A container Meta has accepted, remembered across attempts and crashes.

    Carousel children already survive a failed attempt (post_platforms.carousel_children);
    the container that actually gets published — a photo's, a reel's, a carousel's
    parent — did not. So a crash, timeout or deploy between /media and /media_publish
    either orphaned it (and the retry paid for a new one) or, worse, left a publish whose
    outcome nobody knew, and the retry published a second copy.

    Persisted by the caller (on_checkpoint) BEFORE media_publish is sent, for the same
    survive-the-rollback reason as staging_remote_id. publish_sent_at is the
    "unconfirmed" marker: set just before media_publish, cleared only when Meta answers
    with a definite no. The collaborators ride along because a resumed container keeps
    the collaborators it was created with, and the bookkeeping must record those.
    """
    container_id: str
    created_at: datetime                      # naive UTC, like every stored timestamp
    publish_sent_at: datetime | None = None
    collaborators: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    # content_fingerprint() of what the container was built from. None on checkpoints
    # written before fingerprints existed; those are resumed as before.
    fingerprint: str | None = None
    # The caption actually SUBMITTED with the container. Recovery searches for this, not
    # for whatever the caption is now — the post may have been edited since. None on
    # older checkpoints, which fall back to the current caption.
    caption: str | None = None
    # What kind of reel the container was built as: None for an ordinary reel (and for
    # every photo/carousel), else the trial's graduation_strategy. Recorded so that a
    # recovered publish reports what actually went out, not what the reel says now.
    # Checkpoints written before trials existed have none, and were all ordinary.
    trial_graduation: str | None = None

    def to_json(self) -> str:
        return json.dumps({
            "caption": self.caption,
            "trial_graduation": self.trial_graduation,
            "fingerprint": self.fingerprint,
            "id": self.container_id,
            "created_at": self.created_at.isoformat(),
            "publish_sent_at": self.publish_sent_at.isoformat() if self.publish_sent_at else None,
            "collaborators": self.collaborators,
            "rejected": self.rejected,
        })

    @classmethod
    def from_json(cls, raw: str | None) -> "ContainerCheckpoint | None":
        """Tolerant: an unreadable checkpoint is treated as none, never as an error —
        the worst outcome of forgetting a checkpoint is today's behaviour."""
        if not raw:
            return None
        try:
            d = json.loads(raw)
            sent = d.get("publish_sent_at")
            return cls(
                container_id=str(d["id"]),
                created_at=datetime.fromisoformat(d["created_at"]),
                publish_sent_at=datetime.fromisoformat(sent) if sent else None,
                collaborators=list(d.get("collaborators") or []),
                rejected=list(d.get("rejected") or []),
                fingerprint=d.get("fingerprint"),
                caption=d.get("caption"),
                trial_graduation=d.get("trial_graduation"),
            )
        except (ValueError, KeyError, TypeError):
            log.warning("instagram: ignoring unreadable container checkpoint %r", raw[:200])
            return None


def aspect_ok(width: int, height: int) -> bool:
    if not width or not height:
        return True  # unknown dims — let Meta be the judge rather than block the post
    ratio = width / height
    # Small epsilon: Meta accepts exactly-4:5 crops that integer dims land a hair under.
    return (MIN_ASPECT - 0.005) <= ratio <= (MAX_ASPECT + 0.005)


def _client() -> httpx.Client:
    return http_client.client(base_url=f"{GRAPH}/{API_VERSION}", timeout=60.0)


def _auth(token: str) -> dict[str, str]:
    """The token as an Authorization header, for GETs.

    A GET's parameters are its URL, and httpx logs every request URL at INFO — so an
    `access_token` query parameter put the live token in the worker's docker log on every
    status poll. Meta's content-publishing guide sends this same token as
    `Authorization: Bearer` to graph.instagram.com, and comments.py has read media,
    comments and insights that way since August. POSTs keep the token in the form body:
    a body is never part of the logged request line, so there is nothing to move.
    The one exception is /refresh_access_token (see _refresh), documented query-only.
    """
    return {"Authorization": f"Bearer {token}"}


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
        http_status=r.status_code,
    )


# -----------------------------------------------------------------------------
# Connection lifecycle
# -----------------------------------------------------------------------------

def connect(db: Session, *, access_token: str) -> PlatformCredential:
    """Validate a pasted long-lived token via GET /me and persist it encrypted."""
    access_token = access_token.strip()
    # Known to redact() before it is ever sent: crypto only learns it at encryption,
    # after /me — and a /me that fails can echo the token into the error shown here.
    redact.remember(access_token)
    try:
        with _client() as c:
            r = c.get("/me", params={"fields": "user_id,username,account_type"},
                      headers=_auth(access_token))
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
    # Update in place, never delete-and-insert: post_platforms rows hang off this id,
    # and a fresh uuid orphaned (pre-0033: cascaded away) every Instagram post on record.
    cred = credentials.upsert(db, PLATFORM)
    cred.access_token = encrypt_token(access_token)
    cred.token_expires = now + TOKEN_LIFETIME
    cred.extra_json = json.dumps({
        "ig_user_id": ig_user_id,
        "account_type": account_type,
    })
    credentials.mark_connected(cred, account_name=me.get("username") or "")
    cred.last_success_at = now
    cred.key_version = KEY_VERSION
    db.commit()
    db.refresh(cred)
    log.info("instagram connected: @%s (ig_user_id=%s, type=%s)",
             cred.account_name, ig_user_id, account_type or "?")
    return cred


def disconnect(db: Session) -> bool:
    """Forget the token; keep the row so published history (and its media ids) survive."""
    return credentials.disconnect(db, PLATFORM, keep_extra=("ig_user_id", "account_type"))


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
    # Note: refresh_access_token is unversioned (no /vXX.X prefix). It stays a query
    # parameter: Meta documents this endpoint only in that form, and a refresh that fails
    # on an undocumented header is how a token quietly expires. The logging redaction
    # (services/redact.py) is what keeps this one URL out of the log.
    with http_client.client(base_url=GRAPH, timeout=30.0) as c:
        r = c.get("/refresh_access_token", params={
            "grant_type": "ig_refresh_token",
            "access_token": token,
        })
    if r.status_code >= 400:
        _raise_api_error(r, "Token refresh")
    body = r.json()
    row.access_token = encrypt_token(body["access_token"])
    # A freshly stored token means the channel is authorised again — drop any
    # "needs reconnecting" flag so the health banner clears immediately rather
    # than waiting for the next scheduled post to prove it.
    row.auth_status, row.auth_error, row.auth_flagged_at = "ok", None, None
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


def _is_fetch_failure(text: str) -> bool:
    """Does this 400 mean Meta declined to fetch image_url?

    Two phrasings for one condition, and which one comes back depends on whether Meta
    fills in error_user_title: "Media download has failed" and "The media could not be
    fetched from this URI". Matching only the first read the second as a permanent bad
    request, which fails the post outright — and the second is the one that shows up
    when Meta is rationing fetches rather than actually choking on the image.
    """
    low = text.lower()
    return "download" in low or "could not be fetched" in low


def _create_container(
    ig_user_id: str, data: dict, collaborators: list[str], *, fetch_attempts: int = 3,
    heartbeat: Heartbeat = _no_heartbeat,
) -> tuple[str, list[str], list[str]]:
    """Create the media container, degrading gracefully on bad collaborators.

    fetch_attempts: how many times to re-offer image_url when Meta says it couldn't
    download it. Worth 3 for a lone photo, where the likely cause is Meta racing CDN
    propagation of a just-staged variant. Worth exactly 1 inside a carousel, which
    already retries at a better layer — the next attempt resumes from the children that
    landed, spaced by the backoff curve rather than by seconds.

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
        for fetch_attempt in range(fetch_attempts):
            heartbeat()
            with _client() as c:
                r = c.post(f"/{ig_user_id}/media", data=body)
            if r.status_code < 400:
                break
            if _is_fetch_failure(_error_text(r)) and fetch_attempt < fetch_attempts - 1:
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

        if _is_fetch_failure(_error_text(r)):
            raise InstagramError(
                f"Meta couldn't fetch the image URL after retries: {_error_text(r)}",
                permanent=False,
            )
        _raise_api_error(r, "media container creation")

    raise InstagramError("media container creation failed after dropping all collaborators")


Checkpoint = Callable[["ContainerCheckpoint | None"], None]


def _wanted_collaborators(collaborators: list[str] | None) -> list[str]:
    return [
        h.lstrip("@").strip()
        for h in (collaborators or [])
        if h and h.strip()
    ][:MAX_COLLABORATORS]


def _credential_parts(db: Session) -> tuple[str, str]:
    row = _load_credential(db)
    _maybe_refresh(db, row)
    token = decrypt_token(row.access_token)
    ig_user_id = json.loads(row.extra_json or "{}").get("ig_user_id")
    if not ig_user_id:
        raise InstagramError("Credential is missing ig_user_id — reconnect Instagram.", permanent=True)
    return token, ig_user_id


def post_photo(
    db: Session,
    *,
    image_url: MediaURL,
    caption: str,
    alt_text: str | None = None,
    collaborators: list[str] | None = None,
    checkpoint: ContainerCheckpoint | None = None,
    on_checkpoint: Checkpoint | None = None,
    media_identity: str | None = None,
) -> dict:
    """Container → poll → publish. Returns {remote_id, url, collaborators}.

    collaborators: up to MAX_COLLABORATORS Instagram usernames invited as co-authors.
    Accepted invitations put the post on THEIR profile and in their followers' feeds —
    roughly double the impressions of a solo post, which is why this is worth the extra
    failure handling below. Feed images, carousels and Reels only (never Stories).

    checkpoint/on_checkpoint make the publish crash-safe; see _publish_resumable. The
    result carries recovered=True when an earlier attempt's publish turned out to have
    gone through, in which case remote_id may be None (see _find_published).
    """
    token, ig_user_id = _credential_parts(db)
    caption_text = (caption or "")[:MAX_CAPTION]

    def create() -> tuple[str, list[str], list[str]]:
        # Step 1: create the media container. Meta fetches image_url during this call,
        # so a dead/non-JPEG/oversized URL or bad aspect ratio surfaces here as a 400.
        data = {
            "image_url": _resolve(image_url),
            "caption": caption_text,
            "access_token": token,
        }
        alt = (alt_text or "").strip()
        if alt:
            data["alt_text"] = alt[:MAX_ALT_TEXT]
        # Co-authors. Meta validates every handle and rejects the WHOLE container if any
        # one is private, misspelled, or gone — so a stale handle in the performer roster
        # would otherwise cost us the entire post. _create_container drops the named
        # offenders and retries, so the photo always ships even if a credit doesn't.
        container_id, used, rejected = _create_container(
            ig_user_id, data, _wanted_collaborators(collaborators)
        )
        if rejected:
            log.warning("instagram: dropped un-taggable collaborator(s) %s and retried", rejected)
        return container_id, used, rejected

    return _publish_resumable(
        db, ig_user_id, token, checkpoint=checkpoint, on_checkpoint=on_checkpoint,
        create=create, describing="photo", caption=caption_text, media_types=("IMAGE",),
        fingerprint=content_fingerprint(
            caption_text, _wanted_collaborators(collaborators), media_identity),
    )


def post_reel(
    db: Session,
    *,
    video_url: MediaURL,
    caption: str,
    collaborators: list[str] | None = None,
    share_to_feed: bool = True,
    trial_graduation: str | None = None,
    checkpoint: ContainerCheckpoint | None = None,
    on_checkpoint: Checkpoint | None = None,
    media_identity: str | None = None,
    heartbeat: Heartbeat | None = None,
) -> dict:
    """Publish a reel from a publicly fetchable MP4. Returns {remote_id, url, ...}.

    Same container -> poll -> publish shape as post_photo, with three differences that
    matter:

    - `media_type=REELS` and `video_url`. Meta downloads and transcodes during the
      container step, so a file it cannot read fails here rather than at publish.
    - No alt_text. Meta documents the field as unsupported on reels and stories; sending
      it anyway is a 400 on a post that would otherwise have shipped.
    - A much longer poll (see REEL_POLL_TRIES) because transcoding is not instant.

    `share_to_feed` also puts the reel in the main grid. Left on: a reel that appears
    only under the Reels tab is invisible to the followers who browse the profile, and
    reach is the entire reason for posting one.

    Checkpointed like post_photo — and it matters most here: a reel's container spends
    minutes transcoding, which is the widest window any publish has for a crash.

    `trial_graduation` (SS_PERFORMANCE | MANUAL) publishes a Trial Reel: shown to
    non-followers first, graduating to followers per the strategy. Meta documents only
    the trial_params field itself; the two rules below are the conservative reading of
    what it leaves unsaid, because a Meta 400 on a trial fails the whole reel:

    - No share_to_feed. It asks for the reel in followers' Feed, which is the opposite of
      a trial, and Meta says nothing about combining the two — so it is left out rather
      than sent either way. Graduation is what decides whether followers see it.
    - No collaborators. Meta's docs are silent; schedulers that ship Trial Reels
      (Metricool) say collabs can't be added, and the app offers none on a trial. The
      performers' @handles are still in the caption.

    A refusal that concerns the trial raises TrialReelRejected — never a fallback to an
    ordinary reel, which would show followers what the photographer chose not to.
    """
    token, ig_user_id = _credential_parts(db)
    caption_text = (caption or "")[:MAX_CAPTION]
    if trial_graduation is not None and trial_graduation not in TRIAL_GRADUATION_STRATEGIES:
        raise InstagramError(f"unknown trial graduation strategy {trial_graduation!r}",
                             permanent=True)
    trial = trial_graduation is not None
    wanted = [] if trial else _wanted_collaborators(collaborators)
    if trial and collaborators:
        log.info("instagram: trial reel — not inviting collaborators %s", collaborators)

    def create() -> tuple[str, list[str], list[str]]:
        data = {
            "media_type": "REELS",
            "video_url": _resolve(video_url),
            "caption": caption_text,
            "access_token": token,
        }
        if trial:
            # Form-encoded like collaborators: the Graph API reads a JSON string as the
            # object (Meta's own example sends the same object in a JSON body).
            data["trial_params"] = json.dumps({"graduation_strategy": trial_graduation})
        else:
            data["share_to_feed"] = "true" if share_to_feed else "false"
        try:
            container_id, used, rejected = _create_container(
                ig_user_id, data, wanted, heartbeat=heartbeat or _no_heartbeat)
        except InstagramError as e:
            # Meta documents no error for an account without Trial Reels, so a refusal
            # that doesn't name the trial may still be about it. Classification stays
            # with the text test below; this only points at the likeliest cause.
            if (trial and e.permanent and e.http_status is not None
                    and 400 <= e.http_status < 500 and not _is_trial_rejection(str(e))):
                hinted = InstagramError(
                    f"{e} {TRIAL_HINT}", permanent=True, http_status=e.http_status,
                )
                # The hint says "Trial Reel"; the text test below must not then read our
                # own words as Meta refusing the trial.
                hinted.trial_hinted = True
                raise hinted from e
            raise
        if rejected:
            log.warning("instagram: dropped un-taggable collaborator(s) %s and retried", rejected)
        return container_id, used, rejected

    # The trial setting is part of what the container was built from: toggling it after
    # a failed attempt must not publish the old container as the old kind of reel.
    # Appended only for trials, so an ordinary reel's fingerprint is what it always was
    # and a checkpoint written before this change still resumes.
    identity = f"{media_identity or ''}|trial:{trial_graduation}" if trial else media_identity
    try:
        return _publish_resumable(
            db, ig_user_id, token, checkpoint=checkpoint, on_checkpoint=on_checkpoint,
            create=create, describing="trial reel" if trial else "reel", caption=caption_text,
            media_types=("VIDEO", "REELS"),
            tries=REEL_POLL_TRIES, interval=REEL_POLL_INTERVAL,
            fingerprint=content_fingerprint(caption_text, wanted, identity),
            trial_graduation=trial_graduation,
            heartbeat=heartbeat,
        )
    except PublishUnconfirmed:
        raise
    except InstagramError as e:
        if trial and e.http_status is not None and 400 <= e.http_status < 500 \
                and not getattr(e, "trial_hinted", False) and _is_trial_rejection(str(e)):
            raise TrialReelRejected(str(e), http_status=e.http_status) from e
        raise


def post_carousel(
    db: Session,
    *,
    images: Sequence[CarouselImage],
    caption: str,
    collaborators: list[str] | None = None,
    resume: Sequence[str] = (),
    on_child: Callable[[list[str]], None] | None = None,
    checkpoint: ContainerCheckpoint | None = None,
    on_checkpoint: Checkpoint | None = None,
    media_identity: str | None = None,
) -> dict:
    """Publish 2..MAX_CAROUSEL images as one carousel. Returns the same shape as
    post_photo.

    resume/on_child make the build restartable. Every frame depends on Meta fetching a
    public URL, and a failure is reported as "the media could not be fetched from this
    URI" — indistinguishable from a dead link, and it fires even for URLs that curl
    fetches fine. Once fetches start failing only the frames before the first failure get
    through, so a long carousel may need several attempts. on_child is called with the
    children built so far after each one; passing them back as resume next time lets a
    carousel finish across several attempts instead of restarting forever.

    checkpoint/on_checkpoint do the same for the PARENT container, the one that actually
    publishes. A resumable parent short-circuits the whole child build: Meta already has
    every frame, so none is fetched again.

    Three steps rather than two: a container per image (is_carousel_item, no caption of
    its own), then a parent container listing them, then publish the parent. The caption
    and the co-author invitations belong to the parent — a child carrying either is a
    silent no-op, which is the kind of bug that only shows up on someone else's profile.

    Children are created sequentially. Meta's fetcher races CDN propagation of freshly
    staged images (see _create_container), so firing ten at once mostly buys ten
    simultaneous chances to lose that race.
    """
    if not 2 <= len(images) <= MAX_CAROUSEL:
        raise InstagramError(
            f"a carousel needs 2..{MAX_CAROUSEL} images, got {len(images)}",
            permanent=True,
        )

    token, ig_user_id = _credential_parts(db)
    caption_text = (caption or "")[:MAX_CAPTION]

    def create() -> tuple[str, list[str], list[str]]:
        # Children an earlier attempt already built, in frame order. Meta keeps a
        # container for 24h, far longer than the gap between retry attempts.
        child_ids: list[str] = [c for c in resume if c]
        if len(child_ids) > len(images):
            raise InstagramError(
                f"carousel has {len(images)} frames but {len(child_ids)} children were "
                f"carried over — the group changed underneath the schedule.",
                permanent=True,
            )
        for i, img in enumerate(images):
            if i < len(child_ids):
                continue
            url = _resolve(img.url)
            data = {
                "image_url": url,
                "is_carousel_item": "true",
                "access_token": token,
            }
            alt = (img.alt or "").strip()
            if alt:
                data["alt_text"] = alt[:MAX_ALT_TEXT]
            # No collaborators on children — the caption and the co-author invitations
            # belong to the parent. One fetch attempt only: see _create_container.
            child_id, _, _ = _create_container(ig_user_id, data, [], fetch_attempts=1)
            _await_container(child_id, token, describing=f"child {i + 1}/{len(images)}, url={url}")
            child_ids.append(child_id)
            if on_child:
                on_child(list(child_ids))

        parent_data = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption_text,
            "access_token": token,
        }
        parent_id, used, rejected = _create_container(
            ig_user_id, parent_data, _wanted_collaborators(collaborators)
        )
        if rejected:
            log.warning("instagram: dropped un-taggable collaborator(s) %s and retried", rejected)
        return parent_id, used, rejected

    result = _publish_resumable(
        db, ig_user_id, token, checkpoint=checkpoint, on_checkpoint=on_checkpoint,
        create=create, describing=f"carousel of {len(images)}", caption=caption_text,
        media_types=("CAROUSEL_ALBUM",),
        fingerprint=content_fingerprint(
            caption_text, _wanted_collaborators(collaborators), media_identity),
    )

    media_id = result["remote_id"]
    published = _count_children(media_id, token) if media_id else None
    if published is not None and published != len(images):
        # Meta accepted the parent, published it, and returned 200 while quietly
        # producing fewer frames than the children list named. It happened on
        # 2026-09-14: seven children sent, six live, retry_count 0, no error anywhere.
        # Nothing downstream noticed, because nothing was looking.
        #
        # Not raised. The post is already public; raising here would retry it and put a
        # second copy on the profile, which is worse than a short one. Reported instead,
        # loudly, so the caller can record it against the post.
        log.error(
            "instagram: carousel %s published %d of %d frames — Meta dropped %d",
            media_id, published, len(images), len(images) - published,
        )

    result["frames_sent"] = len(images)
    result["frames_published"] = published
    return result


# -----------------------------------------------------------------------------
# Crash-safe publish: checkpoint the container, never publish twice
# -----------------------------------------------------------------------------

def _publish_resumable(
    db: Session,
    ig_user_id: str,
    token: str,
    *,
    checkpoint: ContainerCheckpoint | None,
    on_checkpoint: Checkpoint | None,
    create: Callable[[], tuple[str, list[str], list[str]]],
    describing: str,
    caption: str,
    media_types: tuple[str, ...],
    tries: int = STATUS_POLL_TRIES,
    interval: float = STATUS_POLL_INTERVAL,
    fingerprint: str | None = None,
    trial_graduation: str | None = None,
    heartbeat: Heartbeat | None = None,
) -> dict:
    """Publish via a container, resuming the checkpointed one when there is one.

    On a resumed attempt Meta is asked what became of the container first:

      PUBLISHED    it went out. Never publish again; recover the media id (best effort)
                   and report success.
      FINISHED     ready and unpublished: publish THIS container. No re-fetch of the
                   image, and the collaborators it was created with stand.
      IN_PROGRESS  still ingesting (reels transcode for minutes): wait, then publish.
      anything else (ERROR, EXPIRED, unknown, gone) → discard it, build a fresh one.

    When the last attempt SENT media_publish without learning the outcome, FINISHED is
    not trusted on its own — the account's recent media is checked for the post first,
    and a failure to look is a reason to wait, not to publish blind.

    The caller clears the checkpoint when it records the success (in the same commit),
    not this function: clearing first and crashing before the record would leave the
    next attempt with neither a record nor a checkpoint, which is how duplicates happen.
    """
    save = on_checkpoint or (lambda _cp: None)
    beat = heartbeat or _no_heartbeat
    cp = checkpoint
    if cp is not None:
        resumed = _resume_checkpoint(
            db, ig_user_id, token, cp, save=save, describing=describing, caption=caption,
            media_types=media_types, tries=tries, interval=interval, fingerprint=fingerprint,
            trial_graduation=trial_graduation, heartbeat=beat,
        )
        if resumed is not None:
            return resumed
        save(None)

    container_id, used, rejected = create()
    cp = ContainerCheckpoint(container_id=container_id, created_at=_utcnow(),
                             collaborators=list(used), rejected=list(rejected),
                             fingerprint=fingerprint, caption=caption,
                             trial_graduation=trial_graduation)
    save(cp)
    _await_container(container_id, token, describing=describing, tries=tries, interval=interval,
                     heartbeat=beat)
    return _publish_checkpointed(ig_user_id, token, cp, save=save, heartbeat=beat)


def _resume_checkpoint(
    db: Session, ig_user_id: str, token: str, cp: ContainerCheckpoint, *, save: Checkpoint,
    describing: str, caption: str, media_types: tuple[str, ...],
    tries: int, interval: float, fingerprint: str | None = None,
    trial_graduation: str | None = None, heartbeat: Heartbeat = _no_heartbeat,
) -> dict | None:
    """Act on a checkpoint. Returns the publish result, or None to build a fresh one."""
    # Built as a different kind of reel than is now wanted. The fingerprint catches this
    # for new checkpoints; a legacy one has no fingerprint (and no kind, because it
    # predates trials and was ordinary), so without this a trial request would resume —
    # and publish — the old ordinary container. Legacy ordinary reels still resume.
    wrong_kind = (cp.trial_graduation or None) != (trial_graduation or None)
    fresh = _utcnow() - cp.created_at < CONTAINER_REUSE_WINDOW
    if not fresh and not cp.publish_sent_at:
        log.info("instagram: checkpointed container %s is past Meta's 24h window — "
                 "building a new one", cp.container_id)
        return None
    if not cp.publish_sent_at and (wrong_kind or (
            cp.fingerprint and fingerprint and cp.fingerprint != fingerprint)):
        # The caption, the co-authors or the image changed since this container was
        # built (typically after a 4xx sent the post back for editing). Publishing it
        # would ship the old version. Safe to drop only because no publish was ever sent
        # for it; once one has been, the question is "is it live?", not "is it current?".
        log.info("instagram: container %s was built from different content — "
                 "building a new one", cp.container_id)
        return None

    status = _container_status(cp.container_id, token)
    log.info("instagram: resuming container %s (%s, publish %s)", cp.container_id,
             status, "sent" if cp.publish_sent_at else "not sent")
    # Search for what was submitted, not what the post says now.
    sent_caption = cp.caption if cp.caption is not None else caption

    if status == "PUBLISHED":
        # Meta itself says it is live, so it is never published again. The media id is a
        # nicety: take it only when exactly one candidate fits. Two same-caption posts in
        # the window means we cannot tell which is ours, and a wrong id is worse than
        # none (engagement would be sampled from somebody else's post).
        try:
            found = _find_published(db, ig_user_id, token, cp, sent_caption, media_types,
                                    heartbeat=heartbeat)
        except StopAttempt:
            raise
        except Exception as e:  # noqa: BLE001 — it is live either way; the id is a nicety
            log.warning("instagram: container %s is published but its media id could not "
                        "be looked up: %s", cp.container_id, e)
            found = []
        return _recovered(cp, found[0] if len(found) == 1 else None)

    if not cp.publish_sent_at:
        if not fresh:
            return None
        if status == "FINISHED":
            return _publish_checkpointed(ig_user_id, token, cp, save=save, heartbeat=heartbeat)
        if status == "IN_PROGRESS":
            _await_container(cp.container_id, token, describing=f"resumed {describing}",
                             tries=tries, interval=interval, heartbeat=heartbeat)
            return _publish_checkpointed(ig_user_id, token, cp, save=save, heartbeat=heartbeat)
        log.info("instagram: discarding container %s (status %r)", cp.container_id, status)
        return None

    # A publish went out last time and nobody heard back. Every branch below has to
    # choose between "it's live" and "it isn't", and the costs are lopsided: wrongly
    # "live" loses one post; wrongly "not live" puts a duplicate on the profile. So a
    # failed lookup, an ambiguous match, or a container Meta can no longer describe all
    # stay unresolved (PublishUnconfirmed) — the retry budget then ends the row as
    # failed, where a human can look, rather than the code guessing.
    try:
        found = _find_published(db, ig_user_id, token, cp, sent_caption, media_types,
                                heartbeat=heartbeat)
    except StopAttempt:
        raise
    except Exception as e:  # noqa: BLE001
        raise PublishUnconfirmed(
            f"media publish outcome still unconfirmed for container {cp.container_id} "
            f"(status {status}); recent-media lookup failed: {e}"
        ) from e

    if status in ("FINISHED", "IN_PROGRESS"):
        # Meta says this container has NOT been published. A caption+time match then
        # contradicts Meta's own answer, so it is weak evidence — it may be a different
        # post that happens to share the caption. Adopt it only when it is unambiguous:
        # exactly one candidate, of the right type, in the window, not recorded against
        # any other post or reel, with the submitted caption byte-for-byte (whitespace
        # aside). Status can lag a publish by moments; a unique match is the likelier
        # explanation than coincidence. Several matches: we can't tell, so unresolved.
        if len(found) == 1:
            return _recovered(cp, found[0])
        if len(found) > 1:
            raise PublishUnconfirmed(
                f"media publish outcome unconfirmed for container {cp.container_id}: "
                f"{len(found)} same-caption posts in the window and Meta reports the "
                f"container {status}"
            )
        # Meta says not published and nothing like it is live: send THIS container again
        # (never a new one — if Meta did take the first request after all, re-sending the
        # same creation_id is the only way it can recognise it).
        log.info("instagram: container %s was sent for publish but isn't live — "
                 "Meta didn't take it", cp.container_id)
        cp.publish_sent_at = None
        save(cp)
        if wrong_kind or (cp.fingerprint and fingerprint and cp.fingerprint != fingerprint):
            log.info("instagram: container %s was built from different content — "
                     "building a new one", cp.container_id)
            return None
        if not fresh:
            return None
        if status == "IN_PROGRESS":
            _await_container(cp.container_id, token, describing=f"resumed {describing}",
                             tries=tries, interval=interval, heartbeat=heartbeat)
        return _publish_checkpointed(ig_user_id, token, cp, save=save, heartbeat=heartbeat)

    # ERROR, EXPIRED, unknown, or gone — after a publish was sent. Meta can't say what
    # happened, so only a unique match settles it. A negative search is not proof: the
    # media may be outside what the listing returns, or captioned differently than
    # recorded. Building a fresh container here is exactly how duplicates were made.
    if len(found) == 1:
        return _recovered(cp, found[0])
    raise PublishUnconfirmed(
        f"media publish outcome unconfirmed for container {cp.container_id}: Meta reports "
        f"it {status or 'gone'} and {len(found)} matching post(s) were found — not "
        f"publishing again"
    )


def _publish_checkpointed(
    ig_user_id: str, token: str, cp: ContainerCheckpoint, *, save: Checkpoint,
    heartbeat: Heartbeat = _no_heartbeat,
) -> dict:
    """media_publish, with the "sent" mark made durable before the request goes out."""
    # Last chance to stop: whoever this attempt answers to must still want it published.
    # Outside the try below, so a refusal here is never mistaken for an unconfirmed send.
    heartbeat(force=True)
    cp.publish_sent_at = _utcnow()
    save(cp)
    try:
        media_id, permalink = _publish_container(ig_user_id, cp.container_id, token)
    except PublishUnconfirmed:
        raise
    except InstagramError as e:
        if e.http_status is not None and 400 <= e.http_status < 500:
            # Meta answered, and the answer was no: nothing went out. Keep the container
            # (it may be publishable next time) but drop the "sent" mark, so the retry
            # doesn't go hunting for a post that was refused.
            cp.publish_sent_at = None
            save(cp)
            raise
        raise PublishUnconfirmed(
            f"media publish outcome unconfirmed for container {cp.container_id} ({e}) — "
            f"the next attempt checks the container before publishing again"
        ) from e
    except Exception as e:  # noqa: BLE001 — timeout, reset, garbage body: we don't know
        raise PublishUnconfirmed(
            f"media publish outcome unconfirmed for container {cp.container_id} "
            f"({type(e).__name__}: {e}) — the next attempt checks the container before "
            f"publishing again"
        ) from e
    return {
        "remote_id": str(media_id),
        "url": permalink,
        "collaborators": list(cp.collaborators),
        "collaborators_rejected": list(cp.rejected),
        "trial_graduation": cp.trial_graduation,
    }


def _recovered(cp: ContainerCheckpoint, found: tuple[str, str | None] | None) -> dict:
    if found:
        log.warning("instagram: container %s had already published as %s — not publishing "
                    "again", cp.container_id, found[0])
    else:
        log.error("instagram: container %s had already published but its media id could "
                  "not be matched — recorded as posted without one", cp.container_id)
    return {
        "remote_id": found[0] if found else None,
        "url": found[1] if found else None,
        "collaborators": list(cp.collaborators),
        "collaborators_rejected": list(cp.rejected),
        "trial_graduation": cp.trial_graduation,
        "recovered": True,
    }


def _container_status(container_id: str, token: str) -> str | None:
    """status_code of a container, or None when Meta no longer knows it.

    A token problem or a 5xx raises: neither says anything about the container, and
    discarding it on that basis could publish a second copy of a post already live.
    """
    with _client() as c:
        r = c.get(f"/{container_id}", params={"fields": "status_code"}, headers=_auth(token))
    if r.status_code >= 500:
        _raise_api_error(r, "container status check")
    if r.status_code >= 400:
        try:
            code = (r.json().get("error") or {}).get("code")
        except Exception:  # noqa: BLE001
            code = None
        if r.status_code in (401, 403) or code in (190, 10, 200):
            _raise_api_error(r, "container status check")
        return None
    return r.json().get("status_code")


def _parse_ig_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").astimezone(
            timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def _find_published(
    db: Session, ig_user_id: str, token: str, cp: ContainerCheckpoint, caption: str,
    media_types: tuple[str, ...], heartbeat: Heartbeat = _no_heartbeat,
) -> list[tuple[str, str | None]]:
    """Media the checkpointed container might have become, oldest first.

    Meta documents no field on a container that names the media it published as (the
    IG Container reference lists status_code values only; media_publish's response is the
    one place the id is returned). So the only way back is to match. A candidate must be:
      * of the right media type;
      * timestamped inside the recovery interval — from container creation (minus clock
        skew) to RECOVERY_WINDOW after the publish request (or creation);
      * captioned with exactly the caption that was submitted. An empty submitted
        caption matches nothing: time alone would claim any post in the window;
      * not already recorded against any post or reel (captions are reused — 55 titles
        across the queue — and another post's media must never be adopted).

    Pages through the account's media (newest first, `after` cursor rather than the
    paging.next URL, which embeds the token) until it is past the interval, rather than
    stopping at the first page: a busy hour must not turn "it's live" into "not found".
    Raises when the lookup fails. Returns every candidate; callers decide how many is
    too many.
    """
    want = " ".join((caption or "").split())
    if not want:
        return []
    since = cp.created_at - RECOVERY_SKEW
    until = (cp.publish_sent_at or cp.created_at) + RECOVERY_WINDOW

    items: list[dict] = []
    after: str | None = None
    for _page in range(RECOVERY_MAX_PAGES):
        # Up to RECOVERY_MAX_PAGES requests: long enough to need renewing a claim.
        heartbeat()
        params = {
            "fields": "id,caption,media_type,timestamp,permalink",
            "limit": RECOVERY_PAGE_SIZE,
        }
        if after:
            params["after"] = after
        with _client() as c:
            r = c.get(f"/{ig_user_id}/media", params=params, headers=_auth(token))
        if r.status_code >= 400:
            _raise_api_error(r, "recent media lookup")
        body = r.json()
        page = body.get("data") or []
        items.extend(page)
        oldest = min((t for t in (_parse_ig_time(m.get("timestamp")) for m in page) if t),
                     default=None)
        after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
        has_next = bool((body.get("paging") or {}).get("next"))
        if not page or not after or not has_next or (oldest is not None and oldest < since):
            break

    ids = [str(m.get("id")) for m in items if m.get("id")]
    recorded: set[str] = set()
    if ids:
        recorded |= set(db.execute(
            select(PostPlatform.remote_id).where(PostPlatform.remote_id.in_(ids))
        ).scalars())
        recorded |= set(db.execute(
            select(Reel.remote_id).where(Reel.remote_id.in_(ids))
        ).scalars())
    matches = []
    for m in items:
        ts = _parse_ig_time(m.get("timestamp"))
        if ts is None or ts < since or ts > until:
            continue
        if str(m.get("id")) in recorded:
            continue
        if media_types and m.get("media_type") not in media_types:
            continue
        if " ".join((m.get("caption") or "").split()) != want:
            continue
        matches.append((ts, str(m.get("id")), m.get("permalink")))
    matches.sort()
    return [(mid, link) for _ts, mid, link in matches]


def _count_children(media_id: str, token: str) -> int | None:
    """How many frames the published carousel actually has, or None if unreadable.

    Best-effort by design: this runs after a successful publish, and a failure to count
    is not a reason to report the publish as failed.
    """
    try:
        with _client() as c:
            r = c.get(f"/{media_id}", params={"fields": "children{id}"}, headers=_auth(token))
        if r.status_code >= 400:
            return None
        return len(((r.json().get("children") or {}).get("data")) or [])
    except Exception:  # noqa: BLE001 — a verification step must not fail the publish
        return None


def _await_container(
    container_id: str, token: str, *, describing: str,
    tries: int = STATUS_POLL_TRIES, interval: float = STATUS_POLL_INTERVAL,
    heartbeat: Heartbeat = _no_heartbeat,
) -> None:
    """Block until Meta says the container is ready. Image containers usually come back
    FINISHED on the first check; reels pass a much longer budget (see post_reel)."""
    status_code = None
    for _attempt in range(tries):
        heartbeat()
        with _client() as c:
            r = c.get(f"/{container_id}", params={"fields": "status_code"},
                      headers=_auth(token))
        if r.status_code >= 400:
            _raise_api_error(r, "container status check")
        status_code = r.json().get("status_code")
        if status_code == "FINISHED":
            return
        if status_code in ("ERROR", "EXPIRED"):
            raise InstagramError(
                f"media container ended in {status_code} — Meta couldn't ingest the media "
                f"({describing})",
                permanent=True,
            )
        time.sleep(interval)
    raise InstagramError(
        f"media container still {status_code!r} after "
        f"{tries * interval:.0f}s ({describing}) — will retry"
    )


def _publish_container(ig_user_id: str, container_id: str, token: str) -> tuple[str, str | None]:
    """Publish a finished container. Returns (media_id, permalink).

    Retries only the "media is not ready" race (see PUBLISH_RETRY_TRIES). Every other
    4xx is raised on the first answer, because re-posting the same creation_id against
    a genuine rejection just burns attempts.
    """
    for attempt in range(1, PUBLISH_RETRY_TRIES + 1):
        with _client() as c:
            r = c.post(f"/{ig_user_id}/media_publish", data={
                "creation_id": container_id,
                "access_token": token,
            })
        if r.status_code < 400:
            break
        if attempt == PUBLISH_RETRY_TRIES or not _NOT_READY_RE.search(_error_text(r)):
            _raise_api_error(r, "media publish")
        log.info(
            "instagram: container %s not ready to publish, waiting %.0fs (attempt %d/%d)",
            container_id, PUBLISH_RETRY_INTERVAL, attempt, PUBLISH_RETRY_TRIES,
        )
        time.sleep(PUBLISH_RETRY_INTERVAL)
    media_id = r.json().get("id")
    if not media_id:
        raise InstagramError(f"media_publish returned no id: {r.text[:200]}")

    # Permalink is cosmetic — the post is live even if this lookup fails.
    permalink = None
    try:
        with _client() as c:
            r = c.get(f"/{media_id}", params={"fields": "permalink"}, headers=_auth(token))
        if r.status_code < 400:
            permalink = r.json().get("permalink")
    except Exception:
        log.warning("instagram permalink lookup failed for media %s", media_id)
    return str(media_id), permalink


def publishing_quota(db: Session) -> dict:
    """Current rolling-24h API publish usage — surfaced by the Settings test button."""
    row = _load_credential(db)
    token = decrypt_token(row.access_token)
    ig_user_id = json.loads(row.extra_json or "{}").get("ig_user_id")
    with _client() as c:
        r = c.get(f"/{ig_user_id}/content_publishing_limit",
                  params={"fields": "quota_usage,config"}, headers=_auth(token))
    if r.status_code >= 400:
        _raise_api_error(r, "quota lookup")
    entries = r.json().get("data") or [{}]
    entry = entries[0]
    return {
        "quota_usage": entry.get("quota_usage"),
        "quota_total": (entry.get("config") or {}).get("quota_total"),
    }
