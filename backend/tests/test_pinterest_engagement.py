"""Pinterest in the engagement sync and the target pickers.

analytics_core.PLATFORMS has listed Pinterest since the integration shipped, but
sync_all never sampled a pin, so every Pinterest analytics view was empty by
construction. And /api/platforms left Pinterest out of the targeting list, so a
connected Pinterest could not be chosen per post or in Bulk Edit -- worse, saving an
explicit target set from those pickers quietly dropped it.

No network: pinterest._client is replaced with a recorder that answers by path.
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from crypto import encrypt_token
from models import EngagementSnapshot, PlatformCredential, Post, PostPlatform
from services import comments as comments_svc
from services.platforms import pinterest

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet
    from config import settings
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _Recorder:
    """httpx stand-in: records every GET and answers from a per-pin table."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        self.calls.append((url, kw.get("params") or {}))
        for pin_id, resp in self.answers.items():
            if f"/pins/{pin_id}/analytics" in url:
                return resp(kw.get("params") or {}) if callable(resp) else resp
        raise AssertionError(f"unexpected request to {url}")


def _analytics(**summary):
    return _Resp(200, {"all": {"daily_metrics": [], "lifetime_metrics": {},
                               "summary_metrics": summary}})


def _cred(db, *, token="tok", platform="pinterest") -> PlatformCredential:
    c = PlatformCredential(
        id=uuid.uuid4().hex, platform=platform,
        access_token=encrypt_token(token) if token else None,
        # Far from expiry, so the sync never tries to refresh.
        token_expires=(datetime.now(timezone.utc) + timedelta(days=20)).replace(tzinfo=None),
    )
    db.add(c)
    db.commit()
    return c


def _pin(db, cred, *, remote_id="pin1", days_ago=3, status="posted") -> Post:
    posted = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(tzinfo=None)
    p = Post(id=uuid.uuid4().hex, status="posted", posted_at=posted)
    db.add(p)
    db.add(PostPlatform(post_id=p.id, platform_id=cred.id, status=status,
                        remote_id=remote_id, posted_at=posted))
    db.commit()
    return p


def _snaps(db):
    return db.query(EngagementSnapshot).filter_by(platform="pinterest").all()


def test_a_posted_pin_gets_a_snapshot_with_the_documented_mapping(db, monkeypatch):
    cred = _cred(db)
    post = _pin(db, cred)
    rec = _Recorder({"pin1": _analytics(
        IMPRESSION=240, SAVE=20, PIN_CLICK=37, OUTBOUND_CLICK=19,
        TOTAL_REACTIONS=12, TOTAL_COMMENTS=2, PROFILE_VISIT=5, USER_FOLLOW=1)})
    monkeypatch.setattr(pinterest, "_client", rec)

    out = comments_svc.sync_all(db)

    assert out["pinterest"]["sampled"] == 1
    assert out["pinterest"]["outbound_clicks"] == 19
    (s,) = _snaps(db)
    assert s.post_id == post.id
    assert (s.views, s.saves, s.likes, s.comments_count) == (240, 20, 12, 2)
    assert (s.profile_visits, s.follows) == (5, 1)
    assert s.reach is None, "Pinterest reports impressions, not unique reach"
    assert s.reposts == 0 and s.shares is None


def test_the_request_covers_the_pin_s_life_and_asks_for_pins_read_metrics(db, monkeypatch):
    cred = _cred(db)
    _pin(db, cred, days_ago=3)
    rec = _Recorder({"pin1": _analytics(IMPRESSION=1)})
    monkeypatch.setattr(pinterest, "_client", rec)

    comments_svc.sync_all(db)

    (url, params), = rec.calls
    assert url.endswith("/v5/pins/pin1/analytics")
    assert params["start_date"] == (TODAY - timedelta(days=3)).isoformat()
    assert params["end_date"] == TODAY.isoformat()
    assert set(params["metric_types"].split(",")) >= {"IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK"}
    assert "pins:read" in pinterest.SCOPES


def test_start_date_is_clamped_to_the_90_day_lookback():
    rec = _Recorder({"old": _analytics(IMPRESSION=1)})
    import services.platforms.pinterest as mod
    orig = mod._client
    mod._client = rec
    try:
        pinterest.fetch_pin_analytics("tok", "old", since=date(2020, 1, 1), today=TODAY)
    finally:
        mod._client = orig
    start = date.fromisoformat(rec.calls[0][1]["start_date"])
    assert (TODAY - start).days <= 90


