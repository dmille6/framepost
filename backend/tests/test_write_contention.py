"""Two settings that only matter on the day something else goes wrong.

Through September the nightly engagement sync held one write transaction across every
HTTP call it made, and the per-minute heartbeat and 5-minute disk sampler died with
`database is locked` two or three times a night — 36 tracebacks in the week to
2026-09-26. WAL does not queue writers, and with no busy timeout SQLite fails the loser
immediately. Nothing was lost, but a real fault would have been invisible in that noise.

The companion change is that sync_all commits per platform instead of once at the end,
so the lock is held in short spans rather than for the whole sync.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services import comments as comments_svc
from services.platforms import instagram as ig


# --- the pragma ---------------------------------------------------------------------

def test_engine_sets_a_busy_timeout():
    """A fresh connection from the app's own engine must be willing to wait for a write
    lock. Asserted through the engine, not by reading database.py, so deleting the pragma
    line fails here."""
    from database import engine

    with engine.connect() as conn:
        timeout_ms = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert timeout_ms >= 5000, f"busy_timeout is {timeout_ms}ms — a contended write will fail instantly"


def test_a_second_writer_waits_instead_of_failing(tmp_path):
    """The actual behaviour the pragma buys, demonstrated on a real file DB: holding a
    write lock and then writing from another connection raises without a timeout and
    succeeds with one."""
    path = tmp_path / "contend.db"
    holder = sqlite3.connect(str(path), isolation_level=None)
    holder.execute("PRAGMA journal_mode = WAL")
    holder.execute("CREATE TABLE t (x INTEGER)")
    holder.execute("BEGIN IMMEDIATE")          # take the write lock and keep it

    impatient = sqlite3.connect(str(path), timeout=0)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        impatient.execute("INSERT INTO t VALUES (1)")
    impatient.close()

    patient = sqlite3.connect(str(path), timeout=0.3)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        patient.execute("INSERT INTO t VALUES (2)")   # still fails, but only after waiting
    patient.close()

    holder.execute("COMMIT")
    freed = sqlite3.connect(str(path), timeout=5)
    freed.execute("INSERT INTO t VALUES (3)")          # lock released, no error
    freed.commit()
    assert freed.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    freed.close()
    holder.close()


# --- per-stage commits --------------------------------------------------------------

def test_one_failing_platform_does_not_discard_the_others(db, monkeypatch):
    """A single trailing commit meant an exception anywhere threw away every reading taken
    before it. Each stage now commits on its own."""
    calls: list[str] = []

    def ok(name):
        def _run(*a, **k):
            calls.append(name)
            return {"sampled": 1, "errors": 0}
        return _run

    def boom(*a, **k):
        calls.append("pixelfed")
        raise RuntimeError("pixelfed exploded")

    monkeypatch.setattr(comments_svc, "_sync_flickr", ok("flickr"))
    monkeypatch.setattr(comments_svc, "_sync_bluesky", ok("bluesky"))
    monkeypatch.setattr(comments_svc, "_sync_pixelfed", boom)
    monkeypatch.setattr(comments_svc, "_sync_instagram", ok("instagram"))
    monkeypatch.setattr(comments_svc, "sync_instagram_reels", ok("reels"))
    monkeypatch.setattr(comments_svc, "sync_instagram_account_stats", ok("account"))

    out = comments_svc.sync_all(db)

    # The explosion is recorded, and every other platform still ran and reported.
    assert out["pixelfed"]["errors"] == 1
    assert "exploded" in out["pixelfed"]["failed"]
    for name in ("flickr", "bluesky", "instagram", "instagram_reels", "instagram_account"):
        assert out[name]["sampled"] == 1, f"{name} was lost to pixelfed's failure"
    assert calls.count("instagram") == 1        # kept going past the failure


# --- refresh leeway ------------------------------------------------------------------

def _cred(expires_in_days: float):
    class Row:
        access_token = "tok"
        token_expires = (datetime.now(timezone.utc)
                         + timedelta(days=expires_in_days)).replace(tzinfo=None)
    return Row()


def test_refresh_starts_well_before_the_last_week(monkeypatch):
    """The 2026-09-26 finding: a 7-day leeway gave the whole IG pipeline one real chance
    at the one refresh it depends on. 20 days out must now be inside the window."""
    fired: list[bool] = []
    monkeypatch.setattr(ig, "_refresh", lambda db, row: fired.append(True))
    ig._maybe_refresh(None, _cred(20))
    assert fired == [True]


def test_a_freshly_issued_token_is_left_alone(monkeypatch):
    """Still no pointless daily churn on a token with weeks of life."""
    fired: list[bool] = []
    monkeypatch.setattr(ig, "_refresh", lambda db, row: fired.append(True))
    ig._maybe_refresh(None, _cred(55))
    assert fired == []


def test_leeway_leaves_room_for_many_daily_attempts():
    """The point of the change is the number of retries, so assert that directly."""
    assert ig.REFRESH_LEEWAY >= timedelta(days=14)
    assert ig.REFRESH_LEEWAY < ig.TOKEN_LIFETIME
