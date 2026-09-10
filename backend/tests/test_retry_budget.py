"""The attempt budget and the backoff schedule have to agree.

They are two independent fields in Settings. When max_attempts outran the schedule,
next_retry_at() returned None for the surplus attempts while the count was still under
the max — so the row stayed 'pending' with no retry timer, and the scanner (which
requires a timer) never looked at it again. The post sat on Flickr, missing from
Instagram, with nothing marked failed for the health banner to surface.
"""
from models import AppConfig
from services import retry


def _cfg(db, **pairs):
    for k, v in pairs.items():
        row = db.get(AppConfig, k)
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit()


def test_max_attempts_cannot_outrun_the_schedule(db):
    """The exact live configuration that produced the stuck post: 7 attempts against a
    five-step schedule."""
    _cfg(db, retry_max_attempts="7", retry_backoff_minutes="1,5,15,60,240")
    assert retry.max_attempts(db) == 5


def test_max_attempts_is_honoured_when_the_schedule_can_time_it(db):
    _cfg(db, retry_max_attempts="7", retry_backoff_minutes="1,5,15,60,240,360,480")
    assert retry.max_attempts(db) == 7


def test_a_shorter_budget_than_the_schedule_is_left_alone(db):
    """Capping is one-directional — fewer attempts than steps is a normal choice."""
    _cfg(db, retry_max_attempts="3", retry_backoff_minutes="1,5,15,60,240")
    assert retry.max_attempts(db) == 3


def test_every_attempt_inside_the_budget_can_be_scheduled(db):
    """The invariant that makes a stuck row impossible: for any count below the max
    there is a real next-retry time, so exhaustion is always reported rather than
    silently leaving a row pending and unreachable."""
    for attempts, schedule in (("7", "1,5,15,60,240"), ("3", "1,5"), ("20", "1")):
        _cfg(db, retry_max_attempts=attempts, retry_backoff_minutes=schedule)
        cap = retry.max_attempts(db)
        for n in range(1, cap):
            assert retry.next_retry_at(db, n) is not None, (attempts, schedule, n)


def test_a_broken_schedule_falls_back_rather_than_capping_to_nothing(db):
    _cfg(db, retry_max_attempts="7", retry_backoff_minutes="not,minutes")
    assert retry.max_attempts(db) == len(retry.DEFAULT_BACKOFF)
