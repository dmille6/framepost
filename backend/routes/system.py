"""System endpoints — disk usage, backup management, activity feed, metrics. Auth-gated."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from config import settings
from database import get_session
from models import AppConfig, DiskSample, Post, PostEvent, User
from routes.auth import current_user
from services import backup

router = APIRouter()


class BackupOut(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime


class DiskUsageOut(BaseModel):
    photo_root: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float
    warning_percent: int
    hardstop_gb: int


@router.get("/disk", response_model=DiskUsageOut)
def disk_usage(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    try:
        usage = shutil.disk_usage(settings.photo_root)
    except OSError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"can't stat photo root: {e}")
    used_pct = (usage.used / usage.total * 100) if usage.total else 0.0
    warning = _config_int(db, "storage_warning_percent", 80)
    hardstop = _config_int(db, "storage_hardstop_gb", 5)
    return DiskUsageOut(
        photo_root=settings.photo_root,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        used_percent=round(used_pct, 2),
        warning_percent=warning,
        hardstop_gb=hardstop,
    )


class DiskSamplePoint(BaseModel):
    sampled_at: datetime
    total_bytes: int
    used_bytes: int
    free_bytes: int

    class Config:
        from_attributes = True


@router.get("/disk-history", response_model=list[DiskSamplePoint])
def disk_history(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    hours: int = Query(default=24 * 7, ge=1, le=24 * 30),
):
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    rows = db.execute(
        select(DiskSample)
        .where(DiskSample.sampled_at >= cutoff)
        .order_by(DiskSample.sampled_at.asc())
    ).scalars().all()
    return [DiskSamplePoint.model_validate(r) for r in rows]


@router.get("/backups", response_model=list[BackupOut])
def list_backups(_user: User = Depends(current_user)):
    return [
        BackupOut(name=b.name, size_bytes=b.size_bytes, created_at=b.created_at)
        for b in backup.list_backups()
    ]


@router.post("/backups", response_model=BackupOut, status_code=status.HTTP_201_CREATED)
def trigger_backup(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> Any:
    try:
        b = backup.run_backup()
    except RuntimeError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
    # Mirror the latest backup time into app_config so /health and the System tab agree.
    row = db.execute(select(AppConfig).where(AppConfig.key == "last_backup")).scalar_one_or_none()
    iso = b.created_at.isoformat()
    if row:
        row.value = iso
    else:
        db.add(AppConfig(key="last_backup", value=iso))
    db.commit()
    return BackupOut(name=b.name, size_bytes=b.size_bytes, created_at=b.created_at)


def _config_int(db: Session, key: str, default: int) -> int:
    row = db.execute(select(AppConfig).where(AppConfig.key == key)).scalar_one_or_none()
    if not row or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


# --- Activity feed (Phase 7A) ---


class ActivityRow(BaseModel):
    id: int
    post_id: str
    post_title: str | None
    post_filename: str | None
    event_type: str
    actor: str
    details: dict[str, Any] | None
    created_at: datetime


@router.get("/activity", response_model=list[ActivityRow])
def list_activity(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(default=None),
    actor: str | None = Query(default=None),
):
    stmt = (
        select(PostEvent, Post.title, Post.original_filename)
        .join(Post, Post.id == PostEvent.post_id)
        .order_by(PostEvent.created_at.desc(), PostEvent.id.desc())
    )
    if event_type:
        stmt = stmt.where(PostEvent.event_type == event_type)
    if actor:
        stmt = stmt.where(PostEvent.actor == actor)
    stmt = stmt.limit(limit).offset(offset)

    rows = db.execute(stmt).all()
    out: list[ActivityRow] = []
    for ev, title, fname in rows:
        details: dict[str, Any] | None = None
        if ev.details:
            try:
                details = json.loads(ev.details)
            except (TypeError, ValueError):
                details = {"raw": ev.details}
        out.append(
            ActivityRow(
                id=ev.id,
                post_id=ev.post_id,
                post_title=title,
                post_filename=fname,
                event_type=ev.event_type,
                actor=ev.actor,
                details=details,
                created_at=ev.created_at,
            )
        )
    return out


# --- Aggregated metrics (Phase 7A) ---


class DayPoint(BaseModel):
    day: str           # YYYY-MM-DD
    imported: int
    posted: int
    failed: int


class MetricsResponse(BaseModel):
    window_days: int
    daily: list[DayPoint]
    totals: dict[str, int]
    counts_now: dict[str, int]
    retry_rate: float           # 0.0 - 1.0
    success_rate: float         # 0.0 - 1.0
    avg_upload_seconds: float | None  # avg between flickr_uploading and flickr_uploaded


@router.get("/metrics", response_model=MetricsResponse)
def metrics(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    days: int = Query(30, ge=1, le=365),
):
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Daily counts of imported / flickr_uploaded / flickr_failed events.
    day_expr = func.strftime("%Y-%m-%d", PostEvent.created_at).label("day")
    rows = db.execute(
        select(
            day_expr,
            func.sum(case((PostEvent.event_type == "imported", 1), else_=0)).label("imported"),
            func.sum(case((PostEvent.event_type == "flickr_uploaded", 1), else_=0)).label("posted"),
            func.sum(case((PostEvent.event_type == "flickr_failed", 1), else_=0)).label("failed"),
        )
        .where(PostEvent.created_at >= cutoff)
        .group_by(day_expr)
        .order_by(day_expr.asc())
    ).all()
    daily = [
        DayPoint(day=r.day, imported=int(r.imported or 0), posted=int(r.posted or 0), failed=int(r.failed or 0))
        for r in rows
    ]

    totals = {
        "imported": sum(p.imported for p in daily),
        "posted": sum(p.posted for p in daily),
        "failed": sum(p.failed for p in daily),
    }

    status_rows = db.execute(
        select(Post.status, func.count(Post.id)).group_by(Post.status)
    ).all()
    counts_now = {row[0]: int(row[1]) for row in status_rows}

    upload_attempts = totals["posted"] + totals["failed"]
    retry_rate = (totals["failed"] / upload_attempts) if upload_attempts else 0.0
    success_rate = (totals["posted"] / upload_attempts) if upload_attempts else 0.0

    pair_rows = db.execute(
        select(
            PostEvent.post_id,
            PostEvent.event_type,
            PostEvent.created_at,
        )
        .where(
            PostEvent.created_at >= cutoff,
            PostEvent.event_type.in_(["flickr_uploading", "flickr_uploaded"]),
        )
        .order_by(PostEvent.post_id, PostEvent.created_at.asc())
    ).all()
    by_post: dict[str, dict[str, datetime]] = {}
    for pid, etype, ts in pair_rows:
        bucket = by_post.setdefault(pid, {})
        bucket[etype] = ts
    durations: list[float] = []
    for bucket in by_post.values():
        a = bucket.get("flickr_uploading")
        b = bucket.get("flickr_uploaded")
        if a and b and b > a:
            durations.append((b - a).total_seconds())
    avg_upload = sum(durations) / len(durations) if durations else None

    return MetricsResponse(
        window_days=days,
        daily=daily,
        totals=totals,
        counts_now=counts_now,
        retry_rate=round(retry_rate, 4),
        success_rate=round(success_rate, 4),
        avg_upload_seconds=round(avg_upload, 2) if avg_upload is not None else None,
    )
