"""What the tests of the reading stack, the list, the authors and the tidying share: two libraries with invented books
(an e-book and a recording of the same story among them), four people and the clock set by the test.

Nothing here is real: the names are made up, the books are built in the test."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, BookFile, Edition, LibraryMember

from .conftest import make_account
from .library_helpers import age, book, books_folder, make_library, recording, run_jobs


class Clock:
    """The server's clock, set by the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(clock, "now", lambda: self.now)

    def advance(self, seconds: float = 0, days: float = 0) -> None:
        self.now += timedelta(seconds=seconds, days=days)


class World:
    """Two libraries and four people: tester (operator, manages both), rita (family), sam (club), tom (none).

    Family: ``Alpha`` as an e-book and as a recording (one work), ``Beta`` (volume 2 of a series, an e-book), ``Gamma``.
    Club: ``Delta``."""

    def __init__(self) -> None:
        self.family = books_folder("family")
        self.club = books_folder("club")
        self.alpha = book(self.family, "Ada Feldkamp/Alpha/alpha.epub", title="Alpha", authors=("Ada Feldkamp",),
                          language="en", chapters=("One", "Two", "Three", "Four"), text_bytes=40_000,
                          series="Harbour Tales", series_pos=1, date="2012-05-01", subjects=("Fiction",))
        self.alpha_audio = recording(self.family, "Ada Feldkamp/Alpha Audio/alpha.m4b", 600.0, title="Alpha",
                                     album="Alpha", artist="Ada Feldkamp", narrator="Rune Falkner")
        book(self.family, "Ada Feldkamp/Beta/beta.epub", title="Beta", authors=("Ada Feldkamp",), language="en",
             series="Harbour Tales", series_pos=2, text_bytes=70_000, date="2014-03-01")
        book(self.family, "Cy Dorn/Gamma/gamma.epub", title="Gamma", authors=("Cy Dorn",), language="de",
             text_bytes=20_000, date="2019")
        book(self.club, "Ben Hollister/Delta/delta.epub", title="Delta", authors=("Ben Hollister",), language="en")
        self.family_id = make_library(self.family, "Family", scan=True)
        self.club_id = make_library(self.club, "Club", scan=True)
        self.rita = make_account("rita")
        self.sam = make_account("sam")
        self.tom = make_account("tom")
        with SessionLocal() as db:
            tester = db.scalar(select(Account).where(Account.name == "tester"))
            assert tester is not None
            self.tester_id = tester.id
            db.query(LibraryMember).delete()
            for user, library, right in ((tester.id, self.family_id, "manage"), (tester.id, self.club_id, "manage"),
                                         (self.rita.id, self.family_id, "read"), (self.sam.id, self.club_id, "read")):
                db.add(LibraryMember(library_id=library, user_id=user, right=right))
            db.commit()
        run_jobs()

    def edition(self, title: str, kind: str = "ebook") -> int:
        with SessionLocal() as db:
            return db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == kind)).one()

    def work_of(self, edition_id: int) -> int:
        with SessionLocal() as db:
            return db.scalars(select(Edition.work_id).where(Edition.id == edition_id)).one()

    def file(self, edition_id: int, fmt: str) -> int:
        with SessionLocal() as db:
            return db.scalars(select(BookFile.id).where(BookFile.edition_id == edition_id,
                                                        BookFile.format == fmt)).one()

    def tester(self) -> Account:
        with SessionLocal() as db:
            row = db.scalar(select(Account).where(Account.name == "tester"))
            assert row is not None
            db.expunge(row)
            return row


def report(**over: object) -> dict[str, object]:
    return {"locator": "epubcfi(/6/4!/4/2)", "fraction": 0.25, "chapter": 2, "chapter_count": 4,
            "chapter_title": "Two", "device_key": "dev-a", "device": "Firefox on Linux", **over}


def at(fraction: float, **over: object) -> dict[str, object]:
    """A report of an e-book at ``fraction`` of the book."""
    return report(fraction=fraction, **over)


def heard(seconds: float, total: float = 600.0, **over: object) -> dict[str, object]:
    """A report of a recording at ``seconds``."""
    return {"locator": f"time:{seconds:g}", "fraction": min(seconds / total, 1.0), "unit": "time", "chapter": 1,
            "chapter_count": 1, "chapter_title": "", "device_key": "dev-a", "device": "Firefox on Linux", **over}


def fresh(path: Path) -> Path:
    age(path)
    return path
