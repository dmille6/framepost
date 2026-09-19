"""The publish-time half of Meta's readiness race.

`_await_container` asks for status_code and Meta says FINISHED. That answer does not
mean the media has replicated: on 2026-09-19 the container POST, the FINISHED reply and
a 400 "The media is not ready for publishing" all landed inside 400ms, and the post
never went out. The adapter now waits that race out in-process, because a post that
lands on time is worth more than one the worker retries minutes later.

The companion classification test lives in test_publish_errors.py — belt and braces:
this keeps the post on schedule, that one keeps it alive if the wait isn't enough.
"""
import json

import pytest

from services.platforms import instagram as ig


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _not_ready() -> FakeResponse:
    return FakeResponse(400, {"error": {
        "message": "Cannot Publish: The media is not ready for publishing, "
                   "please wait for a moment",
        "code": 9007,
    }})


def _published(media_id: str = "media-1") -> FakeResponse:
    return FakeResponse(200, {"id": media_id})


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(ig.time, "sleep", lambda s: slept.append(s))
    return slept


def _client_returning(responses, calls=None):
    """A fake _client whose media_publish POST walks `responses`, then 200s forever."""
    seq = list(responses)

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def post(self, path, data=None):
            if calls is not None:
                calls.append(data.get("creation_id"))
            return seq.pop(0) if seq else _published()

        def get(self, path, params=None):
            return FakeResponse(200, {"permalink": "https://instagram.com/p/abc/"})

    return lambda: FakeClient()


def test_waits_out_not_ready_and_publishes(monkeypatch, _no_real_sleeping):
    calls: list[str] = []
    monkeypatch.setattr(
        ig, "_client", _client_returning([_not_ready(), _not_ready(), _published()], calls))

    media_id, permalink = ig._publish_container("ig1", "container-9", "tok")

    assert media_id == "media-1"
    assert permalink == "https://instagram.com/p/abc/"
    assert calls == ["container-9"] * 3      # same container, never re-created
    assert _no_real_sleeping == [ig.PUBLISH_RETRY_INTERVAL] * 2


def test_gives_up_after_the_budget_and_stays_retryable(monkeypatch, _no_real_sleeping):
    """Exhausting the in-process wait must not be the end of the post: the raised text
    still has to classify as RETRY so the worker picks it up later."""
    from services.publish_errors import FailureCategory, classify

    monkeypatch.setattr(
        ig, "_client", _client_returning([_not_ready()] * ig.PUBLISH_RETRY_TRIES))

    with pytest.raises(ig.InstagramError) as excinfo:
        ig._publish_container("ig1", "container-9", "tok")

    assert len(_no_real_sleeping) == ig.PUBLISH_RETRY_TRIES - 1   # no sleep after the last try
    assert classify("instagram", excinfo.value).retryable is True


def test_other_4xx_is_not_retried(monkeypatch, _no_real_sleeping):
    """Re-posting the same creation_id against a real rejection just burns attempts."""
    calls: list[str] = []
    monkeypatch.setattr(ig, "_client", _client_returning([
        FakeResponse(400, {"error": {"message": "Invalid aspect ratio", "code": 2207}}),
    ], calls))

    with pytest.raises(ig.InstagramError, match="aspect ratio"):
        ig._publish_container("ig1", "container-9", "tok")

    assert len(calls) == 1
    assert _no_real_sleeping == []
