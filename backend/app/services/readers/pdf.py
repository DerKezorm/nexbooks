"""PDF: title, author, the number of pages, and a cover when the first page is a picture.

PDFs are the most complicated of the formats, so the reader asks little: the document information, the page count, and
the pictures on the first page. The cover is taken only when that is cheap and bounded: a page that is one picture of
reasonable size (a scanned or designed cover); a page of text has no cover, and the book shows the drawn one. A
PDF too large to be parsed within the limits is listed by its name and size alone.
"""

from __future__ import annotations

import logging
import os
from typing import IO, Any

from . import images
from .limits import PDF_COVER_MAX_BYTES, PDF_MAX_BYTES, PDF_MAX_PAGES, TITLE_MAX, ReadError
from .names import split_authors
from .text import clean, year_of

logger = logging.getLogger("nexbooks.readers")


def read(handle: IO[bytes]) -> dict[str, Any]:
    size = os.fstat(handle.fileno()).st_size
    out: dict[str, Any] = {"format": "pdf", "title": "", "authors": [], "pages": 0, "chapters": [], "cover": None,
                           "year": None, "subjects": []}
    if size > PDF_MAX_BYTES:
        return out
    if handle.read(5) != b"%PDF-":
        # Some producers put a few bytes before the header; a file with none within the first kilobyte is no PDF.
        handle.seek(0)
        if b"%PDF-" not in handle.read(1024):
            raise ReadError("unreadable")
    handle.seek(0)
    from pypdf import PdfReader

    try:
        reader = PdfReader(handle, strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    out["drm"] = True
                    return out
            except Exception:  # noqa: BLE001 - an encryption pypdf cannot open: listed by name, as a protected file
                out["drm"] = True
                return out
        count = len(reader.pages)
    except Exception as exc:
        raise ReadError("unreadable") from exc
    out["pages"] = min(count, PDF_MAX_PAGES)
    try:
        info = reader.metadata
        if info is not None:
            out["title"] = clean(info.title, TITLE_MAX) if info.title else ""
            out["authors"] = split_authors(str(info.author)) if info.author else []
            out["year"] = year_of(str(info.get("/CreationDate", "")))
    except Exception as exc:  # noqa: BLE001
        logger.info("PDF information unreadable: %s", type(exc).__name__)
    out["cover"] = _cover(reader) if count else None
    return out


def _cover(reader: Any) -> tuple[bytes, str] | None:
    """The picture that is (most of) the first page, made small; None when the page is anything else."""
    try:
        page = reader.pages[0]
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        found = list(page.images)
        if not found or len(found) > 4 or width <= 0 or height <= 0:
            return None
        best = max(found, key=lambda item: len(item.data))
        data = best.data
        if len(data) > PDF_COVER_MAX_BYTES:
            return None
        return images.reduce_cover(data)
    except Exception as exc:  # noqa: BLE001 - no cover is the answer to anything odd on the first page
        logger.info("PDF cover unreadable: %s", type(exc).__name__)
        return None
