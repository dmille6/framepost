"""Reaching a carousel's frames after it has published.

A carousel's members go `status="posted"` when it publishes. The draft queue lists
exactly (pending AND scheduled_at IS NULL) and the calendar groups by slot, so from that
moment the frames appear in neither and there was no way to fix a crop after the fact —
on 2026-09-11 it took direct database edits to reopen eight of them.
"""
import uuid

import pytest
from fastapi import HTTPException

from models import Performer, PlatformCredential, Post, PostPerformer
from routes.carousels import get_carousel_posts
from routes.history import HistoryPost
from services import carousel


def _set(db, n=3, *, w=1000, h=1250):
    perf = Performer(id=uuid.uuid4().hex, display_name="hellinheels",
                     instagram_handle="hellinheels")
    db.add(perf)
    db.flush()
    posts = []
    for i in range(n):
        p = Post(id=uuid.uuid4().hex, status="pending", width=w, height=h, title=f"frame {i}")
        db.add(p)
        db.flush()
        db.add(PostPerformer(post_id=p.id, performer_id=perf.id, position=0))
        posts.append(p)
    db.commit()
    return posts


def test_frames_come_back_after_the_carousel_has_published(db):
    """The case that had no answer: every frame posted, nothing reachable."""
    posts = _set(db, 4)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    for p in posts:
        p.status = "posted"
    db.commit()

    out = get_carousel_posts(cid, db, None)
    assert len(out) == 4
    assert {p.status for p in out} == {"posted"}


def test_frames_come_back_in_slide_order(db):
    posts = _set(db, 5)
    cid = carousel.group(db, posts, lead_id=posts[2].id)
    out = get_carousel_posts(cid, db, None)
    assert [p.carousel_position for p in out] == [0, 1, 2, 3, 4]
    assert out[0].id == posts[2].id, "the lead is slide 0"


def test_frames_carry_what_the_crop_editor_needs(db):
    """CarouselOut's summary is enough to draw a list and not enough to edit a crop."""
    posts = _set(db, 2)
    posts[0].ig_fit = "pad_blur"
    posts[0].ig_crop_x, posts[0].ig_crop_y = 0.1, 0.2
    posts[0].ig_crop_w, posts[0].ig_crop_h = 0.5, 0.6
    posts[0].ig_focal_x, posts[0].ig_focal_y = 0.4, 0.3
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    db.commit()

    lead = get_carousel_posts(cid, db, None)[0]
    assert lead.ig_fit == "pad_blur"
    assert (lead.ig_crop_x, lead.ig_crop_y, lead.ig_crop_w, lead.ig_crop_h) == (0.1, 0.2, 0.5, 0.6)
    assert (lead.ig_focal_x, lead.ig_focal_y) == (0.4, 0.3)
    assert lead.width and lead.height, "the editor needs the source dimensions"


def test_an_unknown_carousel_is_a_404_not_an_empty_list(db):
    """An empty list would render as a carousel with no frames rather than an error."""
    with pytest.raises(HTTPException) as e:
        get_carousel_posts("does-not-exist", db, None)
    assert e.value.status_code == 404


def test_a_history_row_says_which_carousel_it_belongs_to(db):
    """The published list is the only place a posted frame appears, so it has to carry
    the id the frames view is opened by."""
    posts = _set(db, 3)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    for p in posts:
        p.status = "posted"
    db.commit()

    row = HistoryPost.model_validate(posts[1])
    assert row.carousel_id == cid
    assert row.carousel_position == 1
