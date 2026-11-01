"""Unpacks one archive into a folder, in a process of its own.

Run as ``python -I unpack_worker.py <zip|tar> <archive> <folder> <max bytes> <max files>``; the answer is one line of
JSON on standard output: ``{"ok": true, "files": n, "bytes": n}`` or ``{"ok": false, "reason": ...}`` with ``encrypted``
(a password), ``broken`` (not readable to its end), ``unsafe`` (a name that climbs out, a link, a device), ``too_large``
(more bytes than allowed, or packed far tighter than a book is) or ``too_many`` (more files than allowed).

Only the standard library, nothing of nexbooks: the archive is a file from outside, and what reads it should be able
to do as little as possible. On Linux the process may not take more than ``MEMORY_LIMIT`` of memory or write a file
larger than the bytes allowed. Nothing is ever written over: every file is made new (``xb``), below the folder only.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tarfile
import zipfile
from pathlib import PurePosixPath
from typing import IO, Any

MEMORY_LIMIT = 1024 * 1024 * 1024
#: A file packed tighter than this is no book (a text compresses to about a third, audio barely): a bomb.
MAX_RATIO = 200
#: Below this a high ratio harms nobody.
RATIO_FREE_BYTES = 16 * 1024 * 1024
#: All the files of an archive together, against the size of the archive: many small files, each packed no tighter than
#: ``MAX_RATIO`` allows, are a bomb as well. Books and recordings are packed already (EPUB, MP3, M4B, FLAC): 30 is wide.
MAX_TOTAL_RATIO = 30
#: Below this the ratio of the whole archive harms nobody.
TOTAL_FREE_BYTES = 64 * 1024 * 1024
CHUNK = 1024 * 1024


_SWAPPED = str.maketrans({char: "_" for char in '<>:"|?*'})


class Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _limit(max_bytes: int) -> None:
    try:
        import resource
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes + 1, max_bytes + 1))
    except (OSError, ValueError):
        pass


def safe_parts(name: str) -> list[str]:
    """The parts of a name in an archive, or ``Refused("unsafe")`` for one that climbs out or is no plain name."""
    text = name.replace("\\", "/")
    if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise Refused("unsafe")
    parts = [part for part in PurePosixPath(text).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise Refused("unsafe")
    if any(ord(char) < 32 for part in parts for char in part):
        raise Refused("unsafe")
    # A mark some file systems do not take (``: ? " < > | *``, a name from a Linux ZIP like ``Wer? Wie.epub``) is a
    # name, not a danger: it is written with ``_`` in its place.
    return [part.translate(_SWAPPED) for part in parts]


class Writer:
    def __init__(self, folder: str, max_bytes: int, max_files: int) -> None:
        self.folder = os.path.realpath(folder)
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.files = 0
        self.bytes = 0

    def _inside(self, parts: list[str]) -> str:
        target = os.path.join(self.folder, *parts)
        if os.path.commonpath([self.folder, os.path.realpath(os.path.dirname(target))]) != self.folder:
            raise Refused("unsafe")
        return target

    def folder_of(self, parts: list[str]) -> None:
        current = self.folder
        for part in parts:
            current = os.path.join(current, part)
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                os.mkdir(current)
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise Refused("unsafe")

    def write(self, parts: list[str], source: IO[bytes], declared: int) -> None:
        self.files += 1
        if self.files > self.max_files:
            raise Refused("too_many")
        if self.bytes + declared > self.max_bytes:
            raise Refused("too_large")
        self.folder_of(parts[:-1])
        target = self._inside(parts)
        written = 0
        with open(target, "xb") as handle:
            while True:
                piece = source.read(CHUNK)
                if not piece:
                    break
                written += len(piece)
                # The header may lie: what really comes out counts.
                if written > declared or self.bytes + written > self.max_bytes:
                    raise Refused("too_large")
                handle.write(piece)
        self.bytes += written


def unzip(archive: str, writer: Writer) -> None:
    try:
        opened = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise Refused("broken") from None
    with opened:
        infos = opened.infolist()
        if any(info.flag_bits & 0x1 for info in infos):
            raise Refused("encrypted")
        if len(infos) > writer.max_files * 2:
            raise Refused("too_many")
        total = sum(info.file_size for info in infos)
        if total > writer.max_bytes:
            raise Refused("too_large")
        if total > TOTAL_FREE_BYTES and total > max(os.path.getsize(archive), 1) * MAX_TOTAL_RATIO:
            raise Refused("too_large")
        for info in infos:
            parts = safe_parts(info.filename)
            mode = info.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Refused("unsafe")
            if info.is_dir():
                writer.folder_of(parts)
                continue
            if info.file_size > RATIO_FREE_BYTES and info.file_size > max(info.compress_size, 1) * MAX_RATIO:
                raise Refused("too_large")
            try:
                with opened.open(info) as source:
                    writer.write(parts, source, info.file_size)
            except (zipfile.BadZipFile, EOFError, OSError, NotImplementedError, RuntimeError):
                raise Refused("broken") from None


def untar(archive: str, writer: Writer) -> None:
    try:
        opened = tarfile.open(archive, "r:*")  # noqa: SIM115 - closed by the ``with`` below
    except (tarfile.TarError, OSError, EOFError):
        raise Refused("broken") from None
    packed = max(os.path.getsize(archive), 1)
    with opened:
        try:
            members = []
            for member in opened:
                members.append(member)
                if len(members) > writer.max_files * 2:
                    raise Refused("too_many")
        except (tarfile.TarError, OSError, EOFError):
            raise Refused("broken") from None
        total = sum(member.size for member in members if member.isreg())
        if total > writer.max_bytes:
            raise Refused("too_large")
        if total > TOTAL_FREE_BYTES and total > packed * MAX_TOTAL_RATIO:
            raise Refused("too_large")
        for member in members:
            parts = safe_parts(member.name)
            if member.isdir():
                writer.folder_of(parts)
                continue
            if not member.isreg():
                raise Refused("unsafe")
            try:
                source = opened.extractfile(member)
                if source is None:
                    raise Refused("broken")
                with source:
                    writer.write(parts, source, member.size)
            except (tarfile.TarError, OSError, EOFError):
                raise Refused("broken") from None


def run(arguments: list[str]) -> dict[str, Any]:
    kind, archive, folder, max_bytes, max_files = arguments
    writer = Writer(folder, int(max_bytes), int(max_files))
    try:
        (unzip if kind == "zip" else untar)(archive, writer)
    except Refused as exc:
        return {"ok": False, "reason": exc.reason}
    except FileExistsError:
        return {"ok": False, "reason": "unsafe"}
    except MemoryError:
        return {"ok": False, "reason": "too_large"}
    except OSError:
        return {"ok": False, "reason": "broken"}
    return {"ok": True, "files": writer.files, "bytes": writer.bytes}


def main() -> int:
    if len(sys.argv) != 6 or sys.argv[1] not in ("zip", "tar"):
        print(json.dumps({"ok": False, "reason": "broken"}))
        return 2
    # Here and not in ``run``: the limits stay with the process for good.
    _limit(int(sys.argv[4]))
    print(json.dumps(run(sys.argv[1:])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
