"""Instagram images are staged in our own bucket rather than served from Flickr.

Meta's publishing API only ingests from a public URL, which made Flickr the de-facto
host for every IG post. On 2026-09-11 Meta could not fetch from live.staticflickr.com at
all (9004/2207052) while fetching the identical bytes from an R2 presigned URL in the
same minute. These tests pin the behaviour that took Flickr out of that path.

Nothing here touches the real bucket — conftest strips R2 credentials from every test by
default and r2_stub supplies an in-memory one.
"""
import uuid

import pytest
from PIL import Image

from models import PlatformCredential, Post, PostPlatform
from services import ig_variant


def _jpeg(tmp_path, w: int, h: int):
    path = tmp_path / f"{uuid.uuid4().hex}.jpg"
    Image.new("RGB", (w, h), (90, 20, 60)).save(path, "JPEG")
    return path


def _post(db, tmp_path, *, w=1200, h=1500) -> Post:
    p = Post(id=uuid.uuid4().hex, status="pending", width=w, height=h,
             original_path=str(_jpeg(tmp_path, w, h)))
    db.add(p)
    db.commit()
    return p


def _cred(db) -> PlatformCredential:
    c = PlatformCredential(id=uuid.uuid4().hex, platform="instagram")
    db.add(c)
    db.commit()
    return c


# --- staging ---------------------------------------------------------------------------

def test_the_variant_lands_in_the_bucket_and_the_url_is_presigned(db, tmp_path, r2_stub):
    post, cred = _post(db, tmp_path), _cred(db)
    ref, url = ig_variant.ensure_staged(
        db, post, None, platform_id=cred.id, ratio_key="4:5", fit="crop", offset=None)
    assert len(r2_stub) == 1
    key = next(iter(r2_stub))
    assert ref == ig_variant.R2_PREFIX + key
    assert url == f"https://r2.test/{key}?sig=stub"
    assert r2_stub[key][:2] == b"\xff\xd8"  # a real JPEG, not an error page


def test_flickr_is_never_consulted_when_r2_is_configured(db, tmp_path, r2_stub, monkeypatch):
    """The whole point. A staging path that still called Flickr would still be broken."""
    def _boom(*a, **kw):
        raise AssertionError("Flickr was called while R2 was configured")

    monkeypatch.setattr(ig_variant.flickr, "upload_photo", _boom)
    monkeypatch.setattr(ig_variant.flickr, "get_display_image_url", _boom)
    post, cred = _post(db, tmp_path), _cred(db)
    ig_variant.ensure_staged(db, post, None, platform_id=cred.id,
                             ratio_key="4:5", fit="crop", offset=None)


def test_a_native_staging_keeps_the_photos_own_shape(db, tmp_path, r2_stub):
    """An in-range photo is re-hosted, not reshaped — cropping one that Meta would have
    accepted as-is would silently change what gets published."""
    post, cred = _post(db, tmp_path, w=1500, h=1000), _cred(db)
    ig_variant.ensure_staged(
        db, post, None, platform_id=cred.id,
        ratio_key=ig_variant.NATIVE_RATIO_KEY, fit="crop", offset=None)
    import io
    with Image.open(io.BytesIO(next(iter(r2_stub.values())))) as im:
        assert abs((im.width / im.height) - 1.5) < 0.01


def test_the_staged_ref_survives_for_a_retry(db, tmp_path, r2_stub):
    """Committed before the publish attempt so a rolled-back failure doesn't re-upload."""
    post, cred = _post(db, tmp_path), _cred(db)
    ref, _ = ig_variant.ensure_staged(
        db, post, None, platform_id=cred.id, ratio_key="4:5", fit="crop", offset=None)
    pp = db.get(PostPlatform, (post.id, cred.id))
    assert pp is not None and pp.staging_remote_id.startswith(ig_variant.R2_PREFIX)
    assert ig_variant._decode_staging(pp.staging_remote_id) == (ref, "4:5")


