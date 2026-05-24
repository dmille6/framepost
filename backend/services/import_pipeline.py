"""Shared import pipeline used by both browser uploads and the watch folder.

Both paths converge here once the file is on disk somewhere accessible to the backend
(under `/originals/<tmp>.tmp` for HTTP uploads, under `/incoming/<filename>` for watch).
The pipeline:

  1. Disk-full check  →  StorageFull
  2. Hash + size      (chunked read)
  3. Pillow validate  →  image.InvalidImage
  4. Duplicate check  →  DuplicateExists  (caller decides whether to override)
  5. Move to /originals/<post_id>.<ext>
  6. Best-effort EXIF + IPTC
  7. Thumbnail        →  fatal if it fails (UI can't render the card)
  8. Insert post + log 'imported' event

The function consumes `src_path` on the success path (rename to /originals/...).
On failure it leaves the file in place so the caller can move it to errors/ or retry.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from models import Post
from services import duplicate, events, exif, image, iptc, storage

log = logging.getLogger("framepost.import")

CHUNK = 1024 * 1024


class StorageFull(Exception):
    pass


class DuplicateExists(Exception):
    def __init__(self, existing: Post):
        super().__init__(f"duplicate of {existing.id}")
        self.existing = existing


@dataclass
class ImportResult:
    post: Post
    duplicate_of: str | None


def hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _ext_for(filename: str | None, fmt: str) -> str:
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png"}:
            return "jpg" if ext == "jpeg" else ext
    return {"JPEG": "jpg", "MPO": "jpg", "PNG": "png"}.get(fmt, "bin")


def import_image(
    src_path: Path,
    *,
    db: Session,
    source: str,
    actor: str,
    allow_duplicate: bool,
    original_filename: str | None,
) -> ImportResult:
    storage.ensure_layout()

    if storage.below_hardstop(db):
        raise StorageFull(f"photo volume below hardstop ({storage.free_gb():.2f} GB free)")

    sha256, size = hash_file(src_path)

    # image.validate() raises image.InvalidImage on non-image input.
    width, height, fmt = image.validate(src_path)

    existing = duplicate.find_by_hash(db, sha256)
    if existing and not allow_duplicate:
        raise DuplicateExists(existing)

    post_id = uuid.uuid4().hex
    ext = _ext_for(original_filename, fmt)
    final_path = storage.original_path(post_id, ext)
    src_path.rename(final_path)

    exif_fields = exif.extract(str(final_path))
    iptc_fields = iptc.extract(str(final_path))

    thumb_path = storage.thumbnail_path(post_id)
    try:
        image.make_thumbnail(final_path, thumb_path)
    except Exception:
        log.exception("thumbnail generation failed for %s", post_id)
        final_path.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
        raise

    now = datetime.now(timezone.utc)
    post = Post(
        id=post_id,
        title=iptc_fields["title"],
        description=iptc_fields["description"],
        tags=iptc_fields["tags"],
        status="pending",
        original_filename=original_filename,
        original_path=str(final_path),
        thumbnail_path=str(thumb_path),
        file_size_bytes=size,
        width=width,
        height=height,
        sha256=sha256,
        captured_at=exif_fields["captured_at"],
        camera_make=exif_fields["camera_make"],
        camera_model=exif_fields["camera_model"],
        lens=exif_fields["lens"],
        focal_length=exif_fields["focal_length"],
        iso=exif_fields["iso"],
        shutter_speed=exif_fields["shutter_speed"],
        aperture=exif_fields["aperture"],
        gps_lat=exif_fields["gps_lat"],
        gps_lng=exif_fields["gps_lng"],
        exif_raw=exif_fields["exif_raw"],
        iptc_raw=iptc_fields["iptc_raw"],
        created_at=now,
        updated_at=now,
    )
    db.add(post)
    events.log_event(
        db,
        post_id=post_id,
        event_type="imported",
        actor=actor,
        details={
            "source": source,
            "filename": original_filename,
            "bytes": size,
            "format": fmt,
            "had_iptc": bool(
                iptc_fields["title"] or iptc_fields["description"] or iptc_fields["tags"]
            ),
            "duplicate_of": existing.id if existing else None,
        },
    )
    db.commit()
    db.refresh(post)

    # Best-effort AI auto-apply (no-ops unless ai_tagging_enabled + ai_auto_apply are both on).
    # Runs in a fresh DB session inside the helper; never raises.
    from services import ai_tagging  # local import — keeps cold-path latency off the hot path
    try:
        ai_tagging.apply_to_post(post_id)
        # Re-read post after AI may have updated tags/description.
        db.expire(post)
        db.refresh(post)
    except Exception:
        log.exception("ai auto-apply unexpectedly raised — import succeeded regardless")

    return ImportResult(post=post, duplicate_of=existing.id if existing else None)


def move_to_errors(src: Path, reason: str) -> Path:
    """Move a failed-import source file into errors/ with a sidecar .log explaining why."""
    storage.ensure_layout()
    if not src.exists():
        return src
    dst = storage.ERRORS / src.name
    counter = 1
    while dst.exists():
        dst = storage.ERRORS / f"{src.stem}.{counter}{src.suffix}"
        counter += 1
    src.rename(dst)
    log_path = dst.with_suffix(dst.suffix + ".log")
    stamp = datetime.now(timezone.utc).isoformat()
    log_path.write_text(f"{stamp}\nimport failed: {reason}\noriginal source: {src}\n")
    return dst


def metadata_for_log(result: ImportResult) -> dict[str, Any]:
    p = result.post
    return {"id": p.id, "title": p.title, "filename": p.original_filename}
