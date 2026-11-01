"""The matching, from the scan to the answer: which editions have to be looked at, asking the sources, deciding, and
putting
the answer into effect.

**In the queue, after the scan, only for what is new or changed.** An edition gets its entry (``MatchEntry``) when the
scan
makes it, with the fingerprint of what its files say; another fingerprint (a changed file) makes it pending again,
unless a
person decided something about it. A job of kind ``match`` (one per library) works through the pending entries of a
library;
it is started by the scan and by the clock, and only while a source is ready. What is not decided yet (the sources are
off,
resting, or did not answer) stays pending: nothing is marked "open" because a source was away. That holds for a source
the book was to be asked at, by its language (``plan_for``), that failed, was at its daily limit or was switched off
meanwhile, while the others found nothing or found it only by halves: the book waits until that source answered (a
book that is sure needs no more). A source that rests holds the books that wait for it back, without asking, until it is
back.

**Asking costs requests**, so: the searches of Hardcover go in bundles, an ISBN that settles the book stops the
asking, a
source that is resting is not asked again and the run ends when none is left, and every answer is kept
(``sources/cache``).

**Nothing is decided in the dark.** The proposals of a book that is not sure are kept with their points and reasons
(``score.py``), for the list a person looks at.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ... import clock
from ...models import AUTHOR, ROLE_BOOK, BookFile, Credit, Edition, Job, Library, MatchEntry, Person, PersonAlias
from .. import genres, jobs, settings_service
from ..readers.names import (
    FolderBook,
    author_names,
    authors_compatible,
    folder_book,
    is_shelf_name,
    strip_part_folders,
    titles_alike,
    top_folder_author,
    year_in_path,
)
from ..sources import audible, config, dnb, hardcover, http, openlibrary
from ..sources.model import Candidate, Query
from . import apply, overlay
from .score import Decision, Scored, Wanted, decide, score, volume_in

logger = logging.getLogger("nexbooks.match")

#: The reason of an entry that was sure and is looked at again only because the rules are newer: it waits behind every
#: other entry (the unsure ones, the books that are new) and is the last to be asked about.
RECHECK = "recheck"
#: Entries asked in one go when Hardcover's searches are bundled.
BATCH = 5
#: How many proposals are kept for the list.
KEEP = 6
#: Entries the clock queues for one library at a time never exceeds what one job does; the job goes on until none is
# left.
MAX_PER_RUN = 5000

#: The version of the rules books are matched by. Raised when the rules change in a way that the answers given before
#: should be looked at again (the sources asked for a language, the points, what a title is): the next time the clock
#: looks, every book no person decided on is matched anew, once (``upgrade_rules``).
MATCH_REV = 5


def fingerprint(meta: Any) -> str:
    """What the files say about an edition, as far as matching cares, as a short hash: kind, title, author, language,
    ISBN, ASIN, series and volume, and the running time in half minutes. Taken before anything a source gave is
    laid over it (``in_place``)."""
    parts = [meta.kind, meta.title, meta.subtitle, "\x1e".join(meta.authors), meta.language, meta.isbn, meta.asin,
             str(int(meta.duration or 0) // 30), meta.series, "" if meta.series_pos is None else f"{meta.series_pos:g}"]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:32]


def in_place(files_say: str, parts: int, work_id: int) -> str:
    """The fingerprint of an edition: what the files say, how many files it is made of and which work it stands in.
    Another one means the answer given before may be wrong (a recording put together from its discs has a new running
    time and a new number of parts), so the book is matched again."""
    text = f"{files_say}\x1f{int(parts)}\x1f{int(work_id)}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def note(db: Session, edition: Edition, fp: str) -> None:
    """The scan made or changed an edition: its entry exists, and is pending again when the files changed (a person's
    decision is not undone by it)."""
    entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == edition.id))
    now = clock.now()
    if entry is None:
        db.add(MatchEntry(library_id=edition.library_id, edition_id=edition.id, state="pending", fingerprint=fp,
                          created_at=now, updated_at=now))
    elif not entry.fingerprint:
        # An entry made by the catch-up has not seen the files yet: it adopts what they say, nothing changed.
        entry.fingerprint = fp
    elif entry.fingerprint != fp:
        entry.fingerprint = fp
        WAITING.pop(entry.id, None)
        if not entry.decision:
            entry.state, entry.reason, entry.proposals, entry.updated_at = "pending", "", None, now


def catch_up(db: Session) -> int:
    """Entries for editions that have none (the libraries of a version before the sources). Returns how many."""
    now = clock.now()
    rows = db.execute(select(Edition.id, Edition.library_id).where(
        Edition.missing_at.is_(None), Edition.id.not_in(select(MatchEntry.edition_id)))).all()
    for edition_id, library_id in rows:
        db.add(MatchEntry(library_id=library_id, edition_id=edition_id, state="pending", created_at=now,
                          updated_at=now))
    if rows:
        db.commit()
    return len(rows)


@dataclass
class PathHints:
    """What the names of the folders and the file say of a book: a volume ("Band 2"), the year in brackets, the author
    at the top of the library, the title the folder of the book carries."""

    volume: float | None = None
    year: int | None = None
    author: str = ""
    title: str = ""


def path_hints(kind: str, path: str, series_pos: float | None, top: str | None = None) -> PathHints:
    """The hints of a path. They were written by the person who sorted the books and tell volume 1 from volume 2 where
    nothing else does. **The number a file of a recording begins or ends with is its part, never a volume**
    (``001 - Titel.mp3``): only a marked one (``Band 3``) counts there, and the name of the folder of the book."""
    if not path:
        return PathHints()
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # The folder of the book: not one that only holds its parts (``Audio``, ``CD 2``).
    book_path = strip_part_folders(path.rsplit("/", 1)[0] if "/" in path else "")
    folders = [part for part in book_path.split("/") if part]
    name = folders[-1] if folders else ""
    folder = folder_book(name, depth=len(folders), parent=folders[-2] if len(folders) >= 2 else "") if (
        name and not is_shelf_name(name)) else FolderBook()
    volume = None
    if series_pos is None:
        if kind == "audio":
            volume = volume_in(stem, marked_only=True)
            if volume is None:
                volume = folder.pos if folder.pos is not None else volume_in(name)
        else:
            volume = volume_in(stem) if volume_in(stem) is not None else volume_in(name)
    author = top_folder_author(path) if top is None else top
    return PathHints(volume=volume, year=year_in_path(path), author=author, title=folder.title)


def wanted_of(db: Session, edition: Edition) -> Wanted:
    credited = db.execute(select(Person.id, Person.name).join(Credit, Credit.person_id == Person.id).where(
        Credit.edition_id == edition.id, Credit.role == AUTHOR).order_by(Credit.position)).all()
    authors = [name for _ident, name in credited]
    # A name of the files that leads to another person (a pen name) is asked for as the files say it: the sources are
    # asked the same question as before the names were joined, and give the same evidence again.
    led = {row.key: row.person_id for row in db.scalars(select(PersonAlias).where(
        PersonAlias.person_id.in_([ident for ident, _name in credited])))} if credited else {}
    if led:
        from .. import aliases

        named = aliases.named_authors(db, edition)
        through = {person for key, person in led.items() if key in named}
        if through:
            authors = [name for key, name in named.items() if key in led] + [
                name for ident, name in credited if ident not in through]
    published = edition.published[:4]
    path = db.scalar(select(BookFile.path).where(
        BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))
        .order_by(BookFile.part, BookFile.path).limit(1)) or ""
    from .. import catalog

    top = catalog.top_author(db, edition.library_id, path) if path else ""
    return wanted_from(edition, authors, path, published, top=top)


def file_value(edition: Any, name: str) -> str:
    """The ISBN or the ASIN of an edition as its files say it: one a source gave (kept in the overlay) is never the
    file's. A match that wrote a source's ISBN into the edition must not take it for proof the next time."""
    value = str(getattr(edition, name, "") or "")
    given = overlay.source_entry(getattr(edition, "overlay", None), name)
    if given is not None and str(given.get("v") or "") == value:
        return ""
    return value


def wanted_from(edition: Any, authors: list[str], path: str, published: str = "", top: str | None = None) -> Wanted:
    """The book as it is asked for: the edition's own values, and what its path adds. The author at the top of the
    library counts as a name of the book's author beside the tags (a tag that names a series or a narrator does not
    bar the match); the title of the folder is a second title the book may be known by. The ISBN and the ASIN are the
    files' own (``file_value``); a language that was only worked out is asked for as such (``language_guess``)."""
    hints = path_hints(edition.kind, path, edition.series_pos, top)
    names = list(authors)
    known = [piece for name in names for piece in author_names(name)]
    if hints.author and not any(authors_compatible(hints.author, piece) for piece in known):
        names.append(hints.author)
    alt = [hints.title] if hints.title and not titles_alike(hints.title, edition.title) else []
    if alt and len(names) >= 2:
        # A file whose fields are swapped (the title in the field of the author, ``Wilde Lupinen`` by ``Wilde
        # Lupinen``): a name that is the title of the folder of the book names no author, while another name is left.
        names = [name for name in names if not titles_alike(name, alt[0])] or names
    return Wanted(kind=edition.kind, title=edition.title, subtitle=edition.subtitle, authors=names,
                  language=edition.language, isbn=file_value(edition, "isbn"), asin=file_value(edition, "asin"),
                  series=edition.series_name, series_pos=edition.series_pos, duration=edition.duration,
                  year=int(published) if published.isdigit() else None, original_title=edition.original_title,
                  name_year=hints.year, name_volume=hints.volume, alt_titles=alt,
                  language_guess=overlay.is_guess(getattr(edition, "overlay", None), "language"))


