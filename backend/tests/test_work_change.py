"""An edition that is not the book of its work any more leaves it (``catalog.leave_foreign_work``).

A match that was wrong once put an edition under the work of another book of the same author. The right answer has changed
what the edition is, but the edition kept standing in the old work, next to the editions that really are that book. When
the books are put together again (a newer generation of the rules, a new match, a changed file) the edition moves to the
work it fits, or gets one of its own; the place in the book, the bookmarks and the history hang on the edition and go with
it. An edition a person placed stays where it was put."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app import clock
from app.db import SessionLocal
from app.models import Bookmark, Edition, ExternalRef, HistoryEntry, Progress, Work
from app.services import catalog, scanner, settings_service
from app.services.match import apply, overlay

from .conftest import make_account
from .library_helpers import book, books_folder, make_library, recording, run_jobs, scan

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


class Shelf:
    """Two books by one author and a recording of the second, scanned: three editions in two works. ``wrong`` is the
    e-book of the second book, put by hand-made data under the work of the first, as a wrong match once did."""

    def __init__(self, grouped_by: str = "auto") -> None:
        folder = books_folder("work-change")
        book(folder, "Ada Feldkamp/Harbour Lights/harbour.epub", title="Harbour Lights", authors=("Ada Feldkamp",),
             language="en", text_bytes=3000)
        book(folder, "Ada Feldkamp/Salt Marsh/salt.epub", title="Salt Marsh", authors=("Ada Feldkamp",),
             language="en", text_bytes=3000)
        recording(folder, "Ada Feldkamp/Salt Marsh Audio/salt.m4b", 600.0, title="Salt Marsh", album="Salt Marsh",
                  artist="Ada Feldkamp", narrator="Rune Falkner")
        recording(folder, "Ada Feldkamp/Harbour Lights Audio/harbour.m4b", 600.0, title="Harbour Lights",
                  album="Harbour Lights", artist="Ada Feldkamp", narrator="Rune Falkner")
        self.library_id = make_library(folder, "Wrong", scan=True)
        self.reader = make_account("wanda")
        with SessionLocal() as db:
            editions = {(e.title, e.kind): e for e in db.scalars(select(Edition))}
            self.first = editions[("Harbour Lights", "ebook")].id
            self.wrong = editions[("Salt Marsh", "ebook")].id
            self.audio = editions[("Salt Marsh", "audio")].id
            self.first_audio = editions[("Harbour Lights", "audio")].id
            assert editions[("Harbour Lights", "audio")].work_id == editions[("Harbour Lights", "ebook")].work_id
            self.first_work = editions[("Harbour Lights", "ebook")].work_id
            self.right_work = editions[("Salt Marsh", "audio")].work_id
            assert editions[("Salt Marsh", "ebook")].work_id == self.right_work
            # What a wrong match once did: the e-book of one book stands in the work of the other.
            edition = db.get(Edition, self.wrong)
            edition.work_id = self.first_work  # type: ignore[union-attr]
            edition.grouped_by = grouped_by  # type: ignore[union-attr]
            db.add(Progress(user_id=self.reader.id, edition_id=self.wrong, locator="epubcfi(/6/4)", fraction=0.4,
                            moved_at=clock.now(), received_at=clock.now()))
            db.add(Bookmark(user_id=self.reader.id, edition_id=self.wrong, locator="epubcfi(/6/8)", fraction=0.5))
            db.add(HistoryEntry(user_id=self.reader.id, edition_id=self.wrong, kind="read", finished_on="2026-09-01",
                                source="hand", created_at=WHEN))
            db.commit()

    def work_of(self, edition_id: int) -> int:
        with SessionLocal() as db:
            return db.get(Edition, edition_id).work_id  # type: ignore[union-attr]


def reassemble() -> None:
    """The next generation of the rules of putting books together (what the clock does once for every database)."""
    with SessionLocal() as db:
        settings_service.save(db, {"assemble_rev": scanner.ASSEMBLE_REV - 1})
        db.commit()
        assert scanner.reassemble_if_due(db) is True
    run_jobs()


@pytest.fixture
def shelf() -> Shelf:
    return Shelf()


def test_an_edition_in_a_work_of_another_book_moves_to_the_work_it_fits_when_the_books_are_put_together_again(
        shelf: Shelf) -> None:
    assert shelf.work_of(shelf.wrong) == shelf.first_work
    reassemble()
    assert shelf.work_of(shelf.wrong) == shelf.right_work == shelf.work_of(shelf.audio)
    assert shelf.work_of(shelf.first) == shelf.first_work
    with SessionLocal() as db:
        # The work it left keeps its own book; both works show what their editions say.
        assert db.get(Work, shelf.first_work).title == "Harbour Lights"  # type: ignore[union-attr]
        assert db.get(Work, shelf.right_work).title == "Salt Marsh"  # type: ignore[union-attr]
        # What hangs on the edition went with it: the place in the book, the mark and the finished pass.
        assert db.get(Progress, (shelf.reader.id, shelf.wrong)).fraction == 0.4  # type: ignore[union-attr]
        assert db.scalar(select(Bookmark.id).where(Bookmark.edition_id == shelf.wrong)) is not None
        entry = db.scalars(select(HistoryEntry)).one()
        assert entry.edition_id == shelf.wrong and entry.work_id == shelf.right_work


def test_an_edition_with_no_work_to_go_to_gets_one_of_its_own() -> None:
    shelf = Shelf()
    with SessionLocal() as db:
        # The recording and the other e-book of the right work are gone: nowhere to go but a work of its own.
        db.execute(delete(Edition).where(Edition.id == shelf.audio))
        db.commit()
        db.execute(delete(Work).where(Work.id == shelf.right_work))
        db.commit()
    reassemble()
    own = shelf.work_of(shelf.wrong)
    assert own != shelf.first_work
    assert shelf.work_of(shelf.first) == shelf.first_work
    with SessionLocal() as db:
        assert db.get(Work, own).title == "Salt Marsh"  # type: ignore[union-attr]
        assert set(db.scalars(select(Edition.id).where(Edition.work_id == shelf.first_work))) == {shelf.first, shelf.first_audio}


def test_an_edition_a_person_placed_stays_where_it_was_put() -> None:
    shelf = Shelf(grouped_by="hand")
    reassemble()
    assert shelf.work_of(shelf.wrong) == shelf.first_work


def test_an_edition_that_a_source_calls_by_the_name_of_the_work_stays_in_it() -> None:
    shelf = Shelf()
    with SessionLocal() as db:
        for edition in (shelf.first, shelf.wrong):
            db.add(ExternalRef(edition_id=edition, source="hardcover", kind="work", key="777"))
        db.commit()
    reassemble()
    assert shelf.work_of(shelf.wrong) == shelf.first_work


def test_the_translation_of_a_book_stays_with_its_original() -> None:
    shelf = Shelf()
    with SessionLocal() as db:
        edition = db.get(Edition, shelf.wrong)
        assert edition is not None
        edition.overlay = overlay.with_source(edition.overlay, {"original_title": "Harbour Lights"},
                                              origins={"original_title": "hardcover"})
        db.commit()
    reassemble()
    assert shelf.work_of(shelf.wrong) == shelf.first_work


def test_a_new_match_puts_the_edition_where_the_answer_says(shelf: Shelf) -> None:
    """The way a match goes: the edition is put together again out of its files and its overlay, and it leaves."""
    with SessionLocal() as db:
        edition = db.get(Edition, shelf.wrong)
        assert edition is not None
        apply.rebuild(db, edition)
        db.commit()
    assert shelf.work_of(shelf.wrong) == shelf.right_work


def test_an_edition_that_stands_alone_is_left_where_it_is() -> None:
    shelf = Shelf()
    with SessionLocal() as db:
        db.execute(delete(Edition).where(Edition.id.in_([shelf.first, shelf.first_audio])))
        db.commit()
    reassemble()
    assert shelf.work_of(shelf.wrong) == shelf.first_work
    assert scan(shelf.library_id).report["aborted"] is None


def test_the_rules_of_putting_books_together_are_a_newer_generation_than_before_the_work_change() -> None:
    """Databases that were made before need the one-time put-together to find the editions in a work of another book."""
    assert scanner.ASSEMBLE_REV >= 2


def test_two_editions_a_person_placed_in_one_work_both_stay() -> None:
    shelf = Shelf(grouped_by="hand")
    with SessionLocal() as db:
        for ident in (shelf.first, shelf.first_audio):
            db.get(Edition, ident).grouped_by = "hand"  # type: ignore[union-attr]
        db.commit()
    reassemble()
    assert shelf.work_of(shelf.wrong) == shelf.first_work and shelf.work_of(shelf.first) == shelf.first_work


def test_the_work_it_is_leaving_is_not_the_work_it_goes_to_even_when_it_is_called_after_the_edition() -> None:
    """The work took its name from the wrong edition (it speaks first in its work): looking for the right work by that name
    finds the very work it is leaving."""
    shelf = Shelf()
    with SessionLocal() as db:
        work = db.get(Work, shelf.first_work)
        assert work is not None
        work.title, work.key, work.main_key = "Salt Marsh", "salt marsh", "salt marsh"
        db.commit()
        edition = db.get(Edition, shelf.wrong)
        assert edition is not None
        touched: set[int] = set()
        assert catalog.leave_foreign_work(db, edition, touched) is True
        assert edition.work_id == shelf.right_work and touched == {shelf.first_work, shelf.right_work}
        db.commit()
    assert shelf.work_of(shelf.first) == shelf.first_work == shelf.work_of(shelf.first_audio)
