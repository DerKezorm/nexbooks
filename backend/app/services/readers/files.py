"""Opening a file of a library safely: below the library's folder, never through a link.

The scan has already looked at the file once; between that look and the opening somebody may have swapped it for a link
to ``/etc/passwd`` (or the scan was fooled by a name). So the opening checks again, every step of the way, and the file
that was opened must be the one that was looked at.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

from .. import memory
from .limits import ReadError

#: ``FILE_ATTRIBUTE_REPARSE_POINT`` on Windows: a junction, a symbolic link, a cloud placeholder.
REPARSE = 0x400


def is_link(info: os.stat_result) -> bool:
    """Whether an ``lstat`` result is a link of any kind (a symbolic link, a junction or other reparse point)."""
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE)


def parts_of(relative: str) -> list[str]:
    """The parts of a path inside a library, checked: relative, no ``..``, no empty or NUL part."""
    if not relative or "\x00" in relative or relative.startswith("/") or "\\" in relative:
        raise ReadError("unreadable")
    parts = relative.split("/")
    # A part with a drive in it ("C:") makes ``os.path.join`` start over at that drive: out of the library, on Windows.
    if any(part in ("", ".", "..") or os.path.splitdrive(part)[0] for part in parts):
        raise ReadError("unreadable")
    return parts


@contextmanager
def open_below(root: str, relative: str) -> Iterator[IO[bytes]]:
    """The file ``relative`` below ``root``, opened for reading. ``ReadError("link")`` when any part of the way is a
    link, ``ReadError("unreadable")`` for what is no plain file, ``ReadError("changed")`` when the file is not the one
    that was looked at or changed while it was read."""
    current = root
    seen = None
    parts = parts_of(relative)
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            seen = os.lstat(current)
        except OSError as exc:
            raise ReadError("unreadable") from exc
        if is_link(seen):
            raise ReadError("link")
        last = index == len(parts) - 1
        if last and not stat.S_ISREG(seen.st_mode):
            raise ReadError("unreadable")
        if not last and not stat.S_ISDIR(seen.st_mode):
            raise ReadError("unreadable")
    assert seen is not None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(current, flags)
    except OSError as exc:
        raise ReadError("unreadable") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != seen.st_size or (
            opened.st_ino and seen.st_ino and (opened.st_ino, opened.st_dev) != (seen.st_ino, seen.st_dev)
        ):
            raise ReadError("changed")
        handle = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with handle:
        try:
            yield handle
            after = os.fstat(handle.fileno())
            if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                raise ReadError("changed")
        finally:
            # What the scan read of a book is not read again soon: out of the page cache (``services/memory``).
            memory.forget_pages(handle.fileno())
