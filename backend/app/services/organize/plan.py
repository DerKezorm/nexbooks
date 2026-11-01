"""The plan of a tidying: for every file of a library where the scheme puts it, and what stands in the way.

**Nothing is touched here**: the plan is made from the database and from looking at the disk (does the goal
exist, is a folder on the way a link), and the preview shows it. The run does exactly what the plan says
(``run.py``) and refuses what is no longer as the plan found it.

**A book moves whole.** The unit of tidying is the edition: every file of the e-book (its formats), every part
of the recording, and the files beside it that belong to it (``cover.jpg``, ``metadata.json``, also in a folder of
parts or pictures below the book's folder such as ``Audio`` or ``Cover``: only when the book is the only one of its kind
at or below that folder, the rule the scan uses to read them, ``scanner._sidecars_for``) go to the same place or none
of them does. A file that has a problem blocks its whole book (``book_blocked`` for the others). A file beside two
books (an e-book and a recording in one folder) cannot follow both: both stay (``shares_beside``).

**A recording stays one recording.** The scan puts the parts of a folder together (``catalog.unit_key``: the folder
without the folders of parts, and the album). So a recording is never sent into a folder that already holds another
book (``folder_taken``), two recordings never to one folder (``collision``), and a recording's folder is never named
like a folder of parts or pictures (``part_folder``: ``Audio``, ``CD 2``, ``Cover``), which the scan would read as
part of the folder above.

**Never overwritten, never merged.** A goal that exists on the disk (a file, or a folder where a file should go)
is a problem (``exists``), whatever it is; two books that the scheme sends to the same name are both a problem
(``collision``). Chains (A to where B lies, B elsewhere) are problems too: the plan keeps to one step, run it
again to take the next. A path longer than ``scheme.MAX_PATH`` (``too_long``), a goal behind a link (``link``)
or without a title (``no_title``) is a problem.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import stat
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    AUTHOR,
    EDITION_AUDIO,
    NARRATOR,
    ROLE_BOOK,
    ROLE_SIDECAR,
    BookFile,
    Credit,
    Edition,
    Library,
    Person,
    Series,
    Work,
)
from .. import catalog
from ..readers.files import is_link
from ..readers.names import is_cd_folder
from ..readers.text import fold
from ..scanner import COVER_FOLDERS, _sidecar_home
from . import scheme as schemes


@dataclass
class Move:
    file_id: int
    edition_id: int
    source: str
    target: str
    size: int
    mtime_ns: int
    #: ``planned``, ``problem`` (``reason`` says which).
    state: str = "planned"
    reason: str = ""


@dataclass
class Plan:
    moves: list[Move] = field(default_factory=list)
    #: Books (editions) that move, stay where they are, or have a problem; files in all.
    move_books: int = 0
    stay_books: int = 0
    problem_books: int = 0
    files: int = 0
    problems: dict[str, int] = field(default_factory=dict)
    #: How many books lacked a value for a name of the scheme (the part was left out).
    empty: dict[str, int] = field(default_factory=dict)
    digest: str = ""

    def summary(self) -> dict[str, object]:
        return {"move_books": self.move_books, "stay_books": self.stay_books, "problem_books": self.problem_books,
                "files": self.files, "move_files": sum(1 for m in self.moves if m.state == "planned"),
                "problems": self.problems, "empty": self.empty, "digest": self.digest}


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _fold(path: str) -> str:
    """A path as two file systems that fold case and normalise text would
    read it: equal for names that could be one file on some system."""
    return unicodedata.normalize("NFC", path).casefold()


def _chunks(items: list[int], size: int = 500) -> list[list[int]]:
    return [items[start:start + size] for start in range(0, len(items), size)]


class Disk:
    """What the disk under a library says about a goal, asked once for each
    name (a plan asks thousands of times about the same folders)."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._kinds: dict[str, str] = {}

    def kind(self, relative: str) -> str:
        """``none`` (not there), ``dir``, ``file``, ``link`` or ``other`` for ``relative`` below the root."""
        if relative not in self._kinds:
            try:
                info = os.lstat(os.path.join(self.root, *relative.split("/"))) if relative else os.lstat(self.root)
            except FileNotFoundError:
                kind = "none"
            except OSError:
                kind = "other"
            else:
                kind = "link" if is_link(info) else "dir" if stat.S_ISDIR(info.st_mode) else \
                    "file" if stat.S_ISREG(info.st_mode) else "other"
            self._kinds[relative] = kind
        return self._kinds[relative]

    def blocked(self, target: str) -> str:
        """Why a goal cannot be used: ``exists`` (something is there,
        or a file where a folder must be), ``link`` (a link
        on the way) - or an empty string."""
        parts = target.split("/")
        for depth in range(1, len(parts)):
            kind = self.kind("/".join(parts[:depth]))
            if kind == "none":
                break
            if kind == "link":
                return "link"
            if kind != "dir":
                return "exists"
        return "" if self.kind(target) == "none" else "exists"


