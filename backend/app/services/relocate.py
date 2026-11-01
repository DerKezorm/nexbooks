"""An edition into another library: every file of it, from the folder of one library into the folder of another, by
the naming scheme of filing.

**The edition stays the same.** Its row is not made anew: its library changes, its files follow (their rows keep their
ids, their paths and units change), and so everything that hangs on the edition stays where it is: the progress, the
bookmarks, the history, the corrections and what the matching found. The next scan of either library finds every file at
the place the database knows, unchanged (``held_paths`` keeps a scan off files a move is busy with).

**Who.** A member with ``manage`` in the library the edition lies in *and* in the one it goes to, the operator included
(no role is a right in a library). The target must take the kind of the edition and be writable, and so must the
source (its files are taken away at the end).

**How, never over anything and never half.** A move is a job in the background (kind ``relocate``, main lane, so it
takes turns with the scans, the tidying and the filing) with a journal of its files (``RelocationFile``), and it
survives a restart. It goes in three steps:

1. *place*: every file is put at its goal and the source stays as it is: a second name on the same disk (``os.link``),
   else a copy under a hidden partial name, its size compared, then given its name (``placing.place``, which never
   replaces anything). A goal that is taken is never used: a recording gets a folder of its own (``Title (2)``), an
   e-book a name of its own.
2. *switch*: in one transaction the edition and its files belong to the target.
3. *clear*: the files at the source are taken away (each only when its twin lies at the goal with its size), and the
   folders that became empty by it (never the library's folder, never through a link).

A step that fails before the switch puts back what it did (the goals it made, the folders it made) and the edition lies
where it lay; one that fails after it leaves the edition in the target and says how many files are still at the source.
Either can be tried again and goes on from where it stopped.
"""

from __future__ import annotations

import errno
import logging
import os
import posixpath
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import (
    AUTHOR,
    EDITION_AUDIO,
    MANAGE,
    NARRATOR,
    ROLE_BOOK,
    Account,
    BookFile,
    Credit,
    Edition,
    Library,
    LibraryMember,
    MatchEntry,
    Person,
    Relocation,
    RelocationFile,
    Series,
    Work,
)
from . import catalog, jobs, libraries, search_index
from .organize import plan as planning
from .organize import scheme as schemes
from .procure import formats, placing
from .readers.files import parts_of

logger = logging.getLogger("nexbooks.relocate")

KIND = "relocate"
ACTIVE = ("queued", "running")
#: How many goals a preview names (an e-book in four formats, the first parts of a recording).
PREVIEW = 4


