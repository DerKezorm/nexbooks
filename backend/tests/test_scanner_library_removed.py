"""A library taken out of nexbooks while it is being read: the run ends, quietly, and leaves nothing behind. Only invented
books."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import BookFile, Edition, Job, Library, Work
from app.services import libraries, probe, scanner

from .bookmaker import make_epub
from .library_helpers import age_all, books_folder, make_library, run_jobs


def a_library(name: str, books: int = 6) -> int:
    folder = books_folder(name)
    for number in range(books):
        make_epub(folder / f"b{number}.epub", title=f"Buch Nr {number} Nebel", authors=("Mara Lind",), language="de")
    age_all(folder)
    return make_library(folder, name)


def remove(library_id: int) -> None:
    with SessionLocal() as db:
        libraries.remove(db, db.get(Library, library_id))


def rows_of(model: Any) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count(model.id))) or 0)


def jobs_of_scans() -> list[tuple[str, str]]:
    with SessionLocal() as db:
        return [(job.state, job.error or "") for job in db.scalars(select(Job).where(Job.kind == "scan"))]


def scan_with(monkeypatch: pytest.MonkeyPatch, library_id: int, during: Callable[[int], None]) -> None:
    """Runs a scan, with ``during(n)`` called before the ``n``-th file is read."""
    original = probe.Worker.read
    seen = {"count": 0}

    def read(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen["count"] += 1
        during(seen["count"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(probe.Worker, "read", read)
    with SessionLocal() as db:
        libraries.start_scan(db, library_id)
    run_jobs()


def test_a_library_removed_between_two_files_ends_the_run_quietly(operator: Any, monkeypatch: pytest.MonkeyPatch,
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    library_id = a_library("weg")
    with caplog.at_level(logging.INFO):
        scan_with(monkeypatch, library_id, lambda count: remove(library_id) if count == 4 else None)
    assert jobs_of_scans() == [("done", "")]
    assert (rows_of(Library), rows_of(Edition), rows_of(BookFile), rows_of(Work)) == (0, 0, 0, 0)
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_a_library_removed_while_a_file_is_read_ends_the_run_quietly(operator: Any, monkeypatch: pytest.MonkeyPatch,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    library_id = a_library("weg")
    original = scanner.sha256_of

    def digest(*args: Any, **kwargs: Any) -> Any:
        if rows_of(Library):
            remove(library_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(scanner, "sha256_of", digest)
    with caplog.at_level(logging.INFO), SessionLocal() as db:
        libraries.start_scan(db, library_id)
        run_jobs()
    assert jobs_of_scans() == [("done", "")]
    assert (rows_of(Library), rows_of(Edition), rows_of(BookFile), rows_of(Work)) == (0, 0, 0, 0)
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_a_failure_of_a_file_in_a_library_that_is_there_is_still_a_failure(operator: Any, monkeypatch: pytest.MonkeyPatch,
                                                                           caplog: pytest.LogCaptureFixture) -> None:
    library_id = a_library("bleibt", books=2)

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("no")

    monkeypatch.setattr(scanner, "sha256_of", broken)
    with caplog.at_level(logging.INFO), SessionLocal() as db:
        libraries.start_scan(db, library_id)
        run_jobs()
    assert "Handling a file failed" in caplog.text
    with SessionLocal() as db:
        assert db.get(Library, library_id).report["problems"] >= 1


def test_the_run_of_another_library_goes_on(operator: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gone, stays = a_library("weg", books=3), a_library("bleibt", books=2)
    with SessionLocal() as db:
        libraries.start_scan(db, stays)
    scan_with(monkeypatch, gone, lambda count: remove(gone) if count == 1 and rows_of(Library) == 2 else None)
    assert rows_of(Library) == 1 and rows_of(BookFile) == 2


def test_a_scan_that_cannot_write_for_another_reason_still_fails(operator: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    library_id = a_library("bleibt", books=1)

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("no")

    monkeypatch.setattr(scanner.catalog, "settle_library", broken)
    with SessionLocal() as db:
        libraries.start_scan(db, library_id)
    run_jobs()
    assert [state for state, _error in jobs_of_scans()] != ["done"]
    with SessionLocal() as db:
        assert db.get(Library, library_id).report["aborted"] == "error"
