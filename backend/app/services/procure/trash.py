"""The trash of the books an upgrade replaced.

* **Where**: in the library itself, in the hidden folder ``.nexbooks-trash`` (the scan leaves hidden folders alone), one
  folder per entry with the files as they lay (``<entry>/<path in the library>``). A move inside one disk costs nothing,
  and a file never travels to another disk to be thrown away.
* **What**: the files of the format that was replaced (a PDF beside the new EPUB; every part of a recording and the
  files beside it), with what they were (title, author, format) as they were, so that a line stays readable when the
  book goes.
* **How long**: ``trash_days`` of the operator's choice (``formats.config``), then the files are deleted for good, with
  the entry. Emptying it before asks first (the interface); so does deleting one entry.
* **Back**: an entry is brought back to where its files lay, as long as nothing lies there now; the scan reads them
  again as any file that came.
* **Who**: the people who look after the library (the right ``manage``) see its entries with their titles, bring them
  back and delete them. The operator has no right of his own, as everywhere with books.
* ⚠️ Every path is checked: no link on the way, nothing outside the library, nothing written over. The trash folder
  itself too (``hidden``): when it is a link, nothing goes in, nothing comes out, and nothing is deleted there.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import Account, BookFile, Library, TrashEntry
from .. import libraries
from ..readers.files import parts_of
from . import hidden, kept

logger = logging.getLogger("nexbooks.procure")

FOLDER = ".nexbooks-trash"
DEFAULT_DAYS = 30


def days(db: Session) -> int:
    from . import formats

    return int(formats.config(db).get("trash_days") or DEFAULT_DAYS)


def _real(path: Path) -> Path | None:
    try:
        return Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None


def _no_link_on_the_way(root: Path, relative: str) -> Path:
    current = root
    for part in parts_of(relative):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return root.joinpath(*parts_of(relative))
        if stat.S_ISLNK(info.st_mode) or current.is_junction():
            raise OSError("link on the way")
    return current


def _move(source: Path, target: Path) -> None:
    """``source`` to ``target``, never over anything (a link is made, then the old name goes)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        if os.path.lexists(target):
            raise FileExistsError(str(target)) from None
        os.rename(source, target)
        return
    os.unlink(source)


def _drop_empty(root: Path, relative_folder: str) -> None:
    """Removes the folders of ``relative_folder`` below ``root`` that are empty now, from the deepest up."""
    parts = relative_folder.split("/") if relative_folder else []
    while parts:
        folder = root.joinpath(*parts)
        try:
            if folder.is_symlink():
                return
            folder.rmdir()
        except OSError:
            return
        parts = parts[:-1]


def drop_empty_folders(library: Library, folders: list[str]) -> None:
    """Removes the folders (inside the library) that are empty now, each from the deepest up; never the library itself,
    never a link."""
    root = Path(library.path)
    for folder in sorted({item for item in folders if item}, key=len, reverse=True):
        _drop_empty(root, folder)


def put(db: Session, library: Library, rows: list[BookFile], *, kind: str, title: str, author: str, fmt: str,
        work_id: int | None, edition_id: int | None, download_id: int | None) -> TrashEntry:
    """Moves the files of ``rows`` into the trash of their library and forgets them (the rows go). Does not commit.
    ``OSError`` when a file could not go (what went comes back first)."""
    root = Path(library.path)
    name = secrets.token_hex(8)
    # ``hidden.Unsafe`` (an ``OSError``) when the trash folder is a link: nothing moves.
    trash = hidden.folder(root, FOLDER, make=True) / name
    moved: list[tuple[Path, Path, BookFile]] = []
    try:
        for row in rows:
            source = _no_link_on_the_way(root, row.path)
            real = _real(source)
            if real is None or not real.is_relative_to(_real(root) or root):
                raise OSError("outside")
            target = trash.joinpath(*parts_of(row.path))
            _move(source, target)
            moved.append((source, target, row))
    except OSError:
        for source, target, _row in reversed(moved):
            try:
                _move(target, source)
            except OSError:
                logger.warning("A file could not be put back after a failed move to the trash library=%s", library.id)
        raise
    files = [{"path": row.path, "size": int(row.size or 0)} for _s, _t, row in moved]
    for _source, _target, row in moved:
        db.delete(row)
    for folder in sorted({row.path.rsplit("/", 1)[0] for row in rows if "/" in row.path}, key=len, reverse=True):
        _drop_empty(root, folder)
    moment = clock.now()
    entry = TrashEntry(library_id=library.id, work_id=work_id, edition_id=edition_id, kind=kind, title=title[:300],
                       author=author[:300], format=fmt[:12], files=files, folder=name,
                       size=sum(item["size"] for item in files), download_id=download_id, created_at=moment,
                       expires_at=moment + timedelta(days=days(db)))
    db.add(entry)
    db.flush()
    logger.info("Files went to the trash library=%s entry=%s files=%s", library.id, entry.id, len(files))
    return entry


