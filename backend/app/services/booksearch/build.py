"""From the records the sources gave for a search to the hits a person sees: one for each work, in the order of a
library.

1. **Who wrote it.** The records are put in groups by their authors first (``authors_compatible``: ``J. K. Ash`` and
   ``Joanna K. Ash`` are one, ``Ash`` alone is nobody): two books of the same title by two people are two hits, and
   nothing of one author is ever joined to a book of another.
2. **What it is.** Within an author, records that are a play made of a book (``Hörspiel``, a full cast, a short
   recording of three speakers), a study guide, a calendar, a retelling or another book about the book, a box or a
   part of a book (a reading sold in numbered parts, the volumes of one edition beside the whole) are set apart before
   the works are made: they carry the title of the book and would otherwise be joined to it. Each of those piles is
   made into works of its own.
3. **The works** of a pile are made the way the catalogue of an author makes them (``oeuvre.works.build``): one work
   for the editions, translations and recordings of a book, across the languages. A work in none of the languages
   fetched in, and not in the language its author writes in, is a translation that could not be joined to its book:
   it is left out (it would stand beside the book as a hit of its own). The series of a hit is named as the records
   of its title's language name it (``series_in``); one that a single source names for a single book is left out (a
   line of a publisher, of a reader, of a newspaper).
4. **The order** (``rank``): how much of what was typed the titles, the series and the authors of a work hold, whether
   the volume asked for is its place in the series, how well known it is (the sources that know it, the editions,
   the readers). The book asked for first, then the other volumes of its series, then the rest; after them what was
   set apart, and last the books that exist in none of the languages fetched in.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import series_names
from ..oeuvre import works as works_service
from ..readers.names import compare_key, core_title, is_company, keys_alike, person_key
from ..readers.text import fold, key_fold
from ..sources.model import Entry
from . import titles, words
from .asked import VOLUME_WORDS, Asked
from .authors import by_author, lead_author
from .titles import names_of, tidy
from .words import LABEL_WORDS, LABELS

#: Where a hit stands: the book asked for, the other volumes of its series, the rest, what was set apart, and the books
#: in none of the languages fetched in.
GROUPS = ("match", "series", "more", "related", "elsewhere")
#: Hits of an answer at most, and of each group after the books.
MAX_HITS = 24
MAX_RELATED = 6
MAX_ELSEWHERE = 4
#: The records of a hit shown under "show the editions" at most.
MAX_EDITIONS = 40


@dataclass
class Hit:
    work: works_service.Work
    label: str
    authors: list[str]
    #: The title, and the language of it: the first language fetched in that the work has (``language`` is empty and
    #: ``allowed`` is False when it has none of them; the title is then the one of its own language).
    title: str
    language: str
    allowed: bool
    #: Every language the sources know the work in.
    languages: list[str]
    #: The forms the sources know it in, in the languages fetched in.
    ebook: bool = False
    audio: bool = False
    cover_url: str = ""
    group: str = "more"
    #: How much of the typed words the work holds (``cover``: of all of them; ``titled``: of the ones that are not the
    #: author's name), whether its place in its series is the volume asked for, and how well known it is.
    cover: float = 0.0
    titled: float = 0.0
    volume_fits: int = 0
    exact: bool = False
    known: float = 0.0
    best_rank: int = 99
    cluster: int = 0
    #: The language the author writes in (``language_of_author``).
    author_language: str = ""
    #: The words ask for what the hit is (``Lantern Saga Box``, ``… Folge 1`` for a play): it comes before the books.
    asked_for: bool = False
    #: The title is the name of a series of the author's whose volumes are numbered, and the hit has no place in it
    #: (``series_named``): which volume it is, or whether it is the whole, nobody knows.
    series_named: bool = False
    editions: list[dict[str, Any]] = field(default_factory=list)
    #: The languages the operator fetches in (a wish made from the hit looks for its titles in those).
    fetched: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """The name of the hit that stays the same while the sources answer: the work's key and its place in the series,
        its label and its author, and a number when another hit of the answer makes the same (``unique_keys``)."""
        return self._key or self.base_key

    @property
    def base_key(self) -> str:
        place = f"{self.place:g}" if self.place is not None else ""
        author = person_key(self.authors[0]) if self.authors else ""
        return hashlib.sha1(f"{self.work.key}|{place}|{self.label}|{author}".encode()).hexdigest()[:20]

    _key: str = ""
    #: The work's place in its series as its records give it, before the hit names the series in the language of its
    #: title or leaves it out (``_hit``): what tells two volumes of one work apart in ``key``.
    place: float | None = None


def unique_keys(hits: list[Hit]) -> None:
    """Every hit of an answer a key of its own (two hits that make the same get a number: ``…~2``)."""
    seen: Counter[str] = Counter()
    for hit in hits:
        base = hit.base_key
        seen[base] += 1
        hit._key = base if seen[base] == 1 else f"{base}~{seen[base]}"


# ---- What it is ---------------------------------------------------------------------------------------------------


def _said(entry: Entry) -> str:
    """The whole title of a record with its subtitle and series, folded (the core of the title is cut before a bracket
    or a colon, where a calendar or a study guide says what it is)."""
    texts = [entry.title, *(entry.signals.get(name) for name in (
        "raw_title", "full_title", "shown_title", "subtitle"))]
    return fold(" ".join(dict.fromkeys(text for text in texts if isinstance(text, str) and text)))


def _about_behind_mark(entry: Entry) -> bool:
    """Whether what follows the title of a record behind a full stop or a colon names a thing about the book
    (``Die Laternenstraße: Ein Journal``, ``Die Laternenstraße. Postkarten``); a title that only has such a word in it
    (``Das Journal der Lena Falk``) is a book."""
    for text in (entry.signals.get("raw_title"), entry.signals.get("full_title"), entry.title):
        if isinstance(text, str) and text:
            parts = re.split(r"\.\s|:\s", text, maxsplit=1)
            if len(parts) == 2 and parts[0].strip() and words.about_a_book(fold(parts[1])):
                return True
    return False


#: Speakers of a shop's recording from which on a short one is a play with a cast, not a reading.
CAST = 3


def _a_cast(entry: Entry) -> bool:
    """Whether a shop's recording is a play although it says nothing of it: three speakers and more for no longer than
    an episode runs (``EPISODE_MINUTES``; a reading of a children's book by one person runs as long, a reading by many
    voices of a novel runs for hours)."""
    narrators = entry.signals.get("narrators")
    return (isinstance(narrators, list) and len(narrators) >= CAST and entry.runtime_min is not None
            and 0 < entry.runtime_min <= EPISODE_MINUTES)


def pile_of(entry: Entry) -> str:
    """The pile a record goes on before the works are made (``work`` for a book; the words are in ``words.py``)."""
    said = _said(entry)
    if entry.authors and all(is_company(name) for name in entry.authors):
        # A book whose authors are companies (a studio, a publisher of calendars) is one about the book.
        return "companion"
    if entry.form == "audio" and (entry.variant == "dramatized" or words.DRAMA.search(said)
                                  or words.DRAMA.search(fold(entry.series))
                                  or words.DRAMA_CREDITS.search(fold(str(entry.signals.get("credits") or "")))
                                  or _a_cast(entry)):
        return "drama"
    if words.GUIDE.search(said):
        return "guide"
    if words.COMPANION.search(said) or _about_behind_mark(entry):
        return "companion"
    if words.ADAPTED.search(said) and not words.COMIC.search(said):
        # (A book retold after the book, for children or for learners: ``Lantern Road (adaptation)``.)
        return "companion"
    if entry.signals.get("volume_part") or words.PART.search(said) or any(
            words.PART_SLASH.search(str(entry.signals.get(name) or "")) for name in ("full_title", "shown_title")) or \
            words.PART_SLASH.search(entry.title):
        return "part"
    if words.BOX.search(said) or entry.compilation:
        return "box"
    if words.COMIC.search(said) or words.COMIC.search(fold(entry.series)):
        # (A library files a comic under a series that says so: ``Eine Example Graphic Novel``.)
        return "comic"
    return "work"


def piles_of(entries: list[Entry]) -> list[str]:
    """The pile of each record (``pile_of``); a work of a catalogue and its editions share one: the work that says it
    is a retelling (``1984 (adaptation)``) makes its edition (``1984``) one too."""
    found = [pile_of(entry) for entry in entries]
    shared: dict[tuple[str, str], str] = {}
    for entry, pile in zip(entries, found, strict=True):
        if entry.work_key and pile != "work":
            shared.setdefault((entry.source, entry.work_key), pile)
    return [shared.get((entry.source, entry.work_key), pile) if entry.work_key else pile
            for entry, pile in zip(entries, found, strict=True)]


# ---- The works ----------------------------------------------------------------------------------------------------

def series_core(name: str) -> str:
    """A series as it is compared: without what a shop adds to its name (``Lantern Saga - read by Ada Example``,
    ``Lantern Saga (Spanish Edition)``) and without the words that only say it is one (``Lantern Series``,
    ``series_names.key``)."""
    return series_names.key(core_title(name)) or compare_key(name) if name else ""


#: Words behind a dash in the name of a series that say how a shop sells it, not which series it is (folded).
_SERIES_TAIL_NOISE = re.compile(r"\b(gelesen|liest|read|reads|narrated|lesung|horbuch\w*|audiobook\w*|horspiel\w*|"
                                r"ungekurzt|gekurzt|unabridged|abridged|edition|ausgabe|german|deutsch|english|"
                                r"spanish|french|cd|cds|mp3|box|teil|band|folge|staffel|season|gesprochen|sprecher|"
                                r"neu|ubersetzt|uebersetzt|neuubersetzung|fassung|version|dramatisiert)\b")


def shown_series(name: str) -> str:
    """The name of a series as a hit shows it: without what a shop adds to it (``Lantern Saga - read by Ada Example``,
    ``Lantern Saga (Spanish Edition)``), but whole when what follows a dash is a series of its own of the same world
    (``Naruto - Konoha Shinden``, ``Reihe - Before the Fall``: band 1 of those is not band 1 of ``Naruto``)."""
    cut = core_title(name)
    tail = name[len(cut):]
    dash = re.match(r"^\s+[-–]\s+(?P<rest>[^\W\d_][^()\[\]:|]*)$", tail)
    if dash is not None and words.title_like(dash.group("rest")) and not _SERIES_TAIL_NOISE.search(
            fold(dash.group("rest"))):
        return " ".join(name.split())
    return cut


def _languages_of(work: works_service.Work) -> list[str]:
    return [code for code in work.titles if code and code != "und"]


def _title_in(work: works_service.Work, languages: list[str], original: bool, author_language: str) -> tuple[str, str]:
    """``(title, language)`` in the first language fetched in that the work has; the language the author writes in
    counts too when the operator fetches the original language (``original``). ``("", "")`` when none. A title only
    the original title of a translation gives (no record carries it) comes after the ones the records carry."""
    for carried in (True, False):
        for code in languages:
            if code in work.titles and bool(work.titles[code].sources) == carried:
                return work.titles[code].title, code
    own = author_language or work.orig_language
    if original and own and own != "und" and own in work.titles:
        # (A title of no known language is in no language fetched in.)
        return work.titles[own].title, own
    return "", ""


def _cover_of(work: works_service.Work, language: str) -> str:
    """A cover of the book in that language: of a printed book or an e-book first, then of a recording, then any."""
    def order(entry: Entry) -> tuple[int, int, int]:
        return ((entry.language or work.orig_language) != language, entry.form != "text",
                works_service.SPEAKER_ORDER.get(entry.source, 9))

    with_cover = [entry for entry in work.entries if entry.cover_url]
    return min(with_cover, key=order).cover_url if with_cover else ""


def _edition(entry: Entry, orig: str) -> dict[str, Any]:
    signals = entry.signals
    narrators = signals.get("narrators")
    return {"source": entry.source, "form": "audio" if entry.form == "audio" else "ebook",
            "language": entry.language or orig, "title": entry.title[:300],
            "subtitle": str(signals.get("subtitle") or "")[:200], "year": entry.year,
            "isbn": next((isbn for isbn in entry.isbns if isbn.isdigit()), ""), "asin": entry.asin,
            "publisher": str(signals.get("publisher") or "")[:120],
            "narrators": [n for n in narrators if isinstance(n, str)][:4] if isinstance(narrators, list) else [],
            "minutes": entry.runtime_min, "variant": entry.variant, "pages": entry.pages}


def editions_of(work: works_service.Work, languages: list[str]) -> list[dict[str, Any]]:
    """The single editions behind a hit (a record of a library or a shop, never a work of Open Library or Hardcover):
    the ones in the languages fetched in first, in their order; within a language the e-books, then the recordings,
    the newest first."""
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for entry in work.entries:
        if entry.signals.get("work_level") or (entry.source, entry.key) in seen:
            continue
        seen.add((entry.source, entry.key))
        rows.append(_edition(entry, work.orig_language))
    position = {code: index for index, code in enumerate(languages)}
    rows.sort(key=lambda row: (position.get(row["language"], len(position)), row["form"] != "ebook",
                               -(row["year"] or 0), row["title"]))
    return rows[:MAX_EDITIONS]


def _one_name(a: str, b: str) -> bool:
    """Whether two names of series (``series_core``) are one series named more or less fully (``kommissar lund`` and
    ``krimi reihe um kommissar lund``): the words of one are all in the other."""
    one, two = set(a.split()), set(b.split())
    return bool(one) and bool(two) and (one <= two or two <= one)


def series_in(work: works_service.Work, language: str) -> tuple[str, float | None]:
    """The series of a work and its place as the records of the title's language name them (``Die Lichter von Varn``
    for the German title, not ``The Lights of Varn``; the German numbering of a series that numbers otherwise there): a
    shop's before a library's, the most records first. The work's own when no record of that language names one."""
    names: Counter[str] = Counter()
    places: dict[str, Counter[float]] = {}
    for library in (False, True):
        for entry in work.entries:
            if ((entry.language or work.orig_language) != language or (entry.source in series_names.LIBRARIES)
                    != library or _place_of(entry) is None):
                continue
            name = shown_series(entry.series.strip())
            names[name] += 1
            places.setdefault(name, Counter())[entry.series_pos] += 1  # type: ignore[index]
        if names:
            break
    if not names:
        return work.series, work.series_pos
    best = min(names.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0]
    if series_core(best) == series_core(work.series) and work.series_pos is not None and \
            work.series_pos in places[best]:
        return work.series, work.series_pos
    return best, places[best].most_common(1)[0][0]


