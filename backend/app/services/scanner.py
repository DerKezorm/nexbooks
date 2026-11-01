"""The scan: nexbooks reads a library's folder and learns what is in it.

**It only reads.** No file of a library is ever written, renamed, moved or deleted. Everything nexbooks learns is kept
in its own database; the folder stays as it is.

**In the background, and it survives a restart.** A scan is a job in the queue in the database (``services/jobs.py``,
kind ``scan``, the library as ``ref``): one at a time, one run per library, a second request while one waits is the
same request. A run stopped by a restart goes back in the queue and continues: what was read is kept (committed in
small steps), and files whose size and time are as they were are not read again.

**Only what changed.** A file is read when it is new or its size or time changed; the checksum (SHA-256, in pieces) is
made only then. A file changed in the last minute is left for the next run (a copy may still be in progress); the run
says so and asks to be run again soon.

**Never out of the folder.** Links (symbolic links, junctions) are not followed, in files or folders, and are listed as
problems so that the operator knows; devices, pipes and sockets are left alone; hidden files and the folders NASes
and systems make (``@eaDir``, ``#recycle``, ``$RECYCLE.BIN`` ...) are skipped. The files are opened below the
library's folder only (``readers/files.py``).

**A drive that is gone is no empty library.** If the folder is not there, cannot be listed, or holds no book file at
all while the database knows books in it (a share that was not mounted may hold a stray file of the mount point), the
run stops and says so (``aborted``): nothing is marked missing, so the library does not look deleted. A folder too big
for one walk (``MAX_ENTRIES``) is read as far as the walk went, but nothing is marked missing (``truncated``): what lay
beyond was not seen. Files that really are gone are *marked* missing, never deleted; what hangs on a work (progress,
history, wish list) stays.

**A broken file breaks nothing.** The files are opened by a helper process with a time limit (``services/probe.py``).
What cannot be read goes into the list of problems with a reason, is not tried again until it changes, and the run goes
on. So does what goes wrong inside nexbooks while one file or one unit is handled (reason ``failed``, the cause in the
log): only a fault of the infrastructure (the database, the folder) ends a run. A name that is no valid text (bytes
that are no UTF-8 on Linux, a lone surrogate on Windows) or holds a control character is listed too (reason
``bad_name``): that folder or file is left out, the rest is read.

**Phases.** (A) look at the folder and read new and changed files; (B) put the units that changed together
(``services/catalog.py``); (C) settle: editions without files go, editions without a file on the disk are marked, works
take over what they show; covers nothing refers to any more are removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import stat
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import clock
from ..db import SessionLocal
from ..models import ROLE_BOOK, ROLE_SIDECAR, BookFile, Edition, Job, Library, LibraryProblem, MatchEntry, Work
from . import catalog, covers, jobs, kosync, libraries, settings_service
from .match import run as matching
from .probe import ProbeFailed, Stopping, Worker
from .readers import AUDIO_FORMATS, format_of
from .readers.files import is_link, open_below
from .readers.limits import ReadError
from .readers.names import is_cd_folder
from .readers.sidecar import kind_of as sidecar_kind
from .readers.text import fold

logger = logging.getLogger("nexbooks.scanner")

#: A file changed less than this long ago is left for the next run.
QUIET_SECONDS = 60
#: A run that left files in the making is due again after this long.
RETRY_SECONDS = 90
#: Limits of the walk: entries seen in all, and how deep.
MAX_ENTRIES = 500_000
MAX_DEPTH = 24
#: Links and names that are no valid text, listed as problems, at most (a folder full of them must not fill the table).
MAX_LINK_PROBLEMS = 200
#: Folders and files never looked at.
SKIP_DIR_PREFIXES = (".", "@", "#", "$", "~")
SKIP_DIRS = frozenset({"lost+found", "system volume information", "__macosx", "recycler", "found.000"})
SKIP_FILE_PREFIXES = (".", "~$")
SKIP_FILE_SUFFIXES = (".part", ".tmp", ".temp", ".crdownload", ".partial", ".filepart", ".download", ".!qb", ".swp",
                      ".lock", ".bak")
SKIP_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
HASH_PIECE = 1024 * 1024
#: The generation of reading the files. Raised when what is read out of a file, or how a book is put together out of
#: what was read, changes in a way the books that are in should get (a rule for the year, the author of a PDF, the
#: genre): the next run reads every file again once (``BookFile.rev``, ``Library.rev``). The operator can ask for the
#: same with "read everything again" (``Library.reread_rev``).
#: 4: the names in a field of authors read whole (``Feldkamp, Mara (als Mara Lind)`` is Mara Feldkamp writing as Mara
#: Lind, not ``Feldkamp`` and ``Mara (als Mara Lind)``; ``Ben Hollister liest Mara Feldkamp``, a title beside its
#: author), so that an e-book and its recording are one work again and the people an older rule made go.
#: 5: the language of the text of an e-book, from a sample of it (``epub._text_language``), so that a book whose package
#: names a wrong language gets the one of its text.
#: 6: a copy protection that names itself nowhere (a Kobo rights file in the root, a licence, chapters that are no
#: markup) is recognised, so that such a book is marked as protected and not opened as noise.
READ_REV = 6
#: The generation of putting the books together out of what was read, without reading a file again: raised when how a
#: book is put together changes in a way the books that are in should get (advertising in a title, a word for a part
#: as a series, the top folder as the author, a comment as a description, the evidence of pen names, an edition that
#: is not the book of its work any more, the files of a recording that are one chapter, files whose titles name no
#: chapter, the name of a track as a series, a narrator beside the author in the artist, an episode of a play, a title
#: written for sorting, a title and an author in each other's fields, the number a scheme puts in front of a title, a
#: note about the reader or the form at the end of a title, a title that goes on from the name of its series, a
#: publisher's line or a translator behind a title, the number of a disc behind an author). The next look of the clock
#: marks every book file to be put together again and runs the scan of each library that is read by itself
#: (``reassemble_if_due``); one the operator keeps from being read by itself gets it at its next run.
ASSEMBLE_REV = 12
#: A library whose last run did not get to the newer rules (the folder was away) is tried again after this many seconds.
RULES_WAIT = 120
#: A file that is read again only because the rules are newer (nothing changed in it) is read at a pace: after it the
#: run rests for as long as the reading took (``REREAD_DUTY`` times), at most ``REREAD_PAUSE_MAX`` seconds, so that
#: reading a whole library again takes at most half of the time of the disk and the processor (a network share is not
#: kept busy for hours). A new or changed file is read at full speed.
REREAD_DUTY = 1.0
REREAD_PAUSE_MAX = 1.0
#: How the run rests and how it measures the time a file took; the tests put their own in.
rest: Any = jobs.stopping.wait
ticks: Any = time.monotonic
#: The libraries are read every so often (minutes) unless the operator says otherwise; 0 is never by itself.
DEFAULT_INTERVAL = 60
INTERVALS = (0, 15, 60, 360, 1440)


@dataclass
class Found:
    path: str
    size: int
    mtime_ns: int
    format: str
    role: str
    #: The file is known and has not changed: it is read again because it was read in an older generation of the rules.
    again: bool = False


@dataclass
class Walked:
    found: list[Found] = field(default_factory=list)
    #: Folders that could not be listed: what is below them is not known to be gone.
    unreadable: set[str] = field(default_factory=set)
    #: Links met on the way (not followed).
    links: list[str] = field(default_factory=list)
    #: Folders, and book files, whose name is no valid text (shown with ``?`` for what could not be shown); left out.
    bad_names: list[str] = field(default_factory=list)
    offline: bool = False
    #: The walk met more than ``MAX_ENTRIES`` entries and stopped: what was not met is not known to be gone.
    truncated: bool = False


def _skip_dir(name: str) -> bool:
    return name.startswith(SKIP_DIR_PREFIXES) or name.casefold() in SKIP_DIRS


def _skip_file(name: str) -> bool:
    lowered = name.casefold()
    return name.startswith(SKIP_FILE_PREFIXES) or lowered in SKIP_FILES or lowered.endswith(SKIP_FILE_SUFFIXES)


def _printable(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8")


def valid_name(name: str) -> bool:
    """Whether a folder or file name is text nexbooks can keep: it can be written as UTF-8 (a name made of bytes that
    are none, or with a lone surrogate, cannot) and holds no control character (keys and paths are joined with one)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(ord(char) < 32 for char in name)