def query_of(wanted: Wanted) -> Query:
    # A book that is asked for by the title of its folder too costs no question by the fields on top (``fallback``).
    return Query(kind=wanted.kind, title=wanted.title, authors=wanted.authors, language=wanted.language,
                 isbn=wanted.isbn, asin=wanted.asin, fallback=not wanted.alt_titles)


@dataclass
class Lookup:
    candidates: list[Candidate] = field(default_factory=list)
    #: The sources that answered (also with nothing), and those that were asked and did not (source -> code).
    answered: list[str] = field(default_factory=list)
    failed: dict[str, http.SourceError] = field(default_factory=dict)
    #: The sources that were switched off between choosing them and asking them.
    off: set[str] = field(default_factory=set)

    @property
    def asked(self) -> int:
        return len(self.answered) + len(self.failed)


#: The sources to ask for a book by the language of the file, as ``(always, if not sure)``: each language is told by
#: the catalogue that knows it best, first. German books: the German National Library and Audible, Open Library only as
#: an addition (it knows few German books, mostly as translations); English: Open Library, Audible and Hardcover; any
#: other language: Audible, Open Library and Hardcover. A book of an unknown language is asked as any other and, if
#: that does not settle it, at the German National Library too. Hardcover is asked when the operator connected it. The
#: table is the one place to teach nexbooks the catalogue of another language.
PLANS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "de": (("dnb", "audible"), ("openlibrary", "hardcover")),
    "en": (("openlibrary", "audible", "hardcover"), ()),
}
OTHER_LANGUAGE = (("audible", "openlibrary", "hardcover"), ())
UNKNOWN_LANGUAGE = (("audible", "openlibrary", "hardcover"), ("dnb",))


