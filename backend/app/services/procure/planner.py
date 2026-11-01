"""The planner of the automatic work: the one loop that comes back to what waits.

Every ``TICK_SECONDS`` one **tick** runs, never two at once (a tick that finds another running does nothing), and it
looks at the database only, so a restart in the middle loses nothing:

1. the hand-overs that a download program did not answer are made again (``handover``), and the questions under "Braucht
   dich" that the rules of the check have left behind are judged again, a few at a time (``recheck``);
2. the indexers whose time has come are read for their newest entries and what is new is held against the wishes
   (``rss``);
3. one wish that is due is searched (``wishes.run_due``): one per tick, person by person in turn (the person whose wish
   was searched longest ago first, and of each person the wish that waited longest), so that wishes that fall due
   together are searched one after the other, an indexer is not asked for all of them at once, and one person's many
   wishes push nobody else's back;
4. the authors people watch (``oeuvre.watch.tick``): a few watches whose catalogue was built since their last look are
   looked at (wishes made for what is new), and one catalogue that is due is asked for, when the lane of the catalogues
   is free and the sources have room;
5. once an hour: the wishes that look for a better format of a book that has the goal by now end, the entries seen two
   weeks ago, the searches of the newest entries of a day ago and the release files kept for a hand-over for two days
   go, and the log keeps its newest lines.

With the main switch off nothing runs by itself (``wishes.run_due`` still settles the searches that are over, and a
person who presses "Jetzt suchen" is served as ever). The clock is ``clock.now()`` everywhere, so that a test moves
time.

``status`` is what the operator reads: the last time the automatic work did something, the next time it comes back to an
indexer or a wish, what each indexer's reading did.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import SessionLocal
from ...models import Indexer, ProcureWish, ReleaseSearch
from .. import timezones
from ..oeuvre import watch as watching
from . import autolog, automation, budget, handover, recheck, releasefiles, rss, wishes

logger = logging.getLogger("nexbooks.procure")

TICK_SECONDS = 15.0
HOUSEKEEPING = timedelta(hours=1)
_lock = threading.Lock()
_last_housekeeping: datetime | None = None


def reset() -> None:
    global _last_housekeeping
    _last_housekeeping = None


def tick() -> dict[str, int] | None:
    """One round of everything that is due; None when another tick is running. Returns what it did, in numbers."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        return _tick()
    finally:
        _lock.release()


def _tick() -> dict[str, int]:
    done = {"handovers": 0, "entries": 0, "wishes": 0}
    round_ = autolog.Round("handover")
    try:
        with SessionLocal() as db:
            if automation.config(db)["enabled"]:
                done["handovers"] = handover.run(db, round_)
    except Exception:
        logger.exception("Handing releases over again failed")
    round_.finish()
    try:
        # (The questions under "Braucht dich" that the rules of today have not judged: a few at a time.)
        with SessionLocal() as db:
            if automation.config(db)["enabled"]:
                recheck.run(db)
    except Exception:
        logger.exception("Judging the open questions again failed")
    try:
        done["entries"] = rss.sync()
    except Exception:
        logger.exception("Reading the newest entries failed")
    try:
        with SessionLocal() as db:
            done["wishes"] = wishes.run_due(db)
    except Exception:
        logger.exception("Coming back to the wishes failed")
    try:
        # (The books of the authors people watch: the first look, a look after a new build, a new build asked for.)
        with SessionLocal() as db:
            watching.tick(db)
    except Exception:
        logger.exception("Watching the authors failed")
    _housekeeping()
    return done


def _housekeeping() -> None:
    global _last_housekeeping
    moment = clock.now()
    if _last_housekeeping is not None and moment - _last_housekeeping < HOUSEKEEPING:
        return
    _last_housekeeping = moment
    try:
        with SessionLocal() as db:
            wishes.end_reached_upgrades(db)
            wishes.met_elsewhere(db)
            rss.prune(db)
            autolog.prune(db)
            db.execute(delete(ReleaseSearch).where(
                ReleaseSearch.origin == "rss", ReleaseSearch.created_at < moment - timedelta(hours=24)))
            db.commit()
        releasefiles.prune()
    except Exception:
        logger.exception("The housekeeping of the automatic work failed")


async def run_forever(stop: asyncio.Event) -> None:
    """The background loop: a tick now and then."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
            return
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(tick)
        except Exception:
            logger.exception("A tick of the automatic work failed")


# --- What the operator reads -----------------------------------------------------------------------------------------


def status(db: Session) -> dict[str, Any]:
    """The state of the automatic work: switch, last and next time, what is due, and each indexer."""
    config = automation.config(db)
    moment = clock.now()
    rows = list(db.scalars(select(Indexer).order_by(Indexer.priority, Indexer.id)))
    minutes = config["rss_minutes"]
    lines = []
    for row in rows:
        if not row.enabled:
            continue
        rest = _resting(row)
        lines.append({
            "id": row.id, "name": row.name, "checked_at": row.rss_checked_at,
            "next_at": (row.rss_checked_at + rss.interval_of(row, minutes)) if row.rss_checked_at else moment,
            "error": row.rss_error, "resting_until": rest,
        })
    last = max([row.rss_checked_at for row in rows if row.rss_checked_at is not None] +
               ([found] if (found := autolog.last_run_at(db)) is not None else []), default=None)
    soonest = [line["next_at"] for line in lines if line["resting_until"] is None]
    due = wishes.next_due_at(db)
    if due is not None:
        soonest.append(due)
    day_dates = db.scalars(select(ProcureWish.release_date).where(
        ProcureWish.state == "waiting", ProcureWish.release_date != "")).all()
    return {
        "enabled": config["enabled"], "last_run_at": last,
        "next_run_at": max(min(soonest), moment) if config["enabled"] and soonest else None,
        "due": wishes.due_count(db, moment), "waiting_for_a_day": len(day_dates),
        "wishes_open": db.scalar(select(func.count(ProcureWish.id)).where(ProcureWish.state != "done")) or 0,
        "indexers": lines,
        "day_ends_at": budget.midnight(moment),
        "zone": str(getattr(timezones.instance_zone(), "key", "") or ""),
    }


def _resting(row: Indexer) -> datetime | None:
    from . import indexers

    rest = indexers.resting(row, background=True)
    return rest[0] if rest else None


__all__ = ["TICK_SECONDS", "reset", "run_forever", "status", "tick"]
