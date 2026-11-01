"""What every source hands over in the same shape: a ``Candidate``, one book (or one recording) as a source knows it.

The clients (``hardcover``, ``openlibrary``, ``audible``, ``dnb``) turn their own answers into candidates and nothing
else leaves them. A candidate is the *cut down* answer: only the fields nexbooks uses are kept, which is what the cache
holds (``cache.py``) and what the proposals of the list for a person are made of (``services/matching.py``).

``cover_url`` is internal: it never reaches a browser (the interface may only load pictures from nexbooks itself); the
server fetches a cover from a source's own host when it is asked to, through ``http.py``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import book_words
from ..readers.text import html_to_text, isbn13, year_of

#: Names of languages as the sources write them -> the two letters nexbooks uses. Audible says "german", Hardcover says
#: "German", Open Library and the German National Library use the three letters of the library world (MARC).
LANGUAGE_NAMES = {
    "german": "de", "english": "en", "french": "fr", "spanish": "es", "spanish; castilian": "es", "castilian": "es",
    "italian": "it", "dutch": "nl", "dutch; flemish": "nl", "flemish": "nl", "swedish": "sv", "danish": "da",
    "norwegian": "no", "norwegian bokmal": "no", "finnish": "fi", "portuguese": "pt", "polish": "pl", "russian": "ru",
    "turkish": "tr", "czech": "cs", "japanese": "ja", "chinese": "zh", "greek": "el", "hungarian": "hu",
    "catalan": "ca", "ukrainian": "uk", "korean": "ko", "latin": "la", "romanian": "ro", "hebrew": "he",
}
MARC_CODES = {
    "ger": "de", "deu": "de", "eng": "en", "fre": "fr", "fra": "fr", "spa": "es", "ita": "it", "dut": "nl", "nld": "nl",
    "swe": "sv", "dan": "da", "nor": "no", "nob": "no", "fin": "fi", "por": "pt", "pol": "pl", "rus": "ru", "tur": "tr",
    "cze": "cs", "ces": "cs", "jpn": "ja", "chi": "zh", "zho": "zh", "gre": "el", "ell": "el", "hun": "hu", "cat": "ca",
    "ukr": "uk", "kor": "ko", "lat": "la", "rum": "ro", "ron": "ro", "heb": "he",
}

#: A recording that is a play, not a reading: the words that say so (one list for every place, ``book_words``; it
#: reads folded text).
DRAMATIZED = book_words.DRAMATIZED


@dataclass
class Query:
    """What is asked of a source: the book as nexbooks knows it from its files. Titles, names and an ISBN or an ASIN go
    out, nothing else."""

    kind: str
    title: str
    authors: list[str] = field(default_factory=list)
    language: str = ""
    isbn: str = ""
    asin: str = ""
    limit: int = 10
    #: Whether a catalogue that finds nothing by the words may try once more by its fields (Audible). Off when the book
    #: is asked for again by another title anyway: an unknown book costs at most two questions.
    fallback: bool = True


def language_code(value: object) -> str:
    """The two letters of a language given by name (``German``), by the three letters of the library world (``ger``) or
    by two letters already; empty when it is none of them."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("é", "e").replace("ç", "c")
    if text in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[text]
    if text in MARC_CODES:
        return MARC_CODES[text]
    if re.fullmatch(r"[a-z]{2}(?:[-_][a-z]{2,4})?", text):
        return text[:2]
    return ""


