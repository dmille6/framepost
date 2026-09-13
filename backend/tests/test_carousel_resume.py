"""A carousel is built across attempts, not in one burst.

Meta will not accept uploaded bytes for an image — it ingests only from a public URL —
so every frame depends on Meta's fetcher succeeding. A failed fetch is reported as "the
media could not be fetched from this URI": a 400 shaped exactly like a dead link, fired
for URLs curl pulls down fine.

The outage this machinery was built for (2026-09-11) was Flickr refusing to serve Meta's
fetcher. That was proven by handing Meta the identical bytes from R2 in the same minute
and having them accepted — it was NOT a Meta rate limit, which was an earlier reading the
evidence disproved. Don't rebuild that theory from the error text alone. Staging to R2
took Flickr out of the path entirely.

The resume machinery still earns its keep: a fetch can fail intermittently, most often
Meta racing CDN propagation of a just-staged object, and a partly built carousel must not
restart from zero. Children are therefore durable — each attempt resumes from the last
one's, and an attempt that got a frame through doesn't spend a try.
"""
import json
import types
import uuid

import pytest

from models import PlatformCredential, Post, PostPlatform
from services import scheduler
from services.platforms import instagram as ig


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


_THROTTLED = FakeResponse(400, {"error": {
    "message": "Media download has failed",
    "code": 9004,
    "error_user_msg": "The media could not be fetched from this URI: https://example/x.jpg",
}})


