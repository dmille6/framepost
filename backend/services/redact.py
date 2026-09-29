"""Mask platform secrets in any text headed for a log, the database or the UI.

Tokens reach text by three routes, and none of them is a bug anyone will notice:
  * a URL — httpx logs every request line at INFO, and its exception messages quote the
    URL ("... for url 'https://graph.instagram.com/...?access_token=...'");
  * an echoed request — a platform error body that repeats what it was sent;
  * an exception chained into a stored error string (post_platforms.error_message,
    reels.publish_error, platform_credentials.last_error/auth_error, post_events.details).

Everything is text by the time it leaks, so one function masks it: redact(). It works
two ways, because neither alone is enough:
  1. By shape — `access_token=…`, `"refresh_token": "…"`, `Authorization: Bearer …` and
     friends, including the URL-encoded form a URL takes when it is itself a parameter.
     Catches a token we have never seen (a fresh one from an OAuth exchange).
  2. By value — every credential this process has decrypted or encrypted, plus the
     secrets in settings. Catches a token with no label next to it (a JWT in an error
     body, a header dumped by a debugging line). Cheap: crypto.py already touches every
     one of them, so it hands each plaintext to remember() on the way past.

install_logging() puts redact() under every log record the process creates, so it is
the only logging change either process needs.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

MASK = "[REDACTED]"

# Names whose value is a secret wherever they appear as key=value, key: value or a
# JSON/dict key. oauth_verifier is single-use but rides in the Flickr callback URL
# next to oauth_token; X-Amz-Signature is what makes an R2 presigned URL work.
_SECRET_KEYS = (
    "access_token", "refresh_token", "client_secret", "oauth_token_secret", "oauth_token",
    "oauth_signature", "oauth_verifier", "app_password", "password", "fb_exchange_token",
    "input_token", "id_token", "x-amz-signature",
)
_KEYS = "|".join(re.escape(k) for k in _SECRET_KEYS)

# key=value in a query string / form body / OAuth1 header (value optionally quoted),
# plus the URL-encoded form (key%3Dvalue, ending at the encoded & — %26).
# The name must start a word — or follow an encoded ? or & (%3F, %26), whose last
# character is alphanumeric and so would otherwise read as the middle of a word.
_KV = re.compile(
    rf"(?i)(?:(?<![A-Za-z0-9])|(?<=%3F)|(?<=%26))({_KEYS})(=|%3D)(\"?)"
    rf"((?:(?!%26)[^&\s\"'<>,;\\])+)")
# An OAuth authorization code: `code=` only as a query/form parameter (right after ?, &
# or their encoded forms) and only when the value has a letter in it. That is the shape
# Pinterest's and Pixelfed's callbacks arrive in, and it leaves alone everything else
# that says "code": Meta's `"code": 190`, "error code=98" (publish_errors classifies on
# `code.?:?\s*190`), status_code=, zipcode=, a caption about a promo code.
_QUERY_CODE = re.compile(
    r"(?i)(?:(?<=[?&])|(?<=%3F)|(?<=%26))(code)(=|%3D)"
    r"(?=(?:(?!%26)[^&\s\"'<>,;\\])*[A-Za-z])((?:(?!%26)[^&\s\"'<>,;\\])+)")
# "key": "value" / 'key': 'value' — JSON bodies and Python dict reprs, and JSON quoted
# inside JSON (\"key\": \"value\"), which is how a response body lands in post_events.
# Neither pattern consumes a backslash, so masking never breaks a JSON escape.
_JSONISH = re.compile(
    rf"(?i)(\\?[\"']({_KEYS})\\?[\"']\s*:\s*\\?[\"'])([^\"'\\]*)")
# Authorization header values. The 16-char floor keeps prose intact: Meta's own error
# says "Invalid OAuth 2.0 access token", and publish_errors matches on that wording.
# An OAuth1 header ("OAuth oauth_consumer_key=...") is left to _KV, field by field.
_AUTH = re.compile(r"(?i)\b(bearer|oauth)(\s+|%20|\+)(?!oauth_)([A-Za-z0-9._~+/\-]{16,}=*)")

# Values shorter than this are never remembered: a short string (a test's "tok", an
# empty setting) would mask ordinary words everywhere it happened to occur.
_MIN_SECRET_LEN = 12
_MAX_REMEMBERED = 256
# secret -> its URL-encoded form. A dict for insertion order, so the oldest can go first.
_known: dict[str, str] = {}


def remember(secret: str | None) -> None:
    """Mask this exact value from now on, wherever it appears. Called by crypto.py for
    every token it encrypts or decrypts, so 'currently known credentials' costs nothing."""
    if not secret or len(secret) < _MIN_SECRET_LEN or secret in _known:
        return
    _known[secret] = quote(secret, safe="")
    while len(_known) > _MAX_REMEMBERED:
        _known.pop(next(iter(_known)))


def remember_settings() -> None:
    """The secrets that live in .env rather than the database."""
    from config import settings

    for name in ("secret_key", "token_encryption_key", "flickr_api_secret",
                 "anthropic_api_key", "openai_api_key", "pinterest_app_secret"):
        for part in str(getattr(settings, name, "") or "").split(","):
            remember(part.strip())


def redact(text: str | None) -> str | None:
    """`text` with every secret masked. None and "" pass through untouched."""
    if not text:
        return text
    out = text
    # Longest first, so a secret that contains another is masked whole.
    for secret, encoded in sorted(_known.items(), key=lambda kv: len(kv[0]), reverse=True):
        if secret in out:
            out = out.replace(secret, MASK)
        if encoded != secret and encoded in out:
            out = out.replace(encoded, MASK)
    out = _KV.sub(lambda m: f"{m[1]}{m[2]}{m[3]}{MASK}", out)
    out = _QUERY_CODE.sub(lambda m: f"{m[1]}{m[2]}{MASK}", out)
    out = _JSONISH.sub(lambda m: f"{m[1]}{MASK}", out)
    out = _AUTH.sub(lambda m: f"{m[1]}{m[2]}{MASK}", out)
    return out


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

_PLAIN = (int, float, bool, type(None))
# %-placeholders in a log format string: %s, %d, %-8.3f, %(name)s, %%.
_PLACEHOLDER = re.compile(r"(%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[a-zA-Z%])")


def _redact_format(msg: str) -> str:
    """redact() the literal text of a format string, never its placeholders — masking
    `access_token=%s` whole would leave an argument with nowhere to go."""
    parts = _PLACEHOLDER.split(msg)
    return "".join(p if i % 2 else redact(p) for i, p in enumerate(parts))


def _scrub_arg(arg):
    if isinstance(arg, str):
        return redact(arg)
    if isinstance(arg, _PLAIN):
        return arg
    # An exception or URL object passed for %s. Only swap it for text when it actually
    # held a secret, so %r and %d keep working on everything else.
    try:
        text = str(arg)
    except Exception:  # noqa: BLE001 — a broken __str__ must not break logging
        return arg
    clean = redact(text)
    return arg if clean == text else clean


def scrub_record(record: logging.LogRecord) -> logging.LogRecord:
    """Mask secrets in a record's message, arguments, traceback and stack, in place.

    Arguments are scrubbed one by one rather than folded into the message: uvicorn's
    access formatter unpacks record.args by position (client, method, path, version,
    status), and the Flickr OAuth callback path carries oauth_token and oauth_verifier.
    """
    if isinstance(record.msg, str):
        record.msg = _redact_format(record.msg) if record.args else redact(record.msg)
    elif record.msg is not None and not isinstance(record.msg, _PLAIN):
        record.msg = _scrub_arg(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(_scrub_arg(a) for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: _scrub_arg(v) for k, v in record.args.items()}
    if record.args:
        # A secret split across the two — `"access_token=%s", token` with a token this
        # process never decrypted — is only visible once rendered. Fold the record to its
        # masked text then, and only then: formatters that read args stay untouched.
        try:
            rendered = record.getMessage()
        except Exception:  # noqa: BLE001 — a malformed record is logging's to report
            rendered = None
        if rendered is not None:
            clean = redact(rendered)
            if clean != rendered:
                record.msg, record.args = clean, None
    if record.exc_info and not record.exc_text:
        # Formatters reuse exc_text when it is set, so rendering the traceback here is
        # the one place its text (httpx quotes the full URL) can be masked.
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    return record


class RedactingFilter(logging.Filter):
    """The same masking as a Filter, for a handler built outside install_logging()'s
    reach. Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        scrub_record(record)
        return True


_installed = False


def install_logging() -> None:
    """Mask secrets in every log record this process creates. Idempotent.

    A record factory rather than a Filter on each handler: handlers come and go
    (uvicorn builds its own at startup, alembic's fileConfig replaces them, the API
    process logs its warnings through logging.lastResort, which no one configures), and
    a filter only covers the handlers that existed when it was added. Every record, from
    every logger, to every handler present or future, is made by the factory.

    httpx's INFO request lines are kept, redacted, rather than quieted to WARNING: the
    request line is the only trace of what the worker asked a platform, and the
    Instagram debugging so far (fetch failures, the not-ready race) was read off them.
    """
    global _installed
    if _installed:
        return
    remember_settings()
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        return scrub_record(base(*args, **kwargs))

    logging.setLogRecordFactory(factory)
    _installed = True
