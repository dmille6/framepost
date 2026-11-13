"""Paid-boosted Instagram posts stay out of the organic analytics.

Three follower surges in 2026 that looked like an unexplained source outside the feed
were all paid boosts. The organic analytics had folded them in, and that is enough to
crown a performer or a posting hour on ad spend. A boost is bought for the Instagram
media only, so only the Instagram series is dropped. The same post's Flickr or Bluesky
readings stay in.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from models import EngagementSnapshot, Post, PostEvent
from services import analytics_core as ac
from services import analytics_distribution as adist


class _User:
    username = "tester"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _post(db, *, boosted=False, carousel_id=None, platforms=("instagram", "flickr")):
    posted = _utcnow() - timedelta(days=10)
    p = Post(id=uuid.uuid4().hex, status="posted", posted_at=posted,
             carousel_id=carousel_id,
             ig_boosted_at=_utcnow() if boosted else None)
    db.add(p)
    db.flush()
    for plat in platforms:
        db.add(EngagementSnapshot(post_id=p.id, platform=plat, sampled_at=posted + timedelta(days=7),
                                  likes=10, comments_count=0, reach=500 if plat == "instagram" else None))
    db.flush()
    return p


def test_a_boosted_posts_instagram_reading_is_left_out(db):
    organic = _post(db)
    boosted = _post(db, boosted=True)
    db.commit()
    ids = {s.post.id for s in ac.collect_samples(db, platform="instagram", window="7d")}
    assert ids == {organic.id}
    assert boosted.id not in ids


def test_its_other_platforms_are_kept(db):
    """Meta's boost did nothing for Flickr; dropping that reading would be a new bias."""
    boosted = _post(db, boosted=True)
    db.commit()
    flickr = ac.collect_samples(db, platform="flickr", window="7d")
    assert [s.post.id for s in flickr] == [boosted.id]
    pooled = ac.collect_samples(db, window="7d")
    assert {(s.post.id, s.platform) for s in pooled} == {(boosted.id, "flickr")}


def test_include_boosted_brings_it_back(db):
    boosted = _post(db, boosted=True)
    db.commit()
    got = ac.collect_samples(db, platform="instagram", window="7d", include_boosted=True)
    assert [s.post.id for s in got] == [boosted.id]


def test_distribution_says_how_many_it_left_out(db):
    _post(db)
    _post(db, boosted=True)
    db.commit()
    assert adist.distribution(db, platform="instagram")["boosted_excluded"] == 1
    assert adist.distribution(db, platform="flickr")["boosted_excluded"] == 0


# --- the route ------------------------------------------------------------------------

def test_flagging_one_frame_flags_the_whole_carousel(db):
    """A carousel is one Instagram media, so it is one boost."""
    from routes.posts import InstagramBoostBody, mark_instagram_boosted

    cid = uuid.uuid4().hex
    frames = [_post(db, carousel_id=cid) for _ in range(3)]
    loner = _post(db)
    db.commit()

    out = mark_instagram_boosted(frames[1].id, InstagramBoostBody(boosted=True), db, _User())
    assert out.ig_boosted_at is not None
    for f in frames:
        db.refresh(f)
        assert f.ig_boosted_at is not None
    db.refresh(loner)
    assert loner.ig_boosted_at is None

    mark_instagram_boosted(frames[0].id, InstagramBoostBody(boosted=False), db, _User())
    for f in frames:
        db.refresh(f)
        assert f.ig_boosted_at is None


def test_reflagging_keeps_the_first_timestamp_and_logs_once(db):
    from routes.posts import InstagramBoostBody, mark_instagram_boosted

    p = _post(db)
    db.commit()
    mark_instagram_boosted(p.id, InstagramBoostBody(boosted=True), db, _User())
    db.refresh(p)
    first = p.ig_boosted_at
    mark_instagram_boosted(p.id, InstagramBoostBody(boosted=True), db, _User())
    db.refresh(p)
    assert p.ig_boosted_at == first
    events = db.execute(
        select(PostEvent.event_type).where(PostEvent.post_id == p.id)
    ).scalars().all()
    assert events == ["instagram_boosted"]
