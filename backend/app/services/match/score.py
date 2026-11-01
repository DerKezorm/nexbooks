"""How a book is told from the others: the points a candidate earns against what the files say, and the three answers.

Nothing here touches the database or the network; it is the rule, in one place, from the lessons of the measuring run on
167 e-books (92 percent right, German 97):

* **An ISBN beats everything.** The same ISBN is the same book.
* **The author is never matched on the last name alone** ("Silas Marlowe" is not "Hedda Thorne", and "Marlowe" is not
  every Marlowe): last name *and* first name or an initial that fits. The last name alone earns a few points and a
  person must look.
* **A volume number must fit**, also a bare one ("Club 5", "(... 04)"): Band 2 is not Band 3.
* **The language of the file decides** (``dc:language``). A recording's source is one language; for a source that
knows a
  work in many languages the title in the language of the file counts (Hardcover lists them).
* **The core of a title decides, not its advertising**: ``Eine unbeliebte Dame (Ein Krimi 1): Ein Mord ...`` is ``Eine
  unbeliebte Frau``; the article at the front is ignored and titles at least 0.9 alike are one (``names.core_title``).
* **The year in the name of the folder** ("Auris (2019)") tells volume 1 from volume 2 where nothing else does.
* **Companions and collections** ("Summary of ...", a box set) are not the book.
* **A recording is proven by its running time**: within 5 percent of a product's. The unabridged reading, the abridged
one
  and the play of one book differ by hundreds of minutes (600, 283, 359).

The answers: ``sure`` (applied by itself), ``maybe`` (a person looks, with the best proposals), ``open`` (nothing
fits). The
reasons are codes the interface puts into words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import series_names
from ..readers.names import (
    author_names,
    compare_key,
    last_first,
    person_key,
    split_series_title,
    strip_blurb,
    strip_noise,
    subtitle_key,
    subtitles_differ,
    title_is_author,
    title_keys,
)
from ..readers.names import (
    titles_alike as core_alike,
)
from ..readers.text import fold
from ..sources.model import Candidate

#: Points. A title that is the same and an author that fits are 70; with the language 80, which is "sure" for a book.
P_ISBN = 100
P_TITLE_EXACT = 40
P_TITLE_MAIN = 37
P_TITLE_PART = 28
P_TITLE_CLOSE = 15
P_AUTHOR = 30
P_AUTHOR_WEAK = 8
P_LANGUAGE = 10
P_VOLUME = 10
P_YEAR = 5
P_RUNTIME = 35
#: The running time is plausible but no proof (the file is shorter than the product by up to a fifth): a few points,
#: no "against".
P_RUNTIME_PLAUSIBLE = 15
#: A title that is the name of the series the book is in ("Auris 2" for the second book of Auris): the series speaks,
#: not another book.
P_TITLE_SERIES = 20
#: The year in the name of the folder fits (more than the year of the file: it was written by the person who sorted the
#: books), and a second source that knows the same book.
P_YEAR_NAME = 8
P_CONFIRM = 10
#: The years of the file and of a source differ by this much or more: said against the book (never decisive: a
#: printing of 2015 of a book of 1998 is the same book).
YEAR_OFF = 3
#: What a candidate with the same ISBN earns when everything else about it says it is another book.
P_ISBN_CONFLICT = 60
SURE = 80
MAYBE = 45
#: Two candidates of one source that are about as good, and are not the same book, are no basis for "sure".
AMBIGUITY = 5
#: The running time of the file against the product's: from 0.95 to 1.05 it proves the recording; from 0.80 up to
#: 0.95 it
#: is plausible (a cut, a measuring difference) and speaks neither for nor against; below, or above 1.05, it is another
#: recording (an abridged one, a part, an extended one). The running time alone is never proof (``decide``).
RUNTIME_PROOF = (0.95, 1.05)
RUNTIME_PLAUSIBLE = 0.80
#: The names of the publishers whose imprint numbers come as series from the catalogues ("Fischer 1983", "Ullstein
#: 2829"), and the number from which on a volume is an imprint's number: shared with the catalogue of an author's
#: works (``services/series_names.py``).
PUBLISHERS = series_names.PUBLISHERS
IMPRINT_NUMBER = series_names.IMPRINT_NUMBER
#: The sources whose records are one edition in one language (the others know a work in many languages).
EDITION_LEVEL = ("audible", "dnb")

COMPANION = re.compile(
    r"\b(begleitbuch|study guide|reading guide|lektureschlussel|lekturehilfe|koenigs erlauterungen|summary|"
    r"zusammenfassung|workbook|arbeitsheft|interpretation|analysis|analyse|companion|unofficial guide|trivia|"
    r"bildband|picture book|graphic novel|comic)\b")
COLLECTION = re.compile(
    r"\b(box set|boxset|collection|sammlung|gesamtausgabe|complete works|omnibus|anthology|anthologie|trilogie|"
    r"trilogy|bundle|megapack|sammelband)\b")
_MARKED_VOLUME = re.compile(
    r"(?:\bbd\.?|\bband|\bteil|\bbuch|\bbook|\bvol\.?|\bvolume|\bnr\.?|\bno\.?|#)\s*0*(\d{1,3})\b", re.IGNORECASE)
_TRAILING_VOLUME = re.compile(r"(?<=[a-z])\s+0*(\d{1,2})\s*$")
_LEADING_VOLUME = re.compile(r"^0*(\d{1,2})\s*[-.:]\s+\S")


@dataclass
class Wanted:
    """The book as the files describe it (and a person corrected it): what is asked of the sources and what a
    candidate is
    held against."""

    kind: str
    title: str
    authors: list[str] = field(default_factory=list)
    language: str = ""
    isbn: str = ""
    asin: str = ""
    subtitle: str = ""
    series: str = ""
    series_pos: float | None = None
    #: A recording's running time in seconds, 0 when unknown.
    duration: int = 0
    year: int | None = None
    original_title: str = ""
    #: The year in brackets in the name of a folder or the file ("Auris (2019)"), and the volume a name gives.
    name_year: int | None = None
    name_volume: float | None = None
    #: Other titles the book may be known by: the title of its folder when the tags say another.
    alt_titles: list[str] = field(default_factory=list)
    #: The language was not said by a file or a source but worked out (from the e-book of the work, or the library):
    #: it is asked for, and weighs less (a language that differs is no reason against the book).
    language_guess: bool = False


@dataclass
class Scored:
    candidate: Candidate
    score: int = 0
    why: list[str] = field(default_factory=list)
    #: Why this candidate cannot be the book at all (empty: it can).
    fatal: str = ""
    #: It earns no "sure": a person has to look (a weak author, an unknown one).
    capped: bool = False
    proof: bool = False
    #: The candidate is the record of the series the book is in, not of the book.
    record: bool = False
    #: The running time is plausible for the product (from 0.80 up to 0.95): a hint, no proof.
    plausible: bool = False
    #: A certain match: an ISBN or an ASIN.
    certain: bool = False
    #: What is missing to be sure of it (grey in the list) and what speaks against it though it is not fatal (red), as
    #: codes; and the numbers some of them carry (``year_off``).
    missing: list[str] = field(default_factory=list)
    against: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    state: str
    reason: str
    best: Scored | None
    #: Candidates that agree with the best one, from other sources (they add what the best lacks).
    support: list[Scored]
    ranked: list[Scored]
    #: A recording is sure as the *book*, but nothing proves which recording it is (the version, abridged or not, is
    #: not known).
    variant_unproven: bool = False


# ---- Titles -----------------------------------------------------------------------------------------------------


def _forms(title: str, subtitle: str = "", authors: list[str] | None = None) -> list[tuple[str, str]]:
    """``(key, main_key)`` of a title as it stands and as far as its series prefix goes ("Anselm Quist Bd. 1:
    Nebelchor" is also "Nebelchor"), and with its subtitle. What a shop adds to sell the book is no part of either
    (``strip_blurb``); the series prefix is looked for in the title only, never in the title with its subtitle (a
    subtitle of advertising, ``Psychothriller | ... von Nr.1-Bestsellerautor ...``, would make a "series"); and a title
    that is only the name of one of the ``authors`` is none."""
    title = strip_blurb(title) or title
    subtitle = strip_blurb(subtitle) if subtitle else ""
    forms = []
    if title and not title_is_author(title, authors):
        forms.append(title_keys(title))
        stripped = split_series_title(title, authors=authors).title
        if stripped and stripped != title and not title_is_author(stripped, authors):
            forms.append(title_keys(stripped))
        if subtitle:
            forms.append(title_keys(f"{title} {subtitle}"))
    return [form for form in dict.fromkeys(forms) if form[0]]


def _same(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """The rule for "the same title" the catalogue uses: whole titles equal, or the part before the subtitle equal
    while one
    of them has no subtitle ("Moorland" and "Moorland: Return" are two books)."""
    return a[0] == b[0] or (a[1] == b[1] and (a[0] == a[1] or b[0] == b[1]))


def _tokens(key: str) -> list[str]:
    return key.split()


def title_state(wanted_forms: list[tuple[str, str]], candidate_forms: list[tuple[str, str]],
                wanted_raw: list[str] | None = None, candidate_raw: list[str] | None = None) -> str:
    """``exact`` (the whole titles are the same), ``main`` (the same up to a subtitle only one of them has:
    "Moorland" and "Moorland: Return" cannot be told apart, a person looks), ``part`` (all words of the shorter in the
    longer), ``close`` (most words shared) or ``differs``."""
    if any(a[0] == b[0] for a in wanted_forms for b in candidate_forms):
        return "exact"
    if any(_same(a, b) for a in wanted_forms for b in candidate_forms):
        return "main"
    # The core of the titles (the part before the first bracket or colon, without the kind of book at the end, without
    # the article), the same or at least 0.9 alike: the same up to what a catalogue adds, as "main".
    if any(core_alike(a, b) for a in wanted_raw or [] for b in candidate_raw or []):
        return "main"
    best = "differs"
    for a in wanted_forms:
        for b in candidate_forms:
            ta, tb = set(_tokens(a[0])), set(_tokens(b[0]))
            if not ta or not tb:
                continue
            small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if small <= large and (len(small) >= 3 or (len(small) == 2 and len(large) <= 3)):
                return "part"
            if len(ta & tb) / len(ta | tb) >= 0.75:
                best = "close"
    return best


def titles_alike(a: Candidate, b: Candidate) -> bool:
    """Whether two candidates (of different sources) are the same book: the title the same, an author that fits, and
    no two subtitles that name two books of one series."""
    same = title_state(_forms(a.title, a.subtitle, a.authors), _forms(b.title, b.subtitle, b.authors), [a.title],
                       [b.title]) in ("exact", "main")
    return same and author_state(a.authors, b.authors) in ("match", "unknown") and not other_subtitle(a, b)


def other_subtitle(a: Candidate, b: Candidate) -> bool:
    """Whether two candidates have the same core and subtitles that name two different books."""
    return subtitles_differ(subtitle_key(a.title, a.subtitle, a.series, a.authors),
                            subtitle_key(b.title, b.subtitle, b.series, b.authors))


# ---- Authors ----------------------------------------------------------------------------------------------------


def author_state(wanted: list[str], found: list[str]) -> str:
    """``match`` (the last name and a first name or an initial that fit), ``weak`` (the last name fits and one side
    has no
    first name), ``unknown`` (a side names nobody) or ``none``."""
    wanted = [name for item in wanted for name in author_names(item)]
    found = [name for item in found for name in author_names(item)]
    if not wanted or not found:
        return "unknown"
    # A catalogue that writes an umlaut without its dots (``Muller`` for ``Müller``): the same words are the same name.
    if any(sorted(fold(a).split()) == sorted(fold(b).split()) and len(fold(a).split()) >= 2
           for a in wanted for b in found):
        return "match"
    weak = False
    for a in wanted:
        for b in found:
            if person_key(a) == person_key(b) and person_key(a):
                return "match"
            last_a, first_a = last_first(a)
            last_b, first_b = last_first(b)
            if not last_a or last_a != last_b:
                continue
            if not first_a or not first_b:
                weak = True
                continue
            from ..readers.names import authors_compatible

            if authors_compatible(a, b):
                return "match"
    return "weak" if weak else "none"


# ---- Volumes ----------------------------------------------------------------------------------------------------


def volume_in(title: str, *, marked_only: bool = False) -> float | None:
    """The volume a title names: ``Band 4``, ``Vol. 2``, ``#3``, or a bare number after the words (``Club 5``,
    ``Die Chroniken 04``). A number of three digits is no volume (``Lantern 451``). ``marked_only`` takes only a number
    that a word says is a volume (the name of a file of a recording begins or ends with the number of its part, which is
    none)."""
    text = strip_noise(title)
    match = _MARKED_VOLUME.search(text)
    if match:
        return float(match.group(1))
    if marked_only:
        return None
    match = _TRAILING_VOLUME.search(text) or _LEADING_VOLUME.match(text)
    return float(match.group(1)) if match else None


def _volumes(title: str, series_pos: float | None) -> set[float]:
    found = set()
    if series_pos is not None:
        found.add(float(series_pos))
    pos = volume_in(title)
    if pos is not None:
        found.add(pos)
    return found


def series_key(text: str) -> str:
    """A series name as it is compared: folded, without what names the form of a recording (``Reihe (gekürzt)``)."""
    return fold(strip_noise(text))


def _series_compatible(a: str, b: str) -> bool:
    fa, fb = series_key(a), series_key(b)
    return not fa or not fb or fa == fb or fa in fb or fb in fa


def junk_series(candidate: Candidate, wanted: Wanted | None = None) -> bool:
    """Whether the series a source names is no series of the book: the imprint of a publisher with its number
    (``Fischer 1983``, ``Ullstein 2829``), the name of one of the authors (a catalogue's list of an author's books), or
    a number no volume has, unless the book's own series says the same."""
    name = series_key(candidate.series)
    if not name:
        return False
    if wanted is not None and wanted.series and series_key(wanted.series) and _series_compatible(
            wanted.series, candidate.series):
        return False
    # A publisher's line (``Bastei-Lübbe-Taschenbuch``, ``Klassiker im Insel Verlag``), the name of one of the authors
    # or a number no volume has.
    return series_names.is_line(candidate.series, candidate.series_pos, authors=candidate.authors, library=True)


