"""The recognition of audiobooks, each with the case that showed it: advertising in a title
that made a book "sure" as another book and moved it into a stranger's work, an ISBN of a source taken for the file's
own, a release note as a description, a folder of a series as an author, the places and marks of editions that become
one, a player that still reports for an edition that went, a cover stored twice at once, the questions to Audible, and
the numbers of the matching per library. Invented books, names and numbers throughout."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, Bookmark, Edition, EditionMove, Job, Library, MatchEntry, Progress
from app.services import covers, merge, scanner, settings_service
from app.services.match import review
from app.services.match import run as matching

from .conftest import make_account
from .fake_sources import audible_product
from .library_helpers import book, books_folder, make_library, recording, run_jobs, scan
from .match_helpers import World, entries, member
from .test_recognition_pen_names import dnb_xml
from .test_scanner import state

AUTHOR = "Lena Vorlage"
BLURB_TITLE = ("Das stille Moor – Ein Fall für Hanna: Thriller | Auftakt der neuen Cold-Case-Serie von "
               f"Nr.1-Bestsellerautor {AUTHOR} (German Edition)")
DNB_BLURB = f"Psychothriller | Nervenkitzel pur von Nr.1-Bestsellerautor {AUTHOR}"


# ---- A case seen on a real library ------------------------------------------------------------------------------


def blurb_library(world: World, name: str) -> int:
    """Two e-books of one author: a book of its own, and one whose title a shop filled with advertising. The National
    Library knows the first one only, with advertising in its subtitle and an ISBN."""
    world.only("dnb")
    world.fake.dnb = dnb_xml([("D1", "Das tiefe Wasser", DNB_BLURB, "Vorlage, Lena", "9783000000010")])
    folder = books_folder(name)
    book(folder, f"{AUTHOR}/Das tiefe Wasser/a.epub", title="Das tiefe Wasser", authors=(AUTHOR,), language="de",
         date="2021-01-01")
    book(folder, f"{AUTHOR}/Das stille Moor/b.epub", title=BLURB_TITLE, authors=(AUTHOR,), language="de",
         date="2022-03-01")
    library_id = make_library(folder, name=name)
    member(library_id)
    scan(library_id)
    return library_id


def test_a_title_full_of_advertising_is_neither_matched_to_another_book_nor_moved_into_its_work(
        client: TestClient, operator: Account, world: World) -> None:
    blurb_library(world, "blurb")
    titles = sorted(work["title"] for work in state()["works"])
    assert titles == ["Das stille Moor – Ein Fall für Hanna", "Das tiefe Wasser"], titles
    found = {title: (state_, reason) for title, state_, reason, _decision in entries()}
    assert found["Das stille Moor – Ein Fall für Hanna"][0] in ("open", "maybe")
    # The book of its own is known to one catalogue alone and has no ISBN in its file: a proposal, not sure.
    assert found["Das tiefe Wasser"] == ("maybe", "one_source")
    with SessionLocal() as db:
        assert db.scalars(select(Edition.isbn)).all() == ["", ""]
        assert db.scalars(select(Edition.series_name)).all() == ["", ""]
    # Matched again by the same rules, nothing changes.
    with SessionLocal() as db:
        matching.rematch_all(db)
    run_jobs()
    assert sorted(work["title"] for work in state()["works"]) == titles


def test_an_isbn_a_source_gave_is_never_the_files_proof(client: TestClient, operator: Account, world: World) -> None:
    """Two sources made a book sure and the National Library gave it an ISBN. Asked again with one of them away, the
    ISBN the source gave is no ISBN of the file: the book is not "sure by the same ISBN"."""
    world.only("dnb", "audible")
    world.fake.dnb = dnb_xml([("D1", "Das tiefe Wasser", "Roman", "Vorlage, Lena", "9783000000010")])
    world.fake.audible = {"de": [audible_product("B0EXAMPL01", "Das tiefe Wasser", [AUTHOR], 600)]}
    folder = books_folder("source-isbn")
    book(folder, f"{AUTHOR}/Das tiefe Wasser/a.epub", title="Das tiefe Wasser", authors=(AUTHOR,), language="de")
    library_id = make_library(folder, name="Isbn")
    member(library_id)
    scan(library_id)
    assert entries()[0][1] == "sure"
    with SessionLocal() as db:
        assert db.scalar(select(Edition.isbn)) == "9783000000010"
    world.only("dnb")
    with SessionLocal() as db:
        matching.rematch_all(db)
    run_jobs()
    (_title, state_, reason, _decision) = entries()[0]
    assert state_ == "maybe" and reason == "one_source", (state_, reason)
    with SessionLocal() as db:
        entry = db.scalar(select(MatchEntry))
        assert entry is not None
        assert all("isbn_match" not in proposal["why"] for proposal in entry.proposals or [])


# ---- A release note is no description; a source's description beats a comment tag ------------------------------


REAL_TEXT = ("Ein Mann verschwindet spurlos aus einem Dorf am Meer. Kommissarin Lindqvist ermittelt in einem Fall, "
             "der sie an ihre Grenzen bringt.")
SOURCE_TEXT = ("Die Kommissarin sucht einen Mann, der aus einem Dorf am Meer verschwunden ist. Ein Fall, der sie weit "
               "zurück in die Vergangenheit führt.")


def test_a_release_note_in_a_comment_tag_is_no_description(client: TestClient, operator: Account) -> None:
    folder = books_folder("release-note")
    recording(folder, "Mara Lindqvist/Nachtglas/01.mp3", seconds=5.0, album="Nachtglas", artist="Mara Lindqvist",
              comment="Upped by TeamXY - enjoy and support the author - encoded with LAME 3.99", track=1)
    scan(make_library(folder))
    with SessionLocal() as db:
        assert db.scalar(select(Edition.description)) == ""


def test_the_description_of_a_source_goes_before_a_comment_tag_but_not_before_the_package_of_an_ebook(
        client: TestClient, operator: Account, world: World) -> None:
    world.only("audible")
    world.fake.audible = {"de": [audible_product("B0EXAMPL01", "Nachtglas", ["Mara Lindqvist"], 1,
                                                 summary=SOURCE_TEXT)]}
    folder = books_folder("comment-tag")
    recording(folder, "Mara Lindqvist/Nachtglas/01.mp3", seconds=60.0, album="Nachtglas", artist="Mara Lindqvist",
              comment=REAL_TEXT, track=1)
    book(folder, "Mara Lindqvist/Nachtglas/text.epub", title="Nachtglas", authors=("Mara Lindqvist",), language="de",
         description=REAL_TEXT)
    library_id = make_library(folder)
    member(library_id)
    scan(library_id)
    with SessionLocal() as db:
        by_kind = dict(db.execute(select(Edition.kind, Edition.description)).all())
    assert by_kind["audio"] == SOURCE_TEXT
    assert by_kind["ebook"] == REAL_TEXT


# ---- A folder of a series whose books name other authors is none --------------------------------------------


def test_a_top_folder_whose_books_name_other_people_is_no_author(client: TestClient, operator: Account) -> None:
    folder = books_folder("top-series")
    recording(folder, "Perry Nebel/Der Turm/01.mp3", seconds=5.0, album="Der Turm", track=1)
    recording(folder, "Perry Nebel/Das Haus/01.mp3", seconds=5.0, album="Das Haus", artist="Kai Ostermann", track=1)
    recording(folder, "Mara Lindqvist/Nachtglas/01.mp3", seconds=5.0, album="Nachtglas", track=1)
    scan(make_library(folder))
    authors = {work["title"]: work["authors"] for work in state()["works"]}
    assert authors["Der Turm"] == []
    assert authors["Das Haus"] == ["Kai Ostermann"]
    assert authors["Nachtglas"] == ["Mara Lindqvist"]


# ---- Places and marks of editions that become one ----------------------------------------------------


def two_recordings(name: str) -> tuple[int, int]:
    folder = books_folder(name)
    recording(folder, "a/Erstes/01.mp3", seconds=120.0, album="Erstes", artist="Mara Lindqvist", track=1)
    recording(folder, "b/Zweites/01.mp3", seconds=120.0, album="Zweites", artist="Mara Lindqvist", track=1)
    scan(make_library(folder))
    with SessionLocal() as db:
        first, second = db.scalars(select(Edition.id).order_by(Edition.id)).all()
    return first, second


def test_marks_move_whole_also_when_one_moves_onto_the_old_place_of_another(client: TestClient,
                                                                           operator: Account) -> None:
    first, _second = two_recordings("marks-move")
    person = make_account("hoerer", "member")
    with SessionLocal() as db:
        for seconds in (10, 70):
            db.add(Bookmark(user_id=person.id, edition_id=first, locator=f"time:{seconds}.000", fraction=0.1))
        db.commit()
        edition = db.get(Edition, first)
        assert edition is not None
        edition.duration = 400
        merge.shift_places(db, edition, 60_000, [])
        db.commit()
        marks = sorted(float(row.locator.removeprefix("time:")) for row in db.scalars(select(Bookmark)))
    assert marks == [70.0, 130.0]


def test_the_places_and_marks_of_an_edition_that_goes_reach_the_right_people_moved(client: TestClient,
                                                                                  operator: Account) -> None:
    survivor_id, other_id = two_recordings("absorb")
    one, two = make_account("eins", "member"), make_account("zwei", "member")
    moment = clock.now()
    with SessionLocal() as db:
        db.add(Progress(user_id=one.id, edition_id=other_id, locator="time:10.000", fraction=0.1, unit="time",
                        moved_at=moment, received_at=moment))
        db.add(Progress(user_id=two.id, edition_id=survivor_id, locator="time:30.000", fraction=0.2, unit="time",
                        moved_at=moment, received_at=moment))
        db.add(Bookmark(user_id=one.id, edition_id=other_id, locator="time:5.000", fraction=0.1, note="hier"))
        # The same moment in both: the place further on in the book stays.
        db.add(Progress(user_id=two.id, edition_id=other_id, locator="time:40.000", fraction=0.3, unit="time",
                        moved_at=moment, received_at=moment))
        db.commit()
        survivor, other = db.get(Edition, survivor_id), db.get(Edition, other_id)
        assert survivor is not None and other is not None
        survivor.duration = 240
        merge.absorb(db, survivor, other, 120_000, [])
        db.commit()
        places = {row.user_id: (row.edition_id, row.locator) for row in db.scalars(select(Progress))}
        (mark,) = db.scalars(select(Bookmark)).all()
        move = db.get(EditionMove, other_id)
    assert places[one.id] == (survivor_id, "time:130.000")
    assert places[two.id] == (survivor_id, "time:160.000")
    assert (mark.user_id, mark.edition_id, mark.locator, mark.note) == (one.id, survivor_id, "time:125.000", "hier")
    assert move is not None and (move.new_id, move.shift_ms) == (survivor_id, 120_000)


def test_a_player_that_reports_for_an_edition_that_went_is_heard_by_the_one_that_stayed(
        client: TestClient, operator: Account) -> None:
    survivor_id, other_id = two_recordings("follow")
    with SessionLocal() as db:
        library_id = db.scalar(select(Edition.library_id).where(Edition.id == survivor_id))
        survivor, other = db.get(Edition, survivor_id), db.get(Edition, other_id)
        assert survivor is not None and other is not None and library_id is not None
        survivor.duration = 240
        merge.absorb(db, survivor, other, 120_000, [])
        db.commit()
    member(library_id)
    answer = client.put(f"/api/v1/editions/{other_id}/progress", json={
        "locator": "time:5.000", "fraction": 0.05, "unit": "time", "chapter": 1, "chapter_count": 1,
        "chapter_title": "", "device_key": "d1", "device": "Test", "age": 0})
    assert answer.status_code == 200, answer.text
    assert answer.json()["progress"]["edition_id"] == survivor_id
    assert answer.json()["progress"]["locator"] == "time:125.000"
    # Nobody else is heard through it: an edition the caller does not see stays not found.
    assert client.put("/api/v1/editions/999999/progress", json={
        "locator": "time:5.000", "fraction": 0.05, "unit": "time", "chapter": 1, "chapter_count": 1,
        "chapter_title": "", "device_key": "d1", "device": "Test", "age": 0}).status_code == 404


# ---- One cover stored twice at once -------------------------------------------------------------------------------


def test_one_cover_stored_by_several_requests_at_once_is_stored_once_without_a_fault() -> None:
    picture = b"RIFF" + bytes(range(256)) * 4
    names: list[str] = []
    faults: list[BaseException] = []
    start = threading.Barrier(4)

    def store() -> None:
        start.wait()
        try:
            names.append(covers.save(picture))
        except BaseException as exc:  # noqa: BLE001
            faults.append(exc)

    threads = [threading.Thread(target=store) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert faults == [] and len(set(names)) == 1
    assert covers.path_of(names[0]) is not None
    assert not list(covers.folder().glob("*.tmp"))


def test_a_cover_whose_replacing_fails_because_it_is_there_already_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    picture = b"RIFF" + bytes(range(200)) * 3
    real_replace = covers.os.replace

    def busy(source: Any, target: Any) -> None:
        real_replace(source, target)
        raise PermissionError("in use")

    monkeypatch.setattr(covers.os, "replace", busy)
    name = covers.save(picture)
    assert covers.path_of(name) is not None


# ---- An unknown book costs Audible two questions at most ---------------------------------------------------------


def test_a_book_audible_does_not_know_costs_two_questions_at_most(client: TestClient, operator: Account,
                                                                  world: World) -> None:
    world.only("audible")
    folder = books_folder("audible-unknown")
    # The package says one title, the folder another: the book is asked for by both.
    book(folder, "Mara Lindqvist/Der Turm (2020)/x.epub", title="Turmgeschichten", authors=("Mara Lindqvist",),
         language="de")
    book(folder, "Mara Lindqvist/y.epub", title="Ganz Unbekannt", authors=("Mara Lindqvist",), language="de")
    library_id = make_library(folder)
    member(library_id)
    scan(library_id)
    assert world.fake.count("api.audible.") <= 4, world.fake.urls("api.audible.")
    asked = world.fake.urls("api.audible.")
    assert sum("Turm" in url for url in asked) <= 2
    assert sum("Unbekannt" in url for url in asked) <= 2
    world.fake.log.clear()
    with SessionLocal() as db:
        matching.rematch_all(db)
    run_jobs()
    assert world.fake.count("api.audible.") == 0, "what was asked is kept"


# ---- The numbers of the matching, per library -------------------------------------------------------------------


def test_the_numbers_of_the_matching_are_per_library(client: TestClient, operator: Account, world: World) -> None:
    world.only()
    folder_a, folder_b = books_folder("count-a"), books_folder("count-b")
    book(folder_a, "x/a.epub", title="Eins", authors=("Mara Lindqvist",), language="de")
    for number in range(3):
        book(folder_b, f"x/{number}.epub", title=f"Buch {number}", authors=("Mara Lindqvist",), language="de")
    library_a, library_b = make_library(folder_a, name="A"), make_library(folder_b, name="B")
    scan(library_a)
    scan(library_b)
    with SessionLocal() as db:
        assert review.progress_of(db, [library_a]) == [(library_a, 1, 1)]
        assert sorted(review.progress_of(db, [library_a, library_b])) == sorted([(library_a, 1, 1), (library_b, 3, 3)])
        assert review.progress_of(db, []) == []


# ---- Putting the books together again by newer rules ------------------------------------------------------------


def test_newer_rules_of_putting_books_together_are_applied_once_without_reading_a_file(
        client: TestClient, operator: Account) -> None:
    folder = books_folder("reassemble")
    book(folder, f"{AUTHOR}/Das stille Moor/b.epub", title=BLURB_TITLE, authors=(AUTHOR,), language="de")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        # What an older version made of it.
        edition = db.scalar(select(Edition))
        assert edition is not None
        edition.title = "Bestsellerautor Lena Vorlage (German Edition)"
        settings_service.save(db, {"assemble_rev": 0})
        db.commit()
        assert scanner.reassemble_if_due(db)
        assert not scanner.reassemble_if_due(db), "once"
        assert db.scalar(select(Job.id).where(Job.kind == "scan", Job.ref == library_id)) is not None
    run_jobs()
    with SessionLocal() as db:
        assert db.scalar(select(Edition.title)) == "Das stille Moor – Ein Fall für Hanna"
        assert db.scalar(select(Library.rev)) == scanner.READ_REV


def test_a_library_kept_from_being_read_by_itself_is_not_read_for_the_newer_rules(client: TestClient,
                                                                                operator: Account) -> None:
    folder = books_folder("reassemble-off")
    book(folder, "x/a.epub", title="Eins", authors=("Mara Lindqvist",), language="de")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        library = db.get(Library, library_id)
        assert library is not None
        library.scan_enabled = False
        settings_service.save(db, {"assemble_rev": 0})
        db.commit()
        assert scanner.reassemble_if_due(db)
        assert db.scalar(select(Job.id).where(Job.kind == "scan", Job.ref == library_id,
                                              Job.state == "queued")) is None
