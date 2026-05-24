"""SQLite hot backup via the online backup API. Never `cp` the live .db under WAL.

Brief retention: 7 daily, 4 weekly (Sunday's daily promoted), 3 monthly (first Sunday's
weekly promoted). Backups land on /mnt/photo-data/backup/ so they survive an OS-disk failure.

Phase 6 will add the rotation cron. Phase 4 ships the on-demand backup + listing for the
Settings → System tab so the operator can verify the backup pipeline works before relying on
automated rotation.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from services import storage

log = logging.getLogger("framepost.backup")


@dataclass
class BackupFile:
    name: str
    path: str
    size_bytes: int
    created_at: datetime


def _db_path() -> Path:
    url = settings.database_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise RuntimeError(f"expected sqlite:/// URL, got {url}")
    raw = url[len(prefix):]
    return Path(("/" + raw) if raw.startswith("/") else raw)


def run_backup() -> BackupFile:
    """Hot-copy the SQLite DB into /mnt/photo-data/backup/. Returns the new file's metadata."""
    storage.ensure_layout()
    src_path = _db_path()
    if not src_path.exists():
        raise RuntimeError(f"database not found at {src_path}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst_path = storage.BACKUP / f"framepost-{stamp}.sqlite"

    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    stat = dst_path.stat()
    log.info("backup written: %s (%d bytes)", dst_path, stat.st_size)
    return BackupFile(
        name=dst_path.name,
        path=str(dst_path),
        size_bytes=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def list_backups() -> list[BackupFile]:
    storage.ensure_layout()
    out: list[BackupFile] = []
    for p in sorted(storage.BACKUP.glob("framepost-*.sqlite"), reverse=True):
        try:
            stat = p.stat()
        except FileNotFoundError:
            continue
        out.append(
            BackupFile(
                name=p.name,
                path=str(p),
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
        )
    return out


def rotate_backups(*, daily: int = 7, weekly: int = 4, monthly: int = 3) -> list[str]:
    """Rolling retention: keep N most-recent dailies, then 1 per ISO-week up to N weekly,
    then 1 per calendar-month up to N monthly. Delete everything else.

    Returns the list of deleted filenames.
    """
    backups = list_backups()  # newest first
    keep: set[str] = set()

    # Daily tier — keep the N most recent.
    for b in backups[:daily]:
        keep.add(b.path)

    # Weekly tier — keep newest in each (year, ISO-week) bucket not already in `keep`.
    seen_weeks: set[tuple[int, int]] = set()
    weekly_kept = 0
    for b in backups:
        if b.path in keep:
            continue
        wk = (b.created_at.isocalendar().year, b.created_at.isocalendar().week)
        if wk in seen_weeks:
            continue
        seen_weeks.add(wk)
        keep.add(b.path)
        weekly_kept += 1
        if weekly_kept >= weekly:
            break

    # Monthly tier — newest in each (year, month) bucket not already kept.
    seen_months: set[tuple[int, int]] = set()
    monthly_kept = 0
    for b in backups:
        if b.path in keep:
            continue
        mo = (b.created_at.year, b.created_at.month)
        if mo in seen_months:
            continue
        seen_months.add(mo)
        keep.add(b.path)
        monthly_kept += 1
        if monthly_kept >= monthly:
            break

    deleted: list[str] = []
    for b in backups:
        if b.path in keep:
            continue
        try:
            Path(b.path).unlink()
            deleted.append(b.name)
        except OSError as e:
            log.warning("could not delete backup %s: %s", b.name, e)
    if deleted:
        log.info("backup rotation: deleted %d (kept %d)", len(deleted), len(keep))
    return deleted


def wal_checkpoint() -> None:
    """Run a WAL checkpoint to fold the .db-wal file into the main DB. Cheap and safe."""
    import sqlite3
    src_path = _db_path()
    if not src_path.exists():
        return
    con = sqlite3.connect(str(src_path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
