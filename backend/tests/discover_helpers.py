"""What the tests of Discover share: the sources switched on with faked services and a fixed day, the lists fetched to
the end, and small ways to look at what was kept. Nothing here is real: the books are invented
(``tests/fixtures/sources``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, ListEntry
from app.services import jobs
from app.services.discover import lists

from .fake_sources import fixture
from .match_helpers import World

#: The day of the tests: ``new`` is what came out since 2026-08-18, ``soon`` what comes out after today.
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
TODAY = "2026-10-13"


class Disc(World):
    """The sources on, the services faked, the day fixed, with the Discover lists to fetch."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, audible: bool = True) -> None:
        monkeypatch.setattr(clock, "now", lambda: NOW)
        super().__init__(monkeypatch, audible=audible)
        self.fake.audible_chart = {"BestSellers": fixture("audible_best.json")["products"],
                                   "-ReleaseDate": fixture("audible_release.json")["products"]}

    def new_among_the_best(self) -> None:
        """Behind the chart books, the best sellers of Audible hold two that came out in the last weeks, one from
        March, one with no day and a pre-order (which is on no list of best sellers): fetched again."""
        wanted = {"B0NEW00001", "B0NEW00002", "B0OLD00001", "B0NODATE01", "B0SOON0003"}
        self.fake.audible_chart["BestSellers"] = fixture("audible_best.json")["products"] + [
            p for p in fixture("audible_release.json")["products"] if p["asin"] in wanted]
        with SessionLocal() as db:
            lists.fetch_unit(db, "audible", "de", "best")

    def fetch(self, market: str = "de", *names: str) -> int:
        """Somebody looks at the lists of a market, and the jobs that gives are run to the end. Returns the jobs that
        ran."""
        with SessionLocal() as db:
            lists.want(db, market, names or lists_of_all())
        ran = 0
        while True:
            step = jobs.run_pending(1000)
            ran += step
            if not step:
                return ran


def lists_of_all() -> tuple[str, ...]:
    return ("best", "new", "soon", "trending")


def entries(list_name: str, market: str | None = None) -> list[tuple[int, str, str]]:
    """``(rank, source, title)`` of a list, in order."""
    with SessionLocal() as db:
        query = select(ListEntry).where(ListEntry.list == list_name).order_by(ListEntry.source, ListEntry.rank)
        if market is not None:
            query = query.where(ListEntry.market == market)
        return [(e.rank, e.source, e.title) for e in db.scalars(query)]


def entries_released(list_name: str) -> list[tuple[str]]:
    """``(day,)`` of a list, in the order of the rank."""
    with SessionLocal() as db:
        return [(e.released,) for e in db.scalars(select(ListEntry).where(ListEntry.list == list_name).order_by(
            ListEntry.rank))]


def foreign(title: str) -> ForeignBook:
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == title))
        assert row is not None, f"no foreign book {title}"
        db.expunge(row)
        return row


def waiting_jobs(kind: str) -> list[Any]:
    from app.models import Job

    with SessionLocal() as db:
        return list(db.scalars(select(Job).where(Job.kind == kind, Job.state.in_(("queued", "running")))))
