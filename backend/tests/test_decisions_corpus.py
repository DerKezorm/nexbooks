"""The decisions nexbooks takes without a person, held against cases shaped like real data (``fixtures/decisions.json``):
whether a release is the book wished, whether the files after a download are, whether two titles are one work, two
names one person, whether an author field names a person, the core of a title, and the chapters of a recording. Every
case says what it is about (``notes``) and whether its shape was seen in real data or made after a pattern
(``source``). Rule of all of them: act only with a strong sign for it and none against; else ask."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import catalog, chapter_groups
from app.services.procure import checking, formats, judge
from app.services.procure import release_match as rm
from app.services.procure import release_name as rn
from app.services.readers import names

CASES = json.loads((Path(__file__).parent / "fixtures" / "decisions.json").read_text(encoding="utf-8"))["cases"]
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def of(stage: str) -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in CASES if case["stage"] == stage]


def subject(wish: dict[str, Any]) -> dict[str, Any]:
    return {"title": wish["title"], "titles": [{"text": text, "language": language} for text, language in wish["titles"]],
            "authors": wish["authors"], "series": wish["series"], "position": wish["position"],
            "others": wish["others"], "language": wish["language"]}


def test_the_cases_name_their_source_and_say_what_they_are_about() -> None:
    assert len(CASES) >= 80
    assert {case["source"] for case in CASES} <= {"observed", "shaped"}
    assert all(case["notes"].strip() for case in CASES)
    assert len({case["id"] for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", of("release"))
def test_a_release_name(case: dict[str, Any]) -> None:
    wish = case["wish"]
    book = rm.Book(titles=tuple(rm.Title(text, language) for text, language in wish["titles"]),
                   authors=tuple(wish["authors"]), series=wish["series"], position=wish["position"],
                   others=tuple(wish["others"]))
    name = case["input"]["release"]
    parsed = rn.parse(name)
    verdict = judge.judge(parsed, judge.Offer(title=name, size=case["input"]["size"], published_at=None,
                                              protocol="usenet"),
                          rm.match(parsed, book), judge.Wanted(book=book, language=wish["language"]),
                          formats.default_config(), NOW)
    codes = [item["code"] for item in verdict.reasons]
    head, _, code = case["expected"].partition(":")
    if head == "pass":
        assert verdict.passes, codes
    elif head == "ask":
        assert verdict.doubtful and code in codes, codes
    else:
        assert not verdict.passes and not verdict.doubtful, codes
        assert not code or code in codes, codes


@pytest.mark.parametrize("case", of("after_download"))
def test_the_files_after_a_download(case: dict[str, Any]) -> None:
    wish = case["wish"]
    meta = catalog.Meta(kind="ebook", title=case["input"]["file_title"], authors=case["input"]["file_authors"],
                        language=wish["language"])
    item = SimpleNamespace(path="Buch/buch.epub", format="epub", probe={"text_language": wish["language"]}, size=1,
                           origin="source", unit="u")
    unit = checking.Unit(key="u", kind="ebook", files=[item], meta=meta)
    wanted = checking.Wanted(kind="ebook", language=wish["language"], accepted=[wish["language"]],
                             subject=subject(wish), config=formats.default_config(), named_author=True)
    verdict = checking.judge_units([unit], wanted)
    got = verdict.outcome + (f":{verdict.code}" if verdict.code else "")
    assert got == case["expected"]


@pytest.mark.parametrize("case", of("grouping"))
def test_two_titles(case: dict[str, Any]) -> None:
    more: dict[str, Any] = {}
    if case["id"] == "grouping-06":
        more = {"series": ("Nora Nachtfalter",), "position": 2.0}
    same = catalog.same_core(case["input"]["title_a"], case["input"]["title_b"], **more)
    assert ("one_work" if same else "two_works") == case["expected"]


@pytest.mark.parametrize("case", of("people"))
def test_two_names(case: dict[str, Any]) -> None:
    same = names.authors_compatible(case["input"]["name_a"], case["input"]["name_b"])
    expected = case["expected"]
    if expected == "one_person_with_proof":
        # The last name alone is one person only with proof (a list of works), never by the names.
        assert not same
    else:
        assert ("one_person" if same else "two_people") == expected


@pytest.mark.parametrize("case", of("author_field"))
def test_an_author_field(case: dict[str, Any]) -> None:
    field = case["input"]["field"]
    found = [name for name in names.split_authors(field) if not names.not_a_person(name)]
    got = "no_person" if not found else ("company" if names.is_company(field) else "person")
    assert got == case["expected"]


@pytest.mark.parametrize("case", of("title"))
def test_the_core_of_a_title(case: dict[str, Any]) -> None:
    title = case["input"]["title"]
    author = "Simon Krieger" if title.startswith("Simon Krieger") else ""
    assert names.core_title(title, author) == case["expected"]


@pytest.mark.parametrize("case", of("chapters"))
def test_the_chapters_of_a_recording(case: dict[str, Any]) -> None:
    found = [[first, count, title] for first, count, title in
             chapter_groups.group(case["input"]["file_titles"], case["input"]["book"])]
    assert found == case["expected"]
