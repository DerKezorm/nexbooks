"""The reading stack: the books the caller finished, year by year, and what a year comes to.

**The history is the caller's own.** Every route works on the person who asks: there is no way to name somebody
else, and the operator has none either. Reading is open to a signed-in browser, a device and a program with a
key; entering a book and taking an entry away want a browser or a device (a key only reads). An edition the
caller cannot see is one that does not exist (404).

A book is entered in two ways (``services/history.py``): by the rule at the end of the book, when the reader or
the player reports the place, and by hand (``POST /editions/{id}/finished``). The same edition on the same day
is one entry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from ..deps import Account, DbSession, Reader
from ..services import history as service
from .works import WorkBrief, brief_of

router = APIRouter(prefix="/api/v1", tags=["history"])


class HistoryOut(BaseModel):
    id: int
    work_id: int
    edition_id: int
    #: ``read`` (an e-book) or ``listened`` (a recording).
    kind: Literal["read", "listened"]
    #: The day, ``YYYY-MM-DD``, in the caller's time zone.
    finished_on: str
    #: ``auto`` (the rule at the end of the book) or ``hand``.
    source: Literal["auto", "hand"]
    created_at: datetime


class FinishedIn(BaseModel):
    #: The day the book was finished, ``YYYY-MM-DD``, not after today. Empty: today.
    finished_on: str | None = Field(default=None, max_length=10)


class StackEntryOut(HistoryOut):
    work: WorkBrief
    #: What the book weighs on the tower: pages for an e-book that
    #: was read (the work's when the edition counts none), the
    #: pages the length comes to for a recording; and the running time in seconds of a recording (0 for an e-book).
    pages: int
    seconds: int


class Thickest(BaseModel):
    work: WorkBrief
    pages: int


class YearOut(BaseModel):
    year: int
    #: Books finished in the year, the pages of those that were read, the seconds of those that were heard.
    books: int
    read: int
    listened: int
    pages: int
    seconds: int
    #: The genre most of the year's books are (empty when none has one), and the book that weighs most.
    genre: str
    thickest: Thickest | None
    #: In the order they were finished: the first lies at the bottom of the tower.
    entries: list[StackEntryOut]


class StackOut(BaseModel):
    #: The oldest year first.
    years: list[YearOut]
    #: Today in the caller's time zone (``YYYY-MM-DD``): the year that is not over yet is the one of this day.
    today: str


def history_out(row: object) -> HistoryOut:
    return HistoryOut.model_validate(row, from_attributes=True)


@router.get("/history", response_model=list[HistoryOut],
            summary="The books the caller finished, the newest first, a page at a time")
def list_history(account: Reader, db: DbSession, response: Response,
                 work: int | None = Query(default=None, ge=1, le=2**31 - 1, description="Only this book"),
                 limit: int = Query(default=service.LIST_LIMIT, ge=1, le=service.LIST_LIMIT),
                 offset: int = Query(default=0, ge=0, le=10_000_000)) -> list[HistoryOut]:
    """The header ``X-Total-Count`` says how many entries the caller
    has in all (for this book, when ``work`` is given)."""
    rows, total = service.listing(db, account.id, work, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [history_out(row) for row in rows]


@router.post("/editions/{edition_id}/finished", response_model=HistoryOut, status_code=201,
             summary="Enters the edition as finished; the same day twice gives the one entry")
def finished(edition_id: int, payload: FinishedIn, response: Response, account: Account,
             db: DbSession) -> HistoryOut:
    row, made = service.add(db, account.id, edition_id, payload.finished_on)
    if not made:
        response.status_code = 200
    return history_out(row)


@router.delete("/history/{entry_id}", status_code=204, summary="Takes one of the caller's own entries away")
def remove(entry_id: int, account: Account, db: DbSession) -> Response:
    service.remove(db, account.id, entry_id)
    return Response(status_code=204)


@router.get("/stack", response_model=StackOut,
            summary="The reading stack: the caller's finished books by the year, with what each year comes to")
def stack(account: Reader, db: DbSession) -> StackOut:
    years = []
    for year in service.stack(db, account.id):
        thickest = year.thickest
        years.append(YearOut(
            year=year.year, books=len(year.entries), read=len(year.read), listened=len(year.listened),
            pages=year.pages, seconds=year.seconds, genre=year.genre,
            thickest=Thickest(work=brief_of(thickest.view), pages=thickest.pages if thickest.entry.kind == "read"
                              else thickest.view.pages) if thickest else None,
            entries=[StackEntryOut(**history_out(e.entry).model_dump(), work=brief_of(e.view), pages=e.pages,
                                   seconds=e.seconds) for e in year.entries]))
    return StackOut(years=years, today=service.today(db, account.id).isoformat())
