"""Rendering age is not publication age; scheduled videos may wait for months."""
from datetime import datetime, timedelta

import pytest

from models import Post, Reel
from services.cleanup import purge_expired_reels


@pytest.mark.parametrize('state,purge', [
    ('scheduled', False), ('draft', False), ('claimed', False),
    ('recently_posted', False), ('old_posted', True), ('abandoned', True),
    ('checkpointed', False),
])
def test_reel_retention_respects_publication_lifecycle(db, tmp_path, state, purge):
    now = datetime.utcnow()
    old = now - timedelta(days=40)
    mp4 = tmp_path / 'reel.mp4'; mp4.write_bytes(b'video')
    db.add(Post(id='p')); db.flush()
    reel = Reel(id='r', cover_post_id='p', status='ready', mp4_path=str(mp4),
                created_at=old, updated_at=old)
    if state == 'scheduled': reel.scheduled_at = now + timedelta(days=10)
    if state == 'claimed': reel.publish_claimed_at = now
    if state == 'recently_posted': reel.posted_at = now - timedelta(days=1)
    if state == 'old_posted': reel.posted_at = old
    if state == 'abandoned': reel.status = 'failed'
    if state == 'checkpointed':
        reel.status = 'failed'; reel.ig_container = '{}'
    db.add(reel); db.commit()
    assert purge_expired_reels(db) == int(purge)
    assert mp4.exists() is (not purge)


@pytest.mark.parametrize('race', ['regenerating', 'regenerated', 'regenerated_published', 'claimed', 'rescheduled'])
def test_reel_purge_rechecks_candidate_before_unlink(db, tmp_path, monkeypatch, race):
    from sqlalchemy import update
    now = datetime.utcnow()
    old = now - timedelta(days=40)
    path = tmp_path / 'reel.mp4'; path.write_bytes(b'old video')
    db.add(Post(id='p')); db.flush()
    db.add(Reel(id='r', cover_post_id='p', status='failed', updated_at=old, mp4_path=str(path),
                posted_at=old if race == 'regenerated_published' else None))
    db.commit()
    execute = db.execute
    raced = False
    def concurrent_update(statement, *args, **kwargs):
        nonlocal raced
        if statement.is_update and not raced:
            raced = True
            values = {
                'regenerating': dict(status='pending', updated_at=now),
                'regenerated': dict(status='ready', updated_at=now),
                'regenerated_published': dict(status='ready', updated_at=now),
                'claimed': dict(publish_claimed_at=now, publish_claim_token='owner'),
                'rescheduled': dict(scheduled_at=now),
            }[race]
            execute(update(Reel).where(Reel.id == 'r').values(**values))
            db.commit()
            if race.startswith('regenerated'): path.write_bytes(b'new render')
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', concurrent_update)
    assert purge_expired_reels(db) == 0
    assert path.exists()
    assert db.get(Reel, 'r').mp4_path == str(path)


def test_reel_purge_commit_failure_keeps_file(db, tmp_path, monkeypatch):
    path = tmp_path / 'reel.mp4'; path.write_bytes(b'video')
    db.add(Post(id='p')); db.flush()
    db.add(Reel(id='r', cover_post_id='p', status='failed',
                updated_at=datetime.utcnow() - timedelta(days=40), mp4_path=str(path)))
    db.commit()
    def fail(): raise RuntimeError('database is locked')
    monkeypatch.setattr(db, 'commit', fail)
    with pytest.raises(RuntimeError): purge_expired_reels(db)
    db.rollback()
    assert path.exists() and db.get(Reel, 'r').mp4_path == str(path)