class Refused(Exception):
    """A move that cannot go (or go on): ``code`` says why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- Who may, and what ------------------------------------------------------------------------------------------------


def _manages(db: Session, uid: int, library_id: int) -> bool:
    return libraries.at_least(libraries.right_in(db, uid, library_id), MANAGE)


def edition_of(db: Session, account: Account, edition_id: int) -> tuple[Edition, Library]:
    """The edition and its library, for a member who manages that library; 404 for one the caller does not see (or that
    has no file on the disk), 403 for one they only read."""
    edition = db.get(Edition, edition_id)
    if edition is None or edition.missing_at is not None:
        raise error("not_found", "Not found.", 404)
    library = libraries.require_reviewer(db, account.id, edition.library_id)
    return edition, library


def _candidates(db: Session, uid: int, kind: str) -> list[Library]:
    """The libraries ``uid`` manages that take editions of ``kind``, by name."""
    rows = db.scalars(select(Library).join(LibraryMember, LibraryMember.library_id == Library.id)
                      .where(LibraryMember.user_id == uid, LibraryMember.right == MANAGE).order_by(Library.name))
    return [row for row in rows if libraries.kind_allows(row.kind, kind)]


def movable(db: Session, uid: int, editions: list[Edition]) -> set[int]:
    """The editions that can be offered to be moved: the caller manages their library, it is writable, and at least one
    other library the caller manages takes the kind and is writable."""
    found: set[int] = set()
    writable: dict[int, bool] = {}

    def ok(library: Library) -> bool:
        if library.id not in writable:
            writable[library.id] = formats.write_state(library.path)[0]
        return writable[library.id]

    by_kind: dict[str, list[Library]] = {}
    for edition in editions:
        if edition.missing_at is not None:
            continue
        if edition.kind not in by_kind:
            by_kind[edition.kind] = _candidates(db, uid, edition.kind)
        places = by_kind[edition.kind]
        source = next((row for row in places if row.id == edition.library_id), None)
        if source is None or not ok(source):
            continue
        if any(row.id != source.id and ok(row) for row in places):
            found.add(edition.id)
    return found


def busy_library(db: Session, library_id: int) -> bool:
    """Whether a move into or out of this library is under way (tidying waits for it)."""
    return db.scalar(select(Relocation.id).where(
        Relocation.state.in_(ACTIVE), (Relocation.source_id == library_id) | (Relocation.target_id == library_id))
        .limit(1)) is not None


def held_paths(db: Session, library_id: int) -> set[str]:
    """The files of a library a move that is not done is busy with, which a scan leaves alone: the goals in the target
    while they are being placed (they belong to the edition in the source until the switch), and what is left at the
    source after it (the edition belongs to the target already)."""
    held: set[str] = set()
    for step, source_id, target_id, source, target, state in db.execute(
            select(Relocation.step, Relocation.source_id, Relocation.target_id, RelocationFile.source,
                   RelocationFile.target, RelocationFile.state)
            .join(RelocationFile, RelocationFile.relocation_id == Relocation.id)
            .where(Relocation.step != "done", (Relocation.source_id == library_id)
                   | (Relocation.target_id == library_id))):
        if step == "place" and target_id == library_id:
            held.add(target)
        elif step == "clear" and source_id == library_id and state != "done":
            held.add(source)
    return held


# --- Where the files go -----------------------------------------------------------------------------------------------


@dataclass
class Goal:
    file: BookFile
    target: str


@dataclass
class Plan:
    goals: list[Goal] = field(default_factory=list)
    #: The folder of the edition in the target, when it has one of its own (a recording, an e-book with files beside
    #: it).
    folder: str = ""


def _values(db: Session, edition: Edition) -> dict[str, str]:
    """The values of the naming scheme for an edition (as tidying takes them)."""
    people: dict[str, list[tuple[str, str]]] = {AUTHOR: [], NARRATOR: []}
    for role, name, sort_name in db.execute(
            select(Credit.role, Person.name, Person.sort_name).join(Person, Person.id == Credit.person_id)
            .where(Credit.edition_id == edition.id, Credit.role.in_((AUTHOR, NARRATOR))).order_by(Credit.position)):
        people[role].append((name, sort_name))
    year = edition.published[:4] if edition.published[:4].isdigit() else ""
    if not year:
        work_year = db.scalar(select(Work.year).where(Work.id == edition.work_id))
        year = str(work_year) if work_year else ""
    series = edition.series_name
    if not series and edition.series_id:
        series = db.scalar(select(Series.name).where(Series.id == edition.series_id)) or ""
    authors, narrators = people[AUTHOR], people[NARRATOR]
    return {
        "Author": authors[0][0] if authors else "", "AuthorSort": authors[0][1] if authors else "",
        "Series": series, "Volume": schemes.volume_text(edition.series_pos) if series else "",
        "Title": edition.title, "Year": year, "Language": edition.language,
        "Narrator": narrators[0][0] if narrators else "",
    }


def _ending(path: str) -> str:
    return posixpath.splitext(path)[1].lstrip(".") or "bin"


def _own_folder(root: Path, parts: list[str], books: list[BookFile],
                beside: list[tuple[BookFile, str]]) -> Plan:
    """An e-book with files beside it: a folder of its own (the scheme's path as the folder, its formats in it under the
    folder's name), so that the files beside it speak for it alone, as they did."""
    base = "/".join(parts)
    for number in range(1, placing.MAX_TRIES + 1):
        folder = placing._numbered(base, number)
        kind = placing._kind_on_disk(root, folder)
        if kind == "link":
            raise Refused("link")
        if kind != "none":
            continue
        stem = folder.rsplit("/", 1)[-1]
        goals = [Goal(row, f"{folder}/{stem}.{_ending(row.path)}") for row in books]
        goals += [Goal(row, f"{folder}/{relative}") for row, relative in beside]
        if len({goal.target.casefold() for goal in goals}) != len(goals):
            raise Refused("collision")
        if any(len(goal.target) > schemes.MAX_PATH for goal in goals):
            raise Refused("too_long")
        return Plan(goals, folder)
    raise Refused("taken")


def plan(db: Session, edition: Edition, source: Library, target: Library) -> Plan:
    """Where each file of the edition goes in ``target`` (nothing is touched): its book files and the files beside it
    that belong to it alone (a file beside two books stays with the other one). ``Refused`` when there is no goal."""
    layout = planning.layout_of(db, source)
    books = sorted(layout.book_rows.get(edition.id, []), key=lambda row: (row.part, row.path))
    if not books:
        raise Refused("no_files")
    beside = [] if edition.id in layout.shared else list(layout.beside.get(edition.id, []))
    parts = placing.render(formats.config(db)["schema"], _values(db, edition))
    root = Path(target.path)
    try:
        if edition.kind == EDITION_AUDIO:
            goal = placing.audio_goal(root, parts, [(str(row.id), row.path, row.size) for row in books],
                                      catalog._unit_dir(books[0].path))
            goals = [Goal(row, goal.targets[str(row.id)]) for row in books]
            goals += [Goal(row, f"{goal.folder}/{relative}") for row, relative in beside]
            if len({item.target.casefold() for item in goals}) != len(goals):
                raise Refused("collision")
            if any(len(item.target) > schemes.MAX_PATH for item in goals):
                raise Refused("too_long")
            return Plan(goals, goal.folder)
        if beside:
            return _own_folder(root, parts, books, beside)
        found = placing.ebook_goal(root, parts, [(str(row.id), _ending(row.path), row.size) for row in books])
        return Plan([Goal(row, found.targets[str(row.id)]) for row in books])
    except placing.PlaceError as exc:
        raise Refused(exc.code) from exc


# --- Asking for a move ------------------------------------------------------------------------------------------------


def _busy(db: Session, edition: Edition, source_id: int, target_id: int) -> bool:
    from .organize import active_run

    if db.scalar(select(Relocation.id).where(Relocation.edition_id == edition.id, Relocation.state.in_(ACTIVE))
                 .limit(1)) is not None:
        return True
    return active_run(db, source_id) is not None or active_run(db, target_id) is not None


def options(db: Session, account: Account, edition: Edition, source: Library) -> dict[str, Any]:
    """What the dialog shows: the libraries the edition may go to (the writable ones can be chosen, the others are named
    with the reason), where its files would lie in each, and the latest move of the edition."""
    ok, why = formats.write_state(source.path)
    files = db.execute(select(func.count(BookFile.id), func.coalesce(func.sum(BookFile.size), 0)).where(
        BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))).one()
    targets: list[dict[str, Any]] = []
    for row in _candidates(db, account.id, edition.kind):
        if row.id == source.id:
            continue
        writable, problem = formats.write_state(row.path)
        entry: dict[str, Any] = {"id": row.id, "name": row.name, "kind": row.kind, "writable": writable,
                                 "write_problem": problem, "preview": [], "more": 0, "copies": False, "problem": "",
                                 "folder": ""}
        if writable:
            try:
                found = plan(db, edition, source, row)
            except Refused as exc:
                entry["problem"] = exc.code
            else:
                shown = [goal.target for goal in found.goals if goal.file.role == ROLE_BOOK]
                entry["preview"] = shown[:PREVIEW]
                entry["more"] = max(0, len(found.goals) - PREVIEW)
                entry["folder"] = found.folder
                entry["copies"] = placing.crosses_disks(Path(source.path), Path(row.path))
        targets.append(entry)
    latest = db.scalar(select(Relocation).where(Relocation.edition_id == edition.id)
                       .order_by(Relocation.id.desc()).limit(1))
    return {"edition_id": edition.id, "kind": edition.kind,
            "source": {"id": source.id, "name": source.name, "writable": ok, "write_problem": why},
            "files": int(files[0]), "bytes": int(files[1]), "targets": targets,
            "busy": _busy(db, edition, source.id, source.id), "latest": latest}


