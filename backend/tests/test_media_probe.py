"""Probe the media URL before Meta is asked to fetch it.

Meta reports an unfetchable URL as a 400 that reads like a content rejection (2207052),
after a container call and on the retry queue's clock. A one-byte ranged GET finds the
dead URL first and can route around it: re-stage, or use the other host. And the probe
must never be the thing that fails a publish whose URL was fine.
"""
import json
import types
import uuid
from datetime import datetime

import httpx
import pytest
from PIL import Image

from models import PlatformCredential, Post, PostPlatform, Reel
from services import media_probe, publish_errors, reel_publish, scheduler
from services.platforms import instagram as ig


def _transport(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(media_probe, "_client", lambda: media_probe.http_client.client(
        transport=httpx.MockTransport(record), follow_redirects=True))
    return seen


def _image(status=206, ctype="image/jpeg"):
    return lambda req: httpx.Response(status, headers={"content-type": ctype} if ctype else {},
                                      content=b"\xff")


# --- the probe itself ----------------------------------------------------------------

def test_a_ranged_get_with_our_own_user_agent(monkeypatch):
    seen = _transport(monkeypatch, _image())
    assert media_probe.probe("https://r2.test/a.jpg?X-Amz-Signature=s").ok
    req = seen[0]
    assert req.method == "GET"                    # presigned GET URLs 403 a HEAD
    assert req.headers["range"] == "bytes=0-0"
    # Not an imitation of Meta's fetcher: that invites the bot rules we must not trip.
    assert req.headers["user-agent"] == media_probe.http_client.USER_AGENT


@pytest.mark.parametrize("status,ctype,ok", [
    (200, "image/jpeg", True),           # host ignored Range: still fine
    (206, "video/mp4", False),           # wrong kind for an image
    (200, "text/html; charset=utf-8", False),   # a proxy's error page with a 200
    (404, "image/jpeg", False),
    (410, None, False),
    (500, "text/html", False),
    (503, None, False),
    # Advisory: may be about this box (bot rules, rate limits), not about Meta.
    (401, None, True),
    (403, "application/xml", True),
    (429, "text/plain", True),
    (200, None, True),                   # no content-type: let Meta judge
    (200, "binary/octet-stream", True),
])
def test_what_counts_as_fetchable(monkeypatch, status, ctype, ok):
    _transport(monkeypatch, _image(status, ctype))
    assert media_probe.probe("https://h/x.jpg").ok is ok


def test_a_get_refused_as_a_method_falls_back_to_head(monkeypatch):
    def handler(req):
        if req.method == "GET":
            return httpx.Response(405)
        return httpx.Response(404)
    seen = _transport(monkeypatch, handler)
    assert not media_probe.probe("https://h/x.jpg").ok
    assert [r.method for r in seen] == ["GET", "HEAD"]


def test_a_bot_wall_never_stops_the_publish(db, monkeypatch):
    """A 403 from a CDN's bot rules: log, and let Meta try the very same URL."""
    _transport(monkeypatch, lambda req: httpx.Response(403, headers={"content-type": "text/html"}))
    assert media_probe.first_fetchable(
        "https://cdn/a.jpg", [("restage", lambda: pytest.fail("re-staged a good URL"))]
    ) == "https://cdn/a.jpg"


def test_a_transport_failure_is_unreachable(monkeypatch):
    def handler(req):
        raise httpx.ConnectError("refused")
    _transport(monkeypatch, handler)
    r = media_probe.probe("https://h/x.jpg")
    assert not r.ok and "unreachable" in r.reason


def test_a_bug_in_the_probe_never_blocks_the_publish(monkeypatch):
    def broken():
        raise RuntimeError("probe bug")
    monkeypatch.setattr(media_probe, "_client", broken)
    assert media_probe.probe("https://h/x.jpg").ok


def test_fallbacks_are_only_built_when_needed(monkeypatch):
    _transport(monkeypatch, _image())
    assert media_probe.first_fetchable(
        "https://h/a.jpg", [("restage", lambda: pytest.fail("built for nothing"))]
    ) == "https://h/a.jpg"


def test_the_first_fetchable_fallback_wins(monkeypatch):
    _transport(monkeypatch, lambda req: _image()(req) if "good" in str(req.url)
               else httpx.Response(404))
    url = media_probe.first_fetchable("https://bad/a.jpg", [
        ("broken", lambda: (_ for _ in ()).throw(RuntimeError("upload failed"))),
        ("also bad", lambda: "https://bad/b.jpg"),
        ("good", lambda: "https://good/c.jpg"),
    ])
    assert url == "https://good/c.jpg"


def test_nothing_fetchable_names_hosts_not_signatures(monkeypatch):
    _transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(media_probe.MediaUnreachable) as ei:
        media_probe.first_fetchable("https://r2.test/a.jpg?X-Amz-Signature=secret",
                                    [("re-staged copy", lambda: "https://r2.test/b.jpg?sig=x")])
    msg = str(ei.value)
    assert "r2.test" in msg and "HTTP 404" in msg and "secret" not in msg


# --- wired into the Instagram publish ------------------------------------------------

def _landscape(db, tmp_path):
    src = tmp_path / "p.jpg"
    Image.new("RGB", (1500, 1000), (80, 20, 40)).save(src, "JPEG")
    post = Post(id=uuid.uuid4().hex, status="posted", width=1500, height=1000,
                original_path=str(src), flickr_photo_id="42", title="t")
    cred = PlatformCredential(id=uuid.uuid4().hex, platform="instagram", access_token="enc",
                              extra_json=json.dumps({"ig_user_id": "ig1"}))
    db.add_all([post, cred])
    db.commit()
    return post, cred


class _Meta:
    def __init__(self):
        self.creates = []

    def client(self):
        meta = self

        class C:
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def post(self, path, data=None):
                if path.endswith("/media"):
                    meta.creates.append(dict(data))
                    return httpx.Response(200, json={"id": "c1"})
                return httpx.Response(200, json={"id": "m1"})

            def get(self, path, params=None):
                if params and params.get("fields") == "status_code":
                    return httpx.Response(200, json={"status_code": "FINISHED"})
                return httpx.Response(200, json={"permalink": "https://instagram.com/p/m1/"})
        return C()


@pytest.fixture()
def meta(monkeypatch):
    m = _Meta()
    cred = types.SimpleNamespace(access_token="enc", extra_json=json.dumps({"ig_user_id": "ig1"}))
    monkeypatch.setattr(ig, "_client", m.client)
    monkeypatch.setattr(ig, "_load_credential", lambda db: cred)
    monkeypatch.setattr(ig, "_maybe_refresh", lambda db, row: None)
    monkeypatch.setattr(ig, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(ig.time, "sleep", lambda s: None)
    return m


def test_a_dead_r2_url_is_restaged_before_meta_sees_it(db, meta, r2_stub, monkeypatch, tmp_path):
    post, cred = _landscape(db, tmp_path)
    dead: set[str] = set()

    def handler(req):
        return httpx.Response(404) if req.url.path in dead else _image()(req)
    _transport(monkeypatch, handler)
    # The first staged object's URL is dead (e.g. deleted by a lifecycle rule).
    real_presign = __import__("services.r2", fromlist=["x"]).presign_get
    first = {}

    def presign(k, **kw):
        url = real_presign(k, **kw)
        if not first:
            first["path"] = httpx.URL(url).path
            dead.add(first["path"])
        return url
    monkeypatch.setattr("services.r2.presign_get", presign)

    scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    assert len(meta.creates) == 1
    assert httpx.URL(meta.creates[0]["image_url"]).path != first["path"]
    assert db.get(PostPlatform, (post.id, cred.id)).status == "posted"


def test_r2_down_falls_back_to_flickr_for_an_unreshaped_photo(db, meta, r2_stub, monkeypatch,
                                                              tmp_path):
    post, cred = _landscape(db, tmp_path)
    _transport(monkeypatch, lambda req: httpx.Response(503) if req.url.host == "r2.test"
               else _image()(req))
    monkeypatch.setattr(scheduler.flickr, "get_display_image_url",
                        lambda db, pid, **kw: "https://live.staticflickr.com/42_b.jpg")

    scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    assert meta.creates[0]["image_url"] == "https://live.staticflickr.com/42_b.jpg"


def test_nothing_fetchable_costs_no_meta_call_and_retries(db, meta, r2_stub, monkeypatch,
                                                          tmp_path):
    post, cred = _landscape(db, tmp_path)
    _transport(monkeypatch, lambda req: httpx.Response(404))
    monkeypatch.setattr(scheduler.flickr, "get_display_image_url",
                        lambda db, pid, **kw: "https://live.staticflickr.com/42_b.jpg")

    scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    assert meta.creates == []
    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp.status == "pending" and pp.next_retry_at is not None
    assert "unreachable" in pp.error_message


def test_the_unreachable_error_is_retry_class_not_reauth():
    err = ig.InstagramError("image URL unreachable for abcd — nothing was sent to Instagram: "
                            "r2.test: HTTP 404")
    f = publish_errors.classify("instagram", err)
    assert f.category is publish_errors.FailureCategory.RETRY


def test_a_reel_url_that_fails_is_restaged_once(db, meta, r2_stub, monkeypatch, tmp_path):
    f = tmp_path / "r.mp4"
    f.write_bytes(b"mp4")
    cover = Post(id=uuid.uuid4().hex, status="posted")
    db.add(cover)
    db.flush()
    reel = Reel(id=uuid.uuid4().hex, cover_post_id=cover.id, status="ready", mp4_path=str(f),
                caption="r", scheduled_at=datetime.utcnow())
    db.add(reel)
    db.commit()
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(404, headers={"content-type": "application/xml"})
        return httpx.Response(206, headers={"content-type": "video/mp4"})
    _transport(monkeypatch, handler)

    reel_publish.publish(db, reel)

    assert calls["n"] == 2
    assert meta.creates[0]["video_url"].startswith("https://r2.test/reels/")
    assert reel.posted_at is not None


# --- GPT review: after the alternatives, only a definitive failure blocks ------------

def _raise_connect(req):
    raise httpx.ConnectError("refused")


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(503),
    lambda req: httpx.Response(500, headers={"content-type": "text/html"}),
    _raise_connect,
])
def test_soft_failures_everywhere_still_hand_meta_the_original(monkeypatch, handler):
    _transport(monkeypatch, handler)
    assert media_probe.first_fetchable(
        "https://r2.test/orig.jpg", [("re-staged copy", lambda: "https://r2.test/new.jpg")]
    ) == "https://r2.test/orig.jpg"


