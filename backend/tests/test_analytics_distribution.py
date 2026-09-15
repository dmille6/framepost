"""Audience-denominated analytics.

These exist because the rest of Analytics divides by posts, and a per-post number
cannot distinguish a photo nobody was shown from one people saw and ignored. The
remedies for those are opposite, so conflating them is worse than reporting
nothing.

The properties worth pinning are mostly about denominators: reach rate must use
the follower count that existed when the post went out (otherwise early posts are
punished for being early), decay must compare a post against itself (otherwise it
compares cohorts), and follow conversion must not difference across a missing day
of stats (otherwise a sync gap reads as a surge).
"""
import uuid
from datetime import datetime, timedelta, timezone

from models import AccountStat, EngagementSnapshot, Post
from services import analytics_distribution as adist


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _post(db, *, posted_days_ago: int, title="t") -> Post:
    when = _now() - timedelta(days=posted_days_ago)
    # analytics_core._platform_posted_at resolves a non-Flickr platform through
    # PostPlatform and falls back to Post.posted_at -- it never reads
    # posted_to_instagram_at. Setting only that column produces zero samples, silently.
    p = Post(
        id=uuid.uuid4().hex,
        status="posted",
        title=title,
        posted_at=when,
        posted_to_instagram_at=when,
    )
    db.add(p)
    db.flush()
    return p


def _snap(db, post, *, days_after: float, reach=None, likes=0):
    posted = post.posted_at
    db.add(EngagementSnapshot(
        post_id=post.id, platform="instagram",
        sampled_at=posted + timedelta(days=days_after),
        likes=likes, comments_count=0, views=0, reposts=0, reach=reach,
    ))
    db.flush()


def _followers(db, *, days_ago: int, followers: int, profile_views=None):
    db.add(AccountStat(
        platform="instagram",
        stat_date=(_now() - timedelta(days=days_ago)).date(),
        followers=followers, profile_views=profile_views,
    ))
    db.flush()


# --------------------------------------------------------------------------
# reach rate
# --------------------------------------------------------------------------

def test_reach_rate_is_a_share_of_followers(db):
    p = _post(db, posted_days_ago=10)
    _snap(db, p, days_after=7, reach=400, likes=40)
    _followers(db, days_ago=10, followers=4000)
    db.commit()

    out = adist.reach_rates(db)
    assert out["median_reach_rate"] == 10.0
    assert out["median_like_rate"] == 10.0


def test_reach_rate_uses_the_audience_that_existed_then(db):
    """A post from when the account was small must not be scored against today's
    follower count -- that marks down every early post for being early."""
    p = _post(db, posted_days_ago=100)
    _snap(db, p, days_after=7, reach=500, likes=0)
    _followers(db, days_ago=100, followers=1000)   # audience at the time
    _followers(db, days_ago=1, followers=8000)     # audience now
    db.commit()

    assert adist.reach_rates(db)["median_reach_rate"] == 50.0


def test_posts_without_reach_are_skipped_not_counted_as_zero(db):
    """Flickr and Bluesky report no reach. Treating that as 0 would drag the median to
    the floor and invent a distribution crisis."""
    good = _post(db, posted_days_ago=10)
    _snap(db, good, days_after=7, reach=400, likes=40)
    blind = _post(db, posted_days_ago=10)
    _snap(db, blind, days_after=7, reach=None, likes=5)
    _followers(db, days_ago=10, followers=4000)
    db.commit()

    out = adist.reach_rates(db)
    assert out["posts"] == 1
    assert out["median_reach_rate"] == 10.0


def test_no_follower_history_yields_no_rate_rather_than_a_guess(db):
    p = _post(db, posted_days_ago=10)
    _snap(db, p, days_after=7, reach=400, likes=40)
    db.commit()

    out = adist.reach_rates(db)
    assert out["posts"] == 0
    assert out["median_reach_rate"] is None


def test_thin_sample_is_flagged(db):
    p = _post(db, posted_days_ago=10)
    _snap(db, p, days_after=7, reach=400, likes=40)
    _followers(db, days_ago=10, followers=4000)
    db.commit()
    assert adist.reach_rates(db)["low_sample"] is True


