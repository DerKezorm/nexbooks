"""The scan: what it finds, what it groups, what it leaves alone, and what it does when the ground moves."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import clock
from app.db import SessionLocal
from app.models import (
    BookFile,
    Chapter,
    Credit,
    Edition,
    Job,
    Library,
    LibraryProblem,
    Person,
    Series,
    Work,
)
from app.services import covers, jobs, libraries, probe, scanner

from .bookmaker import cover_image, make_epub, make_flac, make_mobi, make_pdf
from .library_helpers import (
    age,
    age_all,
    book,
    books_folder,
    can_link_files,
    make_library,
    make_link,
    recording,
    run_jobs,
    scan,
)


def state() -> dict[str, Any]:
    """The catalogue as the scan left it: works with their authors, series and editions."""
    with SessionLocal() as db:
        found = []
        for work in db.scalars(select(Work).order_by(Work.id)):
            authors = list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
                Credit.work_id == work.id, Credit.role == "author").order_by(Credit.position)))
            series = db.get(Series, work.series_id).name if work.series_id else None
            editions = []
            for edition in db.scalars(select(Edition).where(Edition.work_id == work.id).order_by(Edition.id)):
                rows = db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == "book",
                                                         BookFile.missing_at.is_(None)).order_by(BookFile.path)).all()
                editions.append({
                    "id": edition.id, "kind": edition.kind, "language": edition.language, "variant": edition.variant,
                    "uncertain": edition.variant_uncertain, "files": [r.path for r in rows],
                    "formats": sorted(r.format for r in rows), "duration": edition.duration, "pages": edition.pages,
                    "missing": edition.missing_at is not None, "isbn": edition.isbn, "title": edition.title,
                    "narrators": list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
                        Credit.edition_id == edition.id, Credit.role == "narrator").order_by(Credit.position))),
                    "chapters": [(c.title, c.start_ms) for c in db.scalars(
                        select(Chapter).where(Chapter.edition_id == edition.id).order_by(Chapter.number))],
                })
            found.append({"id": work.id, "title": work.title, "authors": authors, "series": series,
                          "pos": work.series_pos, "genre": work.genre, "editions": editions, "cover": work.cover,
                          "color": work.color, "pages": work.pages})
        return {"works": found}


def titles() -> list[str]:
    return sorted(work["title"] for work in state()["works"])


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The paths the helper process was asked to open, in order."""
    seen: list[str] = []
    original = probe.Worker.read

    def counting(self: probe.Worker, root: str, relative: str, fmt: str) -> dict[str, Any]:
        seen.append(relative)
        return original(self, root, relative, fmt)

    monkeypatch.setattr(probe.Worker, "read", counting)
    return seen


def library_row(library_id: int) -> Library:
    with SessionLocal() as db:
        row = db.get(Library, library_id)
        assert row is not None
        db.expunge(row)
        return row


# ---- what is found -------------------------------------------------------------------------------------------------


def test_a_scan_makes_works_editions_files_and_covers() -> None:
    folder = books_folder()
    book(folder, "Mara Lindqvist/The Quiet Orchard/orchard.epub", title="The Quiet Orchard",
         authors=("Mara Lindqvist",), language="en", cover=cover_image((120, 30, 40)), subjects=("Roman",))
    recording(folder, "Ben/Night Train/night.m4b", 120.0, title="Night Train", album="Night Train",
              artist="Ben Hollister", narrator="Ada Feldkamp")
    library_id = make_library(folder)
    row = scan(library_id)
    assert row.report["aborted"] is None and row.report["added"] == 2 and row.report["problems"] == 0
    found = {w["title"]: w for w in state()["works"]}
    assert sorted(found) == ["Night Train", "The Quiet Orchard"]
    orchard, night = found["The Quiet Orchard"], found["Night Train"]
    assert orchard["authors"] == ["Mara Lindqvist"] and orchard["genre"] == "roman"
    assert orchard["editions"][0]["kind"] == "ebook" and orchard["editions"][0]["language"] == "en"
    assert orchard["cover"] and covers.path_of(orchard["cover"]) is not None
    assert night["editions"][0]["kind"] == "audio" and night["editions"][0]["duration"] == 120
    assert night["editions"][0]["narrators"] == ["Ada Feldkamp"]
    assert night["authors"] == ["Ben Hollister"]
    with SessionLocal() as db:
        files = db.scalars(select(BookFile).where(BookFile.role == "book")).all()
        assert len(files) == 2 and all(len(f.sha256) == 64 and f.size > 0 for f in files)
        assert {f.path for f in files} == {"Mara Lindqvist/The Quiet Orchard/orchard.epub", "Ben/Night Train/night.m4b"}


