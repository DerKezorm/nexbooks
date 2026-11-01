"""Which of the books of the sources a person has: **by the books they may see, and by nothing else.**

A person sees the editions of the libraries they are a member of (``works.visible_edition_clause``). Whether a book of
a list stands on their shelf is decided from those editions alone: their ISBNs and ASINs, the names the sources gave
them (``external_refs`` of those editions), and their titles with their authors. A book that is in a library the
person is not in is not on their shelf, whatever else the server knows: the plaque "on the shelf", the switch "hide
what I have" and the numbers that count what was hidden all follow this, so that a list can never tell a person that
somebody else owns a book.

The rule for the title: the same title (as far as its subtitle) **and** an author whose last name and first name or
initial fit (never the last name alone), and a different place in a series is a different book.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import Engine, event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ...models import AUTHOR, Credit, Edition, ExternalRef, LibraryMember, Person
from ..readers.names import authors_compatible, title_keys
from ..works import visible_edition_clause


@dataclass
class Shelf:
    """What a person's shelves hold, as keys to look a book up by."""

    by_asin: dict[str, int] = field(default_factory=dict)
    by_isbn: dict[str, int] = field(default_factory=dict)
    by_ref: dict[tuple[str, str], int] = field(default_factory=dict)
    #: A folded title (whole, and as far as the subtitle) -> the works that carry it, with their authors and place in
    #: a series.
    by_title: dict[str, list[tuple[int, list[str], float | None]]] = field(default_factory=dict)
    works: set[int] = field(default_factory=set)


# ---- Keeping the shelf of a person -------------------------------------------------------------------------------
#
# Every list, every "for you" and every search that sets books against the shelf needs it, and reading it is the cost of
# the whole shelf (about half a second for 10,000 books). So the shelf of a person is kept, for the ones who looked at
# their lists last (``MAX_KEPT``), **until anything it is made of changes**: a statement that writes to the editions,
# their authors, the names the sources gave them, the people, the libraries, the members of a library or the accounts
# counts as a change once it is committed, and the whole store is dropped (one write is a reason to read again, not to
# work out what it touched). What a person may see is also part of the key: the libraries they are a member of are read
# fresh at every call, so a membership that was withdrawn can never be answered from the store. A transaction that has
# written to those tables itself reads from the database and keeps nothing (it sees what nobody else does yet).

#: The keys of a title are worked out once for each title that is read (a shelf that is read again after a change
#: has nearly the same titles).
_title_keys = lru_cache(maxsize=65536)(title_keys)

#: The tables a shelf is read from or through, as the first word after the verb of a statement that writes.
_WATCHED = re.compile(
    r"\s*(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update(?:\s+or\s+\w+)?|delete\s+from)\s+[\"`\[]?"
    r"(?:editions|credits|people|external_refs|library_members|libraries|users)\b", re.IGNORECASE)
#: How many shelves are kept at once, and for how long at most (a safety net; a change drops them at once).
MAX_KEPT = 16
MAX_AGE_SECONDS = 600
_DIRTY = "nexbooks_shelf_dirty"
_lock = threading.Lock()


#: The store of each database (one per engine: the app has one, a test may make others).
_stores: WeakKeyDictionary[Engine, dict[str, Any]] = WeakKeyDictionary()


def _store(engine: Engine) -> dict[str, Any]:
    with _lock:
        store = _stores.get(engine)
        if store is None:
            store = _stores[engine] = {"rev": 0, "kept": OrderedDict()}
        return store


def _bump(engine: Engine) -> None:
    store = _store(engine)
    with _lock:
        store["rev"] += 1
        store["kept"].clear()


@event.listens_for(Engine, "before_cursor_execute")
def _note_write(conn: Connection, _cursor: Any, statement: str, _parameters: Any, _context: Any,
                _executemany: bool) -> None:
    if _WATCHED.match(statement):
        conn.info[_DIRTY] = True


@event.listens_for(Engine, "commit")
def _after_commit(conn: Connection) -> None:
    if conn.info.pop(_DIRTY, False):
        _bump(conn.engine)


@event.listens_for(Engine, "rollback")
def _after_rollback(conn: Connection) -> None:
    conn.info.pop(_DIRTY, None)


def drop_stale(now: float | None = None) -> int:
    """Drops the shelves nobody asked for within ``MAX_AGE_SECONDS`` (counted from the last time one was asked for, not
    from when it was made): a shelf of 10,000 books is tens of megabytes, and one is otherwise only dropped when it is
    asked for again or something changes. Called once a minute by the server. Returns how many went."""
    now = time.monotonic() if now is None else now
    gone = 0
    with _lock:
        for store in list(_stores.values()):
            for key in [key for key, kept in store["kept"].items() if now - kept[4] >= MAX_AGE_SECONDS]:
                del store["kept"][key]
                gone += 1
    return gone


