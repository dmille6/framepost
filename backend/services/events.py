"""Append rows to post_events. Caller owns the surrounding transaction."""
from __future__ import annotations

import json
from typing import Any

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
