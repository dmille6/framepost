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


@pytest.mark.parametrize("state,scheduled", [("pending", True), ("posted", False), ("failed", False)])
@pytest.mark.parametrize("relationship", ["albums", "groups", "routing", "profiles", "performers"])
def test_relationship_autosaves_reject_non_drafts_before_mutation(db, state, scheduled, relationship):
    from models import Album, Group, TagProfile, Performer, PostAlbum, PostGroup, PostProfile, PostPerformer
    from routes.albums import set_post_albums, PostAlbumsUpdate
    from routes.groups import set_post_groups, PostGroupsUpdate
    from routes.profiles import set_post_profiles, PostProfilesUpdate
    from routes.performers import set_post_performers, TagPerformersBody
    post = make_post(db)
    db.add_all([Album(id="a", name="Album"), Group(id="g", name="Group"),
                TagProfile(id="t", name="Profile"), Performer(id="p", display_name="Performer")])
    db.flush()
    db.add_all([PostAlbum(post_id=post.id, album_id="a"),
                PostGroup(id="pg", post_id=post.id, group_id="g"),
                PostProfile(post_id=post.id, profile_id="t"),
                PostPerformer(post_id=post.id, performer_id="p", position=0)])
    post.status = state
    post.scheduled_at = datetime.utcnow() if scheduled else None
    post.groups_overridden = 1
    db.commit()
    endpoints = {
        "albums": (set_post_albums, PostAlbumsUpdate(album_ids=[])),
        "groups": (set_post_groups, PostGroupsUpdate(group_ids=[])),
        "routing": (set_post_groups, PostGroupsUpdate(group_ids=[], use_routing=True)),
        "profiles": (set_post_profiles, PostProfilesUpdate(profile_ids=[])),
        "performers": (set_post_performers, TagPerformersBody(performer_ids=[])),
    }
    endpoint, body = endpoints[relationship]
    with pytest.raises(HTTPException) as exc:
        endpoint(post.id, body, db=db, _user=None, autosave=True)
    assert exc.value.status_code == 409
    db.expire_all()
    for model in [PostAlbum, PostGroup, PostProfile, PostPerformer]:
        assert len(db.scalars(select(model)).all()) == 1
    assert post.groups_overridden == 1
    # Explicit calendar edits remain supported.
    endpoint(post.id, body, db=db, _user=None)


def test_post_response_exposes_schedule_for_relationship_only_autosave_validation(db):
    from routes.posts import get_post
    post = make_post(db, scheduled_at=datetime.utcnow())
    response = get_post(post.id, db=db, _user=None)
    assert response.scheduled_at == post.scheduled_at
