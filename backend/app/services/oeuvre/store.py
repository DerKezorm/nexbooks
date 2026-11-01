"""The catalogue of an author in the database: the author (``CatalogPerson``), found or made for a name, and the
job that builds their works (``catalog``) in the background.

**Never while somebody waits.** A page that is read asks for a build (``request``) when the catalogue is older than a
day (or was never built) and some source may be asked; the job runs in a lane of its own (it waits for the sources, a
request a second, and must not hold up the reading of a library). What it built replaces the works of the author in
one transaction; what a person moved stays moved (``CatalogMove``, by the key of the work).

Every work gets a book of the sources (``ForeignBook``): the wish, the wish list and the page of a book go through it,
as they do for every other book that is not on a shelf. Those rows are public data like the catalogue; whether a
person has a work is worked out when they ask (``view.py``).
"""

from __future__ import annotations

import logging
import secrets
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ... import clock
from ...models import (
    AUTHOR,
    Account,
    AuthorWatch,
    CatalogPerson,
    CatalogWork,
    Credit,
    Edition,
    Job,
    Person,
    PersonAlias,
    WatchItem,
)
from .. import aliases as aliases_service
from .. import jobs
from ..discover import foreign
from ..readers.names import display_name
from ..readers.text import clean
from ..sources import config
from ..sources.model import Candidate, Entry
from . import compute
from . import gather as gather_service
from . import info as info_service
from . import works as works_service

logger = logging.getLogger("nexbooks.oeuvre")

KIND = "catalog"
#: A catalogue is built again when somebody looks at it and it is older than this.
REBUILD_AFTER = timedelta(days=1)
#: A build while a source was away is tried again after this (when somebody looks), and what it built replaces the
#: catalogue only when it has at least this share of the works before (a source that is away for an hour must not
#: thin out a full catalogue for a day).
RETRY_AFTER = timedelta(hours=1)
KEEP_SHARE = 0.7
#: The most works one author's catalogue keeps (a prolific author with every edition is a few hundred).
MAX_WORKS = 500
#: The sources the catalogue asks.
SOURCES = ("openlibrary", "hardcover", "audible", "dnb", "bnf", "libris", "kb", "nb")


def new_token() -> str:
    return secrets.token_urlsafe(14)[:18]


# ---- Finding and making the author -------------------------------------------------------------------------------


def _make(db: Session, name: str, key: str, person_id: int | None) -> CatalogPerson:
    db.execute(sqlite_insert(CatalogPerson).values(
        token=new_token(), name=name[:200], key=key, person_id=person_id, aliases=[], languages=[], sources={},
        created_at=clock.now()).on_conflict_do_nothing(index_elements=["key"]))
    row = db.scalar(select(CatalogPerson).where(CatalogPerson.key == key))
    assert row is not None
    if person_id is not None and row.person_id is None:
        row.person_id = person_id
    return row


def for_person(db: Session, person: Person) -> CatalogPerson:
    """The catalogue of an author of the libraries (made when there is none). Does not commit."""
    return _make(db, person.name, person.key, person.id)


def for_name(db: Session, name: str, library_ids: Any = None, *, make: bool = True) -> CatalogPerson | None:
    """The catalogue of an author by a name a source gave (made when there is none, unless ``make`` is off): the
    author of the libraries the name leads to (their own name, or a pen name proven by the books, of the libraries
    ``library_ids`` when given) when there is one. Does not commit."""
    text = display_name(clean(name, 200))
    key = aliases_service.key_of(text)
    if not text or not key or len(key) < 2:
        return None
    person = aliases_service.person_for(db, text, library_ids)
    if person is not None:
        if not make:
            return db.scalar(select(CatalogPerson).where(CatalogPerson.key == person.key))
        return for_person(db, person)
    known = db.scalar(select(CatalogPerson).where(CatalogPerson.key == key))
    if known is not None:
        return known
    # A pen name the sources know an author by leads to that author.
    named = db.execute(sql_text(
        "SELECT DISTINCT catalog_people.id FROM catalog_people, json_each(catalog_people.aliases) "
        "WHERE json_valid(catalog_people.aliases) AND lower(json_each.value) = lower(:name) LIMIT 20"),
        {"name": text}).scalars().all()
    for row in db.scalars(select(CatalogPerson).where(CatalogPerson.id.in_(named))):
        if any(aliases_service.key_of(str(alias)) == key for alias in row.aliases or []):
            return row
    return _make(db, text, key, None) if make else None