def _hit(work: works_service.Work, label: str, authors: list[str], languages: list[str], original: bool,
         author_language: str, others: list[Entry] | None = None) -> Hit:
    title, language = _title_in(work, languages, original, author_language)
    place = work.series_pos
    allowed = bool(language)
    if not allowed:
        title = work.title_in(work.orig_language)
    wanted = set(languages) | ({author_language or work.orig_language} if original else set())
    in_wanted = [entry for entry in work.entries if (entry.language or work.orig_language) in wanted]
    if work.series and language:
        work.series, work.series_pos = series_in(work, language)
    core = series_core(work.series) if work.series else ""
    # (The names the work's records give the series at its place are one series: ``Kommissar Lund`` of the shop and
    # ``Krimi-Reihe um Kommissar Lund`` of the library.)
    cores = {core} | {other for entry in work.entries
                      if core and entry.series and entry.series_pos == work.series_pos and _place_of(entry) is not None
                      and _one_name(core, other := series_core(entry.series))}
    naming = {id(entry): entry for entry in [*work.entries, *(others or [])]
              if core and entry.series and series_core(entry.series) in cores}.values()
    volume = work.series_pos if work.why in ("volume", "named") and not work.series else None
    if len({entry.source for entry in naming}) < 2 and len({entry.series_pos for entry in naming
                                                               if entry.series_pos is not None}) < 2:
        # (A series one source names for one book only may be a line of a publisher, ``Die Originale``, a library's
        # edition of the works: it is not shown, and a wish made from the hit carries none.)
        work.series, work.series_pos = "", None
    work.series = shown_series(work.series) if work.series else ""
    if volume is not None and not work.series:
        # (One volume of a book in several, or of a comic, that no series numbers: the title says which.)
        title = f"{title} {volume:g}"
    return Hit(work=work, label=label, authors=authors, title=title, language=language, allowed=allowed,
               languages=_languages_of(work), ebook=any(entry.form != "audio" for entry in in_wanted),
               audio=any(entry.form == "audio" for entry in in_wanted),
               cover_url=_cover_of(work, language or work.orig_language), editions=editions_of(work, languages),
               fetched=list(languages), place=place)


def _subtitles_in(work: works_service.Work, code: str) -> set[str]:
    """The subtitles the records of a work carry in a language, as they are compared."""
    return {compare_key(text) for entry in work.entries if (entry.language or work.orig_language) == code
            and isinstance(text := entry.signals.get("subtitle"), str) and len(compare_key(text)) >= 8}


def _titles_agree(a: works_service.Work, b: works_service.Work, *, subtitles: bool = False) -> bool:
    """Whether two works have alike titles in every language both have (none in common agrees too). A title only the
    original title of a translation gives (no record carries it) does not count: a library names the uniform title of
    the series there now and then. With ``subtitles``, the title of one is also alike when it is the subtitle the
    records of the other carry in that language (a library's ``Example: Eine kurze Geschichte`` and the shop's ``Eine
    kurze Geschichte``)."""
    def carried(work: works_service.Work) -> set[str]:
        return {code for code, edition in work.titles.items() if code != "und" and edition.sources}

    def alike(code: str) -> bool:
        key_a, key_b = compare_key(a.titles[code].title), compare_key(b.titles[code].title)
        if keys_alike(key_a, key_b):
            return True
        return subtitles and (key_a in _subtitles_in(b, code) or key_b in _subtitles_in(a, code))

    return all(alike(code) for code in carried(a) & carried(b))


def series_places(entries: list[Entry]) -> dict[str, set[float]]:
    """The places each series of the records has among them."""
    found: dict[str, set[float]] = {}
    for entry in entries:
        if entry.series and entry.series_pos is not None:
            found.setdefault(series_core(entry.series), set()).add(entry.series_pos)
    return found


def _place_of(entry: Entry, places: dict[str, set[float]] | None = None) -> float | None:
    """The place of a record in a series of the author: a publisher's line has numbers no volume has, and a series
    that has a single place among all the records of the author (``Premium Edition 2``) is no series that tells two
    volumes apart."""
    if not entry.series or entry.series_pos is None or entry.series_pos > 100:
        return None
    if series_names.is_line(entry.series, entry.series_pos, library=entry.source in series_names.LIBRARIES):
        return None
    if places is not None and len(places.get(series_core(entry.series), set())) < 2:
        return None
    return entry.series_pos


