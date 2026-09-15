"""The manual Instagram recovery must not require Flickr.

Meta ingests from a public URL rather than an upload, so the photo has to be reachable
somewhere. That used to mean Flickr, and instagram_post_now said so. R2 staging changed
it to our own bucket and took Flickr out of the Instagram path entirely -- but only the
scheduler's copy of the rule was updated. The manual route kept refusing whenever there
was no Flickr photo, which is exactly the situation it exists to rescue: Flickr down, or
deliberately skipped for this post via target_platforms.

The route publishes nothing. It queues the row and the worker runs the same fanout path,
which does its own R2-aware check, so the guard here only decides whether the operator is
allowed to try.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from models import PlatformCredential, Post, PostPlatform
from routes.posts import instagram_post_now

USER = object()


def _ig_cred(db) -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform="instagram",
                           access_token="tok", account_name="acct")
    db.add(c)
    db.flush()
    return c


def _published(db, *, flickr_id=None) -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted", title="t",
             flickr_photo_id=flickr_id,
             posted_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(p)
    db.flush()
    return p


def test_r2_staging_removes_the_flickr_requirement(db, r2_stub):
    """The regression: an Instagram-only post has no Flickr photo and never will."""
    cred = _ig_cred(db)
    post = _published(db, flickr_id=None)
    db.commit()

    resp = instagram_post_now(post.id, db, USER)

    assert resp.queued is True
    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp.status == "pending"
    assert pp.next_retry_at is not None, "queued but the worker will never pick it up"


def test_without_r2_flickr_is_still_required(db):
    """No R2 and no Flickr photo means nowhere public to fetch from — a real blocker,
    and the message should say what to do about it."""
    _ig_cred(db)
    post = _published(db, flickr_id=None)
    db.commit()

    with pytest.raises(HTTPException) as e:
        instagram_post_now(post.id, db, USER)
    assert e.value.status_code == 400
    assert "R2" in str(e.value.detail)


def test_a_flickr_photo_still_works_without_r2(db):
    cred = _ig_cred(db)
    post = _published(db, flickr_id="123456")
    db.commit()
    assert instagram_post_now(post.id, db, USER).queued is True
    assert db.get(PostPlatform, (post.id, cred.id)).status == "pending"


def test_already_posted_is_still_refused(db, r2_stub):
    """The relaxed gate must not let a live post be queued a second time.

    Needs R2 on, or the public-URL guard fires first and we never reach the check
    under test — the guards are ordered, and this one sits behind it.
    """
    cred = _ig_cred(db)
    post = _published(db, flickr_id=None)
    db.add(PostPlatform(post_id=post.id, platform_id=cred.id,
                        status="posted", remote_id="ig-1"))
    db.commit()
    with pytest.raises(HTTPException) as e:
        instagram_post_now(post.id, db, USER)
    assert e.value.status_code == 409


def test_an_unpublished_post_is_still_refused(db, r2_stub):
    _ig_cred(db)
    p = Post(id=uuid.uuid4().hex, status="pending", title="t")
    db.add(p)
    db.commit()
    with pytest.raises(HTTPException) as e:
        instagram_post_now(p.id, db, USER)
    assert e.value.status_code == 400


def test_the_manual_gate_and_the_publish_gate_agree(db):
    """Two copies of one rule, in different files. They drifted once: the scheduler
    learned about R2 and the route did not, and nothing failed until someone tried to
    recover a post by hand."""
    import inspect
    from routes import posts as posts_mod
    from services import scheduler as sched_mod

    needle = "not post.flickr_photo_id and not r2.configured()"
    assert needle in inspect.getsource(posts_mod.instagram_post_now)
    assert needle in inspect.getsource(sched_mod._post_to_platform)
