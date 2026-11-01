"""From the records of the sources to the works of an author: one work for the editions, translations and recordings
of one book, with its title and year in each language.

The rules that carry (measured):

1. **A work is a group over the core title** (``readers.names.compare_key``: the title up to its first bracket,
   colon or dash, without ``Roman``, ``A Novel``, ``ungekürzt``, without the article in front; ``&``, ``and``,
   ``und``, ``et`` and ``och`` left out, so that ``Salt & Ashes`` is ``Salt and Ashes``), records whose titles
   are alike (``keys_alike``, 0.9) in one language are one work. A different volume number is never alike.
2. **Across languages** a work is held together by what the sources know: the same work at Open Library or Hardcover
   (their editions in several languages), the original title a translation names (in the language it names; a record
   that names no language names the author's), the title of Open Library's work behind an edition (a work is named in
   the language it was written in), and the same title in two languages when it says something by itself
   (``distinct_title_key``).
3. **ISBNs join two works only as a pair from two sources**: two numbers the one work has from one source and the other
   from another (``Glockenschlag`` at the German library and the German editions Hardcover files under ``The
   Ninth Bell``). A single number never does: measured against the real German library, records that list ISBNs of other
   books (sets, boxes, wrong entries) joined a prolific author's novels into one work of 180 records. A record of a
   library that lists more than two numbers gives none (a set or a mistake).
4. **Two sources must know a work.** One is enough when only one source answered for a language of the work (the
   Dutch library alone knows the Dutch books of a Dutch author).
5. **A series title** (``The Dark Tower: The Gunslinger``): a name before the colon that comes with at least three
   different titles after it is the series, the part after it the title; with two, when both titles are known to two
   sources each and none is a word of an edition (``Moorland: Das Haus``, ``Moorland: Der Wald``).
6. **Pen names**: the records a source gave for another name of the author make the work come out under that name.
7. **Series** (``series_names.py``): a publisher's line is no series; two spellings of a series are one, and so are
   two names that the same work carries at the same place (``Kommissar Lund`` and ``Inspector
   Lund``); the series a work-level source names (Hardcover, Audible) goes before a library's.
8. **A name two people share** (``people.py``): a work that no number of a source proves the author's is not sure.
9. **What an edition says of itself** is read before the core of its title is taken (``marks_of``): ``Teil 1 von 2``,
   ``1/2``, ``Das Hörspiel``, ``Zwei Romane in einem Band``, ``Neuausgabe``, ``Neu übersetzt von``, a library's
   subtitle and the number of a part, a shop's word for a play. The core groups the records into works; what it cut off
   stays with the work in that language as its marks (``Edition.marks``), so that watching an author never takes a
   part, a play or a new edition for a new book (``watch.doubt_of``). Two records of one language that name different
   translators (``aus dem Englischen von``) make the mark of a new translation; the latest day or year a record of the
   language gives is kept beside the first (``Edition.latest``: a paperback or a new translation announced).

**Cost.** A prolific author is a thousand records and more. Every title is made a key once, and keys are compared only
within the same numbers and a length that can reach the threshold (measured: 20 s down to 1.4 s for 1232 records). A
large catalogue is put together in a helper process (``compute.py``); in place, the work gives the processor up now
and then (``time.sleep(0)``).
"""

from __future__ import annotations

import bisect
import hashlib
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from .. import book_words, series_names
from ..readers.names import compare_key, core_title, distinct_title_key, keys_alike
from ..readers.text import fold
from ..sources.model import Entry
from . import kinds, people

#: The order in which a record speaks for its work (for the book of the sources a wish goes through, and its cover).
SPEAKER_ORDER = {"hardcover": 0, "openlibrary": 1, "audible": 2, "dnb": 3, "bnf": 4, "libris": 5, "kb": 6, "nb": 7}
MAX_ISBNS = 60
MAX_REFS = 30
THRESHOLD = 0.9
_SERIES_NUMBER = re.compile(r"\b(i{1,3}|iv|v|vi{0,3}|ix|x|\d+|book|band|tome|vol|volume|teil|livre)\b")
_DIGITS = re.compile(r"\d+")
#: Words that join two names in a title and are written in many ways: left out of the key a work is grouped by.
JOINING_WORDS = frozenset({"and", "und", "et", "och"})
#: The sources that know a work in many languages (their ISBNs are a work's editions, not one book's).
WORK_LEVEL = ("hardcover", "openlibrary")
#: The parts after a colon that name an edition or the form, not a volume (``Moorland: Roman``, ``Glockenschlag:
#: Illustrierte Ausgabe``).
_EDITION_WORDS = re.compile(
    r"\b(roman|novel|novella|novelle|thriller|krimi|kriminalroman|ausgabe|edition|edicion|edizione|illustriert\w*|"
    r"illustrated|unabridged|ungekurzt|gekurzt|abridged|horbuch|audiobook|sonderausgabe|neuausgabe|taschenbuch|"
    r"paperback|hardcover|classic|klassiker|lesung|horspiel|erzahlung|stories|geschichten)\b")
