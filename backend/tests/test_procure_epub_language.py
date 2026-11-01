"""The language of an e-book after the download (a German retail EPUB whose package says Spanish must not be turned
down as Spanish).

What the package of an EPUB says (``dc:language``) is not the only witness: a sample of its text and the name of the
release say a language too. When they contradict each other the release is never turned down; a person decides. Only
invented books, made here.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.models import BlocklistEntry
from app.services.readers import epub

from .bookmaker import make_epub
from .filing_helpers import download_roots, ebook_world, finish, german_epub, the_download
from .follow_helpers import rows
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item

GERMAN = ("Der Regen fiel auf die alte Straße, und die Laternen waren noch nicht an. Sie ging mit dem Hund nach Hause, "
          "weil es schon spät war und der Wind über die Felder kam. Im Haus war es still, nur die Uhr in der Küche "
          "schlug, als er die Tür hinter sich schloss und sich an den Tisch setzte.")
SPANISH = ("El viento de la tarde llegó por el camino y la casa estaba en silencio. Ella no sabía por qué, pero los "
           "perros de los vecinos se quedaron con su madre en el patio, como cada noche cuando el pueblo duerme y "
           "nadie sale a la calle.")
UNMARKED = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.EPUB-EXAMPLE"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def read(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return epub.read(handle)


def test_the_reading_tells_the_language_of_the_text_beside_the_one_of_the_package(tmp_path: Path) -> None:
    found = read(make_epub(tmp_path / "a.epub", language="es", prose=GERMAN, text_bytes=9000))
    assert found["language"] == "es" and found["text_language"] == "de"
    assert read(make_epub(tmp_path / "b.epub", language="de", prose=GERMAN))["text_language"] == "de"
    assert read(make_epub(tmp_path / "c.epub", language="es", prose=SPANISH))["text_language"] == "es"
    # Latin filler: words of no language the sample knows, a witness against every one of them.
    assert read(make_epub(tmp_path / "d.epub", language="de", prose="Lorem ipsum dolor sit amet."))[
        "text_language"] == "other"
    # Too little text says nothing.
    assert read(make_epub(tmp_path / "e.epub", language="de", prose="Ja.", text_bytes=40,
                          chapters=("Eins",)))["text_language"] == ""


@pytest.mark.parametrize("first", ["", "und", "mul", "zxx", "xx"])
def test_a_package_language_that_says_nothing_does_not_hide_the_one_after_it(tmp_path: Path, first: str) -> None:
    path = make_epub(tmp_path / "a.epub", language=None,
                     extra_opf=f"<dc:language>{first}</dc:language><dc:language>de</dc:language>")
    assert read(path)["language"] == "de"


def test_a_package_that_names_two_languages_says_both(tmp_path: Path) -> None:
    path = make_epub(tmp_path / "a.epub", language="es", extra_opf="<dc:language>de</dc:language>")
    found = read(path)
    assert found["language"] == "es" and found["languages"] == ["es", "de"]


def test_only_a_bounded_sample_of_the_text_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_epub(tmp_path / "a.epub", language="de", prose=GERMAN, text_bytes=4 * 1024 * 1024,
                     chapters=("Eins", "Zwei"))
    read_sizes: list[int] = []
    real = zipfile.ZipExtFile.read

    def counting(self: zipfile.ZipExtFile, n: int | None = -1) -> bytes:
        data = real(self, n)
        if "/text/" in self.name:
            read_sizes.append(len(data))
        return data

    monkeypatch.setattr(zipfile.ZipExtFile, "read", counting)
    assert read(path)["text_language"] == "de"
    assert read_sizes and max(read_sizes) <= epub.TEXT_SAMPLE


def test_a_german_book_whose_package_says_spanish_goes_in(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, language="es", prose=GERMAN, text_bytes=9000)
    finish(sab, job, folder)
    assert the_download().state == "imported", the_download().problem_code
    assert rows(BlocklistEntry) == []


def test_a_german_book_whose_package_says_dutch_is_never_turned_down(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, language="nl", prose=GERMAN, text_bytes=9000)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "language_unclear", (row.state, row.failed_reason)
    assert row.problem_values == {"file": "nl", "text": "de", "name": "", "folder": "", "wanted": "de"}
    assert rows(BlocklistEntry) == []
    shown = world.client.get("/api/v1/procure/problems").json()
    assert [item["code"] for item in shown["problems"]] == ["language_unclear"]
    taken = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"})
    assert taken.status_code == 200, taken.text
    from .library_helpers import run_jobs

    run_jobs()
    assert the_download().state == "imported"


def test_a_release_named_german_whose_package_says_spanish_is_never_turned_down(operator: Any, net: FakeNetwork,
                                                                              roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, language="es")
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "language_unclear", (row.state, row.failed_reason)
    # The text is in the language of the package: two witnesses for Spanish, the name for German. A question.
    assert row.problem_values["name"] == "de" and row.problem_values["text"] == "es"


def test_a_spanish_book_in_an_unmarked_release_is_turned_down_as_before(operator: Any, net: FakeNetwork,
                                                                       roots: Path) -> None:
    _world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    german_epub(folder, language="es", prose=SPANISH, text_bytes=9000)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "rejected" and row.failed_reason == "other_language", (row.state, row.problem_code)
    [block] = rows(BlocklistEntry)
    assert block.reason == "other_language:es"
