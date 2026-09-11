"""Threading a carousel that overflows a platform's per-post image cap.

Bluesky and Pixelfed both take four images. A ten-frame set is threaded rather than
truncated — losing frames silently is worse than an extra post — and the caption goes on
the root only, because three top-level posts carrying the same caption and the same
hashtag block is the pattern that reads as bot output on a chronological feed.
"""
from pathlib import Path

import pytest

from services.platforms import bluesky, pixelfed


def _frames(n: int) -> list[tuple[Path, str | None]]:
    return [(Path(f"/tmp/f{i}.jpg"), f"alt {i}") for i in range(n)]


# --- Bluesky ------------------------------------------------------------------------

def _fake_bluesky(monkeypatch) -> list[dict]:
    """Record every post_photos call and hand back plausible strongRefs."""
    calls: list[dict] = []

    def _post_photos(db, *, frames, text, reply=None):
        i = len(calls)
        calls.append({"frames": frames, "text": text, "reply": reply})
        return {"at_uri": f"at://did/app.bsky.feed.post/{i}", "cid": f"cid{i}",
                "url": f"https://bsky.app/{i}"}

    monkeypatch.setattr(bluesky, "post_photos", _post_photos)
    return calls


def test_a_small_set_is_a_single_bluesky_post(db, monkeypatch):
    calls = _fake_bluesky(monkeypatch)
    bluesky.post_thread(db, frames=_frames(4), text="caption #burlesque")
    assert len(calls) == 1
    assert calls[0]["reply"] is None
    assert calls[0]["text"] == "caption #burlesque"


def test_ten_frames_thread_in_chunks_of_four(db, monkeypatch):
    calls = _fake_bluesky(monkeypatch)
    bluesky.post_thread(db, frames=_frames(10), text="caption")
    assert [len(c["frames"]) for c in calls] == [4, 4, 2]


def test_the_caption_rides_the_root_only(db, monkeypatch):
    """Repeating it on every continuation is the bot-output tell, and a reply is already
    attached to the root so restating it gains nothing."""
    calls = _fake_bluesky(monkeypatch)
    bluesky.post_thread(db, frames=_frames(9), text="caption #burlesque #nolaburlesque")
    assert calls[0]["text"] == "caption #burlesque #nolaburlesque"
    assert [c["text"] for c in calls[1:]] == ["", ""]


def test_every_reply_points_at_the_root_and_chains_to_its_parent(db, monkeypatch):
    """atproto wants root AND parent. Pointing parent at the root instead would make a
    flat fan of replies rather than a readable thread."""
    calls = _fake_bluesky(monkeypatch)
    bluesky.post_thread(db, frames=_frames(12), text="caption")
    root_uri = "at://did/app.bsky.feed.post/0"
    assert all(c["reply"]["root"]["uri"] == root_uri for c in calls[1:])
    assert calls[1]["reply"]["parent"]["uri"] == root_uri
    assert calls[2]["reply"]["parent"]["uri"] == "at://did/app.bsky.feed.post/1"


def test_the_thread_returns_the_root(db, monkeypatch):
    """post_platforms points at the root — that's where engagement accrues."""
    _fake_bluesky(monkeypatch)
    out = bluesky.post_thread(db, frames=_frames(10), text="caption")
    assert out["at_uri"].endswith("/0")


def test_bluesky_refuses_more_than_four_images_in_one_post(db):
    with pytest.raises(bluesky.BlueskyError) as e:
        bluesky.post_photos(db, frames=_frames(5), text="x")
    assert e.value.permanent


# --- Pixelfed -----------------------------------------------------------------------

def _fake_pixelfed(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _post_photos(db, *, frames, text, visibility="public", in_reply_to=None):
        i = len(calls)
        calls.append({"frames": frames, "text": text, "in_reply_to": in_reply_to})
        return {"remote_id": str(i), "url": f"https://pixelfed/{i}"}

    monkeypatch.setattr(pixelfed, "post_photos", _post_photos)
    return calls


def test_pixelfed_threads_in_chunks_of_four(db, monkeypatch):
    calls = _fake_pixelfed(monkeypatch)
    pixelfed.post_thread(db, frames=_frames(10), text="caption")
    assert [len(c["frames"]) for c in calls] == [4, 4, 2]
    assert calls[0]["text"] == "caption"
    assert [c["text"] for c in calls[1:]] == ["", ""]


def test_pixelfed_replies_chain_to_the_previous_status(db, monkeypatch):
    calls = _fake_pixelfed(monkeypatch)
    pixelfed.post_thread(db, frames=_frames(12), text="caption")
    assert calls[0]["in_reply_to"] is None
    assert calls[1]["in_reply_to"] == "0"
    assert calls[2]["in_reply_to"] == "1"


def test_pixelfed_returns_the_root(db, monkeypatch):
    _fake_pixelfed(monkeypatch)
    out = pixelfed.post_thread(db, frames=_frames(9), text="caption")
    assert out["remote_id"] == "0"


def test_pixelfed_refuses_more_than_four_images_in_one_status(db):
    with pytest.raises(pixelfed.PixelfedError) as e:
        pixelfed.post_photos(db, frames=_frames(5), text="x")
    assert e.value.permanent


# --- the form payload actually has to survive httpx ----------------------------------

def test_pixelfed_status_payload_encodes_repeated_media_ids(db, monkeypatch, tmp_path):
    """A list of (key, value) tuples looks like a reasonable way to repeat media_ids[],
    and httpx accepts it at call time and then dies at send time with "expected a
    bytes-like object, tuple found". Only building the request catches that, so this
    test does.
    """
    import httpx
    from PIL import Image

    class FakeResp:
        status_code = 200
        text = "{}"
        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    sent: dict = {}

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, path, headers=None, files=None, data=None):
            if path.endswith("/media"):
                return FakeResp({"id": f"m{len(sent)}"})
            sent["data"] = data
            return FakeResp({"id": "99", "url": "https://pixelfed/99"})

    class FakeRow:
        access_token = "tok"
        instance_url = "https://pixelfed.social"

    monkeypatch.setattr(pixelfed, "_load_credential", lambda db: FakeRow())
    monkeypatch.setattr(pixelfed, "decrypt_token", lambda t: "plain")
    monkeypatch.setattr(pixelfed, "_client", lambda url: FakeClient())

    frames = []
    for i in range(3):
        f = tmp_path / f"{i}.jpg"
        Image.new("RGB", (10, 10)).save(f, "JPEG")
        frames.append((f, f"alt {i}"))

    pixelfed.post_photos(db, frames=frames, text="caption")

    # The real check: httpx must be able to encode it, and produce one key per image.
    body = httpx.Request("POST", "https://x/", data=sent["data"]).read().decode()
    assert body.count("media_ids%5B%5D=") == 3
