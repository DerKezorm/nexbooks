"""The run of a tidying and its undoing: files are moved as the plan says, one book at a time, and written down.

**The only place nexbooks writes in a library.** Everything else only reads. A run is a job in the queue (kind
``organize``, the run as ``ref``): it survives a restart and goes on where it was. Its moves are its journal:
every move is a row (``planned``, then ``moved``), so that the undoing can read them backwards, and a run
stopped in the middle (a restart, a crash) knows what it did by looking at the disk (``_reconcile``): a file
that is gone from its place and lies at its goal was moved.

**Never overwritten.** A file goes to its goal by ``os.link`` and then the old name is removed, which fails if
the goal is there; where links are not possible (some network shares) the goal is looked at once more and the
file is renamed (which no system we run on does over an existing file but POSIX: the plan has refused every goal
that exists, this is the second look). Nothing is ever deleted but a folder that is empty.

**Never out of the library.** Every part of every path is checked on the way: a link is never followed, nothing
climbs out of the folder of the library, and a folder is made only where none is. The goals come from
``scheme.clean_part``, which makes a ``/`` or a ``..`` impossible, and are checked again here.

**A book moves whole or not at all.** If one file of a book cannot go (it changed since the preview, the goal
was taken in the meantime), the files of the book that did go are put back and the whole book is left
(``skipped``).

**What hangs on the book stays.** The files keep their rows (the database follows the move: the path and the
unit), so the edition, its progress, bookmarks and history are where they were. The next scan finds every file
at the place the database knows, unchanged.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from itertools import groupby
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import clock
from ...models import ROLE_BOOK, BookFile, Library, OrganizeMove, OrganizeRun
from .. import catalog, jobs
from ..readers.files import is_link, parts_of
from . import plan as planning

logger = logging.getLogger("nexbooks.organize")

KIND = "organize"
#: Commit the journal after this many books (the files are on the disk already; a crash in between is reconciled).
BATCH = 20
READ_ONLY_ERRORS = {errno.EROFS, errno.EACCES, errno.EPERM}


class Blocked(Exception):
    """A file that cannot go (or come back): the goal is taken, the way is a link, the file is not as it was."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NoWrite(Exception):
    """The folder may not be written to (a read-only mount, no right): the whole run stops."""


def _absolute(root: str, relative: str) -> str:
    return os.path.join(root, *parts_of(relative))


def _lstat(root: str, relative: str) -> os.stat_result | None:
    """The file at ``relative`` below ``root`` when it is a plain file and no part of the way is a link; None when it is
    not there; ``Blocked("link")`` when the way is a link."""
    current = root
    seen: os.stat_result | None = None
    parts = parts_of(relative)
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            seen = os.lstat(current)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise Blocked("unreadable") from exc
        if is_link(seen):
            raise Blocked("link")
        if index < len(parts) - 1 and not stat.S_ISDIR(seen.st_mode):
            return None
    return seen if seen is not None and stat.S_ISREG(seen.st_mode) else None


def _make_dirs(root: str, folder: str, made: list[str]) -> None:
    """Makes the folders of ``folder`` below ``root`` that are not there. A link or a file on the way is ``Blocked``."""
    if not folder:
        return
    current = ""
    for part in parts_of(folder):
        current = f"{current}/{part}" if current else part
        absolute = os.path.join(root, *current.split("/"))
        try:
            info = os.lstat(absolute)
        except FileNotFoundError:
            try:
                os.mkdir(absolute)
            except FileExistsError:
                continue
            except OSError as exc:
                if exc.errno in READ_ONLY_ERRORS:
                    raise NoWrite from exc
                raise Blocked("mkdir") from exc
            made.append(current)
            continue
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise Blocked("link" if is_link(info) else "exists")


def _place(source: str, target: str) -> None:
    """``source`` to ``target``, never over something. See the top of this module."""
    try:
        os.link(source, target)
    except FileExistsError as exc:
        raise Blocked("exists") from exc
    except OSError as exc:
        if exc.errno in READ_ONLY_ERRORS and exc.errno != errno.EPERM:
            raise NoWrite from exc
        # No hard links here (a network share, a file system without them): look once more, then rename.
        if os.path.lexists(target):
            raise Blocked("exists") from exc
        try:
            os.rename(source, target)
        except FileExistsError as inner:
            raise Blocked("exists") from inner
        except OSError as inner:
            if inner.errno in READ_ONLY_ERRORS:
                raise NoWrite from inner
            raise Blocked("move") from inner
        return
    try:
        os.unlink(source)
    except OSError as exc:
        # The file has two names now: take the new one away again, so that nothing is left twice.
        try:
            os.unlink(target)
        except OSError:
            logger.error("A file has two names and one could not be removed")
        if exc.errno in READ_ONLY_ERRORS:
            raise NoWrite from exc
        raise Blocked("move") from exc