def series_record(wanted: Wanted, candidate: Candidate) -> bool:
    """Whether a candidate is the record of the *series* and not of a book: its title is the name of the series,
    alone or with a number (``Auris 2``, ``Elena``, ``Sturmzeit``). It is evidence for the series and the volume,
    never another book."""
    key = compare_key(candidate.title)
    base = " ".join(key.split()[:-1]) if key.split()[-1:] and key.split()[-1].isdigit() else key
    names = {compare_key(name) for name in (candidate.series, wanted.series) if name}
    if base and base in names:
        return True
    # The title of the book names the series first ("Elena - Ein Leben - Folge 2 - ..."), the record is that name alone.
    return bool(base) and len(base) >= 3 and compare_key(wanted.title).startswith(base + " ") and base == key and (
        candidate.series_pos is not None or wanted.series_pos is not None)


def _series_alone(wanted: Wanted, candidate: Candidate) -> bool:
    """Whether the title of a record of the series is the name of the series without a number, while the title of the
    book is another one that does not begin with it."""
    key = compare_key(candidate.title)
    words = key.split()
    return bool(words) and not words[-1].isdigit() and not compare_key(wanted.title).startswith(key)


# ---- Scoring ----------------------------------------------------------------------------------------------------


def _contains_word(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(fold(text)))


