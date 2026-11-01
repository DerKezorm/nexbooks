"""Libraries: the folders of books the operator made known, who may see them, and what is in them.

**Where a library may lie.** Nowhere on the server, whatever the operator types: only at or below one of the folders in
``NEXBOOKS_LIBRARY_ROOTS`` (``/books`` in the container). The path is resolved (``..`` and links followed) *before*
it is compared, so a link inside a root that leads out of it leads out of the root, too. It may not be the data folder
of nexbooks, hold it, or lie in it (the database and the secrets are there), and it may not overlap another library
(the same file would belong to two).

**Who sees what.** A person sees the books of the libraries they are a member of and of no other, the operator
included. The right ``read`` sees; ``acquire`` may also ask for books to be fetched (each right includes the one
before it); ``manage`` may also start a scan and read its list of problems. Making, changing and removing a library and
setting who is in it is the operator's. Every query for works, editions, files, chapters and
covers asks the database for the libraries of the person *in the SQL* (``member_library_ids``): a list that is filtered
afterwards could count what the person may not see.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Select, delete, func, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..errors import error
from ..models import (
    ACQUIRE,
    KIND_AUDIO,
    KIND_EBOOK,
    LIBRARY_KINDS,
    LIBRARY_RIGHTS,
    MANAGE,
    OPERATOR,
    READ,
    ROLE_BOOK,
    Account,
    BookFile,
    Edition,
    Job,
    Library,
    LibraryMember,
    LibraryProblem,
    Work,
)
from . import jobs

logger = logging.getLogger("nexbooks.libraries")

NAME_MAX = 80
MAX_LIBRARIES = 50
MAX_MEMBERS = 500
_RANK = {READ: 0, ACQUIRE: 1, MANAGE: 2}


class PathProblem(Exception):
    """A path that cannot be a library; ``code`` says why (the interface has a sentence for each)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def resolved_roots() -> list[Path]:
    """The folders libraries may lie in, resolved. Those that are not there are left out."""
    found = []
    for root in get_settings().library_root_list():
        try:
            found.append(root.resolve(strict=True))
        except OSError:
            continue
    return found


def check_path(raw: str, *, ignore_id: int | None = None, db: Session | None = None) -> Path:
    """The real path of a folder that may be a library, or ``PathProblem``. See the top of this file."""
    text = (raw or "").strip()
    if not text or len(text) > 1000 or "\x00" in text:
        raise PathProblem("path_invalid")
    if not Path(text).is_absolute():
        raise PathProblem("path_relative")
    if not get_settings().library_root_list():
        raise PathProblem("no_roots")
    try:
        path = Path(text).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathProblem("path_missing") from exc
    if not path.is_dir():
        raise PathProblem("path_not_folder")
    if not any(_is_inside(path, root) for root in resolved_roots()):
        raise PathProblem("path_outside_roots")
    try:
        data = get_settings().data_dir.resolve()
    except OSError:
        data = get_settings().data_dir
    if _is_inside(path, data) or _is_inside(data, path):
        raise PathProblem("path_is_data")
    try:
        with os.scandir(path):
            pass
    except OSError as exc:
        raise PathProblem("path_unreadable") from exc
    if db is not None:
        for other_id, other_path in db.execute(select(Library.id, Library.path)):
            if other_id == ignore_id:
                continue
            other = Path(other_path)
            if _is_inside(path, other) or _is_inside(other, path):
                raise PathProblem("path_overlap")
    return path


def folders_below_roots(limit: int = 200) -> list[dict[str, object]]:
    """For the operator's picker: the roots and the folders directly in them (names only, no links)."""
    found: list[dict[str, object]] = []
    for root in resolved_roots():
        folders: list[str] = []
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if len(folders) >= limit:
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith((".", "@", "#")):
                            folders.append(str(root / entry.name))
                    except OSError:
                        continue
        except OSError:
            pass
        found.append({"root": str(root), "folders": sorted(folders)})
    return found


# ---- Rights ----------------------------------------------------------------------------------------------------


def member_library_ids(uid: int) -> Select[tuple[int]]:
    """The libraries ``uid`` is a member of, as a subquery."""
    return select(LibraryMember.library_id).where(LibraryMember.user_id == uid)


def right_in(db: Session, uid: int, library_id: int) -> str | None:
    return db.scalar(select(LibraryMember.right).where(LibraryMember.library_id == library_id,
                                                       LibraryMember.user_id == uid))