def _titles_differ(parts: list[list[Entry]]) -> bool:
    """Whether the records at two places carry different titles in a language both have (two books, not one book that
    two sources number otherwise). The titles are compared whole, as the records show them: the core of ``Ember - The
    Messenger`` is the title of the first volume."""
    def shown(entry: Entry) -> str:
        return fold(str(entry.signals.get("shown_title") or entry.title))

    for index, first in enumerate(parts):
        mine: dict[str, set[str]] = {}
        for entry in first:
            mine.setdefault(entry.language, set()).add(shown(entry))
        for second in parts[index + 1:]:
            for entry in second:
                keys = mine.get(entry.language)
                if keys and shown(entry) not in keys:
                    return True
    return False


def named_volume(entry: Entry) -> float | None:
    """The one volume a record's own title names (``… Der Comic, Band 1``, a library's ``Die Brüder. Bd. 1``), not its
    place in a series (``_place_of``)."""
    said = entry.signals.get("volume")
    if isinstance(said, int | float) and 0 < said < 1000:
        return float(said)
    texts = [entry.signals.get("full_title"), entry.signals.get("raw_title"), entry.title]
    return next((found for text in texts if isinstance(text, str) and text
                 and (found := words.volume_of(fold(text))) is not None), None)


def _mark(entry: Entry, places: dict[str, set[float]] | None) -> tuple[float, str] | None:
    """Where a record stands: its place in a series (``place``), or the volume its title names (``named``)."""
    place = _place_of(entry, places)
    if place is not None:
        return place, "place"
    named = named_volume(entry)
    return (named, "named") if named is not None else None


def _numbers_apart(parts: list[list[Entry]]) -> bool:
    """Whether the single editions at two places carry ISBNs and none of them in common (two books: one book that two
    sources number otherwise has the same editions)."""
    sets = [{isbn for entry in members if not entry.signals.get("work_level") for isbn in entry.isbns}
            for members in parts]
    return any(a and b and not a & b for index, a in enumerate(sets) for b in sets[index + 1:])


def _shown(entry: Entry) -> str:
    """The whole title a record shows (``titles.tidy``: with the subtitle a shop keeps apart)."""
    return str(entry.signals.get("shown_title") or entry.title)


#: Articles in front of a title, which records write or leave out (folded).
_ARTICLES = frozenset({"der", "die", "das", "the", "a", "an", "le", "la", "les", "el", "il", "de", "het", "ein",
                       "eine"})


def _shown_key(entry: Entry) -> str:
    """The whole title a record shows, folded and without the article in front (``Hund vom Moorhaus``)."""
    found = fold(_shown(entry)).split()
    while len(found) > 1 and found[0] in _ARTICLES:
        found.pop(0)
    return " ".join(found)


def _volume_title(entry: Entry) -> bool:
    """Whether the subtitle of a shop's record is the title of a volume (``Der Hafen``), not the series and its number
    again (``Lantern Saga, Book 1``) or a word of the form."""
    subtitle = entry.signals.get("subtitle")
    if not isinstance(subtitle, str) or not subtitle or any(char.isdigit() for char in subtitle):
        return False
    folded = fold(subtitle)
    return (words.title_like(subtitle) and not series_core(subtitle).startswith(series_core(entry.series))
            and not set(folded.split()) & VOLUME_WORDS and not titles.edition_tail(subtitle)
            and not re.search(r"\b(roman|thriller|krimi|novel|horbuch|horspiel|lesung)\b", folded))


def _carries(loose: Entry, placed: Entry) -> bool:
    """Whether a record without a place carries the title of a record at a place in its language: the core of it, or,
    when the placed record is a volume a shop names by the whole and its own title (``_volume_title``), the whole
    title it shows."""
    if loose.language != placed.language:
        return False
    if placed.series and series_core(placed.title) == series_core(placed.series) and _volume_title(placed):
        return _shown_key(loose) == _shown_key(placed)
    return compare_key(loose.title) == compare_key(placed.title)


def _one_edition_in_volumes(parts: dict[float, list[Entry]], loose: list[Entry]) -> bool:
    """Whether the records at the places of a series are the volumes of one edition of a book printed in several: one
    title at all the places, the title the records without a place carry (the whole), and the series of one source
    that holds the most places (two at least) out within a year. A comic series whose volumes carry its name comes
    out a volume a year or so."""
    if len(parts) < 2 or not loose:
        return False
    keys = {compare_key(entry.title) for members in parts.values() for entry in members}
    if len(keys) != 1 or not any(compare_key(entry.title) in keys for entry in loose):
        return False
    held: dict[tuple[str, str], list[Entry]] = {}
    for members in parts.values():
        for entry in members:
            if entry.series:
                held.setdefault((entry.source, series_core(entry.series)), []).append(entry)
    edition = max(held.values(), key=lambda found: len({entry.series_pos for entry in found}), default=[])
    years = [entry.year for entry in edition if entry.year]
    return len({entry.series_pos for entry in edition}) >= 2 and bool(years) and max(years) - min(years) <= 1


def split_by_place(found: list[works_service.Work], *, author_language: str, author: str,
                   places: dict[str, set[float]] | None = None) -> list[works_service.Work]:
    """Never two volumes in one hit. A work whose records stand at two places of a series is two books (a translation
    that names the original title of the series, not of its volume, joins volume 2 to volume 1): its records are
    parted by their place (whatever the series is called in their language). The same title at two places is one book
    that two sources number otherwise, unless the editions at the two places have ISBNs and none in common.

    A record whose own title names a volume (``Band 2``) that is not the work's place is one volume of a book printed in
    several, or of a comic of it: the volumes are parted from one another and from the whole book, and a volume beside
    a whole book is a part (``why == "volume"``).

    A record without a place goes with the records whose title it carries in its language, or with the other records
    of its work at a catalogue; else it stands apart (with the whole, when the volumes were only named)."""
    out: list[works_service.Work] = []
    for work in found:
        marks = {id(entry): _mark(entry, places) for entry in work.entries}
        # (A volume a title names that is the work's own place is that place: ``Lantern Saga 4`` and ``Band 4``.)
        for entry in work.entries:
            mark = marks[id(entry)]
            if mark is not None and mark[1] == "named" and work.series_pos is not None and mark[0] == work.series_pos:
                marks[id(entry)] = (mark[0], "place")
        placed = [entry for entry in work.entries if marks[id(entry)] is not None]
        values = {marks[id(entry)][0] for entry in placed}  # type: ignore[index]
        named = [entry for entry in placed if marks[id(entry)][1] == "named"]  # type: ignore[index]
        loose = [entry for entry in work.entries if marks[id(entry)] is None]
        if len(values) < 2 and not (named and loose):
            out.append(work)
            continue
        parts: dict[float, list[Entry]] = {}
        for entry in placed:
            parts.setdefault(marks[id(entry)][0], []).append(entry)  # type: ignore[index]
        held: dict[tuple[str, str], set[float]] = {}
        for entry in placed:
            if entry.series:
                held.setdefault((entry.source, series_core(entry.series)), set()).add(
                    marks[id(entry)][0])  # type: ignore[index]
        one_source_twice = any(len(found) > 1 for found in held.values())
        if not named and not one_source_twice and not _titles_differ(list(parts.values())) and not _numbers_apart(
                list(parts.values())):
            # (The same title at two places of two sources is one book a source numbers otherwise: kept together. One
            # source that puts the title at two places has two books of it.)
            out.append(work)
            continue
        left: list[Entry] = []
        # (Every place carries the title of the whole, they came out together, and the whole is there without a place:
        # the volumes of one edition of a book printed in several, ``Die Sagen vom Salztor ; 1`` to ``; 3`` of one
        # year.)
        edition = not named and _one_edition_in_volumes(parts, loose)
        if named or edition:
            # (The volumes of a book printed in several carry the title of the whole: the records without a volume are
            # the whole book, not the first volume.)
            left = loose
        else:
            for entry in loose:
                # (By the whole title the records show: ``Glut`` without a place is not ``Glut: Der Bote`` at place 2,
                # it is the whole of which that is a volume.)
                home = next((place for place, members in parts.items()
                             if any(_carries(entry, member) for member in members)), None)
                if home is None:
                    left.append(entry)
                else:
                    parts[home].append(entry)
        # A record of a work of a catalogue goes where another record of that work went (its title in the other
        # language); what is left belongs to none of the places for sure and stands apart.
        rest: list[Entry] = []
        for entry in left:
            home = next((place for place, members in parts.items() if entry.work_key and not (named or edition) and any(
                (m.source, m.work_key) == (entry.source, entry.work_key) for m in members)), None)
            if home is None:
                rest.append(entry)
            else:
                parts[home].append(entry)
        for members in parts.values():
            made = works_service.make_work(members, author_language=author_language, pen_names=set(), author=author)
            made.kind, made.why = work.kind, work.why
            kinds = {marks[id(entry)][1] for entry in members if marks[id(entry)] is not None}  # type: ignore[index]
            if kinds == {"named"} or edition:
                # A volume a title names: beside the whole book a part of it, else a volume of its own (a comic's).
                made.why = "volume" if rest else "named"
                made.series_pos = made.series_pos if made.series else marks[id(members[0])][0]  # type: ignore[index]
            out.append(made)
        if rest:
            made = works_service.make_work(rest, author_language=author_language, pen_names=set(), author=author)
            made.kind, made.why = work.kind, work.why
            out.append(made)
    return out


