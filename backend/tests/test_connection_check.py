"""The pre-post connection check: warn an hour early, never block, never cry wolf."""
import json
import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from models import AppConfig, PlatformCredential, Post, PostEvent
from services import channel_health, connection_check as cc
from services.platforms import flickr

NOW = datetime(2026, 10, 1, 12, 0)


def _cred(db, platform, **kw):
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform, access_token="tok", **kw)
    db.add(c)
    db.commit()
    return c


def _post(db, *, in_minutes=30, targets=None):
    p = Post(id=uuid.uuid4().hex, status="pending",
             scheduled_at=NOW + timedelta(minutes=in_minutes),
             target_platforms=json.dumps(targets) if targets else None)
    db.add(p)
    db.commit()
    return p


@pytest.fixture()
def calls(monkeypatch):
    """Every verifier replaced by a recorder; tests set what each one does."""
    seen: list[str] = []
    behaviour: dict[str, BaseException | None] = {}

    def make(name):
        def verify(db, cred):
            seen.append(name)
            err = behaviour.get(name)
            if err is not None:
                raise err
        return verify

    monkeypatch.setattr(cc, "VERIFIERS", {n: make(n) for n in cc.VERIFIERS})
    return seen, behaviour


def test_checks_only_platforms_upcoming_posts_use(db, calls):
    seen, _ = calls
    _cred(db, "instagram")
    _cred(db, "bluesky")
    _post(db, targets=["instagram"])
    _post(db, in_minutes=300, targets=["bluesky"])       # outside the lookahead

    assert cc.run(db, now=NOW) == {"instagram": "ok"}
    assert seen == ["instagram"]


def test_one_call_per_platform_and_none_within_the_hour(db, calls):
    seen, _ = calls
    _cred(db, "instagram")
    for _ in range(3):
        _post(db, targets=["instagram"])
    _post(db, in_minutes=80, targets=["instagram"])      # still ahead at the recheck

    cc.run(db, now=NOW)
    assert seen == ["instagram"]
    assert cc.run(db, now=NOW + timedelta(minutes=15)) == {"instagram": "skipped"}
    assert seen == ["instagram"]
    cc.run(db, now=NOW + timedelta(minutes=61))
    assert seen == ["instagram", "instagram"]


def test_a_recent_successful_publish_counts_as_proof(db, calls):
    seen, _ = calls
    _cred(db, "instagram", last_success_at=NOW - timedelta(minutes=10))
    _post(db, targets=["instagram"])
    assert cc.run(db, now=NOW) == {"instagram": "skipped"}
    assert seen == []


def test_a_revoked_token_flags_reauth_and_alerts_once_a_day(db, calls):
    _seen, behaviour = calls
    cred = _cred(db, "instagram", default_target=1)
    post = _post(db, targets=["instagram"])
    behaviour["instagram"] = cc.CheckFailed(
        'instagram check HTTP 400: {"error": {"message": "Error validating access token", '
        '"code": 190}}', http_status=400, code=190, error_type="OAuthException")

    assert cc.run(db, now=NOW) == {"instagram": "reauth"}
    db.refresh(cred)
    assert cred.auth_status == "reauth_required"
    assert [c["platform"] for c in channel_health.broken_channels(db)] == ["instagram"]
    events = db.query(PostEvent).filter_by(post_id=post.id).all()
    assert [e.event_type for e in events] == ["instagram_connection_check_failed"]

    # Later the same day: already flagged, no further calls or alerts.
    assert cc.run(db, now=NOW + timedelta(hours=2)) == {}  # post now outside window
    _post(db, in_minutes=30 + 120, targets=["instagram"])
    assert cc.run(db, now=NOW + timedelta(hours=2)) == {"instagram": "already_flagged"}
    assert db.query(PostEvent).count() == 1


@pytest.mark.parametrize("err", [
    httpx.ConnectTimeout("timed out"),
    cc.CheckFailed("instagram check HTTP 503: upstream", http_status=503),
    cc.CheckFailed("instagram check HTTP 429: slow down", http_status=429),
])
def test_network_blips_never_flag_reauth(db, calls, err):
    _seen, behaviour = calls
    cred = _cred(db, "instagram")
    _post(db, targets=["instagram"])
    behaviour["instagram"] = err

    assert cc.run(db, now=NOW) == {"instagram": "transient"}
    db.refresh(cred)
    assert cred.auth_status == "ok"
    assert db.query(PostEvent).count() == 0


def test_a_flickr_transport_failure_is_transient_even_wrapped(db, calls):
    _seen, behaviour = calls
    _cred(db, "flickr")
    _post(db)                          # default targets include flickr
    try:
        raise flickr.FlickrError("REST transport failed") from httpx.ReadTimeout("x")
    except flickr.FlickrError as e:
        behaviour["flickr"] = e
    assert cc.run(db, now=NOW)["flickr"] == "transient"