def at_least(right: str | None, need: str) -> bool:
    return right is not None and _RANK[right] >= _RANK[need]


def require_readable(db: Session, uid: int, library_id: int) -> Library:
    """The library, for a member; 404 for everybody else (a library one may not see is one that is not there)."""
    row = db.scalar(select(Library).where(Library.id == library_id, Library.id.in_(member_library_ids(uid))))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def managed_library_ids(uid: int) -> Select[tuple[int]]:
    """The libraries ``uid`` looks after (``manage``), as a subquery; the operator only where he is such a member."""
    return select(LibraryMember.library_id).where(LibraryMember.user_id == uid, LibraryMember.right == MANAGE)


def acquirable_library_ids(uid: int) -> Select[tuple[int]]:
    """The libraries ``uid`` may ask for books in (``acquire`` or ``manage``), as a subquery."""
    return select(LibraryMember.library_id).where(LibraryMember.user_id == uid,
                                                  LibraryMember.right.in_((ACQUIRE, MANAGE)))


def require_acquirer(db: Session, uid: int, library_id: int) -> Library:
    """The library, for a member with ``acquire`` or ``manage`` and for nobody else (the operator included: who fetches
    books for a library is for the people who have the right in it); 404 for anybody who does not see it, 403 for a
    member who sees it but may not ask for books."""
    row = require_readable(db, uid, library_id)
    if not at_least(right_in(db, uid, library_id), ACQUIRE):
        raise error("forbidden", "Your right here does not allow this.", 403)
    return row


def require_reviewer(db: Session, uid: int, library_id: int) -> Library:
    """The library, for a member with ``manage`` and for nobody else (the operator included: what a book is called, and
    which book it is, is for the people who look after it); 404 for anybody who does not see it, 403 for a member who
    sees it but may not manage it."""
    row = require_readable(db, uid, library_id)
    if not at_least(right_in(db, uid, library_id), MANAGE):
        raise error("forbidden", "Your right here does not allow this.", 403)
    return row


def require_manageable(db: Session, account: Account, library_id: int) -> Library:
    """The library, for the operator (of any library) and for a member with ``manage``; 404 for anybody who does not
    see it, 403 for a member who sees it but may not manage it."""
    row = db.get(Library, library_id)
    if row is None:
        raise error("not_found", "Not found.", 404)
    if account.role == OPERATOR:
        return row
    right = right_in(db, account.id, library_id)
    if right is None:
        raise error("not_found", "Not found.", 404)
    if not at_least(right, MANAGE):
        raise error("forbidden", "Your right here does not allow this.", 403)
    return row


# ---- State of the scan -----------------------------------------------------------------------------------------


def scan_states(db: Session, library_ids: list[int]) -> dict[int, str]:
    """``idle``, ``queued`` or ``running`` for each library, from the queue (which survives a restart)."""
    states = {ident: "idle" for ident in library_ids}
    if not library_ids:
        return states
    for ref, state in db.execute(select(Job.ref, Job.state).where(
            Job.kind == "scan", Job.ref.in_(library_ids), Job.state.in_(("queued", "running")))):
        if states.get(ref) != "running":
            states[ref] = "running" if state == "running" else "queued"
    return states


def match_states(db: Session, library_ids: list[int]) -> dict[int, str]:
    """``idle``, ``queued`` or ``running`` for the matching of each library, from the queue."""
    states = {ident: "idle" for ident in library_ids}
    if not library_ids:
        return states
    for ref, state in db.execute(select(Job.ref, Job.state).where(
            Job.kind == "match", Job.ref.in_(library_ids), Job.state.in_(("queued", "running")))):
        if states.get(ref) != "running":
            states[ref] = "running" if state == "running" else "queued"
    return states


def start_scan(db: Session, library_id: int, *, everything: bool = False) -> None:
    """Puts a scan in the queue; a scan that already waits for the same library is the same scan. ``everything`` asks
    that every file be read again, not only the new and changed ones (the next generation of reading: a scan that
    already waits reads by it too, a scan that runs finishes as it began and the next one reads by the new)."""
    if everything:
        library = db.get(Library, library_id)
        if library is not None:
            library.reread_rev = int(library.reread_rev or 0) + 1
    jobs.enqueue(db, "scan", ref=library_id)
    db.commit()