def start(db: Session, account: Account, edition: Edition, source: Library, target_id: int) -> Relocation:
    """Puts the move of ``edition`` into ``target_id`` in the queue, after the person confirmed it."""
    if target_id == source.id:
        raise error("move_same", "The book lies in this library already.", 422, field="library_id")
    target = libraries.require_reviewer(db, account.id, target_id)
    if not libraries.kind_allows(target.kind, edition.kind):
        raise error("move_kind", "This library does not take this kind of book.", 422, field="library_id")
    for library, code in ((source, "move_source_not_writable"), (target, "move_target_not_writable")):
        ok, why = formats.write_state(library.path)
        if not ok:
            raise error(code, "nexbooks may not write in this library.", 409, reason=why, library_id=library.id)
    if _busy(db, edition, source.id, target.id):
        raise error("move_busy", "The book or one of the libraries is busy. Try again later.", 409)
    try:
        plan(db, edition, source, target)
    except Refused as exc:
        raise error("move_no_goal", "There is no place for the book in this library.", 409, reason=exc.code) from exc
    row = Relocation(edition_id=edition.id, source_id=source.id, target_id=target.id, created_by=account.id,
                     state="queued", step="place", made=[], created_at=clock.now())
    db.add(row)
    try:
        # One move of an edition at a time, by the index: two presses at once make one move.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise error("move_busy", "The book or one of the libraries is busy. Try again later.", 409) from exc
    jobs.enqueue(db, KIND, ref=row.id)
    db.commit()
    logger.info("Move asked id=%s edition=%s from=%s to=%s by=%s", row.id, edition.id, source.id, target.id,
                account.name)
    return row