def join_by_place(found: list[works_service.Work], *, author_language: str, author: str) -> list[works_service.Work]:
    """Two works of one author at the same place of the same series are one book in two languages (the German
    recording of a shop and the English work of Open Library, which no number of either joins), when their titles
    agree in every language both have. The series may have another name in each language: two names one work carries
    at the same place are one series (``Die Laternen-Saga 1`` and ``The Lantern Saga 1``)."""
    parent: dict[str, str] = {}

    def root(name: str) -> str:
        while parent.get(name, name) != name:
            name = parent[name]
        return name

    def places(work: works_service.Work) -> set[tuple[str, float]]:
        return {(series_core(entry.series), entry.series_pos) for entry in work.entries
                if entry.series and entry.series_pos is not None}

    for work in found:
        by_place: dict[float, set[str]] = {}
        for name, place in places(work):
            by_place.setdefault(place, set()).add(name)
        if len(by_place) == 1:
            names = sorted(next(iter(by_place.values())))
            for name in names[1:]:
                a, b = root(names[0]), root(name)
                if a != b:
                    parent[max(a, b)] = min(a, b)

    def same_place(a: works_service.Work, b: works_service.Work) -> bool:
        mine, theirs = places(a), places(b)
        if len({place for _name, place in mine}) != 1 or len({place for _name, place in theirs}) != 1:
            return False
        return any(pa == pb and root(na) == root(nb) for na, pa in mine for nb, pb in theirs)

    out = list(found)
    changed = True
    while changed:
        changed = False
        for first in range(len(out)):
            a = out[first]
            if not a.series or a.series_pos is None:
                continue
            for second in range(first + 1, len(out)):
                b = out[second]
                if not same_place(a, b) or not _titles_agree(a, b):
                    continue
                joined = works_service.make_work(a.entries + b.entries, author_language=author_language,
                                                 pen_names=set(), author=author)
                joined.kind, joined.why = a.kind, a.why
                out[first] = joined
                del out[second]
                changed = True
                break
            if changed:
                break
    return out


def own_titles(entries: list[Entry]) -> None:
    """The language of the title of a work of Open Library that has editions in several: the language of an edition of
    it that carries the same title (a work carries the title of the language it was written in, or of the one it is
    best known in). Left empty when no edition says."""
    by_work: dict[tuple[str, str], list[Entry]] = {}
    for entry in entries:
        if entry.work_key and entry.language and not entry.signals.get("work_level"):
            by_work.setdefault((entry.source, entry.work_key), []).append(entry)
    for entry in entries:
        if entry.language or not entry.signals.get("work_level"):
            continue
        key = compare_key(entry.title)
        same = [other.language for other in by_work.get((entry.source, entry.work_key), [])
                if compare_key(other.title) == key]
        if same:
            entry.language = same[0]


def language_of_author(members: list[Entry]) -> str:
    """The language the author writes in: what the translations say they were made from; else the language most of
    the records are in, a work weighted by how many editions it has (a single foreign edition that a catalogue files as
    a work of its own does not make its language the author's)."""
    said = Counter(entry.original_language for entry in members if entry.original_language)
    if said:
        return said.most_common(1)[0][0]
    votes: Counter[str] = Counter()
    for entry in members:
        if entry.language and entry.language != "und":
            votes[entry.language] += 1 + max(0, entry.weight) if entry.signals.get("work_level") else 1
    return votes.most_common(1)[0][0] if votes else ""


def distinct_titles(hits: list[Hit]) -> None:
    """Two books of one author that the core of their titles would show alike (``Ember - The Desert Road`` and ``Ember -
    The Messenger``: the core of both is ``Ember``) are shown with the whole title a record gives in that language."""
    seen: dict[tuple[int, str], list[Hit]] = {}
    for hit in hits:
        if hit.label == "work":
            seen.setdefault((hit.cluster, compare_key(hit.title)), []).append(hit)
    for same in seen.values():
        if len(same) < 2:
            continue
        for hit in same:
            language = hit.language or hit.work.orig_language
            mine = [str(entry.signals.get("shown_title") or entry.title) for entry in hit.work.entries
                    if (entry.language or hit.work.orig_language) == language]
            if any(fold(text) == fold(hit.title) for text in mine):
                # (A record carries the title as it is: this one is the book of that name.)
                continue
            whole: Counter[str] = Counter()
            for text in mine:
                if "(" in text or "[" in text:
                    continue
                rest = fold(text).removeprefix(fold(hit.title))
                if titles.edition_tail(rest) or pile_of(Entry(source="", key="", title=text)) != "work":
                    continue
                whole[text] += 1
            if whole:
                hit.title = min(whole.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0]
                if language in hit.work.titles:
                    # (The work carries it too: a wish looks for this book by it, not by the core both share.)
                    hit.work.titles[language].title = hit.title


#: The first word of a name that is a series or a brand credited as an author (``Die drei Schatten``), not a person.
_NOT_A_PERSON = frozenset({"der", "die", "das", "the", "le", "la", "les", "el", "il", "de", "het", "ein", "eine"})


def _a_person(name: str) -> bool:
    """Whether an author's name is a person's: two words at least, a last name of four letters, no article in front,
    nothing but letters (``Die drei Schatten`` is a series a shop credits as the author), no organisation, no two names
    set against each other (``Graf vs Golem``)."""
    words_ = fold(name).split()
    return (len(words_) >= 2 and len(words_[-1]) >= 4 and words_[0] not in _NOT_A_PERSON and not is_company(name)
            and not re.search(r"[?!#*+=&]", name) and not {"vs", "versus", "gegen"} & set(words_))


def about_another(hits: list[Hit]) -> None:
    """A book whose title names another hit's author, first name (or its initial) and last name, *and* one of that
    author's books (``Raum und Figur in Ada Examples Das Salztor``) is a book about that book, not by its author: a
    study guide. A title that names the person and no book of theirs is a biography of them (``Nina Vale`` about Nina
    Vale), or a book named like a person (a classic whose heroine shares her name with a writer of today): a book of its
    own. Study guides that say so (``Interpretation``, ``Lektürehilfe``) were set apart before (``pile_of``).

    Nor is a book a study guide of a "person" its own author wrote with: a library that credits the heroine beside the
    writer (``Pia Example, Astrid Muster``) makes the heroine an author whose name the writer's own books carry."""
    books: dict[tuple[str, ...], set[str]] = {}
    beside: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
    for hit in hits:
        if hit.authors and hit.label == "work" and _a_person(hit.authors[0]):
            name = tuple(fold(hit.authors[0]).split())
            books.setdefault(name, set()).update(
                key for edition in hit.work.titles.values() if len(key := compare_key(edition.title)) >= 3)
            beside.setdefault(name, set()).update(
                tuple(fold(other).split()) for entry in hit.work.entries for other in names_of(entry))
    for hit in hits:
        if hit.label != "work":
            continue
        own = {tuple(fold(name).split()) for name in hit.authors}
        for edition in hit.work.titles.values():
            words_ = fold(edition.title).split()
            mine = f" {compare_key(edition.title)} "
            if any(_names_in(name, words_) and any(f" {key} " in mine and f" {key} " != mine for key in keys)
                   for name, keys in books.items() if name not in own and not own & beside.get(name, set())):
                hit.label = "guide"
                break


def _names_in(name: tuple[str, ...], words: list[str]) -> bool:
    """Whether a title holds the name: its last name (also with an ending: ``Examples``, or with a slip of the keys:
    ``Bram Stocker's Dracula``) after its first name or the initial of it."""
    first, last = name[0], name[-1]
    for index, word in enumerate(words):
        if ((word.startswith(last) and len(word) <= len(last) + 2) or _near(word, [last])) and any(
                before.startswith(first[0]) for before in words[max(0, index - 3):index]):
            return True
    return False


def join_same_title(found: list[works_service.Work], *, author_language: str,
                    author: str) -> list[works_service.Work]:
    """Two works of one author that carry the same title in two languages are one book (``Krabbe`` in German and in an
    English edition that kept the name): the grouping joins a short title across languages only by the numbers of the
    sources, the author of a search is one already. Not when their titles differ in a language both have, not when
    their places in a series differ. (An edition of Open Library that carries only the name of a series is left out
    before: ``works_of``.)"""
    out = list(found)

    def shared(a: works_service.Work, b: works_service.Work) -> bool:
        for code_a, edition_a in a.titles.items():
            for code_b, edition_b in b.titles.items():
                key = compare_key(edition_a.title)
                if code_a == code_b or key != compare_key(edition_b.title):
                    continue
                if "und" not in (code_a, code_b):
                    return True
                # (A work of a catalogue whose language nobody says, ``Glutrot`` with its translations, and the
                # records of the book in a language under the same title: the title says enough when it is long.)
                if code_a != code_b and len(key.replace(" ", "")) >= 6:
                    return True
        return False

    changed = True
    while changed:
        changed = False
        for first in range(len(out)):
            a = out[first]
            for second in range(first + 1, len(out)):
                b = out[second]
                if not shared(a, b) or not _titles_agree(a, b, subtitles=True):
                    continue
                if a.series_pos is not None and b.series_pos is not None and a.series_pos != b.series_pos:
                    continue
                one_placed = (a.series_pos is None) != (b.series_pos is None)
                if one_placed and _same_core_in_a_language(a, b) and not _shown_alike(a, b):
                    # (A volume and the whole it is named like: ``Die Laternenstraße: Der Hafen`` at place 2.)
                    continue
                if (a.why in ("volume", "named")) != (b.why in ("volume", "named")):
                    # (One volume of a book printed in several and the whole book are two things: ``split_by_place``
                    # parted them.)
                    continue
                joined = works_service.make_work(a.entries + b.entries, author_language=author_language,
                                                 pen_names=set(), author=author)
                joined.kind, joined.why = a.kind, a.why
                out[first] = joined
                del out[second]
                changed = True
                break
            if changed:
                break
    return out


