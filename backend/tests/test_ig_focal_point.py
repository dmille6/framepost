"""Photographer-set focal point for the Instagram crop.

Face detection answers "where is the face". The focal point exists because that is
usually but not always "what is this photograph about" — a hand on a fire fan, a
back-turned drop, the wrong face in a duo act.
"""
import pytest
from PIL import Image

from models import Post
from services import ig_variant
from services.ig_variant import FACE_ANCHOR, auto_offset, auto_window, focal_for


def _img(tmp_path, w, h):
    p = tmp_path / "src.jpg"
    Image.new("RGB", (w, h), (40, 40, 40)).save(p, "JPEG", quality=85)
    return p


# --- focal_for ----------------------------------------------------------------------

def test_focal_for_reads_both_coordinates():
    assert focal_for(Post(ig_focal_x=0.25, ig_focal_y=0.75)) == (0.25, 0.75)


def test_focal_for_treats_a_half_written_point_as_absent():
    """Same rule as rect_for: pairing a real coordinate with a guess is worse than
    falling back to detection."""
    assert focal_for(Post(ig_focal_x=0.25, ig_focal_y=None)) is None
    assert focal_for(Post(ig_focal_x=None, ig_focal_y=0.75)) is None
    assert focal_for(Post()) is None


def test_focal_for_clamps_into_range():
    assert focal_for(Post(ig_focal_x=-0.2, ig_focal_y=1.4)) == (0.0, 1.0)


# --- auto_offset --------------------------------------------------------------------

def test_focal_point_replaces_detection_entirely(tmp_path, monkeypatch):
    """Not merged with, not averaged against — replaced. And detection is skipped, which
    is the difference between one Haar pass per photo and none."""
    called = []
    monkeypatch.setattr(
        ig_variant.faces, "detect_face_center",
        lambda *a, **k: called.append(1) or (0.5, 0.9),
    )
    src = _img(tmp_path, 1000, 2000)
    off = auto_offset(src, crop_frac=0.5, vertical=True, focal=(0.5, 0.2))
    assert called == []
    # anchor 0.2 with a half-height window: offset*(1-.5) = .2 - .38*.5 = 0.01
    assert off == pytest.approx((0.2 - FACE_ANCHOR * 0.5) / 0.5)


def test_without_a_focal_point_detection_still_drives_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_variant.faces, "detect_face_center", lambda *a, **k: (0.5, 0.2))
    src = _img(tmp_path, 1000, 2000)
    assert auto_offset(src, crop_frac=0.5, vertical=True) == pytest.approx(
        auto_offset(src, crop_frac=0.5, vertical=True, focal=(0.5, 0.2))
    )


def test_no_face_and_no_focal_point_centres(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_variant.faces, "detect_face_center", lambda *a, **k: None)
    assert auto_offset(_img(tmp_path, 1000, 2000), crop_frac=0.5, vertical=True) == 0.5


# --- auto_window --------------------------------------------------------------------

def test_auto_window_puts_the_anchor_at_the_headroom_line():
    """The editor draws this box; the worker cuts it. If they disagree the preview lies."""
    x, y, w, h = auto_window(1000, 2000, target_ratio=0.8, anchor=(0.5, 0.35))
    assert (x, w) == (0.0, 1.0)
    assert h == pytest.approx(0.625)                  # 1250 of 2000
    # The anchor should sit FACE_ANCHOR of the way down the window.
    assert y + FACE_ANCHOR * h == pytest.approx(0.35)


def test_auto_window_clamps_at_the_edges():
    """An anchor near the top can't pull the window off the frame."""
    _, y, _, h = auto_window(1000, 2000, target_ratio=0.8, anchor=(0.5, 0.01))
    assert y == pytest.approx(0.0)
    _, y2, _, _ = auto_window(1000, 2000, target_ratio=0.8, anchor=(0.5, 0.99))
    assert y2 + h == pytest.approx(1.0)


def test_auto_window_crops_horizontally_on_a_panorama():
    x, y, w, h = auto_window(4000, 1000, target_ratio=1.91, anchor=(0.35, 0.5))
    assert (y, h) == (0.0, 1.0)
    assert w == pytest.approx(1910 / 4000)
    assert x + FACE_ANCHOR * w == pytest.approx(0.35)


def test_an_anchor_too_close_to_the_edge_gives_up_the_headroom_rather_than_the_frame():
    """FACE_ANCHOR is a preference, not a promise: a subject at 0.8 across a panorama
    can't sit at the 38% line without the window leaving the photo."""
    x, _, w, _ = auto_window(4000, 1000, target_ratio=1.91, anchor=(0.8, 0.5))
    assert x + w == pytest.approx(1.0)
    assert x + FACE_ANCHOR * w < 0.8            # anchor drifts right of the line


def test_auto_window_leaves_an_in_range_photo_whole():
    assert auto_window(800, 1000, target_ratio=0.8, anchor=(0.5, 0.3)) == (0.0, 0.0, 1.0, 1.0)


def test_auto_window_survives_missing_dimensions():
    assert auto_window(0, 0, target_ratio=0.8, anchor=(0.5, 0.5)) == (0.0, 0.0, 1.0, 1.0)
