"""Reading the files of a library: what the book says about itself.

Everything here runs in the helper process of a scan (``services/probe.py``), never in the server itself: the files are
not to be trusted. An EPUB is a ZIP full of XML, a MOBI a database with lengths in it, a picture a decoder: each of them
can be made to eat memory or time. So every reader has hard limits (``limits.py``), reads only what it needs, treats XML
with ``defusedxml`` (no entities, no external files) and fails with a ``ReadError`` that carries a code, never a trace.

``read_file`` is the one entry: the format decides the reader. What comes back is a plain dict (the "probe"), small
enough to keep in the database next to the file.
"""

from __future__ import annotations

from typing import Any

from .limits import ReadError

__all__ = ["AUDIO_FORMATS", "EBOOK_FORMATS", "ReadError", "format_of", "read_file"]

#: Formats of e-books and of recordings, by file extension (lower case, without the dot) and the format they are.
EBOOK_FORMATS = {"epub": "epub", "pdf": "pdf", "azw3": "azw3", "azw": "azw3", "mobi": "mobi"}
AUDIO_FORMATS = {"mp3": "mp3", "m4b": "m4b", "m4a": "m4a", "flac": "flac", "ogg": "ogg", "oga": "ogg", "opus": "opus"}


def format_of(name: str) -> str:
    """The format a file name stands for, or an empty string for a file nexbooks does not read."""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return EBOOK_FORMATS.get(extension) or AUDIO_FORMATS.get(extension) or ""


def read_file(root: str, relative: str, fmt: str) -> dict[str, Any]:
    """Opens ``relative`` below ``root`` (never through a link) and reads it as ``fmt``. Raises ``ReadError``."""
    from .files import open_below

    with open_below(root, relative) as handle:
        if fmt == "epub":
            from . import epub

            return epub.read(handle)
        if fmt == "pdf":
            from . import pdf

            return pdf.read(handle)
        if fmt in ("azw3", "mobi"):
            from . import mobi

            return mobi.read(handle, fmt)
        if fmt in AUDIO_FORMATS.values():
            from . import audio

            return audio.read(handle, fmt, relative)
        if fmt in ("json", "opf", "cover"):
            from . import sidecar

            return sidecar.read(handle, fmt)
    raise ReadError("unsupported")
