"""A social delivery must not stand in for the Flickr archive copy."""
from datetime import datetime, timedelta

import pytest

from models import Post
from routes.history import list_history, post_platforms
from routes.posts import get_post
from services.cleanup import purge_expired_originals


@pytest.mark.parametrize('targets', [None, '["flickr", "instagram"]', 'malformed'])
def test_missing_archive_keeps_original_even_for_legacy_posted_rows(db, tmp_path, targets):
    original, thumb = tmp_path / 'original', tmp_path / 'thumb'
    original.write_bytes(b'only archive'); thumb.write_bytes(b'thumb')
    db.add(Post(id='p', status='posted', target_platforms=targets,
                posted_at=datetime.utcnow() - timedelta(days=40),
                original_path=str(original), thumbnail_path=str(thumb)))
    db.commit()
    assert purge_expired_originals(db) == 0
    assert original.exists()
    chip = post_platforms('p', db=db, _user=None)[0]
    assert chip.status == 'failed'
    assert chip.posted_at is None
    assert get_post('p', db=db, _user=None).status == 'failed'
    assert list_history(db=db, _user=None, status_filter=None, q=None,
                        limit=200, offset=0)[0].status == 'failed'


def test_explicit_flickr_opt_out_has_no_flickr_chip_and_can_purge(db, tmp_path):
    original, thumb = tmp_path / 'original', tmp_path / 'thumb'
    original.write_bytes(b'original'); thumb.write_bytes(b'thumb')
    db.add(Post(id='p', status='posted', target_platforms='["instagram"]',
                posted_at=datetime.utcnow() - timedelta(days=40),
                original_path=str(original), thumbnail_path=str(thumb)))
    db.commit()
    assert post_platforms('p', db=db, _user=None) == []
    assert purge_expired_originals(db) == 1
