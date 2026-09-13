"""Per-group submission pacing.

Every Flickr group worth joining publishes a throttle -- "5 per day", "1 per
week", "30 per month". Exceeding it returns `error 5: Photo limit reached`,
which is a permanent FlickrError: the submission is spent, not retried. Pacing
here is what makes a large group roster safe, because it decouples eligibility
(which groups a post may go to) from delivery (when it actually goes).

Windows are rolling rather than calendar. A calendar day would let the worker
fire a full quota at 23:59 and another at 00:01 -- a double burst that reads as
spam to a moderator and that Flickr's own rolling counter would reject anyway.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Group, PostGroup

PERIODS: dict[str, timedelta] = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
DEFAULT_PERIOD = "day"


LIFETIME = "ever"


def period_delta(group: Group) -> timedelta | None:
    """Window length for this group. None means unbounded (a lifetime cap).

    Unknown values fall back to a day -- the most conservative real period, so a
    throttle string Flickr adds later can never silently become unlimited.
    """
    period = group.limit_period or DEFAULT_PERIOD
    if period == LIFETIME:
        return None
    return PERIODS.get(period, PERIODS[DEFAULT_PERIOD])


def _submitted_in_window(db: Session, group: Group, now: datetime) -> list[datetime]:
    """Submission timestamps inside the current window, oldest first."""
    delta = period_delta(group)
    where = [
        PostGroup.group_id == group.id,
        PostGroup.status == "submitted",
        PostGroup.submitted_at.is_not(None),
    ]
    if delta is not None:
        where.append(PostGroup.submitted_at > now - delta)
    return list(
        db.execute(
            select(PostGroup.submitted_at)
            .where(*where)
            .order_by(PostGroup.submitted_at.asc())
        ).scalars().all()
    )


def remaining(db: Session, group: Group, now: datetime) -> int | None:
    """Submissions still allowed in this window, or None when the group is unlimited.

    None and 0 mean opposite things, so callers must distinguish them: a group with
    no configured limit must never be treated as exhausted.
    """
    limit = group.daily_limit
    if not limit or limit <= 0:
        return None
    return max(0, limit - len(_submitted_in_window(db, group, now)))


def next_slot_at(db: Session, group: Group, now: datetime) -> datetime:
    """When the oldest submission in the window ages out, freeing one slot.

    A lifetime cap never frees a slot; it is deferred a day at a time so the row
    stays visible as pending rather than being quietly failed.
    """
    delta = period_delta(group)
    if delta is None:
        return now + PERIODS["day"]
    stamps = _submitted_in_window(db, group, now)
    if not stamps:
        return now
    return stamps[0] + delta


def sync_throttles(db: Session, *, rest_call) -> list[tuple[str, str, str]]:
    """Refresh every group's limit from Flickr. Returns (name, before, after) changes.

    Throttles are the group owner's to change and they do, so a hand-entered value
    drifts out of date silently -- and drifting high is what produces permanent
    `Photo limit reached` failures. Reading them from the source keeps a large
    roster correct without anyone maintaining it.
    """
    changes: list[tuple[str, str, str]] = []
    for group in db.execute(select(Group)).scalars().all():
        if not group.flickr_group_id:
            continue
        try:
            el = rest_call(db, "flickr.groups.getInfo", group_id=group.flickr_group_id)
            node = el.find("group")
            throttle = node.find("throttle") if node is not None else None
        except Exception:  # noqa: BLE001 — one unreachable group must not stop the sweep
            continue

        mode = (throttle.get("mode") if throttle is not None else None) or "none"
        raw = throttle.get("count") if throttle is not None else None
        if mode == "none" or not raw:
            limit, period = None, DEFAULT_PERIOD
        else:
            try:
                limit = int(raw)
            except (TypeError, ValueError):
                continue
            period = mode if (mode in PERIODS or mode == LIFETIME) else DEFAULT_PERIOD

        before = f"{group.daily_limit}/{group.limit_period}"
        after = f"{limit}/{period}"
        if before != after:
            group.daily_limit = limit
            group.limit_period = period
            changes.append((group.name, before, after))
    if changes:
        db.commit()
    return changes
