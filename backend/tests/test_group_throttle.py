"""Per-group submission pacing.

`daily_limit` sat unread in the schema from 0001 until 0025 -- the worker submitted
every due row on every pass. The `error 5: Photo limit reached` rows already in
post_groups are the cost of that: Flickr treats the refusal as permanent, so an
unpaced worker doesn't just get delayed, it destroys the submission.

These tests pin the two properties that make a large group roster safe: the worker
never exceeds a group's published throttle, and waiting on a throttle is not an
error -- it must not spend the retry budget that real failures need.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from models import Group, Post, PostGroup
from services import group_throttle, scheduler

# Anchored to the real clock, not a literal date: the worker reads the wall clock
# itself, so a hardcoded NOW would drift out of every rolling window and time-bomb
# this file the day after it was written.
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _group(db, *, limit=None, period="day", name="G") -> Group:
    g = Group(id=uuid.uuid4().hex, flickr_group_id="1@N01", name=name,
              daily_limit=limit, limit_period=period, no_watermark=0, default_enabled=0)
    db.add(g)
    db.flush()
    return g


def _post(db, *, posted_at=NOW, photo_id="p1") -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted",
             flickr_photo_id=photo_id, posted_at=posted_at)
    db.add(p)
    db.flush()
    return p


def _pending(db, group, post) -> PostGroup:
    pg = PostGroup(id=uuid.uuid4().hex, post_id=post.id, group_id=group.id,
                   status="pending", retry_count=0)
    db.add(pg)
    db.flush()
    return pg


def _already_submitted(db, group, when) -> PostGroup:
    pg = PostGroup(id=uuid.uuid4().hex, post_id=_post(db).id, group_id=group.id,
                   status="submitted", submitted_at=when, retry_count=0)
    db.add(pg)
    db.flush()
    return pg


# --------------------------------------------------------------------------
# quota arithmetic
# --------------------------------------------------------------------------

def test_group_without_a_limit_is_unlimited_not_exhausted(db):
    """None and 0 are opposite answers; conflating them would stop every submission."""
    g = _group(db, limit=None)
    for _ in range(50):
        _already_submitted(db, g, NOW - timedelta(minutes=1))
    assert group_throttle.remaining(db, g, NOW) is None


def test_zero_limit_is_treated_as_unlimited(db):
    assert group_throttle.remaining(db, _group(db, limit=0), NOW) is None


def test_remaining_counts_submissions_in_the_window(db):
    g = _group(db, limit=5)
    for _ in range(2):
        _already_submitted(db, g, NOW - timedelta(hours=1))
    assert group_throttle.remaining(db, g, NOW) == 3


def test_window_is_rolling_so_old_submissions_age_out(db):
    """A calendar day would let a full quota fire at 23:59 and again at 00:01."""
    g = _group(db, limit=2)
    _already_submitted(db, g, NOW - timedelta(hours=25))  # outside
    _already_submitted(db, g, NOW - timedelta(hours=23))  # inside
    assert group_throttle.remaining(db, g, NOW) == 1


def test_remaining_floors_at_zero_when_over_quota(db):
    g = _group(db, limit=1)
    for _ in range(4):
        _already_submitted(db, g, NOW - timedelta(hours=1))
    assert group_throttle.remaining(db, g, NOW) == 0


def test_other_groups_do_not_consume_this_groups_quota(db):
    a, b = _group(db, limit=2, name="A"), _group(db, limit=2, name="B")
    for _ in range(2):
        _already_submitted(db, b, NOW - timedelta(hours=1))
    assert group_throttle.remaining(db, a, NOW) == 2


def test_pending_and_failed_rows_do_not_consume_quota(db):
    g = _group(db, limit=2)
    _pending(db, g, _post(db))
    db.add(PostGroup(id=uuid.uuid4().hex, post_id=_post(db).id, group_id=g.id,
                     status="failed", submitted_at=NOW - timedelta(hours=1)))
    db.flush()
    assert group_throttle.remaining(db, g, NOW) == 2


@pytest.mark.parametrize("period,inside,outside", [
    ("day", timedelta(hours=23), timedelta(hours=25)),
    ("week", timedelta(days=6), timedelta(days=8)),
    ("month", timedelta(days=29), timedelta(days=31)),
])
def test_each_period_measures_its_own_window(db, period, inside, outside):
    g = _group(db, limit=10, period=period)
    _already_submitted(db, g, NOW - inside)
    _already_submitted(db, g, NOW - outside)
    assert group_throttle.remaining(db, g, NOW) == 9


def test_unknown_period_falls_back_to_a_day(db):
    g = _group(db, limit=10, period="fortnight")
    assert group_throttle.period_delta(g) == timedelta(days=1)


def test_next_slot_is_when_the_oldest_submission_ages_out(db):
    g = _group(db, limit=1, period="week")
    oldest = NOW - timedelta(days=2)
    _already_submitted(db, g, oldest)
    _already_submitted(db, g, NOW - timedelta(days=1))
    assert group_throttle.next_slot_at(db, g, NOW) == oldest + timedelta(days=7)


def test_next_slot_is_now_when_nothing_is_in_the_window(db):
    assert group_throttle.next_slot_at(db, _group(db, limit=1), NOW) == NOW


# --------------------------------------------------------------------------
# the worker
# --------------------------------------------------------------------------

@pytest.fixture()
def worker(db, monkeypatch):
    """Run submit_due_groups against the test session, with Flickr stubbed out."""
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    calls: list[dict] = []

    def fake_rest_call(_db, method, **kw):
        calls.append({"method": method, **kw})
        return None

    monkeypatch.setattr(scheduler.flickr, "rest_call", fake_rest_call)
    return calls


def test_worker_stops_at_the_throttle(db, worker):
    g = _group(db, limit=2)
    for i in range(5):
        _pending(db, g, _post(db, posted_at=NOW - timedelta(hours=i), photo_id=f"p{i}"))
    db.commit()

    scheduler.submit_due_groups()

    assert len(worker) == 2, "worker exceeded the group's published throttle"
    statuses = [r.status for r in db.query(PostGroup).all()]
    assert statuses.count("submitted") == 2
    assert statuses.count("pending") == 3


def test_throttled_rows_keep_their_retry_budget(db, worker):
    """Waiting on a throttle is not a failure; spending retries on it would
    exhaust the budget a genuine error needs."""
    g = _group(db, limit=1)
    for i in range(3):
        _pending(db, g, _post(db, posted_at=NOW - timedelta(hours=i), photo_id=f"p{i}"))
    db.commit()

    scheduler.submit_due_groups()

    deferred = db.query(PostGroup).filter(PostGroup.status == "pending").all()
    assert len(deferred) == 2
    assert all(r.retry_count == 0 for r in deferred)
    assert all(r.error_message is None for r in deferred)
    assert all(r.next_retry_at is not None for r in deferred), "deferred without a wake-up time"


def test_unlimited_group_drains_completely(db, worker):
    g = _group(db, limit=None)
    for i in range(6):
        _pending(db, g, _post(db, posted_at=NOW - timedelta(hours=i), photo_id=f"p{i}"))
    db.commit()

    scheduler.submit_due_groups()

    assert len(worker) == 6
    assert all(r.status == "submitted" for r in db.query(PostGroup).all())


def test_quota_is_per_group_not_shared(db, worker):
    a, b = _group(db, limit=1, name="A"), _group(db, limit=1, name="B")
    for g in (a, b):
        for i in range(2):
            _pending(db, g, _post(db, posted_at=NOW - timedelta(hours=i), photo_id=f"p{i}"))
    db.commit()

    scheduler.submit_due_groups()

    assert len(worker) == 2, "each group should have spent exactly one slot"


def test_backlog_drains_oldest_post_first(db, worker):
    g = _group(db, limit=1)
    old = _post(db, posted_at=NOW - timedelta(days=3), photo_id="oldest")
    new = _post(db, posted_at=NOW, photo_id="newest")
    _pending(db, g, new)
    _pending(db, g, old)
    db.commit()

    scheduler.submit_due_groups()

    assert [c["photo_id"] for c in worker] == ["oldest"]


def test_prior_submissions_count_against_the_current_pass(db, worker):
    """The quota is a window, not a per-pass allowance -- a worker that reset each
    tick would submit `limit` every minute."""
    g = _group(db, limit=2)
    _already_submitted(db, g, NOW - timedelta(minutes=5))
    _already_submitted(db, g, NOW - timedelta(minutes=6))
    _pending(db, g, _post(db, photo_id="blocked"))
    db.commit()

    scheduler.submit_due_groups()

    assert worker == [], "spent quota was ignored"


# --------------------------------------------------------------------------
# reading throttles from Flickr instead of maintaining them by hand
# --------------------------------------------------------------------------

def _rest(payload: dict):
    """Stand in for flickr.rest_call, returning one <group><throttle/></group>."""
    import xml.etree.ElementTree as ET

    def call(_db, _method, **_kw):
        root = ET.Element("rsp")
        g = ET.SubElement(root, "group")
        if payload:
            ET.SubElement(g, "throttle", **{k: str(v) for k, v in payload.items()})
        return root
    return call


def test_sync_reads_the_real_throttle(db):
    g = _group(db, limit=2, period="day", name="Guild")
    db.commit()
    changes = group_throttle.sync_throttles(db, rest_call=_rest({"count": 10, "mode": "week"}))
    assert changes == [("Guild", "2/day", "10/week")]
    assert (g.daily_limit, g.limit_period) == (10, "week")


def test_sync_clears_the_limit_when_the_group_has_none(db):
    g = _group(db, limit=2, name="Open")
    db.commit()
    group_throttle.sync_throttles(db, rest_call=_rest({"mode": "none"}))
    assert g.daily_limit is None
    assert group_throttle.remaining(db, g, NOW) is None


def test_sync_reports_nothing_when_already_correct(db):
    _group(db, limit=5, period="day")
    db.commit()
    assert group_throttle.sync_throttles(db, rest_call=_rest({"count": 5, "mode": "day"})) == []


def test_sync_survives_an_unreachable_group(db):
    g = _group(db, limit=3, name="Down")
    db.commit()

    def boom(_db, _m, **_kw):
        raise RuntimeError("flickr down")

    assert group_throttle.sync_throttles(db, rest_call=boom) == []
    assert g.daily_limit == 3, "a failed lookup must not erase a known throttle"


def test_unknown_mode_falls_back_to_a_day_not_unlimited(db):
    """Conservative by construction: a new Flickr throttle string must never be
    read as 'no limit'."""
    g = _group(db, limit=1, name="Odd")
    db.commit()
    group_throttle.sync_throttles(db, rest_call=_rest({"count": 4, "mode": "fortnight"}))
    assert (g.daily_limit, g.limit_period) == (4, "day")


def test_lifetime_cap_counts_every_submission_ever(db):
    g = _group(db, limit=3, period="ever")
    assert group_throttle.period_delta(g) is None
    _already_submitted(db, g, NOW - timedelta(days=900))
    _already_submitted(db, g, NOW - timedelta(days=2))
    assert group_throttle.remaining(db, g, NOW) == 1


def test_lifetime_cap_defers_rather_than_failing(db):
    g = _group(db, limit=1, period="ever")
    _already_submitted(db, g, NOW - timedelta(days=900))
    assert group_throttle.remaining(db, g, NOW) == 0
    assert group_throttle.next_slot_at(db, g, NOW) > NOW
