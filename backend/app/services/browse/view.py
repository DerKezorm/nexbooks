"""The shelves of Browse for the person who looks: the wall of thirteen shelves, and one shelf opened.

A shelf is what the sources said (``ShelfEntry``, public, the same for everybody) with the person's own things laid over
it: does the book stand on *their* shelf, is it on *their* wish list. Nothing of that is stored with the shelf, and
nothing
is shared between two people (``services/discover/owned.py`` decides what a person has, by the libraries they may see).

Two sources may name the same book (Apple's chart and Open Library): it is one book, with the places both gave it. A
section is made of the sources woven together, the first of each, then the second of each.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import ForeignBook, ShelfEntry, ShelfFetch
from .. import settings_service
from ..discover import foreign, foryou, owned, wishlist
from ..discover.view import SOURCE_ORDER, Card
from ..readers.names import authors_compatible, person_key
from ..sources import config
from . import cache, fetch, shelves

#: Who speaks first in a section: the chart of Apple is what people buy now, Open Library knows the old and the prized.
WEAVE = {"popular": ("apple", "openlibrary"), "new": ("apple", "openlibrary"), "soon": ("apple", "openlibrary"),
         "award": ("openlibrary", "apple"), "classic": ("openlibrary", "apple")}
#: The section a strip shows first, when it has books: the new ones, but a shelf of classics begins with
#: what is read.
DEFAULT_SECTIONS = ("new", "popular", "award", "classic")
#: Books of a page of an opened shelf.
PAGE = 60
MAX_PAGE = 120


@dataclass
class ShelfBook(Card):
    """A book of a shelf: the rows of the sources that name it, the places they give it, the prize, the topics."""

    #: section -> place (1 is first) in the woven section.
    places: dict[str, int] = field(default_factory=dict)
    award: str = ""
    topics: list[str] = field(default_factory=list)
    key: str = ""


@dataclass
class ShelfData:
    shelf: shelves.Shelf
    books: dict[str, ShelfBook] = field(default_factory=dict)
    #: section -> the keys of its books, in order.
    order: dict[str, list[str]] = field(default_factory=dict)
    fetched_at: Any = None


def _authors(row: ForeignBook) -> list[str]:
    return [a for a in (row.authors or []) if isinstance(a, str)]


def read(db: Session, market: str) -> dict[str, ShelfData]:
    """Every shelf of a market as the sources said it, the books merged. Nothing of the person is in it: the books are
    copies of what is kept in memory (``cache``), for the caller to lay their own things over."""
    kept = cache.get(market)
    if kept is None:
        seen = cache.version(market)
        kept = _build(db, market)
        cache.put(market, seen, kept)
    return {key: ShelfData(data.shelf, {k: copy.copy(b) for k, b in data.books.items()}, data.order, data.fetched_at)
            for key, data in kept.items()}


def _build(db: Session, market: str) -> dict[str, ShelfData]:
    """The shelves of a market from the database. A book stands on **one** shelf: the one whose source says most
    strongly that it belongs there (``ShelfEntry.evidence``; Apple's chart of a genre of the shelf before any share of
    subjects), the first in the table at a tie. A section holds at most ``shelves.PER_SERIES`` books of one series and
    ``shelves.PER_AUTHOR`` of one author. The
    shelves for children and young people hold no book by an author of the shelves for grown-ups, and the shelf for
    children none by an author of the shelf for young people."""
    out = {shelf.key: ShelfData(shelf) for shelf in shelves.SHELVES}
    found = db.execute(select(ShelfEntry, ForeignBook).join(ForeignBook, ForeignBook.id == ShelfEntry.foreign_id)
                       .where(ShelfEntry.market == market)
                       .order_by(ShelfEntry.shelf, ShelfEntry.section, ShelfEntry.rank)).all()
    # The rows outlive the session (they are kept in memory): nothing in them is read from the database later.
    for _entry, row in found:
        if row in db:
            db.expunge(row)
    # Children and young people: no book by an author whose books stand on a shelf for grown-ups of the market (the
    # charts and the tags put grown-up fantasy and romance among the books for young people; the cost is a few real
    # children's books by authors who write for both).
    # The shelf for children, likewise, holds no book by an author of the shelf for young people.
    grown = {person_key(_authors(row)[0]) for entry, row in found
             if _authors(row) and entry.shelf in shelves.BY_KEY and shelves.BY_KEY[entry.shelf].dept == "bel"}
    young = {person_key(_authors(row)[0]) for entry, row in found if _authors(row) and entry.shelf == "jugend"}

    def too_old(entry: ShelfEntry, row: ForeignBook) -> bool:
        kids = shelves.BY_KEY[entry.shelf].kids if entry.shelf in shelves.BY_KEY else ""
        if not kids or not _authors(row):
            return False
        author = person_key(_authors(row)[0])
        return author in grown or (kids == "kinder" and author in young)

    found = [(entry, row) for entry, row in found if not too_old(entry, row)]
    # The shelf each book stands on: the most evidence, the first shelf of the table at a tie.
    best: dict[str, tuple[float, int, str]] = {}
    # Only the sections a shelf has (a part fetched before a rule changed may hold others until it is fetched again).
    found = [(entry, row) for entry, row in found if entry.shelf in shelves.BY_KEY
             and entry.section in (*shelves.BY_KEY[entry.shelf].sections, "soon")]
    for entry, row in found:
        if entry.shelf not in out:
            continue
        key = foreign.group_key(row.title, _authors(row))
        mine = (float(entry.evidence or 0.0), -shelves.INDEX_OF[entry.shelf], entry.shelf)
        if key not in best or mine > best[key]:
            best[key] = mine
    # (shelf, section) -> source -> [(entry, row)]
    parts: dict[tuple[str, str], dict[str, list[tuple[ShelfEntry, ForeignBook]]]] = {}
    for entry, row in found:
        if entry.shelf in out and best.get(foreign.group_key(row.title, _authors(row)), (0, 0, ""))[2] == entry.shelf:
            parts.setdefault((entry.shelf, entry.section), {}).setdefault(entry.source, []).append((entry, row))
            fetched = out[entry.shelf].fetched_at
            if fetched is None or entry.fetched_at > fetched:
                out[entry.shelf].fetched_at = entry.fetched_at
    for (shelf_key, section), by_source in parts.items():
        data = out[shelf_key]
        lists = [by_source.get(source, []) for source in WEAVE.get(section, ("apple", "openlibrary"))]
        place = 0
        keys = data.order.setdefault(section, [])
        series: dict[str, int] = {}
        by_author: dict[str, int] = {}
        for pair in zip_longest(*lists):
            for item in pair:
                if item is None:
                    continue
                entry, row = item
                key = foreign.group_key(row.title, _authors(row))
                book = data.books.get(key)
                if book is not None and section in book.places:
                    if row.id not in {r.id for r in book.rows}:
                        book.rows.append(row)
                    continue
                if entry.series_key and series.get(entry.series_key, 0) >= shelves.PER_SERIES:
                    continue
                # Apple names no series: an author's place in a section stands for it.
                author = person_key(_authors(row)[0]) if _authors(row) else ""
                if author and by_author.get(author, 0) >= shelves.PER_AUTHOR:
                    continue
                if entry.series_key:
                    series[entry.series_key] = series.get(entry.series_key, 0) + 1
                if author:
                    by_author[author] = by_author.get(author, 0) + 1
                if book is None:
                    book = data.books[key] = ShelfBook(row=row, rows=[row], entries=[], key=key)
                elif row.id not in {r.id for r in book.rows}:
                    book.rows.append(row)
                place += 1
                book.places[section] = place
                keys.append(key)
                if entry.award and not book.award:
                    book.award = entry.award
                for topic in entry.topics or []:
                    if topic not in book.topics:
                        book.topics.append(topic)
    for data in out.values():
        for book in data.books.values():
            _settle(book, data.shelf)
    return out


def _settle(book: ShelfBook, shelf: shelves.Shelf) -> None:
    """What a card says, from the rows of the sources that name the book."""
    rows = sorted(book.rows, key=lambda r: SOURCE_ORDER.get(r.ref.split(":", 1)[0], 9))
    book.rows = rows
    # The row that speaks is the first that has a picture to show (or has its address), else the first.
    book.row = next((r for r in rows if r.cover), next((r for r in rows if r.cover_url), rows[0]))
    forms = foreign.forms_of(rows)
    book.kinds = sorted({f.get("kind") for f in forms if f.get("kind") in ("ebook", "audio")}) or ["ebook"]
    book.sources = sorted({r.ref.split(":", 1)[0] for r in rows}, key=lambda s: SOURCE_ORDER.get(s, 9))
    book.language = next((r.language for r in rows if r.language), "")
    book.genre = next((r.genre for r in rows if r.genre), "") or shelf.genre
    book.released = next((r.released for r in rows if r.released), "")


# ---- What the person has ----------------------------------------------------------------------------------------


def lay_over(db: Session, uid: int, data: dict[str, ShelfData]) -> None:
    """The person's own things on the books of every shelf: the work on their shelf it is, whether it is on their wish
    list."""
    shelf = owned.load(db, uid)
    wished = wishlist.ids_of(db, uid)
    seen: dict[int, int | None] = {}
    for shelf_data in data.values():
        for book in shelf_data.books.values():
            work = None
            for row in book.rows:
                if row.id not in seen:
                    seen[row.id] = foryou.find_owned(shelf, row)
                work = seen[row.id]
                if work is not None:
                    break
            book.owned_work_id = work
            book.wished = any(row.id in wished for row in book.rows)


# ---- The state of the sources ------------------------------------------------------------------------------------


@dataclass
class Sources:
    #: ``ready``, ``fetching`` (parts never fetched, a source may be asked), ``empty``, ``failed``, ``main_off``,
    #: ``no_source``.
    state: str
    availability: list[tuple[str, str]]
    asking: bool
    pending: bool
    fetched_at: Any = None
    sources: list[str] = field(default_factory=list)


def sources_state(db: Session, market: str, have: bool, data: dict[str, ShelfData]) -> Sources:
    states = [(source, config.availability(db, source).state) for source in fetch.SHELF_SOURCES]
    asking = any(state == "ready" for _source, state in states)
    ready = {source for source, state in states if state == "ready"}
    units = fetch.units(market)
    rows = [db.get(ShelfFetch, (source, market, shelf)) for source, shelf in units]
    # Still being filled: a part of a shelf that a source which may be asked has never given, and for which a job waits.
    # A source that is off, resting or past its share of the day fills nothing, and the page need not ask again for it.
    pending = any(source in ready and (row is None or row.fetched_at is None)
                  and fetch.waiting(db, fetch.unit_ref(source, market, shelf))
                  for (source, shelf), row in zip(units, rows, strict=True))
    fetched = max((row.fetched_at for row in rows if row is not None and row.fetched_at is not None), default=None)
    names = sorted({s for d in data.values() for b in d.books.values() for s in b.sources},
                   key=lambda s: SOURCE_ORDER.get(s, 9))
    if have:
        return Sources("ready", states, asking, pending, fetched, names)
    if not asking:
        return Sources("main_off" if not settings_service.get(db, "sources_enabled") else "no_source", states, False,
                       False, fetched, names)
    if pending:
        return Sources("fetching", states, True, True, fetched, names)
    if all(row is not None and row.state == "failed" for row in rows):
        return Sources("failed", states, True, False, fetched, names)
    return Sources("empty", states, True, False, fetched, names)


def want(db: Session, market: str) -> None:
    """A person looks at the shelves of a market: the units that are due are queued (nothing is asked while they
    wait)."""
    fetch.want(db, market)


# ---- The wall ----------------------------------------------------------------------------------------------------


@dataclass
class SectionCount:
    key: str
    count: int


@dataclass
class Tile:
    shelf: shelves.Shelf
    books: int = 0
    new: int = 0
    fan: list[ShelfBook] = field(default_factory=list)


@dataclass
class Strip:
    shelf: shelves.Shelf
    section: str
    sections: list[SectionCount]
    books: list[ShelfBook]


@dataclass
class Wall:
    market: str
    hide_owned: bool
    sources: Sources
    tiles: list[Tile]
    strips: list[Strip]


def visible(books: list[ShelfBook], hide_owned: bool) -> list[ShelfBook]:
    return [b for b in books if b.owned_work_id is None] if hide_owned else books


def in_section(data: ShelfData, section: str) -> list[ShelfBook]:
    return [data.books[key] for key in data.order.get(section, [])]


def union(data: ShelfData) -> list[ShelfBook]:
    """Every book of the shelf once, the ones most wanted first (the order of the sections: popular, new, award,
    classic)."""
    seen: set[str] = set()
    out: list[ShelfBook] = []
    for section in ("popular", "new", "award", "classic"):
        for key in data.order.get(section, []):
            if key not in seen:
                seen.add(key)
                out.append(data.books[key])
    return out


def _fan(data: ShelfData, hide_owned: bool) -> list[ShelfBook]:
    """Three covers for the tile: the most wanted books that have a picture to show, and (a shelf whose books
    have none yet) the most wanted of the rest, which are drawn."""
    books = visible(union(data), hide_owned)
    with_picture = [b for b in books if any(r.cover_url or r.cover for r in b.rows)]
    return (with_picture + [b for b in books if b not in with_picture])[:3]


def wall(db: Session, uid: int, market: str, *, hide_owned: bool, section: str | None = None) -> Wall:
    data = read(db, market)
    have = any(d.books for d in data.values())
    lay_over(db, uid, data)
    tiles: list[Tile] = []
    strips: list[Strip] = []
    for shelf in shelves.SHELVES:
        shelf_data = data[shelf.key]
        books = visible(union(shelf_data), hide_owned)
        fresh = visible(in_section(shelf_data, "new"), hide_owned) if "new" in shelf.sections else []
        tiles.append(Tile(shelf, books=len(books), new=len(fresh), fan=_fan(shelf_data, hide_owned)))
        counts = [SectionCount(s, len(visible(in_section(shelf_data, s), hide_owned))) for s in shelf.sections]
        filled = [c for c in counts if c.count >= shelves.MIN_STRIP]
        if not filled:
            continue
        wanted = section if section in {c.key for c in filled} else None
        first = wanted or next((s for s in DEFAULT_SECTIONS if any(c.key == s for c in filled)), filled[0].key)
        strips.append(Strip(shelf, first, filled,
                            visible(in_section(shelf_data, first), hide_owned)[:shelves.STRIP_BOOKS]))
    return Wall(market, hide_owned, sources_state(db, market, have, data), tiles, strips)


# ---- One shelf opened --------------------------------------------------------------------------------------------


@dataclass
class Facet:
    key: str
    count: int


@dataclass
class ShelfPage:
    shelf: shelves.Shelf
    market: str
    sources: Sources
    #: How many books the shelf holds under the filters (before the page is cut), and how many of them the person has.
    total: int
    owned: int
    books: list[ShelfBook]
    sections: list[Facet]
    languages: list[Facet]
    topics: list[Facet]
    fetched_at: Any = None


def year_bounds() -> tuple[int, int]:
    """The years that cut "new" (this year and the one before), "middle" (back to sixteen years) and "old" (before)."""
    year = clock.now().year
    return year - 1, year - 16


def year_of(book: ShelfBook) -> int | None:
    for row in book.rows:
        data = row.data if isinstance(row.data, dict) else {}
        if isinstance(data.get("year"), int):
            return int(data["year"])
        if row.released[:4].isdigit():
            return int(row.released[:4])
    return None


def pages_of(book: ShelfBook) -> int | None:
    for row in book.rows:
        data = row.data if isinstance(row.data, dict) else {}
        if isinstance(data.get("pages"), int) and data["pages"] > 0:
            return int(data["pages"])
    return None


@dataclass(frozen=True)
class Filters:
    """What the page of a shelf asks for: a section, a language, a format, an age (``new``, ``mid``, ``old``), a
    topic."""

    section: str = ""
    language: str = ""
    fmt: str = "all"
    years: str = ""
    topic: str = ""


def _fits(book: ShelfBook, f: Filters, *, ignore: str = "") -> bool:
    """Whether a book passes the filters (but the one named in ``ignore``, for counting what it would give). A book
    whose
    language no source said passes a language; a book of no known year passes no age."""
    if ignore != "section" and f.section and f.section not in book.places:
        return False
    if ignore != "topic" and f.topic and f.topic not in book.topics:
        return False
    if ignore != "language" and f.language and book.language not in (f.language, ""):
        return False
    if f.fmt != "all" and f.fmt not in book.kinds:
        return False
    if f.years:
        year = year_of(book)
        new_from, mid_from = year_bounds()
        if year is None or (f.years == "new" and year < new_from) or (
                f.years == "mid" and not mid_from <= year < new_from) or (f.years == "old" and year >= mid_from):
            return False
    return True


NO_FILTERS = Filters()


def shelf_page(db: Session, uid: int, market: str, key: str, *, filters: Filters = NO_FILTERS, sort: str = "pop",
               hide_owned: bool = True, limit: int = PAGE, offset: int = 0) -> ShelfPage | None:
    shelf = shelves.BY_KEY.get(key)
    if shelf is None:
        return None
    data = read(db, market)
    have = any(d.books for d in data.values())
    lay_over(db, uid, data)
    mine = data[key]
    base = union(mine)
    under = [b for b in base if _fits(b, filters)]
    chosen = visible(under, hide_owned)
    if sort == "new":
        chosen.sort(key=lambda b: b.released or str(year_of(b) or ""), reverse=True)
    elif sort == "short":
        chosen.sort(key=lambda b: (pages_of(b) is None, pages_of(b) or 0))
    elif filters.section:
        chosen.sort(key=lambda b: b.places.get(filters.section, 10**6))

    def count(ignore: str, keep: Any) -> int:
        return sum(1 for b in visible([b for b in base if _fits(b, filters, ignore=ignore)], hide_owned) if keep(b))

    languages: dict[str, int] = {}
    for b in visible([b for b in base if _fits(b, filters, ignore="language")], hide_owned):
        if b.language:
            languages[b.language] = languages.get(b.language, 0) + 1
    return ShelfPage(
        shelf=shelf, market=market, sources=sources_state(db, market, have, data), total=len(chosen),
        owned=sum(1 for b in under if b.owned_work_id is not None),
        books=chosen[offset:offset + min(max(limit, 1), MAX_PAGE)],
        sections=[f for f in (Facet(s, count("section", lambda b, s=s: s in b.places)) for s in shelf.sections)
                  if f.count],
        topics=[f for f in (Facet(t.key, count("topic", lambda b, k=t.key: k in b.topics)) for t in shelf.topics)
                if f.count],
        languages=[Facet(code, n) for code, n in sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))],
        fetched_at=mine.fetched_at)


def authors_fit(book: ShelfBook, names: list[str]) -> bool:
    return any(authors_compatible(a, b) for a in _authors(book.row) for b in names)
