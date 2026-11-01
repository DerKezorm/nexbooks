"""The covers of the books, as files in ``<data>/covers``.

A cover is made by the scan (``readers/images.py``: small, WebP, drawn anew) and kept under a name made of its own
checksum, so that the same picture is stored once and *a new picture is a new address*: a browser that cached the old
one is never shown it for the new book. Only names of this shape are ever opened (``NAME``), whatever the database
says.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .. import private
from ..config import get_settings

logger = logging.getLogger("nexbooks.covers")

NAME = re.compile(r"^[0-9a-f]{20}\.webp$")
FOLDER_NAME = "covers"


def folder() -> Path:
    path = get_settings().data_dir / FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(webp: bytes) -> str:
    """Stores a cover (once: the same bytes are the same file) and returns its name."""
    name = hashlib.sha256(webp).hexdigest()[:20] + ".webp"
    target = folder() / name
    if target.exists():
        return name
    # A scratch file of its own: two requests that store the same picture at once (a double click on "take it") never
    # write into one file.
    handle, scratch = tempfile.mkstemp(dir=folder(), prefix=name[:20] + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(webp)
        try:
            os.replace(scratch, target)
        except OSError:
            # The other one got there first (Windows does not let a file be replaced while it is being replaced): its
            # file holds the same bytes.
            if not target.exists():
                raise
        private.tighten(target)
    finally:
        if os.path.exists(scratch):
            try:
                os.unlink(scratch)
            except OSError:
                logger.warning("A scratch file of a cover could not be removed")
    return name


def path_of(name: str) -> Path | None:
    """The file of a cover, or None for a name that is none of ours or a file that is gone."""
    if not NAME.fullmatch(name or ""):
        return None
    target = folder() / name
    return target if target.is_file() else None


def sweep(referenced: Iterable[str]) -> int:
    """Removes the covers nothing refers to any more (a book's cover that changed, a library that was removed). Returns
    how many went."""
    keep = set(referenced)
    removed = 0
    for entry in folder().iterdir():
        if entry.is_file() and (entry.name not in keep) and (NAME.fullmatch(entry.name) or entry.suffix == ".tmp"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                logger.warning("A cover could not be removed")
    return removed
