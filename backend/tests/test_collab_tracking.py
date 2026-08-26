"""Collaborator ledger + performer handle health."""
import uuid
from datetime import datetime, timedelta

from models import (
    EngagementSnapshot, Performer, PlatformCredential, Post, PostCollaborator,
    PostPlatform,
)
from services import analytics_core as ac
from services import performers as ps


def _perf(db, name, handle):
    p = Performer(id=uuid.uuid4().hex, display_name=name, instagram_handle=handle)
    db.add(p)
    db.commit()
    return p


def _post(db, **kw):
    p = Post(id=uuid.uuid4().hex, status="published", **kw)
    db.add(p)
    db.commit()
    return p


def test_sent_handle_is_recorded_and_linked(db):
    perf = _perf(db, "Bebe", "bebe.bardeaux")
    post = _post(db, title="x")
    ps.record_collab_outcome(db, post_id=post.id, sent=["bebe.bardeaux"], rejected=[])
    db.commit()

    row = db.get(PostCollaborator, {"post_id": post.id, "handle": "bebe.bardeaux"})
    assert row.status == "sent"
    assert row.performer_id == perf.id
    assert perf.handle_status == "ok"


def test_rejected_handle_flags_the_performer(db):
    perf = _perf(db, "Ghost", "ghost.handle")
    post = _post(db, title="x")
    ps.record_collab_outcome(db, post_id=post.id, sent=[], rejected=["ghost.handle"])
    db.commit()

    assert perf.handle_status == "needs_check"
    assert "private" in (perf.handle_error or "")
    assert perf.handle_checked_at is not None
    assert db.get(PostCollaborator, {"post_id": post.id, "handle": "ghost.handle"}).status == "rejected"


def test_a_working_handle_clears_its_own_flag(db):
    """The performer fixed their privacy setting — the next good post un-flags them
    without anyone touching Settings."""
    perf = _perf(db, "Fixed", "fixed.handle")
    p1, p2 = _post(db, title="a"), _post(db, title="b")

    ps.record_collab_outcome(db, post_id=p1.id, sent=[], rejected=["fixed.handle"])
    db.commit()
    assert perf.handle_status == "needs_check"

    ps.record_collab_outcome(db, post_id=p2.id, sent=["fixed.handle"], rejected=[])
    db.commit()
    assert perf.handle_status == "ok"
    assert perf.handle_error is None


def test_handle_match_is_case_insensitive(db):
    perf = _perf(db, "Caps", "mixedcase")
    post = _post(db, title="x")
    ps.record_collab_outcome(db, post_id=post.id, sent=["MixedCase"], rejected=[])
    db.commit()
    row = db.get(PostCollaborator, {"post_id": post.id, "handle": "MixedCase"})
    assert row.performer_id == perf.id


def test_unknown_handle_is_still_recorded(db):
    """A handle with no matching performer row (typed by hand, performer since deleted)
    still belongs in the ledger — it just has nothing to flag."""
    post = _post(db, title="x")
    ps.record_collab_outcome(db, post_id=post.id, sent=["stranger"], rejected=[])
    db.commit()
    row = db.get(PostCollaborator, {"post_id": post.id, "handle": "stranger"})
    assert row.status == "sent" and row.performer_id is None


def test_rerunning_updates_in_place(db):
    """A retry must not raise on the composite primary key."""
    _perf(db, "Retry", "retry.handle")
    post = _post(db, title="x")
    ps.record_collab_outcome(db, post_id=post.id, sent=[], rejected=["retry.handle"])
    db.commit()
    ps.record_collab_outcome(db, post_id=post.id, sent=["retry.handle"], rejected=[])
    db.commit()
    rows = db.query(PostCollaborator).filter_by(post_id=post.id).all()
    assert len(rows) == 1 and rows[0].status == "sent"


# --- lift -------------------------------------------------------------------------

def _ig_cred(db):
    c = db.query(PlatformCredential).filter_by(platform="instagram").first()
    if not c:
        c = PlatformCredential(id=uuid.uuid4().hex, platform="instagram",
                               access_token="x", account_name="me")
        db.add(c)
        db.commit()
    return c


def _published_ig_post(db, *, when, saves, likes=1, reach=100):
    cred = _ig_cred(db)
    post = _post(db, title="p", posted_at=when)
    db.add(PostPlatform(post_id=post.id, platform_id=cred.id, status="published",
                        remote_id=uuid.uuid4().hex, posted_at=when))
    db.add(EngagementSnapshot(
        post_id=post.id, platform="instagram", sampled_at=when + timedelta(days=7),
        likes=likes, comments_count=0, views=0, reposts=0, reach=reach, saves=saves,
        shares=0, profile_visits=0, follows=0,
    ))
    db.commit()
    return post


def test_lift_compares_collab_posts_against_the_solo_baseline(db):
    base = datetime.utcnow() - timedelta(days=30)
    # Six solo posts, each worth 5*1 save + 1 like = 6.
    for i in range(6):
        _published_ig_post(db, when=base + timedelta(hours=i), saves=1)
    # Two collab posts worth 5*3 + 1 = 16 → lift ≈ 2.67.
    perf = _perf(db, "Star", "star.handle")
    for i in range(2):
        p = _published_ig_post(db, when=base + timedelta(days=1, hours=i), saves=3)
        db.add(PostCollaborator(post_id=p.id, handle="star.handle",
                                performer_id=perf.id, status="sent"))
    db.commit()

    out = ac.collab_lift(db, window="7d")
    assert out["solo_posts"] == 6
    assert out["solo_median_quality"] == 6.0
    assert out["collab_posts"] == 2

    row = next(r for r in out["performers"] if r["handle"] == "star.handle")
    assert row["posts"] == 2
    assert row["median_quality"] == 16.0
    assert row["lift"] == round(16 / 6, 2)
    assert row["provisional"] is True          # 2 posts is thin evidence, say so


def test_lift_is_none_when_baseline_is_too_thin(db):
    """Two solo posts can't establish a baseline — report the collab numbers without
    inventing a ratio."""
    base = datetime.utcnow() - timedelta(days=30)
    for i in range(2):
        _published_ig_post(db, when=base + timedelta(hours=i), saves=1)
    perf = _perf(db, "Thin", "thin.handle")
    p = _published_ig_post(db, when=base + timedelta(days=1), saves=9)
    db.add(PostCollaborator(post_id=p.id, handle="thin.handle",
                            performer_id=perf.id, status="sent"))
    db.commit()

    out = ac.collab_lift(db, window="7d")
    assert out["solo_median_quality"] is None
    row = next(r for r in out["performers"] if r["handle"] == "thin.handle")
    assert row["lift"] is None
    assert row["median_quality"] > 0


def test_rejected_collabs_do_not_count_as_credited(db):
    """A refused invite never reached anyone, so it must not be scored as a collab."""
    base = datetime.utcnow() - timedelta(days=30)
    for i in range(6):
        _published_ig_post(db, when=base + timedelta(hours=i), saves=1)
    perf = _perf(db, "Refused", "refused.handle")
    p = _published_ig_post(db, when=base + timedelta(days=1), saves=50)
    db.add(PostCollaborator(post_id=p.id, handle="refused.handle",
                            performer_id=perf.id, status="rejected"))
    db.commit()

    out = ac.collab_lift(db, window="7d")
    assert out["collab_posts"] == 0
    assert all(r["handle"] != "refused.handle" for r in out["performers"])
    # and that post counts toward the solo baseline instead
    assert out["solo_posts"] == 7
