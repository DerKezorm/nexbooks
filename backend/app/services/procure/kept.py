"""What people had in an edition that is sent away as the wrong book: their place, marks and finished passes.

An edition that leaves takes with it, by the foreign keys, the progress, the bookmarks and the history of everybody who
read or heard it; the files can come back from the trash, but the edition they make again is a new one, with a number of
its own and none of that. So before the edition goes, what the people had is written down here, **with the trash entry
of the files** (it goes when the entry goes: deleted, emptied, or its time over). When the files are taken out of the
trash the notes wait for the scan, which makes an edition of the files again; it is found by the paths the files lay at,
and each person gets back what they had, **unless they have something of their own in it by then** (a place they
reached after the files came back is never written over).

The notes are the people's own, like the progress they come from: nothing here is read for anybody else, and the
operator has no way to them.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ... import clock
from ...models import ROLE_BOOK, Account, BookFile, Bookmark, HistoryEntry, KeptState, Progress

logger = logging.getLogger("nexbooks.procure")

#: How long notes wait for the scan after the files left the trash: the scan comes within minutes, and a file that is
#: still being written waits for the next run.
WAIT_DAYS = 7

_PROGRESS = ("user_id", "locator", "fraction", "unit", "chapter", "chapter_count", "chapter_title", "device_key",
             "device", "moved_at", "received_at", "pass_done", "koreader", "kobo")
_BOOKMARK = ("user_id", "locator", "fraction", "chapter_title", "created_at", "note")
_HISTORY = ("user_id", "kind", "finished_on", "source", "created_at")
_TIMES = frozenset({"moved_at", "received_at", "created_at"})


def _dump(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in fields:
        value = getattr(row, name)
        out[name] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _load(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in fields:
        if name not in data:
            continue
        value = data[name]
        out[name] = datetime.fromisoformat(value) if name in _TIMES and isinstance(value, str) else value
    return out


def snapshot(db: Session, edition_id: int) -> dict[str, list[dict[str, Any]]]:
    """Everything the people have in the edition, as plain values."""
    return {
        "progress": [_dump(row, _PROGRESS) for row in db.scalars(select(Progress).where(
            Progress.edition_id == edition_id))],
        "bookmarks": [_dump(row, _BOOKMARK) for row in db.scalars(select(Bookmark).where(
            Bookmark.edition_id == edition_id).order_by(Bookmark.id))],
        "history": [_dump(row, _HISTORY) for row in db.scalars(select(HistoryEntry).where(
            HistoryEntry.edition_id == edition_id).order_by(HistoryEntry.id))],
    }


def people_in(states: dict[str, list[dict[str, Any]]]) -> int:
    """How many people have something in the notes."""
    return len({item.get("user_id") for part in states.values() for item in part})


def keep(db: Session, *, library_id: int, trash_id: int | None, paths: list[str],
         states: dict[str, list[dict[str, Any]]], days: int) -> KeptState | None:
    """Writes the notes down; nothing when nobody had anything. Does not commit. ``trash_id`` is the entry the files
    went to (the notes go with it); without one (no file was there to move) the notes wait ``days`` for the files to
    come."""
    if not any(states.values()):
        return None
    moment = clock.now()
    row = KeptState(library_id=library_id, trash_id=trash_id, paths=paths[:2000], states=states, created_at=moment,
                    expires_at=moment + timedelta(days=days))
    db.add(row)
    db.flush()
    return row


def leave_trash(db: Session, trash_id: int) -> None:
    """The files of the entry are taken out of the trash: the notes stop depending on it and wait for the scan. Call it
    before the entry is deleted (the row would go with it). Does not commit."""
    db.execute(update(KeptState).where(KeptState.trash_id == trash_id)
               .values(trash_id=None, expires_at=clock.now() + timedelta(days=WAIT_DAYS)))


def reattach(db: Session, library_id: int) -> int:
    """Gives the people what they had back, for the notes of the library whose files now make an edition again. Returns
    how many notes were given back. Commits."""
    waiting = list(db.scalars(select(KeptState).where(KeptState.library_id == library_id,
                                                      KeptState.trash_id.is_(None))))
    given = 0
    for note in waiting:
        paths = [path for path in note.paths or [] if isinstance(path, str)]
        found = Counter(edition_id for edition_id in db.scalars(select(BookFile.edition_id).where(
            BookFile.library_id == library_id, BookFile.path.in_(paths), BookFile.role == ROLE_BOOK,
            BookFile.missing_at.is_(None), BookFile.edition_id.is_not(None))))
        if not found:
            continue
        edition_id = found.most_common(1)[0][0]
        _give(db, edition_id, note.states if isinstance(note.states, dict) else {})
        db.delete(note)
        given += 1
    if given:
        db.commit()
        logger.info("People got back what they had in books taken out of the trash library=%s notes=%s", library_id,
                    given)
    return given


def _present(db: Session, user_id: Any) -> bool:
    return isinstance(user_id, int) and db.get(Account, user_id) is not None


def _give(db: Session, edition_id: int, states: dict[str, Any]) -> None:
    for item in states.get("progress") or []:
        if not isinstance(item, dict) or not _present(db, item.get("user_id")):
            continue
        if db.get(Progress, (item["user_id"], edition_id)) is None:
            db.add(Progress(edition_id=edition_id, **_load(item, _PROGRESS)))
    for item in states.get("bookmarks") or []:
        if not isinstance(item, dict) or not _present(db, item.get("user_id")):
            continue
        if db.scalar(select(Bookmark.id).where(Bookmark.user_id == item["user_id"], Bookmark.edition_id == edition_id,
                                               Bookmark.locator == item.get("locator"))) is None:
            db.add(Bookmark(edition_id=edition_id, **_load(item, _BOOKMARK)))
    for item in states.get("history") or []:
        if not isinstance(item, dict) or not _present(db, item.get("user_id")):
            continue
        if db.scalar(select(HistoryEntry.id).where(
                HistoryEntry.user_id == item["user_id"], HistoryEntry.edition_id == edition_id,
                HistoryEntry.finished_on == item.get("finished_on"))) is None:
            db.add(HistoryEntry(edition_id=edition_id, **_load(item, _HISTORY)))
    db.flush()


def forget_person(db: Session, user_id: int) -> None:
    """What a person had in books sent away goes with their account: their share of every note is taken out, and a note
    nobody has anything in any more is deleted. Does not commit."""
    for note in db.scalars(select(KeptState)):
        states = note.states if isinstance(note.states, dict) else {}
        cleaned = {name: [item for item in part if not (isinstance(item, dict) and item.get("user_id") == user_id)]
                   for name, part in states.items() if isinstance(part, list)}
        if cleaned == states:
            continue
        if any(cleaned.values()):
            note.states = cleaned
        else:
            db.delete(note)


def sweep(db: Session) -> int:
    """Deletes the notes whose time is over (the files never came back). Returns how many. Does not commit. Notes that
    go with a trash entry are left to it: they go when it goes."""
    return db.execute(delete(KeptState).where(KeptState.trash_id.is_(None),
                                              KeptState.expires_at <= clock.now())).rowcount or 0


__all__ = ["WAIT_DAYS", "forget_person", "keep", "leave_trash", "people_in", "reattach", "snapshot", "sweep"]
