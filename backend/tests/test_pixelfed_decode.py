"""Reporting a Pixelfed response that isn't JSON.

Four posts failed in May and June 2026 with `Expecting value: line 1 column 1
(char 0)` -- a JSON parser complaining about byte zero. It named no platform, no
status code and no body, so nothing could be done with it, and each post retried
six times and gave up still saying only that.

The condition itself is specific and diagnosable: a 2xx response carrying a body
that isn't JSON, which pixelfed.social's proxy produces when it serves an HTML
maintenance or rate-limit page. Checked afterwards, all 105 live statuses matched
a recorded remote_id, so nothing was published twice — the posts were simply lost,
quietly.
"""
import pytest

from services.platforms import pixelfed


class _R:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json
        return json.loads(self.text)


def test_valid_json_passes_through():
    assert pixelfed._decode(_R('{"id": "42"}'), "status post") == {"id": "42"}


def test_an_html_body_is_named_as_such():
    with pytest.raises(pixelfed.PixelfedError) as e:
        pixelfed._decode(_R("<html><body>502 Bad Gateway</body></html>"), "status post")
    msg = str(e.value)
    assert "status post" in msg, "the caller must say which call failed"
    assert "HTTP 200" in msg, "the status code is the first thing worth knowing"
    assert "an HTML page" in msg
    assert "502 Bad Gateway" in msg, "a snippet of the body, or there is nothing to act on"


def test_an_empty_body_reports_its_length():
    with pytest.raises(pixelfed.PixelfedError) as e:
        pixelfed._decode(_R(""), "media upload")
    msg = str(e.value)
    assert "media upload" in msg
    assert "0 bytes" in msg


def test_the_failure_is_retryable():
    """A proxy serving the wrong page is the definition of transient. Marking it
    permanent would abandon a post that a retry a minute later would have placed."""
    with pytest.raises(pixelfed.PixelfedError) as e:
        pixelfed._decode(_R("<html>nope</html>"), "status post")
    assert getattr(e.value, "permanent", False) is False


def test_the_old_useless_message_is_gone():
    with pytest.raises(pixelfed.PixelfedError) as e:
        pixelfed._decode(_R("not json"), "status post")
    assert "Expecting value" not in str(e.value)
