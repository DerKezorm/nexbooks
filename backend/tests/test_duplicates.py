"""Editions a library holds twice are named under "Braucht dich" for the people who look after it, and nothing is removed
by it: the same files in two folders, a box that holds a volume again, two recordings of one book that run as long, two
files of one book as large to the byte. A note that was read is not made again; a pair that is gone takes its note."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BookFile, Edition, ProcureNotice, TrashEntry
from app.services import duplicates, settings_service

from .conftest import make_account, new_client
from .filing_helpers import files_in
from .library_helpers import age_all, book, books_folder, join, make_library, recording, scan
from .match_helpers import member


def notes(client: TestClient) -> list[dict[str, Any]]:
    answer = client.get("/api/v1/procure/problems")
    assert answer.status_code == 200, answer.text
    return [note for note in answer.json()["notes"] if note["code"] == "duplicate"]


def shelf(name: str, *, writing: bool = True) -> tuple[Path, int]:
    """A library with a member. ``writing``: the operator switched tidying on, so nexbooks may write in it."""
    folder = books_folder(name)
    library = make_library(folder, name.title())
    member(library)
    if writing:
        with SessionLocal() as db:
            settings_service.save(db, {"organize_enabled": True})
    return folder, library


def test_a_file_an_edition_holds_twice_goes_to_the_trash_by_itself(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-files")
    first = book(folder, "Ada Feldkamp/Die Nebelwacht/nebelwacht.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",),
                 language="de")
    (folder / "Moorland Box" / "Die Nebelwacht").mkdir(parents=True)
    shutil.copy2(first, folder / "Moorland Box" / "Die Nebelwacht" / "nebelwacht.epub")
    age_all(folder)
    scan(library)
    assert notes(client) == []
    with SessionLocal() as db:
        [left] = list(db.scalars(select(BookFile).where(BookFile.role == "book")))
        assert left.path == "Ada Feldkamp/Die Nebelwacht/nebelwacht.epub"
        [entry] = list(db.scalars(select(TrashEntry)))
        assert [item["path"] for item in entry.files] == ["Moorland Box/Die Nebelwacht/nebelwacht.epub"]
    assert not (folder / "Moorland Box" / "Die Nebelwacht" / "nebelwacht.epub").exists()


def test_the_copy_a_program_named_goes_and_the_book_stays(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-named")
    first = book(folder, "Ada Feldkamp/Die Nebelwacht (2011) (2).epub", title="Die Nebelwacht",
                 authors=("Ada Feldkamp",), language="de")
    shutil.copy2(first, folder / "Ada Feldkamp" / "Die Nebelwacht (2011).epub")
    age_all(folder)
    scan(library)
    with SessionLocal() as db:
        [left] = list(db.scalars(select(BookFile).where(BookFile.role == "book")))
        assert left.path == "Ada Feldkamp/Die Nebelwacht (2011).epub"


def test_a_recording_that_lies_there_twice_runs_as_long_as_it_is(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-parts")
    for number in (1, 2, 3):
        made = recording(folder, f"Ada Feldkamp/Die Nebelwacht/0{number} Die Nebelwacht.mp3", seconds=4.0,
                         album="Die Nebelwacht", artist="Ada Feldkamp", title=f"Kapitel {number}", track=number)
        shutil.copy2(made, folder / "Ada Feldkamp" / "Die Nebelwacht" / f"Die Nebelwacht - {number}.mp3")
    age_all(folder)
    scan(library)
    with SessionLocal() as db:
        rows = list(db.scalars(select(BookFile).where(BookFile.role == "book")))
        assert sorted(Path(row.path).name for row in rows) == [f"0{number} Die Nebelwacht.mp3" for number in (1, 2, 3)]
        [edition] = list(db.scalars(select(Edition)))
        assert 10 <= edition.duration <= 14


def test_two_discs_that_look_alike_stay(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-discs")
    for disc in (1, 2):
        for number in (1, 2):
            recording(folder, f"Ada Feldkamp/Die Nebelwacht/CD{disc}/0{number} Die Nebelwacht.mp3", seconds=2.0 + number,
                      album="Die Nebelwacht", artist="Ada Feldkamp", title=f"Kapitel {number}", track=number)
    age_all(folder)
    scan(library)
    with SessionLocal() as db:
        assert list(db.scalars(select(TrashEntry))) == []
    assert len(files_in(folder)) == 4


def test_a_part_a_recording_holds_more_than_once_stays(client: TestClient, operator: Account) -> None:
    """The same jingle at the head of every disc is no copy."""
    folder, library = shelf("twice-jingle")
    jingle = recording(folder, "Ada Feldkamp/Die Nebelwacht/01 Jingle.mp3", seconds=2.0, album="Die Nebelwacht",
                       artist="Ada Feldkamp", title="Jingle", track=1)
    recording(folder, "Ada Feldkamp/Die Nebelwacht/02 Kapitel.mp3", seconds=4.0, album="Die Nebelwacht",
              artist="Ada Feldkamp", title="Kapitel", track=2)
    shutil.copy2(jingle, folder / "Ada Feldkamp" / "Die Nebelwacht" / "03 Jingle.mp3")
    age_all(folder)
    scan(library)
    with SessionLocal() as db:
        assert list(db.scalars(select(TrashEntry))) == []
    assert len(files_in(folder)) == 3


def test_two_different_files_of_one_format_stay(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-other")
    book(folder, "Ada Feldkamp/Die Nebelwacht/a.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",), language="de")
    book(folder, "Ada Feldkamp/Die Nebelwacht (2010)/b.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",),
         language="de", description="Eine andere Ausgabe mit anderem Text.")
    age_all(folder)
    scan(library)
    with SessionLocal() as db:
        assert list(db.scalars(select(TrashEntry))) == []
    assert len(files_in(folder)) == 2


def two_books_in_one_edition(client: TestClient, name: str) -> tuple[Path, dict[str, Any]]:
    folder, library = shelf(name)
    book(folder, "Ada Feldkamp/Die Nebelwacht/a.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",), language="de")
    book(folder, "Ada Feldkamp/Die Nebelwacht (2010)/b.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",),
         language="de", description="Eine andere Ausgabe mit anderem Text.")
    age_all(folder)
    scan(library)
    [note] = notes(client)
    assert note["detail"]["why"] == "same_format"
    return folder, note


def test_a_person_chooses_which_of_two_files_goes(client: TestClient, operator: Account) -> None:
    folder, note = two_books_in_one_edition(client, "twice-choice")
    answer = client.post(f"/api/v1/procure/notes/{note['id']}/drop", json={"folder": note["detail"]["there"]})
    assert answer.status_code == 204, answer.text
    assert notes(client) == []
    assert client.get("/api/v1/procure/counts").json()["needs"] == 0
    with SessionLocal() as db:
        [left] = list(db.scalars(select(BookFile).where(BookFile.role == "book")))
        assert left.path.rpartition("/")[0] == note["detail"]["here"]
        [entry] = list(db.scalars(select(TrashEntry)))
        assert entry.edition_id == left.edition_id
    assert len(files_in(folder)) == 1


def test_only_the_two_folders_of_the_note_can_go(client: TestClient, operator: Account) -> None:
    folder, note = two_books_in_one_edition(client, "twice-other-folder")
    answer = client.post(f"/api/v1/procure/notes/{note['id']}/drop", json={"folder": "Ada Feldkamp"})
    assert answer.status_code == 422
    assert len(files_in(folder)) == 2


def test_a_read_note_counts_no_more(client: TestClient, operator: Account) -> None:
    _folder, note = two_books_in_one_edition(client, "twice-read")
    assert client.get("/api/v1/procure/counts").json()["needs"] == 1
    assert client.post(f"/api/v1/procure/notes/{note['id']}/acknowledge").status_code == 204
    assert client.get("/api/v1/procure/counts").json()["needs"] == 0


def test_a_copy_stays_when_the_file_that_would_stay_is_not_there(client: TestClient, operator: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    folder, library = shelf("twice-gone")
    first = book(folder, "Ada Feldkamp/Die Nebelwacht/nebelwacht.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",),
                 language="de")
    copy = folder / "Ada Feldkamp" / "Die Nebelwacht" / "Kopie.epub"
    shutil.copy2(first, copy)
    age_all(folder)
    with monkeypatch.context() as patch:
        patch.setattr(duplicates, "tidy", lambda _db, _library: 0)
        scan(library)
    # (``Kopie.epub`` comes first by its name: that is the one that would stay.)
    copy.unlink()
    with SessionLocal() as db:
        assert duplicates.tidy(db, library) == 0
        assert list(db.scalars(select(TrashEntry))) == []
    assert first.is_file()


def test_two_recordings_of_one_book_that_run_as_long_are_named(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-runtime")
    recording(folder, "Ada Feldkamp/Die Nebelwacht/nebelwacht.m4b", seconds=3600.0, album="Die Nebelwacht",
              artist="Ada Feldkamp", narrator="Ben Hollister")
    recording(folder, "Ada Feldkamp/Die Nebelwacht (Kopie)/nebelwacht.m4b", seconds=3600.6, album="Die Nebelwacht",
              artist="Ada Feldkamp", narrator="Ben Hollister", genre="Krimi")
    recording(folder, "Ada Feldkamp/Die Nebelwacht (gekürzt)/nebelwacht.m4b", seconds=1800.0, album="Die Nebelwacht",
              artist="Ada Feldkamp", narrator="Ben Hollister", genre="Roman")
    scan(library)
    [note] = notes(client)
    assert note["detail"]["why"] == "runtime"
    assert "gekürzt" not in note["detail"]["here"] + note["detail"]["there"]


def test_a_box_that_holds_a_volume_again_is_named_and_a_pair_that_went_takes_its_note(client: TestClient,
                                                                                      operator: Account) -> None:
    folder, library = shelf("twice-box")
    book(folder, "Ada Feldkamp/Die Nebelwacht.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",), language="de")
    book(folder, "Ada Feldkamp/Das Moorlicht.epub", title="Das Moorlicht", authors=("Ada Feldkamp",), language="de")
    scan(library)
    with SessionLocal() as db:
        rows = {row.edition_id: row for row in db.scalars(select(BookFile).where(BookFile.role == "book"))}
        box, single = (db.scalar(select(Edition.id).where(Edition.title == title)) for title in
                       ("Das Moorlicht", "Die Nebelwacht"))
        assert box is not None and single is not None
        # (The box holds the single volume's file and one more: what a box folder with the volumes in it gives.)
        db.add(BookFile(library_id=library, edition_id=box, path="Ada Feldkamp/Box/teil1.pdf", format="pdf",
                        size=rows[single].size, mtime_ns=rows[single].mtime_ns, sha256=rows[single].sha256,
                        role="book", unit=rows[box].unit))
        db.commit()
        assert duplicates.pairs(db, library) == {(min(box, single), max(box, single)): (box, "contains")}
        # (A note for each who looks after the library: the operator who made it, and tester.)
        assert duplicates.tell(db, library) == 2
        db.commit()
    [note] = notes(client)
    assert note["edition_id"] == box and note["detail"]["other_edition_id"] == single
    # The single volume goes into the trash: the pair is gone, and its note with it.
    assert client.post(f"/api/v1/editions/{single}/trash").status_code == 200
    with SessionLocal() as db:
        duplicates.tell(db, library)
        db.commit()
    assert notes(client) == []




def test_two_files_of_one_e_book_in_two_folders_are_named_and_only_for_who_looks_after_the_library(
        client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-format")
    book(folder, "Ada Feldkamp/Die Nebelwacht/nebelwacht.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",),
         language="de")
    book(folder, "Ada Feldkamp/Die Nebelwacht (Neuauflage)/nebelwacht.epub", title="Die Nebelwacht",
         authors=("Ada Feldkamp",), language="de", publisher="Example")
    reader = make_account("rob")
    join(library, reader.id, "acquire")
    scan(library)
    [note] = notes(client)
    assert note["detail"]["why"] == "same_format"
    assert {note["detail"]["here"], note["detail"]["there"]} == {"Ada Feldkamp/Die Nebelwacht",
                                                                  "Ada Feldkamp/Die Nebelwacht (Neuauflage)"}
    with new_client(reader) as rob:
        assert [note for note in rob.get("/api/v1/procure/problems").json()["notes"] if note["code"] == "duplicate"] \
            == []
    with SessionLocal() as db:
        assert reader.id not in {notice.user_id for notice in db.scalars(select(ProcureNotice))}


def test_two_recordings_of_one_book_as_large_to_the_byte_are_named(client: TestClient, operator: Account) -> None:
    folder, library = shelf("twice-size")
    recording(folder, "Ada Feldkamp/Die Nebelwacht/nebelwacht.m4b", seconds=3600.0, album="Die Nebelwacht",
              artist="Ada Feldkamp")
    recording(folder, "Ada Feldkamp/Die Nebelwacht (MP3)/nebelwacht.m4b", seconds=3000.0, album="Die Nebelwacht",
              artist="Ada Feldkamp", genre="Krimi")
    scan(library)
    with SessionLocal() as db:
        rows = list(db.scalars(select(BookFile).where(BookFile.role == "book")))
        assert len({db.get(Edition, row.edition_id).work_id for row in rows}) == 1  # type: ignore[union-attr]
        # Small files, or two of another size, are no pair; as large to the byte and large enough, they are one.
        assert duplicates.pairs(db, library) == {}
        for row in rows:
            row.size = duplicates.SIZE_MIN
        db.commit()
        assert [why for _about, why in duplicates.pairs(db, library).values()] == ["size"]


def _many_editions(library: int, count: int, *, same_file: bool, kind: str = "ebook", duration: int = 0) -> None:
    """``count`` editions of one work, each with one file: the same file (one checksum), or only files as large."""
    from app.models import Work

    with SessionLocal() as db:
        work = Work(title="Many")
        db.add(work)
        db.flush()
        for number in range(count):
            edition = Edition(work_id=work.id, library_id=library, kind=kind, title="Many", duration=duration)
            db.add(edition)
            db.flush()
            db.add(BookFile(library_id=library, edition_id=edition.id, role="book", path=f"copy-{number}/a.epub",
                            size=duplicates.SIZE_MIN, format="epub",
                            sha256=("ab" * 32) if same_file else f"{number:064x}"))
        db.commit()


@pytest.mark.parametrize("same_file", [True, False])
def test_a_library_of_copies_is_not_looked_at_pair_by_pair(client: TestClient, operator: Account,
                                                          monkeypatch: pytest.MonkeyPatch, same_file: bool) -> None:
    """Hundreds of editions of one file, or of files as large, give the pairs the library is told by and end the walk;
    they do not cost a look at every pair of them, which on every scan would be the square of the editions."""
    _folder, library = shelf("many-copies")
    count = 300
    _many_editions(library, count, same_file=same_file)
    looked = {"at": 0}
    for name in ("_sharing_files", "_as_good_as_equal"):
        real = getattr(duplicates, name)

        def counting(*args: Any, _real: Any = real) -> Any:
            for item in _real(*args):
                looked["at"] += 1
                yield item

        monkeypatch.setattr(duplicates, name, counting)
    with SessionLocal() as db:
        found = duplicates.pairs(db, library)
    assert len(found) == duplicates.MAX_PAIRS
    assert looked["at"] <= 2 * duplicates.MAX_PAIRS, f"{looked['at']} pairs looked at of {count * (count - 1) // 2}"


def test_recordings_within_a_second_of_each_other_are_found_without_a_look_at_every_pair(
        client: TestClient, operator: Account) -> None:
    _folder, library = shelf("near-runtime")
    from app.models import Work

    with SessionLocal() as db:
        work = Work(title="Near")
        db.add(work)
        db.flush()
        for number, seconds in enumerate((3600, 3601, 3603, 5000, 5000)):
            edition = Edition(work_id=work.id, library_id=library, kind="audio", title="Near", duration=seconds)
            db.add(edition)
            db.flush()
            db.add(BookFile(library_id=library, edition_id=edition.id, role="book", path=f"n{number}/a.m4b", size=10,
                            format="m4b", sha256=f"{number:064x}"))
        db.commit()
        ids = [edition.id for edition in db.scalars(select(Edition).order_by(Edition.id))]
        found = duplicates.pairs(db, library)
    assert found == {(ids[0], ids[1]): (ids[1], "runtime"), (ids[3], ids[4]): (ids[4], "runtime")}


def _a_file_held_twice(name: str, *, writing: bool) -> tuple[Path, int]:
    folder, library = shelf(name, writing=writing)
    first = book(folder, "Ada Feldkamp/Die Nebelwacht/nebelwacht.epub", title="Die Nebelwacht", authors=("Ada Feldkamp",),
                 language="de")
    (folder / "Moorland Box" / "Die Nebelwacht").mkdir(parents=True)
    shutil.copy2(first, folder / "Moorland Box" / "Die Nebelwacht" / "nebelwacht.epub")
    age_all(folder)
    return folder, library


def test_nothing_is_taken_out_of_a_library_nexbooks_was_not_given_to_write_in(client: TestClient,
                                                                              operator: Account) -> None:
    """Out of the box nexbooks only reads: with tidying off and the library no place for fetched books, a file held twice
    is named and stays, even if the folder is mounted so that nexbooks could write."""
    folder, library = _a_file_held_twice("read-only", writing=False)
    scan(library)
    with SessionLocal() as db:
        assert len(list(db.scalars(select(BookFile).where(BookFile.role == "book")))) == 2
        assert list(db.scalars(select(TrashEntry))) == []
        assert duplicates.tidy(db, library) == 0
    assert (folder / "Moorland Box" / "Die Nebelwacht" / "nebelwacht.epub").is_file()
    [note] = notes(client)
    assert note["detail"]["why"] == "same_files"


def test_a_library_where_fetched_books_go_is_tidied_without_the_switch(client: TestClient, operator: Account) -> None:
    """nexbooks writes there anyway (the books it fetches), so the copies in it go to the trash."""
    from app.services.procure import formats

    folder, library = _a_file_held_twice("target", writing=False)
    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "library": {"ebook": library, "audio": None}})
    scan(library)
    with SessionLocal() as db:
        assert len(list(db.scalars(select(TrashEntry)))) == 1
    assert not (folder / "Moorland Box" / "Die Nebelwacht" / "nebelwacht.epub").exists()


def test_the_switch_of_the_operator_tidies_every_library(client: TestClient, operator: Account) -> None:
    _folder, library = _a_file_held_twice("switch", writing=True)
    scan(library)
    with SessionLocal() as db:
        assert len(list(db.scalars(select(TrashEntry)))) == 1