#: How many comparisons the grouping makes before it gives the processor up for a moment.
YIELD_EVERY = 400


@dataclass
class Edition:
    """A work in one language."""

    title: str
    year: int | None = None
    released: str = ""
    ebook: bool = False
    audio: bool = False
    sources: list[str] = field(default_factory=list)
    #: What the records of this language say of the edition beside the core of their title (``marks_of``):
    #: ``audio:play``, ``text:part:1:2``, ``text:reissue``. Left out of the JSON when there is none.
    marks: list[str] = field(default_factory=list)
    #: The latest day (or year, when no record gives a day as late) an edition of this language comes out, when it is
    #: not the first (``released``): a later edition. Left out of the JSON when there is none.
    latest: str = ""

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"title": self.title, "year": self.year, "released": self.released,
                                  "ebook": self.ebook, "audio": self.audio, "sources": self.sources}
        if self.marks:
            out["marks"] = self.marks
        if self.latest:
            out["latest"] = self.latest
        return out


@dataclass
class Work:
    key: str
    entries: list[Entry]
    titles: dict[str, Edition]
    kind: str = "roman"
    why: str = ""
    pen: str = ""
    series: str = ""
    series_pos: float | None = None
    year: int | None = None
    orig_language: str = ""
    sources: list[str] = field(default_factory=list)
    isbns: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    pages: int | None = None
    minutes: int | None = None
    weight: int = 0

    @property
    def speaker(self) -> Entry:
        return min(self.entries, key=lambda e: (SPEAKER_ORDER.get(e.source, 9), -e.weight, e.key))

    def title_in(self, language: str) -> str:
        edition = self.titles.get(language) or self.titles.get(self.orig_language)
        return edition.title if edition else next(iter(self.titles.values())).title


# ---- Keys --------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=50000)
def title_key(title: str) -> str:
    """The key a work is grouped by: ``compare_key`` without the words that join two names (see the top)."""
    key = compare_key(title)
    words = [word for word in key.split() if word not in JOINING_WORDS]
    return " ".join(words) if words else key


def key_of(entry: Entry) -> str:
    return title_key(entry.title)


class _Pause:
    """Gives the processor up after every ``YIELD_EVERY`` comparisons (the grouping runs beside the requests of the
    server in the same process)."""

    def __init__(self) -> None:
        self.count = 0

    def tick(self) -> None:
        self.count += 1
        if self.count % YIELD_EVERY == 0:
            time.sleep(0)


class _Near:
    """The keys of one language by their numbers and their length: the ones a key can be alike to."""

    def __init__(self, keys: list[str]) -> None:
        self.by: dict[tuple[str, ...], tuple[list[int], list[str]]] = {}
        buckets: dict[tuple[str, ...], list[str]] = {}
        for key in keys:
            buckets.setdefault(tuple(_DIGITS.findall(key)), []).append(key)
        for digits, items in buckets.items():
            items.sort(key=lambda key: (len(key), key))
            self.by[digits] = ([len(key) for key in items], items)

    def near(self, key: str) -> list[str]:
        lengths, items = self.by.get(tuple(_DIGITS.findall(key)), ([], []))
        # The ratio is at most twice the shorter length over both: lengths beyond these can never reach the threshold.
        low = int(len(key) * THRESHOLD / (2 - THRESHOLD))
        high = int(len(key) * (2 - THRESHOLD) / THRESHOLD) + 1
        return items[bisect.bisect_left(lengths, low):bisect.bisect_right(lengths, high)]

    def pairs(self, pause: _Pause) -> list[tuple[str, str]]:
        """Every pair of keys that are alike (each pair once)."""
        found: list[tuple[str, str]] = []
        for lengths, items in self.by.values():
            for index, key in enumerate(items):
                high = len(key) * (2 - THRESHOLD) / THRESHOLD
                stop = bisect.bisect_right(lengths, int(high) + 1, index + 1)
                for other in items[index + 1:stop]:
                    pause.tick()
                    if keys_alike(key, other, THRESHOLD):
                        found.append((key, other))
        return found


