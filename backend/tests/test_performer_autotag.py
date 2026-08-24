"""@-prefixed IPTC keywords auto-link performers at import."""
import uuid

from models import Performer, PostPerformer
from services import performers as ps


def test_extract_pulls_handles_and_keeps_the_rest():
    handles, keep = ps.extract_at_handles(
        "burlesque, @mx.eli.rose, stage, @bellablueforever, nola"
    )
    assert handles == ["mx.eli.rose", "bellablueforever"]
    assert keep == "burlesque, stage, nola"


def test_extract_handles_none_and_plain():
    assert ps.extract_at_handles(None) == ([], None)
    assert ps.extract_at_handles("burlesque, nola") == ([], "burlesque, nola")


def test_extract_ignores_mid_string_at():
    # An email-ish or embedded @ isn't a performer keyword.
    handles, keep = ps.extract_at_handles("shot@night, @realhandle")
    assert handles == ["realhandle"]
    assert keep == "shot@night"


def test_extract_dedupes_case_insensitively():
    handles, _ = ps.extract_at_handles("@Juju, @juju")
    assert handles == ["Juju"]


def test_all_handles_leaves_no_tags():
    handles, keep = ps.extract_at_handles("@one, @two")
    assert handles == ["one", "two"]
    assert keep is None


def test_find_or_create_matches_existing_handle_case_insensitively(db):
    p = Performer(id=uuid.uuid4().hex, display_name="Mx Eli Rose", instagram_handle="mx.eli.rose")
    db.add(p)
    db.commit()
    found, created = ps.find_or_create_by_handle(db, "@MX.ELI.ROSE")
    assert created is False
    assert found.id == p.id
    assert found.display_name == "Mx Eli Rose"  # existing name preserved


def test_find_or_create_makes_new_performer(db):
    made, created = ps.find_or_create_by_handle(db, "@brandnew")
    db.commit()
    assert created is True
    assert made.display_name == "brandnew"
    assert made.instagram_handle == "brandnew"


def test_find_or_create_backfills_handle_on_name_match(db):
    p = Performer(id=uuid.uuid4().hex, display_name="juju", instagram_handle=None)
    db.add(p)
    db.commit()
    found, created = ps.find_or_create_by_handle(db, "@juju")
    assert created is False
    assert found.id == p.id
    assert found.instagram_handle == "juju"  # learned the handle


def test_autotag_links_and_is_idempotent(db):
    from models import Post
    post = Post(id=uuid.uuid4().hex, status="pending")
    db.add(post)
    db.commit()

    first = ps.autotag_from_handles(db, post.id, ["bellablueforever", "queenquan"])
    db.commit()
    assert len(first) == 2
    assert [r["created"] for r in first] == [True, True]

    links = db.query(PostPerformer).filter_by(post_id=post.id).all()
    assert len(links) == 2
    assert sorted(l.position for l in links) == [0, 1]

    # Re-running (e.g. a republish) must not duplicate the links.
    again = ps.autotag_from_handles(db, post.id, ["bellablueforever"])
    db.commit()
    assert again == []
    assert db.query(PostPerformer).filter_by(post_id=post.id).count() == 2


def test_bare_duplicate_of_claimed_handle_is_dropped():
    # Lightroom exports a keyword and its synonym, so the performer arrives twice.
    handles, keep = ps.extract_at_handles(
        "2026, bebe.bardeaux, burlesque, @bebe.bardeaux, nola"
    )
    assert handles == ["bebe.bardeaux"]
    assert keep == "2026, burlesque, nola"


def test_bare_duplicate_matching_ignores_punctuation():
    # "@mx.eli.rose" and a bare "mxelirose" are the same identity for hashtag purposes.
    handles, keep = ps.extract_at_handles("@mx.eli.rose, mxelirose, stage")
    assert handles == ["mx.eli.rose"]
    assert keep == "stage"


def test_unrelated_tags_survive():
    handles, keep = ps.extract_at_handles("@juju, jujubes, stage")
    assert handles == ["juju"]
    assert keep == "jujubes, stage"
