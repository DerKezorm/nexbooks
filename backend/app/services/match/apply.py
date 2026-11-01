"""Putting a match into effect, and taking it back: what a source said goes into the edition's overlay, its names in the
sources are kept, translations are joined to their work, a cover is fetched. And the edits by hand: correcting a book,
joining it to another work, splitting it off, unlinking it from its sources.

**What is taken from a source, and in which order of the sources** is fixed here (``priority``); *which value wins* is
the
rank of ``overlay.py`` (a person, then the file, then a source). Nothing here writes to a file of a library.

**Joining and splitting are undone by each other.** A source that knows two editions as one work (Hardcover's book, Open
Library's work) moves one edition under the work of the other (``grouped_by`` = ``auto``); a person's join or split sets
``hand``, and nothing automatic moves such an edition again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import clock
from ...models import ROLE_BOOK, BookFile, Edition, ExternalRef, Library, Work
from .. import aliases, catalog, covers, genres, series_names
from ..readers.images import reduce_cover
from ..readers.limits import COVER_MAX_BYTES
from ..readers.names import is_generic_series
from ..readers.text import text_fits_language
from ..sources import http
from ..sources.model import Candidate
from . import overlay
from .score import EDITION_LEVEL, Scored

logger = logging.getLogger("nexbooks.match")

#: Which source speaks first for each kind of edition when several know the same thing.
PRIORITY = {
    "ebook": ("hardcover", "openlibrary", "dnb", "audible"),
    "audio": ("audible", "hardcover", "openlibrary", "dnb"),
}


def ordered(kind: str, best: Candidate, support: list[Candidate]) -> list[Candidate]:
    """The best candidate first, then the others by the order of the sources for this kind of edition."""
    order = PRIORITY.get(kind, PRIORITY["ebook"])
    rest = sorted(support, key=lambda c: order.index(c.source) if c.source in order else len(order))
    return [best, *rest]


def fetch_cover(db: Session, candidate: Candidate, *, interactive: bool) -> tuple[str, str] | None:
    """The cover of a candidate as a stored picture ``(name, colour)``, or None (no address, the source does not answer,
    the picture is none or too large). The address is the source's own, checked again before it is asked."""
    if not candidate.cover_url or not http.host_allowed(candidate.source, candidate.cover_url):
        return None
    try:
        answer = http.request(db, candidate.source, candidate.cover_url, headers={"Accept": "image/*"},
                              limit=COVER_MAX_BYTES, interactive=interactive)
    except http.SourceError:
        return None
    if answer.status_code != 200:
        return None
    made = reduce_cover(answer.content)
    if made is None:
        return None
    webp, color = made
    return covers.save(webp), color