@dataclass
class Layout:
    """How the files of a library lie, as the scan reads them: the book files of each edition, the files beside a book
    that belong to it alone (with their place seen from the folder they speak for), the editions that share a file
    beside them with another, and the editions with book files at or below each folder (folded)."""

    book_rows: dict[int, list[BookFile]]
    beside: dict[int, list[tuple[BookFile, str]]]
    shared: set[int]
    folder_editions: dict[str, set[int]]


def layout_of(db: Session, library: Library) -> Layout:
    """The ``Layout`` of a library, from the database (the rule of ``scanner._sidecars_for``)."""
    rows = list(db.scalars(select(BookFile).where(BookFile.library_id == library.id, BookFile.missing_at.is_(None))
                           .order_by(BookFile.id)))
    book_rows: dict[int, list[BookFile]] = defaultdict(list)
    sidecars: list[BookFile] = []
    #: The units of each kind (``a`` or ``e``, the first letter of a unit key) at or below a folder.
    folder_units: dict[tuple[str, str], set[str]] = defaultdict(set)
    #: The editions with book files at or below a folder (what a recording's new folder must not hold already).
    folder_editions: dict[str, set[int]] = defaultdict(set)
    unit_edition: dict[str, int] = {}
    for row in rows:
        if row.role == ROLE_SIDECAR:
            sidecars.append(row)
            continue
        if row.role != ROLE_BOOK or row.edition_id is None:
            continue
        book_rows[row.edition_id].append(row)
        unit_edition.setdefault(row.unit, row.edition_id)
        folder = _dir_of(row.path)
        while True:
            folder_units[(folder, row.unit[:1])].add(row.unit)
            folder_editions[_fold(folder)].add(row.edition_id)
            if not folder:
                break
            folder = _dir_of(folder)
    units_in: dict[str, list[str]] = defaultdict(list)
    for unit in unit_edition:
        if unit:
            units_in[catalog.unit_folder(unit)].append(unit)
    #: The files beside a book, and where they lie seen from the folder they speak for (``Cover/cover.jpg``).
    beside: dict[int, list[tuple[BookFile, str]]] = defaultdict(list)
    shared: set[int] = set()
    for side in sidecars:
        # The scan's rule: the folder the file speaks for, and every unit there that is the only one of its kind below.
        home = _sidecar_home(side.path)
        owners = {unit_edition[unit] for unit in units_in.get(home, [])
                  if folder_units.get((home, unit[:1])) == {unit}}
        relative = side.path[len(home) + 1:] if home else side.path
        if len(owners) == 1:
            beside[next(iter(owners))].append((side, relative))
        elif len(owners) > 1:
            shared.update(owners)
    return Layout(book_rows, beside, shared, folder_editions)