#: Titles many books of one author share (a collection is no book of its own name): never joined by their title.
_COMMON_TITLES = frozenset({"gedichte", "erzahlungen", "erzaehlungen", "briefe", "werke", "tagebucher", "tagebuecher",
                            "essays", "poems", "stories", "novellen", "geschichten", "romane", "schriften", "aufsatze",
                            "reden", "collected stories", "short stories", "selected poems", "letters", "diaries"})


def join_alike(found: list[works_service.Work], *, author_language: str, author: str) -> list[works_service.Work]:
    """Two works of one author with the same title in a language both carry are one book that the grouping kept apart
    (a recording a shop files under a series and the same recording without, an edition of a catalogue credited to the
    last name alone): joined, unless one is a volume of a book printed in several, their places in a series differ, or
    the title is one many books of an author share (``Gedichte``)."""
    def keys(work: works_service.Work) -> set[tuple[str, str]]:
        return {(code, compare_key(edition.title)) for code, edition in work.titles.items()
                if code != "und" and edition.sources and len(compare_key(edition.title).replace(" ", "")) >= 4
                and compare_key(edition.title) not in _COMMON_TITLES}

    out = list(found)
    changed = True
    while changed:
        changed = False
        for first in range(len(out)):
            a = out[first]
            for second in range(first + 1, len(out)):
                b = out[second]
                if (a.why in ("volume", "named")) or (b.why in ("volume", "named")):
                    continue
                if not keys(a) & keys(b) and not _named_alike(a, b) and not _named_alike(b, a):
                    continue
                if a.series_pos is not None and b.series_pos is not None and a.series_pos != b.series_pos:
                    continue
                if not _titles_agree(a, b) or not (_shown_alike(a, b) or _named_alike(a, b) or _named_alike(b, a)):
                    continue
                joined = works_service.make_work(a.entries + b.entries, author_language=author_language,
                                                 pen_names=set(), author=author)
                joined.kind, joined.why = a.kind, a.why
                out[first] = joined
                del out[second]
                changed = True
                break
            if changed:
                break
    return out


def _same_core_in_a_language(a: works_service.Work, b: works_service.Work) -> bool:
    """Whether two works carry the same core of a title in one language (records of both carry it)."""
    return any(code != "und" and edition.sources and code in b.titles and b.titles[code].sources
               and compare_key(edition.title) == compare_key(b.titles[code].title)
               for code, edition in a.titles.items())


def _named_alike(a: works_service.Work, b: works_service.Work) -> bool:
    """Whether a work known by its translations only names its title in a language (the original title they say they
    were made from: ``Glutrot``) that the records of the other carry, whole, in that language: one book, which the
    grouping kept apart because no record of the first is in that language. Not a core both may share: the whole title
    the other's records show (``Glut`` is no title of ``Glut - Der Bote``)."""
    for code, edition in a.titles.items():
        other = b.titles.get(code)
        if code == "und" or edition.sources or other is None or not other.sources:
            continue
        if len(compare_key(edition.title).replace(" ", "")) < 4:
            continue
        shown = {fold(str(text)) for entry in b.entries if (entry.language or b.orig_language) == code
                 for text in (entry.signals.get("shown_title") or entry.title, entry.signals.get("full_title")) if text}
        # (The whole original title a translation names, ``Glanz : das erste Buch der Nacht``, is the whole title of
        # the other's records, ``Glanz - Das erste Buch der Nacht``, whatever the core of either is.)
        named = {fold(entry.original_title) for entry in a.entries if entry.original_title
                 and (entry.original_language or a.orig_language) == code}
        if fold(edition.title) in shown or {key for key in named if len(key.split()) >= 2} & shown:
            return True
    return False


def _shown_alike(a: works_service.Work, b: works_service.Work) -> bool:
    """Whether a record of each of two works shows the same whole title in a language (``Glut - Der Wüstenweg`` and
    ``Glut - Der Bote`` have the same core and are two books)."""
    def shown(work: works_service.Work) -> set[tuple[str, str]]:
        return {((entry.language or work.orig_language), fold(str(entry.signals.get("shown_title") or entry.title)))
                for entry in work.entries if not entry.signals.get("work_level") or entry.language}

    return bool(shown(a) & shown(b))


def recordings_as_series(entries: list[Entry]) -> None:
    """A shop that files the recordings of one book as a series of its name (``Der Glöckner``, places 1, 2, 6 and 7,
    each called ``Der Glöckner``, and no other title in it; three places at least) numbers recordings, not volumes:
    those places are taken off. A series named for its first book has other titles at its other places, and keeps
    them."""
    places: dict[tuple[str, str], set[float]] = {}
    held: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        # (Only a shop's recordings: a comic series whose volumes all carry its name has a number for each book.)
        shop = entry.form == "audio" and entry.source not in series_names.LIBRARIES
        if shop and entry.series and entry.series_pos is not None:
            key = (entry.source, compare_key(entry.series))
            held.setdefault(key, set()).add(compare_key(entry.title))
            if compare_key(entry.title) == key[1]:
                places.setdefault(key, set()).add(entry.series_pos)
    many = {key for key, found in places.items() if len(found) >= 3 and held.get(key) == {key[1]}}
    for entry in entries:
        named = compare_key(entry.series) if entry.series else ""
        if named and (entry.source, named) in many and compare_key(entry.title) == named:
            entry.series, entry.series_pos = "", None
    numbered_parts(entries)


def _numbered(entry: Entry) -> str:
    """The name of a shop's series whose recording is called by it and its place (``Der Glöckner 2`` at place 2 of
    ``Der Glöckner``), folded; else empty."""
    if entry.form != "audio" or entry.source in series_names.LIBRARIES or not entry.series or entry.series_pos is None:
        return ""
    named = compare_key(entry.series)
    return named if named and compare_key(entry.title) == f"{named} {entry.series_pos:g}" else ""


def numbered_parts(entries: list[Entry]) -> None:
    """A shop that sells the reading of a book in parts files them as a series of the book's name, each called by the
    name and its place (``Der Glöckner 1``, ``Der Glöckner 2``, ``Der Glöckner 3``, beside the book ``Der Glöckner`` of
    the same author): parts of a recording, not volumes. Two places at least, and a record of the author that carries
    the name as its title."""
    places: dict[tuple[str, str], set[float]] = {}
    for entry in entries:
        named = _numbered(entry)
        if named and entry.series_pos is not None:
            places.setdefault((entry.source, named), set()).add(entry.series_pos)
    if not places:
        return
    titles = {compare_key(entry.title) for entry in entries if not _numbered(entry)}
    parted = {key for key, found in places.items() if len(found) >= 2 and key[1] in titles}
    for entry in entries:
        if (entry.source, _numbered(entry)) in parted:
            entry.signals = {**entry.signals, "volume_part": True}


def two_books(hits: list[Hit]) -> None:
    """A title that names two books of the author of the hits (``The Road / The Lantern``, ``Lantern & Road``, both of
    them hits) is a box of both."""
    keys: dict[int, set[str]] = {}
    for hit in hits:
        keys.setdefault(hit.cluster, set()).update(compare_key(edition.title) for edition in hit.work.titles.values())
    for hit in hits:
        if hit.label != "work":
            continue
        others = keys.get(hit.cluster, set()) - {compare_key(hit.title)}
        for edition in hit.work.titles.values():
            text = edition.title
            if " / " in text or "/" in text.replace(" / ", ""):
                parts = [part for part in re.split(r"\s*/\s*", text) if part.strip()]
                if len(parts) >= 2 and any(compare_key(part) in others for part in parts):
                    hit.label = "box"
                    break
            parts = [part for part in re.split(r"\s+(?:&|und|and)\s+", text) if part.strip()]
            if len(parts) >= 2 and all(compare_key(part) in others for part in parts):
                hit.label = "box"
                break


def _said_language(entry: Entry) -> None:
    """The language of a single edition as its source says it, unless its title's small words say clearly that it is
    in another (``Das Geheimnis von Penmoor`` filed as English, a German library's record of an English book that
    names no language). The language typed or fetched in never decides it."""
    if entry.signals.get("work_level") or not entry.language or entry.source not in ("openlibrary", "dnb"):
        return
    if entry.source == "dnb" and not entry.signals.get("language_assumed"):
        return
    guess = words.title_language(entry.title)
    small = words.FUNCTION_WORDS.get(entry.language, frozenset())
    if guess and guess != entry.language and not any(word in small for word in fold(entry.title).split()):
        entry.language = guess


def _work_language(entry: Entry, author_language: str) -> None:
    """The language of the own title of a work of a catalogue that has editions in several (Open Library names none):
    the one its small words say, when the work has editions in it; else the author's, when the work has editions in
    it and the title says nothing against it; else none (``und``), so that a title is never shown as one of a
    language it is not in (``The Penmoor Mystery`` is no German title of an author most of whose records are
    German)."""
    if entry.language or not entry.signals.get("work_level"):
        return
    has = [code for code in entry.signals.get("languages") or [] if isinstance(code, str)]
    guess = words.title_language(entry.title)
    if guess and (not has or guess in has):
        entry.language = guess
    elif author_language and (not has or author_language in has) and not guess:
        entry.language = author_language
    else:
        entry.language = "und"