def walk(root: Path, kind: str) -> Walked:
    """Looks at the folder of a library: the book files and the files beside them, with size and time. Does not follow
    links, does not leave the folder."""
    result = Walked()
    try:
        with os.scandir(root):
            pass
    except OSError:
        result.offline = True
        return result
    stack: list[tuple[str, str, int]] = [(str(root), "", 0)]
    seen = 0
    while stack:
        folder, relative, depth = stack.pop()
        try:
            with os.scandir(folder) as entries:
                listing = list(entries)
        except OSError:
            if relative == "":
                result.offline = True
                return result
            result.unreadable.add(relative)
            continue
        for entry in listing:
            seen += 1
            if seen > MAX_ENTRIES:
                result.truncated = True
                return result
            name = entry.name
            path = f"{relative}/{name}" if relative else name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if is_link(info):
                if not (_skip_file(name) if not stat.S_ISDIR(info.st_mode) else _skip_dir(name)):
                    result.links.append(_printable(path))
                continue
            if stat.S_ISDIR(info.st_mode):
                if depth + 1 <= MAX_DEPTH and not _skip_dir(name):
                    if valid_name(name):
                        stack.append((entry.path, path, depth + 1))
                    else:
                        result.bad_names.append(_printable(path))
                continue
            if not stat.S_ISREG(info.st_mode) or _skip_file(name):
                continue
            if not valid_name(name):
                # Only what nexbooks would have read is worth a line (a stray file with such a name harms nobody).
                if format_of(name) and libraries.kind_allows(
                        kind, "audio" if format_of(name) in AUDIO_FORMATS.values() else "ebook"):
                    result.bad_names.append(_printable(path))
                continue
            sidecar = sidecar_kind(name)
            if sidecar:
                result.found.append(Found(path, info.st_size, info.st_mtime_ns, sidecar, ROLE_SIDECAR))
                continue
            fmt = format_of(name)
            if not fmt:
                continue
            fmt_kind = "audio" if fmt in AUDIO_FORMATS.values() else "ebook"
            if libraries.kind_allows(kind, fmt_kind):
                result.found.append(Found(path, info.st_size, info.st_mtime_ns, fmt, ROLE_BOOK))
    return result


