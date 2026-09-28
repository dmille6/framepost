"""Instagram Trial Reels: shown to non-followers first, graduating to followers later.

Median Instagram reach on this account is ~9% of followers, so distribution is the
constraint and a Trial Reel is the one native lever built for it. What these pin:

- the wire: trial_params goes out only for a trial, and a trial drops share_to_feed and
  collaborators (the conservative reading of what Meta leaves undocumented);
- the checkpoint: toggling Trial after a failed attempt never publishes the old container;
- the refusal: an account without Trial Reels fails the reel, loudly and once — it is
  never quietly published as an ordinary reel to the followers it was meant to skip;
- the numbers: trial and ordinary reels are never pooled, and feed posts see neither.
"""
import json
import types
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks, HTTPException

from models import AppConfig, EngagementSnapshot as ES, Post, PostEvent, Reel, ReelPhoto
from services import reel_publish
from services.platforms import instagram as ig

from test_ig_container_checkpoint import FakeMeta, Store, _Resp


class TrialMeta(FakeMeta):
    """FakeMeta plus a Meta that refuses containers or publishes with a given error."""

    def __init__(self):
        super().__init__()
        self.create_error: dict | None = None
        self.publish_error: dict | None = None

    def _post(self, path, data):
        if path.endswith("/media") and self.create_error is not None:
            self.creates.append(data)
            return _Resp(400, {"error": self.create_error})
        if path.endswith("/media_publish") and self.publish_error is not None:
            self.publishes.append(data["creation_id"])
            return _Resp(400, {"error": self.publish_error})
        return super()._post(path, data)


