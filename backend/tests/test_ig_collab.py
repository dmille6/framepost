"""Instagram collaborator handling — especially the fail-soft path.

Meta rejects the ENTIRE container if any collaborator handle is private, misspelled,
or deleted. A stale handle in the performer roster must never cost the post, so the
adapter drops the named offenders and retries.
"""
import json

import httpx
import pytest

from services.platforms import instagram as ig


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _reject(*handles: str) -> FakeResponse:
    return FakeResponse(400, {
        "error": {
            "message": "Invalid user id",
            "code": 110,
            "error_subcode": 2207018,
            "error_user_msg": "The following user(s) cannot be accessed: " + ", ".join(handles),
        }
    })


def test_identifies_the_named_bad_handle():
    resp = _reject("ghost_handle")
    bad = ig._bad_collaborator_handles(resp, ["good_one", "ghost_handle"])
    assert bad == ["ghost_handle"]


def test_falls_back_to_all_when_message_is_opaque():
    resp = FakeResponse(400, {"error": {"error_subcode": 2207018, "message": "Invalid user id"}})
    bad = ig._bad_collaborator_handles(resp, ["a", "b"])
    assert bad == ["a", "b"]  # can't tell which — drop them all rather than lose the post


def test_unrelated_error_is_not_treated_as_a_collaborator_problem():
    resp = FakeResponse(400, {"error": {"message": "Media download has failed", "code": 9004}})
    assert ig._bad_collaborator_handles(resp, ["someone"]) == []


def test_container_retries_without_the_refused_handle(monkeypatch):
    calls: list[list[str]] = []

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, path, data=None):
            sent = json.loads(data.get("collaborators", "[]"))
            calls.append(sent)
            if "ghost" in sent:
                return _reject("ghost")
            return FakeResponse(200, {"id": "container-123"})

    monkeypatch.setattr(ig, "_client", lambda: FakeClient())
    cid, used, rejected = ig._create_container("ig1", {"image_url": "x"}, ["real", "ghost"])

    assert cid == "container-123"
    assert used == ["real"]        # the good handle survived
    assert rejected == ["ghost"]
    assert calls == [["real", "ghost"], ["real"]]   # tried both, then retried without


def test_container_succeeds_even_when_every_handle_is_bad(monkeypatch):
    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, path, data=None):
            sent = json.loads(data.get("collaborators", "[]"))
            if sent:
                return _reject(*sent)
            return FakeResponse(200, {"id": "solo-container"})

    monkeypatch.setattr(ig, "_client", lambda: FakeClient())
    cid, used, rejected = ig._create_container("ig1", {"image_url": "x"}, ["bad1", "bad2"])

    # The photo ships regardless — a lost credit is recoverable, a lost post isn't.
    assert cid == "solo-container"
    assert used == []
    assert set(rejected) == {"bad1", "bad2"}


def test_no_collaborators_means_no_parameter(monkeypatch):
    seen: list[dict] = []

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, path, data=None):
            seen.append(data)
            return FakeResponse(200, {"id": "plain"})

    monkeypatch.setattr(ig, "_client", lambda: FakeClient())
    cid, used, rejected = ig._create_container("ig1", {"image_url": "x"}, [])
    assert cid == "plain" and used == [] and rejected == []
    assert "collaborators" not in seen[0]


def test_max_three_enforced_by_constant():
    assert ig.MAX_COLLABORATORS == 3
