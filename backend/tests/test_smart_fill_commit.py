"""Smart Fill must save the schedule it showed you.

The dialog called the endpoint twice with byte-identical bodies, changing only
`confirm`. Both calls regenerated the proposal from scratch, and the second one's
result was what got written — so the dates reviewed and the dates saved were
unrelated. In scatter mode they differed by months; in sequential mode the day held
but jitter re-rolled the minute. A preview that cannot predict the write is not a
preview.

Committing is now a separate operation that takes the pairs it is given and either
writes them or says why it did not. It cannot generate a time, so it cannot generate
a different one.
"""
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from models import Post
from routes.schedule import (
    ConfirmSlot,
    SmartFillRequest,
    _apply_fuzz,
    smart_fill,
)

USER = types.SimpleNamespace(username="tester")


def _post(db, **kw) -> Post:
    p = Post(id=uuid.uuid4().hex, status="pending",
             title=kw.pop("title", "t"), original_filename="f.arw", **kw)
    db.add(p)
    db.flush()
    return p


def _req(**kw) -> SmartFillRequest:
    base = dict(post_ids=["x"], time_of_day="10:00", cadence_days=1,
                start_date="2030-01-01", skip_weekends=False, confirm=False)
    base.update(kw)
    return SmartFillRequest(**base)


def _future(days: int, hour: int = 12) -> datetime:
    d = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------
# the regression: previewed == persisted
# --------------------------------------------------------------------------

def test_confirm_writes_exactly_the_slots_it_was_given(db):
    a, b = _post(db), _post(db)
    db.commit()
    ta, tb = _future(3), _future(9)

    resp = smart_fill(_req(post_ids=[a.id, b.id], confirm=True, slots=[
        ConfirmSlot(post_id=a.id, scheduled_at=ta),
        ConfirmSlot(post_id=b.id, scheduled_at=tb),
    ]), db, USER)

    assert resp.scheduled == 2
    assert db.get(Post, a.id).scheduled_at == ta
    assert db.get(Post, b.id).scheduled_at == tb


def test_confirm_ignores_the_generator_inputs_entirely(db):
    """start_date/cadence/mode describe a proposal. Once the operator has chosen, they
    are irrelevant -- the old code re-ran them and overwrote the choice."""
    p = _post(db)
    db.commit()
    chosen = _future(200)

    smart_fill(_req(post_ids=[p.id], confirm=True, mode="random_scatter",
                    start_date="2030-06-15", cadence_days=7,
                    slots=[ConfirmSlot(post_id=p.id, scheduled_at=chosen)]), db, USER)

    assert db.get(Post, p.id).scheduled_at == chosen


def test_preview_writes_nothing(db):
    p = _post(db)
    db.commit()
    resp = smart_fill(_req(post_ids=[p.id], confirm=False), db, USER)
    assert resp.confirmed is False
    assert db.get(Post, p.id).scheduled_at is None


def test_confirm_without_slots_is_refused(db):
    """Better to fail loudly than to fall back to generating a fresh schedule, which is
    exactly the behaviour being removed."""
    p = _post(db)
    db.commit()
    with pytest.raises(HTTPException) as e:
        smart_fill(_req(post_ids=[p.id], confirm=True), db, USER)
    assert e.value.status_code == 400
    assert db.get(Post, p.id).scheduled_at is None


# --------------------------------------------------------------------------
# the calendar can move between preview and confirm
# --------------------------------------------------------------------------

def test_an_hour_taken_since_the_preview_is_reported_not_rerouted(db):
    """The old code silently picked a different date. Saying so is the point."""
    taken_at = _future(5)
    occupant = _post(db, title="already here")
    occupant.scheduled_at = taken_at
    p = _post(db)
    db.commit()

    resp = smart_fill(_req(post_ids=[p.id], confirm=True,
                           slots=[ConfirmSlot(post_id=p.id, scheduled_at=taken_at)]), db, USER)

    assert resp.scheduled == 0
    assert db.get(Post, p.id).scheduled_at is None
    assert "already here" in (resp.slots[0].skipped_reason or "")


def test_a_post_scheduled_elsewhere_since_preview_is_skipped(db):
    p = _post(db)
    p.scheduled_at = _future(2)
    db.commit()
    resp = smart_fill(_req(post_ids=[p.id], confirm=True,
                           slots=[ConfirmSlot(post_id=p.id, scheduled_at=_future(8))]), db, USER)
    assert resp.scheduled == 0
    assert "since the preview" in (resp.slots[0].skipped_reason or "")


def test_a_past_slot_is_refused(db):
    p = _post(db)
    db.commit()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    resp = smart_fill(_req(post_ids=[p.id], confirm=True,
                           slots=[ConfirmSlot(post_id=p.id, scheduled_at=past)]), db, USER)
    assert resp.scheduled == 0
    assert db.get(Post, p.id).scheduled_at is None


def test_two_slots_in_one_hour_within_one_request(db):
    a, b = _post(db), _post(db)
    db.commit()
    t = _future(4)
    resp = smart_fill(_req(post_ids=[a.id, b.id], confirm=True, slots=[
        ConfirmSlot(post_id=a.id, scheduled_at=t),
        ConfirmSlot(post_id=b.id, scheduled_at=t),
    ]), db, USER)
    assert resp.scheduled == 1
    assert resp.skipped == 1


def test_the_reported_count_matches_what_was_written(db):
    """`scheduled` used to be computed before persistence, so a slot the write loop
    skipped was still counted."""
    good = _post(db)
    gone = _post(db)
    gone.status = "posted"
    db.commit()
    resp = smart_fill(_req(post_ids=[good.id, gone.id], confirm=True, slots=[
        ConfirmSlot(post_id=good.id, scheduled_at=_future(6)),
        ConfirmSlot(post_id=gone.id, scheduled_at=_future(7)),
    ]), db, USER)
    written = db.query(Post).filter(Post.scheduled_at.is_not(None)).count()
    assert resp.scheduled == written == 1


# --------------------------------------------------------------------------
# jitter
# --------------------------------------------------------------------------

def test_fuzz_offsets_the_requested_minute_rather_than_replacing_it(db):
    """It used to `replace(minute=...)`, discarding the request: 10:30 with fuzz=10
    came back as 10:02. The existing bounds test only ever passed a :00 base, where
    replacing and adding give the same answer, so it could not see this."""
    base = datetime(2030, 6, 1, 10, 30, 0)
    for _ in range(200):
        out = _apply_fuzz(base, 10)
        assert out.minute >= 30, f"jitter moved the slot backwards: {out}"
        assert out.minute <= 40


def test_fuzz_never_leaves_the_reserved_hour(db):
    """The one-post-per-hour rule is enforced per hour bucket, so spilling over would
    let two posts share one."""
    base = datetime(2030, 6, 1, 10, 55, 0)
    for _ in range(200):
        out = _apply_fuzz(base, 30)
        assert out.hour == 10, f"jitter escaped the hour: {out}"
