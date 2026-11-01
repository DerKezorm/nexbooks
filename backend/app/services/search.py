"""Searching the books: in the caller's libraries, and in the catalogues of the sources.

**Rights are in the query, twice.** Every word in the index carries the library of its book in front
(``services/search_index.py``), and a search asks only for the words of the libraries the caller is a member of:
``("l3lxlant"* OR "l12lxlant"*)``. So the index never reads a word, or the books that carry it, in another library, and
the time a search takes does not depend on what other people have. The statement that finds the works carries the
rights once more (the visible editions: in a library of the caller, with a file on the disk), so the order, the count
and the cut-off follow only what the caller may see. Nothing is filtered afterwards.

**How a word is found**: every word of the search is the start of a word (``lant`` finds ``Lantern``), every word must
be found, each in any of the fields (title, original title, people, series, description, ISBN and ASIN). Case and
accents do not count. A hit in the title weighs more than one in the description. At most ``MAX_WORDS`` words count.

**The catalogue** is the sources the operator switched on (``catalog``): asked side by side, politely (the throttle of
each source and a brake per person), nothing but the typed words goes out; one hit for each book
(``services/booksearch``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Float, Integer, column, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import FTS_TABLE, Edition, Work
from . import booksearch, search_index
from . import works as works_service
from .booksearch import ask as booksearch_ask
from .booksearch import present as booksearch_present
from .libraries import member_library_ids
from .procure import formats as procure_formats
from .sources import config, openlibrary

logger = logging.getLogger("nexbooks.search")

MAX_WORDS = 8
MAX_WORD = 40
MAX_QUERY = 200
#: How many works one answer holds at most.
MAX_HITS = 30
#: Hits the index hands over before the rights are applied again and the works are cut off.
INDEX_HITS = 500
#: Longest a search may take in the database.
MAX_SECONDS = 5.0
PROGRESS_STEPS = 10_000

#: The weights of the fields, in the order of the table: title, original title, people, series, description, ids.
WEIGHTS = (10.0, 6.0, 5.0, 4.0, 1.0, 8.0)
FIELDS = "{title original people series description ids}"


def words_of(raw: str) -> list[str]:
    """The words of a search: letters and digits, lower case, ``ß`` as ``ss``, each cut to ``MAX_WORD``, at most
    ``MAX_WORDS`` of them, none twice."""
    found = search_index.words(raw[:MAX_QUERY])
    return list(dict.fromkeys(word[:MAX_WORD] for word in found))[:MAX_WORDS]


def expression(words: list[str], libraries: list[int]) -> str:
    """The FTS5 query: each word as the start of a word of any of the caller's libraries. The pieces are quoted tokens
    made of letters and digits only; nothing a person typed is syntax."""
    prefixes = [search_index.scope_prefix(library) for library in libraries]
    terms = [" OR ".join(f'"{prefix}{word}"*' for prefix in prefixes) for word in words]
    return f"{FIELDS} : (" + " AND ".join(f"({term})" for term in terms) + ")"


@contextmanager
def _bounded(db: Session) -> Iterator[None]:
    """A search that runs longer than ``MAX_SECONDS`` is stopped by SQLite itself, and answers 503."""
    raw = db.connection().connection.driver_connection
    deadline = time.monotonic() + MAX_SECONDS
    raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, PROGRESS_STEPS)
    try:
        yield
    except OperationalError:
        if time.monotonic() <= deadline:
            raise
        logger.warning("A search was stopped at the time limit seconds=%s", MAX_SECONDS)
        raise error("search_too_slow", "This search takes too long. Narrow it down with more words.", 503) from None
    finally:
        raw.set_progress_handler(None, 0)


def work_ids(db: Session, uid: int, raw: str, limit: int = MAX_HITS) -> list[int]:
    """The works the caller may see that the words find, best first."""
    words = words_of(raw)
    if not words:
        return []
    libraries = sorted(db.scalars(member_library_ids(uid)))
    if not libraries:
        return []
    lookup = text(
        f"SELECT rowid AS id, bm25({FTS_TABLE}, {', '.join(str(w) for w in WEIGHTS)}) AS rank FROM {FTS_TABLE} "  # noqa: S608 - constants
        f"WHERE {FTS_TABLE} MATCH :expr ORDER BY rank LIMIT {INDEX_HITS}").bindparams(
        expr=expression(words, libraries)).columns(column("id", Integer), column("rank", Float))
    hits = lookup.subquery()
    best = func.min(hits.c.rank)
    query = (select(Edition.work_id, best).join(hits, hits.c.id == Edition.id)
             .where(*works_service.visible_edition_clause(uid)).group_by(Edition.work_id)
             .order_by(best, Edition.work_id).limit(max(1, min(limit, MAX_HITS))))
    with _bounded(db):
        return [row[0] for row in db.execute(query).all()]


def find(db: Session, uid: int, raw: str, limit: int = MAX_HITS) -> list[works_service.WorkView]:
    """The works as the caller sees them (``works.views``), in the order of the search."""
    ids = work_ids(db, uid, raw, limit)
    if not ids:
        return []
    found = {work.id: work for work in db.scalars(select(Work).where(Work.id.in_(ids)))}
    ordered = [found[ident] for ident in ids if ident in found]
    return works_service.views(db, uid, ordered)


# ---- The catalogue ----------------------------------------------------------------------------------------------


@dataclass
class CatalogResult:
    #: Whether any source may be asked at all (the operator's switches, a token, a pause after errors).
    available: bool
    #: Every source that was asked failed.
    failed: bool = False
    #: Sources are still being asked: the ticket asks again (``catalog_again``).
    pending: bool = False
    ticket: str = ""
    hits: list[booksearch_present.Shown] = field(default_factory=list)
    #: The authors Open Library knows by the typed words.
    people: list[openlibrary.AuthorHit] = field(default_factory=list)
    #: The words that were searched for.
    words: str = ""
    #: The sources that gave nothing for this search: they rest after errors (until when) or did not answer it
    #: (``booksearch.Run.silent``). The hits are then incomplete.
    resting: dict[str, datetime | None] = field(default_factory=dict)


def catalog(db: Session, uid: int, raw: str) -> CatalogResult:
    """Asks the sources the operator switched on for the typed words (side by side, ``services/booksearch``) and
    answers with one hit for each book, as far as the sources have answered after a moment."""
    text_ = " ".join(search_index.prepare(raw[:MAX_QUERY]).split())
    # (A source that rests after errors is still one the operator switched on: the search says it rests.)
    usable = [source for source in config.CATALOGUES
              if config.availability(db, source).state in ("ready", "resting")]
    if not usable or not text_:
        return CatalogResult(available=bool(usable))
    chosen = procure_formats.config(db)
    run = booksearch.start(db, uid, text_, languages=list(chosen["languages"]) or ["en"],
                           original=bool(chosen["original"]))
    if run is None:
        return CatalogResult(available=True)
    return _result(db, uid, run)


def catalog_again(db: Session, uid: int, ticket: str) -> CatalogResult | None:
    """The search of that ticket once more, with what the sources have answered meanwhile; None when the ticket is
    not the caller's or is gone."""
    run = booksearch.wait_for(uid, ticket)
    return None if run is None else _result(db, uid, run)


def _hits(run: booksearch.Run) -> list[booksearch.Hit]:
    return booksearch_ask.hits_of(run, lambda entries: booksearch.build(
        entries, run.asked, languages=run.languages, original=run.original))


def _result(db: Session, uid: int, run: booksearch.Run) -> CatalogResult:
    # (Whether it is pending is read before the records: a source that answers in between is in the next answer.)
    pending = run.pending
    hits = _hits(run)
    return CatalogResult(available=True, failed=run.failed and not pending, pending=pending, ticket=run.ticket,
                         hits=booksearch_present.present(db, uid, hits, run.ticket), people=list(run.people),
                         words=run.asked.text, resting=run.silent())


def catalog_hit(uid: int, ticket: str, key: str) -> booksearch.Hit | None:
    """A hit of the caller's search of that ticket, by its key; None when either is gone."""
    run = booksearch_ask.find(uid, ticket)
    if run is None:
        return None
    return next((hit for hit in _hits(run) if hit.key == key), None)


def catalog_stop(uid: int, ticket: str) -> bool:
    """The caller's search of that ticket asks nothing more (the page gave up on it). False when it is not theirs."""
    run = booksearch_ask.find(uid, ticket)
    if run is None:
        return False
    booksearch_ask.call_off(run)
    return True