def build_values(
    edition: Edition, best: Scored, support: list[Scored]
) -> tuple[dict[str, Any], dict[str, str], dict[str, dict[str, Any]]]:
    """The fields a match gives the edition, by the order of the sources (empty ones are left out), which source said
    each of them (kept in the overlay, so that a source that cannot be asked later is known by what it gave), and what
    goes with a field besides its value (the votes of the genre)."""
    cands = ordered(edition.kind, best.candidate, [s.candidate for s in support])
    values: dict[str, Any] = {}
    origins: dict[str, str] = {}
    extras: dict[str, dict[str, Any]] = {}

    def first(name: str, attribute: str, fits: Callable[[Candidate], bool] | None = None) -> Any:
        for c in cands:
            if getattr(c, attribute) not in (None, "", []) and (fits is None or fits(c)):
                origins[name] = c.source
                return getattr(c, attribute)
        return None

    def written_right(c: Candidate) -> bool:
        """A description is taken only when it is written in the language of the book: a catalogue now and
        then hands over the text of another book (an English blurb of an author else, for a German thriller)."""
        language = edition.language or (c.languages[0] if len(c.languages) == 1 else "")
        return text_fits_language(c.description, language)

    values["description"] = first("description", "description", written_right)
    # (A line of a publisher is no series here either: ``dtv 2164`` from a library, ``Neobooks`` from a shop.)
    named = next((c for c in cands if c.series and not is_generic_series(c.series) and not series_names.is_line(
        c.series, c.series_pos, authors=c.authors, library=c.source in series_names.LIBRARIES)), None)
    if named is not None:
        values["series"] = named.series
        values["series_pos"] = named.series_pos
        origins["series"] = origins["series_pos"] = named.source
    # The genre is the majority of the voices of the sources that know the book (each with the weight of its source),
    # and later of the voices of the file too (``overlay.apply_to``): the votes are kept for that. (One voice for each
    # source: the first of its candidates; a source that knows the book in three records does not vote three times.)
    voices: dict[str, Candidate] = {}
    for c in cands:
        voices.setdefault(c.source, c)
    votes = genres.add_votes(
        *[genres.tally(c.subjects, genres.WEIGHTS.get(c.source, genres.Fraction(1))) for c in voices.values()])
    values["genre"] = genres.winner(votes)
    origins["genre"] = next((c.source for c in cands if c.subjects), best.candidate.source)
    if votes:
        extras["genre"] = {"votes": {genre: round(float(value), 3) for genre, value in votes.items()}}
    # The year of a recording is the year it was recorded; an e-book's is not that (an audiobook comes out years after
    # the book), so for an e-book only the sources that tell the book's own year speak.
    book_year = next((c.year for c in cands
                      if c.year and not (edition.kind == "ebook" and c.source == "audible")), None)
    if edition.kind == "ebook":
        values["year"] = book_year
        if book_year:
            origins["year"] = next(c.source for c in cands if c.year == book_year)
    else:
        values["year"] = first("year", "year")
    values["original_title"] = first("original_title", "original_title")
    values["original_language"] = first("original_language", "original_language")
    # The edition's own data (ISBN, publisher) comes from a record of an edition of the same kind: a recording's
    # publisher is not the publisher of the e-book.
    level = next((c for c in cands if c.source in EDITION_LEVEL and (edition.kind == "audio" or c.source != "audible")),
                 None)
    # The ISBN and the ASIN the files give (one a source gave the last time is no file's: it is given anew).
    own_isbn, own_asin = _file_value(edition, "isbn"), _file_value(edition, "asin")
    if edition.kind == "audio":
        proven = best.candidate if best.proof else None
        # The narrators belong to a recording: only the recording the running time proves gives them (another reading of
        # the same book has other voices).
        if proven is not None:
            values["narrators"] = first("narrators", "narrators")
        if proven is not None:
            values["variant"] = proven.variant or None
            origins["variant"] = proven.source
        if proven is not None and proven.source == "audible" and not own_asin:
            values["asin"] = proven.asin
            origins["asin"] = proven.source
    if level is not None:
        if not own_isbn and level.isbns:
            values["isbn"] = level.isbns[0]
            origins["isbn"] = level.source
        values["publisher"] = level.publisher
        origins["publisher"] = level.source
        # A language that was only worked out (``overlay.GUESS``) gives way to the one a source names. Of a recording
        # only the recording that is proven says it: the same book in another market is read in another language.
        speaks = edition.kind != "audio" or ((best.proof or best.certain) and level is best.candidate)
        if speaks and (not edition.language or overlay.is_guess(edition.overlay, "language")) \
                and len(level.languages) == 1:
            values["language"] = level.languages[0]
            origins["language"] = level.source
    return {name: value for name, value in values.items() if value not in (None, "", [])}, origins, extras


def _file_value(edition: Edition, name: str) -> str:
    from .run import file_value

    return file_value(edition, name)


def refs_of(candidates: list[Candidate]) -> set[tuple[str, str, str]]:
    """``(source, kind, key)`` of what candidates are called in their sources: the work, and for a source whose records
    are editions, the edition."""
    found = set()
    for c in candidates:
        if c.work_key:
            found.add((c.source, "work", c.work_key[:120]))
        if c.source in EDITION_LEVEL:
            found.add((c.source, "edition", c.key[:120]))
    return found


