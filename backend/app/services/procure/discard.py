"""An edition in the trash: a person who looks after a library takes one edition of a book off the shelf, from the page
of the book, whatever brought it there (the scan of the library, an upload, a release).

* **What happens**: the files of the edition that are on the disk go into the trash of the library (they come back from
  there; a file that is gone from the disk is named and does not hold the edition back), a line in the history says so,
  and the edition is gone from the shelf (its work too, when it was the only edition). Nothing is blocked and no wish is
  made: unlike "Falsches Buch" (``wrong.py``) the book is not said to be another one. **All or nothing**: when the files
  cannot be moved, nothing is written or removed.
* **What people had in it**: the place, the bookmarks and the finished passes of everybody who read or heard the edition
  are written down first (``kept.py``), kept with the trash entry and given back when the files come out of the trash.
* **Who**: a member with ``manage`` in the library of the edition, in a browser; another member of the library gets
  ``403``, a person who is no member ``404``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import ROLE_BOOK, Account, BookFile, Edition, ProcureHistory, Work
from .. import catalog, libraries
from . import hidden, kept, trash, wishes
from .wrong import _lock, _sidecars

logger = logging.getLogger("nexbooks.procure")


@dataclass
class Discarded:
    #: The entry of the trash the files went to; ``None`` when no file of the edition was there to move.
    trash_id: int | None
    #: The work that is left (``None``: the edition was its only one, and the work went with it).
    work_id: int | None
    #: The files of the edition that were not on the disk any more, by their names inside the library.
    missing: list[str] = field(default_factory=list)
    #: How many people had a place, a mark or a finished pass in the edition.
    people: int = 0


def discard(db: Session, account: Account, edition_id: int) -> Discarded:
    """Puts the edition in the trash as described in the module."""
    edition = db.get(Edition, edition_id)
    if edition is None:
        raise error("not_found", "Not found.", 404)
    library = libraries.require_reviewer(db, account.id, edition.library_id)
    with _lock:
        db.expire_all()
        edition = db.get(Edition, edition_id)
        if edition is None:
            raise error("not_found", "Not found.", 404)
        rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK)))
        if not rows:
            raise error("not_found", "Not found.", 404)
        own = [row for row in rows if row.missing_at is None]
        gone = [row for row in rows if row.missing_at is not None]
        work_id, kind, title = edition.work_id, edition.kind, edition.title
        author = wishes.book_text(db, account.id, work_id=work_id, foreign_id=None)[1][:300]
        states = kept.snapshot(db, edition.id)
        line = ProcureHistory(user_id=account.id, library_id=library.id, work_id=None, kind=kind,
                              book_title=title[:300], book_author=author, result="removed", created_at=clock.now())
        db.add(line)
        db.flush()
        entry = None
        if own:
            try:
                entry = trash.put(db, library, own + _sidecars(db, edition, own), kind=kind, title=title,
                                  author=author, fmt=rows[0].format, work_id=work_id, edition_id=None,
                                  download_id=None)
            except hidden.Unsafe as exc:
                db.rollback()
                raise trash.unsafe_error() from exc
            except OSError as exc:
                db.rollback()
                logger.info("The files of edition %d could not be moved to the trash", edition.id)
                raise error("trash_failed", "The files could not be moved to the trash. Nothing was changed.",
                            409) from exc
        for row in gone:
            db.delete(row)
        trash.drop_empty_folders(library, [row.path.rpartition("/")[0] for row in gone])
        note: dict[str, object] = {"code": "trashed"}
        if entry is not None:
            note["trash_id"] = entry.id
        if gone:
            note["missing"] = len(gone)
        line.note = note
        kept.keep(db, library_id=library.id, trash_id=entry.id if entry is not None else None,
                  paths=[row.path for row in rows], states=states, days=trash.days(db))
        others = db.scalar(select(func.count(Edition.id)).where(Edition.work_id == work_id,
                                                                Edition.id != edition.id)) or 0
        db.delete(edition)
        db.flush()
        work = db.get(Work, work_id)
        if work is not None:
            if others:
                catalog.refresh_work(db, work)
                line.work_id = work_id
            else:
                db.delete(work)
        db.commit()
    catalog.forget_top_authors(library.id)
    logger.info("An edition went to the trash edition=%d trash=%s missing=%d", edition_id,
                entry.id if entry is not None else "-", len(gone))
    catalog.refresh_guesses(db, library.id)
    return Discarded(trash_id=entry.id if entry is not None else None, work_id=work_id if others else None,
                     missing=[row.path for row in gone][:50], people=kept.people_in(states))


__all__ = ["Discarded", "discard"]
