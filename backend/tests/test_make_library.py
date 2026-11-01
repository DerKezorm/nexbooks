"""The tool that makes a library of invented books for looking at the shelf (``tools/make_library.py``): every book it
makes is a book of its own once read, whatever the format. A maker that gave all AZW3 files one number lost them into
each other when the library was read (the same ISBN is the same book)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Edition
from app.services.readers.text import isbn13
from tools import make_library as tool

from .library_helpers import books_folder, make_library, scan


def spec(number: int, fmt: str) -> dict:  # type: ignore[type-arg]
    return {
        "title": f"Erfundenes Buch {number}", "author": f"Autorin Nummer{number}", "genre": "roman", "color": "#5e2027",
        "pages": 120, "year": 2011, "series": None, "seriesNo": None, "blurb": "Erfunden.",
        "fassungen": [{"kind": "ebook", "lang": "Deutsch", "format": fmt, "pages": 120}],
    }


def test_the_isbns_it_invents_are_valid_and_all_different() -> None:
    made = [tool.invented_isbn(number) for number in range(1, 6000)]
    assert len(set(made)) == len(made)
    assert all(isbn13(isbn) == isbn for isbn in made)


@pytest.mark.parametrize("fmt", ["AZW3", "EPUB", "PDF"])
def test_books_made_one_after_the_other_are_read_as_as_many_books(client: TestClient, operator: Account, fmt: str) -> None:
    out = books_folder("made")
    tool.make_library([spec(number, fmt) for number in range(1, 7)], out / "set")
    library_id = make_library(out, "Made")
    scan(library_id)
    with SessionLocal() as db:
        editions = db.scalars(select(Edition).where(Edition.library_id == library_id)).all()
        assert len(editions) == 6
        assert len({edition.work_id for edition in editions}) == 6, "no two books were put together by a shared number"
        if fmt == "AZW3":
            assert len({edition.isbn for edition in editions}) == 6 and all(edition.isbn for edition in editions)