def sha256_of(root: str, relative: str) -> str:
    """The SHA-256 of a file, read in pieces; stops with the server (``jobs.Interrupted``)."""
    digest = hashlib.sha256()
    with open_below(root, relative) as handle:
        while True:
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            piece = handle.read(HASH_PIECE)
            if not piece:
                break
            digest.update(piece)
    return digest.hexdigest()


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _under(folder: str) -> ColumnElement[bool]:
    """A SQL condition on ``BookFile.path``: at or below ``folder``. A range, not ``LIKE``, so that a ``%`` or ``_`` in
    a
    name means itself. ('/' is followed by '0' in the character table.)"""
    return (BookFile.path >= folder + "/") & (BookFile.path < folder + "0")


#: Folders beside the parts of a recording that hold the picture of the book.
COVER_FOLDERS = frozenset({"cover", "covers", "artwork"})


def _sidecar_home(path: str) -> str:
    """The folder a file beside a book speaks for: its own, or the one above when it lies in a folder of parts
    (``Audio``, ``CD 1``) or of pictures (``Cover``): ``Titel/Cover/cover.jpg`` is for ``Titel``."""
    parts = [part for part in _dir_of(path).split("/") if part]
    while len(parts) >= 2 and (is_cd_folder(parts[-1]) or fold(parts[-1]) in COVER_FOLDERS):
        parts.pop()
    return "/".join(parts)


def _directly_in(folder: str) -> ColumnElement[bool]:
    if not folder:
        return func.instr(BookFile.path, "/") == 0
    return _under(folder) & (func.instr(func.substr(BookFile.path, len(folder) + 2), "/") == 0)


@dataclass
class Report:
    seen: int = 0
    added: int = 0
    changed: int = 0
    missing: int = 0
    problems: int = 0
    deferred: int = 0
    links: int = 0
    seconds: float = 0.0
    aborted: str | None = None
    #: Files that were not changed but read again for the rules of a newer generation, and books put together while the
    #: run was still reading.
    reread: int = 0
    early: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"seen": self.seen, "added": self.added, "changed": self.changed, "missing": self.missing,
                "problems": self.problems, "deferred": self.deferred, "links": self.links,
                "seconds": round(self.seconds, 1), "aborted": self.aborted, "reread": self.reread,
                "early": self.early}


def _upsert_problem(db: Session, library_id: int, path: str, reason: str, size: int, mtime_ns: int) -> None:
    row = db.scalar(select(LibraryProblem).where(LibraryProblem.library_id == library_id, LibraryProblem.path == path))
    if row is None:
        db.add(LibraryProblem(library_id=library_id, path=path[:1500], reason=reason, size=size, mtime_ns=mtime_ns,
                              seen_at=clock.now()))
    else:
        row.reason, row.size, row.mtime_ns, row.seen_at = reason, size, mtime_ns, clock.now()


def _problem_of_file(db: Session, library_id: int, item: Found, reason: str, report: Report) -> None:
    """A file that could not be handled: whatever was half written for it is undone, and it is listed."""
    db.rollback()
    _upsert_problem(db, library_id, item.path, reason, item.size, item.mtime_ns)
    report.problems += 1


def _problem_of_unit(db: Session, library_id: int, unit: str, report: Report) -> None:
    """A unit that could not be put together (a fault in nexbooks, not in a file): listed under its first file, which
    stays marked as to do, so that the next run tries again."""
    db.rollback()
    first = db.execute(select(BookFile.path, BookFile.size, BookFile.mtime_ns).where(
        BookFile.library_id == library_id, BookFile.unit == unit, BookFile.role == ROLE_BOOK)
        .order_by(BookFile.path).limit(1)).first()
    if first is not None:
        _upsert_problem(db, library_id, first[0], "failed", first[1], first[2])
    report.problems += 1


def _store_probe(probe: dict[str, Any]) -> dict[str, Any]:
    """The probe as the database keeps it: the cover (bytes) becomes a file in the data folder and its name."""
    cover = probe.pop("cover", None)
    if isinstance(cover, list | tuple) and len(cover) == 2 and isinstance(cover[0], bytes):
        probe["cover_file"] = covers.save(cover[0])
        probe["color"] = str(cover[1])[:7]
    return probe


def _reset_progress(library: Library) -> None:
    library.progress_done = library.progress_total = 0


#: The problems the walk itself finds each time (not the reading of a file): listed anew every run, taken off the list
#: when they are gone.
WALK_REASONS = ("link", "bad_name")


def _sync_listed(db: Session, library_id: int, reason: str, paths: list[str]) -> None:
    """What the walk met (links, names that are no valid text) is listed as problems with ``reason``; what is gone from
    the folder is taken off the list. Two paths that look the same once printed are one line."""
    wanted = list(dict.fromkeys(paths))[:MAX_LINK_PROBLEMS]
    other = set(db.scalars(select(LibraryProblem.path).where(LibraryProblem.library_id == library_id,
                                                             LibraryProblem.reason != reason)))
    existing = set(db.scalars(select(LibraryProblem.path).where(LibraryProblem.library_id == library_id,
                                                                LibraryProblem.reason == reason)))
    for path in wanted:
        if path not in existing and path not in other:
            _upsert_problem(db, library_id, path, reason, 0, 0)
    stale = existing - set(wanted)
    if stale:
        db.execute(delete(LibraryProblem).where(LibraryProblem.library_id == library_id,
                                                LibraryProblem.reason == reason, LibraryProblem.path.in_(stale)))