def load(db: Session, account: Account, relocation_id: int) -> Relocation:
    """A move, for a member who manages the library it comes from or the one it goes to; 404 for everybody else."""
    row = db.get(Relocation, relocation_id)
    if row is None or not any(ident is not None and _manages(db, account.id, ident)
                              for ident in (row.source_id, row.target_id)):
        raise error("not_found", "Not found.", 404)
    return row


def retry(db: Session, account: Account, row: Relocation) -> Relocation:
    """A move that failed goes on from its step (both libraries still managed by the caller)."""
    if row.state != "failed":
        raise error("move_state", "This move is not one that failed.", 409)
    if row.edition_id is None or row.source_id is None or row.target_id is None:
        raise error("move_gone", "The book or a library of this move is gone.", 409)
    for ident in (row.source_id, row.target_id):
        libraries.require_reviewer(db, account.id, ident)
    from .organize import active_run

    if active_run(db, row.source_id) is not None or active_run(db, row.target_id) is not None or db.scalar(
            select(Relocation.id).where(Relocation.edition_id == row.edition_id, Relocation.state.in_(ACTIVE))
            .limit(1)) is not None:
        raise error("move_busy", "The book or one of the libraries is busy. Try again later.", 409)
    row.state, row.error, row.finished_at = "queued", "", None
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise error("move_busy", "The book or one of the libraries is busy. Try again later.", 409) from exc
    jobs.enqueue(db, KIND, ref=row.id)
    db.commit()
    return row


def report(db: Session, row: Relocation) -> dict[str, Any]:
    """How far a move is, and where the files lie: ``source`` (all of them where they were), ``target`` (all at their
    goals), or ``both`` (``left`` files at the other end: goals that could not be taken away again, or files at the
    source that are still there after the switch)."""
    counts = dict(db.execute(select(RelocationFile.state, func.count(RelocationFile.id))
                             .where(RelocationFile.relocation_id == row.id).group_by(RelocationFile.state)).all())
    total = sum(counts.values())
    placed, done = int(counts.get("placed", 0)), int(counts.get("done", 0))
    if row.step == "place":
        where, left = ("source", 0) if row.state != "failed" or placed == 0 else ("both", placed)
    elif row.step == "clear":
        where, left = ("both", placed) if placed else ("target", 0)
    else:
        where, left = "target", 0
    names = dict(db.execute(select(Library.id, Library.name).where(
        Library.id.in_([ident for ident in (row.source_id, row.target_id) if ident is not None]))).all())
    return {"id": row.id, "edition_id": row.edition_id,
            "source": {"id": row.source_id, "name": names.get(row.source_id, "")} if row.source_id else None,
            "target": {"id": row.target_id, "name": names.get(row.target_id, "")} if row.target_id else None,
            "state": row.state, "step": row.step, "error": row.error, "files": total,
            "placed": placed + done, "removed": done, "where": where, "left": left,
            "created_at": row.created_at, "started_at": row.started_at, "finished_at": row.finished_at}


