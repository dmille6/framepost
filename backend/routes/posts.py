"""Posts API — upload, list drafts, edit, serve thumbnails."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from database import get_session
from models import AppConfig, EngagementSnapshot, FlickrEngagement, Post, PostComment, PostPlatform, User
from routes.auth import current_user
from services import events, image, import_pipeline, instagram, performers as performers_svc, reddit, storage, tags as tags_svc
from services.platforms import flickr

log = logging.getLogger("framepost.upload")
router = APIRouter()

CHUNK = 1024 * 1024


_UNICODE_PUNCT = {
    "—": "-",   # em-dash
    "–": "-",   # en-dash
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "…": "...", # ellipsis
}


def _ascii_filename(text: str, suffix: str = ".jpg") -> str:
    """Build an ASCII-only filename safe for an HTTP Content-Disposition header.

    Starlette encodes header values as latin-1 and crashes on non-latin-1 codepoints.
    Em-dashes ('—') in our own format strings or unicode in titles would otherwise
    break the response. We replace common smart-punctuation with ASCII equivalents,
    strip anything outside ASCII, and remove filename-hostile chars.
    """
    s = text or ""
    for u, a in _UNICODE_PUNCT.items():
        s = s.replace(u, a)
    s = s.encode("ascii", "ignore").decode("ascii")
    for ch in '"<>|/\\:*?\r\n\t':
        s = s.replace(ch, "")
    s = s.strip()[:80] or "image"
    if not s.lower().endswith(suffix.lower()):
        s += suffix
    return s


class PostOut(BaseModel):
    id: str
    title: str | None
    description: str | None
    tags: str | None
    original_filename: str | None
    file_size_bytes: int | None
    width: int | None
    height: int | None
    captured_at: datetime | None
    camera_make: str | None
    camera_model: str | None
    lens: str | None
    focal_length: float | None
    iso: int | None
    shutter_speed: str | None
    aperture: float | None
    sha256: str | None
    privacy: str | None
    safety_level: str | None
    content_type: str | None
    status: str
    posted_to_instagram_at: datetime | None = None
    reddit_posted_at: datetime | None = None
    target_platforms: list[str] | None = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_validator("target_platforms", mode="before")
    @classmethod
    def _parse_target_platforms(cls, v):
        # SQLAlchemy stores this as a JSON-encoded string; parse on the way out.
        # Accept None, a real list (test/in-memory), or a JSON-string.
        if v is None or isinstance(v, list):
            return v
        if isinstance(v, str):
            if not v.strip():
                return None
            try:
                parsed = json.loads(v)
                return [str(p) for p in parsed] if isinstance(parsed, list) else None
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def from_post(cls, post: Post) -> "PostOut":
        # Kept as a thin alias so the call sites we already changed keep working.
        # The field_validator above does the real work now.
        return cls.model_validate(post)


class UploadResponse(BaseModel):
    post: PostOut
    duplicate_of: str | None = None


class PostUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: str | None = None
    privacy: str | None = Field(default=None, pattern="^(private|friends_family|public)$")
    safety_level: str | None = Field(default=None, pattern="^(safe|moderate|restricted)$")
    content_type: str | None = Field(default=None, pattern="^(photo|screenshot|other)$")
    target_platforms: list[str] | None = Field(default=None)


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload(
    file: UploadFile = File(...),
    allow_duplicate: bool = Query(False),
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    storage.ensure_layout()

    # Stream the upload to a temp file under originals/ — never read all bytes into memory.
    tmp_path = storage.ORIGINALS / f"{uuid.uuid4().hex}.tmp"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tmp_path.open("wb") as out:
            while chunk := await file.read(CHUNK):
                out.write(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        result = import_pipeline.import_image(
            tmp_path,
            db=db,
            source="browser_upload",
            actor=user.username,
            allow_duplicate=allow_duplicate,
            original_filename=file.filename,
        )
    except import_pipeline.DuplicateExists as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "duplicate of an existing post",
                "duplicate_of": e.existing.id,
                "existing_title": e.existing.title,
                "existing_filename": e.existing.original_filename,
            },
        )
    except image.InvalidImage as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except import_pipeline.StorageFull as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, str(e))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return UploadResponse(
        post=PostOut.from_post(result.post),
        duplicate_of=result.duplicate_of,
    )


@router.get("", response_model=list[PostOut])
def list_drafts(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    rows = (
        db.execute(
            select(Post)
            .where(Post.status == "pending", Post.scheduled_at.is_(None))
            .order_by(Post.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return [PostOut.from_post(r) for r in rows]


@router.get("/{post_id}", response_model=PostOut)
def get_post(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    return PostOut.from_post(post)


@router.patch("/{post_id}", response_model=PostOut)
def update_post(
    post_id: str,
    body: PostUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    changed = body.model_dump(exclude_unset=True)
    if not changed:
        return PostOut.from_post(post)
    for field, value in changed.items():
        if field == "target_platforms":
            # JSON-encode the list for storage; None stays None (== "use defaults").
            post.target_platforms = json.dumps(value) if value is not None else None
        elif field == "tags":
            # Collapse internal whitespace in each tag — Flickr/IG/Bluesky/Pixelfed hashtags
            # don't support spaces, so 'New Orleans' becomes 'NewOrleans' at the storage boundary.
            post.tags = tags_svc.normalize_tag_csv(value)
        else:
            setattr(post, field, value)
    post.updated_at = datetime.now(timezone.utc)
    events.log_event(
        db,
        post_id=post_id,
        event_type="edited",
        actor="user",
        details={"fields": list(changed.keys())},
    )
    db.commit()
    db.refresh(post)
    return PostOut.from_post(post)


@router.delete("/{post_id}")
def delete_post(
    post_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Delete a post + cascade child rows (events / albums / groups / profiles via FK), and
    unlink original/thumbnail/derivative files. Does NOT touch the photo on Flickr if posted —
    only removes the FramePost record. Caller can manually delete from Flickr if needed.
    """
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    paths: list[Path] = []
    for raw in (post.original_path, post.thumbnail_path):
        if raw:
            paths.append(Path(raw))
    paths.append(storage.DERIVATIVES / f"{post_id}.jpg")  # cleanup any in-flight derivative
    paths.append(storage.preview_path(post_id))            # cleanup cached lightbox preview

    deleted_files = []
    for p in paths:
        try:
            if p.exists():
                p.unlink()
                deleted_files.append(p.name)
        except OSError as e:
            log.warning("could not unlink %s: %s", p, e)

    # Final event before the cascading delete wipes the post_events rows. Won't survive the
    # delete, but useful in logs.
    log.info(
        "deleting post %s (status=%s, was_on_flickr=%s, files=%d) by %s",
        post.id[:8], post.status, bool(post.flickr_photo_id), len(deleted_files), user.username,
    )

    db.delete(post)
    db.commit()
    return {
        "ok": True,
        "post_id": post_id,
        "files_unlinked": deleted_files,
        "was_on_flickr": bool(post.flickr_photo_id),
    }