def _group_condition(group: str) -> ColumnElement[bool]:
    """A SQL condition on ``BookFile.unit``: the units of a group of files (``catalog.unit_group``). An e-book's group
    is its unit; a recording's group is the folder of its parts, and the units in it are told by the album after it."""
    if group.startswith("e"):
        return BookFile.unit == group
    return (BookFile.unit >= group + catalog.UNIT_JOINT) & (BookFile.unit < group + chr(ord(catalog.UNIT_JOINT) + 1))


class Early:
    """What it takes to put a unit together *while the run is still reading*: the files of a unit are read one after the
    other, and a unit whose files are all read is put together at once (it shows on the shelf, and the matching with the
    sources can begin) instead of at the end.

    **Only what is certain is done early.** A unit waits for the end of the run when a file beside it
    (``metadata.json``) lies in its folder while books below that folder are not all read: such a file speaks for the
    unit only when it is the only one below (``_sidecars_for``), which is known when every file below is read. What
    waits is done at the end, as it always was."""

    def __init__(self, to_read: list[Found]) -> None:
        #: Files still to read, by group and by every folder above them.
        self.groups: dict[str, int] = {}
        self.under: dict[str, int] = {}
        for item in to_read:
            if item.role != ROLE_BOOK:
                continue
            group = catalog.unit_group(item.path, item.format)
            self.groups[group] = self.groups.get(group, 0) + 1
            for folder in _folders_above(item.path):
                self.under[folder] = self.under.get(folder, 0) + 1

    def done(self, item: Found) -> str | None:
        """A book file is done (read, failed or put off): the group when this was its last file, else None."""
        if item.role != ROLE_BOOK:
            return None
        group = catalog.unit_group(item.path, item.format)
        for folder in _folders_above(item.path):
            self.under[folder] = self.under.get(folder, 0) - 1
        self.groups[group] = self.groups.get(group, 0) - 1
        return group if self.groups[group] <= 0 else None

    def folder_open(self, folder: str) -> bool:
        """Whether a file below ``folder`` is not read yet."""
        return self.under.get(folder, 0) > 0


def _folders_above(path: str) -> list[str]:
    """The folders a path lies in, from its own up to the library's (``""``)."""
    folders = []
    folder = _dir_of(path)
    while folder:
        folders.append(folder)
        folder = _dir_of(folder)
    folders.append("")
    return folders


#: A run asks the matching to begin (``matching.enqueue_pending``) every so many books put together early, so that the
#: sources are asked for the first books while the run reads the last.
MATCH_EVERY = 15


class LibraryGone(Exception):
    """The library was taken out of nexbooks during the run: nothing is left to write to, and nothing went wrong."""


def _gone(db: Session, library_id: int) -> bool:
    """Whether the library is gone, after a failed write: what was half written is dropped first."""
    db.rollback()
    return db.scalar(select(Library.id).where(Library.id == library_id)) is None


