"""Config API. Watch-folder controls + a whitelisted generic key/value endpoint."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import AppConfig, User
from routes.auth import current_user

router = APIRouter()


# --- shared helpers ---

def _get(db: Session, key: str) -> str | None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    return row.value if row else None


def _set(db: Session, key: str, value: str) -> None:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppConfig(key=key, value=value))


# --- watch-folder (Phase 2) ---

class WatchConfig(BaseModel):
    enabled: bool
    path: str
    status: str
    last_imported_at: str | None
    last_error: str | None
    error_count: int


class WatchConfigUpdate(BaseModel):
    enabled: bool | None = None
    path: str | None = None


def _build_watch_config(db: Session) -> WatchConfig:
    return WatchConfig(
        enabled=(_get(db, "watch_folder_enabled") or "false").lower() == "true",
        path=_get(db, "watch_folder_path") or "",
        status=_get(db, "watch_folder_status") or "inactive",
        last_imported_at=(_get(db, "watch_folder_last_imported_at") or None) or None,
        last_error=(_get(db, "watch_folder_last_error") or None) or None,
        error_count=int(_get(db, "watch_folder_error_count") or "0"),
    )


@router.get("/watch", response_model=WatchConfig)
def get_watch(db: Session = Depends(get_session), _user: User = Depends(current_user)):
    return _build_watch_config(db)


@router.put("/watch", response_model=WatchConfig)
def put_watch(
    body: WatchConfigUpdate,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    if body.path is not None:
        candidate = Path(body.path)
        if body.path and (not candidate.exists() or not candidate.is_dir()):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"path not found or not a directory: {body.path}",
            )
        _set(db, "watch_folder_path", body.path)
    if body.enabled is not None:
        _set(db, "watch_folder_enabled", "true" if body.enabled else "false")
    db.commit()
    return _build_watch_config(db)


# --- generic whitelisted config (Phase 4 — General + System tabs) ---

# Editable keys with validators. Anything not on this whitelist is silently dropped on PATCH.
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_PRIVACY = {"private", "friends_family", "public"}
_SAFETY = {"safe", "moderate", "restricted"}
_CONTENT = {"photo", "screenshot", "other"}
_START_PAGE = {"draft_queue", "scheduled", "published"}


def _v_int(min_v: int | None = None, max_v: int | None = None):
    def check(raw: Any) -> str:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError("must be an integer")
        if min_v is not None and n < min_v:
            raise ValueError(f"must be ≥ {min_v}")
        if max_v is not None and n > max_v:
            raise ValueError(f"must be ≤ {max_v}")
        return str(n)
    return check


def _v_str(max_len: int = 200, allow_empty: bool = True):
    def check(raw: Any) -> str:
        s = str(raw)
        if not allow_empty and not s.strip():
            raise ValueError("required")
        if len(s) > max_len:
            raise ValueError(f"must be ≤ {max_len} chars")
        return s
    return check


def _v_enum(values: set[str]):
    def check(raw: Any) -> str:
        s = str(raw)
        if s not in values:
            raise ValueError(f"must be one of {sorted(values)}")
        return s
    return check


def _v_time(raw: Any) -> str:
    s = str(raw)
    if not _TIME_RE.fullmatch(s):
        raise ValueError("must be HH:MM in 24-hour format")
    return s


def _v_csv_int(raw: Any) -> str:
    s = str(raw)
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        raise ValueError("must have at least one value")
    for p in parts:
        try:
            int(p)
        except ValueError:
            raise ValueError(f"'{p}' is not an integer")
    return ",".join(parts)


def _v_hashtag_list(raw: Any) -> str:
    """Normalize a space/comma-separated tag list to clean lowercase tokens.

    Accepts inputs like "#photography burlesque, art" → "photography burlesque art".
    Each token is stripped of leading '#' and non-alphanumeric/_ chars. Empty/blank
    inputs are allowed (means "no defaults"). Output is space-separated for storage.
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if len(s) > 500:
        raise ValueError("must be ≤ 500 chars")
    out: list[str] = []
    seen: set[str] = set()
    for raw_token in s.replace(",", " ").split():
        token = raw_token.lstrip("#").strip().lower()
        cleaned = "".join(ch for ch in token if ch.isalnum() or ch == "_")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) > 30:
            raise ValueError("too many tags (max 30)")
    return " ".join(out)