def test_a_rejected_metric_set_falls_back_to_the_core_four(db, monkeypatch):
    cred = _cred(db)
    _pin(db, cred)

    def answer(params):
        if "USER_FOLLOW" in params["metric_types"]:
            return _Resp(400, {"code": 1, "message": "Invalid metric"})
        return _analytics(IMPRESSION=50, SAVE=3)

    rec = _Recorder({"pin1": answer})
    monkeypatch.setattr(pinterest, "_client", rec)

    comments_svc.sync_all(db)

    assert len(rec.calls) == 2
    (s,) = _snaps(db)
    assert (s.views, s.saves) == (50, 3)
    assert s.profile_visits is None, "not asked for, so not reported -- not zero"


def test_a_deleted_pin_is_skipped_not_counted_as_an_error(db, monkeypatch):
    cred = _cred(db)
    _pin(db, cred, remote_id="gone")
    monkeypatch.setattr(pinterest, "_client", _Recorder({"gone": _Resp(404, {"message": "not found"})}))

    out = comments_svc.sync_all(db)

    assert out["pinterest"] == {"sampled": 0, "gone": 1, "errors": 0, "outbound_clicks": 0}
    assert _snaps(db) == []


def test_one_failing_pin_does_not_lose_the_others(db, monkeypatch):
    cred = _cred(db)
    _pin(db, cred, remote_id="bad")
    _pin(db, cred, remote_id="good")
    monkeypatch.setattr(pinterest, "_client", _Recorder({
        "bad": _Resp(500, {"message": "boom"}),
        "good": _analytics(IMPRESSION=9),
    }))

    out = comments_svc.sync_all(db)

    assert (out["pinterest"]["sampled"], out["pinterest"]["errors"]) == (1, 1)
    assert [s.views for s in _snaps(db)] == [9]


def test_no_op_when_pinterest_is_not_connected(db, monkeypatch):
    """Batch-1 rule: connected means the credential row holds an access token. A
    disconnected row keeps its history, and must not be sampled."""
    cred = _cred(db, token=None)
    _pin(db, cred)

    def never():
        raise AssertionError("no Pinterest request when disconnected")
    monkeypatch.setattr(pinterest, "_client", never)

    out = comments_svc.sync_all(db)

    assert out["pinterest"]["sampled"] == 0
    assert _snaps(db) == []


def test_no_op_with_no_pinterest_row_at_all(db, monkeypatch):
    monkeypatch.setattr(pinterest, "_client", lambda: (_ for _ in ()).throw(AssertionError("called")))
    out = comments_svc.sync_all(db)
    assert out["pinterest"] == {"sampled": 0, "gone": 0, "errors": 0, "outbound_clicks": 0}


def test_a_pinterest_failure_does_not_break_sync_all(db, monkeypatch):
    """A dead refresh token fails the Pinterest stage alone."""
    cred = _cred(db)
    _pin(db, cred)

    def dead(_db):
        raise pinterest.PinterestError("refresh failed", permanent=True)
    monkeypatch.setattr(pinterest, "analytics_token", dead)

    out = comments_svc.sync_all(db)

    assert out["pinterest"]["errors"] == 1
    assert "refresh failed" in out["pinterest"]["failed"]
    assert "flickr" in out and "instagram" in out


def test_response_parsing_tolerates_an_unexpected_app_type_key():
    body = {"WEB": {"summary_metrics": {"IMPRESSION": 7.0, "SAVE_RATE": 0.18}}}
    assert pinterest._pin_totals(body) == {"IMPRESSION": 7, "SAVE_RATE": 0}
    assert pinterest._pin_totals({}) == {}
    assert pinterest._pin_totals([]) == {}


# --- the targeting list --------------------------------------------------------------

def test_connected_pinterest_is_offered_as_a_target(db, monkeypatch):
    from routes import platforms as platforms_route
    from services.platforms import flickr

    monkeypatch.setattr(flickr, "current_status", lambda _db: {"connected": False})
    _cred(db)
    _cred(db, platform="bluesky")

    out = platforms_route.list_connected_platforms(db=db, _user=None)

    by_platform = {p["platform"]: p for p in out}
    assert "pinterest" in by_platform
    assert by_platform["pinterest"]["label"] == "Pinterest"


def test_a_disconnected_pinterest_is_not_offered(db, monkeypatch):
    from routes import platforms as platforms_route
    from services.platforms import flickr

    monkeypatch.setattr(flickr, "current_status", lambda _db: {"connected": False})
    _cred(db, token=None)

    out = platforms_route.list_connected_platforms(db=db, _user=None)
    assert [p["platform"] for p in out] == []
