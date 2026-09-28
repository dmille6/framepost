"""A crash mid-publish must not leave a platform row pending forever.

The retry scanner only picks up rows with next_retry_at set, and only the failure
recorder sets it. An attempt killed after creating its row (staging, the Instagram
container checkpoint) but before recording an outcome left the row 'pending' with no
retry time: never retried, never failed, invisible.
"""
import json
import uuid
from datetime import datetime, timedelta

from models import PlatformCredential, Post, PostEvent, PostPlatform
from services import carousel as carousel_svc, scheduler

NOW = datetime(2026, 10, 1, 12, 0)
LONG_AGO = NOW - timedelta(hours=2)


def _row(db, cred, *, post_status="posted", fired=LONG_AGO, **pp):
    post = Post(id=uuid.uuid4().hex, status=post_status, updated_at=fired)
    db.add(post)
    db.flush()
    row = PostPlatform(post_id=post.id, platform_id=cred.id, **{"status": "pending", **pp})
    db.add(row)
    db.commit()
    return post, row


def _cred(db, platform="instagram"):
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform, access_token="t")
    db.add(c)
    db.commit()
    return c


def test_a_stranded_row_is_requeued_and_logged(db):
    cred = _cred(db)
    post, row = _row(db, cred, ig_container=json.dumps({"id": "c1"}))

    assert scheduler.requeue_stranded(db, now=NOW) == 1
    db.refresh(row)
    assert row.next_retry_at == NOW and row.status == "pending"
    ev = db.query(PostEvent).filter_by(post_id=post.id).one()
    assert ev.event_type == "instagram_requeued"
    assert json.loads(ev.details)["container_checkpoint"] is True


def test_the_sweep_leaves_everything_else_alone(db):
    cred = _cred(db)
    untouched = [
        _row(db, cred, fired=NOW - timedelta(minutes=10))[1],       # may still be in flight
        _row(db, cred, post_status="pending")[1],                   # post hasn't fired
        _row(db, cred, next_retry_at=NOW + timedelta(hours=1))[1],  # already scheduled
        _row(db, cred, status="posted", remote_id="m1")[1],         # published
        _row(db, cred, remote_id="m2")[1],                          # published, oddly pending
        _row(db, cred, status="failed")[1],                         # terminal
        _row(db, cred, status=carousel_svc.MEMBER_STATUS)[1],       # carousel member
    ]
    member_post, member_row = _row(db, cred)
    member_post.carousel_id, member_post.carousel_position = "car1", 2
    db.commit()
    untouched.append(member_row)

    assert scheduler.requeue_stranded(db, now=NOW) == 0
    for row in untouched:
        db.refresh(row)
        assert row.next_retry_at in (None, NOW + timedelta(hours=1))


def test_a_requeued_instagram_row_asks_meta_before_publishing(db, monkeypatch):
    """End to end: the retry path picks the row up and, because the container is
    already PUBLISHED, records it without publishing a second time."""
    from services.platforms import instagram as ig

    cred = _cred(db)
    cred.extra_json = json.dumps({"ig_user_id": "ig1"})
    cp = ig.ContainerCheckpoint("c1", datetime.utcnow() - timedelta(minutes=45),
                                publish_sent_at=datetime.utcnow() - timedelta(minutes=45))
    post, row = _row(db, cred, fired=datetime.utcnow() - timedelta(hours=1),
                     ig_container=cp.to_json())
    post.flickr_photo_id = "1"
    db.commit()
    published = []
    monkeypatch.setattr(ig, "_container_status", lambda cid, tok: "PUBLISHED")
    monkeypatch.setattr(ig, "_find_published", lambda *a, **k: [("m9", "https://ig/p/m9")])
    monkeypatch.setattr(ig, "_publish_container", lambda *a: published.append(a))
    monkeypatch.setattr(ig, "_load_credential", lambda db: cred)
    monkeypatch.setattr(ig, "_maybe_refresh", lambda db, r: None)
    monkeypatch.setattr(ig, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(scheduler, "_source_for", lambda p: "x.jpg")
    monkeypatch.setattr(scheduler.flickr, "get_display_image_url", lambda db, pid, **kw: "u")
    monkeypatch.setattr(scheduler.ig_variant, "target_ratio_key",
                        lambda *a: scheduler.ig_variant.NATIVE_RATIO_KEY)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    assert scheduler.requeue_stranded(db) == 1
    scheduler.retry_due_platform_posts()

    db.refresh(row)
    assert published == []
    assert row.status == "posted" and row.remote_id == "m9"


def test_other_platforms_are_flagged_for_review_once_and_never_requeued(db):
    """Bluesky has no container to ask about: the crash may have come after the post
    went live, and a retry would post it twice."""
    for platform in ("bluesky", "pixelfed", "pinterest"):
        cred = _cred(db, platform)
        post, row = _row(db, cred)

        assert scheduler.requeue_stranded(db, now=NOW) == 0
        assert scheduler.requeue_stranded(db, now=NOW + timedelta(minutes=15)) == 0

        db.refresh(row)
        assert row.next_retry_at is None and row.status == "pending"
        assert "won't be retried automatically" in row.error_message
        evs = db.query(PostEvent).filter_by(post_id=post.id).all()
        assert [e.event_type for e in evs] == [f"{platform}_needs_review"]


def test_an_instagram_row_without_a_checkpoint_is_still_requeued(db):
    """No checkpoint means no container was ever created — nothing can be live."""
    cred = _cred(db)
    _post, row = _row(db, cred)
    assert scheduler.requeue_stranded(db, now=NOW) == 1
