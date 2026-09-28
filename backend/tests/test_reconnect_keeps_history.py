"""Reconnecting a platform must not erase what was published through it.

Every connect path used to delete the platform's credential row and insert a fresh one.
post_platforms.platform_id cascaded on delete, and production runs foreign_keys=ON, so
each reconnect silently deleted that platform's entire history: remote ids, permalinks,
and every pending retry. Instagram's 60-day token makes reconnecting routine. Pinterest
and Pixelfed did it before the OAuth round-trip had even started, so an abandoned
reconnect killed a working connection too.

These run with foreign keys enforced (conftest), which is what lets them see it.
"""
import json
import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from crypto import decrypt_token, encrypt_token
from models import PlatformCredential, Post, PostPlatform
from services import channel_health, preflight
from services.platforms import bluesky, credentials, flickr, instagram, pinterest, pixelfed


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """A throwaway key, so these run anywhere — not only where the real one is set."""
    from cryptography.fernet import Fernet
    from config import settings
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _Client:
    """Stands in for an httpx client: answers by path suffix."""

    def __init__(self, routes):
        self.routes = routes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _answer(self, path):
        for suffix, resp in self.routes.items():
            if path.endswith(suffix):
                return resp
        raise AssertionError(f"unexpected request to {path}")

    def get(self, path, **kw):
        return self._answer(path)

    def post(self, path, **kw):
        return self._answer(path)


def _cred(db, platform, **kw) -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform,
                           access_token=encrypt_token("old-token"), **kw)
    db.add(c)
    db.commit()
    return c


def _history(db, cred, *, n=2) -> list[str]:
    """One published row and one pending retry — the two things a cascade destroyed."""
    ids = []
    for i in range(n):
        p = Post(id=uuid.uuid4().hex, status="posted", posted_at=datetime(2026, 9, 1))
        db.add(p)
        db.flush()
        db.add(PostPlatform(
            post_id=p.id, platform_id=cred.id,
            status="posted" if i == 0 else "pending",
            remote_id=f"remote-{i}" if i == 0 else None,
            next_retry_at=None if i == 0 else datetime(2026, 9, 2),
        ))
        ids.append(p.id)
    db.commit()
    return ids


def _rows(db, cred_id) -> list[PostPlatform]:
    return db.query(PostPlatform).filter_by(platform_id=cred_id).all()


# --- the database refuses the old behaviour ----------------------------------------

def test_deleting_a_credential_with_history_is_refused(db):
    cred = _cred(db, "instagram")
    _history(db, cred)
    db.delete(cred)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert len(_rows(db, cred.id)) == 2


# --- reconnect keeps the row id ------------------------------------------------------

def test_instagram_reconnect_keeps_history_and_clears_flags(db, monkeypatch):
    cred = _cred(db, "instagram", account_name="dmp", default_target=0,
                 auth_status="reauth_required", auth_error="token dead")
    _history(db, cred)
    monkeypatch.setattr(instagram, "_client", lambda: _Client({
        "/me": _Resp(200, {"user_id": "1789", "username": "dmp", "account_type": "BUSINESS"}),
    }))

    new = instagram.connect(db, access_token="new-long-lived-token-xxxxxxxx")

    assert new.id == cred.id
    assert decrypt_token(new.access_token) == "new-long-lived-token-xxxxxxxx"
    assert new.auth_status == "ok" and new.auth_error is None
    assert new.default_target == 0          # the user's choice survives a token renewal
    rows = _rows(db, cred.id)
    assert {r.status for r in rows} == {"posted", "pending"}
    assert any(r.remote_id == "remote-0" for r in rows)


def test_bluesky_reconnect_keeps_history(db, monkeypatch):
    cred = _cred(db, "bluesky", account_name="dmp.bsky.social")
    _history(db, cred)
    monkeypatch.setattr(bluesky, "_create_session", lambda h, p, pds=bluesky.DEFAULT_PDS:
                        bluesky._Session(pds=pds, did="did:plc:x", handle="dmp.bsky.social",
                                         access_jwt="a", refresh_jwt="r"))

    new = bluesky.connect(db, handle="dmp.bsky.social", app_password="xxxx")

    assert new.id == cred.id
    assert len(_rows(db, cred.id)) == 2


def test_flickr_reauthorise_keeps_the_row(db, monkeypatch):
    cred = _cred(db, "flickr", account_name="Darrell")
    _history(db, cred)

    class FakeOAuth:
        def __init__(self, **kw): pass
        def fetch_access_token(self, url, verifier):
            return {"oauth_token": "t2", "oauth_token_secret": "s2", "fullname": "Darrell"}

    monkeypatch.setattr(flickr, "_require_app_keys", lambda: ("k", "s"))
    monkeypatch.setattr(flickr, "OAuth1Client", FakeOAuth)

    new = flickr.complete_authorize(oauth_token="rt", oauth_token_secret="rs",
                                    oauth_verifier="v", db=db)
    assert new.id == cred.id
    assert decrypt_token(new.refresh_token) == "s2"
    assert len(_rows(db, cred.id)) == 2


# --- OAuth: starting is harmless, finishing updates in place ------------------------

def _pinterest_setup(monkeypatch):
    monkeypatch.setattr(pinterest, "_require_app_keys", lambda: ("app", "secret"))
    monkeypatch.setattr(pinterest, "_client", lambda: _Client({
        "/oauth/token": _Resp(200, {"access_token": "pin-new", "refresh_token": "ref",
                                    "expires_in": 3600}),
        "/user_account": _Resp(200, {"id": "u1", "username": "dmp"}),
    }))