def run(db: Session, library: Library, report: Report) -> None:
    """One run over one library. ``Report`` is filled as it goes; ``jobs.Interrupted`` when the server stops."""
    started = time.monotonic()
    now = clock.now()
    # Whether the top folder names an author is asked anew in every run (the books below it may have changed).
    catalog.forget_top_authors(library.id)
    #: Kept apart: after a failed write the objects of the session are not to be touched until it is rolled back.
    library_id = library.id
    #: The generation of reading the files must have been read in; a library that has not reached it reads every file
    #: again.
    need = READ_REV + int(library.reread_rev or 0)
    again_all = int(library.rev or 0) < need
    try:
        real = libraries.check_path(library.path)
    except libraries.PathProblem:
        report.aborted = "offline"
        return
    if str(real) != library.path:
        # The folder is somewhere else now (a link was repointed): not the library that was made.
        report.aborted = "offline"
        return
    walked = walk(real, library.kind)
    if walked.offline:
        report.aborted = "offline"
        return
    report.seen = sum(1 for item in walked.found if item.role == ROLE_BOOK)
    report.links = len(walked.links)
    known: dict[str, tuple[int, int, int, bool, str, int]] = {
        path: (ident, size, mtime, missing is not None, role, rev)
        for path, ident, size, mtime, missing, role, rev in db.execute(
            select(BookFile.path, BookFile.id, BookFile.size, BookFile.mtime_ns, BookFile.missing_at, BookFile.role,
                   BookFile.rev)
            .where(BookFile.library_id == library.id))
    }
    # No book file in the folder at all, while books are known in it: an empty mount point (a stray file there does not
    # change that), not a library that was emptied. Nothing is marked missing; the operator is told.
    if not any(item.role == ROLE_BOOK for item in walked.found) and any(
            not gone and role == ROLE_BOOK for _i, _s, _m, gone, role, _r in known.values()):
        report.aborted = "empty"
        return
    problems: dict[str, tuple[int, int, str]] = {
        path: (size, mtime, reason) for path, size, mtime, reason in db.execute(
            select(LibraryProblem.path, LibraryProblem.size, LibraryProblem.mtime_ns, LibraryProblem.reason)
            .where(LibraryProblem.library_id == library.id))
    }
    now_ns = time.time_ns()
    quiet_ns = QUIET_SECONDS * 1_000_000_000
    to_read: list[Found] = []
    on_disk: set[str] = set()
    returned: list[int] = []
    lost = _lost_covers(db, library.id)
    # The files a move into another library is busy with (``services/relocate.py``): the goals it is placing belong to
    # the edition in the other library until the switch, and what it still has to take away at the source belongs to the
    # target after it. Neither is a new book here.
    from .relocate import held_paths

    held = held_paths(db, library.id)
    for item in walked.found:
        on_disk.add(item.path)
        if item.path in held and item.path not in known:
            continue
        row = known.get(item.path)
        unchanged = row is not None and row[1] == item.size and row[2] == item.mtime_ns
        if unchanged and row is not None and row[5] >= need and item.path not in lost:
            if row[3]:
                returned.append(row[0])
            continue
        problem = problems.get(item.path)
        # A file that could not be read is not tried again until it changes; a run that reads everything again tries it
        # again too (the rules that failed on it may be others now).
        if problem is not None and (problem[0], problem[1]) == (item.size, item.mtime_ns) and not again_all:
            continue
        if 0 <= now_ns - item.mtime_ns < quiet_ns:
            report.deferred += 1
            continue
        # Known, not changed, read in an older generation: read again, and it is not a change.
        item.again = unchanged and row is not None and row[5] < need
        to_read.append(item)
    unreadable = tuple(walked.unreadable)

    def under_unreadable(path: str) -> bool:
        return any(path.startswith(folder + "/") for folder in unreadable)

    # A walk that stopped at the limit did not see everything: what it did not see is not known to be gone.
    vanished = [] if walked.truncated else [
        (path, row) for path, row in known.items()
        if path not in on_disk and not row[3] and not under_unreadable(path)]
    gone_problems = [] if walked.truncated else [
        path for path, (_s, _m, reason) in problems.items()
        if reason not in WALK_REASONS and path not in on_disk and not under_unreadable(path)]
    _sync_listed(db, library.id, "link", walked.links)
    _sync_listed(db, library.id, "bad_name", walked.bad_names)
    report.problems += len(walked.bad_names)
    sidecars = sorted((f for f in to_read if f.role == ROLE_SIDECAR), key=lambda f: f.path)
    books = sorted((f for f in to_read if f.role == ROLE_BOOK), key=lambda f: f.path)
    library.progress_total = len(to_read)
    library.progress_done = 0
    db.commit()

    state = _Pass(db=db, library=library, library_id=library_id, real=real, root=str(real), known=known, now=now,
                  report=report, need=need, early=Early(to_read))

    # (A) read the files. First the files beside the books (small, and they speak for the books below them), then what
    # is gone and what is back, then the books themselves: a unit is put together as soon as its files are all read.
    with Worker(stop=jobs.stopping.is_set) as worker:
        state.worker = worker
        for item in sidecars:
            state.read(item)
        _gone_and_back(db, library, report, vanished, returned, gone_problems, state.changed_sidecar_dirs)
        for item in books:
            state.read(item)

    # (B) put the units together that were not put together on the way
    units = list(db.scalars(select(BookFile.unit).where(
        BookFile.library_id == library.id, BookFile.dirty.is_(True), BookFile.role == ROLE_BOOK)
        .group_by(BookFile.unit).order_by(func.min(BookFile.path))))
    units = [unit for unit in units if unit not in state.failed]
    base = state.done
    library.progress_total = base + len(units)
    db.commit()
    for index, unit in enumerate(units, start=1):
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        _assign_safely(db, library, unit, report)
        library.progress_done = base + index
        db.commit()

    # (C) settle
    result = catalog.settle_library(db, library.id, since=now)
    library.rev = need
    db.commit()
    sweep_covers(db)
    if walked.truncated:
        report.aborted = "truncated"
    report.seconds = time.monotonic() - started
    logger.info("Scan done library=%s seen=%s added=%s changed=%s reread=%s missing=%s problems=%s deferred=%s "
                "early=%s works_removed=%s", library.id, report.seen, report.added, report.changed, report.reread,
                report.missing, report.problems, report.deferred, report.early, result.get("works_removed", 0))


def _gone_and_back(db: Session, library: Library, report: Report, vanished: list[Any], returned: list[int],
                   gone_problems: list[str], changed_sidecar_dirs: set[str]) -> None:
    """Files that are gone are marked (a file beside a book is deleted), files that are back are marked back,
    problems of
    files that are gone are taken off the list, and the books beside a changed file are to be put together anew."""
    now = clock.now()
    for path, (ident, _size, _mtime, _gone, role, _rev) in vanished:
        if role == ROLE_SIDECAR:
            db.execute(delete(BookFile).where(BookFile.id == ident))
            changed_sidecar_dirs.add(_sidecar_home(path))
        else:
            row = db.get(BookFile, ident)
            if row is not None:
                row.missing_at = now
                row.dirty = True
        report.missing += 1
    if returned:
        db.execute(update(BookFile).where(BookFile.id.in_(returned)).values(missing_at=None, dirty=True))
    if gone_problems:
        db.execute(delete(LibraryProblem).where(LibraryProblem.library_id == library.id,
                                                LibraryProblem.path.in_(gone_problems)))
    for folder in changed_sidecar_dirs:
        # What a file beside the books says is read again for the books it speaks for.
        where = _directly_in("") if folder == "" else _under(folder)
        db.execute(update(BookFile).where(BookFile.library_id == library.id, BookFile.role == ROLE_BOOK,
                                          BookFile.missing_at.is_(None), where).values(dirty=True))
    db.commit()


