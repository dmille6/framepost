"""Schedule API — schedule, unschedule, list. One-post-per-hour rule enforced here.

Time discipline (brief: Time handling):
- All timestamps stored in UTC.
- Inputs may carry timezone; we normalize to UTC before storing.
"""
from __future__ import annotations

import logging
import random
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from database import get_session
from models import AppConfig, Post, User
from routes.auth import current_user
from routes.posts import PostOut
from services import events


def _user_timezone(db: Session) -> ZoneInfo:
    """Read the configured timezone (Settings → General → Time zone). Falls back to UTC if
    unset or unparseable. Used by Smart Fill to interpret HH:MM-of-day inputs as local time."""
    row = db.execute(select(AppConfig).where(AppConfig.key == "timezone")).scalar_one_or_none()
    name = (row.value if row and row.value else "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _schedule_fuzz_minutes(db: Session) -> int:
    """Read schedule_fuzz_minutes from app_config. Posts get a random offset of 0..N minutes
    + 0..59 seconds applied at schedule time so a streak of automated posts doesn't all land
    at exactly :00:00. 0 disables jitter."""
    row = db.execute(
        select(AppConfig).where(AppConfig.key == "schedule_fuzz_minutes")
    ).scalar_one_or_none()
    try:
        return max(0, min(30, int(row.value))) if row and row.value else 0
    except (TypeError, ValueError):
        return 0


def _apply_fuzz(dt: datetime, fuzz_minutes: int) -> datetime:
    """Return dt with a random additive offset within [0, fuzz_minutes] minutes plus random
    seconds. We always add (never subtract) to keep posts within the same clock-hour bucket
    that Smart Fill reserved — guarantees the 1-post-per-hour rule still holds."""
    if fuzz_minutes <= 0:
        return dt
    minute_off = random.randint(0, fuzz_minutes)
    second_off = random.randint(0, 59)
    return dt.replace(minute=minute_off, second=second_off, microsecond=0)

log = logging.getLogger("framepost.schedule")
router = APIRouter()


class ScheduleRequest(BaseModel):
    post_id: str
    scheduled_at: datetime


class ScheduledItem(BaseModel):
    id: str
    title: str | None
    description: str | None
    original_filename: str | None
    width: int | None
    height: int | None
    scheduled_at: datetime | None
    status: str
    posted_at: datetime | None
    error_message: str | None

    class Config:
        from_attributes = True


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Naive timestamps are treated as UTC (frontend sends ISO Z).
        return dt.replace(tzinfo=timezone.utc).astimezone(timezone.utc).replace(tzinfo=None)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _hour_bounds(when: datetime) -> tuple[datetime, datetime]:
    start = when.replace(minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=1)


def _slot_taken(db: Session, when: datetime, exclude_post_id: str | None = None) -> Post | None:
    start, end = _hour_bounds(when)
    q = select(Post).where(
        and_(
            Post.status == "pending",
            Post.scheduled_at.is_not(None),
            Post.scheduled_at >= start,
            Post.scheduled_at < end,
        )
    )
    if exclude_post_id:
        q = q.where(Post.id != exclude_post_id)
    return db.execute(q).scalar_one_or_none()


@router.post("", response_model=PostOut, status_code=status.HTTP_201_CREATED)
def schedule_post(
    body: ScheduleRequest,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    post = db.get(Post, body.post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    if post.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"post is {post.status}, only pending posts can be scheduled")

    when = _to_utc(body.scheduled_at)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if when <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scheduled_at must be in the future")

    conflict = _slot_taken(db, when, exclude_post_id=post.id)
    if conflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "another post is already scheduled in this hour",
                "conflicting_post_id": conflict.id,
                "conflicting_title": conflict.title,
            },
        )

    is_reschedule = post.scheduled_at is not None
    previous = post.scheduled_at
    post.scheduled_at = when
    post.updated_at = datetime.now(timezone.utc)
    events.log_event(
        db,
        post_id=post.id,
        event_type="rescheduled" if is_reschedule else "scheduled",
        actor=user.username,
        details={
            "scheduled_at": when.isoformat(),
            "previous": previous.isoformat() if previous else None,
        },
    )
    db.commit()
    db.refresh(post)
    return PostOut.model_validate(post)


