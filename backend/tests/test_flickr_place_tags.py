"""Place names are Flickr keywords, with priority below the photographer's tags."""
import shlex
from datetime import datetime

import pytest

from models import Post, TagProfile, Venue
from services import scheduler, tags


def _post(db, *, city="New Orleans", venue="The AllWays Lounge", raw_tags="music", sha="hash"):
    if venue is not None:
        db.add(Venue(id="venue", display_name=venue))
        db.flush()
    post = Post(id="photo", venue_id="venue" if venue is not None else None,
                city=city, tags=raw_tags, sha256=sha)
    db.add(post)
    db.commit()
    return post


def test_places_are_quoted_separate_tags_and_do_not_change_shared_tags(db):
    post = _post(db, city="New Orleans, Louisiana")
    db.add(TagProfile(id="profile", name="Default", tags="stage", is_default=1))
    db.commit()
    formatted = scheduler._flickr_tags_for_post(db, post)
    assert '"The AllWays Lounge"' in formatted
    assert '"New Orleans, Louisiana"' in formatted
    assert shlex.split(formatted) == ["music", "stage", "The AllWays Lounge",
                                      "New Orleans, Louisiana", "framepost:sha256=hash"]
    assert tags.merged_tags_for_post(db, post) == "music, stage"
    assert post.tags == "music"


@pytest.mark.parametrize("city,venue,raw,expected", [
    (" new orleans ", "New Orleans", "music, NEW ORLEANS", ["music", "NEW ORLEANS"]),
    ("   ", None, "music", ["music"]),
    (None, "   ", None, []),
])
def test_duplicate_and_empty_places_are_skipped(db, city, venue, raw, expected):
    post = _post(db, city=city, venue=venue, raw_tags=raw, sha=None)
    assert shlex.split(scheduler._flickr_tags_for_post(db, post)) == expected


@pytest.mark.parametrize("count,sha,places", [
    (72, "hash", ["The AllWays Lounge", "New Orleans"]),
    (73, "hash", ["The AllWays Lounge"]),
    (74, "hash", []),
    (80, "hash", []),
    (73, None, ["The AllWays Lounge", "New Orleans"]),
    (75, None, []),
])
def test_cap_reserves_machine_tag_and_drops_places_last_in(db, count, sha, places):
    ordinary = [f"tag{i}" for i in range(count)]
    post = _post(db, raw_tags=", ".join(ordinary), sha=sha)
    result = shlex.split(scheduler._flickr_tags_for_post(db, post))
    machine = ["framepost:sha256=hash"] if sha else []
    assert len(result) <= 75
    assert result == ordinary[:75 - len(machine)] + places + machine


def test_upload_receives_place_tags(db, monkeypatch, tmp_path):
    post = _post(db)
    src = tmp_path / "source.jpg"
    src.write_bytes(b"jpeg")
    post.original_path = str(src)
    post.scheduled_at = datetime.now()
    db.commit()
    monkeypatch.setattr(scheduler.storage, "DERIVATIVES", tmp_path)
    monkeypatch.setattr(scheduler.image, "make_derivative", lambda s, d, edge: d.write_bytes(b"jpeg"))
    monkeypatch.setattr(scheduler.duplicate, "find_in_flickr_cache", lambda *a: None)
    monkeypatch.setattr(scheduler.duplicate, "find_soft_match", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_flickr_post_bookkeeping", lambda *a, **k: None)
    uploads = []

    def upload(**kw):
        uploads.append(kw)
        return "remote"

    monkeypatch.setattr(scheduler.flickr, "upload_photo", upload)
    scheduler._flickr_post(db, post, datetime.now())
    assert "The AllWays Lounge" in shlex.split(uploads[0]["tags"])
    assert "New Orleans" in shlex.split(uploads[0]["tags"])
    assert post.flickr_photo_id == "remote"


def test_double_quotes_in_a_place_never_break_tag_quoting():
    from services.platforms import flickr
    out = flickr.format_tags(['The "Big" Room', 'plain'], machine_tags=["framepost:sha256=abc"])
    assert out == '"The Big Room" plain framepost:sha256=abc'
