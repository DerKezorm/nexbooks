"""The covers of the books that are not on a shelf, as files in ``<data>/foreign-covers``.

Like the covers of the shelf (``services/covers.py``) but in a folder of their own: a scan sweeps the covers nothing
on a shelf refers to, and these are not on a shelf. A cover is drawn anew from the pixels (``readers/images.py``),
kept under a name made of its own checksum (a new picture is a new address), and fetched by the server from the
source's own host only (``foreign.fetch_cover``); a browser is never sent to a host of a source, and never asks the
server to fetch.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ... import private
from ...config import get_settings

logger = logging.getLogger("nexbooks.discover")

NAME = re.compile(r"^[0-9a-f]{20}\.webp$")
FOLDER_NAME = "foreign-covers"


def folder() -> Path:
    path = get_settings().data_dir / FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(webp: bytes) -> str:
    """Stores a cover (once: the same bytes are the same file) and returns its name."""
    name = hashlib.sha256(webp).hexdigest()[:20] + ".webp"
    target = folder() / name
    if not target.exists():
        scratch = target.with_suffix(".tmp")
        scratch.write_bytes(webp)
        os.replace(scratch, target)
        private.tighten(target)
    return name


def link_of(row: Any) -> str | None:
    """The address of the cover of a book of the sources in nexbooks' own API, when there is a cover to show or the
    source has one to fetch (the server fetches it when it is asked for); None when there is none. The address of the
    source is never given out."""
    base = f"/api/v1/discover/books/{row.token}/cover"
    if row.cover:
        return f"{base}?v={row.cover[:12]}"
    return base if row.cover_url else None


def path_of(name: str) -> Path | None:
    """The file of a cover, or None for a name that is none of ours or a file that is gone."""
    if not NAME.fullmatch(name or ""):
        return None
    target = folder() / name
    return target if target.is_file() else None


def sweep(referenced: Iterable[str]) -> int:
    """Removes the covers no book refers to any more. Returns how many went."""
    keep = set(referenced)
    removed = 0
    for entry in folder().iterdir():
        if entry.is_file() and entry.name not in keep and (NAME.fullmatch(entry.name) or entry.suffix == ".tmp"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                logger.warning("A cover could not be removed")
    return removed
