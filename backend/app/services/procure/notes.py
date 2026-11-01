"""The notes the filing leaves one person: ``upgrade_position``, "your place in the e-book was carried
over by its share only" (a PDF became an EPUB). **The person's own**, like the place it speaks of: every query names
the person, nobody else reads them, the operator included.

* ``acknowledge``: the note is read and goes (a note of an edition held twice, ``duplicates.py``, stays as read).
* ``restore``: "Altes Format zurückholen". The old format comes back from the trash, the new one goes into it, the
  person's place is what it was before, and the wish for a better format of the book ends (it would fetch the format
  again). Asks first in the interface; needs the right ``acquire`` in the library (the files of the library change for
  everybody).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...errors import error
from ...models import ROLE_BOOK, Account, BookFile, Edition, ProcureNotice, ProcureWish, Progress, TrashEntry
from .. import libraries, works
from . import trash

logger = logging.getLogger("nexbooks.procure")


def listing(db: Session, account: Account) -> list[tuple[ProcureNotice, Edition]]:
    """The person's notes of editions they still see, the newest first."""
    rows = db.execute(select(ProcureNotice, Edition).join(Edition, Edition.id == ProcureNotice.edition_id).where(
        ProcureNotice.user_id == account.id, *works.visible_edition_clause(account.id))
        .order_by(ProcureNotice.id.desc()).limit(400)).all()
    # (A note of an edition held twice that was read stays, so that it is not made again: it is not shown.)
    return [(notice, edition) for notice, edition in rows
            if not (isinstance(notice.detail, dict) and notice.detail.get("read"))][:200]


def get(db: Session, account: Account, notice_id: int) -> ProcureNotice:
    row = db.scalar(select(ProcureNotice).where(ProcureNotice.id == notice_id, ProcureNotice.user_id == account.id))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def acknowledge(db: Session, account: Account, notice_id: int) -> None:
    notice = get(db, account, notice_id)
    if notice.code == "duplicate":
        # Read, and kept as read: the next scan would make it again otherwise.
        notice.detail = {**(notice.detail if isinstance(notice.detail, dict) else {}), "read": True}
    else:
        db.delete(notice)
    db.commit()


def drop_copy(db: Session, account: Account, notice_id: int, folder: str) -> None:
    """Of an edition that holds two files of one format, the ones in ``folder`` go to the trash and the note goes (a
    member with ``manage``). The edition stays with the other folder; the two folders are the ones the note names."""
    from ..match import apply

    notice = get(db, account, notice_id)
    detail: dict[str, Any] = notice.detail if isinstance(notice.detail, dict) else {}
    edition = db.get(Edition, notice.edition_id) if notice.edition_id is not None else None
    places = (detail.get("here"), detail.get("there"))
    if notice.code != "duplicate" or detail.get("why") != "same_format" or edition is None:
        raise error("not_found", "Not found.", 404)
    if folder not in places or places[0] == places[1]:
        raise error("invalid_input", "This folder is not one of the two.", 422, field="folder")
    library = libraries.require_reviewer(db, account.id, edition.library_id)
    rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK,
                                                  BookFile.missing_at.is_(None))))
    leaving = [row for row in rows if row.path.rpartition("/")[0] == folder]
    if not leaving or len(leaving) == len(rows):
        raise error("not_found", "Not found.", 404)
    try:
        trash.put(db, library, leaving, kind=edition.kind, title=edition.title, author=str(detail.get("author") or ""),
                  fmt=leaving[0].format, work_id=edition.work_id, edition_id=edition.id, download_id=None)
    except OSError as exc:
        db.rollback()
        raise error("trash_failed", "The files could not be moved to the trash. Nothing was changed.", 409) from exc
    db.delete(notice)
    db.flush()
    apply.rebuild(db, edition)
    db.commit()
    logger.info("A copy of an edition went to the trash by hand edition=%d files=%d", edition.id, len(leaving))


def restore(db: Session, account: Account, notice_id: int) -> None:
    """The old format back, the new one into the trash, the person's place as it was (see the top)."""
    notice = get(db, account, notice_id)
    edition = db.get(Edition, notice.edition_id) if notice.edition_id is not None else None
    entry = db.get(TrashEntry, notice.trash_id) if notice.trash_id is not None else None
    if edition is None or entry is None:
        raise error("trash_gone", "The old format is no longer in the trash.", 409)
    library = libraries.require_acquirer(db, account.id, edition.library_id)
    detail: dict[str, Any] = notice.detail if isinstance(notice.detail, dict) else {}
    new_format = str(detail.get("to") or "")
    newer = [row for row in db.scalars(select(BookFile).where(
        BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None)))
        if row.format == new_format]
    trash.restore(db, entry)
    if newer:
        trash.put(db, library, newer, kind=edition.kind, title=entry.title, author=entry.author, fmt=new_format,
                  work_id=edition.work_id, edition_id=edition.id, download_id=notice.download_id)
    old = detail.get("old") if isinstance(detail.get("old"), dict) else None
    place = db.get(Progress, (account.id, edition.id))
    if place is not None and old is not None:
        place.locator = str(old.get("locator") or "")[:1000]
        place.koreader = ""
        place.kobo = ""
        place.unit = str(old.get("unit") or "chapter")[:8]
        place.chapter = int(old.get("chapter") or 0)
        place.chapter_count = int(old.get("chapter_count") or 0)
        place.chapter_title = str(old.get("chapter_title") or "")[:300]
    # The wish for a better format of this book in this library ends: it would fetch the format again.
    db.execute(delete(ProcureWish).where(ProcureWish.work_id == edition.work_id, ProcureWish.kind == edition.kind,
                                         ProcureWish.library_id == edition.library_id, ProcureWish.state == "upgrade"))
    others = db.scalars(select(ProcureNotice).where(ProcureNotice.trash_id == entry.id))
    for other in others:
        if other.id != notice.id:
            other.trash_id = None
    db.delete(notice)
    db.commit()
    logger.info("An old format came back from the trash library=%s edition=%s", library.id, edition.id)


__all__ = ["acknowledge", "get", "listing", "restore"]
