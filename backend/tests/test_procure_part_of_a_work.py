"""Two volumes in one book: ``Buch 1&2`` of a work of three is a part of it, and a person says whether it will do. Only
invented books."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import EDITION_EBOOK
from app.services import book_words
from app.services.procure import checking, formats


@pytest.mark.parametrize(("text", "part"), [
    ("Die Nebelbahn: Buch 1&2", (1, None)),
    ("Die Nebelbahn Buch 1 & 2", (1, None)),
    ("Die Nebelbahn, Band 1 und 2", (1, None)),
    ("The Fog Line, Book 2 and 3", (2, None)),
])
def test_two_volumes_in_one_book_are_a_part(text: str, part: tuple[int, None]) -> None:
    assert book_words.part_of(text) == part


@pytest.mark.parametrize("text", ["Die Nebelbahn: Buch 1 5", "Die Nebelbahn 1Q84", "Das Buch 12"])
def test_numbers_that_do_not_follow_each_other_are_not(text: str) -> None:
    assert book_words.part_of(text) is None


def verdict_on(title: str, wished: str = "Die Nebelbahn") -> checking.Verdict:
    item = SimpleNamespace(path="a.epub", format="epub", size=2_000_000, origin="source", unit="",
                           probe={"format": "epub", "title": title, "authors": ["Mara Lind"], "language": "de"})
    wanted = checking.Wanted(kind=EDITION_EBOOK, language="de", accepted=["de"], named_language="de",
                             subject={"title": wished, "titles": [{"text": wished, "language": "de"}],
                                      "authors": ["Mara Lind"], "series": "", "position": None},
                             config=formats.default_config())
    return checking.judge_units(checking.units_of([item]), wanted)


def test_a_book_of_two_volumes_of_three_is_a_question() -> None:
    verdict = verdict_on("Die Nebelbahn: Buch 1&2")
    assert (verdict.outcome, verdict.code) == ("ask", "partial")


def test_the_whole_book_is_no_question() -> None:
    assert verdict_on("Die Nebelbahn").outcome == "take"
