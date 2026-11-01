"""Fetching books, the downloads: what runs, what needs somebody, what became of it, and what a person
may do about it.

Everything here needs the right ``acquire`` (or ``manage``) in the library a download goes into, and a work only when
the caller sees it. The operator has no right of his own here, as everywhere with books; he alone confirms a path
mapping.

* ``GET /procure/running`` lists the downloads that run (tracking asks the programs every fifteen seconds),
  ``GET /procure/problems`` the ones that need somebody and the jobs in the category nobody asked for,
  ``GET /procure/history`` what became of the downloads.
* ``POST /procure/downloads/{id}/cancel`` takes a download away (``block``: and never takes the release for the book
  again), ``.../recheck`` looks again, ``.../library`` picks another library for a finished download,
  ``.../stop-seeding`` ends the seeding of a filed torrent.
* ``GET`` and ``POST /procure/downloads/{id}/mapping`` (the operator) show what the search for the folder of a download
  found and confirm it as a path mapping, for a download of a library he looks after (``manage``).
* ``POST /procure/foreign/{id}/ignore`` and ``DELETE /procure/foreign/{id}`` deal with a job nobody asked for: the
  operator alone sees such jobs and deals with them (the programs are shared by every library, and the jobs may be
  what he put there himself); ``.../assign`` makes one the download of an open wish ("Einem Buch zuordnen").
* The check after the download: ``POST /procure/downloads/{id}/decide`` answers a question of the check
  (``runtime``, ``bundle``, ``files_unassigned``) or reads the note of a release it turned down (``language``). The
  notes the filing leaves one person (``upgrade_position``, ``folder_kept``) come with ``/procure/problems`` and are
  read with ``POST /procure/notes/{id}/acknowledge``, or undone with ``.../restore``. The trash of what upgrades
  replaced: ``GET /procure/trash``, ``POST /procure/trash/{id}/restore``, ``DELETE /procure/trash/{id}`` and ``DELETE
  /procure/trash`` (for the people who look after the library; the interface asks first). Deleting for good is for a
  browser session only: a device token or a key never empties the trash.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import Account, BrowserAccount, DbSession, OperatorAccount, Reader
from ..errors import error
from ..models import OPERATOR, Download, DownloadClient, Job, Library, LibraryMember
from ..models import Account as AccountRow
from ..services import brakes, libraries
from ..services.procure import downloads, estimate, history, importing, notes, trash
from ..services.procure import look as look_service
from ..services.procure.wishes import BookInfo
from .procure_wishes import LibraryRef, ProcureBookOut, _book
from .review import FileLineOut, SaysOut, _says_out
from .works import stream_response

logger = logging.getLogger("nexbooks.procure")

router = APIRouter(prefix="/api/v1/procure", tags=["procure"])

Kind = Literal["ebook", "audio"]
UNPACKING = ("extract", "unpack", "repair", "verif", "moving", "post-process", "running script", "unrar")


def _phase(row: Any, checking: frozenset[int] = frozenset()) -> str:
    """Where a download stands, in the words of the list: ``sending``, ``queued``, ``downloading``, ``paused``,
    ``unpacking``, ``waiting`` (done, waits for its turn), ``checking`` (its files are read and held against the wish),
    ``filing`` (put in its place), ``seeding`` (filed, the torrent seeds). ``checking`` lists the downloads whose
    filing job runs."""
    if row.state == "imported":
        return "seeding"
    if row.state == "completed":
        if row.place_step in ("checked", "placed"):
            return "filing"
        return "checking" if row.id in checking else "waiting"
    if row.state == "downloading" and any(word in (row.client_state or "").casefold() for word in UNPACKING):
        return "unpacking"
    return row.state


class RunningOut(BaseModel):
    id: int
    book: ProcureBookOut
    kind: Kind
    language: str
    release: str
    protocol: Literal["usenet", "torrent", "upload"]
    client: str
    indexer: str
    format: str
    size: int | None
    phase: Literal["sending", "unsure", "queued", "downloading", "paused", "unpacking", "waiting", "checking",
                   "filing", "seeding"]
    progress: float | None
    remaining_seconds: int | None
    speed: int | None = Field(description="Bytes per second, worked out from what the program says is left and how "
                                          "long it still needs; null while it does not load.")
    stalled: bool
    seed_ratio: float | None
    goal_ratio: float | None
    seed_seconds: int | None
    goal_seconds: int | None
    library: LibraryRef | None
    by: str
    mine: bool
    forced: list[str]
    created_at: datetime


class RunningListOut(BaseModel):
    downloads: list[RunningOut]


def _speed(row: Any) -> int | None:
    """What is left of the job over the time the program says it still needs: the program's own estimate, in bytes per
    second. Nothing for a job that does not load, or when the program told no size or time."""
    if row.state != "downloading" or not row.remaining_seconds or not row.size or row.progress is None:
        return None
    left = row.size * (1 - min(100.0, max(0.0, row.progress)) / 100)
    return int(left / row.remaining_seconds) if left > 0 else None


def _names(db: DbSession, ids: set[int | None]) -> dict[int, str]:
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    return {row.id: (row.display_name or row.name) for row in db.scalars(select(AccountRow).where(
        AccountRow.id.in_(wanted)))}


def _require_acquirer(db: DbSession, account: Any) -> None:
    if db.scalar(libraries.acquirable_library_ids(account.id).limit(1)) is None:
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)


def _library(db: DbSession, library_id: int | None) -> LibraryRef | None:
    row = db.get(Library, library_id) if library_id is not None else None
    return LibraryRef(id=row.id, name=row.name) if row is not None else None


@router.get("/running", response_model=RunningListOut,
            summary="The downloads that run in the libraries the caller may ask for books in",
            responses={403: {"description": "acquire_not_allowed"}})
def running(account: Reader, db: DbSession, mine: bool = False) -> RunningListOut:
    _require_acquirer(db, account)
    rows = downloads.running(db, account, mine=mine)
    names = _names(db, {row.user_id for row, _info in rows})
    checking = frozenset(db.scalars(select(Job.ref).where(Job.kind == importing.KIND, Job.state == "running")))
    return RunningListOut(downloads=[RunningOut(
        id=row.id, book=_book(info), kind=row.kind, language=row.language, release=row.title,  # type: ignore[arg-type]
        protocol=row.protocol, client=row.client_name, indexer=row.indexer_name, format=row.format,  # type: ignore[arg-type]
        size=row.size, phase=_phase(row, checking), progress=row.progress, remaining_seconds=row.remaining_seconds,  # type: ignore[arg-type]
        speed=_speed(row), stalled=row.problem_code == "stalled", seed_ratio=row.seed_ratio, goal_ratio=row.goal_ratio,
        seed_seconds=row.seed_seconds, goal_seconds=row.goal_seconds, library=_library(db, row.library_id),
        by=names.get(row.user_id or 0, ""), mine=row.user_id == account.id, forced=list(row.forced or []),
        created_at=row.created_at) for row, info in rows])


class DownloadProblemOut(BaseModel):
    id: int
    book: ProcureBookOut
    kind: Kind
    release: str
    code: str = Field(description="path_not_found, no_write, client_error, no_space, gone_from_client, place_failed, "
                                  "a question of the check after the download (runtime, bundle, files_unassigned, "
                                  "language_unclear: the witnesses of the language say different ones; "
                                  "language_unknown: none says one and several are allowed; ``file``, ``text``, "
                                  "``name``, ``folder``, ``wanted``; edition: an edition with additions or the book "
                                  "of a film, partial: a part of the book, pen_name: the files name another author "
                                  "than the release, ``found`` and ``wanted``; same_edition: a better format of an "
                                  "e-book the library has, and nothing proves it the same edition, ``have`` and "
                                  "``found``) "
                                  "or the note of a release it turned down (language).")
    values: dict[str, Any] = Field(description="What the words need. Paths and the like only for the operator.")
    client: str
    library: LibraryRef | None
    at: datetime
    can_map: bool = Field(description="The caller may confirm a path mapping (the operator).")
    protocol: str


class ForeignOut(BaseModel):
    id: int
    name: str
    client: str
    state: str
    first_seen_at: datetime


class NoteOut(BaseModel):
    id: int
    code: Literal["upgrade_position", "upgrade_replaced", "folder_kept", "works_joined", "duplicate",
                  "kindle_failed"] = Field(
        description="``upgrade_position``: the place in an e-book was carried over by its share only; "
                    "``upgrade_replaced``: a better format replaced the old one, which lies in the trash; "
                    "``folder_kept``: the program put the job into a folder it shares with others, and nexbooks took "
                    "nothing away there (``client``); ``works_joined``: the scan put an edition the library had as "
                    "a book of its own into another book (``title``, ``into``; the way back is "
                    "``POST /editions/{id}/split``); ``duplicate``: the library holds this edition twice, by its "
                    "files (``why``: ``same_files``, ``contains``), its running time or its size against the edition "
                    "``other_edition_id`` (``title``, ``other_title``, the folders ``here`` and ``there``; "
                    "``POST /editions/{id}/trash`` puts one in the trash); ``kindle_failed``: a mail with this "
                    "e-book to the person's Kindle did not go for good (``reason``: ``mail`` the mail server did not "
                    "take it, ``no_address`` the address was gone when the job ran, ``off`` the operator switched "
                    "e-readers off, ``not_sendable`` the file could not be sent any more, ``unreadable``).")
    book: ProcureBookOut
    edition_id: int | None
    detail: dict[str, Any] = Field(description="``from`` and ``to`` (formats), ``fraction`` (0 to 1), ``page`` and "
                                               "``pages`` of a PDF; ``client`` (the program's name) of "
                                               "``folder_kept``; ``reason`` of ``kindle_failed``.")
    can_restore: bool = Field(description="The old format is still in the trash.")
    release: str = Field(default="", description="The release the new format came with.")
    at: datetime


class DownloadProblemsOut(BaseModel):
    problems: list[DownloadProblemOut]
    foreign: list[ForeignOut]
    notes: list[NoteOut] = Field(description="The notes the filing left the caller, theirs alone.")


def _values(account: Any, code: str, values: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(values or {})
    if account.role == OPERATOR:
        return values
    allowed = {"library_id", "reason", "search", "where", "file", "wanted", "loaded", "unabridged", "abridged",
               "units", "title", "authors", "volume", "text", "name", "folder", "category", "found", "why"}
    return {key: value for key, value in values.items() if key in allowed}


def _card_values(db: DbSession, account: Any, row: Download) -> dict[str, Any]:
    """The values of a card: what the check stored, and for a question about the running time where no catalogue gives
    the unabridged one, a rough time from the pages of the book (``pages``, ``estimate``; it decides nothing)."""
    values = _values(account, row.problem_code, row.problem_values)
    if row.problem_code == "runtime":
        values.update(estimate.for_card(db, row, account.id, values))
    return values


@router.get("/problems", response_model=DownloadProblemsOut,
            summary="The downloads that need somebody, and the jobs in the category nobody asked for",
            responses={403: {"description": "acquire_not_allowed"}})
def problems(account: Reader, db: DbSession) -> DownloadProblemsOut:
    _require_acquirer(db, account)
    rows = downloads.problems(db, account)
    found = [DownloadProblemOut(id=row.id, book=_book(info), kind=row.kind, release=row.title,  # type: ignore[arg-type]
                        code=row.problem_code, values=_card_values(db, account, row),
                        client=row.client_name, library=_library(db, row.library_id), at=row.updated_at,
                        can_map=row.problem_code == "path_not_found" and account.role == OPERATOR
                        and downloads.manages(db, account, row), protocol=row.protocol)
             for row, info in rows]
    foreign = [ForeignOut(id=job.id, name=job.name, client=client.name, state=job.job_state,
                          first_seen_at=job.first_seen_at) for job, client in downloads.foreign_jobs(db, account)]
    return DownloadProblemsOut(problems=found, foreign=foreign, notes=_notes(db, account))


def _notes(db: DbSession, account: Any) -> list[NoteOut]:
    from ..models import TrashEntry
    from ..services.procure import wishes

    rows = notes.listing(db, account)
    books = wishes.books_for(db, account.id, [(note.id, edition.work_id, None) for note, edition in rows])
    out = []
    for note, edition in rows:
        if note.id not in books:
            continue
        detail = {key: value for key, value in (note.detail or {}).items() if key != "old"}
        download = db.get(Download, note.download_id) if note.download_id is not None else None
        out.append(NoteOut(id=note.id, code=note.code, book=_book(books[note.id]),  # type: ignore[arg-type]
                           edition_id=edition.id, detail=detail,
                           can_restore=note.trash_id is not None and db.get(TrashEntry, note.trash_id) is not None,
                           release=download.title if download is not None else "", at=note.created_at))
    return out


class WishedOut(BaseModel):
    """What was wished, as it read when the wish was made."""

    title: str
    authors: list[str]
    kind: Kind
    language: str


class DownloadHistoryEntryOut(BaseModel):
    id: int
    book_title: str
    book_author: str
    work_id: int | None
    book: str | None
    kind: Kind
    release: str
    indexer: str
    result: Literal["done", "rejected", "failed", "removed", "joined"] = Field(
        description="``joined``: no download; the scan put an edition the library had as a book of its own into this "
                    "one (``note.edition_id``, ``note.title``).")
    note: dict[str, Any] | None
    created_at: datetime
    wished: WishedOut | None = Field(default=None, description="What was wished, as it read when the wish was made; "
                                                                "for a line from before, the book of the sources it "
                                                                "was for, else nothing.")
    filed_as: str | None = Field(default=None, description="The title of the edition the files went into.")
    differs: bool = Field(default=False, description="What went in is not the book wished.")
    can_split: bool = Field(default=False, description="Of a ``joined`` line: the caller may make the edition a book "
                                                       "of its own again (``POST /editions/{id}/split``).")


class DownloadHistoryOut(BaseModel):
    entries: list[DownloadHistoryEntryOut]


def _can_split(db: Session, uid: int, row: Any) -> bool:
    """Whether the edition a ``joined`` line names is still in the book the line is about, in a library ``uid`` looks
    after."""
    from ..models import Edition

    ident = (row.note or {}).get("edition_id") if isinstance(row.note, dict) else None
    edition = db.get(Edition, ident) if isinstance(ident, int) else None
    return edition is not None and edition.work_id == row.work_id and edition.grouped_by != "hand" and db.scalar(
        libraries.managed_library_ids(uid).where(LibraryMember.library_id == edition.library_id)) is not None


@router.get("/history", response_model=DownloadHistoryOut, summary="What became of the downloads, newest first",
            responses={403: {"description": "acquire_not_allowed"}})
def read_history(account: Reader, db: DbSession, result: Literal["done", "bad"] | None = None) -> DownloadHistoryOut:
    from ..models import ForeignBook

    if db.scalar(libraries.acquirable_library_ids(account.id).limit(1)) is None:
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)
    out = []
    for row in history.listing(db, account, result):
        token = db.scalar(select(ForeignBook.token).where(ForeignBook.id == row.foreign_id)) \
            if row.foreign_id is not None and row.work_id is None else None
        note = row.note if isinstance(row.note, dict) else {}
        wished = history.wished_of(db, row)
        filed = note.get("filed_as") if row.result == "done" else None
        out.append(DownloadHistoryEntryOut(id=row.id, book_title=row.book_title, book_author=row.book_author,
                                   work_id=row.work_id, book=token, kind=row.kind, release=row.release,  # type: ignore[arg-type]
                                   indexer=row.indexer_name, result=row.result,  # type: ignore[arg-type]
                                   note=row.note, created_at=row.created_at,
                                   wished=WishedOut(**wished) if wished else None,
                                   filed_as=str(filed)[:300] if isinstance(filed, str) and filed else None,
                                   differs=row.result == "done"
                                   and bool(note.get("other_book") or note.get("differs")),
                                   can_split=row.result == "joined" and _can_split(db, account.id, row)))
    return DownloadHistoryOut(entries=out)


class CancelIn(BaseModel):
    block: bool = Field(default=False, description="Never take the release for the book again; the wish goes on "
                                                   "searching.")


class StateOut(BaseModel):
    id: int
    state: str


@router.post("/downloads/{download_id}/cancel", response_model=StateOut,
             summary="Take a download away (the interface asks first)",
             responses={404: {"description": "not_found"}, 409: {"description": "not_cancellable"}})
def cancel(download_id: int, payload: CancelIn, account: Account, db: DbSession) -> StateOut:
    row = downloads.cancel(db, account, download_id, block=payload.block)
    return StateOut(id=row.id, state=row.state)


@router.post("/downloads/{download_id}/recheck", response_model=StateOut,
             summary="Look again at a download that needs somebody",
             responses={404: {"description": "not_found"}, 409: {"description": "not_a_problem"}})
def recheck(download_id: int, account: Account, db: DbSession) -> StateOut:
    row = downloads.recheck(db, account, download_id)
    return StateOut(id=row.id, state=row.state)


class DownloadLibraryIn(BaseModel):
    library_id: int = Field(ge=1)


@router.post("/downloads/{download_id}/library", response_model=StateOut,
             summary="Put a finished download into another library",
             responses={403: {"description": "library_not_allowed"}, 404: {"description": "not_found"},
                        409: {"description": "already_placed"}, 422: {"description": "library_kind"}})
def choose_library(download_id: int, payload: DownloadLibraryIn, account: Account, db: DbSession) -> StateOut:
    row = downloads.choose_library(db, account, download_id, payload.library_id)
    return StateOut(id=row.id, state=row.state)


@router.post("/downloads/{download_id}/stop-seeding", response_model=StateOut,
             summary="End the seeding of a filed torrent (the interface asks first)",
             responses={404: {"description": "not_found"}, 409: {"description": "not_seeding"}})
def stop_seeding(download_id: int, account: Account, db: DbSession) -> StateOut:
    row = downloads.stop_seeding(db, account, download_id)
    return StateOut(id=row.id, state=row.state)


class MappingOptionsOut(BaseModel):
    reported: str | None
    search: Literal["proposed", "twice", "cut", "nowhere", "no_path"]
    proposal: dict[str, str] | None
    matches: list[str]
    choices: list[dict[str, str]] = Field(description="For ``twice``: the mapping each place would make "
                                                      "(``remote``, ``local``, ``place``).")
    roots: list[str]


@router.get("/downloads/{download_id}/mapping", response_model=MappingOptionsOut,
            summary="What the search for the folder of a download found (the operator)",
            responses={404: {"description": "not_found"}})
def mapping_options(download_id: int, operator: OperatorAccount, db: DbSession) -> MappingOptionsOut:
    return MappingOptionsOut(**downloads.mapping_options(db, operator, download_id))  # type: ignore[arg-type]


class MappingIn(BaseModel):
    remote: str = Field(min_length=1, max_length=1000)
    local: str = Field(min_length=1, max_length=1000)


class ConfirmedMappingOut(BaseModel):
    id: int
    client: str
    remote: str
    local: str


@router.post("/downloads/{download_id}/mapping", response_model=ConfirmedMappingOut, status_code=201,
             summary="Confirm the path mapping nexbooks suggested (the operator)",
             responses={404: {"description": "not_found"}, 409: {"description": "mapping_not_offered"}})
def confirm_mapping(download_id: int, payload: MappingIn, operator: OperatorAccount,
                    db: DbSession) -> ConfirmedMappingOut:
    mapping = downloads.confirm_mapping(db, operator, download_id, payload.remote, payload.local)
    client = db.get(DownloadClient, mapping.client_id)
    return ConfirmedMappingOut(id=mapping.id, client=client.name if client else "", remote=mapping.remote,
                               local=mapping.local)


class DecideIn(BaseModel):
    action: Literal["keep", "pick", "whole", "take", "block", "acknowledge"]
    unit: str | None = Field(default=None, max_length=64, description="For ``pick``: the book of the bundle, as the "
                                                                       "question names it.")


@router.post("/downloads/{download_id}/decide", response_model=StateOut,
             summary="Answer a question of the check after the download, or read the note of a release it turned down",
             responses={404: {"description": "not_found"}, 409: {"description": "not_a_question"},
                        422: {"description": "invalid_input"}})
def decide(download_id: int, payload: DecideIn, account: Account, db: DbSession) -> StateOut:
    row = downloads.decide(db, account, download_id, payload.action, payload.unit)
    return StateOut(id=row.id, state=row.state)


class LookChapterOut(BaseModel):
    number: int
    title: str
    #: Seconds from the beginning of all the parts of the recording.
    start: float


class DownloadLookOut(BaseModel):
    """The files of a download that waits for a person, like the facts of a book on the list."""

    download_id: int
    kind: Kind
    #: The folder of the first file, inside the download; empty for its top.
    folder: str
    files: list[FileLineOut]
    says: SaysOut
    chapters: list[LookChapterOut]


@router.get("/downloads/{download_id}/look", response_model=DownloadLookOut,
            summary="What the files of a download that asks a question say about themselves",
            responses={403: {"description": "acquire_not_allowed"}, 404: {"description": "not_found"},
                       409: {"description": "not_a_question"}})
def look(download_id: int, account: Reader, db: DbSession) -> DownloadLookOut:
    """The files of the download (name and folder inside the download, format, size, the order of the parts), what is
    written in them (title, authors, narrators, series, ISBN, publisher, year, language, running time, chapters,
    pages) and the chapters of a recording. For the people who may ask for books in the library the book goes into,
    while the download asks a question. To read in the book or to listen, a client fetches the bytes with
    ``/procure/downloads/{id}/files/{file}/content``."""
    _require_acquirer(db, account)
    found = look_service.look_into(db, account, download_id)
    facts = found.facts
    return DownloadLookOut(
        download_id=found.download.id, kind=found.kind,  # type: ignore[arg-type]
        folder=facts.folder, says=_says_out(facts.says),
        files=[FileLineOut(id=f.id, name=f.name, folder=f.folder, format=f.format, size=f.size, part=f.part,
                           duration=f.duration, pages=f.pages, chapters=f.chapters, protected=f.protected)
               for f in facts.files],
        chapters=[LookChapterOut(**chapter) for chapter in found.chapters])


@router.get("/downloads/{download_id}/files/{file_id}/content", response_model=None,
            summary="The bytes of one file of a download that asks a question, for reading and playing",
            responses={200: {"content": {"application/octet-stream": {}}}, 206: {"description": "A part (``Range``)"},
                       304: {"description": "Not changed"}, 404: {"description": "not_found"},
                       409: {"description": "not_a_question"}})
def look_content(download_id: int, file_id: int, request: Request, account: Reader, db: DbSession) -> Response:
    """The file as the reader in the browser and the player want it: with ``Range`` and an ``ETag``, like
    ``/files/{id}/content``. The request names the download and the file by number; the place on the disk comes from the
    journal of the filing and is checked against the folder of the download again. Too many requests, or too many
    files open at once for one person, are ``429``."""
    _require_acquirer(db, account)
    brakes.take("stream", account.id)
    item, path = look_service.file_path(db, account, download_id, file_id)
    return stream_response(account.id, request, path, item.format, f"look{download_id}:{item.id}", path.name)


@router.post("/notes/{note_id}/acknowledge", status_code=204, summary="A note of the filing is read",
             responses={404: {"description": "not_found"}})
def acknowledge_note(note_id: int, account: Account, db: DbSession) -> Response:
    notes.acknowledge(db, account, note_id)
    return Response(status_code=204)


class DropCopyIn(BaseModel):
    #: One of the two folders the note names (``detail.here``, ``detail.there``).
    folder: str = Field(min_length=1, max_length=300)


@router.post("/notes/{note_id}/drop", status_code=204,
             summary="Of an edition with two files of one format, put the ones of a folder in the trash (a member "
                     "with manage; the interface asks first)",
             responses={403: {"description": "forbidden"}, 404: {"description": "not_found"},
                        409: {"description": "trash_failed"}, 422: {"description": "invalid_input"}})
def drop_copy(note_id: int, payload: DropCopyIn, account: BrowserAccount, db: DbSession) -> Response:
    notes.drop_copy(db, account, note_id, payload.folder)
    return Response(status_code=204)


@router.post("/notes/{note_id}/restore", status_code=204,
             summary="Bring the old format back from the trash, the new one into it (the interface asks first)",
             responses={403: {"description": "forbidden"}, 404: {"description": "not_found"},
                        409: {"description": "trash_gone, trash_taken"}})
def restore_note(note_id: int, account: Account, db: DbSession) -> Response:
    notes.restore(db, account, note_id)
    return Response(status_code=204)


class AssignIn(BaseModel):
    wish_id: int = Field(ge=1)


@router.post("/foreign/{job_id}/assign", response_model=StateOut, status_code=201,
             summary="Make a job nobody asked for the download of an open wish (the operator)",
             responses={404: {"description": "not_found"}, 409: {"description": "wish_loading, already_loading"}})
def assign_foreign(job_id: int, payload: AssignIn, operator: OperatorAccount, db: DbSession) -> StateOut:
    row = downloads.assign_foreign(db, operator, job_id, payload.wish_id)
    return StateOut(id=row.id, state=row.state)


class TrashEntryOut(BaseModel):
    id: int
    library: LibraryRef | None
    work_id: int | None
    kind: Kind
    title: str
    author: str
    format: str
    files: int
    size: int
    created_at: datetime
    expires_at: datetime


class TrashOut(BaseModel):
    entries: list[TrashEntryOut]
    days: int = Field(description="How long an entry stays.")
    size: int


@router.get("/trash", response_model=TrashOut,
            summary="What upgrades replaced, in the libraries the caller looks after")
def read_trash(account: Reader, db: DbSession) -> TrashOut:
    rows = trash.listing(db, account)
    entries = [TrashEntryOut(id=row.id, library=_library(db, row.library_id), work_id=row.work_id,
                             kind=row.kind, title=row.title, author=row.author,  # type: ignore[arg-type]
                             format=row.format, files=len(row.files or []), size=row.size, created_at=row.created_at,
                             expires_at=row.expires_at) for row in rows]
    return TrashOut(entries=entries, days=trash.days(db), size=sum(row.size for row in rows))


@router.post("/trash/{entry_id}/restore", status_code=204,
             summary="Put the files of a trash entry back where they lay (the interface asks first)",
             responses={404: {"description": "not_found"}, 409: {"description": "trash_taken, hidden_folder_link"}})
def restore_trash(entry_id: int, account: Account, db: DbSession) -> Response:
    trash.restore(db, trash.get(db, account, entry_id))
    db.commit()
    return Response(status_code=204)


@router.delete("/trash/{entry_id}", status_code=204,
               summary="Delete a trash entry and its files for good (in the browser; the interface asks first)",
               responses={404: {"description": "not_found"}, 409: {"description": "hidden_folder_link"}})
def delete_trash(entry_id: int, account: BrowserAccount, db: DbSession) -> Response:
    from ..services.procure import hidden

    try:
        trash.delete(db, trash.get(db, account, entry_id))
    except hidden.Unsafe as exc:
        raise trash.unsafe_error() from exc
    db.commit()
    return Response(status_code=204)


class EmptiedOut(BaseModel):
    deleted: int


@router.delete("/trash", response_model=EmptiedOut,
               summary="Empty the trash of the libraries the caller looks after (in the browser; the interface asks "
                       "first)",
               responses={403: {"description": "forbidden (the caller looks after no library), browser_only"}})
def empty_trash(account: BrowserAccount, db: DbSession) -> EmptiedOut:
    return EmptiedOut(deleted=trash.empty(db, account))


@router.post("/foreign/{job_id}/ignore", status_code=204,
             summary="Ignore a job nobody asked for (the operator; the interface asks first)",
             responses={404: {"description": "not_found"}})
def ignore_foreign(job_id: int, operator: OperatorAccount, db: DbSession) -> Response:
    downloads.ignore_foreign(db, operator, job_id)
    return Response(status_code=204)


@router.delete("/foreign/{job_id}", status_code=204,
               summary="Take a job nobody asked for out of its program with its files (the operator; asks first)",
               responses={404: {"description": "not_found"}, 502: {"description": "client_*"}})
def remove_foreign(job_id: int, operator: OperatorAccount, db: DbSession) -> Response:
    downloads.remove_foreign(db, operator, job_id)
    return Response(status_code=204)


__all__ = ["BookInfo", "router"]
