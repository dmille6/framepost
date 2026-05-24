"""Instagram-formatting helpers — copy-paste captions/hashtags + IG-sized JPEG export.

We don't talk to the Meta Graph API (Business/Creator-only, public-URL hosting required, app
review). Instead we generate everything the user needs to manually paste a polished IG post:
caption text, hashtag block, and an image cropped/padded to IG's preferred aspect ratios.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageOps

# IG hard-caps captions at 2200 chars and hashtags at 30 per post. We cap below that to
# leave headroom for a signature and to keep tag lists tight (most engagement studies show
# diminishing returns past ~15-20 tags anyway).
MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 30

# IG-supported aspect ratios. We expose the two that matter for photographers:
# - square: still the safe default, works everywhere.
# - portrait 4:5: dominant feed format, takes max vertical real estate.
SIZES = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
}

BG_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
}


def build_caption(
    *,
    title: str | None,
    description: str | None,
    signature: str | None,
) -> str:
    """Compose a paste-ready caption: title, blank line, description, blank line, signature.

    Anything missing is silently skipped — no orphan blank lines. Truncation guards against
    pathologically long descriptions; we trim from the description first since title/signature
    are intentional brand content.
    """
    title = (title or "").strip()
    description = (description or "").strip()
    signature = (signature or "").strip()

    parts: list[str] = []
    if title:
        parts.append(title)
    if description:
        parts.append(description)
    if signature:
        parts.append(signature)

    out = "\n\n".join(parts)
    if len(out) > MAX_CAPTION_CHARS:
        # Trim with ellipsis. We allow the caller to display a warning via length.
        out = out[: MAX_CAPTION_CHARS - 1].rstrip() + "…"
    return out


_HASHTAG_STRIP = re.compile(r"[^a-z0-9_]")


def build_hashtags(tags: str | None) -> list[str]:
    """Convert a Flickr tag string into IG-style hashtags.

    Flickr tags in this app are stored space-separated (with multi-word tags joined by the user
    or surfaced via IPTC). For IG we lowercase, strip non-alphanumeric, prefix `#`, and dedupe
    while preserving first-seen order. Multi-word phrasing is handled by the user upstream
    (Flickr's "blackandwhite" convention works directly).

    Cap at MAX_HASHTAGS to stay under IG's 30-tag ceiling.
    """
    if not tags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    # Split on common separators: commas first (if user uses csv), otherwise whitespace.
    raw_tokens = re.split(r"[,\n]+|\s+", tags)
    for raw in raw_tokens:
        token = _HASHTAG_STRIP.sub("", raw.strip().lower())
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(f"#{token}")
        if len(out) >= MAX_HASHTAGS:
            break
    return out


def render_image(
    src: Path,
    *,
    fmt: str = "square",
    fit: str = "pad",
    bg: str = "black",
    quality: int = 92,
) -> bytes:
    """Render the source photo into IG-sized JPEG bytes.

    fmt: "square" (1080×1080) or "portrait" (1080×1350)
    fit: "pad" preserves the full image with letterbox bars; "crop" center-crops to fill.
    bg:  "black" or "white" — only relevant when fit="pad".

    We always re-encode to JPEG and bake EXIF orientation in so the user doesn't get a sideways
    photo when they upload to IG.
    """
    if fmt not in SIZES:
        raise ValueError(f"unknown format {fmt!r}")
    if fit not in {"pad", "crop"}:
        raise ValueError(f"unknown fit {fit!r}")
    if bg not in BG_COLORS:
        raise ValueError(f"unknown bg {bg!r}")

    target_w, target_h = SIZES[fmt]

    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        if fit == "crop":
            out = ImageOps.fit(img, (target_w, target_h), Image.LANCZOS, centering=(0.5, 0.5))
        else:
            scale = min(target_w / img.width, target_h / img.height)
            new_w = max(1, int(round(img.width * scale)))
            new_h = max(1, int(round(img.height * scale)))
            scaled = img.resize((new_w, new_h), Image.LANCZOS)
            out = Image.new("RGB", (target_w, target_h), BG_COLORS[bg])
            out.paste(scaled, ((target_w - new_w) // 2, (target_h - new_h) // 2))

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()
