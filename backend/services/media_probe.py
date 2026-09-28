"""Is this URL actually serving the media — checked before Meta is asked to fetch it?

Meta ingests from a public URL only, and when it can't fetch one it says so in a 400
("the media could not be fetched from this URI", 2207052) that looks exactly like a
content rejection. On 2026-09-11 that turned out to be Flickr refusing to serve Meta,
proven by R2 serving the identical bytes in the same minute. Every such failure spent a
container-creation call, and the retry queue's backoff, finding out something a
one-byte request could have told us first — and could have routed around.

The probe is deliberately humble. It exists to catch "definitely not servable" and
must never be the reason a publish stops when the URL is fine. So it only believes
answers that cannot be about *us*:

  unreachable  404/410 (the object is gone), any 5xx, a transport error, or a 2xx
               whose content-type is an error page (text/*, JSON, XML) or the other
               media kind (a video where a photo belongs).
  advisory     401, 403, 405, 429 and any other status. A CDN's bot rules, rate
               limits or method rules may apply to this box and not to Meta — the
               probe is not Meta — so these are logged and the URL goes to Meta as is.
               (An expired R2 presign also lands here: a 403. Meta then says so in its
               own words and the next attempt re-presigns, exactly as before.)
  fetchable    200/206 with the expected media type, or with no/unknown content-type
               (Meta may well accept it and we can't prove otherwise).

How it asks:

  * GET with Range: bytes=0-0, not HEAD. A presigned R2 URL is signed for GET; a HEAD
    against it is a signature mismatch on a perfectly good URL. Only if the GET is
    refused as a method (405/501) do we try HEAD.
  * The body is never read beyond headers, so a host that ignores Range costs nothing.
  * The ordinary FramePost User-Agent (services/http_client), not an imitation of
    Meta's fetcher: pretending to be facebookexternalhit invites exactly the bot
    rules that the advisory class above exists to ignore.
  * A transport error (timeout, refused, DNS) counts as unreachable. Those hosts are
    global CDNs; if we cannot reach one, our own outbound path is the likeliest
    culprit, and the Meta API call would not have fared better.
  * Any other exception inside the probe is a bug in the probe, not a fact about the
    URL: it is logged and the URL is treated as fine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit

import httpx

from services import http_client

log = logging.getLogger("framepost.media_probe")

PROBE_TIMEOUT = 10.0
IMAGE = ("image/",)
VIDEO = ("video/",)
_BAD_TYPES = ("text/", "application/json", "application/xml")


class MediaUnreachable(Exception):
    """No candidate URL served the media."""


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str


def _client() -> httpx.Client:
    return http_client.client(timeout=PROBE_TIMEOUT, follow_redirects=True)


def _judge(status: int, content_type: str, kinds: Sequence[str]) -> ProbeResult:
    if status in (404, 410) or status >= 500:
        return ProbeResult(False, f"HTTP {status}")
    if status not in (200, 206):
        # 401/403/405/429 and friends: possibly about this box, not about Meta.
        log.warning("probe: HTTP %s — advisory only, handing the URL to Meta as is", status)
        return ProbeResult(True, f"HTTP {status} (advisory)")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if any(ctype.startswith(k) for k in kinds):
        return ProbeResult(True, f"HTTP {status} {ctype}")
    if ctype.startswith(_BAD_TYPES + IMAGE + VIDEO):
        # An error page, or media of the wrong kind (a video where a photo belongs).
        return ProbeResult(False, f"HTTP {status} but served {ctype}")
    log.info("probe: HTTP %s with content-type %r — letting Meta judge", status, ctype)
    return ProbeResult(True, f"HTTP {status} {ctype or 'no content-type'}")


def probe(url: str, *, kinds: Sequence[str] = IMAGE) -> ProbeResult:
    """One cheap request. See the module docstring for what counts as fetchable."""
    try:
        with _client() as c:
            with c.stream("GET", url, headers={"Range": "bytes=0-0"}) as r:
                status, ctype = r.status_code, r.headers.get("content-type", "")
            if status in (405, 501):
                r = c.head(url)
                status, ctype = r.status_code, r.headers.get("content-type", "")
        return _judge(status, ctype, kinds)
    except httpx.HTTPError as e:
        return ProbeResult(False, f"unreachable ({type(e).__name__}: {e})")
    except Exception as e:  # noqa: BLE001 — a probe bug must never fail a publish
        log.exception("probe of %s raised — treating the URL as fetchable", _host(url))
        return ProbeResult(True, f"probe error ignored ({type(e).__name__})")


def _host(url: str) -> str:
    """Just the host, for logs and error text: presigned query strings carry
    credentials-adjacent material and don't belong in post_platforms.error_message."""
    try:
        return urlsplit(url).netloc or "?"
    except ValueError:
        return "?"


def first_fetchable(
    url: str,
    fallbacks: Sequence[tuple[str, Callable[[], str]]] = (),
    *,
    kinds: Sequence[str] = IMAGE,
) -> str:
    """url if it serves, else the first fallback that does. Raises MediaUnreachable.

    Fallbacks are (label, make_url) and are built lazily: re-staging costs an upload,
    and is only worth paying when the first URL has actually failed.
    """
    result = probe(url, kinds=kinds)
    if result.ok:
        return url
    tried = [f"{_host(url)}: {result.reason}"]
    for label, make in fallbacks:
        try:
            alt = make()
        except Exception as e:  # noqa: BLE001 — a failed fallback is just another miss
            tried.append(f"{label}: couldn't prepare ({type(e).__name__}: {e})")
            continue
        alt_result = probe(alt, kinds=kinds)
        if alt_result.ok:
            log.warning("media URL on %s not fetchable (%s); using %s instead",
                        _host(url), result.reason, label)
            return alt
        tried.append(f"{label} ({_host(alt)}): {alt_result.reason}")
    raise MediaUnreachable("; ".join(tried))