# ---- Making, changing and removing -----------------------------------------------------------------------------


def create(db: Session, operator: Account, name: str, path: str, kind: str, scan_enabled: bool) -> Library:
    name = name.strip()
    if not name or len(name) > NAME_MAX:
        raise error("invalid_input", "The name is not valid.", 422)
    if kind not in LIBRARY_KINDS:
        raise error("invalid_input", "The kind is not valid.", 422)
    if (db.scalar(select(func.count(Library.id))) or 0) >= MAX_LIBRARIES:
        raise error("too_many_libraries", "nexbooks holds at most 50 libraries.", 409)
    if db.scalar(select(Library.id).where(func.lower(Library.name) == name.lower())):
        raise error("name_taken", "There is a library with this name.", 409)
    try:
        real = check_path(path, db=db)
    except PathProblem as exc:
        raise error(exc.code, "This folder cannot be a library.", 422, field="path") from exc
    row = Library(name=name, path=str(real), kind=kind, scan_enabled=scan_enabled, created_at=clock.now())
    db.add(row)
    db.flush()
    # The one who made it sees it, and may take themselves out again: the operator reads nobody's books by their role.
    db.add(LibraryMember(library_id=row.id, user_id=operator.id, right=MANAGE))
    jobs.enqueue(db, "scan", ref=row.id)
    db.commit()
    logger.info("Library made id=%s kind=%s by=%s", row.id, kind, operator.name)
    return row


def update(db: Session, library: Library, *, name: str | None, kind: str | None, scan_enabled: bool | None) -> Library:
    if name is not None:
        name = name.strip()
        if not name or len(name) > NAME_MAX:
            raise error("invalid_input", "The name is not valid.", 422)
        if db.scalar(select(Library.id).where(func.lower(Library.name) == name.lower(), Library.id != library.id)):
            raise error("name_taken", "There is a library with this name.", 409)
        library.name = name
    if kind is not None:
        if kind not in LIBRARY_KINDS:
            raise error("invalid_input", "The kind is not valid.", 422)
        library.kind = kind
    if scan_enabled is not None:
        library.scan_enabled = scan_enabled
    db.commit()
    return library


def remove(db: Session, library: Library) -> None:
    """Takes a library out of nexbooks: its books, editions, files and chapters go from the database, and works that
    had no other edition. **The files stay where they are.** A scan that waits goes with it, and one that runs ends by
    itself at its next write (``scanner.LibraryGone``)."""
    library_id = library.id
    touched = set(db.scalars(select(Edition.work_id).where(Edition.library_id == library_id)))
    db.execute(delete(Job).where(Job.kind == "scan", Job.ref == library_id, Job.state == "queued"))
    db.execute(delete(Library).where(Library.id == library_id))
    db.flush()
    for work_id in touched:
        if not db.scalar(select(func.count(Edition.id)).where(Edition.work_id == work_id)):
            db.execute(delete(Work).where(Work.id == work_id))
        else:
            work = db.get(Work, work_id)
            if work is not None:
                from . import catalog

                catalog.refresh_work(db, work)
    db.commit()
    logger.info("Library removed id=%s", library_id)


@dataclass(frozen=True)
class Member:
    user_id: int
    right: str


def set_members(db: Session, library: Library, members: list[Member]) -> None:
    """Who is in the library, all at once; a refusal changes nothing."""
    if len(members) > MAX_MEMBERS:
        raise error("invalid_input", "Too many members.", 422)
    seen: set[int] = set()
    for member in members:
        if member.right not in LIBRARY_RIGHTS:
            raise error("invalid_input", "The right is not valid.", 422)
        if member.user_id in seen:
            raise error("invalid_input", "A person is listed twice.", 422)
        seen.add(member.user_id)
    existing = set(db.scalars(select(Account.id).where(Account.id.in_(seen)))) if seen else set()
    if existing != seen:
        raise error("invalid_input", "There is no such person.", 422)
    db.execute(delete(LibraryMember).where(LibraryMember.library_id == library.id))
    for member in members:
        db.add(LibraryMember(library_id=library.id, user_id=member.user_id, right=member.right))
    db.commit()


@dataclass(frozen=True)
class MemberChange:
    """One person's right in a library: from ``was`` (what the caller saw; None for not a member) to ``right`` (None
    for leaving)."""

    user_id: int
    was: str | None
    right: str | None


