"""The day of the indexers: what is counted today, and when the count starts again.

* **The day** is the day of the instance's time zone (``timezones.instance_zone``): a limit of requests "per day" starts
  again at midnight there, not at midnight in Greenwich.
* **Two counts, apart**: the requests (searches and the reading of the newest entries) count against the operator's
  limit of the day; the releases fetched (downloads of an NZB or a torrent file) are counted for themselves and held
  back only by what the indexer says about its own grabs (``newznab:apilimits``). An indexer that spent its requests
  still gives the file of a release that was found before.
* **Reserved one by one**: every request (and every release fetched) takes its place in the count of the day in one
  statement that holds only while the limit is not reached (``reserve_request``, ``reserve_grab``), so that searches,
  the reading of the newest entries and "Jetzt suchen" running at the same moment never ask for more than the limit
  and never lose a count to each other.
* **The background keeps a part for people**: the planner (a wish that comes back), the reading of the newest entries
and
  the search for a better format use at most ``BACKGROUND_SHARE`` of the limit of the day; the rest is for a new wish
  and
  for "Jetzt suchen" (``spent`` and ``reserve_request`` with ``background``).
* Every function takes the moment as an argument (the clock of the app by default), so that a test moves time by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from ... import clock
from ...models import Indexer
from .. import timezones

#: How long an indexer's own count of grabs holds a fetch back when it names no time.
UNKNOWN_RESET = timedelta(hours=24)
#: The part of an indexer's limit of the day that the background (planner, newest entries, better formats) may use.
BACKGROUND_SHARE = 0.7


def day(moment: datetime | None = None) -> str:
    """``YYYY-MM-DD`` of the moment in the instance's time zone."""
    return (moment or clock.now()).astimezone(timezones.instance_zone()).strftime("%Y-%m-%d")


def midnight(moment: datetime | None = None) -> datetime:
    """The next midnight of the instance's time zone, as UTC: when the count of the day starts again."""
    zone = timezones.instance_zone()
    local = (moment or clock.now()).astimezone(zone)
    following = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=zone)
    return following.astimezone(UTC)


def used_today(row: Indexer, moment: datetime | None = None) -> int:
    """Requests (searches, reading of the newest entries) made today."""
    return row.used_count if row.used_day == day(moment) else 0


def grabs_today(row: Indexer, moment: datetime | None = None) -> int:
    """Releases fetched today."""
    return row.grab_count if row.grab_day == day(moment) else 0


def cap_of(limit: int, *, background: bool = False) -> int:
    """How many requests of the day the limit allows to the caller: all for a person, ``BACKGROUND_SHARE`` for the
    background."""
    if not background:
        return limit
    # One request is always the background's, so that even a limit of one is not useless to the planner.
    return max(1, min(limit - 1, int(limit * BACKGROUND_SHARE)))


def requests_left(row: Indexer, moment: datetime | None = None, *, background: bool = False) -> int | None:
    """How many requests the operator's limit still allows today; None without a limit."""
    if row.daily_limit <= 0:
        return None
    return max(0, cap_of(row.daily_limit, background=background) - used_today(row, moment))


def spent(row: Indexer, moment: datetime | None = None, *, background: bool = False) -> bool:
    """Whether the operator's limit of requests is used up for today (for the background: its part of it)."""
    return row.daily_limit > 0 and used_today(row, moment) >= cap_of(row.daily_limit, background=background)


def grab_limit(row: Indexer) -> int | None:
    """How many releases a day the indexer says it gives (its own ``apilimits``); None when it says nothing."""
    return row.grab_max if row.grab_max is not None and row.grab_max > 0 else None


def reserve_request(db: Session, indexer_id: int, *, background: bool = False, moment: datetime | None = None) -> bool:
    """Takes one request of the day in one statement: the count goes up by one only while it is below the limit (or the
    background's part of it). Returns whether the request may be made. Committed at once, so that nobody else waits."""
    now = moment or clock.now()
    today = day(now)
    limit = db.scalar(select(Indexer.daily_limit).where(Indexer.id == indexer_id))
    if limit is None:
        return False
    so_far = case((Indexer.used_day == today, Indexer.used_count), else_=0)
    statement = update(Indexer).where(Indexer.id == indexer_id)
    if limit > 0:
        statement = statement.where(so_far < cap_of(limit, background=background))
    changed = db.execute(statement.values(used_count=so_far + 1, used_day=today).execution_options(
        synchronize_session=False)).rowcount
    db.commit()
    _reread(db, indexer_id, ["used_count", "used_day"])
    return bool(changed)


def reserve_grab(db: Session, indexer_id: int, *, moment: datetime | None = None) -> bool:
    """Takes one fetch of a release in the count of the day: only while the indexer's own number of releases a day is
    not reached (nothing holds it back when the indexer names none). Committed at once."""
    now = moment or clock.now()
    today = day(now)
    limit = db.scalar(select(Indexer.grab_max).where(Indexer.id == indexer_id))
    so_far = case((Indexer.grab_day == today, Indexer.grab_count), else_=0)
    statement = update(Indexer).where(Indexer.id == indexer_id)
    if limit is not None and limit > 0:
        statement = statement.where(so_far < limit)
    changed = db.execute(statement.values(grab_count=so_far + 1, grab_day=today).execution_options(
        synchronize_session=False)).rowcount
    db.commit()
    _reread(db, indexer_id, ["grab_count", "grab_day"])
    return bool(changed)


def _reread(db: Session, indexer_id: int, names: list[str]) -> None:
    """The statement changed the row behind the session's back (the session keeps what it read after a commit): the
    counts it changed are read again when the row is looked at next."""
    row = db.get(Indexer, indexer_id)
    if row is not None:
        db.expire(row, names)


def grab_hold(row: Indexer, moment: datetime | None = None) -> datetime | None:
    """Until when no release is fetched from the indexer (its own count of grabs is used up); None when one may be."""
    now = moment or clock.now()
    if row.grab_paused_until is not None and row.grab_paused_until > now:
        return row.grab_paused_until
    return None


__all__ = ["BACKGROUND_SHARE", "cap_of", "day", "grab_hold", "grab_limit", "grabs_today", "midnight", "requests_left",
           "reserve_grab", "reserve_request", "spent", "used_today"]
