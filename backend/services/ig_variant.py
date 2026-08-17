"""Instagram portrait auto-transform — crop/pad out-of-range photos to an IG-safe
aspect ratio and stage them as hidden Flickr photos for Meta's URL-based ingest.

Why staging on Flickr: Meta fetches image_url server-side, FramePost's host is
LAN-only, and Flickr static URLs are secret-guarded (resolve regardless of photo
privacy). Variants upload private+hidden with a `framepost:ig_variant=<post_id>`
machine tag so flickr_sync and the duplicate checks ignore them, and are deleted
right after a successful publish (daily cleanup sweeps any orphans from failures).

The 3:4 experiment: Meta's docs still say the feed floor is 4:5, but the app has
accepted 3:4 since May 2025 and several schedulers report the API quietly follows.
Rather than trust either story we probe empirically: stay optimistic (3:4) until a
container creation fails with an aspect-ratio error, then record 4:5 in app_config
and regenerate. One wasted upload, once, ever — and if 3:4 works, tall portraits
keep 11% more of the frame.
"""
from __future__ import annotations

import io
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AppConfig, Post, PostPlatform
from services import faces, storage
from services.platforms import flickr

log = logging.getLogger("framepost.ig_variant")

# app_config key recording what Meta actually accepts as the portrait floor.
# Values: "3:4" (probe succeeded), "4:5" (probe failed). Unset = untested → optimistic.
RATIO_CONFIG_KEY = "ig_min_ratio_support"

MACHINE_TAG_NS = "framepost:ig_variant"

RATIOS = {"3:4": 3 / 4, "4:5": 4 / 5}
MAX_ASPECT = 1.91  # landscape ceiling is not in dispute
EPS = 0.005

# Output geometry. Meta downscales anything over 1440px wide; matching that cap keeps
# uploads small without giving up quality.
OUT_WIDTH = 1440
JPEG_QUALITY = 90

# Where the face center lands vertically inside the crop window (0=top). 38% ≈ portrait
# headroom convention — eyes on the upper third without decapitating hair/hats.
FACE_ANCHOR = 0.38

FITS = ("crop", "pad", "pad_blur")

# Meta's fetcher rejects Flickr `_o` Original URLs but takes derivatives of the same
# photo. Staging uploads are ≤1440px, so "Large 2048" is their exact native pixels.
STAGING_URL_PREFERENCE = ("Large 2048", "Large 1600", "Large", "Medium 800")


# -----------------------------------------------------------------------------
# Ratio support probing
# -----------------------------------------------------------------------------

def supported_floor(db: Session) -> tuple[float, str, bool]:
    """Return (min_aspect, ratio_key, tested). Optimistic 3:4 until proven otherwise."""
    row = db.get(AppConfig, RATIO_CONFIG_KEY)
    val = (row.value or "").strip() if row else ""
    if val == "4:5":
        return RATIOS["4:5"], "4:5", True
    if val == "3:4":
        return RATIOS["3:4"], "3:4", True
    return RATIOS["3:4"], "3:4", False


def record_floor(db: Session, ratio_key: str) -> None:
    row = db.get(AppConfig, RATIO_CONFIG_KEY)
    if row:
        row.value = ratio_key
    else:
        db.add(AppConfig(key=RATIO_CONFIG_KEY, value=ratio_key))
    db.commit()
    log.info("instagram portrait floor recorded: %s", ratio_key)


def is_aspect_error(err: Exception) -> bool:
    """Meta rejects out-of-range containers with an aspect-ratio message. That's the
    signal to fall back from the optimistic 3:4 to the documented 4:5."""
    return "aspect ratio" in str(err).lower()


def needs_transform(ratio: float | None, floor: float) -> bool:
    """Unknown dims → False (let Meta judge the untouched rendition)."""
    if not ratio:
        return False
    return ratio < floor - EPS or ratio > MAX_ASPECT + EPS


# -----------------------------------------------------------------------------
# Rendering
# -----------------------------------------------------------------------------

def _source_path(post: Post, *, prefer_preview: bool = False) -> Path:
    """prefer_preview: the 1600px cached preview — plenty for a 540px editor preview and
    ~50× faster to decode than a 60MP original under a live slider."""
    original = Path(post.original_path) if post.original_path else None
    preview = storage.preview_path(post.id)
    order = (preview, original) if prefer_preview else (original, preview)
    for p in order:
        if p and p.exists():
            return p
    raise FileNotFoundError(
        f"no source image for post {post.id[:8]} (original purged, no preview cached)"
    )


