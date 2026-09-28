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


def test_fanout_recovers_failed_platform_but_retry_claim_does_not(sessions, monkeypatch):
    with sessions() as db:
        post = db.get(Post, 'p')
        post.status = 'posted'
        db.add(PostPlatform(post_id='p', platform_id='ig', status='failed', retry_count=99))
        db.commit()
        assert feed_claim.platform_claim(db, 'p', 'ig') is None
        calls = []
        def publish(db, cred, post, fired_at):
            calls.append(cred.id)
            scheduler._record_published(db, post, cred, 'live', 'https://ig/live', fired_at)
        monkeypatch.setattr(scheduler, '_post_to_platform', publish)
        scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=['instagram'])
        assert calls == ['ig']
        assert db.get(PostPlatform, ('p', 'ig')).status == 'posted'


def test_instagram_post_now_accepts_failed_archive(sessions, monkeypatch):
    from routes import posts
    monkeypatch.setattr(posts.r2, 'configured', lambda: True)
    with sessions() as db:
        db.get(Post, 'p').status = 'failed'
        db.add(PostPlatform(post_id='p', platform_id='ig', status='failed'))
        db.commit()
        result = posts.instagram_post_now('p', db=db, _user=None)
        assert result.queued
        assert db.get(Post, 'p').status == 'failed'
        assert db.get(PostPlatform, ('p', 'ig')).next_retry_at is not None


def test_renewal_db_failure_does_not_abort_a_live_thread(sessions):
    from sqlalchemy.exc import OperationalError
    with sessions() as db:
        claim = feed_claim.platform_claim(db, 'p', 'ig')
        sent = []
        def fail_second_renewal(session):
            if len(sent) == 1:
                raise OperationalError('renew', {}, RuntimeError('database is locked'))
        with claim:
            event.listen(db, 'before_commit', fail_second_renewal)
            with http_client.client(transport=httpx.MockTransport(
                    lambda r: sent.append(r.url.path) or httpx.Response(200))) as client:
                client.post('https://test.invalid/root')
                client.post('https://test.invalid/reply')
            event.remove(db, 'before_commit', fail_second_renewal)
            db.commit()
        assert sent == ['/root', '/reply']


@pytest.mark.parametrize('failure_site', ['take', 'exit', 'release', 'review'])
def test_fanout_contains_database_failures_and_keeps_recovery_rows(sessions, monkeypatch, failure_site):
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr(scheduler, 'SessionLocal', sessions)
    with sessions() as db:
        post = db.get(Post, 'p')
        post.status = 'posted'
        post.updated_at = datetime.utcnow() - timedelta(hours=2)
        db.delete(db.get(PlatformCredential, 'ig'))
        db.commit()
        db.add(PlatformCredential(id='bs', platform='bluesky', access_token='token'))
        db.flush()
        db.add(PlatformCredential(id='ig', platform='instagram', access_token='token'))
        # Insert Bluesky first so the failed destination precedes Instagram.
        if failure_site != 'take':
            db.add(PostPlatform(post_id='p', platform_id='bs', status='pending',
                publish_claim_token='dead' if failure_site == 'review' else None,
                publish_claimed_at=datetime.utcnow() - timedelta(hours=1) if failure_site == 'review' else None))
        db.commit()
        armed = False
        failed = False
        calls = []
        def commit_fault(session):
            nonlocal failed
            if armed and not failed:
                failed = True
                raise OperationalError('commit', {}, RuntimeError('database is locked'))
        event.listen(db, 'before_commit', commit_fault)
        original_take = feed_claim._take
        def take(db, model, key, conditions, now):
            nonlocal armed
            if failure_site == 'take' and not failed:
                armed = True
            return original_take(db, model, key, conditions, now)
        monkeypatch.setattr(feed_claim, '_take', take)
        original_review = scheduler._flag_needs_review
        def review(*args):
            nonlocal armed
            original_review(*args)
            armed = True
        monkeypatch.setattr(scheduler, '_flag_needs_review', review)
        original_exit = feed_claim.Claim.__exit__
        def exit_claim(claim, *args):
            nonlocal armed
            if failure_site == 'exit' and not failed:
                armed = True
            return original_exit(claim, *args)
        monkeypatch.setattr(feed_claim.Claim, '__exit__', exit_claim)
        def release_fault(session, statement, *a, **kw):
            nonlocal armed
            if failure_site == 'release' and statement.is_update:
                values = statement.compile().params
                if 'publish_claim_token' in values and values['publish_claim_token'] is None:
                    armed = True
        event.listen(db, 'do_orm_execute', lambda state: release_fault(db, state.statement))
        def publish(db, cred, post, fired_at):
            calls.append(cred.platform)
            if cred.platform == 'bluesky' and failure_site in ('exit', 'release'):
                return
            scheduler._record_published(db, post, cred, 'live', 'https://live', fired_at)
        monkeypatch.setattr(scheduler, '_post_to_platform', publish)
        scheduler.fanout_to_platforms(db, post, fired_at=datetime.utcnow(), targets=['bluesky', 'instagram'])
        assert failed
        assert 'instagram' in calls
        assert db.get(PostPlatform, ('p', 'ig')).status == 'posted'
        row = db.get(PostPlatform, ('p', 'bs'))
        assert row.status == 'pending'
        # An unsent claim failure stays visible to the stranded sweep; ambiguous
        # sends stay pending and are flagged for human review, never auto-reposted.
        scheduler.requeue_stranded(db, now=datetime.utcnow() + timedelta(hours=1))
        assert row.next_retry_at is not None or row.error_message


