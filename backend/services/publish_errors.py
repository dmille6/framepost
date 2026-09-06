"""Classify platform failures into what the operator should actually DO about them.

A boolean `permanent` flag answers "retry or not" and nothing else. It cannot say
*this channel is dead until you re-authorise*, which is the one failure that never
resolves on its own — and which retried silently, nightly, for two weeks when Flickr's
OAuth scope changed and the staging sweep lost delete permission.

Five categories, each mapping to a different response:

    RETRY          transient (5xx, rate limit, network). Back off and try again.
    BAD_CONTENT    the post itself is wrong. Retrying re-submits the same bad thing.
    REFRESH_TOKEN  the token is stale but the grant is intact. Refresh, then retry.
    REAUTH         the grant is gone or insufficient. Only the user can fix it, by
                   reconnecting the account. Retrying is pure waste.
    UNKNOWN        unrecognised. Treated as retryable so a novel transient failure
                   isn't misfiled as fatal, but surfaced so the mapping can be taught.

REAUTH is the category that earns this module. It's the one that stops the retry loop
and puts a reconnect prompt in front of the user instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FailureCategory(str, Enum):
    RETRY = "retry"
    BAD_CONTENT = "bad_content"
    REFRESH_TOKEN = "refresh_token"
    REAUTH = "reauth"
    UNKNOWN = "unknown"


# Categories where trying the identical request again cannot possibly help.
_TERMINAL = {FailureCategory.BAD_CONTENT, FailureCategory.REAUTH}


@dataclass(frozen=True)
class PublishFailure:
    """The classified form of a platform exception."""
    platform: str
    category: FailureCategory
    user_message: str          # written for the photographer, not the log
    detail: str                # the raw platform text, kept for the activity log
    code: str | None = None

    @property
    def retryable(self) -> bool:
        return self.category not in _TERMINAL

    @property
    def requires_reauth(self) -> bool:
        return self.category is FailureCategory.REAUTH


# --- per-platform rules -------------------------------------------------------------
#
# Matched against the lowercased exception text. Ordered: first hit wins, so put the
# specific patterns above the general ones.

_RULES: dict[str, list[tuple[str, FailureCategory, str]]] = {
    "flickr": [
        # Flickr error 98/99 are the login/permission family. 99 is what the staging
        # sweep hits when the stored token predates the delete scope.
        (r"insufficient permissions|requires delete privileges|error 99",
         FailureCategory.REAUTH,
         "Flickr needs reconnecting — the saved permission doesn't allow this action."),
        (r"login failed|invalid auth token|oauth_problem|token_rejected|error 98",
         FailureCategory.REAUTH,
         "Flickr needs reconnecting — the saved login is no longer valid."),
        (r"filesize|file size|too large|invalid file|photo not found",
         FailureCategory.BAD_CONTENT,
         "Flickr rejected this photo."),
        (r"rate limit|too many requests|\b429\b",
         FailureCategory.RETRY,
         "Flickr is rate-limiting; it will retry shortly."),
    ],
    "instagram": [
        # 190 = invalid/expired token; 10 & 200 = permission not granted.
        (r"error validating access token|session has been invalidated|code.?:?\s*190"
         r"|oauthexception|access token.*expired",
         FailureCategory.REAUTH,
         "Instagram needs reconnecting — the access token is no longer valid."),
        (r"missing ig_user_id|reconnect instagram|permission|not authorized"
         r"|code.?:?\s*(10|200)\b",
         FailureCategory.REAUTH,
         "Instagram needs reconnecting — a required permission is missing."),
        (r"aspect ratio|caption.*too long|media download|couldn't fetch|unsupported format"
         r"|invalid image|2207",
         FailureCategory.BAD_CONTENT,
         "Instagram rejected this post's image or caption."),
        (r"rate limit|too many|application request limit|\b(4|17|32|613)\b.*limit",
         FailureCategory.RETRY,
         "Instagram is rate-limiting; it will retry shortly."),
    ],
    "bluesky": [
        (r"invalid.*(identifier|password)|authentication required|expiredtoken"
         r"|invalidtoken|account is deactivated",
         FailureCategory.REAUTH,
         "Bluesky needs reconnecting — the app password was rejected."),
        (r"blob too large|unsupported|invalid record|text too long",
         FailureCategory.BAD_CONTENT,
         "Bluesky rejected this post."),
        (r"rate ?limit|upstream|\b429\b|\b50[0234]\b",
         FailureCategory.RETRY,
         "Bluesky is unavailable or rate-limiting; it will retry shortly."),
    ],
    "pixelfed": [
        (r"unauthorized|invalid.?token|revoked|\b401\b",
         FailureCategory.REAUTH,
         "Pixelfed needs reconnecting — the saved token was rejected."),
        (r"unprocessable|validation|too large|unsupported|\b422\b",
         FailureCategory.BAD_CONTENT,
         "Pixelfed rejected this post."),
        (r"rate ?limit|\b429\b|\b50[0234]\b",
         FailureCategory.RETRY,
         "Pixelfed is unavailable or rate-limiting; it will retry shortly."),
    ],
    "pinterest": [
        (r"unauthorized|invalid.?(access.?)?token|expired|\b401\b",
         FailureCategory.REAUTH,
         "Pinterest needs reconnecting — the saved token was rejected."),
        (r"trial|not approved|scope|\b403\b",
         FailureCategory.REAUTH,
         "Pinterest needs reconnecting — the app lacks the required access."),
        (r"invalid.*(board|image|url)|too large|validation",
         FailureCategory.BAD_CONTENT,
         "Pinterest rejected this pin."),
        (r"rate ?limit|\b429\b|\b50[0234]\b",
         FailureCategory.RETRY,
         "Pinterest is rate-limiting; it will retry shortly."),
    ],
}

# Applied to every platform after its own rules miss. Generic transport symptoms.
_FALLBACK: list[tuple[str, FailureCategory, str]] = [
    (r"\b(timeout|timed out|connection|temporarily|unavailable|\b50[0234]\b)\b",
     FailureCategory.RETRY,
     "The platform was unreachable; it will retry shortly."),
]


def classify(platform: str, err: BaseException) -> PublishFailure:
    """Map a platform exception onto a category and a message worth showing a human."""
    text = str(err) or err.__class__.__name__
    low = text.lower()
    code = getattr(err, "code", None)

    for pattern, category, message in _RULES.get(platform, []) + _FALLBACK:
        if re.search(pattern, low):
            return PublishFailure(
                platform=platform, category=category, user_message=message,
                detail=text[:1000], code=str(code) if code is not None else None,
            )

    # Fall back to the adapter's own judgement where it has one. A `permanent` flag
    # still tells us not to retry, we just can't say anything more specific than that.
    if getattr(err, "permanent", False):
        return PublishFailure(
            platform=platform, category=FailureCategory.BAD_CONTENT,
            user_message=f"{platform.title()} rejected this post.",
            detail=text[:1000], code=str(code) if code is not None else None,
        )

    return PublishFailure(
        platform=platform, category=FailureCategory.UNKNOWN,
        user_message=f"{platform.title()} failed for an unrecognised reason; it will retry.",
        detail=text[:1000], code=str(code) if code is not None else None,
    )
