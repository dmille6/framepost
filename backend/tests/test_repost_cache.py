"""A deliberate Flickr re-upload must forget only the confirmed-deleted archive id."""
import pytest
from fastapi import HTTPException

from models import FlickrPhoto, Post
from routes.posts import repost_to_flickr
from services import duplicate
from services.platforms import flickr


@pytest.mark.parametrize('missing', [False, True])
def test_repost_invalidates_deleted_photo_cache(db, tmp_path, monkeypatch, missing):
    original = tmp_path / 'original.jpg'
    original.write_bytes(b'original')
    post = Post(id='p', status='posted', original_path=str(original),
                flickr_photo_id='old', sha256='hash')
    db.add_all([post, FlickrPhoto(flickr_photo_id='old', machine_tags='framepost:sha256=hash'),
                FlickrPhoto(flickr_photo_id='other', machine_tags='framepost:sha256=other')])
    db.commit()

    def delete(*a, **kw):
        if missing:
            raise flickr.FlickrError('Photo not found', code=1)
    monkeypatch.setattr(flickr, 'rest_call', delete)
    repost_to_flickr('p', db=db, _user=None)
    assert duplicate.find_in_flickr_cache(db, 'hash') is None
    assert db.get(FlickrPhoto, 'other') is not None
    assert post.status == 'pending' and post.flickr_photo_id is None


def test_repost_keeps_archive_identity_when_delete_fails(db, tmp_path, monkeypatch):
    original = tmp_path / 'original.jpg'
    original.write_bytes(b'original')
    post = Post(id='p', status='posted', original_path=str(original), flickr_photo_id='old')
    db.add_all([post, FlickrPhoto(flickr_photo_id='old')])
    db.commit()
    def fail(*a, **kw):
        raise flickr.FlickrError('Insufficient permissions', code=99)
    monkeypatch.setattr(flickr, 'rest_call', fail)
    with pytest.raises(HTTPException) as exc:
        repost_to_flickr('p', db=db, _user=None)
    assert exc.value.status_code == 409
    assert post.flickr_photo_id == 'old'
    assert db.get(FlickrPhoto, 'old') is not None
