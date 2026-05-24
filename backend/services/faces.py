"""Face detection helper — picks a sensible default crop center for the Reel CropModal.

Uses OpenCV's bundled Haar cascade frontal-face classifier. Fast, no model download, no
GPU. Misses side-profiles and partial faces, which is fine — when detection fails we
return None and the frontend falls back to image center. The user can always drag.

Used only at startup-position time; the user still controls the final crop in the UI.
We don't store these — just compute on demand when the CropModal opens.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("framepost.faces")

# OpenCV ships this XML with opencv-python-headless. The path varies by install but
# cv2.data.haarcascades is the documented locator.
_CASCADE: cv2.CascadeClassifier | None = None


def _get_cascade() -> cv2.CascadeClassifier:
    global _CASCADE
    if _CASCADE is None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _CASCADE = cv2.CascadeClassifier(path)
        if _CASCADE.empty():
            raise RuntimeError(f"failed to load Haar cascade at {path}")
    return _CASCADE


# To keep detection fast on 60MP RAW-exports we downscale before classification.
_MAX_DETECT_LONG_EDGE = 1200


def detect_face_center(image_path: Path) -> tuple[float, float] | None:
    """Return the normalized (x_frac, y_frac) center of the largest detected face, or None.

    Coordinates are in [0..1] relative to the image's natural width/height. Caller
    converts to whatever space they need.

    Returns None if:
    - file can't be opened
    - no face detected by the frontal classifier (side profiles, no face, full-body shots)
    """
    try:
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    except Exception:
        log.exception("face detect: imread failed for %s", image_path)
        return None
    if img is None:
        return None

    h, w = img.shape[:2]
    if h == 0 or w == 0:
        return None

    # Downscale for speed; rescale detected coords back to the original.
    long_edge = max(h, w)
    if long_edge > _MAX_DETECT_LONG_EDGE:
        scale = _MAX_DETECT_LONG_EDGE / long_edge
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0
        small = img

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    try:
        cascade = _get_cascade()
    except RuntimeError:
        log.exception("face detect: cascade unavailable")
        return None

    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.15,
        minNeighbors=4,
        minSize=(40, 40),
    )
    if len(faces) == 0:
        return None

    # Pick the largest detected box — most likely the photographic subject.
    faces_arr = np.array(faces)
    areas = faces_arr[:, 2] * faces_arr[:, 3]
    largest = faces_arr[int(np.argmax(areas))]
    fx, fy, fw, fh = largest

    # Center of the face box in the small-image space, then unscale to original.
    cx = (fx + fw / 2.0) / scale
    cy = (fy + fh / 2.0) / scale

    return (float(cx / w), float(cy / h))
