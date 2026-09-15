"""Instagram Reels generator — silent 1080x1920 MP4s from up to 10 stills.

User picks photos in the frontend, optionally crops each to 9:16, and we produce a finished
MP4 that they drag into instagram.com. No audio (user adds music in IG manually if desired),
no IG Graph API involvement.

Two render modes per photo:
- Simple (default, crop_end_json NULL): one static crop with a subtle auto-zoom (1.0 -> 1.05
  over the segment) so a 6s still doesn't feel like a glitch on a Reel.
- Director (crop_end_json set): two viewports, frames interpolated linearly between them
  so the camera "pans" or "zooms" between user-picked start and end positions.

Segments are concat'd hard-cut for v1. Crossfade transitions are a polish item.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

OUTPUT_W = 1080
OUTPUT_H = 1920
FPS = 30

# What Instagram requires of a reel that arrives through the API, as opposed to one
# dragged into the website. Two of these are not defaults and the file was failing both:
#
#   +faststart   moves the moov atom to the front. Meta fetches the video from a URL and
#                needs the header before the payload; without it the container is only
#                playable once fully downloaded, and Meta rejects it.
#   silent AAC   the spec lists an audio codec and Meta's encoder is unreliable on a
#                track-less file. A silent stereo AAC track costs a few KB and removes
#                the whole class of failure. Music still gets added in-app afterwards.
#
# Segments stay video-only; this applies to the finished file, which is what gets
# uploaded.
# JPEG is full-range ("yuvj420p"); video is conventionally limited-range. Handing a
# full-range stream to a player that assumes limited range crushes blacks and blows
# highlights -- on stage photography, which lives in both, that is exactly the wrong
# failure. Converting at the segment stage means concat and the single-segment copy
# both inherit it.
_RANGE_FIX = "scale=in_range=full:out_range=tv"

_SILENT_AUDIO_IN = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
_DELIVERY_ARGS = [
    "-c:a", "aac", "-b:a", "128k", "-shortest",
    "-movflags", "+faststart",
]
# Upscale factor when handing a static frame to ffmpeg zoompan — without this the gentle
# zoom-in reveals pixel boundaries. 4x is overkill for the 5% zoom we apply but keeps the
# math simple and the file irrelevant after concat.
ZOOM_UPSCALE = 4
ZOOM_AMOUNT = 0.05  # 1.0 -> 1.05 over the segment
# Crossfade duration between adjacent segments. 0.4s reads as a soft cut that doesn't
# slow the pace, while masking the boundary cleanly. Total output is slightly shorter
# than the sum of segment durations: total = sum(durations) - (N-1) * CROSSFADE_S.
CROSSFADE_S = 0.4


@dataclass
class CropRect:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_json(cls, s: Optional[str]) -> Optional["CropRect"]:
        if not s:
            return None
        d = json.loads(s)
        return cls(int(d["x"]), int(d["y"]), int(d["width"]), int(d["height"]))


@dataclass
class PhotoSegment:
    source_path: Path
    duration_s: float
    crop_start: CropRect
    crop_end: Optional[CropRect] = None  # None = simple mode, set = director mode


class ReelGenerationError(Exception):
    pass


def generate(
    segments: list[PhotoSegment],
    output_path: Path,
) -> None:
    """Generate a silent 1080x1920 h.264 MP4 from the given photo segments.

    Caller is responsible for setting reel.status before/after this call. Raises
    ReelGenerationError on any ffmpeg failure with the trimmed stderr attached.
    """
    if not segments:
        raise ReelGenerationError("no segments to render")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="reel_") as tmp:
        tmpdir = Path(tmp)
        seg_paths: list[Path] = []
        for i, seg in enumerate(segments):
            seg_path = tmpdir / f"seg_{i:02d}.mp4"
            work = tmpdir / f"work_{i:02d}"
            work.mkdir()
            try:
                if seg.crop_end is None:
                    _render_simple(seg, seg_path, work)
                else:
                    _render_director(seg, seg_path, work)
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode("utf-8", errors="replace")[-2000:]
                raise ReelGenerationError(f"segment {i} failed: {stderr}") from e
            seg_paths.append(seg_path)

        try:
            durations = [s.duration_s for s in segments]
            _concat(seg_paths, durations, output_path, tmpdir)
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b"").decode("utf-8", errors="replace")[-2000:]
            raise ReelGenerationError(f"concat failed: {stderr}") from e


def _render_simple(seg: PhotoSegment, segment_path: Path, work: Path) -> None:
    """Static crop + gentle auto-zoom via ffmpeg zoompan.

    Pillow crops to the user's chosen rectangle, upscales 4x, writes a JPEG. ffmpeg loops the
    still and zooms 1.0 -> 1.05 over the segment duration.
    """
    frame_path = work / "frame.jpg"
    with Image.open(seg.source_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        c = seg.crop_start
        cropped = img.crop((c.x, c.y, c.x + c.width, c.y + c.height))
        upscaled = cropped.resize(
            (OUTPUT_W * ZOOM_UPSCALE, OUTPUT_H * ZOOM_UPSCALE), Image.LANCZOS
        )
        upscaled.save(frame_path, "JPEG", quality=90)

    frame_count = max(1, int(round(seg.duration_s * FPS)))
    zoom_step = ZOOM_AMOUNT / frame_count

    zoompan = (
        f"zoompan=z='min(zoom+{zoom_step:.6f},{1.0 + ZOOM_AMOUNT})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frame_count}:s={OUTPUT_W}x{OUTPUT_H}:fps={FPS}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(frame_path),
        "-vf", f"{zoompan},{_RANGE_FIX}",
        "-t", f"{seg.duration_s:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-an",
        str(segment_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _render_director(seg: PhotoSegment, segment_path: Path, work: Path) -> None:
    """Two-viewport interpolated pan — pre-render every frame via Pillow, assemble with ffmpeg.

    Linear interpolation between crop_start and crop_end across the segment. Slower than
    simple mode (180 Pillow operations for a 6s segment) but only used for hero shots, so
    the cost is bounded.
    """
    assert seg.crop_end is not None
    start = seg.crop_start
    end = seg.crop_end
    frame_count = max(1, int(round(seg.duration_s * FPS)))
    frames_dir = work / "frames"
    frames_dir.mkdir()

    with Image.open(seg.source_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        for i in range(frame_count):
            t = i / max(1, frame_count - 1)
            x = start.x + (end.x - start.x) * t
            y = start.y + (end.y - start.y) * t
            w = start.width + (end.width - start.width) * t
            h = start.height + (end.height - start.height) * t
            crop = img.crop((int(x), int(y), int(x + w), int(y + h)))
            frame = crop.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
            frame.save(frames_dir / f"f_{i:05d}.jpg", "JPEG", quality=88)

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f_%05d.jpg"),
        "-vf", _RANGE_FIX,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-an",
        str(segment_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat(segments: list[Path], durations: list[float], output_path: Path, tmpdir: Path) -> None:
    """Concat segments. Single segment → fast copy. Multi-segment → xfade crossfades.

    xfade math: with N segments of durations d_0..d_{N-1} and crossfade duration X, the
    i-th crossfade (between the running chain and segments[i+1]) has its offset at
    sum(d_0..d_i) - (i+1)*X — i.e. it begins X seconds before the chain ends. The output
    total length is sum(durations) - (N-1)*X.
    """
    if len(segments) == 1:
        # Nothing to fade against. Not a stream copy any more: the delivery file needs
        # an audio track and a relocated moov atom, neither of which survives -c copy.
        cmd = [
            "ffmpeg", "-y",
            "-i", str(segments[0]),
            *_SILENT_AUDIO_IN,
            "-c:v", "copy",
            *_DELIVERY_ARGS,
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return

    # Build the xfade filter chain.
    inputs: list[str] = []
    for p in segments:
        inputs.extend(["-i", str(p)])

    chain: list[str] = []
    prev_label = "[0:v]"
    for i in range(len(segments) - 1):
        cumulative = sum(durations[: i + 1])
        offset = cumulative - (i + 1) * CROSSFADE_S
        # Guard against negative offsets (only happens if a segment is shorter than the
        # crossfade itself — unlikely with 10-90s reels, but be safe).
        offset = max(0.01, offset)
        out_label = f"[v{i:02d}]"
        chain.append(
            f"{prev_label}[{i + 1}:v]xfade=transition=fade:"
            f"duration={CROSSFADE_S}:offset={offset:.3f}{out_label}"
        )
        prev_label = out_label

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        *_SILENT_AUDIO_IN,
        "-filter_complex", "; ".join(chain),
        "-map", prev_label,
        "-map", f"{len(segments)}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        *_DELIVERY_ARGS,
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