def sources_known(db: Session, edition: Edition) -> set[str]:
    """The sources an earlier match of the edition came from."""
    return set(db.scalars(select(ExternalRef.source).where(ExternalRef.edition_id == edition.id).distinct()))


def set_refs(db: Session, edition: Edition, candidates: list[Candidate], keep: frozenset[str] = frozenset()) -> None:
    """What the edition is called in the sources, replaced as a whole; except for the sources in ``keep``."""
    db.execute(delete(ExternalRef).where(ExternalRef.edition_id == edition.id, ExternalRef.source.not_in(list(keep))))
    seen: set[tuple[str, str, str, str]] = set()
    for c in candidates:
        wanted = []
        if c.work_key:
            wanted.append(("work", c.work_key))
        if c.source in EDITION_LEVEL:
            wanted.append(("edition", c.key))
        for kind, key in wanted:
            marker = (c.source, kind, key[:120], c.market)
            if marker not in seen:
                seen.add(marker)
                db.add(ExternalRef(edition_id=edition.id, source=c.source, kind=kind, key=key[:120], market=c.market))
    db.flush()


def rebuild(db: Session, edition: Edition) -> None:
    """Puts the edition together again from its files and its overlay (the scan's own step for one unit)."""
    from .. import scanner

    library = db.get(Library, edition.library_id)
    rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK,
                                                  BookFile.missing_at.is_(None))))
    if library is None or not rows:
        return
    catalog.assign(db, library, rows, scanner._sidecars_for(db, library.id, rows[0].unit))


def apply_match(db: Session, edition: Edition, best: Scored, support: list[Scored], *,
                cover: tuple[str, str] | None, away: frozenset[str] = frozenset()) -> None:
    """A match goes into effect: the overlay, the names in the sources, the cover, the edition put together anew, and
    the translations joined. The caller fetched the cover beforehand (no request is made inside a write).

    ``away`` are the sources that took part in an earlier match and were not asked this time (switched off, not
    connected): what they said stays where the new answer brings nothing in its place, **when the new answer still
    knows the book by the same name in another source** (else it may be another book now, and nothing of the old
    stays)."""
    values, origins, extras = build_values(edition, best, support)
    candidates = [best.candidate, *[s.candidate for s in support]]
    keep = away
    if keep:
        old = {(r.source, r.kind, r.key) for r in db.scalars(select(ExternalRef).where(
            ExternalRef.edition_id == edition.id, ExternalRef.source.not_in(list(keep))))}
        if not old & refs_of(candidates):
            keep = frozenset()
    before = overlay.source_entry(edition.overlay, "cover")
    if cover is not None and not edition.cover:
        values["cover"] = cover[0]
        origins["cover"] = best.candidate.source
        extras["cover"] = {"color": cover[1]}
    elif before is not None and covers.path_of(str(before["v"])) is not None:
        # The cover a source gave last time stays while no new one is asked for.
        values["cover"] = before["v"]
        if before.get("src"):
            origins["cover"] = str(before["src"])
        extras["cover"] = {"color": before.get("color", "")}
    edition.overlay = overlay.with_source(edition.overlay, values, origins=origins, keep=keep, **extras)
    set_refs(db, edition, candidates, keep)
    rebuild(db, edition)
    merge_by_refs(db, edition)


def note_evidence(db: Session, edition: Edition, best: Scored | None, support: list[Scored] | None = None,
                  ranked: list[Scored] | None = None) -> None:
    """What the answer of the sources proves about the names of the author (``aliases.note_match``) replaces what the
    answer before proved, and what follows from it is worked out anew (``aliases.recompute``)."""
    aliases.note_match(db, edition, best, support, ranked)
    aliases.recompute(db)


