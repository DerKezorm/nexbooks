"""Helpers for the tests of the automatic work: a clock the test moves by hand, the requests an indexer got
for the newest entries (a search without a query text), and one tick of the planner."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app import clock
from app.db import SessionLocal
from app.models import AutoRun, Indexer, RssSeen
from app.services.procure import automation, planner

from .follow_helpers import rows


class Moving:
    """The clock of the app, moved by the test (``pass_by``); every part of the app reads ``clock.now``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.offset = timedelta()
        start = clock.now()
        monkeypatch.setattr(clock, "now", lambda: start + self.offset)

    def pass_by(self, **kwargs: float) -> None:
        self.offset += timedelta(**kwargs)


def reading_requests(indexer: Any) -> list[Any]:
    """The requests that read the newest entries: a search without text."""
    return [request for request in indexer.requests
            if request.url.params.get("t") == "search" and not request.url.params.get("q")]


def searching_requests(indexer: Any) -> list[Any]:
    return [request for request in indexer.requests
            if request.url.params.get("t") in ("search", "book") and (request.url.params.get("q")
                                                                      or request.url.params.get("author"))]


def tick() -> dict[str, int] | None:
    planner.reset()
    return planner.tick()


def configure(**changes: Any) -> dict[str, Any]:
    with SessionLocal() as db:
        return automation.save(db, {**automation.config(db), **changes})


def indexer_row(indexer_id: int | None = None) -> Indexer:
    found = rows(Indexer)
    return found[0] if indexer_id is None else next(row for row in found if row.id == indexer_id)


def runs() -> list[AutoRun]:
    return rows(AutoRun)  # type: ignore[no-any-return]


def seen() -> list[RssSeen]:
    with SessionLocal() as db:
        found = list(db.query(RssSeen).order_by(RssSeen.key))
        for row in found:
            db.expunge(row)
        return found