@pytest.mark.parametrize('platform', [False, True])
def test_lost_claim_preserves_delivery_proof_only(sessions, monkeypatch, tmp_path, platform):
    with sessions() as first, sessions() as second:
        post = first.get(Post, 'p')
        src = tmp_path / 'source'; src.write_bytes(b'original')
        post.original_path = str(src)
        first.commit()
        parent = feed_claim.post_claim(first, 'p')
        child = feed_claim.platform_claim(first, 'p', 'ig') if platform else None
        newer = None
        def replace_owner():
            nonlocal newer
            model = PostPlatform if platform else Post
            second.execute(update(model).values(publish_claimed_at=datetime.utcnow() - timedelta(hours=1)))
            second.commit()
            newer = (feed_claim.platform_claim(second, 'p', 'ig') if platform
                     else feed_claim.post_claim(second, 'p'))
        def upload(**kw):
            replace_owner()
            post.title = 'stale bookkeeping'
            return 'flickr-live'
        monkeypatch.setattr(scheduler.storage, 'DERIVATIVES', tmp_path)
        monkeypatch.setattr(scheduler.image, 'make_derivative', lambda s, d, n: d.write_bytes(b'jpeg'))
        monkeypatch.setattr(scheduler.flickr, 'upload_photo', upload)
        bookkeeping = []
        monkeypatch.setattr(scheduler, '_flickr_post_bookkeeping', lambda *a: bookkeeping.append(True))
        with pytest.raises(feed_claim.ClaimLost):
            with parent:
                if platform:
                    with child:
                        replace_owner()
                        post.title = 'stale bookkeeping'
                        scheduler._record_published(first, post, first.get(PlatformCredential, 'ig'),
                                                    'ig-live', 'https://live', datetime.utcnow())
                else:
                    scheduler._flickr_post(first, post, datetime.utcnow())
        with sessions() as check:
            fresh = check.get(Post, 'p')
            assert fresh.title is None
            if platform:
                row = check.get(PostPlatform, ('p', 'ig'))
                assert row.remote_id == 'ig-live' and row.status == 'posted'
                assert row.publish_claim_token == newer.token
            else:
                assert fresh.flickr_photo_id == 'flickr-live'
                assert fresh.status in ('posted', 'late')
                assert fresh.publish_claim_token == newer.token
            assert not bookkeeping
