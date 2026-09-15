"""Whether the work is reaching anyone -- as distinct from whether it lands.

Analytics answered "which photos did best", which silently assumes the audience is
fixed and the photo is the variable. On this account that assumption is wrong. A
sweep of the live data in September 2026 found a median post reaching about 9% of
followers while roughly 10% of the people reached pressed like. Those two numbers
say opposite things: the photographs are working and the distribution is not, and
nothing in the existing Analytics page could tell them apart, because every panel
divides by posts and none divides by audience.

Five measures, each chosen because it separates a cause we can act on from one we
cannot:

  reach rate        reach / followers at the time. Follower count is a vanity
                    denominator; this is the real one.
  like rate         likes / reach. Appeal among people who actually saw it. High
                    here with low reach rate means "show it to more people",
                    which is a completely different instruction from "shoot
                    better".
  decay             share of the 7-day reach already in by 24h and 48h. Decides
                    how long to wait before judging a post at all.
  follow conversion profile visits -> new followers. Measured because it was
                    assumed to be working and turned out to be ~0.
  cadence           reach rate on days with one post vs days with several. The
                    pilot data suggests the second post of a day costs the first;
                    this is the panel that would prove or kill that.

Medians throughout, and every figure carries its sample size: at 25 Instagram
posts most of these are indicative, not conclusive, and a UI that hides that
would invite exactly the over-reading this module exists to prevent.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AccountStat
from services import analytics_core as core

# Below this, a figure is shown as provisional. Same threshold as the rest of
# Analytics so one number does not look firmer than its neighbour.
MIN_SAMPLE = core.MIN_SAMPLE


def _median(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(median(vals), 1) if vals else None


# --- reach and like rates ----------------------------------------------------------

@dataclass
class RatePoint:
    post_id: str
    title: str | None
    posted_at: str
    reach: int
    followers: int
    reach_rate: float       # percent of followers reached
    likes: int
    like_rate: float | None  # percent of those reached who liked


def reach_rates(
    db: Session, *, platform: str = "instagram", window: str | None = "7d"
) -> dict[str, Any]:
    """Per-post reach as a share of the audience that existed when it was posted.

    Followers come from `followers_near`, not today's count: an early post measured
    against a later, larger audience would be marked down for having been early.
    """
    points: list[RatePoint] = []
    for s in core.collect_samples(db, platform=platform, window=window):
        if not s.reach:
            continue
        followers = core.followers_near(db, platform, s.posted_at)
        if not followers:
            continue
        points.append(RatePoint(
            post_id=s.post.id,
            title=s.post.title,
            posted_at=s.posted_at.isoformat(),
            reach=s.reach,
            followers=followers,
            reach_rate=round(100 * s.reach / followers, 1),
            likes=s.likes,
            like_rate=round(100 * s.likes / s.reach, 1) if s.reach else None,
        ))
    points.sort(key=lambda p: p.reach_rate, reverse=True)
    return {
        "platform": platform,
        "window": window,
        "posts": len(points),
        "low_sample": len(points) < MIN_SAMPLE,
        "median_reach_rate": _median(p.reach_rate for p in points),
        "median_like_rate": _median(p.like_rate for p in points),
        "best": [vars(p) for p in points[:5]],
        "worst": [vars(p) for p in points[-5:]][::-1],
    }


# --- decay -------------------------------------------------------------------------

def decay_curve(db: Session, *, platform: str = "instagram") -> dict[str, Any]:
    """How much of a post's first-week reach has arrived by 24h and by 48h.

    Computed per post and then medianed, rather than medianing the three windows
    separately: the latter would mix different sets of posts (a post can have a 24h
    reading and no usable 7d one) and quietly compare a number from one cohort against
    a number from another.
    """
    by_window = {
        w: {s.post.id: s.reach for s in core.collect_samples(db, platform=platform, window=w)
            if s.reach}
        for w in ("24h", "48h", "7d")
    }
    day1: list[float] = []
    day2: list[float] = []
    for post_id, final in by_window["7d"].items():
        if not final:
            continue
        if post_id in by_window["24h"]:
            day1.append(100 * by_window["24h"][post_id] / final)
        if post_id in by_window["48h"]:
            day2.append(100 * by_window["48h"][post_id] / final)
    return {
        "platform": platform,
        "posts": len(by_window["7d"]),
        "low_sample": len(day1) < MIN_SAMPLE,
        "pct_by_24h": _median(day1),
        "pct_by_48h": _median(day2),
        "n_24h": len(day1),
        "n_48h": len(day2),
    }


# --- follow conversion -------------------------------------------------------------

def follow_conversion(
    db: Session, *, platform: str = "instagram", days: int = 90
) -> dict[str, Any]:
    """Profile visits against the follower count they moved.

    Follower change is a day-over-day difference on `account_stats`, so it counts net
    growth: someone unfollowing cancels someone arriving. That is the honest number for
    "is the profile converting", even though it cannot separate a quiet day from a busy
    one with equal churn.
    """
    rows = db.execute(
        select(AccountStat)
        .where(AccountStat.platform == platform)
        .order_by(AccountStat.stat_date)
    ).scalars().all()
    if len(rows) < 2:
        return {"platform": platform, "days": 0, "low_sample": True,
                "median_profile_views": None, "median_new_followers": None,
                "conversion_pct": None}

    rows = rows[-(days + 1):]
    visits: list[int] = []
    gains: list[int] = []
    for prev, cur in zip(rows, rows[1:]):
        # Only consecutive days can be differenced; a gap means the sync missed a day
        # and the delta would silently span it.
        if (cur.stat_date - prev.stat_date).days != 1:
            continue
        if cur.followers is None or prev.followers is None:
            continue
        gains.append(cur.followers - prev.followers)
        if cur.profile_views is not None:
            visits.append(cur.profile_views)

    total_visits = sum(visits)
    total_gain = sum(g for g in gains if g > 0)
    return {
        "platform": platform,
        "days": len(gains),
        "low_sample": len(gains) < MIN_SAMPLE,
        "median_profile_views": _median(visits),
        "median_new_followers": _median(gains),
        # Share of profile visits that became a follow, over the whole span. Per-day
        # medians would both be small integers and round the answer to nothing.
        "conversion_pct": round(100 * total_gain / total_visits, 2) if total_visits else None,
    }


# --- posting cadence ---------------------------------------------------------------

def cadence(
    db: Session, *, platform: str = "instagram", window: str | None = "7d"
) -> dict[str, Any]:
    """Reach rate on single-post days versus days carrying more than one post.

    The question is whether a second post in a day takes reach from the first. This
    cannot answer it -- the days are not randomised, and a day with two posts may
    differ in other ways -- so it reports the two medians and their sample sizes and
    leaves the causal claim to an actual experiment.
    """
    by_day: dict[Any, list[float]] = defaultdict(list)
    for s in core.collect_samples(db, platform=platform, window=window):
        if not s.reach:
            continue
        followers = core.followers_near(db, platform, s.posted_at)
        if not followers:
            continue
        by_day[s.posted_at.date()].append(100 * s.reach / followers)

    single = [rates[0] for rates in by_day.values() if len(rates) == 1]
    multi = [r for rates in by_day.values() if len(rates) > 1 for r in rates]
    return {
        "platform": platform,
        "window": window,
        "single_post_days": len(single),
        "multi_post_posts": len(multi),
        "low_sample": min(len(single), len(multi)) < MIN_SAMPLE,
        "median_reach_rate_single": _median(single),
        "median_reach_rate_multi": _median(multi),
    }


def distribution(
    db: Session, *, platform: str = "instagram", window: str | None = "7d"
) -> dict[str, Any]:
    """Everything above, in one call — the page renders it as one panel."""
    return {
        "rates": reach_rates(db, platform=platform, window=window),
        "decay": decay_curve(db, platform=platform),
        "conversion": follow_conversion(db, platform=platform),
        "cadence": cadence(db, platform=platform, window=window),
    }
