"""Image validation + thumbnail + derivative generation.

Brief: open with Image.open(), call thumbnail() with a max dim, never call .load() on the
full image. MAX_IMAGE_PIXELS raised to fit 60–70 MP exports while keeping a decompression-bomb
ceiling instead of disabling the protection entirely.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 200_000_000

THUMBNAIL_LONG_EDGE = 320
THUMBNAIL_QUALITY = 85
DERIVATIVE_QUALITY = 92
PREVIEW_LONG_EDGE = 1600
PREVIEW_QUALITY = 88


class InvalidImage(Exception):
    pass


def validate(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as e:
        raise InvalidImage(f"not a valid image: {e}") from e
    with Image.open(path) as img:
        width, height = img.size
        fmt = img.format or "UNKNOWN"
    return width, height, fmt


def _preserved_save_kwargs(img: Image.Image) -> dict:
    """Pull EXIF / ICC / XMP / DPI from a source image so we can pass them through to save().

    Pillow re-encoding does NOT preserve any of these by default. For the Flickr derivative
    in particular, dropping EXIF kills camera/lens/ISO/exposure metadata that Flickr surfaces
    on the photo page — surprising and undesirable for a photographer's workflow. We always
    re-stamp orientation to 1 (image bytes are physically rotated by exif_transpose), so the
    EXIF Orientation field stays consistent with the pixel data.
    """
    out: dict = {}
    exif = img.info.get("exif")
    if exif:
        out["exif"] = exif
    icc = img.info.get("icc_profile")
    if icc:
        out["icc_profile"] = icc
    xmp = img.info.get("xmp")
    if xmp:
        out["xmp"] = xmp
    if "dpi" in img.info:
        out["dpi"] = img.info["dpi"]
    return out


def make_thumbnail(src: Path, dst: Path) -> None:
    """Generate a 320px-long-edge JPEG thumbnail. Honours EXIF orientation."""
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((THUMBNAIL_LONG_EDGE, THUMBNAIL_LONG_EDGE), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Thumbnails are display-only — strip metadata so we ship as few bytes as possible.
        img.save(dst, "JPEG", quality=THUMBNAIL_QUALITY, optimize=True)


def make_preview(src: Path, dst: Path) -> None:
    """1600-px JPEG for the in-app lightbox. EXIF orientation baked in. Strips all metadata
    (EXIF, XMP, ICC, IPTC) — the lightbox doesn't surface any of that, and Pillow's auto-
    preservation of bloated Lightroom XMP packets (~20-35KB) was producing JPEGs some
    browser decoders refused to render. Clean stripped previews load faster and reliably.

    Pillow auto-preserves img.info entries (xmp, icc_profile, exif) into save() output even
    when those kwargs aren't explicitly passed. The only reliable strip is to clear info BEFORE
    save and pass nothing.
    """
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if max(img.size) > PREVIEW_LONG_EDGE:
            img.thumbnail((PREVIEW_LONG_EDGE, PREVIEW_LONG_EDGE), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Clear all metadata Pillow would otherwise embed.
        for key in ("exif", "xmp", "icc_profile", "photoshop", "iptc", "dpi"):
            img.info.pop(key, None)
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "JPEG", quality=PREVIEW_QUALITY, optimize=True, progressive=True)


def make_derivative(src: Path, dst: Path, max_long_edge: int) -> None:
    """Generate a Flickr-sized JPEG. Preserves EXIF / ICC / XMP / DPI from the source —
    Flickr surfaces all of these on the photo page (camera, lens, ISO, color profile,
    Lightroom-stamped XMP keywords). EXIF Orientation is baked in via exif_transpose.

    If the source's long edge is already <= max_long_edge, we still re-encode to JPEG —
    keeps content-type predictable for the upload regardless of source format (PNG, etc.).
    """
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if max(img.size) > max_long_edge:
            img.thumbnail((max_long_edge, max_long_edge), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(
            dst, "JPEG",
            quality=DERIVATIVE_QUALITY, optimize=True, progressive=True,
            **_preserved_save_kwargs(img),
        )
