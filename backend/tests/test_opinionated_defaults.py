"""Defaults that stop the editor asking a question whose answer never changes.

Two fields were re-answered on every photo for no reason: `city` (IPTC carries
none, so it was always blank and the Analytics city breakdown was always empty)
and the group checklist (routing had decided it since services/group_routing.py
landed, but the editor still rendered 25 checkboxes capped at five).

Tags are deliberately not defaulted -- this install has a working per-subject
profile system and seeding the always-on profile would both duplicate it and
mis-tag the circus and drag work. The profile tests below cover the merge
machinery, not any particular content.

What these tests actually guard is the *opt-out*. A default that cannot be
declined is not a default, it is a behaviour change -- so each one here is paired
with a test that a deliberate contrary choice survives.
"""
import uuid

from models import AppConfig, Group, Post, PostGroup, TagProfile
from services import group_routing, import_pipeline, tags as tags_svc


def _cfg(db, key, value):
    db.add(AppConfig(key=key, value=value))
    db.flush()


# --------------------------------------------------------------------------
# default city
# --------------------------------------------------------------------------

def test_default_city_comes_from_config(db):
    _cfg(db, "default_city", "New Orleans")
    assert import_pipeline._default_city(db) == "New Orleans"


def test_default_city_unset_leaves_the_field_empty(db):
    """No config row must not become the string "None" on every import."""
    assert import_pipeline._default_city(db) is None


def test_default_city_can_be_switched_off_with_a_blank(db):
    """Clearing the setting in Settings -> General is how you opt out; a blank has to
    mean "no city", not "fall back to the built-in"."""
    _cfg(db, "default_city", "   ")
    assert import_pipeline._default_city(db) is None


# --------------------------------------------------------------------------
# default tags
# --------------------------------------------------------------------------

def test_first_run_creates_an_empty_default_profile(db):
    """Empty on purpose: whatever belongs on every photo is the photographer's call,
    and anything seeded here reaches all of them at publish time."""
    p = tags_svc.ensure_default_profile(db)
    assert p.is_default == 1
    assert p.tags == ""


def test_existing_default_profile_is_never_overwritten(db):
    """The photographer's own tag list outranks ours -- ensure_default_profile is called
    on every start, so overwriting here would undo his edit on the next restart."""
    mine = TagProfile(id=uuid.uuid4().hex, name="Global default",
                      tags="only, mine", is_default=1, sort_order=0)
    db.add(mine)
    db.commit()
    assert tags_svc.ensure_default_profile(db).tags == "only, mine"


def test_default_profile_tags_reach_a_post(db):
    """Whatever the photographer puts in the always-on profile ships with every post."""
    p = tags_svc.ensure_default_profile(db)
    p.tags = "darrellmiller, neworleans"
    db.flush()
    post = Post(id=uuid.uuid4().hex, status="pending", tags="xenazeitgeist")
    db.add(post)
    db.flush()
    merged = tags_svc.merged_tags_for_post(db, post)
    assert "xenazeitgeist" in merged
    assert "darrellmiller" in merged


def test_post_tags_are_not_duplicated_by_the_default_profile(db):
    """A photo already carrying a profile tag must not ship it twice, whatever the
    casing -- the profile system's whole job is merging, not concatenating."""
    p = tags_svc.ensure_default_profile(db)
    p.tags = "neworleans"
    db.flush()
    post = Post(id=uuid.uuid4().hex, status="pending", tags="NewOrleans")
    db.add(post)
    db.flush()
    merged = [t.strip().lower() for t in tags_svc.merged_tags_for_post(db, post).split(",")]
    assert merged.count("neworleans") == 1


# --------------------------------------------------------------------------
# tags_from_csv — one parser for the rule and for the preview of the rule
# --------------------------------------------------------------------------

def test_tags_from_csv_matches_post_tags(db):
    """The route-preview endpoint parses typed text; the publish path parses the stored
    column. If these two disagreed, the preview would promise groups that never arrive."""
    post = Post(id=uuid.uuid4().hex, status="pending", tags="Burlesque, NOLA , stage")
    db.add(post)
    db.flush()
    assert group_routing.tags_from_csv("Burlesque, NOLA , stage") == group_routing.post_tags(post)


def test_tags_from_csv_folds_case(db):
    assert group_routing.tags_from_csv("Burlesque") == group_routing.tags_from_csv("burlesque")


def test_tags_from_csv_on_empty_input(db):
    assert group_routing.tags_from_csv("") == set()
    assert group_routing.tags_from_csv(None) == set()


# --------------------------------------------------------------------------
# handing groups back to routing
# --------------------------------------------------------------------------

def _group(db, *, name="G", match=None) -> Group:
    g = Group(id=uuid.uuid4().hex, flickr_group_id=f"{uuid.uuid4().hex[:4]}@N01",
              name=name, default_enabled=1, match_tags=match, no_watermark=0)
    db.add(g)
    db.flush()
    return g


