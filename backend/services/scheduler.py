"""Worker entrypoint. APScheduler with MemoryJobStore — DB is the source of truth.

Phase 3 wires real Flickr upload via services/platforms/flickr.py. Failures are captured
through the retry policy in services/retry.py.
"""
from __future__ import annotations

import logging
import shutil
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import delete, select

from config import settings
from database import SessionLocal
from models import Album, AppConfig, DiskSample, PlatformCredential, Post, PostAlbum, PostGroup, PostPlatform, Group
from services import backup, cleanup, comments as comments_sync, duplicate, engagement, events, flickr_sync, image, retry, storage, tags, trending, watcher
from services import performers as performers_svc
from services.platforms import bluesky, flickr, pinterest, pixelfed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("framepost.worker")

LATE_THRESHOLD = timedelta(minutes=5)
MISSED_THRESHOLD = timedelta(hours=24)
DEFAULT_DERIVATIVE_LONG_EDGE = 2048


def _set_config(key: str, value: str) -> None:
    db = SessionLocal()
    try:
        row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(AppConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()


def heartbeat() -> None:
    _set_config("worker_last_heartbeat", datetime.now(timezone.utc).isoformat())


DISK_SAMPLE_RETENTION_DAYS = 30


def sample_disk_usage() -> None:
    """Append a row to disk_samples and prune anything older than 30 days."""
    try:
        usage = shutil.disk_usage(settings.photo_root)
    except OSError:
        log.exception("disk usage sample failed")
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        db.add(
            DiskSample(
                sampled_at=now,
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
            )
        )
        cutoff = now - timedelta(days=DISK_SAMPLE_RETENTION_DAYS)
        db.execute(delete(DiskSample).where(DiskSample.sampled_at < cutoff))
        db.commit()
    except Exception:
        log.exception("disk sample write failed")
        db.rollback()
    finally:
        db.close()


def _derivative_long_edge(db) -> int:
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "flickr_max_long_edge")
    ).scalar_one_or_none()
    if row and row.value:
        try:
            return int(row.value)
        except ValueError:
            pass
    return DEFAULT_DERIVATIVE_LONG_EDGE


def _flickr_post(db, post: Post, fired_at: datetime) -> None:
    """Build derivative, upload, stamp machine tag, transition status. Failures bubble up."""
    src = Path(post.original_path) if post.original_path else None
    if not src or not src.exists():
        raise flickr.FlickrError("original file missing on disk", permanent=True)

    # Layer-2 pre-publish check: if this hash is already on Flickr (machine-tag match),
    # don't double-post. Permanent failure — the user can manually dismiss or delete.
    if post.sha256:
        existing = duplicate.find_in_flickr_cache(db, post.sha256)
        if existing:
            raise flickr.FlickrError(
                f"already on Flickr as photo {existing.flickr_photo_id}",
                permanent=True,
            )

    # Soft match (title + date + dims): just a warning event; proceed.
    soft = duplicate.find_soft_match(
        db,
        title=post.title,
        captured_at=post.captured_at,
        width=post.width,
        height=post.height,
    )
    if soft:
        events.log_event(
            db,
            post_id=post.id,
            event_type="flickr_failed",
            actor="worker",
            details={
                "action": "soft_duplicate_warning",
                "matching_flickr_photo_id": soft.flickr_photo_id,
                "matching_title": soft.title,
            },
        )

    derivative = storage.DERIVATIVES / f"{post.id}.jpg"
    image.make_derivative(src, derivative, _derivative_long_edge(db))
    try:
        events.log_event(
            db,
            post_id=post.id,
            event_type="flickr_uploading",
            actor="worker",
            details={"attempt": post.retry_count + 1, "derivative_bytes": derivative.stat().st_size},
        )
        db.commit()

        machine_tag = f"framepost:sha256={post.sha256}" if post.sha256 else None
        merged = tags.merged_tags_for_post(db, post)
        flickr_tags = flickr.format_tags(
            merged,
            machine_tags=[machine_tag] if machine_tag else None,
        )
        photo_id = flickr.upload_photo(
            db=db,
            image_path=derivative,
            title=post.title,
            description=post.description,
            tags=flickr_tags,
            privacy=post.privacy or "private",
            safety_level=post.safety_level or "safe",
            content_type=post.content_type or "photo",
        )
        post.flickr_photo_id = photo_id
        post.flickr_url = flickr.photo_url(photo_id)
        post.posted_at = fired_at
        post.error_message = None
        post.next_retry_at = None
        late = post.scheduled_at < (fired_at - LATE_THRESHOLD)
        post.status = "late" if late else "posted"
        post.updated_at = fired_at

        events.log_event(
            db,
            post_id=post.id,
            event_type="flickr_uploaded",
            actor="worker",
            details={"flickr_photo_id": photo_id, "url": post.flickr_url},
        )

        # Add to selected albums. Failures are non-fatal — log and continue.
        rows = db.execute(
            select(Album).join(PostAlbum, PostAlbum.album_id == Album.id).where(PostAlbum.post_id == post.id)
        ).scalars().all()
        for album in rows:
            if not album.flickr_album_id:
                continue
            try:
                flickr.rest_call(
                    db,
                    "flickr.photosets.addPhoto",
                    photoset_id=album.flickr_album_id,
                    photo_id=photo_id,
                )
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="edited",
                    actor="worker",
                    details={"action": "added_to_album", "album": album.name, "flickr_album_id": album.flickr_album_id},
                )
            except Exception as ae:  # noqa: BLE001
                log.warning("post %s: failed to add to album %s: %s", post.id[:8], album.name, ae)
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="flickr_failed",
                    actor="worker",
                    details={"action": "add_to_album", "album": album.name, "error": str(ae)},
                )
        if late:
            events.log_event(
                db,
                post_id=post.id,
                event_type="marked_late",
                actor="worker",
                details={
                    "scheduled_at": post.scheduled_at.isoformat(),
                    "fired_at": fired_at.isoformat(),
                },
            )
        _set_config("flickr_last_success", fired_at.isoformat())
        log.info("post %s posted to flickr as %s", post.id[:8], photo_id)
    finally:
        derivative.unlink(missing_ok=True)


