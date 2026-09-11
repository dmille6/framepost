"""Carousels: a group of posts publishing as one Instagram post.

The load-bearing tests are the two about members — a member that publishes on its own
makes a duplicate post, and a member whose row reads as pending makes the health banner
wait forever on a post that will never be made.
"""
import uuid

import pytest

from models import Performer, PlatformCredential, Post, PostPerformer, PostPlatform
from services import carousel
from services.platforms import instagram


def _post(db, *, w=1000, h=1250, **kw) -> Post:
    p = Post(id=uuid.uuid4().hex, status="pending", width=w, height=h, **kw)
    db.add(p)
    return p


def _performer(db, handle: str) -> Performer:
    perf = Performer(id=uuid.uuid4().hex, display_name=handle, instagram_handle=handle)
    db.add(perf)
    db.flush()
    return perf


def _tag(db, post: Post, perf: Performer, position: int = 0) -> None:
    db.add(PostPerformer(post_id=post.id, performer_id=perf.id, position=position))


def _set(db, n=3, *, w=1000, h=1250):
    """n frames that are legal to group: same shape, same performer."""
    perf = _performer(db, "hellinheels")
    posts = [_post(db, w=w, h=h, title=f"frame {i}") for i in range(n)]
    db.flush()
    for p in posts:
        _tag(db, p, perf)
    db.commit()
    return posts


# --- validation ---------------------------------------------------------------------

def test_a_legal_set_validates_clean(db):
    posts = _set(db, 3)
    assert carousel.validate(db, posts, lead_id=posts[0].id) == []


def test_one_photo_is_not_a_carousel(db):
    posts = _set(db, 1)
    assert any("2" in e for e in carousel.validate(db, posts, lead_id=posts[0].id))


def test_eleven_photos_is_too_many(db):
    posts = _set(db, 11)
    assert any("10" in e for e in carousel.validate(db, posts, lead_id=posts[0].id))


def test_mixed_ratios_are_rejected(db):
    """Instagram crops every frame to match the first, so a landscape and a portrait in
    one carousel silently mangles the rest."""
    portrait = _set(db, 2)
    wide = _post(db, w=4000, h=1000, title="pano")
    db.commit()
    errors = carousel.validate(db, portrait + [wide], lead_id=portrait[0].id)
    assert any("ratio" in e.lower() for e in errors)


def test_mixed_performers_are_rejected(db):
    """One caption, one set of co-authors — mixed performers would lose credits."""
    posts = _set(db, 2)
    other = _post(db, title="someone else")
    db.flush()
    _tag(db, other, _performer(db, "ariadelanoche"))
    db.commit()
    errors = carousel.validate(db, posts + [other], lead_id=posts[0].id)
    assert any("performer" in e.lower() for e in errors)


def test_photos_already_in_a_carousel_are_rejected(db):
    posts = _set(db, 4)
    carousel.group(db, posts[:2], lead_id=posts[0].id)
    errors = carousel.validate(db, posts, lead_id=posts[0].id)
    assert any("already in a carousel" in e for e in errors)


def test_the_cover_has_to_be_in_the_selection(db):
    posts = _set(db, 2)
    stranger = _post(db, title="not selected")
    db.commit()
    errors = carousel.validate(db, posts, lead_id=stranger.id)
    assert any("cover" in e.lower() for e in errors)


def test_missing_dimensions_are_caught_rather_than_guessed(db):
    posts = _set(db, 2)
    posts[1].width = None
    posts[1].height = None
    db.commit()
    assert any("dimensions" in e for e in carousel.validate(db, posts, lead_id=posts[0].id))


# --- grouping -----------------------------------------------------------------------

def test_group_makes_a_lead_and_members(db):
    posts = _set(db, 3)
    cid = carousel.group(db, posts, lead_id=posts[1].id)
    lead = carousel.lead_for(db, cid)
    assert lead.id == posts[1].id
    assert carousel.is_lead(lead) and not carousel.is_member(lead)
    others = [p for p in carousel.members(db, cid) if p.id != lead.id]
    assert len(others) == 2
    assert all(carousel.is_member(p) and not carousel.is_lead(p) for p in others)


