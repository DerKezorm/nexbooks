"""Judging an open question again when the rules of the check have changed.

A download that waits under "Braucht dich" with a question (``importing.QUESTIONS``) carries the verdict of the code of
its day. When a rule changes (``checking.CHECK_REV``) the card would stay wrong: the question says 70 minutes where the
books are 7 hours and 28 minutes, and a person blocks a good release because of it. So the planner takes the open
questions whose generation (``Download.check_rev``) is older, a few at a time, and lets each go through **the same check
as a fresh download**: the files it has are put together again into books (``catalog.unit_key``), the running time is
worked out and held against the catalogue anew, and what follows is what always follows: the book is filed, the release
is turned down and blocked and the wish searches on, or the question is asked again with the numbers of today. There is
no way of its own.

* What is not there stays as it is: a download whose files have gone keeps its card unchanged (and is not asked again
  until the rules change once more).
* Only a question that is open is judged again. What a person decided is not a question any more; an upload keeps its
  rules, and so does a recording whose version a person set.
* A download is claimed with one conditional update (``state`` still ``problem``, generation still older): two runs at
  the same moment, or a person answering at that moment, can never both have it, so nothing is filed twice.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ... import clock
from ...models import ROLE_BOOK, Download, Library, PlaceFile
from . import checking, importing

logger = logging.getLogger("nexbooks.procure")

#: How many open questions one run takes: the check reads no file again, but a filing may follow.
PORTION = 3


def candidates(db: Session, limit: int = PORTION) -> list[int]:
    """The downloads with an open question that the rules of today have not judged, the oldest first."""
    return list(db.scalars(
        select(Download.id).where(
            Download.state == "problem", Download.problem_code.in_(tuple(importing.QUESTIONS)),
            Download.check_rev < checking.CHECK_REV, Download.protocol != "upload")
        .order_by(Download.id).limit(limit)))


def _files_there(db: Session, row: Download, library: Library) -> bool:
    """Whether the files the judgement stands on are still where they were read: every book file, in the folder of the
    download or in the one nexbooks unpacked its archives into."""
    items = list(db.scalars(select(PlaceFile).where(
        PlaceFile.download_id == row.id, PlaceFile.role == ROLE_BOOK, PlaceFile.probe.is_not(None))))
    if not items:
        return False
    try:
        for origin in {item.origin for item in items}:
            if origin == "source" and not row.source_path:
                return False
            root = importing._root_of(row, library, origin)
            if not root.is_dir():
                return False
            for item in items:
                if item.origin == origin and not Path(root, *item.path.split("/")).is_file():
                    return False
    except importing.Stop:
        return False
    return True


def _claim(db: Session, row: Download) -> bool:
    """Takes the download for the check: from a question to a download that is checked again. One conditional update, so
    that a person answering, or another run, at the same moment leaves it to whoever came first."""
    moment = clock.now()
    taken = db.execute(
        update(Download)
        .where(Download.id == row.id, Download.state == "problem", Download.check_rev < checking.CHECK_REV,
               Download.problem_code.in_(tuple(importing.QUESTIONS)))
        .values(state="completed", problem_code="", problem_values=None, place_step="",
                check_rev=checking.CHECK_REV, updated_at=moment))
    db.commit()
    return bool(taken.rowcount)


def _note(db: Session, row: Download) -> None:
    """The generation is written down for a download that is left as it is, so that it is not looked at again."""
    db.execute(update(Download).where(Download.id == row.id, Download.state == "problem",
                                      Download.check_rev < checking.CHECK_REV).values(check_rev=checking.CHECK_REV))
    db.commit()


def run(db: Session, limit: int = PORTION) -> int:
    """Judges a few open questions again. Returns how many were handed to the check."""
    handed = 0
    for download_id in candidates(db, limit):
        row = db.get(Download, download_id)
        if row is None or row.state != "problem":
            continue
        before = row.check_rev
        code = row.problem_code
        library = db.get(Library, row.library_id) if row.library_id is not None else None
        if (row.decision or {}).get("variant"):
            _note(db, row)
            logger.info("Download %d keeps its question code=%s: the version of the recording was set", row.id, code)
            continue
        if library is None or not _files_there(db, row, library):
            _note(db, row)
            logger.info("Download %d keeps its question code=%s: its files are not there", row.id, code)
            continue
        if not _claim(db, row):
            continue
        db.refresh(row)
        importing.request(db, row)
        db.commit()
        handed += 1
        logger.info("Download %d is judged again by the rules of version %d (was %d) code=%s", row.id,
                    checking.CHECK_REV, before, code)
    return handed