@dataclass
class Candidate:
    """One book as a source knows it. ``key`` names it in its source (the ASIN at Audible); ``work_key`` names the work
    behind it where the source has such a thing (Hardcover's book, Open Library's work): two editions that share one
    are translations or forms of the same book."""

    source: str
    key: str
    title: str
    work_key: str = ""
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    #: Other names the source knows its authors by (Hardcover's ``alternate_names``), kept only when one of them is the
    #: name the file gives: the proof that a pen name is the author (``services/aliases.py``).
    author_aliases: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    #: The languages the book is known in (one for an edition-level source like Audible).
    languages: list[str] = field(default_factory=list)
    isbns: list[str] = field(default_factory=list)
    asin: str = ""
    year: int | None = None
    #: The year of the edition a shelf of Open Library showed, where ``year`` is the work's first year.
    edition_year: int | None = None
    #: The genre a chart files the book under (Apple's number); empty for every other source.
    category: str = ""
    #: The day it came out (or comes out), ``YYYY-MM-DD``, when the source says it: a day after today is a book to come.
    released: str = ""
    series: str = ""
    series_pos: float | None = None
    description: str = ""
    subjects: list[str] = field(default_factory=list)
    #: A recording's running time in minutes, and what kind of recording it is (``unabridged``, ``abridged``,
    #: ``dramatized``, or empty when the source does not say).
    runtime_min: int | None = None
    variant: str = ""
    publisher: str = ""
    pages: int | None = None
    market: str = ""
    original_title: str = ""
    original_language: str = ""
    #: The titles the book has in other languages, ``{"de": ["Nebelchor"]}`` (Hardcover, from its editions).
    titles: dict[str, list[str]] = field(default_factory=dict)
    cover_url: str = ""
    #: How well known the book is at its source (readers): the tie-break between records of one book.
    weight: int = 0
    #: The title as the source wrote it, before its core was taken (``Lantern Road (1/2)``): what it says of the
    #: edition (a part, a play) is in what the core leaves out. Empty when it is the title.
    full_title: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Candidate:
        """A candidate from what ``to_json`` made (the cache, the proposals); fields it does not know are left out,
        and a
        field of the wrong type is dropped rather than trusted."""
        known = cls.__dataclass_fields__
        values: dict[str, Any] = {}
        for name, value in (data or {}).items():
            if name not in known:
                continue
            kind = known[name].type
            if kind in ("str", str):
                values[name] = value if isinstance(value, str) else ""
            elif kind in ("list[str]",):
                values[name] = [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
            elif kind == "int | None":
                values[name] = value if isinstance(value, int) and not isinstance(value, bool) else None
            elif kind == "float | None":
                values[name] = float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
            elif kind == "int":
                values[name] = value if isinstance(value, int) and not isinstance(value, bool) else 0
            elif kind == "dict[str, list[str]]":
                values[name] = {str(k)[:8]: [t for t in v if isinstance(t, str)] for k, v in value.items()
                                if isinstance(v, list)} if isinstance(value, dict) else {}
        if not values.get("source") or not values.get("key") or not values.get("title"):
            raise ValueError("a candidate has a source, a key and a title")
        return cls(**values)


@dataclass
class Entry:
    """One record of a source about a book of an author, as the catalogue of the author's works is built from it
    (``services/oeuvre``): an edition in one language (a library's record, an Audible product) or a work with its title
    in one language (Open Library, Hardcover). Only what the building uses is kept: titles, language, years, numbers
    and the few signals that say what kind of book it is (``signals``); never a description or a picture."""

    source: str
    key: str
    title: str
    #: The language of this title (two letters), empty when the source does not say.
    language: str = ""
    #: The work behind the record where the source has one (Hardcover's book, Open Library's work): records that share
    #: it are one work in different languages.
    work_key: str = ""
    #: A translation names the title and the language it was made from.
    original_title: str = ""
    original_language: str = ""
    authors: list[str] = field(default_factory=list)
    #: The name the source was asked by: the author's own, or one of their pen names.
    via: str = ""
    year: int | None = None
    released: str = ""
    isbns: list[str] = field(default_factory=list)
    asin: str = ""
    pages: int | None = None
    runtime_min: int | None = None
    variant: str = ""
    #: ``text`` (a book, printed or as a file) or ``audio`` (a recording).
    form: str = "text"
    series: str = ""
    series_pos: float | None = None
    #: What says what kind of book it is: ``group`` (the subject group of the German library: ``B``, ``K``, digits),
    #: ``audience`` and ``litcode`` (the French library), ``nur`` (the Dutch one), ``lit`` and ``cat`` (Hardcover's
    #: fiction and category), ``genres``, ``subjects``, ``keywords`` (Audible), ``form`` (a library's word for the
    #: form).
    signals: dict[str, Any] = field(default_factory=dict)
    #: How many authors the record names (an anthology has many), and whether the source says it is a box of books.
    authors_count: int = 1
    compilation: bool = False
    #: How well known it is at its source (readers, editions): the tie-break for the record that speaks for a work.
    weight: int = 0
    cover_url: str = ""
    #: Who the source says wrote it, by the source's own number of that person (Open Library's author key, Audible's
    #: author ASIN, the German library's GND number); empty when the source has none. Two people of one name have two
    #: numbers (``services/oeuvre/people.py``).
    person: str = ""
    #: What the source said of the person: ``same`` (the author asked about, proven by the number), ``""`` (unknown).
    who: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Entry:
        """An entry from what ``to_json`` made (the cache); a field of the wrong type is dropped, not trusted."""
        known = cls.__dataclass_fields__
        values: dict[str, Any] = {}
        for name, value in (data or {}).items():
            if name not in known:
                continue
            kind = known[name].type
            if kind == "str":
                values[name] = value if isinstance(value, str) else ""
            elif kind == "list[str]":
                values[name] = [item for item in value if isinstance(item, str)][:60] if isinstance(value, list) else []
            elif kind == "int | None":
                values[name] = value if isinstance(value, int) and not isinstance(value, bool) else None
            elif kind == "float | None":
                values[name] = float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
            elif kind == "int":
                values[name] = value if isinstance(value, int) and not isinstance(value, bool) else 0
            elif kind == "bool":
                values[name] = value is True
            elif kind == "dict[str, Any]":
                values[name] = value if isinstance(value, dict) else {}
        if not values.get("source") or not values.get("key") or not values.get("title"):
            raise ValueError("an entry has a source, a key and a title")
        return cls(**values)


def as_written(title: str, core: str, **more: str) -> dict[str, str]:
    """What a record wrote beside the core of its title, for the catalogue of an author's works to read what the edition
    says of itself (``Teil 1 von 2``, ``Das Hörspiel``, ``Neuausgabe``): the title as it came when the core cut
    something off (``full_title``), and the other texts given (``subtitle``, ``part``) when they say something."""
    found = {"full_title": title.strip()[:300]} if title and title.strip() != core else {}
    for name, value in more.items():
        if value and value.strip():
            found[name] = value.strip()[:300]
    return found


_DAY = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?!\d)")


def day_of(value: object) -> str:
    """The ``YYYY-MM-DD`` in a date a source gave (``2026-10-13T00:00:00Z`` gives ``2026-10-13``); empty when none."""
    found = _DAY.search(str(value or ""))
    return "-".join(found.groups()) if found else ""


__all__ = ["DRAMATIZED", "Candidate", "Query", "as_written", "day_of", "html_to_text", "isbn13", "language_code",
           "year_of"]