@pytest.fixture()
def meta(monkeypatch):
    m = TrialMeta()
    cred = types.SimpleNamespace(access_token="enc", extra_json=json.dumps({"ig_user_id": "ig1"}))
    monkeypatch.setattr(ig, "_client", m.client)
    monkeypatch.setattr(ig, "_load_credential", lambda db: cred)
    monkeypatch.setattr(ig, "_maybe_refresh", lambda db, row: None)
    monkeypatch.setattr(ig, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(ig.time, "sleep", lambda s: None)
    return m


NOT_ELIGIBLE = {"message": "Invalid parameter", "code": 100,
                "error_user_msg": "This account can't share trial reels."}


def _reel_post(db, **kw):
    return ig.post_reel(db, video_url="https://r2.test/r.mp4", caption="Roxie at the Allways",
                        collaborators=["roxie", "allways"], media_identity="mp4-v1", **kw)


# --- the wire ------------------------------------------------------------------------

def test_an_ordinary_reel_is_sent_exactly_as_before(db, meta):
    _reel_post(db, on_checkpoint=Store())
    sent = meta.creates[0]
    assert "trial_params" not in sent
    assert sent["share_to_feed"] == "true"
    assert json.loads(sent["collaborators"]) == ["roxie", "allways"]


@pytest.mark.parametrize("strategy", ["SS_PERFORMANCE", "MANUAL"])
def test_a_trial_reel_sends_trial_params(db, meta, strategy):
    _reel_post(db, trial_graduation=strategy, on_checkpoint=Store())
    sent = meta.creates[0]
    assert json.loads(sent["trial_params"]) == {"graduation_strategy": strategy}
    assert sent["media_type"] == "REELS"


def test_a_trial_reel_asks_for_neither_the_feed_nor_collaborators(db, meta):
    """share_to_feed asks for followers' Feed — the opposite of a trial — and collabs are
    reported unsupported on trials. Either could 400 the whole reel."""
    result = _reel_post(db, trial_graduation="SS_PERFORMANCE", on_checkpoint=Store())
    sent = meta.creates[0]
    assert "share_to_feed" not in sent
    assert "collaborators" not in sent
    assert result["collaborators"] == []


def test_an_unknown_strategy_sends_nothing(db, meta):
    with pytest.raises(ig.InstagramError) as e:
        _reel_post(db, trial_graduation="AUTO", on_checkpoint=Store())
    assert e.value.permanent and meta.creates == []


def test_a_photo_post_never_carries_trial_params(db, meta):
    """Feed posts (and the timing experiment riding on them) are untouched."""
    ig.post_photo(db, image_url="https://r2.test/x.jpg", caption="x", on_checkpoint=Store())
    assert "trial_params" not in meta.creates[0]


# --- the checkpoint ------------------------------------------------------------------

def _fp(identity="mp4-v1", collabs=("roxie", "allways")):
    return ig.content_fingerprint("Roxie at the Allways", list(collabs), identity)


def test_an_ordinary_reels_fingerprint_is_unchanged(db, meta):
    """A checkpoint written before trials existed must still resume after the deploy."""
    meta.containers["c0"] = {"status": "FINISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow(), fingerprint=_fp())
    _reel_post(db, checkpoint=cp, on_checkpoint=Store())
    assert meta.publishes == ["c0"] and meta.creates == []


def test_turning_trial_on_after_a_failed_attempt_builds_a_new_container(db, meta):
    """The checkpointed container was built as an ordinary reel. Publishing it would put
    the reel in front of followers after the photographer chose a trial."""
    meta.containers["c0"] = {"status": "FINISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow(), fingerprint=_fp())
    _reel_post(db, trial_graduation="MANUAL", checkpoint=cp, on_checkpoint=Store())
    assert "c0" not in meta.publishes
    assert len(meta.creates) == 1 and "trial_params" in meta.creates[0]


def test_changing_the_strategy_also_builds_a_new_container(db, meta):
    store = Store()
    _reel_post(db, trial_graduation="MANUAL", on_checkpoint=store)
    built_as_manual = store.saved[0]
    meta.containers[built_as_manual.container_id]["status"] = "FINISHED"
    meta.publishes.clear()
    built_as_manual.publish_sent_at = None

    _reel_post(db, trial_graduation="SS_PERFORMANCE", checkpoint=built_as_manual,
               on_checkpoint=Store())
    assert built_as_manual.container_id not in meta.publishes
    assert json.loads(meta.creates[-1]["trial_params"]) == {"graduation_strategy": "SS_PERFORMANCE"}


def test_an_unchanged_trial_resumes_its_container(db, meta):
    meta.containers["c0"] = {"status": "FINISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow(), trial_graduation="MANUAL",
                                fingerprint=_fp("mp4-v1|trial:MANUAL", collabs=()))
    _reel_post(db, trial_graduation="MANUAL", checkpoint=cp, on_checkpoint=Store())
    assert meta.publishes == ["c0"] and meta.creates == []


# --- the refusal ---------------------------------------------------------------------

def test_an_ineligible_account_fails_permanently_and_is_not_retried_as_a_normal_reel(db, meta):
    meta.create_error = NOT_ELIGIBLE
    with pytest.raises(ig.TrialReelRejected) as e:
        _reel_post(db, trial_graduation="SS_PERFORMANCE", on_checkpoint=Store())
    assert e.value.permanent
    assert "Trial Reel" in str(e.value) and "can't share trial reels" in str(e.value)
    assert len(meta.creates) == 1, "no second, non-trial container"
    assert meta.publishes == []


def test_a_refusal_at_publish_is_classified_too(db, meta):
    meta.publish_error = NOT_ELIGIBLE
    store = Store()
    with pytest.raises(ig.TrialReelRejected):
        _reel_post(db, trial_graduation="MANUAL", on_checkpoint=store)
    assert store.last.publish_sent_at is None, "Meta said no: nothing is unconfirmed"


def test_an_unrelated_400_on_a_trial_keeps_its_own_message(db, meta):
    """A bad video is a bad video; blaming the trial would send someone the wrong way."""
    meta.create_error = {"message": "The video format is not supported", "code": 352}
    with pytest.raises(ig.InstagramError) as e:
        _reel_post(db, trial_graduation="MANUAL", on_checkpoint=Store())
    assert not isinstance(e.value, ig.TrialReelRejected)
    assert e.value.permanent


def test_trial_wording_on_an_ordinary_reel_is_not_a_trial_rejection(db, meta):
    meta.create_error = NOT_ELIGIBLE
    with pytest.raises(ig.InstagramError) as e:
        _reel_post(db, on_checkpoint=Store())
    assert not isinstance(e.value, ig.TrialReelRejected)


def _db_reel(db, tmp_path, **kw):
    f = tmp_path / f"{uuid.uuid4().hex}.mp4"
    f.write_bytes(b"mp4")
    cover = Post(id=uuid.uuid4().hex, status="posted", title="t")
    db.add(cover)
    db.flush()
    r = Reel(id=uuid.uuid4().hex, cover_post_id=cover.id, status="ready", mp4_path=str(f),
             caption="Roxie at the Allways",
             scheduled_at=datetime.utcnow() - timedelta(minutes=1), **kw)
    db.add(r)
    db.commit()
    return r


def test_the_worker_records_the_refusal_and_stops(db, meta, r2_stub, tmp_path):
    r = _db_reel(db, tmp_path, trial_graduation="SS_PERFORMANCE")
    meta.create_error = NOT_ELIGIBLE

    assert reel_publish.run_due(db) == 0
    db.refresh(r)
    assert r.posted_at is None
    assert "Trial Reel" in r.publish_error
    assert reel_publish.due_reels(db) == [], "permanent: not retried every pass"
    ev = db.query(PostEvent).filter_by(post_id=r.cover_post_id,
                                        event_type="reel_trial_rejected").one()
    assert json.loads(ev.details)["reel_id"] == r.id


def test_the_worker_publishes_a_trial_with_its_strategy(db, meta, r2_stub, tmp_path):
    r = _db_reel(db, tmp_path, trial_graduation="MANUAL")
    assert reel_publish.run_due(db) == 1
    db.refresh(r)
    assert r.remote_id and json.loads(meta.creates[0]["trial_params"]) == {
        "graduation_strategy": "MANUAL"}


def test_a_lost_publish_reply_on_a_trial_is_recovered_not_repeated(db, meta, r2_stub, tmp_path):
    """Batch-1 safety holds for trials: the reply is lost, the next attempt finds the
    live media (media_type VIDEO) and does not publish a second copy."""
    r = _db_reel(db, tmp_path, trial_graduation="SS_PERFORMANCE")
    meta.publish_behaviour = ["timeout_after"]

    with pytest.raises(reel_publish.ReelPublishError):
        reel_publish.publish(db, r)
    reel_publish.publish(db, r)
    db.refresh(r)
    assert meta.publishes == ["c1"]
    assert r.remote_id == "m1" and r.posted_at is not None


# --- default and override ------------------------------------------------------------

def _create(db, **fields):
    from routes import reels as routes_reels

    cover = Post(id=uuid.uuid4().hex, status="draft", title="t")
    db.add(cover)
    db.commit()
    crop = {"x": 0, "y": 0, "width": 9, "height": 16}
    body = routes_reels.ReelCreate(cover_post_id=cover.id, photos=[
        {"post_id": cover.id, "position": 0, "crop_start": crop}], **fields)
    return routes_reels.create_reel(body, BackgroundTasks(), db, None)


def _set_default(db, value):
    db.add(AppConfig(key="reel_trial_default", value=value))
    db.commit()


def test_new_reels_are_ordinary_until_the_setting_is_turned_on(db):
    assert _create(db).trial_graduation is None


@pytest.mark.parametrize("value, expected", [
    ("SS_PERFORMANCE", "SS_PERFORMANCE"), ("MANUAL", "MANUAL"), ("off", None), ("junk", None),
])
def test_the_setting_decides_a_new_reel(db, value, expected):
    _set_default(db, value)
    assert _create(db).trial_graduation == expected


def test_an_explicit_choice_overrides_the_setting(db):
    _set_default(db, "SS_PERFORMANCE")
    assert _create(db, trial_graduation=None).trial_graduation is None
    assert _create(db, trial_graduation="MANUAL").trial_graduation == "MANUAL"


def test_the_setting_validates(db):
    from routes import config as routes_config

    out = routes_config.patch_config({"reel_trial_default": "MANUAL"}, db, None)
    assert out["reel_trial_default"] == "MANUAL"
    with pytest.raises(HTTPException):
        routes_config.patch_config({"reel_trial_default": "sometimes"}, db, None)


def test_a_queued_reel_can_be_switched(db, tmp_path):
    from routes import reels as routes_reels

    r = _db_reel(db, tmp_path)
    out = routes_reels.update_reel(r.id, routes_reels.ReelPatch(trial_graduation="MANUAL"),
                                   db, None)
    assert out.trial_graduation == "MANUAL"
    out = routes_reels.update_reel(r.id, routes_reels.ReelPatch(caption="new"), db, None)
    assert out.trial_graduation == "MANUAL", "a patch that doesn't mention it leaves it"
    out = routes_reels.update_reel(r.id, routes_reels.ReelPatch(trial_graduation=None), db, None)
    assert out.trial_graduation is None


def test_a_published_reel_cannot_change_kind(db, tmp_path):
    from routes import reels as routes_reels

    r = _db_reel(db, tmp_path, trial_graduation="MANUAL", posted_at=datetime.utcnow())
    with pytest.raises(HTTPException) as e:
        routes_reels.update_reel(r.id, routes_reels.ReelPatch(trial_graduation=None), db, None)
    assert e.value.status_code == 409


# --- the numbers ---------------------------------------------------------------------

def _snap(db, reel, reach):
    db.add(ES(post_id=reel.cover_post_id, platform="instagram", reel_id=reel.id,
              sampled_at=reel.posted_at + timedelta(days=7), likes=1, comments_count=0,
              views=0, reposts=0, reach=reach))


def test_trial_and_ordinary_reels_are_never_pooled(db, tmp_path):
    from services import analytics_core as core

    posted = datetime.utcnow() - timedelta(days=10)
    ordinary = _db_reel(db, tmp_path, posted_at=posted)
    trial = _db_reel(db, tmp_path, posted_at=posted, trial_graduation="SS_PERFORMANCE")
    _snap(db, ordinary, 100)
    _snap(db, trial, 5000)
    db.commit()

    assert [s.reach for s in core.collect_reel_samples(db, trial=False, window="7d")] == [100]
    assert [s.reach for s in core.collect_reel_samples(db, trial=True, window="7d")] == [5000]
    # ...and feed-post analytics see neither.
    assert core.collect_samples(db, platform="instagram", window="7d") == []


def test_the_reels_summary_reports_both_groups_separately(db, tmp_path):
    from routes import analytics as routes_analytics

    posted = datetime.utcnow() - timedelta(days=10)
    trial = _db_reel(db, tmp_path, posted_at=posted, trial_graduation="MANUAL")
    _snap(db, trial, 700)
    db.commit()

    out = routes_analytics.reel_summary("7d", db, None)
    assert out["ordinary"]["posts"] == 0
    assert out["trial"]["posts"] == 1 and out["trial"]["median_reach"] == 700


# --- the migration -------------------------------------------------------------------

def test_migrations_0036_to_0038_up_and_down(tmp_path, monkeypatch):
    """Existing reels come through as ordinary reels, and the column goes away cleanly."""
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    import config as app_config

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setattr(app_config.settings, "database_url", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0035_group_audience_size")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO posts (id, status) VALUES ('p1', 'posted')"))
        c.execute(sa.text("INSERT INTO reels (id, cover_post_id) VALUES ('r1', 'p1')"))

    command.upgrade(cfg, "0036_reel_trial")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT trial_graduation FROM reels")).scalar_one() is None

    command.upgrade(cfg, "0037_reel_frame_ig_follows")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO reel_photos (reel_id, position, post_id) "
                          "VALUES ('r1', 0, 'p1')"))
        assert c.execute(sa.text("SELECT ig_follows_reel FROM reel_photos")).scalar_one() == 0
    command.upgrade(cfg, "0038_reel_publish_claim")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT publish_claimed_at FROM reels")).scalar_one() is None
    command.downgrade(cfg, "0037_reel_frame_ig_follows")
    assert "publish_claimed_at" not in {c["name"] for c in sa.inspect(eng).get_columns("reels")}
    command.downgrade(cfg, "0036_reel_trial")
    assert "ig_follows_reel" not in {c["name"] for c in sa.inspect(eng).get_columns("reel_photos")}

    command.downgrade(cfg, "0035_group_audience_size")
    cols = {c["name"] for c in sa.inspect(eng).get_columns("reels")}
    assert "trial_graduation" not in cols
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT id FROM reels")).scalar_one() == "r1"
    eng.dispose()


