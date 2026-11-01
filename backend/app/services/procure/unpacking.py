"""Unpacking the archives of a release.

* Only when the release holds no book outside its archives (a release with an EPUB and a ZIP of extras is not unpacked).
* **In a process of its own** (``unpack_worker.py``, started with ``python -I``), with a time limit, a memory limit
  (Linux) and limits of its own: at most ``MAX_FILES`` files, at most the bytes allowed (``MAX_BYTES``, and never more
  than the free space of the library less ``RESERVE``), nothing packed tighter than a book can be. A worker that does
  not answer in time is killed.
* ZIP and TAR (also ``.tar.gz``, ``.tgz``, ``.tar.bz2``, ``.tar.xz``). RAR and 7z are not unpacked: the release is
  turned down with ``archive_unsupported`` and the next one is looked for. An archive with a password is
  ``archive_encrypted``, one that cannot be read to its end ``archive_broken``, one over a limit ``archive_too_large``,
  a name that climbs out of the folder or a link ``archive_unsafe``.
* What comes out lies in a folder of nexbooks in the library (``.nexbooks-unpack/<download>``, a hidden folder the scan
  leaves alone, on the disk the book goes to) and is gone after the filing, whatever its end.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("nexbooks.procure")

WORKER = Path(__file__).with_name("unpack_worker.py")
MAX_FILES = 2000
MAX_BYTES = 20 * 1024**3
RESERVE = 100 * 1024**2
TIMEOUT_BASE = 120.0
TIMEOUT_PER_GIB = 60.0
FOLDER = ".nexbooks-unpack"
REASONS = {"encrypted": "archive_encrypted", "broken": "archive_broken", "unsafe": "archive_unsafe",
           "too_large": "archive_too_large", "too_many": "archive_too_large"}


class Refused(Exception):
    """The release is turned down: ``code`` is a code of the check."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def free_bytes(folder: Path) -> int:
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return 0


def folder_for(library_path: str, download_id: int) -> Path:
    return Path(library_path) / FOLDER / str(int(download_id))


def remove(folder: Path) -> None:
    """Removes an unpack folder of nexbooks, never through a link (not the folder, not the hidden folder above it)."""
    from . import hidden

    if not hidden.holds(folder, FOLDER):
        return
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError:
        logger.info("An unpack folder could not be removed")
    try:
        folder.parent.rmdir()
    except OSError:
        pass


def timeout_for(size: int) -> float:
    return TIMEOUT_BASE + TIMEOUT_PER_GIB * size / 1024**3


def _environment() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL")
    return {name: os.environ[name] for name in keep if name in os.environ}


def run_worker(kind: str, archive: Path, folder: Path, allowed: int, max_files: int = MAX_FILES,
               timeout: float | None = None) -> dict[str, object]:
    command = [sys.executable, "-I", str(WORKER), kind, str(archive), str(folder), str(int(allowed)), str(max_files)]
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout or timeout_for(archive.stat().st_size),
                              env=_environment(), check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "broken"}
    except OSError:
        return {"ok": False, "reason": "broken"}
    try:
        answer = json.loads(done.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "reason": "too_large" if done.returncode in (-25, 153) else "broken"}
    return answer if isinstance(answer, dict) else {"ok": False, "reason": "broken"}


def unpack(archives: list[tuple[Path, str]], folder: Path) -> None:
    """Unpacks every archive (``(path, kind)``) into a folder of its own below ``folder``. ``Refused`` with the code of
    the first that cannot be."""
    from . import hidden

    if any(kind == "other" for _path, kind in archives):
        raise Refused("archive_unsupported")
    # ``hidden.Unsafe`` when the hidden folder is a link: nothing is unpacked there.
    hidden.folder(folder.parent.parent, FOLDER, make=True)
    folder.mkdir(exist_ok=True)
    used = 0
    for number, (archive, kind) in enumerate(archives, start=1):
        allowed = min(MAX_BYTES - used, free_bytes(folder) - RESERVE)
        if allowed <= 0:
            raise Refused("archive_too_large")
        target = folder / str(number)
        target.mkdir(exist_ok=True)
        answer = run_worker(kind, archive, target, allowed)
        if not answer.get("ok"):
            reason = str(answer.get("reason") or "broken")
            logger.info("An archive of a release was not unpacked reason=%s", reason)
            raise Refused(REASONS.get(reason, "archive_broken"))
        used += int(answer.get("bytes") or 0)  # type: ignore[call-overload]


__all__ = ["FOLDER", "Refused", "folder_for", "free_bytes", "remove", "run_worker", "unpack"]