def forget() -> None:
    """Drops every shelf that is kept (the tests, between two of them)."""
    from ...db import engine

    _bump(engine)


def keep(db: Session, uid: int, name: str, read: Callable[[], Any]) -> Any:
    """What ``read()`` makes of the books a person sees, kept under ``name`` by the rules above (the shelf is one such
    thing, the cards of the authors another). Kept until something it is made of changes, per person, and never across a
    change of the libraries the person is a member of. The caller must not change what it gets, and ``read`` must read
    nothing but the tables that are watched (``_WATCHED``)."""
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    if db.in_transaction() and db.connection().info.get(_DIRTY):
        return read()
    store = _store(engine)
    members = tuple(sorted(db.scalars(select(LibraryMember.library_id).where(LibraryMember.user_id == uid))))
    now = time.monotonic()
    key = (name, uid)
    with _lock:
        revision = store["rev"]
        kept = store["kept"].get(key)
        if kept is not None and kept[0] == revision and kept[1] == members and now - kept[2] < MAX_AGE_SECONDS:
            store["kept"][key] = (*kept[:4], now)
            store["kept"].move_to_end(key)
            return kept[3]
    value = read()
    with _lock:
        if store["rev"] == revision:
            store["kept"][key] = (revision, members, now, value, now)
            while len(store["kept"]) > MAX_KEPT:
                store["kept"].popitem(last=False)
    return value


def load(db: Session, uid: int) -> Shelf:
    """The shelf of a person: what the editions they see hold. Kept until something it is made of changes (see
    above); the caller must not change what it gets."""
    shelf: Shelf = keep(db, uid, "shelf", lambda: _read(db, uid))
    return shelf


def _read(db: Session, uid: int) -> Shelf:
    """The shelf of a person, read from the editions they see, in one go."""
    shelf = Shelf()
    visible = visible_edition_clause(uid)
    rows = db.execute(select(Edition.id, Edition.work_id, Edition.title, Edition.isbn, Edition.asin,
                             Edition.series_pos).where(*visible).order_by(Edition.id)).all()
    if not rows:
        return shelf
    work_of = {row[0]: row[1] for row in rows}
    authors: dict[int, list[str]] = {}
    credits = (select(Credit.edition_id, Person.name).join(Person, Person.id == Credit.person_id)
               .where(Credit.edition_id.in_(select(Edition.id).where(*visible)), Credit.role == AUTHOR)
               .order_by(Credit.edition_id, Credit.position))
    for edition_id, name in db.execute(credits):
        authors.setdefault(edition_id or 0, []).append(name)
    for edition_id, work_id, title, isbn, asin, position in rows:
        shelf.works.add(work_id)
        if asin:
            shelf.by_asin.setdefault(asin.upper(), work_id)
        if isbn:
            shelf.by_isbn.setdefault(isbn, work_id)
        whole, main = _title_keys(title)
        for key in {whole, main}:
            if key:
                shelf.by_title.setdefault(key, []).append((work_id, authors.get(edition_id, []), position))
    for source, key, edition_id in db.execute(
            select(ExternalRef.source, ExternalRef.key, ExternalRef.edition_id)
            .where(ExternalRef.edition_id.in_(select(Edition.id).where(*visible)))):
        # An edition's own name at a source stands for its work; Audible's key is the ASIN.
        owner = work_of.get(edition_id or 0)
        if owner is not None:
            shelf.by_ref.setdefault((source, key), owner)
            if source == "audible":
                shelf.by_asin.setdefault(key.upper(), owner)
    return shelf


def find(
    shelf: Shelf, *, title: str, authors: list[str], isbns: list[str] | None = None, asin: str = "",
    refs: list[tuple[str, str]] | None = None, position: float | None = None,
) -> int | None:
    """The work of the shelf this book is, or None. By the names the sources gave (a Hardcover or Audible number), by
    ASIN and ISBN, and last by title and author."""
    for ref in refs or []:
        if ref in shelf.by_ref:
            return shelf.by_ref[ref]
    if asin and asin.upper() in shelf.by_asin:
        return shelf.by_asin[asin.upper()]
    for isbn in isbns or []:
        if isbn in shelf.by_isbn:
            return shelf.by_isbn[isbn]
    if not authors:
        return None
    whole, main = title_keys(title)
    for key in dict.fromkeys([whole, main]):
        for work_id, theirs, place in shelf.by_title.get(key, []):
            if position is not None and place is not None and position != place:
                continue
            if any(authors_compatible(a, b) for a in authors for b in theirs):
                return work_id
    return None
