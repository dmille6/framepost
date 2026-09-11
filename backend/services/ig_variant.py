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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AppConfig, Post, PostPlatform
from services import faces, storage
from services.platforms import flickr
from services import channel_health, publish_errors, r2

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

Rect = tuple[float, float, float, float]

# Meta's fetcher rejects Flickr `_o` Original URLs but takes derivatives of the same
# photo. Staging uploads are ≤1440px, so "Large 2048" is their exact native pixels.
STAGING_URL_PREFERENCE = ("Large 2048", "Large 1600", "Large", "Medium 800")

# Staging refs are "<remote>|<ratio_key>". A bare remote is a Flickr photo id; this
# prefix marks an R2 object key instead. One column, two backends, and the existing
# orphan sweep keeps working for both.
R2_PREFIX = "r2:"

# Ratio key recorded when the photo needed no reshaping and was staged as-is.
NATIVE_RATIO_KEY = "native"


def _is_r2(ref: str) -> bool:
    return ref.startswith(R2_PREFIX)


def _r2_key(ref: str) -> str:
    return ref[len(R2_PREFIX):]


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


def _offset_for_anchor(along_axis: float, crop_frac: float) -> float:
    """Window position, 0..1, that puts `along_axis` at FACE_ANCHOR inside the window."""
    movable = 1.0 - crop_frac
    if movable <= 0:
        return 0.5
    # offset*movable = window top; we want along_axis = top + FACE_ANCHOR*crop_frac
    return min(1.0, max(0.0, (along_axis - FACE_ANCHOR * crop_frac) / movable))


def focal_for(post: Post) -> tuple[float, float] | None:
    """The photographer's anchor for this post, or None to fall back to detection.

    Both coordinates must be present — a half-written point is treated as absent rather
    than paired with a guess, matching rect_for().
    """
    x, y = post.ig_focal_x, post.ig_focal_y
    if x is None or y is None:
        return None
    return (min(max(0.0, float(x)), 1.0), min(max(0.0, float(y)), 1.0))


def anchor_for(src: Path, focal: tuple[float, float] | None) -> tuple[float, float]:
    """Effective anchor point: the photographer's if set, else the detected face, else
    the centre. Detection is skipped entirely when a focal point exists — it's both
    wasted work and a worse answer than the one the photographer already gave."""
    if focal is not None:
        return focal
    return faces.detect_face_center(src) or (0.5, 0.5)


def auto_offset(
    src: Path,
    *,
    crop_frac: float,
    vertical: bool,
    focal: tuple[float, float] | None = None,
) -> float:
    """Anchored default for the crop window position, 0..1 along the cropped axis.

    crop_frac is window_size / image_size on that axis. Places the anchor at FACE_ANCHOR
    inside the window; center-crops when there's nothing to anchor on (side profiles,
    full-body silhouettes — Haar misses those, and center is the least-wrong default).

    focal is the photographer's own anchor. Face detection answers "where is the face",
    which is usually but not always "what is this photograph about" — a hand on a fire
    fan, a back-turned drop, the wrong face in a duo act. When they've told us, use it.
    """
    center = focal if focal is not None else faces.detect_face_center(src)
    if center is None:
        return 0.5
    return _offset_for_anchor(center[1] if vertical else center[0], crop_frac)


def auto_window(
    src_w: int, src_h: int, *, target_ratio: float, anchor: tuple[float, float]
) -> Rect:
    """The window auto would cut, as a normalized rect — no image decode required.

    Mirrors the auto branch of render_variant so the crop editor can draw the box the
    worker will actually use. Without it the editor had to approximate with a centre
    crop, which meant moving the anchor changed the published photo but not the preview.
    """
    if not src_w or not src_h:
        return (0.0, 0.0, 1.0, 1.0)
    ratio = src_w / src_h
    if ratio < target_ratio:            # too tall — crop vertically
        frac = min(1.0, (src_w / target_ratio) / src_h)
        return (0.0, _offset_for_anchor(anchor[1], frac) * (1.0 - frac), 1.0, frac)
    if ratio > target_ratio:            # too wide (pano) — crop horizontally
        frac = min(1.0, (src_h * target_ratio) / src_w)
        return (_offset_for_anchor(anchor[0], frac) * (1.0 - frac), 0.0, frac, 1.0)
    return (0.0, 0.0, 1.0, 1.0)