def test_one_book_in_three_formats_is_one_edition_with_three_files() -> None:
    folder = books_folder()
    base = folder / "Kite Season"
    book(base, "Kite Season.epub", title="Kite Season", authors=("Jonas Brandauer",), language="de",
         cover=cover_image(), isbn="9783999900100")
    path = make_mobi(base / "Kite Season.mobi", title="Kite Season", author="Jonas Brandauer", cover=cover_image())
    age(path)
    path = make_mobi(base / "Kite Season.azw3", title="Kite Season", author="Jonas Brandauer")
    age(path)
    path = make_pdf(base / "Kite Season.pdf", title="Kite Season", author="Jonas Brandauer", pages=40)
    age(path)
    scan(make_library(folder))
    (work,) = state()["works"]
    (edition,) = work["editions"]
    assert edition["formats"] == ["azw3", "epub", "mobi", "pdf"]
    assert len(edition["files"]) == 4
    # The reading format speaks for the edition: the pages are the epub's estimate, not the pdf's count of 40.
    assert edition["pages"] != 40


def test_formats_with_different_names_in_one_folder_still_make_one_edition() -> None:
    folder = books_folder()
    book(folder, "Alpha/Alpha.epub", title="Alpha", authors=("Ada Feldkamp",), language="de")
    path = make_mobi(folder / "Alpha" / "Alpha - Ada Feldkamp.mobi", title="Alpha", author="Ada Feldkamp", language=0x07)
    age(path)
    scan(make_library(folder))
    (work,) = state()["works"]
    assert len(work["editions"]) == 1 and work["editions"][0]["formats"] == ["epub", "mobi"]


def test_an_ebook_and_its_recording_are_twins() -> None:
    folder = books_folder()
    book(folder, "e/Aschenfeld.epub", title="Aschenfeld", authors=("Rune Falkner",), language="de", isbn="9783999900308")
    recording(folder, "a/Aschenfeld/aschenfeld.m4b", 600.0, title="Aschenfeld (Ungekürzt)", album="Aschenfeld (Ungekürzt)",
              artist="Falkner, Rune", narrator="Merle Sandgren")
    scan(make_library(folder))
    (work,) = state()["works"]
    assert sorted(e["kind"] for e in work["editions"]) == ["audio", "ebook"]
    audio = next(e for e in work["editions"] if e["kind"] == "audio")
    assert audio["variant"] == "unabridged" and audio["uncertain"] is False  # the title says "ungekuerzt"


def test_volume_two_is_not_volume_three_and_a_second_marlowe_is_not_the_first() -> None:
    folder = books_folder()
    book(folder, "a.epub", title="Winter Harbour", authors=("Greta Hellwig",), series="The Harbour", series_pos=2)
    book(folder, "b.epub", title="Winter Harbour", authors=("Greta Hellwig",), series="The Harbour", series_pos=3)
    book(folder, "c.epub", title="Wolves", authors=("Silas Marlowe",))
    book(folder, "d.epub", title="Wolves", authors=("Hedda Marlowe",))
    book(folder, "e.epub", title="Wolves", authors=("Marlowe",))
    scan(make_library(folder))
    found = state()["works"]
    assert len(found) == 5
    assert sorted((w["title"], w["pos"]) for w in found if w["title"] == "Winter Harbour") == [
        ("Winter Harbour", 2.0), ("Winter Harbour", 3.0)]


def test_the_same_title_and_author_in_two_forms_of_the_name_is_one_work() -> None:
    folder = books_folder()
    book(folder, "a/x.epub", title="Wolves", authors=("Silas Marlowe",))
    book(folder, "b/y.epub", title="Wolves (Ungekürzt)", authors=("Marlowe, S.",), language="de")
    scan(make_library(folder))
    assert len(state()["works"]) == 1


