"""Put a finished reel on Instagram without anyone touching a phone.

The reel builder has produced a correct MP4 since May and stopped there: the
photographer downloaded it, moved it to a phone, and uploaded it by hand. One reel
has ever been made. A workflow with a manual step in the middle is a workflow that
does not get run, which is the whole argument for this module.

The shape mirrors the photo path deliberately. Meta does not accept an upload; it
fetches a URL, so the MP4 goes to R2 exactly as staged JPEGs do, and the staged
object is deleted once Meta has ingested it. What differs is time: Meta transcodes
a reel server-side and that can take minutes, so `instagram.post_reel` polls far
longer than the image path and a timeout here is a retry rather than a failure.

Nothing in here raises past the caller. A reel that fails records why and waits for
the next pass; the worker must not die because Meta was slow.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from models import Performer, PostPerformer, Reel, ReelPhoto
from services import events, media_probe, r2, reel_frames
from services.platforms import instagram as ig

log = logging.getLogger("framepost.reel_publish")

# Meta fetches once, during container creation, but transcoding runs afterwards and the
# URL must survive it. r2.DEFAULT_EXPIRY (2h) is ample; named here so the reason is
# attached to the decision rather than inherited silently.
STAGE_EXPIRY = r2.DEFAULT_EXPIRY

# A reel that has failed this many times is not going to succeed by being tried again,
# and a permanently broken one retried forever is how a worker queue fills with noise.
MAX_ATTEMPTS = 4

# A claim not renewed for this long belongs to a worker that died mid-attempt (a crash
# or deploy runs no cleanup). A live attempt renews it every CLAIM_RENEW_EVERY while it
# waits on Meta -- each container-status poll, each container-creation try -- so the
# longest silence is one HTTP call (60s timeout) plus a sleep, whatever Meta's pace.
# Measured against renewal, not against an attempt's total length, which HTTP time can
# stretch far past the poll budget.
CLAIM_STALE_AFTER = timedelta(minutes=10)
CLAIM_RENEW_EVERY = 60.0  # seconds


class ReelPublishError(Exception):
    """Something stopped this reel going out. Carries whether retrying is worthwhile."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class ReelBusy(ReelPublishError):
    """Another attempt holds the claim. Not a failure of this reel: no attempt is spent."""


