"""The scan while it reads: a unit shows on the shelf as soon as its files are read, the matching begins for it at once in a
lane of its own, what is certain is done early and what is not waits for the end; and a rule that changed is applied to the
books that are there once ("read everything again"). All invented books."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, BookFile, Edition, Job, Library, LibraryMember, Work
from app.services import jobs, libraries, scanner
from app.services.match import run as matching

from .conftest import make_account, new_client
from .fake_sources import ol_doc
from .library_helpers import age, book, books_folder, make_library, recording, run_jobs, scan
from .match_helpers import World, member


def editions_now() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count(Edition.id))) or 0)


def watch_reading(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """``(path of the file about to be hashed, editions that exist then)`` for every book file the run reads."""
    seen: list[tuple[str, int]] = []
    real = scanner.sha256_of

    def watching(root: str, relative: str) -> str:
        seen.append((relative, editions_now()))
        return real(root, relative)

    monkeypatch.setattr(scanner, "sha256_of", watching)
    return seen


def library_row(library_id: int) -> Library:
    with SessionLocal() as db:
        row = db.get(Library, library_id)
        assert row is not None
        db.expunge(row)
        return row


# ---- Books show while the run is reading ----------------------------------------------------------------------------------


def test_a_book_is_on_the_shelf_before_the_run_has_read_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("early")
    for number in range(6):
        book(folder, f"Author {number}/Book {number}/b{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    seen = watch_reading(monkeypatch)
    scan(library_id)
    assert [path for path, _count in seen] == sorted(path for path, _count in seen)
    # When the third file is about to be read, the first two are already books on the shelf.
    assert [count for _path, count in seen] == [0, 1, 2, 3, 4, 5]
    assert editions_now() == 6
    row = library_row(library_id)
    assert row.report["early"] == 6 and row.report["added"] == 6


def test_the_parts_of_a_recording_are_one_unit_that_is_put_together_when_the_last_part_is_read(
        monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("parts")
    for part in range(3):
        recording(folder, f"Hoerbuch/part{part}.mp3", seconds=4.0, title=f"Teil {part}", album="Das Hoerbuch",
                  artist="Ada Feldkamp", track=part + 1)
    book(folder, "zzz/after.epub", title="After", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    seen = watch_reading(monkeypatch)
    scan(library_id)
    counts = dict(seen)
    assert counts["Hoerbuch/part0.mp3"] == counts["Hoerbuch/part1.mp3"] == counts["Hoerbuch/part2.mp3"] == 0
    assert counts["zzz/after.epub"] == 1, "the recording was one book, put together after its last part"
    with SessionLocal() as db:
        rows = db.scalars(select(BookFile).where(BookFile.role == "book", BookFile.format == "mp3")).all()
        assert len({r.edition_id for r in rows}) == 1 and sorted(r.part for r in rows) == [1, 2, 3]


def test_a_book_that_cannot_be_read_does_not_hold_back_the_ones_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("broken")
    book(folder, "a/a.epub", title="A", authors=("Ada Feldkamp",))
    (folder / "b").mkdir()
    (folder / "b" / "b.epub").write_bytes(b"this is not a zip")
    age(folder / "b" / "b.epub")
    book(folder, "c/c.epub", title="C", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    seen = watch_reading(monkeypatch)
    scan(library_id)
    assert dict(seen)["c/c.epub"] == 1, "A was on the shelf when C was read; the broken B is a problem and nothing else"
    assert editions_now() == 2 and library_row(library_id).report["problems"] == 1


def test_a_unit_that_cannot_be_put_together_is_listed_once_and_not_tried_again_in_the_same_run(
        monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("boom")
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    book(folder, "b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    original = scanner.catalog.assign
    tried: list[str] = []

    def boom(db: Any, library: Any, rows: Any, sidecars: Any) -> object:
        tried.append(rows[0].path)
        if rows[0].path == "a.epub":
            raise RuntimeError("something odd")
        return original(db, library, rows, sidecars)

    monkeypatch.setattr(scanner.catalog, "assign", boom)
    row = scan(library_id)
    assert tried.count("a.epub") == 1 and row.report["problems"] == 1 and editions_now() == 1


def test_a_run_that_is_stopped_goes_on_and_makes_every_book_once(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("stop")
    for number in range(6):
        book(folder, f"b{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    real = scanner.sha256_of
    calls = {"n": 0}

    def stopping(root: str, relative: str) -> str:
        calls["n"] += 1
        if calls["n"] == 4:
            raise jobs.Interrupted
        return real(root, relative)

    monkeypatch.setattr(scanner, "sha256_of", stopping)
    with SessionLocal() as db:
        libraries.start_scan(db, library_id)
    jobs.run_pending(5)
    assert editions_now() == 3, "the three read before the stop are books already"
    monkeypatch.setattr(scanner, "sha256_of", real)
    run_jobs()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Edition.id))) == 6 and db.scalar(select(func.count(Work.id))) == 6


# ---- What waits for the end ----------------------------------------------------------------------------------------------


def write_sidecar(folder: Path, relative: str, genres: list[str]) -> None:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"genres": genres}), encoding="utf-8")
    age(path)


def genre_of(title: str) -> str:
    with SessionLocal() as db:
        return str(db.scalar(select(Edition.genre).where(Edition.title == title)))


def test_a_file_beside_one_book_speaks_for_it(client: TestClient) -> None:
    folder = books_folder("single")
    book(folder, "Solo/solo.epub", title="Solo", authors=("Ada Feldkamp",))
    write_sidecar(folder, "Solo/metadata.json", ["Krimi"])
    scan(make_library(folder))
    assert genre_of("Solo") == "krimi"


def test_a_file_in_a_folder_of_several_books_speaks_for_none_of_them_also_when_the_first_is_put_together_early(
        monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("several")
    book(folder, "Shelf/a.epub", title="Alpha", authors=("Ada Feldkamp",))
    book(folder, "Shelf/b.epub", title="Beta", authors=("Ada Feldkamp",))
    write_sidecar(folder, "Shelf/metadata.json", ["Krimi"])
    seen = watch_reading(monkeypatch)
    scan(make_library(folder))
    # Alpha was read first; it must wait until Beta is read too, else it would have taken the file for its own.
    assert dict(seen)["Shelf/b.epub"] == 0
    assert genre_of("Alpha") == "" and genre_of("Beta") == ""


# ---- Matching beside the scan ----------------------------------------------------------------------------------------------


def test_the_matching_is_asked_to_begin_while_the_run_is_still_reading(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Book 0", ["Ada Feldkamp"], languages=("eng",))]
    folder = books_folder("beside")
    for number in range(8):
        book(folder, f"b{number}/b{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",), language="en")
    library_id = make_library(folder)
    member(library_id)
    monkeypatch.setattr(scanner, "MATCH_EVERY", 3)
    seen = watch_reading(monkeypatch)
    asked: list[int] = []
    real = jobs.enqueue

    def enqueue(db: Any, kind: str, ref: int = 0, payload: Any = None) -> None:
        if kind == "match":
            asked.append(len(seen))
        real(db, kind, ref=ref, payload=payload)

    monkeypatch.setattr(jobs, "enqueue", enqueue)
    scan(library_id)
    assert asked and asked[0] < 8, f"the matching was asked for before the last book was read: {asked}"


# ---- The lanes of the job loops ----------------------------------------------------------------------------------------


def test_the_matching_has_a_lane_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setitem(jobs.HANDLERS, "match", lambda db, job: ran.append("match"))
    monkeypatch.setitem(jobs.HANDLERS, "dummy", lambda db, job: ran.append("dummy"))
    with SessionLocal() as db:
        jobs.enqueue(db, "dummy", ref=1)
        jobs.enqueue(db, "match", ref=1)
        db.commit()
    assert jobs.run_pending(10, lane="match") == 1 and ran == ["match"]
    assert jobs.run_pending(10, lane="match") == 0
    assert jobs.run_pending(10, lane="main") == 1 and ran == ["match", "dummy"]
    with SessionLocal() as db:
        jobs.enqueue(db, "dummy", ref=2)
        jobs.enqueue(db, "match", ref=2)
        db.commit()
    assert jobs.run_pending(10, lane="main") == 1 and ran[-1] == "dummy" and ran.count("match") == 1
    assert jobs.run_pending(10) == 1 and ran[-1] == "match", "without a lane, every kind"


def test_a_lane_that_waits_does_not_hold_back_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setitem(jobs.HANDLERS, "match", lambda db, job: ran.append("match"))
    with SessionLocal() as db:
        for number in range(3):
            jobs.enqueue(db, "scan", ref=900 + number)
        jobs.enqueue(db, "match", ref=5)
        db.commit()
    # (The scans of libraries that are not there give up at once.) The match job is run by its lane though the scans stand
    # in front of it in the queue.
    assert jobs.run_pending(10, lane="match") == 1 and ran == ["match"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Job.id)).where(Job.kind == "scan", Job.state == "queued")) == 3


# ---- Reading everything again ----------------------------------------------------------------------------------------------


def hashed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = scanner.sha256_of

    def watching(root: str, relative: str) -> str:
        seen.append(relative)
        return real(root, relative)

    monkeypatch.setattr(scanner, "sha256_of", watching)
    return seen


def test_a_newer_generation_of_the_rules_reads_every_file_again_once_without_calling_it_a_change(
        monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("generation")
    for number in range(3):
        book(folder, f"b{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    assert library_row(library_id).rev == scanner.READ_REV
    seen = hashed(monkeypatch)
    row = scan(library_id)
    assert row.report["reread"] == 0 and row.report["added"] == 0 and seen == [], "nothing new: nothing read"
    # The code now needs a newer generation: the three files are read again, none is counted as changed.
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    row = scan(library_id)
    assert row.report["reread"] == 3 and row.report["changed"] == 0 and row.report["added"] == 0
    assert seen == [], "the checksums of files that did not change are not made again"
    assert row.rev == scanner.READ_REV
    row = scan(library_id)
    assert row.report["reread"] == 0, "once"


def test_reading_again_puts_a_book_right_that_an_older_rule_got_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("again")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",), description="<p>Eins.</p><p>Zwei.</p>")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        # (How the book stands after a version that read the description as one line, and the date as year 101.)
        row = db.scalar(select(BookFile).where(BookFile.role == "book"))
        row.probe = {**row.probe, "description": "Eins. Zwei.", "published": "0101-01-01", "year": 101}
        db.execute(Edition.__table__.update().values(description="Eins. Zwei.", published="0101-01-01"))
        db.commit()
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    with SessionLocal() as db:
        edition = db.scalar(select(Edition))
        assert edition.description == "Eins.\n\nZwei." and edition.published == ""


def test_a_person_s_correction_survives_reading_everything_again(client: TestClient, operator: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder("keep")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",), subjects=("Krimi",))
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library_id, user_id=operator.id, right="manage"))
        db.commit()
        edition_id = db.scalar(select(Edition.id))
    assert client.patch(f"/api/v1/editions/{edition_id}", json={"genre": "lyrik", "title": "Eigener Titel"}).status_code == 200
    with SessionLocal() as db:
        libraries.start_scan(db, library_id, everything=True)
    run_jobs()
    with SessionLocal() as db:
        edition = db.get(Edition, edition_id)
        assert edition.genre == "lyrik" and edition.title == "Eigener Titel"


def test_the_operator_can_ask_to_read_everything_again_and_a_reader_cannot(client: TestClient, operator: Account) -> None:
    folder = books_folder("ask")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    rita = make_account("rita")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library_id, user_id=operator.id, right="manage"))
        db.add(LibraryMember(library_id=library_id, user_id=rita.id, right="read"))
        db.commit()
    with new_client(rita) as reader:
        assert reader.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": True}).status_code == 403
    assert library_row(library_id).reread_rev == 0
    # A plain scan asks for nothing more than it did.
    assert client.post(f"/api/v1/libraries/{library_id}/scan").status_code == 202
    assert library_row(library_id).reread_rev == 0
    run_jobs()
    answer = client.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": True})
    assert answer.status_code == 202
    assert library_row(library_id).reread_rev == 1
    row = scan(library_id)
    assert row.report["reread"] == 1 and row.rev == scanner.READ_REV + 1
    assert client.post(f"/api/v1/libraries/{library_id}/scan", json={"everything": "yes please"}).status_code == 422
    assert matching and Work


def test_two_books_an_older_rule_kept_apart_come_together_when_they_are_read_again(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The PDF with the wrong author in its information and the recording of the same book were two works, the author being
    another; read again by the new rules (the path names the author) they are one work, a book with two forms. A book that
    already has its twin is not taken from it."""
    from .bookmaker import make_pdf

    folder = books_folder("together")
    pdf = make_pdf(folder / "Dan Example" / "Zeichen - Dan Example.pdf", title="Zeichen", author="Martin Wegener")
    age(pdf)
    recording(folder, "Dan Example/zeichen/zeichen.m4b", 600.0, title="Zeichen", album="Zeichen", artist="Dan Example")
    library_id = make_library(folder)
    monkeypatch.setattr(scanner.catalog, "_pdf_against_the_path", lambda path, authors, title: (authors, title))
    scan(library_id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Work.id))) == 2
    monkeypatch.undo()
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    with SessionLocal() as db:
        works = db.scalars(select(Work)).all()
        assert len(works) == 1
        assert sorted(db.scalars(select(Edition.kind).where(Edition.work_id == works[0].id)).all()) == ["audio", "ebook"]


