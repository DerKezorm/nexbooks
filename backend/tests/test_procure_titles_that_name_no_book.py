"""The title in the files of a release whose name named the book: a number, a series and a number, the name of a track, a
slip of the keys. None of that is another book, and nobody is asked; a title that really names another book still is
a question. Only invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO, EDITION_EBOOK
from app.services.procure import checking, formats

from .filing_helpers import (
    audio_wish,
    download_roots,
    ebook_world,
    editions,
    finish,
    german_epub,
    history,
    the_download,
)
from .filing_helpers import recording as parts
from .procure_fakes import FakeNetwork
from .procure_search_helpers import TITLE

WISHED = "Lara Lind und der Stein der Wege"
SUBJECT = {"title": WISHED, "titles": [{"text": WISHED, "language": "de"}], "authors": ["Mara Lind"], "series": "",
           "position": None}


def wanted(kind: str = EDITION_AUDIO, *, named: bool = True, subject: dict[str, Any] = SUBJECT) -> checking.Wanted:
    return checking.Wanted(kind=kind, language="de", accepted=["de"], named_language="de", named_book=named,
                           subject=subject, config=formats.default_config(),
                           runtimes={"unabridged": [3600]} if kind == EDITION_AUDIO else {})


def recording(album: str, path: str = "01.mp3", title: str = "") -> list[SimpleNamespace]:
    return [SimpleNamespace(path=path, format="mp3", size=3600 * 16000, origin="source", unit="",
                            probe={"format": "mp3", "duration": 3600.0, "album": album, "title": title,
                                   "authors": ["Mara Lind"], "language": "de"})]


def verdict_on(files: list[SimpleNamespace], found: checking.Wanted | None = None) -> checking.Verdict:
    return checking.judge_units(checking.units_of(files), found or wanted())


@pytest.mark.parametrize("files", [
    recording("", "101.mp3"),
    recording("2473"),
    recording("Der Stein CD3 Erstes Kapitel"),
    recording("", "Der Stein CD01-01 Erstes Kapitel.mp3"),
    recording("Der lange Turm 1"),
    recording("und der Stein der Wege"),
    recording("Lara Lind und der Stein der Wegee"),
    recording("Lara Lind und der Stien der Wege"),
    recording("", "Kapitel eins.mp3"),
])
def test_a_title_that_names_no_other_book_needs_nobody(files: list[SimpleNamespace]) -> None:
    verdict = verdict_on(files)
    assert verdict.outcome == "take", (files[0].path, verdict.code, verdict.values)


@pytest.mark.parametrize("files", [
    recording("Lara Lind und der Stein der Wege im neuen Zeitalter"),
    recording("Endlich Ferien: Rabbat und Ida"),
    recording("", "Lara Lind und der Stein der Wege im neuen Zeitalter.mp3"),
])
def test_a_title_that_names_another_book_stays_a_question(files: list[SimpleNamespace]) -> None:
    verdict = verdict_on(files)
    assert (verdict.outcome, verdict.code) == ("ask", "files_unassigned"), (files[0].path, verdict.code)


@pytest.mark.parametrize("files", [recording("", "101.mp3"), recording("Der lange Turm 1"),
                                   recording("und der Stein der Wege")])
def test_without_the_name_of_the_release_it_stays_a_question(files: list[SimpleNamespace]) -> None:
    assert verdict_on(files, wanted(named=False)).outcome == "ask"


def test_a_short_end_of_the_title_is_no_title() -> None:
    assert verdict_on(recording("der Wege")).outcome == "ask"


def test_a_title_of_two_words_is_not_read_for_slips() -> None:
    subject = {**SUBJECT, "title": "Die Nacht", "titles": [{"text": "Die Nacht", "language": "de"}]}
    assert verdict_on(recording("Die Macht"), wanted(subject=subject)).outcome == "ask"


def test_the_name_of_a_file_of_an_ebook_is_no_title_either() -> None:
    item = SimpleNamespace(path="x1f3.epub", format="epub", size=2_000_000, origin="source", unit="",
                           probe={"format": "epub", "authors": ["Mara Lind"], "language": "de"})
    assert verdict_on([item], wanted(EDITION_EBOOK)).outcome == "take"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_the_book_goes_in_under_the_title_wished(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    parts(folder, parts=2, seconds=4.0, album="Der lange Turm 7 (ungekürzt)", title="Schluss - 001", series_pos=None,
          series=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert audio.title == TITLE
    assert not history()[-1].note.get("other_book")


def test_an_ebook_whose_title_names_no_book_takes_the_title_wished(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / "Release"
    german_epub(folder, title="Der lange Turm 7", series=None, series_pos=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)
    book = [edition for edition in editions() if edition.kind == "ebook"][-1]
    assert book.title == TITLE
    assert history()[-1].note.get("file_title") == "Der lange Turm 7"
    assert not history()[-1].note.get("other_book")
