"""Trending tags from Flickr — what tags do successful burlesque photos use?

Two signals per seed:
  • flickr.tags.getRelated  — tags that co-occur with the seed (broad)
  • flickr.photos.search    — tags from the top-relevance photos for the seed (weighted)

We blend them and expose per-tag aggregate score so the UI ranks consistently.
Worker refreshes weekly; user can also trigger an on-demand refresh from the UI.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from models import AppConfig, TrendingTag
from services.platforms import flickr

log = logging.getLogger("framepost.trending")

POPULAR_PER_PAGE = 100
POPULAR_TAGS_KEEP = 50


def get_seed_tags(db: Session) -> list[str]:
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "trending_seed_tags")
    ).scalar_one_or_none()
    raw = (row.value if row else "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]


def set_seed_tags(db: Session, tags: list[str]) -> None:
    cleaned = ", ".join(t.strip() for t in tags if t.strip())
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "trending_seed_tags")
    ).scalar_one_or_none()
    if row:
        row.value = cleaned
    else:
        db.add(AppConfig(key="trending_seed_tags", value=cleaned))
    db.commit()


def refresh(db: Session, *, seeds: list[str] | None = None) -> dict:
    """Refresh trending data for the given seeds (or all configured). Returns a summary."""
    if seeds is None:
        seeds = get_seed_tags(db)
    if not seeds:
        return {"refreshed": 0, "seeds": [], "errors": []}

    seeds_lower = [s.lower() for s in seeds]
    db.execute(delete(TrendingTag).where(TrendingTag.seed_tag.in_(seeds_lower)))
    db.commit()

    now = datetime.utcnow()
    summary = {"refreshed": 0, "seeds": seeds_lower, "errors": []}

    for seed in seeds:
        seed_lc = seed.lower()
        # 1. Related tags
        try:
            root = flickr.rest_call(db, "flickr.tags.getRelated", tag=seed)
            container = root.find("tags")
            if container is not None:
                for el in container.findall("tag"):
                    tag = (el.text or "").strip().lower()
                    if not tag or tag == seed_lc:
                        continue
                    db.add(
                        TrendingTag(
                            source="related",
                            seed_tag=seed_lc,
                            tag=tag,
                            score=1.0,
                            last_synced_at=now,
                        )
                    )
                    summary["refreshed"] += 1
        except Exception as e:
            log.warning("getRelated(%s) failed: %s", seed, e)
            summary["errors"].append(f"related/{seed}: {e}")

        # 2. Popular-photo tag mining
        try:
            root = flickr.rest_call(
                db,
                "flickr.photos.search",
                tags=seed,
                sort="relevance",
                per_page=str(POPULAR_PER_PAGE),
                page="1",
                extras="tags",
            )
            container = root.find("photos")
            counter: Counter[str] = Counter()
            if container is not None:
                for ph in container.findall("photo"):
                    tags_str = ph.get("tags") or ""
                    for t in tags_str.split():
                        t = t.strip().lower()
                        if not t or t == seed_lc:
                            continue
                        counter[t] += 1
            for tag, count in counter.most_common(POPULAR_TAGS_KEEP):
                db.add(
                    TrendingTag(
                        source="popular_photos",
                        seed_tag=seed_lc,
                        tag=tag,
                        score=float(count),
                        last_synced_at=now,
                    )
                )
                summary["refreshed"] += 1
        except Exception as e:
            log.warning("photos.search(%s) failed: %s", seed, e)
            summary["errors"].append(f"popular/{seed}: {e}")

    db.commit()
    # Mirror last refresh time so UI can show "synced N hours ago".
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "trending_last_refresh")
    ).scalar_one_or_none()
    iso = datetime.now(timezone.utc).isoformat()
    if row:
        row.value = iso
    else:
        db.add(AppConfig(key="trending_last_refresh", value=iso))
    db.commit()
    log.info(
        "trending refresh: %d rows across %d seed(s)", summary["refreshed"], len(seeds)
    )
    return summary


def list_trending(db: Session, *, limit: int = 40) -> list[dict]:
    """Aggregate per-tag scores across sources/seeds. Returns top tags with their seed origins."""
    # Use SQLite group_concat(DISTINCT ...) to surface which seeds contributed each tag.
    rows = db.execute(
        select(
            TrendingTag.tag,
            func.sum(TrendingTag.score).label("score"),
            func.group_concat(TrendingTag.seed_tag.distinct()).label("seeds"),
        )
        .group_by(TrendingTag.tag)
        .order_by(func.sum(TrendingTag.score).desc())
        .limit(limit)
    ).all()
    return [
        {
            "tag": r.tag,
            "score": float(r.score),
            "seeds": [s for s in (r.seeds or "").split(",") if s],
        }
        for r in rows
    ]
