"""A recording whose release holds its discs right below its own folder: ``CD1/``, ``CD2/`` are parts of one book, never
books of their own. Taken apart, the check would hold one disc against the wish and file a quarter of a book. Only
invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO
from app.services import catalog
from app.services.procure import checking, formats
from app.services.readers import names

from .bookmaker import make_mp3
from .filing_helpers import audio_wish, book_files, download_roots, editions, finish, the_download
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE


def part(path: str, *, album: str, seconds: float = 3600.0) -> SimpleNamespace:
    return SimpleNamespace(path=path, format="mp3", size=int(seconds * 16000), origin="source", unit="",
                           probe={"format": "mp3", "duration": seconds, "album": album, "title": "",
                                  "authors": ["Mara Lind"], "language": "de"})


def wanted(**more: Any) -> checking.Wanted:
    values: dict[str, Any] = {
        "kind": EDITION_AUDIO, "language": "de", "accepted": ["de"],
        "subject": {"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                    "authors": ["Mara Lind"], "series": "", "position": None},
        "config": formats.default_config(), "named_language": "de",
        "runtimes": {"unabridged": [4 * 3600 * 3]}}
    values.update(more)
    return checking.Wanted(**values)


@pytest.mark.parametrize("folder", ["CD1", "CD 1", "cd01", "Disc 1", "Disk 01", "MP3 CD 1", "Teil 1", "01"])
def test_a_disc_right_below_the_release_is_a_part_of_it(folder: str) -> None:
    assert names.strip_part_folders(folder, release=True) == ""
    assert catalog._unit_dir(f"{folder}/01.mp3", release=True) == ""


@pytest.mark.parametrize("path", ["Audio/Autor/01.mp3", "Audio/01.mp3"])
def test_a_library_keeps_its_first_folder_even_when_it_is_called_like_a_part(path: str) -> None:
    assert catalog._unit_dir(path).startswith("Audio")


def test_the_discs_of_a_release_are_one_book_and_all_of_it_counts() -> None:
    files = [part(f"CD{disc}/{track:02d}.mp3", album="Die Nebelbahn") for disc in (1, 2, 3) for track in (1, 2, 3, 4)]
    units = checking.units_of(files)
    assert len(units) == 1
    assert units[0].meta.duration == 12 * 3600
    verdict = checking.judge_units(units, wanted())
    assert verdict.outcome == "take" and verdict.variant == "unabridged" and verdict.variant_proven
    assert not verdict.drop


def test_discs_with_an_album_each_are_still_one_book() -> None:
    files = [part(f"Disc {disc}/{track:02d}.mp3", album=f"Band {disc}, die Nebelbahn")
             for disc in (1, 2) for track in (1, 2)]
    assert len(checking.units_of(files)) == 1


@pytest.mark.parametrize("album", ["Die Nebelbahn (CD #1)", "Die Nebelbahn (CD #1-4)", "Die Nebelbahn CD 1/4",
                                   "Die Nebelbahn - Disc 2"])
def test_the_number_of_a_disc_is_no_part_of_the_title(album: str) -> None:
    assert names.clean_album(album) == "Die Nebelbahn"


def test_numbered_folders_with_albums_of_their_own_are_a_question_not_the_better_half() -> None:
    files = [part(f"{folder}/{track:02d}.mp3", album=album)
             for folder, album in (("01", "Die Nebelbahn"), ("02", "Die Nebelbahn (Fortsetzung)"))
             for track in (1, 2)]
    units = checking.units_of(files)
    assert len(units) == 2
    verdict = checking.judge_units(units, wanted())
    assert (verdict.outcome, verdict.code) == ("ask", "bundle")


def test_books_of_a_box_in_folders_of_their_own_stay_apart() -> None:
    files = [part(f"{title}/CD{disc}/01.mp3", album=title) for title in ("Die Nebelbahn", "Der Salzpfad")
             for disc in (1, 2)]
    assert len(checking.units_of(files)) == 2


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_release_of_discs_goes_into_the_library_whole(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    for disc in (1, 2):
        for track in (1, 2):
            make_mp3(folder / f"CD{disc}" / f"{track:02d}.mp3", seconds=4.0, track=track, title=TITLE,
                     album=f"{TITLE} (ungekürzt)", artist=AUTHOR, series=SERIES, series_pos="2")
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert len([item for item in book_files() if item.edition_id == audio.id]) == 4
    assert 15 <= audio.duration <= 17


def test_a_download_that_waited_is_grouped_anew_when_it_is_looked_at_again(operator: Any, net: FakeNetwork,
                                                                           roots: Path) -> None:
    from app.db import SessionLocal
    from app.models import Download, PlaceFile
    from app.services.procure import importing

    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    for disc in (1, 2):
        make_mp3(folder / f"CD{disc}" / "01.mp3", seconds=4.0, track=1, title=TITLE, album=TITLE, artist=AUTHOR)
    finish(sab, job, folder)
    assert the_download().problem_code == "runtime"
    with SessionLocal() as db:
        # The grouping of an earlier version: a unit for each disc.
        for item in db.query(PlaceFile).all():
            item.unit = f"a{catalog.UNIT_JOINT}{item.path.split('/')[0]}{catalog.UNIT_JOINT}"
        db.commit()
    answer = world.client.post(f"/api/v1/procure/downloads/{the_download().id}/decide", json={"action": "keep"})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    with SessionLocal() as db:
        assert db.get(Download, the_download().id).place_step == "filed"
    assert len([item for item in book_files() if item.format == "mp3"]) == 2
    assert importing.KIND == "pimport"
