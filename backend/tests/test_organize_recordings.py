"""Tidying meets the recognition of recordings in real folders: the parts of a recording lie in ``CD 1``,
``Audio`` and ``Cover`` folders, a collection folder holds a book in every numbered folder, the folder names a book.
After a run, a recording is still the one edition it was, what people did with it stays (the place, the marks, the
finished passes, a decision on the matching, a correction by hand), and that holds when the library is read again from
scratch and when the run is undone. A run never sends recordings where the scan would put them together. All names and
tags are invented."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    ROLE_BOOK,
    Account,
    BookFile,
    Bookmark,
    Edition,
    HistoryEntry,
    LibraryMember,
    MatchEntry,
    Progress,
)
from app.services import libraries, settings_service
from app.services.organize import plan as planning

from .bookmaker import cover_image
from .conftest import make_account
from .library_helpers import age, book, books_folder, join, make_library, recording, run_jobs

WHEN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def parts(folder: Path, relative: str, numbers: range | list[int], *, seconds: float = 30.0, **tags: Any) -> None:
    for number in numbers:
        recording(folder, relative.format(number=number), seconds=seconds, track=number, **tags)


def picture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cover_image((40, 70, 110), (300, 300), "JPEG"))
    age(path)
    return path


def library_of(folder: Path, operator: Account) -> int:
    with SessionLocal() as db:
        settings_service.save(db, {"organize_enabled": True})
    library = make_library(folder, "Hören", scan=True)
    join(library, operator.id)
    run_jobs()
    return library


def preview(client: TestClient, library: int, scheme: str) -> dict[str, Any]:
    answer = client.post("/api/v1/organize/preview", json={"library_id": library, "scheme": scheme})
    assert answer.status_code == 201, answer.text
    return answer.json()


def moves(client: TestClient, run: int) -> list[dict[str, Any]]:
    page = client.get(f"/api/v1/organize/runs/{run}/moves", params={"offset": 0, "limit": 100}).json()
    assert page["total"] == len(page["items"])
    return page["items"]


def tidy(client: TestClient, library: int, scheme: str) -> dict[str, Any]:
    run = preview(client, library, scheme)
    assert client.post(f"/api/v1/organize/runs/{run['id']}/start", json={"confirm": True}).status_code == 202
    run_jobs()  # the run and the scan it asks for
    return run


def audio_editions() -> dict[int, list[str]]:
    """Every recording with the paths of its parts."""
    with SessionLocal() as db:
        found: dict[int, list[str]] = {}
        for edition_id in db.scalars(select(Edition.id).where(Edition.kind == "audio", Edition.missing_at.is_(None))):
            found[edition_id] = sorted(db.scalars(select(BookFile.path).where(
                BookFile.edition_id == edition_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))))
        return found


def what_hangs_on(edition_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == edition_id))
        edition = db.get(Edition, edition_id)
        assert edition is not None
        return {
            "progress": [(p.user_id, p.locator) for p in db.scalars(select(Progress).where(Progress.edition_id == edition_id))],
            "marks": [(m.user_id, m.locator, m.note) for m in db.scalars(select(Bookmark).where(Bookmark.edition_id == edition_id))],
            "history": [(h.user_id, h.finished_on, h.work_id == edition.work_id)
                        for h in db.scalars(select(HistoryEntry).where(HistoryEntry.edition_id == edition_id))],
            "decision": (entry.decision, entry.state) if entry is not None else None,
            "by_hand": {name: item.get("v") for name, item in (edition.overlay or {}).items()
                        if isinstance(item, dict) and item.get("by") == "hand"},
        }


def worked_on(edition_id: int, library: int, operator: Account) -> int:
    """A reader listened, set a mark, finished it; the operator decided the matching and corrected the series."""
    reader = make_account("hoerer")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library, user_id=reader.id, right="read"))
        db.add(Progress(user_id=reader.id, edition_id=edition_id, locator="time:75.000", fraction=0.6, unit="time",
                        chapter=1, chapter_count=1, chapter_title="", moved_at=WHEN, received_at=WHEN))
        db.add(Bookmark(user_id=reader.id, edition_id=edition_id, locator="time:40.000", fraction=0.3,
                        chapter_title="", note="Stelle"))
        db.add(HistoryEntry(user_id=reader.id, edition_id=edition_id, kind="listened", finished_on="2026-09-30",
                            source="hand", created_at=WHEN))
        entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == edition_id))
        if entry is None:
            entry = MatchEntry(library_id=library, edition_id=edition_id, created_at=WHEN, updated_at=WHEN)
            db.add(entry)
        entry.state, entry.decision, entry.decided_by, entry.decided_at = "sure", "corrected", operator.id, WHEN
        edition = db.get(Edition, edition_id)
        assert edition is not None
        edition.overlay = {**(edition.overlay or {}), "series": {"v": "Glas-Reihe", "by": "hand"}}
        db.commit()
    return reader.id


def read_everything_again(library: int) -> None:
    with SessionLocal() as db:
        libraries.start_scan(db, library, everything=True)
    run_jobs()


# ---- A recording in folders of discs and pictures --------------------------------------------------------------------


def disc_recording(name: str) -> Path:
    folder = books_folder(name)
    parts(folder, "Neu/Rune Falkner - Nachtglas/CD 1/{number:02d}.mp3", range(1, 3), album="Nachtglas MP3CD",
          artist="Rune Falkner")
    parts(folder, "Neu/Rune Falkner - Nachtglas/CD 2/{number:02d}.mp3", range(1, 3), album="Nachtglas MP3CD",
          artist="Rune Falkner")
    picture(folder / "Neu" / "Rune Falkner - Nachtglas" / "Cover" / "cover.jpg")
    return folder


def test_a_recording_on_discs_moves_whole_with_its_picture_folder_and_stays_one_edition(
        client: TestClient, operator: Account) -> None:
    folder = disc_recording("tidy-discs")
    library = library_of(folder, operator)
    ((edition_id, paths),) = audio_editions().items()
    assert len(paths) == 4
    reader = worked_on(edition_id, library, operator)
    before = what_hangs_on(edition_id)
    assert before["progress"] and before["marks"] and before["history"] and before["decision"] == ("corrected", "sure")

    run = preview(client, library, "{Author}/{Title}")
    found = {m["source"]: m["target"] for m in moves(client, run["id"])}
    # The discs keep their folders below the book's new folder, and the picture folder goes with them.
    assert found == {
        "Neu/Rune Falkner - Nachtglas/CD 1/01.mp3": "Rune Falkner/Nachtglas/CD 1/01.mp3",
        "Neu/Rune Falkner - Nachtglas/CD 1/02.mp3": "Rune Falkner/Nachtglas/CD 1/02.mp3",
        "Neu/Rune Falkner - Nachtglas/CD 2/01.mp3": "Rune Falkner/Nachtglas/CD 2/01.mp3",
        "Neu/Rune Falkner - Nachtglas/CD 2/02.mp3": "Rune Falkner/Nachtglas/CD 2/02.mp3",
        "Neu/Rune Falkner - Nachtglas/Cover/cover.jpg": "Rune Falkner/Nachtglas/Cover/cover.jpg",
    }
    assert client.post(f"/api/v1/organize/runs/{run['id']}/start", json={"confirm": True}).status_code == 202
    run_jobs()
    assert not (folder / "Neu").exists()
    assert (folder / "Rune Falkner" / "Nachtglas" / "Cover" / "cover.jpg").is_file()
    # One recording, the same edition, every part at its new place; what hangs on it is all there.
    assert audio_editions() == {edition_id: sorted(found[p] for p in paths)}
    assert what_hangs_on(edition_id) == before
    with SessionLocal() as db:
        # The picture is still the recording's: the scan gives it to the book of the folder above ``Cover``.
        side = db.scalar(select(BookFile).where(BookFile.path == "Rune Falkner/Nachtglas/Cover/cover.jpg"))
        assert side is not None and side.missing_at is None
        assert db.scalar(select(func.count()).select_from(Edition)) == 1

    # Read again from scratch, by the folder names it has now: still one recording with all that hangs on it.
    read_everything_again(library)
    assert audio_editions() == {edition_id: sorted(found[p] for p in paths)}
    assert what_hangs_on(edition_id) == before
    with SessionLocal() as db:
        edition = db.get(Edition, edition_id)
        assert edition is not None and edition.title == "Nachtglas" and edition.series_name == "Glas-Reihe"
        assert 119 <= edition.duration <= 121

    # Undone: back where it was, and still the one recording.
    assert client.post(f"/api/v1/organize/runs/{run['id']}/undo", json={"confirm": True}).status_code == 202
    run_jobs()
    assert audio_editions() == {edition_id: paths}
    assert (folder / "Neu" / "Rune Falkner - Nachtglas" / "Cover" / "cover.jpg").is_file()
    assert what_hangs_on(edition_id) == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(HistoryEntry).where(HistoryEntry.user_id == reader)) == 1


def test_a_picture_in_the_book_folder_goes_with_the_recording_in_its_audio_folder(
        client: TestClient, operator: Account) -> None:
    """``Titel/cover.jpg`` beside ``Titel/Audio`` and ``Titel/eBook``: the scan gives the picture to the recording (the
    only recording below), so the picture goes with it and the e-book goes alone."""
    folder = books_folder("tidy-beside")
    parts(folder, "Stapel/Sturmlicht (2017)/Audio/{number:02d}.mp3", range(1, 3), album="Sturmlicht",
          artist="Ada Feldkamp")
    book(folder, "Stapel/Sturmlicht (2017)/eBook/sturmlicht.epub", title="Sturmlicht", authors=("Ada Feldkamp",),
         language="de")
    picture(folder / "Stapel" / "Sturmlicht (2017)" / "cover.jpg")
    library = library_of(folder, operator)
    found = {m["source"]: (m["target"], m["state"]) for m in moves(client, preview(client, library, "{Author}/{Title}")["id"])}
    assert found["Stapel/Sturmlicht (2017)/cover.jpg"] == ("Ada Feldkamp/Sturmlicht/cover.jpg", "planned")
    assert found["Stapel/Sturmlicht (2017)/Audio/01.mp3"] == ("Ada Feldkamp/Sturmlicht/Audio/01.mp3", "planned")
    assert found["Stapel/Sturmlicht (2017)/eBook/sturmlicht.epub"] == ("Ada Feldkamp/Sturmlicht.epub", "planned")


def test_files_beside_an_ebook_and_a_recording_in_one_folder_keep_both_where_they_are(
        client: TestClient, operator: Account) -> None:
    """A picture the scan gives to both books of a folder cannot follow both: neither moves, and the preview says why."""
    folder = books_folder("tidy-shared")
    parts(folder, "Stapel/Sturmlicht/{number:02d}.mp3", range(1, 3), album="Sturmlicht", artist="Ada Feldkamp")
    book(folder, "Stapel/Sturmlicht/sturmlicht.epub", title="Sturmlicht", authors=("Ada Feldkamp",), language="de")
    picture(folder / "Stapel" / "Sturmlicht" / "cover.jpg")
    library = library_of(folder, operator)
    run = preview(client, library, "{Author}/{Title}")
    found = moves(client, run["id"])
    assert {m["source"] for m in found} == {"Stapel/Sturmlicht/01.mp3", "Stapel/Sturmlicht/02.mp3",
                                            "Stapel/Sturmlicht/sturmlicht.epub"}
    assert {(m["state"], m["reason"]) for m in found} == {("problem", "shares_beside")}
    assert run["summary"]["problem_books"] == 2 and run["summary"]["move_books"] == 0


# ---- Recordings never meet in one folder -----------------------------------------------------------------------------


def test_two_recordings_sent_to_one_new_folder_are_both_a_problem(client: TestClient, operator: Account) -> None:
    """Two copies of one recording in two folders (different file names): sent to one folder the scan would read them
    as one, so both stay."""
    folder = books_folder("tidy-two")
    parts(folder, "Eingang/erste/{number:02d}.mp3", [1, 2], album="Nachtglas", artist="Rune Falkner")
    parts(folder, "Eingang/zweite/{number:02d}.mp3", [3, 4], album="Nachtglas", artist="Rune Falkner")
    library = library_of(folder, operator)
    assert len(audio_editions()) == 2
    run = preview(client, library, "{Author}/{Title}")
    found = moves(client, run["id"])
    assert len(found) == 4 and {m["target"].rsplit("/", 1)[0] for m in found} == {"Rune Falkner/Nachtglas"}
    assert {(m["state"], m["reason"]) for m in found} == {("problem", "collision")}


def test_a_recording_is_never_sent_into_a_folder_that_holds_another_book(client: TestClient, operator: Account) -> None:
    folder = books_folder("tidy-taken")
    parts(folder, "Rune Falkner/Nachtglas/{number:02d}.mp3", [1, 2], album="Nachtglas", artist="Rune Falkner")
    parts(folder, "Eingang/kopie/{number:02d}.mp3", [3, 4], album="Nachtglas", artist="Rune Falkner")
    library = library_of(folder, operator)
    assert len(audio_editions()) == 2
    run = preview(client, library, "{Author}/{Title}")
    found = moves(client, run["id"])
    # The one that is there stays (not listed); the other one would join it in the scan, so it stays too.
    assert {m["source"]: (m["state"], m["reason"]) for m in found} == {
        "Eingang/kopie/03.mp3": ("problem", "folder_taken"), "Eingang/kopie/04.mp3": ("problem", "folder_taken")}
    assert run["summary"]["stay_books"] == 1


def test_a_recordings_folder_is_never_named_like_a_folder_of_parts() -> None:
    for base in ("Rune Falkner/CD 2", "Rune Falkner/Audio", "Rune Falkner/Cover", "Hörbuch"):
        assert planning._audio_folder_problem(base, "elsewhere", 1, {}) == "part_folder", base
    assert planning._audio_folder_problem("Rune Falkner/Nachtglas", "elsewhere", 1, {}) == ""
    # Its own folder is never taken by itself.
    assert planning._audio_folder_problem("Rune Falkner/Nachtglas", "Rune Falkner/Nachtglas", 1,
                                          {"rune falkner/nachtglas": {1}}) == ""


# ---- A collection folder ---------------------------------------------------------------------------------------------


def test_the_books_of_a_collection_folder_stay_one_edition_each(client: TestClient, operator: Account) -> None:
    folder = books_folder("tidy-collection")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/1.-.Steinfeuer/Audio/{number:02d}.mp3", [1, 2], album="Quist-Saga",
          artist="Rune Falkner")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/2.-.Eisglut/Audio/{number:02d}.mp3", [1, 2], album="Quist-Saga",
          artist="Rune Falkner")
    library = library_of(folder, operator)
    before = audio_editions()
    assert len(before) == 2 and all(len(paths) == 2 for paths in before.values())
    with SessionLocal() as db:
        titles = {e.id: (e.title, e.series_pos) for e in db.scalars(select(Edition))}
    assert sorted(titles.values()) == [("Eisglut", 2.0), ("Steinfeuer", 1.0)]
    tidy(client, library, "{Author}/{Series}/{Volume} - {Title}")
    after = audio_editions()
    assert set(after) == set(before)
    for edition_id, paths in after.items():
        title, position = titles[edition_id]
        assert all(path.startswith(f"Rune Falkner/Quist-Saga/0{int(position or 0)} - {title}/Audio/") for path in paths)
    read_everything_again(library)
    assert audio_editions() == after
    with SessionLocal() as db:
        assert {e.id: (e.title, e.series_pos) for e in db.scalars(select(Edition))} == titles
