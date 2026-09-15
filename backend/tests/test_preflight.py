"""Delivery readiness, decided on the server.

The queue's "ready" badge was computed in the browser from title, tags and alt text.
That is metadata completeness, not delivery readiness: a post could be green while
Instagram was disconnected, the original file had moved off disk, or a carousel had
lost a frame. None of that surfaced until the worker tried, hours later.

Blank alt text counted as done, because the check asked whether the AI sweep had run
rather than whether there was any alt text. "We tried" is not "there is".
"""
import json
import uuid

import pytest

from models import PlatformCredential, Post
from services import preflight


def _cred(db, platform, *, token="tok", auth="ok", default=1) -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform, access_token=token,
                           auth_status=auth, default_target=default, account_name="a")
    db.add(c)
    db.flush()
    return c


def _post(db, tmp_path, **kw) -> Post:
    img = tmp_path / f"{uuid.uuid4().hex}.arw"
    img.write_bytes(b"raw")
    kw.setdefault("title", "A title")
    kw.setdefault("tags", "burlesque, nola")
    kw.setdefault("alt_text", "A performer mid-drop under a red spotlight.")
    p = Post(id=uuid.uuid4().hex, status="pending", original_path=str(img), **kw)
    db.add(p)
    db.flush()
    return p


def _codes(findings, level=None):
    return {f.code for f in findings if level is None or f.level == level}


# --------------------------------------------------------------------------
# the delivery checks the browser could never make
# --------------------------------------------------------------------------

def test_a_complete_post_to_a_connected_platform_is_ready(db, tmp_path, r2_stub):
    _cred(db, "flickr")
    _cred(db, "instagram")
    post = _post(db, tmp_path)
    db.commit()
    assert preflight.summarize(preflight.check(db, post))["ready"] is True


def test_a_disconnected_destination_blocks(db, tmp_path):
    _cred(db, "flickr")
    post = _post(db, tmp_path, target_platforms=json.dumps(["flickr", "instagram"]))
    db.commit()
    f = preflight.check(db, post)
    assert "not_connected" in _codes(f, preflight.BLOCKER)


def test_a_platform_needing_reauth_blocks(db, tmp_path):
    _cred(db, "flickr", auth="reauth_required")
    post = _post(db, tmp_path, target_platforms=json.dumps(["flickr"]))
    db.commit()
    assert "reauth_required" in _codes(preflight.check(db, post), preflight.BLOCKER)


def test_a_missing_image_blocks(db, tmp_path):
    _cred(db, "flickr")
    post = _post(db, tmp_path, target_platforms=json.dumps(["flickr"]))
    post.original_path = str(tmp_path / "gone.arw")
    db.commit()
    assert "image_missing" in _codes(preflight.check(db, post), preflight.BLOCKER)


def test_instagram_without_a_public_url_blocks(db, tmp_path):
    """No R2 and no Flickr in the destinations: Meta has nowhere to fetch from."""
    _cred(db, "instagram")
    post = _post(db, tmp_path, target_platforms=json.dumps(["instagram"]))
    db.commit()
    assert "no_public_image_url" in _codes(preflight.check(db, post), preflight.BLOCKER)


def test_r2_satisfies_instagram_without_flickr(db, tmp_path, r2_stub):
    _cred(db, "instagram")
    post = _post(db, tmp_path, target_platforms=json.dumps(["instagram"]))
    db.commit()
    assert "no_public_image_url" not in _codes(preflight.check(db, post))


def test_a_one_frame_carousel_blocks(db, tmp_path):
    _cred(db, "flickr")
    cid = uuid.uuid4().hex
    lead = _post(db, tmp_path, target_platforms=json.dumps(["flickr"]),
                 carousel_id=cid, carousel_position=0)
    db.commit()
    assert "carousel_too_small" in _codes(preflight.check(db, lead), preflight.BLOCKER)


# --------------------------------------------------------------------------
# content quality is a warning, not a blocker
# --------------------------------------------------------------------------

def test_blank_alt_text_is_missing_not_done(db, tmp_path, r2_stub):
    """The old check passed "" because the AI sweep had run."""
    _cred(db, "flickr")
    post = _post(db, tmp_path, alt_text="", target_platforms=json.dumps(["flickr"]))
    db.commit()
    f = preflight.check(db, post)
    assert "no_alt_text" in _codes(f, preflight.WARNING)
    assert preflight.summarize(f)["ready"] is False
    assert preflight.summarize(f)["deliverable"] is True, "it still publishes"


def test_whitespace_alt_text_is_also_missing(db, tmp_path):
    _cred(db, "flickr")
    post = _post(db, tmp_path, alt_text="   ", target_platforms=json.dumps(["flickr"]))
    db.commit()
    assert "no_alt_text" in _codes(preflight.check(db, post), preflight.WARNING)


def test_missing_title_and_tags_warn(db, tmp_path):
    _cred(db, "flickr")
    post = _post(db, tmp_path, title="", tags="", target_platforms=json.dumps(["flickr"]))
    db.commit()
    w = _codes(preflight.check(db, post), preflight.WARNING)
    assert {"no_title", "no_tags"} <= w


def test_deliverable_and_ready_are_different_questions(db, tmp_path):
    """A post can be publishable and still not be worth publishing yet."""
    _cred(db, "flickr")
    post = _post(db, tmp_path, tags="", target_platforms=json.dumps(["flickr"]))
    db.commit()
    s = preflight.summarize(preflight.check(db, post))
    assert s["deliverable"] is True and s["ready"] is False


def test_blockers_sort_before_warnings(db, tmp_path):
    post = _post(db, tmp_path, title="", target_platforms=json.dumps(["instagram"]))
    db.commit()
    levels = [f.level for f in preflight.check(db, post)]
    assert levels == sorted(levels, key=lambda l: 0 if l == preflight.BLOCKER else 1)


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------

def test_default_targets_are_flickr_plus_connected_defaults(db, tmp_path):
    _cred(db, "flickr")
    _cred(db, "bluesky")
    _cred(db, "pinterest", default=0)
    post = _post(db, tmp_path)
    db.commit()
    t = preflight.targets_for(post, preflight.load_credentials(db))
    assert "flickr" in t and "bluesky" in t
    assert "pinterest" not in t, "default_target=0 is not a destination"


def test_an_unparseable_target_list_falls_back_to_defaults(db, tmp_path):
    _cred(db, "flickr")
    post = _post(db, tmp_path, target_platforms="{not json")
    db.commit()
    assert "flickr" in preflight.targets_for(post, preflight.load_credentials(db))