def auto_offset(src: Path, *, crop_frac: float, vertical: bool) -> float:
    """Face-anchored default for the crop window position, 0..1 along the cropped axis.

    crop_frac is window_size / image_size on that axis. Places the detected face center
    at FACE_ANCHOR inside the window; center-crops when no face is found (side profiles,
    full-body silhouettes — Haar misses those, and center is the least-wrong default).
    """
    center = faces.detect_face_center(src)
    if center is None:
        return 0.5
    face_along_axis = center[1] if vertical else center[0]
    movable = 1.0 - crop_frac
    if movable <= 0:
        return 0.5
    # offset*movable = window top; we want face_along_axis = top + FACE_ANCHOR*crop_frac
    offset = (face_along_axis - FACE_ANCHOR * crop_frac) / movable
    return min(1.0, max(0.0, offset))


def render_variant(
    src: Path,
    *,
    target_ratio: float,
    fit: str = "crop",
    offset: float | None = None,
    out_width: int = OUT_WIDTH,
    quality: int = JPEG_QUALITY,
) -> bytes:
    """Render src to a JPEG at exactly target_ratio (w/h).

    fit="crop": slide a target_ratio window across the long axis. offset 0..1 positions
    it (0=top/left, 1=bottom/right); None = face-anchored auto.
    fit="pad": letterbox on black. fit="pad_blur": letterbox on a blurred, darkened
    cover-fill of the photo itself (what most social tools do — reads less like bars).
    """
    if fit not in FITS:
        raise ValueError(f"unknown fit {fit!r}")

    out_w = out_width
    out_h = int(round(out_w / target_ratio))

    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.width, img.height
        ratio = w / h

        if fit == "crop":
            if ratio < target_ratio:  # too tall — crop vertically
                crop_h = int(round(w / target_ratio))
                crop_frac = crop_h / h
                if offset is None:
                    offset = auto_offset(src, crop_frac=crop_frac, vertical=True)
                top = int(round(min(1.0, max(0.0, offset)) * (h - crop_h)))
                box = (0, top, w, top + crop_h)
            else:  # too wide (pano) — crop horizontally
                crop_w = int(round(h * target_ratio))
                crop_frac = crop_w / w
                if offset is None:
                    offset = auto_offset(src, crop_frac=crop_frac, vertical=False)
                left = int(round(min(1.0, max(0.0, offset)) * (w - crop_w)))
                box = (left, 0, left + crop_w, h)
            out = img.crop(box).resize((out_w, out_h), Image.LANCZOS)
        else:
            scale = min(out_w / w, out_h / h)
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            scaled = img.resize((new_w, new_h), Image.LANCZOS)
            if fit == "pad_blur":
                bg = ImageOps.fit(img, (out_w, out_h), Image.LANCZOS)
                # Heavy blur + darken: texture without competing with the photo.
                bg = bg.filter(ImageFilter.GaussianBlur(radius=40))
                bg = Image.eval(bg, lambda px: int(px * 0.45))
            else:
                bg = Image.new("RGB", (out_w, out_h), (0, 0, 0))
            bg.paste(scaled, ((out_w - new_w) // 2, (out_h - new_h) // 2))
            out = bg

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def render_preview(db: Session, post: Post, *, fit: str, offset: float | None,
                   width: int = 540) -> tuple[bytes, str]:
    """Small inline preview for the editor UI. Returns (jpeg_bytes, ratio_key) so the
    frontend can label what the live post will use."""
    floor, ratio_key, _tested = supported_floor(db)
    src = _source_path(post, prefer_preview=True)
    ratio = (post.width / post.height) if post.width and post.height else None
    target = MAX_ASPECT if (ratio and ratio > MAX_ASPECT + EPS) else floor
    data = render_variant(
        src, target_ratio=target, fit=fit, offset=offset, out_width=width, quality=82,
    )
    return data, ratio_key


# -----------------------------------------------------------------------------
# Flickr staging
# -----------------------------------------------------------------------------

def _encode_staging(photo_id: str, ratio_key: str) -> str:
    return f"{photo_id}|{ratio_key}"


def _decode_staging(value: str | None) -> tuple[str, str] | None:
    if not value or "|" not in value:
        return None
    photo_id, ratio_key = value.split("|", 1)
    return (photo_id, ratio_key) if photo_id else None


def ensure_staged(
    db: Session,
    post: Post,
    pp: PostPlatform | None,
    *,
    platform_id: str,
    ratio_key: str,
    fit: str,
    offset: float | None,
    force: bool = False,
) -> tuple[str, str]:
    """Upload (or reuse) the hidden staging variant. Returns (staging_photo_id, url).

    The staging id is committed to post_platforms *before* the publish attempt so a
    retry after a transient failure reuses the upload instead of re-staging — and so
    the daily orphan sweep can tell in-flight variants from abandoned ones.
    """
    existing = _decode_staging(pp.staging_remote_id if pp else None)
    if existing and not force:
        staged_id, staged_ratio = existing
        if staged_ratio == ratio_key:
            try:
                return staged_id, flickr.get_display_image_url(
                    db, staged_id, preference=STAGING_URL_PREFERENCE
                )
            except flickr.FlickrError:
                log.info("staged variant %s gone from Flickr — re-staging", staged_id)
        else:
            _delete_photo(db, staged_id)

    src = _source_path(post)
    data = render_variant(
        src, target_ratio=RATIOS[ratio_key], fit=fit, offset=offset
    )
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        staging_id = flickr.upload_photo(
            db=db,
            image_path=tmp_path,
            title=f"IG variant — {post.title or post.id[:8]}",
            description="FramePost staging image for Instagram ingest — auto-deleted.",
            tags=flickr.format_tags(None, machine_tags=[f"{MACHINE_TAG_NS}={post.id}"]),
            privacy="private",
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    if pp is None:
        pp = db.get(PostPlatform, (post.id, platform_id))
    if pp is None:
        pp = PostPlatform(post_id=post.id, platform_id=platform_id)
        db.add(pp)
    pp.staging_remote_id = _encode_staging(staging_id, ratio_key)
    db.commit()  # survive the caller's rollback if the publish attempt fails

    url = flickr.get_display_image_url(db, staging_id, preference=STAGING_URL_PREFERENCE)
    _wait_until_fetchable(url)
    log.info("post %s: staged IG %s variant as flickr %s", post.id[:8], ratio_key, staging_id)
    return staging_id, url


def _wait_until_fetchable(url: str, *, tries: int = 8, interval: float = 5.0) -> None:
    """Block until the staging URL actually serves the JPEG. Flickr's CDN can lag a few
    seconds behind the upload API, and Meta fetches image_url the instant the container
    is created — handing it a not-yet-propagated URL fails the whole publish."""
    for attempt in range(tries):
        try:
            r = httpx.head(url, timeout=15.0, follow_redirects=True)
            if r.status_code == 200 and "image" in (r.headers.get("content-type") or ""):
                if attempt:
                    log.info("staging URL became fetchable after %.0fs", attempt * interval)
                return
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    log.warning("staging URL still not fetchable after %.0fs — letting Meta try anyway",
                tries * interval)


def _delete_photo(db: Session, photo_id: str) -> None:
    try:
        flickr.rest_call(db, "flickr.photos.delete", photo_id=photo_id)
    except flickr.FlickrError as e:
        log.warning("couldn't delete staging photo %s (%s) — daily sweep will retry",
                    photo_id, e)


def cleanup_staged(db: Session, pp: PostPlatform | None) -> None:
    """Best-effort removal after a successful publish. Caller commits."""
    if pp is None:
        return
    existing = _decode_staging(pp.staging_remote_id)
    if existing:
        _delete_photo(db, existing[0])
    pp.staging_remote_id = None


def purge_orphans(db: Session) -> int:
    """Daily sweep: delete ig_variant-tagged photos on Flickr that no pending
    post_platforms row still claims. Covers crashed fanouts and permanent failures."""
    try:
        root = flickr.rest_call(
            db, "flickr.photos.search",
            user_id="me",
            machine_tags=f"{MACHINE_TAG_NS}=",
            per_page="500",
            extras="date_upload",
        )
    except flickr.FlickrError as e:
        log.warning("ig_variant orphan sweep skipped: %s", e)
        return 0

    active: set[str] = set()
    for (value,) in db.execute(
        select(PostPlatform.staging_remote_id).where(
            PostPlatform.staging_remote_id.is_not(None)
        )
    ).all():
        decoded = _decode_staging(value)
        if decoded:
            active.add(decoded[0])

    now = datetime.now(timezone.utc).timestamp()
    removed = 0
    for ph in root.findall("photos/photo"):
        pid = ph.get("id")
        if not pid or pid in active:
            continue
        try:
            uploaded = float(ph.get("dateupload") or 0)
        except ValueError:
            uploaded = 0
        if now - uploaded < 48 * 3600:
            continue  # too fresh — might belong to an in-flight fanout mid-commit
        _delete_photo(db, pid)
        removed += 1
    if removed:
        log.info("ig_variant orphan sweep removed %d staging photos", removed)
    return removed
