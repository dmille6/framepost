"""Mentions that are true on the platform they're posted to.

A caption stores '@instagram_handle' once, at authoring time, and that same string
goes to every platform. On Bluesky the account doesn't exist under that name: the
text renders grey, nobody is notified, and it misleads anyone who clicks. 334 of
518 captions carry at least one.

Two failures have to be fixed together. The handle has to be translated to the
performer's actual Bluesky handle, and the result has to carry a mention facet --
atproto records a DID, so text alone is never a mention however it looks.

The property guarded hardest here is the negative one: an unknown handle must not
become a facet. A facet pointing at the wrong DID doesn't fail loudly, it tags a
stranger on someone else's photo.
"""
import uuid

import pytest

from models import Performer, Venue
from services.platforms import bluesky


def _performer(db, name, ig=None, bsky=None):
    p = Performer(id=uuid.uuid4().hex, display_name=name,
                  instagram_handle=ig, bluesky_handle=bsky)
    db.add(p)
    db.flush()
    return p


def _venue(db, name, ig=None, bsky=None):
    v = Venue(id=uuid.uuid4().hex, display_name=name,
              instagram_handle=ig, bluesky_handle=bsky)
    db.add(v)
    db.flush()
    return v


def _resolver(mapping):
    return lambda h: mapping.get(h)


# --------------------------------------------------------------------------
# rewriting
# --------------------------------------------------------------------------

def test_known_performer_gets_their_bluesky_handle(db):
    _performer(db, "Bebe Bardeaux", ig="bebe.bardeaux", bsky="bebe.bsky.social")
    db.commit()
    assert bluesky.rewrite_mentions(db, "shot of @bebe.bardeaux last night") == \
        "shot of @bebe.bsky.social last night"


def test_performer_without_a_bluesky_handle_becomes_a_readable_credit(db):
    """A dead '@handle' reads as a broken link; the name reads as a credit."""
    _performer(db, "Roxie LaRouge", ig="roxielarouge")
    db.commit()
    assert bluesky.rewrite_mentions(db, "with @roxielarouge") == "with Roxie LaRouge"


def test_an_unknown_handle_is_left_untouched(db):
    """Mangling text we don't understand is worse than leaving it."""
    db.commit()
    assert bluesky.rewrite_mentions(db, "hi @somebody.else") == "hi @somebody.else"


def test_venues_are_rewritten_too(db):
    """Venues reach captions on the same path and had the same dead handle."""
    _venue(db, "The Allways Lounge", ig="allwayslounge", bsky="allways.bsky.social")
    db.commit()
    assert bluesky.rewrite_mentions(db, "at @allwayslounge") == "at @allways.bsky.social"


def test_matching_is_case_insensitive(db):
    _performer(db, "Bebe", ig="Bebe.Bardeaux", bsky="bebe.bsky.social")
    db.commit()
    assert bluesky.rewrite_mentions(db, "@BEBE.BARDEAUX") == "@bebe.bsky.social"


def test_several_mentions_in_one_caption(db):
    _performer(db, "A", ig="a_one", bsky="a.bsky.social")
    _performer(db, "Bee Two", ig="b_two")
    db.commit()
    assert bluesky.rewrite_mentions(db, "@a_one and @b_two and @c_three") == \
        "@a.bsky.social and Bee Two and @c_three"


def test_an_email_like_token_is_not_a_mention(db):
    """'@' mid-word isn't a mention; only one at a word boundary is."""
    _performer(db, "X", ig="x", bsky="x.bsky.social")
    db.commit()
    assert bluesky.rewrite_mentions(db, "mail me at me@x today") == "mail me at me@x today"


def test_a_performer_wins_a_handle_collision_with_a_venue(db):
    _venue(db, "Venue Name", ig="shared", bsky="venue.bsky.social")
    _performer(db, "Performer Name", ig="shared", bsky="performer.bsky.social")
    db.commit()
    assert bluesky.rewrite_mentions(db, "@shared") == "@performer.bsky.social"


def test_text_without_mentions_is_unchanged(db):
    db.commit()
    assert bluesky.rewrite_mentions(db, "no mentions #here") == "no mentions #here"


# --------------------------------------------------------------------------
# facets
# --------------------------------------------------------------------------

def _mentions(facets):
    return [f for f in facets
            if f["features"][0]["$type"] == "app.bsky.richtext.facet#mention"]


def test_a_resolving_handle_becomes_a_mention_facet():
    facets = bluesky._detect_facets(
        "hi @bebe.bsky.social", resolve=_resolver({"bebe.bsky.social": "did:plc:abc"}))
    got = _mentions(facets)
    assert len(got) == 1
    assert got[0]["features"][0]["did"] == "did:plc:abc"


def test_an_unresolvable_handle_produces_no_facet():
    """The whole point: no facet is correct, a guessed one tags a stranger."""
    assert _mentions(bluesky._detect_facets("hi @ghost.bsky.social", resolve=_resolver({}))) == []


def test_a_bare_word_is_never_looked_up():
    """Every real handle is domain-shaped, so '@someone' can't be one -- and asking
    the network about it leaks caption text."""
    asked = []

    def resolve(h):
        asked.append(h)
        return "did:plc:x"

    assert _mentions(bluesky._detect_facets("hi @someone", resolve=resolve)) == []
    assert asked == []


def test_the_facet_range_covers_the_at_sign():
    text = "hi @bebe.bsky.social"
    got = _mentions(bluesky._detect_facets(text, resolve=_resolver({"bebe.bsky.social": "did:plc:abc"})))
    idx = got[0]["index"]
    assert text.encode()[idx["byteStart"]:idx["byteEnd"]] == b"@bebe.bsky.social"


def test_byte_offsets_survive_non_ascii_text():
    """atproto indexes bytes, not characters -- an emoji earlier in the line shifts
    every offset after it."""
    text = "🔥 @bebe.bsky.social"
    got = _mentions(bluesky._detect_facets(text, resolve=_resolver({"bebe.bsky.social": "did:plc:abc"})))
    idx = got[0]["index"]
    assert text.encode()[idx["byteStart"]:idx["byteEnd"]] == b"@bebe.bsky.social"


def test_one_lookup_per_distinct_handle():
    calls = []

    def resolve(h):
        calls.append(h)
        return "did:plc:abc"

    bluesky._detect_facets("@a.bsky.social @a.bsky.social @b.bsky.social", resolve=resolve)
    assert sorted(calls) == ["a.bsky.social", "b.bsky.social"]


def test_hashtags_and_links_still_work_alongside_mentions():
    facets = bluesky._detect_facets(
        "#burlesque @bebe.bsky.social https://example.com/x",
        resolve=_resolver({"bebe.bsky.social": "did:plc:abc"}))
    kinds = sorted(f["features"][0]["$type"].rsplit("#", 1)[1] for f in facets)
    assert kinds == ["link", "mention", "tag"]
