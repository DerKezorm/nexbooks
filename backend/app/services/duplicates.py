"""Editions a library holds twice: the same recording in two folders, a box whose folder holds single volumes the
library has as well. Those are **a hint**: nothing is joined, moved or removed for them; the people who look after the
library find a note under "Braucht dich" and decide (the page of the book puts an edition in the trash).

**One thing goes by itself** (``tidy``), only in a library nexbooks may write in (the operator switched tidying on, or
the library is where fetched books go): an edition that holds a file twice, the same to the byte (by its checksum, read
and unchanged since). There is nothing to decide about it: one copy stays, the others go to the trash of the library,
from where they can be taken back, and the edition is put together anew (a recording whose files all lie there twice
ran twice as long). Between two editions nothing goes: which book it is, is a person's call.

Two editions of one kind in one library are taken for one held twice when

* **their files are the same** (by their checksums): the files of the one are all among the files of the other
  (``same_files``, or ``contains`` when the other has more: a box with the volume in it), whatever book each is under;
  one edition whose files hold one file twice, or an e-book with two files of one format (the scan puts two copies
  of an e-book from two folders under one edition, ``same_format``), is one too;
* or, for two editions of **one work**, their running times are within ``RUNTIME_SLACK`` seconds of each other
  (``runtime``, recordings), or **their files are as large** to the byte, from ``SIZE_MIN`` on (``size``).

A note is made once for a pair, for every member with ``manage``. One that was read stays read (``detail.read``); a
note whose pair is no pair any more (an edition went) goes.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import ROLE_BOOK, BookFile, Credit, Edition, Library, LibraryMember, Person, ProcureNotice
from .readers.text import fold

logger = logging.getLogger("nexbooks.catalog")

CODE = "duplicate"
#: Two recordings of one work whose running times differ by this many seconds or less are taken for one.
RUNTIME_SLACK = 1
#: Files smaller than this are not compared by their size (two small files are as large by chance).
SIZE_MIN = 64 * 1024
#: Not more pairs than this are looked at in one library (a library of copies is told by the first ones).
MAX_PAIRS = 500
#: Not more editions than this are tidied in one run: the rest follows with the next.
TIDY_MAX = 50
#: ``Titel (2).epub``: what a program makes of a copy (``Titel (2011).epub`` is a year).
_COPY_NAME = re.compile(r"\(\d{1,2}\)(\.\w+)?$")


def _folder(paths: list[str]) -> str:
    first = min(paths) if paths else ""
    return first.rpartition("/")[0][:300]


def pairs(db: Session, library_id: int) -> dict[tuple[int, int], tuple[int, str]]:
    """``(one, other) -> (the edition the note is about, why)`` of the library, ``one < other`` (``one == other`` for
    an edition that holds one file twice)."""
    editions = {row.id: row for row in db.scalars(select(Edition).where(Edition.library_id == library_id,
                                                                      Edition.missing_at.is_(None)))}
    sums: dict[int, set[str]] = defaultdict(set)
    sizes: dict[int, int] = defaultdict(int)
    unhashed: set[int] = set()
    twice: set[int] = set()
    formats: dict[int, list[str]] = defaultdict(list)
    for edition_id, sha, size, fmt in db.execute(select(BookFile.edition_id, BookFile.sha256, BookFile.size,
                                                        BookFile.format).where(
            BookFile.library_id == library_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))):
        if edition_id not in editions:
            continue
        formats[edition_id].append(fmt)
        if sha:
            if sha in sums[edition_id]:
                twice.add(edition_id)
            sums[edition_id].add(sha)
        else:
            unhashed.add(edition_id)
        sizes[edition_id] += int(size or 0)
    found: dict[tuple[int, int], tuple[int, str]] = {}

    def add(a: int, b: int, about: int, why: str) -> None:
        key = (min(a, b), max(a, b))
        if key not in found and len(found) < MAX_PAIRS:
            found[key] = (about, why)

    for edition_id in sorted(twice):
        add(edition_id, edition_id, edition_id, "same_files")
    # An e-book with two files of one format (two copies under one edition, from two folders): a recording is made of
    # many files of one format, and is no such case.
    for edition_id, kinds in sorted(formats.items()):
        if editions[edition_id].kind == "ebook" and len(kinds) != len(set(kinds)):
            add(edition_id, edition_id, edition_id, "same_format")
    # The same files, under whatever book. Every walk ends when the library has its pairs: a library of copies would
    # otherwise cost the square of its editions on every scan.
    for edition_id, other in _sharing_files(editions, sums, unhashed):
        if len(found) >= MAX_PAIRS:
            return found
        if sums[other] == sums[edition_id]:
            add(edition_id, other, max(edition_id, other), "same_files")
        else:
            add(edition_id, other, other, "contains")
    # One work, one kind: the running time, the size.
    by_work: dict[tuple[int, str], list[Edition]] = defaultdict(list)
    for edition in editions.values():
        by_work[(edition.work_id, edition.kind)].append(edition)
    for (_work, kind), group in by_work.items():
        for one, other in _as_good_as_equal(group, kind, sizes):
            if len(found) >= MAX_PAIRS:
                return found
            if kind == "audio" and one.duration and other.duration and abs(
                    one.duration - other.duration) <= RUNTIME_SLACK:
                add(one.id, other.id, other.id, "runtime")
            else:
                add(one.id, other.id, other.id, "size")
    return found


def _sharing_files(editions: dict[int, Edition], sums: dict[int, set[str]],
                   unhashed: set[int]) -> Iterator[tuple[int, int]]:
    """``(one, other)`` for two editions of one kind whose files are all among the files of ``other`` (by their
    checksums), ``one`` and ``other`` any two ids. Kept by kind, so that no edition looks at those it can never be a
    pair with."""
    by_sum: dict[tuple[str, str], set[int]] = defaultdict(set)
    for edition_id, shas in sums.items():
        if edition_id not in unhashed:
            for sha in shas:
                by_sum[(editions[edition_id].kind, sha)].add(edition_id)
    for edition_id, shas in sorted(sums.items()):
        if edition_id in unhashed or not shas:
            continue
        kind = editions[edition_id].kind
        for other in sorted(set.intersection(*(by_sum[(kind, sha)] for sha in shas)) - {edition_id}):
            yield edition_id, other


def _as_good_as_equal(group: list[Edition], kind: str, sizes: dict[int, int]) -> Iterator[tuple[Edition, Edition]]:
    """The pairs ``(one, other)`` of editions of one work and one kind with ``one.id < other.id`` whose running times
    are within ``RUNTIME_SLACK`` of each other (recordings) or whose files are as large to the byte, from ``SIZE_MIN``
    on. Found by sorting and grouping, not by looking at every pair: a work with thousands of editions would cost the
    square of them."""
    group.sort(key=lambda e: e.id)
    by_size: dict[int, list[Edition]] = defaultdict(list)
    for edition in group:
        if sizes.get(edition.id, 0) >= SIZE_MIN:
            by_size[sizes[edition.id]].append(edition)
    timed = sorted((e for e in group if kind == "audio" and e.duration), key=lambda e: (e.duration, e.id))
    durations = [e.duration for e in timed]
    for one in group:
        near: dict[int, Edition] = {}
        if sizes.get(one.id, 0) >= SIZE_MIN:
            near.update((e.id, e) for e in by_size[sizes[one.id]] if e.id > one.id)
        if timed and one.duration:
            low, high = bisect_left(durations, one.duration - RUNTIME_SLACK), bisect_right(
                durations, one.duration + RUNTIME_SLACK)
            near.update((e.id, e) for e in timed[low:high] if e.id > one.id)
        for ident in sorted(near):
            yield one, near[ident]


def _stays_first(path: str, title: str) -> tuple[bool, bool, str]:
    """The order in which copies of a file stay: the one not named as a copy, the one in the folder of the book, then
    by name, so that of a recording that lies there twice one whole set stays and not a mix of the two."""
    folder, _, name = path.rpartition("/")
    return bool(_COPY_NAME.search(name)), fold(title) not in fold(folder), path


def surplus(db: Session, library_id: int) -> dict[int, list[BookFile]]:
    """The files of each edition that are another copy of a file of the same edition, to the byte."""
    groups: dict[tuple[int, str, int], list[BookFile]] = defaultdict(list)
    for row in db.scalars(select(BookFile).where(
            BookFile.library_id == library_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
            BookFile.dirty.is_(False), BookFile.edition_id.is_not(None))):
        if row.sha256:
            groups[(int(row.edition_id or 0), row.sha256, int(row.size or 0))].append(row)
    found: dict[int, list[BookFile]] = defaultdict(list)
    editions = {row.id: row for row in db.scalars(select(Edition).where(Edition.library_id == library_id))}
    twice: dict[int, list[int]] = defaultdict(list)
    for (edition_id, _sha, _size), rows in groups.items():
        twice[edition_id].append(len(rows))
    for (edition_id, _sha, _size), rows in groups.items():
        edition = editions.get(edition_id)
        if len(rows) < 2 or edition is None:
            continue
        # A recording can hold the same part more than once (the jingle of every disc, a silence): it goes only when
        # all of it lies there twice, side by side in one folder; two discs that look alike stay and are only named.
        if edition.kind == "audio" and (len(twice[edition_id]) < 2 or set(twice[edition_id]) != {2}
                                        or len({row.path.rpartition("/")[0] for row in rows}) != 1):
            continue
        rows.sort(key=lambda row: _stays_first(row.path, edition.title))
        found[edition_id].extend(rows[1:])
    return found


def _on_disk(library: Library, row: BookFile) -> bool:
    path = Path(library.path, *row.path.split("/"))
    return path.is_file() and path.stat().st_size == int(row.size or 0)


def _author_of(db: Session, edition_id: int) -> str:
    return db.scalar(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
        Credit.edition_id == edition_id, Credit.role == "author").order_by(Credit.position)) or ""


def may_write(db: Session, library_id: int) -> bool:
    """Whether nexbooks may write in this library by its own settings: the operator switched tidying on (it is for every
    library), or the library is where fetched books go (Settings, Formats and places). Elsewhere nexbooks only reads,
    which is what it promises, even when the folder is mounted so that it could write."""
    from . import organize
    from .procure import formats

    if organize.enabled(db):
        return True
    return library_id in {ident for ident in formats.config(db)["library"].values() if ident is not None}


def tidy(db: Session, library_id: int) -> int:
    """The copies of a file within an edition go to the trash, and the edition is put together anew; only in a library
    nexbooks may write in (``may_write``), else the copies stay and ``tell`` names them like any other. One that cannot
    go stays, and its note says so. Commits each edition. Returns how many files went."""
    from .match import apply
    from .procure import trash

    library = db.get(Library, library_id)
    if library is None or not may_write(db, library_id):
        return 0
    gone = 0
    for edition_id, rows in sorted(surplus(db, library_id).items())[:TIDY_MAX]:
        edition = db.get(Edition, edition_id)
        if edition is None:
            continue
        leaving = {row.id for row in rows}
        twins = {row.sha256 for row in rows}
        staying = [row for row in db.scalars(select(BookFile).where(
            BookFile.edition_id == edition_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
            BookFile.sha256.in_(twins))) if row.id not in leaving]
        # (Before anything moves: the copy that stays is there, and as large as the book says.)
        if {row.sha256 for row in staying} != twins or not all(_on_disk(library, row) for row in staying):
            logger.warning("Copies in an edition were left, the file to stay is not there edition=%d", edition_id)
            continue
        count = len(rows)
        try:
            trash.put(db, library, rows, kind=edition.kind, title=edition.title, author=_author_of(db, edition_id),
                      fmt=rows[0].format, work_id=edition.work_id, edition_id=edition_id, download_id=None)
            db.flush()
            apply.rebuild(db, edition)
            db.commit()
        except OSError:
            db.rollback()
            logger.warning("Copies in an edition could not go to the trash edition=%d", edition_id)
            continue
        gone += count
        logger.info("Copies in an edition went to the trash edition=%d files=%d", edition_id, count)
    return gone


def tell(db: Session, library_id: int) -> int:
    """Makes the notes of the pairs of the library that have none yet, and takes away the notes of pairs that are no
    pairs any more. Does not commit. Returns how many notes were made."""
    found = pairs(db, library_id)
    ids = list({ident for pair in found for ident in pair})
    managers = list(db.scalars(select(LibraryMember.user_id).where(LibraryMember.library_id == library_id,
                                                                   LibraryMember.right == "manage")))
    existing: dict[tuple[int, tuple[int, int]], ProcureNotice] = {}
    for notice in db.scalars(select(ProcureNotice).join(Edition, Edition.id == ProcureNotice.edition_id).where(
            ProcureNotice.code == CODE, Edition.library_id == library_id)):
        detail: Any = notice.detail if isinstance(notice.detail, dict) else {}
        pair = detail.get("pair")
        key = tuple(pair) if isinstance(pair, list) and len(pair) == 2 else None
        if key is None or key not in found:
            db.delete(notice)
            continue
        existing[(notice.user_id, key)] = notice  # type: ignore[index]
    editions = {row.id: row for row in db.scalars(select(Edition).where(Edition.id.in_(ids)))} if ids else {}
    paths: dict[int, list[str]] = defaultdict(list)
    copies: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    if ids:
        for edition_id, path, sha, fmt in db.execute(select(BookFile.edition_id, BookFile.path, BookFile.sha256,
                                                            BookFile.format).where(
                BookFile.edition_id.in_(ids), BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))):
            paths[edition_id].append(path)
            copies[edition_id][f"sha:{sha}" if sha else f"path:{path}"].append(path)
            copies[edition_id][f"format:{fmt}"].append(path)
    made = 0
    now = clock.now()
    for key, (about, why) in found.items():
        other = key[0] if about == key[1] else key[1]
        here, there = editions.get(about), editions.get(other)
        if here is None or there is None:
            continue
        if key[0] == key[1]:
            # One edition with one file twice: the folders of the two copies (the same file, else the same format).
            kind = "sha:" if why == "same_files" else "format:"
            twin = sorted(next((found for name, found in copies[about].items()
                                if name.startswith(kind) and len(found) >= 2), ["", ""]))
            places = (_folder(twin[:1]), _folder(twin[1:2]))
        else:
            places = (_folder(paths[about]), _folder(paths[other]))
        for user_id in managers:
            if (user_id, key) in existing:
                continue
            db.add(ProcureNotice(user_id=user_id, edition_id=about, code=CODE, created_at=now, detail={
                "pair": list(key), "why": why, "title": here.title[:300], "other_title": there.title[:300],
                "other_edition_id": other, "other_work_id": there.work_id, "here": places[0], "there": places[1]}))
            made += 1
    db.flush()
    if made:
        logger.info("Editions held twice told library=%s notes=%s", library_id, made)
    return made


__all__ = ["CODE", "pairs", "surplus", "tell", "tidy"]
