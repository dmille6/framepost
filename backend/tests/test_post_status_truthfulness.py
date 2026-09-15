"""post.status must not claim a post never went out when it did.

post.status is set by Flickr's outcome alone -- it is really the Flickr delivery's
state wearing the post's name. A photo Flickr rejected as a duplicate was marked
`failed` even after Instagram published it. The post was live, the queue said it had
failed, and the only way to tell was to open the platform rows.

Two things kept it stuck there. Nothing re-examined the post after the fanout, which
is the first moment the answer is knowable -- when _record_failure decides Flickr is
exhausted, nothing else has been attempted. And retry_due_platform_posts skipped posts
marked failed, so a platform that could have rescued the post was filtered out by the
very status it would have corrected.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from models import PlatformCredential, Post, PostPlatform
from services import scheduler
from services.platforms import flickr as flickr_mod


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _due_post(db, **kw) -> Post:
    p = Post(id=uuid.uuid4().hex, status="pending", title="t", original_filename="f.arw",
             scheduled_at=_now() - timedelta(minutes=5), retry_count=0, **kw)
    db.add(p)
    db.flush()
    return p


def _cred(db, platform="instagram") -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform,
                           access_token="tok", account_name="a", default_target=1)
    db.add(c)
    db.flush()
    return c


@pytest.fixture()
def worker(db, monkeypatch):
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    return db


def _flickr_permanently_fails(monkeypatch):
    def boom(db, post, fired_at):
        raise flickr_mod.FlickrError("already on Flickr as photo 123", permanent=True)
    monkeypatch.setattr(scheduler, "_flickr_post", boom)


# --------------------------------------------------------------------------

def test_a_post_that_published_elsewhere_is_not_marked_failed(db, worker, monkeypatch):
    """The reported defect, end to end."""
    post = _due_post(db)
    cred = _cred(db)
    db.commit()
    _flickr_permanently_fails(monkeypatch)

    def fake_fanout(d, p, *, fired_at, targets=None):
        d.add(PostPlatform(post_id=p.id, platform_id=cred.id,
                           status="posted", remote_id="ig-1", posted_at=fired_at))
    monkeypatch.setattr(scheduler, "fanout_to_platforms", fake_fanout)

    scheduler.fire_due_posts()

    fresh = db.get(Post, post.id)
    assert fresh.status in ("posted", "late"), \
        "Instagram published it; the post must not report that it failed"


def test_flickrs_failure_is_still_visible_after_reconciling(db, worker, monkeypatch):
    """Promoting the post must not hide why Flickr did not run."""
    post = _due_post(db)
    cred = _cred(db)
    db.commit()
    _flickr_permanently_fails(monkeypatch)
    monkeypatch.setattr(scheduler, "fanout_to_platforms",
                        lambda d, p, *, fired_at, targets=None: d.add(PostPlatform(
                            post_id=p.id, platform_id=cred.id, status="posted",
                            remote_id="ig-1", posted_at=fired_at)))

    scheduler.fire_due_posts()

    fresh = db.get(Post, post.id)
    assert "already on Flickr" in (fresh.error_message or "")
    assert fresh.flickr_photo_id is None


def test_a_post_that_reached_nowhere_stays_failed(db, worker, monkeypatch):
    """The promotion is conditional, not automatic."""
    post = _due_post(db)
    _cred(db)
    db.commit()
    _flickr_permanently_fails(monkeypatch)
    monkeypatch.setattr(scheduler, "fanout_to_platforms",
                        lambda d, p, *, fired_at, targets=None: None)

    scheduler.fire_due_posts()

    assert db.get(Post, post.id).status == "failed"


def test_a_transient_flickr_failure_still_leaves_the_post_pending(db, worker, monkeypatch):
    """Flickr has retries left, so the post must stay in the queue for them."""
    post = _due_post(db)
    cred = _cred(db)
    db.commit()
    monkeypatch.setattr(scheduler, "_flickr_post",
                        lambda d, p, fired_at: (_ for _ in ()).throw(RuntimeError("500")))
    monkeypatch.setattr(scheduler, "fanout_to_platforms",
                        lambda d, p, *, fired_at, targets=None: d.add(PostPlatform(
                            post_id=p.id, platform_id=cred.id, status="posted",
                            remote_id="ig-1", posted_at=fired_at)))

    scheduler.fire_due_posts()

    assert db.get(Post, post.id).status == "pending"


# --------------------------------------------------------------------------
# the retry path could not rescue a failed post
# --------------------------------------------------------------------------

def test_a_platform_retry_runs_for_a_failed_post(db, worker, monkeypatch):
    """Previously filtered out: the post could never leave 'failed' because the retry
    that would have fixed it was excluded for being 'failed'."""
    post = _due_post(db)
    post.status = "failed"
    cred = _cred(db)
    db.add(PostPlatform(post_id=post.id, platform_id=cred.id, status="pending",
                        next_retry_at=_now() - timedelta(minutes=1)))
    db.commit()

    def succeed(d, c, p, fired_at):
        pp = d.get(PostPlatform, (p.id, c.id))
        pp.status, pp.remote_id, pp.next_retry_at = "posted", "ig-9", None
    monkeypatch.setattr(scheduler, "_post_to_platform", succeed)

    scheduler.retry_due_platform_posts()

    fresh = db.get(Post, post.id)
    assert db.get(PostPlatform, (post.id, cred.id)).status == "posted"
    assert fresh.status in ("posted", "late"), "a late rescue should correct the post too"


def test_a_pending_post_is_not_retried_by_the_platform_job(db, worker, monkeypatch):
    """Widening the filter must not reach posts that never fired at all."""
    post = _due_post(db)   # status pending
    cred = _cred(db)
    db.add(PostPlatform(post_id=post.id, platform_id=cred.id, status="pending",
                        next_retry_at=_now() - timedelta(minutes=1)))
    db.commit()
    calls = []
    monkeypatch.setattr(scheduler, "_post_to_platform",
                        lambda *a, **k: calls.append(1))

    scheduler.retry_due_platform_posts()

    assert calls == []