def _conflicts(wanted: Wanted, candidate: Candidate, title: str, authors: str, said: str, mine: str) -> list[str]:
    """The reasons to doubt a candidate that has the book's ISBN or ASIN. A companion or a collection is one on its own
    (its ISBN is then the file's mistake); otherwise it takes two of: another title, another author, another language
    (in a source whose records are one edition in one language)."""
    found = []
    if _contains_word(COMPANION, said) and not _contains_word(COMPANION, mine):
        found.append("companion")
    elif _contains_word(COLLECTION, said) and not _contains_word(COLLECTION, mine):
        found.append("collection")
    others = []
    if title == "differs":
        others.append("title_differs")
    if authors == "none":
        others.append("author_differs")
    if (wanted.language and candidate.source in EDITION_LEVEL and len(candidate.languages) == 1
            and wanted.language not in candidate.languages):
        others.append("language_differs")
    return found if found else (others if len(others) >= 2 else [])


def score(wanted: Wanted, candidate: Candidate) -> Scored:
    """The points of one candidate against the book, and why."""
    out = Scored(candidate=candidate)
    why = out.why
    isbn_hit = bool(wanted.isbn and wanted.isbn in candidate.isbns)
    asin_hit = bool(wanted.asin and wanted.asin.upper() == candidate.asin.upper())
    if isbn_hit or asin_hit:
        why.append("isbn_match" if isbn_hit else "asin_match")
    wanted_forms = _forms(wanted.title, wanted.subtitle, wanted.authors)
    if wanted.original_title:
        wanted_forms += _forms(wanted.original_title, authors=wanted.authors)
    for alt in wanted.alt_titles:
        wanted_forms += _forms(alt, authors=wanted.authors)
    candidate_forms = _forms(candidate.title, candidate.subtitle, candidate.authors)
    for text in candidate.titles.get(wanted.language, []):
        candidate_forms += _forms(text, authors=candidate.authors)
    if candidate.original_title:
        candidate_forms += _forms(candidate.original_title, authors=candidate.authors)
    # A title that is only an author's name (a title a shop's text broke, a record of a catalogue) is no title to
    # compare.
    wanted_raw = [text for text in (wanted.title, wanted.original_title, *wanted.alt_titles)
                  if text and not title_is_author(text, wanted.authors)]
    candidate_raw = [text for text in (candidate.title, *candidate.titles.get(wanted.language, []),
                                       candidate.original_title)
                     if text and not title_is_author(text, candidate.authors)]
    title = title_state(wanted_forms, candidate_forms, wanted_raw, candidate_raw)
    # The same core and two subtitles that name two books (``Reihe: Erster Band``, ``Reihe: Zweiter Band``): another
    # volume, unless the volume, the ISBN or the running time says it is this one (see the end).
    other_volume = title == "main" and subtitles_differ(
        subtitle_key(wanted.title, wanted.subtitle, wanted.series, wanted.authors),
        subtitle_key(candidate.title, candidate.subtitle, candidate.series, candidate.authors))
    if title == "differs" and series_record(wanted, candidate):
        title = "series"
        out.record = True
    authors = author_state(wanted.authors, [*candidate.authors, *candidate.author_aliases])
    said = f"{candidate.title} {candidate.subtitle}"
    mine = f"{wanted.title} {wanted.subtitle}"
    # What says that a candidate with the book's ISBN is another book (the ISBN in a file is sometimes a stranger's).
    conflicts = _conflicts(wanted, candidate, title, authors, said, mine) if isbn_hit or asin_hit else []
    if (isbn_hit or asin_hit) and not conflicts:
        out.score, out.certain = P_ISBN, True
    if not out.certain:
        if title == "exact":
            out.score += P_TITLE_EXACT
            why.append("title_exact")
        elif title == "main":
            out.score += P_TITLE_MAIN
            why.append("title_main")
        elif title == "part":
            out.score += P_TITLE_PART
            why.append("title_part")
        elif title == "close":
            out.score += P_TITLE_CLOSE
            # A title that only shares most of its words is no ground to be sure, whatever the rest says (the running
            # time of another book can fit too).
            out.capped = True
            why.append("title_close")
        elif title == "series":
            out.score += P_TITLE_SERIES
            why.append("title_series")
        else:
            out.fatal = "title_differs"
    if not out.certain:
        if authors == "match":
            out.score += P_AUTHOR
            why.append("author_match")
        elif authors == "weak":
            out.score += P_AUTHOR_WEAK
            out.capped = True
            why.append("author_weak")
        elif authors == "unknown":
            out.capped = True
            why.append("author_unknown")
        elif not out.fatal:
            out.fatal = "author_differs"
    # Companions and collections: when the candidate says so and the book does not.
    if not out.certain and not out.fatal:
        if _contains_word(COMPANION, said) and not _contains_word(COMPANION, mine):
            out.fatal = "companion"
        elif _contains_word(COLLECTION, said) and not _contains_word(COLLECTION, mine):
            out.fatal = "collection"
    # The volume.
    mine_volumes = _volumes(wanted.title, wanted.series_pos) | (
        {float(wanted.name_volume)} if wanted.name_volume is not None else set())
    their_volumes = _volumes(candidate.title, candidate.series_pos if _series_compatible(
        wanted.series, candidate.series) and not junk_series(candidate, wanted) else None)
    if mine_volumes and their_volumes:
        if mine_volumes & their_volumes:
            if not out.certain:
                out.score += P_VOLUME
                why.append("volume_ok")
        elif not out.certain and not out.fatal:
            out.fatal = "volume_differs"
    elif their_volumes and not mine_volumes and not out.certain:
        out.missing.append("no_volume")
    # The language of the file decides; one that was only worked out weighs half, and is no reason against a book.
    if wanted.language and candidate.languages:
        if wanted.language in candidate.languages:
            if not out.certain:
                out.score += P_LANGUAGE // 2 if wanted.language_guess else P_LANGUAGE
            why.append("language_ok")
        elif wanted.language_guess:
            if wanted.kind == "audio" and candidate.source in EDITION_LEVEL and len(candidate.languages) == 1 \
                    and not out.certain:
                # A recording in another language than the one worked out: another recording, or the guess is wrong.
                # A person looks.
                out.capped = True
                why.append("language_missing")
        elif candidate.source in EDITION_LEVEL and len(candidate.languages) == 1:
            if not out.certain and not out.fatal:
                out.fatal = "language_differs"
        elif not out.certain:
            # A work that is not known in this language yet: it may well be the book, but a person should see.
            out.capped = True
            why.append("language_missing")
    if not out.certain:
        if wanted.name_year and candidate.year and abs(wanted.name_year - candidate.year) <= 1:
            out.score += P_YEAR_NAME
            why.append("year_name_ok")
        elif wanted.year and candidate.year and abs(wanted.year - candidate.year) <= 1:
            out.score += P_YEAR
            why.append("year_ok")
        else:
            reference = wanted.name_year or wanted.year
            # A recording comes out after its book: a source's year later than the recording's year is wrong, an earlier
            # one is the book's.
            off = False
            if reference and candidate.year:
                gap = candidate.year - reference
                off = gap >= YEAR_OFF if wanted.kind == "audio" else abs(gap) >= YEAR_OFF
            if off and reference and candidate.year:
                out.against.append("year_off")
                out.detail["year_off"] = {"file": reference, "source": candidate.year}
        # What would make it surer, said as what the file lacks.
        if not wanted.year and not wanted.name_year:
            out.missing.append("no_year")
        if not wanted.isbn and wanted.kind == "ebook":
            out.missing.append("no_isbn")
        if not wanted.language:
            out.missing.append("language_unknown")
        if not wanted.authors:
            out.missing.append("no_author")
    # The running time of a recording.
    if wanted.kind == "audio" and wanted.duration > 0 and candidate.runtime_min:
        ratio = wanted.duration / (candidate.runtime_min * 60)
        if RUNTIME_PROOF[0] <= ratio <= RUNTIME_PROOF[1]:
            out.proof = True
            out.score += P_RUNTIME if not out.certain else 0
            why.append("runtime_ok")
        elif RUNTIME_PLAUSIBLE <= ratio < RUNTIME_PROOF[0]:
            out.plausible = True
            out.score += P_RUNTIME_PLAUSIBLE if not out.certain else 0
            why.append("runtime_plausible")
        else:
            why.append("runtime_differs")
            out.against.append("runtime_differs")
    elif wanted.kind == "audio" and wanted.duration > 0 and not out.certain:
        out.missing.append("no_runtime")
    # The record of a series that carries the name of the series alone (``Moorland``, no volume) for a book with a title
    # of its own: evidence for the series, never "sure" of the book (a shop files the series at books it is not).
    if out.record and not out.certain and _series_alone(wanted, candidate):
        out.capped = True
        out.against.append("series_only")
    if other_volume and not out.certain and not out.fatal and not out.proof and "volume_ok" not in why:
        out.capped = True
        out.against.append("subtitle_differs")
    if out.certain:
        out.fatal, out.capped = "", False
    elif conflicts:
        # The same ISBN, and yet another book by everything else: a person looks, and the matching never takes it alone.
        reasons = [*conflicts, *([out.fatal] if out.fatal and out.fatal not in conflicts else [])]
        out.fatal, out.capped, out.score = "", True, P_ISBN_CONFLICT
        why.append("isbn_conflict")
        why.extend(reason for reason in reasons if reason not in why)
    return out


