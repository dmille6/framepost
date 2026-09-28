"""Separate worker sessions must agree on who owns a delivery, even after a crash."""
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from database import Base
from models import PlatformCredential, Post, PostPlatform, Reel, ReelPhoto
from services import feed_claim, http_client, scheduler


@pytest.fixture()
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'claims.db'}")
    @event.listens_for(engine, 'connect')
    def pragma(c, _):
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([Post(id='p', status='pending', scheduled_at=datetime.utcnow()),
                    PlatformCredential(id='ig', platform='instagram', access_token='token')])
        db.commit()
    yield factory
    engine.dispose()


def test_fire_cannot_upload_the_same_flickr_post_twice(sessions, monkeypatch):
    monkeypatch.setattr(scheduler, 'SessionLocal', sessions)
    calls = []
    def publish(db, post, fired_at):
        calls.append(post.id)
        if len(calls) == 1:
            scheduler.fire_due_posts()  # another session scans while this upload waits
        post.flickr_photo_id = 'remote'; post.status = 'posted'
        db.commit()
    monkeypatch.setattr(scheduler, '_flickr_post', publish)
    monkeypatch.setattr(scheduler, 'fanout_to_platforms', lambda *a, **k: None)
    scheduler.fire_due_posts()
    assert calls == ['p']


def test_retry_and_fanout_cannot_send_same_platform_row(sessions, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, 'SessionLocal', sessions)
    with sessions() as setup:
        setup.get(Post, 'p').status = 'posted'
        setup.add(PostPlatform(post_id='p', platform_id='ig', status='pending',
                               next_retry_at=datetime.utcnow() - timedelta(minutes=1)))
        setup.commit()
    with sessions() as first, sessions() as second:
        p1, c1 = first.get(Post, 'p'), first.get(PlatformCredential, 'ig')
        p2, c2 = second.get(Post, 'p'), second.get(PlatformCredential, 'ig')
        # Both jobs already hold their scan results. Losing the claim must still stop
        # dispatch, and after release the durable posted status must stop a stale scan.
        def publish(db, cred, post, fired_at):
            calls.append(post.id)
            if len(calls) == 1:
                scheduler.retry_due_platform_posts()
            row = db.get(PostPlatform, ('p', 'ig'))
            row.status, row.remote_id = 'posted', 'm1'
            db.commit()
        monkeypatch.setattr(scheduler, '_post_to_platform', publish)
        scheduler.fanout_to_platforms(first, p1, fired_at=datetime.utcnow(), targets=['instagram'])
        scheduler.fanout_to_platforms(second, p2, fired_at=datetime.utcnow(), targets=['instagram'])
    assert calls == ['p']


@pytest.mark.parametrize('platform', [False, True])
def test_stale_takeover_fences_commits_requests_and_release(sessions, platform):
    with sessions() as first, sessions() as second:
        def take(db, **kw):
            return (feed_claim.platform_claim(db, 'p', 'ig', **kw) if platform
                    else feed_claim.post_claim(db, 'p', **kw))
        old = take(first)
        model = PostPlatform if platform else Post
        first.execute(update(model).values(publish_claimed_at=datetime.utcnow() - timedelta(hours=1)))
        first.commit()
        newer = take(second)
        assert old and newer
        assert take(first) is None
        sent = []
        with pytest.raises(feed_claim.ClaimLost):
            with old:
                first.get(Post, 'p').title = 'stale write'
                with http_client.client(transport=httpx.MockTransport(
                        lambda r: sent.append(r) or httpx.Response(200))) as client:
                    client.post('https://test.invalid/publish')
        assert sent == []
        with sessions() as check:
            model = PostPlatform if platform else Post
            key = ('p', 'ig') if platform else 'p'
            assert check.get(model, key).publish_claim_token == newer.token
            assert check.get(Post, 'p').title is None
        with newer:
            second.get(Post, 'p').title = 'new owner'
            second.commit()


def test_requests_renew_lease_and_sweep_skips_live_attempt(sessions):
    with sessions() as db:
        post = db.get(Post, 'p'); post.status = 'posted'
        post.updated_at = datetime.utcnow() - timedelta(hours=2)
        db.commit()
        lease = feed_claim.platform_claim(db, 'p', 'ig')
        with lease:
            with http_client.client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
                c.get('https://test.invalid/poll')
            with sessions() as other:
                assert scheduler.requeue_stranded(other) == 0
            row = db.get(PostPlatform, ('p', 'ig'))
            assert row.publish_claimed_at > datetime.utcnow() - timedelta(seconds=5)
        # A dead owner has no finally block. Its checkpoint and row become recoverable.
        row.publish_claimed_at = datetime.utcnow() - timedelta(hours=1)
        row.publish_claim_token = 'dead'
        row.ig_container = '{"id":"container"}'
        db.commit()
        assert scheduler.requeue_stranded(db) == 1
        recovered = feed_claim.platform_claim(db, 'p', 'ig')
        assert recovered is not None
        with recovered:
            assert db.get(PostPlatform, ('p', 'ig')).ig_container == '{"id":"container"}'


def test_claim_migration_round_trip_preserves_child_rows(sessions):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('claim_migration', Path(__file__).parents[1] /
            'alembic/versions/0040_feed_publish_claim.py')
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with sessions() as db:
        db.add(Reel(id='r', cover_post_id='p')); db.flush()
        db.add_all([ReelPhoto(reel_id='r', post_id='p', position=0),
                    PostPlatform(post_id='p', platform_id='ig', remote_id='live', status='posted')])
        db.commit()
        with db.bind.begin() as conn:
            migration.op = Operations(MigrationContext.configure(conn))
            migration.downgrade()
            migration.upgrade()
        db.expire_all()
        assert db.get(ReelPhoto, ('r', 0)).post_id == 'p'
        assert db.get(PostPlatform, ('p', 'ig')).remote_id == 'live'


def test_context_exit_cannot_autoflush_a_lost_owners_changes(sessions):
    with sessions() as first, sessions() as second:
        old = feed_claim.post_claim(first, 'p')
        with pytest.raises(feed_claim.ClaimLost):
            with old:
                second.execute(update(Post).where(Post.id == 'p').values(
                    publish_claimed_at=datetime.utcnow() - timedelta(hours=1)))
                second.commit()
                newer = feed_claim.post_claim(second, 'p')
                first.get(Post, 'p').title = 'must be rolled back'
        with sessions() as check:
            assert check.get(Post, 'p').title is None
            assert check.get(Post, 'p').publish_claim_token == newer.token
