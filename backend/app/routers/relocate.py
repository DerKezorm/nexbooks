"""Moving an edition into another library: what may be chosen, the move itself, how far it is, and
another try after a failure.

For a member with ``manage`` in the library the edition lies in and in the one it goes to, in a signed-in browser (the
operator included: no role is a right in a library). Nothing moves without ``confirm: true`` (the interface asks first
and says what happens). The move is a job in the background (``services/relocate.py``): ``POST`` answers 202 with the
move, ``GET /moves/{id}`` says how far it is and where the files lie.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import BrowserAccount, DbSession
from ..errors import error
from ..services import relocate as service

router = APIRouter(prefix="/api/v1", tags=["books"])


class LibraryRef(BaseModel):
    id: int
    name: str


class SourceOut(LibraryRef):
    writable: bool
    #: Why nexbooks may not write there: ``missing``, ``read_only`` or ``no_permission`` (empty when it may).
    write_problem: str


class MoveTarget(BaseModel):
    id: int
    name: str
    kind: Literal["ebook", "audio", "both"]
    #: A library nexbooks may not write in is named (with the reason) but cannot be chosen.
    writable: bool
    write_problem: str
    #: Where the book files would lie, relative to the library, by the naming scheme of filing (the first few), and how
    #: many files more go (other parts, the files beside the book).
    preview: list[str]
    more: int
    #: The folder of the book in the library, when it has one of its own (a recording, an e-book with files beside it).
    folder: str = ""
    #: The files are copied (another disk), not given a second name.
    copies: bool
    #: Why there is no place for the book there (``taken``, ``too_long``, ``link`` ...); empty when there is.
    problem: str


class MoveOut(BaseModel):
    id: int
    edition_id: int | None
    source: LibraryRef | None
    target: LibraryRef | None
    #: ``queued``, ``running``, ``done`` or ``failed``.
    state: Literal["queued", "running", "done", "failed"]
    #: ``place`` (the files are being put at their goals, the source is untouched), ``clear`` (the book belongs to the
    #: target, the files at the source are being taken away), ``done``.
    step: Literal["place", "clear", "done"]
    #: Why a move failed: ``no_space``, ``target_taken``, ``source_changed``, ``target_not_writable``,
    #: ``source_not_writable``, ``source_left``, ``no_write``, ``gone``, ``failed`` ...
    error: str
    #: Files of the move, files at their goals, files taken away at the source.
    files: int
    placed: int
    removed: int
    #: Where the book lies now: ``source``, ``target``, or ``both`` (``left`` files are at the other end).
    where: Literal["source", "target", "both"]
    left: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MoveOptions(BaseModel):
    edition_id: int
    kind: Literal["ebook", "audio"]
    source: SourceOut
    #: The book files of the edition and their bytes.
    files: int
    bytes: int
    #: The libraries the caller manages that take this kind, the source left out.
    targets: list[MoveTarget]
    #: A move of the book or a tidying of the library runs: nothing can be started now.
    busy: bool
    #: The newest move of the edition (to show how far it is, or why it failed).
    latest: MoveOut | None


class MoveIn(BaseModel):
    library_id: int = Field(ge=1, le=2**31 - 1)
    #: The person said yes to the question the interface asked. Without it nothing moves.
    confirm: bool = False


class ConfirmIn(BaseModel):
    confirm: bool = False


def _confirmed(confirm: bool) -> None:
    if not confirm:
        raise error("confirm_needed", "This needs a confirmation.", 422, field="confirm")


@router.get("/editions/{edition_id}/move", response_model=MoveOptions,
            summary="Where an edition may be moved, where its files would lie, and its latest move",
            responses={403: {"description": "forbidden"}, 404: {"description": "not_found"}})
def move_options(edition_id: int, account: BrowserAccount, db: DbSession) -> MoveOptions:
    edition, source = service.edition_of(db, account, edition_id)
    found = service.options(db, account, edition, source)
    latest = found.pop("latest")
    return MoveOptions(**found, latest=MoveOut(**service.report(db, latest)) if latest is not None else None)


@router.post("/editions/{edition_id}/move", response_model=MoveOut, status_code=202,
             summary="Moves an edition into another library, once the person confirmed it",
             responses={403: {"description": "forbidden"}, 404: {"description": "not_found"},
                        409: {"description": "move_busy, move_no_goal, move_source_not_writable, "
                                             "move_target_not_writable"},
                        422: {"description": "confirm_needed, move_same, move_kind"}})
def move(edition_id: int, payload: MoveIn, account: BrowserAccount, db: DbSession) -> MoveOut:
    edition, source = service.edition_of(db, account, edition_id)
    _confirmed(payload.confirm)
    row = service.start(db, account, edition, source, payload.library_id)
    return MoveOut(**service.report(db, row))


@router.get("/moves/{move_id}", response_model=MoveOut, summary="How far a move into another library is",
            responses={404: {"description": "not_found"}})
def read_move(move_id: int, account: BrowserAccount, db: DbSession) -> MoveOut:
    return MoveOut(**service.report(db, service.load(db, account, move_id)))


@router.post("/moves/{move_id}/retry", response_model=MoveOut, status_code=202,
             summary="Tries a failed move again, from the step it stopped at, once the person confirmed it",
             responses={404: {"description": "not_found"}, 409: {"description": "move_state, move_busy, move_gone"},
                        422: {"description": "confirm_needed"}})
def retry_move(move_id: int, payload: ConfirmIn, account: BrowserAccount, db: DbSession) -> MoveOut:
    row = service.load(db, account, move_id)
    _confirmed(payload.confirm)
    return MoveOut(**service.report(db, service.retry(db, account, row)))
