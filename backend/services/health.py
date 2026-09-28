"""Aggregate /health data. Brief: System Health & Failure Handling → Health endpoint."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text, func

from config import settings
from database import SessionLocal
from models import AppConfig, PlatformCredential, Post
from services import storage

VERSION = "0.1.0"
HEARTBEAT_TTL_SECONDS = 120  # brief: "last heartbeat within 2 minutes"

# Token-expiry warning threshold. Deliberately BELOW the 7-day auto-refresh leeway:
# a healthy token rides down to ~7 days and silently renews, so anything under 5 days
# means the daily refresh has been failing for 2+ days and a human needs to know.
TOKEN_WARN_DAYS = 5


def _platform_warnings(db) -> list[dict[str, str]]:
    """Credential problems worth a banner: expired/expiring tokens, sticky errors."""
    out: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)
    creds = db.execute(
        select(PlatformCredential).where(PlatformCredential.access_token.is_not(None))
    ).scalars().all()
    pending = db.execute(
        select(func.count()).select_from(Post).where(
            Post.status == "pending", Post.scheduled_at.is_not(None)
        )
    ).scalar_one()

    for cred in creds:
        if cred.auth_status == "reauth_required":
            # The blast radius is what makes this actionable. "Flickr error" gets
            # ignored for a fortnight; "231 scheduled posts affected" does not.
            impact = (f" {pending} scheduled post{'s' if pending != 1 else ''} affected."
                      if pending and cred.default_target else "")
            out.append({
                "platform": cred.platform,
                "severity": "error",
                "message": (f"{cred.auth_error or cred.platform.capitalize() + ' needs reconnecting.'}"
                            f"{impact} Reconnect in Settings → Platforms."),
            })
            continue
        expires = cred.token_expires
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                out.append({
                    "platform": cred.platform,
                    "severity": "error",
                    "message": f"{cred.platform.capitalize()} token has EXPIRED — posts to it "
                               "are failing. Reconnect in Settings → Platforms.",
                })
                continue
            days_left = (expires - now).days
            if days_left < TOKEN_WARN_DAYS:
                out.append({
                    "platform": cred.platform,
                    "severity": "warning",
                    "message": f"{cred.platform.capitalize()} token expires in {days_left} "
                               f"day{'s' if days_left != 1 else ''} and auto-refresh isn't "
                               "keeping up — check Settings → Platforms.",
                })
        if cred.last_error and not cred.last_success_at:
            # Never-succeeded credential with an error — connection is effectively dead.
            out.append({
                "platform": cred.platform,
                "severity": "warning",
                "message": f"{cred.platform.capitalize()}: {cred.last_error[:140]}",
            })
    return out


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


def _backup_warnings(last_backup: str | None) -> list[str]:
    """The daily backup gets a two-day grace. A saved success timestamp alone is
    insufficient if rotation/manual cleanup removed every file afterwards.

    Off-box pushes currently write only a host log outside the container mounts; this
    check cannot assert remote health from a fresh local backup.
    """
    try:
        exists = any(p.is_file() and p.stat().st_size > 0
                     for p in storage.BACKUP.glob("framepost-*.sqlite"))
    except OSError:
        return ["Local backups cannot be read — check the backup volume."]
    if not exists:
        return ["No local database backup is available. Run a backup in Settings → System."]
    stamp = _parse_iso(last_backup)
    if stamp is None:
        return ["No successful backup time is recorded. Run a backup in Settings → System."]
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - stamp
    if age > timedelta(days=2):
        return [f"Last backup was {age.days} days ago. Check Settings → System."]
    return []


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
        platform_warnings = _platform_warnings(db)
        backup_warnings = _backup_warnings(last_backup)

        if not (db_writable and photo_writable):
            status = "down"
        elif not worker_alive or free_gb < 5.0 or platform_warnings or backup_warnings:
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
            "backup_warnings": backup_warnings,
            "platform_warnings": platform_warnings,
            "version": VERSION,
        }
    finally:
        db.close()
