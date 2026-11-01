"""Asking the sources for the words of a search, side by side.

Each source is asked in a thread of its own, with a database session of its own (a session belongs to one thread), and
through the one way out (``sources/http.py``: the switches, the throttle, the hosts). A run keeps what came back; the
request that started it waits a moment (``FIRST_WAIT``) and answers with what is there, and the page asks again by the
ticket (``wait_for``) until every source has answered. Audible and the German National Library answer in about a
second, Open Library in two to four.

What is asked, in the languages the operator fetches in (``languages``):

* **Open Library** by its own search over titles, authors and subjects (``q``), twice: with the edition in the first
  language fetched in, and with the edition in a second one (the titles in those languages);
* **the German National Library** by every word in any of its fields, when German is fetched in;
* **Audible** by the words, in the markets of the languages fetched in that the operator chose;
* **Hardcover** by the words, when the operator connected it (its titles in other languages come with it);
* and Open Library's authors by the words (the authors among the hits).

A run is the caller's: it is found by its ticket and the account together, and forgotten after ``KEEP_SECONDS``.

**What a run costs** (five to six requests to the sources for each search): a person has **one run that asks** at a
time. A new
search calls the person's last one off (``call_off``), and so does the page that gave up on it (``DELETE`` by its
ticket): what was not asked yet is never asked, and a question waiting for its turn at a source stops there
(``http.calls_off``). A run is kept (its hits can still be opened) but asks nothing more. A person keeps at most
``MAX_PER_PERSON`` runs, so that one person's typing never pushes out another's.

The hits are built once for each state of the answers (``hits_of``): the page that asks again, the cover and the book
that is opened read the same hits without building them anew.

**A source that is not asked is named** (``Run.silent``): one that rests after errors (``throttle``: until when) and one
that did not answer this search. The person is told that the hits are incomplete, never that there are none.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...db import SessionLocal
from ..sources import audible, config, dnb, hardcover, http, openlibrary, throttle
from ..sources.model import Candidate, Entry, Query
from .asked import Asked, read

logger = logging.getLogger("nexbooks.search")

#: How long the request that starts a search waits for the sources before it answers with what is there (seconds).
FIRST_WAIT = 2.5
#: How long a request that asks again by the ticket waits for more.
NEXT_WAIT = 2.0
#: How long a run is kept, and how many runs at most (of everybody, and of one person).
KEEP_SECONDS = 600
MAX_RUNS = 300
MAX_PER_PERSON = 6
#: The most markets of Audible a search asks.
MAX_MARKETS = 3
#: Threads that ask the sources (a search asks five or six questions; the throttle of each source does the rest).
WORKERS = 12

_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="nexbooks-search")
_runs: OrderedDict[str, Run] = OrderedDict()
_runs_lock = threading.Lock()


@dataclass
class Run:
    ticket: str
    uid: int
    asked: Asked
    #: The languages the operator fetches in, in his order, and whether the original language counts too.
    languages: list[str]
    original: bool
    started: float = field(default_factory=time.monotonic)
    #: For each question (``openlibrary:de``, ``dnb``, ``audible:de``, ``hardcover``, ``people``): ``waiting``,
    #: ``done``, ``failed``, ``off`` or ``called_off`` (the person searched for something else meanwhile); and what came
    #: back.
    states: dict[str, str] = field(default_factory=dict)
    entries: dict[str, list[Entry]] = field(default_factory=dict)
    people: list[openlibrary.AuthorHit] = field(default_factory=list)
    #: When each question was answered (seconds after the start), for the log and the measurement.
    took: dict[str, float] = field(default_factory=dict)
    changed: threading.Condition = field(default_factory=threading.Condition)
    #: The person searched for something else, or the page gave up on it: nothing more is asked.
    called_off: bool = False
    #: The hits built of the answers as they were (``hits_of``), and of which state of them.
    built: tuple[tuple[tuple[str, str], ...], list[Any]] | None = None
    #: The sources a search asks that rested when it started, and until when (``resting_sources``).
    resting: dict[str, datetime | None] = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return any(state == "waiting" for state in self.states.values())

    @property
    def failed(self) -> bool:
        """Every source that was asked about books failed. Not when one of them was called off before it answered (the
        person gave up waiting): that one did not fail, and its answer is not known."""
        books = {name: state for name, state in self.states.items() if name != "people" and state != "off"}
        return bool(books) and all(state == "failed" for state in books.values())

    def silent(self) -> dict[str, datetime | None]:
        """The sources that gave nothing for this search because they rest (until when) or did not answer it (until
        when they rest now, or None when they do not), in the order of ``config.CATALOGUES``."""
        found: dict[str, datetime | None] = dict(self.resting)
        with self.changed:
            failed = [name.split(":")[0] for name, state in self.states.items()
                      if state == "failed" and name != "people"]
        for source in failed:
            if source not in found:
                until = throttle.state_of(source).blocked_until
                found[source] = until if until is not None and until > throttle.now() else None
        order = {source: index for index, source in enumerate(config.CATALOGUES)}
        return dict(sorted(found.items(), key=lambda item: order.get(item[0], 99)))

    def all_entries(self) -> list[Entry]:
        """Copies of every record that came back (the building changes them), each record once."""
        with self.changed:
            found = {(entry.source, entry.key): entry for name in sorted(self.entries) for entry in self.entries[name]}
            return [Entry.from_json(entry.to_json()) for entry in found.values()]


# ---- What each source gives ------------------------------------------------------------------------------------


def audible_entries(found: list[Candidate]) -> list[Entry]:
    """The products of a search of Audible as entries: a recording each, with its narrators and its place in the
    answer."""
    entries: list[Entry] = []
    for rank, candidate in enumerate(found):
        entry = audible.entry_of(candidate, len(candidate.authors))
        entry.signals = {**entry.signals, "rank": rank, "narrators": candidate.narrators[:4],
                         "publisher": candidate.publisher[:120], "subtitle": candidate.subtitle[:200],
                         "market": candidate.market, "variant": candidate.variant}
        if candidate.full_title:
            entry.signals["full_title"] = candidate.full_title[:300]
        entries.append(entry)
    return entries


def hardcover_entries(found: list[Candidate]) -> list[Entry]:
    """The books of a search of Hardcover as entries: the book with its own title, and its title in each language its
    editions have (one work: the same book of Hardcover)."""
    entries: list[Entry] = []
    for rank, candidate in enumerate(found):
        signals: dict[str, Any] = {"rank": rank, "work_level": True,
                                   "genres": [s.lower() for s in candidate.subjects[:20]]}
        entries.append(Entry(
            source="hardcover", key=candidate.key, work_key=candidate.work_key, title=candidate.title,
            language="", authors=candidate.authors[:6], year=candidate.year, isbns=candidate.isbns[:60],
            asin=candidate.asin, series=candidate.series, series_pos=candidate.series_pos, signals=signals,
            authors_count=max(1, len(candidate.authors)), weight=candidate.weight, cover_url=candidate.cover_url))
        for language, titles in list(candidate.titles.items())[:12]:
            if titles and titles[0]:
                entries.append(Entry(
                    source="hardcover", key=f"{candidate.key}:{language}", work_key=candidate.work_key,
                    title=titles[0], language=language, authors=candidate.authors[:6], series=candidate.series,
                    series_pos=candidate.series_pos, signals={**signals}, weight=candidate.weight,
                    authors_count=max(1, len(candidate.authors))))
    return entries


#: Records of the library for the words, fewer than this: the words are asked for once more by their start.
NEAR_BELOW = 3


def library_entries(db: Session, asked: Asked) -> list[Entry]:
    """The records of the German National Library for the words. The library knows no slips of the keys (``Hary Poter
    Feuerkelch``, ``Frankenstain``): when it has fewer than ``NEAR_BELOW`` records for the words, it is asked once more
    for the starts of the words (``dnb.word_start``), and those records follow."""
    found = dnb.text_entries(db, asked.base_text, volume=asked.volume, interactive=True)
    words = [word for word in asked.base if len(word) >= 2 and not word.isdigit()]
    if not words or len(found) >= NEAR_BELOW:
        return found
    if all(dnb.word_start(word) == word for word in words):
        return found
    try:
        near = dnb.text_entries(db, asked.base_text, volume=asked.volume, interactive=True, starts=True)
    except http.SourceError:
        # (The first answer stands: the second question was only a try.)
        return found
    seen = {entry.key for entry in found}
    more = [entry for entry in near if entry.key not in seen]
    for index, entry in enumerate(more):
        entry.signals = {**entry.signals, "rank": len(found) + index, "near": True}
    return found + more


def _asked_sources(languages: list[str]) -> list[str]:
    """The sources a search in these languages asks when they may be asked."""
    return [source for source in config.CATALOGUES if source != "dnb" or "de" in languages]


def resting_sources(db: Session, languages: list[str]) -> dict[str, datetime | None]:
    """The sources a search would ask that are switched on but rest after errors now, and until when."""
    found: dict[str, datetime | None] = {}
    for source in _asked_sources(languages):
        if config.availability(db, source).state == "resting":
            found[source] = throttle.state_of(source).blocked_until
    return found


def questions(db: Session, asked: Asked, languages: list[str]) -> dict[str, Callable[[Session], list[Entry]]]:
    """The questions a search asks, by name, of the sources that may be asked now."""
    ready = set(config.ready_sources(db, config.CATALOGUES))
    text = asked.text
    first = languages[0] if languages else "en"
    found: dict[str, Callable[[Session], list[Entry]]] = {}
    if "openlibrary" in ready:
        # Once in the first language fetched in and once in another (English when it is not fetched in): the title of
        # the work is then known in two languages, and in which of them the work carries its own.
        for code in list(dict.fromkeys([first, *languages[1:2], "en"]))[:2]:
            found[f"openlibrary:{code}"] = lambda db, code=code: openlibrary.text_entries(db, text, code,
                                                                                         interactive=True)
    if "dnb" in ready and "de" in languages:
        found["dnb"] = lambda db: library_entries(db, asked)
    if "audible" in ready:
        chosen = config.chosen_markets(db)
        markets = [config.MARKET_OF_LANGUAGE[code] for code in languages
                   if config.MARKET_OF_LANGUAGE.get(code) in chosen] or chosen[:1]
        for market in list(dict.fromkeys(markets))[:MAX_MARKETS]:
            query = Query(kind="audio", title=text, fallback=False)
            found[f"audible:{market}"] = lambda db, market=market, query=query: audible_entries(
                audible.search(db, query, market, interactive=True))
    if "hardcover" in ready:
        query = Query(kind="ebook", title=text)
        found["hardcover"] = lambda db: hardcover_entries(hardcover.search(db, query, interactive=True))
    return found


# ---- A run -------------------------------------------------------------------------------------------------------


def _ask(run: Run, name: str, question: Callable[[Session], Any]) -> None:
    state = "done"
    result: Any = []
    if run.called_off:
        return
    http.calls_off.check = lambda: run.called_off
    try:
        with SessionLocal() as db:
            result = question(db)
    except http.SourceOff:
        state = "off"
    except http.SourceError as exc:
        state = "called_off" if exc.code == "called_off" else "failed"
        if state == "failed":
            logger.info("A source did not answer a catalogue search question=%s error=%s", name, exc.code)
    except Exception:
        logger.exception("A catalogue search question failed question=%s", name)
        state = "failed"
    finally:
        http.calls_off.check = None
    with run.changed:
        if run.called_off:
            # (What came back after the run was called off is not taken: the answer stays as the person last saw it.)
            return
        if name == "people":
            run.people = list(result) if state == "done" else []
        else:
            run.entries[name] = list(result) if state == "done" else []
        run.states[name] = state
        run.took[name] = round(time.monotonic() - run.started, 3)
        run.changed.notify_all()


def call_off(run: Run) -> None:
    """Nothing more is asked for this run: the questions not asked yet are not asked, one waiting for its turn at a
    source stops there, and the answer stays as it is (``called_off``)."""
    with run.changed:
        if run.called_off:
            return
        run.called_off = True
        for name, state in run.states.items():
            if state == "waiting":
                run.states[name] = "called_off"
        run.changed.notify_all()


def _keep(run: Run) -> None:
    """Keeps a new run: the person's runs that still ask are called off, and the oldest runs go (the person's own first,
    past ``MAX_PER_PERSON``; then anybody's, past ``MAX_RUNS``)."""
    now = time.monotonic()
    with _runs_lock:
        for ticket in [t for t, r in _runs.items() if now - r.started > KEEP_SECONDS]:
            _runs.pop(ticket, None)
        mine = [r for r in _runs.values() if r.uid == run.uid]
        for old in mine:
            call_off(old)
        for old in mine[: max(0, len(mine) - MAX_PER_PERSON + 1)]:
            _runs.pop(old.ticket, None)
        while len(_runs) >= MAX_RUNS:
            _runs.popitem(last=False)
        _runs[run.ticket] = run