def _record_failure(db, post: Post, err: Exception, fired_at: datetime) -> None:
    msg = str(err)
    permanent = isinstance(err, flickr.FlickrError) and err.permanent
    post.retry_count = (post.retry_count or 0) + 1
    post.error_message = msg

    events.log_event(
        db,
        post_id=post.id,
        event_type="flickr_failed",
        actor="worker",
        details={"attempt": post.retry_count, "permanent": permanent, "error": msg},
    )

    if permanent or post.retry_count >= retry.max_attempts(db):
        post.status = "failed"
        post.next_retry_at = None
        post.updated_at = fired_at
        log.error("post %s permanently failed after %d attempts: %s",
                  post.id[:8], post.retry_count, msg)
    else:
        post.next_retry_at = retry.next_retry_at(db, post.retry_count)
        post.updated_at = fired_at
        log.warning("post %s failed (attempt %d/%s), retry at %s: %s",
                    post.id[:8], post.retry_count, retry.max_attempts(db),
                    post.next_retry_at, msg)


def fire_due_posts() -> None:
    """Phase 3 implementation. Real Flickr upload + retry policy.

    A post is due when:
      - status='pending'
      - scheduled_at IS NOT NULL and <= now
      - next_retry_at is null or <= now (so failed-but-retrying posts wait their turn)
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff_missed = now - MISSED_THRESHOLD
        due = db.execute(
            select(Post).where(
                Post.status == "pending",
                Post.scheduled_at.is_not(None),
                Post.scheduled_at <= now,
                (Post.next_retry_at.is_(None)) | (Post.next_retry_at <= now),
            )
        ).scalars().all()
        if not due:
            return

        for post in due:
            sched = post.scheduled_at
            if sched < cutoff_missed and not post.flickr_photo_id:
                post.status = "missed"
                post.updated_at = now
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="marked_missed",
                    actor="worker",
                    details={"scheduled_at": sched.isoformat(), "fired_at": now.isoformat()},
                )
                log.info("post %s missed (scheduled %s)", post.id[:8], sched)
                db.commit()
                continue

            targets = _parse_target_platforms(post)

            if targets is None or "flickr" in targets:
                try:
                    _flickr_post(db, post, fired_at=now)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    # Fresh transaction for the failure record so the prior partial state isn't
                    # written. Reload post to get current state.
                    refreshed = db.get(Post, post.id)
                    if refreshed:
                        _record_failure(db, refreshed, e, fired_at=now)
                        db.commit()
                    else:
                        log.exception("post %s vanished mid-fire", post.id[:8])
                    continue
            else:
                # User opted out of Flickr for this post. Transition state directly so the post
                # leaves the queue and fanout can proceed to non-Flickr platforms.
                late = post.scheduled_at < (now - LATE_THRESHOLD)
                post.status = "late" if late else "posted"
                post.posted_at = now
                post.error_message = None
                post.next_retry_at = None
                post.updated_at = now
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="flickr_skipped",
                    actor="worker",
                    details={"reason": "not in target_platforms"},
                )
                db.commit()
                log.info("post %s skipping flickr per target_platforms=%s", post.id[:8], targets)

            # Fanout to non-Flickr platforms. Failures here are isolated per-platform.
            try:
                fanout_to_platforms(db, post, fired_at=now, targets=targets)
                db.commit()
            except Exception:
                log.exception("post %s: platform fanout failed", post.id[:8])
                db.rollback()
    except Exception:
        log.exception("fire_due_posts failed")
        db.rollback()
    finally:
        db.close()


def _build_caption_for(platform: str, post: Post, db) -> str:
    """Compose the caption text passed to non-Flickr platforms. Bluesky has a 300-char cap so we
    keep it tight; Pixelfed/Mastodon allow longer text so we include the full description."""
    title = (post.title or "").strip()
    description = (post.description or "").strip()
    tag_str = (post.tags or "").strip()
    # Pull the IG signature row — convenient since the user already configured it for IG.
    sig_row = db.execute(
        select(AppConfig).where(AppConfig.key == "instagram_signature")
    ).scalar_one_or_none()
    signature = (sig_row.value if sig_row and sig_row.value else "").strip()

    # Performer @-mentions + their hashtags. Mentions get priority on tight budgets
    # (Bluesky) because they're attribution — losing a tag is annoying but losing a
    # credited performer is rude. We also dedupe against the description/tags so a
    # manually-typed @handle or #handle doesn't get echoed by the auto-insert.
    all_performers = performers_svc.get_post_performers(db, post.id)
    filtered = performers_svc.dedupe_against_text(
        all_performers,
        existing_text=(post.description or "") + " " + (post.title or ""),
        existing_tags=tag_str,
    )
    perf_mention = performers_svc.mention_block(filtered)
    perf_hashtags = performers_svc.hashtag_tokens(filtered)

    if platform == "bluesky":
        # 300-graphemes hard cap. Skip signature (would eat budget). Build:
        # title + blank + description + blank + mentions + blank + hashtags
        # Hashtags = bluesky_default_hashtags + post-specific tags + performer hashtags,
        # fit greedily until 300.
        parts: list[str] = []
        if title:
            parts.append(title)
        if description:
            parts.append(description)
        body = "\n\n".join(parts)

        # Mentions go in as a whole block (cheap, attribution-critical). Bail out if
        # even adding the mentions overflows — performers come before tags in priority.
        BUDGET = 300
        if perf_mention:
            candidate = (body + "\n\n" + perf_mention) if body else perf_mention
            if len(candidate) <= BUDGET:
                body = candidate

        # Read default hashtags from app_config (set in Settings → Platforms → Bluesky).
        defaults_row = db.execute(
            select(AppConfig).where(AppConfig.key == "bluesky_default_hashtags")
        ).scalar_one_or_none()
        default_tokens = (defaults_row.value or "").split() if defaults_row and defaults_row.value else []

        ordered_tags: list[str] = []
        seen: set[str] = set()
        # Defaults first so they're guaranteed to fit if anything does.
        for tok in default_tokens:
            cleaned = "".join(ch for ch in tok.lower() if ch.isalnum() or ch == "_")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                ordered_tags.append(f"#{cleaned}")
        # Performer hashtags before generic post tags — they're more specific. Preserve
        # the helper's case (handle stays lower, display-name fallback stays CamelCase).
        for raw_tag in perf_hashtags:
            key = raw_tag.lstrip("#").lower()
            if key and key not in seen:
                seen.add(key)
                ordered_tags.append(raw_tag)
        # Then post-specific tags.
        for raw in tag_str.split():
            cleaned = "".join(ch for ch in raw.lower() if ch.isalnum() or ch == "_")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                ordered_tags.append(f"#{cleaned}")

        # Fit as many tags as the 300-char budget allows. Stop greedily once the next tag
        # would push us over.
        if ordered_tags:
            current = body
            sep = "\n\n" if current else ""
            for i, tag in enumerate(ordered_tags):
                joiner = sep if i == 0 else " "
                candidate = current + joiner + tag
                if len(candidate) > BUDGET:
                    break
                current = candidate
            body = current

        return body[:300]

    # Pixelfed (and future Mastodon): long text OK. Caption + mentions + signature + hashtags.
    parts = []
    if title:
        parts.append(title)
    if description:
        parts.append(description)
    if signature:
        parts.append(signature)
    if perf_mention:
        parts.append(perf_mention)
    body = "\n\n".join(parts)
    # Hashtag block at the bottom, IG-style (works on Pixelfed too). Performer hashtags
    # blend in alongside post tags; we de-dupe so #roxielarouge doesn't appear twice if
    # the user also typed it as a manual tag.
    hashtags: list[str] = []
    seen: set[str] = set()
    for raw_tag in perf_hashtags:
        key = raw_tag.lstrip("#").lower()
        if key and key not in seen:
            seen.add(key)
            hashtags.append(raw_tag)  # preserves CamelCase fallback for readability
    if tag_str:
        for raw in tag_str.split():
            cleaned = "".join(ch for ch in raw.lower() if ch.isalnum() or ch == "_")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                hashtags.append(f"#{cleaned}")
            if len(hashtags) >= 30:
                break
    if hashtags:
        body = f"{body}\n\n{' '.join(hashtags)}".strip()
    return body


def _post_to_platform(db, cred: PlatformCredential, post: Post, fired_at: datetime) -> None:
    """Attempt one platform fanout; persist outcome to post_platforms + activity timeline."""
    src = Path(post.original_path) if post.original_path else None
    if not src or not src.exists():
        # Fallback: cached preview (1600px). Still well above quality bar for Bluesky/Pixelfed.
        preview = storage.preview_path(post.id)
        if preview.exists():
            src = preview
    if src is None:
        raise RuntimeError("no source image (original purged, no preview cached)")

    text = _build_caption_for(cred.platform, post, db)
    alt = (post.title or "") + ("\n\n" + post.description if post.description else "")

    if cred.platform == "bluesky":
        result = bluesky.post_photo(db=db, src=src, text=text, alt_text=alt)
        remote_id, remote_url = result["at_uri"], result["url"]
    elif cred.platform == "pixelfed":
        result = pixelfed.post_photo(db=db, src=src, text=text, alt_text=alt)
        remote_id, remote_url = result["remote_id"], result["url"]
    elif cred.platform == "pinterest":
        # Pinterest has structured title/description/link rather than a blob, so we don't
        # use _build_caption_for's output — we pass the fields directly. Link defaults to the
        # photo's Flickr URL (drives the killer perpetual referral traffic) when present.
        #
        # Performer handling: Pinterest has no @-mention culture, so we skip the mentions
        # block. But the performer hashtags belong in description — we prepend their
        # tokens to the tags string so pinterest.post_pin's existing hashtag builder
        # de-dupes them against post.tags naturally. Dedupe against existing description/tags
        # so manually-typed performer references aren't doubled.
        _perf_performers = performers_svc.get_post_performers(db, post.id)
        _filtered = performers_svc.dedupe_against_text(
            _perf_performers,
            existing_text=(post.description or "") + " " + (post.title or ""),
            existing_tags=post.tags or "",
        )
        perf_hashtag_tokens = performers_svc.hashtag_tokens(_filtered)
        merged_tags = " ".join(
            [t.lstrip("#") for t in perf_hashtag_tokens] + ((post.tags or "").split())
        )
        result = pinterest.post_pin(
            db=db,
            src=src,
            title=post.title,
            description=post.description,
            tags=merged_tags or None,
            link=post.flickr_url,
            alt_text=alt,
        )
        remote_id, remote_url = result["remote_id"], result["url"]
    else:
        raise RuntimeError(f"unsupported platform: {cred.platform}")

    # Upsert post_platforms row.
    pp = db.get(PostPlatform, (post.id, cred.id))
    if not pp:
        pp = PostPlatform(post_id=post.id, platform_id=cred.id)
        db.add(pp)
    pp.status = "posted"
    pp.remote_id = remote_id
    pp.remote_url = remote_url
    pp.posted_at = fired_at
    pp.error_message = None
    pp.next_retry_at = None
    cred.last_success_at = fired_at
    cred.last_error = None

    events.log_event(
        db,
        post_id=post.id,
        event_type=f"{cred.platform}_uploaded",
        actor="worker",
        details={"remote_id": remote_id, "url": remote_url, "account": cred.account_name},
    )
    log.info("post %s posted to %s as %s", post.id[:8], cred.platform, remote_id)


def _parse_target_platforms(post: Post) -> list[str] | None:
    """Read post.target_platforms as a list of platform names. None = use defaults."""
    if not post.target_platforms:
        return None
    try:
        import json
        parsed = json.loads(post.target_platforms)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except (TypeError, ValueError):
        log.warning("post %s: malformed target_platforms %r — falling back to defaults",
                    post.id[:8], post.target_platforms)
    return None


def fanout_to_platforms(
    db,
    post: Post,
    *,
    fired_at: datetime,
    targets: list[str] | None = None,
) -> None:
    """Fire non-Flickr platforms. If targets is None, use every connected default_target=1
    platform. If targets is a list, post only to platforms in that list (and skip Flickr,
    which is handled in the main fire path).

    Per-platform failures are isolated — they record into post_platforms with status='failed'
    but don't raise upward. Flickr already succeeded (or was skipped); we don't undo that on a
    Bluesky 500.
    """
    if targets is None:
        creds = db.execute(
            select(PlatformCredential).where(
                PlatformCredential.platform.in_(("bluesky", "pixelfed", "pinterest")),
                PlatformCredential.default_target == 1,
            )
        ).scalars().all()
    else:
        wanted = {p for p in targets if p != "flickr"}
        if not wanted:
            return
        creds = db.execute(
            select(PlatformCredential).where(
                PlatformCredential.platform.in_(wanted),
            )
        ).scalars().all()
    targets_creds = creds  # rename for clarity below
    if not targets_creds:
        return

    # On a repost we only want to re-fire Flickr — non-Flickr platforms posted successfully
    # the first time around and the photos are still live. Skip any platform that already
    # has a 'posted' row for this post.
    already_posted_ids = {
        row.platform_id for row in db.execute(
            select(PostPlatform).where(
                PostPlatform.post_id == post.id,
                PostPlatform.status == "posted",
            )
        ).scalars().all()
    }

    for cred in targets_creds:
        if not cred.access_token:
            continue  # Pixelfed connection not yet completed (still pending OAuth callback)
        if cred.id in already_posted_ids:
            log.info(
                "post %s: %s already posted, skipping fanout",
                post.id[:8], cred.platform,
            )
            continue

        try:
            _post_to_platform(db, cred, post, fired_at)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            _record_platform_failure(db, post, cred, e)


def _record_platform_failure(
    db, post: Post, cred: PlatformCredential, err: Exception
) -> None:
    """Persist a fanout failure to post_platforms + activity timeline. Decides whether the
    failure is retryable (transient + retries left) or permanent (4xx-class or attempts
    exhausted). Caller has already rolled back the failed transaction."""
    permanent = getattr(err, "permanent", False)
    log.warning("post %s: %s fanout failed (%s): %s",
                post.id[:8], cred.platform, "permanent" if permanent else "transient", err)
    try:
        refreshed_cred = db.get(PlatformCredential, cred.id)
        pp = db.get(PostPlatform, (post.id, cred.id))
        if not pp:
            pp = PostPlatform(post_id=post.id, platform_id=cred.id)
            db.add(pp)
        pp.retry_count = (pp.retry_count or 0) + 1
        pp.error_message = str(err)[:1000]
        max_attempts = retry.max_attempts(db)
        pp.status = "failed" if permanent or pp.retry_count >= max_attempts else "pending"
        if pp.status == "pending":
            pp.next_retry_at = retry.next_retry_at(db, pp.retry_count)
        else:
            pp.next_retry_at = None
        if refreshed_cred:
            refreshed_cred.last_error = str(err)[:500]
        events.log_event(
            db,
            post_id=post.id,
            event_type=f"{cred.platform}_failed",
            actor="worker",
            details={
                "attempt": pp.retry_count,
                "max_attempts": max_attempts,
                "permanent": permanent,
                "error": str(err)[:500],
            },
        )
        db.commit()
    except Exception:
        log.exception("post %s: failed to record %s failure", post.id[:8], cred.platform)
        db.rollback()


def retry_due_platform_posts() -> None:
    """Pick up post_platforms rows whose retry timer has elapsed and try them again.

    Mirrors the Flickr retry logic: each tick scans for status='pending' rows on already-fired
    posts (post.status in posted/late) where next_retry_at <= now. Backoff schedule is the
    same retry.next_retry_at curve used for Flickr, so users see consistent behavior.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = db.execute(
            select(PostPlatform, Post, PlatformCredential)
            .join(Post, Post.id == PostPlatform.post_id)
            .join(PlatformCredential, PlatformCredential.id == PostPlatform.platform_id)
            .where(
                PostPlatform.status == "pending",
                PostPlatform.next_retry_at.is_not(None),
                PostPlatform.next_retry_at <= now,
                Post.status.in_(("posted", "late")),
            )
        ).all()
        if not rows:
            return

        for pp, post, cred in rows:
            if not cred.access_token:
                continue
            try:
                _post_to_platform(db, cred, post, fired_at=now)
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                _record_platform_failure(db, post, cred, e)
    except Exception:
        log.exception("retry_due_platform_posts failed")
        db.rollback()
    finally:
        db.close()


