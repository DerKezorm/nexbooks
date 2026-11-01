"""Upgrades: a better format replaces a worse one, the old one goes into the trash of its library, the
place of every person is carried over (an e-book by its share and the person is told, a recording by its time), and
the trash keeps it for its days, gives it back, or deletes it when asked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import BookFile, Download, Edition, ProcureNotice, Progress, TrashEntry

from .audiomaker import make_flac
from .bookmaker import make_pdf
from .conftest import new_client
from .filing_helpers import (
    download_roots,
    editions,
    files_in,
    finish,
    german_epub,
    history,
    library_path,
    the_download,
    world_with,
)
from .follow_helpers import BOOK, rows, sabnzbd, wish
from .library_helpers import operator_account, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE, Item


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def a_place(user_id: int, edition_id: int, locator: str, fraction: float, unit: str = "page",
            koreader: str = "") -> None:
    with SessionLocal() as db:
        db.add(Progress(user_id=user_id, edition_id=edition_id, locator=locator, fraction=fraction, unit=unit,
                        koreader=koreader,
                        chapter=12 if unit == "page" else 1, chapter_count=310 if unit == "page" else 3,
                        moved_at=clock.now(), received_at=clock.now()))
        db.commit()


def pdf_world(net: FakeNetwork) -> tuple[Any, Any, str, int]:
    """The world's book also as a PDF without a language, and a wish for the German e-book: an upgrade."""
    sab = sabnzbd(net)
    world = world_with(net, lambda folder: make_pdf(folder / "falkner" / "laternenstrasse.pdf", title=TITLE,
                                                    author=AUTHOR, pages=5))
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    [pdf] = editions()
    made = wish(world, kinds=("ebook",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    [row] = rows(Download)
    return world, sab, row.client_ref, pdf.id


def test_an_epub_replaces_a_pdf_the_pdf_goes_to_the_trash_and_the_reader_is_told(operator: Any, net: FakeNetwork,
                                                                                 roots: Path) -> None:
    world, sab, job, pdf_id = pdf_world(net)
    a_place(world.person.id, pdf_id, "page:142", 0.46, koreader="/body/DocFragment[3]/body/div/p[12]/text().34")
    folder = roots / "complete" / BOOK
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    shelf = library_path(world)
    names = files_in(shelf)
    assert "falkner/laternenstrasse.epub" in names and "falkner/laternenstrasse.pdf" not in names
    with SessionLocal() as db:
        files = list(db.scalars(select(BookFile.format).where(BookFile.edition_id == pdf_id)))
        assert files == ["epub"]
        [entry] = db.scalars(select(TrashEntry)).all()
        assert entry.format == "pdf" and entry.files == [{"path": "falkner/laternenstrasse.pdf",
                                                           "size": entry.size}]
        assert (shelf / ".nexbooks-trash" / entry.folder / "falkner" / "laternenstrasse.pdf").is_file()
        place = db.get(Progress, (world.person.id, pdf_id))
        assert place is not None and place.locator == "" and place.fraction == pytest.approx(0.46)
        # The words of KOReader belonged to the old format: they lead nowhere in the new one.
        assert place.koreader == ""
        # (A report from KOReader that comes before the person takes the old format back.)
        place.koreader = "/body/DocFragment[1]"
        db.commit()
    assert row.replaced == {"from": "pdf", "to": "epub", "trash_id": entry.id}
    assert history()[-1].note["upgrade"] == {"from": "pdf", "to": "epub"}
    notes = world.client.get("/api/v1/procure/problems").json()["notes"]
    assert len(notes) == 1 and notes[0]["code"] == "upgrade_position" and notes[0]["can_restore"] is True
    assert notes[0]["detail"]["page"] == 142 and notes[0]["detail"]["from"] == "pdf" and "old" not in notes[0]["detail"]
    assert notes[0]["release"] == row.title and row.title
    # Nobody else sees the note of this person.
    assert new_client(operator_account()).get("/api/v1/procure/problems").status_code in (200, 403)
    with SessionLocal() as db:
        assert db.scalar(select(ProcureNotice.user_id)) == world.person.id
    # "Altes Format zurückholen": the PDF is back, the EPUB in the trash, the place as it was.
    answer = world.client.post(f"/api/v1/procure/notes/{notes[0]['id']}/restore")
    assert answer.status_code == 204, answer.text
    run_jobs()
    names = files_in(shelf)
    assert "falkner/laternenstrasse.pdf" in names and "falkner/laternenstrasse.epub" not in names
    with SessionLocal() as db:
        place = db.get(Progress, (world.person.id, pdf_id))
        assert place is not None and place.locator == "page:142" and place.koreader == ""
        assert [entry.format for entry in db.scalars(select(TrashEntry))] == ["epub"]
    assert world.client.get("/api/v1/procure/problems").json()["notes"] == []


def test_a_flac_recording_replaces_an_mp3_one_and_the_places_move_with_it(operator: Any, net: FakeNetwork,
                                                                         roots: Path) -> None:
    from .filing_helpers import recording

    sab = sabnzbd(net)
    world = world_with(net, lambda folder: recording(folder / "falkner" / "hoerbuch", parts=2, seconds=10.0))
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-FLAC-DE-2021-EXAMPLE", size=300_000_000,
                                categories=(3130,))]
    shelf = library_path(world)
    [mp3] = editions()
    a_place(world.person.id, mp3.id, "time:5.000", 0.25, unit="time")
    made = wish(world, kinds=("audio",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()


    [row] = rows(Download)
    folder = roots / "complete" / "flac"
    for number in (1, 2):
        make_flac(folder / f"{number:02d}.flac", 10.0, title=TITLE, album=TITLE, artist=AUTHOR, tracknumber=str(number))
    finish(sab, row.client_ref, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    assert row.replaced["from"] == "mp3_128" and row.replaced["to"] == "flac"
    names = files_in(shelf)
    assert not [name for name in names if name.startswith("falkner/hoerbuch")]
    audio = [edition for edition in editions() if edition.kind == "audio"]
    assert len(audio) == 1 and audio[0].id != mp3.id
    with SessionLocal() as db:
        place = db.get(Progress, (world.person.id, audio[0].id))
        assert place is not None and place.locator.startswith("time:")
        assert abs(float(place.locator[5:]) - 5.0) < 0.6
        assert db.get(Edition, mp3.id) is None
        [entry] = db.scalars(select(TrashEntry)).all()
        assert entry.format == "mp3_128" and len(entry.files) == 2
    # The person who wished it is told, also where no place of an e-book moved, with the way back.
    notes = world.client.get("/api/v1/procure/problems").json()["notes"]
    assert [(note["code"], note["detail"], note["can_restore"]) for note in notes
            if note["code"] != "folder_kept"] == [("upgrade_replaced", {"from": "mp3_128", "to": "flac"}, True)]


def test_the_trash_is_seen_by_who_looks_after_the_library_and_emptied_after_a_question(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job, _pdf_id = pdf_world(net)
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, job, folder)
    keeper = new_client(operator_account())
    listed = keeper.get("/api/v1/procure/trash").json()
    assert [entry["format"] for entry in listed["entries"]] == ["pdf"] and listed["days"] == 30
    # A person who may only ask for books there does not look after the library: no titles, nothing to delete.
    assert world.client.get("/api/v1/procure/trash").json()["entries"] == []
    entry_id = listed["entries"][0]["id"]
    assert world.client.delete(f"/api/v1/procure/trash/{entry_id}").status_code == 404
    answer = keeper.delete("/api/v1/procure/trash")
    assert answer.status_code == 200 and answer.json() == {"deleted": 1}
    assert not (library_path(world) / ".nexbooks-trash").exists()


def test_a_trash_entry_whose_days_are_over_goes_with_its_files(operator: Any, net: FakeNetwork, roots: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from app.services.procure import trash

    world, sab, job, _pdf_id = pdf_world(net)
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, job, folder)
    later = clock.now() + timedelta(days=31)
    with SessionLocal() as db:
        assert trash.sweep(db) == 0
        monkeypatch.setattr(clock, "now", lambda: later)
        assert trash.sweep(db) == 1
    assert not (library_path(world) / ".nexbooks-trash").exists()


def three_versions(folder: Path) -> None:
    """The same book as a reading, a radio play and an abridged reading, side by side (all MP3, same length but the
    last one's)."""
    from .filing_helpers import recording

    recording(folder / "falkner" / "hoerbuch", parts=2, seconds=10.0)
    recording(folder / "falkner" / "hoerspiel", parts=2, seconds=10.0, album=TITLE + " (Hörspiel)")
    recording(folder / "falkner" / "gekuerzt", parts=1, seconds=10.0, album=TITLE + " (gekürzt)")


def test_a_better_recording_betters_only_the_version_it_is_not_the_others(operator: Any, net: FakeNetwork,
                                                                          roots: Path) -> None:
    sab = sabnzbd(net)
    world = world_with(net, three_versions)
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-FLAC-DE-2021-EXAMPLE", size=300_000_000,
                                categories=(3130,))]
    shelf = library_path(world)
    before = {edition.variant: edition.id for edition in editions() if edition.kind == "audio"}
    assert sorted(before) == ["abridged", "dramatized", "unabridged"], before
    made = wish(world, kinds=("audio",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    [row] = rows(Download)
    folder = roots / "complete" / "flac"
    for number in (1, 2):
        make_flac(folder / f"{number:02d}.flac", 10.0, title=TITLE, album=TITLE, artist=AUTHOR, tracknumber=str(number))
    finish(sab, row.client_ref, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    names = files_in(shelf)
    # The reading (MP3) is replaced by the FLAC; the radio play and the abridged reading are still there, untouched.
    assert [name for name in names if name.startswith("falkner/hoerbuch")] == []
    assert len([name for name in names if name.startswith("falkner/hoerspiel/")]) == 2
    assert len([name for name in names if name.startswith("falkner/gekuerzt/")]) == 1
    with SessionLocal() as db:
        [entry] = db.scalars(select(TrashEntry)).all()
        assert entry.format == "mp3_128" and len(entry.files) == 2
        assert db.get(Edition, before["dramatized"]) is not None and db.get(Edition, before["abridged"]) is not None
    assert row.replaced is not None and row.replaced["from"] == "mp3_128"
    audio = {edition.variant for edition in editions() if edition.kind == "audio"}
    assert audio == {"unabridged", "dramatized", "abridged"}


def test_a_recording_of_another_reader_or_length_stands_beside_the_old_one(operator: Any, net: FakeNetwork,
                                                                           roots: Path) -> None:
    from .filing_helpers import recording

    sab = sabnzbd(net)
    # The old one is 20 seconds, the one that comes 22: another reading (or a cut one), never "the same, better".
    world = world_with(net, lambda folder: recording(folder / "falkner" / "alt", parts=2, seconds=10.0))
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-FLAC-DE-2021-EXAMPLE", size=300_000_000,
                                categories=(3130,))]
    made = wish(world, kinds=("audio",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    [row] = rows(Download)
    folder = roots / "complete" / "flac"
    for number in (1, 2):
        make_flac(folder / f"{number:02d}.flac", 11.0, title=TITLE, album=TITLE, artist=AUTHOR, tracknumber=str(number))
    finish(sab, row.client_ref, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    assert row.replaced is None
    names = files_in(library_path(world))
    assert len([name for name in names if name.startswith("falkner/alt/")]) == 2
    assert len([name for name in names if name.endswith(".flac")]) == 2
    with SessionLocal() as db:
        assert db.scalars(select(TrashEntry)).all() == []


def test_a_release_no_better_than_what_the_library_has_is_turned_down_for_an_upgrade(operator: Any, net: FakeNetwork,
                                                                                   roots: Path) -> None:
    from .filing_helpers import recording

    sab = sabnzbd(net)
    world = world_with(net, lambda folder: recording(folder / "falkner" / "hoerbuch", parts=2, seconds=10.0))
    # The name of the release says FLAC, what comes is an MP3 recording like the one the library has.
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-FLAC-DE-2021-EXAMPLE", size=300_000_000,
                                categories=(3130,))]
    made = wish(world, kinds=("audio",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    [row] = rows(Download)
    folder = roots / "complete" / "same"
    recording(folder, parts=2, seconds=10.0)
    finish(sab, row.client_ref, folder)
    row = the_download()
    assert row.state == "rejected" and row.failed_reason == "not_better", (row.state, row.problem_code, row.failed_reason)
    assert not [name for name in files_in(library_path(world)) if not name.startswith("falkner/hoerbuch/")]
    with SessionLocal() as db:
        assert db.scalars(select(TrashEntry)).all() == []


def test_the_trash_gives_nothing_back_where_something_lies_and_does_not_begin_to(operator: Any, net: FakeNetwork,
                                                                                roots: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import trash

    from .filing_helpers import recording

    sab = sabnzbd(net)
    world = world_with(net, lambda folder: recording(folder / "falkner" / "hoerbuch", parts=3, seconds=10.0))
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-FLAC-DE-2021-EXAMPLE", size=300_000_000,
                                categories=(3130,))]
    made = wish(world, kinds=("audio",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    [row] = rows(Download)
    folder = roots / "complete" / "flac"
    for number in (1, 2, 3):
        make_flac(folder / f"{number:02d}.flac", 10.0, title=TITLE, album=TITLE, artist=AUTHOR, tracknumber=str(number))
    finish(sab, row.client_ref, folder)
    assert the_download().state == "imported"
    shelf = library_path(world)
    keeper = new_client(operator_account())
    [entry] = keeper.get("/api/v1/procure/trash").json()["entries"]
    # Somebody put a file where the last part of the old recording lay.
    where = shelf / "falkner" / "hoerbuch" / "03.mp3"
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_bytes(b"somebody's own file")
    moves: list[tuple[Path, Path]] = []
    real = trash._move
    monkeypatch.setattr(trash, "_move", lambda source, target: (moves.append((source, target)), real(source, target))[1])
    answer = keeper.post(f"/api/v1/procure/trash/{entry['id']}/restore")
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "trash_taken", answer.text
    # Nothing was moved, not even to be moved back: the files of the entry did not leave the trash.
    assert moves == []
    assert where.read_bytes() == b"somebody's own file"
    assert not (shelf / "falkner" / "hoerbuch" / "01.mp3").exists()
    with SessionLocal() as db:
        assert len(db.scalars(select(TrashEntry)).all()) == 1


def test_a_better_recording_is_held_against_its_own_version_not_against_the_best_of_all(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    from .filing_helpers import recording

    def fill(folder: Path) -> None:
        recording(folder / "falkner" / "hoerbuch", parts=2, seconds=10.0)
        # An abridged reading that already is a FLAC: the best format of the book, but not of its full reading.
        make_flac(folder / "falkner" / "gekuerzt" / "01.flac", 10.0, title=TITLE, album=TITLE + " (gekürzt)",
                  artist=AUTHOR, tracknumber="1")

    sab = sabnzbd(net)
    world = world_with(net, fill)
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-FLAC-DE-2021-EXAMPLE", size=300_000_000,
                                categories=(3130,))]
    made = wish(world, kinds=("audio",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    [row] = rows(Download)
    folder = roots / "complete" / "flac"
    for number in (1, 2):
        make_flac(folder / f"{number:02d}.flac", 10.0, title=TITLE, album=TITLE, artist=AUTHOR, tracknumber=str(number))
    finish(sab, row.client_ref, folder)
    row = the_download()
    # It betters the MP3 reading (the same version); the FLAC of the abridged one is no reason to say "not better".
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    assert row.replaced is not None and row.replaced["from"] == "mp3_128" and row.replaced["to"] == "flac"
    names = files_in(library_path(world))
    assert [name for name in names if name.startswith("falkner/hoerbuch")] == []
    assert [name for name in names if name.startswith("falkner/gekuerzt/")] == ["falkner/gekuerzt/01.flac"]
