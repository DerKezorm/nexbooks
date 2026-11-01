"""Uploading books from the browser: the files of a book are sent one by one and filed like a download.

* ``POST /uploads`` begins an upload into a library the caller may bring books into (``acquire``), optionally for a
  book of it (the form it lacks).
* ``PUT /uploads/{id}/files?path=...`` sends one file: the body is the file itself (``application/octet-stream``). Its
  name may hold folders (a recording's ``CD 1/01.mp3``); the size is checked before a byte is read, and while it comes.
* ``POST /uploads/{id}/finish`` hands the upload to the filing; ``GET /uploads/{id}`` says how far it is and what came
  of it; ``DELETE /uploads/{id}`` drops an upload that is still coming, with its files (the interface asks first).

An upload is the person's own until it is filed: the routes answer only to whoever began it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..deps import Account, DbSession
from ..errors import error
from ..services.procure import uploads

logger = logging.getLogger("nexbooks.procure")

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])


class UploadIn(BaseModel):
    library_id: int = Field(ge=1)
    work_id: int | None = Field(default=None, ge=1, description="The book the files are a form of (optional).")
    kind: Literal["ebook", "audio"] | None = Field(default=None, description="What is sent; known from the files "
                                                                             "when left out (for a ZIP, from what is "
                                                                             "in it once it is unpacked).")


class UploadLimits(BaseModel):
    ebook_bytes: int
    file_bytes: int
    total_bytes: int
    files: int


class UploadFileOut(BaseModel):
    path: str
    size: int


class UploadOut(BaseModel):
    id: int
    state: str = Field(description="uploading, completed (being filed), problem, rejected, imported, removed.")
    phase: Literal["uploading", "waiting", "filing", "problem", "rejected", "done", "removed"]
    library_id: int | None
    work_id: int | None = Field(description="The book the files went into, once filed.")
    kind: Literal["ebook", "audio"]
    problem: str = Field(description="What the filing needs a person for, or why it was turned down.")
    files: list[UploadFileOut]
    limits: UploadLimits
    created_at: datetime


def _limits() -> UploadLimits:
    return UploadLimits(ebook_bytes=uploads.MAX_EBOOK_BYTES, file_bytes=uploads.MAX_FILE_BYTES,
                        total_bytes=uploads.MAX_TOTAL_BYTES, files=uploads.MAX_FILES)


def _phase(row: Any) -> str:
    if row.state == "uploading":
        return "uploading"
    if row.state == "completed":
        return "filing" if row.place_step in ("checked", "placed") else "waiting"
    if row.state == "imported":
        return "done"
    return str(row.state) if row.state in ("problem", "rejected", "removed") else "waiting"


def _out(db: DbSession, row: Any) -> UploadOut:
    result = uploads.result_of(db, row)
    problem = row.problem_code if row.state == "problem" else row.failed_reason if row.state == "rejected" else ""
    return UploadOut(id=row.id, state=row.state, phase=_phase(row), library_id=row.library_id,  # type: ignore[arg-type]
                     work_id=result["work_id"], kind=row.kind, problem=problem or "",  # type: ignore[arg-type]
                     files=[UploadFileOut(**item) for item in uploads.files_of(row)], limits=_limits(),
                     created_at=row.created_at)


@router.post("", response_model=UploadOut, status_code=201, summary="Begin an upload of a book into a library",
             responses={403: {"description": "forbidden"}, 404: {"description": "not_found"},
                        409: {"description": "no_write, hidden_folder_link"}, 422: {"description": "library_kind"},
                        429: {"description": "too_many_uploads"}})
def begin(payload: UploadIn, account: Account, db: DbSession) -> UploadOut:
    row = uploads.create(db, account, library_id=payload.library_id, work_id=payload.work_id, kind=payload.kind)
    return _out(db, row)


@router.get("/{upload_id}", response_model=UploadOut, summary="How far an upload is, and what came of it",
            responses={404: {"description": "not_found"}})
def read(upload_id: int, account: Account, db: DbSession) -> UploadOut:
    return _out(db, uploads.get(db, account, upload_id))


@router.put("/{upload_id}/files", response_model=UploadFileOut, status_code=201,
            summary="Send one file of an upload (the body is the file)",
            responses={403: {"description": "forbidden: the right to bring books into the library is gone"},
                       404: {"description": "not_found"}, 409: {"description": "upload_closed, file_exists"},
                       413: {"description": "too_large, too_many_files"},
                       415: {"description": "format_not_accepted"}, 422: {"description": "invalid_input"}})
async def send_file(upload_id: int, request: Request, account: Account,
                    path: str = Query(min_length=1, max_length=uploads.MAX_NAME)) -> UploadFileOut:
    declared_text = request.headers.get("content-length")
    declared = int(declared_text) if declared_text and declared_text.isdigit() else None

    def prepare() -> tuple[Any, int]:
        with SessionLocal() as db:
            return uploads.prepare(db, account, upload_id, path, declared)

    target, allowed = await anyio.to_thread.run_sync(prepare)
    size = 0
    complete = False
    created = False
    lost = False
    try:
        try:
            handle = await anyio.open_file(target, "xb")
        except FileExistsError:
            # Another file of this name was sent at the same moment: that one is the upload's, this one is refused (and
            # leaves the other, and its room, alone).
            lost = True
            raise error("file_exists", "A file of this name is in the upload already.", 409) from None
        created = True
        async with handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > allowed:
                    raise error("too_large", "The file is too large.", 413, max_mb=allowed // (1024 * 1024))
                await handle.write(chunk)
        if declared is not None and size != declared:
            raise error("upload_broken", "The file did not arrive whole.", 400)
        if size == 0:
            raise error("empty", "The file is empty.", 422)
        complete = True
    finally:
        if not lost:
            uploads.release(target)
        if created and not complete:
            try:
                target.unlink()
            except OSError:
                pass

    def note() -> None:
        with SessionLocal() as db:
            uploads.received(db, account, upload_id, size)

    await anyio.to_thread.run_sync(note)
    relative = uploads.clean_name(path)
    return UploadFileOut(path=relative, size=size)


class FinishIn(BaseModel):
    language: str = Field(default="", max_length=8, pattern=r"^[a-z]{0,8}$",
                          description="The language of the book, when the person knows it (else the file says).")


@router.post("/{upload_id}/finish", response_model=UploadOut, status_code=202,
             summary="Every file is there: file the upload like a download",
             responses={403: {"description": "forbidden: the right to bring books into the library is gone"},
                        404: {"description": "not_found"}, 409: {"description": "upload_closed, already_loading"},
                        422: {"description": "upload_empty, upload_mixed, library_kind"}})
def finish(upload_id: int, payload: FinishIn, account: Account, db: DbSession) -> UploadOut:
    return _out(db, uploads.finish(db, account, upload_id, payload.language))


@router.delete("/{upload_id}", status_code=204, summary="Drop an upload that is still coming, with its files",
               responses={404: {"description": "not_found"}, 409: {"description": "upload_closed"}})
def cancel(upload_id: int, account: Account, db: DbSession) -> Response:
    uploads.cancel(db, account, upload_id)
    return Response(status_code=204)
