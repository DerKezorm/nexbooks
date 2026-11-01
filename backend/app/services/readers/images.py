"""Covers: a picture from a book or a folder, made small and safe, and its main colour.

A cover is drawn anew from the pixels (WebP, at most ``SIZE``), so nothing a file hid in the original (a second picture,
a place, a script) reaches the data folder, and the interface never serves bytes it did not make. The picture is
refused before it is opened when it is too large in bytes or in pixels (a 1-bit PNG can be a few kilobytes and a billion
pixels). A cover that cannot be read is no reason to refuse the book: the book just has none.
"""

from __future__ import annotations

import colorsys
import io
import logging
from typing import Any

from ..pictures import sniff
from .limits import COVER_MAX_BYTES, COVER_MAX_PIXELS

logger = logging.getLogger("nexbooks.readers")

#: The longest sides of a stored cover: sharp at the largest size the detail page shows it, on a sharp screen.
SIZE = (720, 1080)
#: The longest sides of a stored photo of an author: shown round, at most 240 pixels wide, on a sharp screen.
PORTRAIT_SIZE = (640, 640)
QUALITY = 82
_KINDS = {"jpeg", "png", "webp", "gif", "bmp"}

#: Bins of hue (30 degrees each) for the main colour.
_BINS = 12


def _dominant(pixels: bytes) -> str:
    """The main colour of a small picture (RGB bytes), ``#rrggbb``.

    The rule: a colour counts by how saturated and how bright it is (grey, near-white and near-black count for little),
    the pixels are sorted into twelve bins of hue, and the bin with the most weight (a neighbour counting half) gives
    the colour, as the weighted mean of its pixels. A cover that is nearly grey has no such colour: then it is the mean
    of
    its mid-tones. The result is then kept in a range a spine can wear (not white, not black, not a neon)."""
    weights = [0.0] * _BINS
    sums = [[0.0, 0.0, 0.0] for _ in range(_BINS)]
    mid = [0.0, 0.0, 0.0, 0.0]
    every = [0.0, 0.0, 0.0, 0.0]
    for index in range(0, len(pixels) - 2, 3):
        r, g, b = pixels[index], pixels[index + 1], pixels[index + 2]
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        every[0] += r
        every[1] += g
        every[2] += b
        every[3] += 1
        if 0.15 <= v <= 0.92:
            mid[0] += r
            mid[1] += g
            mid[2] += b
            mid[3] += 1
        if s >= 0.18 and 0.12 <= v <= 0.95:
            weight = s * v
            slot = int(h * _BINS) % _BINS
            weights[slot] += weight
            for part, value in enumerate((r, g, b)):
                sums[slot][part] += value * weight
    total = sum(weights)
    count = every[3] or 1
    if total < 0.04 * count:
        base = mid if mid[3] else every
        rgb = [base[i] / (base[3] or 1) for i in range(3)]
    else:
        best = max(range(_BINS), key=lambda i: weights[i] + 0.5 * (weights[i - 1] + weights[(i + 1) % _BINS]))
        rgb = [sums[best][i] / weights[best] for i in range(3)]
    h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    r, g, b = colorsys.hsv_to_rgb(h, min(s, 0.85), min(max(v, 0.2), 0.9))
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def reduce_cover(data: bytes) -> tuple[bytes, str] | None:
    """``(WebP bytes, main colour)`` of a cover picture, or None when it is none, too large, or broken."""
    if not data or len(data) < 64 or len(data) > COVER_MAX_BYTES:
        return None
    if sniff(data[:64]) not in _KINDS:
        return None
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > COVER_MAX_PIXELS or min(image.width, image.height) < 8:
                return None
            if image.format == "JPEG":
                image.draft("RGB", (SIZE[0] * 2, SIZE[1] * 2))
            image.load()
            picture: Any = ImageOps.exif_transpose(image)
            if picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info:
                picture = picture.convert("RGBA")
                ground = Image.new("RGBA", picture.size, (255, 255, 255, 255))
                ground.alpha_composite(picture)
                picture = ground
            picture = picture.convert("RGB")
            picture.thumbnail(SIZE, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            picture.save(out, "WEBP", quality=QUALITY, method=4)
            small = picture.copy()
            small.thumbnail((48, 72), Image.Resampling.BILINEAR)
            return out.getvalue(), _dominant(small.tobytes())
    except Exception as exc:  # noqa: BLE001 - a broken or hostile picture: the book has no cover, that is all
        logger.info("A cover could not be read: %s", type(exc).__name__)
        return None


def reduce_portrait(data: bytes) -> bytes | None:
    """WebP bytes of a photo of a person (at most ``PORTRAIT_SIZE``), or None when it is none, too large, or broken.
    Drawn
    anew like a cover: nothing a file hid in the original reaches the data folder."""
    if not data or len(data) < 64 or len(data) > COVER_MAX_BYTES:
        return None
    if sniff(data[:64]) not in _KINDS:
        return None
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > COVER_MAX_PIXELS or min(image.width, image.height) < 32:
                return None
            if image.format == "JPEG":
                image.draft("RGB", (PORTRAIT_SIZE[0] * 2, PORTRAIT_SIZE[1] * 2))
            image.load()
            picture: Any = ImageOps.exif_transpose(image)
            if picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info:
                picture = picture.convert("RGBA")
                ground = Image.new("RGBA", picture.size, (255, 255, 255, 255))
                ground.alpha_composite(picture)
                picture = ground
            picture = picture.convert("RGB")
            picture.thumbnail(PORTRAIT_SIZE, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            picture.save(out, "WEBP", quality=QUALITY, method=4)
            return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - a broken or hostile picture: the author has no photo, that is all
        logger.info("A photo could not be read: %s", type(exc).__name__)
        return None
