"""A publish that happened must survive the bookkeeping that follows it.

By the time a platform's publish call returns, the post is public. Everything after
it -- collaborator outcomes, deleting the staged image, timeline events -- is
bookkeeping. The old code did all of that first and committed once at the end, so a
failure in between rolled the whole transaction back: the post was live on the
platform and absent from post_platforms.

That is not a cosmetic loss. The retry decides whether to publish by reading
post_platforms, so an empty row means it publishes a second copy. The 'already
posted' guard can only protect a success that was recorded.

The window cannot be closed completely -- no API here offers exactly-once, and a
crash between the platform's reply and the commit still loses the record. These
tests pin that it is one insert wide rather than several operations including a
network round-trip.
"""
import types
import uuid
from datetime import datetime, timezone

import pytest

from models import PlatformCredential, Post, PostPlatform
from services import scheduler

NOW = datetime(2030, 5, 1, 12, 0, 0)


def _cred(db, platform="bluesky") -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform,
                           access_token="tok", account_name="acct")
    db.add(c)
    db.flush()
    return c


def _post(db) -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted", title="t",
             original_filename="f.arw", original_path="/nope/f.arw")
    db.add(p)
    db.flush()
    return p


@pytest.fixture()
def bluesky_ok(monkeypatch):
    """A successful publish, with everything the caption builder needs stubbed out."""
    monkeypatch.setattr(scheduler, "_source_for", lambda post: "/tmp/x.jpg")
    monkeypatch.setattr(scheduler, "_build_caption_for", lambda *a, **k: "caption")
    monkeypatch.setattr(scheduler.carousel_svc, "is_lead", lambda p: False)
    monkeypatch.setattr(
        scheduler.bluesky, "post_photo",
        lambda **kw: {"at_uri": "at://did:plc:x/app.bsky.feed.post/abc",
                      "url": "https://bsky.app/p/abc"},
    )


def test_the_publish_is_recorded_before_the_bookkeeping(db, bluesky_ok, monkeypatch):
    """The decisive case: the platform succeeded, then something after it blew up."""
    post, cred = _post(db), _cred(db)
    db.commit()

    real = scheduler.events.log_event

    def explode(dbs, **kw):
        if kw.get("event_type", "").endswith("_uploaded"):
            raise RuntimeError("bookkeeping exploded after the post went live")
        return real(dbs, **kw)

    monkeypatch.setattr(scheduler.events, "log_event", explode)

    with pytest.raises(RuntimeError):
        scheduler._post_to_platform(db, cred, post, NOW)
    db.rollback()   # what the caller does on any exception

    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp is not None, "the publish was lost -- a retry would post a second copy"
    assert pp.status == "posted"
    assert pp.remote_id == "at://did:plc:x/app.bsky.feed.post/abc"


def test_a_recorded_publish_is_not_downgraded_to_failed(db):
    """_record_platform_failure sets status unconditionally. Without a guard it would
    undo the early commit and hand the retry a green light."""
    post, cred = _post(db), _cred(db)
    db.add(PostPlatform(post_id=post.id, platform_id=cred.id, status="posted",
                        remote_id="at://live", posted_at=NOW))
    db.commit()

    scheduler._record_platform_failure(db, post, cred, RuntimeError("cleanup failed"))

    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp.status == "posted"
    assert pp.remote_id == "at://live"
    assert pp.next_retry_at is None, "a live post must not be queued for a retry"


def test_a_genuine_failure_is_still_recorded(db):
    """The guard is narrow: only a row that already carries a remote id is protected."""
    post, cred = _post(db), _cred(db)
    db.commit()
    scheduler._record_platform_failure(db, post, cred, RuntimeError("upload refused"))
    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp.status in ("pending", "failed")
    assert pp.remote_id is None


def test_a_posted_row_without_a_remote_id_is_not_protected(db):
    """'posted' with nothing to show for it is not evidence of a publish."""
    post, cred = _post(db), _cred(db)
    db.add(PostPlatform(post_id=post.id, platform_id=cred.id, status="posted"))
    db.commit()
    scheduler._record_platform_failure(db, post, cred, RuntimeError("nope"))
    assert db.get(PostPlatform, (post.id, cred.id)).status in ("pending", "failed")


def test_the_retry_guard_sees_the_recorded_publish(db, bluesky_ok):
    """The point of recording it: fanout skips a platform already marked posted."""
    post, cred = _post(db), _cred(db)
    db.commit()
    scheduler._post_to_platform(db, cred, post, NOW)
    db.commit()

    calls = []
    original = scheduler.bluesky.post_photo
    scheduler.bluesky.post_photo = lambda **kw: (calls.append(1), original(**kw))[1]
    try:
        scheduler.fanout_to_platforms(db, post, fired_at=NOW, targets=["bluesky"])
    finally:
        scheduler.bluesky.post_photo = original
    assert calls == [], "already-posted platform was published to again"


def test_record_published_commits_immediately(db):
    """Not merely staged in the session -- a rollback afterwards must not lose it."""
    post, cred = _post(db), _cred(db)
    db.commit()
    scheduler._record_published(db, post, cred, "remote-1", "https://x/1", NOW)
    db.rollback()
    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp.status == "posted" and pp.remote_id == "remote-1"