# --- review round: the kind is frozen while Meta has it -------------------------------

def _patch(db, reel_id, **fields):
    from routes import reels as routes_reels
    return routes_reels.update_reel(reel_id, routes_reels.ReelPatch(**fields), db, None)


def test_the_kind_cannot_change_while_a_container_exists(db, tmp_path):
    cp = ig.ContainerCheckpoint("c9", datetime.utcnow())
    r = _db_reel(db, tmp_path, ig_container=cp.to_json())
    with pytest.raises(HTTPException) as e:
        _patch(db, r.id, trial_graduation="MANUAL")
    assert e.value.status_code == 409
    # ...but everything else about the reel can still be edited, and re-sending the
    # current value is not a change.
    assert _patch(db, r.id, caption="new", trial_graduation=None).caption == "new"


def test_the_strategy_cannot_change_while_a_container_exists_either(db, tmp_path):
    cp = ig.ContainerCheckpoint("c9", datetime.utcnow(), trial_graduation="MANUAL")
    r = _db_reel(db, tmp_path, trial_graduation="MANUAL", ig_container=cp.to_json())
    with pytest.raises(HTTPException):
        _patch(db, r.id, trial_graduation="SS_PERFORMANCE")


def test_a_definite_no_from_meta_frees_the_kind_again(db, meta, r2_stub, tmp_path):
    """Refused at media_publish: the container is known unpublished, so it is dropped
    and the photographer can turn Trial off and reschedule."""
    r = _db_reel(db, tmp_path, trial_graduation="MANUAL")
    meta.publish_error = NOT_ELIGIBLE
    reel_publish.run_due(db)
    db.refresh(r)
    assert r.ig_container is None
    assert _patch(db, r.id, trial_graduation=None).trial_graduation is None


