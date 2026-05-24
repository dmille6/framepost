"""HTTP middleware — CSRF (double-submit cookie pattern).

- Every response sets a CSRF cookie if missing. JS reads it.
- State-changing requests (POST/PUT/PATCH/DELETE) under /api/ must echo it back in
  the X-CSRF-Token header. Header value must match the cookie value.
- /health and the React static assets are not gated.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from services.auth import CSRF_COOKIE, CSRF_HEADER, new_csrf_token

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# OAuth callbacks land here from external IdPs (Flickr) via top-level browser nav.
# They carry no CSRF header by definition. We rely on the signed state cookie + the
# verifier from the IdP for those paths.
CSRF_EXEMPT_PREFIXES = (
    "/api/platforms/flickr/callback",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cookie_token = request.cookies.get(CSRF_COOKIE)
        path = request.url.path

        if (
            request.method in UNSAFE_METHODS
            and path.startswith("/api/")
            and not any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES)
        ):
            header_token = request.headers.get(CSRF_HEADER)
            if not cookie_token or not header_token or header_token != cookie_token:
                return JSONResponse(
                    {"detail": "CSRF token missing or invalid"},
                    status_code=403,
                )

        response: Response = await call_next(request)

        if not cookie_token:
            response.set_cookie(
                CSRF_COOKIE,
                new_csrf_token(),
                httponly=False,           # JS must read it for the header echo
                samesite="lax",
                secure=False,             # LAN-only HTTP per brief
                path="/",
            )
        return response