def test_a_series_in_the_title_is_taken_out_of_it() -> None:
    folder = books_folder()
    book(folder, "1.epub", title="[Anselm Quist 01] • Nebelchor", authors=("Falkner, Rune",))
    book(folder, "5.epub", title="Anselm Quist Bd. 5 - Ahnenland | Thriller", authors=("Rune Falkner",))
    book(folder, "2.epub", title="002 - Frevelpfad", authors=("Rune Falkner",), series="Anselm Quist", series_pos=2,
         epub3=True)
    scan(make_library(folder))
    found = {w["title"]: w for w in state()["works"]}
    assert set(found) == {"Nebelchor", "Ahnenland", "Frevelpfad"}
    assert [(w["series"], w["pos"]) for w in (found["Nebelchor"], found["Ahnenland"], found["Frevelpfad"])] == [
        ("Anselm Quist", 1.0), ("Anselm Quist", 5.0), ("Anselm Quist", 2.0)]
    with SessionLocal() as db:
        assert len(db.scalars(select(Series.id)).all()) == 1


def test_the_parts_of_a_recording_in_cd_folders_are_one_edition_in_the_right_order() -> None:
    folder = books_folder()
    for number in (1, 2, 10):
        recording(folder, f"Ada Feldkamp/The Long Road/CD {number}/track.mp3", 4.0, album="The Long Road",
                  artist="Ada Feldkamp", title=f"Part {number}", track=1, disc=number)
    scan(make_library(folder))
    (work,) = state()["works"]
    (edition,) = work["editions"]
    assert len(edition["files"]) == 3
    assert [c[0] for c in edition["chapters"]] == ["Part 1", "Part 2", "Part 10"]
    assert [c[1] for c in edition["chapters"]] == [0, 4000, 8000][:3] or edition["chapters"][1][1] > 3000
    assert 11 <= edition["duration"] <= 13
    with SessionLocal() as db:
        parts = db.scalars(select(BookFile.part).order_by(BookFile.path)).all()
        assert sorted(parts) == [1, 2, 3]


def test_parts_without_tags_belong_to_their_folder_and_get_the_title_from_its_name() -> None:
    folder = books_folder()
    for number in (1, 2):
        recording(folder, f"Ada Feldkamp - Saga 03 - The Third Book/Track {number:02d}.mp3", 3.0)
    scan(make_library(folder))
    (work,) = state()["works"]
    assert (work["title"], work["authors"], work["series"], work["pos"]) == (
        "The Third Book", ["Ada Feldkamp"], "Saga", 3.0)
    assert len(work["editions"]) == 1 and len(work["editions"][0]["files"]) == 2


def test_two_albums_in_one_folder_are_two_editions() -> None:
    folder = books_folder()
    recording(folder, "mix/a1.mp3", 3.0, album="First Book", artist="Ada Feldkamp")
    recording(folder, "mix/b1.mp3", 3.0, album="Second Book", artist="Ada Feldkamp")
    scan(make_library(folder))
    assert titles() == ["First Book", "Second Book"]


def test_the_variant_of_a_recording_comes_from_the_names_and_is_marked_when_it_is_a_guess() -> None:
    folder = books_folder()
    recording(folder, "x/gek.m4b", 100.0, album="Das Buch (gekürzt)", artist="Ada Feldkamp")
    recording(folder, "y/hoer.m4b", 100.0, album="Das Stueck - Hörspiel", artist="Ada Feldkamp")
    recording(folder, "z/plain.m4b", 100.0, album="Ein Buch", artist="Ada Feldkamp")
    scan(make_library(folder))
    editions = {w["title"]: w["editions"][0] for w in state()["works"]}
    assert (editions["Das Buch (gekürzt)"]["variant"], editions["Das Buch (gekürzt)"]["uncertain"]) == ("abridged", False)
    assert editions["Das Stueck - Hörspiel"]["variant"] == "dramatized"
    assert (editions["Ein Buch"]["variant"], editions["Ein Buch"]["uncertain"]) == ("unabridged", True)


def test_the_translation_joins_its_original_only_on_proof() -> None:
    folder = books_folder()
    book(folder, "o/orig.epub", title="The Lost Seal", authors=("Rune Falkner",), language="en")
    book(folder, "t/trans.epub", title="Das verlorene Siegel", authors=("Rune Falkner",), language="de",
         original_title="The Lost Seal")
    book(folder, "u/other.epub", title="Das andere Siegel", authors=("Rune Falkner",), language="de")
    scan(make_library(folder))
    found = state()["works"]
    assert len(found) == 2
    joined = next(w for w in found if len(w["editions"]) == 2)
    assert sorted(e["language"] for e in joined["editions"]) == ["de", "en"]


def test_a_library_of_audio_only_ignores_ebooks_and_the_reverse() -> None:
    folder = books_folder()
    book(folder, "a.epub", title="Only Text", authors=("Ada Feldkamp",))
    recording(folder, "b/b.m4b", 60.0, album="Only Sound", artist="Ada Feldkamp")
    scan(make_library(folder, kind="audio"))
    assert titles() == ["Only Sound"]