@dataclass
class _Pass:
    """The state of the reading of one run (what `Early` and the loop over the files share)."""

    db: Session
    library: Library
    library_id: int
    real: Path
    root: str
    known: dict[str, tuple[int, int, int, bool, str, int]]
    now: Any
    report: Report
    need: int
    early: Early
    worker: Any = None
    done: int = 0
    changed_sidecar_dirs: set[str] = field(default_factory=set)
    #: Books put together early since the matching was last asked to begin.
    since_match: int = 0
    #: Units that could not be put together on the way: listed as problems once, not tried again by this run.
    failed: set[str] = field(default_factory=set)

    def read(self, item: Found) -> None:
        """Reads one file and keeps what it says; a book whose unit is then complete is put together at once."""
        db, report = self.db, self.report
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        began = ticks()
        try:
            probe = self.worker.read(self.root, item.path, item.format)
            digest = partial = ""
            if item.role == ROLE_BOOK:
                existing = db.get(BookFile, self.known[item.path][0]) if item.again else None
                # (A file read again that did not change keeps the checksums it has.)
                kept = existing.sha256 if existing is not None else ""
                digest = kept or sha256_of(self.root, item.path)
                partial = existing.partial_md5 if existing is not None and kept else ""
                if kosync.wanted(item.format, item.role) and not partial:
                    partial = kosync.partial_md5_of(self.root, item.path)
                after = os.lstat(self.real / item.path)
                if (after.st_size, after.st_mtime_ns) != (item.size, item.mtime_ns):
                    # Changed while it was read: the next run reads it again.
                    report.deferred += 1
                    self._finished(item)
                    return
            _write_file(db, self.library_id, item, _store_probe(probe), digest, partial, self.now,
                        self.known.get(item.path), report, self.need)
            db.execute(delete(LibraryProblem).where(LibraryProblem.library_id == self.library_id,
                                                    LibraryProblem.path == item.path))
            # Written now, not at the commit below: what the database refuses is this file's problem.
            db.flush()
            if item.role == ROLE_SIDECAR:
                self.changed_sidecar_dirs.add(_sidecar_home(item.path))
        except (Stopping, jobs.Interrupted):
            # What was read so far is kept (every file is committed as it is done): the next run goes on from here.
            raise jobs.Interrupted from None
        except (ProbeFailed, ReadError) as exc:
            _problem_of_file(db, self.library_id, item, exc.code, report)
        except OperationalError:
            # The database itself (locked for good, the disk full): not the file's fault, the run ends.
            raise
        except OSError:
            _problem_of_file(db, self.library_id, item, "unreadable", report)
        except Exception:
            if _gone(db, self.library_id):
                raise LibraryGone from None
            logger.exception("Handling a file failed library=%s", self.library_id)
            _problem_of_file(db, self.library_id, item, "failed", report)
        self._finished(item)
        if item.again:
            # Reading again for the rules is not urgent: the books are shown as they are meanwhile.
            took = ticks() - began
            if took > 0:
                rest(min(REREAD_PAUSE_MAX, took * REREAD_DUTY))

    def _finished(self, item: Found) -> None:
        self.done += 1
        self.library.progress_done = self.done
        # Every file is committed: the next one may take long (a file that hangs is let to for the time limit), and a
        # transaction held open that long would keep every other writer of the database waiting.
        self.db.commit()
        group = self.early.done(item)
        if group is not None:
            self._put_together(group)

    def _put_together(self, group: str) -> None:
        """Puts the units of a group together whose files are all read, as far as that is certain (see ``Early``)."""
        db = self.db
        units = list(db.scalars(select(BookFile.unit).where(
            BookFile.library_id == self.library_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
            BookFile.dirty.is_(True), _group_condition(group)).group_by(BookFile.unit)))
        for unit in units:
            folder = catalog.unit_folder(unit)
            if self.early.folder_open(folder) and _has_sidecar(db, self.library_id, folder):
                continue
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            if _assign_safely(db, self.library, unit, self.report):
                self.report.early += 1
                self.since_match += 1
            else:
                self.failed.add(unit)
            db.commit()
        if self.since_match >= MATCH_EVERY:
            self.since_match = 0
            # The books that are in are matched with the sources while the run goes on reading (a worker of its own).
            matching.enqueue_pending(db)


def _sidecars_at(db: Session, library_id: int, folder: str) -> list[BookFile]:
    """The files beside the books that speak for the books of ``folder``: those in it, and those in a folder of parts
    or of pictures below it (``Titel/Audio/cover.jpg``, ``Titel/Cover/cover.jpg``)."""
    if folder == "":
        return list(db.scalars(select(BookFile).where(
            BookFile.library_id == library_id, BookFile.role == ROLE_SIDECAR, BookFile.missing_at.is_(None),
            _directly_in(folder))))
    found = db.scalars(select(BookFile).where(
        BookFile.library_id == library_id, BookFile.role == ROLE_SIDECAR, BookFile.missing_at.is_(None),
        _under(folder)).limit(2000))
    return [row for row in found if _sidecar_home(row.path) == folder]


