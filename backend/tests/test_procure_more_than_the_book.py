"""A file whose title says it is more than the book wished, or something else: a person looks. Only invented books."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import EDITION_EBOOK
from app.services.procure import checking, formats


def verdict_on(title: str, wished: str = "Die Nebelbahn") -> checking.Verdict:
    item = SimpleNamespace(path="book.epub", format="epub", size=500_000, origin="source", unit="",
                           probe={"format": "epub", "title": title, "authors": ["Mara Lind"], "language": "de"})
    wanted = checking.Wanted(kind=EDITION_EBOOK, language="de", accepted=["de"], named_language="de",
                             subject={"title": wished, "titles": [{"text": wished, "language": "de"}],
                                      "authors": ["Mara Lind"], "series": "", "position": None},
                             config=formats.default_config())
    return checking.judge_units(checking.units_of([item]), wanted)


@pytest.mark.parametrize("title", ["Die Nebelbahn - Gesamtausgabe", "Die Nebelbahn: Sammelband",
                                   "Die Nebelbahn (Box Set)", "Die Nebelbahn - Das Hörspiel",
                                   "Die Nebelbahn (Hörspiel)"])
def test_it_does_not_go_in_unasked(title: str) -> None:
    verdict = verdict_on(title)
    assert (verdict.outcome, verdict.code) == ("ask", "edition"), (title, verdict.code)


@pytest.mark.parametrize("title", ["Die Nebelbahn - Gesamtausgabe", "Das Hörspiel"])
def test_the_title_wished_may_say_it_itself(title: str) -> None:
    assert verdict_on(title, wished=title).outcome == "take"


def test_the_plain_book_goes_in() -> None:
    assert verdict_on("Die Nebelbahn").outcome == "take"
