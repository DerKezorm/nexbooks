"""nexbooks's own hidden folders in a library: the trash (``.nexbooks-trash``), the
uploads being received (``.nexbooks-incoming``) and the archives being unpacked (``.nexbooks-unpack``).

Each is a plain folder directly in the library, made by nexbooks. ⚠️ Whoever may write into the library besides
nexbooks (a share, another service) could put a link or a junction of that name there, to a place outside. Every use
looks at the folder itself first (``os.lstat``: no link, no junction, a folder) and where it leads (``realpath``:
directly in the library): when it is anything else, nothing goes in and nothing comes out (``Unsafe``), and the problem
is said.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger("nexbooks.procure")


class Unsafe(OSError):
    """A hidden folder of nexbooks is a link, a junction or no folder, or leads out of its library."""

    code = "link"


def _real(path: Path) -> Path | None:
    try:
        return Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None


def folder(library_path: str | Path, name: str, *, make: bool = False) -> Path:
    """The hidden folder ``name`` of the library, checked; made when it is missing and ``make``. A missing one that is
    not to be made is returned as it is (there is nothing in it to read or to remove). ``Unsafe`` when it is not
    nexbooks's own plain folder in the library."""
    root = Path(library_path)
    path = root / name
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not make:
            return path
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or path.is_junction() or not stat.S_ISDIR(info.st_mode):
        logger.warning("A hidden folder of nexbooks in a library is a link or no folder; it is left alone")
        raise Unsafe("hidden folder is a link")
    real, real_root = _real(path), _real(root)
    if real is None or real_root is None or real.parent != real_root:
        logger.warning("A hidden folder of nexbooks leads out of its library; it is left alone")
        raise Unsafe("hidden folder leads outside")
    return path


def safe(library_path: str | Path, name: str) -> bool:
    """Whether the hidden folder may be used (it is missing, or nexbooks's own plain folder)."""
    try:
        folder(library_path, name)
    except Unsafe:
        return False
    return True


def holds(child: Path, name: str) -> bool:
    """Whether ``child`` is a folder directly in the hidden folder ``name`` of a library, and that hidden folder may be
    used: the check before anything below it is read or removed."""
    parent = child.parent
    if parent.name != name or child.is_symlink() or child.is_junction():
        return False
    return safe(parent.parent, name)


__all__ = ["Unsafe", "folder", "holds", "safe"]
