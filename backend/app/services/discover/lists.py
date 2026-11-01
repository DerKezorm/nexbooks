"""The lists of the sources, fetched in the background and kept.

**Who fetches, and when.** Nobody fetches while a person waits. A person who opens a list says that someone looks at
it (``want``: the moment is noted, and a job is queued when the list was never fetched or is older than twelve hours);
a slow loop (``run_forever``) queues the lists that someone looked at in the last fortnight and are old again, at most
two at a time, and not at all in the first minute after the start. A list nobody looks at is not asked for. A job asks
one source for one market (a *unit*: Apple's chart, Audible's best-selling, Audible's newest, Hardcover's trends) and
replaces what the unit gave before, all of it in one transaction: a unit that fails keeps the old list, and a job the
server stops halfway leaves nothing behind (it runs again at the next start).

**Every request goes through the one way out** (``sources/http.py``): the operator's main switch, the switch of the
source, the throttle, the walls of the address. With the main switch off no job is even queued, and a job that was
queued before asks nothing: ``SourceOff`` ends it.

The covers follow, one job each (``foreign.cover_job``), paced by the throttle of the source.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import SessionLocal
from ...models import SOURCES, ForeignBook, Job, ListEntry, ListFetch
from .. import jobs
from ..sources import apple, audible, config, hardcover, http
from ..sources.model import Candidate
from . import foreign, markets

logger = logging.getLogger("nexbooks.discover")

#: A list holds at most this many books.
LIST_SIZE = 50
#: Pages of Audible's best-selling that are read (the pre-orders among them are left out, the new ones picked), and the
#: most pages of its "newest first": the ones that come out later lie before the ones that came out.
BEST_PAGES = 2
RELEASE_PAGES = 2
#: How often the loop looks at what is due, and how long it waits after the start.
CHECK_SECONDS = 600
FIRST_WAIT_SECONDS = 60
#: The most units the loop queues in one look.
PER_LOOK = 2
#: The time of a person's look is noted at most this often.
TOUCH_SECONDS = 3600
#: Failures that are the source's own and pass (it is asked again in an hour); anything else is told to the log.
_PASSING = frozenset({"timeout", "unreachable", "server", "rate_limited", "busy", "daily_limit", "http_error"})


# ---- What is due -----------------------------------------------------------------------------------------------


def is_due(row: ListFetch | None, now: Any) -> bool:
    """Whether a unit is to be fetched: never fetched, older than twelve hours, or (after a failure) older than an
    hour."""
    if row is None or row.fetched_at is None:
        return True
    hours = markets.RETRY_HOURS if row.state == "failed" else markets.REFRESH_HOURS
    return bool(now - row.fetched_at >= timedelta(hours=hours))


def units_for(market: str, list_name: str) -> list[tuple[str, str, str]]:
    """``(source, market, unit)`` for the units that fill a list in a market."""
    unit = markets.UNIT_OF_LIST[list_name]
    return [(source, "" if source == "hardcover" else market, unit) for source in markets.SOURCES_OF_LIST[list_name]]


def _row(db: Session, source: str, market: str, unit: str) -> ListFetch:
    row = db.get(ListFetch, (source, market, unit))
    if row is None:
        row = ListFetch(source=source, market=market, unit=unit, state="", count=0, error="")
        db.add(row)
        db.flush()
    return row


def queue(db: Session, source: str, market: str, unit: str) -> bool:
    """A job for a unit, unless one waits already or the source may not be asked now. Does not commit."""
    if not config.availability(db, source).ready:
        return False
    ref = markets.unit_ref(source, market, unit)
    if db.scalar(select(Job.id).where(Job.kind == "lists", Job.ref == ref, Job.state.in_(("queued", "running")))):
        return False
    jobs.enqueue(db, "lists", ref=ref, payload={"source": source, "market": market, "unit": unit})
    return True


def want(db: Session, market: str, list_names: tuple[str, ...]) -> None:
    """A person looks at lists of a market: it is noted (so that the loop keeps them fresh), and what was never
    fetched, or is old, is queued. Commits."""
    if market not in markets.MARKETS:
        return
    now = clock.now()
    for list_name in list_names:
        for source, area, unit in units_for(market, list_name):
            row = _row(db, source, area, unit)
            if row.wanted_at is None or (now - row.wanted_at).total_seconds() >= TOUCH_SECONDS:
                row.wanted_at = now
            if is_due(row, now):
                queue(db, source, area, unit)
    db.commit()


def enqueue_due(db: Session) -> int:
    """The loop's look: the units someone looked at in the last fortnight that are due, at most ``PER_LOOK``."""
    now = clock.now()
    since = now - timedelta(days=markets.WANTED_DAYS)
    queued = 0
    for row in db.scalars(select(ListFetch).where(ListFetch.wanted_at.is_not(None), ListFetch.wanted_at >= since)
                          .order_by(ListFetch.fetched_at.is_not(None), ListFetch.fetched_at)):
        if queued >= PER_LOOK:
            break
        if (row.market == "" and row.source != "hardcover") or (row.market and row.market not in markets.MARKETS):
            continue
        if is_due(row, now) and queue(db, row.source, row.market, row.unit):
            queued += 1
    db.commit()
    return queued


# ---- Fetching a unit -------------------------------------------------------------------------------------------


def _came_out(found: list[Candidate], today: str) -> tuple[list[Candidate], list[Candidate]]:
    """``(out, to come)``: the products that are out (or have no day) and the ones whose day is after today."""
    return ([c for c in found if not c.released or c.released <= today],
            [c for c in found if c.released and c.released > today])


