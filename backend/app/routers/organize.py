"""Tidying the files of a library: renaming and moving the books by a scheme, with a preview and a way back.

**The operator's alone, and off from the start.** Everything here wants the operator in a signed-in browser, as a
member with ``manage`` of the library (a preview names its books, and those are for its members only), and
answers 409 ``organize_off`` while the operator has not turned tidying on. nexbooks reads libraries and changes
nothing in them; this is the one place it writes, and only after the operator saw the preview and confirmed it
(``confirm: true``; without it nothing starts). The libraries are mounted read-only from the start: ``status``
says whether nexbooks may write in a library and why not.

A run is a preview (nothing on the disk changed), then queued, running and done (or failed, with a code); a
finished run can be undone (the newest of a library only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession, OperatorAccount
from ..errors import error
from ..models import Edition, Library, OrganizeRun
from ..services import libraries, settings_service
from ..services import organize as service
from ..services.organize import scheme as schemes

router = APIRouter(prefix="/api/v1/organize", tags=["organize"])


class RunOut(BaseModel):
    id: int
    library_id: int
    scheme: str
    #: ``preview``, ``queued``, ``running``, ``done``, ``failed``, ``undoing`` or ``undone``.
    state: str
    #: Files to move, and files moved or left so far.
    total: int
    done: int
    #: A code when the run stopped before its end (``no_write_access``, ``read_only``, ``undo_incomplete`` ...).
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    #: What the preview counted: books that move, stay, have a problem; files; problems by reason; names the scheme uses
    #: that some books lack. Folders made and removed by the run, too.
    summary: dict[str, Any]


class MoveOut(BaseModel):
    id: int
    edition_id: int | None
    #: The title of the book the file belongs to (empty when the book is gone).
    title: str
    source: str
    target: str
    #: ``planned``, ``moved``, ``undone``, ``skipped`` or ``problem``.
    state: str
    reason: str


class MovePage(BaseModel):
    items: list[MoveOut]
    total: int


class Placeholder(BaseModel):
    name: str
    aliases: list[str]


class StatusOut(BaseModel):
    #: The operator's switch.
    enabled: bool
    #: Whether nexbooks may write in this library, and if not why: ``read_only``
    #: (mounted read-only), ``no_permission``, ``missing``.
    writable: bool
    reason: str
    #: The scheme offered first, and the names a scheme may use.
    scheme: str
    placeholders: list[Placeholder]
    runs: list[RunOut]


class SwitchIn(BaseModel):
    enabled: bool


class PreviewIn(BaseModel):
    library_id: int = Field(ge=1, le=2**31 - 1)
    scheme: str = Field(min_length=1, max_length=schemes.MAX_SCHEME + 20)


class ConfirmIn(BaseModel):
    #: The operator said yes to the question the interface asked. Without it nothing starts.
    confirm: bool = False


def run_out(run: OrganizeRun) -> RunOut:
    return RunOut(id=run.id, library_id=run.library_id, scheme=run.scheme, state=run.state, total=run.total,
                  done=run.done, error=run.error, created_at=run.created_at, started_at=run.started_at,
                  finished_at=run.finished_at, summary=run.summary if isinstance(run.summary, dict) else {})


def _library(db: DbSession, library_id: int, operator: Any) -> Library:
    """The library, for an operator who manages it as a member. A preview and its moves name the books and their files,
    and what a library holds is for its members, the operator included (as in the review): an operator who is no member
    of a library makes themselves one first (Settings, Libraries), which the other members see. 404 for a library the
    operator does not see, 403 for one they only read."""
    return libraries.require_reviewer(db, operator.id, library_id)


def _run(db: DbSession, run_id: int, operator: Any) -> OrganizeRun:
    """A run, by the rule of ``_library`` for its library: a run of a library the operator does not see is not found."""
    run = service.load(db, run_id)
    try:
        _library(db, run.library_id, operator)
    except HTTPException as exc:
        raise error("not_found", "Not found.", 404) from exc
    return run


def _status(db: DbSession, library: Library) -> StatusOut:
    info = service.status(db, library)
    return StatusOut(enabled=bool(info["enabled"]), writable=bool(info["writable"]), reason=str(info["reason"]),
                     scheme=str(info["scheme"]),
                     placeholders=[Placeholder(name=name, aliases=list(aliases))
                                   for name, aliases in schemes.PLACEHOLDERS.items()],
                     runs=[run_out(run) for run in service.runs_of(db, library.id)])


@router.get("/status", response_model=StatusOut,
            summary="Whether tidying is on and the library can be written, with its latest runs (the operator)")
def status(operator: OperatorAccount, db: DbSession, library: Annotated[int, Query(ge=1, le=2**31 - 1)]) -> StatusOut:
    return _status(db, _library(db, library, operator))


@router.put("/settings", response_model=SwitchIn, summary="Turns tidying on or off (the operator)")
def switch(payload: SwitchIn, _operator: OperatorAccount, db: DbSession) -> SwitchIn:
    settings_service.save(db, {"organize_enabled": payload.enabled})
    return SwitchIn(enabled=payload.enabled)


@router.get("/settings", response_model=SwitchIn, summary="Whether tidying is on (the operator)")
def switch_state(_operator: OperatorAccount, db: DbSession) -> SwitchIn:
    return SwitchIn(enabled=service.enabled(db))


@router.post("/preview", response_model=RunOut, status_code=201,
             summary="Makes the plan for a library and keeps it as a preview; nothing is moved (the operator)")
def preview(payload: PreviewIn, operator: OperatorAccount, db: DbSession) -> RunOut:
    library = _library(db, payload.library_id, operator)
    return run_out(service.preview(db, library, payload.scheme, operator.id))


@router.get("/runs/{run_id}", response_model=RunOut, summary="One run with how far it is (the operator)")
def read(run_id: int, operator: OperatorAccount, db: DbSession) -> RunOut:
    return run_out(_run(db, run_id, operator))


@router.get("/runs/{run_id}/moves", response_model=MovePage,
            summary="The files of a run, with where they lie and where they go (the operator)")
def read_moves(run_id: int, operator: OperatorAccount, db: DbSession,
               state: Annotated[str | None, Query(pattern=r"^(planned|moved|undone|skipped|problem)$")] = None,
               offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
               limit: Annotated[int, Query(ge=1, le=service.MOVES_PER_PAGE)] = service.MOVES_PER_PAGE) -> MovePage:
    run = _run(db, run_id, operator)
    rows, total = service.moves(db, run, state, offset, limit)
    titles = dict(db.execute(select(Edition.id, Edition.title).where(
        Edition.id.in_({row.edition_id for row in rows if row.edition_id}))).all()) if rows else {}
    return MovePage(items=[MoveOut(id=row.id, edition_id=row.edition_id, title=titles.get(row.edition_id or 0, ""),
                                   source=row.source, target=row.target, state=row.state, reason=row.reason)
                           for row in rows], total=total)


def _confirmed(payload: ConfirmIn) -> None:
    if not payload.confirm:
        raise error("confirm_needed", "This needs the operator's confirmation.", 422, field="confirm")


@router.post("/runs/{run_id}/start", response_model=RunOut, status_code=202,
             summary="Starts a previewed tidying, once the operator confirmed it (the operator)")
def start(run_id: int, payload: ConfirmIn, operator: OperatorAccount, db: DbSession) -> RunOut:
    _confirmed(payload)
    return run_out(service.start(db, _run(db, run_id, operator)))


@router.post("/runs/{run_id}/undo", response_model=RunOut, status_code=202,
             summary="Puts back what a tidying moved, once the operator confirmed it (the operator)")
def undo(run_id: int, payload: ConfirmIn, operator: OperatorAccount, db: DbSession) -> RunOut:
    _confirmed(payload)
    return run_out(service.undo(db, _run(db, run_id, operator)))


@router.delete("/runs/{run_id}", status_code=204, summary="Throws a preview away (the operator)")
def discard(run_id: int, operator: OperatorAccount, db: DbSession) -> Response:
    service.discard(db, _run(db, run_id, operator))
    return Response(status_code=204)