def test_an_outstanding_publish_keeps_its_container(db, meta, r2_stub, tmp_path):
    r = _db_reel(db, tmp_path, trial_graduation="MANUAL")
    meta.publish_behaviour = ["timeout_after"]
    with pytest.raises(reel_publish.ReelPublishError):
        reel_publish.publish(db, r)
    db.refresh(r)
    assert r.ig_container is not None
    with pytest.raises(HTTPException):
        _patch(db, r.id, trial_graduation=None)


def test_the_kind_recorded_is_the_kind_that_was_sent(db, r2_stub, tmp_path, monkeypatch):
    """A change that slips in while the worker is publishing does not rewrite history."""
    r = _db_reel(db, tmp_path, trial_graduation="MANUAL")
    seen = {}

    def publish_and_meanwhile_edit(db_, **kw):
        seen["asked"] = kw["trial_graduation"]
        r.trial_graduation = None      # the concurrent edit, landing mid-publish
        return {"remote_id": "m1", "url": None, "collaborators": [],
                "collaborators_rejected": [], "trial_graduation": kw["trial_graduation"]}
    monkeypatch.setattr(ig, "post_reel", publish_and_meanwhile_edit)

    reel_publish.publish(db, r)
    db.refresh(r)
    assert seen["asked"] == "MANUAL" and r.trial_graduation == "MANUAL"


def test_the_checkpoint_remembers_the_kind(db, meta):
    store = Store()
    _reel_post(db, trial_graduation="SS_PERFORMANCE", on_checkpoint=store)
    assert store.saved[0].trial_graduation == "SS_PERFORMANCE"
    assert ig.ContainerCheckpoint.from_json('{"id": "c", "created_at": "2026-01-01T00:00:00"}'
                                            ).trial_graduation is None


def test_a_recovered_publish_records_what_actually_went_out(db, meta, r2_stub, tmp_path):
    """The container went out as a trial; the row now says ordinary. Meta's copy wins."""
    meta.containers["c0"] = {"status": "PUBLISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow() - timedelta(minutes=2),
                                publish_sent_at=datetime.utcnow() - timedelta(minutes=1),
                                trial_graduation="MANUAL", caption="Roxie at the Allways")
    r = _db_reel(db, tmp_path, trial_graduation=None, ig_container=cp.to_json())
    result = reel_publish.publish(db, r)
    db.refresh(r)
    assert result["recovered"] and r.posted_at is not None
    assert r.trial_graduation == "MANUAL"
    assert meta.publishes == []


# --- review round: a legacy checkpoint is not a trial --------------------------------

