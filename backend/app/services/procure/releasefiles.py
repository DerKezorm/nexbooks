"""The file of a release kept for a hand-over that has to come again.

An indexer gave the NZB or the torrent file, and then the download program did not answer. The hand-over comes again a
few minutes later; it must not ask the indexer for the file again every time (an indexer counts every release it gives,
and a program that is away for a day would cost a day of them). So the file is kept here until the program has it:

* **Where**: a folder of the data folder (``release-files``), one file per release, named by a hash of what makes the
  release the same one (indexer, protocol, size, name), never by anything a person or an indexer typed, so no name can
  lead out of the folder. The file holds only what the indexer gave: an NZB (message ids of the news servers) or a
  torrent file, or the magnet link.
* **How much**: each file is at most what a release may weigh (``gate.limit_for``), all of them together at most
  ``MAX_TOTAL``, and none is kept longer than ``MAX_AGE``. ``prune`` (the hourly housekeeping of the planner) removes
  what
  is older and, when the folder is too full, the oldest first.
* **When it goes**: as soon as a program has the release (taken, unsure, refused), or the release fell out of use; at
the
  latest after ``MAX_AGE``.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import time
from pathlib import Path

from ...config import get_settings
from .gate import NZB_MAX, TORRENT_MAX

logger = logging.getLogger("nexbooks.procure")

FOLDER_NAME = "release-files"
MAX_AGE_SECONDS = 48 * 3600
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 500
_LIMIT = max(NZB_MAX, TORRENT_MAX)


def folder() -> Path:
    path = get_settings().data_dir / FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def key_of(indexer_id: int | None, protocol: str, size: int, title: str) -> str:
    """The name of a release's file: a hash of what makes it the same release."""
    text = f"{indexer_id}|{protocol}|{size}|{title}"
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:40]


def put(key: str, content: bytes, magnet: str) -> None:
    """Keeps the file (or the magnet link) of a release; nothing happens for a file that is too big or on any failure of
    the disk: the hand-over then fetches the file again, which is slower and counts, but works."""
    if len(content) > _LIMIT or len(magnet) > 4000:
        return
    try:
        base = folder()
        target = base / f"{key}.dat"
        tmp = base / f"{key}.tmp"
        tmp.write_bytes(content if content else magnet.encode("utf-8"))
        os.replace(tmp, target)
        marker = base / f"{key}.magnet"
        if magnet and not content:
            marker.write_bytes(b"")
        else:
            marker.unlink(missing_ok=True)
    except OSError:
        logger.warning("A release file could not be kept")


def get(key: str) -> tuple[bytes, str] | None:
    """``(content, magnet)`` of a kept file (one of them empty), or None."""
    try:
        base = folder()
        data = (base / f"{key}.dat").read_bytes()
        if time.time() - (base / f"{key}.dat").stat().st_mtime > MAX_AGE_SECONDS:
            drop(key)
            return None
        if (base / f"{key}.magnet").exists():
            return b"", data.decode("utf-8", errors="replace")
        return data, ""
    except OSError:
        return None


def drop(key: str) -> None:
    base = folder()
    for suffix in (".dat", ".magnet", ".tmp"):
        with contextlib.suppress(OSError):
            (base / f"{key}{suffix}").unlink(missing_ok=True)


def prune() -> int:
    """Removes what is older than ``MAX_AGE_SECONDS``, then the oldest until the folder is within its size and number.
    Returns how many files went."""
    removed = 0
    now = time.time()
    entries: list[tuple[float, int, Path]] = []
    try:
        for item in folder().iterdir():
            if item.suffix not in (".dat", ".magnet", ".tmp"):
                continue
            try:
                info = item.stat()
            except OSError:
                continue
            if now - info.st_mtime > MAX_AGE_SECONDS:
                with contextlib.suppress(OSError):
                    item.unlink()
                    removed += 1
                continue
            if item.suffix == ".dat":
                entries.append((info.st_mtime, info.st_size, item))
    except OSError:
        return removed
    entries.sort()
    total = sum(size for _mtime, size, _path in entries)
    while entries and (total > MAX_TOTAL or len(entries) > MAX_FILES):
        _mtime, size, path = entries.pop(0)
        drop(path.stem)
        total -= size
        removed += 1
    return removed


__all__ = ["drop", "folder", "get", "key_of", "prune", "put"]