def test_a_dead_original_prefers_a_merely_flaky_alternative(monkeypatch):
    _transport(monkeypatch, lambda req: httpx.Response(404) if "orig" in str(req.url)
               else httpx.Response(503))
    assert media_probe.first_fetchable(
        "https://r2.test/orig.jpg", [("re-staged copy", lambda: "https://r2.test/new.jpg")]
    ) == "https://r2.test/new.jpg"


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(410),
    lambda req: httpx.Response(200, headers={"content-type": "text/html"}),
    lambda req: httpx.Response(206, headers={"content-type": "video/mp4"}),
])
def test_only_definitive_failures_block(monkeypatch, handler):
    _transport(monkeypatch, handler)
    with pytest.raises(media_probe.MediaUnreachable):
        media_probe.first_fetchable(
            "https://r2.test/orig.jpg", [("re-staged copy", lambda: "https://r2.test/new.jpg")])


def test_r2_and_flickr_both_flaky_still_publishes(db, meta, r2_stub, monkeypatch, tmp_path):
    post, cred = _landscape(db, tmp_path)
    _transport(monkeypatch, lambda req: httpx.Response(502))
    monkeypatch.setattr(scheduler.flickr, "get_display_image_url",
                        lambda db, pid, **kw: "https://live.staticflickr.com/42_b.jpg")

    scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    assert len(meta.creates) == 1
    assert meta.creates[0]["image_url"].startswith("https://r2.test/")   # the original
    assert db.get(PostPlatform, (post.id, cred.id)).status == "posted"