# ---- Series titles -----------------------------------------------------------------------------------------------


def fix_series_titles(entries: list[Entry]) -> None:
    """``Reihe: Titel``: when the part before the colon comes with at least three different parts after it, the part
    after it is the title and the part before it the series (where the record named none). Two parts are enough when
    both are known to two sources each, neither names an edition or the form, and no record is the series' name
    alone (that would be a book of that name with two subtitles)."""
    seen: dict[str, dict[str, set[str]]] = {}
    split: dict[int, tuple[str, str]] = {}
    alone: set[str] = set()
    for index, entry in enumerate(entries):
        if ":" not in entry.title:
            alone.add(" ".join(_SERIES_NUMBER.sub(" ", fold(entry.title)).split()))
            continue
        before, after = entry.title.split(":", 1)
        lead = " ".join(_SERIES_NUMBER.sub(" ", fold(before)).split())
        if lead and after.strip():
            seen.setdefault(lead, {}).setdefault(compare_key(after), set()).add(entry.source)
            split[index] = (lead, after.strip())

    def is_series(lead: str) -> bool:
        rest = seen[lead]
        if len(rest) >= 3:
            return True
        return len(rest) == 2 and lead not in alone and all(
            len(sources) >= 2 and not _EDITION_WORDS.search(part) for part, sources in rest.items())

    series = {lead for lead in seen if is_series(lead)}
    for index, (lead, after) in split.items():
        # (``Reihe: Gesamtausgabe`` is the box of the series, not a volume: its title stays whole.)
        if lead in series and compare_key(after) and not kinds.BOX_TITLE.search(after):
            entry = entries[index]
            if not entry.series:
                entry.series = entry.title.split(":", 1)[0].strip()
                # (The title names the series itself: a series without a place in it, but no publisher's line.)
                entry.signals = {**entry.signals, "series_of_title": True}
            entry.title = after


# ---- Grouping ----------------------------------------------------------------------------------------------------