def _has_sidecar(db: Session, library_id: int, folder: str) -> bool:
    return bool(_sidecars_at(db, library_id, folder))


def _assign_safely(db: Session, library: Library, unit: str, report: Report) -> bool:
    """Puts one unit together; what goes wrong with it is listed and does not stop the run. Returns whether it
    worked."""
    library_id = library.id
    try:
        _assign_unit(db, library, unit)
        db.flush()
        return True
    except OperationalError:
        raise
    except Exception:
        logger.exception("Putting a unit together failed library=%s", library_id)
        _problem_of_unit(db, library_id, unit, report)
        return False


def _lost_covers(db: Session, library_id: int) -> set[str]:
    """The files whose cover is gone from the data folder (a restore does not bring the covers back, they are not in the
    backup): they are read again, which makes the cover again."""
    by_name: dict[str, list[str]] = {}
    for path, name in db.execute(select(BookFile.path, func.json_extract(BookFile.probe, "$.cover_file")).where(
            BookFile.library_id == library_id, BookFile.probe.is_not(None), BookFile.missing_at.is_(None))):
        if name:
            by_name.setdefault(name, []).append(path)
    return {path for name, paths in by_name.items() if covers.path_of(name) is None for path in paths}


def sweep_covers(db: Session) -> None:
    """Removes the cover files nothing refers to: editions, works, and the probes kept in the file rows."""
    live = set(db.scalars(select(Edition.cover).where(Edition.cover != "")))
    live |= set(db.scalars(select(Work.cover).where(Work.cover != "")))
    for (name,) in db.execute(select(func.json_extract(BookFile.probe, "$.cover_file")).where(
            BookFile.probe.is_not(None))):
        if name:
            live.add(name)
    # The covers of the proposals a person is looking at (fetched from a source when the list was opened).
    for (proposals,) in db.execute(select(MatchEntry.proposals).where(MatchEntry.proposals.is_not(None))):
        for item in proposals if isinstance(proposals, list) else []:
            if isinstance(item, dict) and item.get("cover"):
                live.add(str(item["cover"]))
    covers.sweep(live)


def _write_file(db: Session, library_id: int, item: Found, probe: dict[str, Any], digest: str, partial: str, now: Any,
                row: tuple[int, int, int, bool, str, int] | None, report: Report, rev: int) -> None:
    existing = db.get(BookFile, row[0]) if row else None
    duration = round(float(probe.get("duration") or 0) * 1000) if item.role == ROLE_BOOK else 0
    unit = catalog.unit_key(item.path, item.format, probe) if item.role == ROLE_BOOK else ""
    if existing is None:
        db.add(BookFile(library_id=library_id, path=item.path, size=item.size, mtime_ns=item.mtime_ns, sha256=digest,
                        partial_md5=partial, format=item.format, role=item.role, duration_ms=duration, unit=unit,
                        probe=probe, dirty=item.role == ROLE_BOOK, seen_at=now, created_at=now, rev=rev))
        report.added += item.role == ROLE_BOOK
        return
    existing.size, existing.mtime_ns, existing.sha256, existing.format = item.size, item.mtime_ns, digest, item.format
    existing.partial_md5 = partial
    existing.duration_ms, existing.unit, existing.probe, existing.seen_at = duration, unit, probe, now
    existing.missing_at = None
    existing.dirty = item.role == ROLE_BOOK
    existing.rev = rev
    if item.role == ROLE_BOOK:
        if item.again:
            report.reread += 1
        else:
            report.changed += 1


def _assign_unit(db: Session, library: Library, unit: str) -> Edition | None:
    # Another worker (the matching runs beside the scan) may have changed books since this session saw them.
    db.expire_all()
    rows = list(db.scalars(select(BookFile).where(BookFile.library_id == library.id, BookFile.unit == unit,
                                                  BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))))
    if not rows:
        db.execute(update(BookFile).where(BookFile.library_id == library.id, BookFile.unit == unit)
                   .values(dirty=False))
        return None
    edition = catalog.assign(db, library, rows, _sidecars_for(db, library.id, unit))
    # A unit that could not be put together earlier (reason ``failed``) has been now.
    db.execute(delete(LibraryProblem).where(LibraryProblem.library_id == library.id, LibraryProblem.reason == "failed",
                                            LibraryProblem.path.in_([row.path for row in rows])))
    if edition is None:
        db.execute(update(BookFile).where(BookFile.library_id == library.id, BookFile.unit == unit).values(dirty=False))
    return edition


def _sidecars_for(db: Session, library_id: int, unit: str) -> list[BookFile]:
    """The files beside a unit that tell about it: those in its folder, and only while the unit is the only one of its
    kind (a recording or an e-book) at or below that folder (a ``metadata.json`` in a folder of many books would
    belong to none of them)."""
    folder = catalog.unit_folder(unit)
    around = _sidecars_at(db, library_id, folder)
    if not around:
        return []
    below = _directly_in("") if folder == "" else _under(folder)
    kind = unit[:1]
    same_kind = (BookFile.unit >= kind + catalog.UNIT_JOINT) & (BookFile.unit < kind + chr(ord(catalog.UNIT_JOINT) + 1))
    units = db.scalar(select(func.count(BookFile.unit.distinct())).where(
        BookFile.library_id == library_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None), below,
        same_kind))
    return around if units == 1 else []