def test_the_line_of_progress_only_goes_forward_and_ends_at_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """"x of y files": the number done never goes back (not when the books are put together on the way, not when the last
    ones are put together at the end), the number to do never shrinks, and both are gone when the run is."""
    folder = books_folder("progress")
    for number in range(5):
        book(folder, f"Author/Book {number}/b{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    # A file beside the books of a folder with several: its books wait for the end of the run.
    for number in range(3):
        book(folder, f"Wait/w{number}.epub", title=f"Wait {number}", authors=("Ada Feldkamp",))
    write_sidecar(folder, "Wait/metadata.json", ["Krimi"])
    library_id = make_library(folder)
    seen: list[tuple[int, int]] = []
    real = scanner.sha256_of

    def watching(root: str, relative: str) -> str:
        row = library_row(library_id)
        seen.append((row.progress_done, row.progress_total))
        return real(root, relative)

    monkeypatch.setattr(scanner, "sha256_of", watching)
    putting: list[tuple[int, int]] = []
    assign = scanner.catalog.assign

    def assigning(db: Any, library: Any, rows: Any, sidecars: Any) -> object:
        row = library_row(library_id)
        putting.append((row.progress_done, row.progress_total))
        return assign(db, library, rows, sidecars)

    monkeypatch.setattr(scanner.catalog, "assign", assigning)
    row = scan(library_id)
    assert len(seen) == 8
    assert [done for done, _total in seen] == sorted(done for done, _total in seen)
    assert all(done <= total for done, total in seen) and len({total for _done, total in seen}) == 1
    # The two books that waited are put together at the end: the line is at the end of the files and goes on from there.
    last = max(seen)
    assert len(putting) == 8 and putting[-1][1] > last[1] and all(done <= total for done, total in putting)
    assert [done for done, _total in putting] == sorted(done for done, _total in putting)
    assert row.progress_done == 0 and row.progress_total == 0
    # The five single books, and the last of the three that waited for each other (the other two at the end).
    assert row.report["early"] == 6


def test_a_book_that_has_its_twin_is_not_taken_from_it_when_it_is_read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from .bookmaker import make_pdf

    folder = books_folder("twin")
    pdf = make_pdf(folder / "Dan Example" / "Zeichen - Dan Example.pdf", title="Zeichen", author="Martin Wegener")
    age(pdf)
    # A recording of the same book as the information of the PDF has it (the author being another): the twin of the PDF.
    recording(folder, "Wegener/zeichen/zeichen.m4b", 600.0, title="Zeichen", album="Zeichen", artist="Martin Wegener")
    # And the recording the new rules would join the PDF to.
    recording(folder, "Dan Example/zeichen/zeichen.m4b", 600.0, title="Zeichen", album="Zeichen", artist="Dan Example")
    library_id = make_library(folder)
    monkeypatch.setattr(scanner.catalog, "_pdf_against_the_path", lambda path, authors, title: (authors, title))
    scan(library_id)
    monkeypatch.undo()
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    with SessionLocal() as db:
        pdf_work = db.scalar(select(Edition.work_id).where(Edition.kind == "ebook"))
        wegener = db.scalar(select(Edition.work_id).join(BookFile, BookFile.edition_id == Edition.id).where(
            BookFile.path.like("Wegener/%")))
        assert pdf_work == wegener, "the PDF stays with its twin"
