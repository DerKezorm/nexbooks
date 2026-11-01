"""The list "for later": books on the person's own shelves they mean to read or hear, for the apps (``/api/app/1``).

The wish list is for books that are not on a shelf; this one is for books that are.

**It is the person's own.** Every query here names the person; there is no function that reads the list of somebody
else, so the operator sees no more of it than anybody does. An entry is shown only while the person sees the work (a
library they left, a file that is gone): it stays in the database and comes back with the work, as in the history.

**It follows the book.** When the work of an entry loses its last edition to another work (two works joined, by the scan
or by hand) a trigger on the editions takes the entry along (``models.LATER_STATEMENTS``), so that whatever moves
editions is caught. An entry that is already on the list of that person for the other work stays the one.

**A book that is finished leaves the list**, in the transaction that enters it, whichever device reported it: by the
rule at the end of the book or by hand (``history``).
"""

from __future__ import annotations

from sqlalchemy import delete, exists, func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import Edition, LaterEntry
from . import works

#: How many entries a person may hold, the ones they do not see at the moment included.
MAX_ENTRIES = 500


def _seen(uid: int) -> object:
    return exists(select(Edition.id).where(Edition.work_id == LaterEntry.work_id, *works.visible_edition_clause(uid)))


def listing(db: Session, uid: int) -> list[LaterEntry]:
    """The person's own entries for the works they see, the newest first."""
    return list(db.scalars(select(LaterEntry).where(LaterEntry.user_id == uid, _seen(uid))
                           .order_by(LaterEntry.added_at.desc(), LaterEntry.work_id.desc())))


def add(db: Session, uid: int, work_id: int) -> None:
    """Puts a work on the list; a work that is on it stays as it is. 404 for a work the person does not see. The limit
    is kept by the statement that writes (an INSERT ... SELECT that selects nothing once the list is full): counting
    first and writing after would let a crowd of requests at the same moment all write."""
    seen = select(Edition.id).where(Edition.work_id == work_id, *works.visible_edition_clause(uid)).limit(1)
    if db.scalar(seen) is None:
        raise error("not_found", "Not found.", 404)
    held = select(func.count()).select_from(LaterEntry).where(LaterEntry.user_id == uid).scalar_subquery()
    source = select(literal(uid), literal(work_id), literal(clock.now(), type_=LaterEntry.added_at.type)).where(
        held < MAX_ENTRIES)
    columns = ["user_id", "work_id", "added_at"]
    db.execute(sqlite_insert(LaterEntry).from_select(columns, source).on_conflict_do_nothing())
    there = db.get(LaterEntry, (uid, work_id))
    db.commit()
    if there is None:
        raise error("later_full", f"The list holds at most {MAX_ENTRIES} books.", 409, max=MAX_ENTRIES)


def remove(db: Session, uid: int, work_id: int) -> None:
    """Takes a work off the list. Nothing for a work that is not on it: asking twice does no harm."""
    db.execute(delete(LaterEntry).where(LaterEntry.user_id == uid, LaterEntry.work_id == work_id))
    db.commit()


def finished(db: Session, uid: int, work_id: int) -> None:
    """The person finished the book: it leaves the list. In the transaction of the caller (not committed here)."""
    db.execute(delete(LaterEntry).where(LaterEntry.user_id == uid, LaterEntry.work_id == work_id))
