"""Shared rules for turning a post's stored text into caption text.

Three code paths compose captions — the worker's per-platform builder, the Flickr and
Pinterest field passthroughs, and the manual Instagram panel — and they were drifting.
The rules that must agree across all of them live here.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[0-9a-z@_']+")

# Words that carry no identifying weight. A title and a description that share only
# these aren't saying the same thing.
_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "during", "for", "in", "of", "on", "the", "to", "with"}
)

# Share this much of the title's significant words and the description is a restatement.
# Measured against the real library: overlap clusters at 0.9-1.0 for restatements and
# at or below 0.2 for titles that genuinely add something, so the gap is wide.
_REDUNDANT_AT = 0.7


def title_is_redundant(title: str | None, description: str | None) -> bool:
    """True when the description already says everything the title says.

    The AI tagger writes the description as a *paraphrase* of the title far more often
    than as a verbatim copy — 'performing at "Teaser Fest" at Hotel Peter Paul New
    Orleans / Jan 2026' against 'on stage during "Teaser Fest" at Hotel Peter Paul, New
    Orleans. Jan 2026.' — so the old test (does the description *start with* the title?)
    missed nearly every real duplicate and captions went out saying the same sentence
    twice. Compare significant words instead.
    """
    title_words = {w for w in _WORD_RE.findall((title or "").lower()) if w not in _STOPWORDS}
    if not title_words:
        return False
    description_words = set(_WORD_RE.findall((description or "").lower()))
    return len(title_words & description_words) / len(title_words) >= _REDUNDANT_AT


# --- camera / shot information -------------------------------------------------------

_MAKE_NAMES = {
    "sony": "Sony",
    "canon": "Canon",
    "nikon": "Nikon",
    "fujifilm": "Fujifilm",
    "panasonic": "Panasonic",
    "olympus": "Olympus",
    "leica": "Leica",
    "leica camera ag": "Leica",
}

# Sony records bodies as ILCE-7RM4 rather than "α7R IV". Photographers write the
# marketing name, so translate the pattern and fall back to the raw value.
_SONY_ILCE = re.compile(r"^ILCE-(\d+)([A-Z]*?)(?:M(\d+))?$")
_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX"}


def _pretty_make(make: str | None) -> str:
    cleaned = (make or "").strip()
    return _MAKE_NAMES.get(cleaned.lower(), cleaned)


def _pretty_model(model: str | None) -> str:
    cleaned = (model or "").strip()
    match = _SONY_ILCE.match(cleaned)
    if not match:
        return cleaned
    number, suffix, mark = match.groups()
    name = f"α{number}{suffix}"
    if mark:
        roman = _ROMAN.get(int(mark))
        if not roman:
            return cleaned
        name = f"{name} {roman}"
    return name


def _body_name(post) -> str:
    make, model = _pretty_make(post.camera_make), _pretty_model(post.camera_model)
    if not model:
        return make
    # Leica writes the brand into the model ("LEICA Q3 43") — don't say it twice.
    if make and not model.lower().startswith(make.lower()):
        return f"{make} {model}"
    return model


def format_shot_info(post) -> str:
    """One-line camera/lens/exposure summary. Empty when nothing is known."""
    parts: list[str] = []
    body = _body_name(post)
    if body:
        parts.append(body)
    lens = (post.lens or "").strip()
    if lens:
        parts.append(lens)
    if post.focal_length:
        parts.append(f"{post.focal_length:g}mm")
    if post.aperture:
        parts.append(f"f/{post.aperture:g}")
    shutter = (post.shutter_speed or "").strip()
    if shutter:
        parts.append(shutter if shutter.endswith("s") else f"{shutter}s")
    if post.iso:
        parts.append(f"ISO {post.iso}")
    return " · ".join(parts)


def description_with_shot_info(post) -> str:
    """post.description, plus the shot-info line when the post opts in.

    Called at every point where the description is handed to a platform, so the block
    lands in the same place — after the caption text, ahead of the hashtag block —
    whichever destination it's bound for.
    """
    description = (post.description or "").strip()
    if not getattr(post, "include_exif", 0):
        return description
    shot = format_shot_info(post)
    if not shot:
        return description
    return f"{description}\n\n{shot}" if description else shot
