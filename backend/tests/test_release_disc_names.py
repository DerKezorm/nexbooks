"""The folders of the discs of a recording have many names: more than ``CD 1``. They all make one book, the discs in their
order, and a part that does not go in is never left out without a word. Only invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO
from app.services import catalog
from app.services.procure import checking, formats, placing
from app.services.readers import names

from .bookmaker import make_mp3
from .filing_helpers import audio_wish, book_files, download_roots, editions, finish, the_download
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE

#: Each entry: the folders of three discs.
LAYOUTS = [
    ("CD1 - Kapitel 1-12", "CD2 - Kapitel 13-24", "CD3 - Kapitel 25-36"),
    ("CD1 Kapitel 1-12", "CD2 Kapitel 13-24", "CD3 Kapitel 25-36"),
    ("CD 1 (Kap. 1-12)", "CD 2 (Kap. 13-24)", "CD 3 (Kap. 25-36)"),
    ("Die Nebelbahn CD1", "Die Nebelbahn CD2", "Die Nebelbahn CD3"),
    ("Die Nebelbahn - CD 1", "Die Nebelbahn - CD 2", "Die Nebelbahn - CD 3"),
    ("Nebelbahn (CD 1)", "Nebelbahn (CD 2)", "Nebelbahn (CD 3)"),
    ("Titel - CD 1", "Titel - CD 2", "Titel - CD 3"),
    ("1.CD", "2.CD", "3.CD"),
    ("CD I", "CD II", "CD III"),
    ("Disc 1 - Part 1", "Disc 2 - Part 2", "Disc 3 - Part 3"),
    ("CD1 [Kapitel 1-12]", "CD2 [Kapitel 13-24]", "CD3 [Kapitel 25-36]"),
    ("CD1 - Der Anfang", "CD2 - Die Mitte", "CD3 - Das Ende"),
]


def part(path: str, *, album: str = "Die Nebelbahn", minutes: float = 20.0, authors: Any = ("Mara Lind",),
         fmt: str = "mp3", rate: int = 128) -> SimpleNamespace:
    seconds = minutes * 60
    probe = {"format": fmt, "duration": seconds, "album": album, "title": album, "authors": list(authors),
             "language": "de", "bitrate": rate}
    return SimpleNamespace(path=path, format=fmt, size=int(seconds * rate * 125), origin="source",
                           unit=catalog.unit_key(path, fmt, probe, release=True), probe=probe)


def wanted(runtimes: Any = None, **more: Any) -> checking.Wanted:
    config = {**formats.default_config(), **more.pop("config", {})}
    return checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de", config=config,
                           subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                    "authors": ["Mara Lind"], "series": "", "position": None},
                           runtimes={"unabridged": [9 * 20 * 60]} if runtimes is None else runtimes, **more)


def discs(folders: Any, *, per: int = 3, albums: Any = None, **values: Any) -> list[SimpleNamespace]:
    return [part(f"{folder}/{track:02d}.mp3", album=(albums[index] if albums else "Die Nebelbahn"), **values)
            for index, folder in enumerate(folders) for track in range(1, per + 1)]


@pytest.mark.parametrize("folders", LAYOUTS)
def test_three_discs_are_one_whole_book(folders: tuple[str, ...]) -> None:
    files = discs(folders)
    units = checking.units_of(files)
    assert len(units) == 1
    verdict = checking.judge_units(units, wanted())
    assert verdict.outcome == "take" and verdict.variant == "unabridged" and verdict.variant_proven
    assert list(verdict.take) == [units[0].key] and not verdict.drop


@pytest.mark.parametrize("folders", LAYOUTS[:-1])
def test_an_album_of_their_own_on_each_disc_changes_nothing(folders: tuple[str, ...]) -> None:
    files = discs(folders, albums=["Die Nebelbahn CD 1", "Die Nebelbahn (CD 2)", "Die Nebelbahn - CD 3"])
    assert len(checking.units_of(files)) == 1


def test_an_album_that_names_the_chapters_of_a_disc_changes_nothing() -> None:
    files = discs(("CD1 - Kapitel 1-12", "CD2 - Kapitel 13-24"), albums=["Kapitel 1-12", "Kapitel 13-24"])
    assert len(checking.units_of(files)) == 1


def test_a_folder_that_names_the_book_and_its_disc_outweighs_the_albums() -> None:
    files = discs(("Die Nebelbahn CD1", "Die Nebelbahn CD2"), albums=["Erster Abschnitt", "Zweiter Abschnitt"])
    assert len(checking.units_of(files)) == 1


@pytest.mark.parametrize("folders", LAYOUTS)
def test_the_discs_come_in_their_order(folders: tuple[str, ...]) -> None:
    assert [names.disc_in_path(f"{folder}/01.mp3") for folder in folders] == [1, 2, 3]


@pytest.mark.parametrize(("name", "number"), [
    ("CD 10", 10), ("CD X", 10), ("CD IX", 9), ("CD IV", 4), ("CD XII", 12), ("10.CD", 10), ("Disc 2 von 3", 2),
    ("Die Nebelbahn CD 12", 12), ("Die Nebelbahn CD IV", 4), ("Nebelbahn 2.CD", 2), ("cd_3", 3), ("CD-3", 3),
])
def test_the_number_of_a_disc(name: str, number: int) -> None:
    assert names.disc_in_path(f"{name}/01.mp3") == number


@pytest.mark.parametrize("name", [
    "Der CD Mann", "Die CD", "Der Hafen", "3 CDs", "CD Sammlung", "Die CD 5 Verschwörung", "Dancing Queen", "Lied in C",
    "Disco", "Fall 1", "Ivan", "Mix",
])
def test_a_name_that_only_holds_the_word_is_no_disc(name: str) -> None:
    assert not names.is_cd_folder(name)
    assert names.disc_book(name) == ""


def test_two_books_on_discs_in_one_release_stay_two() -> None:
    files = discs(("Der Hafen CD1", "Der Hafen CD2"), albums=["Der Hafen", "Der Hafen"]) + discs(
        ("Die Nebelbahn CD1", "Die Nebelbahn CD2"))
    units = checking.units_of(files)
    assert len(units) == 2


def test_a_box_with_one_book_on_each_disc_does_not_become_one_book() -> None:
    files = discs(("CD1 - Der Hafen", "CD2 - Die Nebelbahn"), albums=["Der Hafen", "Die Nebelbahn"])
    assert len(checking.units_of(files)) == 2


def test_in_a_library_such_a_folder_stays_a_book_unless_the_folder_above_is_that_book() -> None:
    assert names.strip_part_folders("Autor/Die Nebelbahn CD1") == "Autor/Die Nebelbahn CD1"
    assert names.strip_part_folders("Autor/Die Nebelbahn/Die Nebelbahn CD1") == "Autor/Die Nebelbahn"
    assert names.strip_part_folders("Autor/Titel/CD1 - Kapitel 1-12") == "Autor/Titel"


def test_a_disc_name_is_a_release_folder_only_with_the_book_it_names() -> None:
    assert names.strip_part_folders("Die Nebelbahn CD1", release=True) == ""
    assert catalog._unit_dir("Die Nebelbahn CD1/01.mp3", release=True) == "die nebelbahn"
    assert catalog._unit_dir("A/Die Nebelbahn CD1/01.mp3", release=True) == "A/die nebelbahn"


# --- Nothing is left out without a word ------------------------------------------------------------------------------


def test_folders_nothing_marks_as_discs_are_no_copies_of_the_book() -> None:
    files = discs(("Erster Abschnitt", "Zweiter Abschnitt", "Dritter Abschnitt"))
    units = checking.units_of(files)
    assert len(units) == 3
    verdict = checking.judge_units(units, wanted())
    assert (verdict.outcome, verdict.code) == ("ask", "bundle")
    assert verdict.values["whole"] is True and len(verdict.values["units"]) == 3


def test_also_with_abridged_books_allowed_and_no_catalogue() -> None:
    files = discs(("Erster Abschnitt", "Zweiter Abschnitt"))
    verdict = checking.judge_units(checking.units_of(files), wanted({}, config={"abridged": True}))
    assert (verdict.outcome, verdict.code) == ("ask", "bundle")


def test_two_recordings_of_the_whole_book_are_copies_and_the_better_format_goes_in() -> None:
    files = [part("MP3/01.mp3", minutes=180), part("Book/book.m4b", minutes=181, fmt="m4b")]
    verdict = checking.judge_units(checking.units_of(files), wanted())
    assert verdict.outcome == "take"
    assert list(verdict.drop) == ["source:MP3/01.mp3"]


def test_two_lengths_in_two_formats_are_no_copies() -> None:
    files = [part("Eins/01.mp3", minutes=60), part("Zwei/book.m4b", minutes=120, fmt="m4b")]
    verdict = checking.judge_units(checking.units_of(files), wanted())
    assert (verdict.outcome, verdict.code) == ("ask", "bundle")


def test_a_question_says_what_stays_out() -> None:
    files = [*discs(("CD1", "CD2"), per=2), part("Bonus/01.mp3", album="Interview mit der Autorin", minutes=10)]
    verdict = checking.judge_units(checking.units_of(files), wanted())
    assert (verdict.outcome, verdict.code) == ("ask", "runtime")
    assert verdict.values["left_out"] == [{"title": "Interview mit der Autorin", "files": 1}]


def test_a_question_without_anything_left_out_says_nothing_of_it() -> None:
    verdict = checking.judge_units(checking.units_of(discs(("CD1", "CD2"), per=2)), wanted())
    assert (verdict.outcome, verdict.code) == ("ask", "runtime") and "left_out" not in verdict.values


def test_nothing_proves_the_book_and_something_stays_out_then_a_person_looks() -> None:
    files = [*discs(("CD1", "CD2"), per=2), part("Bonus/01.mp3", album="Interview mit der Autorin", minutes=10)]
    verdict = checking.judge_units(checking.units_of(files), wanted({}, config={"abridged": True}))
    assert (verdict.outcome, verdict.code) == ("ask", "bundle")


def test_a_whole_book_beside_something_else_goes_in_and_the_rest_stays_out() -> None:
    files = [*discs(("CD1", "CD2", "CD3")), part("Bonus/01.mp3", album="Interview mit der Autorin", minutes=10)]
    verdict = checking.judge_units(checking.units_of(files), wanted())
    assert verdict.outcome == "take" and verdict.drop == {"source:Bonus/01.mp3": "bundle_rest"}


def test_when_a_person_said_the_folders_are_one_book_nothing_is_left_out() -> None:
    files = discs(("Erster Abschnitt", "Zweiter Abschnitt", "Dritter Abschnitt"))
    for item in files:
        item.unit = catalog.WHOLE_UNIT
    units = checking.units_of(files)
    assert len(units) == 1
    verdict = checking.judge_units(units, wanted(decision={"whole": True}))
    assert verdict.outcome == "take" and not verdict.drop


# --- In the library ---------------------------------------------------------------------------------------------------


def test_folders_the_scan_would_not_read_as_discs_go_by_the_name_of_a_disc(tmp_path: Path) -> None:
    goal = placing.audio_goal(tmp_path, ["Autor", "Titel"], [("1", "Die Nebelbahn CD2/01.mp3", 1),
                                                             ("2", "Die Nebelbahn CD10/01.mp3", 1),
                                                             ("3", "Die Nebelbahn CD1/01.mp3", 1)], "")
    assert goal.targets == {"1": "Autor/Titel/CD 2/01.mp3", "2": "Autor/Titel/CD 3/01.mp3",
                            "3": "Autor/Titel/CD 1/01.mp3"}


def test_folders_the_scan_reads_as_discs_keep_their_names(tmp_path: Path) -> None:
    goal = placing.audio_goal(tmp_path, ["Autor", "Titel"], [("1", "CD1 - Kapitel 1-12/01.mp3", 1),
                                                             ("2", "CD2 - Kapitel 13-24/01.mp3", 1)], "")
    assert goal.targets == {"1": "Autor/Titel/CD1 - Kapitel 1-12/01.mp3", "2": "Autor/Titel/CD2 - Kapitel 13-24/01.mp3"}


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_release_with_such_folders_is_one_book_in_the_library(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    for disc in (1, 2):
        for track in (1, 2):
            make_mp3(folder / f"Nebel (CD {disc})" / f"{track:02d}.mp3", seconds=4.0, track=track, title=TITLE,
                     album=f"{TITLE} (ungekürzt)", artist=AUTHOR)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    run_jobs()
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    files = [item for item in book_files() if item.edition_id == audio.id]
    assert len(files) == 4
    assert {item.path.split("/")[-2] for item in files} == {"CD 1", "CD 2"}


def test_the_answer_that_the_folders_are_one_book(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    for name in ("Erster Abschnitt", "Zweiter Abschnitt", "Dritter Abschnitt"):
        for track in (1, 2):
            make_mp3(folder / name / f"{track:02d}.mp3", seconds=4.0, track=track, title=TITLE,
                     album=f"{TITLE} (ungekürzt)", artist=AUTHOR)
    finish(sab, job, folder)
    row = the_download()
    assert (row.state, row.problem_code) == ("problem", "bundle")
    assert row.problem_values["whole"] is True
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "whole"})
    assert answer.status_code == 200, answer.text
    run_jobs()
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    files = [item for item in book_files() if item.edition_id == audio.id]
    assert len(files) == 6
    assert {item.path.split("/")[-2] for item in files} == {"CD 1", "CD 2", "CD 3"}


def test_the_answer_fits_only_a_question_that_offers_it(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    for disc in (1, 2):
        make_mp3(folder / f"CD{disc}" / "01.mp3", seconds=4.0, track=1, title=TITLE, album=TITLE, artist=AUTHOR)
    finish(sab, job, folder)
    row = the_download()
    assert row.problem_code == "runtime"
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "whole"})
    assert answer.status_code == 422
