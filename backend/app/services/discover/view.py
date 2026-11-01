"""The answers of Discover, put together for the person who asks.

A list is the public list of a source (``lists_cache``) with the person's own things laid over it: does the book stand
on *their* shelf (``owned``), is it on *their* wish list. Nothing of that is stored with the list and nothing is
shared between two people, so the same list reads differently for two people with different libraries and says nothing
of the one to the other.

The cards of a list put together the entries of one book (Apple's e-book and Audible's recording are one card), keep
the numbers of the list (the rank), and follow the filters of the page: the format, the language, the genre and "hide
what I have".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ... import clock
from ...models import AUTHOR, Credit, Edition, ForeignBook, ListEntry, ListFetch, Person, Series, Work
from .. import genres, jobs, settings_service
from .. import works as works_service
from ..readers.names import authors_compatible, person_key
from ..readers.text import fold
from ..sources import config
from ..works import WorkView, views, visible_edition_clause
from . import foreign, foryou, markets, owned, wishlist
from . import lists as lists_service

#: The order sources speak in when two entries of one rank are one book.
SOURCE_ORDER = {"apple": 0, "audible": 1, "hardcover": 2, "openlibrary": 3, "dnb": 4}
MAX_OWN_BY_AUTHOR = 18


@dataclass
class Card:
    """One book of a list: its row (the one that speaks), the rows of the same book, where it stands in the list."""

    row: ForeignBook
    rows: list[ForeignBook]
    entries: list[ListEntry]
    rank: int | None = None
    kinds: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    language: str = ""
    genre: str = ""
    released: str = ""
    owned_work_id: int | None = None
    #: The cover file of the person's own work (``owned_work_id``), when it has one: shown from the library, never
    #: fetched from a source.
    owned_cover: str = ""
    wished: bool = False


@dataclass
class ListView:
    list: str
    market: str
    format: str
    language: str
    genre: str
    hide_owned: bool
    #: ``ready``, ``fetching``, ``empty``, ``failed``, ``main_off`` or ``no_source``.
    state: str
    #: The sources behind the list as they stand now: ``(source, state)``.
    availability: list[tuple[str, str]] = field(default_factory=list)
    #: Whether any source of the list may be asked now (when not, what is shown is what was kept).
    asking: bool = True
    sources: list[str] = field(default_factory=list)
    fetched_at: Any = None
    languages: list[tuple[str, int]] = field(default_factory=list)
    #: The cards that fit the filters (before hiding what the person has) and how many of them the person has.
    total: int = 0
    owned: int = 0
    cards: list[Card] = field(default_factory=list)


def color_of(row: ForeignBook) -> str:
    return row.color or genres.palette_color(row.genre, row.title_key or str(row.id))


def _authors(row: ForeignBook) -> list[str]:
    return [a for a in (row.authors or []) if isinstance(a, str)]


def lay_covers(db: Session, uid: int, cards: Iterable[Card]) -> None:
    """The cover of the work the person has of a book goes onto its card (the cover stays in the library, so the card
    needs none from a source). Only works the person sees; one query for all the cards."""
    pending = [card for card in cards if card.owned_work_id is not None and not card.owned_cover]
    if not pending:
        return
    names = works_service.cover_names(db, uid, sorted({c.owned_work_id for c in pending if c.owned_work_id}))
    for card in pending:
        card.owned_cover = names.get(card.owned_work_id or 0, "")


def owned_with_cover_by_all(db: Session, rows: list[ForeignBook]) -> set[int]:
    """The ids of the rows every account has on its shelf with a cover of its own: nobody needs the cover of a source
    for them."""
    from ...models import Account

    ids = list(db.scalars(select(Account.id)))
    if not ids or not rows:
        return set()
    sure = {row.id for row in rows}
    for uid in ids:
        shelf = owned.load(db, uid)
        found = {row.id: work for row in rows if row.id in sure and (work := foryou.find_owned(shelf, row)) is not None}
        named = works_service.cover_names(db, uid, sorted(set(found.values())))
        sure = {rid for rid, work in found.items() if work in named}
        if not sure:
            break
    return sure


def owned_work(shelf: owned.Shelf, rows: list[ForeignBook]) -> int | None:
    """The work of the person's shelf any row of a book is, or None."""
    for row in rows:
        found = foryou.find_owned(shelf, row)
        if found is not None:
            return found
    return None


