"""Browse: the themed shelves of Discover, like a bookshop.

* ``GET /discover/browse``: the wall of thirteen shelves in three departments, each with a strip of books.
* ``GET /discover/shelves/{key}``: one shelf opened, with the filters of the page (section, language, format, age,
  topic,
  sort), paged.
* ``GET /discover/browse/personal``: the shelves made for the caller from what they read ("because you read ...").

**Public data, a private view.** The shelves are what the open sources say about a topic, the same for everybody.
What the
caller *has* and what they put on their wish list is worked out from the libraries they may see and from nothing
else; the
personal shelves are made from their own history on this server, and nothing of it is asked of a source.

Nothing here asks a source while the caller waits. A shelf that was never fetched is queued for the background and the
answer says ``fetching``; with the operator's main switch off the answer says what is missing, and what was kept is
still
shown. Audible and Hardcover take no part: a shelf is full without them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ..deps import DbSession, Reader
from ..errors import error
from ..services import brakes
from ..services.browse import personal, shelves, view
from ..services.browse.view import ShelfBook
from ..services.discover import markets
from ..services.discover import view as discover_view
from ..services.readers.text import clean
from .auth import profile_of
from .discover import AvailabilityOut, CardOut, SourceName, card_out, market_of, today_text
from .works import WorkBrief, brief_of

router = APIRouter(prefix="/api/v1", tags=["discover"])

SectionName = Literal["new", "popular", "award", "classic"]
MarketParam = Annotated[str, Query(max_length=2, pattern=r"^([a-z]{2})?$", description="Empty: the caller's choice")]
BLURB_MAX = 700


class ShelfCardOut(CardOut):
    """A book on a shelf: the card of a list, and what the cover turned over says."""

    #: The prize the source names for the book (a section "award"), as the source writes it; empty for none.
    award: str
    #: The topics of the shelf the book is about.
    topics: list[str]
    pages: int | None
    #: The longest recording of the book, in minutes, when a source says (Audible does; the open sources do not).
    minutes: int | None
    #: The year of the book (the work's first year, or the year of the edition).
    year: int | None
    #: The words on the back cover when a source gave them (kept for a day, like the cover); empty otherwise.
    blurb: str


class TileOut(BaseModel):
    key: str
    #: How many books the shelf holds (not counting the ones the caller has when those are hidden), and how many of
    #: them are new.
    books: int
    new: int
    #: Three covers for the tile: the books most wanted that have a picture.
    fan: list[CardOut]


class DepartmentOut(BaseModel):
    key: Literal["bel", "sach", "kj"]
    shelves: list[TileOut]


class SectionCountOut(BaseModel):
    key: SectionName
    count: int


class StripOut(BaseModel):
    shelf: str
    #: The section the strip shows, and the sections the shelf has books for (at least three).
    section: SectionName
    sections: list[SectionCountOut]
    items: list[ShelfCardOut]


class Sourcing(BaseModel):
    #: ``ready``; ``fetching`` (parts of the shelves were never fetched and a job is on it); ``empty``; ``failed``;
    #: ``main_off`` (the operator's main switch); ``no_source`` (no source of the shelves is on).
    state: Literal["ready", "fetching", "empty", "failed", "main_off", "no_source"]
    availability: list[AvailabilityOut]
    #: Whether any source of the shelves may be asked now. When not, the books are what was kept earlier.
    asking: bool
    #: Parts of the shelves are still being fetched: the page asks again after a few seconds.
    pending: bool
    #: The sources the books shown come from (the label "Source: ..." names these).
    sources: list[SourceName]
    fetched_at: datetime | None


class BrowseOut(Sourcing):
    market: str
    markets: list[str]
    hide_owned: bool
    departments: list[DepartmentOut]
    strips: list[StripOut]


class PersonalRowOut(BaseModel):
    key: Literal["because", "like", "short", "long"]
    #: "because": the author and genre of the book read last; "like": nothing.
    author: str
    genre: str
    #: The book read last, or the favourites (their covers stand beside the title of the row).
    reads: list[WorkBrief]
    items: list[ShelfCardOut]


class PersonalOut(BaseModel):
    rows: list[PersonalRowOut]


class FacetOut(BaseModel):
    key: str
    count: int


class YearsOut(BaseModel):
    #: "New" is this year and the year before (``new_from`` on), "middle" back to ``mid_from``, "old" before it.
    new_from: int
    mid_from: int


class ShelfOut(Sourcing):
    shelf: str
    dept: Literal["bel", "sach", "kj"]
    market: str
    markets: list[str]
    hide_owned: bool
    section: SectionName | None
    language: str
    format: Literal["all", "ebook", "audio"]
    years: Literal["", "new", "mid", "old"]
    topic: str
    sort: Literal["pop", "new", "short"]
    #: The books under the filters, and how many of them the caller has; the page below.
    total: int
    owned: int
    offset: int
    limit: int
    sections: list[FacetOut]
    languages: list[FacetOut]
    topics: list[FacetOut]
    year_bounds: YearsOut
    items: list[ShelfCardOut]


# ---- Putting the answers together ----------------------------------------------------------------------------------


def _blurb(book: ShelfBook) -> str:
    for row in book.rows:
        data = row.data if isinstance(row.data, dict) else {}
        text = data.get("description")
        if isinstance(text, str) and text.strip():
            return clean(text, BLURB_MAX)
    return ""


def shelf_card(book: ShelfBook, today: str) -> ShelfCardOut:
    base = card_out(book, today, rank=False)
    return ShelfCardOut(
        **base.model_dump(), award=book.award, topics=book.topics, pages=view.pages_of(book),
        minutes=personal.minutes_of(book) or None, year=view.year_of(book), blurb=_blurb(book))


def _sourcing(state: view.Sources) -> dict[str, object]:
    return {
        "state": state.state, "availability": [AvailabilityOut(source=s, state=st) for s, st in state.availability],
        "asking": state.asking, "pending": state.pending, "sources": state.sources, "fetched_at": state.fetched_at}


def _hide(account: Any, hide_owned: bool | None) -> bool:
    return bool(profile_of(account.profile)["discover_hide_owned"]) if hide_owned is None else hide_owned


HideOwned = Annotated[bool | None, Query(description="Empty: the caller's choice (on from the start)")]


# ---- The routes ----------------------------------------------------------------------------------------------------


@router.get("/discover/browse", response_model=BrowseOut,
            summary="The wall of the themed shelves, with a strip of books for each")
def read_browse(request: Request, account: Reader, db: DbSession, market: MarketParam = "",
                hide_owned: HideOwned = None) -> BrowseOut:
    brakes.take("discover", account.id)
    chosen = market_of(account, request, market)
    hide = _hide(account, hide_owned)
    view.want(db, chosen)
    shown = view.wall(db, account.id, chosen, hide_owned=hide)
    today = today_text()
    covered = [b for strip in shown.strips for b in strip.books] + [b for tile in shown.tiles for b in tile.fan]
    discover_view.lay_covers(db, account.id, covered)
    tiles = {tile.shelf.key: tile for tile in shown.tiles}
    departments = [
        DepartmentOut(key=dept, shelves=[
            TileOut(key=shelf.key, books=tiles[shelf.key].books, new=tiles[shelf.key].new,
                    fan=[card_out(b, today, rank=False) for b in tiles[shelf.key].fan])
            for shelf in shelves.SHELVES if shelf.dept == dept])
        for dept in shelves.DEPARTMENTS]
    strips = [StripOut(shelf=strip.shelf.key, section=strip.section,  # type: ignore[arg-type]
                       sections=[SectionCountOut(key=c.key, count=c.count) for c in strip.sections],  # type: ignore[arg-type]
                       items=[shelf_card(b, today) for b in strip.books]) for strip in shown.strips]
    return BrowseOut(market=chosen, markets=list(markets.MARKETS), hide_owned=hide, departments=departments,
                     strips=strips, **_sourcing(shown.sources))  # type: ignore[arg-type]


@router.get("/discover/browse/personal", response_model=PersonalOut,
            summary="The shelves made for the caller from what they read, on this server only")
def read_personal(request: Request, account: Reader, db: DbSession, market: MarketParam = "",
                  hide_owned: HideOwned = None) -> PersonalOut:
    brakes.take("discover", account.id)
    chosen = market_of(account, request, market)
    hide = _hide(account, hide_owned)
    data = view.read(db, chosen)
    view.lay_over(db, account.id, data)
    rows = personal.make(db, account.id, data, hide_owned=hide)
    today = today_text()
    discover_view.lay_covers(db, account.id, [b for row in rows for b in row.books])
    return PersonalOut(rows=[
        PersonalRowOut(key=row.key, author=row.author, genre=row.genre,  # type: ignore[arg-type]
                       reads=[brief_of(item) for item in row.reads], items=[shelf_card(b, today) for b in row.books])
        for row in rows])


@router.get("/discover/shelves/{key}", response_model=ShelfOut,
            summary="One themed shelf, opened: its books under the filters of the page")
def read_shelf(key: str, request: Request, account: Reader, db: DbSession, market: MarketParam = "",
               section: Annotated[SectionName | Literal[""], Query()] = "",
               language: Annotated[str, Query(max_length=2, pattern=r"^([a-z]{2})?$")] = "",
               format: Literal["all", "ebook", "audio"] = "all",
               years: Literal["", "new", "mid", "old"] = "",
               topic: Annotated[str, Query(max_length=24, pattern=r"^[a-z0-9]*$")] = "",
               sort: Literal["pop", "new", "short"] = "pop",
               hide_owned: HideOwned = None,
               limit: Annotated[int, Query(ge=1, le=view.MAX_PAGE)] = view.PAGE,
               offset: Annotated[int, Query(ge=0, le=5000)] = 0) -> ShelfOut:
    brakes.take("discover", account.id)
    chosen = market_of(account, request, market)
    hide = _hide(account, hide_owned)
    if key not in shelves.BY_KEY:
        raise error("not_found", "Not found.", 404)
    view.want(db, chosen)
    page = view.shelf_page(db, account.id, chosen, key,
                           filters=view.Filters(section=section, language=language, fmt=format, years=years,
                                                topic=topic),
                           sort=sort, hide_owned=hide, limit=limit, offset=offset)
    assert page is not None
    discover_view.lay_covers(db, account.id, page.books)
    today = today_text()
    new_from, mid_from = view.year_bounds()
    return ShelfOut(
        shelf=key, dept=page.shelf.dept, market=chosen, markets=list(markets.MARKETS), hide_owned=hide,  # type: ignore[arg-type]
        section=section or None, language=language, format=format, years=years, topic=topic, sort=sort,  # type: ignore[arg-type]
        total=page.total, owned=page.owned, offset=offset, limit=limit,
        sections=[FacetOut(key=f.key, count=f.count) for f in page.sections],
        languages=[FacetOut(key=f.key, count=f.count) for f in page.languages],
        topics=[FacetOut(key=f.key, count=f.count) for f in page.topics],
        year_bounds=YearsOut(new_from=new_from, mid_from=mid_from),
        items=[shelf_card(b, today) for b in page.books], **_sourcing(page.sources))  # type: ignore[arg-type]