@router.post("/{post_id}/post-now", status_code=status.HTTP_200_OK)
def post_now(
    post_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Force a scheduled post to fire on the next worker tick (within ~1 minute).

    Implementation: rewrite scheduled_at to now() and clear retry counters. The existing
    fire_due_posts loop picks up any pending post whose scheduled_at <= now, so we just
    nudge this one into the past.
    """
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    if post.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"post is {post.status}; only pending posts can be force-fired",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    previous = post.scheduled_at
    post.scheduled_at = now
    post.next_retry_at = None
    post.retry_count = 0
    post.error_message = None
    post.updated_at = now
    events.log_event(
        db,
        post_id=post.id,
        event_type="rescheduled",
        actor=user.username,
        details={
            "action": "post_now",
            "scheduled_at": now.isoformat(),
            "previous": previous.isoformat() if previous else None,
        },
    )
    db.commit()
    return {"ok": True, "post_id": post_id, "scheduled_at": now.isoformat()}


@router.delete("/{post_id}", status_code=status.HTTP_200_OK)
def unschedule_post(
    post_id: str,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    if post.status != "pending" or post.scheduled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "post is not currently scheduled")

    previous = post.scheduled_at
    post.scheduled_at = None
    post.updated_at = datetime.now(timezone.utc)
    events.log_event(
        db,
        post_id=post.id,
        event_type="rescheduled",
        actor=user.username,
        details={"scheduled_at": None, "previous": previous.isoformat()},
    )
    db.commit()
    return {"ok": True, "post_id": post_id}


@router.get("", response_model=list[ScheduledItem])
def list_scheduled(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
    range_from: datetime | None = Query(default=None, alias="from"),
    range_to: datetime | None = Query(default=None, alias="to"),
):
    """Return posts with a scheduled_at, plus recently posted/late/missed/failed in the window.

    Default window: -7 days to +60 days from now (covers past month for missed/late display
    and forward calendar for the brief's month/agenda views).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = _to_utc(range_from) if range_from else now - timedelta(days=7)
    end = _to_utc(range_to) if range_to else now + timedelta(days=60)

    rows = (
        db.execute(
            select(Post)
            .where(
                Post.scheduled_at.is_not(None),
                Post.scheduled_at >= start,
                Post.scheduled_at < end,
            )
            .order_by(Post.scheduled_at.asc())
        )
        .scalars()
        .all()
    )
    return [ScheduledItem.model_validate(r) for r in rows]


# --- Smart Fill (Phase 6A) ---


class SmartFillRequest(BaseModel):
    post_ids: list[str] = Field(min_length=1, max_length=200)
    time_of_day: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    cadence_days: int = Field(default=1, ge=1, le=30)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD, local
    skip_weekends: bool = False
    confirm: bool = False  # dry-run unless True
    # When "random_scatter", ignore start_date/cadence/time_of_day and instead pick random
    # unoccupied days in the next 180 days at popular post times (9-11am / 6-8pm local).
    mode: str = Field(default="sequential", pattern=r"^(sequential|random_scatter)$")


class SmartFillSlot(BaseModel):
    post_id: str
    title: str | None
    original_filename: str | None
    scheduled_at: datetime | None
    skipped_reason: str | None


class SmartFillResponse(BaseModel):
    slots: list[SmartFillSlot]
    scheduled: int
    skipped: int
    confirmed: bool


def _next_eligible(
    when: datetime,
    skip_weekends: bool,
    used_hours: set[datetime],
    db: Session,
    *,
    max_attempts: int = 365,
) -> datetime | None:
    """Advance `when` (always at the configured hour:minute) until we find a free clock-hour slot
    that respects the one-post-per-hour rule. Considers both DB-scheduled posts AND already
    in-flight smart-fill picks (`used_hours`)."""
    candidate = when
    for _ in range(max_attempts):
        if skip_weekends and candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)
            continue
        hour_start = candidate.replace(minute=0, second=0, microsecond=0)
        if hour_start in used_hours:
            candidate = candidate + timedelta(days=1)
            continue
        if _slot_taken(db, candidate):
            candidate = candidate + timedelta(days=1)
            continue
        return candidate
    return None


# Popular post times for the random_scatter mode (local hours). Mixes morning + evening
# windows that map to typical IG/Flickr engagement spikes — pre-work browse, lunch break,
# after-work scroll, evening downtime.
_POPULAR_HOURS = [9, 10, 11, 18, 19, 20]
_SCATTER_HORIZON_DAYS = 180


def _random_scatter(db: Session, body: SmartFillRequest, user: User) -> SmartFillResponse:
    """Scatter the selected posts across the next 6 months at popular local-time slots.

    Algorithm:
      1. Validate posts (same eligibility check as the sequential path).
      2. Find days in [tomorrow, +180 days] that don't already have any scheduled post.
      3. Shuffle, pick N free days.
      4. For each, pick a random popular hour from _POPULAR_HOURS, convert local→UTC.
      5. Apply per-hour collision check; fall back to another hour or another day if taken.
      6. Apply the configured fuzz so multiple scatter calls don't all land at :00:00.
    """
    tz = _user_timezone(db)
    fuzz = _schedule_fuzz_minutes(db)
    now_local = datetime.now(tz)
    today_local = now_local.date()

    # Posts to schedule, in deterministic order. Eligibility check matches the sequential path.
    posts: list[tuple[str, Post | None, str | None]] = []
    for pid in body.post_ids:
        p = db.get(Post, pid)
        if not p:
            posts.append((pid, None, "post not found"))
        elif p.status != "pending":
            posts.append((pid, p, f"post is {p.status}, only pending posts can be scheduled"))
        elif p.scheduled_at is not None:
            posts.append((pid, p, "already scheduled"))
        else:
            posts.append((pid, p, None))

    eligible_count = sum(1 for _, _, err in posts if err is None)

    # Days already booked (any post in this user's posted/pending schedule, in local-date space).
    occupied_local_dates: set[date_type] = set()
    existing = db.execute(
        select(Post.scheduled_at).where(Post.scheduled_at.is_not(None))
    ).scalars().all()
    for sched in existing:
        if sched is None:
            continue
        local_d = sched.replace(tzinfo=timezone.utc).astimezone(tz).date()
        occupied_local_dates.add(local_d)

    # Free days in window. Skip today (would need to be a future time too) — start at tomorrow.
    free_days: list[date_type] = []
    for i in range(1, _SCATTER_HORIZON_DAYS + 1):
        d = today_local + timedelta(days=i)
        if d in occupied_local_dates:
            continue
        if body.skip_weekends and d.weekday() >= 5:
            continue
        free_days.append(d)

    if len(free_days) < eligible_count:
        # Not enough free days — we'll fill what we can; the rest get "skipped" slots.
        log.warning(
            "random_scatter: %d posts requested but only %d free days in horizon",
            eligible_count, len(free_days),
        )

    random.shuffle(free_days)
    # Reserve set of (date, hour) slots taken during THIS scatter run so we don't double-book.
    used_local_slots: set[tuple[date_type, int]] = set()

    def _pick_slot(d: date_type) -> datetime | None:
        """Return a UTC-naive datetime for a popular hour on date d that's free; None if all
        tried hours collide with existing posts."""
        hours = list(_POPULAR_HOURS)
        random.shuffle(hours)
        for hr in hours:
            if (d, hr) in used_local_slots:
                continue
            local_dt = datetime(d.year, d.month, d.day, hr, 0, 0, 0, tzinfo=tz)
            if local_dt <= now_local + timedelta(minutes=1):
                continue
            utc_dt = local_dt.astimezone(timezone.utc).replace(tzinfo=None)
            # One-post-per-hour invariant — check against any other Post in same hour.
            if _slot_taken(db, utc_dt) is None:
                used_local_slots.add((d, hr))
                return utc_dt
        return None

    slots: list[SmartFillSlot] = []
    day_iter = iter(free_days)
    for pid, post, err in posts:
        if err is not None:
            slots.append(
                SmartFillSlot(
                    post_id=pid,
                    title=post.title if post else None,
                    original_filename=post.original_filename if post else None,
                    scheduled_at=None,
                    skipped_reason=err,
                )
            )
            continue

        chosen_utc: datetime | None = None
        # Try days until we find a free hour-slot.
        for d in day_iter:
            chosen_utc = _pick_slot(d)
            if chosen_utc is not None:
                break
        if chosen_utc is None:
            slots.append(
                SmartFillSlot(
                    post_id=pid,
                    title=post.title,
                    original_filename=post.original_filename,
                    scheduled_at=None,
                    skipped_reason="no free popular-hour slot found in the next 180 days",
                )
            )
            continue

        fuzzed = _apply_fuzz(chosen_utc, fuzz)
        slots.append(
            SmartFillSlot(
                post_id=pid,
                title=post.title,
                original_filename=post.original_filename,
                scheduled_at=fuzzed,
                skipped_reason=None,
            )
        )

    scheduled_n = sum(1 for s in slots if s.scheduled_at is not None)
    skipped_n = len(slots) - scheduled_n

    if body.confirm and scheduled_n:
        for s in slots:
            if s.scheduled_at is None:
                continue
            post = db.get(Post, s.post_id)
            if not post or post.status != "pending" or post.scheduled_at is not None:
                continue
            post.scheduled_at = s.scheduled_at
            post.updated_at = datetime.utcnow()
            events.log_event(
                db,
                post_id=post.id,
                event_type="scheduled",
                actor=user.username,
                details={
                    "scheduled_at": s.scheduled_at.isoformat(),
                    "previous": None,
                    "via": "smart_fill_random_scatter",
                },
            )
        db.commit()

    return SmartFillResponse(
        slots=slots,
        scheduled=scheduled_n,
        skipped=skipped_n,
        confirmed=body.confirm and scheduled_n > 0,
    )


@router.post("/smart-fill", response_model=SmartFillResponse)
def smart_fill(
    body: SmartFillRequest,
    db: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if body.mode == "random_scatter":
        return _random_scatter(db, body, user)

    # Parse start as a local datetime in the user's configured timezone — `time_of_day` is
    # entered in the dialog as e.g. "10:00" meaning 10am LOCAL, not UTC. Convert to UTC for
    # storage. Cadence advances in local-time space so DST transitions don't drift the slot.
    try:
        start_date = datetime.strptime(body.start_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date must be YYYY-MM-DD")
    h, m = (int(x) for x in body.time_of_day.split(":"))
    tz = _user_timezone(db)
    fuzz = _schedule_fuzz_minutes(db)

    local_cursor = datetime(
        start_date.year, start_date.month, start_date.day, h, m, 0, 0, tzinfo=tz,
    )
    now_utc = datetime.now(timezone.utc)

    def _to_utc_naive(local_dt: datetime) -> datetime:
        return local_dt.astimezone(timezone.utc).replace(tzinfo=None)

    # If the requested first-slot time is already in the past, bump forward by cadence_days
    # in LOCAL time (so 10am stays 10am across DST).
    while _to_utc_naive(local_cursor) <= now_utc.replace(tzinfo=None) + timedelta(minutes=1):
        local_cursor = local_cursor + timedelta(days=body.cadence_days)
    cursor = _to_utc_naive(local_cursor)

    posts = []
    for pid in body.post_ids:
        p = db.get(Post, pid)
        if not p:
            posts.append((pid, None, "post not found"))
        elif p.status != "pending":
            posts.append((pid, p, f"post is {p.status}, only pending posts can be scheduled"))
        elif p.scheduled_at is not None:
            posts.append((pid, p, "already scheduled"))
        else:
            posts.append((pid, p, None))

    used_hours: set[datetime] = set()
    slots: list[SmartFillSlot] = []
    eligible_iter = iter(p for p in posts if p[2] is None)

    for pid, post, err in posts:
        if err is not None:
            slots.append(
                SmartFillSlot(
                    post_id=pid,
                    title=post.title if post else None,
                    original_filename=post.original_filename if post else None,
                    scheduled_at=None,
                    skipped_reason=err,
                )
            )
            continue
        chosen = _next_eligible(cursor, body.skip_weekends, used_hours, db)
        if chosen is None:
            slots.append(
                SmartFillSlot(
                    post_id=pid,
                    title=post.title,
                    original_filename=post.original_filename,
                    scheduled_at=None,
                    skipped_reason="no free clock-hour slot found within a year",
                )
            )
            continue
        used_hours.add(chosen.replace(minute=0, second=0, microsecond=0))
        # Apply jitter AFTER reserving the hour — jitter stays within the same clock hour
        # so the 1-post-per-hour invariant holds.
        fuzzed = _apply_fuzz(chosen, fuzz)
        slots.append(
            SmartFillSlot(
                post_id=pid,
                title=post.title,
                original_filename=post.original_filename,
                scheduled_at=fuzzed,
                skipped_reason=None,
            )
        )
        # Advance cursor in LOCAL time using the un-fuzzed slot (so cadence stays consistent
        # day-to-day; jitter doesn't compound).
        local_cursor = chosen.replace(tzinfo=timezone.utc).astimezone(tz) + timedelta(days=body.cadence_days)
        cursor = _to_utc_naive(local_cursor)

    scheduled_n = sum(1 for s in slots if s.scheduled_at is not None)
    skipped_n = len(slots) - scheduled_n

    if body.confirm and scheduled_n:
        for s in slots:
            if s.scheduled_at is None:
                continue
            post = db.get(Post, s.post_id)
            if not post or post.status != "pending" or post.scheduled_at is not None:
                continue
            post.scheduled_at = s.scheduled_at
            post.updated_at = datetime.utcnow()
            events.log_event(
                db,
                post_id=post.id,
                event_type="scheduled",
                actor=user.username,
                details={
                    "scheduled_at": s.scheduled_at.isoformat(),
                    "previous": None,
                    "via": "smart_fill",
                },
            )
        db.commit()

    return SmartFillResponse(
        slots=slots,
        scheduled=scheduled_n,
        skipped=skipped_n,
        confirmed=body.confirm and scheduled_n > 0,
    )
