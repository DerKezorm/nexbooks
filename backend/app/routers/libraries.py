"""Libraries: the folders of books, who may see them, and the scan.

Listing and reading a library is open to a signed-in browser, a device and a program with a key, and shows a person
only the libraries they are a member of. Making, changing and removing a library and setting who is in it want the
operator in a signed-in browser (``OperatorAccount``, never a device token). Starting a scan and reading the list of
problems want a signed-in browser, the operator or a member with ``manage``.

The path of a library is shown to the operator only. The list of problems shows paths *inside* the library.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import func, select

from ..deps import BrowserAccount, DbSession, OperatorAccount, Reader
from ..errors import error
from ..models import MANAGE, OPERATOR, Account, Library, LibraryMember, LibraryProblem
from ..services import libraries as service
from ..services import scanner
from ..services.readers.limits import REASONS
from ..services.sources import config, throttle

router = APIRouter(prefix="/api/v1/libraries", tags=["libraries"])

#: The reasons a file is on the list of problems, in English (the interface has its own sentences by code).
REASON_TEXT = {
    "unreadable": "The file could not be read: it is broken or not what its name says.",
    "unsupported": "This kind of file is not read.",
    "too_large": "The file, or a part of it, is larger than the limit.",
    "too_many_entries": "The e-book holds more files than a book has.",
    "zip_bomb": "The e-book unpacks to far more than it weighs.",
    "encrypted": "The file is encrypted.",
    "timeout": "Reading the file took too long.",
    "crashed": "The reader stopped while reading the file.",
    "link": "A link: nexbooks does not follow links.",
    "changed": "The file changed while it was read.",
    "bad_name": "The name is not valid text (or holds a control character): it was left out.",
    "failed": "nexbooks failed to handle the file; the log says why. It is tried again when the file changes.",
}


class MemberOut(BaseModel):
    user_id: int
    name: str
    display_name: str
    right: Literal["read", "acquire", "manage"]


class Counts(BaseModel):
    ebooks: int
    audiobooks: int
    files: int
    bytes: int
    problems: int
    #: Books the matching is not sure about and a person has to look at (only for a member with ``manage``).
    review: int
    #: The books that have a place in the matching, and how many of them wait for the sources (pending): "140 of 202
    #: books" is ``entries`` minus ``pending`` of ``entries``. Only for a member with ``manage``, zero for the others.
    entries: int = 0
    pending: int = 0


class Progress(BaseModel):
    done: int
    total: int


class Resting(BaseModel):
    """A source that is left alone until a time (after errors, or at its limit for the day)."""

    source: Literal["hardcover", "openlibrary", "audible", "dnb"]
    until: datetime


class LibraryOut(BaseModel):
    """A library as a member sees it."""

    id: int
    name: str
    kind: Literal["ebook", "audio", "both"]
    my_right: Literal["read", "acquire", "manage"] | None
    state: Literal["idle", "queued", "running"]
    counts: Counts
    #: While a run is going: the files done and the files to do (zero otherwise). The page shows a bar for it.
    progress: Progress
    #: The matching of the books with the sources: running, queued or idle, and the sources it waits for because they
    #: rest. ``off``: books wait, and no source is switched on or ready to be asked, so nothing is being matched. Only
    #: for a member with ``manage`` (idle and empty for the others).
    matching: Literal["idle", "queued", "running", "off"] = "idle"
    resting: list[Resting] = []


class LibraryAdmin(LibraryOut):
    """A library as the operator sees it: with its path, the members, the last run and the progress of the one going."""

    path: str
    scan_enabled: bool
    scanned_at: datetime | None
    report: dict[str, Any] | None
    members: list[MemberOut]


class LibraryIn(BaseModel):
    name: str = Field(min_length=1, max_length=service.NAME_MAX)
    path: str = Field(min_length=1, max_length=1000)
    kind: Literal["ebook", "audio", "both"] = "both"
    scan_enabled: bool = True


class LibraryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=service.NAME_MAX)
    kind: Literal["ebook", "audio", "both"] | None = None
    scan_enabled: bool | None = None


class MemberRow(BaseModel):
    user_id: int = Field(ge=1, le=2**31 - 1)
    right: Literal["read", "acquire", "manage"]


class MembersIn(BaseModel):
    """Everybody who is to be in the library, with their right; who is left out leaves."""

    members: list[MemberRow] = Field(max_length=service.MAX_MEMBERS)


class MemberChangeRow(BaseModel):
    user_id: int = Field(ge=1, le=2**31 - 1)
    #: What the caller saw: the right the person has now, or None when they are not a member.
    was: Literal["read", "acquire", "manage"] | None
    #: What it is to be: a right, or None to take the person out.
    right: Literal["read", "acquire", "manage"] | None


class MembersChangeIn(BaseModel):
    """Only the persons whose right changes, each with the right the caller saw. Everybody else stays as they are."""

    changes: list[MemberChangeRow] = Field(min_length=1, max_length=service.MAX_MEMBERS)


class PathIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class ProblemOut(BaseModel):
    path: str
    reason: str
    message: str
    seen_at: datetime


class ProblemList(BaseModel):
    total: int
    items: list[ProblemOut]


class ScanQueued(BaseModel):
    state: Literal["queued", "running"]


class ScanIn(BaseModel):
    #: Read every file again, not only the new and changed ones. Nothing a person decided is lost by it; it takes as
    #: long as the first reading of the library did. Only a real boolean: `"yes"` or `1` are no request. Allowed to the
    #: operator and to a member with `manage`, like the plain scan.
    everything: StrictBool = False


def _resting(db: DbSession) -> list[Resting]:
    """The sources the matching may ask that rest now, with the time they are left alone until."""
    found = []
    for source in config.CATALOGUES:
        state = throttle.state_of(source)
        if state.blocked_until is not None and state.blocked_until > throttle.now() and (
                config.availability(db, source).state == "resting"):
            found.append(Resting(source=source, until=state.blocked_until))  # type: ignore[arg-type]
    return found


def _brief(db: DbSession, libs: list[Library], uid: int, counts: dict[int, dict[str, int]],
           states: dict[int, str]) -> list[LibraryOut]:
    matching = service.match_states(db, [lib.id for lib in libs])
    resting = _resting(db) if libs else []
    asking = bool(resting) or bool(config.ready_sources(db, config.CATALOGUES))
    rights = dict(db.execute(select(LibraryMember.library_id, LibraryMember.right).where(
        LibraryMember.user_id == uid, LibraryMember.library_id.in_([lib.id for lib in libs]))).all()) if libs else {}
    out = []
    for lib in libs:
        numbers = dict(counts[lib.id])
        right = rights.get(lib.id)
        if right != MANAGE:
            numbers["problems"] = 0
            numbers["review"] = 0
            numbers["entries"] = 0
            numbers["pending"] = 0
        shown = matching[lib.id] if right == MANAGE else "idle"
        if shown == "idle" and numbers["pending"] > 0 and not asking:
            shown = "off"
        out.append(LibraryOut(id=lib.id, name=lib.name, kind=lib.kind, my_right=right,  # type: ignore[arg-type]
                              state=states[lib.id], counts=Counts(**numbers),  # type: ignore[arg-type]
                              progress=Progress(done=lib.progress_done, total=lib.progress_total),
                              matching=shown,  # type: ignore[arg-type]
                              resting=resting if right == MANAGE and numbers["pending"] > 0 else []))
    return out


def _admin(db: DbSession, libs: list[Library], operator: Account) -> list[LibraryAdmin]:
    ids = [lib.id for lib in libs]
    counts = service.counts(db, ids)
    states = service.scan_states(db, ids)
    members: dict[int, list[MemberOut]] = {ident: [] for ident in ids}
    if ids:
        for member, account in db.execute(
                select(LibraryMember, Account).join(Account, Account.id == LibraryMember.user_id)
                .where(LibraryMember.library_id.in_(ids)).order_by(Account.name)):
            members[member.library_id].append(MemberOut(user_id=account.id, name=account.name,
                                                        display_name=account.display_name,
                                                        right=member.right))  # type: ignore[arg-type]
    own = dict(db.execute(select(LibraryMember.library_id, LibraryMember.right).where(
        LibraryMember.user_id == operator.id)).all())
    out = []
    for lib in libs:
        out.append(LibraryAdmin(
            id=lib.id, name=lib.name, kind=lib.kind, my_right=own.get(lib.id),  # type: ignore[arg-type]
            state=states[lib.id], counts=Counts(**counts[lib.id]),  # type: ignore[arg-type]
            path=lib.path, scan_enabled=lib.scan_enabled, scanned_at=lib.scanned_at,
            progress=Progress(done=lib.progress_done, total=lib.progress_total), report=lib.report,
            members=members[lib.id]))
    return out


@router.get("", response_model=list[LibraryOut], summary="The libraries the caller is a member of")
def listing(account: Reader, db: DbSession) -> list[LibraryOut]:
    libs = list(db.scalars(select(Library).where(Library.id.in_(service.member_library_ids(account.id)))
                           .order_by(func.lower(Library.name))))
    ids = [lib.id for lib in libs]
    return _brief(db, libs, account.id, service.counts(db, ids), service.scan_states(db, ids))


@router.get("/admin", response_model=list[LibraryAdmin],
            summary="Every library with its path, members and last run (the operator)")
def admin_listing(operator: OperatorAccount, db: DbSession) -> list[LibraryAdmin]:
    return _admin(db, list(db.scalars(select(Library).order_by(func.lower(Library.name)))), operator)


@router.get("/roots", summary="The folders libraries may lie in, with the folders directly in them (the operator)")
def roots(_operator: OperatorAccount) -> dict[str, Any]:
    return {"roots": service.folders_below_roots()}


@router.post("/check", summary="Whether a folder can be a library; says why not (the operator)")
def check(payload: PathIn, _operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    try:
        real = service.check_path(payload.path, db=db)
    except service.PathProblem as exc:
        raise error(exc.code, "This folder cannot be a library.", 422, field="path") from exc
    return {"ok": True, "path": str(real)}


@router.post("", response_model=LibraryAdmin, status_code=201, summary="Make a library (the operator)")
def create(payload: LibraryIn, operator: OperatorAccount, db: DbSession) -> LibraryAdmin:
    library = service.create(db, operator, payload.name, payload.path, payload.kind, payload.scan_enabled)
    return _admin(db, [library], operator)[0]


@router.get("/{library_id}", response_model=LibraryOut, summary="One library of the caller")
def read(library_id: int, account: Reader, db: DbSession) -> LibraryOut:
    library = service.require_readable(db, account.id, library_id)
    return _brief(db, [library], account.id, service.counts(db, [library.id]),
                  service.scan_states(db, [library.id]))[0]


@router.patch("/{library_id}", response_model=LibraryAdmin,
              summary="Rename a library or change its kind (the operator)")
def patch(library_id: int, payload: LibraryPatch, operator: OperatorAccount, db: DbSession) -> LibraryAdmin:
    library = db.get(Library, library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    service.update(db, library, name=payload.name, kind=payload.kind, scan_enabled=payload.scan_enabled)
    return _admin(db, [library], operator)[0]


@router.delete("/{library_id}", status_code=204,
               summary="Remove a library from nexbooks (the operator); the files stay where they are")
def delete(library_id: int, _operator: OperatorAccount, db: DbSession) -> None:
    library = db.get(Library, library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    service.remove(db, library)
    scanner.sweep_covers(db)


@router.put("/{library_id}/members", response_model=LibraryAdmin,
            summary="Set who is in the library with which right, all at once (the operator)")
def set_members(library_id: int, payload: MembersIn, operator: OperatorAccount, db: DbSession) -> LibraryAdmin:
    library = db.get(Library, library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    service.set_members(db, library, [service.Member(m.user_id, m.right) for m in payload.members])
    return _admin(db, [library], operator)[0]


@router.patch("/{library_id}/members", response_model=LibraryAdmin,
              summary="Change the right of some persons, if nobody changed them meanwhile (the operator)",
              responses={409: {"description": "members_changed: somebody else changed one of them; look again"}})
def change_members(library_id: int, payload: MembersChangeIn, operator: OperatorAccount,
                   db: DbSession) -> LibraryAdmin:
    library = db.get(Library, library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    service.change_members(db, library, [service.MemberChange(item.user_id, item.was, item.right)
                                         for item in payload.changes])
    return _admin(db, [library], operator)[0]


@router.post("/{library_id}/scan", response_model=ScanQueued, status_code=202,
             summary="Read the folder again now, or every file again (the operator, or a member with manage)")
def scan(library_id: int, account: BrowserAccount, db: DbSession, payload: ScanIn | None = None) -> ScanQueued:
    library = service.require_manageable(db, account, library_id)
    service.start_scan(db, library.id, everything=bool(payload and payload.everything))
    state = service.scan_states(db, [library.id])[library.id]
    return ScanQueued(state="running" if state == "running" else "queued")


@router.get("/{library_id}/problems", response_model=ProblemList,
            summary="The files the scan could not read, with the reason (the operator, or a member with manage)")
def problems(library_id: int, account: BrowserAccount, db: DbSession,
             limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0, le=1_000_000),
             ) -> ProblemList:
    if account.role == OPERATOR:
        library = db.get(Library, library_id)
        if library is None:
            raise error("not_found", "Not found.", 404)
    else:
        library = service.require_manageable(db, account, library_id)
    total = db.scalar(select(func.count(LibraryProblem.id)).where(LibraryProblem.library_id == library.id)) or 0
    rows = db.scalars(select(LibraryProblem).where(LibraryProblem.library_id == library.id)
                      .order_by(LibraryProblem.path).limit(limit).offset(offset))
    return ProblemList(total=total, items=[
        ProblemOut(path=row.path, reason=row.reason if row.reason in REASONS else "unreadable",
                   message=REASON_TEXT.get(row.reason, REASON_TEXT["unreadable"]), seen_at=row.seen_at)
        for row in rows])