def by_token(db: Session, token: str) -> CatalogPerson | None:
    if not token or len(token) > 24:
        return None
    return db.scalar(select(CatalogPerson).where(CatalogPerson.token == token))


# ---- Asking for a build ------------------------------------------------------------------------------------------


def _retry_at(row: CatalogPerson) -> datetime | None:
    sources = row.sources if isinstance(row.sources, dict) else {}
    value = sources.get("retry_after")
    try:
        found = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return found if found is None or found.tzinfo is not None else found.replace(tzinfo=UTC)


def due(row: CatalogPerson) -> bool:
    """Whether the catalogue is to be built (again): never built, older than a day, or built while a source was away
    and the hour is over."""
    if row.built_at is None:
        return True
    retry = _retry_at(row)
    if retry is not None:
        return clock.now() >= retry
    return clock.now() - row.built_at >= REBUILD_AFTER


def can_ask(db: Session) -> bool:
    return bool(config.ready_sources(db, SOURCES))


def building(db: Session, row: CatalogPerson) -> bool:
    return db.scalar(select(Job.id).where(Job.kind == KIND, Job.ref == row.id,
                                          Job.state.in_(("queued", "running")))) is not None


def request(db: Session, row: CatalogPerson) -> bool:
    """Notes that somebody looked at the catalogue and queues a build when it is due and a source may be asked.
    Returns whether a build is waiting or running. Commits."""
    row.wanted_at = clock.now()
    queued = False
    if due(row) and can_ask(db):
        jobs.enqueue(db, KIND, ref=row.id)
        queued = True
    db.commit()
    return queued or building(db, row)


# ---- Building ----------------------------------------------------------------------------------------------------


def languages_for(db: Session, row: CatalogPerson) -> list[str]:
    """The languages the catalogue is built for: first the languages books are fetched in (settings, "Sprachen", in
    their order, "Originalsprache" as the author's own): what an author's page offers to fetch and what watching an
    author wishes is in them, whatever language the people's interface is in, so they are never cut off by the
    limit. Then the languages of the people of this server (their interface and their Discover), most people first,
    and the author's own."""
    from ..procure import formats

    found = list(formats.allowed_languages(db, row.orig_language or ""))
    counts: Counter[str] = Counter()
    for language, profile in db.execute(select(Account.language, Account.profile)):
        code = (language or "").lower()[:2]
        if isinstance(profile, dict) and isinstance(profile.get("language"), str):
            code = profile["language"].lower()[:2] or code
        if len(code) == 2 and code.isalpha():
            counts[code] += 1
    found.extend(code for code, _n in counts.most_common())
    if row.orig_language:
        found.append(row.orig_language)
    return list(dict.fromkeys(found or ["en"]))[: gather_service.MAX_LANGUAGES]


def _people_of(db: Session, row: CatalogPerson) -> list[int]:
    ids = {row.person_id} if row.person_id else set()
    ids.update(db.scalars(select(Person.id).where(Person.key == row.key)))
    return [ident for ident in ids if ident]


@dataclass
class Hints:
    """What the books of the libraries say of the author (public data, the same for everybody)."""

    #: The original languages their books name, and the languages the books are in (a weaker hint: a translation is
    #: in another language).
    originals: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    #: The pen names the books proved, and the titles of the books (which of several people of the name is the one).
    pens: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)


def hints_for(db: Session, row: CatalogPerson) -> Hints:
    """What the books of the libraries say of the author (see ``Hints``)."""
    people = _people_of(db, row)
    if not people:
        return Hints()
    rows = db.execute(
        select(Edition.original_language, Edition.language, Edition.title, Edition.original_title)
        .join(Credit, Credit.edition_id == Edition.id)
        .where(Credit.person_id.in_(people), Credit.role == AUTHOR).limit(200)).all()
    pens = list(db.scalars(select(PersonAlias.name).where(PersonAlias.person_id.in_(people)).limit(5)))
    titles = list(dict.fromkeys(title for row_ in rows for title in (row_[2], row_[3]) if title))
    return Hints(originals=[row_[0] for row_ in rows if row_[0]], languages=[row_[1] for row_ in rows if row_[1]],
                 pens=pens, titles=titles[:100])