def test_a_legacy_checkpoint_is_rebuilt_for_a_trial(db, meta):
    """No fingerprint, never sent, FINISHED: it was built as an ordinary reel."""
    meta.containers["c0"] = {"status": "FINISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow())
    _reel_post(db, trial_graduation="MANUAL", checkpoint=cp, on_checkpoint=Store())
    assert "c0" not in meta.publishes
    assert "trial_params" in meta.creates[0]


def test_a_legacy_checkpoint_still_resumes_for_an_ordinary_reel(db, meta):
    meta.containers["c0"] = {"status": "FINISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow())
    _reel_post(db, checkpoint=cp, on_checkpoint=Store())
    assert meta.publishes == ["c0"] and meta.creates == []


# --- review round: a hint when the refusal doesn't name the trial --------------------

def test_an_unrelated_400_on_a_trial_gets_the_trial_hint(db, meta):
    meta.create_error = {"message": "Invalid parameter", "code": 100}
    with pytest.raises(ig.InstagramError) as e:
        _reel_post(db, trial_graduation="MANUAL", on_checkpoint=Store())
    assert not isinstance(e.value, ig.TrialReelRejected), "classification unchanged"
    assert e.value.permanent
    assert str(e.value).endswith(ig.TRIAL_HINT)


def test_an_ordinary_reel_gets_no_trial_hint(db, meta):
    meta.create_error = {"message": "Invalid parameter", "code": 100}
    with pytest.raises(ig.InstagramError) as e:
        _reel_post(db, on_checkpoint=Store())
    assert ig.TRIAL_HINT not in str(e.value)


# --- user decision: a trial reel's draft frames still go to Instagram ----------------

def _connect_instagram(db):
    from models import PlatformCredential
    db.add(PlatformCredential(id=uuid.uuid4().hex, platform="instagram", access_token="x"))
    db.commit()


def _drafts(db, n=2, targets=None):
    out = []
    for i in range(n):
        p = Post(id=uuid.uuid4().hex, status="pending", title=f"f{i}",
                 target_platforms=json.dumps(targets) if targets is not None else None)
        db.add(p)
        out.append(p)
    db.commit()
    return out


def _build_from_drafts(db, frames, **fields):
    from routes import reels as routes_reels
    crop = {"x": 0, "y": 0, "width": 9, "height": 16}
    body = routes_reels.ReelCreate(
        cover_post_id=frames[0].id, frames_from_drafts=True,
        photos=[{"post_id": f.id, "position": i, "crop_start": crop}
                for i, f in enumerate(frames)], **fields)
    return routes_reels.create_reel(body, BackgroundTasks(), db, None)


def _ig(db, post):
    from services import preflight
    from models import PlatformCredential
    db.refresh(post)
    return "instagram" in preflight.targets_for(
        post, list(db.query(PlatformCredential).all()))


def test_an_ordinary_reel_from_drafts_takes_instagram_off_the_frames(db):
    _connect_instagram(db)
    frames = _drafts(db)
    _build_from_drafts(db, frames, trial_graduation=None)
    assert not any(_ig(db, f) for f in frames)
    assert json.loads(frames[0].target_platforms) == ["flickr"], "everything else kept"


def test_a_trial_reel_from_drafts_leaves_the_frames_on_instagram(db):
    _connect_instagram(db)
    frames = _drafts(db)
    _build_from_drafts(db, frames, trial_graduation="SS_PERFORMANCE")
    assert all(_ig(db, f) for f in frames)


def test_switching_a_drafts_reel_retargets_its_frames_both_ways(db):
    _connect_instagram(db)
    frames = _drafts(db)
    reel = _build_from_drafts(db, frames, trial_graduation="MANUAL")
    _patch(db, reel.id, trial_graduation=None)
    assert not any(_ig(db, f) for f in frames)
    _patch(db, reel.id, trial_graduation="SS_PERFORMANCE")
    assert all(_ig(db, f) for f in frames)


def test_a_frame_that_already_posted_is_never_retargeted(db):
    _connect_instagram(db)
    frames = _drafts(db)
    reel = _build_from_drafts(db, frames, trial_graduation="MANUAL")
    frames[1].status = "posted"
    frames[1].posted_at = datetime.utcnow()
    db.commit()
    _patch(db, reel.id, trial_graduation=None)
    assert not _ig(db, frames[0]) and _ig(db, frames[1])


def test_a_frame_that_never_targeted_instagram_is_not_given_it(db):
    _connect_instagram(db)
    frames = _drafts(db, targets=["flickr", "bluesky"])
    reel = _build_from_drafts(db, frames, trial_graduation=None)
    _patch(db, reel.id, trial_graduation="MANUAL")
    assert not any(_ig(db, f) for f in frames)


def test_a_reel_not_built_from_drafts_never_touches_its_frames(db):
    """The Reel tab builds from published history (or the post it is open on); its
    frames' targeting is not the reel's to manage."""
    _connect_instagram(db)
    frames = _drafts(db, n=1)
    reel = _create_for(db, frames[0])
    _patch(db, reel.id, trial_graduation="MANUAL")
    _patch(db, reel.id, trial_graduation=None)
    assert _ig(db, frames[0]) and frames[0].target_platforms is None


def _create_for(db, post):
    from routes import reels as routes_reels
    crop = {"x": 0, "y": 0, "width": 9, "height": 16}
    body = routes_reels.ReelCreate(cover_post_id=post.id, photos=[
        {"post_id": post.id, "position": 0, "crop_start": crop}])
    return routes_reels.create_reel(body, BackgroundTasks(), db, None)


def test_replacing_the_photos_keeps_a_frames_follow_flag(db):
    from routes import reels as routes_reels
    _connect_instagram(db)
    frames = _drafts(db)
    reel = _build_from_drafts(db, frames, trial_graduation="MANUAL")
    crop = {"x": 0, "y": 0, "width": 9, "height": 16}
    _patch(db, reel.id, photos=[routes_reels.ReelPhotoIn(post_id=frames[1].id, position=0,
                                                         crop_start=crop)])
    _patch(db, reel.id, trial_graduation=None)
    assert not _ig(db, frames[1])



# --- second review round: an atomic claim, interleaved for real ----------------------
# Two sessions on one file-backed SQLite database — the worker's and the route's — so
# the interleavings below are the real ones: separate connections, SQLite serialising
# the writers. The in-memory fixture is a single connection and could not show this.

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def two(tmp_path):
    from database import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"timeout": 5})

    @sa_event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)
    worker, route = make(), make()
    yield worker, route
    worker.close()
    route.close()
    engine.dispose()