class _Groups:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def join(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


def originals_of(entry: Entry, author_language: str,
                 own_language_works: set[str] | None = None) -> list[tuple[str, str]]:
    """The titles a record names for its book in the original language, as ``(key, language)``: the original title of
    a translation (in the language it says, else the author's), and the title of Open Library's work behind an edition
    in another language (a work is named in the language it was written in), when that work has no edition in the
    author's language among the records (then the work holds them together already) and its title is whole (a title
    the core would cut, ``Reihe: Band``, would name the series)."""
    found: list[tuple[str, str]] = []
    if entry.original_title:
        language = entry.original_language or author_language
        if language and language != entry.language:
            found.append((title_key(entry.original_title), language))
    work_title = entry.signals.get("work_title") if entry.source == "openlibrary" else None
    if (isinstance(work_title, str) and author_language and author_language != entry.language
            and entry.work_key not in (own_language_works or set()) and core_title(work_title) == work_title.strip()):
        key = title_key(work_title)
        if key and key != key_of(entry):
            found.append((key, author_language))
    return [(key, language) for key, language in found if key]


def _usable_isbns(entry: Entry) -> list[str]:
    if entry.source in WORK_LEVEL:
        return entry.isbns
    return entry.isbns if len(entry.isbns) <= 2 else []


def _places(members: list[Entry]) -> dict[str, set[float]]:
    found: dict[str, set[float]] = {}
    for entry in members:
        if entry.series and entry.series_pos is not None and not series_names.is_line(
                entry.series, entry.series_pos, library=entry.source in series_names.LIBRARIES):
            found.setdefault(series_names.key(entry.series), set()).add(entry.series_pos)
    return found


def _places_conflict(a: dict[str, set[float]], b: dict[str, set[float]]) -> bool:
    """Whether two groups are other volumes of one series (their places in it differ)."""
    return any(name in b and not (places & b[name]) for name, places in a.items())


def group(entries: list[Entry], author_language: str = "") -> list[list[Entry]]:
    """The records in groups, one for each work (rules 1 to 3 above)."""
    groups = _Groups(len(entries))
    keys = [key_of(entry) for entry in entries]
    first: dict[tuple[str, ...], int] = {}
    pause = _Pause()

    def link(name: tuple[str, ...], index: int) -> None:
        if name in first:
            groups.join(first[name], index)
        else:
            first[name] = index

    by_language: dict[str, dict[str, list[int]]] = {}
    for index, entry in enumerate(entries):
        if not keys[index]:
            continue
        if entry.work_key:
            link(("work", entry.source, entry.work_key), index)
        by_language.setdefault(entry.language, {}).setdefault(keys[index], []).append(index)
        if distinct_title_key(keys[index]):
            link(("title", keys[index]), index)
    near = {language: _Near(list(found)) for language, found in by_language.items()}
    # The same title in one language: equal keys first, then alike ones (each pair of different keys once).
    for language, found in by_language.items():
        for members in found.values():
            for other in members[1:]:
                groups.join(members[0], other)
        for a, b in near[language].pairs(pause):
            groups.join(found[a][0], found[b][0])
    # A record names its original title.
    own_language_works = {entry.work_key for entry in entries
                          if entry.source == "openlibrary" and entry.work_key and entry.language == author_language}
    for index, entry in enumerate(entries):
        for wanted, language in originals_of(entry, author_language, own_language_works):
            names = by_language.get(language)
            if not names:
                continue
            if wanted in names:
                groups.join(index, names[wanted][0])
                continue
            for name in near[language].near(wanted):
                pause.tick()
                if keys_alike(name, wanted, THRESHOLD):
                    groups.join(index, names[name][0])
                    break
    # Two ISBNs from two sources (rule 3).
    numbers: dict[str, list[tuple[int, str]]] = {}
    for index, entry in enumerate(entries):
        if keys[index]:
            for isbn in _usable_isbns(entry):
                numbers.setdefault(isbn, []).append((index, entry.source))
    roots = {index: groups.find(index) for index in range(len(entries)) if keys[index]}
    shared: dict[tuple[int, int], set[str]] = {}
    for isbn, holders in numbers.items():
        by_root: dict[int, set[str]] = {}
        for index, source in holders:
            by_root.setdefault(roots[index], set()).add(source)
        if len(by_root) < 2 or len(by_root) > 3:
            # (A number in more than three works is a set's or a mistake.)
            continue
        for a, from_a in by_root.items():
            for b, from_b in by_root.items():
                if a < b and any(x != y for x in from_a for y in from_b):
                    shared.setdefault((a, b), set()).add(isbn)
    if shared:
        members_of: dict[int, list[Entry]] = {}
        for index, root in roots.items():
            members_of.setdefault(root, []).append(entries[index])
        for (a, b), isbns in sorted(shared.items()):
            if len(isbns) >= 2 and not _places_conflict(_places(members_of[a]), _places(members_of[b])):
                groups.join(a, b)
    out: dict[int, list[Entry]] = {}
    for index, entry in enumerate(entries):
        if keys[index]:
            out.setdefault(groups.find(index), []).append(entry)
    return list(out.values())


def kept(members: list[Entry], answered: dict[str, set[str]]) -> bool:
    """Rule 4: two sources know the work, or the one that does is the only one that answered for a language of it."""
    sources = {entry.source for entry in members}
    if len(sources) >= 2:
        return True
    only = next(iter(sources))
    return any(answered.get(entry.language) == {only} for entry in members)


# ---- One work ----------------------------------------------------------------------------------------------------


#: The sources whose way of writing a title (its capitals, its article) is the book's: a library and a shop copy it from
#: the book; Open Library's records are typed in by many hands (``Der Alte Mann Und das Meer``).
_SPELLING_ORDER = {"dnb": 0, "bnf": 0, "libris": 0, "kb": 0, "nb": 0, "audible": 1, "hardcover": 2, "openlibrary": 3}


def _spelling(entry: Entry) -> str:
    """How a record writes the title: Open Library and Hardcover as the book has it (``Reihe: Gesamtausgabe``), short
    as their titles are; a shop and a library only the core (they add advertising)."""
    if entry.source in ("openlibrary", "hardcover") and len(entry.title.split()) <= 10 and "(" not in entry.title:
        return entry.title
    return core_title(entry.title) or entry.title


def _title(members: list[Entry]) -> str:
    """The title most records of a language carry (its core), written the way most of them write it: with its article
    and its capitals (``Das Versprechen``, ``Der alte Mann und das Meer``), not the shortest or the longest spelling.
    Spellings that differ in their capitals only are one; of them, the one a library or a shop writes."""
    counts = Counter(key_of(entry) for entry in members if key_of(entry))
    best = counts.most_common(1)[0][0] if counts else ""
    chosen = [entry for entry in members if key_of(entry) == best]
    if not chosen:
        return members[0].title
    votes: Counter[str] = Counter()
    #: Each way of writing it: how well its best source writes titles, and how many records write it so.
    hands: dict[str, dict[str, tuple[int, int]]] = {}
    for entry in chosen:
        text = " ".join(_spelling(entry).split())
        votes[text.casefold()] += 1
        quality = 3 - _SPELLING_ORDER.get(entry.source, 3)
        known = hands.setdefault(text.casefold(), {}).get(text, (0, 0))
        hands[text.casefold()][text] = (max(known[0], quality), known[1] + 1)

    def order(folded: str) -> tuple[int, int, str]:
        # (Most records first; then the one a library or a shop writes so: ``Das Versprechen``.)
        return (-votes[folded], -max(quality for quality, _count in hands[folded].values()), folded)

    folded = min(votes, key=order)
    # (Capitals throughout are a way of printing a cover, not of writing a title: another spelling first; a short title
    # that is written so everywhere is written with capitals at the start of its words, ``BECOMING`` as ``Becoming``.)
    text = max(hands[folded].items(), key=lambda item: (not item[0].isupper(), item[1], item[0]))[0]
    if text.isupper() and len(text.split()) <= 3:
        return " ".join(word[:1] + word[1:].lower() for word in text.split())
    return text


#: A year one record alone gives, at most this many years before the first year two records agree on, is a slip.
YEAR_SLIP = 10


def first_year_of(year: int | None, members: list[Entry]) -> int | None:
    """The year a work first came out: ``year`` (the earliest the records give), unless one record alone gives it
    and two others agree on a year a few years later (``YEAR_SLIP``): a record of Open Library that says 2018 for a
    book of 2025. A classic whose first year one record knows, and whose other records are editions of a century
    later, keeps it."""
    years = sorted(entry.year for entry in members if entry.year)
    if year is None or not years:
        return year
    if sum(1 for found in years if abs(found - year) <= 1) >= 2:
        return year
    agreed = next((found for found in years if found > year and sum(1 for other in years if abs(other - found) <= 1)
                   >= 2), None)
    return agreed if agreed is not None and agreed - year <= YEAR_SLIP else year


#: The most marks one edition keeps.
MAX_MARKS = 12
#: Who made it, as a library's record says (``bearbeitet von``, ``retold by``): another hand made a book of the work.
_ADAPTED_BY = re.compile(r"(?<![a-z])(?:bearbeit\w* (?:von|durch)|nacherz(?:a|ae)hlt (?:von|durch)|adapted by|"
                         r"retold by|adaptation de)(?![a-z])")
#: A play by its credits (``Hörspielbearbeitung``, ``Drehbuch``).
_PLAY_BY = re.compile(r"(?<![a-z])(?:drehbuch|\w*h(?:o|oe)rspiel\w*|funkbearbeitung)(?![a-z])")
#: Who translated it, as a library's record says (``aus dem Englischen von``, ``Deutsch von``, ``übersetzt von``,
#: ``translated by``): the names up to the next part of the credits.
_TRANSLATED_BY = re.compile(r"(?<![a-z])(?:aus dem \w+(?: (?:u|ue)bersetzt| (?:u|ue)bertragen)? von|deutsch von|"
                            r"(?:u|ue)bersetzt (?:aus dem \w+ )?von|(?:u|ue)bersetzung(?: aus dem \w+)?(?: von)?|"
                            r"translated (?:from the \w+ )?by|traduit de l ?\w+ par)\s+([^;/]+)")
_NAME_LINKS = re.compile(r"\s*(?:,|&|\bund\b|\band\b|\bet\b)\s*")


def marks_of(entry: Entry) -> list[str]:
    """What a record says of its edition (rule 9), read in its title as it came, its subtitle and the number of its
    part, before the core is taken: ``adaptation``, ``companion``, ``edition`` (``book_words.not_the_work``),
    ``play``, ``part:<n>:<m>`` (``m`` empty when the record does not say how many parts there are), ``box``,
    ``reissue``, ``translation``; each with the form of the record in front (``audio:play``, ``text:part:1:2``)."""
    texts = [entry.title, *(entry.signals.get(name) for name in ("full_title", "subtitle", "part"))]
    found: list[str] = []

    def add(code: str) -> None:
        if code not in found:
            found.append(code)

    for text in dict.fromkeys(item for item in texts if isinstance(item, str) and item.strip()):
        code = book_words.not_the_work(text)
        if code:
            add(code)
        if book_words.dramatized_word(text):
            add("play")
        part = book_words.part_of(text) or book_words.bare_part(text)
        if part is not None:
            add(f"part:{part[0]}:{part[1] if part[1] is not None else ''}")
        if book_words.box_word(text) or book_words.more_bundle_word(text) or book_words.package_word(text):
            add("box")
        if book_words.reissue_word(text):
            add("reissue")
        if book_words.translation_word(text):
            add("translation")
    if entry.variant == "dramatized":
        add("play")
    credits = entry.signals.get("credits")
    if isinstance(credits, str) and credits:
        if _ADAPTED_BY.search(f" {fold(credits)} "):
            add("adaptation")
        if _PLAY_BY.search(f" {fold(credits)} "):
            add("play")
    form = "audio" if entry.form == "audio" else "text"
    return [f"{form}:{code}" for code in found]


def translators_of(entry: Entry) -> frozenset[str]:
    """The people a record names as its translators (folded), from its credits; empty when it names none."""
    credits = entry.signals.get("credits")
    if not isinstance(credits, str) or not credits:
        return frozenset()
    found = _TRANSLATED_BY.search(f" {fold(credits)} ")
    if found is None:
        return frozenset()
    return frozenset(name.strip(" .") for name in _NAME_LINKS.split(found.group(1)) if len(name.strip(" .")) > 2)


def _translated_twice(members: list[Entry]) -> bool:
    """Two records of one language whose translators are not the same people: two translations."""
    named = list({names for entry in members if (names := translators_of(entry))})
    return any(not (a & b) for index, a in enumerate(named) for b in named[index + 1:])


def _latest(members: list[Entry], first_day: str) -> str:
    """The latest day an edition of the language comes out (the records' days and Hardcover's latest edition), or
    the latest year when a record gives a later year than every day; empty when that is the first."""
    days = [day for entry in members for day in (entry.released, entry.signals.get("last_day"))
            if isinstance(day, str) and len(day) == 10]
    years = [entry.year for entry in members if isinstance(entry.year, int)]
    last = max(days) if days else ""
    if years and max(years) > (int(last[:4]) if last[:4].isdigit() else 0):
        last = str(max(years))
    first = first_day or (str(min(years)) if years else "")
    return last if last and last != first else ""


def _edition(members: list[Entry]) -> Edition:
    years = [entry.year for entry in members if entry.year]
    days = [entry.released for entry in members if entry.released]
    marks = list(dict.fromkeys(mark for entry in members for mark in marks_of(entry)))
    if _translated_twice(members):
        marks.append("text:translation")
    marks = list(dict.fromkeys(marks))[:MAX_MARKS]
    first_day = min(days) if days else ""
    return Edition(title=_title(members), year=min(years) if years else None, released=first_day,
                   ebook=any(entry.form == "text" for entry in members),
                   audio=any(entry.form == "audio" for entry in members),
                   sources=sorted({entry.source for entry in members}, key=lambda s: SPEAKER_ORDER.get(s, 9)),
                   marks=marks, latest=_latest(members, first_day))


def stable_key(language: str, title: str) -> str:
    """The key of a work that stays the same from one build to the next: its original language and core title."""
    return hashlib.sha1(f"{language}:{title_key(title)}".encode()).hexdigest()[:16]


def series_of(members: list[Entry], author: str = "") -> tuple[str, float | None]:
    """The series of a work and its place in it: what the sources that know works name (Hardcover, Audible) first,
    a library's series only when no such source names one; never a publisher's line (``series_names.is_line``), and
    never a series without a place in it (a line of a publisher numbers no volumes of one work: ``SmartEdition``,
    ``Meine Edition``), unless the title names it (``Moorland: Das Haus``)."""
    names: Counter[str] = Counter()
    spelled: dict[str, Counter[str]] = {}
    places: dict[str, Counter[float]] = {}
    for weight_of in ((lambda e: e.source not in series_names.LIBRARIES), (lambda e: True)):
        for entry in members:
            placed = entry.series_pos is not None or entry.signals.get("series_of_title") is True
            if not entry.series or not placed or not weight_of(entry) or series_names.is_line(
                    entry.series, entry.series_pos, authors=[author] if author else [],
                    library=entry.source in series_names.LIBRARIES):
                continue
            name = series_names.key(entry.series)
            if not name:
                continue
            names[name] += 1 + (entry.source == "hardcover")
            spelled.setdefault(name, Counter())[entry.series.strip()] += 1 + (entry.source == "hardcover")
            if entry.series_pos is not None:
                places.setdefault(name, Counter())[entry.series_pos] += 1
        if names:
            break
    if not names:
        return "", None
    best = names.most_common(1)[0][0]
    text = min(spelled[best].items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0]
    place = places.get(best)
    return text, (place.most_common(1)[0][0] if place else None)


def make_work(members: list[Entry], *, author_language: str, pen_names: set[str], author: str = "") -> Work:
    by_language: dict[str, list[Entry]] = {}
    for entry in members:
        by_language.setdefault(entry.language or author_language or "und", []).append(entry)
    titles = {language: _edition(items) for language, items in by_language.items()}
    said = Counter(entry.original_language for entry in members if entry.original_language)
    if said:
        orig = said.most_common(1)[0][0]
    elif author_language in titles:
        orig = author_language
    else:
        orig = min(titles, key=lambda language: (titles[language].year or 9999, language))
    if orig not in titles:
        # Only translations are known: the original title they name stands for the original edition.
        named = [entry.original_title for entry in members if entry.original_title and entry.original_language == orig]
        if named:
            titles[orig] = Edition(title=core_title(Counter(named).most_common(1)[0][0]))
        else:
            # Nobody knows its title in the original language: it stands under the language it came out in first.
            orig = min(titles, key=lambda language: (titles[language].year or 9999, language))
    year_pool = [edition.year for edition in titles.values() if edition.year]
    # Open Library knows the year the work first came out, in any language.
    firsts = [entry.signals.get("first_year") for entry in members if isinstance(entry.signals.get("first_year"), int)]
    opened = min(firsts) if firsts else None
    series, place = series_of(members, author)
    vias = {entry.via for entry in members}
    pen = next(iter(vias)) if len(vias) == 1 and next(iter(vias)) in pen_names else ""
    # Only the ISBNs of records that name one book (two numbers at most: print and e-book); a record that lists many
    # is a set or a mistake, and its numbers would recognise other books as this one.
    isbns = list(dict.fromkeys(isbn for entry in members if len(entry.isbns) <= 2 for isbn in entry.isbns))[:MAX_ISBNS]
    asins = [entry.asin for entry in members if entry.asin]
    refs = list(dict.fromkeys(f"{entry.source}:{entry.work_key or entry.key}" for entry in members))[:MAX_REFS]
    pages = [entry.pages for entry in members if entry.pages and entry.form == "text"]
    minutes = [entry.runtime_min for entry in members if entry.runtime_min and entry.variant != "abridged"]
    work = Work(
        key=stable_key(orig, titles[orig].title), entries=members, titles=titles, pen=pen, series=series,
        series_pos=place,
        year=first_year_of(min(y for y in (titles[orig].year, opened) if y) if (titles[orig].year or opened) else (
            min(year_pool) if year_pool else None), members), orig_language=orig,
        sources=sorted({entry.source for entry in members}, key=lambda s: SPEAKER_ORDER.get(s, 9)),
        isbns=list(dict.fromkeys(isbns + asins))[:MAX_ISBNS], refs=refs,
        pages=int(statistics.median(pages)) if pages else None, minutes=max(minutes) if minutes else None,
        weight=sum(entry.weight for entry in members))
    return work


def about_the_author(title: str, author: str) -> bool:
    """Whether a title names the author (``Ida Falkner, Der Nebelchor``, ``Ida Falkner: Leben und Werk``): a book about
    the author or one of their books (a study guide), not a book of theirs."""
    name = compare_key(author)
    return bool(name) and len(name.split()) >= 2 and name in compare_key(title.replace(",", " ").replace(":", " "))


def one_series(works: list[Work]) -> None:
    """Two names of one series are one (rule 7): two works or more that carry both at the same place say so (one work
    may stand in two series at the same place: the first book of a cycle within a cycle); every work then names the
    series as most of its works do."""
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        while parent.get(name, name) != name:
            name = parent[name]
        return name

    together: Counter[tuple[str, str]] = Counter()
    for work in works:
        named: dict[float, set[str]] = {}
        for entry in work.entries:
            if entry.series and entry.series_pos is not None and not series_names.is_line(
                    entry.series, entry.series_pos, library=entry.source in series_names.LIBRARIES):
                named.setdefault(entry.series_pos, set()).add(series_names.key(entry.series))
        pairs = {(a, b) for keys in named.values() for a in keys for b in keys if a and b and a < b}
        together.update(pairs)
    for (a, b), count in sorted(together.items()):
        if count >= 2 and find(a) != find(b):
            root_a, root_b = find(a), find(b)
            parent[max(root_a, root_b)] = min(root_a, root_b)
    spelled: dict[str, Counter[str]] = {}
    for work in works:
        if work.series:
            spelled.setdefault(find(series_names.key(work.series)), Counter())[work.series] += 1
    for work in works:
        if work.series:
            names = spelled[find(series_names.key(work.series))]
            work.series = min(names.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0]


def _one_place(found: list[Work], author_language: str, pens: set[str], author: str) -> list[Work]:
    """Two works at the same place of one series whose titles in a language are one the start of the other (``Das
    grobe Netz`` and ``Das grobe Netz. Ein Fall für Kommissar Lund``) are one work: the shop wrote the
    series into the title."""
    by_place: dict[tuple[str, float], list[int]] = {}
    for index, work in enumerate(found):
        if work.series and work.series_pos is not None:
            by_place.setdefault((series_names.key(work.series), work.series_pos), []).append(index)
    gone: set[int] = set()
    out = list(found)
    for indexes in by_place.values():
        for position, first in enumerate(indexes):
            for second in indexes[position + 1:]:
                if first in gone or second in gone:
                    continue
                a, b = out[first], out[second]
                if any(_starts(title_key(a.titles[code].title), title_key(b.titles[code].title))
                       for code in set(a.titles) & set(b.titles)):
                    out[first] = make_work(a.entries + b.entries, author_language=author_language, pen_names=pens,
                                           author=author)
                    out[first].series, out[first].series_pos = a.series, a.series_pos
                    gone.add(second)
    return [work for index, work in enumerate(out) if index not in gone]


def _starts(a: str, b: str) -> bool:
    """Whether one key is the other with words added at the end (two words at least in the shorter)."""
    short, long = sorted((a, b), key=len)
    return len(short.split()) >= 2 and (long == short or long.startswith(short + " "))


def build(entries: list[Entry], *, answered: dict[str, set[str]], author_language: str,
          pen_names: set[str] | None = None, author: str = "", namesakes: int = 0,
          keep_all: bool = False) -> list[Work]:
    """The works of an author from the records of the sources: grouped, kept by the rule of two sources, each with its
    kind. In the order of their years. ``namesakes``: how many other people of the name the sources showed;
    ``keep_all``: every work is kept, also one only a single source knows (the search of the catalogues shows what was
    found, the catalogue of an author only what is sure)."""
    fix_series_titles(entries)
    pens = pen_names or set()
    works = [make_work(members, author_language=author_language, pen_names=pens, author=author)
             for members in group(entries, author_language) if keep_all or kept(members, answered)]
    # Two groups whose titles in the original language are one (``Salt & Ashes`` beside a translation that named
    # ``Salt and Ashes``) are one work.
    by_key: dict[str, Work] = {}
    merged: list[Work] = []
    for work in works:
        same = by_key.get(work.key)
        if same is not None and not _places_conflict(_places(same.entries), _places(work.entries)):
            index = merged.index(same)
            merged[index] = make_work(same.entries + work.entries, author_language=author_language, pen_names=pens,
                                      author=author)
            by_key[work.key] = merged[index]
            continue
        by_key.setdefault(work.key, work)
        merged.append(work)
    works = merged
    one_series(works)
    works = _one_place(works, author_language, pens, author)
    keys = [compare_key(work.title_in(work.orig_language)) for work in works]
    series_keys = {series_names.key(work.series) for work in works if work.series}
    seen: Counter[str] = Counter()
    for work, own in zip(works, keys, strict=True):
        work.kind, work.why = kinds.kind_of(work.entries, keys, own, series_keys)
        # (Most of its records: a shop that writes "Author: Title" on one product does not make a novel a book about
        # it.)
        if author and sum(about_the_author(entry.title, author) for entry in work.entries) * 2 > len(work.entries):
            work.kind, work.why = "unsure", "about"
        elif namesakes and work.kind != "unsure" and not people.proven(work.entries):
            # Another person of the name may have written it: no number of a source says it is the author's.
            work.kind, work.why = "unsure", "namesake"
        seen[work.key] += 1
        if seen[work.key] > 1:
            work.key = f"{work.key[:13]}-{seen[work.key]}"
    return sorted(works, key=lambda w: (w.year or 9999, w.title_in(w.orig_language)))


__all__ = ["Edition", "Work", "build", "fix_series_titles", "group", "kept", "make_work", "marks_of", "stable_key",
           "title_key"]