#: The longest a shop's recording of an episode of a play runs (a reading of a book runs for hours).
EPISODE_MINUTES = 150


def play_episodes(hits: list[Hit]) -> None:
    """A recording at a place of a series where the series has a printed book of another title in the same language is
    an episode of the plays made of the series (``Death Note 1`` is the comic, the recording at place 1 of the series is
    its first play, ``Mustererkennung``): a play, not a book. Only a work whose records are all recordings: a library's
    that names no reading of them, or a shop's that runs no longer than an episode (``EPISODE_MINUTES``; a reading of
    the book runs for hours), whoever they credit."""
    printed: dict[tuple[str, float, str], set[str]] = {}
    #: Where the book itself stands in a series, by its title: a printed edition or a shop's reading of it.
    book_at: dict[tuple[str, str, str], set[float]] = {}
    for hit in hits:
        for entry in hit.work.entries:
            if not entry.series or entry.series_pos is None or not entry.language:
                continue
            if entry.form != "audio":
                printed.setdefault((series_core(entry.series), entry.series_pos, entry.language), set()).add(
                    compare_key(entry.title))
            if entry.form != "audio" or (entry.source not in series_names.LIBRARIES
                                         and entry.variant in ("unabridged", "abridged")):
                book_at.setdefault((series_core(entry.series), entry.language, compare_key(entry.title)),
                                   set()).add(entry.series_pos)

    def episode(entry: Entry) -> bool:
        if entry.form != "audio" or entry.variant == "abridged":
            return False
        if entry.source in series_names.LIBRARIES:
            return not entry.variant
        return entry.runtime_min is not None and 0 < entry.runtime_min <= EPISODE_MINUTES

    for hit in hits:
        entries = hit.work.entries
        if hit.label != "work" or not entries or not all(episode(entry) for entry in entries):
            continue
        mine = {compare_key(entry.title) for entry in entries}
        for entry in entries:
            if not entry.series or entry.series_pos is None:
                continue
            titles_there = printed.get((series_core(entry.series), entry.series_pos, entry.language), set())
            if titles_there and not titles_there & mine:
                hit.label = "drama"
                break
            # (The book of that title stands at another place of the series: a library's recordings at this one are
            # the episode of a play series that numbers its cases otherwise, ``Fall 18``.)
            elsewhere = book_at.get((series_core(entry.series), entry.language, compare_key(entry.title)), set())
            if entry.source in series_names.LIBRARIES and elsewhere and entry.series_pos not in elsewhere:
                hit.label = "drama"
                break


def comics_of_their_own(hits: list[Hit]) -> None:
    """A comic is a comic *of a book* when a book of the answer has its title (``Morgenlicht`` and its
    comic): it is set apart. A comic no book of the answer is called like is a book of its own (a manga, a comic
    series)."""
    books = {compare_key(edition.title) for hit in hits if hit.label == "work"
             for edition in hit.work.titles.values()}
    for hit in hits:
        if hit.label != "comic" or any(compare_key(edition.title) in books for edition in hit.work.titles.values()):
            continue
        # (Its whole title may name the book behind the series: ``Nachtfalter: Morgenlicht - Der Comic, Band 1``.)
        said = [f" {compare_key(_said(entry))} " for entry in hit.work.entries]
        if not any(len(key) >= 5 and f" {key} " in text for key in books for text in said):
            hit.label = "work"


#: How many years before every volume of another title a book called like its series came out, to be the book itself
#: and not the whole of the volumes.
CLASSIC_YEARS = 20


def series_named(hits: list[Hit]) -> None:
    """A book without a place whose title is the name of a series of its author whose volumes carry other titles
    (``Die Krone von Varn`` beside ``Der Eisenweg`` and ``Der Silberpass``, volumes 1 and 2 of ``Die Krone von Varn``):
    the whole or any of the volumes. A book of a series with a title of its own is that book, with a place or without.

    A book that gives the series its name is a book: the series has fewer than two volumes of other titles (the
    recordings of a play of the book, ``Stromriss, Teil 1`` and ``Teil 2``; an edition in two volumes of the same
    title),
    or no volume of another title stands at its first place (``Mira Mohnblume`` beside ``Mira Mohnblume geht an
    Bord``, volume 2), or the book came out long before every volume of another title (``CLASSIC_YEARS``: a classic
    printed in volumes later). Only what is a book counts: the
    episodes of a play, a box, the parts of an edition say nothing of the volumes."""
    #: For each author and series: the titles at each place, and the first year each place came out.
    titles_at: dict[tuple[int, str], dict[float, set[str]]] = {}
    years_at: dict[tuple[int, str], dict[float, int]] = {}
    for hit in hits:
        if hit.label != "work":
            continue
        for entry in hit.work.entries:
            place = _place_of(entry)
            if place is None:
                continue
            at = (hit.cluster, series_core(entry.series))
            titles_at.setdefault(at, {}).setdefault(place, set()).add(compare_key(entry.title))
            if entry.year:
                known = years_at.setdefault(at, {})
                known[place] = min(known.get(place, entry.year), entry.year)
    for hit in hits:
        if hit.label != "work" or hit.work.series_pos is not None:
            continue
        core = series_core(hit.title)
        places = titles_at.get((hit.cluster, core))
        if not places:
            continue
        own = {compare_key(hit.title), core} | {compare_key(edition.title) for edition in hit.work.titles.values()}
        others = sorted(place for place, keys in places.items() if keys - own)
        if len(others) < 2 or others[0] > 1:
            continue
        years = [year for place, year in years_at.get((hit.cluster, core), {}).items() if place in others]
        if hit.work.year and years and hit.work.year + CLASSIC_YEARS <= min(years):
            # (A classic printed in volumes long after: ``Die Laternenstraße`` of 1820 beside its parts of 2024.)
            continue
        hit.series_named = True


#: What a record says of the one who made it after a book of another (``nacherzählt von Rune Muster``, ``Adaption und
#: Zeichnungen Rune Muster``): the name that follows made this book (folded).
_MADE_BY = re.compile(r"\b(?:(?:nacherzahlt|neu erzahlt|frei erzahlt|adaptiert|retold|adapted) (?:von|by)|"
                      r"adaption(?: und zeichn\w*)?(?: von| by)?) (?P<who>[a-z][a-z .]*)")


def adapters(entries: list[Entry]) -> set[str]:
    """The last names of the people the records of an answer say made a book after a book of another (from the
    statement of who made a record: each piece between its marks names one)."""
    found: set[str] = set()
    for entry in entries:
        text = entry.signals.get("credits")
        if not isinstance(text, str) or not text:
            continue
        for piece in re.split(r"[;:/]", text):
            for hit in _MADE_BY.finditer(fold(piece)):
                who: list[str] = []
                for word in hit.group("who").split():
                    if word in _NOT_A_NAME:
                        break
                    who.append(word)
                if len(who) >= 2:
                    found.add(who[-1])
    return found


#: Words after a name in a statement of who made a book that start what another person did.
_NOT_A_NAME = frozenset({"ubers", "ubersetzt", "ubersetzung", "aus", "mit", "bilder", "illustriert", "illustrationen",
                         "und", "sprecher", "sprecherin", "gelesen", "regie", "farben", "translated", "illustrated",
                         "with", "and"})


def without_the_source(entries: list[Entry], authors: list[str], made: set[str] | None = None) -> list[str]:
    """The authors of a hit without the author of the book it was made after: a record of it says so (``Fahrenheit
    451``, a comic by Rune Muster ``nach dem Roman von Ada Example``), or its first author is one who made books after
    books of others (``made``, ``adapters``: ``nacherzählt von Rune Muster``). Ada Example wrote another book. The first
    stays."""
    if len(authors) < 2:
        return authors
    lead = fold(authors[0]).split()[-1:]
    if lead and lead[0] in (made or set()):
        return authors[:1]
    named: set[str] = set()
    for entry in entries:
        for name in ("subtitle", "credits"):
            text = entry.signals.get(name)
            if isinstance(text, str) and text:
                named.update(found.group("who") for found in words.MADE_AFTER.finditer(fold(text)))
    if not named:
        return authors

    def made_it(name: str) -> bool:
        last = fold(name).split()[-1:] if fold(name) else []
        return not last or not any(f" {last[0]} " in f" {who} " for who in named)
    return authors[:1] + [name for name in authors[1:] if made_it(name)]