def build(db: Session, library: Library, scheme_text: str) -> Plan:
    """The plan for the library (``SchemeError`` for a scheme that cannot be used)."""
    scheme = schemes.parse(scheme_text)
    plan = Plan()
    editions = list(db.scalars(select(Edition).where(Edition.library_id == library.id, Edition.missing_at.is_(None))
                               .order_by(Edition.id)))
    if not editions:
        plan.digest = _digest(plan, scheme.text)
        return plan
    ids = [edition.id for edition in editions]
    people: dict[int, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for chunk in _chunks(ids):
        for edition_id, role, name, sort_name in db.execute(
                select(Credit.edition_id, Credit.role, Person.name, Person.sort_name).join(Person)
                .where(Credit.edition_id.in_(chunk), Credit.role.in_((AUTHOR, NARRATOR)))
                .order_by(Credit.edition_id, Credit.position)):
            people[edition_id or 0][role].append((name, sort_name))
    work_years: dict[int, int | None] = {}
    for chunk in _chunks(list({edition.work_id for edition in editions})):
        work_years.update(dict(db.execute(select(Work.id, Work.year).where(Work.id.in_(chunk))).all()))
    series_names = dict(db.execute(select(Series.id, Series.name)).all())
    layout = layout_of(db, library)
    book_rows, beside, shared, folder_editions = layout.book_rows, layout.beside, layout.shared, layout.folder_editions
    disk = Disk(library.path)
    by_edition: dict[int, list[Move]] = {}
    audio_folders: dict[str, list[int]] = defaultdict(list)
    for edition in editions:
        files = book_rows.get(edition.id, [])
        if not files:
            continue
        plan.files += len(files) + len(beside.get(edition.id, []))
        if edition.id in shared:
            by_edition[edition.id] = [Move(f.id, edition.id, f.path, f.path, f.size, f.mtime_ns, "problem",
                                           "shares_beside") for f in files]
            continue
        who = people.get(edition.id, {})
        authors, narrators = who.get(AUTHOR, []), who.get(NARRATOR, [])
        position = edition.series_pos
        year = edition.published[:4] if edition.published[:4].isdigit() else ""
        if not year and work_years.get(edition.work_id):
            year = str(work_years[edition.work_id])
        series = edition.series_name or (series_names.get(edition.series_id or 0, "") if edition.series_id else "")
        values = {
            "Author": authors[0][0] if authors else "", "AuthorSort": authors[0][1] if authors else "",
            "Series": series, "Volume": schemes.volume_text(position) if series else "",
            "Title": edition.title or posixpath.splitext(posixpath.basename(files[0].path))[0],
            "Year": year, "Language": edition.language, "Narrator": narrators[0][0] if narrators else "",
        }
        for name in {name for part in scheme.parts for kind, name in part if kind == "name"}:
            if not values.get(name):
                plan.empty[name] = plan.empty.get(name, 0) + 1
        try:
            parts = schemes.render(scheme, values)
        except schemes.SchemeError:
            by_edition[edition.id] = [Move(f.id, edition.id, f.path, f.path, f.size, f.mtime_ns, "problem", "no_title")
                                      for f in files + [side for side, _relative in beside.get(edition.id, [])]]
            continue
        moves: list[Move] = []
        if edition.kind == EDITION_AUDIO:
            base = "/".join(parts)
            unit_dir = catalog._unit_dir(files[0].path)
            for row in files:
                inside = bool(unit_dir) and row.path.startswith(unit_dir + "/")
                relative = row.path[len(unit_dir) + 1:] if inside else row.path
                moves.append(Move(row.id, edition.id, row.path, f"{base}/{relative}", row.size, row.mtime_ns))
            for row, relative in beside.get(edition.id, []):
                moves.append(Move(row.id, edition.id, row.path, f"{base}/{relative}", row.size, row.mtime_ns))
            why = _audio_folder_problem(base, unit_dir, edition.id, folder_editions)
            if why:
                for move in moves:
                    move.state, move.reason = "problem", why
            elif _fold(base) != _fold(unit_dir):
                audio_folders[_fold(base)].append(edition.id)
        else:
            directory = "/".join(parts[:-1])
            stem = parts[-1]
            for row in files:
                ending = posixpath.splitext(row.path)[1]
                name = f"{stem}{ending}"
                moves.append(Move(row.id, edition.id, row.path, f"{directory}/{name}" if directory else name,
                                  row.size, row.mtime_ns))
            for row, relative in beside.get(edition.id, []):
                moves.append(Move(row.id, edition.id, row.path, f"{directory}/{relative}" if directory else relative,
                                  row.size, row.mtime_ns))
        by_edition[edition.id] = moves
    # Two recordings sent to one new folder would be read as one: both stay. (One that is there already makes the folder
    # taken for the others.)
    for same in audio_folders.values():
        if len(same) > 1:
            for edition_id in same:
                for move in by_edition[edition_id]:
                    if move.state == "planned":
                        move.state, move.reason = "problem", "collision"
    _judge(plan, by_edition, disk)
    plan.digest = _digest(plan, scheme.text)
    return plan


def _audio_folder_problem(base: str, unit_dir: str, edition_id: int, folder_editions: dict[str, set[int]]) -> str:
    """Why a recording cannot go to the folder ``base``, or an empty string. Its own folder is no problem (it stays)."""
    last = base.rsplit("/", 1)[-1]
    if is_cd_folder(last) or fold(last) in COVER_FOLDERS:
        return "part_folder"
    if _fold(base) != _fold(unit_dir) and folder_editions.get(_fold(base), set()) - {edition_id}:
        return "folder_taken"
    return ""


def _judge(plan: Plan, by_edition: dict[int, list[Move]], disk: Disk) -> None:
    """Marks what stays, what cannot go, and puts the rest in the plan."""
    wanted: dict[str, list[Move]] = defaultdict(list)
    for moves in by_edition.values():
        for move in moves:
            if move.state == "planned":
                wanted[_fold(move.target)].append(move)
    for same in wanted.values():
        if len({_fold(m.source) for m in same}) > 1:
            for move in same:
                if move.source != move.target:
                    move.state, move.reason = "problem", "collision"
    for moves in by_edition.values():
        for move in moves:
            if move.state != "planned":
                continue
            if move.source == move.target or _fold(move.source) == _fold(move.target):
                move.state, move.reason = "stays", ""
                continue
            if len(move.target) > schemes.MAX_PATH:
                move.state, move.reason = "problem", "too_long"
                continue
            why = disk.blocked(move.target)
            if why:
                move.state, move.reason = "problem", why
    for moves in by_edition.values():
        states = {m.state for m in moves}
        if "problem" in states:
            plan.problem_books += 1
            for move in moves:
                if move.state in ("planned", "stays"):
                    move.state, move.reason = "problem", "book_blocked"
        elif states == {"stays"}:
            plan.stay_books += 1
        else:
            plan.move_books += 1
        plan.moves.extend(m for m in moves if m.state != "stays")
    counts = Counter(m.reason for m in plan.moves if m.state == "problem")
    plan.problems = dict(sorted(counts.items()))


def _digest(plan: Plan, scheme_text: str) -> str:
    lines = sorted(f"{m.source}\t{m.target}\t{m.size}\t{m.mtime_ns}" for m in plan.moves if m.state == "planned")
    return hashlib.sha256(("\n".join([scheme_text, *lines])).encode("utf-8", "replace")).hexdigest()


def writable(path: str) -> tuple[bool, str]:
    """Whether nexbooks may write in the folder of a library: ``(True, "")``, or ``(False, reason)`` with ``read_only``
    (the
    folder is mounted read-only, as the libraries are from the start) or ``no_permission``."""
    root = Path(path)
    if not root.is_dir():
        return False, "missing"
    statvfs = getattr(os, "statvfs", None)
    if statvfs is not None:
        try:
            if statvfs(root).f_flag & os.ST_RDONLY:
                return False, "read_only"
        except OSError:
            pass
    if not os.access(root, os.W_OK | os.X_OK):
        return False, "no_permission"
    return True, ""
