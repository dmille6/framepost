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
