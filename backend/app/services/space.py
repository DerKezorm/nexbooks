"""What the data folder holds and how much room is left: the numbers of the storage card in the settings.

* the database (with its write-ahead file) and the backups;
* the free room on the disk the data folder lies on;
* where the data folder lies (a local disk or a network share, ``services/mounts.py``): SQLite on a network share loses
  data when two processes meet there, so the card says it.

The books are not counted: they lie in the folders of the libraries, which nexbooks only reads.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from ..config import get_settings
from . import backups, mounts

logger = logging.getLogger("nexbooks.space")

#: How long a count of a folder is believed, and how long a count may take.
REMEMBER_SECONDS = 300.0
COUNT_SECONDS = 15.0
_lock = threading.Lock()
_remembered: dict[str, tuple[float, int, int, bool]] = {}


def forget() -> None:
    with _lock:
        _remembered.clear()


def count_folder(path: Path, *, limit_seconds: float = COUNT_SECONDS) -> tuple[int, int, bool]:
    """``(bytes, files, complete)`` of everything under ``path``; links are not followed. ``complete`` is false when the
    time ran out."""
    deadline = time.monotonic() + limit_seconds
    total = files = 0
    pending = [str(path)]
    while pending:
        if time.monotonic() > deadline:
            return total, files, False
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files, True


def folder_size(path: Path, *, now: float | None = None, limit_seconds: float = COUNT_SECONDS) -> dict[str, Any]:
    """A folder's size, counted at most every ``REMEMBER_SECONDS``."""
    moment = time.monotonic() if now is None else now
    key = str(path)
    with _lock:
        known = _remembered.get(key)
    if known is not None and moment - known[0] < REMEMBER_SECONDS:
        return {"bytes": known[1], "files": known[2], "complete": known[3]}
    size, files, complete = count_folder(path, limit_seconds=limit_seconds)
    with _lock:
        _remembered[key] = (moment, size, files, complete)
    return {"bytes": size, "files": files, "complete": complete}


def database_bytes() -> int:
    database = get_settings().database_path
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += database.with_name(database.name + suffix).stat().st_size
        except OSError:
            continue
    return total


def where(path: Path) -> dict[str, Any]:
    found = mounts.locate(path)
    return {"kind": found.kind, "fstype": found.fstype, "network": found.network}


def free_bytes(path: Path) -> tuple[int, int]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return 0, 0
    return usage.free, usage.total


def overview() -> dict[str, Any]:
    """Everything the card shows."""
    settings = get_settings()
    data = settings.data_dir
    saved = folder_size(backups.folder())
    free, total = free_bytes(data)
    return {
        "database": {"bytes": database_bytes(), "where": where(settings.database_path.parent)},
        "backups": saved,
        "free_bytes": free,
        "total_bytes": total,
        "data": {"where": where(data)},
    }