def _fresh(session_pair, reel_id):
    """What is actually committed, read through a third session."""
    worker, _ = session_pair
    s = sessionmaker(bind=worker.get_bind())()
    try:
        return s.get(Reel, reel_id)
    finally:
        s.close()


def _sent(kw, remote="m1"):
    return {"remote_id": remote, "url": None, "collaborators": [],
            "collaborators_rejected": [], "trial_graduation": kw["trial_graduation"]}


def _patch_expect(route, reel_id, value):
    """PATCH the trial kind from the route's session. Returns the HTTP status."""
    from routes import reels as routes_reels
    try:
        routes_reels.update_reel(reel_id, routes_reels.ReelPatch(trial_graduation=value),
                                 route, None)
        return 200
    except HTTPException as e:
        return e.status_code


def test_a_patch_landing_mid_publish_is_refused_and_the_label_stays_true(
        two, r2_stub, tmp_path, monkeypatch):
    """The reported race: the route read the reel before the worker checkpointed, and
    tries to write while Meta is working. It must lose, and the reel must be recorded
    as what went out."""
    worker, route = two
    r = _db_reel(worker, tmp_path)                       # ordinary
    route.get(Reel, r.id)                                # the route's early read
    outcome = {}

    def post_reel(db_, **kw):
        outcome["patch"] = _patch_expect(route, r.id, "MANUAL")   # before any checkpoint
        return _sent(kw)
    monkeypatch.setattr(ig, "post_reel", post_reel)

    reel_publish.publish(worker, r)
    assert outcome["patch"] == 409
    assert _patch_expect(route, r.id, "MANUAL") == 409, "and after: it is posted"
    assert _fresh(two, r.id).trial_graduation is None
    assert _fresh(two, r.id).publish_claimed_at is None


def test_a_patch_after_the_claim_but_before_the_checkpoint_is_refused(two, tmp_path):
    worker, route = two
    r = _db_reel(worker, tmp_path)
    assert reel_publish.claim(worker, r.id)
    assert _fresh(two, r.id).ig_container is None, "the window the claim closes"
    assert _patch_expect(route, r.id, "MANUAL") == 409
    assert _fresh(two, r.id).trial_graduation is None


def test_a_patch_that_wins_before_the_claim_is_what_gets_published(
        two, r2_stub, tmp_path, monkeypatch):
    """The worker listed the reel as ordinary, then the PATCH committed, then the worker
    claimed. The claim re-reads, so the trial the photographer chose is what goes out."""
    worker, route = two
    r = _db_reel(worker, tmp_path)
    [listed] = reel_publish.due_reels(worker)
    assert _patch_expect(route, r.id, "MANUAL") == 200
    asked = {}

    def post_reel(db_, **kw):
        asked["trial"] = kw["trial_graduation"]
        return _sent(kw)
    monkeypatch.setattr(ig, "post_reel", post_reel)

    reel_publish.publish(worker, listed)
    assert asked["trial"] == "MANUAL"
    assert _fresh(two, r.id).trial_graduation == "MANUAL"


def test_two_workers_never_publish_the_same_reel(two, tmp_path, monkeypatch):
    worker, other = two
    r = _db_reel(worker, tmp_path)
    assert reel_publish.claim(worker, r.id)
    theirs = other.get(Reel, r.id)
    monkeypatch.setattr(ig, "post_reel", lambda db_, **kw: pytest.fail("published twice"))
    with pytest.raises(reel_publish.ReelBusy):
        reel_publish.publish(other, theirs)
    assert _fresh(two, r.id).publish_attempts == 0, "losing the claim spends no attempt"


def test_a_dead_workers_claim_goes_stale(two, tmp_path):
    worker, route = two
    r = _db_reel(worker, tmp_path)
    r.publish_claimed_at = datetime.utcnow() - reel_publish.CLAIM_STALE_AFTER - timedelta(minutes=1)
    worker.commit()
    assert _patch_expect(route, r.id, "MANUAL") == 200, "the photographer isn't locked out"
    assert _fresh(two, r.id).publish_claimed_at is None
    r2 = _db_reel(worker, tmp_path)
    r2.publish_claimed_at = datetime.utcnow() - reel_publish.CLAIM_STALE_AFTER - timedelta(minutes=1)
    worker.commit()
    assert reel_publish.claim(worker, r2.id), "and the next pass takes it over"


