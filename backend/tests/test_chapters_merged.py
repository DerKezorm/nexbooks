"""A chapter that runs over several MP3 files is one chapter (``services/chapter_groups``, ``catalog.build_meta``): in
the database, on the surface of the app, after reading a library again, and without touching what a person saved."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app import clock
from app.db import SessionLocal
from app.models import BookFile, Bookmark, Chapter, Edition, Progress
from app.services import libraries, scanner, settings_service

from .app_helpers import app_of, device_token
from .conftest import make_account
from .library_helpers import books_folder, join, make_library, recording, run_jobs, scan

SECONDS = 6.0


def audiobook(name: str, titles: list[str], *, inner: dict[int, list[tuple[str, float]]] | None = None,
              artist: str = "Ada Feldkamp") -> Path:
    """A folder with one recording in parts, one MP3 for each title (the title tag), in the order of the list. ``inner``:
    the parts (counted from 0) that carry chapters of their own."""
    folder = books_folder(name)
    for number, title in enumerate(titles, start=1):
        recording(folder, f"{artist}/Salt Marsh/{number:02d}.mp3", SECONDS, title=title, album="Salt Marsh",
                  artist=artist, track=number, **({"chapters": inner[number - 1]} if inner and number - 1 in inner
                                                  else {}))
    return folder


def chapters_of() -> list[tuple[int, str, int]]:
    """``(number, title, start in milliseconds)`` of the one recording in the database."""
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.kind == "audio"))
        assert edition is not None
        return [(c.number, c.title, c.start_ms) for c in db.scalars(
            select(Chapter).where(Chapter.edition_id == edition.id).order_by(Chapter.number))]


def part_starts() -> list[int]:
    """Where each file of the recording starts on the line of all the parts, in milliseconds."""
    with SessionLocal() as db:
        rows = db.scalars(select(BookFile).where(BookFile.role == "book").order_by(BookFile.part)).all()
        starts, running = [], 0
        for row in rows:
            starts.append(running)
            running += int(row.duration_ms or 0)
        return starts


GERMAN_AND_ENGLISH = [
    "Prolog",
    "Kapitel 1 (1/3)", "Kapitel 1 (2/3)", "Kapitel 1 (3/3)",
    "Kapitel 2 - Teil 1", "Kapitel 2 - Teil 2",
    "Chapter 3 Part 1", "Chapter 3 Part 2",
    "Chapter 4 [1 of 2]", "Chapter 4 [2 of 2]",
    "Epilog",
]


def test_chapters_over_several_files_are_one_chapter_each_and_start_in_their_first_file() -> None:
    make_library(audiobook("chap-mixed", GERMAN_AND_ENGLISH), "Books", scan=True)
    starts = part_starts()
    assert len(starts) == 11
    assert chapters_of() == [
        (1, "Prolog", starts[0]), (2, "Kapitel 1", starts[1]), (3, "Kapitel 2", starts[4]),
        (4, "Chapter 3", starts[6]), (5, "Chapter 4", starts[8]), (6, "Epilog", starts[10])]


def test_stems_that_differ_and_numbers_that_jump_stay_chapters_of_their_own() -> None:
    titles = ["Kapitel 1 (1/2)", "Kapitel 2 (2/2)",           # two stems
              "The Quay (1/4)", "The Quay (2/4)", "The Quay (4/4)",   # a gap
              "The Mill (1/2)", "The Mill (1/2)",                   # a repeat
              "Lektion Teil 2", "Lektion Teil 1"]                   # backwards
    make_library(audiobook("chap-strict", titles), "Books", scan=True)
    starts = part_starts()
    assert [(title, start) for _n, title, start in chapters_of()] == [
        ("Kapitel 1 (1/2)", starts[0]), ("Kapitel 2 (2/2)", starts[1]),
        ("The Quay", starts[2]), ("The Quay (4/4)", starts[4]),
        ("The Mill (1/2)", starts[5]), ("The Mill (1/2)", starts[6]),
        ("Lektion Teil 2", starts[7]), ("Lektion Teil 1", starts[8])]


def test_the_same_title_on_consecutive_files_is_one_chapter() -> None:
    make_library(audiobook("chap-equal", ["The Quay", "the  quay", "The Mill", "The Quay"]), "Books", scan=True)
    starts = part_starts()
    assert chapters_of() == [(1, "The Quay", starts[0]), (2, "The Mill", starts[2]), (3, "The Quay", starts[3])]


def test_titles_that_are_the_name_of_the_book_are_not_put_together() -> None:
    folder = audiobook("chap-book", ["Salt Marsh"] * 4)
    library = make_library(folder, "Books", scan=True)
    starts = part_starts()
    # The title of the book on every file names no chapter: the parts have no name ("Teil 1" in the interface).
    assert chapters_of() == [(n, "", starts[n - 1]) for n in range(1, 5)]
    scan(library)
    assert len(chapters_of()) == 4


def test_the_parts_of_the_book_numbered_with_its_title_are_not_one_chapter() -> None:
    make_library(audiobook("chap-book-parts", [f"Salt Marsh - Teil {n}" for n in (1, 2, 3)]), "Books", scan=True)
    starts = part_starts()
    assert chapters_of() == [(n, f"Salt Marsh - Teil {n}", starts[n - 1]) for n in (1, 2, 3)]


def test_a_file_with_chapters_of_its_own_ends_a_run_and_is_left_as_it_is() -> None:
    inner = {2: [("Inside A", 0.0), ("Inside B", 3.0)]}
    titles = ["Kapitel 1 (1/3)", "Kapitel 1 (2/3)", "Whole", "Kapitel 1 (3/3)", "Kapitel 2"]
    make_library(audiobook("chap-inner", titles, inner=inner), "Books", scan=True)
    starts = part_starts()
    got = chapters_of()
    assert [(title, start) for _n, title, start in got[:1]] == [("Kapitel 1", starts[0])]
    # The chapters inside the third file keep their place on the line (the start of the file plus their own).
    assert [title for _n, title, _s in got[1:3]] == ["Inside A", "Inside B"]
    assert got[1][2] == starts[2] and abs(got[2][2] - (starts[2] + 3000)) <= 50
    assert [(title, start) for _n, title, start in got[3:]] == [("Kapitel 1 (3/3)", starts[3]),
                                                                ("Kapitel 2", starts[4])]


def test_the_app_gets_the_chapters_of_the_recording_with_the_same_shape_and_all_its_files() -> None:
    library = make_library(audiobook("chap-app", GERMAN_AND_ENGLISH), "Books", scan=True)
    ada = make_account("ada")
    join(library, ada.id, "read")
    client = app_of(device_token(ada))
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.kind == "audio"))
        assert edition is not None
        work_id = edition.work_id
    answer = client.get(f"/api/app/1/works/{work_id}")
    assert answer.status_code == 200, answer.text
    audio = next(e for e in answer.json()["editions"] if e["kind"] == "audio")
    assert [c["number"] for c in audio["chapters"]] == [1, 2, 3, 4, 5, 6]
    assert [c["title"] for c in audio["chapters"]] == ["Prolog", "Kapitel 1", "Kapitel 2", "Chapter 3", "Chapter 4",
                                                       "Epilog"]
    assert all(set(c) == {"number", "title", "start"} for c in audio["chapters"])
    assert [c["start"] for c in audio["chapters"]] == [s / 1000 for s in (
        part_starts()[i] for i in (0, 1, 4, 6, 8, 10))]
    assert len(audio["files"]) == 11 and [f["part"] for f in audio["files"]] == list(range(1, 12))


# ---- Books that are in already ---------------------------------------------------------------------------------


def _as_the_old_rules_had_it() -> None:
    """The state an earlier version left: one chapter for every file."""
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.kind == "audio"))
        assert edition is not None
        rows = db.scalars(select(BookFile).where(BookFile.role == "book").order_by(BookFile.part)).all()
        titles = [(row.probe or {}).get("title") or "" for row in rows]
        db.execute(delete(Chapter).where(Chapter.edition_id == edition.id))
        running = 0
        for number, (row, title) in enumerate(zip(rows, titles, strict=True), start=1):
            db.add(Chapter(edition_id=edition.id, number=number, title=title, start_ms=running))
            running += int(row.duration_ms or 0)
        db.commit()


def test_a_library_that_is_in_gets_the_new_chapters_with_the_next_putting_together() -> None:
    # Generation 3 is the one the versions before this had put their books together by.
    assert scanner.ASSEMBLE_REV >= 4
    make_library(audiobook("chap-old", GERMAN_AND_ENGLISH), "Books", scan=True)
    expected = chapters_of()
    _as_the_old_rules_had_it()
    assert len(chapters_of()) == 11
    with SessionLocal() as db:
        # An installation of the version before: it had put the books together by generation ASSEMBLE_REV - 1.
        settings_service.save(db, {"assemble_rev": scanner.ASSEMBLE_REV - 1})
        db.commit()
        assert scanner.reassemble_if_due(db) is True
    run_jobs()
    assert chapters_of() == expected
    with SessionLocal() as db:
        assert scanner.reassemble_if_due(db) is False


def test_read_everything_again_gives_the_new_chapters_too() -> None:
    library = make_library(audiobook("chap-again", GERMAN_AND_ENGLISH), "Books", scan=True)
    expected = chapters_of()
    _as_the_old_rules_had_it()
    with SessionLocal() as db:
        libraries.start_scan(db, library, everything=True)
    run_jobs()
    assert chapters_of() == expected


def test_a_plain_scan_leaves_the_chapters_alone_and_the_same_again() -> None:
    library = make_library(audiobook("chap-plain", GERMAN_AND_ENGLISH), "Books", scan=True)
    expected = chapters_of()
    scan(library)
    assert chapters_of() == expected


def test_what_a_person_saved_keeps_its_time_and_only_learns_its_chapter() -> None:
    library = make_library(audiobook("chap-saved", GERMAN_AND_ENGLISH), "Books", scan=True)
    ada = make_account("ada")
    join(library, ada.id, "read")
    starts = part_starts()
    # The second file of "Kapitel 1" is old chapter 3 of 11; a second into the sixth file (the first of "Kapitel 2"'s
    # second part, old chapter 6) is the other place.
    here = starts[2] / 1000 + 1.5
    mark_here = starts[5] / 1000 + 1.0
    stamp = clock.now() - timedelta(days=3)
    _as_the_old_rules_had_it()
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.kind == "audio"))
        assert edition is not None
        db.add(Progress(user_id=ada.id, edition_id=edition.id, locator=f"time:{here:.3f}", fraction=0.2, unit="time",
                        chapter=3, chapter_count=11, chapter_title="Kapitel 1 (2/3)", device_key="phone",
                        device="Ada's iPhone", moved_at=stamp, received_at=stamp))
        db.add(Bookmark(user_id=ada.id, edition_id=edition.id, locator=f"time:{mark_here:.3f}", fraction=0.5,
                        chapter_title="Kapitel 2 - Teil 2", note="a note"))
        db.commit()
        edition_id = edition.id
        settings_service.save(db, {"assemble_rev": scanner.ASSEMBLE_REV - 1})
        db.commit()
        scanner.reassemble_if_due(db)
    run_jobs()
    with SessionLocal() as db:
        place = db.get(Progress, (ada.id, edition_id))
        assert place is not None
        assert (place.locator, place.fraction, place.unit) == (f"time:{here:.3f}", 0.2, "time")
        assert (place.device_key, place.device) == ("phone", "Ada's iPhone")
        assert place.moved_at == stamp and place.received_at == stamp
        # Old chapter 3 of 11 is chapter 2 of 6 now, and has a name that is the chapter's.
        assert (place.chapter, place.chapter_count, place.chapter_title) == (2, 6, "Kapitel 1")
        marks = db.scalars(select(Bookmark).where(Bookmark.edition_id == edition_id)).all()
        assert [(m.locator, m.fraction, m.note, m.chapter_title) for m in marks] == [
            (f"time:{mark_here:.3f}", 0.5, "a note", "Kapitel 2 - Teil 2")]