def _units_of(db: Session, file_ids: list[int]) -> dict[int, BookFile]:
    return {row.id: row for row in db.scalars(select(BookFile).where(BookFile.id.in_(file_ids)))} if file_ids else {}


def _follow(db: Session, library_id: int, row: BookFile | None, path: str) -> None:
    """The database follows a file: its path, and for a book file its unit (which names the folder and the name)."""
    if row is None:
        return
    # A file that went missing once leaves its row; a new file at that name takes its place.
    db.execute(delete(BookFile).where(BookFile.library_id == library_id, BookFile.path == path, BookFile.id != row.id,
                                      BookFile.missing_at.is_not(None)))
    db.flush()
    row.path = path[:1500]
    if row.role == ROLE_BOOK:
        row.unit = catalog.unit_key(path, row.format, row.probe if isinstance(row.probe, dict) else None)


def _reconcile(root: str, move: OrganizeMove, *, back: bool) -> str:
    """What the disk says of a move that was left in the middle: ``moved`` (the file lies at the other end already),
    ``todo`` (it lies where it began), ``gone`` (it lies at neither), ``both`` (it lies at both) or ``changed``."""
    here, there = (move.target, move.source) if back else (move.source, move.target)
    start = _lstat(root, here)
    end = _lstat(root, there)
    if start is None and end is not None:
        return "moved" if end.st_size == move.size else "changed"
    if start is None and end is None:
        return "gone"
    if start is not None and end is not None:
        return "both"
    assert start is not None
    return "todo" if (start.st_size, start.st_mtime_ns) == (move.size, move.mtime_ns) else "changed"


def _group(moves: list[OrganizeMove]) -> list[list[OrganizeMove]]:
    ordered = sorted(moves, key=lambda m: (m.edition_id or 0, m.id))
    return [list(items) for _key, items in groupby(ordered, key=lambda m: m.edition_id or 0)]


def _walk(db: Session, library: Library, run: OrganizeRun, group: list[OrganizeMove], *, back: bool,
          made: list[str], files: dict[int, BookFile]) -> bool:
    """Moves one book (all its files). True when it went, False when it was left whole (every move of it
    ``skipped``)."""
    root = library.path
    done: list[OrganizeMove] = []
    wanted = "moved" if not back else "undone"
    try:
        for move in group:
            source, target = (move.target, move.source) if back else (move.source, move.target)
            seen = _reconcile(root, move, back=back)
            if seen == "moved":
                _follow(db, library.id, files.get(move.file_id or 0), target)
                done.append(move)
                continue
            if seen in ("changed", "gone", "both"):
                raise Blocked({"changed": "changed", "gone": "gone", "both": "exists"}[seen])
            _make_dirs(root, planning._dir_of(target), made)
            _place(_absolute(root, source), _absolute(root, target))
            _follow(db, library.id, files.get(move.file_id or 0), target)
            done.append(move)
    except (Blocked, NoWrite) as stop:
        # The book goes back where it was, whole.
        for move in reversed(done):
            source, target = (move.target, move.source) if back else (move.source, move.target)
            try:
                _place(_absolute(root, target), _absolute(root, source))
                _follow(db, library.id, files.get(move.file_id or 0), source)
            except (Blocked, NoWrite):
                logger.error("A file could not be put back run=%s", run.id)
        reason = stop.reason if isinstance(stop, Blocked) else "no_write"
        for move in group:
            move.state, move.reason = "skipped" if not back else "moved", reason if move not in done else "book_blocked"
        if isinstance(stop, NoWrite):
            raise
        return False
    for move in group:
        move.state, move.reason = wanted, ""
    return True


def _tidy_empty(root: str, folders: set[str], keep: set[str]) -> list[str]:
    """Removes the folders that are empty now (deepest first, and
    the empty ancestors of them), never the library's folder
    and never a folder in ``keep``. Returns the folders that went."""
    gone: list[str] = []
    for folder in sorted(folders, key=lambda f: -f.count("/")):
        current = folder
        while current and current not in keep:
            try:
                info = os.lstat(os.path.join(root, *current.split("/")))
                if is_link(info) or not stat.S_ISDIR(info.st_mode):
                    break
                os.rmdir(os.path.join(root, *current.split("/")))
            except OSError:
                break
            gone.append(current)
            current = planning._dir_of(current)
    return gone


def _finish_batch(db: Session, run: OrganizeRun, made: list[str], removed: list[str]) -> None:
    summary = dict(run.summary) if isinstance(run.summary, dict) else {}
    summary["made"] = list(dict.fromkeys([*(summary.get("made") or []), *made]))
    summary["removed"] = list(dict.fromkeys([*(summary.get("removed") or []), *removed]))
    run.summary = summary
    db.commit()


