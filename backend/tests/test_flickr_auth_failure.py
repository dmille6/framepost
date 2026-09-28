"""The Flickr upload path must use the same reconnect policy as social fanout."""
from datetime import datetime

import pytest

from models import PlatformCredential, Post
from services import publish_errors, scheduler
from services.platforms.flickr import FlickrError


@pytest.mark.parametrize('code', [98, 99])
def test_flickr_auth_code_stops_retry_and_flags_channel(db, code):
    post = Post(id='p', status='pending', scheduled_at=datetime.utcnow())
    cred = PlatformCredential(id='flickr', platform='flickr', access_token='token')
    db.add_all([post, cred]); db.commit()
    # The numeric code is authoritative even if Flickr changes/localises the message.
    err = FlickrError('Access refused', code=code)
    assert publish_errors.classify('flickr', err).requires_reauth
    scheduler._record_failure(db, post, err, datetime.utcnow())
    db.commit()
    assert post.status == 'failed' and post.next_retry_at is None
    assert cred.auth_status == 'reauth_required'
    assert 'reconnect' in cred.auth_error.lower()


def test_flickr_auth_text_also_reaches_the_classifier(db):
    post = Post(id='p', status='pending', scheduled_at=datetime.utcnow())
    db.add_all([post, PlatformCredential(id='flickr', platform='flickr', access_token='token')])
    db.commit()
    scheduler._record_failure(db, post, FlickrError('Invalid auth token'), datetime.utcnow())
    db.commit()
    assert post.status == 'failed' and post.next_retry_at is None
    assert db.get(PlatformCredential, 'flickr').auth_status == 'reauth_required'
