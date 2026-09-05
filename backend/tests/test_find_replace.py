"""Find & replace across post text fields."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from models import Post
from services import find_replace as fr


def _post(db, *, desc=None, title=None, tags=None, status="pending", scheduled=None):
    p = Post(id=uuid.uuid4().hex, status=status, description=desc, title=title,
             tags=tags, scheduled_at=scheduled)
    db.add(p)
    db.commit()
    return p


def test_finds_and_rewrites_only_the_matched_substring(db):
    p = _post(db, desc="Shot at the Allways Lounge, New Orleans, Louisana. Great night.")
    out = fr.scan(db, find="Louisana", replace="Louisiana")
    assert out["post_count"] == 1
    assert out["occurrence_count"] == 1

    fr.apply(db, find="Louisana", replace="Louisiana", post_ids=[p.id])
    db.commit()
    # The rest of the sentence is untouched — this is the whole point.
    assert p.description == "Shot at the Allways Lounge, New Orleans, Louisiana. Great night."


def test_case_insensitive_by_default_and_sensitive_on_request(db):
    _post(db, desc="louisana and Louisana")
    assert fr.scan(db, find="Louisana")["occurrence_count"] == 2
    assert fr.scan(db, find="Louisana", case_sensitive=True)["occurrence_count"] == 1


def test_published_posts_are_reported_but_never_edited(db):
    """A published post is already live on Flickr with its old text; rewriting the local
    copy would only make FramePost disagree with the world."""
    live = _post(db, desc="typo here", status="published")
    fr.apply(db, find="typo", replace="fixed", post_ids=[live.id])
    db.commit()
    assert live.description == "typo here"

    out = fr.scan(db, find="typo")
    assert out["post_count"] == 0
    assert out["published_skipped"] == 1


def test_search_string_is_literal_not_a_regex(db):
    """The photographer types a typo, not a pattern. "a.b" must not match "axb"."""
    p = _post(db, desc="axb and a.b")
    out = fr.scan(db, find="a.b", replace="Z")
    assert out["occurrence_count"] == 1
    fr.apply(db, find="a.b", replace="Z", post_ids=[p.id])
    db.commit()
    assert p.description == "axb and Z"


def test_replacement_backslashes_stay_literal(db):
    r"""re.sub treats \1 and \g<0> in the REPLACEMENT as group references — it would
    raise or silently mangle. The replacement must be inserted verbatim."""
    p = _post(db, desc="path X here")
    fr.apply(db, find="X", replace=r"C:\1\new", post_ids=[p.id])
    db.commit()
    assert p.description == r"path C:\1\new here"


def test_deleting_a_substring_with_empty_replacement(db):
    p = _post(db, desc="Great shot -- shot at ISO 6400 -- lovely")
    fr.apply(db, find=" -- shot at ISO 6400", replace="", post_ids=[p.id])
    db.commit()
    assert p.description == "Great shot -- lovely"


def test_apply_only_touches_the_ids_given(db):
    a = _post(db, desc="typo")
    b = _post(db, desc="typo")
    out = fr.apply(db, find="typo", replace="ok", post_ids=[a.id])
    db.commit()
    assert out["changed"] == 1
    assert a.description == "ok"
    assert b.description == "typo"     # not ticked in the preview, not touched


def test_post_that_stopped_matching_since_preview_is_skipped(db):
    p = _post(db, desc="already fixed")
    out = fr.apply(db, find="typo", replace="ok", post_ids=[p.id])
    db.commit()
    assert out["changed"] == 0
    assert p.id in out["skipped"]


def test_tags_are_renormalized_after_replacement(db):
    """Replacing inside tags must not break the comma-separated invariant."""
    p = _post(db, tags="burlesque, louisana, nola")
    fr.apply(db, find="louisana", replace="Louisiana", field="tags", post_ids=[p.id])
    db.commit()
    assert p.tags == "burlesque, Louisiana, nola"


def test_title_field_supported(db):
    p = _post(db, title="Teaser Fest 2026 - Louisana")
    fr.apply(db, find="Louisana", replace="Louisiana", field="title", post_ids=[p.id])
    db.commit()
    assert p.title == "Teaser Fest 2026 - Louisiana"


def test_scheduled_posts_are_in_scope(db):
    """The whole reason this exists: the Drafts list excludes scheduled posts."""
    when = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
    _post(db, desc="typo", scheduled=when)
    out = fr.scan(db, find="typo")
    assert out["post_count"] == 1
    assert out["matches"][0]["scheduled_at"] is not None


def test_preview_shows_where_it_matched(db):
    long = "x" * 300 + " NEEDLE " + "y" * 300
    _post(db, desc=long)
    m = fr.scan(db, find="NEEDLE", replace="FOUND")["matches"][0]
    assert "NEEDLE" in m["before"] and len(m["before"]) < 150
    assert "FOUND" in m["after"]


def test_rejects_bad_field_and_empty_find(db):
    with pytest.raises(ValueError):
        fr.scan(db, find="x", field="status")
    with pytest.raises(ValueError):
        fr.scan(db, find="")