@router.get("/{post_id}/thumbnail")
def get_thumbnail(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post or not post.thumbnail_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail not found")
    p = Path(post.thumbnail_path)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thumbnail file missing")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{post_id}/preview")
def get_preview(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Serve a 1600-px preview JPEG. Generated on first request, cached on the photo volume."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    preview = storage.preview_path(post_id)
    if not preview.exists():
        if not post.original_path:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "original not on disk (purged or never uploaded)")
        src = Path(post.original_path)
        if not src.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "original file missing")
        try:
            image.make_preview(src, preview)
        except Exception as e:
            log.exception("preview generation failed for %s", post_id)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"preview generation failed: {e}")
    return FileResponse(
        preview,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


class InstagramFormat(BaseModel):
    caption: str
    hashtags: list[str]
    title: str | None
    description: str | None
    signature: str | None
    posted_to_instagram_at: datetime | None
    sizes: list[str]


def _read_signature(db: Session) -> str | None:
    row = db.execute(select(AppConfig).where(AppConfig.key == "instagram_signature")).scalar_one_or_none()
    return row.value if row and row.value else None


@router.get("/{post_id}/instagram", response_model=InstagramFormat)
def get_instagram_format(
    post_id: str,
    extra_performer_post_ids: str | None = Query(
        None,
        description=(
            "Comma-separated list of additional post IDs. Performers tagged on any of "
            "these posts are merged (deduped by performer ID) into the mention/hashtag "
            "block. Used by the Reels builder so a Reel's caption aggregates performers "
            "across every photo in the sequence, not just the cover."
        ),
    ),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    signature = _read_signature(db)

    # Collect performers from cover post + any extras (deduped by performer.id, preserving
    # first-seen order so the cover's performers stay at the front).
    all_perfs = performers_svc.get_post_performers(db, post_id)
    seen_ids = {p.id for p in all_perfs}
    if extra_performer_post_ids:
        for extra_id in (s.strip() for s in extra_performer_post_ids.split(",")):
            if not extra_id or extra_id == post_id:
                continue
            for p in performers_svc.get_post_performers(db, extra_id):
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    all_perfs.append(p)

    # Performer @-mentions go between description and signature; hashtags merge into the
    # standard hashtag block (deduplicated against any manual tags the user typed AND
    # against any @/# manually written in the description or title).
    filtered_perfs = performers_svc.dedupe_against_text(
        all_perfs,
        existing_text=(post.description or "") + " " + (post.title or ""),
        existing_tags=post.tags or "",
    )
    perf_mention = performers_svc.mention_block(filtered_perfs)
    perf_hashtags = performers_svc.hashtag_tokens(filtered_perfs)

    base_caption = instagram.build_caption(
        title=post.title, description=post.description, signature=signature
    )
    if perf_mention:
        # Caption is title \n\n description \n\n signature; we insert mentions before
        # signature if signature is present, otherwise at the end.
        if signature and signature.strip() and base_caption.endswith(signature.strip()):
            head = base_caption[: -len(signature.strip())].rstrip("\n")
            caption = f"{head}\n\n{perf_mention}\n\n{signature.strip()}" if head else f"{perf_mention}\n\n{signature.strip()}"
        else:
            caption = f"{base_caption}\n\n{perf_mention}" if base_caption else perf_mention
    else:
        caption = base_caption

    # Hashtag block: performer hashtags first (specific, attribution), then post tags.
    hashtags = list(perf_hashtags)
    seen = {h.lower() for h in hashtags}
    for h in instagram.build_hashtags(post.tags):
        if h.lower() not in seen:
            seen.add(h.lower())
            hashtags.append(h)

    return InstagramFormat(
        caption=caption,
        hashtags=hashtags,
        title=post.title,
        description=post.description,
        signature=signature,
        posted_to_instagram_at=post.posted_to_instagram_at,
        sizes=list(instagram.SIZES.keys()),
    )


@router.get("/{post_id}/instagram-image")
def get_instagram_image(
    post_id: str,
    fmt: str = Query("square", regex="^(square|portrait)$"),
    fit: str = Query("pad", regex="^(pad|crop)$"),
    bg: str = Query("black", regex="^(black|white)$"),
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Render IG-sized JPEG bytes. Falls back to the cached preview if the original was purged
    after the retention window — preview is 1600px so still well above IG's 1080px target."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    src: Path | None = None
    if post.original_path and Path(post.original_path).exists():
        src = Path(post.original_path)
    else:
        preview = storage.preview_path(post_id)
        if preview.exists():
            src = preview
    if src is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no source image available (original purged and no preview cached)",
        )

    try:
        data = instagram.render_image(src, fmt=fmt, fit=fit, bg=bg)
    except Exception as e:
        log.exception("instagram render failed for %s", post_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"render failed: {e}")

    base = (post.title or post.original_filename or post_id).strip()
    filename = _ascii_filename(f"{base} - IG {fmt}", ".jpg")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


class InstagramMarkBody(BaseModel):
    posted: bool = True


@router.patch("/{post_id}/instagram", response_model=PostOut)
def mark_instagram_posted(
    post_id: str,
    body: InstagramMarkBody,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    post.posted_to_instagram_at = datetime.now(timezone.utc) if body.posted else None
    post.updated_at = datetime.now(timezone.utc)
    events.log_event(
        db,
        post_id=post_id,
        event_type="instagram_posted" if body.posted else "instagram_unmarked",
        actor="user",
    )
    db.commit()
    db.refresh(post)
    return PostOut.from_post(post)


# ---- Instagram manual engagement tracking -----------------------------------------
# IG has no public API for personal accounts, so we let the user log activity by hand:
# a likes count (stored as an EngagementSnapshot row) and individual comments (real
# PostComment rows tagged platform="instagram"). This data flows through the same Activity
# views as the auto-synced platforms — by-post aggregation surfaces both, and the comment
# stream merges IG comments alongside Flickr/Bluesky/Pixelfed ones.

class IGLikesUpdate(BaseModel):
    count: int = Field(ge=0, le=10_000_000)


@router.get("/{post_id}/instagram/engagement")
def get_ig_engagement(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    latest_snap = db.execute(
        select(EngagementSnapshot)
        .where(EngagementSnapshot.post_id == post_id, EngagementSnapshot.platform == "instagram")
        .order_by(EngagementSnapshot.sampled_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    comments = db.execute(
        select(PostComment)
        .where(PostComment.post_id == post_id, PostComment.platform == "instagram")
        .order_by(PostComment.posted_at.desc().nulls_last(), PostComment.fetched_at.desc())
    ).scalars().all()
    return {
        "likes_count": int(latest_snap.likes) if latest_snap else 0,
        "comments_count": len(comments),
        "last_updated_at": latest_snap.sampled_at.isoformat() if latest_snap else None,
        "comments": [
            {
                "id": c.id,
                "author_handle": c.author_handle,
                "body": c.body,
                "posted_at": c.posted_at.isoformat() if c.posted_at else None,
                "fetched_at": c.fetched_at.isoformat(),
            }
            for c in comments
        ],
    }


@router.put("/{post_id}/instagram/likes")
def set_ig_likes(
    post_id: str,
    body: IGLikesUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    """Record a likes-count snapshot for IG. Each call appends a new row, preserving the
    history series so analytics can show growth over time."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    db.add(
        EngagementSnapshot(
            post_id=post_id,
            platform="instagram",
            likes=body.count,
            comments_count=db.execute(
                select(func.count(PostComment.id)).where(
                    PostComment.post_id == post_id,
                    PostComment.platform == "instagram",
                )
            ).scalar() or 0,
        )
    )
    db.commit()
    return {"ok": True, "likes_count": body.count}


class IGCommentCreate(BaseModel):
    author_handle: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=2200)
    posted_at: datetime | None = None


@router.post("/{post_id}/instagram/comments")
def add_ig_comment(
    post_id: str,
    body: IGCommentCreate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    """Log a new manually-typed IG comment. Stored as a real PostComment row so it appears
    in the Activity stream and the per-post comments section alongside auto-synced ones."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    # Normalize the handle: strip leading @, lowercase. Then prepend @ for display.
    handle_raw = body.author_handle.strip().lstrip("@")
    handle = f"@{handle_raw}"

    # Synthesize a stable remote_id so re-adding doesn't clash. Use the timestamp + a short
    # hash of the body so the same comment text from the same user can't be silently deduped
    # if logged twice (probably distinct events).
    import hashlib
    seed = f"{post_id}|{handle_raw}|{body.body}|{datetime.utcnow().timestamp()}"
    remote_id = f"manual-{hashlib.sha1(seed.encode()).hexdigest()[:16]}"

    posted_at = body.posted_at or datetime.now(timezone.utc).replace(tzinfo=None)
    if posted_at.tzinfo is not None:
        posted_at = posted_at.astimezone(timezone.utc).replace(tzinfo=None)

    new_comment = PostComment(
        post_id=post_id,
        platform="instagram",
        remote_id=remote_id,
        author_handle=handle,
        author_display_name=handle_raw,
        author_url=f"https://instagram.com/{handle_raw}",
        body=body.body.strip(),
        posted_at=posted_at,
    )
    db.add(new_comment)
    db.commit()
    db.refresh(new_comment)
    return {
        "ok": True,
        "id": new_comment.id,
        "author_handle": new_comment.author_handle,
        "body": new_comment.body,
        "posted_at": new_comment.posted_at.isoformat() if new_comment.posted_at else None,
    }


@router.delete("/{post_id}/instagram/comments/{comment_id}")
def delete_ig_comment(
    post_id: str,
    comment_id: int,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    c = db.execute(
        select(PostComment).where(
            PostComment.id == comment_id,
            PostComment.post_id == post_id,
            PostComment.platform == "instagram",
        )
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comment not found")
    db.delete(c)
    db.commit()
    return {"ok": True, "removed": comment_id}


class RedditFormat(BaseModel):
    title_clean: str
    title_with_oc: str
    subreddits: list[dict]  # [{name, submit_url, submit_url_with_oc}]
    reddit_posted_at: datetime | None
    image_path: str  # url for the full-image download endpoint


@router.get("/{post_id}/reddit", response_model=RedditFormat)
def get_reddit_format(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    # Pull saved subreddits from app_config; fall back to a sensible default.
    cfg_row = db.execute(
        select(AppConfig).where(AppConfig.key == "reddit_subreddits")
    ).scalar_one_or_none()
    raw = (cfg_row.value if cfg_row and cfg_row.value else "").split()
    sub_names = [s for s in raw if s]

    title_clean = (post.title or post.original_filename or "").strip()
    # Reddit caps titles at 300 chars.
    if len(title_clean) > 300:
        title_clean = title_clean[:299].rstrip() + "…"
    title_oc = f"[OC] {title_clean}" if title_clean else "[OC]"
    if len(title_oc) > 300:
        title_oc = title_oc[:299].rstrip() + "…"

    from urllib.parse import quote
    subs: list[dict] = []
    for name in sub_names:
        # Reddit's submit URL accepts ?title= for image submissions; the user still has to
        # drag the image into the form (Reddit requires an authenticated upload).
        base = f"https://www.reddit.com/r/{name}/submit"
        subs.append({
            "name": name,
            "submit_url": f"{base}?title={quote(title_clean)}",
            "submit_url_with_oc": f"{base}?title={quote(title_oc)}",
        })

    return RedditFormat(
        title_clean=title_clean,
        title_with_oc=title_oc,
        subreddits=subs,
        reddit_posted_at=post.reddit_posted_at,
        image_path=f"/api/posts/{post_id}/full-image",
    )


class RedditMarkBody(BaseModel):
    posted: bool = True


@router.patch("/{post_id}/reddit", response_model=PostOut)
def mark_reddit_posted(
    post_id: str,
    body: RedditMarkBody,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    post.reddit_posted_at = datetime.now(timezone.utc) if body.posted else None
    post.updated_at = datetime.now(timezone.utc)
    events.log_event(
        db,
        post_id=post_id,
        event_type="reddit_posted" if body.posted else "reddit_unmarked",
        actor="user",
    )
    db.commit()
    db.refresh(post)
    return PostOut.from_post(post)


@router.get("/{post_id}/reddit-image")
def get_reddit_image(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Reddit-optimized JPEG: 2048px long edge, sRGB-converted, EXIF preserved, ~2-3 MB.

    Falls back to the cached preview if the original was purged. Returns as a download
    with a sensible filename so the user just drags into Reddit's submit form.
    """
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    src: Path | None = None
    if post.original_path and Path(post.original_path).exists():
        src = Path(post.original_path)
    else:
        preview = storage.preview_path(post_id)
        if preview.exists():
            src = preview
    if src is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no source image (original purged and no preview cached)",
        )

    try:
        data = reddit.render_image(src)
    except Exception as e:
        log.exception("reddit render failed for %s", post_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"render failed: {e}")

    base = (post.title or post.original_filename or post_id).strip()
    safe = _ascii_filename(f"{base} - Reddit", ".jpg")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/{post_id}/full-image")
def get_full_image(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Serve the full-resolution JPEG of a post for manual upload to other platforms (Reddit
    drag-and-drop, etc.). Falls back to the cached 1600px preview if the original was purged
    after retention. Streams as a download with a sensible filename."""
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")

    src: Path | None = None
    if post.original_path and Path(post.original_path).exists():
        src = Path(post.original_path)
    else:
        preview = storage.preview_path(post_id)
        if preview.exists():
            src = preview
    if src is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no source image (original purged and no preview cached)",
        )

    base = (post.title or post.original_filename or post_id).strip()
    safe = _ascii_filename(base, ".jpg")
    return FileResponse(
        src,
        media_type="image/jpeg",
        filename=safe,
        headers={"Cache-Control": "private, max-age=300"},
    )


class RepostFlickrResponse(BaseModel):
    post: PostOut
    flickr_deleted: bool
    flickr_delete_error: str | None


@router.post("/{post_id}/repost-flickr", response_model=RepostFlickrResponse)
def repost_to_flickr(
    post_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    """Delete this photo from Flickr and queue it to re-post immediately.

    Useful when a previous upload had a bug we've since fixed (e.g. stripped EXIF). Faves,
    views, and comments on the old Flickr photo are LOST — Flickr ties those to the photo ID
    and the new upload will be a fresh photo.

    We don't touch Bluesky/Pixelfed: those posts already happened and the scheduler's fanout
    will skip already-posted platforms on the next fire (so only Flickr re-fires).
    """
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    if not post.original_path or not Path(post.original_path).exists():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "original file no longer on disk (purged after retention period). Can't re-post.",
        )

    flickr_deleted = False
    flickr_delete_error: str | None = None
    if post.flickr_photo_id:
        try:
            flickr.rest_call(db, "flickr.photos.delete", photo_id=post.flickr_photo_id)
            flickr_deleted = True
        except Exception as e:
            # If Flickr says the photo is already gone (deleted manually), proceed anyway.
            msg = str(e)
            if "not found" in msg.lower() or "1 " in msg.lower()[:5]:
                log.info(
                    "post %s: flickr photo %s already gone, continuing repost",
                    post_id[:8], post.flickr_photo_id,
                )
                flickr_deleted = True  # treat as success — end state is the same
            else:
                flickr_delete_error = msg
                log.warning(
                    "post %s: flickr delete failed (%s); continuing repost anyway",
                    post_id[:8], msg,
                )

    # Wipe engagement rows tied to the old flickr_photo_id; otherwise analytics will conflate
    # old-photo data with the new photo's history.
    db.execute(delete(FlickrEngagement).where(FlickrEngagement.post_id == post_id))

    # Reset Flickr-side state. Keep title/description/tags/album/group selections — those are
    # the user's content, not platform output.
    now = datetime.now(timezone.utc)
    old_flickr_id = post.flickr_photo_id
    post.flickr_photo_id = None
    post.flickr_url = None
    post.posted_at = None
    post.status = "pending"
    post.scheduled_at = now
    post.next_retry_at = None
    post.retry_count = 0
    post.error_message = None
    post.updated_at = now

    events.log_event(
        db,
        post_id=post_id,
        event_type="manual_repost",
        actor="user",
        details={
            "previous_flickr_photo_id": old_flickr_id,
            "flickr_deleted": flickr_deleted,
            "flickr_delete_error": flickr_delete_error,
        },
    )
    db.commit()
    db.refresh(post)

    return RepostFlickrResponse(
        post=PostOut.from_post(post),
        flickr_deleted=flickr_deleted,
        flickr_delete_error=flickr_delete_error,
    )
