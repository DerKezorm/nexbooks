"""Where a book goes in its library and how it gets there.

**Where**: by the scheme of tidying the operator chose for fetched books (``formats.config``, ``organize.scheme``): an
e-book is ``<folders>/<name>.<ending>`` for each of its formats, a recording is the folder ``<folders>/<name>`` with its
parts below it as they lay (``CD 1/01.mp3``) and the files beside it (``cover.jpg``, ``metadata.json``). An upgrade of
an e-book goes next to the file it betters, under the same name (so that the scan sees one book in several formats).

**Never over anything.** A goal that is taken on the disk (a file, a folder that holds a book) is never used: the name
gets `` (2)``, `` (3)`` ... up to ``MAX_TRIES``; a path longer than ``scheme.MAX_PATH`` is ``too_long``. A file that
lies at its goal already with the same size and content counts as placed (a filing that stopped in the middle).

**How**: a torrent is linked (a hard link: the program seeds from its own name, the library has its own), a Usenet
download and an upload are moved (renamed), and where that is not possible (another disk) copied: first under a hidden
partial name, the size compared, then given its name. A copy needs the file's size plus ``RESERVE`` free. A moved or
linked file gets its name by ``os.link`` (which never replaces anything) and the old name goes after.

⚠️ **Every path is checked after links are resolved**: the source lies inside the folder of the download (or the
unpacked one), the goal inside the library; no folder on the way to the goal is a link; nothing is made outside.
"""

from __future__ import annotations

import errno
import logging
import os
import posixpath
import secrets
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..catalog import natural
from ..organize import scheme as schemes
from ..readers import names
from ..readers.files import parts_of

logger = logging.getLogger("nexbooks.procure")

RESERVE = 100 * 1024**2
MAX_TRIES = 20
COPY_CHUNK = 8 * 1024**2
PARTIAL = ".nexbooks-part"
READ_ONLY = {errno.EROFS, errno.EACCES, errno.EPERM}


