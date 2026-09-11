"""Bluesky permalinks survive a handle change.

Handles are mutable on Bluesky; DIDs are not. account_name stores whatever the handle was
at connect time and _load_session rebuilds every session from it, so after a rename each
permalink FramePost recorded pointed at an account that no longer existed. It stayed
invisible because nothing reads its own links back — all 102 stored URLs were dead before
this was noticed, and the only symptom was an engagement number nobody could explain.
"""
import uuid

from models import PlatformCredential
from services.platforms import bluesky


def _cred(db, name="old-name.bsky.social"):
    c = PlatformCredential(id=uuid.uuid4().hex, platform="bluesky", account_name=name)
    db.add(c)
    db.commit()
    return c


def _session(did="did:plc:abc", handle="old-name.bsky.social"):
    return bluesky._Session(pds="https://bsky.social", did=did, handle=handle,
                            access_jwt="a", refresh_jwt="r")


def test_a_renamed_account_corrects_the_stored_name(db, monkeypatch):
    c = _cred(db)
    monkeypatch.setattr(bluesky, "_current_handle", lambda did, fb: "new-name.bsky.social")
    live = bluesky._sync_handle(db, c, _session())
    assert live == "new-name.bsky.social"
    assert c.account_name == "new-name.bsky.social", "the correction must persist"


def test_an_unchanged_handle_writes_nothing(db, monkeypatch):
    c = _cred(db)
    monkeypatch.setattr(bluesky, "_current_handle", lambda did, fb: "old-name.bsky.social")
    assert bluesky._sync_handle(db, c, _session()) == "old-name.bsky.social"
    assert c.account_name == "old-name.bsky.social"


def test_a_lookup_failure_keeps_the_stored_name(db, monkeypatch):
    """The appview being unreachable must not blank the handle or fail the publish —
    a slightly stale permalink beats a broken post."""
    def _boom(*a, **kw):
        raise OSError("appview unreachable")

    monkeypatch.setattr(bluesky.http_client, "client", _boom)
    c = _cred(db)
    assert bluesky._sync_handle(db, c, _session()) == "old-name.bsky.social"
    assert c.account_name == "old-name.bsky.social"


def test_the_permalink_uses_the_corrected_handle(db, monkeypatch):
    monkeypatch.setattr(bluesky, "_current_handle", lambda did, fb: "new-name.bsky.social")
    c = _cred(db)
    handle = bluesky._sync_handle(db, c, _session())
    url = bluesky._public_post_url(handle, "at://did:plc:abc/app.bsky.feed.post/3kxyz")
    assert url == "https://bsky.app/profile/new-name.bsky.social/post/3kxyz"


def test_a_did_that_resolves_to_nothing_falls_back(db, monkeypatch):
    """getProfile 400s for a handle that no longer exists — exactly what happened here."""
    class R:
        status_code = 400
        text = "Profile not found"
        def json(self): return {}

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **kw): return R()

    monkeypatch.setattr(bluesky.http_client, "client", lambda **kw: C())
    assert bluesky._current_handle("did:plc:abc", "fallback.bsky.social") == "fallback.bsky.social"
