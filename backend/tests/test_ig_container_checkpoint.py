"""Crash-safe Instagram publishing: checkpoint the container, never publish twice.

Between POST /media (create) and POST /media_publish there is a window — seconds for a
photo, minutes for a reel that is transcoding — in which a crash, a deploy or a timeout
used to leave the retry two bad choices: build a new container, or (when the publish had
actually gone through) publish a second copy. Meta has been seen publishing on requests
it answered with an error, so "the request failed" is not "nothing went out".

The container is now checkpointed before media_publish is sent, and a later attempt asks
Meta what became of it (status_code) before doing anything else.
"""
import json
import types
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from models import PlatformCredential, Post, PostEvent, PostPlatform, Reel
from services import publish_errors, reel_publish, scheduler
from services.platforms import instagram as ig


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeMeta:
    """A small graph.instagram.com: containers, published media, and knobs for how
    media_publish misbehaves."""

    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.media: list[dict] = []
        self.creates: list[dict] = []
        self.publishes: list[str] = []
        self.status_checks: list[str] = []
        # Per media_publish call: ok | timeout_after (published, reply lost) |
        # timeout_before (never arrived) | 500 | 400
        self.publish_behaviour: list[str] = []
        self.media_lookup_fails = False

    def client(self):
        meta = self

        class Client:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, path, data=None): return meta._post(path, dict(data or {}))
            def get(self, path, params=None): return meta._get(path, dict(params or {}))

        return Client()

    def _post(self, path, data):
        if path.endswith("/media"):
            cid = f"c{len(self.containers) + 1}"
            kind = {"REELS": "VIDEO", "CAROUSEL": "CAROUSEL_ALBUM"}.get(
                data.get("media_type"), "IMAGE")
            self.containers[cid] = {"status": "FINISHED", "caption": data.get("caption"),
                                    "type": kind}
            self.creates.append(data)
            return _Resp(200, {"id": cid})
        if path.endswith("/media_publish"):
            cid = data["creation_id"]
            self.publishes.append(cid)
            how = self.publish_behaviour.pop(0) if self.publish_behaviour else "ok"
            if how == "timeout_before":
                raise httpx.ReadTimeout("The read operation timed out")
            if how == "500":
                return _Resp(500, {"error": {"message": "An unexpected error has occurred"}})
            if how == "400":
                return _Resp(400, {"error": {"message": "Invalid parameter", "code": 100}})
            c = self.containers[cid]
            mid = f"m{len(self.media) + 1}"
            c["status"] = "PUBLISHED"
            self.media.insert(0, {
                "id": mid, "caption": c["caption"], "media_type": c["type"],
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                "permalink": f"https://www.instagram.com/p/{mid}/",
            })
            if how == "timeout_after":
                raise httpx.ReadTimeout("The read operation timed out")
            return _Resp(200, {"id": mid})
        raise AssertionError(path)

    def _get(self, path, params):
        if path.endswith("/media"):
            if self.media_lookup_fails:
                return _Resp(500, {"error": {"message": "try later"}})
            return _Resp(200, {"data": self.media})
        obj = path.strip("/")
        if params.get("fields") == "status_code":
            self.status_checks.append(obj)
            if obj not in self.containers:
                return _Resp(400, {"error": {"message": "does not exist", "code": 100}})
            return _Resp(200, {"status_code": self.containers[obj]["status"]})
        if params.get("fields") == "permalink":
            return _Resp(200, {"permalink": f"https://www.instagram.com/p/{obj}/"})
        return _Resp(200, {})


