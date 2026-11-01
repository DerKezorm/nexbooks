"""A file whose title and author stand in each other's fields: the names of the file or the wish say so, and the book is
not turned down for it. Only invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import catalog
from app.services.procure import checking, formats

from .filing_helpers import download_roots, ebook_world, editions, finish, german_epub, the_download, works
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE


def subject(title: str, authors: tuple[str, ...] = ("Mara Lind",)) -> dict[str, Any]:
    return {"title": title, "titles": [{"text": title, "language": "de"}], "authors": list(authors), "series": "",
            "position": None}


def meta_of(path: str, title: str, authors: list[str]) -> catalog.Meta:
    rows = [SimpleNamespace(path=path, format="epub", probe={"title": title, "authors": authors, "format": "epub"},
                            duration_ms=0)]
    return catalog.build_meta("ebook", rows, [], top="")  # type: ignore[arg-type]


@pytest.mark.parametrize("path", ["Mara Lind - Die Stille unter der Nebelbahn.epub",
                                  "Mara Lind/Die Stille unter der Nebelbahn (2020).epub",
                                  "Die Stille unter der Nebelbahn.epub"])
def test_the_fields_turned_around_are_seen_by_the_names_of_the_file(path: str) -> None:
    meta = meta_of(path, "Mara Lind", ["Die Stille unter der Nebelbahn"])
    assert (meta.title, meta.authors) == ("Die Stille unter der Nebelbahn", ["Mara Lind"])


@pytest.mark.parametrize(("path", "title", "authors"), [
    ("Mara Lind.epub", "Anna Marten", ["Mara Lind"]),
    ("x1f3.epub", "Anna Marten", ["Die Stille unter der Nebelbahn"]),
    ("Die Stille unter der Nebelbahn.epub", "Die Stille unter der Nebelbahn", ["Mara Lind"]),
    ("Die Stille unter der Nebelbahn.epub", "Der kurze Weg", ["Die Stille unter der Nebelbahn"]),
    ("Die Stille unter der Nebelbahn/Mara Lind.epub", "Mara Lind", ["Die Stille unter der Nebelbahn"]),
])
def test_fields_nothing_says_are_turned_around_stay(path: str, title: str, authors: list[str]) -> None:
    meta = meta_of(path, title, authors)
    assert (meta.title, meta.authors) == (title, authors)


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_book_with_its_fields_turned_around_is_the_book_wished(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / "Release"
    german_epub(folder, name="x1f3.epub", title=AUTHOR, authors=(TITLE,), series=None, series_pos=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    filed = [item for item in editions() if item.kind == "ebook"][-1]
    assert filed.title == TITLE
    assert TITLE in {work.title for work in works()}


def test_fields_turned_around_with_the_name_written_without_its_marks_are_the_author_wished() -> None:
    wished = subject("Die Stille unter der Nebelbahn", ("Mara Länd",))
    meta = meta_of("x1f3.epub", "Land, Mara", ["Die Stille unter der Nebelbahn"])
    checking._turn_fields_round(meta, wished)
    assert (meta.title, meta.authors) == ("Die Stille unter der Nebelbahn", ["Land, Mara"])
    assert checking._authors(meta, ["Mara Länd"]) == "yes"


def test_another_book_of_the_author_does_not_pass_for_turned_around() -> None:
    item = SimpleNamespace(path="x.epub", format="epub", size=2_000_000, origin="source", unit="",
                           probe={"format": "epub", "title": "Mara Lind", "authors": ["Der kurze Weg"], "language": "de"})
    wanted = checking.Wanted(kind="ebook", language="de", accepted=["de"], subject=subject("Die Nebelbahn"),
                             config=formats.default_config())
    verdict = checking.judge_units(checking.units_of([item]), wanted)
    assert (verdict.outcome, verdict.code) == ("reject", "author_mismatch")