def plan_for(wanted: Wanted) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(always, if not sure)``: the sources worth asking about this book, the likeliest first. A recording is asked at
    Audible first, whatever the language (its running time is the proof)."""
    always, extra = PLANS.get(wanted.language, OTHER_LANGUAGE) if wanted.language else UNKNOWN_LANGUAGE
    if wanted.kind == "audio" and "audible" in always:
        always = ("audible", *[name for name in always if name != "audible"])
    return always, extra


def sources_for(db: Session, wanted: Wanted, *, extra: bool = False) -> list[str]:
    """The ready sources worth asking about this book, the likeliest first; ``extra`` adds the ones asked only when the
    book is not settled yet."""
    ready = set(config.ready_sources(db, config.CATALOGUES))
    always, more = plan_for(wanted)
    return [source for source in (*always, *(more if extra else ())) if source in ready]


def ask(db: Session, source: str, wanted: Wanted, *, interactive: bool,
        override: Query | None = None) -> list[Candidate]:
    query = override or query_of(wanted)
    if source == "audible":
        found: list[Candidate] = []
        for market in config.markets_for(db, wanted.language):
            found += audible.search(db, query, market, interactive=interactive)
        return found
    if source == "hardcover":
        return hardcover.search(db, Query(kind=query.kind, title=query.title, authors=query.authors,
                                          language=query.language), interactive=interactive)
    if source == "openlibrary":
        return openlibrary.search(db, query, interactive=interactive)
    return dnb.search(db, query, interactive=interactive)


def lookup(db: Session, wanted: Wanted, *, interactive: bool = False, override: Query | None = None,
           stop_when_certain: bool = True) -> Lookup:
    """Asks the sources for a book, one after the other. A source that fails is noted and the rest are asked; asking
    stops
    early when an ISBN or an ASIN has settled the book."""
    result = Lookup()
    asked: set[str] = set()
    for stage in (False, True):
        for source in sources_for(db, wanted, extra=stage):
            if source in asked:
                continue
            asked.add(source)
            try:
                found = ask(db, source, wanted, interactive=interactive, override=override)
            except http.SourceOff:
                result.off.add(source)
                continue
            except http.SourceError as exc:
                result.failed[source] = exc
                continue
            result.answered.append(source)
            result.candidates += found
            if stop_when_certain and override is None and any(score(wanted, c).certain for c in found):
                return result
        # The sources asked only "if not sure" are asked when the book is not settled yet, or nobody said what kind of
        # book it is; a search made by hand asks them all.
        if not stage and stop_when_certain and override is None and _settled(wanted, result):
            return result
    return result


def _settled(wanted: Wanted, result: Lookup) -> bool:
    """Whether the sources asked so far settle the book and say what kind of book it is: no need to ask more."""
    if not result.answered or not result.candidates:
        return False
    decision = decide(wanted, result.candidates)
    seen = genres.winner(genres.add_votes(*[genres.tally(c.subjects, 1) for c in result.candidates]))
    return decision.state == "sure" and bool(seen)


def proposals_of(decision: Decision) -> list[dict[str, Any]]:
    """What is kept of the candidates for the list: the best ones with their points and reasons. A book none of the
    candidates could be gets the nearest few, with what is wrong with each."""
    shown = [s for s in decision.ranked if not s.fatal][:KEEP] or decision.ranked[:3]
    return [proposal_of(s) for s in shown]


def proposal_of(scored: Scored, **extra: Any) -> dict[str, Any]:
    c = scored.candidate
    return {"id": f"{c.source}:{c.key}:{c.market}", "score": scored.score,
            "why": [*scored.why, *([scored.fatal] if scored.fatal else [])], "fatal": scored.fatal,
            "missing": scored.missing, "against": scored.against, "detail": scored.detail,
            "proof": scored.proof, "cover": "", "c": c.to_json(), **extra}


def complete(db: Session, scored: Scored, *, interactive: bool) -> None:
    """Fetches what a chosen candidate still lacks: the description of an Open Library work."""
    c = scored.candidate
    if c.source == "openlibrary" and not c.description and config.availability(db, "openlibrary").ready:
        try:
            c.description = openlibrary.work_details(db, c.work_key, interactive=interactive).get("description", "")
        except http.SourceError:
            pass


#: The formats nexbooks opens in the reader: a book is protected when it has some of them and none can be opened.
READABLE = ("epub", "azw3", "mobi", "pdf")


def is_protected(db: Session, edition_id: int) -> bool:
    """Whether every file of the edition that nexbooks could open is copy-protected (the reader says so and does not
    open it; the files can still be downloaded). A book with one open format beside a protected one is not."""
    probes = db.execute(select(BookFile.probe).where(
        BookFile.edition_id == edition_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
        BookFile.format.in_(READABLE))).scalars().all()
    return bool(probes) and all(isinstance(probe, dict) and probe.get("drm") for probe in probes)


def settle(db: Session, entry: MatchEntry, edition: Edition, decision: Decision, *, interactive: bool = False,
           away: frozenset[str] = frozenset(), fingerprint: str | None = None) -> bool:
    """Writes an answer: the entry, and for a sure one the edition. What the sources gave before and is no longer sure
    is taken back, unless a source that gave some of it could not be asked this time (``away``): then nothing is taken
    back on the word of the others alone, and a person looks at what they say.

    **A person's decision made meanwhile is never overwritten.** The sources were asked with no write held, so the entry
    can have been decided (or taken back, or left as it is) since. The entry is written only if it is still waiting and
    undecided, in the same step as that check (the ``WHERE`` of the ``UPDATE``); when it is not, nothing is written and
    ``False`` comes back. **Nor is an answer written for a book that changed meanwhile**: ``fingerprint`` is the one the
    entry had when the sources were asked, and another one now (the discs of a recording were put together while
    its first disc was being asked about) leaves the entry pending, to be matched as the book it is now."""
    now = clock.now()
    cover = None
    if decision.state == "sure" and decision.best is not None:
        complete(db, decision.best, interactive=interactive)
        for other in decision.support:
            complete(db, other, interactive=interactive)
        if not edition.cover:
            cover = apply.fetch_cover(db, decision.best.candidate, interactive=interactive)
    guard = [MatchEntry.id == entry.id, MatchEntry.decision == "", MatchEntry.state == "pending"]
    if fingerprint is not None:
        guard.append(MatchEntry.fingerprint == fingerprint)
    state, reason = decision.state, decision.reason
    if is_protected(db, edition.id):
        # A book nexbooks cannot open is on the list of what needs a person, whatever the sources know of it: what they
        # know is still applied below when it is sure, and the person takes it or leaves the book as it is.
        state, reason = ("open" if state == "open" else "maybe"), "protected"
    taken = db.execute(update(MatchEntry).where(*guard).values(
        state=state, reason=reason, proposals=proposals_of(decision), updated_at=now)).rowcount
    if taken != 1:
        db.rollback()
        return False
    db.refresh(entry)
    sure = decision.state == "sure" and decision.best is not None
    if sure:
        apply.apply_match(db, edition, decision.best, decision.support, cover=cover, away=away)  # type: ignore[arg-type]
    elif not away:
        apply.clear_match(db, edition)
    # What the answer proves about the names of the author (a pen name), sure or not.
    apply.note_evidence(db, edition, decision.best if sure else None, decision.support if sure else [],
                        decision.ranked)
    return True


#: The sources an entry waits for, by the id of the entry: what the last look at the book found away (failed, resting,
#: at its daily limit, switched off meanwhile). While one of them rests the entry is not looked at again; a restart
#: forgets it, and the first look afterwards finds out again.
WAITING: dict[int, tuple[str, ...]] = {}


def waiting_per_source() -> dict[str, int]:
    """How many books wait for each source (as far as the last look found: a restart forgets it)."""
    counts: dict[str, int] = {}
    for sources in list(WAITING.values()):
        for source in set(sources):
            counts[source] = counts.get(source, 0) + 1
    return counts


def _unanswered(db: Session, wanted: Wanted, result: Lookup) -> list[str]:
    """The sources of the book's plan (``plan_for``) that gave no answer this time and will give one by themselves: they
    failed, were switched off just now, or rest (after errors or at their daily limit). One the operator did not switch
    on, or did not connect, is no one the book waits for."""
    always, extra = plan_for(wanted)
    out: list[str] = []
    for source in dict.fromkeys((*always, *extra)):
        if source in result.answered:
            continue
        if source in result.failed or source in result.off or config.availability(db, source).state == "resting":
            out.append(source)
    return out


def _resting(db: Session) -> set[str]:
    return {source for source in config.CATALOGUES if config.availability(db, source).state == "resting"}


def _comes_back(db: Session, source: str, result: Lookup) -> bool:
    """Whether a source that is away will be back by itself: it failed just now, or it rests after errors. One the
    operator switched off, or did not connect, is away until a person acts."""
    return source in result.failed or config.availability(db, source).state == "resting"


def match_entry(db: Session, entry: MatchEntry, *, interactive: bool = False) -> str:
    """Looks at one edition. Returns ``done``, ``skipped``, ``off`` (no source is ready), ``failed`` (the sources
    asked did not answer; the entry stays pending) or ``waiting`` (one that gave the edition its values before did not
    answer and will be back: nothing is decided on the word of the rest, and the entry stays pending, while the run
    goes on with the others).

    **What a source gave is never dropped because it was not asked this time.** A source that took part in the last
    match and is away for good (switched off, no token) leaves its values where the new answer brings none of its
    own."""
    # Another worker (the scan) may have changed the book since this session saw it: it is read anew.
    db.expire_all()
    edition = db.get(Edition, entry.edition_id)
    if edition is None or edition.missing_at is not None:
        return "skipped"
    wanted = wanted_of(db, edition)
    known = apply.sources_known(db, edition)
    asked_for = entry.fingerprint
    # Nothing is held open (no write transaction) while the sources are asked.
    db.commit()
    result = lookup(db, wanted, interactive=interactive)
    if result.asked == 0:
        return "off"
    if not result.answered:
        codes = ",".join(sorted(e.code for e in result.failed.values()))
        logger.info("No source answered edition=%s errors=%s", edition.id, codes)
        return "failed"
    away = frozenset(known - set(result.answered))
    decision = decide(wanted, result.candidates)
    if decision.state != "sure" and wanted.alt_titles:
        # The sources do not know the book by the title of the tags: before "they do not know it" is said, they are
        # asked for the title of its folder too (the name a person gave the folder is often the cleaner one).
        for alt in wanted.alt_titles[:1]:
            again = lookup(db, wanted, interactive=interactive, override=Query(
                kind=wanted.kind, title=alt, authors=wanted.authors, language=wanted.language, isbn=wanted.isbn,
                asin=wanted.asin, fallback=False))
            result.candidates += again.candidates
            result.answered += [source for source in again.answered if source not in result.answered]
            for source, error in again.failed.items():
                result.failed.setdefault(source, error)
            result.off |= again.off
        decision = decide(wanted, result.candidates)
    db.commit()
    unanswered = _unanswered(db, wanted, result)
    if any(_comes_back(db, source, result) for source in away):
        logger.info("A source of an earlier match is away, the entry waits edition=%s", edition.id)
        WAITING[entry.id] = tuple(unanswered or sorted(away))
        return "waiting"
    if unanswered and decision.state != "sure":
        # Not sure, and a source the book is asked at has not spoken: what the others said is not the answer. The book
        # is not "open" for that; it waits for the source.
        logger.info("A source of the plan did not answer, the entry waits edition=%s sources=%s", edition.id,
                    ",".join(unanswered))
        WAITING[entry.id] = tuple(unanswered)
        return "waiting"
    WAITING.pop(entry.id, None)
    if not settle(db, entry, edition, decision, interactive=interactive, away=away, fingerprint=asked_for):
        logger.info("The book was decided on or changed while it was matched, the answer is dropped edition=%s",
                    edition.id)
        return "skipped"
    db.commit()
    return "done"


def _pending(db: Session, library_id: int) -> list[int]:
    """The entries of a library to look at: pending, not decided by a person, and not held back by a source they wait
    for that still rests."""
    query = (select(MatchEntry.id).join(Edition, Edition.id == MatchEntry.edition_id)
             .where(MatchEntry.library_id == library_id, MatchEntry.state == "pending", MatchEntry.decision == "",
                    Edition.missing_at.is_(None)).order_by(MatchEntry.reason == RECHECK, MatchEntry.id))
    library = db.get(Library, library_id)
    scanning = library is not None and (library.progress_total or db.scalar(select(Job.id).where(
        Job.kind == "scan", Job.ref == library_id, Job.state.in_(("queued", "running"))).limit(1)) is not None)
    if scanning:
        # A run is reading the files: a book with a file that is not read by the present rules yet (or not put together
        # yet) is a piece of something the run is about to make (the discs of one recording): it is matched when the
        # whole is there, and not as a piece.
        from ..scanner import READ_REV

        need = READ_REV + int(library.reread_rev or 0)
        unsettled = select(BookFile.edition_id).where(
            BookFile.library_id == library_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
            BookFile.edition_id.is_not(None), or_(BookFile.dirty.is_(True), BookFile.rev < need))
        query = query.where(Edition.id.not_in(unsettled))
    ids = list(db.scalars(query))
    if WAITING:
        resting = _resting(db)
        ids = [ident for ident in ids if not (resting & set(WAITING.get(ident, ())))]
    return ids[:MAX_PER_RUN]


def run_library(db: Session, library_id: int) -> dict[str, int]:
    """Works through the pending entries of a library, in bundles, until none is left, the sources give out, or the
    server
    stops (``jobs.Interrupted``). Returns what was done."""
    done = {"done": 0, "skipped": 0, "failed": 0, "waiting": 0}
    if not config.ready_sources(db, config.CATALOGUES):
        return done
    ids = _pending(db, library_id)
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        if "hardcover" in config.ready_sources(db, config.CATALOGUES):
            queries = []
            for entry_id in chunk:
                entry = db.get(MatchEntry, entry_id)
                edition = db.get(Edition, entry.edition_id) if entry else None
                if edition is not None and edition.missing_at is None:
                    wanted = wanted_of(db, edition)
                    queries.append(Query(kind=wanted.kind, title=wanted.title, authors=wanted.authors))
            db.commit()
            try:
                hardcover.prefetch(db, queries)
            except http.SourceError:
                pass
        for entry_id in chunk:
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            entry = db.get(MatchEntry, entry_id)
            if entry is None or entry.state != "pending" or entry.decision:
                continue
            if WAITING.get(entry_id) and _resting(db) & set(WAITING[entry_id]):
                # A source it waits for rests (it failed in this very run): not asked again until it is back.
                done["waiting"] += 1
                continue
            try:
                outcome = match_entry(db, entry)
            except (jobs.Interrupted, http.SourceError):
                db.rollback()
                raise
            except Exception:
                logger.exception("Matching an edition failed edition=%s", entry.edition_id)
                db.rollback()
                entry = db.get(MatchEntry, entry_id)
                if entry is not None:
                    entry.state, entry.reason, entry.updated_at = "open", "failed", clock.now()
                    db.commit()
                done["failed"] += 1
                continue
            if outcome == "off" or outcome == "failed":
                # The sources are away or not answering: what is left waits.
                db.rollback()
                return done
            done["done" if outcome == "done" else "waiting" if outcome == "waiting" else "skipped"] += 1
    return done


def match_job(db: Session, job: Job) -> None:
    """Works through the pending entries of a library, and goes on while the scan (which runs beside it) adds more: a
    run that did something looks again at once, one that did nothing ends."""
    from .. import catalog

    library = db.get(Library, job.ref)
    if library is None:
        raise jobs.GiveUp("library gone")
    total = {"done": 0, "skipped": 0, "failed": 0, "waiting": 0}
    for _round in range(50):
        counts = run_library(db, library.id)
        for name in total:
            total[name] += counts[name]
        # Books that wait for a source are no progress: looking at them again at once would find the same.
        if not (counts["done"] + counts["skipped"] + counts["failed"]) or not _pending(db, library.id):
            break
    if total["done"] or total["failed"]:
        logger.info("Matching done library=%s done=%s failed=%s", library.id, total["done"], total["failed"])
        # A guess of the genre rests on the genres the books have; the sources just changed some.
        catalog.refresh_guesses(db, library.id)


jobs.register("match", match_job)


def enqueue_pending(db: Session) -> int:
    """Puts a job in the queue for each library that has entries waiting, while a source is ready to be asked. Returns
    how
    many were queued."""
    if not config.ready_sources(db, config.CATALOGUES):
        return 0
    catch_up(db)
    queued = 0
    for library_id in db.scalars(select(MatchEntry.library_id).where(
            MatchEntry.state == "pending", MatchEntry.decision == "").group_by(MatchEntry.library_id)).all():
        if not _pending(db, library_id):
            # Everything that waits there waits for a source that rests: the clock looks again when it is back.
            continue
        if db.scalar(select(Job.id).where(Job.kind == "match", Job.ref == library_id,
                                          Job.state.in_(("queued", "running")))):
            continue
        jobs.enqueue(db, "match", ref=library_id)
        queued += 1
    db.commit()
    return queued


def upgrade_rules(db: Session) -> bool:
    """When the rules of matching are newer than the ones the books were last matched by, every book no person decided
    on is matched anew, once (what the sources gave stays until a new answer replaces it). Waits for a source to be
    ready: books put back in the queue while none can be asked would leave the list of what a person has to look at.
    Returns whether it did."""
    if int(settings_service.get(db, "match_rev") or 0) >= MATCH_REV or not config.ready_sources(db, config.CATALOGUES):
        return False
    rematch_all(db)
    settings_service.save(db, {"match_rev": MATCH_REV})
    logger.info("Books are matched again by the rules of version %s", MATCH_REV)
    return True


def rematch_all(db: Session) -> int:
    """"Match everything again": every entry no person decided on is pending anew (what the sources gave stays until a
    new answer replaces it), **the unsure first**: what a person has to look at, or has not been looked at, is asked
    about before the books that were sure (``RECHECK``, which come last, after the books that are new). Returns how many
    entries."""
    catch_up(db)
    WAITING.clear()
    count = db.scalar(select(func.count(MatchEntry.id)).where(MatchEntry.decision == "")) or 0
    now = clock.now()
    db.execute(update(MatchEntry).where(MatchEntry.decision == "", MatchEntry.state == "sure").values(
        state="pending", reason=RECHECK, proposals=None, updated_at=now))
    db.execute(update(MatchEntry).where(MatchEntry.decision == "", MatchEntry.reason != RECHECK).values(
        state="pending", reason="", proposals=None, updated_at=now))
    db.commit()
    enqueue_pending(db)
    return int(count)
