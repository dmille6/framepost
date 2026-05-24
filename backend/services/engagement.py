"""Flickr engagement sync — nightly snapshots of views/faves/comments per posted photo.

Runs after the daily Flickr index sync. For each post the user has on Flickr (status in
posted/late) within the configured lookback window, calls:
  • flickr.photos.getInfo       — views + comments count
  • flickr.photos.getFavorites  — total faves

Stores a row in flickr_engagement with the current timestamp. The deltas between snapshots
power "best times to post" / "tag performance" / "group performance" analytics.

Bound the lookback to keep API usage sane: at 200 photos × 2 calls = 400 calls/night, well
under Flickr's ~3600 calls/hour soft limit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import FlickrEngagement, Post
from services.platforms import flickr

log = logging.getLogger("framepost.engagement")

DEFAULT_LOOKBACK_DAYS = 90  # only sync posts from the last N days


def sync(db: Session, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict[str, int]:
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    posts = db.execute(
        select(Post)
        .where(
            Post.status.in_(["posted", "late"]),
            Post.flickr_photo_id.is_not(None),
            Post.posted_at >= cutoff,
        )
    ).scalars().all()

    summary = {"sampled": 0, "errors": 0}
    now = datetime.utcnow()

    for p in posts:
        try:
            info = flickr.rest_call(db, "flickr.photos.getInfo", photo_id=p.flickr_photo_id)
            photo_el = info.find("photo")
            views = int(photo_el.get("views") or 0) if photo_el is not None else 0
            comments_el = photo_el.find("comments") if photo_el is not None else None
            comments = (
                int(comments_el.text or 0) if comments_el is not None and comments_el.text else 0
            )

            faves_root = flickr.rest_call(
                db, "flickr.photos.getFavorites", photo_id=p.flickr_photo_id
            )
            faves_photo = faves_root.find("photo")
            faves = int(faves_photo.get("total") or 0) if faves_photo is not None else 0

            db.add(
                FlickrEngagement(
                    post_id=p.id,
                    flickr_photo_id=p.flickr_photo_id,
                    sampled_at=now,
                    views=views,
                    faves=faves,
                    comments=comments,
                )
            )
            summary["sampled"] += 1
        except Exception as e:
            log.warning("engagement sync failed for %s: %s", p.id[:8], e)
            summary["errors"] += 1

    db.commit()
    log.info("engagement sync: %d sampled, %d errors", summary["sampled"], summary["errors"])
    return summary
