"""The photos that lie in the authors' folders: ``author.jpg`` or ``folder.jpg`` (also ``.jpeg``,
``.png``, ``.webp``) in the folder an author's books are in.

**Read, never written.** A job ``portraits`` (``ref`` is the library) looks at the folders directly below the
library's folder, one level, links never followed: a folder whose name is an author nexbooks has books of in
this library takes the picture that lies in it. ``author.*`` wins over ``folder.*``; ``folder.*`` counts only in
a folder that holds no book file itself (in a folder with books it is the cover of those, which the scan reads
as such). The picture is opened below the library's folder only (``readers/files.open_below``), held to a size,
and drawn anew (``readers/images.reduce_portrait``).

The photo is kept **per library** (``PersonPhoto``): a person is shown the photo of a library they are a member
of and of no other. A picture that did not change (same file, size and time) is not read again; a picture that
is gone from the folder goes from the database at the next run, unless the folder could not be listed (then
nothing is taken away).

A folder called by another name of the author (a pen name proved by a source, ``services/aliases.py``) counts for
the person too, after the folder called by the person's own name: the photo of the person's own folder wins.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AUTHOR, ROLE_BOOK, BookFile, Credit, Edition, Library, Person, PersonAlias, PersonPhoto
from . import jobs, photos
from .readers.files import is_link, open_below
from .readers.images import reduce_portrait
from .readers.limits import COVER_MAX_BYTES, ReadError
from .readers.names import person_key
from .scanner import MAX_ENTRIES, SKIP_DIR_PREFIXES, SKIP_DIRS, _directly_in, valid_name

logger = logging.getLogger("nexbooks.people")

KIND = "portraits"
#: The names (lower case, without the ending) of a photo of the author, the one that is certain first.
STEMS = ("author", "folder")
SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def _candidates(folder: str) -> list[tuple[str, os.stat_result]]:
    """The files of a folder that can be the photo of its author, best first, as ``(name, lstat)``."""
    found: dict[tuple[int, int], tuple[str, os.stat_result]] = {}
    try:
        with os.scandir(folder) as entries:
            listing = list(entries)
    except OSError:
        return []
    for entry in listing[:MAX_ENTRIES]:
        stem, suffix = os.path.splitext(entry.name.casefold())
        if stem not in STEMS or suffix not in SUFFIXES or not valid_name(entry.name):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_size > COVER_MAX_BYTES:
            continue
        found[(STEMS.index(stem), SUFFIXES.index(suffix))] = (entry.name, info)
    return [found[key] for key in sorted(found)]


def _authors_here(db: Session, library_id: int) -> tuple[dict[str, int], dict[str, int]]:
    """The authors nexbooks has books of in the library, by their key: ``{person_key: person_id}``, and by the keys of
    their other names (pen names)."""
    rows = db.execute(
        select(Person.key, Person.id).join(Credit, Credit.person_id == Person.id)
        .join(Edition, Edition.id == Credit.edition_id)
        .where(Credit.role == AUTHOR, Edition.library_id == library_id).distinct())
    own = {key: ident for key, ident in rows}
    other = {key: ident for key, ident in db.execute(
        select(PersonAlias.key, PersonAlias.person_id).where(PersonAlias.person_id.in_(set(own.values()))))
        if key not in own} if own else {}
    return own, other


def run(db: Session, library: Library) -> dict[str, int]:
    """Reads the photos of one library. Returns ``{"found", "kept", "removed"}``."""
    report = {"found": 0, "kept": 0, "removed": 0}
    root = Path(library.path)
    try:
        with os.scandir(root) as entries:
            listing = list(entries)
    except OSError:
        return report
    own, other = _authors_here(db, library.id)
    authors = {**other, **own}
    known = {row.person_id: row for row in db.scalars(select(PersonPhoto).where(PersonPhoto.library_id == library.id))}
    seen: set[int] = set()
    # The folders called by a person's own name first: their photo wins over one in a folder of another name.
    for entry in sorted(listing[:MAX_ENTRIES], key=lambda item: (person_key(item.name)[:200] not in own, item.name)):
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        name = entry.name
        if name.startswith(SKIP_DIR_PREFIXES) or name.casefold() in SKIP_DIRS or not valid_name(name):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            continue
        person_id = authors.get(person_key(name)[:200])
        if person_id is None or person_id in seen:
            continue
        # A photo called "folder" is the author's only in a folder that holds no book of its own.
        holds_books = db.scalar(select(BookFile.id).where(
            BookFile.library_id == library.id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
            _directly_in(name)).limit(1)) is not None
        for file_name, stat_result in _candidates(entry.path):
            if holds_books and os.path.splitext(file_name.casefold())[0] == "folder":
                continue
            relative = f"{name}/{file_name}"
            row = known.get(person_id)
            if row is not None and (row.path, row.size, row.mtime_ns) == (relative, stat_result.st_size,
                                                                         stat_result.st_mtime_ns) \
                    and photos.path_of(row.photo) is not None:
                seen.add(person_id)
                report["kept"] += 1
                break
            made = _read(library, relative)
            if made is None:
                continue
            stored = photos.save(made)
            if row is None:
                db.add(PersonPhoto(person_id=person_id, library_id=library.id, photo=stored, path=relative,
                                   size=stat_result.st_size, mtime_ns=stat_result.st_mtime_ns))
            else:
                row.photo, row.path, row.size, row.mtime_ns = stored, relative, stat_result.st_size, \
                    stat_result.st_mtime_ns
            seen.add(person_id)
            report["found"] += 1
            break
        db.flush()
    for person_id, row in known.items():
        if person_id not in seen:
            db.delete(row)
            report["removed"] += 1
    db.commit()
    from . import people_info

    people_info.sweep(db)
    return report


def _read(library: Library, relative: str) -> bytes | None:
    """The photo at ``relative`` below the library's folder, drawn anew; None when it cannot be read."""
    try:
        with open_below(library.path, relative) as handle:
            data = handle.read(COVER_MAX_BYTES + 1)
    except (ReadError, OSError):
        return None
    return reduce_portrait(data) if len(data) <= COVER_MAX_BYTES else None


def portraits_job(db: Session, job: Any) -> None:
    """The handler of the job ``portraits``: the library ``job.ref``."""
    library = db.get(Library, job.ref)
    if library is None:
        return
    report = run(db, library)
    logger.info("Photos in the authors' folders library=%s found=%s kept=%s removed=%s", library.id, report["found"],
                report["kept"], report["removed"])


jobs.register(KIND, portraits_job)
