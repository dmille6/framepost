"""Literal paths must out-rank path-parameter routes that could swallow them.

Regression: /api/posts/recent-shows was declared AFTER /api/posts/{post_id}, so FastAPI
matched it as a post id and returned 404 "Post not found". The show/city type-ahead had
been silently dead ever since it shipped.
"""
import pytest

from main import app


def _routes_for(prefix: str) -> list[str]:
    return [r.path for r in app.routes if r.path.startswith(prefix)]


@pytest.mark.parametrize("literal", ["/api/posts/recent-shows", "/api/posts/recent-cities"])
def test_literal_route_declared_before_the_catchall(literal: str):
    paths = _routes_for("/api/posts")
    catchall = "/api/posts/{post_id}"
    assert literal in paths, f"{literal} is not registered at all"
    assert catchall in paths
    assert paths.index(literal) < paths.index(catchall), (
        f"{literal} is declared after {catchall} and will be swallowed by it"
    )


def test_no_single_segment_literal_is_shadowed():
    """Catches the next one automatically rather than one URL at a time."""
    catchall = "/api/posts/{post_id}"
    paths = _routes_for("/api/posts")
    if catchall not in paths:
        pytest.skip("no single-segment catch-all on this router")
    cut = paths.index(catchall)
    shadowed = [
        p for p in paths[cut + 1:]
        if p.count("/") == catchall.count("/") and "{" not in p.rsplit("/", 1)[-1]
    ]
    assert not shadowed, f"declared after {catchall} and unreachable: {shadowed}"
