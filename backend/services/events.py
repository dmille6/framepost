"""Append rows to post_events. Caller owns the surrounding transaction."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PostEvent


def log_event(
    db: Session,
    *,
    post_id: str,
    event_type: str,
    actor: str = "user",
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        PostEvent(
            post_id=post_id,
            event_type=event_type,
            actor=actor,
            details=json.dumps(details, default=str) if details else None,
        )
    )


def log_edit(db: Session, *, post_id: str, fields: list[str], autosave: bool = False) -> None:
    """Keep an uninterrupted draft-editing burst to one activity row."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if autosave:
        previous = db.execute(
            select(PostEvent).where(PostEvent.post_id == post_id)
            .order_by(PostEvent.id.desc()).limit(1)
        ).scalar_one_or_none()
        if (previous and previous.event_type == "edited"
                and previous.actor == "user"
                and previous.created_at >= now - timedelta(minutes=5)):
            details = json.loads(previous.details or "{}")
            if details.get("autosave"):
                details["fields"] = sorted(set(details.get("fields", [])) | set(fields))
                previous.details = json.dumps(details)
                previous.created_at = now
                return
    log_event(db, post_id=post_id, event_type="edited", actor="user",
              details={"fields": fields, **({"autosave": True} if autosave else {})})
