"""Watch-folder import (Phase 2).

Uses watchdog's PollingObserver — inotify is unreliable on SMB-mounted paths because
Windows/Mac SMB clients don't generate Linux inotify events. Polling is slower but reliable.

The worker process owns this. The backend reads status by reading mirrored app_config rows.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from sqlalchemy import select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from database import SessionLocal
from models import AppConfig
from services import image, import_pipeline

log = logging.getLogger("framepost.watcher")

POLL_INTERVAL = 5.0
STABILITY_CHECK = 5.0
STABILITY_TIMEOUT = 120.0
IMG_SUFFIXES = {".jpg", ".jpeg", ".png"}


class _Status:
    alive: bool = False
    path: str = ""
    last_imported_at: datetime | None = None
    last_error: str | None = None
    error_count: int = 0


_status = _Status()
_observer: PollingObserver | None = None
_queue: Queue = Queue()
_worker_thread: threading.Thread | None = None
_lock = threading.Lock()


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: Queue):
        self.queue = queue

    def _enqueue(self, path: str) -> None:
        if Path(path).suffix.lower() in IMG_SUFFIXES:
            self.queue.put(path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._enqueue(event.dest_path)


def _wait_stable(p: Path) -> bool:
    """Block until size+mtime are unchanged for one full STABILITY_CHECK interval, or time out."""
    last: tuple[int, float] | None = None
    elapsed = 0.0
    while elapsed <= STABILITY_TIMEOUT:
        try:
            stat = p.stat()
        except FileNotFoundError:
            return False
        cur = (stat.st_size, stat.st_mtime)
        if cur[0] > 0 and cur == last:
            return True
        last = cur
        time.sleep(STABILITY_CHECK)
        elapsed += STABILITY_CHECK
    return False


def _persist_status(db) -> None:
    rows = {
        "watch_folder_status": "active" if _status.alive else "inactive",
        "watch_folder_last_imported_at": _status.last_imported_at.isoformat() if _status.last_imported_at else "",
        "watch_folder_last_error": _status.last_error or "",
        "watch_folder_error_count": str(_status.error_count),
    }
    for k, v in rows.items():
        existing = db.execute(select(AppConfig).where(AppConfig.key == k)).scalar_one_or_none()
        if existing:
            existing.value = v
        else:
            db.add(AppConfig(key=k, value=v))


def _process(path: Path) -> None:
    if not _wait_stable(path):
        log.warning("watch-folder file never stabilized: %s", path)
        return
    if not path.exists():
        return

    db = SessionLocal()
    try:
        try:
            result = import_pipeline.import_image(
                path,
                db=db,
                source="watch_folder",
                actor="watcher",
                allow_duplicate=False,
                original_filename=path.name,
            )
            _status.last_imported_at = datetime.now(timezone.utc)
            _status.last_error = None
            log.info("watcher imported %s as post %s", path.name, result.post.id[:8])
        except import_pipeline.DuplicateExists as e:
            import_pipeline.move_to_errors(path, f"duplicate of {e.existing.id}")
            _status.error_count += 1
            _status.last_error = f"duplicate ({path.name})"
        except image.InvalidImage as e:
            import_pipeline.move_to_errors(path, f"invalid image: {e}")
            _status.error_count += 1
            _status.last_error = f"invalid image ({path.name})"
        except import_pipeline.StorageFull as e:
            _status.last_error = f"storage full: {e}"
            log.error("watcher: storage full, leaving %s in place", path.name)
        except Exception as e:  # noqa: BLE001 — broad on purpose: any pipeline failure is "move to errors"
            log.exception("watcher: import failed for %s", path.name)
            import_pipeline.move_to_errors(path, f"unexpected error: {e}")
            _status.error_count += 1
            _status.last_error = str(e)
        _persist_status(db)
        db.commit()
    finally:
        db.close()


def _worker_loop() -> None:
    while True:
        try:
            path_str = _queue.get(timeout=1.0)
        except Empty:
            continue
        if path_str is None:
            return
        try:
            _process(Path(path_str))
        except Exception:
            log.exception("watcher worker tick failed")


def is_running() -> bool:
    with _lock:
        return _observer is not None and _observer.is_alive()


def status() -> dict[str, Any]:
    return {
        "alive": _status.alive,
        "path": _status.path,
        "last_imported_at": _status.last_imported_at.isoformat() if _status.last_imported_at else None,
        "last_error": _status.last_error,
        "error_count": _status.error_count,
    }


def start(path: str) -> None:
    global _observer, _worker_thread
    with _lock:
        target = Path(path)
        if not target.exists() or not target.is_dir():
            _status.alive = False
            _status.path = path
            _status.last_error = f"path not found or not a directory: {path}"
            log.warning("watcher start refused: %s", _status.last_error)
            return
        if _observer is not None and _observer.is_alive():
            _observer.stop()
            _observer.join(timeout=10)
            _observer = None
        observer = PollingObserver(timeout=POLL_INTERVAL)
        observer.schedule(_Handler(_queue), str(target), recursive=False)
        observer.daemon = True
        observer.start()
        if _worker_thread is None or not _worker_thread.is_alive():
            t = threading.Thread(target=_worker_loop, daemon=True, name="watcher-worker")
            t.start()
            _worker_thread = t
        _observer = observer
        _status.alive = True
        _status.path = path
        _status.last_error = None
        log.info("watcher started on %s", path)

        # Pick up files dropped while we were down.
        for child in target.iterdir():
            if child.is_file() and child.suffix.lower() in IMG_SUFFIXES:
                _queue.put(str(child))


def stop() -> None:
    global _observer
    with _lock:
        if _observer is not None:
            _observer.stop()
            _observer.join(timeout=10)
            _observer = None
        _status.alive = False
        log.info("watcher stopped")


def reconcile() -> None:
    """Periodic job. Aligns the observer with current app_config values."""
    db = SessionLocal()
    try:
        rows = {
            r.key: r.value
            for r in db.execute(
                select(AppConfig).where(
                    AppConfig.key.in_(["watch_folder_enabled", "watch_folder_path"])
                )
            )
            .scalars()
            .all()
        }
        enabled = (rows.get("watch_folder_enabled") or "false").lower() == "true"
        path = rows.get("watch_folder_path") or ""
        changed = False
        if enabled and path:
            if not is_running() or _status.path != path:
                start(path)
                changed = True
        else:
            if is_running():
                stop()
                changed = True
        if changed:
            _persist_status(db)
            db.commit()
    finally:
        db.close()