def fit_rect_to_ratio(rect: Rect, src_w: int, src_h: int, target_ratio: float) -> Rect:
    """Largest target_ratio window that fits inside `rect`, sharing its centre.

    A stored rect carries the ratio it was authored against. If the runtime probe later
    moves the floor (4:5 <-> 3:4) the old rect no longer matches the new target, so
    rather than distorting the photo we keep the photographer's centre of interest and
    re-fit the window around it. When the rect already matches, this returns it unchanged.
    """
    x, y, w, h = rect
    # Work in pixels: a normalized rect is not square, so ratios don't survive naively.
    pw, ph = w * src_w, h * src_h
    if ph <= 0 or pw <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    if pw / ph > target_ratio:          # too wide for the target — pull the sides in
        new_pw, new_ph = ph * target_ratio, ph
    else:                               # too tall — pull top and bottom in
        new_pw, new_ph = pw, pw / target_ratio
    cx, cy = (x + w / 2) * src_w, (y + h / 2) * src_h
    left = min(max(0.0, cx - new_pw / 2), max(0.0, src_w - new_pw))
    top = min(max(0.0, cy - new_ph / 2), max(0.0, src_h - new_ph))
    return (left / src_w, top / src_h, new_pw / src_w, new_ph / src_h)


def render_variant(
    src: Path,
    *,
    target_ratio: float,
    fit: str = "crop",
    offset: float | None = None,
    rect: Rect | None = None,
    focal: tuple[float, float] | None = None,
    out_width: int = OUT_WIDTH,
    quality: int = JPEG_QUALITY,
) -> bytes:
    """Render src to a JPEG at exactly target_ratio (w/h).

    fit="crop" picks its window by the first of these that is given:
      rect    an explicit normalized (x, y, w, h) in 0..1 source coordinates — the crop
              studio's output, and the only form able to express a TIGHTER window than
              maximum area.
      offset  legacy single-axis position, 0=top/left .. 1=bottom/right, always at
              maximum area.
      neither face-anchored auto, or focal-anchored when the post carries a
              photographer-set focal point.
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

        if fit == "crop" and rect is not None:
            # Honour the requested window, but never distort: the output ratio is fixed,
            # so re-fit the target ratio inside the rect rather than stretching it.
            fx, fy, fw, fh = fit_rect_to_ratio(rect, w, h, target_ratio)
            left = int(round(fx * w))
            top = int(round(fy * h))
            right = min(w, left + max(1, int(round(fw * w))))
            bottom = min(h, top + max(1, int(round(fh * h))))
            out = img.crop((left, top, right, bottom)).resize((out_w, out_h), Image.LANCZOS)
        elif fit == "crop":
            if ratio < target_ratio:  # too tall — crop vertically
                crop_h = int(round(w / target_ratio))
                crop_frac = crop_h / h
                if offset is None:
                    offset = auto_offset(src, crop_frac=crop_frac, vertical=True, focal=focal)
                top = int(round(min(1.0, max(0.0, offset)) * (h - crop_h)))
                box = (0, top, w, top + crop_h)
            else:  # too wide (pano) — crop horizontally
                crop_w = int(round(h * target_ratio))
                crop_frac = crop_w / w
                if offset is None:
                    offset = auto_offset(src, crop_frac=crop_frac, vertical=False, focal=focal)
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


def rect_for(post: Post) -> Rect | None:
    """The stored crop window for a post, or None to fall back to offset/auto.

    Every component must be present; a half-written rect is treated as absent rather
    than guessed at.
    """
    vals = (post.ig_crop_x, post.ig_crop_y, post.ig_crop_w, post.ig_crop_h)
    if any(v is None for v in vals):
        return None
    x, y, w, h = (float(v) for v in vals)
    if w <= 0 or h <= 0:
        return None
    # Clamp into range rather than reject: a rect nudged slightly out of bounds by
    # floating-point drift in the browser should still render.
    x = min(max(0.0, x), 1.0)
    y = min(max(0.0, y), 1.0)
    return (x, y, min(w, 1.0 - x), min(h, 1.0 - y))


def render_preview(db: Session, post: Post, *, fit: str, offset: float | None,
                   rect: Rect | None = None, width: int = 540) -> tuple[bytes, str]:
    """Small inline preview for the editor UI. Returns (jpeg_bytes, ratio_key) so the
    frontend can label what the live post will use."""
    floor, ratio_key, _tested = supported_floor(db)
    src = _source_path(post, prefer_preview=True)
    ratio = (post.width / post.height) if post.width and post.height else None
    target = MAX_ASPECT if (ratio and ratio > MAX_ASPECT + EPS) else floor
    # An explicit rect wins (the studio previewing unsaved state); otherwise fall
    # back to whatever is stored on the post.
    data = render_variant(
        src, target_ratio=target, fit=fit, offset=offset,
        rect=rect if rect is not None else rect_for(post),
        focal=focal_for(post),
        out_width=width, quality=82,
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
            if _is_r2(staged_id):
                # Presigned URLs expire, so mint a fresh one rather than reusing the
                # last — the object itself is what we're reusing, not the link.
                return staged_id, r2.presign_get(_r2_key(staged_id))
            try:
                return staged_id, flickr.get_display_image_url(
                    db, staged_id, preference=STAGING_URL_PREFERENCE
                )
            except flickr.FlickrError:
                log.info("staged variant %s gone from Flickr — re-staging", staged_id)
        else:
            _delete_photo(db, staged_id)

    src = _source_path(post)
    if ratio_key == NATIVE_RATIO_KEY:
        # Nothing is out of range — we only re-render so the file we hand Meta comes
        # from a host we control. Target the photo's own ratio so the crop is a no-op.
        with Image.open(src) as _im:
            target_ratio = _im.width / _im.height
    else:
        target_ratio = RATIOS[ratio_key]
    data = render_variant(
        src, target_ratio=target_ratio, fit=fit, offset=offset,
        rect=rect_for(post), focal=focal_for(post),
    )

    if r2.configured():
        key = f"ig/{post.id}/{uuid.uuid4().hex}.jpg"
        r2.put(key, data)
        ref = R2_PREFIX + key
        if pp is None:
            pp = db.get(PostPlatform, (post.id, platform_id))
        if pp is None:
            pp = PostPlatform(post_id=post.id, platform_id=platform_id)
            db.add(pp)
        pp.staging_remote_id = _encode_staging(ref, ratio_key)
        db.commit()  # survive the caller's rollback if the publish attempt fails
        url = r2.presign_get(key)
        log.info("post %s: staged IG %s variant in R2 as %s", post.id[:8], ratio_key, key)
        return ref, url
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
    if _is_r2(photo_id):
        r2.delete(_r2_key(photo_id))
        return
    try:
        flickr.rest_call(db, "flickr.photos.delete", photo_id=photo_id)
    except flickr.FlickrError as e:
        failure = publish_errors.classify("flickr", e)
        if failure.requires_reauth:
            # This is the case that ran silently for weeks: the stored Flickr token
            # predates the delete scope, so the sweep retried nightly and could never
            # succeed. Flag the channel so it asks for a reconnect instead.
            channel_health.flag_reauth(db, "flickr", failure.user_message)
            log.error("staging photo %s can't be deleted: %s", photo_id, failure.user_message)
        else:
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
