"""What the sources answered, kept for a while: so that no question is asked twice.

An answer that found something is good for 30 days (Audible's and Apple's for a day: what they say is there to be
shown), one that found nothing for a day (the book may be added there tomorrow; asking again every hour would be a
bad guest). What is kept is the *cut down* answer, the candidates
(``model.Candidate``), not the page a source sent. The table is bounded: past ``MAX_ROWS`` the expired go first, then
the
oldest.

Its own short sessions, like the throttle: a cache write must not commit (or wait for) whatever the caller has half
done.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select

from ... import clock
from ...db import SessionLocal
from ...models import SourceCache

logger = logging.getLogger("nexbooks.sources")

HIT_DAYS = 30
MISS_DAYS = 1
#: What Audible and Apple answered is there to be shown (descriptions, covers): it is kept for a day, not a month.
SHOWN_ONLY_DAYS = 1
SHOWN_ONLY = frozenset({"audible", "apple"})
MAX_ROWS = 20_000
#: Past this many puts the size is looked at (counting rows each time would be wasted work).
PRUNE_EVERY = 100
KEY_MAX = 300

_lock = threading.Lock()
_puts = 0


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


#: What ``get`` answers when nothing good is kept (a kept value may be an empty list).
MISSING: Any = _Missing()


def make_key(*parts: str) -> str:
    """A key from the parts of a question, lower case and short (the table's column is that long)."""
    return "|".join(" ".join(str(part).lower().split()) for part in parts)[:KEY_MAX]


def get(source: str, key: str) -> Any:
    with SessionLocal() as db:
        row = db.scalar(select(SourceCache).where(SourceCache.source == source, SourceCache.key == key[:KEY_MAX]))
        if row is None or row.expires_at <= clock.now():
            return MISSING
        return row.value


def put(source: str, key: str, value: Any, *, hit: bool, days: int | None = None) -> None:
    """Keeps an answer: ``days`` (when given) instead of the usual 30 for an answer that found something."""
    global _puts
    moment = clock.now()
    if source in SHOWN_ONLY:
        days = min(days or HIT_DAYS, SHOWN_ONLY_DAYS)
    expires = moment + timedelta(days=(days or HIT_DAYS) if hit else MISS_DAYS)
    with _lock, SessionLocal() as db:
        row = db.scalar(select(SourceCache).where(SourceCache.source == source, SourceCache.key == key[:KEY_MAX]))
        if row is None:
            db.add(SourceCache(source=source, key=key[:KEY_MAX], value=value, fetched_at=moment, expires_at=expires))
        else:
            row.value, row.fetched_at, row.expires_at = value, moment, expires
        db.commit()
        _puts += 1
        if _puts % PRUNE_EVERY == 0:
            prune(db)


def prune(db: Any = None) -> int:
    """Keeps the table within ``MAX_ROWS``: the expired go, then the oldest. Returns how many went."""
    own = db is None
    session = SessionLocal() if own else db
    try:
        removed = session.execute(delete(SourceCache).where(SourceCache.expires_at <= clock.now())).rowcount or 0
        total = session.scalar(select(func.count(SourceCache.id))) or 0
        if total > MAX_ROWS:
            oldest = select(SourceCache.id).order_by(SourceCache.fetched_at, SourceCache.id).limit(total - MAX_ROWS)
            removed += session.execute(delete(SourceCache).where(SourceCache.id.in_(oldest))).rowcount or 0
        session.commit()
        return int(removed)
    finally:
        if own:
            session.close()


def forget(source: str | None = None) -> None:
    with _lock, SessionLocal() as db:
        query = delete(SourceCache)
        if source:
            query = query.where(SourceCache.source == source)
        db.execute(query)
        db.commit()
