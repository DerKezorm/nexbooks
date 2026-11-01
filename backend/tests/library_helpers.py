"""What the tests of libraries and books share: a library made in the folder for libraries, a scan run to its end, and
links of every kind the system can make (a skipped test never counts as a pass, so the way to a link is tried in order)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import OPERATOR, Account, Library
from app.services import jobs, libraries

from .bookmaker import cover_image, make_epub, make_m4b, make_mp3
from .conftest import BOOKS_DIR, make_account


def books_folder(name: str = "shelf") -> Path:
    """A new empty folder for a library, inside the folder libraries may lie in."""
    folder = Path(BOOKS_DIR) / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


def operator_account() -> Account:
    with SessionLocal() as db:
        row = db.scalar(select(Account).where(Account.name == "ops"))
        if row is not None:
            db.expunge(row)
            return row
    return make_account("ops", OPERATOR)


def make_library(folder: Path, name: str = "Books", kind: str = "both", scan: bool = False) -> int:
    """A library on ``folder`` made the way the operator makes one; its first scan is not run unless asked."""
    operator = operator_account()
    with SessionLocal() as db:
        library = libraries.create(db, operator, name, str(folder), kind, True)
        library_id = library.id
        if not scan:
            from app.models import Job

            db.query(Job).delete()
            db.commit()
    if scan:
        run_jobs()
    return library_id


def join(library_id: int, account_id: int, right: str = "manage") -> None:
    """Makes an account a member of a library (or changes its right there)."""
    from app.models import LibraryMember

    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(library_id=library_id, user_id=account_id).delete()
        db.add(LibraryMember(library_id=library_id, user_id=account_id, right=right))
        db.commit()


def run_jobs() -> int:
    """Runs every job that waits (the scans), to the end."""
    total = 0
    while True:
        ran = jobs.run_pending(1000)
        total += ran
        if not ran:
            return total


def scan(library_id: int) -> Library:
    """Asks for a scan of the library and runs it. Returns the library as it stands afterwards."""
    with SessionLocal() as db:
        libraries.start_scan(db, library_id)
    run_jobs()
    with SessionLocal() as db:
        row = db.get(Library, library_id)
        assert row is not None
        db.expunge(row)
        return row


def age(path: Path, seconds: float = 3600) -> None:
    """Makes a file look older than the scan's quiet time: a file changed a moment ago is left for the next run."""
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def age_all(folder: Path, seconds: float = 3600) -> None:
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink():
            age(path, seconds)


def book(folder: Path, relative: str, **kwargs: object) -> Path:
    """An e-book in the folder, old enough for the scan."""
    path = make_epub(folder / relative, **kwargs)  # type: ignore[arg-type]
    age(path)
    return path


def recording(folder: Path, relative: str, seconds: float = 6.0, **tags: object) -> Path:
    path = make_mp3(folder / relative, seconds, **tags) if relative.endswith(".mp3") else make_m4b(
        folder / relative, seconds, **tags)
    age(path)
    return path


def make_link(link: Path, target: Path) -> None:
    """A link at ``link`` pointing to ``target`` (a folder or a file): a symbolic link where the system allows it, else
    on Windows a junction (for folders). Fails the test when neither can be made, so that a missing right is seen."""
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32" and target.is_dir():
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.fail("no kind of link can be made here")


def can_link_files() -> bool:
    """Whether a symbolic link to a file can be made (Windows wants the developer mode for it)."""
    probe_dir = Path(BOOKS_DIR).parent / "link-probe"
    probe_dir.mkdir(exist_ok=True)
    target = probe_dir / "t.txt"
    target.write_text("x", encoding="utf-8")
    link = probe_dir / "l.txt"
    link.unlink(missing_ok=True)
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return True


__all__ = ["age", "age_all", "book", "books_folder", "can_link_files", "cover_image", "join", "make_library", "make_link",
           "operator_account", "recording", "run_jobs", "scan"]
