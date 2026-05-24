"""Reddit-optimized image rendering.

Reddit ignores embedded ICC profiles in feed renders, so an Adobe-RGB or ProPhoto-tagged
JPEG will appear desaturated/off-color compared to how it looks in Lightroom. We always
convert to sRGB color space (using ImageCms when a non-sRGB profile is present) and embed
the sRGB profile in the output, which keeps the photo looking right on Reddit.

Target: 2048 px on the long edge, JPEG quality 92, sRGB. ~2–3 MB typical, well under Reddit's
20 MB upload cap and right-sized for retina displays where most of Reddit's audience views.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

log = logging.getLogger("framepost.reddit")

LONG_EDGE = 2048
QUALITY = 92
_SRGB_PROFILE: ImageCms.ImageCmsProfile | None = None


def _get_srgb_profile() -> ImageCms.ImageCmsProfile:
    global _SRGB_PROFILE
    if _SRGB_PROFILE is None:
        _SRGB_PROFILE = ImageCms.createProfile("sRGB")
    return _SRGB_PROFILE


def _is_srgb_icc(icc_bytes: bytes) -> bool:
    """Best-effort sRGB detection. The ICC profile description is at a known offset, but
    parsing the full structure is overkill — we just check for common sRGB description
    markers in the first 256 bytes. False negatives are fine (we'll just convert needlessly,
    output is identical); false positives would cause color drift, so we err on the side of
    conversion."""
    try:
        head = icc_bytes[:512].lower()
        return b"srgb" in head
    except Exception:
        return False


def render_image(src: Path) -> bytes:
    """Render the source photo as a Reddit-optimized JPEG. Returns the bytes.

    - Long edge clamped to 2048 px
    - Always sRGB color space (converts from Adobe RGB / ProPhoto / etc. if present)
    - JPEG quality 92, optimized + progressive
    - sRGB ICC profile embedded so Reddit's renders are color-correct
    """
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)

        # If we have a non-sRGB ICC profile, do a real color-managed conversion.
        icc_bytes = img.info.get("icc_profile")
        if icc_bytes and not _is_srgb_icc(icc_bytes):
            try:
                src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))
                img = ImageCms.profileToProfile(
                    img,
                    src_profile,
                    _get_srgb_profile(),
                    outputMode="RGB",
                )
            except Exception as e:
                # Fall back to a plain mode convert if color management blows up.
                log.warning("ICC→sRGB conversion failed (%s); falling back to plain RGB", e)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
        else:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

        if max(img.size) > LONG_EDGE:
            img.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)

        # Embed sRGB ICC so Reddit's renderer (and downstream re-encoders) interpret pixel
        # values correctly. ImageCms gives us the bytes for free.
        srgb_bytes = ImageCms.ImageCmsProfile(_get_srgb_profile()).tobytes()

        buf = io.BytesIO()
        save_kwargs = {
            "quality": QUALITY,
            "optimize": True,
            "progressive": True,
            "icc_profile": srgb_bytes,
        }
        # Preserve EXIF (camera/lens info) but drop XMP — Reddit doesn't surface it and
        # XMP can balloon file size for marginal benefit.
        if img.info.get("exif"):
            save_kwargs["exif"] = img.info["exif"]
        img.save(buf, "JPEG", **save_kwargs)
        return buf.getvalue()
