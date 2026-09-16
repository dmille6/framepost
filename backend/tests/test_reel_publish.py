"""Publishing a reel without a phone in the loop.

The builder produced a correct MP4 in May and the last two steps stayed manual:
download, upload by hand. Exactly one reel has ever been made, which is the
evidence that a workflow with a manual step in the middle does not get run.

What these pin is mostly about not doing damage. Publishing is irreversible and
slow: Meta transcodes server-side for minutes, so a timeout is a retry and a
rejected file is not, and getting that distinction wrong either spams the account
with duplicates or silently drops reels. The selection query and the retry
accounting are therefore tested harder than the happy path.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from models import Performer, Post, PostPerformer, Reel, ReelPhoto
from services import reel_publish
from services.platforms import instagram as ig


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _post(db, title="p") -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted", title=title)
    db.add(p)
    db.flush()
    return p


def _reel(db, *, status="ready", scheduled_at=None, posted_at=None,
          attempts=0, mp4="/tmp/x.mp4") -> Reel:
    cover = _post(db)
    r = Reel(id=uuid.uuid4().hex, cover_post_id=cover.id, total_duration_seconds=30.0,
             caption="cap", mp4_path=mp4, status=status, scheduled_at=scheduled_at,
             posted_at=posted_at, publish_attempts=attempts)
    db.add(r)
    db.flush()
    return r


def _performer(db, post_id, handle, name=None):
    perf = Performer(id=uuid.uuid4().hex, display_name=name or handle,
                     instagram_handle=handle)
    db.add(perf)
    db.flush()
    db.add(PostPerformer(post_id=post_id, performer_id=perf.id))
    db.flush()
    return perf


# --------------------------------------------------------------------------
# which reels go out
# --------------------------------------------------------------------------

def test_a_scheduled_ready_reel_is_due(db):
    r = _reel(db, scheduled_at=_now() - timedelta(minutes=1))
    db.commit()
    assert [x.id for x in reel_publish.due_reels(db)] == [r.id]


def test_a_reel_still_rendering_is_never_due(db):
    """It has no file behind it. Sending it would fail minutes later in a log rather
    than at the moment someone could act on it."""
    _reel(db, status="pending", scheduled_at=_now() - timedelta(minutes=1))
    db.commit()
    assert reel_publish.due_reels(db) == []


def test_a_failed_render_is_never_due(db):
    _reel(db, status="failed", scheduled_at=_now() - timedelta(minutes=1))
    db.commit()
    assert reel_publish.due_reels(db) == []


def test_an_unscheduled_reel_is_never_due(db):
    _reel(db, scheduled_at=None)
    db.commit()
    assert reel_publish.due_reels(db) == []


def test_a_future_reel_is_not_yet_due(db):
    _reel(db, scheduled_at=_now() + timedelta(hours=2))
    db.commit()
    assert reel_publish.due_reels(db) == []


def test_an_already_published_reel_is_never_due_again(db):
    """The property that stops a duplicate reel appearing on the account."""
    _reel(db, scheduled_at=_now() - timedelta(days=1), posted_at=_now())
    db.commit()
    assert reel_publish.due_reels(db) == []


def test_a_reel_out_of_attempts_is_dropped(db):
    _reel(db, scheduled_at=_now() - timedelta(minutes=1),
          attempts=reel_publish.MAX_ATTEMPTS)
    db.commit()
    assert reel_publish.due_reels(db) == []


def test_due_reels_come_out_in_scheduled_order(db):
    late = _reel(db, scheduled_at=_now() - timedelta(minutes=1))
    early = _reel(db, scheduled_at=_now() - timedelta(hours=5))
    db.commit()
    assert [x.id for x in reel_publish.due_reels(db)] == [early.id, late.id]


# --------------------------------------------------------------------------
# collaborators
# --------------------------------------------------------------------------

def test_collaborators_gather_from_every_frame_not_just_the_cover(db):
    """A reel is several performers' photographs. Crediting only whoever is on the
    thumbnail both misses people and wastes the reach each invite brings."""
    r = _reel(db)
    other = _post(db)
    db.add(ReelPhoto(reel_id=r.id, position=0, post_id=r.cover_post_id))
    db.add(ReelPhoto(reel_id=r.id, position=1, post_id=other.id))
    _performer(db, r.cover_post_id, "cover_performer")
    _performer(db, other.id, "second_performer")
    db.commit()

    assert reel_publish.collaborators_for(db, r) == ["cover_performer", "second_performer"]


def test_the_cover_performer_comes_first(db):
    """Instagram takes three. The cap should land on whoever is most prominent."""
    r = _reel(db)
    other = _post(db)
    db.add(ReelPhoto(reel_id=r.id, position=0, post_id=other.id))
    db.add(ReelPhoto(reel_id=r.id, position=1, post_id=r.cover_post_id))
    _performer(db, other.id, "later")
    _performer(db, r.cover_post_id, "cover")
    db.commit()

    assert reel_publish.collaborators_for(db, r)[0] == "cover"


def test_a_performer_in_two_frames_is_credited_once(db):
    r = _reel(db)
    second = _post(db)
    db.add(ReelPhoto(reel_id=r.id, position=0, post_id=r.cover_post_id))
    db.add(ReelPhoto(reel_id=r.id, position=1, post_id=second.id))
    perf = _performer(db, r.cover_post_id, "repeat")
    db.add(PostPerformer(post_id=second.id, performer_id=perf.id))
    db.commit()

    assert reel_publish.collaborators_for(db, r) == ["repeat"]


def test_performers_without_a_handle_are_skipped(db):
    r = _reel(db)
    db.add(ReelPhoto(reel_id=r.id, position=0, post_id=r.cover_post_id))
    db.add(Performer(id=uuid.uuid4().hex, display_name="No Handle"))
    perf = Performer(id=uuid.uuid4().hex, display_name="Anon", instagram_handle=None)
    db.add(perf)
    db.flush()
    db.add(PostPerformer(post_id=r.cover_post_id, performer_id=perf.id))
    db.commit()

    assert reel_publish.collaborators_for(db, r) == []


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------

def test_staging_without_r2_is_a_permanent_failure(db):
    """A photo can fall back to its Flickr rendition; a reel has no equivalent. Retrying
    this forever would never succeed."""
    r = _reel(db)
    db.commit()
    with pytest.raises(reel_publish.ReelPublishError) as e:
        reel_publish.stage(r)
    assert e.value.permanent is True


def test_staging_a_missing_file_is_permanent(db, r2_stub, tmp_path):
    r = _reel(db, mp4=str(tmp_path / "gone.mp4"))
    db.commit()
    with pytest.raises(reel_publish.ReelPublishError) as e:
        reel_publish.stage(r)
    assert e.value.permanent is True


def test_staging_uploads_the_mp4_as_video(db, r2_stub, tmp_path):
    f = tmp_path / "reel.mp4"
    f.write_bytes(b"\x00\x00\x00 ftypmp42fake")
    r = _reel(db, mp4=str(f))
    db.commit()

    key, url = reel_publish.stage(r)
    assert key in r2_stub
    assert r2_stub[key] == f.read_bytes()
    assert url.startswith("https://")


# --------------------------------------------------------------------------
# publishing and retries
# --------------------------------------------------------------------------

def _ok_publish(monkeypatch, **over):
    result = {"remote_id": "17900", "url": "https://instagram.com/reel/x",
              "collaborators": [], "collaborators_rejected": []}
    result.update(over)
    monkeypatch.setattr(ig, "post_reel", lambda db, **kw: result)
    return result


def test_publish_records_where_it_landed_and_unstages(db, r2_stub, tmp_path, monkeypatch):
    f = tmp_path / "r.mp4"
    f.write_bytes(b"mp4")
    r = _reel(db, mp4=str(f), scheduled_at=_now() - timedelta(minutes=1))
    db.commit()
    _ok_publish(monkeypatch)

    reel_publish.publish(db, r)
    db.refresh(r)
    assert r.remote_id == "17900"
    assert r.posted_at is not None
    assert r.publish_error is None
    assert r.staged_key is None, "the staged MP4 should not outlive the publish"
    assert r2_stub == {}, "and it should be gone from the bucket"


def test_a_transient_failure_leaves_the_reel_retryable(db, r2_stub, tmp_path, monkeypatch):
    """Meta being slow is not the reel being wrong. Marking it spent here would lose a
    perfectly good reel to one bad minute."""
    f = tmp_path / "r.mp4"
    f.write_bytes(b"mp4")
    r = _reel(db, mp4=str(f), scheduled_at=_now() - timedelta(minutes=1))
    db.commit()

    def boom(db_, **kw):
        raise ig.InstagramError("container still IN_PROGRESS after 480s — will retry")
    monkeypatch.setattr(ig, "post_reel", boom)

    assert reel_publish.run_due(db) == 0
    db.refresh(r)
    assert r.posted_at is None
    assert r.publish_attempts == 1
    assert r.publish_attempts < reel_publish.MAX_ATTEMPTS
    assert [x.id for x in reel_publish.due_reels(db)] == [r.id]


def test_a_permanent_failure_stops_being_retried(db, r2_stub, tmp_path, monkeypatch):
    f = tmp_path / "r.mp4"
    f.write_bytes(b"mp4")
    r = _reel(db, mp4=str(f), scheduled_at=_now() - timedelta(minutes=1))
    db.commit()

    def boom(db_, **kw):
        raise ig.InstagramError("unsupported video format", permanent=True)
    monkeypatch.setattr(ig, "post_reel", boom)

    assert reel_publish.run_due(db) == 0
    db.refresh(r)
    assert reel_publish.due_reels(db) == []
    assert "unsupported video format" in (r.publish_error or "")


def test_one_bad_reel_does_not_stop_the_next(db, r2_stub, tmp_path, monkeypatch):
    good_f = tmp_path / "good.mp4"
    good_f.write_bytes(b"mp4")
    bad = _reel(db, mp4=str(tmp_path / "missing.mp4"),
                scheduled_at=_now() - timedelta(hours=2))
    good = _reel(db, mp4=str(good_f), scheduled_at=_now() - timedelta(minutes=1))
    db.commit()
    _ok_publish(monkeypatch)

    assert reel_publish.run_due(db) == 1
    db.refresh(good)
    db.refresh(bad)
    assert good.posted_at is not None
    assert bad.posted_at is None


# --------------------------------------------------------------------------
# engagement collection
# --------------------------------------------------------------------------
# Reels publish through reels.remote_id, not post_platforms, and the engagement sync
# iterates post_platforms. The first reel published through the API therefore collected
# nothing for 7.5 hours. Engagement not sampled on the day is gone, not merely late.

def test_reel_snapshots_do_not_pollute_the_cover_posts_analytics(db):
    """A cover photo can have both its own Instagram post and a reel it appears in.
    Merging the two series would silently average two different pieces of media."""
    from models import EngagementSnapshot as ES
    from services import analytics_core as core

    r = _reel(db, posted_at=_now() - timedelta(days=10))
    cover = db.get(Post, r.cover_post_id)
    cover.posted_at = _now() - timedelta(days=10)
    # The cover's own post engagement...
    db.add(ES(post_id=cover.id, platform="instagram",
              sampled_at=cover.posted_at + timedelta(days=7),
              likes=10, comments_count=0, views=0, reposts=0, reach=100))
    # ...and the reel's, which happens to be much larger.
    db.add(ES(post_id=cover.id, platform="instagram", reel_id=r.id,
              sampled_at=cover.posted_at + timedelta(days=7),
              likes=999, comments_count=0, views=0, reposts=0, reach=9999))
    db.commit()

    samples = core.collect_samples(db, platform="instagram", window="7d")
    mine = [s for s in samples if s.post.id == cover.id]
    assert len(mine) == 1
    assert mine[0].reach == 100, "the reel's reach must not be read as the photo's"


def test_reel_engagement_sync_is_wired_into_sync_all(db):
    """A collector nothing calls collects nothing."""
    import inspect
    from services import comments as comments_svc

    assert "instagram_reels" in inspect.getsource(comments_svc.sync_all)


def test_reel_engagement_sync_without_a_credential_is_not_an_error(db):
    from services import comments as comments_svc

    assert comments_svc.sync_instagram_reels(db) == {"sampled": 0, "errors": 0}
