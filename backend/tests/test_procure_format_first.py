"""A release that holds nothing the operator takes is turned down for that, before anybody is asked about its author or
its title: the answer to such a question could not bring the book in. Only invented books."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO, EDITION_EBOOK
from app.services.procure import checking, formats


def wanted(kind: str = EDITION_EBOOK, **values: Any) -> checking.Wanted:
    return checking.Wanted(kind=kind, language="de", accepted=["de"], named_language="de", named_author=True,
                           subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                    "authors": ["Mara Lind"], "series": "", "position": None},
                           config=formats.default_config(), **values)


def book(fmt: str, path: str = "Die Nebelbahn", **probe: Any) -> SimpleNamespace:
    values = {"format": fmt, "title": "Die Nebelbahn", "authors": ["Lind Mara Xaver"], "language": "de", **probe}
    return SimpleNamespace(path=f"{path}.{fmt}", format=fmt, size=2_000_000, origin="source", unit="", probe=values)


@pytest.mark.parametrize("author", ["dfg", "Ida Brandt", "Lind Mara Xaver"])
def test_a_pdf_alone_is_turned_down_for_its_format_whoever_it_names(author: str) -> None:
    verdict = checking.judge_units(checking.units_of([book("pdf", authors=[author])]), wanted())
    assert (verdict.outcome, verdict.code, verdict.values["found"]) == ("reject", "format_not_wanted", ["pdf"])


def test_an_epub_beside_it_is_still_held_against_the_book() -> None:
    files = [book("pdf"), book("epub")]
    verdict = checking.judge_units(checking.units_of(files), wanted())
    assert (verdict.outcome, verdict.code) == ("ask", "pen_name")


def test_a_thin_recording_is_turned_down_for_its_format_before_its_author() -> None:
    item = book("mp3", album="Die Nebelbahn", duration=3600.0)
    item.size = int(3600 * 64 * 1000 / 8)
    verdict = checking.judge_units(checking.units_of([item]), wanted(EDITION_AUDIO))
    assert (verdict.outcome, verdict.code, verdict.values["found"]) == ("reject", "format_not_wanted", ["mp3_64"])


def test_a_recording_whose_rate_nothing_says_comes_to_the_question_about_its_author() -> None:
    item = book("mp3", album="Die Nebelbahn", duration=0.0, authors=["Ida Brandt"])
    item.size = 0
    verdict = checking.judge_units(checking.units_of([item]), wanted(EDITION_AUDIO))
    assert (verdict.outcome, verdict.code) == ("ask", "pen_name")


def test_a_persons_own_upload_is_not_held_back_by_the_ranking() -> None:
    verdict = checking.judge_units(checking.units_of([book("pdf", authors=["Mara Lind"])]), wanted(upload=True))
    assert verdict.outcome == "take"