def change_members(db: Session, library: Library, changes: list[MemberChange]) -> None:
    """Only the given changes, each **in one step with the check** that the person still has the right the caller saw
    (the ``WHERE`` of the ``UPDATE`` or ``DELETE``, a new row only where there is none): a person that somebody else
    changed meanwhile makes the whole call fail with 409 and change nothing. Everybody not named is left as they
    are."""
    if len(changes) > MAX_MEMBERS:
        raise error("invalid_input", "Too many changes.", 422)
    seen: set[int] = set()
    for change in changes:
        if change.user_id in seen:
            raise error("invalid_input", "A person is listed twice.", 422)
        seen.add(change.user_id)
        if any(right is not None and right not in LIBRARY_RIGHTS for right in (change.was, change.right)):
            raise error("invalid_input", "The right is not valid.", 422)
        if change.was == change.right:
            raise error("invalid_input", "That is no change.", 422)
    if seen and set(db.scalars(select(Account.id).where(Account.id.in_(seen)))) != seen:
        raise error("invalid_input", "There is no such person.", 422)
    where = [LibraryMember.library_id == library.id]
    try:
        for change in changes:
            mine = [*where, LibraryMember.user_id == change.user_id]
            if change.was is None:
                db.add(LibraryMember(library_id=library.id, user_id=change.user_id, right=change.right))
                db.flush()
                continue
            if change.right is None:
                taken = db.execute(delete(LibraryMember).where(*mine, LibraryMember.right == change.was)).rowcount
            else:
                taken = db.execute(sql_update(LibraryMember).where(*mine, LibraryMember.right == change.was)
                                   .values(right=change.right)).rowcount
            if taken != 1:
                raise error("members_changed", "Somebody changed the rights meanwhile. Look at the list again.", 409)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise error("members_changed", "Somebody changed the rights meanwhile. Look at the list again.", 409) from exc
    except Exception:
        db.rollback()
        raise


# ---- Numbers -----------------------------------------------------------------------------------------------------


def counts(db: Session, library_ids: list[int]) -> dict[int, dict[str, int]]:
    """Per library: editions by kind, files (books) and their bytes, problems, from the database."""
    result = {ident: {"ebooks": 0, "audiobooks": 0, "files": 0, "bytes": 0, "problems": 0, "review": 0,
                      "entries": 0, "pending": 0} for ident in library_ids}
    if not library_ids:
        return result
    for library_id, kind, number in db.execute(
            select(Edition.library_id, Edition.kind, func.count(Edition.id))
            .where(Edition.library_id.in_(library_ids), Edition.missing_at.is_(None))
            .group_by(Edition.library_id, Edition.kind)):
        result[library_id]["audiobooks" if kind == KIND_AUDIO else "ebooks"] += number
    for library_id, number, size in db.execute(
            select(BookFile.library_id, func.count(BookFile.id), func.coalesce(func.sum(BookFile.size), 0))
            .where(BookFile.library_id.in_(library_ids), BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
                   BookFile.edition_id.is_not(None))
            .group_by(BookFile.library_id)):
        result[library_id]["files"] = number
        result[library_id]["bytes"] = int(size)
    for library_id, number in db.execute(
            select(LibraryProblem.library_id, func.count(LibraryProblem.id))
            .where(LibraryProblem.library_id.in_(library_ids)).group_by(LibraryProblem.library_id)):
        result[library_id]["problems"] = number
    from .match import review

    for library_id, number in review.to_look_at(db, library_ids).items():
        result[library_id]["review"] = number
    for library_id, total, waiting in review.progress_of(db, library_ids):
        result[library_id]["entries"] = total
        result[library_id]["pending"] = waiting
    return result


def kind_allows(library_kind: str, fmt_kind: str) -> bool:
    """Whether a library of ``library_kind`` takes files of ``fmt_kind`` (``ebook`` or ``audio``)."""
    return library_kind == "both" or library_kind == fmt_kind


__all__ = [
    "ACQUIRE", "KIND_AUDIO", "KIND_EBOOK", "MAX_MEMBERS", "Member", "PathProblem", "acquirable_library_ids",
    "at_least", "check_path", "counts", "create", "folders_below_roots", "kind_allows", "member_library_ids", "remove",
    "require_acquirer", "require_manageable", "require_readable", "require_reviewer", "resolved_roots", "right_in",
    "scan_states", "set_members", "start_scan", "update",
]