# ---- A list ----------------------------------------------------------------------------------------------------


def _entries(db: Session, market: str, list_name: str) -> list[tuple[ListEntry, ForeignBook]]:
    chart = [source for source in markets.SOURCES_OF_LIST[list_name] if source != "hardcover"]
    clause = []
    if chart:
        clause.append(and_(ListEntry.source.in_(chart), ListEntry.market == market))
    if "hardcover" in markets.SOURCES_OF_LIST[list_name]:
        clause.append(and_(ListEntry.source == "hardcover", ListEntry.market == ""))
    return [(entry, book) for entry, book in db.execute(
        select(ListEntry, ForeignBook).join(ForeignBook, ForeignBook.id == ListEntry.foreign_id)
        .where(ListEntry.list == list_name, or_(*clause)).order_by(ListEntry.rank, ListEntry.source)).all()]


def _state(db: Session, market: str, list_name: str, have: bool) -> tuple[str, list[tuple[str, str]], bool]:
    units = lists_service.units_for(market, list_name)
    states = [(source, config.availability(db, source).state) for source, _area, _unit in units]
    asking = any(state == "ready" for _source, state in states)
    if have:
        return "ready", states, asking
    if not asking:
        return ("main_off" if not settings_service.get(db, "sources_enabled") else "no_source"), states, False
    rows = [db.get(ListFetch, (source, area, unit)) for source, area, unit in units]
    if any(row is None or row.fetched_at is None for row in rows):
        return "fetching", states, True
    if all(row is not None and row.state == "failed" for row in rows):
        return "failed", states, True
    return "empty", states, True


#: The lists the themed shelves fill when none of their own sources may be asked (Audible for the new ones and the ones
#: to come, Hardcover for the trends): the open sources alone then fill them, by the same strict rules as the shelves.
FROM_SHELVES = frozenset({"new", "soon", "trending"})
#: How many books a list made from the shelves holds.
SHELF_LIST_SIZE = 100


def _switched_on(db: Session, source: str) -> bool:
    """Whether the operator has a source on (and, for Hardcover, connected): what the main switch or a rest after errors
    does is not asked here, because then the list shows what was kept, as before."""
    if not settings_service.get(db, f"source_{source}"):
        return False
    if source == "hardcover":
        return config.availability(db, source).state not in ("not_connected", "token_invalid")
    return True


def uses_shelves(db: Session, list_name: str) -> bool:
    """Whether a list is made from the themed shelves: none of the sources it is the list of is on (Audible is off from
    the start, Hardcover needs a token of the instance's own)."""
    return list_name in FROM_SHELVES and not any(_switched_on(db, s) for s in markets.SOURCES_OF_LIST[list_name])


def _shelf_cards(db: Session, market: str, list_name: str) -> tuple[list[Card], Any]:
    """The books of the themed shelves of a market as one list: the new ones (newest first), the ones to come (the
    soonest first; only with a day a source gave), or what the shelves rank highest now (the first of every shelf,
    then the second, and so on). Returns the cards and the state of the shelves' sources."""
    from ..browse import view as browse

    data = browse.read(db, market)
    have = any(d.books for d in data.values())
    today = clock.now().date().isoformat()
    section = {"new": "new", "soon": "soon", "trending": "popular"}[list_name]
    columns = [browse.in_section(data[shelf.key], section) for shelf in browse.shelves.SHELVES]
    seen: set[str] = set()
    cards: list[Card] = []
    for rank in zip_longest(*columns):
        for book in rank:
            if book is None or book.key in seen:
                continue
            if list_name == "soon" and not (book.released and book.released > today):
                continue
            if list_name == "new" and book.released and book.released > today:
                continue
            seen.add(book.key)
            cards.append(book)
    if list_name == "new":
        cards.sort(key=lambda c: c.released or f"{browse.year_of(c) or 0:04d}", reverse=True)  # type: ignore[arg-type]
    elif list_name == "soon":
        cards.sort(key=lambda c: c.released)
    for place, card in enumerate(cards, start=1):
        card.rank = place
    return cards[:SHELF_LIST_SIZE], browse.sources_state(db, market, have, data)


