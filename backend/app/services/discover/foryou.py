"""For you: the gaps in the series a person reads, and more of the authors on their shelves.

**What a person reads is what they may see.** The series and the authors are taken from the editions of the libraries
the person is a member of and from nothing else (``works.visible_edition_clause``); a series that lies only in
somebody else's library is not theirs, and neither is its gap. What the sources say about a series (its volumes and
when each comes out) is public and is kept once for everybody (``source_cache``, a week); the person's own volumes are
set against it at the moment they ask.

**Nobody waits for a source.** A request reads what is kept. A series or an author nothing is kept for is *pending*: a
job is queued (``series``, ``authors``) and the page asks again a little later. With no source ready nothing is queued
and the page says what is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ... import clock
from ...models import AUTHOR, Credit, Edition, ExternalRef, ForeignBook, Person, Series
from .. import jobs, settings_service
from ..readers.text import fold
from ..sources import audible, cache, config, hardcover, http
from ..sources.model import Candidate
from ..works import visible_edition_clause
from . import foreign, owned

logger = logging.getLogger("nexbooks.discover")

CACHE = "discover"
CACHE_DAYS = 7
#: How many series and authors one page looks at, and how many books of an author it shows.
MAX_SERIES = 12
MAX_AUTHORS = 4
PER_AUTHOR = 8


@dataclass
class Volume:
    """One place in a series: a volume the person has (``work_id``), or one they have not (``book``)."""

    pos: float
    state: str  # ``own``, ``missing`` or ``soon``
    work_id: int | None = None
    book: ForeignBook | None = None


@dataclass
class SeriesCard:
    series_id: int
    name: str
    author: str
    #: Volumes the person has, volumes that are out and they have not, volumes that are yet to come out.
    have: int = 0
    out: int = 0
    missing: int = 0
    soon: int = 0
    pending: bool = False
    volumes: list[Volume] = field(default_factory=list)


@dataclass
class AuthorCard:
    person_id: int
    name: str
    #: How many books of theirs the person has.
    on_shelf: int
    pending: bool = False
    books: list[ForeignBook] = field(default_factory=list)


@dataclass
class ForYou:
    #: ``ready``, ``main_off`` or ``no_source`` (nothing is kept and no source can be asked).
    state: str
    gaps: list[SeriesCard] = field(default_factory=list)
    authors: list[AuthorCard] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    pending: bool = False


def series_key(series_id: int) -> str:
    return cache.make_key("series", str(series_id))


def author_key(person_id: int) -> str:
    return cache.make_key("author", str(person_id))


# ---- What the person reads -------------------------------------------------------------------------------------


@dataclass
class MySeries:
    series_id: int
    name: str
    author: str
    #: The place in the series -> the work, for the volumes the person has (a volume with no place is not listed).
    places: dict[float, int]
    works: int


def my_series(db: Session, uid: int) -> list[MySeries]:
    """The series of the editions the person sees: their name, their first author and the volumes they have, the
    series with the most volumes first."""
    visible = visible_edition_clause(uid)
    rows = db.execute(select(Edition.series_id, Edition.work_id, Edition.series_pos, Edition.id).where(
        *visible, Edition.series_id.is_not(None)).order_by(Edition.kind, Edition.id)).all()
    if not rows:
        return []
    by_series: dict[int, dict[int, float | None]] = {}
    first_edition: dict[int, int] = {}
    for series_id, work_id, position, edition_id in rows:
        works = by_series.setdefault(series_id, {})
        if work_id not in works or (works[work_id] is None and position is not None):
            works[work_id] = position
        first_edition.setdefault(series_id, edition_id)
    names = dict(db.execute(select(Series.id, Series.name).where(Series.id.in_(list(by_series)))).all())
    writer: dict[int, str] = {}
    for edition_id, name in db.execute(
            select(Credit.edition_id, Person.name).join(Person, Person.id == Credit.person_id)
            .where(Credit.edition_id.in_(list(first_edition.values())), Credit.role == AUTHOR)
            .order_by(Credit.edition_id, Credit.position)):
        writer.setdefault(edition_id or 0, name)
    found = []
    for series_id, works in by_series.items():
        places = {position: work_id for work_id, position in works.items() if position is not None}
        found.append(MySeries(series_id, names.get(series_id, ""), writer.get(first_edition[series_id], ""), places,
                              len(works)))
    return sorted(found, key=lambda s: (-s.works, s.name))


def my_authors(db: Session, uid: int, limit: int = MAX_AUTHORS) -> list[tuple[int, str, int]]:
    """``(person, name, books on the shelf)`` of the authors with the most books among those the person sees."""
    count = func.count(distinct(Edition.work_id))
    rows = db.execute(
        select(Person.id, Person.name, count).join(Credit, Credit.person_id == Person.id)
        .join(Edition, Edition.id == Credit.edition_id).where(*visible_edition_clause(uid), Credit.role == AUTHOR)
        .group_by(Person.id).order_by(count.desc(), Person.name).limit(limit)).all()
    return [(person_id, name, int(number)) for person_id, name, number in rows]


# ---- What the sources said (kept) ------------------------------------------------------------------------------


def _kept(key: str) -> dict | None:
    value = cache.get(CACHE, key)
    return value if isinstance(value, dict) else None


def _books(db: Session, ids: list[int]) -> dict[int, ForeignBook]:
    return {row.id: row for row in db.scalars(select(ForeignBook).where(ForeignBook.id.in_(ids)))} if ids else {}


def _can_ask(db: Session) -> list[str]:
    return config.ready_sources(db, ("hardcover", "audible"))


def compute(db: Session, uid: int, today: str | None = None) -> ForYou:
    """The page "for you" for a person. Reads what is kept; queues a job for what is not. Commits when it queued
    some."""
    today = today or clock.now().date().isoformat()
    ready = _can_ask(db)
    out = ForYou(state="ready", sources=ready)
    mine = my_series(db, uid)[:MAX_SERIES]
    people = my_authors(db, uid)
    queued = False
    shelf = owned.load(db, uid)
    claimed: set[int] = set()
    for item in mine:
        kept = _kept(series_key(item.series_id))
        if kept is None:
            if ready and jobs_enqueue(db, "series", item.series_id):
                queued = True
            out.pending = out.pending or bool(ready)
            continue
        card = _series_card(db, item, kept, today)
        claimed.update(v.book.id for v in card.volumes if v.book is not None)
        if card.missing or card.soon:
            out.gaps.append(card)
    names = {fold(item.name) for item in mine}
    for person_id, name, number in people:
        kept = _kept(author_key(person_id))
        card = AuthorCard(person_id=person_id, name=name, on_shelf=number)
        if kept is None:
            if not ready:
                continue
            card.pending = True
            out.pending = True
            if jobs_enqueue(db, "authors", person_id):
                queued = True
            out.authors.append(card)
            continue
        books = _books(db, [int(i) for i in kept.get("fids") or [] if isinstance(i, int)])
        seen: set[str] = set()
        for fid in kept.get("fids") or []:
            row = books.get(fid)
            if row is None or row.id in claimed or fold(row.series) in names and row.series:
                continue
            key = foreign.group_key(row.title, [a for a in row.authors or [] if isinstance(a, str)])
            if key in seen:
                continue
            seen.add(key)
            if find_owned(shelf, row) is not None:
                continue
            card.books.append(row)
            if len(card.books) >= PER_AUTHOR:
                break
        if card.books:
            out.authors.append(card)
    if queued:
        db.commit()
    if not ready and not out.gaps and not out.authors:
        out.state = "no_source" if settings_service.get(db, "sources_enabled") else "main_off"
    return out


def find_owned(shelf: owned.Shelf, row: ForeignBook) -> int | None:
    data = row.data if isinstance(row.data, dict) else {}
    source, _, key = row.ref.partition(":")
    # A shelf of Open Library names a book once for each language its title is shown in (``OL1W~de``): the work is the
    # name the owned editions carry.
    key = key.partition("~")[0]
    return owned.find(shelf, title=row.title, authors=[a for a in row.authors or [] if isinstance(a, str)],
                      isbns=list(data.get("isbns") or []), asin=str(data.get("asin") or ""),
                      refs=[(source, key)], position=row.series_pos)


def jobs_enqueue(db: Session, kind: str, ref: int) -> bool:
    from ...models import Job

    if db.scalar(select(Job.id).where(Job.kind == kind, Job.ref == ref, Job.state.in_(("queued", "running")))):
        return False
    jobs.enqueue(db, kind, ref=ref)
    return True


def _series_card(db: Session, item: MySeries, kept: dict, today: str) -> SeriesCard:
    """The card of a series: the volumes the sources know set against the ones the person has."""
    card = SeriesCard(series_id=item.series_id, name=item.name, author=item.author)
    rows = _books(db, [int(v["fid"]) for v in kept.get("volumes") or [] if isinstance(v, dict) and
                       isinstance(v.get("fid"), int)])
    places: dict[float, Volume] = {}
    for place, work_id in item.places.items():
        places[place] = Volume(pos=place, state="own", work_id=work_id)
    for volume in kept.get("volumes") or []:
        if not isinstance(volume, dict) or not isinstance(volume.get("fid"), int) or not isinstance(
                volume.get("pos"), int | float):
            continue
        position = float(volume["pos"])
        row = rows.get(volume["fid"])
        if row is None or position in places:
            continue
        released = str(volume.get("released") or row.released or "")
        places[position] = Volume(pos=position, state="soon" if released and released > today else "missing", book=row)
    card.volumes = sorted(places.values(), key=lambda v: v.pos)
    card.have = sum(1 for v in card.volumes if v.state == "own")
    card.missing = sum(1 for v in card.volumes if v.state == "missing")
    card.soon = sum(1 for v in card.volumes if v.state == "soon")
    card.out = card.have + card.missing
    return card


def series_card(db: Session, uid: int, series_id: int, today: str | None = None) -> SeriesCard | None:
    """The card of one series for the page of a book of it. None when the person sees no volume of it (the series is
    not theirs). A series nothing is kept for comes back with ``pending`` and a job queued, if a source can be
    asked."""
    today = today or clock.now().date().isoformat()
    item = next((s for s in my_series(db, uid) if s.series_id == series_id), None)
    if item is None:
        return None
    kept = _kept(series_key(series_id))
    if kept is None:
        card = SeriesCard(series_id=series_id, name=item.name, author=item.author, pending=True)
        card.volumes = [Volume(pos=p, state="own", work_id=w) for p, w in sorted(item.places.items())]
        card.have = len(card.volumes)
        if _can_ask(db) and jobs_enqueue(db, "series", series_id):
            db.commit()
        card.pending = bool(_can_ask(db))
        return card
    return _series_card(db, item, kept, today)


# ---- The jobs --------------------------------------------------------------------------------------------------


def _keep(db: Session, key: str, candidates: list[Candidate], kind: str, extra: dict) -> list[ForeignBook]:
    rows = []
    volumes = []
    fids = []
    for candidate in candidates:
        row = foreign.upsert(db, candidate, kind)
        rows.append(row)
        fids.append(row.id)
        if candidate.series_pos is not None:
            volumes.append({"fid": row.id, "pos": candidate.series_pos, "released": candidate.released})
    # The cache writes in a session of its own: what this one holds is committed first, or the two wait for each
    # other.
    db.commit()
    cache.put(CACHE, key, {**extra, "volumes": volumes, "fids": fids}, hit=bool(rows), days=CACHE_DAYS)
    foreign.enqueue_covers(db, rows)
    return rows


def series_job(db: Session, job: object) -> None:
    """The handler of the job ``series``: asks a source for the volumes of the series ``job.ref`` and keeps the
    answer."""
    series_id = int(getattr(job, "ref", 0))
    row = db.get(Series, series_id)
    if row is None:
        raise jobs.GiveUp("series gone")
    editions = db.scalars(select(Edition).where(Edition.series_id == series_id).order_by(Edition.id).limit(50)).all()
    author = db.scalar(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
        Credit.edition_id.in_([e.id for e in editions]), Credit.role == AUTHOR).order_by(Credit.position).limit(1)) \
        if editions else None
    try:
        found: list[Candidate] = []
        source = ""
        if "hardcover" in _can_ask(db):
            book = db.scalar(select(ExternalRef.key).where(
                ExternalRef.edition_id.in_([e.id for e in editions]), ExternalRef.source == "hardcover",
                ExternalRef.kind == "work").limit(1))
            if book and book.isdigit():
                named = hardcover.series_of(db, int(book))
                if named is not None:
                    found, source = hardcover.series_books(db, named[0]), "hardcover"
        if not found and "audible" in _can_ask(db) and editions:
            language = editions[0].language
            market = config.markets_for(db, language)[0]
            found, source = audible.series_volumes(db, market, row.name, author or ""), "audible"
    except http.SourceOff:
        db.rollback()
        return
    except http.SourceError as exc:
        db.rollback()
        if exc.code in ("refused", "bad_answer"):
            raise jobs.GiveUp(exc.code) from exc
        raise jobs.Retry(exc.code) from exc
    if jobs.stopping.is_set():
        raise jobs.Interrupted
    _keep(db, series_key(series_id), found, "audio" if source == "audible" else "ebook", {"source": source})
    db.commit()


def authors_job(db: Session, job: object) -> None:
    """The handler of the job ``authors``: asks a source for the books of the author ``job.ref`` and keeps the
    answer."""
    person_id = int(getattr(job, "ref", 0))
    person = db.get(Person, person_id)
    if person is None:
        raise jobs.GiveUp("person gone")
    try:
        found: list[Candidate] = []
        source = ""
        if "hardcover" in _can_ask(db):
            found, source = hardcover.author_books(db, person.name), "hardcover"
        elif "audible" in _can_ask(db):
            language = db.scalar(select(Edition.language).join(Credit, Credit.edition_id == Edition.id).where(
                Credit.person_id == person_id, Credit.role == AUTHOR).limit(1)) or ""
            found, source = audible.by_author(db, config.markets_for(db, language)[0], person.name), "audible"
    except http.SourceOff:
        db.rollback()
        return
    except http.SourceError as exc:
        db.rollback()
        if exc.code in ("refused", "bad_answer"):
            raise jobs.GiveUp(exc.code) from exc
        raise jobs.Retry(exc.code) from exc
    if jobs.stopping.is_set():
        raise jobs.Interrupted
    found = [c for c in found if c.authors][:40]
    _keep(db, author_key(person_id), found, "audio" if source == "audible" else "ebook", {"source": source})
    db.commit()


jobs.register("series", series_job)
jobs.register("authors", authors_job)

