"""What the automatic work did: one line for each round that did something, kept for the operator.

* A **round** is the reading of the newest entries of the indexers (``rss``), the settling of a wish's search
  (``search``: requests per indexer, whether a release was loaded) or a new hand-over to a download program
  (``handover``). A round that did nothing leaves no line.
* The numbers are counts: requests and entries per indexer (by its number), wishes looked at, releases that fit,
  releases
  loaded. **Never a key, a name, a title or an address.** The log (``logger``) says the same in words of numbers.
* The newest ``KEEP_RUNS`` are kept; the operator reads them in the settings of the automatic work.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import SessionLocal
from ...models import AutoRun

logger = logging.getLogger("nexbooks.procure")

KEEP_RUNS = 400
KINDS = ("rss", "search", "handover")


class Round:
    """The numbers of one round, written when it is over (with its own session: the caller may be in the middle of a
    transaction)."""

    def __init__(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(kind)
        self.kind = kind
        self.started = clock.now()
        self.indexers: dict[int, dict[str, Any]] = {}
        self.numbers: dict[str, int] = {}

    def indexer(self, indexer_id: int, *, requests: int = 0, entries: int = 0, error: str = "") -> None:
        line = self.indexers.setdefault(int(indexer_id), {"requests": 0, "entries": 0, "error": ""})
        line["requests"] += requests
        line["entries"] += entries
        if error:
            line["error"] = error

    def add(self, name: str, number: int = 1) -> None:
        self.numbers[name] = self.numbers.get(name, 0) + number

    def did_something(self, error: str = "") -> bool:
        return bool(error) or any(self.numbers.values()) or any(
            line["requests"] or line["entries"] or line["error"] for line in self.indexers.values())

    def finish(self, error: str = "") -> bool:
        """Writes the line when the round did something; returns whether it did."""
        if not self.did_something(error):
            return False
        counts: dict[str, Any] = {"indexers": {str(key): dict(value) for key, value in self.indexers.items()},
                                  **self.numbers}
        with SessionLocal() as db:
            db.add(AutoRun(kind=self.kind, started_at=self.started, finished_at=clock.now(), counts=counts,
                           error=error[:64]))
            db.commit()
        logger.info("Automatic round kind=%s requests=%d entries=%d %s error=%s", self.kind,
                    sum(line["requests"] for line in self.indexers.values()),
                    sum(line["entries"] for line in self.indexers.values()),
                    " ".join(f"{name}={number}" for name, number in sorted(self.numbers.items())), error or "-")
        return True


def last_run_at(db: Session) -> datetime | None:
    """When the automatic work last did something (a line of the log)."""
    return db.scalar(select(AutoRun.finished_at).order_by(AutoRun.id.desc()).limit(1))


def recent(db: Session, limit: int = 30) -> list[AutoRun]:
    return list(db.scalars(select(AutoRun).order_by(AutoRun.id.desc()).limit(max(1, min(limit, 200)))))


def prune(db: Session) -> int:
    """Only the newest ``KEEP_RUNS`` stay."""
    newest = select(AutoRun.id).order_by(AutoRun.id.desc()).limit(KEEP_RUNS)
    removed = db.execute(delete(AutoRun).where(AutoRun.id.not_in(newest))).rowcount
    db.commit()
    return removed or 0


__all__ = ["KEEP_RUNS", "KINDS", "Round", "last_run_at", "prune", "recent"]