def list_view(db: Session, uid: int, *, market: str, list_name: str, fmt: str = "all", language: str = "",
              genre: str = "", hide_owned: bool = True) -> ListView:
    """A list for a person: merged, ranked, filtered and set against their shelf."""
    if uses_shelves(db, list_name):
        return _list_of_shelves(db, uid, market=market, list_name=list_name, fmt=fmt, language=language, genre=genre,
                                hide_owned=hide_owned)
    found = _entries(db, market, list_name)
    state, states, asking = _state(db, market, list_name, bool(found))
    out = ListView(list=list_name, market=market, format=fmt, language=language, genre=genre, hide_owned=hide_owned,
                   state=state, availability=states, asking=asking)
    if not found:
        return out
    out.fetched_at = max(entry.fetched_at for entry, _row in found)
    groups: dict[str, Card] = {}
    for entry, row in found:
        key = foreign.group_key(row.title, _authors(row))
        card = groups.get(key)
        if card is None:
            card = groups[key] = Card(row=row, rows=[row], entries=[entry])
        else:
            if row.id not in {r.id for r in card.rows}:
                card.rows.append(row)
            card.entries.append(entry)
    cards = list(groups.values())
    if fmt != "all":
        cards = [c for c in cards if any(e.kind == fmt for e in c.entries)]
    ranked = list_name in markets.RANKED
    for card in cards:
        card.entries.sort(key=lambda e: (e.rank, SOURCE_ORDER.get(e.source, 9)))
        counted = [e for e in card.entries if fmt == "all" or e.kind == fmt]
        card.row = next((r for e in counted for r in card.rows if r.id == e.foreign_id), card.row)
        card.kinds = sorted({e.kind for e in card.entries})
        # The sources the card is shown for: with a format chosen, those that speak for that format.
        card.sources = sorted({e.source for e in (counted or card.entries)}, key=lambda s: SOURCE_ORDER.get(s, 9))
        card.language = next((e.language for e in card.entries if e.language), "") or next(
            (r.language for r in card.rows if r.language), "")
        card.genre = next((r.genre for r in card.rows if r.genre), "")
        card.released = next((e.released for e in card.entries if e.released), "") or card.row.released
        card.rank = min(e.rank for e in counted) if counted else card.entries[0].rank
    if ranked:
        cards.sort(key=lambda c: (c.rank or 0, SOURCE_ORDER.get(c.entries[0].source, 9)))
        for place, card in enumerate(cards, start=1):
            card.rank = place
    elif list_name == "new":
        cards.sort(key=lambda c: c.released, reverse=True)
    elif list_name == "soon":
        cards.sort(key=lambda c: c.released)
    else:
        cards.sort(key=lambda c: c.rank or 0)
    if not ranked:
        for card in cards:
            card.rank = None
    return _finish(db, uid, out, cards, list_name, fmt, language, genre, hide_owned)


def _list_of_shelves(db: Session, uid: int, *, market: str, list_name: str, fmt: str, language: str, genre: str,
                     hide_owned: bool) -> ListView:
    cards, state = _shelf_cards(db, market, list_name)
    out = ListView(list=list_name, market=market, format=fmt, language=language, genre=genre, hide_owned=hide_owned,
                   state=state.state, availability=state.availability, asking=state.asking, fetched_at=state.fetched_at)
    if fmt != "all":
        cards = [c for c in cards if fmt in c.kinds]
    if list_name not in markets.RANKED:
        for card in cards:
            card.rank = None
    out = _finish(db, uid, out, cards, list_name, fmt, language, genre, hide_owned)
    if not out.cards:
        out.sources = list(state.sources) or ["apple", "openlibrary"]
    return out


