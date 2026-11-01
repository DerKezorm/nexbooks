"""The shelves of Browse, fetched in the background and kept.

**Who fetches, and when.** Exactly as for the lists of Discover (``services/discover/lists.py``): nobody fetches while a
person waits. A person who opens Browse says that someone looks at a market (``want``: the moment is noted, and a job is
queued for every part of a shelf that was never fetched or is a day old); a slow loop queues the parts that someone
looked at in the last fortnight and are old again, a few at a time. A market nobody looks at is not asked for.

**A unit is one source's part of one shelf in one market**: Apple's charts of the genres of the shelf, or Open Library's
four questions (the books most wanted of the last years, of the last two years, the ones that won a prize, the old ones
still read). A job replaces what the unit gave before, all of it in one transaction; a unit that fails keeps the old
books.

**Every request goes through the one way out** (``sources/http.py``): the operator's main switch, the switch of the
source, the throttle, the walls of the address. With the main switch off no job is even queued, and a job queued before
asks nothing.

**Half a day of a source at most.** The shelves queue nothing more once a source was asked half as often today as it
may be (``BUDGET_SHARE``): the lists of Discover and the matching of the books need the rest, however many markets
people look at. No cover is fetched with a shelf; the covers of the first books of each section are queued afterwards
(``foreign.plan_front_covers``) and the rest are fetched when their pictures are asked for, in the lane of Discover
(``jobs.LANES``), so that a scan never waits behind them.

**Audible and Hardcover are not needed.** Both are off or unconnected unless the operator chose otherwise, and a
shelf is
full without them; a source that needs a token or an account takes no part here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import zip_longest
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ... import clock
from ...models import SOURCES, Job, ShelfEntry, ShelfFetch
from .. import genres, jobs
from ..discover import foreign, markets
from ..sources import apple, config, http, openlibrary, throttle
from ..sources.model import Candidate
from . import cache as view_cache
from . import shelves

logger = logging.getLogger("nexbooks.browse")

#: The sources that fill shelves, in the order they speak when two of them name the same place.
SHELF_SOURCES = ("apple", "openlibrary")
#: A part of a shelf is fetched again after a day, and after an hour when the last try failed.
REFRESH_HOURS = 24
RETRY_HOURS = 1
#: A market nobody looked at for this long is not fetched again.
WANTED_DAYS = 14
#: The most units the loop queues in one look, and how often a person's look is noted.
PER_LOOK = 8
TOUCH_SECONDS = 3600
#: Failures that are the source's own and pass (it is asked again in an hour); anything else is told to the log.
_PASSING = frozenset({"timeout", "unreachable", "server", "rate_limited", "busy", "daily_limit", "http_error"})
#: The base of the numbers jobs wait on (the lists use the small ones; the queue keeps one waiting job per number and
#: kind).
_REF_BASE = 1_000_000


# ---- What is due -----------------------------------------------------------------------------------------------


def units(market: str) -> list[tuple[str, str]]:
    """``(source, shelf)`` of every part of a shelf there is to fetch in a market."""
    if market not in markets.MARKETS:
        return []
    return [(source, shelf.key) for shelf in shelves.SHELVES for source in SHELF_SOURCES
            if source != "apple" or shelf.apple]


def unit_ref(source: str, market: str, shelf: str) -> int:
    """A number for a job to wait on, the same for the same unit."""
    keys = list(markets.MARKETS)
    return _REF_BASE + (SHELF_SOURCES.index(source) * 100 + keys.index(market)) * 100 + shelves.INDEX_OF[shelf]


def is_due(row: ShelfFetch | None, now: Any) -> bool:
    """Whether a unit is to be fetched: never fetched, older than a day, or (after a failure) older than an hour."""
    if row is None or row.fetched_at is None:
        return True
    hours = RETRY_HOURS if row.state == "failed" else REFRESH_HOURS
    return bool(now - row.fetched_at >= timedelta(hours=hours))


def _row(db: Session, source: str, market: str, shelf: str) -> ShelfFetch:
    row = db.get(ShelfFetch, (source, market, shelf))
    if row is None:
        row = ShelfFetch(source=source, market=market, shelf=shelf, state="", count=0, error="")
        db.add(row)
        db.flush()
    return row


#: The share of a source's daily limit the shelves may use (in the background and for their covers): the rest stays
#: for the lists of Discover and the matching of the books, whatever the number of markets people look at.
BUDGET_SHARE = 0.5


def within_budget(source: str) -> bool:
    """Whether the shelves may still ask ``source`` today (``BUDGET_SHARE`` of its daily limit)."""
    return throttle.state_of(source).day_count < BUDGET_SHARE * config.LIMITS[source].per_day


def waiting(db: Session, ref: int) -> bool:
    """Whether a job for the unit ``ref`` waits or runs."""
    return bool(db.scalar(select(Job.id).where(Job.kind == "shelves", Job.ref == ref,
                                               Job.state.in_(("queued", "running")))))


def queue(db: Session, source: str, market: str, shelf: str) -> bool:
    """A job for a unit, unless one waits already, the source may not be asked now or the shelves used their share of
    its day. Does not commit."""
    if not config.availability(db, source).ready or not within_budget(source):
        return False
    ref = unit_ref(source, market, shelf)
    if waiting(db, ref):
        return False
    jobs.enqueue(db, "shelves", ref=ref, payload={"source": source, "market": market, "shelf": shelf})
    return True


def want(db: Session, market: str) -> int:
    """A person looks at the shelves of a market: it is noted (so that the loop keeps them fresh), and what was never
    fetched, or is old, is queued. Commits. Returns how many jobs were queued."""
    if market not in markets.MARKETS:
        return 0
    now = clock.now()
    queued = 0
    for source, shelf in units(market):
        row = _row(db, source, market, shelf)
        if row.wanted_at is None or (now - row.wanted_at).total_seconds() >= TOUCH_SECONDS:
            row.wanted_at = now
        if is_due(row, now) and queue(db, source, market, shelf):
            queued += 1
    db.commit()
    return queued


def enqueue_due(db: Session) -> int:
    """The loop's look: the units someone looked at in the last fortnight that are due, the longest waiting first, at
    most ``PER_LOOK``."""
    now = clock.now()
    since = now - timedelta(days=WANTED_DAYS)
    queued = 0
    for row in db.scalars(select(ShelfFetch).where(ShelfFetch.wanted_at.is_not(None), ShelfFetch.wanted_at >= since)
                          .order_by(ShelfFetch.fetched_at.is_not(None), ShelfFetch.fetched_at)):
        if queued >= PER_LOOK:
            break
        if row.market not in markets.MARKETS or row.source not in SHELF_SOURCES or row.shelf not in shelves.BY_KEY:
            continue
        if is_due(row, now) and queue(db, row.source, row.market, row.shelf):
            queued += 1
    db.commit()
    return queued


# ---- Asking the sources ----------------------------------------------------------------------------------------


@dataclass
class Placed:
    """A book a source gives for a section, with what speaks for it: the prize, how strongly it belongs to the shelf
    (``evidence``: the share of its subjects that speak for the shelf, or ``shelves.APPLE_EVIDENCE`` for a book of
    Apple's chart of a genre of the shelf), the series it is a volume of."""

    candidate: Candidate
    award: str = ""
    evidence: float = 0.0
    series: str = ""


Found = dict[str, list[Placed]]


def _weave(lists: list[list[Candidate]]) -> list[Candidate]:
    """Several charts as one: the first of each, then the second of each, and so on, a book once."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for rank in zip_longest(*lists):
        for candidate in rank:
            if candidate is not None and candidate.key not in seen:
                seen.add(candidate.key)
                out.append(candidate)
    return out


def _charts(db: Session, country: str, asked: tuple[int, ...], allowed: frozenset[int]) -> list[Candidate]:
    """Apple's charts of these genres, woven, and of them the books Apple files under a genre of the shelf (a chart of
    classics holds novels of the day, filed under fiction)."""
    charts: list[list[Candidate]] = []
    failure: http.SourceError | None = None
    for genre in asked:
        try:
            charts.append(apple.genre_chart(db, country, genre))
        except http.SourceOff:
            raise
        except http.SourceError as exc:
            failure = exc
    if not charts and failure is not None:
        raise failure
    return [c for c in _weave(charts) if c.category.isdigit() and int(c.category) in allowed]


def _apple_place(candidate: Candidate) -> Placed:
    return Placed(candidate, evidence=shelves.APPLE_EVIDENCE,
                  series=shelves.series_key(candidate.title, candidate.authors, []))


def _from_apple(db: Session, shelf: shelves.Shelf, market: str, today: date) -> Found:
    """Apple's charts of the genres of the shelf: the chart is ``popular``; what came out in the last ``NEW_DAYS`` is
    ``new``; what is announced is ``soon``; the charts of classics for children and young people are ``classic``. Only
    the books filed under a genre of the shelf count. Apple does not say what language a book is in, and none is put
    on it."""
    place = markets.MARKETS[market]
    out: Found = {"popular": [], "new": [], "soon": []}
    day, begin = today.isoformat(), (today - timedelta(days=shelves.NEW_DAYS)).isoformat()
    for candidate in _charts(db, place.apple, shelf.apple, shelf.apple_ok):
        if candidate.released and candidate.released > day:
            out["soon"].append(_apple_place(candidate))
            continue
        out["popular"].append(_apple_place(candidate))
        if candidate.released and candidate.released >= begin:
            out["new"].append(_apple_place(candidate))
    if shelf.apple_classic and "classic" in shelf.sections:
        # Only what Apple files under the classics for them: the chart holds novels of the day as well.
        out["classic"] = [_apple_place(c) for c in _charts(db, place.apple, shelf.apple_classic,
                                                            frozenset(shelf.apple_classic))
                          if not (c.released and c.released > day)]
    return out


def _from_openlibrary(db: Session, shelf: shelves.Shelf, market: str, today: date) -> Found:
    """Open Library's questions for the shelf, in the language of the market (the title and the picture of the edition
    in that language), and of the answers the books whose subjects say plainly that they belong (``shelves.judge``)."""
    language = markets.MARKETS[market].language
    year = today.year
    base = {"subjects": shelf.subjects, "exclude": shelf.exclude}
    asked = [s for s in shelf.ol_sections if s in shelf.sections]
    out: Found = {}

    def books(section: str, *, awards: tuple[str, ...] = (), years: tuple[int, int] | None = None) -> None:
        query = openlibrary.subject_query(awards=awards, years=years, **base)
        kept: list[Placed] = []
        for candidate in openlibrary.shelf(db, query, language=language, sort="readinglog"):
            if not candidate.cover_url:
                continue
            evidence = shelves.judge(shelf, candidate.subjects, title=candidate.title)
            if evidence is None:
                continue
            if section == "new" and ((candidate.edition_year is not None and candidate.edition_year < year - 1)
                                     or shelves.is_old_text(candidate.subjects)):
                continue
            if section == "classic" and (candidate.year is None
                                         or not shelf.classic_from <= candidate.year <= shelf.classic_until):
                # A first year no second edition backs is no proof of age.
                continue
            award = shelves.award_of(shelf, candidate.subjects, candidate.year) if section == "award" else ""
            if section == "award" and not award:
                continue
            kept.append(Placed(candidate, award, evidence,
                               shelves.series_key(candidate.title, candidate.authors, candidate.subjects)))
        out[section] = kept

    if "popular" in asked:
        books("popular", years=(year - 9, year))
    if "new" in asked:
        books("new", years=(year - 1, year))
    if "award" in asked and shelf.awards:
        books("award", awards=shelf.awards)
    if "classic" in asked:
        books("classic", years=(shelf.classic_from, shelf.classic_until))
    return out


# ---- Keeping a unit --------------------------------------------------------------------------------------------


def _with_hint(shelf: shelves.Shelf, candidate: Candidate) -> Candidate:
    """The book with a word of the shelf among its subjects when its own give no genre, so that it stands on the
    shelf in the colours of the shelf."""
    if not genres.genre_of(candidate.subjects):
        candidate.subjects = [*candidate.subjects, shelf.hint]
    return candidate


def fetch_unit(db: Session, source: str, market: str, shelf_key: str) -> str:
    """Fetches one unit and keeps its books. Returns ``ok``, ``empty``, ``failed`` or ``off`` (the source may not be
    asked: nothing was sent and nothing is noted). Covers are not fetched here: the first books of the shelf are queued
    for the lane of Discover (``foreign.plan_front_covers``), the rest are fetched when a person is shown them."""
    shelf = shelves.BY_KEY.get(shelf_key)
    if source not in SHELF_SOURCES or source not in SOURCES or shelf is None:
        raise jobs.GiveUp("unknown unit")
    if market not in markets.MARKETS:
        raise jobs.GiveUp("unknown market")
    now = clock.now()
    today = now.date()
    try:
        found = _from_apple(db, shelf, market, today) if source == "apple" else _from_openlibrary(
            db, shelf, market, today)
    except http.SourceOff:
        db.rollback()
        return "off"
    except http.SourceError as exc:
        db.rollback()
        row = _row(db, source, market, shelf_key)
        row.state, row.error, row.fetched_at = "failed", exc.code[:40], now
        db.commit()
        if exc.code not in _PASSING:
            logger.warning("A shelf could not be fetched source=%s market=%s shelf=%s error=%s", source, market,
                           shelf_key, exc.code)
        return "failed"
    db.execute(delete(ShelfEntry).where(ShelfEntry.source == source, ShelfEntry.market == market,
                                        ShelfEntry.shelf == shelf_key))
    total = 0
    for section in shelves.STORED_SECTIONS:
        seen: set[str] = set()
        series: dict[str, int] = {}
        rank = 0
        for placed in found.get(section, []):
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            candidate = placed.candidate
            ref = foreign.ref_of(candidate)
            if ref in seen or rank >= shelves.SECTION_BOOKS:
                continue
            if placed.series and series.get(placed.series, 0) >= shelves.PER_SERIES:
                continue
            seen.add(ref)
            if placed.series:
                series[placed.series] = series.get(placed.series, 0) + 1
            rank += 1
            topics = shelves.topics_of(shelf, " ".join([candidate.title, *candidate.subjects]))
            row = foreign.upsert(db, _with_hint(shelf, candidate), "ebook", market=market)
            db.add(ShelfEntry(source=source, market=market, shelf=shelf_key, section=section, rank=rank,
                              foreign_id=row.id, award=placed.award[:80], topics=topics, fetched_at=now,
                              evidence=round(placed.evidence, 4), series_key=placed.series[:160]))
        total += rank
    log = _row(db, source, market, shelf_key)
    log.state, log.count, log.error, log.fetched_at = "ok" if total else "empty", total, "", now
    db.commit()
    view_cache.forget(market)
    foreign.plan_front_covers(db)
    logger.info("Shelf fetched source=%s market=%s shelf=%s books=%s", source, market, shelf_key, total)
    return log.state


def shelves_job(db: Session, job: Job) -> None:
    """The handler of the job ``shelves``: one unit."""
    payload = job.payload if isinstance(job.payload, dict) else {}
    source, market, shelf = (str(payload.get(name) or "") for name in ("source", "market", "shelf"))
    fetch_unit(db, source, market, shelf)


jobs.register("shelves", shelves_job)
