"""What lands in Instagram's five hashtag slots.

Instagram cut the per-post limit from 30 to 5 in Dec 2025, and 517 of 518 posts here
fill all five -- so the cap binds on essentially every post and a wasted slot is a
fifth of the post's discovery budget, not a rounding error.

An audit of the built captions found two kinds of waste that survived the existing
filters. Proper-noun fragments rode along with the full form (#allways beside
#allwayslounge on 48 posts, #venardos beside #venardoscircus on 27) -- two slots
naming one venue, and the fragment alone reaches people misspelling "always".
_spread_stems allows two tags per stem by design, so it passes these through. And
#louisana, a misspelling, took a slot on 40 posts and never once appeared alongside
the correct spelling, so that slot bought nothing.
"""
import re
import uuid

import pytest

from models import Post
from services import instagram as ig_svc, scheduler


def _tags(db, tag_str, platform="instagram"):
    post = Post(id=uuid.uuid4().hex, status="pending", title="", description="", tags=tag_str)
    db.add(post)
    db.flush()
    caption = scheduler._build_caption_for(platform, post, db)
    return [t.lower() for t in re.findall(r"#([A-Za-z0-9_]+)", caption)]


# --------------------------------------------------------------------------
# the two caps are separate constants and must not drift
# --------------------------------------------------------------------------

def test_the_two_hashtag_caps_agree():
    """instagram.MAX_HASHTAGS and scheduler.HASHTAG_CAP are independent constants
    describing one platform rule; nothing else stops them drifting apart."""
    assert ig_svc.MAX_HASHTAGS == scheduler.HASHTAG_CAP["instagram"]


def test_instagram_is_capped_at_five(db):
    got = _tags(db, "burlesque, neworleans, nola, circus, drag, cabaret, showgirl, stage")
    assert len(got) == 5


# --------------------------------------------------------------------------
# proper-noun fragments
# --------------------------------------------------------------------------

def test_a_venue_fragment_does_not_take_a_slot(db):
    got = _tags(db, "allways, allwayslounge, burlesque, neworleans, nola, drag")
    assert "allways" not in got
    assert "allwayslounge" in got


def test_a_company_fragment_does_not_take_a_slot(db):
    got = _tags(db, "venardos, venardoscircus, circus, neworleans, nola, drag")
    assert "venardos" not in got
    assert "venardoscircus" in got


def test_the_freed_slot_is_reused_not_lost(db):
    """Dropping a tag must promote the next one, not shorten the block."""
    got = _tags(db, "allways, allwayslounge, burlesque, neworleans, nola, drag")
    assert len(got) == 5


def test_fragments_survive_where_no_cap_is_in_force(db):
    """The filter is justified by scarcity. Pixelfed still allows 30, so nothing there
    is competing for a slot and the archival tag is kept."""
    got = _tags(db, "allways, allwayslounge, burlesque", platform="pixelfed")
    assert "allways" in got


# --------------------------------------------------------------------------
# misspellings
# --------------------------------------------------------------------------

def test_a_misspelled_tag_is_corrected(db):
    got = _tags(db, "louisana, burlesque, neworleans")
    assert "louisana" not in got
    assert "louisiana" in got


def test_correction_applies_even_without_a_cap(db):
    """A hashtag has no spellcheck on any platform; the correction isn't about scarcity."""
    got = _tags(db, "louisana, burlesque", platform="pixelfed")
    assert "louisiana" in got and "louisana" not in got


def test_correcting_does_not_duplicate_an_existing_correct_tag(db):
    got = _tags(db, "louisana, louisiana, burlesque, neworleans, nola, drag")
    assert got.count("louisiana") == 1


def test_an_unlisted_tag_is_left_alone(db):
    got = _tags(db, "burlesque, neworleans")
    assert "burlesque" in got and "neworleans" in got