def _finish(db: Session, uid: int, out: ListView, cards: list[Card], list_name: str, fmt: str, language: str,
            genre: str, hide_owned: bool) -> ListView:
    """The filters of the page and the person's own things, the same for every list."""
    # The languages the list holds (before the language is chosen), for the filter.
    counts: dict[str, int] = {}
    for card in cards:
        if card.language:
            counts[card.language] = counts.get(card.language, 0) + 1
    out.languages = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if language:
        # A book whose language no source said (Apple's chart and Hardcover's trends do not say) is shown, not left out:
        # it may well be the one asked for.
        cards = [c for c in cards if c.language in (language, "")]
    if genre:
        cards = [c for c in cards if c.genre == genre]
    shelf = owned.load(db, uid)
    wished = wishlist.ids_of(db, uid)
    for card in cards:
        card.owned_work_id = owned_work(shelf, card.rows)
        card.wished = any(row.id in wished for row in card.rows)
    out.total = len(cards)
    out.owned = sum(1 for c in cards if c.owned_work_id is not None)
    out.cards = [c for c in cards if c.owned_work_id is None] if hide_owned else cards
    out.sources = sorted({s for c in out.cards for s in c.sources}, key=lambda s: SOURCE_ORDER.get(s, 9))
    if not out.sources:
        out.sources = [s for s in markets.SOURCES_OF_LIST[list_name] if fmt in ("all", markets.KIND_OF_SOURCE[s])]
    return out


# ---- The wish list ---------------------------------------------------------------------------------------------


def wish_cards(db: Session, uid: int) -> list[Card]:
    """The person's list as cards, the newest first, one card for a book (two entries of one book are one)."""
    shelf = owned.load(db, uid)
    cards: dict[str, Card] = {}
    for _wish, row in wishlist.listing(db, uid):
        key = foreign.group_key(row.title, _authors(row))
        if key in cards:
            continue
        rows = foreign.family(db, row)
        card = Card(row=row, rows=rows, entries=[], wished=True)
        card.kinds = sorted({f.get("kind") for f in foreign.forms_of(rows) if f.get("kind") in ("ebook", "audio")})
        card.sources = sorted({r.ref.split(":", 1)[0] for r in rows}, key=lambda s: SOURCE_ORDER.get(s, 9))
        card.language = row.language
        card.genre = row.genre
        card.released = row.released
        card.owned_work_id = owned_work(shelf, rows)
        cards[key] = card
    return list(cards.values())


# ---- The page of a book ----------------------------------------------------------------------------------------


@dataclass
class BookView:
    card: Card
    description: str
    forms: list[dict[str, Any]]
    year: int | None
    upcoming: bool
    #: The place in the series of the book and the volumes of that series (the person's own and the ones they lack).
    series: str
    volumes: list[foryou.Volume] = field(default_factory=list)
    series_pending: bool = False
    #: The works on the person's shelves by the same author.
    more: list[WorkView] = field(default_factory=list)
    own_series_works: dict[int, WorkView] = field(default_factory=dict)
    pending: bool = False


def own_views(db: Session, uid: int, work_ids: list[int]) -> dict[int, WorkView]:
    """The works of the person's shelves with these numbers, as they see them (a number they do not see is left
    out)."""
    if not work_ids:
        return {}
    found = views(db, uid, list(db.scalars(select(Work).where(Work.id.in_(work_ids)))))
    return {item.work.id: item for item in found}


def by_token(db: Session, token: str) -> ForeignBook | None:
    if not token or len(token) > 24 or not token.replace("-", "").replace("_", "").isalnum():
        return None
    return db.scalar(select(ForeignBook).where(ForeignBook.token == token))


def _own_by_author(db: Session, uid: int, authors: list[str]) -> list[WorkView]:
    keys = {person_key(name) for name in authors[:1]}
    people = [p for p in db.scalars(select(Person).where(Person.key.in_(keys)))] if keys else []
    if not people:
        return []
    ids = list(db.scalars(select(Edition.work_id).join(Credit, Credit.edition_id == Edition.id).where(
        Credit.person_id.in_([p.id for p in people]), Credit.role == AUTHOR,
        *visible_edition_clause(uid)).distinct()))
    works = list(db.scalars(select(Work).where(Work.id.in_(ids)).order_by(Work.id).limit(MAX_OWN_BY_AUTHOR))) \
        if ids else []
    return views(db, uid, works)