def test_routing_resumes_once_the_override_is_cleared(db):
    """`groups_overridden` back to 0 with the pending rows gone is the only state that
    lets ensure_assignments() fill the post again -- that is what "use automatic routing
    instead" has to reproduce."""
    g = _group(db, match=None)
    post = Post(id=uuid.uuid4().hex, status="posted", tags="burlesque",
                flickr_photo_id="p1", groups_overridden=1)
    db.add(post)
    db.add(PostGroup(id=uuid.uuid4().hex, post_id=post.id, group_id=g.id, status="pending"))
    db.flush()

    assert group_routing.ensure_assignments(db, post) == []

    # What the endpoint does for use_routing=True.
    post.groups_overridden = 0
    db.execute(PostGroup.__table__.delete().where(
        PostGroup.post_id == post.id, PostGroup.status == "pending"))
    db.flush()

    assert [x.id for x in group_routing.ensure_assignments(db, post)] == [g.id]


def test_an_override_still_blocks_routing(db):
    """The opt-out for the opt-out. Clearing groups by hand has to stick."""
    _group(db, match=None)
    post = Post(id=uuid.uuid4().hex, status="posted", tags="burlesque",
                flickr_photo_id="p1", groups_overridden=1)
    db.add(post)
    db.flush()
    assert group_routing.ensure_assignments(db, post) == []


# --------------------------------------------------------------------------
# the API handlers
# --------------------------------------------------------------------------
# Called as plain functions, matching test_carousel.py: this repo has no
# TestClient/CSRF harness and the behaviour worth pinning is the handler logic.

class _User:
    username = "tester"


def test_route_preview_returns_the_groups_routing_would_pick(db):
    open_pool = _group(db, name="Stage", match=None)
    concert = _group(db, name="Concert", match="concert, livemusic")
    db.commit()

    from routes.groups import RoutePreviewIn, route_preview

    out = route_preview(RoutePreviewIn(tags="burlesque, stage"), db, _User())
    ids = {g.id for g in out}
    assert open_pool.id in ids
    assert concert.id not in ids, "a concert pool must not be promised burlesque frames"


def test_route_preview_reports_why_a_group_matched(db):
    _group(db, name="Concert", match="concert, livemusic")
    db.commit()

    from routes.groups import RoutePreviewIn, route_preview

    out = route_preview(RoutePreviewIn(tags="burlesque, concert"), db, _User())
    assert [g.matched for g in out] == [["concert"]]


def test_route_preview_skips_groups_with_no_flickr_id(db):
    """resolve() requires one to submit at all, so previewing it would be a lie."""
    g = _group(db, name="Draft group", match=None)
    g.flickr_group_id = None
    db.commit()

    from routes.groups import RoutePreviewIn, route_preview

    assert route_preview(RoutePreviewIn(tags="burlesque"), db, _User()) == []


def test_use_routing_clears_the_override_and_the_pending_rows(db):
    g = _group(db, match=None)
    post = Post(id=uuid.uuid4().hex, status="pending", tags="burlesque",
                groups_overridden=1)
    db.add(post)
    db.add(PostGroup(id=uuid.uuid4().hex, post_id=post.id, group_id=g.id, status="pending"))
    db.commit()

    from routes.groups import PostGroupsUpdate, set_post_groups

    out = set_post_groups(post.id, PostGroupsUpdate(group_ids=[], use_routing=True), db, _User())
    assert out == []
    db.refresh(post)
    assert post.groups_overridden == 0
    assert db.query(PostGroup).filter_by(post_id=post.id).count() == 0


def test_use_routing_leaves_already_submitted_rows_alone(db):
    """Those are a record of what Flickr has, not a pending intention."""
    g = _group(db, match=None)
    post = Post(id=uuid.uuid4().hex, status="posted", tags="burlesque",
                groups_overridden=1)
    db.add(post)
    db.add(PostGroup(id=uuid.uuid4().hex, post_id=post.id, group_id=g.id, status="submitted"))
    db.commit()

    from routes.groups import PostGroupsUpdate, set_post_groups

    set_post_groups(post.id, PostGroupsUpdate(group_ids=[], use_routing=True), db, _User())
    assert db.query(PostGroup).filter_by(post_id=post.id, status="submitted").count() == 1


def test_saving_an_empty_selection_without_use_routing_still_means_no_groups(db):
    """The ambiguity groups_overridden exists to resolve: an empty list on its own is a
    deliberate "nowhere", not a request for the defaults."""
    _group(db, match=None)
    post = Post(id=uuid.uuid4().hex, status="pending", tags="burlesque")
    db.add(post)
    db.commit()

    from routes.groups import PostGroupsUpdate, set_post_groups

    set_post_groups(post.id, PostGroupsUpdate(group_ids=[]), db, _User())
    db.refresh(post)
    assert post.groups_overridden == 1
    assert group_routing.ensure_assignments(db, post) == []
