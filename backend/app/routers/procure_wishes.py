"""Fetching books, the wishes: asking for a book, seeing what is wished, searching again, and the
numbers for the head of the page.

Everything here needs the right ``acquire`` (or ``manage``) in a library that takes the kind of book, except the
numbers (``/procure/counts`` answers ``allowed: false`` to anybody else) and the options of a book (they say what the
caller may do). A wish is seen by everybody who may ask for books in the library it goes into; there is one open wish
per book, kind and language, whoever asked.

* ``POST /procure/wishes`` makes the wishes of one or many books (the same kinds, language and library for all) and
  answers for each book and kind what became of it. It searches at once and, with the main switch of the automatic work
  on, loads the best release that fits.
* ``GET /procure/wishes`` lists what is wished and not on its way yet; ``DELETE`` takes a wish away; ``POST .../search``
  searches again now.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import Account, DbSession, Reader
from ..errors import error
from ..models import OPERATOR, Download, DownloadClient, Indexer, Library, ProcureWish
from ..models import Account as AccountRow
from ..services import libraries
from ..services.oeuvre import watch as watching
from ..services.procure import automation, formats, search, wishes
from ..services.procure.wishes import SEEKING

logger = logging.getLogger("nexbooks.procure")

router = APIRouter(prefix="/api/v1/procure", tags=["procure"])

Kind = Literal["ebook", "audio"]
UNREACHABLE = ("client_unreachable", "client_timeout", "client_blocked")


class ProcureBookOut(BaseModel):
    work_id: int | None
    book: str | None = Field(description="The token of the book of the sources; null for a work of a library.")
    title: str
    authors: list[str]
    series: str
    series_pos: float | None
    genre: str
    color: str
    cover_url: str | None


class LibraryRef(BaseModel):
    id: int
    name: str


class TargetRef(LibraryRef):
    writable: bool = Field(description="Whether nexbooks may write into the library (a library mounted read-only is no "
                                       "place for a book).")
    write_problem: str = Field(description="Why not: ``missing``, ``read_only`` or ``no_permission``; empty when it "
                                           "may.")


class WishOut(BaseModel):
    id: int
    kind: Kind
    language: str = Field(description="A language code; empty for every language the operator accepts.")
    state: Literal["searching", "idle", "not_found", "waiting", "capped", "upgrade", "loading", "done"]
    detail: dict[str, Any] | None = Field(description="Why: ``total`` and ``reasons`` [{code, count}] of a search that "
                                                       "found nothing, ``have`` and ``goal`` of an upgrade, "
                                                       "``indexer`` and ``until`` of a limit, ``load_error``, "
                                                       "``error``, ``reason`` (``language_off`` with the ``language`` "
                                                       "no longer allowed), ``look``: how many releases only doubts "
                                                       "stand against, for a person to look at.")
    library: LibraryRef | None
    by: str = Field(description="Who asked.")
    mine: bool
    created_at: datetime
    searched_at: datetime | None
    next_try_at: datetime | None
    release_date: str = Field(description="``YYYY-MM-DD`` while the book is not out yet; else empty.")
    waiting_for_a_place: bool = Field(description="Searching, but the person has no free place for a search yet.")
    handing_over_again: bool = Field(description="A release was found and the download program did not answer: it is "
                                                 "handed over again in a few minutes, no new search.")
    book: ProcureBookOut
    via: str = Field(default="", description="The name of the author whose watching made this wish, on the caller's "
                                              "own wishes; empty when it was wished by hand, and on the wishes of "
                                              "other people (whom a person watches is theirs).")


def _book(info: wishes.BookInfo) -> ProcureBookOut:
    return ProcureBookOut(work_id=info.work_id, book=info.token, title=info.title, authors=info.authors,
                          series=info.series, series_pos=info.position, genre=info.genre, color=info.color,
                          cover_url=info.cover_url)


def _wish(db: DbSession, account: Any, row: ProcureWish, info: wishes.BookInfo, via: str | None = None) -> WishOut:
    library = db.get(Library, row.library_id) if row.library_id is not None else None
    owner = db.get(AccountRow, row.user_id)
    return WishOut(
        id=row.id, kind=row.kind, language=row.language, state=row.state,  # type: ignore[arg-type]
        detail=row.detail, library=LibraryRef(id=library.id, name=library.name) if library else None,
        by=(owner.display_name or owner.name) if owner is not None else "", mine=row.user_id == account.id,
        created_at=row.created_at, searched_at=row.searched_at, next_try_at=row.next_try_at,
        release_date=row.release_date, waiting_for_a_place=row.state == "searching" and row.search_id is None,
        handing_over_again=row.handover is not None, book=_book(info),
        via="" if row.user_id != account.id else via if via is not None else watching.via_of_wishes(
            db, [row.id]).get(row.id, ""))


class WishesOut(BaseModel):
    wishes: list[WishOut]


class ItemIn(BaseModel):
    work_id: int | None = Field(default=None, ge=1, description="A work of a library the caller sees.")
    book: str | None = Field(default=None, max_length=24, description="The token of a book of the sources.")


class WishesIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=wishes.MAX_ITEMS)
    kinds: list[Kind] = Field(min_length=1, max_length=2)
    language: str | None = Field(default=None, min_length=2, max_length=8,
                                 description="The language wished (a code), one the operator allows "
                                             "(``language_not_accepted`` else); by default every language he allows.")
    library_id: int | None = Field(default=None, ge=1,
                                   description="The library to put the books in; by kind the operator's choice by "
                                               "default.")


class WishMadeOut(BaseModel):
    index: int = Field(description="The place of the book in ``items``.")
    kind: Kind
    status: Literal["created", "exists", "have", "error"]
    code: str = Field(description="For ``error``: the code of the failure.")
    wish: WishOut | None


class WishesMadeOut(BaseModel):
    results: list[WishMadeOut]


@router.get("/wishes", response_model=WishesOut,
            summary="What is wished in the libraries the caller may ask for books in (the right to acquire)",
            responses={403: {"description": "acquire_not_allowed"}})
def list_wishes(account: Reader, db: DbSession, mine: bool = False) -> WishesOut:
    # Reading only: the wishes that wait for a place of their person are started by the planner and when a search is
    # done, never by somebody (or a key) reading the list.
    if not search.targets(db, account.id):
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)
    shown = wishes.listing(db, account, mine=mine)
    vias = watching.via_of_wishes(db, [row.id for row, _info in shown])
    return WishesOut(wishes=[_wish(db, account, row, info, vias.get(row.id, "")) for row, info in shown])


@router.post("/wishes", response_model=WishesMadeOut, status_code=201,
             summary="Wish for books: one wish per book and kind, searched at once (the right to acquire)",
             responses={200: {"description": "Nothing was made: every book and kind is wished already or there.",
                              "model": WishesMadeOut},
                        403: {"description": "acquire_not_allowed, library_not_allowed"},
                        404: {"description": "not_found (no book of the request was made a wish)"},
                        409: {"description": "no_indexers"},
                        422: {"description": "language_not_accepted (a language the operator does not allow), "
                                             "library_required (several libraries take a kind and none is named), "
                                             "not_a_book, volume_unknown (a hit of the catalogue search that cannot be "
                                             "acquired)"},
                        429: {"description": "too_many_wishes_today"}})
def make_wishes(payload: WishesIn, account: Account, db: DbSession, response: Response) -> WishesMadeOut:
    """201 when at least one wish was made, 200 when none was and the books are wished already or there; when every
    book failed, the failure of the first (its code and status, ``429`` with ``Retry-After`` for the day's wishes)."""
    out: list[WishMadeOut] = []
    first: HTTPException | None = None
    for index, item in enumerate(payload.items):
        try:
            made = wishes.create(db, account, work_id=item.work_id, token=item.book, kinds=list(payload.kinds),
                                 language=payload.language, library_id=payload.library_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = str(detail.get("code", "failed"))
            if code in ("no_indexers", "acquire_not_allowed", "library_not_allowed", "library_kind",
                        "library_required"):
                raise
            db.rollback()
            first = first or exc
            out.extend(WishMadeOut(index=index, kind=kind, status="error", code=code, wish=None)
                       for kind in payload.kinds)
            continue
        for result in made:
            shown = None
            if result.wish is not None:
                info = wishes.books_for(db, account.id, [(result.wish.id, result.wish.work_id,
                                                           result.wish.foreign_id)]).get(result.wish.id)
                shown = _wish(db, account, result.wish, info) if info is not None else None
            out.append(WishMadeOut(index=index, kind=result.kind, status=result.status,  # type: ignore[arg-type]
                               code=result.code, wish=shown))
    if not any(result.status == "created" for result in out):
        if first is not None and all(result.status == "error" for result in out):
            raise first
        response.status_code = 200
    return WishesMadeOut(results=out)


@router.delete("/wishes/{wish_id}", status_code=204, summary="Take a wish away (the right to acquire)",
               responses={404: {"description": "not_found"}})
def remove_wish(wish_id: int, account: Account, db: DbSession) -> Response:
    wishes.remove(db, account, wish_id)
    return Response(status_code=204)


@router.post("/wishes/{wish_id}/search", response_model=WishOut,
             summary="Search for a wish now (also with the main switch off)",
             responses={404: {"description": "not_found"}, 409: {"description": "wish_loading, wish_waiting"},
                        422: {"description": "language_not_accepted (the wish's language is no longer allowed)"},
                        429: {"description": "too_many_searches, too_many_searches_today"}})
def search_wish(wish_id: int, account: Account, db: DbSession) -> WishOut:
    row = wishes.search_now(db, account, wish_id)
    info = wishes.books_for(db, account.id, [(row.id, row.work_id, row.foreign_id)])[row.id]
    return _wish(db, account, row, info)


class WishLanguageIn(BaseModel):
    language: str | None = Field(default=None, min_length=2, max_length=8,
                                 description="A language the operator allows; null for every language he allows.")


@router.post("/wishes/{wish_id}/language", response_model=WishOut,
             summary="Let a wish look for another language, one the operator allows (the right to acquire)",
             responses={404: {"description": "not_found"}, 409: {"description": "wish_loading"},
                        422: {"description": "language_not_accepted"}})
def wish_language(wish_id: int, payload: WishLanguageIn, account: Account, db: DbSession) -> WishOut:
    """A wish that rests because its language is no longer allowed (``reason`` ``language_off``) searches again in the
    languages allowed. When a wish of the book in that language is open already, that one is the answer and this one
    goes."""
    row = wishes.relanguage(db, account, wish_id, payload.language)
    info = wishes.books_for(db, account.id, [(row.id, row.work_id, row.foreign_id)])[row.id]
    return _wish(db, account, row, info)


# --- The numbers for the head, and the options of a book ---------------------


class ClientDownOut(BaseModel):
    id: int
    name: str
    code: str


class CountsOut(BaseModel):
    allowed: bool = Field(description="Whether the caller may ask for books in any library at all.")
    seeking: int = Field(description="Wishes that wait or are looked for (the list \"Gesucht\").")
    running: int = Field(description="Downloads that run (the list \"Läuft\").")
    needs: int = Field(description="Downloads and jobs that need somebody (the list \"Braucht dich\").")
    blocklist: int
    automatic: bool = Field(description="The main switch of the automatic work.")
    indexers: int = Field(description="How many indexers are switched on.")
    clients_down: list[ClientDownOut] = Field(description="Programs that could not be reached the last time.")


@router.get("/counts", response_model=CountsOut,
            summary="The numbers for the head of the page and the notes above the lists")
def counts(account: Reader, db: DbSession) -> CountsOut:
    from ..models import ForeignJob
    from ..services.procure import blocklist

    allowed = db.scalar(libraries.acquirable_library_ids(account.id).limit(1)) is not None
    if not allowed:
        return CountsOut(allowed=False, seeking=0, running=0, needs=0, blocklist=0, automatic=False, indexers=0,
                         clients_down=[])
    numbers = wishes.counts(db, account)
    # The jobs nobody asked for are the operator's to see: the programs are shared by every library.
    foreign = (db.scalar(select(func.count(ForeignJob.id)).where(ForeignJob.ignored.is_(False))) or 0
               if account.role == OPERATOR else 0)
    down = [ClientDownOut(id=row.id, name=row.name, code=row.last_error) for row in db.scalars(
        select(DownloadClient).where(DownloadClient.enabled.is_(True), DownloadClient.last_error.in_(UNREACHABLE))
        .order_by(DownloadClient.name))]
    return CountsOut(
        allowed=True, seeking=numbers["seeking"], running=numbers["running"], needs=numbers["needs"] + foreign,
        blocklist=blocklist.count(db, account), automatic=bool(automation.config(db)["enabled"]),
        indexers=db.scalar(select(func.count(Indexer.id)).where(Indexer.enabled.is_(True))) or 0, clients_down=down)


class OptionsKind(BaseModel):
    kind: Kind
    state: Literal["free", "have", "upgrade", "wished", "loading"]
    wish_id: int | None
    have: str | None = Field(description="The best format there is already (in the words of the ranking).")
    goal: str
    libraries: list[TargetRef] = Field(
        description="Where a book of this kind may go (the caller's choice), with whether nexbooks may write there.")
    default_library: int | None = Field(
        description="Where it goes when the caller does not choose: the operator's choice, else the first library "
                    "nexbooks may write in; none when the caller has to choose (``choose``).")
    choose: bool = Field(default=False, description="Several libraries nexbooks may write in take the kind and the "
                                                    "operator chose none: a wish names one (``library_id``).")
    takes: list[str] = Field(description="The formats nexbooks takes, the best first (in the words of the ranking).")
    abridged: bool = Field(description="Whether an abridged recording is taken (always false for e-books).")


class OptionsOut(BaseModel):
    allowed: bool
    kinds: list[OptionsKind]
    original_language: str = Field(description="The language the book first came out in, as far as it is known.")
    languages: list[str] = Field(description="The languages the book may be fetched in: the ones the operator ticked "
                                             "(settings, \"Sprachen\"), and the original language of the book when "
                                             "\"Originalsprache\" is ticked. A wish names one of them or none (all "
                                             "of them).")
    release_date: str
    automatic: bool
    indexers: int


@router.get("/book-options", response_model=OptionsOut,
            summary="What the caller may wish for of a book, and what is wished already",
            responses={404: {"description": "not_found"}})
def book_options(account: Reader, db: DbSession, work_id: int | None = None, book: str | None = None) -> OptionsOut:
    from ..models import ForeignBook
    from ..services import works
    from ..services.discover.view import by_token

    if (work_id is None) == (not book):
        raise error("invalid_input", "Name a work or a book of the sources.", 422, field="work_id")
    places = search.targets(db, account.id)
    open_choice = search.must_choose(db, account.id)
    config = formats.config(db)
    indexers = db.scalar(select(func.count(Indexer.id)).where(Indexer.enabled.is_(True))) or 0
    original, release_date = "", ""
    foreign_row: ForeignBook | None = None
    if work_id is not None:
        view = works.get_work(db, account.id, work_id)
        original = view.original_language or ""
    else:
        foreign_row = by_token(db, book or "")
        if foreign_row is None:
            raise error("not_found", "Not found.", 404)
        original = foreign_row.language or ""
        release_date = wishes._released_after_today(foreign_row.released)
    allowed = formats.allowed_languages(db, original)
    kinds: list[OptionsKind] = []
    allowed_rows = list(db.scalars(select(Library).where(Library.id.in_(libraries.acquirable_library_ids(account.id)))
                                   .order_by(Library.name)))
    for kind in ("ebook", "audio"):
        place = places.get(kind)
        if place is None:
            continue
        refs = []
        for row in allowed_rows:
            if libraries.kind_allows(row.kind, kind):
                ok, why = formats.write_state(row.path)
                refs.append(TargetRef(id=row.id, name=row.name, writable=ok, write_problem=why))
        # What is wished or on its way for the library the book would go into (the caller has the right there): another
        # library's wish is not the caller's to know, and does not stop them from wishing the book for theirs.
        open_wish = db.scalar(select(ProcureWish).where(
            (ProcureWish.work_id == work_id) if work_id is not None
            else (ProcureWish.foreign_id == foreign_row.id),  # type: ignore[union-attr]
            ProcureWish.kind == kind, ProcureWish.library_id == place.id, ProcureWish.state != "done").limit(1))
        loading_now = db.scalar(select(Download.id).where(
            (Download.work_id == work_id) if work_id is not None
            else (Download.foreign_id == foreign_row.id),  # type: ignore[union-attr]
            Download.kind == kind, Download.library_id == place.id,
            Download.state.in_(("sending", "unsure", "queued", "downloading", "paused", "completed", "problem")))
            .limit(1))
        have: str | None = None
        state = "free"
        if work_id is not None:
            better, info = wishes._upgrade_of(db, account.id, work_id, kind, search.accepted_of(db, "", original))
            if info:
                have = info.get("have") or None
                state = "upgrade" if better else "have"
        if loading_now is not None:
            state = "loading"
        elif open_wish is not None:
            state = "wished"
        kinds.append(OptionsKind(kind=kind, state=state, wish_id=open_wish.id if open_wish else None,  # type: ignore[arg-type]
                                 have=have, goal=config["target"][kind], libraries=refs,
                                 default_library=None if kind in open_choice else place.id,
                                 choose=kind in open_choice,
                                 takes=[entry["f"] for entry in config[kind] if entry["on"]],
                                 abridged=bool(config["abridged"]) if kind == "audio" else False))
    return OptionsOut(allowed=bool(places), kinds=kinds, original_language=original,
                      languages=allowed, release_date=release_date,
                      automatic=bool(automation.config(db)["enabled"]), indexers=indexers)


__all__ = ["SEEKING", "router"]
