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

from models import AppConfig, EngagementSnapshot as ES, Post, PostEvent, Reel
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
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow(),
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

def test_migration_0036_up_and_down(tmp_path, monkeypatch):
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

    command.downgrade(cfg, "0035_group_audience_size")
    cols = {c["name"] for c in sa.inspect(eng).get_columns("reels")}
    assert "trial_graduation" not in cols
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT id FROM reels")).scalar_one() == "r1"
    eng.dispose()
