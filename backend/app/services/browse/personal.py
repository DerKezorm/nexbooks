"""The shelves made for one person: "because you read ...", "like your favourite books", "short" and "for
long
evenings".

**Made on this server and nowhere else.** What a person read (the history, the editions they may see) is used here to
choose
among the books the sources already named for the market; no question goes to a source for it, and nothing of the person
is in any request. A row is a choice among public shelves, laid over what the person has.

**What a person reads is what they may see**: the history and the works come through ``history.listing`` and
``works.views``, which show only the editions of the libraries the person is a member of.

* *Because you read* ``X``: the book finished last. More by the same people (the author, the series), and then the
  books of
  the shelves of its genre that the sources rank highest.
* *Like your favourite books*: a favourite is a book finished within ``FAVOURITE_DAYS`` of the first report of the pass.
  The shelves of their genres, without their authors (those are in the row before).
* *Short*: fewer than ``SHORT_PAGES`` pages, where a source says the pages.
* *For long evenings*: books of more than ``LONG_PAGES`` pages, where a source says the pages (Open Library does, as
  the middle of its editions), and recordings of ``LONG_MINUTES`` or more, where a source says the running time
  (Audible does). A box of several books or a collected edition is never one of them: it is long because it is many.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Work
from .. import history, works
from ..discover import foreign
from ..readers.text import fold
from . import shelves, view
from .view import ShelfBook, ShelfData

#: A favourite is finished within this many days of the first report of the pass.
FAVOURITE_DAYS = 5
FAVOURITES = 3
PER_ROW = 14
SHORT_PAGES = 250
LONG_MINUTES = 20 * 60
LONG_PAGES = 600
#: How many finished books are looked at for the choice (the newest).
LOOKED_AT = 60


@dataclass
class Row:
    key: str  # ``because``, ``like``, ``short`` or ``long``
    books: list[ShelfBook]
    #: ``because``: the book read last; ``like``: the favourites.
    reads: list[works.WorkView] = field(default_factory=list)
    author: str = ""
    genre: str = ""


def _pool(data: dict[str, ShelfData], hide_owned: bool = True) -> list[tuple[ShelfBook, list[str]]]:
    """Every book on any shelf once, with the shelves it stands on, in the order the sources rank them (the most wanted
    first)."""
    seen: dict[str, tuple[ShelfBook, list[str]]] = {}
    for key, shelf_data in data.items():
        for book in view.union(shelf_data):
            if hide_owned and book.owned_work_id is not None:
                continue
            if book.key in seen:
                seen[book.key][1].append(key)
            else:
                seen[book.key] = (book, [key])
    return sorted(seen.values(), key=lambda item: _weight(item[0]))


def _weight(book: ShelfBook) -> tuple[int, int]:
    """The place of a book among the most wanted: those in a section ``popular`` first, by their place there."""
    for order, section in enumerate(("popular", "new", "award", "classic")):
        if section in book.places:
            return order, book.places[section]
    return 9, 0


def minutes_of(book: ShelfBook) -> int:
    best = 0
    for form in foreign.forms_of(book.rows):
        if form.get("kind") == "audio" and isinstance(form.get("minutes"), int):
            best = max(best, int(form["minutes"]))
    return best


def is_long(book: ShelfBook) -> bool:
    """A book for long evenings: a recording of ``LONG_MINUTES`` or more, or more than ``LONG_PAGES`` pages, where a
    source says so; never a box or a collected edition."""
    if shelves.is_bundle(book.row.title):
        return False
    pages = view.pages_of(book)
    return minutes_of(book) >= LONG_MINUTES or (pages is not None and pages > LONG_PAGES)


def _finished(db: Session, uid: int) -> list[tuple[works.WorkView, int | None, str]]:
    """The books the person finished, the newest first: ``(the work as they see it, days the pass took, kind)``."""
    entries, _total = history.listing(db, uid, limit=LOOKED_AT)
    by_id = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(list({e.work_id for e in entries}) or [0])))}
    seen = {v.work.id: v for v in works.views(db, uid, list(by_id.values()))}
    found: list[tuple[works.WorkView, int | None, str]] = []
    for entry in entries:
        item = seen.get(entry.work_id)
        if item is not None:
            found.append((item, entry.days, entry.kind))
    return found


def _same_author(book: ShelfBook, names: list[str]) -> bool:
    return bool(names) and view.authors_fit(book, names)


def _series_of(book: ShelfBook) -> str:
    return fold(next((r.series for r in book.rows if r.series), ""))


def make(db: Session, uid: int, data: dict[str, ShelfData], *, hide_owned: bool = True) -> list[Row]:
    """The four rows for a person, those that have books; ``data`` is the market's shelves with the person's own things
    laid over them (``view.lay_over``)."""
    pool = _pool(data, hide_owned)
    rows: list[Row] = []
    finished = _finished(db, uid)
    used: set[str] = set()

    if finished and pool:
        last = finished[0][0]
        names = [name for _id, name in last.authors]
        series = fold(last.series_name)
        near = {s for g in (last.genre,) for s in shelves.SHELVES_OF_GENRE.get(g, ())}
        scored = []
        for book, on in pool:
            score = (10 if _same_author(book, names) else 0) + (8 if series and _series_of(book) == series else 0) + (
                3 if near.intersection(on) else 0)
            if score >= 3:
                scored.append((-score, _weight(book), book))
        scored.sort(key=lambda item: (item[0], item[1]))
        books = [b for _s, _w, b in scored[:PER_ROW]]
        if books:
            used.update(b.key for b in books)
            rows.append(Row("because", books, reads=[last], author=names[0] if names else "", genre=last.genre))

    liked: list[works.WorkView] = []
    for item, days, _kind in finished:
        if days is not None and days <= FAVOURITE_DAYS and item.work.id not in {v.work.id for v in liked}:
            liked.append(item)
        if len(liked) >= FAVOURITES:
            break
    if liked and pool:
        names = [name for item in liked for _id, name in item.authors]
        counts: dict[str, int] = {}
        for item in liked:
            for shelf_key in shelves.SHELVES_OF_GENRE.get(item.genre, ()):
                counts[shelf_key] = counts.get(shelf_key, 0) + 1
        books = [book for book, on in pool if book.key not in used and set(on).intersection(counts)
                 and not _same_author(book, names)][:PER_ROW]
        if books:
            rows.append(Row("like", books, reads=liked))

    short = [book for book, on in pool if (pages := view.pages_of(book)) is not None and pages < SHORT_PAGES
             and "kinder" not in on and "jugend" not in on][:PER_ROW]
    if short:
        rows.append(Row("short", short))
    long = sorted((book for book, _on in pool if is_long(book)), key=lambda b: (_weight(b), -minutes_of(b)))[:PER_ROW]
    if long:
        rows.append(Row("long", long))
    return rows