def test_an_abandoned_pinterest_reconnect_changes_nothing(db, monkeypatch):
    cred = _cred(db, "pinterest", account_name="dmp",
                 extra_json=json.dumps({"user_id": "u1", "default_board_id": "b9"}))
    _history(db, cred)
    _pinterest_setup(monkeypatch)

    pinterest.begin_connect(db, redirect_uri="http://fp/cb")   # ...and the user walks away

    db.expire_all()
    row = db.get(PlatformCredential, cred.id)
    assert decrypt_token(row.access_token) == "old-token"
    assert pinterest.current_status(db)["connected"] is True
    assert len(_rows(db, cred.id)) == 2


def test_pinterest_reconnect_keeps_row_and_default_board(db, monkeypatch):
    cred = _cred(db, "pinterest", account_name="dmp",
                 extra_json=json.dumps({"user_id": "u1", "default_board_id": "b9",
                                        "default_board_name": "Burlesque"}))
    _history(db, cred)
    _pinterest_setup(monkeypatch)

    _url, state = pinterest.begin_connect(db, redirect_uri="http://fp/cb")
    new = pinterest.complete_connect(db, code="c", state=state)

    assert new.id == cred.id
    assert decrypt_token(new.access_token) == "pin-new"
    extra = json.loads(new.extra_json)
    assert extra["default_board_id"] == "b9"
    assert "pending_oauth" not in extra
    assert pinterest.current_status(db)["pending"] is False
    assert len(_rows(db, cred.id)) == 2


def test_pinterest_callback_with_the_wrong_state_leaves_the_connection(db, monkeypatch):
    cred = _cred(db, "pinterest", account_name="dmp")
    _pinterest_setup(monkeypatch)
    pinterest.begin_connect(db, redirect_uri="http://fp/cb")
    with pytest.raises(pinterest.PinterestError):
        pinterest.complete_connect(db, code="c", state="forged")
    assert decrypt_token(db.get(PlatformCredential, cred.id).access_token) == "old-token"


def test_pixelfed_first_connect_then_reconnect_keeps_one_row(db, monkeypatch):
    routes = {
        "/api/v1/apps": _Resp(200, {"client_id": "cid", "client_secret": "csec"}),
        "/oauth/token": _Resp(200, {"access_token": "pf-1"}),
        "/api/v1/accounts/verify_credentials": _Resp(200, {"id": "9", "acct": "dmp"}),
    }
    monkeypatch.setattr(pixelfed, "_client", lambda instance: _Client(routes))

    _u, state = pixelfed.begin_connect(db, instance_url="pixelfed.social", redirect_uri="cb")
    assert pixelfed.current_status(db) ["connected"] is False
    assert pixelfed.current_status(db)["pending"] is True
    first = pixelfed.complete_connect(db, code="c", state=state)
    _history(db, first)

    routes["/oauth/token"] = _Resp(200, {"access_token": "pf-2"})
    _u, state = pixelfed.begin_connect(db, instance_url="pixelfed.social", redirect_uri="cb")
    # Mid-flow, the live connection still works.
    assert pixelfed.current_status(db)["connected"] is True
    second = pixelfed.complete_connect(db, code="c", state=state)

    assert second.id == first.id
    assert decrypt_token(second.access_token) == "pf-2"
    assert db.query(PlatformCredential).filter_by(platform="pixelfed").count() == 1
    assert len(_rows(db, first.id)) == 2
    with pytest.raises(pixelfed.PixelfedError):     # a replayed callback finds nothing
        pixelfed.complete_connect(db, code="c", state=state)


# --- disconnect: tokens go, history stays, and nothing treats it as connected -------

@pytest.mark.parametrize("module,platform", [
    (instagram, "instagram"), (bluesky, "bluesky"), (pixelfed, "pixelfed"),
    (pinterest, "pinterest"), (flickr, "flickr"),
])
def test_disconnect_keeps_history_and_reads_as_not_connected(db, module, platform):
    cred = _cred(db, platform, account_name="dmp", refresh_token=encrypt_token("r"),
                 extra_json=json.dumps({"app_password": "secret", "ig_user_id": "1"}))
    _history(db, cred)

    assert module.disconnect(db) is True

    db.expire_all()
    row = db.get(PlatformCredential, cred.id)
    assert row.access_token is None and row.refresh_token is None
    assert row.auth_status == credentials.DISCONNECTED
    assert "secret" not in (row.extra_json or "")
    assert module.current_status(db)["connected"] is False
    assert len(_rows(db, cred.id)) == 2
    assert module.disconnect(db) is False          # idempotent


def test_a_disconnected_channel_is_not_flagged_or_targeted(db):
    cred = _cred(db, "instagram", account_name="dmp")
    credentials.disconnect(db, "instagram")

    assert channel_health.flag_reauth(db, "instagram", "dead") is False
    assert channel_health.broken_channels(db) == []
    post = Post(id=uuid.uuid4().hex, status="pending")
    assert "instagram" not in preflight.targets_for(post, [db.get(PlatformCredential, cred.id)])


def test_fanout_skips_a_disconnected_platform_without_touching_its_rows(db, monkeypatch):
    from services import scheduler

    cred = _cred(db, "instagram", account_name="dmp")
    credentials.disconnect(db, "instagram")
    post = Post(id=uuid.uuid4().hex, status="posted")
    db.add(post)
    db.commit()
    called = []
    monkeypatch.setattr(scheduler, "_post_to_platform", lambda *a, **k: called.append(a))

    scheduler.fanout_to_platforms(db, post, fired_at=datetime(2026, 9, 1))

    assert called == []
    assert _rows(db, cred.id) == []
