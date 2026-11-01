"""The names nexbooks gives its own files are read again by the scan and after a filing: ``02 - Titel (2020)`` and a number
of four digits of a series of plays say the place of the book, never its title. Only invented books."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import catalog
from app.services.readers import names


@pytest.mark.parametrize(("name", "title", "position"), [
    ("01 - Die Nebelbahn", "Die Nebelbahn", 1.0),
    ("12 - Die Nebelbahn (2003)", "Die Nebelbahn", 12.0),
    ("2473 - Die Nebelbahn 451 (2008)", "Die Nebelbahn 451", 2473.0),
    ("1984 - Mara Lind", "1984", None),
    ("1984 - Mara Lind (2003)", "1984", None),
    ("1984", "1984", None),
    ("2473 - Die Nebelbahn 451", "2473", None),
    ("Die Nebelbahn", "Die Nebelbahn", None),
])
def test_a_name_with_a_number_in_front(name: str, title: str, position: float | None) -> None:
    parts = names.parse_name(name)
    assert (parts.title, parts.pos) == (title, position)


def test_the_author_behind_the_number_stays_the_author() -> None:
    parts = names.parse_name("07 - Mara Lind - Die Nebelbahn")
    assert (parts.title, parts.author, parts.pos) == ("Die Nebelbahn", "Mara Lind", 7.0)


@pytest.mark.parametrize(("folder", "title", "position"), [
    ("2473 - Die Nebelbahn 451 (2008)", "Die Nebelbahn 451", 2473.0),
    ("02 - Die Nebelbahn (2003)", "Die Nebelbahn", 2.0),
])
def test_the_folder_of_a_book_with_a_number_in_front(folder: str, title: str, position: float) -> None:
    found = names.folder_book(folder, depth=3, parent="Hoerspiele")
    assert (found.title, found.pos) == (title, position)


@pytest.mark.parametrize("folder", ["2473 - Die Nebelbahn 451", "1984 - Mara Lind (2003)"])
def test_a_year_or_a_person_keeps_a_number_from_being_a_place(folder: str) -> None:
    assert names.folder_book(folder, depth=2).pos not in (2473.0, 1984.0)


def recording(path: str, album: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(path=path, format="mp3", duration_ms=600_000,
                            probe={"album": album, "title": "Kapitel 1", "authors": ["Mara Lind"], "format": "mp3"})]


def test_the_name_of_its_own_folder_does_not_beat_the_tags() -> None:
    rows = recording("Mara Lind/Hoerspiele/2473 - Die Nebelbahn 451 (2008)/01.mp3", "Die Nebelbahn 451")
    meta = catalog.build_meta("audio", rows, [], top="")  # type: ignore[arg-type]
    assert meta.title == "Die Nebelbahn 451"
    assert meta.series != "Die Nebelbahn"


def test_a_folder_that_is_only_a_number_does_not_beat_the_tags() -> None:
    rows = recording("Mara Lind/2473 (2008)/01.mp3", "Die Nebelbahn")
    assert catalog.build_meta("audio", rows, [], top="").title == "Die Nebelbahn"  # type: ignore[arg-type]


def test_an_ebook_named_by_the_scheme_without_a_title_of_its_own() -> None:
    rows = [SimpleNamespace(path="Mara Lind/Nebelchor/01 - Die Nebelbahn (2003).epub", format="epub", duration_ms=0,
                            probe={"format": "epub", "authors": ["Mara Lind"]})]
    assert catalog.build_meta("ebook", rows, [], top="").title == "Die Nebelbahn"  # type: ignore[arg-type]