class PlaceError(Exception):
    """A file that cannot be placed: ``code`` is ``no_write`` (the folder takes nothing), ``no_space``, ``link`` (a
    link on the way), ``outside`` (a path that leaves its folder), ``source_changed`` (the file is not as it was read)
    or ``failed``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- Where -----------------------------------------------------------------------------------------------------------


def render(schema: str, values: dict[str, str]) -> list[str]:
    """The parts of the path of a book by the scheme; falls back to ``{Author}/{Title}`` and to the title alone when the
    scheme leaves nothing (``SchemeError`` is never passed on)."""
    for text in (schema, schemes.DEFAULT, "{Title}"):
        try:
            return schemes.render(schemes.parse(text), values)
        except schemes.SchemeError:
            continue
    return [schemes.clean_part(values.get("Title") or "") or "Book"]


def _kind_on_disk(root: Path, relative: str) -> str:
    """``none``, ``dir``, ``file``, ``link`` or ``other`` for ``relative`` below ``root``; ``link`` for any link on
    the way."""
    current = root
    try:
        parts = parts_of(relative)
    except Exception:  # noqa: BLE001 - a name that is no path inside the library is taken
        return "other"
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return "none"
        except OSError:
            return "other"
        if stat.S_ISLNK(info.st_mode) or (hasattr(current, "is_junction") and current.is_junction()):
            return "link"
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "file"
        if index == len(parts) - 1:
            return "dir" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "other"
    return "dir"


def _numbered(text: str, number: int) -> str:
    return text if number == 1 else f"{text} ({number})"


@dataclass
class Goal:
    #: Relative to the library, with ``/``, one per file (by the key the caller gave).
    targets: dict[str, str]
    #: For a recording: the folder it goes to.
    folder: str = ""


def ebook_goal(root: Path, parts: list[str], files: list[tuple[str, str, int]], *,
               beside: str | None = None, planned: set[str] = frozenset()) -> Goal:  # type: ignore[assignment]
    """The goals of an e-book's files (``(key, ending, size)``): each format under one name. ``beside`` is the path of a
    file the book has already (an upgrade): the new formats go next to it, under its name."""
    if beside:
        directory = posixpath.dirname(beside)
        stem = posixpath.splitext(posixpath.basename(beside))[0]
    else:
        directory = "/".join(parts[:-1])
        stem = parts[-1]
    for number in range(1, MAX_TRIES + 1):
        name = _numbered(stem, number)
        targets = {key: (f"{directory}/{name}.{fmt}" if directory else f"{name}.{fmt}") for key, fmt, _size in files}
        if len(set(targets.values())) != len(targets):
            raise PlaceError("failed")
        if any(len(target) > schemes.MAX_PATH for target in targets.values()):
            raise PlaceError("too_long")
        kinds = [_kind_on_disk(root, target) for target in targets.values()]
        if "link" in kinds or (directory and _kind_on_disk(root, directory) in ("link", "file", "other")):
            raise PlaceError("link")
        if all(kind == "none" for kind in kinds) and not {t.casefold() for t in targets.values()} & {
                item.casefold() for item in planned}:
            return Goal(targets)
    raise PlaceError("taken")


def _below(path: str, unit_dir: str) -> str:
    """A file of a recording, relative to the folder of the recording."""
    if not unit_dir:
        return path
    return path[len(unit_dir) + 1:] if path.startswith(unit_dir + "/") else posixpath.basename(path)


def _disc_folders(folders: set[str]) -> dict[str, str]:
    """The new names of the folders a recording lies in, when the scan would not read them as the parts of one book
    (``Nebelbahn (CD 2)``, or folders a person said are one book): ``CD 1``, ``CD 2`` in their order. Folders it reads
    as parts keep their names."""
    below = folders - {""}
    if len(below) < 2 or all(names.is_cd_folder(piece) for folder in below for piece in folder.split("/")):
        return {}
    ordered = sorted(below, key=lambda folder: (names.disc_in_path(f"{folder}/file") or 0, natural(folder)))
    return {folder: f"CD {number}" for number, folder in enumerate(ordered, start=1)}


def audio_goal(root: Path, parts: list[str], files: list[tuple[str, str, int]], unit_dir: str, *,
               taken: set[str] = frozenset()) -> Goal:  # type: ignore[assignment]
    """The goals of a recording's files (``(key, path relative to the release, size)``): the folder of the scheme, the
    parts below it as they lay below the recording's own folder. A folder that is there already is never used (it may
    hold another book, which the scan would read as one with this); ``taken`` names folders other books of the same
    filing go to."""
    base = "/".join(parts)
    for number in range(1, MAX_TRIES + 1):
        folder = _numbered(base, number)
        if folder.casefold() in {item.casefold() for item in taken}:
            continue
        kind = _kind_on_disk(root, folder)
        if kind == "link":
            raise PlaceError("link")
        if kind != "none":
            continue
        targets: dict[str, str] = {}
        relatives = {key: _below(path, unit_dir) for key, path, _size in files}
        discs = _disc_folders({posixpath.dirname(relative) for relative in relatives.values()})
        for key, relative in relatives.items():
            folder_of, name = posixpath.split(relative)
            relative = posixpath.join(discs.get(folder_of, folder_of), name)
            pieces = relative.split("/")
            # The folders of the parts are cleaned as parts, the file as a file: its ending stays whatever its length.
            clean = "/".join((schemes.clean_file(part) if index == len(pieces) - 1 else schemes.clean_part(part)) or "_"
                             for index, part in enumerate(pieces))
            targets[key] = f"{folder}/{clean}"
        if len(set(targets.values())) != len(targets):
            # Two parts with one name once cleaned: they are numbered by their place.
            targets = {key: f"{folder}/{index:03d} {posixpath.basename(target)}"
                       for index, (key, target) in enumerate(sorted(targets.items()), start=1)}
        if any(len(target) > schemes.MAX_PATH for target in targets.values()):
            raise PlaceError("too_long")
        return Goal(targets, folder)
    raise PlaceError("taken")


# --- How -------------------------------------------------------------------------------------------------------------


def resolved(path: Path) -> Path | None:
    try:
        return Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


def source_file(root: Path, relative: str) -> Path:
    """The file to place, checked: a plain file, no link on the way, inside ``root``."""
    real_root = resolved(root)
    if real_root is None:
        raise PlaceError("source_changed")
    current = real_root
    for part in parts_of(relative):
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            raise PlaceError("source_changed") from None
        if stat.S_ISLNK(info.st_mode) or current.is_junction():
            raise PlaceError("link")
    if not stat.S_ISREG(os.lstat(current).st_mode):
        raise PlaceError("source_changed")
    real = resolved(current)
    if real is None or not _inside(real, real_root):
        raise PlaceError("outside")
    return current


def _make_dirs(root: Path, folder: str) -> Path:
    """Makes the folders of ``folder`` below ``root`` that are not there; a link or a file on the way is refused."""
    current = root
    for part in parts_of(folder) if folder else []:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current)
            except FileExistsError:
                pass
            except OSError as exc:
                raise PlaceError("no_write" if exc.errno in READ_ONLY else "failed") from exc
            continue
        if stat.S_ISLNK(info.st_mode) or current.is_junction():
            raise PlaceError("link")
        if not stat.S_ISDIR(info.st_mode):
            raise PlaceError("taken")
    real = resolved(current)
    real_root = resolved(root)
    if real is None or real_root is None or not _inside(real, real_root):
        raise PlaceError("outside")
    return current


def _name(source: Path, target: Path) -> None:
    """Gives ``source`` the name ``target`` and takes the old name away; never over anything."""
    try:
        os.link(source, target)
    except FileExistsError:
        raise PlaceError("taken") from None
    except OSError as exc:
        if exc.errno == errno.EROFS:
            raise PlaceError("no_write") from exc
        # No links here (a share without them): look once more, then rename.
        if os.path.lexists(target):
            raise PlaceError("taken") from exc
        try:
            os.rename(source, target)
        except FileExistsError:
            raise PlaceError("taken") from None
        except OSError as inner:
            raise PlaceError("no_write" if inner.errno in READ_ONLY else "failed") from inner
        return
    try:
        os.unlink(source)
    except OSError:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise PlaceError("failed") from None


def _copy(source: Path, partial: Path, size: int) -> None:
    if shutil.disk_usage(partial.parent).free < size + RESERVE:
        raise PlaceError("no_space")
    try:
        with open(source, "rb") as reading, open(partial, "xb") as writing:
            while True:
                piece = reading.read(COPY_CHUNK)
                if not piece:
                    break
                writing.write(piece)
            writing.flush()
            os.fsync(writing.fileno())
        shutil.copystat(source, partial, follow_symlinks=False)
    except OSError as exc:
        _remove(partial)
        if exc.errno == errno.ENOSPC:
            raise PlaceError("no_space") from exc
        raise PlaceError("no_write" if exc.errno in READ_ONLY else "failed") from exc
    if os.lstat(partial).st_size != size:
        _remove(partial)
        raise PlaceError("failed")


def _remove(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _partial(folder: Path, name: str) -> Path:
    return folder / f".{name[:60]}.{secrets.token_hex(4)}{PARTIAL}"


def place(source: Path, size: int, library: Path, target: str, *, how: str) -> str:
    """Puts ``source`` at ``target`` (relative to ``library``); ``how`` is ``link`` (keep the source) or ``move``.
    Returns what was done: ``link``, ``rename`` or ``copy``. ``PlaceError`` when it cannot."""
    try:
        info = os.lstat(source)
    except OSError:
        raise PlaceError("source_changed") from None
    if info.st_size != size:
        raise PlaceError("source_changed")
    folder = _make_dirs(library, posixpath.dirname(target))
    goal = folder / posixpath.basename(target)
    if os.path.lexists(goal):
        raise PlaceError("taken")
    if how == "link":
        try:
            os.link(source, goal)
            return "link"
        except FileExistsError:
            raise PlaceError("taken") from None
        except OSError as exc:
            if exc.errno == errno.EROFS:
                raise PlaceError("no_write") from exc
    else:
        try:
            os.link(source, goal)
        except FileExistsError:
            raise PlaceError("taken") from None
        except OSError as exc:
            if exc.errno == errno.EROFS:
                raise PlaceError("no_write") from exc
        else:
            _remove(source)
            return "rename"
    partial = _partial(folder, goal.name)
    _copy(source, partial, size)
    try:
        _name(partial, goal)
    except PlaceError:
        _remove(partial)
        raise
    if how == "move":
        _remove(source)
    return "copy"


def crosses_disks(source: Path, library: Path) -> bool:
    """Whether ``source`` lies on another disk than the library, so that placing it costs a copy (and room)."""
    try:
        return os.stat(source).st_dev != os.stat(library).st_dev
    except OSError:
        return True


def room_for(library: Path, needed: int) -> bool:
    """Whether the library's disk has ``needed`` bytes and the reserve."""
    try:
        return shutil.disk_usage(library).free >= needed + RESERVE
    except OSError:
        return False