def clear_match(db: Session, edition: Edition) -> bool:
    """Takes everything a source gave back: the source values of the overlay and the names in the sources. Returns
    whether
    there was anything to take."""
    # (What nexbooks worked out itself, the language of a recording from its twin or the library, came from no match
    # and stays.)
    kept = overlay.without_match(edition.overlay)
    had = len(kept) != len(overlay._valid(edition.overlay)) or bool(
        db.scalar(select(func.count(ExternalRef.id)).where(ExternalRef.edition_id == edition.id)))
    if had:
        edition.overlay = kept or None
        db.execute(delete(ExternalRef).where(ExternalRef.edition_id == edition.id))
        rebuild(db, edition)
    # The evidence of a pen name the match gave goes with it, and a name that rested on it only is its own again.
    if aliases.drop_match_evidence(db, edition.id):
        aliases.recompute(db)
    return had


# ---- Works ------------------------------------------------------------------------------------------------------


def _settle(db: Session, work_id: int) -> None:
    work = db.get(Work, work_id)
    if work is None:
        return
    if db.scalar(select(func.count(Edition.id)).where(Edition.work_id == work_id)):
        catalog.refresh_work(db, work)
    else:
        db.delete(work)
    db.flush()


def move(db: Session, edition: Edition, work_id: int, how: str) -> None:
    """The edition goes under another work (``how``: ``auto`` or ``hand``); the work it left is settled, or gone."""
    old = edition.work_id
    edition.work_id = work_id
    edition.grouped_by = how
    db.flush()
    for ident in {old, work_id}:
        _settle(db, ident)


def merge_by_refs(db: Session, edition: Edition) -> int | None:
    """When a source knows another edition as the same work, this one goes under that one's work (the oldest work wins).
    Returns the work it joined, or None. Never for an edition a person placed."""
    if edition.grouped_by in ("hand", "wish"):
        return None
    target: int | None = None
    for ref in db.scalars(select(ExternalRef).where(ExternalRef.edition_id == edition.id, ExternalRef.kind == "work")):
        found = db.scalar(
            select(func.min(Edition.work_id)).join(ExternalRef, ExternalRef.edition_id == Edition.id)
            .where(ExternalRef.source == ref.source, ExternalRef.kind == "work", ExternalRef.key == ref.key,
                   Edition.id != edition.id, Edition.work_id != edition.work_id, Edition.grouped_by != "hand"))
        if found is not None and (target is None or found < target):
            target = found
    if target is not None and target < edition.work_id:
        move(db, edition, target, "auto")
        return target
    return None


def split(db: Session, edition: Edition) -> Work:
    """The edition leaves its work and becomes a work of its own (placed by hand: nothing moves it again by itself)."""
    now = clock.now()
    work = Work(title=edition.title[:300], added_at=now, updated_at=now)
    db.add(work)
    db.flush()
    work.pattern, work.tall, work.gold = genres.style_of(work.id)
    move(db, edition, work.id, "hand")
    return work


def join(db: Session, edition: Edition, work: Work) -> None:
    """A person puts the edition under the work. The editions that stand in the work already are the book the person
    joined it to: they stand there on purpose from now on, or the next time one of them is put together again the one
    placed by hand would keep the work and send them away (the two may be called differently, that is why a person
    joins them)."""
    for other in db.scalars(select(Edition).where(Edition.work_id == work.id, Edition.id != edition.id,
                                                  Edition.missing_at.is_(None))):
        if other.grouped_by not in ("hand", "wish"):
            other.grouped_by = "hand"
    move(db, edition, work.id, "hand")
    db.flush()


# ---- Corrections by hand ----------------------------------------------------------------------------------------

#: What a person may correct by hand, and how each value is checked.
CORRECTABLE = ("title", "subtitle", "authors", "narrators", "series", "series_pos", "genre", "language", "variant")


def correct(db: Session, edition: Edition, changes: dict[str, Any]) -> None:
    """A person's corrections go into the overlay (a ``None`` takes one back) and the edition is put together anew."""
    edition.overlay = overlay.with_hand(edition.overlay, {k: v for k, v in changes.items() if k in CORRECTABLE}) or None
    rebuild(db, edition)
