"""Import failures must retain bytes even when there is no post row to own them."""
import asyncio
import io
from types import SimpleNamespace

from fastapi import UploadFile
from PIL import Image
import pytest

from models import Post
from routes.posts import upload
from services import import_pipeline, storage


@pytest.fixture()
def layout(tmp_path, monkeypatch):
    for name in ('ROOT', 'INCOMING', 'ORIGINALS', 'THUMBNAILS', 'DERIVATIVES',
                 'PREVIEWS', 'ERRORS', 'BACKUP', 'REELS'):
        monkeypatch.setattr(storage, name, tmp_path / name.lower())
    storage.ensure_layout()
    monkeypatch.setattr(storage, 'below_hardstop', lambda db: False)
    return tmp_path


@pytest.mark.parametrize('stage', ['thumbnail', 'flush', 'commit'])
def test_failed_import_keeps_source_recoverable(db, layout, monkeypatch, stage):
    src = storage.INCOMING / 'only-copy.jpg'
    Image.new('RGB', (20, 20), 'red').save(src)
    original = src.read_bytes()
    def fail(*a, **k): raise RuntimeError('injected failure')
    if stage == 'thumbnail': monkeypatch.setattr(import_pipeline.image, 'make_thumbnail', fail)
    else: monkeypatch.setattr(db, stage, fail)
    with pytest.raises(RuntimeError):
        import_pipeline.import_image(src, db=db, source='watch_folder', actor='watcher',
                                     allow_duplicate=False, original_filename=src.name)
    assert src.exists() and src.read_bytes() == original
    db.rollback()
    with db.no_autoflush:
        assert db.query(Post).count() == 0


def test_upload_error_quarantines_received_bytes(db, layout, monkeypatch):
    original = b'uploaded bytes worth recovering'
    def fail(*a, **k): raise RuntimeError('injected import failure')
    monkeypatch.setattr(import_pipeline, 'import_image', fail)
    with pytest.raises(RuntimeError):
        asyncio.run(upload(file=UploadFile(filename='photo.jpg', file=io.BytesIO(original)),
                           allow_duplicate=False, db=db, user=SimpleNamespace(username='u')))
    assert any(p.read_bytes() == original for p in storage.ERRORS.iterdir() if p.suffix != '.log')


def test_success_consumes_source_only_after_durable_copy(db, layout, monkeypatch):
    from services import ai_tagging
    monkeypatch.setattr(ai_tagging, 'apply_to_post', lambda *a: None)
    src = storage.INCOMING / 'photo.jpg'
    Image.new('RGB', (20, 20), 'red').save(src)
    original = src.read_bytes()
    result = import_pipeline.import_image(src, db=db, source='watch_folder', actor='watcher',
                                          allow_duplicate=False, original_filename=src.name)
    from pathlib import Path
    assert not src.exists()
    assert Path(result.post.original_path).read_bytes() == original
    assert db.get(Post, result.post.id) is not None
