"""Reading and listening: one edition for the reader or the player, where the person is in it, and their bookmarks.

**Progress and bookmarks are the person's own.** Every route here works on the person who asks: there is no way to
name somebody else, and the operator has none either. Reading them is open to a signed-in browser, a device and a
program with a key; setting them wants a browser or a device (a key only reads). An edition the person cannot see is
one that does not exist (404).

The place in a recording is a time (``time:8040.5``, seconds from the beginning of all its parts, unit ``time``); the
place in an e-book never is. The e-book and the recording of a book keep a place each, and neither moves the other.

A device reports where the reader is and how long ago it was there (``age``); the server keeps the newest report
(``services/progress.py``) and says whether it was this one. The answer carries the place that stands afterwards, so a
device that lost can show it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from .. import clock
from ..deps import Account, DbSession, Reader
from ..models import Bookmark, Progress
from ..services import progress as service
from ..services import works as works_service
from .works import EditionOut, WorkBrief, brief_of, edition_out, part_language

router = APIRouter(prefix="/api/v1", tags=["reading"])


class EditionToRead(BaseModel):
    """An edition with the work it belongs to: what the reader needs to open it."""

    work: WorkBrief
    edition: EditionOut


class ProgressIn(BaseModel):
    #: The place: a CFI for an EPUB, MOBI or AZW3, ``page:12`` for a PDF, ``time:8040.5`` for a recording (seconds from
    #: the beginning of all its parts, at most three places after the point).
    locator: str = Field(min_length=1, max_length=service.MAX_LOCATOR)
    #: How far in the whole book, 0 to 1.
    fraction: float = Field(ge=0, le=1)
    #: What ``chapter`` counts; ``time`` for a recording (and only there).
    unit: Literal["chapter", "page", "time"] = "chapter"
    chapter: int = Field(default=0, ge=0, le=1_000_000)
    chapter_count: int = Field(default=0, ge=0, le=1_000_000)
    chapter_title: str = Field(default="", max_length=300)
    #: A key the device made up for itself (the same on every report), and a name for people.
    device_key: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
    device: str = Field(default="", max_length=80)
    #: How many seconds ago the reader was at this place. The server counts back from the moment the report arrives, so
    #: a clock that is wrong keeps no other device out.
    age: float = Field(default=0, ge=0, le=60 * 60 * 24 * 365)


class ProgressOut(BaseModel):
    edition_id: int
    work_id: int
    locator: str
    #: Of a recording: the place in seconds from the beginning of all its parts (what ``locator`` says). Null else.
    position: float | None
    fraction: float
    unit: str
    chapter: int
    chapter_count: int
    chapter_title: str
    device_key: str
    device: str
    #: When the reader was at this place, and when the server heard of it.
    moved_at: datetime
    received_at: datetime
    #: How many seconds ago the reader was at this place, by the server's clock at the moment of this answer. A device
    #: that wants to know whether the place is newer than its own compares ages on its own clock and never compares the
    #: server's time of day with its own.
    age: float


class ProgressSaved(BaseModel):
    #: Whether this report is the one that stands. False: a newer one from another device was there (``progress``).
    applied: bool
    progress: ProgressOut


class BookmarkIn(BaseModel):
    locator: str = Field(min_length=1, max_length=service.MAX_LOCATOR)
    fraction: float = Field(ge=0, le=1)
    chapter_title: str = Field(default="", max_length=300)
    #: A few words of the person's own (what was heard there).
    note: str = Field(default="", max_length=service.MAX_NOTE)


class NoteIn(BaseModel):
    note: str = Field(max_length=service.MAX_NOTE)


class BookmarkOut(BaseModel):
    id: int
    edition_id: int
    locator: str
    #: Of a recording: the place in seconds. Null else.
    position: float | None
    fraction: float
    chapter_title: str
    note: str
    created_at: datetime


def progress_out(row: Progress, work_id: int) -> ProgressOut:
    age = max(0.0, (clock.now() - row.moved_at).total_seconds())
    return ProgressOut(
        edition_id=row.edition_id, work_id=work_id, locator=row.locator, position=service.seconds_of(row.locator),
        fraction=row.fraction, unit=row.unit,
        chapter=row.chapter, chapter_count=row.chapter_count, chapter_title=row.chapter_title,
        device_key=row.device_key, device=row.device, moved_at=row.moved_at, received_at=row.received_at,
        age=age)


def bookmark_out(row: Bookmark) -> BookmarkOut:
    return BookmarkOut(id=row.id, edition_id=row.edition_id, locator=row.locator,
                       position=service.seconds_of(row.locator), fraction=row.fraction,
                       chapter_title=row.chapter_title, note=row.note, created_at=row.created_at)


@router.get("/editions/{edition_id}", response_model=EditionToRead,
            summary="One edition with its work, files and chapters, for the reader or the player")
def read_edition(edition_id: int, account: Reader, db: DbSession) -> EditionToRead:
    edition = service.visible_edition(db, account.id, edition_id)
    view = works_service.get_work(db, account.id, edition.work_id)
    chapters = works_service.chapters_of(db, [edition.id])
    item = next(v for v in view.editions if v.edition.id == edition.id)
    return EditionToRead(work=brief_of(view), edition=edition_out(item, chapters.get(edition.id, []),
                                                                  part_language(view, account)))


@router.get("/progress", response_model=list[ProgressOut],
            summary="The caller's own places in books, the newest first, a page at a time")
def list_progress(account: Reader, db: DbSession, response: Response,
                  work: int | None = Query(default=None, ge=1, le=2**31 - 1, description="Only this book"),
                  limit: int = Query(default=service.LIST_LIMIT, ge=1, le=service.LIST_LIMIT,
                                     description="How many places at most"),
                  offset: int = Query(default=0, ge=0, le=10_000_000, description="How many places to skip")
                  ) -> list[ProgressOut]:
    """The header ``X-Total-Count`` says how many places the caller has in all (for this book, when ``work`` is given),
    so that a program knows whether to ask for the next page."""
    rows, total = service.listing(db, account.id, work, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [progress_out(row, work_id) for row, work_id in rows]


@router.get("/editions/{edition_id}/progress", response_model=ProgressOut | None,
            summary="Where the caller is in an edition; null when they have not begun")
def read_progress(edition_id: int, account: Reader, db: DbSession) -> ProgressOut | None:
    row = service.get(db, account.id, edition_id)
    if row is None:
        return None
    return progress_out(row, service.visible_edition(db, account.id, edition_id).work_id)


@router.put("/editions/{edition_id}/progress", response_model=ProgressSaved,
            summary="Reports where the reader is; the newest report from all devices stays")
def save_progress(edition_id: int, payload: ProgressIn, account: Account, db: DbSession) -> ProgressSaved:
    """A report for an edition that went into another one (the parts of a recording put together while a player had
    it open) counts for that one: the answer names it (``progress.edition_id``)."""
    applied, row = service.save(db, account.id, edition_id, service.Report(
        locator=payload.locator, fraction=payload.fraction, unit=payload.unit, chapter=payload.chapter,
        chapter_count=payload.chapter_count, chapter_title=payload.chapter_title, device_key=payload.device_key,
        device=payload.device, age=payload.age))
    edition = service.visible_edition(db, account.id, row.edition_id)
    return ProgressSaved(applied=applied, progress=progress_out(row, edition.work_id))


@router.get("/editions/{edition_id}/bookmarks", response_model=list[BookmarkOut],
            summary="The caller's bookmarks in an edition, in the order of the book")
def list_bookmarks(edition_id: int, account: Reader, db: DbSession) -> list[BookmarkOut]:
    return [bookmark_out(row) for row in service.bookmarks(db, account.id, edition_id)]


@router.post("/editions/{edition_id}/bookmarks", response_model=BookmarkOut, status_code=201,
             summary="Sets a bookmark; the same place twice gives the one bookmark")
def add_bookmark(edition_id: int, payload: BookmarkIn, response: Response, account: Account,
                 db: DbSession) -> BookmarkOut:
    row, made = service.add_bookmark(db, account.id, edition_id, payload.locator, payload.fraction,
                                     payload.chapter_title, payload.note.strip())
    if not made:
        response.status_code = 200
    return bookmark_out(row)


@router.patch("/bookmarks/{bookmark_id}", response_model=BookmarkOut,
              summary="Changes the note of one of the caller's own bookmarks")
def change_bookmark(bookmark_id: int, payload: NoteIn, account: Account, db: DbSession) -> BookmarkOut:
    return bookmark_out(service.change_note(db, account.id, bookmark_id, payload.note.strip()))


@router.delete("/bookmarks/{bookmark_id}", status_code=204, summary="Removes one of the caller's own bookmarks")
def remove_bookmark(bookmark_id: int, account: Account, db: DbSession) -> Response:
    service.remove_bookmark(db, account.id, bookmark_id)
    return Response(status_code=204)
