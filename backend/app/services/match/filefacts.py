"""What the files of a book say about themselves, set beside what a source proposes for it.

The list of books the matching is not sure about shows each book as the library knows it, after the matching and the
corrections of people. A person who has to decide wants more than that: the file name and its folder, and what is
written *in* the file (title, author, narrator, ISBN, publisher, year, language, running time, chapters, pages). This
module reads that from what the scan kept of each file (``BookFile.probe``); it opens no file and touches nothing.

Two things are told apart, because they mean different things to a person:

* the file **contradicts** the proposal (it names another title, another author, another ISBN): a reason against it;
* the file **says nothing** (no ISBN, no narrator in its tags): no reason against it, but nothing for it either.

Only names relative to the library ever leave this module; the path on the disk is never part of an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import ROLE_BOOK, BookFile, Chapter, Edition
from ..readers.text import fold
from ..sources.model import Candidate
from .score import _forms, author_state, series_key, title_state

#: The most subjects and chapters named in an answer.
MAX_SUBJECTS = 8

#: How a field of the file stands against the proposal.
AGREES, SIMILAR, DIFFERS, FILE_SILENT, SOURCE_SILENT = "agrees", "similar", "differs", "file_silent", "source_silent"
STATES = (AGREES, SIMILAR, DIFFERS, FILE_SILENT, SOURCE_SILENT)

#: The title tag of a track that only counts ("Kapitel 1", "Track 03", "07"): it names no book, so the file says nothing
#: about the title (and a proposal is not held against it).
_TRACK_WORDS = (r"kapitel|kap|chapter|chap|ch|track|trk|titel|title|spur|teil|part|cd|disc|disk|file|datei|section|"
                r"piste|chapitre|capitulo|capítulo|pista|hoofdstuk|kapittel|kapitola|rozdział|spår")
_TRACK_COUNT = r"\d{1,3}(?:\s*(?:/|of|von|de|sur)\s*\d{1,3})?"
_TRACK_TITLE = re.compile(
    rf"^(?:{_TRACK_COUNT}|(?:{_TRACK_WORDS})\.?[\s_\-]*{_TRACK_COUNT}(?:\s*[:.\-–].*)?)$", re.IGNORECASE)

#: How far apart two years may be before the file is said to contradict the source: a new edition, a reprint, a
#: recording of an older book are normal, so a gap of one or two years is only "close".
YEAR_CLOSE = 2

#: The fields of the comparison, in the order they are shown.
FIELDS = ("title", "authors", "narrators", "series", "volume", "isbn", "language", "text_language", "year", "runtime",
          "publisher")


@dataclass
class FileLine:
    """One file of the edition, as the library lists it."""

    id: int
    name: str
    folder: str
    format: str
    size: int
    part: int
    duration: float
    pages: int
    chapters: int
    protected: bool


@dataclass
class Says:
    """What the files of one edition say, field by field; an empty value is "the file says nothing"."""

    title: str = ""
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series: str = ""
    series_pos: float | None = None
    isbn: str = ""
    asin: str = ""
    publisher: str = ""
    year: int | None = None
    language: str = ""
    text_language: str = ""
    genre: str = ""
    subjects: list[str] = field(default_factory=list)
    pages: int = 0
    #: A page count that was counted (a PDF), not estimated from the amount of text.
    pages_counted: bool = False
    #: The running time of a recording in seconds, all parts together.
    duration: float = 0.0
    chapters: int = 0
    cover_file: str = ""


@dataclass
class Facts:
    folder: str
    files: list[FileLine]
    says: Says


@dataclass
class Row:
    field: str
    file: str
    source: str
    state: str
    #: ``minutes`` when both values are numbers of minutes (the interface words the time).
    unit: str = ""


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _names(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        name = _text(item, 200)
        if name and name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _split(path: str) -> tuple[str, str]:
    folder, _, name = path.rpartition("/")
    return folder, name


def book_files(db: Session, edition_id: int) -> list[BookFile]:
    """The files of an edition that are on the disk, in the order of their parts."""
    return list(db.scalars(select(BookFile).where(
        BookFile.edition_id == edition_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))
        .order_by(BookFile.part, BookFile.path)))


def facts_of(db: Session, edition: Edition) -> Facts:
    """The files of ``edition`` and what they say. The first file that says a thing names it (the parts of a recording
    carry the same book in their tags; the formats of an e-book the same title)."""
    facts = facts_from(book_files(db, edition.id), edition.kind == "audio")
    counted = len(db.scalars(select(Chapter.id).where(Chapter.edition_id == edition.id)).all())
    facts.says.chapters = counted or facts.says.chapters
    return facts


def facts_from(rows: list[Any], audio: bool) -> Facts:
    """What the ``rows`` say, whatever they are: files of a library (``BookFile``) or of a download that waits for a
    decision. A row has ``id``, ``path`` (inside the folder it lies in), ``format``, ``size``, ``part``, ``duration_ms``
    and ``probe``."""
    says = Says()
    lines: list[FileLine] = []
    for row in rows:
        probe = row.probe if isinstance(row.probe, dict) else {}
        folder, name = _split(row.path)
        chapters = probe.get("chapters")
        pages = probe.get("pages")
        lines.append(FileLine(
            id=row.id, name=name, folder=folder, format=row.format, size=int(row.size or 0), part=int(row.part or 1),
            duration=round((row.duration_ms or 0) / 1000, 3),
            pages=int(pages) if isinstance(pages, int) and not isinstance(pages, bool) else 0,
            chapters=len(chapters) if isinstance(chapters, list) else 0, protected=bool(probe.get("drm"))))
        # A recording's title tag is the title of the track; its album is the book.
        title = (_text(probe.get("album"), 300) or _text(probe.get("title"), 300)) if audio \
            else _text(probe.get("title"), 300)
        if audio and _TRACK_TITLE.match(title.strip()):
            title = ""
        says.title = says.title or title
        says.subtitle = says.subtitle or _text(probe.get("subtitle"), 300)
        says.authors = says.authors or _names(probe.get("authors"))
        for name_ in _names(probe.get("narrators")):
            if name_ not in says.narrators and len(says.narrators) < 8:
                says.narrators.append(name_)
        says.series = says.series or _text(probe.get("series"), 200)
        if says.series_pos is None:
            says.series_pos = _number(probe.get("series_pos"))
        says.isbn = says.isbn or _text(probe.get("isbn"), 20)
        says.asin = says.asin or _text(probe.get("asin"), 20)
        says.publisher = says.publisher or _text(probe.get("publisher"), 200)
        year = probe.get("year")
        if says.year is None and isinstance(year, int) and not isinstance(year, bool) and 0 < year < 3000:
            says.year = year
        says.language = says.language or _text(probe.get("language"), 8).lower()
        says.text_language = says.text_language or _text(probe.get("text_language"), 8).lower()
        says.genre = says.genre or _text(probe.get("genre"), 80)
        for subject in _names(probe.get("subjects"), MAX_SUBJECTS):
            if subject not in says.subjects and len(says.subjects) < MAX_SUBJECTS:
                says.subjects.append(subject)
        if isinstance(pages, int) and not isinstance(pages, bool) and pages > says.pages:
            says.pages = pages
            says.pages_counted = row.format == "pdf"
        says.cover_file = says.cover_file or _text(probe.get("cover_file"), 64)
    says.duration = round(sum(line.duration for line in lines), 3)
    says.chapters = sum(line.chapters for line in lines)
    return Facts(folder=lines[0].folder if lines else "", files=lines, says=says)


def _state_for(file_value: bool, source_value: bool) -> str | None:
    """The state when one side or both say nothing; None when both say something."""
    if not file_value and not source_value:
        return ""
    if not file_value:
        return FILE_SILENT
    if not source_value:
        return SOURCE_SILENT
    return None


def _title(says: Says, candidate: Candidate) -> Row:
    file_text = says.title + (f": {says.subtitle}" if says.subtitle else "")
    source_text = candidate.title + (f": {candidate.subtitle}" if candidate.subtitle else "")
    empty = _state_for(bool(says.title), bool(candidate.title))
    if empty is not None:
        return Row("title", file_text, source_text, empty)
    verdict = title_state(_forms(says.title, says.subtitle, says.authors),
                          _forms(candidate.title, candidate.subtitle, candidate.authors), [says.title],
                          [candidate.title])
    state = AGREES if verdict in ("exact", "main") else SIMILAR if verdict in ("part", "close") else DIFFERS
    return Row("title", file_text, source_text, state)


def _authors(says: Says, candidate: Candidate) -> Row:
    empty = _state_for(bool(says.authors), bool(candidate.authors))
    shown = (", ".join(says.authors), ", ".join(candidate.authors[:4]))
    if empty is not None:
        return Row("authors", *shown, empty)
    verdict = author_state(says.authors, candidate.authors)
    state = AGREES if verdict == "match" else SIMILAR if verdict in ("weak", "unknown") else DIFFERS
    return Row("authors", *shown, state)


def _narrators(says: Says, candidate: Candidate) -> Row:
    empty = _state_for(bool(says.narrators), bool(candidate.narrators))
    shown = (", ".join(says.narrators), ", ".join(candidate.narrators[:3]))
    if empty is not None:
        return Row("narrators", *shown, empty)
    mine = {fold(name) for name in says.narrators}
    theirs = {fold(name) for name in candidate.narrators}
    return Row("narrators", *shown, AGREES if mine & theirs else DIFFERS)


def _volume_text(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _series(says: Says, candidate: Candidate) -> Row:
    empty = _state_for(bool(says.series), bool(candidate.series))
    shown = (says.series, candidate.series)
    if empty is not None:
        return Row("series", *shown, empty)
    same = series_key(says.series) == series_key(candidate.series)
    return Row("series", *shown, AGREES if same else DIFFERS)


def _volume(says: Says, candidate: Candidate) -> Row:
    empty = _state_for(says.series_pos is not None, candidate.series_pos is not None)
    shown = (_volume_text(says.series_pos), _volume_text(candidate.series_pos))
    if empty is not None:
        return Row("volume", *shown, empty)
    return Row("volume", *shown, AGREES if says.series_pos == candidate.series_pos else DIFFERS)


def _isbn(says: Says, candidate: Candidate) -> Row:
    theirs = [value for value in candidate.isbns if value]
    empty = _state_for(bool(says.isbn), bool(theirs))
    shown = (says.isbn, ", ".join(theirs[:2]))
    if empty is not None:
        return Row("isbn", *shown, empty)
    return Row("isbn", *shown, AGREES if says.isbn in theirs else DIFFERS)


def _language(name: str, value: str, candidate: Candidate) -> Row:
    languages = [code.lower() for code in candidate.languages]
    empty = _state_for(bool(value), bool(languages))
    shown = (value, ", ".join(languages[:3]))
    if empty is not None:
        return Row(name, *shown, empty)
    return Row(name, *shown, AGREES if value in languages else DIFFERS)


def _year(says: Says, candidate: Candidate) -> Row:
    empty = _state_for(says.year is not None, candidate.year is not None)
    shown = (str(says.year or ""), str(candidate.year or ""))
    if empty is not None:
        return Row("year", *shown, empty)
    assert says.year is not None and candidate.year is not None
    gap = abs(says.year - candidate.year)
    return Row("year", *shown, AGREES if gap == 0 else SIMILAR if gap <= YEAR_CLOSE else DIFFERS)


def _runtime(says: Says, candidate: Candidate) -> Row:
    mine = round(says.duration / 60) if says.duration else 0
    theirs = candidate.runtime_min or 0
    empty = _state_for(bool(mine), bool(theirs))
    shown = (str(mine or ""), str(theirs or ""))
    if empty is not None:
        return Row("runtime", *shown, empty, unit="minutes")
    ratio = abs(mine - theirs) / max(mine, theirs)
    return Row("runtime", *shown, AGREES if ratio <= 0.05 else SIMILAR if ratio <= 0.15 else DIFFERS, unit="minutes")


def _publisher(says: Says, candidate: Candidate) -> Row:
    empty = _state_for(bool(says.publisher), bool(candidate.publisher))
    shown = (says.publisher, candidate.publisher)
    if empty is not None:
        return Row("publisher", *shown, empty)
    a, b = fold(says.publisher), fold(candidate.publisher)
    # A publisher is written many ways ("Orchard" and "Orchard Books Ltd."); a name that is part of the other is the
    # same, another name is no reason against a book (an edition of another year has another one).
    return Row("publisher", *shown, AGREES if a in b or b in a else SIMILAR)


def compare(facts: Facts, candidate: Candidate, kind: str) -> list[Row]:
    """The fields of the file against the proposal. A field both sides leave empty is not listed; a recording is also
    compared by its narrators and its running time, an e-book is not."""
    says = facts.says
    rows = [_title(says, candidate), _authors(says, candidate)]
    if kind == "audio":
        rows.append(_narrators(says, candidate))
    rows += [_series(says, candidate), _volume(says, candidate), _isbn(says, candidate),
             _language("language", says.language, candidate)]
    if says.text_language:
        rows.append(_language("text_language", says.text_language, candidate))
    rows.append(_year(says, candidate))
    if kind == "audio":
        rows.append(_runtime(says, candidate))
    rows.append(_publisher(says, candidate))
    _isbn_of_another_edition(rows)
    return [row for row in rows if row.file or row.source]


def _isbn_of_another_edition(rows: list[Row]) -> None:
    """Another ISBN is the sign of another *edition* (a paperback, an e-book, a reprint have each their own) as often as
    of another book: it speaks against the proposal only when the title or the author do not fit either. Where they fit,
    it is "close"."""
    by_field = {row.field: row for row in rows}
    isbn = by_field.get("isbn")
    if isbn is None or isbn.state != DIFFERS:
        return
    title, authors = by_field.get("title"), by_field.get("authors")
    if title is not None and title.state in (AGREES, SIMILAR) and (authors is None or authors.state != DIFFERS):
        isbn.state = SIMILAR
