"""Filesystem layout helpers + disk-full hard-stop check.

Brief: storage layout under /mnt/photo-data/. Originals retained 30 days, thumbnails permanent.
Hard-stop refuses uploads when free space drops below storage_hardstop_gb (default 5).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from models import AppConfig

ROOT = Path(settings.photo_root)
INCOMING = ROOT / "incoming"
ORIGINALS = ROOT / "originals"
THUMBNAILS = ROOT / "thumbnails"
DERIVATIVES = ROOT / "derivatives"
PREVIEWS = ROOT / "previews"
ERRORS = ROOT / "errors"
BACKUP = ROOT / "backup"
REELS = ROOT / "reels"


def ensure_layout() -> None:
    for p in (INCOMING, ORIGINALS, THUMBNAILS, DERIVATIVES, PREVIEWS, ERRORS, BACKUP, REELS):
        p.mkdir(parents=True, exist_ok=True)


def free_gb() -> float:
    return shutil.disk_usage(ROOT).free / (1024 ** 3)


def hardstop_gb(db: Session) -> float:
    row = db.execute(select(AppConfig).where(AppConfig.key == "storage_hardstop_gb")).scalar_one_or_none()
    return float(row.value) if row and row.value else 5.0


def below_hardstop(db: Session) -> bool:
    return free_gb() < hardstop_gb(db)


def original_path(post_id: str, ext: str) -> Path:
    ext = ext.lower().lstrip(".")
    return ORIGINALS / f"{post_id}.{ext}"


def thumbnail_path(post_id: str) -> Path:
    return THUMBNAILS / f"{post_id}.jpg"


def preview_path(post_id: str) -> Path:
    """Cached 1600-px preview used by the lightbox in the metadata editor."""
    return PREVIEWS / f"{post_id}.jpg"


def reel_path(reel_id: str) -> Path:
    return REELS / f"{reel_id}.mp4"
