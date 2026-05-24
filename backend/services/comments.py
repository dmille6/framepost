"""Comment + engagement sync across Flickr, Bluesky, and Pixelfed.

For each platform we (a) pull the current comment thread and (b) snapshot the aggregate
engagement counts. Comments are deduped by (platform, remote_id) — re-syncing is idempotent;
existing rows are left untouched (their `seen_at` survives) and only genuinely new comments
get inserted with seen_at=NULL so the unread badge ticks up.

Engagement snapshots get appended each run; the historical series powers analytics.

Per-post failures are isolated — one platform's outage doesn't break the others.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from crypto import decrypt_token
from models import EngagementSnapshot, FlickrEngagement, PlatformCredential, Post, PostComment, PostLike, PostPlatform
from services.platforms import bluesky as bluesky_svc
from services.platforms import flickr

log = logging.getLogger("framepost.comments")

DEFAULT_LOOKBACK_DAYS = 90


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _upsert_comment(
    db: Session,
    *,
    post_id: str,
    platform: str,
    remote_id: str,
    author_handle: str | None,
    author_display_name: str | None,
    author_url: str | None,
    body: str,
    posted_at: datetime | None,
) -> bool:
    """Insert if new, otherwise leave existing row alone (preserves seen_at).
    Returns True if a fresh row was created."""
    stmt = (
        sqlite_insert(PostComment)
        .values(
            post_id=post_id,
            platform=platform,
            remote_id=remote_id,
            author_handle=author_handle,
            author_display_name=author_display_name,
            author_url=author_url,
            body=body,
            posted_at=posted_at,
        )
        .on_conflict_do_nothing(index_elements=["platform", "remote_id"])
    )
    result = db.execute(stmt)
    return result.rowcount > 0


def _upsert_like(
    db: Session,
    *,
    post_id: str,
    platform: str,
    remote_id: str,
    actor_handle: str | None,
    actor_display_name: str | None,
    actor_url: str | None,
    liked_at: datetime | None,
) -> bool:
    stmt = (
        sqlite_insert(PostLike)
        .values(
            post_id=post_id,
            platform=platform,
            remote_id=remote_id,
            actor_handle=actor_handle,
            actor_display_name=actor_display_name,
            actor_url=actor_url,
            liked_at=liked_at,
        )
        .on_conflict_do_nothing(index_elements=["platform", "remote_id"])
    )
    result = db.execute(stmt)
    return result.rowcount > 0


def _snapshot(
    db: Session,
    *,
    post_id: str,
    platform: str,
    views: int = 0,
    likes: int = 0,
    comments_count: int = 0,
    reposts: int = 0,
) -> None:
    db.add(
        EngagementSnapshot(
            post_id=post_id,
            platform=platform,
            views=views,
            likes=likes,
            comments_count=comments_count,
            reposts=reposts,
        )
    )


# -----------------------------------------------------------------------------
# Flickr — extends existing engagement.py functionality with comment fetching.
# Comments come from flickr.photos.comments.getList; engagement counts continue
# to be written to flickr_engagement (separate table, kept for analytics back-compat).
# -----------------------------------------------------------------------------

def _sync_flickr(db: Session, posts: list[Post]) -> dict[str, int]:
    summary = {"sampled": 0, "comments_new": 0, "likes_new": 0, "errors": 0}
    for p in posts:
        if not p.flickr_photo_id:
            continue
        try:
            # Engagement counts (also written to flickr_engagement for analytics back-compat).
            info = flickr.rest_call(db, "flickr.photos.getInfo", photo_id=p.flickr_photo_id)
            photo_el = info.find("photo")
            views = int(photo_el.get("views") or 0) if photo_el is not None else 0
            comments_count_el = photo_el.find("comments") if photo_el is not None else None
            comments_count = (
                int(comments_count_el.text or 0)
                if comments_count_el is not None and comments_count_el.text
                else 0
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
                    views=views,
                    faves=faves,
                    comments=comments_count,
                )
            )
            _snapshot(
                db, post_id=p.id, platform="flickr",
                views=views, likes=faves, comments_count=comments_count,
            )

            # Per-user fave list — getFavorites returns <person> nodes when faves > 0.
            if faves > 0 and faves_photo is not None:
                for person in faves_photo.iter("person"):
                    nsid = person.get("nsid")
                    if not nsid:
                        continue
                    favedate = person.get("favedate")
                    liked_dt = (
                        datetime.fromtimestamp(int(favedate), tz=timezone.utc).replace(tzinfo=None)
                        if favedate else None
                    )
                    name = person.get("realname") or person.get("username") or nsid
                    if _upsert_like(
                        db,
                        post_id=p.id,
                        platform="flickr",
                        remote_id=f"{p.flickr_photo_id}:{nsid}",
                        actor_handle=person.get("username"),
                        actor_display_name=name,
                        actor_url=f"https://www.flickr.com/people/{nsid}/",
                        liked_at=liked_dt,
                    ):
                        summary["likes_new"] += 1

            # Comment text — only call this if there are comments to fetch (saves an API call).
            if comments_count > 0:
                comments_root = flickr.rest_call(
                    db, "flickr.photos.comments.getList", photo_id=p.flickr_photo_id
                )
                for c in comments_root.iter("comment"):
                    cid = c.get("id")
                    if not cid:
                        continue
                    posted = c.get("datecreate")
                    posted_dt = (
                        datetime.fromtimestamp(int(posted), tz=timezone.utc).replace(tzinfo=None)
                        if posted else None
                    )
                    if _upsert_comment(
                        db,
                        post_id=p.id,
                        platform="flickr",
                        remote_id=cid,
                        author_handle=c.get("authorname"),
                        author_display_name=c.get("realname") or c.get("authorname"),
                        author_url=c.get("permalink"),
                        body=(c.text or "").strip(),
                        posted_at=posted_dt,
                    ):
                        summary["comments_new"] += 1

            summary["sampled"] += 1
        except Exception as e:
            log.warning("flickr comment/engagement sync failed for %s: %s", p.id[:8], e)
            summary["errors"] += 1
    return summary


# -----------------------------------------------------------------------------
# Bluesky — uses the shared atproto session helpers from the bluesky platform module.
# Endpoint: app.bsky.feed.getPostThread returns the post + replies + counts.
# -----------------------------------------------------------------------------

def _sync_bluesky(db: Session, post_platforms: list[tuple[PostPlatform, PlatformCredential]]) -> dict[str, int]:
    summary = {"sampled": 0, "comments_new": 0, "likes_new": 0, "errors": 0}
    if not post_platforms:
        return summary

    try:
        row, session = bluesky_svc._load_session(db)
    except bluesky_svc.BlueskyError as e:
        log.warning("bluesky sync skipped: %s", e)
        summary["errors"] = len(post_platforms)
        return summary

    for pp, cred in post_platforms:
        if not pp.remote_id:
            continue
        try:
            r = bluesky_svc._post_with_retry(
                db, row, session, "GET",
                "/xrpc/app.bsky.feed.getPostThread",
                params={"uri": pp.remote_id, "depth": 4},
            )
            if r.status_code >= 400:
                raise RuntimeError(f"getPostThread HTTP {r.status_code}: {r.text[:200]}")
            body = r.json()
            thread = body.get("thread") or {}
            root_post = thread.get("post") or {}
            like_count = int(root_post.get("likeCount") or 0)
            reply_count = int(root_post.get("replyCount") or 0)
            repost_count = int(root_post.get("repostCount") or 0)

            _snapshot(
                db, post_id=pp.post_id, platform="bluesky",
                views=0, likes=like_count, comments_count=reply_count, reposts=repost_count,
            )

            # Walk the replies tree (atproto returns nested replies).
            for reply in _walk_bluesky_replies(thread.get("replies") or []):
                rpost = reply.get("post") or {}
                cid = rpost.get("uri")
                if not cid:
                    continue
                author = rpost.get("author") or {}
                handle = author.get("handle")
                record = rpost.get("record") or {}
                posted_at_str = record.get("createdAt")
                posted_dt = _parse_iso(posted_at_str)
                if _upsert_comment(
                    db,
                    post_id=pp.post_id,
                    platform="bluesky",
                    remote_id=cid,
                    author_handle=f"@{handle}" if handle else None,
                    author_display_name=author.get("displayName") or handle,
                    author_url=f"https://bsky.app/profile/{handle}" if handle else None,
                    body=(record.get("text") or "").strip(),
                    posted_at=posted_dt,
                ):
                    summary["comments_new"] += 1

            # Per-user like list. Paginate via cursor; cap to keep API usage reasonable.
            if like_count > 0:
                cursor = None
                fetched = 0
                MAX_LIKES = 200
                while fetched < MAX_LIKES:
                    params = {"uri": pp.remote_id, "limit": 100}
                    if cursor:
                        params["cursor"] = cursor
                    lr = bluesky_svc._post_with_retry(
                        db, row, session, "GET",
                        "/xrpc/app.bsky.feed.getLikes",
                        params=params,
                    )
                    if lr.status_code >= 400:
                        log.warning("getLikes HTTP %s for %s: %s",
                                    lr.status_code, pp.post_id[:8], lr.text[:200])
                        break
                    lbody = lr.json()
                    for like in (lbody.get("likes") or []):
                        actor = like.get("actor") or {}
                        actor_handle = actor.get("handle")
                        actor_did = actor.get("did")
                        if not actor_did:
                            continue
                        liked_dt = _parse_iso(like.get("indexedAt"))
                        if _upsert_like(
                            db,
                            post_id=pp.post_id,
                            platform="bluesky",
                            remote_id=f"{pp.remote_id}::{actor_did}",
                            actor_handle=f"@{actor_handle}" if actor_handle else None,
                            actor_display_name=actor.get("displayName") or actor_handle,
                            actor_url=f"https://bsky.app/profile/{actor_handle}" if actor_handle else None,
                            liked_at=liked_dt,
                        ):
                            summary["likes_new"] += 1
                        fetched += 1
                    cursor = lbody.get("cursor")
                    if not cursor:
                        break
            summary["sampled"] += 1
        except Exception as e:
            log.warning("bluesky sync failed for %s: %s", pp.post_id[:8], e)
            summary["errors"] += 1
    return summary


def _walk_bluesky_replies(replies: list[dict]) -> list[dict]:
    """atproto threads are nested; flatten so we can iterate every reply at any depth."""
    out: list[dict] = []
    for r in replies:
        out.append(r)
        if "replies" in r and r["replies"]:
            out.extend(_walk_bluesky_replies(r["replies"]))
    return out


# -----------------------------------------------------------------------------
# Pixelfed — Mastodon-compatible API. /api/v1/statuses/{id} returns counts;
# /api/v1/statuses/{id}/context returns ancestors + descendants (replies).
# -----------------------------------------------------------------------------

def _sync_pixelfed(db: Session, post_platforms: list[tuple[PostPlatform, PlatformCredential]]) -> dict[str, int]:
    summary = {"sampled": 0, "comments_new": 0, "likes_new": 0, "errors": 0}
    for pp, cred in post_platforms:
        if not pp.remote_id or not cred.access_token or not cred.instance_url:
            continue
        try:
            access = decrypt_token(cred.access_token)
            base = cred.instance_url.rstrip("/")
            headers = {"Authorization": f"Bearer {access}"}
            with httpx.Client(timeout=30.0) as c:
                # Status object — has favourites_count, reblogs_count, replies_count.
                r1 = c.get(f"{base}/api/v1/statuses/{pp.remote_id}", headers=headers)
                if r1.status_code >= 400:
                    raise RuntimeError(f"status fetch HTTP {r1.status_code}: {r1.text[:200]}")
                status = r1.json()
                fav_count = int(status.get("favourites_count") or 0)
                rep_count = int(status.get("replies_count") or 0)
                _snapshot(
                    db, post_id=pp.post_id, platform="pixelfed",
                    views=0,
                    likes=fav_count,
                    comments_count=rep_count,
                    reposts=int(status.get("reblogs_count") or 0),
                )

                # Replies — only fetch if there are any.
                if rep_count > 0:
                    r2 = c.get(f"{base}/api/v1/statuses/{pp.remote_id}/context", headers=headers)
                    if r2.status_code >= 400:
                        raise RuntimeError(f"context HTTP {r2.status_code}: {r2.text[:200]}")
                    ctx = r2.json()
                    for desc in (ctx.get("descendants") or []):
                        cid = desc.get("id")
                        if not cid:
                            continue
                        account = desc.get("account") or {}
                        posted_dt = _parse_iso(desc.get("created_at"))
                        if _upsert_comment(
                            db,
                            post_id=pp.post_id,
                            platform="pixelfed",
                            remote_id=str(cid),
                            author_handle=f"@{account.get('acct')}" if account.get("acct") else None,
                            author_display_name=account.get("display_name") or account.get("username"),
                            author_url=account.get("url"),
                            body=_strip_html(desc.get("content") or ""),
                            posted_at=posted_dt,
                        ):
                            summary["comments_new"] += 1

                # Per-user fave list. Mastodon uses cursor-style pagination via Link headers,
                # but for our scale (a few faves per post) one page of 80 is plenty.
                if fav_count > 0:
                    r3 = c.get(
                        f"{base}/api/v1/statuses/{pp.remote_id}/favourited_by",
                        headers=headers,
                        params={"limit": 80},
                    )
                    if r3.status_code >= 400:
                        log.warning("favourited_by HTTP %s for %s: %s",
                                    r3.status_code, pp.post_id[:8], r3.text[:200])
                    else:
                        for actor in r3.json():
                            actor_id = actor.get("id")
                            if not actor_id:
                                continue
                            if _upsert_like(
                                db,
                                post_id=pp.post_id,
                                platform="pixelfed",
                                remote_id=f"{pp.remote_id}::{actor_id}",
                                actor_handle=f"@{actor.get('acct')}" if actor.get("acct") else None,
                                actor_display_name=actor.get("display_name") or actor.get("username"),
                                actor_url=actor.get("url"),
                                # Pixelfed's favourited_by doesn't include a per-fave timestamp;
                                # fetched_at serves as our best approximation.
                                liked_at=None,
                            ):
                                summary["likes_new"] += 1
            summary["sampled"] += 1
        except Exception as e:
            log.warning("pixelfed sync failed for %s: %s", pp.post_id[:8], e)
            summary["errors"] += 1
    return summary


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Mastodon/atproto both emit ISO8601 with Z or offset.
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def _strip_html(s: str) -> str:
    """Mastodon API returns HTML <p>…</p> for content. Cheap strip — no need for a full parser
    since we already render this as plain text in the UI."""
    import re as _re
    s = _re.sub(r"<br\s*/?>", "\n", s)
    s = _re.sub(r"</p>\s*<p>", "\n\n", s)
    s = _re.sub(r"<[^>]+>", "", s)
    # Decode common HTML entities.
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return s.strip()


# -----------------------------------------------------------------------------
# Top-level entry point — called from the daily sync job.
# -----------------------------------------------------------------------------

def sync_all(db: Session, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict[str, Any]:
    """Sync comments + engagement for posts in the last `lookback_days` window across all
    connected platforms. Returns a summary dict keyed by platform."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    # Flickr posts: have flickr_photo_id and were posted recently.
    flickr_posts = db.execute(
        select(Post)
        .where(
            Post.status.in_(["posted", "late"]),
            Post.flickr_photo_id.is_not(None),
            Post.posted_at >= cutoff,
        )
    ).scalars().all()

    # Non-Flickr platforms: walk post_platforms rows for posted-status posts in window.
    bluesky_targets: list[tuple[PostPlatform, PlatformCredential]] = []
    pixelfed_targets: list[tuple[PostPlatform, PlatformCredential]] = []
    rows = db.execute(
        select(PostPlatform, PlatformCredential, Post)
        .join(PlatformCredential, PlatformCredential.id == PostPlatform.platform_id)
        .join(Post, Post.id == PostPlatform.post_id)
        .where(
            PostPlatform.status == "posted",
            Post.posted_at >= cutoff,
        )
    ).all()
    for pp, cred, _post in rows:
        if cred.platform == "bluesky":
            bluesky_targets.append((pp, cred))
        elif cred.platform == "pixelfed":
            pixelfed_targets.append((pp, cred))

    out = {
        "flickr": _sync_flickr(db, flickr_posts),
        "bluesky": _sync_bluesky(db, bluesky_targets),
        "pixelfed": _sync_pixelfed(db, pixelfed_targets),
    }
    db.commit()
    log.info("comments+engagement sync: %s", json.dumps(out))
    return out
