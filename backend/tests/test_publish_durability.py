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


# --------------------------------------------------------------------------
# the Flickr path has the same shape, and a duplicate there lands in the archive
# --------------------------------------------------------------------------

@pytest.fixture()
def flickr_ok(monkeypatch, tmp_path):
    """A successful Flickr upload with the surrounding machinery stubbed out."""
    src = tmp_path / "shot.arw"
    src.write_bytes(b"raw")
    monkeypatch.setattr(scheduler.storage, "DERIVATIVES", tmp_path)
    monkeypatch.setattr(scheduler.image, "make_derivative",
                        lambda s, d, edge: d.write_bytes(b"jpeg"))
    monkeypatch.setattr(scheduler.duplicate, "find_in_flickr_cache", lambda db, h: None)
    monkeypatch.setattr(scheduler.duplicate, "find_soft_match", lambda db, **kw: None)
    monkeypatch.setattr(scheduler.tags, "merged_tags_for_post", lambda db, p: "a b")
    monkeypatch.setattr(scheduler.flickr, "format_tags", lambda *a, **k: "a b")
    monkeypatch.setattr(scheduler.caption_text, "description_for", lambda *a, **k: "desc")
    monkeypatch.setattr(scheduler.flickr, "upload_photo", lambda **kw: "9988776655")
    monkeypatch.setattr(scheduler.flickr, "photo_url", lambda pid: f"https://flickr/{pid}")
    return str(src)


def _pending(db, src: str) -> Post:
    p = Post(id=uuid.uuid4().hex, status="pending", title="t", original_filename="f.arw",
             original_path=src, scheduled_at=NOW, retry_count=0)
    db.add(p)
    db.flush()
    return p


def test_flickr_upload_survives_failing_bookkeeping(db, flickr_ok, monkeypatch):
    """The photo is in the archive. Losing the id means the retry uploads it twice."""
    post = _pending(db, flickr_ok)
    db.commit()
    monkeypatch.setattr(scheduler, "_flickr_post_bookkeeping",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("groups blew up")))

    scheduler._flickr_post(db, post, fired_at=NOW)

    fresh = db.get(Post, post.id)
    assert fresh.flickr_photo_id == "9988776655"
    assert fresh.status in ("posted", "late")


def test_failing_bookkeeping_does_not_raise_past_the_fanout(db, flickr_ok, monkeypatch):
    """fire_due_posts skips the non-Flickr fanout when this raises, and will never
    revisit the post because it is no longer pending -- so Instagram would never fire."""
    post = _pending(db, flickr_ok)
    db.commit()
    monkeypatch.setattr(scheduler, "_flickr_post_bookkeeping",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    scheduler._flickr_post(db, post, fired_at=NOW)   # must not raise


def test_flickr_id_is_committed_not_just_staged(db, flickr_ok):
    post = _pending(db, flickr_ok)
    db.commit()
    scheduler._flickr_post(db, post, fired_at=NOW)
    db.rollback()
    assert db.get(Post, post.id).flickr_photo_id == "9988776655"


def test_record_failure_leaves_an_uploaded_post_alone(db):
    post = Post(id=uuid.uuid4().hex, status="posted", title="t",
                flickr_photo_id="123456", scheduled_at=NOW, retry_count=0)
    db.add(post)
    db.commit()

    scheduler._record_failure(db, post, RuntimeError("album add failed"), fired_at=NOW)

    assert post.status == "posted"
    assert post.retry_count == 0, "a live photo must not consume a retry"
    assert post.next_retry_at is None


def test_record_failure_still_works_for_a_real_upload_failure(db):
    post = Post(id=uuid.uuid4().hex, status="pending", title="t",
                scheduled_at=NOW, retry_count=0)
    db.add(post)
    db.commit()
    scheduler._record_failure(db, post, RuntimeError("upload refused"), fired_at=NOW)
    assert post.retry_count == 1
    assert post.status in ("pending", "failed")