class ClaimLost(ReelBusy, ig.StopAttempt):
    """This attempt's claim was taken over (it went quiet long enough to look dead).
    Raised before any further write or media_publish; the checkpoint, if one was saved,
    makes the next attempt safe."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def claim_cutoff(now: datetime | None = None) -> datetime:
    """Claims taken before this are stale (see CLAIM_STALE_AFTER)."""
    return (now or _utcnow()) - CLAIM_STALE_AFTER


class Claim:
    """This attempt's hold on a reel: its token, and the guarded way to commit.

    Every write the attempt makes goes through commit(): one UPDATE that renews the
    claim WHERE token = mine, in the same transaction as the attempt's own changes. If
    the claim has been taken over the UPDATE matches nothing, the transaction is rolled
    back and ClaimLost is raised -- so a worker that lost its claim can neither record
    anything nor go on to publish. SQLite serialises writers, so nothing can take the
    claim between that check and the commit.
    """

    def __init__(self, db: Session, reel_id: str, token: str):
        self.db, self.reel_id, self.token = db, reel_id, token
        self._renewed = time.monotonic()

    def __bool__(self) -> bool:
        return True

    def commit(self) -> None:
        mine = self.db.execute(
            update(Reel).where(Reel.id == self.reel_id, Reel.publish_claim_token == self.token)
            .values(publish_claimed_at=_utcnow())
            .execution_options(synchronize_session=False)
        ).rowcount
        if not mine:
            self.db.rollback()
            raise ClaimLost(f"reel {self.reel_id[:8]}: publish claim was taken over — stopping")
        self.db.commit()
        self._renewed = time.monotonic()

    def heartbeat(self, force: bool = False) -> None:
        """Renew during long waits (post_reel calls it from its poll loops); force=True
        right before media_publish, which must never go out under a lost claim."""
        if force or time.monotonic() - self._renewed >= CLAIM_RENEW_EVERY:
            self.commit()

    def release(self) -> None:
        """End the claim, if it is still this attempt's. The attempt is over; if a
        container is still checkpointed (outcome unknown), ig_container keeps the trial
        kind frozen on its own."""
        self.db.execute(
            update(Reel).where(Reel.id == self.reel_id, Reel.publish_claim_token == self.token)
            .values(publish_claimed_at=None, publish_claim_token=None)
            .execution_options(synchronize_session=False)
        )
        self.db.commit()


def claim(db: Session, reel_id: str, *, now: datetime | None = None) -> Claim | None:
    """Take the reel for one publish attempt. The Claim if this caller won, else None.

    A conditional UPDATE, committed at once: SQLite serialises writers, so of a worker
    and a PATCH changing the trial kind (routes/reels.update_reel, its own conditional
    UPDATE) exactly one wins, with no window between checking and writing. A stale
    claim — not renewed for CLAIM_STALE_AFTER — is taken over.
    """
    now = now or _utcnow()
    token = uuid.uuid4().hex
    won = db.execute(
        update(Reel)
        .where(Reel.id == reel_id, Reel.posted_at.is_(None),
               or_(Reel.publish_claimed_at.is_(None),
                   Reel.publish_claimed_at < claim_cutoff(now)))
        .values(publish_claimed_at=now, publish_claim_token=token)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return Claim(db, reel_id, token) if won else None


def due_reels(db: Session, *, now: datetime | None = None) -> list[Reel]:
    """Reels whose time has come: rendered, scheduled, not yet posted, not exhausted.

    `status == "ready"` matters -- a reel still rendering has no file to stage, and one
    that failed to render has nothing worth sending.
    """
    now = now or _utcnow()
    return list(db.execute(
        select(Reel).where(
            Reel.status == "ready",
            Reel.scheduled_at.is_not(None),
            Reel.scheduled_at <= now,
            Reel.posted_at.is_(None),
            Reel.publish_attempts < MAX_ATTEMPTS,
        ).order_by(Reel.scheduled_at)
    ).scalars().all())


def collaborators_for(db: Session, reel: Reel) -> list[str]:
    """Instagram handles of every performer appearing anywhere in the reel.

    Not just the cover. A reel is a set of photographs of several performers, and the
    ones whose frames are in it have as much claim to a credit as whoever happens to be
    on the thumbnail -- and each accepted invite puts the reel in front of that
    performer's followers, which is the reach this whole exercise is chasing.

    Order follows the reel, so the cover's performers come first and Instagram's cap of
    three lands on the people most prominent in it.
    """
    positions = db.execute(
        select(ReelPhoto.post_id)
        .where(ReelPhoto.reel_id == reel.id)
        .order_by(ReelPhoto.position)
    ).scalars().all()
    ordered = [reel.cover_post_id] + [p for p in positions if p != reel.cover_post_id]

    seen: set[str] = set()
    handles: list[str] = []
    for post_id in ordered:
        rows = db.execute(
            select(Performer.instagram_handle)
            .join(PostPerformer, PostPerformer.performer_id == Performer.id)
            .where(PostPerformer.post_id == post_id,
                   Performer.instagram_handle.is_not(None))
        ).scalars().all()
        for h in rows:
            key = (h or "").lstrip("@").strip().lower()
            if key and key not in seen:
                seen.add(key)
                handles.append(key)
    return handles


def stage(reel: Reel) -> tuple[str, str]:
    """Upload the MP4 to R2. Returns (key, publicly fetchable URL).

    R2 is not optional for this the way it is for a photo: a photo can fall back to its
    Flickr rendition, and a reel has no equivalent -- the MP4 exists nowhere but this
    disk until it is staged.
    """
    if not r2.configured():
        raise ReelPublishError(
            "Instagram fetches the video from a public URL, and R2 staging isn't "
            "configured — there is nowhere to serve the MP4 from.",
            permanent=True,
        )
    if not reel.mp4_path:
        raise ReelPublishError("This reel has no rendered MP4.", permanent=True)
    path = Path(reel.mp4_path)
    if not path.exists():
        raise ReelPublishError(
            f"The rendered MP4 is gone from disk ({reel.mp4_path}) — regenerate the reel.",
            permanent=True,
        )

    key = f"reels/{reel.id}.mp4"
    r2.put(key, path.read_bytes(), content_type="video/mp4")
    return key, r2.presign_get(key, expires=STAGE_EXPIRY)


def unstage(reel: Reel) -> None:
    """Drop the staged MP4. Best-effort: a leaked object costs pennies, and failing the
    publish over cleanup would trade a real success for a bookkeeping problem."""
    if not reel.staged_key:
        return
    try:
        r2.delete(reel.staged_key)
    except Exception:  # noqa: BLE001 — cleanup must never mask a completed publish
        log.warning("reel %s: couldn't remove staged object %s", reel.id[:8], reel.staged_key)
    reel.staged_key = None


def _media_identity(reel: Reel) -> str:
    """The rendered file, by path, size and mtime: a re-render after a failed attempt
    must not be published from the container built from the old MP4."""
    try:
        st = Path(reel.mp4_path).stat() if reel.mp4_path else None
    except OSError:
        st = None
    return f"{reel.id}|{reel.mp4_path}|{st.st_size if st else ''}|{st.st_mtime_ns if st else ''}"


def publish(db: Session, reel: Reel) -> dict:
    """Claim, stage, publish, record, release. Raises ReelPublishError (ReelBusy when
    another attempt holds the reel); the caller decides about retries.

    The container is checkpointed on the reel (reel.ig_container) and committed before
    media_publish goes out, so an attempt that died mid-publish — or while Meta spent
    minutes transcoding — is resumed from that container next time instead of uploading
    and publishing the reel a second time. Staging is deferred for the same reason: a
    resumed container was already ingested by Meta, and needs no MP4 in R2.

    The claim covers the whole attempt, including the window before the first
    checkpoint is saved, in which nothing else would stop the trial kind changing.
    """
    held = claim(db, reel.id)
    if not held:
        raise ReelBusy(f"reel {reel.id[:8]} is already being published")
    # Re-read after winning: a PATCH that committed before the claim must be what is
    # published, not the row as it was when this pass listed its due reels.
    db.refresh(reel)
    try:
        result = _publish_claimed(db, reel, held)
    except ReelPublishError:
        held.release()
        db.refresh(reel)
        raise
    except Exception:
        db.rollback()
        held.release()
        raise
    held.release()
    return result


def _publish_claimed(db: Session, reel: Reel, held: Claim) -> dict:
    reel.publish_attempts = (reel.publish_attempts or 0) + 1
    # Read once. Whatever the row says by the time Meta answers, this is what was asked
    # for — and the PATCH that could change it is refused while a container exists.
    trial = reel.trial_graduation
    checkpoint = ig.ContainerCheckpoint.from_json(reel.ig_container)
    staged_url: str | None = None
    if checkpoint is None:
        # Nothing to resume, so this attempt will certainly need the MP4 — stage now,
        # and let a missing file or unconfigured R2 fail before Meta is involved.
        try:
            key, staged_url = stage(reel)
        except ReelPublishError as e:
            reel.publish_error = str(e)
            if e.permanent:
                reel.publish_attempts = MAX_ATTEMPTS
            held.commit()
            raise
        reel.staged_key = key
    held.commit()

    def restage() -> str:
        key, url = stage(reel)
        reel.staged_key = key
        held.commit()
        return url

    def video_url() -> str:
        # Probed right before Meta is asked to fetch it (see services/media_probe). A
        # reel has no Flickr copy to fall back to, so the one alternative is a fresh
        # upload with a fresh presign.
        url = staged_url if staged_url is not None else restage()
        try:
            return media_probe.first_fetchable(
                url, [("re-staged copy", restage)], kinds=media_probe.VIDEO)
        except media_probe.MediaUnreachable as e:
            raise ig.InstagramError(
                f"video URL unreachable — nothing was sent to Instagram: {e}",
                permanent=False,
            ) from e

    def save(cp) -> None:
        reel.ig_container = cp.to_json() if cp else None
        held.commit()

    try:
        result = ig.post_reel(
            db,
            video_url=video_url,
            caption=reel.caption or "",
            collaborators=collaborators_for(db, reel),
            # post_reel owns what a trial changes on the wire (no collaborators, no
            # share_to_feed) so no caller can get those rules half right.
            trial_graduation=trial,
            checkpoint=checkpoint,
            on_checkpoint=save,
            media_identity=_media_identity(reel),
            heartbeat=held.heartbeat,
        )
    except ig.InstagramError as e:
        reel.publish_error = str(e)
        if e.permanent and not isinstance(e, ig.PublishUnconfirmed):
            # Meta said a definite no, and no publish is outstanding: the container can't
            # go out as it is. Forget it, so the photographer can change the reel (the
            # trial setting is frozen while a checkpoint exists) and the next attempt
            # builds a fresh one. A container with a publish in flight is never dropped.
            cp = ig.ContainerCheckpoint.from_json(reel.ig_container)
            if cp is not None and cp.publish_sent_at is None:
                reel.ig_container = None
        if isinstance(e, ig.TrialReelRejected):
            # On the cover's timeline: reels have no event log of their own, and this
            # one needs a person — Trial off, or wait and reschedule.
            events.log_event(db, post_id=reel.cover_post_id, event_type="reel_trial_rejected",
                             actor="worker", details={
                                 "reel_id": reel.id,
                                 "graduation": trial,
                                 "error": str(e),
                             })
        if e.permanent:
            # Stop it being picked up again; recorded under the claim like the rest.
            reel.publish_attempts = MAX_ATTEMPTS
        held.commit()
        raise ReelPublishError(str(e), permanent=getattr(e, "permanent", False)) from e

    # What actually went out: the checkpoint's kind when Meta's container carried one
    # (a recovered publish reports it), else what this attempt asked for.
    reel.trial_graduation = result["trial_graduation"] if "trial_graduation" in result else trial
    # The frames follow what actually went out, which after a recovery can differ from
    # what was asked for. Posted frames are never touched (see reel_frames).
    reel_frames.sync_frame_instagram(db, reel)
    reel.remote_id = result.get("remote_id")
    reel.remote_url = result.get("url")
    reel.posted_at = _utcnow()
    reel.publish_error = None
    # Cleared with the record of success, never before it (see instagram._publish_resumable).
    reel.ig_container = None
    if result.get("recovered"):
        log.warning("reel %s: an earlier attempt had already published it (%s) — "
                    "not published again", reel.id[:8], reel.remote_id)
    unstage(reel)
    # If the claim was lost, a later attempt resumes the checkpoint, finds the container
    # PUBLISHED, and records it — so losing this record costs nothing but a moment.
    held.commit()
    log.info("reel %s published as %s", reel.id[:8], reel.remote_id)
    return result


def run_due(db: Session, *, now: datetime | None = None) -> int:
    """Publish every due reel. Returns how many went out.

    One reel's failure never stops the next: they are independent pieces of work, and a
    permanent failure on one is not evidence about another.
    """
    published = 0
    for reel in due_reels(db, now=now):
        try:
            publish(db, reel)
            published += 1
        except ReelBusy as e:
            log.info("%s — skipping", e)
        except ReelPublishError as e:
            # Recorded (message, and attempts spent if permanent) under the claim; this
            # loop writes nothing, because it no longer holds one.
            log.warning("reel %s not published: %s", reel.id[:8], e)
        except Exception:  # noqa: BLE001 — a worker pass must survive one bad reel
            log.exception("reel %s: unexpected failure", reel.id[:8])
            db.rollback()
    return published