def test_members_inherit_the_leads_scheduled_time(db):
    """A carousel whose frames fire on three different days is a confusing bug, and
    Smart Fill's scatter is built to spread a show's photos apart."""
    from datetime import datetime
    posts = _set(db, 3)
    when = datetime(2026, 9, 20, 18, 30)
    posts[0].scheduled_at = when
    posts[1].scheduled_at = datetime(2026, 10, 5, 9, 0)
    posts[2].scheduled_at = None
    db.commit()
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    assert [p.scheduled_at for p in carousel.members(db, cid)] == [when, when, when]


def test_members_come_back_in_position_order(db):
    posts = _set(db, 4)
    cid = carousel.group(db, posts, lead_id=posts[2].id)
    ordered = carousel.members(db, cid)
    assert ordered[0].id == posts[2].id
    assert [p.carousel_position for p in ordered] == [0, 1, 2, 3]


def test_reorder_promotes_a_new_lead(db):
    posts = _set(db, 3)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    carousel.reorder(db, cid, [posts[2].id, posts[0].id, posts[1].id])
    assert carousel.lead_for(db, cid).id == posts[2].id


def test_ungroup_returns_them_to_ordinary_posts(db):
    posts = _set(db, 3)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    assert carousel.ungroup(db, cid) == 3
    for p in posts:
        db.refresh(p)
        assert not carousel.is_in_carousel(p)
        assert not carousel.is_lead(p) and not carousel.is_member(p)


# --- the API guard ------------------------------------------------------------------

def test_post_carousel_refuses_a_single_image(db):
    with pytest.raises(instagram.InstagramError) as e:
        instagram.post_carousel(db, images=[instagram.CarouselImage("u")], caption="x")
    assert e.value.permanent


def test_post_carousel_refuses_more_than_ten(db):
    imgs = [instagram.CarouselImage(f"u{i}") for i in range(11)]
    with pytest.raises(instagram.InstagramError) as e:
        instagram.post_carousel(db, images=imgs, caption="x")
    assert e.value.permanent


# --- the fanout: who actually publishes ---------------------------------------------

def _ig_cred(db) -> PlatformCredential:
    cred = PlatformCredential(
        id=uuid.uuid4().hex, platform="instagram", access_token="tok",
        default_target=1, auth_status="ok",
    )
    db.add(cred)
    db.commit()
    return cred


def _ready(db, posts, tmp_path=None):
    """Give every frame what the Instagram path needs: a source file on disk, a Flickr
    id (Meta fetches by URL), and the instagram target."""
    from PIL import Image
    for i, p in enumerate(posts):
        p.flickr_photo_id = f"photo{i}"
        p.target_platforms = "instagram"
        if tmp_path is not None:
            f = tmp_path / f"{p.id}.jpg"
            Image.new("RGB", (p.width or 1000, p.height or 1250), (20, 20, 20)).save(f, "JPEG")
            p.original_path = str(f)
    db.commit()


