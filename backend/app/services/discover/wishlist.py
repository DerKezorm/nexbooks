"""The wish list: books a person marked in Discover, **theirs alone**. Nothing here reads a row of anybody else, and
there is no way to ask for one: every function takes the person that asks and looks at their rows only (the operator
has no other way in). Putting a book on the list and taking it off are both safe to do twice (a double click, two
devices, a retry): the pair (person, book) is unique, and the second try changes nothing."""

from __future__ import annotations

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ... import clock
from ...models import ForeignBook, Wish
from . import foreign

#: The most books on one list: a list that grows without end is a way to fill the database.
MAX_WISHES = 500

#: How often a put meets a removal of the same book at the same moment before it gives in (see ``add``).
TRIES = 3


class Full(Exception):
    """The list holds as many books as it may."""


def ids_of(db: Session, uid: int) -> set[int]:
    """The books on the person's list (the numbers of their rows)."""
    return set(db.scalars(select(Wish.foreign_id).where(Wish.user_id == uid)))


def add(db: Session, uid: int, row: ForeignBook) -> bool:
    """Puts a book on the person's list. True when it is on the list now (new or not). ``Full`` when the list is full.
    **Atomic**: the room is counted inside the statement that writes, so two requests at the same moment cannot both
    pass a check made before. Commits.

    A statement that wrote nothing has three reasons: the book was there already, the list is full, or the book was
    taken off again by another request between the statement and the look. Only the second one is ``Full``; the third
    is tried again (the try then wins, or the book is taken off once more, and then it is as if the request came
    first and the other one second)."""
    authors = ", ".join(a for a in (row.authors or []) if isinstance(a, str))[:300]
    room = select(func.count(Wish.id)).where(Wish.user_id == uid).scalar_subquery() < MAX_WISHES
    for _ in range(TRIES):
        written = db.execute(sqlite_insert(Wish).from_select(
            ["user_id", "foreign_id", "title", "author", "cover", "added_at"],
            select(literal(uid), literal(row.id), literal(row.title), literal(authors), literal(row.cover),
                   literal(clock.now())).where(room)).on_conflict_do_nothing(index_elements=["user_id", "foreign_id"]))
        db.commit()
        if written.rowcount > 0:
            return True
        if db.scalar(select(Wish.id).where(Wish.user_id == uid, Wish.foreign_id == row.id)) is not None:
            return True
        if (db.scalar(select(func.count(Wish.id)).where(Wish.user_id == uid)) or 0) >= MAX_WISHES:
            raise Full
    return True


def remove(db: Session, uid: int, row: ForeignBook) -> int:
    """Takes a book (and the other rows of the same book: Apple's and Audible's entry are one card) off the person's
    list. Returns how many rows went; 0 is not an error. Commits."""
    ids = [member.id for member in foreign.family(db, row)]
    gone = db.execute(delete(Wish).where(Wish.user_id == uid, Wish.foreign_id.in_(ids))).rowcount or 0
    db.commit()
    return int(gone)


def listing(db: Session, uid: int) -> list[tuple[Wish, ForeignBook]]:
    """The person's list, the newest first."""
    return [(wish, book) for wish, book in db.execute(
        select(Wish, ForeignBook).join(ForeignBook, ForeignBook.id == Wish.foreign_id)
        .where(Wish.user_id == uid).order_by(Wish.added_at.desc(), Wish.id.desc())).all()]
