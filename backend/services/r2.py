"""Cloudflare R2 staging for Instagram ingest.

Instagram's publishing API will not accept uploaded bytes — `upload_type=resumable` is
video only, and an image request without `image_url` is rejected outright. Meta has to
fetch the photo from a public URL, which made Flickr the de-facto host for every IG post.

On 2026-09-11 Meta stopped being able to fetch from live.staticflickr.com at all
(9004/2207052, "the media could not be fetched from this URI") while fetching the
identical bytes from an R2 presigned URL in the same minute without complaint. Staging
here takes Flickr out of the Instagram path entirely, so a bad day at one host can't
stop publishing at the other.

Presigned GETs rather than a public bucket: the key is unguessable, the link expires on
its own, and there is nothing to lock down by IP or user agent — which matters because
Meta does not document which addresses its media fetcher uses.

SigV4 is signed here by hand. boto3 would pull tens of megabytes into the image to do
four operations on small objects with simple keys, and would mean rebuilding the
container rather than shipping a file.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import urllib.parse
import xml.etree.ElementTree as ET

from services import http_client

log = logging.getLogger("framepost.r2")

REGION = "auto"
SERVICE = "s3"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
DEFAULT_EXPIRY = 7200  # Meta fetches during container creation; 2h is ample slack.
_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


class R2Error(Exception):
    pass


def _cfg() -> tuple[str, str, str, str]:
    account = os.getenv("R2_ACCOUNT_ID") or ""
    access = os.getenv("R2_ACCESS_KEY_ID") or ""
    secret = os.getenv("R2_SECRET_ACCESS_KEY") or ""
    bucket = os.getenv("R2_BUCKET") or ""
    return account, access, secret, bucket


def configured() -> bool:
    """All four settings present. Callers fall back to Flickr staging when false, so a
    half-configured install degrades to the old behaviour instead of failing to post."""
    return all(_cfg())


def _host() -> str:
    return f"{_cfg()[0]}.r2.cloudflarestorage.com"


def _quote(key: str) -> str:
    # "/" stays literal so nested keys address correctly; everything else is escaped.
    return urllib.parse.quote(key, safe="/~")


def _signing_key(secret: str, datestamp: str) -> bytes:
    k = hmac.new(("AWS4" + secret).encode(), datestamp.encode(), hashlib.sha256).digest()
    for part in (REGION, SERVICE, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def _request(method: str, path: str, *, query: str = "", body: bytes | None = None,
             content_type: str | None = None, timeout: float = 120.0):
    account, access, secret, _bucket = _cfg()
    if not all((account, access, secret)):
        raise R2Error("R2 is not configured")
    host = _host()
    payload_hash = hashlib.sha256(body).hexdigest() if body is not None else EMPTY_SHA
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    canonical_headers = (f"host:{host}\n"
                         f"x-amz-content-sha256:{payload_hash}\n"
                         f"x-amz-date:{amzdate}\n")
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = (f"{method}\n{path}\n{query}\n"
                         f"{canonical_headers}\n{signed_headers}\n{payload_hash}")
    scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
    sts = (f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n"
           f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")
    signature = hmac.new(_signing_key(secret, datestamp), sts.encode(),
                         hashlib.sha256).hexdigest()
    headers = {
        "x-amz-date": amzdate,
        "x-amz-content-sha256": payload_hash,
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
                          f"SignedHeaders={signed_headers}, Signature={signature}"),
    }
    if content_type:
        headers["Content-Type"] = content_type
    url = f"https://{host}{path}" + (f"?{query}" if query else "")
    with http_client.client(timeout=timeout) as c:
        return c.request(method, url, headers=headers, content=body)


def put(key: str, body: bytes, *, content_type: str = "image/jpeg") -> None:
    bucket = _cfg()[3]
    r = _request("PUT", f"/{bucket}/{_quote(key)}", body=body, content_type=content_type)
    if r.status_code >= 300:
        raise R2Error(f"upload of {key} failed: HTTP {r.status_code} {r.text[:200]}")


def presign_get(key: str, *, expires: int = DEFAULT_EXPIRY) -> str:
    """A time-limited GET URL. Meta only needs it to resolve once, during container
    creation — the container it builds then lives on Meta's side for 24h."""
    account, access, secret, bucket = _cfg()
    if not all((account, access, secret, bucket)):
        raise R2Error("R2 is not configured")
    host = _host()
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
    path = f"/{bucket}/{_quote(key)}"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access}/{scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_qs = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(params.items())
    )
    canonical_request = (f"GET\n{path}\n{canonical_qs}\n"
                         f"host:{host}\n\nhost\nUNSIGNED-PAYLOAD")
    sts = (f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n"
           f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")
    signature = hmac.new(_signing_key(secret, datestamp), sts.encode(),
                         hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{canonical_qs}&X-Amz-Signature={signature}"


def delete(key: str) -> None:
    """Best effort. A leftover object is a few hundred KB that the bucket's lifecycle
    rule reaps anyway; a raised exception here would fail an otherwise good publish."""
    bucket = _cfg()[3]
    try:
        r = _request("DELETE", f"/{bucket}/{_quote(key)}", timeout=30.0)
        if r.status_code >= 300 and r.status_code != 404:
            log.warning("r2: delete of %s returned HTTP %s", key, r.status_code)
    except Exception as e:  # noqa: BLE001
        log.warning("r2: delete of %s failed: %s", key, e)


def list_keys(prefix: str = "") -> list[str]:
    bucket = _cfg()[3]
    query = "list-type=2"
    if prefix:
        query += f"&prefix={urllib.parse.quote(prefix, safe='')}"
    r = _request("GET", f"/{bucket}", query=query, timeout=60.0)
    if r.status_code >= 300:
        raise R2Error(f"list failed: HTTP {r.status_code} {r.text[:200]}")
    root = ET.fromstring(r.text)
    return [e.text or "" for e in root.findall(".//s3:Contents/s3:Key", _S3_NS)]