def _v_subreddit_list(raw: Any) -> str:
    """Normalize a list of subreddit names to space-separated, no leading 'r/' or '#'.

    Reddit subreddit names: 3-21 chars, alphanumeric + underscore, can't start with digit.
    We're permissive on input and strict on output — duplicates dropped, casing preserved
    (sub names are display-case; Reddit URLs are case-insensitive).
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if len(s) > 600:
        raise ValueError("must be ≤ 600 chars")
    out: list[str] = []
    seen: set[str] = set()
    for raw_token in s.replace(",", " ").split():
        token = raw_token.strip().lstrip("#").lstrip("/")
        if token.lower().startswith("r/"):
            token = token[2:]
        # Validate against Reddit's rules.
        if not (3 <= len(token) <= 21):
            raise ValueError(f"'{token}' must be 3-21 chars")
        if not all(c.isalnum() or c == "_" for c in token):
            raise ValueError(f"'{token}' has invalid characters")
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) > 30:
            raise ValueError("too many subreddits (max 30)")
    return " ".join(out)


_EDITABLE: dict[str, Any] = {
    "studio_name": _v_str(max_len=120),
    "instagram_signature": _v_str(max_len=500),
    "bluesky_default_hashtags": _v_hashtag_list,
    "reddit_subreddits": _v_subreddit_list,
    "timezone": _v_str(max_len=64, allow_empty=False),
    "start_page": _v_enum(_START_PAGE),
    "session_timeout_minutes": _v_int(5, 60 * 24 * 30),
    "default_publish_time": _v_time,
    "default_privacy": _v_enum(_PRIVACY),
    "default_safety_level": _v_enum(_SAFETY),
    "default_content_type": _v_enum(_CONTENT),
    "original_retention_days": _v_int(1, 3650),
    "storage_warning_percent": _v_int(50, 99),
    "storage_hardstop_gb": _v_int(1, 1000),
    "cleanup_time": _v_time,
    "flickr_sync_time": _v_time,
    "flickr_max_long_edge": _v_int(0, 8192),
    "max_groups_default": _v_int(1, 30),
    "warn_groups_threshold": _v_int(1, 30),
    "schedule_fuzz_minutes": _v_int(0, 30),
    "retry_max_attempts": _v_int(1, 20),
    "retry_backoff_minutes": _v_csv_int,
    "upload_max_mb": _v_int(1, 1000),
    "theme": _v_enum({"dark"}),  # locked to dark for v1
}

# Read-only keys returned in GET so the UI can display them.
_READONLY = {
    "photo_root",
    "watch_folder_status",
    "watch_folder_last_imported_at",
    "watch_folder_last_error",
    "watch_folder_error_count",
    "worker_last_heartbeat",
    "flickr_last_success",
    "last_backup",
}


@router.get("")
def get_config(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, str | None]:
    keys = list(_EDITABLE.keys()) + list(_READONLY)
    rows = {
        r.key: r.value
        for r in db.execute(select(AppConfig).where(AppConfig.key.in_(keys))).scalars().all()
    }
    return {k: rows.get(k) for k in keys}


@router.patch("")
def patch_config(
    body: dict[str, Any],
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, str | None]:
    errors: dict[str, str] = {}
    cleaned: dict[str, str] = {}
    for k, v in body.items():
        if k not in _EDITABLE:
            continue  # silently ignore unknown / read-only keys
        try:
            cleaned[k] = _EDITABLE[k](v)
        except ValueError as e:
            errors[k] = str(e)
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"validation": errors})

    for k, v in cleaned.items():
        _set(db, k, v)
    db.commit()
    # Return a fresh full read so the UI doesn't have to.
    keys = list(_EDITABLE.keys()) + list(_READONLY)
    rows = {
        r.key: r.value
        for r in db.execute(select(AppConfig).where(AppConfig.key.in_(keys))).scalars().all()
    }
    return {k: rows.get(k) for k in keys}
