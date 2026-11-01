"""What the tests of the themed shelves share: the open sources alone (Apple's charts of the genres and Open
Library's search; Audible off, Hardcover not connected), the shelves of a market fetched to the end, and small ways to
look at what was kept. Every book is invented (``tests/fixtures/sources``)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ForeignBook, Job, ShelfEntry
from app.services import jobs
from app.services.browse import fetch

from .discover_shelves import Shelves

#: The books of the fixtures by the place they take on the shelf "krimi" of the German market when only the open sources
#: speak (Apple's chart first, then Open Library), and the day of the tests (2026-10-13).
APPLE_NEW = ["Nebelwacht", "Die Salzwiese"]
APPLE_POPULAR = ["Nebelwacht", "Der Leuchtturmwaerter", "Die Salzwiese", "Orbit Null", "Das Raetsel von Kalmar",
                 "Fremdes Bild"]
OL_POPULAR_DE = ["Der stille Pier", "Hafenlichter", "Die neunte Glocke", "Nebelläufer", "Die Salztöchter",
                 "Acht Stunden bis zum Morgen", "Kurze Nacht"]
OL_AWARD_DE = ["Der lange Heimweg", "Ein Fall von Salz"]


def done_jobs(kind: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Job).where(Job.kind == kind, Job.state == "done")) or 0


class Browse(Shelves):
    """The world of ``Shelves`` (two libraries, three people, the German lists) with only the open sources on, and the
    shelves of the German market fetched."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fetch_shelves: bool = True) -> None:
        super().__init__(monkeypatch)
        self.only("apple", "openlibrary")
        self.settings(source_audible=False)
        self.fake.log.clear()
        if fetch_shelves:
            self.shelves("de")

    def shelves(self, market: str = "de") -> int:
        """Somebody looks at the shelves of a market, and the jobs that gives are run to the end. Returns the jobs that
        ran."""
        before = done_jobs("shelves")
        with SessionLocal() as db:
            fetch.want(db, market)
        while jobs.run_pending(1000):
            pass
        # The jobs of the shelves; the covers of the first books that are fetched after them are counted by the tests
        # that are about covers.
        return done_jobs("shelves") - before


def wall(client: TestClient, **params: Any) -> dict[str, Any]:
    params = {"market": "de", "hide_owned": "false", **params}
    answer = client.get("/api/v1/discover/browse", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def shelf(client: TestClient, key: str = "krimi", **params: Any) -> dict[str, Any]:
    params = {"market": "de", "hide_owned": "false", **params}
    answer = client.get(f"/api/v1/discover/shelves/{key}", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def personal(client: TestClient, **params: Any) -> dict[str, Any]:
    params = {"market": "de", "hide_owned": "false", **params}
    answer = client.get("/api/v1/discover/browse/personal", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def titles(body: dict[str, Any]) -> list[str]:
    return [item["title"] for item in body["items"]]


def stored(shelf_key: str, section: str, market: str = "de", source: str | None = None) -> list[str]:
    """The titles of a section as they were kept, by source and rank."""
    with SessionLocal() as db:
        query = (select(ShelfEntry, ForeignBook).join(ForeignBook, ForeignBook.id == ShelfEntry.foreign_id)
                 .where(ShelfEntry.shelf == shelf_key, ShelfEntry.section == section, ShelfEntry.market == market)
                 .order_by(ShelfEntry.source, ShelfEntry.rank))
        if source is not None:
            query = query.where(ShelfEntry.source == source)
        return [book.title for _entry, book in db.execute(query).all()]


def every_unit() -> int:
    return len(fetch.units("de"))