def test_flickr_invalid_token_is_reauth(db, calls):
    _seen, behaviour = calls
    cred = _cred(db, "flickr")
    _post(db)
    behaviour["flickr"] = flickr.FlickrError("flickr error 98: Invalid auth token", code=98)
    assert cc.run(db, now=NOW)["flickr"] == "reauth"
    db.refresh(cred)
    assert cred.auth_status == "reauth_required"


def test_a_passing_check_does_not_clear_an_existing_flag(db, calls):
    """test.login passed throughout the Flickr delete-scope incident."""
    seen, _ = calls
    _cred(db, "flickr", auth_status="reauth_required", auth_error="no delete scope")
    _post(db)
    assert cc.run(db, now=NOW)["flickr"] == "already_flagged"
    assert "flickr" not in seen


def test_disconnected_platforms_are_left_alone(db, calls):
    seen, _ = calls
    c = _cred(db, "instagram")
    c.access_token = None
    db.commit()
    _post(db, targets=["instagram"])
    assert cc.run(db, now=NOW) == {}
    assert seen == []


def test_the_worker_job_survives_a_crashing_check(monkeypatch):
    from services import scheduler

    class Boom:
        def close(self): pass
        def rollback(self): pass

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: Boom())
    monkeypatch.setattr(cc, "run", lambda db: 1 / 0)
    scheduler.pre_post_connection_check()      # must not raise


def test_instagram_verifier_uses_get_me(db, monkeypatch):
    """The real verifier: one GET /me, and a 400 surfaces as CheckFailed."""
    from services.platforms import instagram

    seen = []

    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, params=None, headers=None):
            seen.append(path)
            return httpx.Response(400, json={"error": {"code": 190}})

    monkeypatch.setattr(instagram, "_client", lambda: Client())
    monkeypatch.setattr(cc, "decrypt_token", lambda t: t)
    cred = _cred(db, "instagram")
    with pytest.raises(cc.CheckFailed) as ei:
        cc._verify_instagram(db, cred)
    assert seen == ["/me"] and ei.value.http_status == 400
    assert cc.is_reauth("instagram", ei.value)[0] is True


# --- reading Meta's error codes, not its prose ---------------------------------------

def _ig(status, code, etype="OAuthException", subcode=None):
    return cc.CheckFailed(f"instagram check HTTP {status}: permission denied, expired?",
                          http_status=status, code=code, subcode=subcode, error_type=etype)


@pytest.mark.parametrize("err,reauth", [
    (_ig(400, 190), True),
    (_ig(400, 190, subcode=460), True),          # password changed
    (_ig(400, 102), True),                       # API session
    (_ig(400, 4), False),                        # app rate limit
    (_ig(400, 17), False),                       # user rate limit
    (_ig(400, 32), False),                       # page rate limit
    (_ig(400, 613), False),                      # custom rate limit
    (_ig(400, 10), False),                       # OAuthException, not about the token
    (_ig(403, None, etype=None), False),         # bare 403 with no code
    (_ig(401, None, etype=None), False),         # bare 401 with no code
])
def test_instagram_reauth_is_decided_by_error_code(err, reauth):
    assert cc.is_reauth("instagram", err)[0] is reauth


@pytest.mark.parametrize("platform,status,reauth", [
    ("pixelfed", 401, True), ("pixelfed", 403, False),
    ("pinterest", 401, True), ("pinterest", 403, False),
    ("bluesky", 401, True), ("bluesky", 400, False),
])
def test_other_platforms_only_treat_401_as_reauth(platform, status, reauth):
    err = cc.CheckFailed(f"{platform} check HTTP {status}: token scope expired",
                         http_status=status)
    assert cc.is_reauth(platform, err)[0] is reauth


def test_an_adapter_5xx_mentioning_expiry_is_not_reauth():
    from services.platforms import pinterest
    err = pinterest.PinterestError(
        "Pinterest token refresh failed (HTTP 503): token expired? upstream", permanent=False)
    assert cc.is_reauth("pinterest", err)[0] is False


def test_the_verifier_parses_metas_error_code(db, monkeypatch):
    from services.platforms import instagram

    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, path, params=None, headers=None):
            return httpx.Response(400, json={"error": {
                "message": "Application request limit reached", "type": "OAuthException",
                "code": 4}})

    monkeypatch.setattr(instagram, "_client", lambda: Client())
    monkeypatch.setattr(cc, "decrypt_token", lambda t: t)
    with pytest.raises(cc.CheckFailed) as ei:
        cc._verify_instagram(db, _cred(db, "instagram"))
    assert (ei.value.code, ei.value.error_type) == (4, "OAuthException")
    assert cc.is_reauth("instagram", ei.value)[0] is False
