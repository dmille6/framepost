"""IPTC extraction for Lightroom-written title/description/keywords.

Maps:
  IPTC ObjectName        → posts.title
  IPTC Caption-Abstract  → posts.description
  IPTC Keywords          → posts.tags (comma-separated)

IPTCInfo3 sometimes prints noisy warnings to stderr on files without IPTC; suppress them.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
from typing import Any

logging.getLogger("iptcinfo").setLevel(logging.ERROR)


def _decode(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace").strip()
        except Exception:
            return None
    return str(value).strip() or None


def extract(path: str) -> dict[str, Any]:
    out = {"title": None, "description": None, "tags": None, "iptc_raw": None}
    try:
        from iptcinfo3 import IPTCInfo
    except Exception:
        return out

    try:
        # IPTCInfo writes to stderr when no IPTC block exists; capture and discard.
        with contextlib.redirect_stderr(io.StringIO()):
            info = IPTCInfo(path, force=True)
    except Exception:
        return out

    out["title"] = _decode(info["object name"])
    out["description"] = _decode(info["caption/abstract"])
    keywords = info["keywords"] or []
    if keywords:
        kept = [k for k in (_decode(k) for k in keywords) if k]
        if kept:
            out["tags"] = ", ".join(kept)

    raw: dict[str, Any] = {}
    for key, value in info._data.items():
        decoded = _decode(value) if not isinstance(value, list) else [_decode(v) for v in value]
        if decoded:
            raw[str(key)] = decoded
    if raw:
        try:
            out["iptc_raw"] = json.dumps(raw, default=str)
        except (TypeError, ValueError):
            out["iptc_raw"] = None
    return out