def test_a_fresh_claim_is_not_taken_over(two, tmp_path):
    worker, other = two
    r = _db_reel(worker, tmp_path)
    assert reel_publish.claim(worker, r.id)
    assert not reel_publish.claim(other, r.id)


def test_a_confirmed_non_publish_releases_the_claim(two, r2_stub, tmp_path, monkeypatch):
    worker, route = two
    r = _db_reel(worker, tmp_path)

    def refused(db_, **kw):
        raise ig.InstagramError("Invalid parameter", permanent=True, http_status=400)
    monkeypatch.setattr(ig, "post_reel", refused)

    reel_publish.run_due(worker)
    row = _fresh(two, r.id)
    assert row.publish_claimed_at is None and row.publish_attempts == reel_publish.MAX_ATTEMPTS
    assert _patch_expect(route, r.id, "MANUAL") == 200


def test_a_transient_failure_releases_the_claim_for_the_next_pass(
        two, r2_stub, tmp_path, monkeypatch):
    worker, _ = two
    r = _db_reel(worker, tmp_path)

    def slow(db_, **kw):
        raise ig.InstagramError("still IN_PROGRESS — will retry")
    monkeypatch.setattr(ig, "post_reel", slow)
    reel_publish.run_due(worker)
    assert _fresh(two, r.id).publish_claimed_at is None
    assert reel_publish.claim(worker, r.id)


def test_an_unexpected_crash_in_the_attempt_releases_the_claim(two, r2_stub, tmp_path, monkeypatch):
    worker, _ = two
    r = _db_reel(worker, tmp_path)
    monkeypatch.setattr(ig, "post_reel", lambda db_, **kw: 1 / 0)
    reel_publish.run_due(worker)
    assert _fresh(two, r.id).publish_claimed_at is None


# --- second review round: frames follow what actually went out ------------------------

def test_a_recovered_trial_puts_its_frames_back_on_instagram(db, meta, r2_stub):
    """Built as ordinary (frames lost Instagram); the container that actually published
    was a trial. The frames follow Meta's copy — except one that already posted."""
    _connect_instagram(db)
    frames = _drafts(db, n=3)
    built = _build_from_drafts(db, frames, trial_graduation=None)
    assert not any(_ig(db, f) for f in frames)
    frames[2].status = "posted"
    frames[2].posted_at = datetime.utcnow()
    db.commit()

    reel = db.get(Reel, built.id)
    meta.containers["c0"] = {"status": "PUBLISHED", "caption": "Roxie at the Allways",
                             "type": "VIDEO"}
    reel.caption = "Roxie at the Allways"
    reel.status = "ready"
    reel.mp4_path = "/nonexistent.mp4"
    reel.ig_container = ig.ContainerCheckpoint(
        "c0", datetime.utcnow() - timedelta(minutes=2),
        publish_sent_at=datetime.utcnow() - timedelta(minutes=1),
        trial_graduation="MANUAL", caption="Roxie at the Allways").to_json()
    db.commit()

    reel_publish.publish(db, reel)
    assert reel.trial_graduation == "MANUAL"
    assert _ig(db, frames[0]) and _ig(db, frames[1])
    assert not _ig(db, frames[2]), "a posted frame is history"


def test_a_published_ordinary_reel_leaves_its_frames_off_instagram(db, r2_stub, tmp_path, monkeypatch):
    _connect_instagram(db)
    frames = _drafts(db)
    built = _build_from_drafts(db, frames, trial_graduation=None)
    reel = db.get(Reel, built.id)
    f = tmp_path / "r.mp4"
    f.write_bytes(b"mp4")
    reel.status, reel.mp4_path = "ready", str(f)
    db.commit()
    monkeypatch.setattr(ig, "post_reel", lambda db_, **kw: _sent(kw))
    reel_publish.publish(db, reel)
    assert not any(_ig(db, f) for f in frames)


# --- second review round: only true drafts are managed -------------------------------

def test_a_scheduled_frame_is_never_managed(db):
    """Scheduled is a plan someone made; the reel keeps its hands off it, whatever the
    client says about where the frames came from."""
    _connect_instagram(db)
    draft, scheduled = _drafts(db)
    scheduled.scheduled_at = datetime.utcnow() + timedelta(days=2)
    db.commit()
    reel = _build_from_drafts(db, [draft, scheduled], trial_graduation=None)
    assert not _ig(db, draft)
    assert _ig(db, scheduled) and scheduled.target_platforms is None
    flags = {p.post_id: p.ig_follows_reel for p in
             db.query(ReelPhoto).filter_by(reel_id=reel.id).all()}
    assert flags == {draft.id: True, scheduled.id: False}
    _patch(db, reel.id, trial_graduation="MANUAL")
    _patch(db, reel.id, trial_graduation=None)
    assert _ig(db, scheduled) and scheduled.target_platforms is None


def test_published_photos_claimed_as_drafts_are_not_managed(db):
    _connect_instagram(db)
    [p] = _drafts(db, n=1)
    p.status, p.posted_at = "posted", datetime.utcnow()
    db.commit()
    _build_from_drafts(db, [p], trial_graduation=None)
    assert _ig(db, p) and p.target_platforms is None


# --- third review round: claims are owned and renewed ---------------------------------