def same_file(path: Path, size: int, digest: str = "") -> bool:
    """Whether ``path`` is a plain file of ``size`` bytes (and, when given, of that SHA-256)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
        return False
    return not digest or sha256(path) == digest


def sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            piece = handle.read(1024 * 1024)
            if not piece:
                break
            digest.update(piece)
    return digest.hexdigest()


def remove(path: Path) -> None:
    """Takes a file away (the old name of a file that was given a new one)."""
    _remove(path)


def clear_partials(folder: Path, only: str = "") -> None:
    """Removes partial files nexbooks left in ``folder`` (a filing that stopped while it copied); with ``only`` just
    those of the goal of that name (their name begins with it)."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        if (entry.name.startswith(".") and entry.name.endswith(PARTIAL) and entry.is_file(follow_symlinks=False)
                and (not only or entry.name.startswith(f".{only[:60]}."))):
            _remove(Path(entry.path))


def clear_own_partials(root: Path, target: str) -> None:
    """Removes the partial files of the goal ``target`` (relative to ``root``) in its own folder, never through a
    link: a copy that stopped for good, whose download is turned down or taken away now."""
    parent = posixpath.dirname(target)
    if parent and _kind_on_disk(root, parent) != "dir":
        return
    clear_partials(root.joinpath(*parts_of(parent)) if parent else root, only=posixpath.basename(target))