def submit_due_groups() -> None:
    """Process pending group submissions for posts that have already landed on Flickr.

    A submission is due when:
      - status='pending'
      - parent post is in posted/late
      - next_retry_at is null or <= now
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        max_attempts = retry.max_attempts(db)

        rows = db.execute(
            select(PostGroup, Post, Group)
            .join(Post, Post.id == PostGroup.post_id)
            .join(Group, Group.id == PostGroup.group_id)
            .where(
                PostGroup.status == "pending",
                Post.status.in_(("posted", "late")),
                Post.flickr_photo_id.is_not(None),
                (PostGroup.next_retry_at.is_(None)) | (PostGroup.next_retry_at <= now),
            )
        ).all()

        for pg, post, group in rows:
            if not group.flickr_group_id:
                pg.status = "failed"
                pg.error_message = "no flickr_group_id configured"
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="group_rejected",
                    actor="worker",
                    details={"group": group.name, "reason": "no flickr_group_id"},
                )
                continue
            try:
                flickr.rest_call(
                    db,
                    "flickr.groups.pools.add",
                    group_id=group.flickr_group_id,
                    photo_id=post.flickr_photo_id,
                )
                pg.status = "submitted"
                pg.submitted_at = now
                pg.error_message = None
                pg.next_retry_at = None
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="group_submitted",
                    actor="worker",
                    details={"group": group.name, "flickr_group_id": group.flickr_group_id},
                )
                log.info("post %s submitted to group %s", post.id[:8], group.name)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                permanent = isinstance(e, flickr.FlickrError) and e.permanent
                pg.retry_count = (pg.retry_count or 0) + 1
                pg.error_message = msg
                events.log_event(
                    db,
                    post_id=post.id,
                    event_type="group_rejected" if permanent else "flickr_failed",
                    actor="worker",
                    details={
                        "action": "submit_to_group",
                        "group": group.name,
                        "attempt": pg.retry_count,
                        "permanent": permanent,
                        "error": msg,
                    },
                )
                if permanent or pg.retry_count >= max_attempts:
                    pg.status = "failed"
                    pg.next_retry_at = None
                else:
                    pg.next_retry_at = retry.next_retry_at(db, pg.retry_count)
        db.commit()
    except Exception:
        log.exception("submit_due_groups failed")
        db.rollback()
    finally:
        db.close()


def daily_flickr_sync() -> None:
    """Refresh albums + recent-photos cache + engagement snapshots."""
    db = SessionLocal()
    try:
        try:
            flickr_sync.sync_albums(db)
        except Exception:
            log.exception("daily album sync failed")
        try:
            flickr_sync.sync_recent_photos(db)
        except Exception:
            log.exception("daily photo sync failed")
        # Unified comments + engagement sync across Flickr, Bluesky, Pixelfed. Replaces the
        # old Flickr-only engagement.sync() call — that path still works since
        # comments.sync_all() also writes to flickr_engagement for analytics back-compat.
        try:
            comments_sync.sync_all(db)
        except Exception:
            log.exception("comments+engagement sync failed")
    finally:
        db.close()


def weekly_trending_refresh() -> None:
    """Refresh trending tags from Flickr for the configured seed tags. Mondays 02:00 UTC."""
    db = SessionLocal()
    try:
        try:
            trending.refresh(db)
        except Exception:
            log.exception("weekly trending refresh failed")
    finally:
        db.close()


def daily_cleanup() -> None:
    """Daily cron job — runs at app_config.cleanup_time (default 03:00 UTC):
       1. SQLite hot backup to /mnt/photo-data/backup/.
       2. Rotate backups (7 daily / 4 weekly / 3 monthly).
       3. Purge originals for posts past retention.
       4. WAL checkpoint.
       Mirror last_backup into app_config so /health + Settings → System reflect it.
    """
    db = SessionLocal()
    try:
        try:
            b = backup.run_backup()
            _set_config("last_backup", b.created_at.isoformat())
            log.info("daily backup: %s (%d bytes)", b.name, b.size_bytes)
        except Exception:
            log.exception("daily backup failed")

        try:
            deleted = backup.rotate_backups()
            if deleted:
                log.info("daily backup rotation: deleted %s", deleted)
        except Exception:
            log.exception("backup rotation failed")

        try:
            n = cleanup.purge_expired_originals(db)
            log.info("daily cleanup: purged %d original(s)", n)
        except Exception:
            log.exception("original purge failed")
            db.rollback()

        try:
            n = cleanup.purge_expired_reels(db)
            log.info("daily cleanup: purged %d reel mp4(s)", n)
        except Exception:
            log.exception("reel purge failed")
            db.rollback()

        try:
            backup.wal_checkpoint()
        except Exception:
            log.exception("WAL checkpoint failed")
    finally:
        db.close()


def _read_cron_time(key: str, default: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
        raw = (row.value if row else default) or default
        h, m = raw.split(":")
        return int(h), int(m)
    finally:
        db.close()


def main() -> int:
    cleanup_h, cleanup_m = _read_cron_time("cleanup_time", "03:00")
    flickr_h, flickr_m = _read_cron_time("flickr_sync_time", "04:00")

    scheduler = BlockingScheduler(
        jobstores={"default": MemoryJobStore()},
        timezone="UTC",
    )

    scheduler.add_job(heartbeat, "interval", minutes=1, id="heartbeat",
                      next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(sample_disk_usage, "interval", minutes=5, id="sample_disk_usage",
                      next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(fire_due_posts, "interval", minutes=1, id="fire_due_posts")
    scheduler.add_job(submit_due_groups, "interval", minutes=1, id="submit_due_groups")
    scheduler.add_job(retry_due_platform_posts, "interval", minutes=1, id="retry_platform_posts")
    scheduler.add_job(daily_flickr_sync, "cron", hour=flickr_h, minute=flickr_m, id="daily_flickr_sync")
    scheduler.add_job(daily_cleanup, "cron", hour=cleanup_h, minute=cleanup_m, id="daily_cleanup")
    scheduler.add_job(weekly_trending_refresh, "cron", day_of_week="mon", hour=2, minute=0,
                      id="weekly_trending_refresh")
    scheduler.add_job(
        watcher.reconcile,
        "interval",
        seconds=30,
        id="reconcile_watch_folder",
        next_run_time=datetime.now(timezone.utc),
    )

    def _shutdown(signum, _frame):
        log.info("Worker shutting down (signal %s)", signum)
        watcher.stop()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("Worker starting — heartbeat 1m; cleanup %02d:%02d UTC; flickr sync %02d:%02d UTC",
             cleanup_h, cleanup_m, flickr_h, flickr_m)
    scheduler.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
