"""An age-window figure must come from a post that lived through that age.

collect_samples picks the snapshot nearest posted_at + window, within a tolerance.
The tolerance is 18 hours because readings are taken daily -- tightening it would
empty the window rather than sharpen it. But the tolerance alone also let in posts
that had not lived through the window at all: a 6-hour-old post with a 6-hour reading
sits within 18 hours of the 24-hour mark, so it counted as a 24-hour figure. The
docstring claimed those were skipped. Nothing skipped them.

Since "nearest" can be hours off the mark, the sample now carries how old the reading
actually is, and the summary reports the median, so a 24h figure sourced from a 19h
reading says so instead of rounding itself into a claim.
"""
import uuid
from datetime import datetime, timedelta, timezone

from models import EngagementSnapshot, Post
from services import analytics_core as ac


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _post_with_reading(db, *, age_of_post, age_of_reading, likes=10, platform="flickr"):
    posted = _utcnow() - age_of_post
    p = Post(id=uuid.uuid4().hex, status="posted", posted_at=posted)
    db.add(p)
    db.add(EngagementSnapshot(post_id=p.id, platform=platform,
                              sampled_at=posted + age_of_reading,
                              likes=likes, comments_count=0))
    db.flush()
    return p


def test_a_post_younger_than_the_window_is_excluded(db):
    """The reported defect: 6 hours old, counted as a 24-hour number."""
    _post_with_reading(db, age_of_post=timedelta(hours=6), age_of_reading=timedelta(hours=6))
    db.commit()
    assert ac.collect_samples(db, platform="flickr", window="24h") == []


def test_a_post_old_enough_is_included(db):
    _post_with_reading(db, age_of_post=timedelta(days=3), age_of_reading=timedelta(hours=24))
    db.commit()
    assert len(ac.collect_samples(db, platform="flickr", window="24h")) == 1


def test_the_readings_real_age_is_reported(db):
    """Daily sampling means "24h" is usually a nearby reading, not that exact hour."""
    _post_with_reading(db, age_of_post=timedelta(days=3), age_of_reading=timedelta(hours=19))
    db.commit()
    s = ac.collect_samples(db, platform="flickr", window="24h")[0]
    assert s.age_hours == 19.0
    assert ac.summarize([s])["median_age_hours"] == 19.0


def test_a_reading_outside_the_tolerance_is_not_used(db):
    """Better no number than a lifetime number wearing a 24h label."""
    _post_with_reading(db, age_of_post=timedelta(days=30), age_of_reading=timedelta(days=20))
    db.commit()
    assert ac.collect_samples(db, platform="flickr", window="24h") == []


def test_lifetime_still_takes_the_latest_reading_regardless_of_age(db):
    _post_with_reading(db, age_of_post=timedelta(hours=2), age_of_reading=timedelta(hours=1))
    db.commit()
    s = ac.collect_samples(db, platform="flickr", window=None)
    assert len(s) == 1
    assert s[0].age_hours is None, "lifetime has no window age to report"
