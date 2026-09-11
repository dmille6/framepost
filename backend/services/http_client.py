"""One place that builds outbound HTTP clients, so every request says who it is.

Flickr began returning 502 — an HTML gateway page, not an API error — for httpx's
default `python-httpx/x.y` User-Agent on api.flickr.com. Uploads kept working because
those go to up.flickr.com, so it presented as "Instagram is broken" rather than
"Flickr is refusing us", and every retry burned budget against a wall.

Any host can decide the same thing tomorrow. An unattributed default User-Agent is a
standing liability, so nothing here sends one.
"""
from __future__ import annotations

import httpx

USER_AGENT = "FramePost/0.1 (+https://github.com/dmille6/framepost)"


def client(**kwargs) -> httpx.Client:
    """httpx.Client with a real User-Agent. Caller headers win over the default."""
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", None) or {})
    return httpx.Client(headers=headers, **kwargs)
