"""The check after the download and the filing: a finished download is read as the scan reads it, held
against the wish, placed by the scheme without overwriting anything, and the library knows the book; what does not pass
is turned down, blocked and searched again; what is unclear waits for a person."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BlocklistEntry, Download, Edition

from .filing_helpers import (
    book_files,
    download_roots,
    ebook_world,
    editions,
    files_in,
    finish,
    german_epub,
    hidden_in,
    history,
    library_path,
    the_download,
    wishes,
)
from .follow_helpers import BOOK, rows
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_finished_e_book_goes_into_the_library_by_the_scheme_and_its_wish_is_done(operator: Any, net: FakeNetwork,
                                                                                     roots: Path) -> None:
    world, sab, job = ebook_world(net)
    # SABnzbd names the folder of a job after the job (the release).
    folder = roots / "complete" / BOOK
    folder.mkdir(parents=True)
    german_epub(folder)
    (folder / "release.nfo").write_text("x", encoding="utf-8")
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values, row.failed_reason)
    shelf = library_path(world)
    placed = [name for name in files_in(shelf) if name.endswith(".epub") and "Nebelchor" in name]
    assert placed == ["Rune Falkner/Nebelchor/02 - Die Laternenstraße.epub"], files_in(shelf)
    assert not hidden_in(shelf)
    german = [edition for edition in editions() if edition.language == "de"]
    assert len(german) == 1 and german[0].work_id == world.work_id and row.edition_id == german[0].id
    assert {item.state for item in wishes()} == {"done"}
    [line] = history()
    assert line.result == "done" and line.note["library"] == "Books"
    # Usenet: the file left the download folder (moved), the job's own folder with its rest is gone.
    assert not folder.exists()
    # The library knows the file as the scan does: a scan finds nothing new.
    known = {item.path: item for item in book_files()}
    assert "Rune Falkner/Nebelchor/02 - Die Laternenstraße.epub" in known
    with SessionLocal() as db:
        assert db.scalar(select(Download.place_step)) == "filed"


def test_an_e_book_in_another_language_is_turned_down_blocked_and_searched_again(operator: Any, net: FakeNetwork,
                                                                                 roots: Path) -> None:
    # A release whose name says no language: the package is the only witness (a name that says German beside an
    # English package is a question, ``test_procure_language_witnesses``).
    world, sab, job = ebook_world(net, Item("Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.EPUB-EXAMPLE",
                                            size=2_000_000, categories=(7120,)))
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, language="en")
    finish(sab, job, folder)
    first = rows(Download)[0]
    assert first.state == "rejected" and first.problem_code == "language", (first.state, first.problem_code)
    assert first.problem_values == {"file": "en", "wanted": "de"}
    [block] = rows(BlocklistEntry)
    assert block.reason == "other_language:en" and block.library_id == world.library_id
    assert [line.result for line in history()] == ["rejected"]
    assert not [name for name in files_in(library_path(world)) if "Nebelchor" in name]
    # Nobody has anything to decide: the history and the blocklist say what happened.
    assert world.client.get("/api/v1/procure/problems").json()["problems"] == []
    assert world.client.get("/api/v1/procure/counts").json()["needs"] == 0
    with SessionLocal() as db:
        assert db.scalar(select(Edition.id).where(Edition.language == "de")) is None


# --- Recordings --------------------------------------------------------------------------------------------------


def test_the_parts_of_a_recording_go_into_one_folder_with_their_cover(operator: Any, net: FakeNetwork,
                                                                       roots: Path) -> None:
    from .bookmaker import cover_image
    from .filing_helpers import audio_wish, recording

    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Rune Falkner-Die Laternenstrasse-MP3CD"
    recording(folder / "CD 1", parts=2)
    (folder / "cover.jpg").write_bytes(cover_image(kind="JPEG"))
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values, row.failed_reason)
    shelf = library_path(world)
    placed = [name for name in files_in(shelf) if name.startswith("Rune Falkner/Nebelchor/02")]
    assert placed == ["Rune Falkner/Nebelchor/02 - Die Laternenstraße/CD 1/01.mp3",
                      "Rune Falkner/Nebelchor/02 - Die Laternenstraße/CD 1/02.mp3",
                      "Rune Falkner/Nebelchor/02 - Die Laternenstraße/cover.jpg"], files_in(shelf)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert audio.work_id == world.work_id
    parts = sorted((item.part, item.path) for item in book_files() if item.edition_id == audio.id)
    assert [part for part, _path in parts] == [1, 2]


def test_a_recording_whose_running_time_is_an_abridged_one_asks_and_goes_in_as_abridged(
        operator: Any, net: FakeNetwork, roots: Path, world: Any) -> None:
    from .fake_sources import audible_product
    from .filing_helpers import audio_wish, recording
    from .procure_search_helpers import AUTHOR, TITLE

    world.fake.audible = {"de": [audible_product("B000000001", TITLE, [AUTHOR], 3),
                                 audible_product("B000000002", TITLE, [AUTHOR], 1, kind="abridged")]}
    people, sab, job = audio_wish(net)
    folder = roots / "complete" / "short"
    recording(folder, parts=3, seconds=20.0)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "runtime"
    assert row.problem_values["reason"] == "abridged" and row.problem_values["unabridged"] == 180
    assert row.problem_values["abridged"] == 60 and 55 <= row.problem_values["loaded"] <= 65
    assert not [name for name in files_in(library_path(people)) if name.endswith(".mp3")]
    shown = people.client.get("/api/v1/procure/problems").json()["problems"]
    assert shown[0]["code"] == "runtime" and shown[0]["values"]["unabridged"] == 180
    answer = people.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "keep"})
    assert answer.status_code == 200, answer.text
    from .library_helpers import run_jobs

    run_jobs()
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert audio.variant == "abridged"
    assert history()[-1].note.get("variant") == "abridged"


def test_blocking_a_question_blocks_the_release_takes_the_files_out_and_searches_on(
        operator: Any, net: FakeNetwork, roots: Path, world: Any) -> None:
    from .fake_sources import audible_product
    from .filing_helpers import audio_wish, recording
    from .procure_search_helpers import AUTHOR, TITLE

    world.fake.audible = {"de": [audible_product("B000000001", TITLE, [AUTHOR], 3),
                                 audible_product("B000000002", TITLE, [AUTHOR], 1, kind="abridged")]}
    people, sab, job = audio_wish(net)
    folder = roots / "complete" / "short"
    recording(folder, parts=3, seconds=20.0)
    finish(sab, job, folder)
    row = the_download()
    answer = people.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "block"})
    assert answer.status_code == 200, answer.text
    first = rows(Download)[0]
    assert first.state == "removed"
    [block] = rows(BlocklistEntry)
    assert block.reason == "abridged"
    assert sab.removed and sab.removed[-1] == job
