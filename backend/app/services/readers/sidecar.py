"""Files that lie beside a book and tell about it: Audiobookshelf's ``metadata.json``, Calibre's ``metadata.opf`` and a
picture called ``cover`` or ``folder``.

They rank below the book file itself (a name or a sidecar may be wrong, the book is what it is) and above what a file
name guesses. They are small and read with a limit, and their content is never trusted by type: a field that is not what
it should be is a field that is not there.
"""

from __future__ import annotations

import json
from typing import IO, Any

from . import images, opf
from .limits import AUDIO_MAX_CHAPTERS, SIDECAR_COVER_MAX, SIDECAR_MAX_BYTES, TITLE_MAX, ReadError
from .names import authors_of_field, number_of, series_from_field, split_authors
from .text import clean, html_to_text, isbn13, language_of, year_of

#: The file names (lower case) that are sidecars, and the kind each is.
SIDECAR_NAMES = {
    "metadata.json": "json",
    "metadata.opf": "opf",
    "cover.jpg": "cover", "cover.jpeg": "cover", "cover.png": "cover", "cover.webp": "cover",
    "folder.jpg": "cover", "folder.jpeg": "cover", "folder.png": "cover", "folder.webp": "cover",
}


def kind_of(name: str) -> str:
    """``json``, ``opf`` or ``cover`` for a file name that is a sidecar, else an empty string."""
    return SIDECAR_NAMES.get(name.casefold(), "")


def _bounded(handle: IO[bytes], limit: int) -> bytes:
    data = handle.read(limit + 1)
    if len(data) > limit:
        raise ReadError("too_large")
    return data


def _strings(value: Any, limit: int = 40) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for text in (clean(item, 120) for item in value[:limit] if isinstance(item, str)) if text]


def _series(value: Any) -> tuple[str, float | None]:
    """The first series of ``series`` in Audiobookshelf's file: a list of ``"Name #1.0"`` (or of objects)."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return "", None
    for item in value[:10]:
        if isinstance(item, str) and clean(item, 200):
            return series_from_field(item)
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            sequence = item.get("sequence")
            pos = number_of(str(sequence)) if isinstance(sequence, str | int | float) else None
            return clean(item["name"], 200), pos
    return "", None


def read_json(handle: IO[bytes]) -> dict[str, Any]:
    """Audiobookshelf's ``metadata.json``."""
    data = _bounded(handle, SIDECAR_MAX_BYTES)
    try:
        raw = json.loads(data.decode("utf-8-sig", "replace"))
    except (ValueError, RecursionError) as exc:
        raise ReadError("unreadable") from exc
    if not isinstance(raw, dict):
        raise ReadError("unreadable")
    series, pos = _series(raw.get("series"))
    chapters: list[dict[str, Any]] = []
    if isinstance(raw.get("chapters"), list):
        for item in raw["chapters"][:AUDIO_MAX_CHAPTERS]:
            if isinstance(item, dict) and isinstance(item.get("start"), int | float) and not isinstance(
                    item.get("start"), bool) and 0 <= item["start"] < 10**7:
                chapters.append({"title": clean(item.get("title"), TITLE_MAX), "start": float(item["start"])})
    abridged = raw.get("abridged")
    year = raw.get("publishedYear")
    title = clean(raw.get("title"), TITLE_MAX)
    authors, pen_names = authors_of_field(raw.get("authors"), titles=[title])
    out: dict[str, Any] = {
        "format": "json",
        "title": title,
        "subtitle": clean(raw.get("subtitle"), TITLE_MAX),
        "authors": authors,
        "pen_names": pen_names,
        "narrators": split_authors(raw.get("narrators"), any_role=True),
        "series": series,
        "series_pos": pos,
        "subjects": _strings(raw.get("genres")) + _strings(raw.get("tags")),
        "year": year_of(year) if isinstance(year, str | int) else None,
        "published": clean(raw.get("publishedDate"), 10) if isinstance(raw.get("publishedDate"), str) else "",
        "publisher": clean(raw.get("publisher"), 200),
        "description": html_to_text(raw.get("description")) if isinstance(raw.get("description"), str) else "",
        "isbn": isbn13(raw.get("isbn")) if isinstance(raw.get("isbn"), str) else "",
        "asin": clean(raw.get("asin"), 20).upper() if isinstance(raw.get("asin"), str) else "",
        "language": language_of(raw.get("language")) if isinstance(raw.get("language"), str) else "",
        "abridged": abridged if isinstance(abridged, bool) else None,
        "chapters": chapters,
    }
    return out


def read_opf(handle: IO[bytes]) -> dict[str, Any]:
    """Calibre's ``metadata.opf``: the metadata of the package, the same as inside an EPUB."""
    package = opf.parse_package(_bounded(handle, SIDECAR_MAX_BYTES))
    meta = dict(package.meta)
    meta["format"] = "opf"
    return meta


def read_cover(handle: IO[bytes]) -> dict[str, Any]:
    """A picture beside the book, made small. A picture that cannot be used is no problem of the book: no cover."""
    data = _bounded(handle, SIDECAR_COVER_MAX)
    return {"format": "cover", "cover": images.reduce_cover(data)}


def read(handle: IO[bytes], kind: str) -> dict[str, Any]:
    if kind == "json":
        return read_json(handle)
    if kind == "opf":
        return read_opf(handle)
    if kind == "cover":
        return read_cover(handle)
    raise ReadError("unsupported")
