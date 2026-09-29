"""Autosave preserves sparse PATCH semantics without flooding the activity feed."""
import json
import pytest
from fastapi import HTTPException
from datetime import datetime, timedelta

from sqlalchemy import select

from models import Post, PostEvent
from routes.posts import PostUpdate, update_post
from services.events import log_event


def make_post(db, **kwargs):
    post = Post(id="draft", title="Original", description="Keep", status="pending",
                original_path="/missing/original", **kwargs)
    db.add(post)
    db.commit()
    return post


def patch(db, **changes):
    return update_post("draft", PostUpdate(**changes), db=db, _user=None, autosave=True)


def rows(db):
    return db.scalars(select(PostEvent).order_by(PostEvent.id)).all()


def test_autosave_coalesces_fields_and_returns_preflight(db):
    make_post(db)
    patch(db, title="First")
    saved = patch(db, title="Second", ig_focal_x=0.2, ig_focal_y=0.4)
    assert saved.title == "Second"
    assert saved.description == "Keep"
    assert saved.preflight["deliverable"] is False
    assert len(rows(db)) == 1
    assert json.loads(rows(db)[0].details) == {
        "autosave": True, "fields": ["ig_focal_x", "ig_focal_y", "title"]}


def test_empty_patch_does_not_add_activity(db):
    make_post(db)
    patch(db)
    assert rows(db) == []


def test_explicit_saves_and_intervening_events_keep_their_own_rows(db):
    make_post(db)
    patch(db, title="Auto")
    update_post("draft", PostUpdate(title="Explicit"), db=db, _user=None)
    patch(db, title="Auto again")
    log_event(db, post_id="draft", event_type="scheduled")
    db.commit()
    patch(db, title="After scheduling event")
    assert [row.event_type for row in rows(db)] == ["edited", "edited", "edited", "scheduled", "edited"]
    assert "autosave" not in json.loads(rows(db)[1].details)


def test_old_bursts_and_live_scheduled_edits_are_not_coalesced(db):
    post = make_post(db)
    patch(db, title="Earlier")
    rows(db)[0].created_at = datetime.utcnow() - timedelta(minutes=6)
    db.commit()
    patch(db, title="Later")
    assert len(rows(db)) == 2
    post.scheduled_at = datetime.utcnow() + timedelta(days=1)
    db.commit()
    update_post("draft", PostUpdate(title="Live 1"), db=db, _user=None)
    update_post("draft", PostUpdate(title="Live 2"), db=db, _user=None)
    assert len(rows(db)) == 4
    assert "autosave" not in json.loads(rows(db)[-1].details)


@pytest.mark.parametrize("state,scheduled", [("pending", True), ("posted", False), ("failed", False)])
@pytest.mark.parametrize("changes", [{"title": "Stale edit"}, {}])
def test_autosave_rejects_posts_that_are_no_longer_drafts(db, state, scheduled, changes):
    post = make_post(db)
    post.status = state
    post.scheduled_at = datetime.utcnow() if scheduled else None
    db.commit()
    with pytest.raises(HTTPException) as exc:
        patch(db, **changes)
    assert exc.value.status_code == 409
    assert "no longer a draft" in exc.value.detail
    db.refresh(post)
    assert post.title == "Original"
    assert rows(db) == []