def test_a_member_never_publishes_on_its_own(db, monkeypatch):
    """The duplicate-post bug. A member reaching post_photo would put the same frame on
    Instagram twice — once alone, once inside its own carousel."""
    from services import scheduler

    posts = _set(db, 3)
    _ready(db, posts)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    cred = _ig_cred(db)

    published: list[str] = []
    monkeypatch.setattr(
        scheduler.instagram, "post_photo",
        lambda **kw: published.append("single") or {"remote_id": "1", "url": None,
                                                   "collaborators": [], "collaborators_rejected": []},
    )
    monkeypatch.setattr(
        scheduler.instagram, "post_carousel",
        lambda **kw: published.append(f"carousel:{len(kw['images'])}") or
        {"remote_id": "2", "url": None, "collaborators": [], "collaborators_rejected": []},
    )
    monkeypatch.setattr(
        scheduler.flickr, "get_display_image_url", lambda db, pid, **kw: f"https://x/{pid}.jpg"
    )

    from datetime import datetime
    member = carousel.members(db, cid)[1]
    scheduler.fanout_to_platforms(db, member, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    assert published == []        # nothing published from a member
    pp = db.get(PostPlatform, (member.id, cred.id))
    assert pp is not None and pp.status == carousel.MEMBER_STATUS


def test_a_members_row_never_reads_as_pending(db, monkeypatch):
    """The stuck-queue bug. A pending row on a post that will never be published leaves
    the health banner waiting forever."""
    from datetime import datetime
    from services import scheduler

    posts = _set(db, 2)
    _ready(db, posts)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    cred = _ig_cred(db)
    member = carousel.members(db, cid)[1]

    scheduler.fanout_to_platforms(db, member, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    pp = db.get(PostPlatform, (member.id, cred.id))
    assert pp.status != "pending"
    assert pp.next_retry_at is None


def test_the_lead_publishes_every_frame_once(db, monkeypatch, tmp_path):
    from datetime import datetime
    from services import scheduler

    posts = _set(db, 4)
    _ready(db, posts, tmp_path)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    _ig_cred(db)

    seen: dict = {}

    def _fake_carousel(**kw):
        seen.update(kw)
        return {"remote_id": "99", "url": "https://instagram.com/p/x/",
                "collaborators": kw.get("collaborators") or [], "collaborators_rejected": []}

    monkeypatch.setattr(scheduler.instagram, "post_carousel", _fake_carousel)
    monkeypatch.setattr(
        scheduler.flickr, "get_display_image_url", lambda db, pid, **kw: f"https://x/{pid}.jpg"
    )

    lead = carousel.lead_for(db, cid)
    scheduler.fanout_to_platforms(db, lead, fired_at=datetime.utcnow(), targets=["instagram"])
    db.commit()

    assert len(seen["images"]) == 4
    # Frame order is the carousel's order, not whatever the DB felt like returning.
    assert [i.url for i in seen["images"]] == [f"https://x/photo{n}.jpg" for n in range(4)]
    assert seen["collaborators"] == ["hellinheels"]


def test_a_carousel_that_lost_its_frames_fails_permanently(db, monkeypatch):
    """Ungrouping or deleting underneath a schedule shouldn't retry forever."""
    from datetime import datetime
    from services import scheduler

    posts = _set(db, 2)
    _ready(db, posts)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    _ig_cred(db)
    lead = carousel.lead_for(db, cid)

    # Everyone else leaves; the lead still thinks it's a carousel.
    for m in carousel.members(db, cid):
        if m.id != lead.id:
            m.carousel_id = None
            m.carousel_position = None
    db.commit()

    with pytest.raises(instagram.InstagramError) as e:
        scheduler._post_instagram_carousel(
            db, db.query(PlatformCredential).first(), lead, caption="x"
        )
    assert e.value.permanent


# --- scheduling ---------------------------------------------------------------------

def test_sync_pulls_a_drifted_member_back_onto_the_lead(db):
    """Any path that moves a lead has to bring its frames along, or the carousel fires
    across several days. _stratified actively spreads a show's photos apart, so this is
    a live hazard rather than a theoretical one."""
    from datetime import datetime
    from routes.schedule import _sync_carousel_members

    posts = _set(db, 3)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    lead = carousel.lead_for(db, cid)
    lead.scheduled_at = datetime(2026, 11, 1, 19, 0)
    # Something scatters a member off on its own.
    carousel.members(db, cid)[2].scheduled_at = datetime(2026, 12, 25, 8, 0)
    db.commit()

    assert _sync_carousel_members(db) == 2
    db.commit()
    assert {p.scheduled_at for p in carousel.members(db, cid)} == {lead.scheduled_at}


def test_sync_leaves_ordinary_posts_alone(db):
    from datetime import datetime
    from routes.schedule import _sync_carousel_members

    loner = _post(db, title="not in a carousel")
    loner.scheduled_at = datetime(2026, 11, 2, 12, 0)
    db.commit()
    _sync_carousel_members(db)
    db.commit()
    assert loner.scheduled_at == datetime(2026, 11, 2, 12, 0)


# --- the API handlers ---------------------------------------------------------------
# Called as plain functions: this repo has no TestClient/CSRF harness, and the behaviour
# worth pinning here is the handler logic, not FastAPI's routing.

class _User:
    username = "tester"


def test_dry_run_reports_problems_without_changing_anything(db):
    from routes.carousels import CarouselCreate, create_carousel

    posts = _set(db, 2)
    wide = _post(db, w=4000, h=1000, title="pano")
    db.commit()
    ids = [p.id for p in posts] + [wide.id]

    out = create_carousel(
        CarouselCreate(post_ids=ids, lead_id=posts[0].id, dry_run=True), db, _User()
    )
    assert out.errors and out.carousel_id is None
    for p in posts:
        db.refresh(p)
        assert not carousel.is_in_carousel(p)


def test_create_keeps_the_order_it_was_given(db):
    """The dialog's arrangement is the carousel's order — the first id is the cover."""
    from routes.carousels import CarouselCreate, create_carousel

    posts = _set(db, 3)
    wanted = [posts[2].id, posts[0].id, posts[1].id]
    out = create_carousel(
        CarouselCreate(post_ids=wanted, lead_id=wanted[0]), db, _User()
    )
    assert out.errors == []
    assert [f.post_id for f in out.frames] == wanted
    assert [f.position for f in out.frames] == [0, 1, 2]


def test_create_refuses_when_a_selected_photo_is_gone(db):
    from routes.carousels import CarouselCreate, create_carousel

    posts = _set(db, 2)
    out = create_carousel(
        CarouselCreate(post_ids=[posts[0].id, posts[1].id, "deadbeef"], lead_id=posts[0].id),
        db, _User(),
    )
    assert out.carousel_id is None
    assert any("no longer exist" in e for e in out.errors)


def test_reorder_and_ungroup_round_trip(db):
    from routes.carousels import (
        CarouselCreate, CarouselReorder, create_carousel, reorder_carousel,
        ungroup_carousel,
    )

    posts = _set(db, 3)
    made = create_carousel(
        CarouselCreate(post_ids=[p.id for p in posts], lead_id=posts[0].id), db, _User()
    )
    cid = made.carousel_id
    assert cid

    flipped = [posts[1].id, posts[2].id, posts[0].id]
    out = reorder_carousel(cid, CarouselReorder(ordered_ids=flipped), db, _User())
    assert [f.post_id for f in out.frames] == flipped

    assert ungroup_carousel(cid, db, _User())["freed"] == 3
    for p in posts:
        db.refresh(p)
        assert not carousel.is_in_carousel(p)


def test_reorder_renumbers_everyone_even_from_a_partial_list(db):
    """The dialog can be opened on a subset of a carousel. Renumbering only the named
    frames would leave the rest on stale positions, colliding with the new ones and
    making the published order depend on how SQLite broke the tie."""
    posts = _set(db, 5)
    cid = carousel.group(db, posts, lead_id=posts[0].id)

    # Caller names only three of the five.
    carousel.reorder(db, cid, [posts[3].id, posts[1].id, posts[0].id])
    ordered = carousel.members(db, cid)

    positions = [p.carousel_position for p in ordered]
    assert positions == [0, 1, 2, 3, 4]          # contiguous, no duplicates
    assert [p.id for p in ordered][:3] == [posts[3].id, posts[1].id, posts[0].id]
    # The unnamed two keep their relative order, at the end.
    assert [p.id for p in ordered][3:] == [posts[2].id, posts[4].id]


def test_reorder_with_every_frame_named_is_exactly_that_order(db):
    posts = _set(db, 4)
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    wanted = [posts[2].id, posts[3].id, posts[0].id, posts[1].id]
    carousel.reorder(db, cid, wanted)
    assert [p.id for p in carousel.members(db, cid)] == wanted
