"""Geometry + policy tests for the Instagram variant pipeline."""
import io
from pathlib import Path

import pytest
from PIL import Image

from services import ig_variant
from services.platforms import instagram


def test_needs_transform_bounds():
    floor = ig_variant.RATIOS["3:4"]
    assert ig_variant.needs_transform(2 / 3, floor) is True      # 2:3 portrait
    assert ig_variant.needs_transform(3 / 4, floor) is False     # exactly at floor
    assert ig_variant.needs_transform(4 / 5, floor) is False     # squarer than floor
    assert ig_variant.needs_transform(1.0, floor) is False       # square
    assert ig_variant.needs_transform(2.5, floor) is True        # pano
    assert ig_variant.needs_transform(None, floor) is False      # unknown dims → let Meta judge


def test_aspect_ok_epsilon():
    # 2477x3096 is 0.80006… — must pass the 4:5 gate (real photos land a hair off).
    assert instagram.aspect_ok(2477, 3096) is True
    assert instagram.aspect_ok(2064, 3096) is False  # 2:3
    assert instagram.aspect_ok(0, 0) is True         # unknown dims


@pytest.fixture()
def tall_jpeg(tmp_path: Path) -> Path:
    p = tmp_path / "tall.jpg"
    Image.new("RGB", (200, 300), (120, 40, 40)).save(p, "JPEG")
    return p


@pytest.mark.parametrize("fit", ["crop", "pad", "pad_blur"])
def test_render_variant_hits_exact_ratio(tall_jpeg: Path, fit: str):
    data = ig_variant.render_variant(
        tall_jpeg, target_ratio=3 / 4, fit=fit, offset=0.5, out_width=300,
    )
    img = Image.open(io.BytesIO(data))
    assert (img.width, img.height) == (300, 400)
    assert img.format == "JPEG"


def test_render_variant_offset_moves_window(tmp_path: Path):
    # Top half red, bottom half blue — offset 0 must keep red, offset 1 must keep blue.
    src = tmp_path / "split.jpg"
    img = Image.new("RGB", (200, 400), (255, 0, 0))
    img.paste(Image.new("RGB", (200, 200), (0, 0, 255)), (0, 200))
    img.save(src, "JPEG", quality=95)

    def corner_color(offset: float) -> tuple[int, ...]:
        data = ig_variant.render_variant(
            src, target_ratio=1.0, fit="crop", offset=offset, out_width=100,
        )
        out = Image.open(io.BytesIO(data))
        return out.getpixel((50, 5 if offset == 0.0 else out.height - 5))

    r, g, b = corner_color(0.0)
    assert r > 180 and b < 80  # top-aligned keeps red
    r, g, b = corner_color(1.0)
    assert b > 180 and r < 80  # bottom-aligned keeps blue


def test_is_aspect_error_matching():
    assert ig_variant.is_aspect_error(Exception("The submitted image with aspect ratio ..."))
    assert not ig_variant.is_aspect_error(Exception("Media download has failed."))
