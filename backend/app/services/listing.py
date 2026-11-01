"""What the list view of the library adds to a book: where the person stands in it, whether they
finished it, and, for a person who looks after a library, how sure the matching is of the book and which library
it lies in.

Everything is asked for the books of one page of the list in a few queries, and **only from what the caller may
see**:

* *The place and the end* are the person's own (``progress``, ``history``), found by the editions the caller
  sees.
* *The matching and the library* are given to a member with ``manage`` for the editions of the libraries they
  manage, and to nobody else: a member who only reads gets none of it, and no edition of another library adds to
  what a manager sees. (The operator is no exception unless they are a member with ``manage`` themselves.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EDITION_AUDIO, MANAGE, HistoryEntry, Library, LibraryMember, MatchEntry, Progress
from .works import WorkView

#: How much a matching state asks of the person who looks after the book: the worst of a work's editions is shown.
_WORST = {"open": 4, "maybe": 3, "pending": 2, "sure": 1, "decided": 0}


@dataclass
class RowExtra:
    #: How far the person is in the e-book and in the recording (0 to 1), None when they have not begun.
    ebook: float | None = None
    audio: float | None = None
    #: Whether they entered the e-book / the recording as finished.
    ebook_done: bool = False
    audio_done: bool = False
    #: ``sure``, ``maybe``, ``open``, ``pending`` (not looked at
    #: yet), ``decided`` (a person said which book it is); None
    #: when the caller does not manage a library the book lies in.
    match: str | None = None
    libraries: list[str] = field(default_factory=list)
    managed: bool = False
    #: When the person was last in the book, if ever.
    moved_at: datetime | None = None


def state_of(entry: MatchEntry) -> str:
    return "decided" if entry.decision else entry.state


def rows(db: Session, uid: int, views: list[WorkView]) -> dict[int, RowExtra]:
    """The extras for the works of ``views``, by work id."""
    found = {view.work.id: RowExtra() for view in views}
    kind_of = {item.edition.id: item.edition.kind for view in views for item in view.editions}
    owner = {item.edition.id: view.work.id for view in views for item in view.editions}
    if not owner:
        return found
    ids = list(owner)
    moved: dict[tuple[int, str], datetime] = {}
    for row in db.scalars(select(Progress).where(Progress.user_id == uid, Progress.edition_id.in_(ids))):
        work = owner[row.edition_id]
        kind = "audio" if kind_of[row.edition_id] == EDITION_AUDIO else "ebook"
        # Of several editions of one kind, the one the person was in last.
        if (work, kind) not in moved or row.moved_at > moved[(work, kind)]:
            moved[(work, kind)] = row.moved_at
            setattr(found[work], kind, row.fraction)
            if found[work].moved_at is None or row.moved_at > found[work].moved_at:  # type: ignore[operator]
                found[work].moved_at = row.moved_at
    for edition_id in db.scalars(select(HistoryEntry.edition_id).where(HistoryEntry.user_id == uid,
                                                                       HistoryEntry.edition_id.in_(ids)).distinct()):
        work = owner[edition_id]
        setattr(found[work], "audio_done" if kind_of[edition_id] == EDITION_AUDIO else "ebook_done", True)
    managed = set(db.scalars(select(LibraryMember.library_id).where(LibraryMember.user_id == uid,
                                                                    LibraryMember.right == MANAGE)))
    if managed:
        names = dict(db.execute(select(Library.id, Library.name).where(Library.id.in_(managed))).all())
        library_of = {item.edition.id: item.edition.library_id for view in views for item in view.editions}
        mine = [edition_id for edition_id in ids if library_of[edition_id] in managed]
        states: dict[int, str] = {}
        for entry in db.scalars(select(MatchEntry).where(MatchEntry.edition_id.in_(mine))):
            states[entry.edition_id] = state_of(entry)
        for edition_id in mine:
            extra = found[owner[edition_id]]
            extra.managed = True
            name = names.get(library_of[edition_id], "")
            if name and name not in extra.libraries:
                extra.libraries.append(name)
            state = states.get(edition_id, "pending")
            if extra.match is None or _WORST[state] > _WORST[extra.match]:
                extra.match = state
    return found