def _forms(work: works_service.Work) -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = []
    for language, edition in work.titles.items():
        mine = [entry for entry in work.entries if (entry.language or work.orig_language) == language]
        if edition.ebook:
            pages = [entry.pages for entry in mine if entry.pages and entry.form == "text"]
            forms.append({"kind": "ebook", "variant": "", "language": language, "minutes": None, "narrators": [],
                          "pages": int(statistics.median(pages)) if pages else None,
                          "source": next((e.source for e in mine if e.form == "text"), work.sources[0])})
        if edition.audio:
            audio = [entry for entry in mine if entry.form == "audio"]
            variants = Counter(entry.variant or "unabridged" for entry in audio)
            minutes = [entry.runtime_min for entry in audio if entry.runtime_min and entry.variant != "abridged"]
            forms.append({"kind": "audio", "variant": variants.most_common(1)[0][0] if variants else "unabridged",
                          "language": language, "minutes": max(minutes) if minutes else None, "narrators": [],
                          "pages": None, "source": audio[0].source if audio else work.sources[0]})
    return forms[: foreign.MAX_FORMS]


def foreign_row(db: Session, author: str, work: works_service.Work) -> Any:
    """The book of the sources for a work: the record that speaks for it, with every title and form the work has."""
    speaker: Entry = work.speaker
    orig = work.titles.get(work.orig_language)
    cover = next((entry.cover_url for entry in sorted(work.entries, key=lambda e: works_service.SPEAKER_ORDER.get(
        e.source, 9)) if entry.cover_url), "")
    subjects: list[str] = []
    for entry in work.entries:
        for name in ("genres", "keywords", "subjects"):
            value = entry.signals.get(name)
            if isinstance(value, list):
                subjects += [item for item in value if isinstance(item, str)]
    candidate = Candidate(
        source=speaker.source, key=(speaker.work_key or speaker.key)[:70], title=work.title_in(work.orig_language),
        authors=[author], languages=[work.orig_language] if work.orig_language else [],
        isbns=[isbn for isbn in work.isbns if isbn.isdigit()][:20], year=work.year,
        released=orig.released if orig else "", series=work.series, series_pos=work.series_pos,
        subjects=list(dict.fromkeys(subjects))[:10], pages=work.pages, cover_url=cover)
    row = foreign.upsert(db, candidate, "ebook")
    data = dict(row.data) if isinstance(row.data, dict) else {}
    data["titles"] = [{"text": edition.title, "language": language} for language, edition in work.titles.items()][:12]
    data["forms"] = foreign.merge_forms([], _forms(work))
    row.data = data
    return row


def store(db: Session, row: CatalogPerson, built: list[works_service.Work], gathered: gather_service.Gathered) -> None:
    """Replaces the works of the author with the ones built, in the caller's transaction."""
    db.execute(delete(CatalogWork).where(CatalogWork.catalog_person_id == row.id))
    db.flush()
    for work in built[:MAX_WORKS]:
        book = foreign_row(db, row.name, work)
        db.add(CatalogWork(
            catalog_person_id=row.id, key=work.key, foreign_id=book.id, kind=work.kind, why=work.why,
            pen=work.pen[:200], series=work.series[:200], series_pos=work.series_pos, year=work.year,
            orig_language=work.orig_language[:8],
            titles={language: edition.to_json() for language, edition in work.titles.items()},
            sources=work.sources, isbns=work.isbns, refs=work.refs, pages=work.pages, minutes=work.minutes,
            weight=work.weight))
    per_source: dict[str, dict[str, Any]] = {}
    for work in built:
        for source in work.sources:
            per_source.setdefault(source, {"works": 0})["works"] += 1
    for source, count in gathered.counts.items():
        per_source.setdefault(source, {"works": 0})["entries"] = count
    row.sources = {"by": per_source, "away": sorted(gathered.away),
                   "answered": {language: sorted(names) for language, names in gathered.answered.items()},
                   "namesakes": gathered.namesakes}
    if gathered.away:
        row.sources["retry_after"] = (clock.now() + RETRY_AFTER).isoformat()
    row.aliases = gathered.pens
    row.orig_language = gathered.orig_language[:8]
    row.languages = gathered.languages
    row.state = "ready"
    row.built_at = clock.now()


