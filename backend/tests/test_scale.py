"""The floor under the scan: five thousand small files are read with an amount of work a household can live with, and a
second run over the same folder, which finds nothing new, does almost none. The limits are generous on purpose; they
exist to catch a scan that has become quadratic, not to time it.

**The work is counted, not timed.** A test that compares seconds says something about the machine it runs on (a busy
one failed it while the scan was fine). What a scan costs is told by what it does: how many files it hands to the helper
process that reads them, and how many steps the database takes (SQLite counts its own virtual-machine steps, which grow
with every row a query walks over, so a query that began to read the whole table for each file would show at once). Both
numbers are the same on any machine. The seconds are printed for a person who wants them."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select

from app.db import SessionLocal, engine
from app.models import BookFile, Edition, Work
from app.services import probe

from .bookmaker import make_epub, make_mp3, make_pdf
from .library_helpers import age, books_folder, make_library, scan

FILES = 5000
#: What the database may do for each file, in thousands of its virtual-machine steps. Measured on 5.000 files (see the
#: print): 0.63 for a file read for the first time, 0.23 for one that has not changed. The limits are about three times
#: that; a query that walks the whole table for each file costs fifty to a hundred times as much.
FIRST_STEPS_PER_FILE = 2.0
SECOND_STEPS_PER_FILE = 0.7

FIRST = ["Ilse", "Jonas", "Mara", "Theo", "Ada", "Lenja", "Paul", "Greta", "Emil", "Nora", "Linus", "Ruth", "Matteo"]
LAST = ["Marwitz", "Feldkamp", "Lindqvist", "Brandauer", "Okafor", "Hellwig", "Sommerfeld", "Ravensberg", "Duvall",
        "Kranich", "Neuhof", "Esterhaus", "Morrow", "Valcourt", "Brenner", "Asmussen", "Tiedemann"]


def make_files(folder: Path) -> None:
    for number in range(FILES):
        author = f"{FIRST[number % len(FIRST)]} {LAST[(number // 3) % len(LAST)]}"
        place = folder / f"{LAST[number % len(LAST)]}" / f"group {number // 10}"
        if number % 25 == 0:
            path = make_pdf(place / f"p{number}.pdf", title=f"Printed {number}", author=author, pages=5)
        elif number % 6 == 0:
            path = make_mp3(place / f"a{number}.mp3", 1.0, album=f"Recording {number}", artist=author, title="One")
        else:
            path = make_epub(place / f"e{number}.epub", title=f"Quiet Book {number}", authors=(author,),
                             chapters=("One",), text_bytes=300, language="en")
        age(path)


class Counted:
    """What a scan did, in numbers that do not depend on the machine."""

    def __init__(self) -> None:
        #: Files handed to the helper process (each is one reading of a book).
        self.reads = 0
        #: Steps of the database, in thousands of its virtual-machine instructions.
        self.steps = 0


@contextlib.contextmanager
def counting(monkeypatch: pytest.MonkeyPatch) -> Iterator[Counted]:
    done = Counted()

    def handler() -> int:
        done.steps += 1
        return 0

    def checked_out(connection: Any, _record: Any, _proxy: Any) -> None:
        connection.set_progress_handler(handler, 1000)

    def checked_in(connection: Any, _record: Any) -> None:
        connection.set_progress_handler(None, 0)

    real = probe.Worker.read

    def read(self: probe.Worker, root: str, relative: str, fmt: str) -> dict[str, Any]:
        done.reads += 1
        return real(self, root, relative, fmt)

    monkeypatch.setattr(probe.Worker, "read", read)
    event.listen(engine, "checkout", checked_out)
    event.listen(engine, "checkin", checked_in)
    try:
        yield done
    finally:
        event.remove(engine, "checkout", checked_out)
        event.remove(engine, "checkin", checked_in)


def test_five_thousand_files_are_read_with_an_amount_of_work_a_household_can_live_with(
        monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("scale")
    started = time.monotonic()
    make_files(folder)
    made = time.monotonic() - started
    library_id = make_library(folder, "Scale")
    first_started = time.monotonic()
    with counting(monkeypatch) as first:
        row = scan(library_id)
    first_seconds = time.monotonic() - first_started
    assert row.report["aborted"] is None and row.report["problems"] == 0
    with SessionLocal() as db:
        files = db.scalar(select(func.count(BookFile.id)).where(BookFile.role == "book")) or 0
        works = db.scalar(select(func.count(Work.id))) or 0
        editions = db.scalar(select(func.count(Edition.id))) or 0
    assert files == FILES and works == FILES and editions == FILES  # every title is its own
    second_started = time.monotonic()
    with counting(monkeypatch) as second:
        again = scan(library_id)
    second_seconds = time.monotonic() - second_started
    assert again.report["added"] == 0 and again.report["changed"] == 0
    print(f"\nscale: made {FILES} files in {made:.1f}s; first scan {first_seconds:.1f}s, {first.reads} reads, "
          f"{first.steps} steps; second scan {second_seconds:.1f}s, {second.reads} reads, {second.steps} steps")
    # Every book is read once the first time, and none again.
    assert first.reads == FILES, f"the first scan read {first.reads} files for {FILES} books"
    assert second.reads == 0, f"the second scan read {second.reads} files that had not changed"
    # The database: a bounded number of steps for each file, the second run (which only looks) far fewer.
    assert first.steps <= FIRST_STEPS_PER_FILE * FILES, f"first scan: {first.steps / FILES:.2f} steps for each file"
    assert second.steps <= SECOND_STEPS_PER_FILE * FILES, f"second scan: {second.steps / FILES:.2f} steps for each file"
