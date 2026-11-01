"""How fast a source is asked, and how it fared: one row per source in the database.

* **A bucket of tokens** (``Limits.burst`` at most, refilled with ``Limits.rate`` a second): a request takes one. When
  the bucket is empty the caller has to wait for the next token, and ``take`` says for how long. The bucket is kept in
  the database, so a restart does not give a source a free burst (a Hardcover token that is asked 10 times in the first
  second after every start would be a bad guest).
* **A count per day** (UTC): past ``Limits.per_day`` the source is not asked again until midnight (``DailyLimit``).
* **Rest after errors**: after a failure the source is left alone for 30 seconds, doubled with every failure in a row up
  to an hour (``fail``), and a source that answered with 429 rests for as long as it asks (``fail(..., wait=...)``).
  A request that ran out of time once is no reason to leave a source alone for everybody (a slow answer now and then is
  how the catalogues are): it rests only when it runs out of time again within ``SOFT_WINDOW`` seconds.
* **"Am I asked" is one atom**: reading the bucket, taking from it and writing it back happen under one lock and in one
  transaction, so two requests at the same moment do not take the same token.

The moment comes from ``now`` and the waiting from ``http.sleep``: the tests set both.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from ... import clock
from ...db import SessionLocal
from ...models import SourceState
from .config import LIMITS

logger = logging.getLogger("nexbooks.sources")

#: The longest a source rests after errors.
REST_MAX = 3600
REST_FIRST = 30
#: Failures that make a source rest only when they come again within ``SOFT_WINDOW`` seconds; those within
#: ``SOFT_SAME`` seconds of each other are one.
SOFT = frozenset({"timeout"})
SOFT_WINDOW = 300
SOFT_SAME = 10

#: The tokens the work in the background (reading a library, matching, the shelves of Discover) leaves in a bucket, so
#: that a person's search does not wait behind it: the reading of a large library asks the same sources at their full
#: rate, and a search that finds every bucket taken waits a second per question, or gives up (``INTERACTIVE_WAIT``).
BACKGROUND_KEEPS = 1

_lock = threading.Lock()


def now() -> datetime:
    return clock.now()


class DailyLimit(Exception):
    """The source was asked as often today as it may be."""


@dataclass(frozen=True)
class Snapshot:
    """A source's row, copied (the row itself belongs to a session that is gone)."""

    source: str
    day_count: int
    last_ok_at: datetime | None
    last_error: str
    last_error_at: datetime | None
    fail_count: int
    blocked_until: datetime | None
    state: str


def _row(db, source: str) -> SourceState:  # type: ignore[no-untyped-def]
    row = db.get(SourceState, source)
    if row is None:
        row = SourceState(source=source, tokens=float(LIMITS[source].burst), refilled_at=now(), day="", day_count=0)
        db.add(row)
        db.flush()
    return row


def _today() -> str:
    return now().strftime("%Y-%m-%d")


def state_of(source: str) -> Snapshot:
    with SessionLocal() as db:
        row = db.get(SourceState, source)
        if row is None:
            return Snapshot(source, 0, None, "", None, 0, None, "")
        today = row.day == _today()
        return Snapshot(source, row.day_count if today else 0, row.last_ok_at, row.last_error, row.last_error_at,
                        row.fail_count, row.blocked_until, row.state)


def take(source: str, keep: int = 0) -> float:
    """Takes a token. Returns 0 when it was taken, else the seconds to wait for the next one (nothing is taken then).
    ``DailyLimit`` when today's count is used up. ``keep``: tokens left in the bucket for others (the work in the
    background leaves one for a person who searches: ``BACKGROUND_KEEPS``)."""
    limits = LIMITS[source]
    with _lock, SessionLocal() as db:
        row = _row(db, source)
        moment = now()
        elapsed = (moment - row.refilled_at).total_seconds() if row.refilled_at else limits.burst / limits.rate
        row.tokens = min(float(limits.burst), row.tokens + max(0.0, elapsed) * limits.rate)
        row.refilled_at = moment
        today = _today()
        if row.day != today:
            row.day, row.day_count = today, 0
        if row.day_count >= limits.per_day:
            db.commit()
            raise DailyLimit(source)
        needed = 1.0 + max(0, min(keep, limits.burst - 1))
        if row.tokens >= needed:
            row.tokens -= 1.0
            row.day_count += 1
            db.commit()
            return 0.0
        wait = (needed - row.tokens) / limits.rate
        db.commit()
        return wait


def ok(source: str) -> None:
    with _lock, SessionLocal() as db:
        row = _row(db, source)
        row.last_ok_at, row.fail_count, row.blocked_until = now(), 0, None
        db.commit()


def fail(source: str, code: str, *, wait: float | None = None) -> None:
    """A request failed: remembered with its code, and the source rests (``wait`` seconds when it said how long)."""
    with _lock, SessionLocal() as db:
        row = _row(db, source)
        moment = now()
        before = row.last_error_at
        since = (moment - before).total_seconds() if before is not None else None
        same = code in SOFT and wait is None and row.last_error in SOFT and since is not None and since < SOFT_SAME
        if same and (row.blocked_until is None or row.blocked_until <= moment):
            # (The questions of one search time out together: one slow moment of the source, counted once.)
            db.commit()
            return
        recent = row.fail_count > 0 and since is not None and since <= SOFT_WINDOW
        row.fail_count = row.fail_count + 1 if recent or code not in SOFT else 1
        row.last_error, row.last_error_at = code[:40], moment
        if code in SOFT and wait is None and row.fail_count < 2:
            # (Once is no reason to rest: see the top.)
            row.blocked_until = None
        else:
            steps = row.fail_count - 1 if code not in SOFT or wait is not None else row.fail_count - 2
            rest = wait if wait is not None else min(REST_MAX, REST_FIRST * 2 ** max(0, steps))
            row.blocked_until = moment + timedelta(seconds=min(float(REST_MAX), max(1.0, rest)))
        db.commit()
    logger.info("Source failed source=%s error=%s fails=%s", source, code, row.fail_count)


def limit_reached(source: str) -> None:
    """The source was asked as often today as it may be: it rests until midnight (the day of the count), like after an
    error that said when to come back. Nobody asks it again until then, and what waited for it waits for it."""
    with _lock, SessionLocal() as db:
        row = _row(db, source)
        moment = now()
        row.last_error, row.last_error_at = "daily_limit", moment
        row.blocked_until = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        db.commit()
    logger.info("Source at its daily limit source=%s", source)


def set_state(source: str, state: str) -> None:
    """``token_invalid`` when a source refused the token, empty when the operator connected anew."""
    with _lock, SessionLocal() as db:
        row = _row(db, source)
        row.state = state
        if not state:
            row.fail_count, row.blocked_until, row.last_error = 0, None, ""
        db.commit()


def refund(source: str) -> None:
    """Gives a token back (a request that was never sent)."""
    limits = LIMITS[source]
    with _lock, SessionLocal() as db:
        row = _row(db, source)
        row.tokens = min(float(limits.burst), row.tokens + 1.0)
        row.day_count = max(0, row.day_count - 1)
        db.commit()


def forget() -> None:
    """For the tests: every source starts again."""
    with _lock, SessionLocal() as db:
        db.query(SourceState).delete()
        db.commit()