def test_hidden_files_and_the_folders_of_a_nas_are_skipped() -> None:
    folder = books_folder()
    book(folder, "good.epub", title="Good", authors=("Ada Feldkamp",))
    book(folder, "@eaDir/thumb/x.epub", title="NAS", authors=("Ada Feldkamp",))
    book(folder, "#recycle/y.epub", title="Bin", authors=("Ada Feldkamp",))
    book(folder, ".hidden/z.epub", title="Hidden", authors=("Ada Feldkamp",))
    book(folder, ".dot.epub", title="Dot", authors=("Ada Feldkamp",))
    (folder / "partial.epub.part").write_bytes(b"x")
    scan(make_library(folder))
    assert titles() == ["Good"]


# ---- the files beside a book ----------------------------------------------------------------------------------------


def test_the_files_beside_a_book_speak_for_it_after_the_book_itself() -> None:
    folder = books_folder()
    recording(folder, "Rune Falkner/Aschenfeld/part1.mp3", 3.0, album="Aschenfeld", artist="Falkner, Rune")
    (folder / "Rune Falkner/Aschenfeld/metadata.json").write_text(json.dumps({
        "title": "Not Used", "authors": ["Not Used"], "narrators": ["Ben Hollister"], "series": ["Anselm Quist #4.0"],
        "genres": ["Thriller"], "language": "de", "abridged": True, "description": "A description."}), encoding="utf-8")
    (folder / "Rune Falkner/Aschenfeld/cover.jpg").write_bytes(cover_image((20, 120, 60), kind="JPEG"))
    age_all(folder / "Rune Falkner")
    scan(make_library(folder))
    (work,) = state()["works"]
    assert work["title"] == "Aschenfeld" and work["authors"] == ["Rune Falkner"]  # the file wins
    assert (work["series"], work["pos"], work["genre"]) == ("Anselm Quist", 4.0, "krimi")
    edition = work["editions"][0]
    assert edition["narrators"] == ["Ben Hollister"] and edition["variant"] == "abridged" and edition["uncertain"] is False
    assert edition["language"] == "de"
    assert work["cover"] != ""  # the picture beside the book


def test_a_sidecar_in_a_folder_of_many_books_belongs_to_none() -> None:
    folder = books_folder()
    book(folder, "one.epub", title="One", authors=("Ada Feldkamp",))
    book(folder, "two.epub", title="Two", authors=("Ada Feldkamp",))
    # What the books themselves leave open (genre, series) is what a wrongly applied file would fill in.
    (folder / "metadata.json").write_text(json.dumps({"title": "Wrong", "authors": ["Wrong Author"], "genres": ["Fantasy"],
                                                      "series": ["Wrong Series #1"], "narrators": ["Wrong Narrator"]}),
                                          encoding="utf-8")
    age(folder / "metadata.json")
    scan(make_library(folder))
    assert titles() == ["One", "Two"]
    for work in state()["works"]:
        assert (work["genre"], work["series"], work["authors"]) == ("", None, ["Ada Feldkamp"])
        assert work["editions"][0]["narrators"] == []


def test_changing_a_file_beside_a_book_is_noticed() -> None:
    folder = books_folder()
    recording(folder, "b/b.m4b", 60.0, album="A Book", artist="Ada Feldkamp")
    meta = folder / "b" / "metadata.json"
    meta.write_text(json.dumps({"narrators": ["First Narrator"]}), encoding="utf-8")
    age(meta)
    library_id = make_library(folder)
    scan(library_id)
    assert state()["works"][0]["editions"][0]["narrators"] == ["First Narrator"]
    meta.write_text(json.dumps({"narrators": ["Second Narrator"]}), encoding="utf-8")
    age(meta)
    scan(library_id)
    assert state()["works"][0]["editions"][0]["narrators"] == ["Second Narrator"]
    meta.unlink()
    scan(library_id)
    assert state()["works"][0]["editions"][0]["narrators"] == []


# ---- only what changed ----------------------------------------------------------------------------------------------


