"""Pure-logic tests for the scheduling core: fuzz bounds, engagement-learned hours."""
import uuid
from datetime import datetime, timedelta, timezone

from models import AppConfig, EngagementSnapshot, Post
from routes.schedule import (
    _LEARNED_HOUR_COUNT,
    _MIN_HOUR_SAMPLES,
    _MIN_LEARNED_POSTS,
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


def _ig_post(db, posted_at_utc: datetime, *, quality: int) -> Post:
    """A published Instagram post with a snapshot at the 7-day mark.

    The window picks the reading nearest posted_at + 7d, so the snapshot has to sit
    there -- a lifetime reading is exactly what this ranking stopped using.
    """
    p = Post(id=uuid.uuid4().hex, status="posted", posted_at=posted_at_utc)
    db.add(p)
    db.add(EngagementSnapshot(
        post_id=p.id, platform="instagram",
        sampled_at=posted_at_utc + timedelta(days=7),
        likes=quality, comments_count=0,
    ))
    return p


def _flickr_post(db, posted_at_utc: datetime, *, likes: int) -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted", posted_at=posted_at_utc)
    db.add(p)
    db.add(EngagementSnapshot(
        post_id=p.id, platform="flickr",
        sampled_at=posted_at_utc + timedelta(days=7),
        likes=likes, comments_count=0,
    ))
    return p


_BASE = datetime(2026, 5, 1)


def _fill(db, hour: int, n: int, *, quality: int, day0: int = 1):
    """n posts on consecutive days at `hour`. Stepping with timedelta rather than
    incrementing the day number, which overflows the month past 31."""
    start = _BASE + timedelta(days=day0 - 1)
    for i in range(n):
        d = start + timedelta(days=i)
        _ig_post(db, d.replace(hour=hour, minute=5), quality=quality)


def test_learned_hours_defaults_when_no_history(db):
    hours, learned, n = _learned_popular_hours(db)
    assert hours == _POPULAR_HOURS
    assert learned is False
    assert n == 0


def test_flickr_engagement_does_not_decide_instagram_hours(db):
    """The original defect. Flickr has orders of magnitude more engagement rows, so
    pooling platforms made this ranking Flickr's -- presented as advice for a dialog
    whose output goes to Instagram."""
    for i in range(_MIN_LEARNED_POSTS + 10):
        _flickr_post(db, datetime(2026, 5, 1, 3, 0, 0) + timedelta(days=i), likes=9999)
    db.commit()

    hours, learned, n = _learned_popular_hours(db)
    assert n == 0, "Flickr samples must not count toward the Instagram ranking"
    assert learned is False
    assert hours == _POPULAR_HOURS


def test_too_few_instagram_posts_returns_defaults(db):
    """A ranking drawn from a handful of posts is noise wearing a number, so the
    function says it does not know instead of dressing it up."""
    _fill(db, 15, _MIN_HOUR_SAMPLES + 1, quality=500)
    db.commit()

    hours, learned, n = _learned_popular_hours(db)
    assert 0 < n < _MIN_LEARNED_POSTS
    assert learned is False
    assert hours == _POPULAR_HOURS


def test_learned_hours_ranks_once_there_is_enough(db):
    _fill(db, 15, 20, quality=500, day0=1)
    _fill(db, 20, 20, quality=1, day0=1)
    db.commit()

    hours, learned, n = _learned_popular_hours(db)
    assert learned is True
    assert n >= _MIN_LEARNED_POSTS
    assert hours[0] == 15, "the better-performing hour should lead"
    assert 20 in hours
    assert len(hours) == _LEARNED_HOUR_COUNT


def test_learned_hours_clamps_to_waking_window(db):
    """A strong 1 AM bucket must still be excluded -- the configured 8:00-23:00 window
    beats the raw data."""
    _fill(db, 1, 20, quality=9999, day0=1)
    _fill(db, 15, 20, quality=10, day0=1)
    db.commit()

    hours, learned, _ = _learned_popular_hours(db)
    assert 1 not in hours


def test_learned_hours_respects_min_samples_per_hour(db):
    """One viral 3 AM post must not drag 3 AM in, even with plenty of data elsewhere."""
    _fill(db, 15, 35, quality=10, day0=1)
    _ig_post(db, (_BASE + timedelta(days=60)).replace(hour=3), quality=9999)
    db.commit()

    hours, _, _ = _learned_popular_hours(db)
    assert 3 not in hours


def test_learned_hours_converts_to_the_configured_timezone(db):
    db.add(AppConfig(key="timezone", value="America/Chicago"))
    # 15:00 UTC in May = 10:00 CDT.
    _fill(db, 15, 35, quality=100, day0=1)
    db.commit()

    hours, learned, _ = _learned_popular_hours(db)
    assert learned is True
    assert hours[0] == 10, "local hour, not the UTC 15"


def test_ranking_uses_the_seven_day_reading_not_the_lifetime_total(db):
    """Lifetime totals partly measure age: an older post has had longer to accumulate,
    so ranking on them ranks by how long ago something was posted. Each post here has a
    modest 7-day reading and a huge later one; only the 7-day figures should count."""
    # Hour 15: weak at 7 days, enormous by now.
    for i in range(20):
        d = _BASE + timedelta(days=i)
        p = Post(id=uuid.uuid4().hex, status="posted", posted_at=d.replace(hour=15))
        db.add(p)
        db.add(EngagementSnapshot(post_id=p.id, platform="instagram",
                                  sampled_at=d.replace(hour=15) + timedelta(days=7),
                                  likes=1, comments_count=0))
        db.add(EngagementSnapshot(post_id=p.id, platform="instagram",
                                  sampled_at=d.replace(hour=15) + timedelta(days=200),
                                  likes=100_000, comments_count=0))
    # Hour 20: strong at 7 days, never revisited.
    for i in range(20):
        d = _BASE + timedelta(days=i)
        p = Post(id=uuid.uuid4().hex, status="posted", posted_at=d.replace(hour=20))
        db.add(p)
        db.add(EngagementSnapshot(post_id=p.id, platform="instagram",
                                  sampled_at=d.replace(hour=20) + timedelta(days=7),
                                  likes=500, comments_count=0))
    db.commit()

    hours, learned, _ = _learned_popular_hours(db)
    assert learned is True
    assert hours[0] == 20, "ranked on lifetime totals rather than the 7-day reading"


def test_a_post_too_young_for_a_seven_day_reading_is_not_counted(db):
    """Otherwise a post published this morning votes on posting times."""
    _fill(db, 15, 35, quality=10)
    for i in range(10):
        d = _BASE + timedelta(days=100 + i)
        p = Post(id=uuid.uuid4().hex, status="posted", posted_at=d.replace(hour=22))
        db.add(p)
        db.add(EngagementSnapshot(post_id=p.id, platform="instagram",
                                  sampled_at=d.replace(hour=22) + timedelta(hours=2),
                                  likes=9999, comments_count=0))
    db.commit()

    hours, _, n = _learned_popular_hours(db)
    assert n == 35, "a 2-hour-old reading is not a 7-day reading"
    assert 22 not in hours