def values_of(book: dict[str, Any], meta: Any, language: str) -> dict[str, str]:
    """The values of the scheme for a book: what nexbooks knows of the book wished (its title, author, series, year),
    else what the file says."""
    authors = [str(name) for name in book.get("authors") or [] if name] or list(getattr(meta, "authors", []) or [])
    series = str(book.get("series") or "") or str(getattr(meta, "series", "") or "")
    position = book.get("position") if book.get("series") else getattr(meta, "series_pos", None)
    year = str(book.get("year") or "") or (str(meta.year) if getattr(meta, "year", None) else "")
    narrators = list(getattr(meta, "narrators", []) or [])
    title = str(book.get("title") or "") or str(getattr(meta, "title", "") or "")
    return {
        "Author": authors[0] if authors else "", "AuthorSort": _sort_name(authors[0]) if authors else "",
        "Series": series, "Volume": schemes.volume_text(position) if series else "", "Title": title,
        "Year": year[:4], "Language": language or str(getattr(meta, "language", "") or ""),
        "Narrator": narrators[0] if narrators else "",
    }


def _sort_name(name: str) -> str:
    from ..readers.names import sort_name

    try:
        return sort_name(name)
    except Exception:  # noqa: BLE001 - a name that cannot be sorted is used as it is
        return name


__all__ = ["Goal", "PlaceError", "audio_goal", "clear_own_partials", "clear_partials", "crosses_disks", "ebook_goal",
           "place", "remove", "render", "room_for", "same_file", "sha256", "source_file", "values_of"]