def _confirms(a: Scored, b: Scored) -> bool:
    """Whether ``b`` is the same book as ``a`` told by another source: the title alike and the author the same."""
    return (a.candidate.source != b.candidate.source and _same_book(a, b)
            and author_state(a.candidate.authors, b.candidate.authors) == "match")


def _same_book(a: Scored, b: Scored) -> bool:
    """Whether two scored candidates tell of one book: the titles alike, or one of them is the record of the series
    (``Auris 2``), which speaks for every book of the series and against none. Two with the same core and subtitles
    that name two books are two books, whatever else (``titles_alike``): two such that are about as good are
    ``ambiguous``, never one."""
    if a.record or b.record:
        return True
    return titles_alike(a.candidate, b.candidate)


def distinct(candidates: list[Candidate]) -> list[Candidate]:
    """The candidates without the ones a source lists twice (the same title, authors, year, volume and running time): a
    record that is there twice is one voice, the better known of them."""
    where: dict[tuple[object, ...], int] = {}
    out: list[Candidate] = []
    for c in candidates:
        key = (c.source, compare_key(c.title), tuple(sorted(person_key(a) for a in c.authors)), c.runtime_min,
               c.series_pos, c.variant, c.year, tuple(c.isbns[:1]))
        if key in where:
            if c.weight > out[where[key]].weight:
                out[where[key]] = c
            continue
        where[key] = len(out)
        out.append(c)
    return out


