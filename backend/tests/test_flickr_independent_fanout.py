"""A Flickr failure must not take the other destinations with it.

fire_due_posts published to Flickr first and `continue`d on failure, skipping the
fanout entirely. Instagram ingests from R2 and Bluesky and Pixelfed upload the local
file, so none of them needed Flickr -- they were simply downstream of it in the loop.

For a transient failure that was a delay: the post stayed pending and the next attempt
carried everything. For a PERMANENT one it was a loss. _record_failure marks the post
failed, fire_due_posts only selects pending posts, and nothing revisits it -- so a
photo rejected as a Flickr duplicate was dead for Instagram too, for a reason that had
nothing to do with Instagram.

Flickr is now one destination among several rather than the gate in front of them.
"""
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from models import PlatformCredential, Post
from services import scheduler
from services.platforms import flickr as flickr_mod


def _due_post(db, **kw) -> Post:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    p = Post(id=uuid.uuid4().hex, status="pending", title="t",
             original_filename="f.arw",
             scheduled_at=now - timedelta(minutes=5), retry_count=0, **kw)
    db.add(p)
    db.flush()
    return p


def _cred(db, platform: str) -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform,
                           access_token="tok", account_name="a", default_target=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture()
def fired(db, monkeypatch):
    """Run fire_due_posts against the test session, recording fanout calls."""
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    calls: list[str] = []
    monkeypatch.setattr(scheduler, "fanout_to_platforms",
                        lambda d, post, **kw: calls.append(post.id))
    return calls


def _flickr_raises(monkeypatch, exc):
    def boom(db, post, fired_at):
        raise exc
    monkeypatch.setattr(scheduler, "_flickr_post", boom)


# --------------------------------------------------------------------------

def test_a_transient_flickr_failure_does_not_skip_the_fanout(db, fired, monkeypatch):
    post = _due_post(db)
    _cred(db, "instagram")
    db.commit()
    _flickr_raises(monkeypatch, RuntimeError("flickr 500"))

    scheduler.fire_due_posts()

    assert fired == [post.id], "the other destinations were skipped because Flickr failed"


def test_a_permanent_flickr_failure_does_not_skip_the_fanout(db, fired, monkeypatch):
    """The loss case: the post is marked failed and never revisited, so this is the
    only chance the other destinations get."""
    post = _due_post(db)
    _cred(db, "instagram")
    db.commit()
    _flickr_raises(monkeypatch, flickr_mod.FlickrError(
        "already on Flickr as photo 123", permanent=True))

    scheduler.fire_due_posts()

    assert fired == [post.id]
    assert db.get(Post, post.id).status == "failed", "permanent failure should be recorded"


def test_the_flickr_failure_is_still_recorded(db, fired, monkeypatch):
    """Decoupling the fanout must not make Flickr failures invisible."""
    post = _due_post(db)
    _cred(db, "instagram")
    db.commit()
    _flickr_raises(monkeypatch, RuntimeError("flickr 500"))

    scheduler.fire_due_posts()

    fresh = db.get(Post, post.id)
    assert fresh.retry_count == 1
    assert fresh.next_retry_at is not None, "Flickr must still be queued for a retry"
    assert fresh.status == "pending"


def test_a_successful_flickr_post_still_fans_out(db, fired, monkeypatch):
    post = _due_post(db)
    _cred(db, "instagram")
    db.commit()
    monkeypatch.setattr(scheduler, "_flickr_post", lambda d, p, fired_at: None)

    scheduler.fire_due_posts()

    assert fired == [post.id]


def test_a_post_that_skips_flickr_still_fans_out(db, fired, monkeypatch):
    """Pre-existing behaviour for target_platforms without flickr, unchanged."""
    post = _due_post(db, target_platforms='["instagram"]')
    _cred(db, "instagram")
    db.commit()

    scheduler.fire_due_posts()

    assert fired == [post.id]
    assert db.get(Post, post.id).status in ("posted", "late")


def test_a_missed_post_is_not_fanned_out(db, fired, monkeypatch):
    """Older than the missed threshold with nothing on Flickr: it is abandoned, and
    that path is untouched by this change."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    post = _due_post(db)
    post.scheduled_at = now - scheduler.MISSED_THRESHOLD - timedelta(hours=1)
    _cred(db, "instagram")
    db.commit()

    scheduler.fire_due_posts()

    assert fired == []
    assert db.get(Post, post.id).status == "missed"
