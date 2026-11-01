"""The photos of authors, as files in ``<data>/people``.

Like the covers of the shelf (``services/covers.py``) but in a folder of their own: a scan sweeps the covers
nothing on a shelf refers to, and these are not covers. A photo is drawn anew from the pixels
(``readers/images.reduce_portrait``), kept under a name made of its own checksum (a new photo is a new address),
and fetched by the server only (from a source's own host, or read from the author's folder of a library); a
browser is never sent to a host of a source.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path

from .. import private
from ..config import get_settings

logger = logging.getLogger("nexbooks.people")

NAME = re.compile(r"^[0-9a-f]{20}\.webp$")
FOLDER_NAME = "people"


def folder() -> Path:
    path = get_settings().data_dir / FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(webp: bytes) -> str:
    """Stores a photo (once: the same bytes are the same file) and returns its name."""
    name = hashlib.sha256(webp).hexdigest()[:20] + ".webp"
    target = folder() / name
    if not target.exists():
        scratch = target.with_suffix(".tmp")
        scratch.write_bytes(webp)
        os.replace(scratch, target)
        private.tighten(target)
    return name


def path_of(name: str) -> Path | None:
    """The file of a photo, or None for a name that is none of ours or a file that is gone."""
    if not NAME.fullmatch(name or ""):
        return None
    target = folder() / name
    return target if target.is_file() else None


def sweep(referenced: Iterable[str]) -> int:
    """Removes the photos nothing refers to any more. Returns how many went."""
    keep = set(referenced)
    removed = 0
    for entry in folder().iterdir():
        if entry.is_file() and entry.name not in keep and (NAME.fullmatch(entry.name) or entry.suffix == ".tmp"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                logger.warning("A photo could not be removed")
    return removed
