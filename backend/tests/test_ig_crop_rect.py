"""Normalized crop rect: the studio's storage format.

The load-bearing test is the last one — a rect derived from an offset must render
byte-identical to the offset path, or migration 0019 silently re-crops every photo
someone had already positioned by hand.
"""
import io

import pytest
from PIL import Image

from models import Post
from services import ig_variant
from services.ig_variant import fit_rect_to_ratio, rect_for


def _img(tmp_path, w, h, name="src.jpg"):
    p = tmp_path / name
    im = Image.new("RGB", (w, h))
    # A gradient, so a shifted crop produces genuinely different bytes.
    for y in range(h):
        for x in range(0, w, 8):
            im.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    im.save(p, "JPEG", quality=90)
    return p


def _dims(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.size


# --- fit_rect_to_ratio --------------------------------------------------------------

def test_matching_rect_is_returned_unchanged():
    # 1000x1250 source, rect covering it all, target 4:5 == the rect's own ratio.
    assert fit_rect_to_ratio((0.0, 0.0, 1.0, 1.0), 1000, 1250, 0.8) == (0.0, 0.0, 1.0, 1.0)


def test_too_wide_rect_is_narrowed_around_its_centre():
    # Full frame of a square source, target 4:5 → sides pulled in, height kept.
    x, y, w, h = fit_rect_to_ratio((0.0, 0.0, 1.0, 1.0), 1000, 1000, 0.8)
    assert h == pytest.approx(1.0)
    assert w == pytest.approx(0.8)
    assert x == pytest.approx(0.1)      # centred: (1 - 0.8) / 2
    assert y == pytest.approx(0.0)


def test_too_tall_rect_is_shortened_around_its_centre():
    x, y, w, h = fit_rect_to_ratio((0.0, 0.0, 1.0, 1.0), 1000, 2000, 0.8)
    assert w == pytest.approx(1.0)
    assert h == pytest.approx(0.625)    # 1000/0.8 = 1250 of 2000
    assert y == pytest.approx(0.1875)


def test_refit_stays_inside_the_source():
    """A rect near an edge must not produce a window hanging off the image."""
    for rect in [(0.0, 0.0, 0.4, 0.4), (0.6, 0.6, 0.4, 0.4), (0.9, 0.9, 0.1, 0.1)]:
        x, y, w, h = fit_rect_to_ratio(rect, 1200, 1600, 0.8)
        assert x >= -1e-9 and y >= -1e-9
        assert x + w <= 1.0 + 1e-9
        assert y + h <= 1.0 + 1e-9


def test_degenerate_rect_falls_back_to_full_frame():
    assert fit_rect_to_ratio((0.0, 0.0, 0.0, 0.5), 100, 100, 0.8) == (0.0, 0.0, 1.0, 1.0)


# --- rect_for -----------------------------------------------------------------------

def test_rect_for_requires_every_component():
    p = Post(id="x", status="pending", ig_crop_x=0.1, ig_crop_y=0.1, ig_crop_w=0.5)
    assert rect_for(p) is None            # h missing — treated as absent, not guessed


def test_rect_for_clamps_drifted_values():
    p = Post(id="x", status="pending", ig_crop_x=0.9, ig_crop_y=0.9,
             ig_crop_w=0.5, ig_crop_h=0.5)
    x, y, w, h = rect_for(p)
    assert x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9


def test_rect_for_rejects_zero_area():
    p = Post(id="x", status="pending", ig_crop_x=0.0, ig_crop_y=0.0,
             ig_crop_w=0.0, ig_crop_h=0.5)
    assert rect_for(p) is None


# --- render_variant -----------------------------------------------------------------

def test_output_is_always_exactly_the_target_ratio(tmp_path):
    src = _img(tmp_path, 1200, 1800)
    for rect in [(0.0, 0.0, 1.0, 1.0), (0.25, 0.25, 0.5, 0.5), (0.0, 0.4, 0.6, 0.6)]:
        data = ig_variant.render_variant(src, target_ratio=0.8, rect=rect, out_width=400)
        w, h = _dims(data)
        assert w == 400
        assert w / h == pytest.approx(0.8, abs=0.01)


def test_a_tighter_rect_actually_crops_tighter(tmp_path):
    """The whole point: a rect can express a window smaller than maximum area."""
    src = _img(tmp_path, 1200, 1800)
    full = ig_variant.render_variant(src, target_ratio=0.8, rect=(0.0, 0.0, 1.0, 1.0),
                                     out_width=300)
    tight = ig_variant.render_variant(src, target_ratio=0.8, rect=(0.3, 0.3, 0.4, 0.4),
                                      out_width=300)
    assert full != tight


def test_rect_takes_precedence_over_offset(tmp_path):
    src = _img(tmp_path, 1200, 1800)
    by_rect = ig_variant.render_variant(src, target_ratio=0.8, rect=(0.0, 0.0, 1.0, 0.5),
                                        offset=1.0, out_width=300)
    by_rect_only = ig_variant.render_variant(src, target_ratio=0.8,
                                             rect=(0.0, 0.0, 1.0, 0.5), out_width=300)
    assert by_rect == by_rect_only        # the offset was ignored entirely


def test_offset_path_still_works_when_no_rect_given(tmp_path):
    src = _img(tmp_path, 1200, 1800)
    top = ig_variant.render_variant(src, target_ratio=0.8, offset=0.0, out_width=300)
    bottom = ig_variant.render_variant(src, target_ratio=0.8, offset=1.0, out_width=300)
    assert top != bottom


@pytest.mark.parametrize("sw,sh,offset", [
    (1200, 1800, 0.0), (1200, 1800, 0.5), (1200, 1800, 1.0),   # portrait, crops vertically
    (3000, 1000, 0.0), (3000, 1000, 0.5), (3000, 1000, 1.0),   # pano, crops horizontally
])
def test_migrated_rect_renders_identically_to_the_offset_it_replaced(tmp_path, sw, sh, offset):
    """Migration 0019 converts every hand-set offset into a rect. If the conversion is
    even slightly off, every photo the photographer positioned by hand silently moves."""
    src = _img(tmp_path, sw, sh, name=f"{sw}x{sh}-{offset}.jpg")
    target = 0.8

    # The exact arithmetic the migration performs.
    ratio = sw / sh
    if ratio < target:
        fw, fh = 1.0, ratio / target
        x, y = 0.0, offset * (1.0 - fh)
    elif ratio > target:
        fw, fh = target / ratio, 1.0
        x, y = offset * (1.0 - fw), 0.0
    else:
        fw = fh = 1.0
        x = y = 0.0

    by_offset = ig_variant.render_variant(src, target_ratio=target, offset=offset,
                                          out_width=400)
    by_rect = ig_variant.render_variant(src, target_ratio=target, rect=(x, y, fw, fh),
                                        out_width=400)
    assert by_offset == by_rect