def translation_of(wanted: Wanted, ranked: list[Scored]) -> Scored | None:
    """A candidate that is probably the book in another language: another title, the same author, and the series and
    volume or the year (one year apart at most) the same. Never more than a hint for a person: a book with another title
    is not the book on the strength of its author."""
    for s in ranked:
        if s.fatal != "title_differs":
            continue
        c = s.candidate
        if author_state(wanted.authors, c.authors) != "match":
            continue
        if wanted.language and c.languages and wanted.language in c.languages and len(c.languages) == 1:
            continue
        mine = _volumes(wanted.title, wanted.series_pos) | (
            {float(wanted.name_volume)} if wanted.name_volume is not None else set())
        series_fits = bool(wanted.series and c.series and _series_compatible(wanted.series, c.series)
                           and not junk_series(c, wanted) and c.series_pos is not None and c.series_pos in mine)
        reference = wanted.name_year or wanted.year
        year_fits = bool(reference and c.year and abs(reference - c.year) <= 1)
        if series_fits or year_fits:
            return s
    return None


def decide(wanted: Wanted, candidates: list[Candidate]) -> Decision:
    """The three answers for a book from the candidates the sources gave."""
    candidates = distinct(candidates)
    scored = [score(wanted, c) for c in candidates]
    # A second source that knows the same book is worth points; without one, "only one source" is said.
    usable_first = [s for s in scored if not s.fatal]
    for s in usable_first:
        if s.certain:
            continue
        if any(_confirms(s, other) for other in usable_first if other is not s):
            s.score += P_CONFIRM
            s.why.append("second_source")
        else:
            s.missing.append("one_source")
    ranked = sorted(scored, key=lambda s: (s.fatal != "", -s.score, -s.candidate.weight, s.candidate.source,
                                           s.candidate.key))
    usable = [s for s in ranked if not s.fatal]
    if not candidates:
        return Decision("open", "nothing_found", None, [], [])
    if not usable:
        translated = translation_of(wanted, ranked)
        if translated is not None:
            return Decision("maybe", "possible_translation", translated, [], ranked)
        return Decision("open", "nothing_fits", None, [], ranked)
    best = usable[0]
    support = [s for s in usable[1:] if s.candidate.source != best.candidate.source and s.score >= MAYBE + 15
               and _same_book(best, s)]
    sureish = [s for s in usable if s.score >= SURE and not s.capped]
    reason = ""
    state = "open"
    if best.score >= MAYBE or best.certain:
        state = "maybe"
    if best.score >= SURE and not best.capped:
        state = "sure"
    unproven = False
    # The book is known only by the core of its title, and another record has the same core with a subtitle that names
    # another book (two volumes of one series): which one it is, nothing says.
    core_only = "title_main" in best.why and not (best.certain or best.proof or "volume_ok" in best.why)
    if state == "sure" and core_only and any(
            other is not best and "title_main" in other.why and other_subtitle(best.candidate, other.candidate)
            for other in usable):
        state, reason = "maybe", "ambiguous"
    if state == "sure" and wanted.kind == "audio" and not (best.proof or best.certain):
        if best.plausible or "second_source" in best.why:
            # The *book* is sure (the running time is plausible, or a second source tells the same book): which
            # recording it is (abridged or not) is not proven, and the edition says so.
            unproven = True
        else:
            state, reason = "maybe", "runtime_unproven"
    if state == "sure":
        # Another source holds a different book that is as good: the sources disagree.
        for other in sureish[1:]:
            if other.candidate.source != best.candidate.source and not _same_book(best, other) \
                    and not other.certain:
                state, reason = "maybe", "sources_disagree"
                break
        else:
            # Two records of one source that differ and are about as good: it is not clear which is the book.
            for other in usable[1:]:
                if other.candidate.source == best.candidate.source and best.score - other.score <= AMBIGUITY \
                        and not _same_book(best, other) and not best.certain:
                    state, reason = "maybe", "ambiguous"
                    break
    if state == "sure" and wanted.kind == "ebook" and not best.certain and "second_source" not in best.why:
        # An e-book is sure by the ISBN of its file, or when two sources tell the same book: one catalogue alone, with
        # no ISBN, is a proposal (its record may be another book of the author with a title alike).
        state, reason = "maybe", "one_source"
    if state == "maybe" and not reason:
        if "isbn_conflict" in best.why:
            reason = "isbn_conflict"
        elif best.capped and "subtitle_differs" in best.against:
            reason = "subtitle_differs"
        elif best.capped and "series_only" in best.against:
            reason = "series_only"
        elif best.capped and "author_weak" in best.why:
            reason = "author_weak"
        elif best.capped and "author_unknown" in best.why:
            reason = "author_unknown"
        elif best.capped and "language_missing" in best.why:
            reason = "language_missing"
        elif wanted.kind == "audio" and not best.proof:
            reason = "runtime_unproven"
        else:
            reason = "low_score"
    if state == "open":
        reason = "low_score"
    return Decision(state, reason if state != "sure" else "", best, support if state == "sure" else [], ranked,
                    variant_unproven=unproven and state == "sure")
