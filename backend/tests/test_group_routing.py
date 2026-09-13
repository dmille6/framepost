"""Tag-gated group routing.

A 25-group roster can't be driven from a checklist -- and it doesn't need to be:
517 of 518 posts carry a stage/performance tag, so for the groups that take any
subject there is no decision to make. `default_enabled` covers those and
`match_tags` gates the minority that fit only part of the catalogue.

The property that matters most here is the negative one. Submitting 500 burlesque
frames to a concert pool is how a moderator removes you from a group, so a gated
group must stay out of posts that don't carry its tags -- and a post the
photographer already decided about must never be quietly refilled.
"""
import uuid

from models import Group, Post, PostGroup
from services import group_routing


def _group(db, *, name="G", enabled=1, match=None, fid="1@N01") -> Group:
    g = Group(id=uuid.uuid4().hex, flickr_group_id=fid, name=name,
              default_enabled=enabled, match_tags=match, no_watermark=0)
    db.add(g)
    db.flush()
    return g


def _post(db, tags="burlesque, stage, sony") -> Post:
    p = Post(id=uuid.uuid4().hex, status="posted", tags=tags, flickr_photo_id="p1")
    db.add(p)
    db.flush()
    return p


# --------------------------------------------------------------------------
# the two rules
# --------------------------------------------------------------------------

def test_ungated_default_group_takes_every_post(db):
    g = _group(db, match=None)
    assert group_routing.accepts(g, group_routing.post_tags(_post(db))) is True


def test_group_that_is_not_default_is_never_auto_assigned(db):
    g = _group(db, enabled=0, match=None)
    assert group_routing.accepts(g, group_routing.post_tags(_post(db))) is False


def test_gated_group_takes_a_post_carrying_its_tag(db):
    g = _group(db, match="concert, livemusic")
    post = _post(db, tags="burlesque, concert, nola")
    assert group_routing.accepts(g, group_routing.post_tags(post)) is True


def test_gated_group_stays_out_of_posts_without_its_tag(db):
    """The whole point: a concert pool must not receive 500 burlesque frames."""
    g = _group(db, match="concert, livemusic")
    post = _post(db, tags="burlesque, stage, showgirl")
    assert group_routing.accepts(g, group_routing.post_tags(post)) is False


def test_matching_ignores_case(db):
    g = _group(db, match="Concert")
    assert group_routing.accepts(g, group_routing.post_tags(_post(db, tags="concert"))) is True


def test_matching_ignores_surrounding_whitespace(db):
    g = _group(db, match="  concert ,  livemusic  ")
    assert group_routing.accepts(g, group_routing.post_tags(_post(db, tags=" concert "))) is True


def test_any_one_matching_tag_is_enough(db):
    g = _group(db, match="concert, livemusic, gig")
    post = _post(db, tags="burlesque, gig")
    assert group_routing.accepts(g, group_routing.post_tags(post)) is True


def test_untagged_post_reaches_ungated_groups_only(db):
    post = _post(db, tags="")
    assert group_routing.accepts(_group(db, match=None), group_routing.post_tags(post)) is True
    assert group_routing.accepts(_group(db, match="concert"), group_routing.post_tags(post)) is False


def test_partial_word_is_not_a_match(db):
    """'concert' must not be satisfied by 'concertina' -- tags match whole, not prefix."""
    g = _group(db, match="concert")
    assert group_routing.accepts(g, group_routing.post_tags(_post(db, tags="concertina"))) is False


def test_group_without_a_flickr_id_is_skipped(db):
    _group(db, fid=None, match=None)
    post = _post(db)
    db.commit()
    assert group_routing.resolve(db, post) == []


def test_resolve_picks_only_the_qualifying_groups(db):
    _group(db, name="anysubject", match=None)
    _group(db, name="concerts", match="concert")
    _group(db, name="circus", match="circus")
    _group(db, name="offbydefault", enabled=0, match=None)
    post = _post(db, tags="burlesque, circus, nola")
    db.commit()
    assert sorted(g.name for g in group_routing.resolve(db, post)) == ["anysubject", "circus"]


# --------------------------------------------------------------------------
# materialising assignments
# --------------------------------------------------------------------------

def test_assignments_are_created_as_pending(db):
    _group(db, name="anysubject", match=None)
    post = _post(db)
    db.commit()

    assigned = group_routing.ensure_assignments(db, post)
    db.commit()

    assert [g.name for g in assigned] == ["anysubject"]
    rows = db.query(PostGroup).filter(PostGroup.post_id == post.id).all()
    assert len(rows) == 1 and rows[0].status == "pending"


def test_existing_assignments_are_left_alone(db):
    """A selection already made outranks the defaults."""
    chosen = _group(db, name="chosen", enabled=0, match=None)
    _group(db, name="wouldbedefault", match=None)
    post = _post(db)
    db.add(PostGroup(id=uuid.uuid4().hex, post_id=post.id,
                     group_id=chosen.id, status="pending"))
    db.commit()

    assert group_routing.ensure_assignments(db, post) == []
    rows = db.query(PostGroup).filter(PostGroup.post_id == post.id).all()
    assert [r.group_id for r in rows] == [chosen.id]


def test_a_deliberately_empty_selection_is_not_refilled(db):
    """Clearing every group off a post reads as 'untouched' without the flag --
    and would come back with the full default set at publish."""
    _group(db, name="wouldbedefault", match=None)
    post = _post(db)
    post.groups_overridden = 1
    db.commit()

    assert group_routing.ensure_assignments(db, post) == []
    assert db.query(PostGroup).filter(PostGroup.post_id == post.id).count() == 0


def test_seeding_is_idempotent(db):
    _group(db, name="anysubject", match=None)
    post = _post(db)
    db.commit()

    group_routing.ensure_assignments(db, post)
    db.commit()
    group_routing.ensure_assignments(db, post)
    db.commit()

    assert db.query(PostGroup).filter(PostGroup.post_id == post.id).count() == 1