def start(db: Session, uid: int, raw: str, *, languages: list[str], original: bool) -> Run | None:
    """Starts a search and waits up to ``FIRST_WAIT`` for the sources. None when no source may be asked (or there is
    nothing to ask for)."""
    asked = read(raw)
    if not asked.text or not asked.words:
        return None
    found = questions(db, asked, languages)
    resting = resting_sources(db, languages)
    if not found and not resting:
        return None
    run = Run(ticket=secrets.token_urlsafe(12), uid=uid, asked=asked, languages=languages, original=original,
              resting=resting)
    run.states = {name: "waiting" for name in [*found, "people"]}
    if not found or not config.availability(db, "openlibrary").ready or len(asked.text) < 3:
        run.states.pop("people")
    _keep(run)
    for name, question in found.items():
        _pool.submit(_ask, run, name, question)
    if "people" in run.states:
        text = asked.text
        _pool.submit(_ask, run, "people", lambda db: openlibrary.find_authors(db, text, interactive=True))
    _wait(run, FIRST_WAIT)
    return run


def _wait(run: Run, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    with run.changed:
        while run.pending:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            run.changed.wait(left)


def find(uid: int, ticket: str) -> Run | None:
    """The run of this ticket, when it is the caller's and still kept."""
    with _runs_lock:
        run = _runs.get(ticket)
    if run is None or run.uid != uid or time.monotonic() - run.started > KEEP_SECONDS:
        return None
    return run


def wait_for(uid: int, ticket: str, seconds: float = NEXT_WAIT) -> Run | None:
    """The run of this ticket, when it is the caller's, after waiting up to ``seconds`` for the sources still asked."""
    run = find(uid, ticket)
    if run is not None:
        _wait(run, seconds)
    return run


def hits_of(run: Run, build: Callable[[list[Entry]], list[Any]]) -> list[Any]:
    """The hits of the answers as they are now: built once for each state of them (``build`` of the records)."""
    with run.changed:
        state = tuple(sorted(run.states.items()))
        if run.built is not None and run.built[0] == state:
            return run.built[1]
    hits = build(run.all_entries())
    with run.changed:
        if tuple(sorted(run.states.items())) == state:
            run.built = (state, hits)
    return hits


def forget() -> None:
    """Every run forgotten (the tests)."""
    with _runs_lock:
        _runs.clear()


__all__ = ["FIRST_WAIT", "MAX_PER_PERSON", "Run", "audible_entries", "call_off", "find", "forget",
           "hardcover_entries", "hits_of", "questions", "resting_sources", "start", "wait_for"]
