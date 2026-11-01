"""The shelf of a person, kept between two requests (``owned.load``): kept while nothing it is made of changes, read
again the moment something does, and never answered for somebody who may not see it. All the books are invented."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import delete, insert, select, update

from app.db import SessionLocal
from app.models import AUTHOR, Credit, Edition, Person, Work
from app.services.discover import owned

from .conftest import make_account
from .library_helpers import books_folder, make_library
from .match_helpers import member


def _work(db, library: int, title: str, author: str, *, isbn: str = "") -> int:  # type: ignore[no-untyped-def]
    """A work with one e-book edition and its author, written the way the scanner writes (Core statements)."""
    key = title.lower()
    work = db.execute(insert(Work).values(title=title, key=key, main_key=key)).inserted_primary_key[0]
    person = db.scalar(select(Person.id).where(Person.name == author))
    if person is None:
        person = db.execute(insert(Person).values(name=author, sort_name=author, key=author.lower())
                            ).inserted_primary_key[0]
    edition = db.execute(insert(Edition).values(work_id=work, library_id=library, kind="ebook", title=title,
                                                language="de", isbn=isbn, title_key=key, shelf_key=key)
                         ).inserted_primary_key[0]
    db.execute(insert(Credit).values(person_id=person, edition_id=edition, work_id=work, role=AUTHOR, position=0))
    db.commit()
    return int(work)


@pytest.fixture
def two() -> tuple[int, int, int]:
    """A library with two books, the account ``reader`` in it, and the account ``other`` in none: ``(library,
    reader, other)``."""
    library = make_library(books_folder("kept"), "Kept")
    reader, other = make_account("reader"), make_account("other")
    member(library, "reader", "read")
    with SessionLocal() as db:
        _work(db, library, "Der Leuchtturmwaerter", "Mara Feldkamp", isbn="9783000000019")
        _work(db, library, "Die Salzwiese", "Ilse Marwitz")
    return library, reader.id, other.id


def test_the_shelf_is_read_once_and_kept_until_something_changes(two: tuple[int, int, int]) -> None:
    library, reader, _other = two
    with SessionLocal() as db:
        first = owned.load(db, reader)
    with SessionLocal() as db:
        assert owned.load(db, reader) is first, "the second look reads nothing"
        assert len(first.works) == 2 and "9783000000019" in first.by_isbn
        # A write to something a shelf is made of (an edition of the library) makes the next look read again.
        _work(db, library, "Nach dem Sturm", "Hans Feldkamp", isbn="9783000000026")
    with SessionLocal() as db:
        second = owned.load(db, reader)
    assert second is not first and len(second.works) == 3 and "9783000000026" in second.by_isbn


@pytest.mark.parametrize("statement", [
    lambda: update(Edition).values(title="Anders", isbn="9783000000040"),
    lambda: update(Person).values(name="Mara Feldkamp-Quist"),
    lambda: delete(Credit),
    lambda: delete(Edition),
    lambda: update(Edition).values(missing_at=None),
], ids=["edition", "person", "credit", "edition-gone", "edition-update-again"])
def test_every_kind_of_write_to_the_tables_of_a_shelf_makes_it_be_read_again(
        two: tuple[int, int, int], statement) -> None:  # type: ignore[no-untyped-def]
    _library, reader, _other = two
    with SessionLocal() as db:
        first = owned.load(db, reader)
    with SessionLocal() as db:
        db.execute(statement())
        db.commit()
    with SessionLocal() as db:
        assert owned.load(db, reader) is not first


def test_a_shelf_that_is_asked_for_stays_for_as_long_as_it_is_asked_for(
        two: tuple[int, int, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """The time to leave counts from the last time the shelf was asked for, not from when it was made."""
    _library, reader, other = two
    with SessionLocal() as db:
        owned.load(db, reader)
        owned.load(db, other)
    made = time.monotonic()
    # The reader's shelf is asked for again after half the time: it has been in use, the other has not.
    monkeypatch.setattr(time, "monotonic", lambda: made + owned.MAX_AGE_SECONDS // 2)
    with SessionLocal() as db:
        kept = owned.load(db, reader)
    assert owned.drop_stale(made + owned.MAX_AGE_SECONDS + 1) == 1
    monkeypatch.setattr(time, "monotonic", lambda: made + owned.MAX_AGE_SECONDS // 2 + 1)
    with SessionLocal() as db:
        assert owned.load(db, reader) is kept