def build(db: Session, row: CatalogPerson) -> bool:
    """Asks the sources and builds the works of the author. Returns whether anything was built (False: no source
    answered; what was built before stays). Commits."""
    languages = languages_for(db, row)
    hints = hints_for(db, row)
    name = row.name
    gathered = gather_service.gather(db, name, languages=languages, aliases=hints.pens, hints=hints.originals,
                                     weak=hints.languages, own=hints.titles)
    if not gathered.entries and gathered.away:
        row.state = row.state if row.built_at else "failed"
        db.commit()
        return False
    # A record in a language the catalogue is not built for (Audible's market lists recordings in many languages, the
    # German library translations into many) is left out: it would only stand as a work of its own.
    wanted = set(gathered.languages)
    entries = [entry for entry in gathered.entries if not entry.language or entry.language in wanted]
    answered = {language: names for language, names in gathered.answered.items() if language in wanted}
    # (Many records are put together in a helper process: the server's requests do not wait for it.)
    try:
        built = compute.build(entries, answered=answered, author_language=gathered.orig_language,
                              pen_names=set(gathered.pens), author=row.name, namesakes=gathered.namesakes,
                              stop=jobs.stopping.is_set)
    except compute.Failed as exc:
        db.rollback()
        if jobs.stopping.is_set():
            raise jobs.Interrupted from exc
        raise jobs.Retry(f"compute {exc}") from exc
    before = db.scalar(select(func.count(CatalogWork.id)).where(CatalogWork.catalog_person_id == row.id)) or 0
    if gathered.away and row.built_at is not None and len(built) < KEEP_SHARE * before:
        # A source was away and the catalogue came out much thinner: the one before stays, and it is tried again in
        # an hour.
        row.sources = {**(row.sources if isinstance(row.sources, dict) else {}),
                       "retry_after": (clock.now() + RETRY_AFTER).isoformat()}
        db.commit()
        logger.info("A thinner catalogue of an author was not kept while a source was away works=%s before=%s",
                    len(built), before)
        return True
    store(db, row, built, gathered)
    info_service.enqueue(db, row)
    db.commit()
    logger.info("The works of an author were built works=%s entries=%s", len(built), len(gathered.entries))
    return True


def build_job(db: Session, job: Any) -> None:
    """The handler of the job ``catalog``: builds the catalogue ``job.ref`` when it is still due."""
    row = db.get(CatalogPerson, int(getattr(job, "ref", 0)))
    if row is None:
        raise jobs.GiveUp("catalog gone")
    if not due(row) or not can_ask(db):
        return
    if not build(db, row):
        raise jobs.Retry("sources away")


jobs.register(KIND, build_job)


#: An author nobody of the libraries is and nobody looked at for this long is forgotten (a search names many).
FORGET_AFTER = timedelta(days=90)


def prune(db: Session) -> int:
    """Forgets the authors of the catalogue that are no author of the libraries and that nobody looked at for
    ``FORGET_AFTER`` (an author a search named and nobody opened, after a month). Their works go with them; the books
    of the sources behind them go when nothing else names them (``discover.foreign.prune``). Returns how many went."""
    now = clock.now()
    # (An author somebody watches is not forgotten, nor one a watch has made wishes for: the wish keeps saying where it
    # came from.)
    stale = select(CatalogPerson.id).where(
        CatalogPerson.person_id.is_(None),
        CatalogPerson.id.not_in(select(AuthorWatch.catalog_person_id)),
        CatalogPerson.id.not_in(select(WatchItem.catalog_person_id)),
        or_(CatalogPerson.wanted_at < now - FORGET_AFTER,
            and_(CatalogPerson.wanted_at.is_(None), CatalogPerson.created_at < now - timedelta(days=30))))
    ids = list(db.scalars(stale))
    if ids:
        db.execute(delete(CatalogWork).where(CatalogWork.catalog_person_id.in_(ids)))
        db.execute(delete(CatalogPerson).where(CatalogPerson.id.in_(ids)))
    db.commit()
    return len(ids)

__all__ = ["KIND", "REBUILD_AFTER", "build", "by_token", "can_ask", "due", "for_name", "for_person", "request"]