def book_view(db: Session, uid: int, row: ForeignBook, today: str | None = None) -> BookView:
    """The page of a book of a source, for a person. Brings no source into play: it reads what is kept, and queues
    what is missing (the other forms of the book, its cover) for the background."""
    today = today or clock.now().date().isoformat()
    rows = foreign.family(db, row)
    shelf = owned.load(db, uid)
    wished = wishlist.ids_of(db, uid)
    card = Card(row=row, rows=rows, entries=[], wished=any(r.id in wished for r in rows))
    forms = foreign.forms_of(rows)
    card.kinds = sorted({f.get("kind") for f in forms if f.get("kind") in ("ebook", "audio")})
    card.sources = sorted({r.ref.split(":", 1)[0] for r in rows}, key=lambda s: SOURCE_ORDER.get(s, 9))
    card.language, card.genre = row.language, row.genre or next((r.genre for r in rows if r.genre), "")
    card.released = next((r.released for r in [row, *rows] if r.released), "")
    card.owned_work_id = owned_work(shelf, rows)
    data = row.data if isinstance(row.data, dict) else {}
    description = str(data.get("description") or "") or next(
        (str(r.data.get("description") or "") for r in rows if isinstance(r.data, dict) and r.data.get("description")),
        "")
    year = data.get("year") if isinstance(data.get("year"), int) else None
    out = BookView(card=card, description=description, forms=forms, year=year or (
        int(card.released[:4]) if card.released[:4].isdigit() else None),
        upcoming=bool(card.released and card.released > today), series=row.series)
    # The cover and the colour: of the first row that has them.
    if not row.cover:
        for other in rows:
            if other.cover:
                row.color, row.cover = other.color, other.cover
                break
    if row.series:
        out.volumes, out.series_pending = _series_volumes(db, uid, row, today)
        out.own_series_works = own_views(db, uid, [v.work_id for v in out.volumes if v.work_id is not None])
    out.more = _own_by_author(db, uid, _authors(row))
    # What the sources did not say yet is looked for in the background: the other form of the book, the cover.
    if row.enriched_at is None and config.ready_sources(db, config.CATALOGUES):
        jobs.enqueue(db, "fenrich", ref=row.id)
        out.pending = True
    db.commit()
    return out


def _series_volumes(db: Session, uid: int, row: ForeignBook, today: str) -> tuple[list[foryou.Volume], bool]:
    """The series of a book: the person's volumes and the ones they lack when they read it; else the volumes that are
    known for the series (public)."""
    key = fold(row.series)[:200]
    series_id = db.scalar(select(Series.id).where(Series.key == key))
    mine = foryou.series_card(db, uid, series_id, today) if series_id is not None else None
    if mine is not None:
        volumes = list(mine.volumes)
        if row.series_pos is not None and all(v.pos != row.series_pos for v in volumes):
            volumes.append(foryou.Volume(pos=row.series_pos, state="soon" if row.released > today else "missing",
                                         book=row))
        return sorted(volumes, key=lambda v: v.pos), mine.pending
    volumes = {}
    for other in db.scalars(select(ForeignBook).where(ForeignBook.series != "", ForeignBook.series_pos.is_not(None))
                            .where(ForeignBook.series.ilike(row.series)).limit(80)):
        if other.series_pos is None or (other.authors and row.authors and not any(
                authors_compatible(a, b) for a in _authors(row) for b in _authors(other))):
            continue
        volumes.setdefault(other.series_pos, foryou.Volume(
            pos=other.series_pos, state="soon" if other.released > today else "missing", book=other))
    if row.series_pos is not None:
        volumes[row.series_pos] = foryou.Volume(pos=row.series_pos, state="soon" if row.released > today else "missing",
                                                book=row)
    return sorted(volumes.values(), key=lambda v: v.pos), False