from sqlalchemy import update as sa_update


def _age_claim(session, reel_id, token="thief"):
    """Another worker takes the claim over as stale (what a slow Meta used to allow)."""
    session.execute(sa_update(Reel).where(Reel.id == reel_id)
                    .values(publish_claim_token=token, publish_claimed_at=datetime.utcnow()))
    session.commit()


def test_a_stolen_claim_is_not_cleared_by_its_previous_owner(two, tmp_path):
    worker, other = two
    r = _db_reel(worker, tmp_path)
    mine = reel_publish.claim(worker, r.id)
    other.execute(sa_update(Reel).where(Reel.id == r.id).values(
        publish_claimed_at=datetime.utcnow() - reel_publish.CLAIM_STALE_AFTER - timedelta(minutes=1)))
    other.commit()
    theirs = reel_publish.claim(other, r.id)
    assert theirs, "a claim not renewed for CLAIM_STALE_AFTER is taken over"

    mine.release()
    row = _fresh(two, r.id)
    assert row.publish_claim_token == theirs.token and row.publish_claimed_at is not None


def test_a_worker_that_lost_its_claim_writes_nothing(two, tmp_path):
    worker, other = two
    r = _db_reel(worker, tmp_path)
    mine = reel_publish.claim(worker, r.id)
    _age_claim(other, r.id)
    worker.refresh(r)
    r.publish_error = "written by the loser"
    with pytest.raises(reel_publish.ClaimLost):
        mine.commit()
    assert _fresh(two, r.id).publish_error is None


def test_renewal_keeps_a_long_attempt_from_looking_dead(two, tmp_path):
    worker, other = two
    r = _db_reel(worker, tmp_path)
    mine = reel_publish.claim(worker, r.id)
    other.execute(sa_update(Reel).where(Reel.id == r.id).values(
        publish_claimed_at=datetime.utcnow() - reel_publish.CLAIM_STALE_AFTER - timedelta(minutes=1)))
    other.commit()
    mine.heartbeat(force=True)                     # the attempt is alive and says so
    assert not reel_publish.claim(other, r.id)


def test_slow_meta_takeover_mid_poll_stops_before_publishing(
        two, meta, r2_stub, tmp_path, monkeypatch):
    """The reproduction: Meta is slow, the claim is taken over while this worker polls.
    It must stop at its next renewal — before media_publish — and must not clear the
    new owner's claim. The checkpoint it saved lets the next attempt finish, once."""
    worker, other = two
    monkeypatch.setattr(reel_publish, "CLAIM_RENEW_EVERY", 0.0)
    r = _db_reel(worker, tmp_path)
    polls = {"n": 0}
    real_get = meta._get

    def slow_get(path, params):
        if params.get("fields") == "status_code" and polls["n"] == 0:
            polls["n"] += 1
            _age_claim(other, r.id)                        # taken over during the wait
            return _Resp(200, {"status_code": "IN_PROGRESS"})
        return real_get(path, params)
    monkeypatch.setattr(meta, "_get", slow_get)

    with pytest.raises(reel_publish.ClaimLost):
        reel_publish.publish(worker, r)
    assert meta.publishes == []
    row = _fresh(two, r.id)
    assert row.publish_claim_token == "thief", "the old owner didn't clear the new claim"
    assert json.loads(row.ig_container)["id"] == "c1"
    assert row.posted_at is None

    # The thief dies; its claim goes stale; the next pass resumes the checkpoint.
    other.execute(sa_update(Reel).where(Reel.id == r.id).values(
        publish_claimed_at=datetime.utcnow() - reel_publish.CLAIM_STALE_AFTER - timedelta(minutes=1)))
    other.commit()
    monkeypatch.setattr(meta, "_get", real_get)
    assert reel_publish.run_due(other) == 1
    assert meta.publishes == ["c1"] and len(meta.creates) == 1
    assert _fresh(two, r.id).posted_at is not None


def test_a_claim_lost_after_the_container_is_ready_never_reaches_media_publish(
        two, meta, r2_stub, tmp_path, monkeypatch):
    """Renewal is throttled while polling. What stops it here is the guarded save of the
    "publish sent" mark, which must commit before media_publish goes out; a forced
    renewal in _publish_checkpointed backs that up for callers whose save isn't guarded."""
    worker, other = two
    r = _db_reel(worker, tmp_path)
    real_get = meta._get

    def get(path, params):
        out = real_get(path, params)
        if params.get("fields") == "status_code":
            _age_claim(other, r.id)                        # lost as it finishes
        return out
    monkeypatch.setattr(meta, "_get", get)

    with pytest.raises(reel_publish.ClaimLost):
        reel_publish.publish(worker, r)
    assert meta.publishes == []
    assert _fresh(two, r.id).publish_claim_token == "thief"


def test_a_lost_claim_spends_no_attempt_in_the_worker_loop(two, meta, r2_stub, tmp_path, monkeypatch):
    worker, other = two
    r = _db_reel(worker, tmp_path)
    real_get = meta._get

    def get(path, params):
        out = real_get(path, params)
        if params.get("fields") == "status_code":
            _age_claim(other, r.id)
        return out
    monkeypatch.setattr(meta, "_get", get)
    assert reel_publish.run_due(worker) == 0
    row = _fresh(two, r.id)
    assert row.publish_attempts == 1 and row.publish_error is None