def _folder_of(entry: TrashEntry, library: Library) -> Path:
    """The folder of an entry; ``hidden.Unsafe`` when the trash folder of the library is a link."""
    if not entry.folder or not entry.folder.isalnum():
        raise OSError("not a folder of the trash")
    return hidden.folder(library.path, FOLDER) / entry.folder


def _remove_folder(folder: Path) -> None:
    if not hidden.holds(folder, FOLDER):
        return
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.parent.rmdir()
    except OSError:
        pass


def delete(db: Session, entry: TrashEntry) -> None:
    """The entry and its files go for good. Does not commit. ``hidden.Unsafe`` when the trash folder of its library is a
    link: then nothing is deleted and the entry stays."""
    library = db.get(Library, entry.library_id)
    if library is not None:
        try:
            folder = _folder_of(entry, library)
        except hidden.Unsafe:
            raise
        except OSError:
            folder = None
        if folder is not None:
            _remove_folder(folder)
    db.delete(entry)


def unsafe_error() -> Exception:
    return error("hidden_folder_link", "A hidden folder of nexbooks in this library is a link. nexbooks leaves it "
                                       "alone.", 409)


def restore(db: Session, entry: TrashEntry) -> list[str]:
    """Puts the files back where they lay; ``taken`` (409) when anything lies there now. Asks the library to be read.
    Returns the paths. Does not commit."""
    library = db.get(Library, entry.library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    root = Path(library.path)
    try:
        folder = _folder_of(entry, library)
    except hidden.Unsafe as exc:
        raise unsafe_error() from exc
    paths = [str(item.get("path") or "") for item in entry.files or [] if isinstance(item, dict)]
    for path in paths:
        if os.path.lexists(_no_link_on_the_way(root, path)):
            raise error("trash_taken", "Something lies where the files lay.", 409)
    done: list[tuple[Path, Path]] = []
    try:
        for path in paths:
            source = folder.joinpath(*parts_of(path))
            target = _no_link_on_the_way(root, path)
            _move(source, target)
            done.append((target, source))
    except OSError as exc:
        for target, source in reversed(done):
            try:
                _move(target, source)
            except OSError:
                pass
        raise error("trash_taken", "The files could not be put back.", 409) from exc
    _remove_folder(folder)
    # What the people had in the book goes on waiting for the scan; the entry is about to go.
    kept.leave_trash(db, entry.id)
    db.delete(entry)
    from .. import jobs

    jobs.enqueue(db, "scan", ref=library.id)
    logger.info("Files came back from the trash library=%s files=%s", library.id, len(paths))
    return paths


def sweep(db: Session) -> int:
    """Deletes the entries whose time is over. Returns how many."""
    gone = 0
    for entry in list(db.scalars(select(TrashEntry).where(TrashEntry.expires_at <= clock.now()).limit(200))):
        try:
            delete(db, entry)
        except hidden.Unsafe:
            continue
        gone += 1
    if kept.sweep(db):
        db.commit()
    if gone:
        db.commit()
        logger.info("Trash entries deleted after their time count=%s", gone)
    return gone


def _managed(uid: int) -> Any:
    from ...models import LibraryMember

    return select(LibraryMember.library_id).where(LibraryMember.user_id == uid, LibraryMember.right == libraries.MANAGE)


def listing(db: Session, account: Account) -> list[TrashEntry]:
    return list(db.scalars(select(TrashEntry).where(TrashEntry.library_id.in_(_managed(account.id)))
                           .order_by(TrashEntry.created_at.desc(), TrashEntry.id.desc()).limit(500)))


def get(db: Session, account: Account, entry_id: int) -> TrashEntry:
    row = db.scalar(select(TrashEntry).where(TrashEntry.id == entry_id,
                                             TrashEntry.library_id.in_(_managed(account.id))))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def empty(db: Session, account: Account) -> int:
    """Deletes every entry of the libraries the person looks after (not of a library whose trash folder is a link).
    Returns how many; ``403`` for a person who looks after no library (there is no trash of theirs to empty)."""
    if db.scalar(_managed(account.id).limit(1)) is None:
        raise error("forbidden", "You look after no library.", 403)
    gone = 0
    for row in listing(db, account):
        try:
            delete(db, row)
        except hidden.Unsafe:
            continue
        gone += 1
    db.commit()
    return gone


__all__ = ["FOLDER", "days", "delete", "drop_empty_folders", "empty", "get", "listing", "put", "restore", "sweep"]
