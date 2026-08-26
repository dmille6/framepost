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

from models import (
    AccountStat, EngagementSnapshot, Performer, PlatformCredential, Post,
    PostCollaborator, PostPlatform,
)

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


# --- collaborator lift -------------------------------------------------------------
#
# Instagram does not tell us whether a collaboration invite was accepted: the
# `collaborators` edge exists only on the Facebook-Login API surface, and this install
# uses Instagram Login (see services/platforms/instagram.py). So acceptance is not a
# recorded fact anywhere in this codebase.
#
# What we can measure is the consequence. An accepted collab publishes the photo to the
# performer's followers too, which shows up as materially higher reach and saves; a
# declined or ignored invite leaves the post performing exactly like a solo post.
# Median lift over several posts is therefore a usable proxy for "does this performer
# actually accept" — and it is the thing you actually care about either way, since an
# accepted collab that reaches nobody is worth no more than a declined one.
#
# It is a proxy, not a measurement. Callers must present it as such.

@dataclass
class CollabLift:
    performer_id: str | None
    display_name: str
    handle: str
    posts: int
    median_quality: float
    median_reach: float | None
    lift: float | None          # ratio vs solo-post baseline; None when baseline is thin
    handle_status: str
    provisional: bool           # too few posts to lean on


def collab_lift(db: Session, *, window: str | None = "7d") -> dict[str, Any]:
    """Rank tagged performers by how much better your posts do when they are credited.

    Compares each performer's collab posts against the median SOLO post at the same
    age, so a performer who only appears in your strongest shots doesn't automatically
    look like a distribution win.
    """
    samples = {s.post.id: s for s in collect_samples(db, platform="instagram", window=window)}

    sent = list(db.execute(
        select(PostCollaborator).where(PostCollaborator.status == "sent")
    ).scalars())
    collab_post_ids = {c.post_id for c in sent}

    solo = [s for s in samples.values() if s.post.id not in collab_post_ids]
    baseline = median([s.quality for s in solo]) if len(solo) >= MIN_SAMPLE else None

    names = {
        p.id: p for p in db.execute(select(Performer)).scalars()
    }

    grouped: dict[str, list[Any]] = {}
    meta: dict[str, PostCollaborator] = {}
    for c in sent:
        smp = samples.get(c.post_id)
        if not smp:
            continue  # no engagement reading yet at this age
        key = c.performer_id or f"handle:{c.handle.lower()}"
        grouped.setdefault(key, []).append(smp)
        meta.setdefault(key, c)

    rows: list[CollabLift] = []
    for key, smps in grouped.items():
        c = meta[key]
        perf = names.get(c.performer_id) if c.performer_id else None
        reaches = [s.reach for s in smps if s.reach]
        med_q = median([s.quality for s in smps])
        rows.append(CollabLift(
            performer_id=c.performer_id,
            display_name=(perf.display_name if perf else c.handle),
            handle=c.handle,
            posts=len(smps),
            median_quality=round(med_q, 1),
            median_reach=round(median(reaches), 1) if reaches else None,
            lift=round(med_q / baseline, 2) if baseline else None,
            handle_status=(perf.handle_status if perf else "ok"),
            provisional=len(smps) < MIN_SAMPLE,
        ))

    rows.sort(key=lambda r: (r.lift if r.lift is not None else -1, r.posts), reverse=True)

    collab_smps = [s for pid, s in samples.items() if pid in collab_post_ids]
    return {
        "window": window,
        "basis": (
            "Instagram does not report whether a collaboration invite was accepted, so "
            "this ranks performers by how your posts actually performed when they were "
            "credited — an accepted collab shows up as extra reach, a declined one "
            "looks like a normal post."
        ),
        "solo_posts": len(solo),
        "solo_median_quality": round(baseline, 1) if baseline else None,
        "collab_posts": len(collab_smps),
        "collab_median_quality": (
            round(median([s.quality for s in collab_smps]), 1) if collab_smps else None
        ),
        "min_sample": MIN_SAMPLE,
        "performers": [r.__dict__ for r in rows],
    }
