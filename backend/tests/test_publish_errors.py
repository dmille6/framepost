"""Failure classification, and the channel state a REAUTH failure sets."""
import uuid

import pytest

from models import PlatformCredential, Post
from services import channel_health
from services.platforms.flickr import FlickrError
from services.platforms.instagram import InstagramError
from services.publish_errors import FailureCategory, classify


def _cred(db, platform="flickr", default_target=True):
    c = PlatformCredential(id=uuid.uuid4().hex, platform=platform,
                           access_token="x", account_name="me",
                           default_target=1 if default_target else 0)
    db.add(c)
    db.commit()
    return c


# --- the bug this module exists for ------------------------------------------------

def test_flickr_missing_delete_scope_is_reauth_not_retry():
    """The real message that retried nightly for two weeks. It can never succeed on a
    retry — only reconnecting with the delete scope fixes it."""
    err = FlickrError(
        "flickr error 99: Insufficient permissions. "
        "Method requires delete privileges; write granted.", code=99)
    f = classify("flickr", err)
    assert f.category is FailureCategory.REAUTH
    assert f.requires_reauth is True
    assert f.retryable is False
    assert "reconnect" in f.user_message.lower()


def test_expired_instagram_token_is_reauth():
    err = InstagramError("Error validating access token: Session has been invalidated")
    f = classify("instagram", err)
    assert f.category is FailureCategory.REAUTH
    assert f.retryable is False


# --- the other categories ----------------------------------------------------------

def test_rate_limit_is_retryable():
    f = classify("instagram", InstagramError("Application request limit reached"))
    assert f.category is FailureCategory.RETRY
    assert f.retryable is True
    assert f.requires_reauth is False


def test_bad_content_is_not_retried():
    """Re-submitting the same oversized caption just fails again."""
    f = classify("instagram", InstagramError("The caption is too long"))
    assert f.category is FailureCategory.BAD_CONTENT
    assert f.retryable is False


def test_aspect_ratio_rejection_is_bad_content():
    f = classify("instagram", InstagramError("Aspect ratio not supported"))
    assert f.category is FailureCategory.BAD_CONTENT


def test_transport_failure_falls_back_to_retry():
    f = classify("bluesky", RuntimeError("Connection timed out"))
    assert f.category is FailureCategory.RETRY


def test_unknown_error_stays_retryable():
    """A novel failure must not be misfiled as fatal — retry, but say it's unrecognised."""
    f = classify("pixelfed", RuntimeError("something nobody has seen before"))
    assert f.category is FailureCategory.UNKNOWN
    assert f.retryable is True


def test_permanent_flag_is_honoured_when_no_rule_matches():
    err = InstagramError("some unmapped rejection", permanent=True)
    f = classify("instagram", err)
    assert f.retryable is False
    assert f.category is FailureCategory.BAD_CONTENT


def test_detail_preserves_the_raw_platform_text():
    f = classify("flickr", FlickrError("flickr error 99: Insufficient permissions", code=99))
    assert "Insufficient permissions" in f.detail
    assert f.code == "99"


# --- channel health ----------------------------------------------------------------

def test_flagging_sets_reauth_state(db):
    cred = _cred(db)
    changed = channel_health.flag_reauth(db, "flickr", "Flickr needs reconnecting.")
    assert changed is True
    assert cred.auth_status == "reauth_required"
    assert cred.auth_flagged_at is not None


def test_flagging_is_idempotent(db):
    """The nightly sweep hits this every run; it must not re-log or re-stamp."""
    _cred(db)
    assert channel_health.flag_reauth(db, "flickr", "same message") is True
    assert channel_health.flag_reauth(db, "flickr", "same message") is False


def test_clear_restores_the_channel(db):
    cred = _cred(db)
    channel_health.flag_reauth(db, "flickr", "broken")
    channel_health.clear(db, "flickr")
    assert cred.auth_status == "ok"
    assert cred.auth_error is None


def test_broken_channels_report_blast_radius(db):
    """The count is what makes the warning actionable."""
    _cred(db)
    for _ in range(3):
        db.add(Post(id=uuid.uuid4().hex, status="pending",
                    scheduled_at=__import__("datetime").datetime(2027, 1, 1)))
    db.commit()
    channel_health.flag_reauth(db, "flickr", "Flickr needs reconnecting.")

    broken = channel_health.broken_channels(db)
    assert len(broken) == 1
    assert broken[0]["platform"] == "flickr"
    assert broken[0]["scheduled_posts_affected"] == 3


def test_channel_that_receives_no_new_posts_reports_no_impact(db):
    """A platform switched off as a default target isn't costing you the queue."""
    _cred(db, platform="pinterest", default_target=False)
    db.add(Post(id=uuid.uuid4().hex, status="pending",
                scheduled_at=__import__("datetime").datetime(2027, 1, 1)))
    db.commit()
    channel_health.flag_reauth(db, "pinterest", "Pinterest needs reconnecting.")
    assert channel_health.broken_channels(db)[0]["scheduled_posts_affected"] == 0


def test_summary_is_ok_when_nothing_is_broken(db):
    _cred(db)
    assert channel_health.summary(db)["ok"] is True
