"""Owned leases for feed delivery, shared by the fire and retry jobs.

A scan is only a hint: another job can publish between SELECT and dispatch. The
conditional UPDATE is the decision. Every commit checks ownership in the same write
transaction; every outbound HTTP request renews first. The longest ordinary silence
is Flickr's 300s upload timeout, below the ten-minute lease. No SQLite write lock is
held across a network request. Instagram's existing checkpoint remains the authority
for recovering an ambiguous remote publish; the lease only excludes live competitors.
"""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import event, or_, update
from sqlalchemy.dialects.sqlite import insert

from models import Post, PostPlatform
from services.platforms.instagram import StopAttempt

STALE_AFTER = timedelta(minutes=10)
_active: ContextVar[tuple] = ContextVar("feed_claims", default=())


class ClaimLost(StopAttempt):
    """A replaced owner must neither send another request nor commit stale results."""


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def available(model, now):
    return or_(model.publish_claimed_at.is_(None),
               model.publish_claimed_at < now - STALE_AFTER)


class Claim:
    def __init__(self, db, model, key, token):
        self.db, self.model, self.key, self.token = db, model, key, token

    def _guard(self, session):
        # before_commit runs before autoflush, so stale ORM changes cannot slip past
        # the ownership check. SQLite serialises this UPDATE with a competing claim.
        with session.no_autoflush:
            won = session.execute(update(self.model).where(
                *self.key, self.model.publish_claim_token == self.token,
            ).values(publish_claimed_at=now_utc())
              .execution_options(synchronize_session=False)).rowcount
        if not won:
            raise ClaimLost("Feed publish claim was taken over — stopping")

    def __enter__(self):
        event.listen(self.db, "before_commit", self._guard)
        self.context_token = _active.set((*_active.get(), self))
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                # A caller may leave pending bookkeeping for context exit. Check it
                # before removing the guard, or release would autoflush stale writes.
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            _active.reset(self.context_token)
            event.remove(self.db, "before_commit", self._guard)
            self.db.rollback()
            self.db.execute(update(self.model).where(
                *self.key, self.model.publish_claim_token == self.token,
            ).values(publish_claimed_at=None, publish_claim_token=None)
              .execution_options(synchronize_session=False))
            self.db.commit()


def before_request(request):
    """HTTP hook, scoped to this thread/context's attempt, including poll/retry loops.
    All feed adapters use http_client; unrelated API requests pay no DB work.
    """
    active = _active.get()
    if active:
        # Nested post + platform claims share a session; both guards run on this commit.
        active[-1].db.commit()


def _take(db, model, key, conditions, now):
    token = uuid.uuid4().hex
    won = db.execute(update(model).where(*key, *conditions, available(model, now))
        .values(publish_claimed_at=now, publish_claim_token=token)
        .execution_options(synchronize_session=False)).rowcount
    db.commit()
    return Claim(db, model, key, token) if won else None


def post_claim(db, post_id, *, now=None):
    now = now or now_utc()
    return _take(db, Post, [Post.id == post_id], [
        Post.status == "pending", Post.scheduled_at.is_not(None), Post.scheduled_at <= now,
        or_(Post.next_retry_at.is_(None), Post.next_retry_at <= now),
    ], now)


def platform_claim(db, post_id, platform_id, *, now=None, recoverable=True):
    now = now or now_utc()
    # First attempts do not yet have a row. INSERT ... ON CONFLICT avoids racing two
    # ORM inserts; the following conditional UPDATE still has exactly one winner.
    db.execute(insert(PostPlatform).values(post_id=post_id, platform_id=platform_id)
               .on_conflict_do_nothing(index_elements=["post_id", "platform_id"]))
    conditions = [PostPlatform.status == "pending",
                  or_(PostPlatform.next_retry_at.is_(None), PostPlatform.next_retry_at <= now)]
    if not recoverable:
        # Enforce the ambiguous-crash policy at the atomic decision too: a stale
        # scan may not have seen the previous attempt's token or cleared retry timer.
        conditions.append(or_(PostPlatform.publish_claim_token.is_(None),
                              PostPlatform.next_retry_at.is_not(None)))
    return _take(db, PostPlatform,
                 [PostPlatform.post_id == post_id, PostPlatform.platform_id == platform_id],
                 conditions, now)
