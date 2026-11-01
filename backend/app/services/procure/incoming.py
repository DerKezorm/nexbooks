"""The files of a finished download or an upload, as the filing finds them.

* **Never through a link.** Walking a download never follows a link or a junction; a link is neither filed nor looked
  into (it is counted, and that is all).
* ⚠️ **A dangerous file refuses the whole release**: a program or a script (``.exe``, ``.bat``, ``.sh``, ``.ps1`` ...)
  or a shortcut anywhere in the download, in folders that are left out too. A book release never needs one.
* **What counts**: the books by the formats the scan reads (``readers.format_of``), the files that tell about a book
  beside it (``cover.jpg``, ``metadata.json``, ``metadata.opf``), and archives that may hold books (ZIP and TAR are
  unpacked by ``unpacking``; RAR and 7z are named so that the release can be turned down with a reason).
* Left out: hidden files and folders, ``._`` files, the folders NASes make, ``Thumbs.db``; a walk stops after
  ``MAX_ENTRIES`` entries or ``MAX_DEPTH`` folders deep.
* A folder whose name starts with ``_UNPACK_`` or ``_FAILED_`` is a program still at work (``arrival``).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from ..readers import AUDIO_FORMATS, format_of
from ..readers.sidecar import kind_of as sidecar_kind

MAX_ENTRIES = 20_000
MAX_DEPTH = 12
#: Programs, scripts and shortcuts: a book release never carries one, and one of them refuses the release. (A ``.url``
#: is no program: the address of the page of whoever put the release up, which many a release carries, nothing opens.)
DANGEROUS = frozenset({
    "exe", "bat", "cmd", "com", "scr", "pif", "lnk", "ps1", "psm1", "vbs", "vbe", "wsf", "wsh", "msi", "msp", "jar",
    "sh", "bash", "dll", "cpl", "hta", "reg", "zipx", "arj", "lzh", "apk", "app", "desktop", "iso", "img",
})
#: Archives that are unpacked, and the ones that are only named.
UNPACKED = ("zip", "tar", "tgz", "tbz2", "tar.gz", "tar.bz2", "tar.xz", "txz")
NOT_UNPACKED = ("rar", "7z", "r00", "001", "cbr", "cb7")
SKIP_DIRS = frozenset({"@eadir", ".@__thumb", "__macosx", "#recycle", "$recycle.bin", "system volume information"})
SKIP_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})


def ending(name: str) -> str:
    """The ending of a name in lower case, ``tar.gz`` and the like as one."""
    lowered = name.casefold()
    for double in ("tar.gz", "tar.bz2", "tar.xz"):
        if lowered.endswith("." + double):
            return double
    return lowered.rsplit(".", 1)[-1] if "." in lowered else ""


def archive_kind(name: str) -> str:
    """``zip`` or ``tar`` for an archive that is unpacked, ``other`` for one that is not, empty for no archive."""
    found = ending(name)
    if found == "zip":
        return "zip"
    if found in UNPACKED:
        return "tar"
    if found in NOT_UNPACKED or (found.startswith("r") and found[1:].isdigit() and len(found) == 3):
        return "other"
    return ""


@dataclass
class Item:
    #: Relative to the root of the walk, with ``/``, as the disk spells it.
    path: str
    size: int
    format: str
    #: ``book`` or ``sidecar``.
    role: str


@dataclass
class Walked:
    books: list[Item] = field(default_factory=list)
    sidecars: list[Item] = field(default_factory=list)
    #: Archives as (relative path, size, ``zip`` / ``tar`` / ``other``).
    archives: list[tuple[str, int, str]] = field(default_factory=list)
    dangerous: list[str] = field(default_factory=list)
    links: int = 0
    truncated: bool = False
    #: The root could not be read at all.
    offline: bool = False

    def kinds(self) -> set[str]:
        return {"audio" if item.format in AUDIO_FORMATS.values() else "ebook" for item in self.books}


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name.casefold() in SKIP_DIRS


def _skip_file(name: str) -> bool:
    return name.startswith((".", "._", "~$")) or name.casefold() in SKIP_FILES


def _valid(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(ord(char) < 32 for char in name)


def _note(found: Walked, path: str, size: int, name: str) -> None:
    kind = ending(name)
    if kind in DANGEROUS:
        found.dangerous.append(path)
        return
    archive = archive_kind(name)
    if archive:
        found.archives.append((path, size, archive))
        return
    sidecar = sidecar_kind(name)
    if sidecar:
        found.sidecars.append(Item(path, size, sidecar, "sidecar"))
        return
    fmt = format_of(name)
    if fmt:
        found.books.append(Item(path, size, fmt, "book"))


def walk(root: Path, only: str | None = None) -> Walked:
    """Looks at ``root`` (a folder) or, with ``only``, at the one file of that name in it. Never follows a link, never
    leaves ``root``. Dangerous files are looked for in every folder, left-out ones included."""
    found = Walked()
    if only is not None:
        try:
            info = os.lstat(root / only)
        except OSError:
            found.offline = True
            return found
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            found.links += 1
            return found
        _note(found, only, info.st_size, only)
        return found
    try:
        with os.scandir(root):
            pass
    except OSError:
        found.offline = True
        return found
    stack: list[tuple[Path, str, int, bool]] = [(root, "", 0, False)]
    seen = 0
    while stack:
        folder, relative, depth, left_out = stack.pop()
        try:
            with os.scandir(folder) as entries:
                listing = list(entries)
        except OSError:
            continue
        for entry in listing:
            seen += 1
            if seen > MAX_ENTRIES:
                found.truncated = True
                return found
            name = entry.name
            path = f"{relative}/{name}" if relative else name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or entry.is_junction():
                found.links += 1
                continue
            if stat.S_ISDIR(info.st_mode):
                if depth + 1 <= MAX_DEPTH and _valid(name):
                    stack.append((Path(entry.path), path, depth + 1, left_out or _skip_dir(name)))
                elif depth + 1 > MAX_DEPTH:
                    found.truncated = True
                continue
            if not stat.S_ISREG(info.st_mode) or not _valid(name):
                continue
            if ending(name) in DANGEROUS:
                found.dangerous.append(path)
                continue
            if left_out or _skip_file(name):
                continue
            _note(found, path, info.st_size, name)
    found.books.sort(key=lambda item: item.path)
    found.sidecars.sort(key=lambda item: item.path)
    found.archives.sort()
    return found


__all__ = ["DANGEROUS", "Item", "Walked", "archive_kind", "ending", "walk"]
