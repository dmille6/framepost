"""Analytics — best times to post, group/tag performance. Read-only aggregations
over the flickr_engagement snapshot data.

All numbers are based on the *latest snapshot per post*. Older snapshots in
flickr_engagement are kept around so we could compute time-series later, but for v1
the analytics surface is "current totals per post, grouped by various dimensions."
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_session
from models import (
    AccountStat,
    AppConfig,
    FlickrEngagement,
    Group,
    Performer,
    PlatformCredential,
    Post,
    PostGroup,
    PostPerformer,
    User,
    Venue,
)
from routes.auth import current_user
from services import engagement
from services.platforms import flickr

router = APIRouter()


class TimeSlot(BaseModel):
    dow: int           # 0 = Sunday … 6 = Saturday (SQLite strftime %w)
    hour: int          # 0–23
    posts: int
    avg_views: float
    avg_faves: float
    avg_comments: float


class GroupStat(BaseModel):
    group_id: str
    name: str
    category: str | None
    submissions: int
    accepted: int
    failed: int
    avg_views: float
    avg_faves: float
    avg_comments: float


class TagStat(BaseModel):
    tag: str
    posts: int
    avg_views: float
    avg_faves: float
    avg_comments: float


class TopPost(BaseModel):
    post_id: str
    title: str | None
    flickr_url: str | None
    posted_at: datetime | None
    views: int
    faves: int
    comments: int


class AnalyticsOverview(BaseModel):
    posts_with_engagement: int
    total_views: int
    total_faves: int
    total_comments: int
    last_sync: str | None


def _latest_engagement_subquery():
    """Latest FlickrEngagement row per post_id."""
    return (
        select(
            FlickrEngagement.post_id,
            func.max(FlickrEngagement.sampled_at).label("latest"),
        )
        .group_by(FlickrEngagement.post_id)
        .subquery()
    )


def _latest_engagement_rows(db: Session):
    """(Post, FlickrEngagement) tuples for the latest engagement snapshot per post."""
    sub = _latest_engagement_subquery()
    return db.execute(
        select(Post, FlickrEngagement)
        .join(FlickrEngagement, FlickrEngagement.post_id == Post.id)
        .join(
            sub,
            (sub.c.post_id == FlickrEngagement.post_id)
            & (sub.c.latest == FlickrEngagement.sampled_at),
        )
        .where(Post.status.in_(["posted", "late"]), Post.posted_at.is_not(None))
    ).all()


@router.get("/overview", response_model=AnalyticsOverview)
def overview(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    rows = _latest_engagement_rows(db)
    total_v = sum(e.views for _, e in rows)
    total_f = sum(e.faves for _, e in rows)
    total_c = sum(e.comments for _, e in rows)
    last = db.execute(
        select(func.max(FlickrEngagement.sampled_at))
    ).scalar_one_or_none()
    return AnalyticsOverview(
        posts_with_engagement=len(rows),
        total_views=total_v,
        total_faves=total_f,
        total_comments=total_c,
        last_sync=last.isoformat() if last else None,
    )


@router.get("/best-times", response_model=list[TimeSlot])
def best_times(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Heatmap data: average engagement per (day-of-week, hour) bucket."""
    rows = _latest_engagement_rows(db)
    buckets: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    for post, eng in rows:
        if not post.posted_at:
            continue
        dow = int(post.posted_at.strftime("%w"))
        hour = post.posted_at.hour
        buckets[(dow, hour)].append((eng.views, eng.faves, eng.comments))
    out: list[TimeSlot] = []
    for (dow, hour), samples in sorted(buckets.items()):
        n = len(samples)
        out.append(
            TimeSlot(
                dow=dow,
                hour=hour,
                posts=n,
                avg_views=round(sum(s[0] for s in samples) / n, 2),
                avg_faves=round(sum(s[1] for s in samples) / n, 2),
                avg_comments=round(sum(s[2] for s in samples) / n, 2),
            )
        )
    return out