# --------------------------------------------------------------------------
# decay
# --------------------------------------------------------------------------

def test_decay_compares_a_post_against_its_own_final_reach(db):
    p = _post(db, posted_days_ago=10)
    _snap(db, p, days_after=1, reach=550)
    _snap(db, p, days_after=2, reach=790)
    _snap(db, p, days_after=7, reach=1000)
    db.commit()

    out = adist.decay_curve(db)
    assert out["pct_by_24h"] == 55.0
    assert out["pct_by_48h"] == 79.0


def test_decay_ignores_a_post_with_no_seven_day_reading(db):
    """Without a denominator there is no share to report, and assuming the latest
    reading is final would score a two-day-old post as 100% delivered."""
    p = _post(db, posted_days_ago=1)
    _snap(db, p, days_after=1, reach=550)
    db.commit()

    out = adist.decay_curve(db)
    assert out["pct_by_24h"] is None
    assert out["n_24h"] == 0


# --------------------------------------------------------------------------
# follow conversion
# --------------------------------------------------------------------------

def test_conversion_divides_follower_gain_by_profile_views(db):
    for i, (f, v) in enumerate([(1000, None), (1010, 100), (1020, 100)]):
        _followers(db, days_ago=3 - i, followers=f, profile_views=v)
    db.commit()

    out = adist.follow_conversion(db)
    assert out["days"] == 2
    assert out["conversion_pct"] == 10.0


def test_conversion_counts_days_that_lost_followers(db):
    """Net, not gross. Summing only the days that gained would count arrivals and
    ignore departures, which reports a healthy conversion rate for an account that is
    treading water."""
    for i, (f, v) in enumerate([(1000, None), (1020, 100), (1000, 100)]):
        _followers(db, days_ago=3 - i, followers=f, profile_views=v)
    db.commit()

    out = adist.follow_conversion(db)
    assert out["days"] == 2
    # +20 then -20 is no growth at all, against 200 views.
    assert out["conversion_pct"] == 0.0


def test_conversion_never_reports_a_negative_rate(db):
    for i, (f, v) in enumerate([(1000, None), (990, 100), (980, 100)]):
        _followers(db, days_ago=3 - i, followers=f, profile_views=v)
    db.commit()
    assert adist.follow_conversion(db)["conversion_pct"] == 0.0


def test_conversion_does_not_difference_across_a_missing_day(db):
    """A gap in account_stats means the sync missed a day. Differencing across it
    attributes a week of growth to one day."""
    _followers(db, days_ago=10, followers=1000, profile_views=100)
    _followers(db, days_ago=1, followers=2000, profile_views=100)
    db.commit()

    out = adist.follow_conversion(db)
    assert out["days"] == 0
    assert out["conversion_pct"] is None


def test_conversion_with_no_history_is_not_an_error(db):
    out = adist.follow_conversion(db)
    assert out["low_sample"] is True
    assert out["conversion_pct"] is None


# --------------------------------------------------------------------------
# cadence
# --------------------------------------------------------------------------

def test_cadence_separates_single_post_days_from_crowded_ones(db):
    _followers(db, days_ago=10, followers=1000)
    _followers(db, days_ago=20, followers=1000)

    alone = _post(db, posted_days_ago=20)
    _snap(db, alone, days_after=7, reach=200)

    a = _post(db, posted_days_ago=10)
    b = _post(db, posted_days_ago=10)
    _snap(db, a, days_after=7, reach=100)
    _snap(db, b, days_after=7, reach=100)
    db.commit()

    out = adist.cadence(db)
    assert out["single_post_days"] == 1
    assert out["multi_post_posts"] == 2
    assert out["median_reach_rate_single"] == 20.0
    assert out["median_reach_rate_multi"] == 10.0


def test_distribution_bundles_all_four(db):
    db.commit()
    out = adist.distribution(db)
    assert set(out) == {"rates", "decay", "conversion", "cadence"}
