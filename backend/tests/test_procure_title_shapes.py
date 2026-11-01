"""A title as catalogues write it for sorting, with the article behind it: the book takes it as it is read, and a file
is held against it as such. Only invented books."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO
from app.services import catalog
from app.services.procure import checking, formats
from app.services.readers import names


@pytest.mark.parametrize(("title", "read"), [
    ("Frage der Nebelbahn, eine", "Eine Frage der Nebelbahn"),
    ("Nebelbahn, die", "Die Nebelbahn"),
    ("Salzpfad, der", "Der Salzpfad"),
    ("Lantern Road, The", "The Lantern Road"),
    ("Long Quiet, the.", "The Long Quiet"),
])
def test_the_article_goes_in_front(title: str, read: str) -> None:
    assert names.article_in_front(title) == read


@pytest.mark.parametrize("title", ["Die Frau, die", "Die Nebelbahn", "Nacht, Stille", "Eine, die", "Ada, Ben, Cleo"])
def test_a_title_that_only_ends_on_such_a_word_stays(title: str) -> None:
    assert names.article_in_front(title) == title


def subject(title: str, authors: tuple[str, ...] = ("Mara Lind",)) -> dict[str, Any]:
    return {"title": title, "titles": [{"text": title, "language": "de"}], "authors": list(authors), "series": "",
            "position": None}


def recording(album: str, *, authors: tuple[str, ...] = ("Mara Lind",)) -> SimpleNamespace:
    return SimpleNamespace(path="01.mp3", format="mp3", size=600 * 12000, origin="source", unit="",
                           probe={"format": "mp3", "duration": 600.0, "album": album, "title": album,
                                  "authors": list(authors), "language": "de"})


def test_a_recording_is_held_against_a_work_whose_title_is_written_for_sorting() -> None:
    wanted = checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de",
                             subject=subject("Frage der Nebelbahn, eine"), config=formats.default_config())
    verdict = checking.judge_units(checking.units_of([recording("Eine Frage der Nebelbahn (ungekürzt)")]), wanted)
    assert verdict.outcome == "take", verdict.code


def test_a_title_written_for_sorting_is_the_title_on_either_side() -> None:
    assert checking.title_against("Frage der Nebelbahn, eine", subject("Eine Frage der Nebelbahn")) == "yes"
    titled = {**subject("x"), "title": "", "titles": [{"text": "Frage der Nebelbahn, eine", "language": "de"}]}
    assert checking.title_against("Eine Frage der Nebelbahn", titled) == "yes"
    named = {**subject("Frage der Nebelbahn, eine"), "titles": []}
    assert checking.title_against("Eine Frage der Nebelbahn", named) == "yes"


def test_the_book_takes_the_title_as_it_is_read() -> None:
    rows = [SimpleNamespace(path="a.epub", format="epub", probe={"title": "Frage der Nebelbahn, eine",
                                                                 "authors": ["Mara Lind"], "format": "epub"},
                            duration_ms=0)]
    assert catalog.build_meta("ebook", rows, [], top="").title == "Eine Frage der Nebelbahn"  # type: ignore[arg-type]