def _fake_ig(monkeypatch, *, children_allowed: int | None = None) -> list[dict]:
    """Stand in for graph.instagram.com, returning the bodies actually POSTed.

    children_allowed: how many child containers get through before Meta starts claiming
    it can't fetch the URL. None means every fetch succeeds.
    """
    posts: list[dict] = []

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def post(self, path, data=None):
            body = dict(data or {})
            posts.append(body)
            if body.get("is_carousel_item"):
                built = sum(1 for p in posts if p.get("is_carousel_item"))
                if children_allowed is not None and built > children_allowed:
                    return _THROTTLED
            return FakeResponse(200, {"id": f"c{len(posts)}"})

    cred = types.SimpleNamespace(
        access_token="enc", extra_json=json.dumps({"ig_user_id": "ig1"})
    )
    monkeypatch.setattr(ig, "_client", lambda: FakeClient())
    monkeypatch.setattr(ig, "_load_credential", lambda db: cred)
    monkeypatch.setattr(ig, "_maybe_refresh", lambda db, row: None)
    monkeypatch.setattr(ig, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(ig, "_await_container", lambda cid, tok, describing="": None)
    monkeypatch.setattr(
        ig, "_publish_container", lambda uid, pid, tok: ("media1", "https://instagram.com/p/x/")
    )
    return posts


def _images(n: int) -> list[ig.CarouselImage]:
    return [ig.CarouselImage(url=f"https://example/u{i}.jpg", alt=None) for i in range(n)]


def _children(posts: list[dict]) -> list[str]:
    return [p["image_url"] for p in posts if p.get("is_carousel_item")]


# --- resuming ------------------------------------------------------------------------

def test_children_carried_over_are_not_built_again(db, monkeypatch):
    """The whole point: a resumed attempt spends its one fetch on a new frame."""
    posts = _fake_ig(monkeypatch)
    ig.post_carousel(db, images=_images(4), caption="c", resume=["old0", "old1"])
    assert _children(posts) == ["https://example/u2.jpg", "https://example/u3.jpg"]


def test_the_parent_lists_carried_over_children_first(db, monkeypatch):
    """Order is the user's chosen slide order — dropping the resumed ones or appending
    them would reshuffle the carousel."""
    posts = _fake_ig(monkeypatch)
    ig.post_carousel(db, images=_images(4), caption="c", resume=["old0", "old1"])
    parent = next(p for p in posts if p.get("media_type") == "CAROUSEL")
    assert parent["children"] == "old0,old1,c1,c2"


def test_each_child_is_reported_as_soon_as_it_exists(db, monkeypatch):
    """on_child fires per child rather than at the end, so a failed attempt still
    leaves the frames it finished behind."""
    _fake_ig(monkeypatch)
    seen: list[list[str]] = []
    ig.post_carousel(db, images=_images(3), caption="c", on_child=lambda ids: seen.append(ids))
    assert seen == [["c1"], ["c1", "c2"], ["c1", "c2", "c3"]]


def test_more_children_than_frames_is_refused_permanently(db, monkeypatch):
    """Frames were removed from the group since the last attempt — the carried-over
    children describe a carousel that no longer exists, and retrying can't fix it."""
    _fake_ig(monkeypatch)
    with pytest.raises(ig.InstagramError) as e:
        ig.post_carousel(db, images=_images(2), caption="c", resume=["a", "b", "c"])
    assert e.value.permanent


# --- when a frame's fetch fails -------------------------------------------------------

def test_a_failed_child_is_not_re_offered_in_line(db, monkeypatch):
    """A carousel already retries at a better layer: the next attempt resumes from the
    children that landed, spaced by the backoff curve rather than by seconds. Re-offering
    the same URL in line duplicates that in the worst place. Three in-line attempts is
    right for a lone photo and wrong here."""
    posts = _fake_ig(monkeypatch, children_allowed=1)
    with pytest.raises(ig.InstagramError):
        ig.post_carousel(db, images=_images(3), caption="c")
    assert _children(posts) == ["https://example/u0.jpg", "https://example/u1.jpg"]


def test_a_fetch_failure_is_retryable(db, monkeypatch):
    """Meta's wording reads like a dead link. Treating it as permanent would fail every
    carousel outright."""
    _fake_ig(monkeypatch, children_allowed=1)
    with pytest.raises(ig.InstagramError) as e:
        ig.post_carousel(db, images=_images(3), caption="c")
    assert not e.value.permanent


def test_the_frames_that_landed_survive_the_failure(db, monkeypatch):
    _fake_ig(monkeypatch, children_allowed=2)
    seen: list[list[str]] = []
    with pytest.raises(ig.InstagramError):
        ig.post_carousel(db, images=_images(5), caption="c", on_child=lambda ids: seen.append(ids))
    assert seen[-1] == ["c1", "c2"]


def test_an_eight_frame_carousel_finishes_across_attempts(db, monkeypatch):
    """End to end at the shape that exposed this: one frame per attempt, then publish."""
    built: list[str] = []
    for _ in range(9):
        posts = _fake_ig(monkeypatch, children_allowed=1)
        # Ids restart each attempt; namespace them so the assertion is meaningful.
        stamp = len(built)
        try:
            result = ig.post_carousel(
                db, images=_images(8), caption="c", resume=list(built),
                on_child=lambda ids: built.__setitem__(
                    slice(0, len(ids)), [f"{i}@{stamp}" if j >= len(built) else i
                                         for j, i in enumerate(ids)]
                ),
            )
        except ig.InstagramError:
            continue
        assert len(result["remote_id"]) > 0
        assert len(built) == 8
        return
    pytest.fail(f"carousel never finished; built {len(built)} of 8")


# --- the retry ledger -------------------------------------------------------------------

def _failure_row(db, err: Exception) -> PostPlatform:
    cred = PlatformCredential(id=uuid.uuid4().hex, platform="instagram")
    post = Post(id=uuid.uuid4().hex, status="posted", width=1000, height=1250)
    db.add_all([cred, post])
    db.commit()
    scheduler._record_platform_failure(db, post, cred, err)
    return db.get(PostPlatform, (post.id, cred.id))


def test_progress_does_not_spend_a_try(db):
    """Eight frames at one per attempt would exhaust a five-attempt budget before the
    carousel could finish. An attempt that got further than the last one earned its
    keep."""
    err = ig.InstagramError("could not be fetched")
    err.made_progress = True
    err.retry_after_seconds = scheduler.CAROUSEL_RESUME_SECONDS
    assert _failure_row(db, err).retry_count == 0


def test_a_failure_with_nothing_to_show_still_spends_one(db):
    """The budget has to keep bounding something that is merely failing over and over."""
    assert _failure_row(db, ig.InstagramError("nope")).retry_count == 1


def test_the_platforms_own_cooldown_wins_over_the_backoff_curve(db):
    """A platform that states its own wait knows better than the shared curve, which
    opens at one minute."""
    err = ig.InstagramError("could not be fetched")
    err.made_progress = True
    err.retry_after_seconds = scheduler.CAROUSEL_RESUME_SECONDS
    row = _failure_row(db, err)
    assert row.status == "pending"
    wait = (row.next_retry_at - scheduler.datetime.now(scheduler.timezone.utc).replace(tzinfo=None))
    assert 300 <= wait.total_seconds() <= scheduler.CAROUSEL_RESUME_SECONDS + 5
