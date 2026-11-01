"""What a package or a tag says of the language against the rest of the witnesses: tools write ``en`` into German books and
``eng`` into every tag. Where the rest outweighs it nobody is asked, the book keeps the language it is in, and a book in
another language is still a question. Only invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO, EDITION_EBOOK
from app.services.match import overlay
from app.services.procure import checking, formats, judge
from app.services.procure import release_match as rm
from app.services.procure import release_name as rn

from .filing_helpers import download_roots, ebook_world, editions, finish, german_epub, the_download
from .procure_fakes import FakeNetwork


def subject(*titles: tuple[str, str]) -> dict[str, Any]:
    return {"title": titles[0][0], "titles": [{"text": text, "language": code} for text, code in titles],
            "authors": ["Mara Lind"], "series": "", "position": None}


def wanted(kind: str, name: str = "", **values: Any) -> checking.Wanted:
    values.setdefault("subject", subject(("Die Nebelbahn", "de")))
    return checking.Wanted(kind=kind, language="de", accepted=["de"], named_language=name,
                           config=formats.default_config(), **values)


def ebook(package: str = "", text: str = "", clear: str = "") -> list[SimpleNamespace]:
    return [SimpleNamespace(path="a.epub", format="epub", size=2_000_000, origin="source", unit="",
                            probe={"format": "epub", "title": "Die Nebelbahn", "authors": ["Mara Lind"],
                                   "language": package, "text_language": text, "text_language_clear": clear})]


def recording(tag: str = "", title: str = "Die Nebelbahn (ungekürzt)", **names: Any) -> list[SimpleNamespace]:
    return [SimpleNamespace(path=names.get("path", "01.mp3"), format="mp3", size=3600 * 16000, origin="source", unit="",
                            probe={"format": "mp3", "duration": 3600.0, "album": title, "title": title,
                                   "authors": ["Mara Lind"], "language": tag})]


def verdict_on(files: list[SimpleNamespace], found: checking.Wanted) -> checking.Verdict:
    return checking.judge_units(checking.units_of(files), found)


@pytest.mark.parametrize("package", ["en", "el", "es", "fr"])
def test_a_german_text_beyond_doubt_outweighs_a_wrong_package(package: str) -> None:
    verdict = verdict_on(ebook(package, "de", "de"), wanted(EDITION_EBOOK, "de"))
    assert verdict.outcome == "take" and verdict.language == "de"


def test_a_text_that_only_leans_to_a_language_does_not() -> None:
    verdict = verdict_on(ebook("en", "de", ""), wanted(EDITION_EBOOK, "de"))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unclear")


def test_a_language_next_to_it_stays_a_question() -> None:
    verdict = verdict_on(ebook("nl", "de", "de"), wanted(EDITION_EBOOK, "de"))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unclear")


def test_an_english_book_in_a_release_named_german_stays_a_question() -> None:
    verdict = verdict_on(ebook("en", "en", "en"), wanted(EDITION_EBOOK, "de"))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unclear")


def test_a_name_that_says_another_language_than_the_text_stays_a_question() -> None:
    verdict = verdict_on(ebook("en", "de", "de"), wanted(EDITION_EBOOK, "fr"))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unclear")


def test_a_language_nobody_wished_does_not_outweigh_the_package() -> None:
    verdict = verdict_on(ebook("fr", "en", "en"), wanted(EDITION_EBOOK, "en"))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unclear")


def test_a_tag_of_a_recording_whose_title_is_not_the_book_is_not_outweighed() -> None:
    [unit] = checking.units_of(recording("en"))
    unit.title = "unknown"
    assert checking._wrong_package(unit, wanted(EDITION_AUDIO, "de"), ["de"], {"en", "de"}) == ""
    unit.title = "yes"
    assert checking._wrong_package(unit, wanted(EDITION_AUDIO, "de"), ["de"], {"en", "de"}) == "de"


def test_a_book_all_witnesses_call_english_is_turned_down() -> None:
    verdict = verdict_on(ebook("en", "en", "en"), wanted(EDITION_EBOOK, ""))
    assert (verdict.outcome, verdict.code) == ("reject", "other_language")


def test_a_text_of_no_known_words_is_no_witness_beside_a_package_and_a_name() -> None:
    assert verdict_on(ebook("de", "other"), wanted(EDITION_EBOOK, "de")).outcome == "take"


def test_a_text_of_no_known_words_alone_is_still_a_question() -> None:
    assert verdict_on(ebook("de", "other"), wanted(EDITION_EBOOK, "")).code == "language_unclear"
    assert verdict_on(ebook("", "other"), wanted(EDITION_EBOOK, "de")).code == "language_unclear"


def test_a_text_in_two_languages_stays_a_question() -> None:
    assert verdict_on(ebook("de", "mixed"), wanted(EDITION_EBOOK, "de")).code == "language_unclear"


def test_a_german_name_and_a_german_title_outweigh_a_tag_that_says_english() -> None:
    verdict = verdict_on(recording("en"), wanted(EDITION_AUDIO, "de"))
    assert verdict.outcome == "take" and verdict.language == "de"


def test_a_title_of_both_languages_says_no_language() -> None:
    both = subject(("Die Nebelbahn", "de"), ("Die Nebelbahn", "en"))
    verdict = verdict_on(recording("en"), wanted(EDITION_AUDIO, "de", subject=both))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unclear")


def test_a_tag_against_a_name_without_the_title_stays_a_question() -> None:
    verdict = verdict_on(recording("en", title="Teil 1"), wanted(EDITION_AUDIO, "de"))
    assert verdict.outcome == "ask"


def test_a_book_of_the_language_it_came_out_in_needs_no_word_for_it() -> None:
    assert verdict_on(recording(""), wanted(EDITION_AUDIO, "", original="de")).outcome == "take"


def test_a_translation_nothing_says_the_language_of_stays_a_question() -> None:
    verdict = verdict_on(recording(""), wanted(EDITION_AUDIO, "", original="en"))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unknown")


def test_a_title_known_abroad_needs_a_word_also_from_a_book_of_that_language() -> None:
    both = subject(("Die Nebelbahn", "de"), ("Die Nebelbahn", "nl"))
    verdict = verdict_on(recording(""), wanted(EDITION_AUDIO, "", original="de", subject=both))
    assert (verdict.outcome, verdict.code) == ("ask", "language_unknown")


@pytest.mark.parametrize("name", ["Lena French - Die Nebelbahn - Hoerbuch MP3", "Lena.French.Die.Nebelbahn.EPUB"])
def test_a_word_of_a_language_in_the_name_of_the_author_is_no_mark_after_the_download(name: str) -> None:
    book = {**subject(("Die Nebelbahn", "de")), "authors": ["Lena French"]}
    parsed = rn.parse(name)
    code, how = rn.language_of(parsed.language_signs, judge.names_of_the_book(
        rm.match(parsed, checking.book_of(book))))
    assert code != "fr", (code, how)


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


GERMAN = (" Der Mann ging in das Haus und sah sich um, denn er wollte nicht, dass sie es wusste. Sie aber war schon "
          "lange bei ihm und auch die anderen kamen mit, weil es so kalt war und der Weg nach Hause noch weit. ") * 12


def test_a_german_book_with_a_wrong_package_goes_in_as_german(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / "Rune Falkner - Die Laternenstrasse - GERMAN"
    german_epub(folder, language="el", prose=GERMAN)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)
    book = [item for item in editions() if item.kind == "ebook"][-1]
    assert book.language == "de" and "language" in overlay.hand_fields(book.overlay)
