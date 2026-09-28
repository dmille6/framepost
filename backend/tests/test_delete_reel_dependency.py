"""A refused post deletion must leave the reel's source files intact."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from models import Post, Reel, ReelPhoto
from routes.posts import delete_post


@pytest.mark.parametrize('cover', [True, False])
def test_delete_reports_reel_dependencies_before_touching_files(db, tmp_path, cover):
    original = tmp_path / 'original'; original.write_bytes(b'only original')
    thumb = tmp_path / 'thumb'; thumb.write_bytes(b'thumb')
    db.add_all([Post(id='p', original_path=str(original), thumbnail_path=str(thumb)), Post(id='other')])
    db.flush()
    db.add(Reel(id='reel-one', cover_post_id='p' if cover else 'other', caption='Opening night'))
    db.flush()
    if not cover:
        db.add(ReelPhoto(reel_id='reel-one', position=0, post_id='p'))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        delete_post('p', db=db, user=SimpleNamespace(username='u'))
    assert exc.value.status_code == 409
    assert 'reel-one' in exc.value.detail and 'Opening night' in exc.value.detail
    assert original.read_bytes() == b'only original' and thumb.exists()
    assert db.get(Post, 'p') is not None


def test_commit_failure_does_not_unlink_files(db, tmp_path, monkeypatch):
    original = tmp_path / 'original'; original.write_bytes(b'only original')
    db.add(Post(id='p', original_path=str(original))); db.commit()
    def fail(): raise RuntimeError('disk full')
    monkeypatch.setattr(db, 'commit', fail)
    with pytest.raises(RuntimeError):
        delete_post('p', db=db, user=SimpleNamespace(username='u'))
    assert original.exists()


def test_reel_dependency_racing_delete_returns_409_and_preserves_files(db, tmp_path, monkeypatch):
    original = tmp_path / 'original'; original.write_bytes(b'original')
    db.add(Post(id='p', original_path=str(original))); db.commit()
    delete = db.delete
    def race(post):
        # The friendly dependency query already ran; the FK is the final arbiter.
        db.add(Reel(id='new-reel', cover_post_id='p'))
        db.commit()
        delete(post)
    monkeypatch.setattr(db, 'delete', race)
    with pytest.raises(HTTPException) as exc:
        delete_post('p', db=db, user=SimpleNamespace(username='u'))
    assert exc.value.status_code == 409
    assert 'reel' in exc.value.detail
    assert original.read_bytes() == b'original'
    assert db.get(Post, 'p') is not None
