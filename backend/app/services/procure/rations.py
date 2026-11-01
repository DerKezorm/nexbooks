"""Brakes per person on fetching books: one person with the right to acquire never uses
the indexers up for everybody else.

* **Per day** (the day of the instance's time zone, ``budget.day``): at most ``wishes_per_day`` new wishes and
  ``searches_per_day`` searches a person starts by hand ("Neu suchen" in the release table, "Jetzt suchen" of a wish).
  Above them ``429`` (``too_many_wishes_today``, ``too_many_searches_today``) with ``Retry-After`` until midnight.
* **A fair share of an indexer's limit of the day**: an indexer with a limit gives each person at most the limit
  divided by the people who asked it today plus one (a share is kept for whoever comes next), never less than
  ``indexer_share_min`` requests (and never more than the limit leaves). A search of a person whose share is used ends
  at that indexer with ``indexer_share_used`` until midnight; the others go on. The searches of a person's wishes count
  as the person's.
* **The operator** has no brake and no share: the limits are his.
* The values come from the settings (``NEXBOOKS_WISHES_PER_DAY``, ``NEXBOOKS_SEARCHES_PER_DAY``,
  ``NEXBOOKS_INDEXER_SHARE_MIN``); 0 switches a brake per day off.

Kept in memory, per process, like the other brakes: a restart forgets the counts of the day (the limit of each indexer,
which the database keeps, holds all the same). New wishes are also counted in the database, so a restart does not give
a person their wishes of the day again.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...config import get_settings
from ...errors import error
from ...models import OPERATOR, Account, Indexer, ProcureWish
from .. import timezones
from . import budget

KINDS = {
    "wish": ("too_many_wishes_today", "You have made as many wishes today as one person may. It goes on tomorrow."),
    "search": ("too_many_searches_today",
               "You have started as many searches today as one person may. It goes on tomorrow."),
}

_lock = threading.Lock()
#: (kind, person, day) to how many.
_counts: dict[tuple[str, int, str], int] = {}
#: (indexer, day) to the requests of each person.
_used: dict[tuple[int, str], dict[int, int]] = {}


def exempt(account: Account) -> bool:
    return account.role == OPERATOR


def _limit(kind: str) -> int:
    settings = get_settings()
    return int(settings.wishes_per_day if kind == "wish" else settings.searches_per_day)


def _seconds_to_midnight(moment: datetime) -> int:
    return max(1, math.ceil((budget.midnight(moment) - moment).total_seconds()))


def _made_today(db: Session, account: Account, moment: datetime) -> int:
    """The person's wishes made today that the database still has (a wish taken away again is gone from it)."""
    zone = timezones.instance_zone()
    local = moment.astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return db.scalar(select(func.count(ProcureWish.id)).where(ProcureWish.user_id == account.id,
                                                             ProcureWish.created_at >= start)) or 0


def used(kind: str, account: Account, db: Session | None = None) -> int:
    moment = clock.now()
    with _lock:
        counted = _counts.get((kind, account.id, budget.day(moment)), 0)
    if kind == "wish" and db is not None:
        counted = max(counted, _made_today(db, account, moment))
    return counted


def exhausted(kind: str, account: Account, db: Session | None = None) -> bool:
    """Whether the person has used the day's brake of ``kind`` (the operator and a brake set to 0 never)."""
    limit = _limit(kind)
    return not (exempt(account) or limit <= 0) and used(kind, account, db) >= limit


def ensure(kind: str, account: Account, db: Session | None = None) -> None:
    """``429`` with ``Retry-After`` when the person has used the day's brake of ``kind``."""
    limit = _limit(kind)
    if exhausted(kind, account, db):
        code, text = KINDS[kind]
        exc = error(code, text, 429, limit=limit)
        exc.headers = {"Retry-After": str(_seconds_to_midnight(clock.now()))}
        raise exc


def add(kind: str, account: Account, number: int = 1) -> None:
    if exempt(account) or number <= 0:
        return
    key = (kind, account.id, budget.day())
    with _lock:
        _counts[key] = _counts.get(key, 0) + number
        _forget_old_days()


# --- The fair share of an indexer ------------------------------------------------------------------------------------


def share_left(row: Indexer, person: int | None) -> int | None:
    """How many requests the person may still make at the indexer today; None for no share (no limit, the operator,
    the automatic reading of the newest entries)."""
    if person is None or row.daily_limit <= 0:
        return None
    with _lock:
        seen = dict(_used.get((row.id, budget.day()), {}))
    people = len(set(seen) | {person})
    share = max(int(get_settings().indexer_share_min), row.daily_limit // (people + 1))
    return max(0, share - seen.get(person, 0))


def spend(indexer_id: int, person: int | None, number: int) -> None:
    """``number`` requests of the person at the indexer today."""
    if person is None or number <= 0:
        return
    key = (indexer_id, budget.day())
    with _lock:
        seen = _used.setdefault(key, {})
        seen[person] = seen.get(person, 0) + number
        _forget_old_days()


def take(row: Indexer, person: int | None) -> bool:
    """One request of the person's share, taken only while the share is not used up (the check and the count in one
    step,
    so that a person's searches running at the same moment never pass it together). True without a share to hold."""
    if person is None or row.daily_limit <= 0:
        return True
    key = (row.id, budget.day())
    with _lock:
        seen = _used.setdefault(key, {})
        people = len(set(seen) | {person})
        share = max(int(get_settings().indexer_share_min), row.daily_limit // (people + 1))
        if seen.get(person, 0) >= share:
            return False
        seen[person] = seen.get(person, 0) + 1
        _forget_old_days()
        return True


def give_back(indexer_id: int, person: int | None) -> None:
    """The request ``take`` counted was not made (the limit of the day refused it)."""
    if person is None:
        return
    with _lock:
        seen = _used.get((indexer_id, budget.day()))
        if seen and seen.get(person, 0) > 0:
            seen[person] -= 1


def _forget_old_days() -> None:
    """Keeps today and yesterday (a request made just before midnight may be counted just after it)."""
    if len(_counts) + len(_used) < 2000:
        return
    days = sorted({key[2] for key in _counts} | {key[1] for key in _used})[-2:]
    for key in [key for key in _counts if key[2] not in days]:
        del _counts[key]
    for key in [key for key in _used if key[1] not in days]:
        del _used[key]


def forget() -> None:
    """For the tests."""
    with _lock:
        _counts.clear()
        _used.clear()


__all__ = ["KINDS", "add", "ensure", "exempt", "forget", "give_back", "share_left", "spend", "take", "used"]
