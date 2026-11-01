"""Discover: the lists of the sources set against what the caller has, what to read next, books that are not on a
shelf, and the wish list.

**Public data, a private view.** The lists, the volumes of a series and the pages of the books are what the sources
say, the same for everybody. What the caller *has* (the plaque "on the shelf", the switch that hides it, the number of
books that were hidden, the gaps in their series) is worked out from the libraries the caller is a member of and from
nothing else, and the wish list is the caller's own: nobody else reads it, the operator included
(``services/discover``).

Nothing here asks a source while the caller waits. A list that was never fetched, or is old, is queued for the
background and the answer says ``fetching``; with the operator's switches off the answer says what is missing, and
what was kept is still shown. The pages of books are reached by a ``token`` nobody can count up to.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import clock
from ..deps import Account, DbSession, Reader
from ..errors import error
from ..models import ForeignBook
from ..services import brakes
from ..services.discover import enrich, foreign, foryou, markets, pics, view, wishlist
from ..services.discover import lists as lists_service
from ..services.discover.view import Card
from ..services.oeuvre import watch as watching
from ..services.works import WorkView
from .auth import interface_language, profile_of
from .catalog import AuthorHit, foreign_hits
from .works import WorkBrief, brief_of

# The jobs of Discover are taught to the queue by importing their modules: the other forms of a book are looked for by
# ``enrich``, which nothing else imports.
_JOBS = (enrich,)

router = APIRouter(prefix="/api/v1", tags=["discover"])

SourceName = Literal["hardcover", "openlibrary", "audible", "dnb", "apple", "bnf", "libris", "kb", "nb"]
ListName = Literal["best", "new", "soon", "trending"]
GenreName = Literal["roman", "krimi", "fantasy", "scifi", "klassiker", "sachbuch", "kinder", "lyrik"]


class FormBrief(BaseModel):
    kind: Literal["ebook", "audio"]
    language: str
    #: ``unabridged``, ``abridged`` or ``dramatized`` for a recording; empty for an e-book.
    variant: str


class FormOut(FormBrief):
    #: Of a recording: the running time in minutes and who reads; of an e-book: the pages when a source says.
    minutes: int | None
    narrators: list[str]
    pages: int | None
    source: SourceName


class CardOut(BaseModel):
    """A book of a source that is not (necessarily) on a shelf."""

    #: The name of its page (``/api/v1/discover/books/{token}``).
    token: str
    title: str
    authors: list[str]
    #: The place in the list (lists with numbers only).
    rank: int | None
    #: What the book is known as: an e-book, a recording, both.
    kinds: list[Literal["ebook", "audio"]]
    forms: list[FormBrief]
    language: str
    #: The day it came out or comes out, ``YYYY-MM-DD``, and whether that day is after today.
    released: str
    upcoming: bool
    genre: str
    #: The main colour of the cover, ``#rrggbb`` (a colour of the genre's palette without a cover).
    color: str
    #: The address of the cover (fetched from the source by the server); null: the interface draws one.
    cover_url: str | None
    sources: list[SourceName]
    #: The book on the caller's own shelf this is, when it is one (only books they may see).
    owned_work_id: int | None
    wished: bool
    series: str
    series_pos: float | None


class AvailabilityOut(BaseModel):
    source: SourceName
    #: ``ready``, ``main_off``, ``off``, ``not_connected``, ``token_invalid`` or ``resting``.
    state: str


class LanguageCount(BaseModel):
    code: str
    count: int


class ListOut(BaseModel):
    list: ListName
    market: str
    format: Literal["all", "ebook", "audio"]
    language: str
    genre: str
    hide_owned: bool
    #: ``ready``; ``fetching`` (never fetched, a job is on it); ``empty`` (the source answered with nothing);
    #: ``failed``; ``main_off`` (the operator's main switch); ``no_source`` (no source of this list is on or
    #: connected).
    state: Literal["ready", "fetching", "empty", "failed", "main_off", "no_source"]
    availability: list[AvailabilityOut]
    #: Whether any source of the list may be asked now. When not, the cards are what was kept earlier.
    asking: bool
    #: The sources the books shown come from (the label "Source: ..." names these).
    sources: list[SourceName]
    fetched_at: datetime | None
    #: The languages the list holds, the most books first.
    languages: list[LanguageCount]
    markets: list[str]
    #: The books that fit the filters, and how many of them are on the caller's shelves.
    total: int
    owned: int
    items: list[CardOut]


class VolumeOut(BaseModel):
    pos: float
    #: ``own`` (on the caller's shelf: ``work``), ``missing`` (out, not on their shelf: ``book``), ``soon`` (yet to
    #: come).
    state: Literal["own", "missing", "soon"]
    work: WorkBrief | None
    book: CardOut | None


class SeriesGapOut(BaseModel):
    series_id: int
    name: str
    author: str
    have: int
    #: Volumes that are out, and how many of them the caller lacks; how many are yet to come out.
    out: int
    missing: int
    soon: int
    #: Nothing is kept for the series yet; it is being looked up.
    pending: bool
    volumes: list[VolumeOut]


class AuthorMoreOut(BaseModel):
    person_id: int
    name: str
    #: How many books of theirs are on the caller's shelves.
    on_shelf: int
    pending: bool
    books: list[CardOut]


class ForYouOut(BaseModel):
    state: Literal["ready", "main_off", "no_source"]
    sources: list[SourceName]
    pending: bool
    gaps: list[SeriesGapOut]
    authors: list[AuthorMoreOut]


class BookOut(CardOut):
    description: str
    year: int | None
    #: Every form the sources know the book in.
    forms_known: list[FormOut]
    #: The series: the caller's volumes and the ones they lack (empty without a series).
    volumes: list[VolumeOut]
    series_pending: bool
    #: Books of the same author that are on the caller's shelves.
    more: list[WorkBrief]
    #: The other forms of the book are being looked for.
    pending: bool
    #: The authors, each with the page of their works and what the caller has of them: "Über ...".
    about: list[AuthorHit] = []


class WishList(BaseModel):
    count: int
    items: list[CardOut]
    #: The books a watch of an author put on the list: the token of the book to the author's name, and why it is not
    #: wished when the caller may ask for books (``language:en`` announced only in another language, ``reissue``,
    #: ``adaptation``, ``companion``, ``edition``, ``play``, ``part``, ``box``, ``library``).
    via: dict[str, str] = {}
    via_why: dict[str, str] = {}


# ---- Putting the answers together ----------------------------------------------------------------------------------


def card_out(card: Card, today: str, *, rank: bool = True) -> CardOut:
    row = card.row
    forms = [FormBrief(kind=f["kind"], language=str(f.get("language") or ""), variant=str(f.get("variant") or ""))
             for f in (foreign.forms_of(card.rows) if card.rows else []) if f.get("kind") in ("ebook", "audio")]
    return CardOut(
        token=row.token, title=row.title, authors=[a for a in row.authors or [] if isinstance(a, str)],
        rank=card.rank if rank else None, kinds=card.kinds,  # type: ignore[arg-type]
        forms=forms, language=card.language, released=card.released, upcoming=bool(card.released > today),
        genre=card.genre, color=view.color_of(row),
        cover_url=(f"/api/v1/works/{card.owned_work_id}/cover?v={card.owned_cover[:12]}"
                   if card.owned_work_id is not None and card.owned_cover else pics.link_of(row)),
        sources=card.sources,  # type: ignore[arg-type]
        owned_work_id=card.owned_work_id, wished=card.wished, series=row.series, series_pos=row.series_pos)


def _plain_card(row: ForeignBook, today: str, *, owned_id: int | None = None, wished: bool = False) -> CardOut:
    """A card for a single row (a volume of a series, a book of an author)."""
    kind = "audio" if row.ref.startswith("audible:") else "ebook"
    card = Card(row=row, rows=[row], entries=[], kinds=[kind], sources=[row.ref.split(":", 1)[0]],
                language=row.language, genre=row.genre, released=row.released, owned_work_id=owned_id, wished=wished)
    return card_out(card, today, rank=False)


def _volumes(items: list[foryou.Volume], own: dict[int, WorkView], today: str) -> list[VolumeOut]:
    out = []
    for volume in items:
        brief = brief_of(own[volume.work_id]) if volume.work_id in own else None
        out.append(VolumeOut(pos=volume.pos, state=volume.state,  # type: ignore[arg-type]
                             work=brief, book=_plain_card(volume.book, today) if volume.book is not None else None))
    return out


def today_text() -> str:
    return clock.now().date().isoformat()


def market_of(account: Any, request: Request, asked: str) -> str:
    chosen = asked or str(profile_of(account.profile).get("market") or "")
    return markets.market_of(chosen, interface_language(request) or account.language)


# ---- The routes ------------------------------------------------------------------------------------------------


MarketParam = Annotated[str, Query(max_length=2, pattern=r"^([a-z]{2})?$", description="Empty: the caller's choice")]


@router.get("/discover/lists/{name}", response_model=ListOut,
            summary="A list of the sources (best-selling, new, to come, trending) set against the caller's shelves")
def read_list(name: ListName, request: Request, account: Reader, db: DbSession, market: MarketParam = "",
              format: Literal["all", "ebook", "audio"] = "all",
              language: Annotated[str, Query(max_length=2, pattern=r"^([a-z]{2})?$")] = "",
              genre: Annotated[GenreName | Literal[""], Query()] = "",
              hide_owned: Annotated[bool | None, Query(description="Empty: the caller's choice (on from the start)")]
              = None) -> ListOut:
    brakes.take("discover", account.id)
    chosen = market_of(account, request, market)
    profile = profile_of(account.profile)
    hide = bool(profile["discover_hide_owned"]) if hide_owned is None else hide_owned
    lists_service.want(db, chosen, (name,))
    if view.uses_shelves(db, name):
        # Without its own sources the list is made from the themed shelves: those are kept fresh as when Browse is open.
        from ..services.browse import fetch as shelves_fetch

        shelves_fetch.want(db, chosen)
    shown = view.list_view(db, account.id, market=chosen, list_name=name, fmt=format, language=language, genre=genre,
                           hide_owned=hide)
    view.lay_covers(db, account.id, shown.cards)
    today = today_text()
    return ListOut(
        list=name, market=chosen, format=format, language=language, genre=genre, hide_owned=hide,
        state=shown.state,  # type: ignore[arg-type]
        availability=[AvailabilityOut(source=s, state=st) for s, st in shown.availability],  # type: ignore[arg-type]
        asking=shown.asking, sources=shown.sources,  # type: ignore[arg-type]
        fetched_at=shown.fetched_at, languages=[LanguageCount(code=c, count=n) for c, n in shown.languages],
        markets=list(markets.MARKETS), total=shown.total, owned=shown.owned,
        items=[card_out(card, today) for card in shown.cards])


@router.get("/discover/for-you", response_model=ForYouOut,
            summary="Gaps in the series the caller reads, and more of the authors on their shelves")
def for_you(account: Reader, db: DbSession) -> ForYouOut:
    brakes.take("discover", account.id)
    shown = foryou.compute(db, account.id)
    today = today_text()
    gaps = []
    for card in shown.gaps:
        own = view.own_views(db, account.id, [v.work_id for v in card.volumes if v.work_id])
        gaps.append(SeriesGapOut(series_id=card.series_id, name=card.name, author=card.author, have=card.have,
                                 out=card.out, missing=card.missing, soon=card.soon, pending=card.pending,
                                 volumes=_volumes(card.volumes, own, today)))
    authors = [AuthorMoreOut(person_id=a.person_id, name=a.name, on_shelf=a.on_shelf, pending=a.pending,
                             books=[_plain_card(b, today) for b in a.books]) for a in shown.authors]
    return ForYouOut(state=shown.state, sources=shown.sources, pending=shown.pending,  # type: ignore[arg-type]
                     gaps=gaps, authors=authors)


@router.get("/discover/series/{series_id}", response_model=SeriesGapOut,
            summary="The volumes of a series the caller reads, with the ones they lack (404: not a series of theirs)")
def series_gaps(series_id: int, account: Reader, db: DbSession) -> SeriesGapOut:
    brakes.take("discover", account.id)
    card = foryou.series_card(db, account.id, series_id)
    if card is None:
        raise error("not_found", "Not found.", 404)
    own = view.own_views(db, account.id, [v.work_id for v in card.volumes if v.work_id])
    return SeriesGapOut(series_id=card.series_id, name=card.name, author=card.author, have=card.have, out=card.out,
                        missing=card.missing, soon=card.soon, pending=card.pending,
                        volumes=_volumes(card.volumes, own, today_text()))


def _form(f: dict[str, Any]) -> FormOut:
    return FormOut(kind=f["kind"], language=str(f.get("language") or ""), variant=str(f.get("variant") or ""),
                   minutes=f.get("minutes") if isinstance(f.get("minutes"), int) else None,
                   narrators=[n for n in f.get("narrators") or [] if isinstance(n, str)],
                   pages=f.get("pages") if isinstance(f.get("pages"), int) else None,
                   source=f.get("source", "audible"))


@router.get("/discover/books/{token}", response_model=BookOut,
            summary="The page of a book of the sources that is not (necessarily) on a shelf")
def read_book(token: str, account: Reader, db: DbSession) -> BookOut:
    brakes.take("discover", account.id)
    row = view.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    today = today_text()
    shown = view.book_view(db, account.id, row, today)
    view.lay_covers(db, account.id, [shown.card])
    card = card_out(shown.card, today, rank=False)
    return BookOut(
        **card.model_dump(), description=shown.description, year=shown.year,
        forms_known=[_form(f) for f in shown.forms if f.get("kind") in ("ebook", "audio")],
        volumes=_volumes(shown.volumes, shown.own_series_works, today), series_pending=shown.series_pending,
        more=[brief_of(v) for v in shown.more], pending=shown.pending,
        about=foreign_hits(db, account.id, "", [(name, "") for name in card.authors[:2]]))


@router.get("/discover/books/{token}/cover", summary="The cover of a book of the sources, as a WebP picture",
            response_model=None, responses={200: {"content": {"image/webp": {}}}, 304: {"description": "Not changed"},
                                            404: {"description": "No cover"}, 429: {"description": "Try again later"}})
def book_cover(token: str, request: Request, account: Reader, db: DbSession,
               v: Annotated[str | None, Query(max_length=40)] = None) -> Response:
    """The stored cover, else the one the source has, fetched now (once for two who ask together, within the turn and
    the day of the source) and kept for a day. Without one the interface draws its own."""
    row = view.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    try:
        name = foreign.cover_for(db, row, account.id)
    except foreign.NoCover as exc:
        raise error("no_cover", "No cover.", 404) from exc
    except foreign.CoverBusy as exc:
        busy = error("busy", "Try again in a moment.", 429)
        busy.headers = {"Retry-After": str(exc.retry_after)}
        raise busy from exc
    path = pics.path_of(name)
    if path is None:
        raise error("no_cover", "No cover.", 404)
    etag = '"' + hashlib.sha256(name.encode()).hexdigest()[:20] + '"'
    stable = v is not None and name.startswith(v) and len(v) >= 12
    headers = {"ETag": etag, "X-Content-Type-Options": "nosniff",
               "Cache-Control": "private, max-age=31536000, immutable" if stable else "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)


def _wish_list(db: DbSession, uid: int) -> WishList:
    cards = view.wish_cards(db, uid)
    view.lay_covers(db, uid, cards)
    today = today_text()
    vias = watching.via_of_list(db, uid)
    found = {c.row.token: hit for c in cards if (hit := next((vias[r.id] for r in [c.row, *c.rows] if r.id in vias),
                                                             None))}
    return WishList(count=len(cards), items=[card_out(c, today, rank=False) for c in cards],
                    via={token: name for token, (name, _why) in found.items()},
                    via_why={token: why for token, (_name, why) in found.items() if why})


@router.get("/wishlist", response_model=WishList, summary="The caller's own wish list, the newest first")
def read_wishlist(account: Reader, db: DbSession) -> WishList:
    brakes.take("discover", account.id)
    return _wish_list(db, account.id)


@router.put("/wishlist/{token}", response_model=WishList, summary="Put a book on the caller's wish list")
def wish(token: str, account: Account, db: DbSession) -> WishList:
    brakes.take("wish", account.id)
    row = view.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    try:
        wishlist.add(db, account.id, row)
    except wishlist.Full:
        raise error("wishlist_full", "The wish list is full.", 409, limit=wishlist.MAX_WISHES) from None
    return _wish_list(db, account.id)


@router.delete("/wishlist/{token}", response_model=WishList, summary="Take a book off the caller's wish list")
def unwish(token: str, account: Account, db: DbSession) -> WishList:
    brakes.take("wish", account.id)
    row = view.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    wishlist.remove(db, account.id, row)
    return _wish_list(db, account.id)