# ---- The job, and the schedule ------------------------------------------------------------------------------


def scan_job(db: Session, job: Any) -> None:
    """The handler of the job ``scan``: one run over the library ``job.ref``."""
    library = db.get(Library, job.ref)
    if library is None:
        raise jobs.GiveUp("library gone")
    report = Report()
    try:
        run(db, library, report)
    except jobs.Interrupted:
        db.rollback()
        library = db.get(Library, job.ref)
        if library is not None:
            _reset_progress(library)
            db.commit()
        raise
    except Exception:
        db.rollback()
        library = db.get(Library, job.ref)
        if library is None:
            # (Whatever the failure said: a write to a library that is gone.)
            logger.info("Scan ended: the library was removed id=%s", job.ref)
            return
        _reset_progress(library)
        library.report = {**Report().as_dict(), "aborted": "error"}
        library.scanned_at = clock.now()
        db.commit()
        raise
    library = db.get(Library, job.ref)
    if library is None:
        return
    library.report = report.as_dict()
    library.scanned_at = clock.now()
    # Files that were still being written ask for another run soon.
    again = report.deferred and not report.aborted
    library.retry_at = clock.now() + timedelta(seconds=RETRY_SECONDS) if again else None
    _reset_progress(library)
    db.commit()
    if report.aborted:
        logger.warning("Scan stopped library=%s reason=%s", library.id, report.aborted)
    # What is new or changed is matched with the sources next (no-op while none is ready).
    matching.enqueue_pending(db)
    # The photos that lie in the authors' folders are read next (``services/portraits.py``), unless the run broke off.
    if not report.aborted:
        jobs.enqueue(db, "portraits", ref=library.id)
        db.commit()
        # A book read in for the first time may be what a wish looks for: that wish is done.
        from .procure import wishes as wish_service

        wish_service.met_elsewhere(db)
        # Books taken out of the trash that were sent away as the wrong book have their readers' places back.
        from .procure import kept

        kept.reattach(db, library.id)


jobs.register("scan", scan_job)


def interval_minutes(db: Session) -> int:
    value = settings_service.get(db, "scan_minutes")
    return int(value) if isinstance(value, int) and value in INTERVALS else DEFAULT_INTERVAL


def enqueue_due(db: Session) -> int:
    """Puts the libraries that are due in the queue: those not read for the interval of the instance (0: never by
    itself),
    and those that asked to come again soon. Returns how many were queued."""
    now = clock.now()
    minutes = interval_minutes(db)
    queued = 0
    for library in db.scalars(select(Library)):
        retry = library.retry_at is not None and library.retry_at <= now
        regular = library.scan_enabled and minutes > 0 and (
            library.scanned_at is None or (now - library.scanned_at).total_seconds() >= minutes * 60)
        # Read by older rules (after an update, or asked for by hand): due at once, not at the next turn of the interval
        # (which is an hour away, or never). A library the operator keeps from being read by itself is left alone.
        newer_rules = library.scan_enabled and minutes > 0 and library.scanned_at is not None and (
            now - library.scanned_at).total_seconds() >= RULES_WAIT and int(
            library.rev or 0) < READ_REV + int(library.reread_rev or 0)
        if not (retry or regular or newer_rules):
            continue
        if db.scalar(select(Job.id).where(Job.kind == "scan", Job.ref == library.id,
                                          Job.state.in_(("queued", "running")))):
            continue
        jobs.enqueue(db, "scan", ref=library.id)
        library.retry_at = None
        queued += 1
    db.commit()
    return queued


async def run_forever(stop: asyncio.Event) -> None:
    """Looks every half minute whether a library is due."""
    await asyncio.sleep(3)
    while not stop.is_set():
        try:
            await asyncio.to_thread(_enqueue_due_alone)
        except Exception:
            logger.exception("Scheduling the scans failed")
        try:
            await asyncio.wait_for(stop.wait(), 30)
        except TimeoutError:
            continue


def reassemble_if_due(db: Session) -> bool:
    """Every book is put together again from what was read, once, when the rules of that are newer
    (``ASSEMBLE_REV``). Returns whether it was asked for."""
    if int(settings_service.get(db, "assemble_rev") or 0) >= ASSEMBLE_REV:
        return False
    db.execute(update(BookFile).where(BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None)).values(dirty=True))
    for library in db.scalars(select(Library).where(Library.scan_enabled.is_(True))):
        if not db.scalar(select(Job.id).where(Job.kind == "scan", Job.ref == library.id,
                                              Job.state.in_(("queued", "running")))):
            jobs.enqueue(db, "scan", ref=library.id)
    settings_service.save(db, {"assemble_rev": ASSEMBLE_REV})
    db.commit()
    logger.info("Books are put together again by the rules of version %s", ASSEMBLE_REV)
    return True


def _enqueue_due_alone() -> int:
    with SessionLocal() as db:
        # Newer rules of putting books together come first: the matching that follows asks about the books as they
        # are then.
        reassemble_if_due(db)
        queued = enqueue_due(db)
        # The clock also picks up what waits for the sources (a source that was away, one that was switched on), and
        # begins the matching again by newer rules once, when there are any.
        matching.upgrade_rules(db)
        return queued + matching.enqueue_pending(db)