@router.get("/groups", response_model=list[GroupStat])
def groups(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Per-group performance ranking — submissions, accepted/failed counts, average
    engagement of posts that were submitted to this group."""
    rows = _latest_engagement_rows(db)
    eng_by_post: dict[str, FlickrEngagement] = {p.id: e for p, e in rows}

    pg_rows = db.execute(
        select(PostGroup, Group).join(Group, Group.id == PostGroup.group_id)
    ).all()
    by_group: dict[str, dict[str, Any]] = {}
    for pg, g in pg_rows:
        b = by_group.setdefault(
            g.id,
            {"name": g.name, "category": g.category, "submissions": 0, "accepted": 0, "failed": 0,
             "views": [], "faves": [], "comments": []},
        )
        b["submissions"] += 1
        if pg.status == "submitted" or pg.status == "accepted":
            b["accepted"] += 1
        elif pg.status in ("failed", "rejected"):
            b["failed"] += 1
        eng = eng_by_post.get(pg.post_id)
        if eng:
            b["views"].append(eng.views)
            b["faves"].append(eng.faves)
            b["comments"].append(eng.comments)

    out: list[GroupStat] = []
    for gid, b in by_group.items():
        n = len(b["views"]) or 1
        out.append(
            GroupStat(
                group_id=gid,
                name=b["name"],
                category=b["category"],
                submissions=b["submissions"],
                accepted=b["accepted"],
                failed=b["failed"],
                avg_views=round(sum(b["views"]) / n, 2) if b["views"] else 0.0,
                avg_faves=round(sum(b["faves"]) / n, 2) if b["faves"] else 0.0,
                avg_comments=round(sum(b["comments"]) / n, 2) if b["comments"] else 0.0,
            )
        )
    out.sort(key=lambda g: g.avg_faves, reverse=True)
    return out


@router.get("/tags", response_model=list[TagStat])
def tags(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    min_posts: int = Query(2, ge=1, le=50),
    limit: int = Query(40, ge=1, le=200),
):
    """Tag-engagement correlation. For each tag, average engagement of posts using it.
    Only includes tags that appear on at least `min_posts` posts (signal vs noise)."""
    rows = _latest_engagement_rows(db)
    bucket: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"posts": 0, "views": 0, "faves": 0, "comments": 0}
    )
    for post, eng in rows:
        for raw in (post.tags or "").split(","):
            t = raw.strip().lower()
            if not t:
                continue
            b = bucket[t]
            b["posts"] += 1
            b["views"] += eng.views
            b["faves"] += eng.faves
            b["comments"] += eng.comments

    out: list[TagStat] = []
    for tag, b in bucket.items():
        if b["posts"] < min_posts:
            continue
        n = b["posts"]
        out.append(
            TagStat(
                tag=tag,
                posts=n,
                avg_views=round(b["views"] / n, 2),
                avg_faves=round(b["faves"] / n, 2),
                avg_comments=round(b["comments"] / n, 2),
            )
        )
    out.sort(key=lambda t: t.avg_faves, reverse=True)
    return out[:limit]


@router.get("/top-posts", response_model=list[TopPost])
def top_posts(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    sort: str = Query("faves", pattern="^(views|faves|comments)$"),
    limit: int = Query(10, ge=1, le=100),
):
    rows = _latest_engagement_rows(db)
    rows_sorted = sorted(rows, key=lambda r: getattr(r[1], sort), reverse=True)[:limit]
    return [
        TopPost(
            post_id=p.id,
            title=p.title,
            flickr_url=p.flickr_url,
            posted_at=p.posted_at,
            views=e.views,
            faves=e.faves,
            comments=e.comments,
        )
        for p, e in rows_sorted
    ]


@router.post("/sync")
def trigger_sync(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    """Manual trigger — useful right after first connecting Flickr or to refresh on demand."""
    try:
        return engagement.sync(db)
    except flickr.FlickrError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Flickr error: {e}")
    except RuntimeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# -----------------------------------------------------------------------------
# Cross-platform analytics (0016). Everything below reads engagement_snapshots via
# services/analytics_core, so Instagram / Bluesky / Pixelfed are first-class rather
# than the Flickr-only view the endpoints above provide.
# -----------------------------------------------------------------------------

from services import analytics_core as ac  # noqa: E402


class PlatformSummary(BaseModel):
    platform: str
    connected: bool
    posts: int
    median_quality: float | None
    median_likes: float | None
    median_comments: float | None
    median_reach: float | None
    median_views: float | None
    saves_per_1k: float | None
    shares_per_1k: float | None
    comments_per_1k: float | None
    visits_per_1k: float | None
    follows_per_1k: float | None
    low_sample: bool


class LeaderboardRow(BaseModel):
    key: str
    label: str
    posts: int
    median_quality: float | None
    saves_per_1k: float | None
    comments_per_1k: float | None
    visits_per_1k: float | None
    lift: float | None          # vs. the median post on the same platform, as a ratio
    low_sample: bool
    last_posted: datetime | None


class AccountPoint(BaseModel):
    date: str
    followers: int | None
    reach: int | None
    profile_views: int | None
    accounts_engaged: int | None
    website_clicks: int | None


@router.get("/platforms", response_model=list[PlatformSummary])
def platform_summaries(
    window: str | None = Query(None, pattern="^(24h|48h|7d)$"),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Side-by-side platform comparison. Metrics a platform doesn't report come back
    null — the UI must show a dash, never a zero, or Bluesky looks like it has terrible
    reach when it simply has no such concept."""
    connected = {
        c.platform for c in db.execute(
            select(PlatformCredential).where(PlatformCredential.access_token.is_not(None))
        ).scalars()
    }
    samples = ac.collect_samples(db, window=window)
    out: list[PlatformSummary] = []
    for plat in ac.PLATFORMS:
        subset = [s for s in samples if s.platform == plat]
        if not subset and plat not in connected:
            continue
        out.append(PlatformSummary(platform=plat, connected=plat in connected, **ac.summarize(subset)))
    return out


@router.get("/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(
    dimension: str = Query("performer", pattern="^(performer|venue|show|city)$"),
    platform: str | None = Query(None),
    window: str | None = Query(None, pattern="^(24h|48h|7d)$"),
    limit: int = Query(15, ge=1, le=100),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Which performers / venues / shows / cities actually earn engagement.

    Medians, not averages — one viral frame shouldn't crown a performer forever. `lift`
    compares each group's median against the platform-wide median, so 1.4 means "40%
    better than a typical post"; that is the number worth acting on, not the raw score.
    """
    samples = ac.collect_samples(db, platform=platform, window=window)
    if not samples:
        return []
    baseline = ac._median([s.quality for s in samples]) or 0

    groups: dict[str, tuple[str, list]] = {}
    for s in samples:
        if dimension == "performer":
            names = db.execute(
                select(Performer.id, Performer.display_name)
                .join(PostPerformer, PostPerformer.performer_id == Performer.id)
                .where(PostPerformer.post_id == s.post.id)
            ).all()
            keys = [(pid, name) for pid, name in names]
        elif dimension == "venue":
            v = db.get(Venue, s.post.venue_id) if s.post.venue_id else None
            keys = [(v.id, v.display_name)] if v else []
        elif dimension == "show":
            keys = [(s.post.show, s.post.show)] if s.post.show else []
        else:
            keys = [(s.post.city, s.post.city)] if s.post.city else []
        for key, label in keys:
            slot = groups.setdefault(str(key), (label, []))
            slot[1].append(s)

    rows: list[LeaderboardRow] = []
    for key, (label, subset) in groups.items():
        summary = ac.summarize(subset)
        mq = summary["median_quality"]
        rows.append(
            LeaderboardRow(
                key=key, label=label, posts=summary["posts"],
                median_quality=mq,
                saves_per_1k=summary["saves_per_1k"],
                comments_per_1k=summary["comments_per_1k"],
                visits_per_1k=summary["visits_per_1k"],
                lift=round(mq / baseline, 2) if (mq and baseline) else None,
                low_sample=summary["low_sample"],
                last_posted=max((x.posted_at for x in subset), default=None),
            )
        )
    # Confident rows first, then by performance — a 1-post wonder shouldn't top the list.
    rows.sort(key=lambda r: (not r.low_sample, r.median_quality or 0), reverse=True)
    return rows[:limit]


@router.get("/account-trend", response_model=list[AccountPoint])
def account_trend(
    platform: str = Query("instagram"),
    days: int = Query(90, ge=7, le=730),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Daily audience series — follower growth and reach/profile-view trend."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = db.execute(
        select(AccountStat)
        .where(AccountStat.platform == platform, AccountStat.stat_date >= cutoff)
        .order_by(AccountStat.stat_date)
    ).scalars().all()
    return [
        AccountPoint(
            date=r.stat_date.isoformat(), followers=r.followers, reach=r.reach,
            profile_views=r.profile_views, accounts_engaged=r.accounts_engaged,
            website_clicks=r.website_clicks,
        )
        for r in rows
    ]


@router.get("/top-posts-v2")
def top_posts_v2(
    platform: str | None = Query(None),
    window: str | None = Query(None, pattern="^(24h|48h|7d)$"),
    limit: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Best posts by the platform-appropriate quality score, with the rate metrics
    that explain WHY they did well."""
    samples = ac.collect_samples(db, platform=platform, window=window)
    samples.sort(key=lambda s: s.quality, reverse=True)
    out = []
    for s in samples[:limit]:
        out.append({
            "post_id": s.post.id,
            "title": s.post.title,
            "platform": s.platform,
            "posted_at": s.posted_at.isoformat() if s.posted_at else None,
            "quality": round(s.quality, 1),
            "likes": s.likes, "comments": s.comments,
            "views": s.views or None, "reach": s.reach,
            "saves": s.saves, "shares": s.shares,
            "profile_visits": s.profile_visits, "follows": s.follows,
            "saves_per_1k": s.rate("saves"),
            "comments_per_1k": s.rate("comments"),
            "flickr_url": s.post.flickr_url,
        })
    return out


@router.get("/collab-lift")
def collab_lift(
    window: str | None = Query("7d", pattern="^(24h|48h|7d)$"),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """How much better Instagram posts do when a performer is credited as collaborator.

    Instagram exposes no accept/decline signal on this API surface, so this measures the
    consequence rather than the invite: see analytics_core.collab_lift.
    """
    return ac.collab_lift(db, window=window)