def execute(db: Session, run: OrganizeRun) -> None:
    """Does what the plan of the run says, book by book. Resumable: the books done are not done again."""
    library = db.get(Library, run.library_id)
    if library is None:
        raise jobs.GiveUp("library gone")
    ok, why = planning.writable(library.path)
    if not ok:
        run.state, run.error, run.finished_at = "failed", why or "no_write_access", clock.now()
        db.commit()
        return
    run.state = "running"
    run.started_at = run.started_at or clock.now()
    db.commit()
    made: list[str] = []
    sources: set[str] = set()
    try:
        moves = list(db.scalars(select(OrganizeMove).where(OrganizeMove.run_id == run.id,
                                                           OrganizeMove.state == "planned")
                                .order_by(OrganizeMove.id)))
        files = _units_of(db, [m.file_id for m in moves if m.file_id])
        for number, group in enumerate(_group(moves), start=1):
            if jobs.stopping.is_set():
                # What is done is written down: the next run goes on from here.
                _finish_batch(db, run, made, [])
                raise jobs.Interrupted
            if _walk(db, library, run, group, back=False, made=made, files=files):
                sources.update(planning._dir_of(m.source) for m in group)
            db.flush()
            run.done = db.scalar(select(func.count(OrganizeMove.id)).where(OrganizeMove.run_id == run.id,
                                                                     OrganizeMove.state != "planned")) or 0
            if number % BATCH == 0:
                _finish_batch(db, run, made, [])
                made = []
    except NoWrite:
        run.state, run.error, run.finished_at = "failed", "no_write_access", clock.now()
        db.commit()
        return
    kept = set((run.summary or {}).get("made") or []) | set(made)
    removed = _tidy_empty(library.path, sources, kept)
    _finish_batch(db, run, made, removed)
    run.state, run.finished_at = "done", clock.now()
    db.flush()
    run.done = db.scalar(select(func.count(OrganizeMove.id)).where(OrganizeMove.run_id == run.id,
                                                             OrganizeMove.state != "planned")) or 0
    db.commit()
    jobs.enqueue(db, "scan", ref=library.id)
    db.commit()


def undo(db: Session, run: OrganizeRun) -> None:
    """Puts back what the run moved, newest move first, book by book.

    Resumable. Folders the run made go if they are empty."""
    library = db.get(Library, run.library_id)
    if library is None:
        raise jobs.GiveUp("library gone")
    ok, why = planning.writable(library.path)
    if not ok:
        run.state, run.error, run.finished_at = "failed", why or "no_write_access", clock.now()
        db.commit()
        return
    run.state = "undoing"
    db.commit()
    made: list[str] = []
    try:
        moves = list(db.scalars(select(OrganizeMove).where(OrganizeMove.run_id == run.id, OrganizeMove.state == "moved")
                                .order_by(OrganizeMove.id.desc())))
        files = _units_of(db, [m.file_id for m in moves if m.file_id])
        for number, group in enumerate(_group(moves), start=1):
            if jobs.stopping.is_set():
                # What is done is written down: the next run goes on from here.
                _finish_batch(db, run, made, [])
                raise jobs.Interrupted
            _walk(db, library, run, group, back=True, made=made, files=files)
            if number % BATCH == 0:
                db.commit()
    except NoWrite:
        run.state, run.error, run.finished_at = "failed", "no_write_access", clock.now()
        db.commit()
        return
    db.flush()
    summary: dict[str, Any] = dict(run.summary) if isinstance(run.summary, dict) else {}
    targets = {planning._dir_of(m.target) for m in db.scalars(
        select(OrganizeMove).where(OrganizeMove.run_id == run.id, OrganizeMove.state == "undone"))}
    _tidy_empty(library.path, targets, set())
    summary["made"] = []
    summary["undone"] = True
    run.summary = summary
    db.flush()
    left = db.scalar(select(func.count(OrganizeMove.id)).where(OrganizeMove.run_id == run.id,
                                                               OrganizeMove.state == "moved"))
    run.state, run.finished_at = ("undone" if not left else "failed"), clock.now()
    if left:
        run.error = "undo_incomplete"
    db.commit()
    jobs.enqueue(db, "scan", ref=library.id)
    db.commit()


def organize_job(db: Session, job: Any) -> None:
    """The handler of the job ``organize``: the run ``job.ref``."""
    run = db.get(OrganizeRun, job.ref)
    if run is None:
        return
    if run.state in ("queued", "running"):
        execute(db, run)
    elif run.state == "undoing":
        undo(db, run)


jobs.register(KIND, organize_job)
