"""Pure-logic tests for the scheduling core: fuzz bounds, engagement-learned hours."""
import uuid
from datetime import datetime, timedelta, timezone

from models import AppConfig, EngagementSnapshot, Post
from routes.schedule import (
    _LEARNED_HOUR_COUNT,
    _MIN_HOUR_SAMPLES,
    _POPULAR_HOURS,
    _apply_fuzz,
    _learned_popular_hours,
)


def test_apply_fuzz_bounds():
    base = datetime(2026, 6, 1, 10, 0, 0)
    for _ in range(200):
        out = _apply_fuzz(base, 5)
        delta = out - base
        assert timedelta(0) <= delta < timedelta(minutes=6)
        assert out.hour == 10  # fuzz never crosses out of the chosen hour + 5min window


def test_apply_fuzz_zero_is_identity():
    base = datetime(2026, 6, 1, 10, 0, 0)
    assert _apply_fuzz(base, 0) == base


def _mk_post(db, posted_at_utc: datetime) -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted", posted_at=posted_at_utc)
    db.add(p)
    return p


def _snap(db, post: Post, platform: str, likes: int, comments: int = 0):
    db.add(EngagementSnapshot(
        post_id=post.id, platform=platform,
        sampled_at=datetime(2026, 8, 1, 12, 0, 0),
        likes=likes, comments_count=comments,
    ))


def test_learned_hours_defaults_when_no_history(db):
    hours, learned, n = _learned_popular_hours(db)
    assert hours == _POPULAR_HOURS
    assert learned is False
    assert n == 0


def test_learned_hours_ranks_high_engagement_hour_first(db):
    # 15:00 UTC posts (high engagement) and 20:00 UTC posts (low), enough samples each.
    for i in range(_MIN_HOUR_SAMPLES):
        p = _mk_post(db, datetime(2026, 5, 1 + i, 15, 2, 0))
        _snap(db, p, "flickr", likes=50, comments=10)
    for i in range(_MIN_HOUR_SAMPLES):
        p = _mk_post(db, datetime(2026, 5, 10 + i, 20, 2, 0))
        _snap(db, p, "flickr", likes=1)
    db.commit()

    hours, learned, n = _learned_popular_hours(db)
    assert learned is True
    assert n == _MIN_HOUR_SAMPLES * 2
    # No timezone configured → UTC fallback: hours are the raw UTC hours.
    assert hours[0] == 15
    assert 20 in hours
    assert len(hours) == _LEARNED_HOUR_COUNT  # topped up from defaults


def test_learned_hours_respects_min_samples(db):
    # One viral 3 AM post must NOT drag 3 AM into the pool.
    p = _mk_post(db, datetime(2026, 5, 1, 3, 0, 0))
    _snap(db, p, "flickr", likes=9999, comments=500)
    db.commit()

    hours, learned, _ = _learned_popular_hours(db)
    assert learned is False
    assert 3 not in hours


def test_learned_hours_sums_platforms_and_converts_timezone(db):
    db.add(AppConfig(key="timezone", value="America/Chicago"))
    # 15:00 UTC in July = 10:00 CDT. Engagement split across two platforms per post.
    for i in range(_MIN_HOUR_SAMPLES):
        p = _mk_post(db, datetime(2026, 7, 1 + i, 15, 0, 0))
        _snap(db, p, "flickr", likes=10)
        _snap(db, p, "bluesky", likes=10, comments=5)
    db.commit()

    hours, learned, _ = _learned_popular_hours(db)
    assert learned is True
    assert hours[0] == 10  # local hour, not the UTC 15
