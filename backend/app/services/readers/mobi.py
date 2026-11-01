"""MOBI and AZW3 (Kindle): title, author, language, ISBN, publisher and cover from the header of the book.

A MOBI file is a Palm database: a list of records with their offsets. The first record holds the MOBI header and, behind
it, the EXTH block with the metadata (type, length, data), the second place a length can lie. Every length read here is
checked against the record it lies in and against a limit before it is used; only the headers and the cover record are
read, never the text. The book is not converted (that comes with block B-devices); a file that is encrypted (DRM) still
gives its metadata, and is marked, because it cannot be read in the browser.
"""

from __future__ import annotations

import struct
from itertools import pairwise
from typing import IO, Any

from . import images
from .limits import (
    BYTES_PER_PAGE,
    MOBI_MAX_FILE,
    MOBI_MAX_HEADER,
    MOBI_MAX_IMAGE,
    MOBI_MAX_RECORDS,
    PAGES_MAX,
    TITLE_MAX,
    ReadError,
)
from .names import authors_of_field
from .text import clean, html_to_text, isbn13, language_of, year_of

#: The Windows language ids (the low byte) a MOBI header names.
_LOCALES = {0x07: "de", 0x09: "en", 0x0C: "fr", 0x0A: "es", 0x10: "it", 0x13: "nl", 0x16: "pt", 0x1D: "sv",
            0x06: "da", 0x14: "no", 0x0B: "fi", 0x15: "pl", 0x05: "cs", 0x19: "ru", 0x11: "ja", 0x1F: "tr"}
EXTH_TEXT = {100: "author", 101: "publisher", 103: "description", 104: "isbn", 105: "subject", 106: "published",
             108: "contributor", 113: "asin", 503: "title", 524: "language"}
MAX_EXTH_RECORDS = 400


def _text(data: bytes, encoding: int) -> str:
    return data.decode("utf-8" if encoding == 65001 else "cp1252", "replace")


def read(handle: IO[bytes], fmt: str) -> dict[str, Any]:
    """The metadata of a MOBI or AZW3 file. ``ReadError("unreadable")`` when it is none."""
    import os

    size = os.fstat(handle.fileno()).st_size
    if size > MOBI_MAX_FILE:
        raise ReadError("too_large")
    if size < 100:
        raise ReadError("unreadable")

    def at(offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > size:
            raise ReadError("unreadable")
        handle.seek(offset)
        data = handle.read(length)
        if len(data) != length:
            raise ReadError("unreadable")
        return data

    head = at(0, 78)
    count = struct.unpack(">H", head[76:78])[0]
    if count < 2 or count > MOBI_MAX_RECORDS:
        raise ReadError("unreadable")
    table = at(78, 8 * count)
    offsets = [struct.unpack(">I", table[8 * i:8 * i + 4])[0] for i in range(count)]
    if any(later < earlier for earlier, later in pairwise(offsets)) or offsets[-1] > size:
        raise ReadError("unreadable")
    length0 = offsets[1] - offsets[0]
    if not 100 <= length0 <= MOBI_MAX_HEADER:
        raise ReadError("unreadable")
    rec0 = at(offsets[0], length0)
    if rec0[16:20] != b"MOBI":
        raise ReadError("unreadable")
    _compression, _unused, text_length, _text_records, _record_size, encryption = struct.unpack(">HHIHHH", rec0[:14])
    header_length = struct.unpack(">I", rec0[20:24])[0]
    encoding = struct.unpack(">I", rec0[28:32])[0]
    out: dict[str, Any] = {"format": fmt, "drm": encryption != 0}
    full_offset, full_length = struct.unpack(">II", rec0[84:92]) if length0 >= 92 else (0, 0)
    locale = struct.unpack(">I", rec0[92:96])[0] if length0 >= 96 else 0
    first_image = struct.unpack(">I", rec0[108:112])[0] if length0 >= 112 else 0xFFFFFFFF
    exth_flags = struct.unpack(">I", rec0[128:132])[0] if length0 >= 132 else 0

    title = ""
    if 0 < full_length <= 1024 and full_offset + full_length <= length0:
        title = clean(_text(rec0[full_offset:full_offset + full_length], encoding), TITLE_MAX)
    values: dict[str, list[str]] = {}
    cover_offset: int | None = None
    start = 16 + header_length
    if exth_flags & 0x40 and start + 12 <= length0 and rec0[start:start + 4] == b"EXTH":
        exth_length, exth_count = struct.unpack(">II", rec0[start + 4:start + 12])
        end = min(start + exth_length, length0)
        position = start + 12
        for _ in range(min(exth_count, MAX_EXTH_RECORDS)):
            if position + 8 > end:
                break
            kind, length = struct.unpack(">II", rec0[position:position + 8])
            if length < 8 or position + length > end:
                break
            data = rec0[position + 8:position + length]
            if kind == 201 and len(data) == 4:
                cover_offset = struct.unpack(">I", data)[0]
            elif kind in EXTH_TEXT and len(data) <= 8000:
                values.setdefault(EXTH_TEXT[kind], []).append(_text(data, encoding))
            position += length
    if values.get("title"):
        title = clean(values["title"][0], TITLE_MAX) or title
    out["title"] = title
    out["authors"], out["pen_names"] = authors_of_field(values.get("author", []), titles=[title])
    out["publisher"] = clean((values.get("publisher") or [""])[0], 200)
    out["description"] = html_to_text((values.get("description") or [""])[0])
    out["isbn"] = next((found for found in (isbn13(v) for v in values.get("isbn", [])) if found), "")
    asin = clean((values.get("asin") or [""])[0], 20).upper()
    out["asin"] = asin if len(asin) == 10 and asin.isalnum() else ""
    out["subjects"] = [text for text in (clean(v, 80) for v in values.get("subject", [])[:24]) if text]
    published = clean((values.get("published") or [""])[0], 40)
    out["published"] = published[:10] if published[:4].isdigit() else ""
    out["year"] = year_of(published)
    out["language"] = language_of((values.get("language") or [""])[0]) or _LOCALES.get(locale & 0xFF, "")
    out["pages"] = min(PAGES_MAX, max(1, round(text_length / BYTES_PER_PAGE))) if text_length else 0
    out["chapters"] = []
    out["cover"] = None
    if cover_offset is not None and first_image != 0xFFFFFFFF and first_image < count:
        index = first_image + cover_offset
        if 0 <= index < count - 1 or index == count - 1:
            stop = offsets[index + 1] if index + 1 < count else size
            length = stop - offsets[index]
            if 0 < length <= MOBI_MAX_IMAGE:
                out["cover"] = images.reduce_cover(at(offsets[index], length))
    return out