def works_of(entries: list[Entry], languages: list[str], original: bool) -> list[Hit]:
    """The hits the records make, unordered (see the top)."""
    hits: list[Hit] = []
    for entry in entries:
        _said_language(entry)
    own_titles(entries)
    made = adapters(entries)
    for cluster, members in enumerate(by_author(entries)):
        authors = lead_author(members)
        author_language = language_of_author(members)
        lines = titles.taglines(members)
        known = frozenset(compare_key(entry.series) for entry in members if _place_of(entry) is not None)
        for entry in members:
            tidy(entry, names_of(entry), lines, known)
            _work_language(entry, author_language)
        recordings_as_series(members)
        series_names = {name for name, places in series_places(members).items() if len(places) >= 2}
        # (An edition of Open Library that carries only the name of a series of several volumes says nothing of the
        # volume it is: its title would join it to the first volume, which is called so.)
        members = [entry for entry in members if entry.source != "openlibrary" or entry.signals.get("work_level")
                   or series_core(entry.title) not in series_names]
        piles: dict[str, list[Entry]] = {}
        for entry, pile in zip(members, piles_of(members), strict=True):
            piles.setdefault(pile, []).append(entry)
        author = authors[0] if authors else ""
        for pile, items in piles.items():
            built = works_service.build(items, answered={}, author_language=author_language, author=author,
                                        keep_all=True)
            built = split_by_place(built, author_language=author_language, author=author,
                                   places=series_places(members))
            built = join_by_place(built, author_language=author_language, author=author)
            built = join_same_title(built, author_language=author_language, author=author)
            built = join_alike(built, author_language=author_language, author=author)
            for work in built:
                label = pile
                if work.why == "volume":
                    label = "part"
                elif pile == "work":
                    label = "box" if work.kind == "box" else "companion" if work.why == "about" else \
                        "part" if work.why == "sample" else "work"
                hit = _hit(work, label, without_the_source(work.entries, lead_author(work.entries) or authors, made),
                           languages, original, author_language,
                           members)
                if not hit.allowed and author_language and work.orig_language != author_language:
                    # A translation into a language nobody fetches in that could not be joined to its book.
                    continue
                hit.cluster, hit.author_language = cluster, author_language
                hits.append(hit)
    comics_of_their_own(hits)
    about_another(hits)
    two_books(hits)
    play_episodes(hits)
    distinct_titles(hits)
    return hits


# ---- The order ----------------------------------------------------------------------------------------------------


#: Words that join two words of a title or come before it: a title is found without them (``Salt and Ashes`` is
#: ``Salt & Ashes``, ``The Lantern`` is ``Lantern``).
SMALL_WORDS = frozenset({"and", "und", "et", "och", "the", "a", "an", "der", "die", "das", "ein", "eine", "le", "la",
                         "les", "el", "il"})


def _words(text: str) -> list[str]:
    return key_fold(text.replace("&", " and ")).split()


def _found(word: str, words: set[str], starts: list[str]) -> bool:
    """A typed word is found as a whole word, or (three letters and more) as the start of one."""
    return word in words or (len(word) >= 3 and any(item.startswith(word) for item in starts))


#: What a word found with a slip of the keys counts, against one found as typed.
NEAR_WEIGHT = 0.75


def _slips(a: str, b: str, most: int) -> int:
    """How many letters two words differ by (one left out, one too many, one other, two swapped), counted up to
    ``most + 1``."""
    if abs(len(a) - len(b)) > most:
        return most + 1
    before = list(range(len(b) + 1))
    earlier: list[int] = []
    for i, char_a in enumerate(a, start=1):
        row = [i] + [0] * len(b)
        for j, char_b in enumerate(b, start=1):
            row[j] = min(before[j] + 1, row[j - 1] + 1, before[j - 1] + (char_a != char_b))
            if i > 1 and j > 1 and char_a == b[j - 2] and a[i - 2] == char_b:
                row[j] = min(row[j], earlier[j - 2] + 1)
        if min(row) > most:
            return most + 1
        earlier, before = before, row
    return before[-1]


def _near(word: str, starts: list[str]) -> bool:
    """A typed word of four letters and more that a word of the hit is with a slip of the keys (``Hary``, ``Poter``,
    ``Frankenstain``): one letter off, two from eight letters on, the first letter right."""
    if len(word) < 4 or not word.isalpha():
        return False
    most = 2 if len(word) >= 8 else 1
    return any(item[:1] == word[:1] and _slips(word, item, most) <= most for item in starts if len(item) >= 3)


def _score(word: str, words: set[str], starts: list[str]) -> float:
    if _found(word, words, starts):
        return 1.0
    return NEAR_WEIGHT if _near(word, starts) else 0.0


def _number_words(text: str) -> list[str]:
    """The words of a title, its numbers as numbers (``01`` is ``1``), without the words of a volume (``Band``)."""
    return [str(int(word)) if word.isdigit() else word for word in _words(text) if word not in VOLUME_WORDS]


def _joined(key: str) -> str:
    """A folded name without the words that join two names (``Bibi und Tina`` is ``Bibi & Tina``)."""
    return " ".join(word for word in key.split() if word not in ("und", "and", "et", "och", "y"))


def volume_fit(hit: Hit, asked: Asked, texts: list[str]) -> int:
    """How well a hit is the volume asked for (``Death Note 1``): 4 when it is that volume of the series of exactly the
    typed name, or its title is the typed name and the number (``Death Note 01``, a library's ``Reihe 1`` whose title is
    no other); 3 when it is that volume of a series whose name holds the typed words; 2 when it is that volume of
    another series (a spin-off of the series, ``Reihe - Before the Fall``, is one); 1 when its title only holds the
    number (``Lantern 2``); else 0."""
    work = hit.work
    number = asked.volume
    if number is None:
        return 0
    base = [str(int(word)) if word.isdigit() else word for word in asked.base if word not in VOLUME_WORDS]
    wanted = base + [f"{number:g}"]
    if any(_number_words(text) == wanted for text in texts):
        return 4
    if work.series_pos != number:
        return 1 if any(f"{number:g}" in _number_words(text) for text in texts) else 0
    typed = _joined(series_names.key(" ".join(asked.base)))
    # (The names the records give the series, whole: the work's own is cut to its core, ``Reihe - Before the Fall``.)
    names = {_joined(series_names.key(entry.series)) for entry in work.entries
             if entry.series and entry.series_pos == number}
    if typed in names:
        return 4
    if any(set(typed.split()) <= set(name.split()) for name in names if name):
        return 3
    return 2


def measure(hit: Hit, asked: Asked) -> None:
    """How well a hit fits the typed words (see ``Hit``)."""
    work = hit.work
    typed = asked.base if asked.volume is not None else asked.words
    typed = [word for word in typed if word not in SMALL_WORDS] or typed or asked.words
    author_words = {word for name in hit.authors for word in _words(name)}
    # (``J. K. Ash`` is typed for ``Joanna Kay Ash``.)
    author_words |= {word[0] for word in list(author_words) if word}
    # (A name typed with a slip is the author's too: ``Dracula Bram Stocker``.)
    # (Only a person's name, and only beside words of the title: a series a library credits as the author, ``Dracula vs
    # Frankenstein``, is no name a person typed with a slip.)
    named = list(author_words)
    people_words = [word for name in hit.authors if _a_person(name) for word in _words(name)]
    by_slip = {word for word in typed if word not in author_words and _near(word, people_words)}
    if len(by_slip) >= len(typed):
        by_slip = set()
    titled_words = [word for word in typed if word not in author_words and word not in by_slip] or typed
    # (Every name the records give the series: one of them is in the language typed.)
    series_words = [word for name in dict.fromkeys([work.series, *(entry.series for entry in work.entries)])
                    for word in _words(name)]
    best_all, best_titled, exact = 0.0, 0.0, False
    # (The title shown stands for its language: the core of it may be the one of another book.)
    texts = [hit.title, *(edition.title for code, edition in work.titles.items() if code != hit.language)]
    # (What a record says after the title counts too: ``Lektürehilfe`` is the subtitle of a study guide.)
    subtitles = [word for entry in work.entries if isinstance(entry.signals.get("subtitle"), str)
                 for word in _words(str(entry.signals.get("subtitle")))]
    for text in texts:
        words = _words(text) + series_words + subtitles
        pool = set(words)
        found_titled = sum(_score(word, pool, words) for word in titled_words)
        # (A name is typed with a slip more often than a title: one of the author's counts in full.)
        found_all = sum(1.0 if word in by_slip else _score(word, pool | author_words, words + named)
                        for word in typed)
        best_titled = max(best_titled, found_titled / len(titled_words))
        best_all = max(best_all, found_all / len(typed))
    # Exactly the title typed: as the shown title, or as the whole title a record of it carries (a core can be the
    # title of another book of the series).
    wanted = [word for word in titled_words if word not in SMALL_WORDS]
    shown = [hit.title, *(str(entry.signals.get("shown_title") or entry.title) for entry in work.entries)]
    exact = any(wanted == [word for word in _words(text) if word not in SMALL_WORDS] for text in shown)
    hit.cover, hit.titled, hit.exact = best_all, best_titled, exact
    hit.volume_fits = volume_fit(hit, asked, texts) if asked.volume is not None else 0
    sources = {entry.source for entry in work.entries}
    # (A work of Open Library comes as its own record and as its edition in a language: one book of the catalogue, its
    # number of editions once.)
    units: dict[tuple[str, str], int] = {}
    for entry in work.entries:
        unit = (entry.source, entry.work_key or entry.key)
        units[unit] = max(units.get(unit, 0), max(0, entry.weight))
    hit.known = math.log1p(sum(units.values())) + 1.5 * len(sources) + 1.5 * math.log1p(len(units))
    ranks = [entry.signals.get("rank") for entry in work.entries]
    hit.best_rank = min((r for r in ranks if isinstance(r, int)), default=99)
    # The first works Open Library and the shop give are what their own search found best for the words (Open Library
    # also by a title of an edition that is not among the records: ``2048`` for ``Twenty Forty-Eight``).
    for source in ("openlibrary", "audible"):
        first = min((entry.signals.get("rank", 99) for entry in work.entries if entry.source == source
                     and isinstance(entry.signals.get("rank"), int)), default=99)
        if first <= 1:
            hit.known += 1.0 - 0.5 * first
            if source == "openlibrary":
                hit.cover = max(hit.cover, 0.5)


