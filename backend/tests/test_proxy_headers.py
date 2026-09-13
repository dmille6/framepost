"""OAuth callback URLs must carry the scheme the browser actually used.

Every OAuth integration builds its callback from request.base_url. Behind a TLS
terminator -- a Cloudflare tunnel, say -- that has to say https, because the provider
compares it against a registered redirect URI and rejects a mismatch. The failure is
quiet and misleading: the error names a redirect_uri problem, not a proxy one.

Two pieces have to hold, and neither is exercised by ordinary use, since browsing
over plain http on the LAN looks identical whether they are right or wrong:

  - nginx must pass an upstream X-Forwarded-Proto through rather than overwriting it
    with its own $scheme (guarded by the map in nginx/nginx.conf, which lives outside
    this image and so cannot be asserted here -- `nginx -t` at build time is its check)
  - uvicorn must be told to honour that header, or it reports the scheme of nginx's
    own hop and the nginx fix achieves nothing
"""
import re
from pathlib import Path

import pytest
from starlette.requests import Request

from routes.platforms import _absolute_url

DOCKERFILE = Path("/app/Dockerfile")


def _request(scheme: str, host: str = "framepost.example.com") -> Request:
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": scheme, "path": "/", "raw_path": b"/",
        "query_string": b"", "root_path": "", "client": ("1.2.3.4", 1),
        "server": ("backend", 8000), "headers": [(b"host", host.encode())],
    })


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_callback_url_uses_the_requests_scheme(scheme):
    url = _absolute_url(_request(scheme), "/api/platforms/pinterest/callback")
    assert url == f"{scheme}://framepost.example.com/api/platforms/pinterest/callback"


def test_callback_url_uses_the_forwarded_host():
    """The tunnel hostname, not the container's own name."""
    url = _absolute_url(_request("https", host="abc123.trycloudflare.com"), "/api/x")
    assert url == "https://abc123.trycloudflare.com/api/x"


@pytest.mark.skipif(not DOCKERFILE.exists(), reason="Dockerfile not in this image")
def test_uvicorn_is_told_to_honour_forwarded_headers():
    """Without --proxy-headers uvicorn ignores X-Forwarded-Proto entirely, and every
    callback URL comes out http:// no matter what nginx forwarded."""
    cmd = DOCKERFILE.read_text()
    assert "--proxy-headers" in cmd, "uvicorn must run with --proxy-headers"


@pytest.mark.skipif(not DOCKERFILE.exists(), reason="Dockerfile not in this image")
def test_forwarded_ips_are_scoped_to_the_compose_network():
    """Trusting * is only sound while port 8000 is unpublished. If it is ever exposed
    to the host, this must be narrowed or the header becomes forgeable."""
    text = DOCKERFILE.read_text()
    assert "--forwarded-allow-ips" in text
    compose = Path("/app/../docker-compose.yml")
    if compose.exists():
        body = compose.read_text()
        assert not re.search(r"^\s*-\s*\"?\d+:8000\"?\s*$", body, re.M), \
            "backend port 8000 is published — forwarded-allow-ips=* is no longer safe"