@pytest.fixture()
def meta(monkeypatch):
    m = FakeMeta()
    cred = types.SimpleNamespace(access_token="enc", extra_json=json.dumps({"ig_user_id": "ig1"}))
    monkeypatch.setattr(ig, "_client", m.client)
    monkeypatch.setattr(ig, "_load_credential", lambda db: cred)
    monkeypatch.setattr(ig, "_maybe_refresh", lambda db, row: None)
    monkeypatch.setattr(ig, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(ig.time, "sleep", lambda s: None)
    return m


class Store:
    """What on_checkpoint persisted — the stand-in for the committed DB column."""

    def __init__(self):
        self.saved: list = []

    def __call__(self, cp):
        self.saved.append(None if cp is None else ig.ContainerCheckpoint.from_json(cp.to_json()))

    @property
    def last(self):
        return self.saved[-1] if self.saved else None


class Crash(BaseException):
    """A process dying: not an Exception, so nothing in the adapter swallows it."""


def _photo(db, **kw):
    return ig.post_photo(db, image_url="https://r2.test/x.jpg", caption="Roxie at the Allways",
                         **kw)


# --- the four required behaviours ---------------------------------------------------

def test_crash_after_create_resumes_and_publishes_the_same_container_once(db, meta, monkeypatch):
    store = Store()
    real = ig._publish_container

    def die(*a, **k):
        raise Crash()
    monkeypatch.setattr(ig, "_publish_container", die)
    with pytest.raises(Crash):
        _photo(db, on_checkpoint=store)
    cp = store.last
    assert cp is not None and cp.container_id == "c1"
    assert cp.publish_sent_at is not None         # durable before the request was sent

    monkeypatch.setattr(ig, "_publish_container", real)
    result = _photo(db, checkpoint=cp, on_checkpoint=store)

    assert len(meta.creates) == 1, "the retry built a second container"
    assert meta.publishes == ["c1"]
    assert result["remote_id"] == "m1"


def test_a_published_container_is_never_published_again(db, meta):
    cp = ig.ContainerCheckpoint("c1", datetime.utcnow() - timedelta(minutes=5))
    meta.containers["c1"] = {"status": "PUBLISHED", "caption": "Roxie at the Allways",
                             "type": "IMAGE"}
    meta.media.append({"id": "m77", "caption": "Roxie at the Allways", "media_type": "IMAGE",
                       "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                       "permalink": "https://www.instagram.com/p/m77/"})

    result = _photo(db, checkpoint=cp, on_checkpoint=Store())

    assert meta.publishes == [] and meta.creates == []
    assert result["remote_id"] == "m77" and result["recovered"] is True
    assert result["url"] == "https://www.instagram.com/p/m77/"


def test_an_ambiguous_publish_is_confirmed_next_time_not_repeated(db, meta):
    """Meta published, the reply was lost. The retry must find it, not post it again."""
    store = Store()
    meta.publish_behaviour = ["timeout_after"]
    with pytest.raises(ig.PublishUnconfirmed) as ei:
        _photo(db, on_checkpoint=store)
    failure = publish_errors.classify("instagram", ei.value)
    assert failure.retryable and not failure.requires_reauth
    assert store.last.publish_sent_at is not None
    meta.status_checks.clear()

    result = _photo(db, checkpoint=store.last, on_checkpoint=store)

    assert meta.publishes == ["c1"], "published a second time"
    assert len(meta.creates) == 1, "re-created instead of checking the container"
    assert meta.status_checks == ["c1"]       # asked Meta first
    assert result["remote_id"] == "m1" and result.get("recovered")


def test_expired_checkpoint_gets_a_fresh_container(db, meta):
    store = Store()
    old = ig.ContainerCheckpoint("c-old", datetime.utcnow() - timedelta(hours=25))

    result = _photo(db, checkpoint=old, on_checkpoint=store)

    assert "c-old" not in meta.status_checks        # past 24h: nothing to ask about
    assert len(meta.creates) == 1 and meta.publishes == ["c1"]
    assert store.saved[0] is None                  # the stale one was discarded first
    assert result["remote_id"] == "m1"


# --- the rest of the state machine ---------------------------------------------------

def test_a_publish_that_never_arrived_is_sent_again_on_the_same_container(db, meta):
    store = Store()
    meta.publish_behaviour = ["500"]
    with pytest.raises(ig.PublishUnconfirmed):
        _photo(db, on_checkpoint=store)

    result = _photo(db, checkpoint=store.last, on_checkpoint=store)

    assert meta.publishes == ["c1", "c1"]         # same container, no second create
    assert len(meta.creates) == 1
    assert result["remote_id"] == "m1" and not result.get("recovered")


def test_an_unconfirmed_publish_is_not_repeated_while_meta_cant_be_asked(db, meta):
    store = Store()
    meta.publish_behaviour = ["timeout_before"]
    with pytest.raises(ig.PublishUnconfirmed):
        _photo(db, on_checkpoint=store)
    meta.media_lookup_fails = True

    with pytest.raises(ig.PublishUnconfirmed):
        _photo(db, checkpoint=store.last, on_checkpoint=store)
    assert meta.publishes == ["c1"]


def test_a_clear_rejection_is_not_left_unconfirmed(db, meta):
    store = Store()
    meta.publish_behaviour = ["400"]
    with pytest.raises(ig.InstagramError) as ei:
        _photo(db, on_checkpoint=store)
    assert not isinstance(ei.value, ig.PublishUnconfirmed)
    assert ei.value.permanent
    assert store.last.publish_sent_at is None


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_a_dead_container_is_replaced(db, meta, status):
    meta.containers["c0"] = {"status": status, "caption": "x", "type": "IMAGE"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow() - timedelta(hours=1))
    _photo(db, checkpoint=cp, on_checkpoint=Store())
    assert meta.publishes == ["c2"]


def test_an_in_progress_reel_is_waited_on_not_recreated(db, meta, monkeypatch):
    meta.containers["c0"] = {"status": "IN_PROGRESS", "caption": "reel", "type": "VIDEO"}
    polls = []

    def fake_await(cid, token, *, describing, tries=0, interval=0):
        polls.append((cid, tries))
        meta.containers[cid]["status"] = "FINISHED"
    monkeypatch.setattr(ig, "_await_container", fake_await)
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow() - timedelta(minutes=3))

    ig.post_reel(db, video_url=lambda: pytest.fail("resumed reel was re-staged"),
                 caption="reel", checkpoint=cp, on_checkpoint=Store())

    assert polls == [("c0", ig.REEL_POLL_TRIES)]
    assert meta.publishes == ["c0"] and meta.creates == []


def test_a_resumed_container_keeps_the_collaborators_it_was_created_with(db, meta):
    meta.containers["c0"] = {"status": "FINISHED", "caption": "x", "type": "IMAGE"}
    cp = ig.ContainerCheckpoint("c0", datetime.utcnow(), collaborators=["roxie"],
                                rejected=["ghost"])
    result = _photo(db, checkpoint=cp, collaborators=["someone_else"], on_checkpoint=Store())
    assert result["collaborators"] == ["roxie"]
    assert result["collaborators_rejected"] == ["ghost"]


def test_a_resumed_carousel_parent_builds_no_children(db, meta):
    meta.containers["p0"] = {"status": "FINISHED", "caption": "set", "type": "CAROUSEL_ALBUM"}
    cp = ig.ContainerCheckpoint("p0", datetime.utcnow())
    images = [ig.CarouselImage(url=lambda: pytest.fail("frame re-fetched")) for _ in range(3)]

    ig.post_carousel(db, images=images, caption="set", checkpoint=cp, on_checkpoint=Store())

    assert meta.creates == [] and meta.publishes == ["p0"]


# --- through the scheduler: the checkpoint survives the failed attempt's rollback ----

def _ig_post(db):
    post = Post(id=uuid.uuid4().hex, status="posted", title="Roxie", flickr_photo_id="1")
    cred = PlatformCredential(id=uuid.uuid4().hex, platform="instagram", access_token="enc",
                              extra_json=json.dumps({"ig_user_id": "ig1"}))
    db.add_all([post, cred])
    db.commit()
    return post, cred


def test_scheduler_persists_the_checkpoint_and_recovers_on_retry(db, meta, monkeypatch, tmp_path):
    post, cred = _ig_post(db)
    monkeypatch.setattr(scheduler, "_source_for", lambda p: tmp_path / "x.jpg")
    monkeypatch.setattr(scheduler.flickr, "get_display_image_url",
                        lambda db, pid, **kw: "https://live.staticflickr.com/x.jpg")
    monkeypatch.setattr(scheduler.ig_variant, "target_ratio_key",
                        lambda *a: scheduler.ig_variant.NATIVE_RATIO_KEY)
    meta.publish_behaviour = ["timeout_after"]

    scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()
    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp.status == "pending"                      # retryable, not failed
    assert json.loads(pp.ig_container)["id"] == "c1"   # survived the rollback

    pp.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    scheduler.retry_due_platform_posts()

    db.refresh(pp)
    assert meta.publishes == ["c1"]
    assert pp.status == "posted" and pp.remote_id == "m1"
    assert pp.ig_container is None
    types_ = [e.event_type for e in db.query(PostEvent).filter_by(post_id=post.id)]
    assert "instagram_publish_recovered" in types_


def test_reel_checkpoint_lives_on_the_reel(db, meta, r2_stub, tmp_path):
    f = tmp_path / "r.mp4"
    f.write_bytes(b"mp4")
    cover = Post(id=uuid.uuid4().hex, status="posted")
    db.add(cover)
    db.flush()
    reel = Reel(id=uuid.uuid4().hex, cover_post_id=cover.id, status="ready", mp4_path=str(f),
                caption="reel", scheduled_at=datetime.utcnow() - timedelta(minutes=1))
    db.add(reel)
    db.commit()
    meta.publish_behaviour = ["500"]

    with pytest.raises(reel_publish.ReelPublishError):
        reel_publish.publish(db, reel)
    db.refresh(reel)
    assert json.loads(reel.ig_container)["id"] == "c1"

    reel_publish.publish(db, reel)
    db.refresh(reel)
    assert meta.publishes == ["c1", "c1"] and len(meta.creates) == 1
    assert reel.remote_id == "m1" and reel.ig_container is None