# --- The job ----------------------------------------------------------------------------------------------------------


def _absolute(library: Library, relative: str) -> Path:
    return Path(library.path).joinpath(*parts_of(relative))


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _same(first: os.stat_result | None, second: os.stat_result | None) -> bool:
    return first is not None and second is not None and (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _missing_folders(root: Path, folder: str) -> list[str]:
    """The folders of ``folder`` below ``root`` that are not there yet, the outermost first."""
    missing: list[str] = []
    current = ""
    for part in parts_of(folder) if folder else []:
        current = f"{current}/{part}" if current else part
        if placing._kind_on_disk(root, current) == "none":
            missing.append(current)
    return missing


def _remove_empty(root: Path, folders: list[str]) -> list[str]:
    """Removes the folders that are empty, the deepest first, never ``root`` and never through a link. Returns those
    that stay."""
    kept: list[str] = []
    for folder in sorted(set(folders), key=lambda item: -item.count("/")):
        path = root.joinpath(*parts_of(folder))
        info = _lstat(path)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            kept.append(folder)
            continue
        try:
            os.rmdir(path)
        except OSError:
            kept.append(folder)
    return kept


def _fail(db: Session, row: Relocation, code: str, source: Library | None, target: Library | None) -> None:
    """The move stops with ``code``. Before the switch whatever it put in the target goes again (only what it made: a
    goal that is the source's second name, or a copy of the source's size), so that the edition lies whole where it
    lay; after it nothing is taken back (the edition belongs to the target, the rest of the source waits for a try)."""
    db.rollback()
    files = list(db.scalars(select(RelocationFile).where(RelocationFile.relocation_id == row.id)))
    if row.step == "place" and target is not None:
        root = Path(target.path)
        for item in files:
            placing.clear_own_partials(root, item.target)
            if item.state != "placed":
                continue
            goal = _absolute(target, item.target)
            seen = _lstat(goal)
            origin = _lstat(_absolute(source, item.source)) if source is not None else None
            ours = seen is not None and stat.S_ISREG(seen.st_mode) and (
                _same(seen, origin) or (item.how == "copy" and seen.st_size == item.size))
            if seen is None:
                item.state = "planned"
            elif ours:
                try:
                    os.unlink(goal)
                    item.state = "planned"
                except OSError:
                    logger.error("A goal of a move could not be taken away again id=%s", row.id)
        row.made = _remove_empty(root, list(row.made or []))
        if all(item.state == "planned" for item in files):
            # Nothing of it is left in the target: a new try plans anew (the goals may be others by then).
            db.execute(delete(RelocationFile).where(RelocationFile.relocation_id == row.id))
    row.state, row.error, row.finished_at = "failed", code[:40], clock.now()
    db.commit()
    logger.warning("Move failed id=%s step=%s error=%s", row.id, row.step, code)


def _place(db: Session, row: Relocation, edition: Edition, source: Library, target: Library) -> None:
    files = list(db.scalars(select(RelocationFile).where(RelocationFile.relocation_id == row.id)
                            .order_by(RelocationFile.id)))
    if not files:
        found = plan(db, edition, source, target)
        files = [RelocationFile(relocation_id=row.id, file_id=goal.file.id, source=goal.file.path,
                                target=goal.target[:1500], size=goal.file.size, mtime_ns=goal.file.mtime_ns,
                                state="planned") for goal in found.goals]
        db.add_all(files)
        db.commit()
    root = Path(target.path)
    copying = sum(item.size for item in files if item.state == "planned"
                  and placing.crosses_disks(_absolute(source, item.source), root))
    if copying and not placing.room_for(root, copying):
        raise Refused("no_space")
    # Everything at the source must be taken away at the end: a folder that keeps its files is found out now.
    for folder in {posixpath.dirname(item.source) for item in files}:
        if not os.access(_absolute(source, folder) if folder else Path(source.path), os.W_OK | os.X_OK):
            raise Refused("source_not_writable")
    for item in files:
        if item.state != "planned":
            continue
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        origin = placing.source_file(Path(source.path), item.source)
        info = os.lstat(origin)
        if (info.st_size, info.st_mtime_ns) != (item.size, item.mtime_ns):
            raise Refused("source_changed")
        goal = _absolute(target, item.target)
        seen = _lstat(goal)
        if seen is not None:
            # A try that stopped after the goal was there (and before it was written down) left it: it is taken when it
            # is the source's second name or the same content; anything else is somebody else's file.
            if _same(seen, info):
                item.state, item.how = "placed", "link"
            elif stat.S_ISREG(seen.st_mode) and placing.same_file(goal, item.size, placing.sha256(origin)):
                item.state, item.how = "placed", "copy"
            else:
                raise Refused("target_taken")
            db.commit()
            continue
        new = _missing_folders(root, posixpath.dirname(item.target))
        placing.clear_own_partials(root, item.target)
        try:
            how = placing.place(origin, item.size, root, item.target, how="link")
        finally:
            # The folders it made are written down whatever happened: a failure takes them away again.
            made = [folder for folder in new if placing._kind_on_disk(root, folder) == "dir"]
            if made:
                row.made = list(dict.fromkeys([*(row.made or []), *made]))
                db.commit()
        item.state, item.how = "placed", "link" if how == "link" else "copy"
        db.commit()
    _switch(db, row, edition, source, target, files)


def _switch(db: Session, row: Relocation, edition: Edition, source: Library, target: Library,
            files: list[RelocationFile]) -> None:
    """The edition and its files belong to the target, in one transaction."""
    from .scanner import READ_REV

    need_source = READ_REV + int(source.reread_rev or 0)
    need_target = READ_REV + int(target.reread_rev or 0)
    for item in files:
        goal = os.lstat(_absolute(target, item.target))
        if goal.st_size != item.size:
            raise Refused("target_changed")
        file_row = db.get(BookFile, item.file_id) if item.file_id is not None else None
        if file_row is None:
            continue
        # A file that went missing once at this name in the target leaves its row; this file takes its place. A row of
        # a file that is there is somebody else's: nothing is taken over.
        if db.scalar(select(BookFile.id).where(BookFile.library_id == target.id, BookFile.path == item.target,
                                               BookFile.id != file_row.id, BookFile.missing_at.is_(None))) is not None:
            raise Refused("target_taken")
        db.execute(delete(BookFile).where(BookFile.library_id == target.id, BookFile.path == item.target,
                                          BookFile.id != file_row.id, BookFile.missing_at.is_not(None)))
        db.flush()
        file_row.library_id = target.id
        file_row.path = item.target
        file_row.size, file_row.mtime_ns = goal.st_size, goal.st_mtime_ns
        if file_row.role == ROLE_BOOK:
            file_row.unit = catalog.unit_key(item.target, file_row.format,
                                             file_row.probe if isinstance(file_row.probe, dict) else None)
        file_row.dirty = False
        if int(file_row.rev or 0) >= need_source:
            file_row.rev = max(int(file_row.rev or 0), need_target)
    # Rows of files of the edition that were gone from the disk already stay behind no more.
    db.execute(delete(BookFile).where(BookFile.edition_id == edition.id, BookFile.library_id == source.id,
                                      BookFile.missing_at.is_not(None)))
    edition.library_id = target.id
    edition.updated_at = clock.now()
    db.execute(update(MatchEntry).where(MatchEntry.edition_id == edition.id).values(library_id=target.id))
    search_index.update(db, [edition.id])
    row.step = "clear"
    db.commit()
    # What the work shows and the guessed genres follow; a fault there is no fault of the move (it is done).
    try:
        work = db.get(Work, edition.work_id)
        if work is not None:
            catalog.refresh_work(db, work)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("The work of a moved edition could not be brought up to date id=%s", row.id)
    for library_id in (source.id, target.id):
        catalog.refresh_guesses(db, library_id)
    logger.info("Move switched id=%s edition=%s to=%s", row.id, edition.id, target.id)


def _clear(db: Session, row: Relocation, source: Library, target: Library) -> None:
    files = list(db.scalars(select(RelocationFile).where(RelocationFile.relocation_id == row.id)
                            .order_by(RelocationFile.id)))
    emptied: list[str] = []
    for item in files:
        if item.state == "done":
            continue
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        origin = _absolute(source, item.source)
        seen = _lstat(origin)
        goal = _lstat(_absolute(target, item.target))
        if goal is None or not stat.S_ISREG(goal.st_mode) or goal.st_size != item.size:
            # The file is not (or no longer) whole at its goal: the source is the copy that counts, it stays.
            raise Refused("target_changed")
        if seen is not None:
            if not stat.S_ISREG(seen.st_mode) or (seen.st_size != item.size and not _same(seen, goal)):
                raise Refused("source_changed")
            try:
                os.unlink(origin)
            except OSError as exc:
                raise Refused("source_left" if exc.errno != errno.ENOENT else "source_changed") from exc
        item.state = "done"
        db.commit()
        folder = posixpath.dirname(item.source)
        while folder:
            emptied.append(folder)
            folder = posixpath.dirname(folder)
    _remove_empty(Path(source.path), emptied)
    row.step, row.state, row.finished_at = "done", "done", clock.now()
    db.commit()
    for library_id in (source.id, target.id):
        jobs.enqueue(db, "scan", ref=library_id)
    db.commit()
    logger.info("Move done id=%s", row.id)


def execute(db: Session, row: Relocation) -> None:
    """Does the move ``row`` from the step it is at. Resumable; a refusal or a failure stops it with a code."""
    if row.state not in ACTIVE:
        return
    edition = db.get(Edition, row.edition_id) if row.edition_id is not None else None
    source = db.get(Library, row.source_id) if row.source_id is not None else None
    target = db.get(Library, row.target_id) if row.target_id is not None else None
    if edition is None or source is None or target is None:
        _fail(db, row, "gone", source, target)
        return
    row.state = "running"
    row.started_at = row.started_at or clock.now()
    db.commit()
    try:
        if row.step == "place":
            if edition.library_id != source.id:
                raise Refused("moved_meanwhile")
            for library, code in ((source, "source_not_writable"), (target, "target_not_writable")):
                if not formats.write_state(library.path)[0]:
                    raise Refused(code)
            _place(db, row, edition, source, target)
        if row.step == "clear":
            _clear(db, row, source, target)
    except jobs.Interrupted:
        db.commit()
        raise
    except Refused as exc:
        _fail(db, row, exc.code, source, target)
    except placing.PlaceError as exc:
        _fail(db, row, exc.code, source, target)
    except OSError as exc:
        _fail(db, row, "no_space" if exc.errno == errno.ENOSPC else "failed", source, target)
    except Exception:
        logger.exception("Move broke off id=%s", row.id)
        _fail(db, row, "failed", source, target)


def relocate_job(db: Session, job: Any) -> None:
    row = db.get(Relocation, job.ref)
    if row is not None:
        execute(db, row)


def settle(db: Session) -> None:
    """A move whose job is gone for good (given up after its tries) stops as failed, with what it did put back."""
    from ..models import Job

    for row in list(db.scalars(select(Relocation).where(Relocation.state.in_(ACTIVE)))):
        alive = db.scalar(select(Job.id).where(Job.kind == KIND, Job.ref == row.id,
                                               Job.state.in_(("queued", "running"))).limit(1))
        if alive is None:
            _fail(db, row, "interrupted", db.get(Library, row.source_id) if row.source_id else None,
                  db.get(Library, row.target_id) if row.target_id else None)


jobs.register(KIND, relocate_job, settle)


__all__ = ["KIND", "Refused", "busy_library", "edition_of", "execute", "held_paths", "load", "movable", "options",
           "plan", "report", "retry", "start"]