def test_a_second_attempt_reuses_the_object_and_mints_a_fresh_link(db, tmp_path, r2_stub):
    """Presigned URLs expire; the object does not. Re-uploading each retry would waste a
    round trip and orphan the previous copy."""
    post, cred = _post(db, tmp_path), _cred(db)
    ref1, url1 = ig_variant.ensure_staged(
        db, post, None, platform_id=cred.id, ratio_key="4:5", fit="crop", offset=None)
    pp = db.get(PostPlatform, (post.id, cred.id))
    ref2, url2 = ig_variant.ensure_staged(
        db, post, pp, platform_id=cred.id, ratio_key="4:5", fit="crop", offset=None)
    assert ref2 == ref1
    assert len(r2_stub) == 1
    assert url2 == url1  # stub is deterministic; the real one re-signs


# --- cleanup ---------------------------------------------------------------------------

def test_publishing_clears_the_object_out_of_the_bucket(db, tmp_path, r2_stub):
    post, cred = _post(db, tmp_path), _cred(db)
    ig_variant.ensure_staged(db, post, None, platform_id=cred.id,
                             ratio_key="4:5", fit="crop", offset=None)
    pp = db.get(PostPlatform, (post.id, cred.id))
    ig_variant.cleanup_staged(db, pp)
    db.commit()
    assert r2_stub == {}
    assert pp.staging_remote_id is None


def test_deletion_routes_to_the_backend_the_ref_names(db, monkeypatch, r2_stub):
    """One column holds both Flickr photo ids and R2 keys. Sending an R2 key to Flickr's
    delete endpoint would fail nightly and never clean anything."""
    def _boom(*a, **kw):
        raise AssertionError("an R2 ref was sent to Flickr's delete")

    monkeypatch.setattr(ig_variant.flickr, "rest_call", _boom)
    r2_stub["ig/abc/def.jpg"] = b"x"
    ig_variant._delete_photo(db, ig_variant.R2_PREFIX + "ig/abc/def.jpg")
    assert r2_stub == {}


# --- the fallback ------------------------------------------------------------------------

def test_without_r2_the_flickr_path_is_untouched(db, tmp_path, monkeypatch):
    """A half-configured install must degrade to the old behaviour, not fail to post."""
    from services import r2
    assert not r2.configured()          # conftest strips the credentials
    uploaded: list = []
    monkeypatch.setattr(ig_variant.flickr, "upload_photo",
                        lambda **kw: uploaded.append(kw) or "999")
    monkeypatch.setattr(ig_variant.flickr, "get_display_image_url",
                        lambda db, pid, **kw: f"https://flickr/{pid}.jpg")
    monkeypatch.setattr(ig_variant, "_wait_until_fetchable", lambda url, **kw: None)
    post, cred = _post(db, tmp_path), _cred(db)
    ref, url = ig_variant.ensure_staged(
        db, post, None, platform_id=cred.id, ratio_key="4:5", fit="crop", offset=None)
    assert ref == "999" and url == "https://flickr/999.jpg"
    assert len(uploaded) == 1


def test_a_published_carousel_leaves_nothing_in_the_bucket(db, tmp_path, r2_stub, monkeypatch):
    """One object per frame is staged. Cleanup ran only on the single-photo path, so a
    carousel leaked a file per frame on every publish — and because each row kept
    claiming its ref, the orphan sweep read them as live and skipped them too."""
    from datetime import datetime

    from services import carousel, scheduler

    posts = []
    for _ in range(3):
        posts.append(_post(db, tmp_path))
    for p in posts:
        p.flickr_photo_id = "1"
        p.status = "posted"
    db.commit()
    cid = carousel.group(db, posts, lead_id=posts[0].id)
    cred = _cred(db)

    monkeypatch.setattr(
        scheduler.instagram, "post_carousel",
        lambda **kw: {"remote_id": "1", "url": "https://instagram.com/p/x/",
                      "collaborators": [], "collaborators_rejected": []})

    lead = carousel.lead_for(db, cid)
    scheduler.fanout_to_platforms(db, lead, fired_at=datetime.now(), targets=["instagram"])
    db.commit()

    assert r2_stub == {}, f"leaked {len(r2_stub)} staged object(s)"
    for p in posts:
        pp = db.get(PostPlatform, (p.id, cred.id))
        assert pp is None or pp.staging_remote_id is None
