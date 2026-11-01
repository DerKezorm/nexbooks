"""The order of changes the iPhone app follows: ``/api/app/1/library`` with its marker.

What is held fast here: **every way a change is written moves the marker** (the scan, a correction, joining and
splitting works, moving an edition into another library, removing a library, filing an uploaded book, reading
everything again, the rights of a library, a plain statement past all the code), **what changes nothing the app is told
moves nothing**, **what went or is out of sight comes in ``removed``, and only to who had it**, **a marker that is too
old is 410**, and **nothing is lost while others write**, also between the two reads of a look.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from app import clock
from app import db as database
from app.db import SessionLocal, engine
from app.models import AppChanges, AppGone, AppSeen, Edition, Library, Person, Series, Work
from app.services import app_changes, libraries

from .app_helpers import Shelf, app_of, device_token, make_shelf, sync, sync_all, titles
from .conftest import make_account, new_client
from .library_helpers import book, join, run_jobs, scan


@pytest.fixture
def shelf() -> Shelf:
    return make_shelf()


def latest() -> int:
    with SessionLocal() as db:
        return app_changes.latest(db)[0]


def write(statement: str, **values: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), values)


# --- The whole, then only what changed ----------------------------------------------------------------------------------


def test_the_library_whole_and_then_only_what_changed(shelf: Shelf) -> None:
    first = sync(shelf.ada_app)
    assert titles({item["id"]: item for item in first["items"]}) == ["Kite Season", "The Quiet Orchard"]
    assert first["removed"] == [] and first["marker"] == first["latest"] == latest() > 0
    orchard = next(item for item in first["items"] if item["title"] == "The Quiet Orchard")
    assert {e["kind"] for e in orchard["editions"]} == {"ebook", "audio"}
    # Nothing changed: the marker stands, however often the app looks, and a scan that finds nothing new moves nothing.
    assert sync(shelf.ada_app, first["marker"]) == {"items": [], "removed": [], "marker": first["marker"],
                                                    "latest": first["marker"]}
    scan(shelf.library)
    scan(shelf.private)
    assert latest() == first["marker"]
    assert sync(shelf.ada_app, first["marker"])["items"] == []


def test_the_numbers_are_given_once_and_never_shared() -> None:
    folder_shelf = make_shelf()
    with SessionLocal() as db:
        numbers = list(db.scalars(select(Work.change_seq)))
    assert len(numbers) == len(set(numbers)) and all(numbers)
    # A change for a whole library at once (a new member) still gives every work a number of its own.
    carol = make_account("carol")
    join(folder_shelf.library, carol.id, "read")
    with SessionLocal() as db:
        numbers = list(db.scalars(select(Work.change_seq)))
        top = app_changes.latest(db)[0]
    assert len(numbers) == len(set(numbers)) and max(numbers) <= top


#: Plain statements, past every line of code: each changes something the app is told about the work.
CHANGES = [
    "UPDATE editions SET title = 'The Quiet Orchard' || ' (new)' WHERE id = :edition",
    # Another edition of the work (a translation), without a file yet.
    ("INSERT INTO editions (work_id, library_id, kind, title, language, pages, created_at, updated_at) SELECT work_id, "
     "library_id, 'ebook', 'Der stille Obstgarten', 'de', 10, created_at, updated_at FROM editions WHERE id = :edition"),
    "UPDATE editions SET description = 'Another text' WHERE id = :edition",
    "UPDATE editions SET cover = '' WHERE id = :edition",
    "UPDATE editions SET missing_at = '2026-10-01 00:00:00.000000' WHERE id = :edition",
    "UPDATE works SET title = 'Renamed' WHERE id = :work",
    "UPDATE works SET cover = '' WHERE id = :work",
    "UPDATE files SET size = size + 1 WHERE edition_id = :edition AND role = 'book'",
    "UPDATE files SET sha256 = 'f' WHERE edition_id = :edition AND role = 'book'",
    "UPDATE files SET missing_at = '2026-10-01 00:00:00.000000' WHERE edition_id = :edition AND role = 'book'",
    "DELETE FROM files WHERE edition_id = :edition AND role = 'book'",
    # Another format of the same edition, filed straight into it (an upgrade, a download of a second format).
    ("INSERT INTO files (library_id, edition_id, role, path, size, mtime_ns, sha256, format, part, duration_ms, unit, "
     "dirty, seen_at, created_at, rev) SELECT library_id, id, 'book', 'other.azw3', 10, 1, 'b', 'azw3', 1, 0, '', 0, "
     "created_at, created_at, 0 FROM editions WHERE id = :edition"),
    "UPDATE chapters SET title = 'Another' WHERE edition_id = :audio",
    "INSERT INTO chapters (edition_id, number, title, start_ms, target, depth) VALUES (:audio, 9, 'Late', 500000, '', 0)",
    "DELETE FROM chapters WHERE edition_id = :audio",
    "UPDATE people SET name = 'Mara L. Lindqvist' WHERE name = 'Mara Lindqvist'",
    "UPDATE people SET name = 'Iris V. Vale' WHERE name = 'Iris Vale'",
    "UPDATE series SET name = 'Orchard Stories'",
    "DELETE FROM credits WHERE edition_id = :edition",
    "UPDATE credits SET position = position + 1 WHERE edition_id = :edition",
]


@pytest.mark.parametrize("statement", CHANGES)
def test_every_change_the_app_is_told_moves_the_marker_whatever_writes_it(shelf: Shelf, statement: str) -> None:
    marker = sync(shelf.ada_app)["marker"]
    write(statement, edition=shelf.editions["The Quiet Orchard/ebook"], audio=shelf.editions["The Quiet Orchard/audio"],
          work=shelf.works["The Quiet Orchard"])
    changed = sync(shelf.ada_app, marker)
    orchard = shelf.works["The Quiet Orchard"]
    assert [item["id"] for item in changed["items"]] + changed["removed"] == [orchard], statement
    assert changed["marker"] == changed["latest"] > marker


def test_an_edition_that_goes_moves_its_work_even_with_nothing_hanging_on_it(shelf: Shelf) -> None:
    write("INSERT INTO editions (work_id, library_id, kind, title, language, pages, created_at, updated_at) SELECT "
          "work_id, library_id, 'ebook', 'Der stille Obstgarten', 'de', 10, created_at, updated_at FROM editions "
          "WHERE id = :edition", edition=shelf.editions["The Quiet Orchard/ebook"])
    marker = sync(shelf.ada_app)["marker"]
    write("DELETE FROM editions WHERE title = 'Der stille Obstgarten'")
    changed = sync(shelf.ada_app, marker)
    assert [item["id"] for item in changed["items"]] == [shelf.works["The Quiet Orchard"]]


def test_an_edition_that_goes_to_another_work_moves_the_work_it_left_too(shelf: Shelf) -> None:
    marker = sync(shelf.ada_app)["marker"]
    write("UPDATE editions SET work_id = :work WHERE id = :edition", work=shelf.works["The Quiet Orchard"],
          edition=shelf.editions["Kite Season/ebook"])
    changed = sync(shelf.ada_app, marker)
    # The work it left has no edition any more: it is gone for the app. The work it went to has one more.
    assert changed["removed"] == [shelf.works["Kite Season"]]
    assert [item["id"] for item in changed["items"]] == [shelf.works["The Quiet Orchard"]]


#: What the scan writes for itself, and what the app is never told: nothing of it moves the marker.
NOISE = [
    "UPDATE files SET seen_at = '2026-10-02 00:00:00.000000'",
    "UPDATE files SET dirty = 1",
    "UPDATE files SET rev = rev + 7",
    "UPDATE files SET probe = '{}'",
    "UPDATE files SET path = path || '.x' WHERE role = 'sidecar'",
    "UPDATE editions SET updated_at = '2026-10-02 00:00:00.000000'",
    "UPDATE works SET updated_at = '2026-10-02 00:00:00.000000'",
    "UPDATE works SET title = title",
    "UPDATE libraries SET scanned_at = '2026-10-02 00:00:00.000000', progress_done = 3",
    "UPDATE people SET sort_name = 'x'",
]


@pytest.mark.parametrize("statement", NOISE)
def test_what_the_app_is_not_told_moves_nothing(shelf: Shelf, statement: str) -> None:
    marker = sync(shelf.ada_app)["marker"]
    write(statement)
    assert latest() == marker, statement


# --- Every way of writing ---------------------------------------------------------------------------------------------


def test_a_new_book_found_by_the_scan_comes(shelf: Shelf) -> None:
    marker = sync(shelf.ada_app)["marker"]
    book(shelf.folder, "Nils Ek/Third.epub", title="The Third Door", authors=("Nils Ek",), language="en")
    scan(shelf.library)
    works, removed, _ = sync_all(shelf.ada_app, marker)
    assert titles(works) == ["The Third Door"] and removed == []


def test_a_correction_by_hand_moves_the_work(shelf: Shelf) -> None:
    operator = new_client(make_account("chief", "operator"))
    join(shelf.library, _account_id("chief"), "manage")
    marker = sync(shelf.ada_app)["marker"]
    edition = shelf.editions["Kite Season/ebook"]
    answer = operator.patch(f"/api/v1/editions/{edition}", json={"title": "Kite Summer"})
    assert answer.status_code == 200, answer.text
    works, _removed, _ = sync_all(shelf.ada_app, marker)
    assert titles(works) == ["Kite Summer"]


def _account_id(name: str) -> int:
    from app.models import Account

    with SessionLocal() as db:
        ident = db.scalar(select(Account.id).where(Account.name == name))
    assert ident is not None
    return ident


def test_joining_and_splitting_works_move_both_and_name_the_one_that_went(shelf: Shelf) -> None:
    chief = make_account("chief", "operator")
    join(shelf.library, chief.id, "manage")
    browser = new_client(chief)
    marker = sync(shelf.ada_app)["marker"]
    kite = shelf.editions["Kite Season/ebook"]
    joined = browser.post(f"/api/v1/editions/{kite}/join", json={"work_id": shelf.works["The Quiet Orchard"]})
    assert joined.status_code == 200, joined.text
    works, removed, marker = sync_all(shelf.ada_app, marker)
    assert removed == [shelf.works["Kite Season"]]
    assert [e["id"] for e in works[shelf.works["The Quiet Orchard"]]["editions"]].count(kite) == 1
    split = browser.post(f"/api/v1/editions/{kite}/split")
    assert split.status_code == 200, split.text
    works, removed, _ = sync_all(shelf.ada_app, marker)
    assert removed == []
    assert kite not in [e["id"] for e in works[shelf.works["The Quiet Orchard"]]["editions"]]
    assert any(kite in [e["id"] for e in item["editions"]] for item in works.values())


def test_moving_an_edition_into_a_library_the_person_is_not_in_removes_it_for_them(shelf: Shelf) -> None:
    owner = make_account("owner")
    join(shelf.library, owner.id, "manage")
    join(shelf.private, owner.id, "manage")
    owner_app = app_of(device_token(owner))
    browser = new_client(owner)
    ada_marker = sync(shelf.ada_app)["marker"]
    owner_marker = sync(owner_app)["marker"]
    kite = shelf.editions["Kite Season/ebook"]
    moved = browser.post(f"/api/v1/editions/{kite}/move", json={"library_id": shelf.private, "confirm": True})
    assert moved.status_code == 202, moved.text
    run_jobs()
    with SessionLocal() as db:
        edition = db.get(Edition, kite)
        assert edition is not None and edition.library_id == shelf.private
    works, removed, _ = sync_all(shelf.ada_app, ada_marker)
    assert works == {} and removed == [shelf.works["Kite Season"]]
    works, removed, _ = sync_all(owner_app, owner_marker)
    assert removed == [] and works[shelf.works["Kite Season"]]["library_ids"] == [shelf.private]


def test_a_right_taken_names_the_books_to_who_lost_them_and_to_nobody_else(shelf: Shelf) -> None:
    ada_marker = sync(shelf.ada_app)["marker"]
    ben_marker = sync(shelf.ben_app)["marker"]
    with SessionLocal() as db:
        library = db.get(Library, shelf.private)
        assert library is not None
        libraries.change_members(db, library, [libraries.MemberChange(shelf.ben.id, "read", None)])
    works, removed, _ = sync_all(shelf.ben_app, ben_marker)
    assert works == {} and removed == [shelf.works["A Secret Shelf"]]
    # Ada was never in the library: she does not even hear the number of its book.
    works, removed, _ = sync_all(shelf.ada_app, ada_marker)
    assert works == {} and removed == []
    # Given back, it comes again.
    with SessionLocal() as db:
        library = db.get(Library, shelf.private)
        assert library is not None
        libraries.change_members(db, library, [libraries.MemberChange(shelf.ben.id, None, "read")])
    works, removed, _ = sync_all(shelf.ben_app, ben_marker)
    assert titles(works) == ["A Secret Shelf"]


def test_a_new_member_gets_the_books_of_the_library_and_a_change_of_right_moves_them(shelf: Shelf) -> None:
    marker = sync(shelf.ada_app)["marker"]
    join(shelf.private, shelf.ada.id, "read")
    works, _removed, marker = sync_all(shelf.ada_app, marker)
    assert titles(works) == ["A Secret Shelf"]
    with SessionLocal() as db:
        library = db.get(Library, shelf.private)
        assert library is not None
        libraries.change_members(db, library, [libraries.MemberChange(shelf.ada.id, "read", "manage")])
    works, _removed, _ = sync_all(shelf.ada_app, marker)
    assert titles(works) == ["A Secret Shelf"]


def test_removing_a_library_and_deleting_a_work_name_the_works_that_went(shelf: Shelf) -> None:
    marker = sync(shelf.ben_app)["marker"]
    ada_marker = sync(shelf.ada_app)["marker"]
    with SessionLocal() as db:
        library = db.get(Library, shelf.private)
        assert library is not None
        libraries.remove(db, library)
    works, removed, marker = sync_all(shelf.ben_app, marker)
    assert works == {} and removed == [shelf.works["A Secret Shelf"]]
    # Ada never had the book: she does not hear the number of a work that went from a library she was never in.
    assert sync_all(shelf.ada_app, ada_marker)[:2] == ({}, [])
    with SessionLocal() as db:
        assert db.get(AppGone, shelf.works["A Secret Shelf"]) is not None
    write("DELETE FROM works WHERE id = :work", work=shelf.works["Kite Season"])
    works, removed, _ = sync_all(shelf.ben_app, marker)
    assert works == {} and removed == [shelf.works["Kite Season"]]


def test_reading_everything_again_moves_what_it_puts_right(shelf: Shelf) -> None:
    edition = shelf.editions["Kite Season/ebook"]
    # As if an older rule had read the book: the next "read everything again" puts the title right.
    write("UPDATE editions SET title = 'Kite Seasn' WHERE id = :edition", edition=edition)
    marker = sync(shelf.ada_app)["marker"]
    browser = new_client(make_account("chief", "operator"))
    join(shelf.library, _account_id("chief"), "manage")
    answer = browser.post(f"/api/v1/libraries/{shelf.library}/scan", json={"everything": True})
    assert answer.status_code == 202, answer.text
    run_jobs()
    works, _removed, _ = sync_all(shelf.ada_app, marker)
    # Reading everything again writes the chapters anew, so other books may come too; the one put right comes for sure.
    assert works[shelf.works["Kite Season"]]["title"] == "Kite Season"


def test_a_book_filed_from_an_upload_moves_the_work_it_joins(operator: Any, net: Any, tmp_path: Path) -> None:
    from .bookmaker import make_epub, make_mp3
    from .filing_helpers import world_with, zip_of
    from .procure_search_helpers import AUTHOR, TITLE

    world = world_with(net, lambda folder: make_epub(folder / "falkner" / "laternen.epub", title=TITLE,
                                                     authors=(AUTHOR,), language="de"))
    reader = app_of(device_token(world.person))
    marker = sync(reader)["marker"]
    parts = {f"CD 1/{number:02d}.mp3": make_mp3(tmp_path / f"{number}.mp3", seconds=3.0, title=TITLE, album=TITLE,
                                               artist=AUTHOR, track=number).read_bytes() for number in (1, 2)}
    upload = world.client.post("/api/v1/uploads", json={"library_id": world.library_id, "work_id": world.work_id,
                                                       "kind": "audio"}).json()
    sent = world.client.put(f"/api/v1/uploads/{upload['id']}/files", params={"path": "book.zip"},
                            content=zip_of(tmp_path / "book.zip", parts).read_bytes(),
                            headers={"content-type": "application/octet-stream"})
    assert sent.status_code == 201, sent.text
    assert world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={}).status_code == 202
    run_jobs()
    works, removed, _ = sync_all(reader, marker)
    assert removed == [] and list(works) == [world.work_id]
    assert {e["kind"] for e in works[world.work_id]["editions"]} == {"ebook", "audio"}


# --- Pages ------------------------------------------------------------------------------------------------------------


def test_pages_of_one_lose_nothing_even_inside_a_change_of_a_whole_library(shelf: Shelf) -> None:
    for number in range(5):
        book(shelf.folder, f"Extra/{number}.epub", title=f"Extra Book {number}", authors=("Pia Hold",), language="en")
    scan(shelf.library)
    whole, _removed, marker = sync_all(shelf.ada_app, 0, limit=1)
    assert len(whole) == 7
    # One statement gives all of them a number (Ben joins once more): every one comes, one page at a time.
    with SessionLocal() as db:
        library = db.get(Library, shelf.library)
        assert library is not None
        libraries.change_members(db, library, [libraries.MemberChange(shelf.ben.id, "read", "manage")])
    again, _removed, _ = sync_all(shelf.ada_app, marker, limit=2)
    assert sorted(again) == sorted(whole)


# --- Too old, too new ---------------------------------------------------------------------------------------------------


def test_a_marker_older_than_what_was_forgotten_or_never_given_is_410(shelf: Shelf, monkeypatch: pytest.MonkeyPatch,
                                                                      ) -> None:
    marker = sync(shelf.ben_app)["marker"]
    assert shelf.ben_app.get("/api/app/1/library", params={"after": marker + 1}).status_code == 410
    write("DELETE FROM works WHERE id = :work", work=shelf.works["Kite Season"])
    # 89 days later the removal is still there; 91 days later it is forgotten, and the old marker is too old.
    start = clock.now()
    with SessionLocal() as db:
        assert app_changes.prune(db, start + timedelta(days=89)) == 0
    with SessionLocal() as db:
        assert app_changes.prune(db, start + timedelta(days=91)) == 1
    refused = shelf.ben_app.get("/api/app/1/library", params={"after": marker})
    assert refused.status_code == 410 and refused.json()["detail"]["code"] == "marker_too_old"
    # From nothing it works, and from the new marker on too.
    fresh = sync(shelf.ben_app)
    assert titles({item["id"]: item for item in fresh["items"]}) == ["A Secret Shelf", "The Quiet Orchard"]
    assert sync(shelf.ben_app, fresh["marker"])["items"] == []


def test_a_restored_database_sends_every_device_back_to_the_start(shelf: Shelf) -> None:
    from app.services import backups

    name = backups.create(kind=backups.MANUAL).name
    edition = shelf.editions["Kite Season/ebook"]
    for number in range(3):
        write("UPDATE editions SET title = :title WHERE id = :edition", title=f"Kite {number}", edition=edition)
    # The device knows the state after the backup.
    _works, _removed, marker = sync_all(shelf.ada_app)
    backups.stage_restore(name)
    assert backups.apply_pending() is True
    with SessionLocal() as db:
        assert app_changes.after_restore(db) is True
        assert app_changes.after_restore(db) is False
        assert db.scalar(select(AppSeen.work_id)) is None
    # The restored database counts on; even once it is past the device's marker, it does not answer it as if it were
    # the database the device knew.
    for number in range(5):
        write("UPDATE editions SET title = :title WHERE id = :edition", title=f"Kite again {number}", edition=edition)
    assert latest() > marker
    refused = shelf.ada_app.get("/api/app/1/library", params={"after": marker})
    assert refused.status_code == 410, refused.text
    assert titles({item["id"]: item for item in sync(shelf.ada_app)["items"]}) == ["Kite again 4", "The Quiet Orchard"]


# --- Others writing at the same time ----------------------------------------------------------------------------------


def test_a_change_that_lands_between_the_two_reads_of_a_look_comes_with_the_next(shelf: Shelf,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    marker = sync(shelf.ada_app)["marker"]
    edition = shelf.editions["Kite Season/ebook"]

    def another_request_writes() -> None:
        write("UPDATE editions SET title = 'Kite Winter' WHERE id = :edition", edition=edition)

    monkeypatch.setattr(app_changes, "_between_reads", another_request_writes)
    first = sync(shelf.ada_app, marker)
    monkeypatch.setattr(app_changes, "_between_reads", lambda: None)
    assert first["items"] == [] and first["marker"] == marker
    second = sync(shelf.ada_app, first["marker"])
    assert [item["title"] for item in second["items"]] == ["Kite Winter"]


def test_writers_at_the_same_time_leave_no_gap(shelf: Shelf) -> None:
    for number in range(6):
        book(shelf.folder, f"Crowd/{number}.epub", title=f"Crowd {number}", authors=("Ola Sand",), language="en")
    scan(shelf.library)
    seen, _removed, marker = sync_all(shelf.ada_app)
    with SessionLocal() as db:
        targets = list(db.scalars(select(Edition.id).where(Edition.title.like("Crowd %"))))
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer(edition: int) -> None:
        try:
            for round_number in range(15):
                write("UPDATE editions SET title = :title WHERE id = :edition",
                      title=f"Crowd {edition} round {round_number}", edition=edition)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(edition,)) for edition in targets]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        works, _gone, marker = sync_all(shelf.ada_app, marker, limit=2)
        seen.update(works)
    stop.set()
    works, _gone, marker = sync_all(shelf.ada_app, marker)
    seen.update(works)
    assert errors == []
    with SessionLocal() as db:
        truth = {edition.work_id: edition.title for edition in db.scalars(select(Edition).where(Edition.id.in_(targets)))}
    assert {ident: seen[ident]["title"] for ident in truth} == truth


# --- The schema step ----------------------------------------------------------------------------------------------------


def test_the_step_to_schema_19_numbers_the_works_and_makes_the_same_triggers_as_a_new_database(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import create_engine

    from app.services import backups

    from .test_schema import RECORDS

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((RECORDS / "v18.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (3, 'Old', 'x', 'x'), "
                           "(8, 'Older', 'x', 'x')")
        connection.execute("PRAGMA user_version = 18")
        connection.commit()
    scratch = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", scratch)
    monkeypatch.setattr(backups, "create", lambda **_kwargs: path)
    database.init_db()
    scratch.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id, change_seq FROM works ORDER BY id").fetchall() == [(3, 3), (8, 8)]
        assert connection.execute("SELECT id, seq, floor FROM app_changes").fetchall() == [(1, 8, 0)]
        # The same triggers as a new database: a restore of this database is not refused for them.
        assert database._foreign_objects(connection) == []
        triggers = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        made = {name for name in database._reference()[1] if name.startswith("app_")}
        assert len(made) == 21 and made <= triggers
        connection.execute("UPDATE works SET title = 'Old, read again' WHERE id = 3")
        assert connection.execute("SELECT change_seq FROM works WHERE id = 3").fetchone() == (9,)


def test_the_row_that_counts_comes_back_when_it_is_gone(shelf: Shelf) -> None:
    write("DELETE FROM app_changes")
    write("UPDATE editions SET title = 'Kite Again' WHERE id = :edition", edition=shelf.editions["Kite Season/ebook"])
    with SessionLocal() as db:
        assert db.scalar(select(AppChanges.seq)) == 1


def test_people_and_series_renamed_move_only_their_works(shelf: Shelf) -> None:
    marker = sync(shelf.ada_app)["marker"]
    with SessionLocal() as db:
        person = db.scalar(select(Person).where(Person.name == "Jonas Brandauer"))
        assert person is not None
        person.name = "Jonas B. Brandauer"
        db.commit()
    works, _removed, marker = sync_all(shelf.ada_app, marker)
    assert titles(works) == ["Kite Season"]
    with SessionLocal() as db:
        series = db.scalar(select(Series))
        assert series is not None
        series.name = "Orchard Stories"
        db.commit()
    works, _removed, _ = sync_all(shelf.ada_app, marker)
    assert titles(works) == ["The Quiet Orchard"]
    assert works[shelf.works["The Quiet Orchard"]]["series"]["name"] == "Orchard Stories"
