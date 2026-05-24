"""EXIF extraction via piexif. Returns columnar fields + a JSON-safe dump for posts.exif_raw.

Designed to never raise — partial data is better than failing the whole import on a tag edge case.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

import piexif


def _coerce(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace").rstrip("\x00")
        except Exception:
            return base64.b64encode(value).decode()
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(v, int) for v in value):
        num, den = value
        return num / den if den else None
    if isinstance(value, list):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    return value


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _shutter(value) -> str | None:
    if not value or not isinstance(value, tuple) or len(value) != 2:
        return None
    num, den = value
    if not den:
        return None
    if num <= den:
        # ≤ 1s — render as 1/N
        if num and den % num == 0:
            return f"1/{den // num}"
        if num:
            return f"1/{round(den / num)}"
    seconds = num / den
    return f"{seconds:.1f}s"


def _gps_to_decimal(rationals, ref) -> float | None:
    if not rationals or len(rationals) != 3:
        return None
    try:
        d, m, s = (n / d if d else 0 for n, d in rationals)
    except Exception:
        return None
    decimal = d + m / 60 + s / 3600
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", errors="ignore")
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def extract(path: str) -> dict[str, Any]:
    """Return a dict with the columnar fields and `exif_raw` as a JSON string."""
    out: dict[str, Any] = {
        "captured_at": None,
        "camera_make": None,
        "camera_model": None,
        "lens": None,
        "focal_length": None,
        "iso": None,
        "shutter_speed": None,
        "aperture": None,
        "gps_lat": None,
        "gps_lng": None,
        "exif_raw": None,
    }
    try:
        data = piexif.load(path)
    except Exception:
        return out

    zeroth = data.get("0th", {})
    exif = data.get("Exif", {})
    gps = data.get("GPS", {})

    out["camera_make"] = _coerce(zeroth.get(piexif.ImageIFD.Make))
    out["camera_model"] = _coerce(zeroth.get(piexif.ImageIFD.Model))
    out["lens"] = _coerce(exif.get(piexif.ExifIFD.LensModel))
    fl = exif.get(piexif.ExifIFD.FocalLength)
    if isinstance(fl, tuple) and len(fl) == 2 and fl[1]:
        out["focal_length"] = fl[0] / fl[1]
    iso = exif.get(piexif.ExifIFD.ISOSpeedRatings)
    if isinstance(iso, list) and iso:
        iso = iso[0]
    if isinstance(iso, int):
        out["iso"] = iso
    out["shutter_speed"] = _shutter(exif.get(piexif.ExifIFD.ExposureTime))
    fn = exif.get(piexif.ExifIFD.FNumber)
    if isinstance(fn, tuple) and len(fn) == 2 and fn[1]:
        out["aperture"] = fn[0] / fn[1]
    out["captured_at"] = _parse_dt(_coerce(exif.get(piexif.ExifIFD.DateTimeOriginal)))
    out["gps_lat"] = _gps_to_decimal(gps.get(piexif.GPSIFD.GPSLatitude), gps.get(piexif.GPSIFD.GPSLatitudeRef))
    out["gps_lng"] = _gps_to_decimal(gps.get(piexif.GPSIFD.GPSLongitude), gps.get(piexif.GPSIFD.GPSLongitudeRef))

    raw = {section: _coerce(values) for section, values in data.items() if section != "thumbnail"}
    try:
        out["exif_raw"] = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        out["exif_raw"] = None
    return out
