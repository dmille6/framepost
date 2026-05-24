"""Aggregate /health data. Brief: System Health & Failure Handling → Health endpoint."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text

from config import settings
from database import SessionLocal
from models import AppConfig

VERSION = "0.1.0"
HEARTBEAT_TTL_SECONDS = 120  # brief: "last heartbeat within 2 minutes"


def _read_config(db, key: str) -> str | None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    return row.value if row else None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def collect_health() -> dict[str, Any]:
    db = SessionLocal()
    try:
        try:
            db.execute(text("SELECT 1"))
            db_writable = True
        except Exception:
            db_writable = False

        photo_root = settings.photo_root
        photo_writable = os.access(photo_root, os.W_OK)
        try:
            usage = shutil.disk_usage(photo_root)
            free_gb = round(usage.free / (1024 ** 3), 2)
        except OSError:
            free_gb = 0.0

        heartbeat = _parse_iso(_read_config(db, "worker_last_heartbeat"))
        worker_alive = bool(
            heartbeat
            and (datetime.now(timezone.utc) - heartbeat) < timedelta(seconds=HEARTBEAT_TTL_SECONDS)
        )

        flickr_last_success = _read_config(db, "flickr_last_success") or None
        last_backup = _read_config(db, "last_backup") or None

        if not (db_writable and photo_writable):
            status = "down"
        elif not worker_alive or free_gb < 5.0:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "worker_alive": worker_alive,
            "db_writable": db_writable,
            "photo_volume_writable": photo_writable,
            "photo_volume_free_gb": free_gb,
            "flickr_last_success": flickr_last_success,
            "last_backup": last_backup,
            "version": VERSION,
        }
    finally:
        db.close()