def _fit(hit: Hit) -> tuple[Any, ...]:
    """How well a hit fits: the volume, what of the typed words it holds, then how well known it is (an equal title
    adds to it, but does not beat a book many know: ``Lantern`` is the known book first, not a pamphlet of the name)."""
    return (hit.asked_for, hit.volume_fits, round(hit.cover, 2), round(hit.titled, 2), hit.known + 3 * hit.exact,
            -hit.best_rank)


def _place(hit: Hit) -> float:
    return hit.work.series_pos if hit.work.series_pos is not None else 9e9


def rank(hits: list[Hit], asked: Asked) -> list[Hit]:
    """The hits in the order of a library (see the top), cut to ``MAX_HITS``. A hit that holds less than half of what
    was typed is left out, unless it is another volume of the series of the book asked for."""
    for hit in hits:
        measure(hit, asked)
    # (What a person asks for by its name is no aside: ``Lantern Saga Box`` wants the box.)
    typed = set(fold(asked.text).split())
    wanted = {"work"} | {label for label, words in LABEL_WORDS.items() if words & typed}
    # (A word that is part of the title of a book is no question for what is set apart: ``The Hitchhiker's Guide to
    # the Galaxy`` asks for the novel, not for a guide to it. ``… Folge 1`` and ``… Box`` are.)
    in_titles = {word for hit in hits if hit.label == "work" for edition in hit.work.titles.values()
                 for word in fold(edition.title).split()}
    asked_labels = {label for label in wanted - {"work"} if (LABEL_WORDS[label] & typed) - in_titles}
    for hit in hits:
        hit.asked_for = hit.label in asked_labels
    every_book = [hit for hit in hits if hit.label in wanted and hit.allowed]
    books = [hit for hit in every_book if hit.cover >= 0.5]
    ordered: list[Hit] = []
    best: Hit | None = None
    best_series = ""
    if books:
        best = max(books, key=_fit)
        if not best.exact:
            # (Of one author's books, the one whose title is what was typed: ``Example Hero`` is the book of that name,
            # not ``Example Hero Goes to Sea``, however many editions that one has.)
            same = [hit for hit in books if hit.exact and hit.cluster == best.cluster
                    and (hit.volume_fits, round(hit.cover, 2), round(hit.titled, 2)) >= (
                        best.volume_fits, round(best.cover, 2), round(best.titled, 2))]
            best = max(same, key=_fit) if same else best
        best_series = series_core(best.work.series) if best.work.series else ""
    lead = best

    def in_series(hit: Hit) -> bool:
        return (bool(best_series) and lead is not None and hit.cluster == lead.cluster
                and series_core(hit.work.series) == best_series)

    if best is not None:
        typed = (asked.base if asked.volume is not None else asked.words) or asked.words
        author_words = {word for name in best.authors for word in _words(name)}
        named = set(_words(best.work.series))
        if asked.volume is None and best_series and not best.exact and all(
                _found(word, named, list(named)) for word in typed if word not in author_words):
            # The words name the series and no volume (and no book of that title): the series from its first volume
            # on.
            peers = sorted((hit for hit in books if in_series(hit) and hit.work.series_pos is not None
                            and hit.cover >= best.cover - 0.01), key=_place)
            best = peers[0] if peers else best
        series = sorted((hit for hit in every_book if hit is not best and in_series(hit)),
                        key=lambda hit: (_place(hit), -hit.known))
        taken = {id(best)} | {id(hit) for hit in series}
        more = sorted((hit for hit in books if id(hit) not in taken), key=_fit, reverse=True)
        best.group = "match"
        for group, items in (("series", series), ("more", more)):
            for hit in items:
                hit.group = group
        ordered = [best, *series, *more]
    related = sorted((hit for hit in hits if hit.label not in wanted and hit.allowed and (hit.cover >= 0.5 or in_series(
        hit))), key=_fit, reverse=True)
    elsewhere = sorted((hit for hit in hits if not hit.allowed and hit.cover >= 0.5), key=_fit, reverse=True)
    for hit in related:
        hit.group = "related"
    for hit in elsewhere:
        hit.group = "elsewhere"
    room = MAX_HITS - min(len(related), MAX_RELATED) - min(len(elsewhere), MAX_ELSEWHERE)
    found = ordered[:room] + related[:MAX_RELATED] + elsewhere[:MAX_ELSEWHERE]
    unique_keys(found)
    return found


def _shop_found(hit: Hit) -> bool:
    """Whether the shop gave the hit among its first answers to the words (it searches the original titles too)."""
    return any(entry.source == "audible" and isinstance(entry.signals.get("rank"), int)
               and entry.signals["rank"] <= 2 for entry in hit.work.entries)


def _places_of(hit: Hit) -> set[float]:
    return {entry.series_pos for entry in hit.work.entries if entry.series and entry.series_pos is not None
            and _place_of(entry) is not None}


def join_shop_translations(hits: list[Hit], asked: Asked, *, languages: list[str], original: bool) -> list[Hit]:
    """A book typed by its title in one language, and the one recording of the same author in another language that the
    shop gave first for those words although its title holds none of them (``The Example Patient`` and ``Die stumme
    Patientin``): the shop found it by the original title it keeps, and the two are one book that no number of the
    sources joins. Only when the author has exactly one such recording, neither has a title in the other's language,
    and their places in a series do not differ."""
    for hit in hits:
        measure(hit, asked)
    out = list(hits)
    for typed in [hit for hit in hits if hit.label == "work" and hit.exact and hit.titled >= 1.0]:
        if typed not in out:
            continue
        mine = {code for code in typed.work.titles if code != "und"}
        found = [hit for hit in out if hit is not typed and hit.cluster == typed.cluster and hit.label == "work"
                 and hit.titled < 1.0 and _shop_found(hit) and not mine & set(hit.work.titles)
                 and not {code for code in hit.work.titles if code != "und"} & mine
                 and not (_places_of(hit) and _places_of(typed) and _places_of(hit) != _places_of(typed))]
        if len(found) != 1:
            continue
        other = found[0]
        author = typed.authors[0] if typed.authors else ""
        work = works_service.make_work(typed.work.entries + other.work.entries, author_language=typed.author_language,
                                       pen_names=set(), author=author)
        work.kind, work.why = typed.work.kind, typed.work.why
        joined = _hit(work, "work", typed.authors, languages, original, typed.author_language, work.entries)
        joined.cluster, joined.author_language = typed.cluster, typed.author_language
        out[out.index(typed)] = joined
        out.remove(other)
    return out


def build(entries: list[Entry], asked: Asked, *, languages: list[str], original: bool) -> list[Hit]:
    """The hits of a search from the records the sources gave, in their order."""
    hits = works_of(entries, languages, original)
    hits = join_shop_translations(hits, asked, languages=languages, original=original)
    series_named(hits)
    found = rank(hits, asked)
    as_written(found)
    kept_titles(found, asked)
    return found


def kept_titles(hits: list[Hit], asked: Asked) -> None:
    """A translation that keeps the original title and adds a title of its own (``Lantern Girl - Das stille Haus``) is
    shown with both when the words typed are that title of its own and none of the original (``Das stille Haus``): the
    reader knows the book by it (``titles.own_subtitle``). Only what is shown; the search and a wish go by the title the
    records carry."""
    for hit in hits:
        work = hit.work
        orig = work.orig_language
        if hit.label != "work" or not hit.language or hit.language == orig or orig not in work.titles:
            continue
        if compare_key(work.titles[orig].title) != compare_key(hit.title):
            continue
        mine = [entry for entry in work.entries if (entry.language or orig) == hit.language]
        added = titles.own_subtitle(mine, hit.title)
        if not added or compare_key(added) in compare_key(hit.title):
            continue
        author_words = {word for name in hit.authors for word in _words(name)}
        typed = [word for word in (asked.base or asked.words) if word not in author_words and word not in SMALL_WORDS]
        own, kept = set(_words(added)), set(_words(hit.title))
        if typed and all(word in own for word in typed) and not any(word in kept for word in typed):
            hit.title = f"{hit.title} - {added}"


#: A word with letters in brackets inside it (``Bis(s)``).
_BRACKETED_WORD = re.compile(r"[^\W\d_]+\([^\W\d_]{1,3}\)[^\W\d_]*")


def as_written(hits: list[Hit]) -> None:
    """A word the records of the title's language write with letters in brackets inside it (``Bis(s) zum
    Morgenlicht``) is shown so. The search compares it without the brackets (``readers.names.IN_WORD_BRACKET``), so
    this comes last."""
    for hit in hits:
        language = hit.language or hit.work.orig_language
        spelled: Counter[str] = Counter()
        for entry in hit.work.entries:
            if (entry.language or hit.work.orig_language) == language:
                raw = entry.signals.get("raw_title")
                spelled.update(_BRACKETED_WORD.findall(raw if isinstance(raw, str) and raw else entry.title))
        for word, _count in spelled.most_common():
            plain = word.replace("(", "").replace(")", "")
            hit.title = re.sub(rf"(?<!\w){re.escape(plain)}(?!\w)", lambda _m, word=word: word, hit.title, count=1)


__all__ = ["GROUPS", "LABELS", "MAX_HITS", "Hit", "build", "by_author", "editions_of", "measure", "pile_of", "rank",
           "works_of"]
