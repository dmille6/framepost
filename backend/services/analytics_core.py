"""Cross-platform analytics helpers.

Everything reads `engagement_snapshots` — the append-only per-(post, platform) series
— so Flickr, Bluesky, Pixelfed, and Instagram are all first-class. The legacy
`flickr_engagement` table is left alone for back-compat but is no longer a source.

Three ideas drive the shape of this module:

1. WINDOWS, NOT LIFETIME. Posts scatter across 12 months, so a post from January has
   had six more months to accumulate than one from July. Ranking on lifetime totals
   just ranks by age. `window_value` picks the snapshot nearest `posted_at + window`
   (within a tolerance) so posts are compared at the same age.

2. RATES, NOT JUST COUNTS. "500 views" means little; "saves per 1k reached" says
   whether the photo made someone act. Rates are only computed where the platform
   actually reports the denominator.

3. MEDIANS + MINIMUM SAMPLES. One viral frame would otherwise crown a performer
   forever. Rankings use medians and carry their sample size so the UI can mute or
   flag thin evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AccountStat, EngagementSnapshot, PlatformCredential, Post, PostPlatform

PLATFORMS = ("flickr", "bluesky", "pixelfed", "instagram", "pinterest")

# Below this many posts a ranking is presented as provisional rather than a finding.
MIN_SAMPLE = 5

# How far from the ideal window boundary a snapshot may sit and still count. The sync
# runs daily, so a "48h" reading is realistically 36-60h old — label it honestly
# rather than pretending to precision we don't have.
WINDOW_TOLERANCE = timedelta(hours=18)

WINDOWS = {"24h": timedelta(hours=24), "48h": timedelta(hours=48), "7d": timedelta(days=7)}


@dataclass
class Sample:
    """One post's engagement on one platform, at a chosen age."""
    post: Post
    platform: str
    posted_at: datetime
    likes: int
    comments: int
    views: int
    reposts: int
    reach: int | None
    saves: int | None
    shares: int | None
    profile_visits: int | None
    follows: int | None

    @property
    def quality(self) -> float:
        """Per-platform weighting of the actions that actually signal reach-worthiness.

        Deliberately NOT one universal score: a Flickr fave and an Instagram save mean
        very different things. Saves/shares/follows outrank likes because they are
        costly actions; views and reach are context, not score.
        """
        if self.platform == "instagram":
            return (
                (self.saves or 0) * 5
                + (self.shares or 0) * 4
                + (self.follows or 0) * 6
                + (self.profile_visits or 0) * 2
                + self.comments * 3
                + self.likes
            )
        if self.platform == "flickr":
            return self.likes + self.comments * 3
        return self.likes + self.reposts * 3 + self.comments * 3

    def rate(self, metric: str) -> float | None:
        """Per-1000-reached rate for an action, or None when reach isn't reported."""
        if not self.reach:
            return None
        val = getattr(self, metric, None)
        if val is None:
            return None
        return round(val / self.reach * 1000, 1)


def _platform_posted_at(db: Session, post: Post, platform: str) -> datetime | None:
    """When THIS platform actually got the post. Post.posted_at is the Flickr fire
    time; a retried Bluesky post can be hours later, which would skew time-of-day
    analysis if we used the Flickr timestamp for everything."""
    if platform == "flickr":
        return post.posted_at
    row = db.execute(
        select(PostPlatform.posted_at)
        .join(PlatformCredential, PlatformCredential.id == PostPlatform.platform_id)
        .where(PostPlatform.post_id == post.id, PlatformCredential.platform == platform)
    ).scalars().first()
    return row or post.posted_at


def collect_samples(
    db: Session,
    *,
    platform: str | None = None,
    window: str | None = None,
) -> list[Sample]:
    """One Sample per (post, platform).

    window=None uses the latest snapshot (lifetime). A named window picks the snapshot
    closest to posted_at + window, skipping posts too young or without a nearby
    reading — so a 48h ranking never silently mixes in lifetime numbers.
    """
    q = select(EngagementSnapshot, Post).join(Post, Post.id == EngagementSnapshot.post_id)
    if platform:
        q = q.where(EngagementSnapshot.platform == platform)
    rows = db.execute(q.order_by(EngagementSnapshot.sampled_at)).all()

    by_key: dict[tuple[str, str], list[EngagementSnapshot]] = {}
    posts: dict[str, Post] = {}
    for snap, post in rows:
        by_key.setdefault((snap.post_id, snap.platform), []).append(snap)
        posts[post.id] = post

    delta = WINDOWS.get(window or "", None)
    out: list[Sample] = []
    for (post_id, plat), snaps in by_key.items():
        post = posts[post_id]
        posted = _platform_posted_at(db, post, plat)
        if not posted:
            continue
        if delta is None:
            chosen = snaps[-1]
        else:
            target = posted + delta
            candidates = [s for s in snaps if abs(s.sampled_at - target) <= WINDOW_TOLERANCE]
            if not candidates:
                continue
            chosen = min(candidates, key=lambda s: abs(s.sampled_at - target))
        out.append(
            Sample(
                post=post, platform=plat, posted_at=posted,
                likes=chosen.likes or 0, comments=chosen.comments_count or 0,
                views=chosen.views or 0, reposts=chosen.reposts or 0,
                reach=chosen.reach, saves=chosen.saves, shares=chosen.shares,
                profile_visits=chosen.profile_visits, follows=chosen.follows,
            )
        )
    return out


def _median(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(median(vals), 1) if vals else None


def summarize(samples: list[Sample]) -> dict[str, Any]:
    """Median-centred summary of a group of samples, plus its sample size."""
    return {
        "posts": len(samples),
        "median_quality": _median(s.quality for s in samples),
        "median_likes": _median(s.likes for s in samples),
        "median_comments": _median(s.comments for s in samples),
        "median_reach": _median(s.reach for s in samples),
        "median_views": _median(s.views for s in samples),
        "saves_per_1k": _median(s.rate("saves") for s in samples),
        "shares_per_1k": _median(s.rate("shares") for s in samples),
        "comments_per_1k": _median(s.rate("comments") for s in samples),
        "visits_per_1k": _median(s.rate("profile_visits") for s in samples),
        "follows_per_1k": _median(s.rate("follows") for s in samples),
        "low_sample": len(samples) < MIN_SAMPLE,
    }


def followers_near(db: Session, platform: str, when: datetime) -> int | None:
    """Follower count sampled closest to `when` — never today's number for an old
    post, which would understate how well early posts did against a smaller audience."""
    rows = db.execute(
        select(AccountStat).where(
            AccountStat.platform == platform, AccountStat.followers.is_not(None)
        )
    ).scalars().all()
    if not rows:
        return None
    target = when.date()
    return min(rows, key=lambda r: abs((r.stat_date - target).days)).followers