def _new_of(out: list[Candidate], today: str) -> list[Candidate]:
    """What came out in the last weeks (``NEW_WEEKS``), the newest first. A product with no day is not in it."""
    begin = (clock.now().date() - timedelta(weeks=markets.NEW_WEEKS)).isoformat()
    return sorted((c for c in out if c.released and begin <= c.released <= today), key=lambda c: c.released,
                  reverse=True)


def _fetch(db: Session, source: str, market: str, unit: str) -> dict[str, list[Candidate]]:
    today = clock.now().date().isoformat()
    if source == "apple":
        place = markets.MARKETS[market]
        # Apple's chart does not say what language a book is in (it is the chart of a country): none is put on it.
        out, _soon = _came_out(apple.chart(db, place.apple), today)
        return {"best": out}
    if source == "audible":
        place = markets.MARKETS[market]
        if unit == "best":
            # The best-selling, and of them the ones that came out in the last weeks (Audible has no question for the
            # new ones that is not "all of them, a few hundred a day"). A pre-order is not among the best sellers: it
            # is on the list of what comes out.
            out, _soon = _came_out(audible.chart(db, place.audible, "best", pages=BEST_PAGES), today)
            return {"best": out, "new": _new_of(out, today)}
        # The newest first puts what comes out first, the furthest day first: the pages are read until they reach today.
        def reached(so_far: list[Candidate]) -> bool:
            return bool(so_far) and bool(so_far[-1].released) and so_far[-1].released <= today

        found = audible.chart(db, place.audible, "release", pages=RELEASE_PAGES, enough=reached)
        return {"soon": sorted(_came_out(found, today)[1], key=lambda c: c.released)}
    return {"trending": hardcover.trending(db, clock.now().date())}


def fetch_unit(db: Session, source: str, market: str, unit: str) -> str:
    """Fetches one unit and keeps its lists. Returns ``ok``, ``empty``, ``failed`` or ``off`` (the source may not be
    asked: nothing was sent and nothing is noted)."""
    if source not in SOURCES or (source, unit) not in markets.UNITS:
        raise jobs.GiveUp("unknown unit")
    if source != "hardcover" and market not in markets.MARKETS:
        raise jobs.GiveUp("unknown market")
    now = clock.now()
    kind = markets.KIND_OF_SOURCE[source]
    try:
        fetched = _fetch(db, source, market, unit)
    except http.SourceOff:
        db.rollback()
        return "off"
    except http.SourceError as exc:
        db.rollback()
        row = _row(db, source, market, unit)
        row.state, row.error, row.fetched_at = "failed", exc.code[:40], now
        db.commit()
        if exc.code not in _PASSING:
            logger.warning("A list could not be fetched source=%s market=%s unit=%s error=%s", source, market, unit,
                           exc.code)
        return "failed"
    rows: list[ForeignBook] = []
    total = 0
    for list_name in markets.LISTS_OF_UNIT[(source, unit)]:
        db.execute(delete(ListEntry).where(ListEntry.source == source, ListEntry.market == market,
                                           ListEntry.list == list_name))
        seen: set[str] = set()
        rank = 0
        for candidate in fetched.get(list_name, []):
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            ref = foreign.ref_of(candidate)
            if ref in seen or rank >= LIST_SIZE:
                continue
            seen.add(ref)
            rank += 1
            row = foreign.upsert(db, candidate, kind, market=market)
            rows.append(row)
            db.add(ListEntry(
                source=source, market=market, list=list_name, rank=rank, ref=ref, foreign_id=row.id,
                title=candidate.title[:300], author=", ".join(candidate.authors[:3])[:300],
                cover_url=candidate.cover_url[:500], released=candidate.released, kind=kind,
                language=row.language, genre=row.genre, fetched_at=now))
        total += rank
    state = "ok" if total else "empty"
    log = _row(db, source, market, unit)
    log.state, log.count, log.error, log.fetched_at = state, total, "", now
    db.commit()
    foreign.plan_front_covers(db)
    logger.info("List fetched source=%s market=%s unit=%s books=%s", source, market, unit, total)
    return state


def lists_job(db: Session, job: Job) -> None:
    """The handler of the job ``lists``: one unit."""
    payload = job.payload if isinstance(job.payload, dict) else {}
    source, market, unit = (str(payload.get(name) or "") for name in ("source", "market", "unit"))
    fetch_unit(db, source, market, unit)


jobs.register("lists", lists_job)


# ---- The loop --------------------------------------------------------------------------------------------------


def housekeeping(db: Session) -> None:
    from ..oeuvre import store as catalogue

    catalogue.prune(db)
    foreign.prune(db)


def _look(rounds: int) -> None:
    """One look: the lists and the shelves that are due are queued; every hour the covers and descriptions that are a
    day old go; once a
    day the books nothing names are forgotten."""
    from ..browse import fetch as shelves

    with SessionLocal() as db:
        enqueue_due(db)
        shelves.enqueue_due(db)
        foreign.plan_front_covers(db)
        if rounds % 6 == 0:
            foreign.expire_pictures(db)
        if rounds % 144 == 0:
            housekeeping(db)


async def run_forever(stop: asyncio.Event) -> None:
    """Queues the lists that are due, now and then; forgets the books nothing names once a day."""
    waited = FIRST_WAIT_SECONDS
    rounds = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), waited)
            return
        except TimeoutError:
            pass
        waited = CHECK_SECONDS
        try:
            await asyncio.to_thread(_look, rounds)
        except Exception:
            logger.exception("Looking at the lists failed")
        rounds += 1