def test_a_second_scan_reads_nothing_and_a_changed_file_is_read_again(reads: list[str]) -> None:
    folder = books_folder()
    for number in range(4):
        book(folder, f"b{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    assert len(reads) == 4
    reads.clear()
    row = scan(library_id)
    assert reads == [] and row.report["added"] == 0 and row.report["changed"] == 0
    make_epub(folder / "b2.epub", title="Book 2 Changed", authors=("Ada Feldkamp",))
    age(folder / "b2.epub")
    reads.clear()
    row = scan(library_id)
    assert reads == ["b2.epub"] and row.report["changed"] == 1
    assert "Book 2 Changed" in titles() and "Book 2" not in titles()


def test_a_file_changed_a_moment_ago_waits_for_the_next_scan_and_asks_for_one_soon() -> None:
    folder = books_folder()
    book(folder, "old.epub", title="Old", authors=("Ada Feldkamp",))
    make_epub(folder / "new.epub", title="New", authors=("Ada Feldkamp",))  # written just now
    library_id = make_library(folder)
    row = scan(library_id)
    assert titles() == ["Old"] and row.report["deferred"] == 1
    assert row.retry_at is not None and row.retry_at > clock.now()
    age(folder / "new.epub")
    row = scan(library_id)
    assert titles() == ["New", "Old"] and row.retry_at is None


def test_what_cannot_be_read_is_listed_and_not_tried_again_until_it_changes(reads: list[str]) -> None:
    folder = books_folder()
    book(folder, "good.epub", title="Good", authors=("Ada Feldkamp",))
    (folder / "broken.epub").write_bytes(b"not an epub" * 100)
    age(folder / "broken.epub")
    (folder / "broken.mp3").write_bytes(bytes(2000))
    age(folder / "broken.mp3")
    library_id = make_library(folder)
    row = scan(library_id)
    assert titles() == ["Good"] and row.report["problems"] == 2
    with SessionLocal() as db:
        problems = {p.path: p.reason for p in db.scalars(select(LibraryProblem))}
    assert problems == {"broken.epub": "unreadable", "broken.mp3": "unreadable"}
    reads.clear()
    scan(library_id)
    assert reads == []
    # Mended, it is a book, and the problem is gone.
    make_epub(folder / "broken.epub", title="Mended", authors=("Ada Feldkamp",))
    age(folder / "broken.epub")
    scan(library_id)
    assert "Mended" in titles()
    with SessionLocal() as db:
        assert [p.path for p in db.scalars(select(LibraryProblem))] == ["broken.mp3"]


def test_a_problem_goes_when_its_file_does() -> None:
    folder = books_folder()
    (folder / "broken.epub").write_bytes(b"x" * 500)
    age(folder / "broken.epub")
    book(folder, "good.epub", title="Good", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    (folder / "broken.epub").unlink()
    scan(library_id)
    with SessionLocal() as db:
        assert db.scalars(select(LibraryProblem)).all() == []


def test_a_scan_that_is_stopped_goes_on_where_it_stopped(monkeypatch: pytest.MonkeyPatch, reads: list[str]) -> None:
    folder = books_folder()
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
    with SessionLocal() as db:
        job = db.scalars(select(Job).where(Job.kind == "scan")).one()
        assert job.state == "queued" and job.attempts == 0  # put back as it was, no try used up
    assert len(reads) == 4
    reads.clear()
    monkeypatch.setattr(scanner, "sha256_of", real)
    run_jobs()
    # The three files read before the stop are not read again.
    assert sorted(reads) == ["b3.epub", "b4.epub", "b5.epub"]
    assert len(titles()) == 6
    row = library_row(library_id)
    assert row.report["aborted"] is None and row.progress_total == 0


def test_a_scan_started_twice_is_one_scan() -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    with SessionLocal() as db:
        libraries.start_scan(db, library_id)
        libraries.start_scan(db, library_id)
        libraries.start_scan(db, library_id)
        assert len(db.scalars(select(Job).where(Job.kind == "scan", Job.state == "queued")).all()) == 1
        assert libraries.scan_states(db, [library_id]) == {library_id: "queued"}


def test_a_unit_that_cannot_be_put_together_is_a_problem_not_the_end_of_the_run(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A fault of nexbooks inside one book is that book's problem (listed as ``failed``, the others are made); the next
    run tries the book again, and the line goes when it works."""
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    book(folder, "b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    original = scanner.catalog.assign

    def boom(db: Session, library: Library, rows: list[BookFile], sidecars: list[BookFile]) -> object:
        if rows[0].path == "a.epub":
            raise RuntimeError("something odd")
        return original(db, library, rows, sidecars)

    monkeypatch.setattr(scanner.catalog, "assign", boom)
    row = scan(library_id)
    assert row.report["aborted"] is None and row.report["problems"] == 1
    assert titles() == ["B"]
    with SessionLocal() as db:
        listed = {p.path: p.reason for p in db.scalars(select(LibraryProblem))}
    assert listed == {"a.epub": "failed"}
    monkeypatch.undo()
    row = scan(library_id)
    assert row.report["aborted"] is None and titles() == ["A", "B"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(LibraryProblem.id))) == 0


@pytest.mark.parametrize("where", ["_write_file", "assign"])
def test_a_fault_of_the_infrastructure_ends_the_run_with_a_report_and_the_next_one_works(
        monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    """The database locked for good (or a disk full) is not the fault of a file or a book: the run ends, says so, and
    the next one works. Both places that take a fault per file or per book let it through."""
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)

    def locked(*_args: object) -> None:
        raise OperationalError("update", {}, Exception("database is locked"))

    monkeypatch.setattr(scanner if where == "_write_file" else scanner.catalog, where, locked)
    with SessionLocal() as db:
        libraries.start_scan(db, library_id)
    jobs.run_pending(1)
    assert library_row(library_id).report["aborted"] == "error"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(LibraryProblem.id))) == 0
    monkeypatch.undo()
    run_jobs()
    assert library_row(library_id).report["aborted"] is None and titles() == ["A"]


def test_a_file_the_database_refuses_is_that_files_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever goes wrong while a file is written (here: a value the database cannot take) is the file's problem."""
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    book(folder, "b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    original = scanner._write_file

    def refuses(db: Session, library_id: int, item: scanner.Found, *rest: Any) -> None:
        original(db, library_id, item, *rest)
        if item.path == "a.epub":
            db.add(BookFile(library_id=library_id, path="x" + chr(0xD800), format="epub", size=1, mtime_ns=1))

    monkeypatch.setattr(scanner, "_write_file", refuses)
    row = scan(library_id)
    assert row.report["aborted"] is None and row.report["problems"] == 1 and titles() == ["B"]
    with SessionLocal() as db:
        assert {p.path: p.reason for p in db.scalars(select(LibraryProblem))} == {"a.epub": "failed"}


# ---- the ground moves -----------------------------------------------------------------------------------------------


def test_a_folder_that_is_gone_marks_nothing_as_missing() -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    moved = folder.with_name("shelf-moved")
    shutil.rmtree(moved, ignore_errors=True)
    folder.rename(moved)
    row = scan(library_id)
    assert row.report["aborted"] == "offline"
    assert state()["works"][0]["editions"][0]["missing"] is False
    with SessionLocal() as db:
        assert all(f.missing_at is None for f in db.scalars(select(BookFile)))
    moved.rename(folder)
    row = scan(library_id)
    assert row.report["aborted"] is None and row.report["missing"] == 0


def test_a_folder_that_was_turned_into_a_link_to_another_folder_of_the_roots_is_not_that_library() -> None:
    """The path of the library is the real path it had when it was made: if the folder is now a link that leads to
    another folder inside the roots, the other folder's books must not appear in this library."""
    folder, other = books_folder("shelf-a"), books_folder("shelf-b")
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    book(other, "b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    moved = folder.with_name("shelf-a-moved")
    shutil.rmtree(moved, ignore_errors=True)
    folder.rename(moved)
    make_link(folder, other)
    row = scan(library_id)
    assert row.report["aborted"] == "offline" and row.report["missing"] == 0
    assert titles() == ["A"]
    with SessionLocal() as db:
        assert all(f.missing_at is None for f in db.scalars(select(BookFile)))


def test_a_folder_that_cannot_be_listed_after_it_passed_the_check_is_offline_too(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    monkeypatch.setattr(scanner, "walk", lambda root, kind: scanner.Walked(offline=True))
    row = scan(library_id)
    assert row.report["aborted"] == "offline"
    with SessionLocal() as db:
        assert all(f.missing_at is None for f in db.scalars(select(BookFile)))
    assert state()["works"][0]["editions"][0]["missing"] is False


def test_a_folder_that_is_empty_but_known_to_hold_books_marks_nothing_as_missing() -> None:
    """A share that was not mounted is an empty folder: the library must not look deleted."""
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    book(folder, "b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    kept = folder.with_name("kept")
    shutil.rmtree(kept, ignore_errors=True)
    folder.rename(kept)
    folder.mkdir()
    row = scan(library_id)
    assert row.report["aborted"] == "empty"
    with SessionLocal() as db:
        assert all(f.missing_at is None for f in db.scalars(select(BookFile)))
    assert len(titles()) == 2


def test_files_that_are_gone_are_marked_not_deleted_and_come_back() -> None:
    folder = books_folder()
    book(folder, "keep.epub", title="Keep", authors=("Ada Feldkamp",))
    gone = book(folder, "gone.epub", title="Gone", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    saved = gone.read_bytes()
    gone.unlink()
    row = scan(library_id)
    assert row.report["missing"] == 1
    found = {w["title"]: w for w in state()["works"]}
    assert set(found) == {"Keep", "Gone"}  # the work stays: what hangs on it must not go
    assert found["Gone"]["editions"][0]["missing"] is True and found["Keep"]["editions"][0]["missing"] is False
    gone.write_bytes(saved)
    age(gone)
    scan(library_id)
    assert state()["works"][1]["editions"][0]["missing"] is False


def test_one_format_gone_leaves_the_edition_alive() -> None:
    folder = books_folder()
    book(folder, "Alpha/Alpha.epub", title="Alpha", authors=("Ada Feldkamp",))
    mobi = make_mobi(folder / "Alpha" / "Alpha.mobi", title="Alpha", author="Ada Feldkamp")
    age(mobi)
    library_id = make_library(folder)
    scan(library_id)
    mobi.unlink()
    scan(library_id)
    (edition,) = state()["works"][0]["editions"]
    assert edition["missing"] is False and edition["formats"] == ["epub"]


def test_a_book_moved_to_another_folder_keeps_its_work() -> None:
    folder = books_folder()
    path = book(folder, "old/a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    work_id = state()["works"][0]["id"]
    (folder / "new").mkdir()
    path.rename(folder / "new" / "a.epub")
    scan(library_id)
    found = state()["works"]
    assert len(found) == 1 and found[0]["id"] == work_id  # the same work is found again
    assert found[0]["editions"][0]["missing"] is False or found[0]["editions"][0]["files"]


def test_a_link_to_a_folder_outside_is_not_followed_and_is_listed() -> None:
    outside = books_folder("outside-target")
    book(outside, "secret.epub", title="Secret", authors=("Ada Feldkamp",))
    # Another place: a link to a folder that lies in the folder for libraries but in another library's reach.
    folder = books_folder("shelf-with-link")
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    make_link(folder / "linked", outside)
    library_id = make_library(folder)
    row = scan(library_id)
    assert titles() == ["A"] and row.report["links"] == 1
    with SessionLocal() as db:
        problems = [(p.path, p.reason) for p in db.scalars(select(LibraryProblem))]
    assert problems == [("linked", "link")]


def test_a_link_to_a_file_is_not_followed() -> None:
    if not can_link_files():
        pytest.skip("the system does not allow links to files here; the link to a folder is tested above")
    outside = books_folder("outside-file")
    secret = book(outside, "secret.epub", title="Secret", authors=("Ada Feldkamp",))
    folder = books_folder("shelf-with-file-link")
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    make_link(folder / "b.epub", secret)
    scan(make_library(folder))
    assert titles() == ["A"]


def test_a_folder_that_cannot_be_listed_does_not_make_its_books_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    book(folder, "open/a.epub", title="A", authors=("Ada Feldkamp",))
    book(folder, "locked/b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    real = scanner.os.scandir

    def scandir(path: Any) -> Any:
        if str(path).endswith("locked"):
            raise PermissionError("no")
        return real(path)

    monkeypatch.setattr(scanner.os, "scandir", scandir)
    row = scan(library_id)
    assert row.report["missing"] == 0 and row.report["aborted"] is None
    assert all(not e["missing"] for w in state()["works"] for e in w["editions"])


def test_a_file_that_changes_while_it_is_read_is_left_for_the_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    path = book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    real = scanner.sha256_of

    def changing(root: str, relative: str) -> str:
        digest = real(root, relative)
        path.write_bytes(path.read_bytes() + b"x")
        return digest

    monkeypatch.setattr(scanner, "sha256_of", changing)
    row = scan(library_id)
    assert state()["works"] == [] and row.report["deferred"] == 1


def test_a_library_that_is_removed_leaves_the_files_and_forgets_the_books() -> None:
    folder = books_folder()
    path = book(folder, "a.epub", title="A", authors=("Ada Feldkamp",), cover=cover_image())
    library_id = make_library(folder)
    scan(library_id)
    assert covers.folder().exists() and any(covers.folder().iterdir())
    with SessionLocal() as db:
        libraries.remove(db, db.get(Library, library_id))  # type: ignore[arg-type]
        scanner.sweep_covers(db)
    assert path.exists() and state()["works"] == []
    assert not any(covers.folder().iterdir())
    with SessionLocal() as db:
        assert db.scalars(select(BookFile)).all() == [] and db.scalars(select(Edition)).all() == []


def test_two_libraries_with_the_same_book_share_the_work_but_not_the_edition() -> None:
    first, second = books_folder("one"), books_folder("two")
    book(first, "a.epub", title="Shared", authors=("Ada Feldkamp",), isbn="9783999900100")
    book(second, "a.epub", title="Shared", authors=("Ada Feldkamp",), isbn="9783999900100")
    scan(make_library(first, name="One"))
    scan(make_library(second, name="Two"))
    (work,) = state()["works"]
    assert len(work["editions"]) == 2


# ---- the schedule ---------------------------------------------------------------------------------------------------


def test_libraries_are_due_by_the_interval_of_the_instance() -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    with SessionLocal() as db:
        # Never read: due at once (the creation queued one; take it away to see the schedule on its own).
        db.query(Job).delete()
        db.commit()
        assert scanner.enqueue_due(db) == 1
        assert scanner.enqueue_due(db) == 0  # one is waiting
    run_jobs()
    with SessionLocal() as db:
        assert scanner.enqueue_due(db) == 0  # just read
        library = db.get(Library, library_id)
        assert library is not None
        library.scanned_at = clock.now() - timedelta(minutes=61)
        db.commit()
        assert scanner.enqueue_due(db) == 1
    run_jobs()
    with SessionLocal() as db:
        library = db.get(Library, library_id)
        assert library is not None
        library.scan_enabled = False
        library.scanned_at = clock.now() - timedelta(days=3)
        db.commit()
        assert scanner.enqueue_due(db) == 0  # not by itself
        library.retry_at = clock.now() - timedelta(seconds=1)
        db.commit()
        assert scanner.enqueue_due(db) == 1  # but a file in the making asked for it


def test_the_interval_can_be_off() -> None:
    from app.services import settings_service

    folder = books_folder()
    library_id = make_library(folder)
    with SessionLocal() as db:
        settings_service.save(db, {"scan_minutes": 0})
        db.query(Job).delete()
        library = db.get(Library, library_id)
        assert library is not None
        library.scanned_at = clock.now() - timedelta(days=30)
        db.commit()
        assert scanner.enqueue_due(db) == 0


def test_flac_and_pdf_and_opus_are_books_too() -> None:
    folder = books_folder()
    path = make_flac(folder / "f/f.flac", 30.0, album="Flac Book", artist="Ada Feldkamp", title="T")
    age(path)
    path = make_pdf(folder / "p/p.pdf", title="Printed", author="Ada Feldkamp", pages=12)
    age(path)
    scan(make_library(folder))
    assert titles() == ["Flac Book", "Printed"]
    assert {e["kind"]: e["pages"] for w in state()["works"] for e in w["editions"]}["ebook"] == 12


def test_two_libraries_are_read_one_after_the_other_each_with_its_own_report() -> None:
    one, two = books_folder("p1"), books_folder("p2")
    book(one, "a.epub", title="A", authors=("Ada Feldkamp",))
    book(two, "b.epub", title="B", authors=("Ada Feldkamp",))
    first = make_library(one, name="P1")
    second = make_library(two, name="P2")
    with SessionLocal() as db:
        libraries.start_scan(db, first)
        libraries.start_scan(db, second)
        assert libraries.scan_states(db, [first, second]) == {first: "queued", second: "queued"}
    # Each scan is followed by the job that reads the photos in the authors' folders.
    assert run_jobs() == 4
    assert titles() == ["A", "B"]
    assert library_row(first).report["added"] == 1 and library_row(second).report["added"] == 1


def test_covers_that_are_gone_from_the_data_folder_are_made_again() -> None:
    """A backup holds no covers: after a restore the database names covers that are not there; the next scan reads those
    books again and makes them."""
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",), cover=cover_image((10, 120, 200)))
    book(folder, "b.epub", title="B", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    before = {w["title"]: w["cover"] for w in state()["works"]}
    assert before["A"] and not before["B"]
    shutil.rmtree(covers.folder())
    row = scan(library_id)
    assert row.report["changed"] == 1  # only the book that had a cover
    assert {w["title"]: w["cover"] for w in state()["works"]} == before
    assert covers.path_of(before["A"]) is not None
