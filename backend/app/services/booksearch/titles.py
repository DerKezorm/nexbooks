"""What a shop or a library writes around the title of a book, taken off before the works are made: the records of one
book must carry one title in a language, or they are made into several works.

Seen in the answers of Open Library, the German National Library and Audible: a reader in
front (``Ada Example liest Die Laternenstraße``), the author in front (``Falkner. Die Laternenstraße``), the series and
its number in front (``Lantern Saga 01: The Road``, ``Lantern Saga 2. The Road``), the edition behind a full stop
(``The Road. Sonderausgabe``, ``. In klarer, moderner Sprache``, ``. Eine Novelle``), the language of an easy edition
(``in einfacher Sprache``), the second half of a double title (``Mira oder Die seltsame Reise``), the volume at
the end (``The Road, Book 2``). And the years of a person's life beside a name (``Falkner, Rune, 1950-``).
"""

from __future__ import annotations

import re

from ..readers.names import compare_key, core_title, display_name
from ..readers.text import clean, fold
from ..sources.model import Entry
from .words import COMIC, EDITION_WORDS, NO_SERIES_END, NO_TITLE_WORDS, title_like, volume_of

#: A name that names nobody.
NOBODY = frozenset({"no author", "unknown", "anonymous", "anonym", "various", "various authors", "verschiedene",
                    "diverse", "nn", "n n", "collectif", "autorenkollektiv", "unbekannt"})
_LIFE = re.compile(r",*\s*\(?\s*\b\d{3,4}\s*-\s*(?:\d{3,4})?\s*\)?")


#: Degrees a shop writes behind a name (folded, without their dots).
_DEGREES = frozenset({"phd", "md", "dr", "prof", "ma", "msc", "mba"})


def _once(name: str) -> str:
    """A name with a part a source wrote twice written once (``Rune Falkner Falkner``, ``R. J R. J. Falkner``,
    ``Rune Falkner Ph.D. Ph.D.``), and without a degree at its end."""
    words = name.split()
    keys = [word.replace(".", "").casefold() for word in words]
    changed = True
    while changed:
        changed = False
        for size in range(len(keys) // 2, 0, -1):
            for start in range(len(keys) - 2 * size + 1):
                if size == 1 and len(keys[start]) == 1:
                    # (An initial comes twice by right: ``George R. R. Example``.)
                    continue
                if keys[start:start + size] == keys[start + size:start + 2 * size]:
                    # (The second writing is kept: it is the one with its dots, ``R. J.``.)
                    del words[start:start + size], keys[start:start + size]
                    changed = True
                    break
            if changed:
                break
    while len(words) > 2 and keys[-1] in _DEGREES:
        words.pop()
        keys.pop()
    return " ".join(words).strip(" ,")


def name_of(name: str) -> str:
    """A name the way it reads, without the years of the life (``Falkner, Rune, (1950-)`` is ``Rune Falkner``), with
    no part of it twice (``_once``)."""
    text = _LIFE.sub(" ", clean(name, 200))
    return _once(display_name(" ".join(text.split()).strip(" ,")))


def names_of(entry: Entry) -> list[str]:
    """The authors of a record (at most four) the way they read; names that name nobody left out."""
    names = [name_of(name) for name in entry.authors[:4]]
    return [name for name in names if name and fold(name) not in NOBODY]


#: The author's whole name in front of the title (``Ada Falkner, Die Straße``, ``Ada Falkner's Die Straße``, ``Ada
#: Falkners Die Straße``).
_OWN_NAME = re.compile(r"^(?P<name>[^\W\d_][\w.\- ]{3,60}?)(?:\s*[,:]\s+|['’´`]?s\s+)(?P<rest>\S.*)$")
#: A recording read by somebody, written in front of the title.
_READER = re.compile(r"^(?P<who>[^\W\d_][\w.'\- ]{2,60}?) (?:liest|lesen|reads|lit|legge|lee)\.{0,3} (?P<title>\S.+)$")
#: ``Lantern Saga 01: The Road``, ``Lantern Saga 2. The Road``, ``Lantern Saga 2 - The Road``.
_SERIES_FRONT = re.compile(r"^(?P<series>[^\d:]+?)[\s,]+(?:band|bd\.?|book|vol\.?|teil|tome|folge)?\s*(?P<pos>\d{1,3})"
                           r"\s*(?::|\.|\s-|\s–)\s*(?P<title>[^\W\d_].*)$", re.IGNORECASE)
#: A title that is only a series and its number (``Lantern Saga 01``), the title of the book being the subtitle.
_SERIES_ONLY = re.compile(r"^(?P<series>[^\W\d][^\d]*?)[\s,]+(?:band|bd\.?|book|vol\.?|teil|tome)?\s*(?P<pos>\d{1,3})$",
                          re.IGNORECASE)
#: A volume a shop writes at the end of a title (``The Road, Book 2``, ``The Road - Band 2``).
_VOLUME_AT_END = re.compile(r"[\s,.:;-]+(book|band|bd\.?|tome|vol\.?|volume|teil|livre|libro|deel|del|bind|osa|#)\s*"
                            r"(\d{1,3})\s*$", re.IGNORECASE)
#: ``Die Krone von Varn. Zweiter Teil: Der Silberpass``: a part written as a word.
_ORDINAL_PART = re.compile(r"^(?P<series>.+?)[.,:]\s*(?P<ord>erster|zweiter|dritter|vierter|f[uü]nfter|sechster|"
                           r"siebter|achter|neunter|zehnter)\s+(?:teil|band)\s*[:.\-–]\s*(?P<title>\S.*)$",
                           re.IGNORECASE)
_ORDINALS = {"erster": 1, "zweiter": 2, "dritter": 3, "vierter": 4, "funfter": 5, "fuenfter": 5, "sechster": 6,
             "siebter": 7, "achter": 8, "neunter": 9, "zehnter": 10}
#: A text that names only the form of a book (``Psychothriller``, ``Historischer Roman``, ``Ein Krimi``).
_FORM_ONLY = re.compile(r"^(?:(?:ein|eine|a|an)\s+)?(?:[^\W\d_]+(?:er|es|en|e)\s+)?"
                        r"(?:psycho|kriminal|liebes|abenteuer)?"
                        r"(?:thriller|krimi|roman|novel|novelle|erz[aä]hlung|lesung|h[oö]rspiel|h[oö]rbuch|audiobook)"
                        r"\.?$", re.IGNORECASE)
#: The first words of a line that says what a book is, not what it is called (``Eine Geschichte aus dem Wald``).
_DESCRIBES = frozenset({"ein", "eine", "einer", "a", "an", "un", "une"})
#: Letters in brackets inside a word (``Bis(s) zum Morgenlicht``): part of the word, not the start of a subtitle.
_IN_WORD = re.compile(r"(?<=[^\W\d_])\(([^\W\d_]{1,3})\)")
#: What cuts a title to its core (``readers.names``): a bracket, a colon, a dash, a bar.
_CUT_MARK = re.compile(r"\s*[(\[]|:(?=\s|$)|\s[-–—](?=\s|$)|\s\|\s")
#: The form named at the end of a title without a mark before it (``Lantern Road Roman``).
_FORM_AT_END = re.compile(r"\s+(roman|novelle|kriminalroman|thriller|a novel)\s*$", re.IGNORECASE)
_EASY = re.compile(r"\s+in\s+(einfacher|leichter|klarer)(\s+moderner)?\s+sprache\s*$", re.IGNORECASE)
#: ``Mira oder Die seltsame Reise``: a double title (the second half is a subtitle).
_DOUBLE = re.compile(r"^(?P<head>.+?)\s+(?:oder|or,?)\s+(?P<tail>(?:die|der|das|the|wie|hin|ein|eine)\s+\S+\s+\S.*)$",
                     re.IGNORECASE)


def edition_tail(tail: str) -> bool:
    """Whether what follows a title names only the edition (``Sonderausgabe``, ``in klarer, moderner Sprache``). A
    number alone names a volume (``Bunte Hefte Nr. 31``), not an edition."""
    words = fold(tail).split()
    return bool(words) and not all(word.isdigit() for word in words) and all(
        word in EDITION_WORDS or word.endswith("ausgabe") or word.isdigit() for word in words)


def keeps_title(cut: str, rest: str = "") -> bool:
    """Whether what is left of a title after cutting it is still a title: four letters at least (fewer only when what
    was cut off names the edition: ``Es. Roman``), a word that is no filler (``Bis``, ``Film``, ``Jahrhundert``, ``Die
    3``), not a number alone."""
    folded = fold(cut)
    if not folded:
        return False
    if rest and edition_tail(rest):
        return sum(char.isalpha() for char in folded) >= 2
    found = folded.split()
    return sum(char.isalpha() for char in folded) >= 4 and not all(
        word in NO_TITLE_WORDS or word.isdigit() for word in found)


def _whole(text: str) -> str:
    """A title whose core would be no title (see ``keeps_title``), written so that nothing cuts it: its marks become
    spaces (``Die 3 ??? - Toteninsel`` is ``Die 3 ??? Toteninsel``)."""
    return " ".join(_CUT_MARK.sub(" ", text).replace(")", " ").replace("]", " ").split())


def core_of(text: str) -> str:
    """The core of a title (``readers.names.core_title``), unless the core would be no title: then the whole title,
    written so that the core of it is itself."""
    cut = core_title(text)
    if cut == text or keeps_title(cut, text[len(cut):].strip(" :-–—([|")):
        return cut
    return _whole(text)


def _series_fits(series: str, title: str) -> bool:
    """Whether ``Reihe 2. Titel`` is a series, a number and a title: the title is one (``keeps_title``) and names no
    form or edition (``Passagier 23. Psychothriller`` is a book with a number in its title), and the series does not end
    with a word that joins it to the number (``Das Geld im 21. Jahrhundert``)."""
    words = fold(series).split()
    return (bool(words) and words[-1] not in NO_SERIES_END and keeps_title(title) and keeps_title(series)
            and not edition_tail(title) and not _FORM_ONLY.match(title.strip()))


#: A subtitle that says what a book was made after, not what it is called (folded): ``nach dem Roman von …``.
_SAYS_WHAT = re.compile(r"(?:frei )?nach (?:dem|der|einem|einer|den)\b|based on\b|after the (?:novel|book)\b")
#: A part named by its number written as a word (folded): ``Des Spiels erster Teil``, ``zweiter Band``.
_ORDINAL_PART_OF = re.compile(r"\b(?:erster|zweiter|dritter|vierter|funfter|sechster|first|second|third)\s+"
                              r"(?:teil|band|part|book|volume)\b")
#: The sources whose records are a library's.
_LIBRARIES = ("dnb", "bnf", "libris", "kb", "nb")
#: The words in front of the number of a volume (folded).
_VOLUME_WORDS = frozenset({"band", "bd", "teil", "folge", "book", "buch", "vol", "volume", "tome", "nr"})
#: ``Iron Gate - Flammenkuss``: a title with what follows its dash.
_DASHED = re.compile(r"^(?P<head>.+?)\s[-–]\s(?P<tail>[^-–]+)$")


def taglines(entries: list[Entry]) -> frozenset[str]:
    """What a shop writes behind the dash of the titles of several books of a series (``Fourth Gate - Flammenkuss``,
    ``Iron Gate - Flammenkuss``): the name the series goes by in a language, no title of a book (folded)."""
    heads: dict[str, set[str]] = {}
    alone: set[str] = set()
    for entry in entries:
        full = entry.signals.get("full_title")
        text = full if isinstance(full, str) and full else entry.title
        found = _DASHED.match(_IN_WORD.sub(r"\1", text))
        if found is None:
            alone.add(fold(text))
        elif keeps_title(found.group("tail")):
            # (``Reihe - Titel`` and ``Reihe 1 - Titel`` are one head: one book, not a line behind several.)
            head = " ".join(word for word in fold(found.group("head")).split()
                            if not word.isdigit() and word not in _VOLUME_WORDS)
            heads.setdefault(fold(found.group("tail")), set()).add(head)
    # (What other records carry as their whole title is the title of a book: ``Mias Heft - Von Riesen umzingelt!``
    # beside ``Von Riesen umzingelt!``.)
    return frozenset(tail for tail, found in heads.items() if len(found) >= 2 and tail not in alone)


def tidy(entry: Entry, authors: list[str], lines: frozenset[str] = frozenset(),
         series: frozenset[str] = frozenset()) -> None:
    """Takes off the title of a record what a shop or a library wrote around it (see the top); a series and its number
    found in front of the title become the record's when it names none. ``authors``: the names of the record;
    ``lines``: what stands behind the dash of several books (``taglines``); ``series``: the series the author's other
    records name (keys), so that ``Reihe - Titel`` of a record that names none is the book ``Titel``."""
    full = entry.signals.get("full_title")
    whole = isinstance(full, str) and bool(full)
    # (What the record said before, for what it says it is: ``Detektiv Kenta - 5. Film`` is the film's.)
    entry.signals = {"raw_title": entry.title[:300], **entry.signals}
    text = _IN_WORD.sub(r"\1", full if isinstance(full, str) and full else entry.title)
    if entry.original_title:
        named = _SERIES_FRONT.match(entry.original_title)
        if named is not None and _series_fits(named.group("series"), named.group("title")):
            # (``The Lord of the Rings 2. The Two Towers``: the original title of the volume, not of the series.)
            entry.original_title = named.group("title")
    ordinal = _ORDINAL_PART.match(text)
    if ordinal is not None and entry.series_pos in (None, _ORDINALS.get(fold(ordinal.group("ord")))):
        entry.series = entry.series or ordinal.group("series").strip(" ,.")
        entry.series_pos = float(_ORDINALS.get(fold(ordinal.group("ord")), 0)) or None
        text = ordinal.group("title")
    if entry.form == "audio":
        reader = _READER.match(text)
        if reader is not None and not any(fold(reader.group("who")) == fold(name) for name in authors):
            text = reader.group("title")
    # (Only the first author's: ``Ada Example, Die Straße`` by Ulla Muster is her book about Ada Example's.)
    for name in authors[:1]:
        last = name.split()[-1] if name.split() else ""
        if len(last) >= 4 and fold(text).startswith(fold(last) + " ") and text[len(last):len(last) + 1] in ".:":
            text = text[len(last) + 1:].strip()
            break
        # (``Ada Falkner, Die Laternenstraße`` and ``Ada Falkner's Die Laternenstraße`` of a record by Ada Falkner: the
        # book itself, a shop's way of naming it.)
        own = _OWN_NAME.match(text)
        if own is not None and len(name.split()) >= 2 and fold(own.group("name")) == fold(name) and \
                keeps_title(own.group("rest")):
            text = own.group("rest").strip()
            break
    subtitle = entry.signals.get("subtitle")
    only = _SERIES_ONLY.match(text)
    # (A library writes two things into the subtitle now and then: ``Der Titel | Nach dem Erfolg der Novel …``.)
    named = subtitle.split(" | ")[0].strip() if isinstance(subtitle, str) else ""
    if only is not None and named and not edition_tail(named) and _series_fits(only.group("series"), named) \
            and int(only.group("pos")) < 100 and not _SAYS_WHAT.match(fold(named)):
        # (``Lantern Saga 01`` with the subtitle ``The Road``: the series and its number stand where the title is. A
        # subtitle that is a sentence about the book, or advertising, names no book: ``Lantern Saga 01`` stays the
        # title, with the series and its volume.)
        number = float(only.group("pos"))
        if entry.series_pos is None or entry.series_pos == number:
            entry.series = entry.series or only.group("series").strip(" ,.")
            entry.series_pos = number
            if title_like(named):
                text = named
    elif (entry.source in _LIBRARIES and entry.series and entry.series_pos is not None and named
          and compare_key(text) == compare_key(entry.series)):
        # (A library that writes the work where the title is and the volume's title with its number as the subtitle:
        # ``Die Krone von Varn`` at place 2 of ``Die Krone von Varn``, with the subtitle ``Der Silberpass, 2``.)
        volume = re.sub(rf"[,\s]+0*{entry.series_pos:g}\s*$", "", named)
        if (volume != named and not any(char.isdigit() for char in volume) and _series_fits(entry.series, volume)
                and title_like(volume) and fold(volume).split()[0] not in _DESCRIBES
                and not compare_key(volume).startswith(compare_key(entry.series))
                and not set(fold(volume).split()) & _VOLUME_WORDS):
            text = volume
    front = _SERIES_FRONT.match(text)
    if front is not None and _series_fits(front.group("series"), front.group("title")):
        number = float(front.group("pos"))
        if entry.series_pos is None or entry.series_pos == number:
            entry.series = entry.series or front.group("series").strip(" ,.")
            entry.series_pos = number
            text = front.group("title")
    dashed = _DASHED.match(text)
    if dashed is not None and fold(dashed.group("tail")) in lines and keeps_title(dashed.group("head")):
        # (``Fourth Gate - Flammenkuss`` is ``Fourth Gate``: ``Flammenkuss`` stands behind every book of the series.)
        text = dashed.group("head").strip()
    if entry.series or series:
        prefix, mark, rest = text.partition(". ") if ". " in text else text.partition(": ")
        if not mark and (dash := re.search(r"\s[-–]\s", text)) is not None:
            # (``Lantern Saga - The Road`` after ``Lantern Saga, Band 2:``; not a volume or a comic of its own.)
            prefix, mark, rest = text[: dash.start()], dash.group(), text[dash.end():]
            if volume_of(fold(rest)) is not None or COMIC.search(fold(rest)):
                mark = ""
        # (A record that names no series, whose title starts with a series of the author's other records: ``Reihe -
        # Titel`` of a library is the book ``Titel`` of that series.)
        # (Behind a dash only: ``2Q19. Alle Bücher in einem E-Book`` names what the edition holds.)
        known = (compare_key(prefix) in series and mark.strip() in ("-", "–")) or bool(entry.series) and compare_key(
            prefix) == compare_key(entry.series)
        # (``Mondspiel. Des Spiels erster Teil`` names a part of the book called like the series, not a title.)
        if mark and rest.strip() and known and keeps_title(rest) and not _ORDINAL_PART_OF.search(fold(rest)):
            # ``Reihe. Titel`` (but not ``Reihe – Die Graphic Novel. Band 01``: ``Band 01`` is no title); and
            # ``Rehkitz. Eine Geschichte aus dem Wald`` is the book the series is named for, with a line that says
            # what it is.
            first = fold(rest).split()[:1]
            text = prefix.strip() if first and first[0] in _DESCRIBES else rest.strip()
    while True:
        head, dot, tail = text.rpartition(". ")
        # (``Tinnit. Nach einer Idee von Ada Example``: what follows says what it was made after, not its name.)
        if not dot or not head.strip() or not (edition_tail(tail) or _SAYS_WHAT.match(fold(tail))):
            break
        text = head.strip()
    text = _EASY.sub("", text)
    double = _DOUBLE.match(text)
    if double is not None and len(double.group("head").split()) <= 4 and entry.series_pos is None:
        # (Not in a series: ``Der Mittwochsclub oder Ein Teufel kommt selten allein`` is the fourth book, the
        # first is called ``Der Mittwochsclub``.)
        text = double.group("head")
    form = _FORM_AT_END.search(text)
    if form is not None and form.start() > 0:
        text = text[: form.start()].strip()
    if entry.series and _ORDINAL_PART_OF.fullmatch(fold(text)):
        # (A library's record of a part, ``Erster Teil`` of the series ``Mondspiel``: the part of that book.)
        text = f"{entry.series.strip()}. {text}"
    shown = text
    if entry.source == "audible" and isinstance(subtitle, str) and subtitle and not any(c.isdigit() for c in subtitle) \
            and fold(subtitle) != fold(text):
        # (A shop cuts the title to its core and keeps the rest as the subtitle: ``Lantern: The Road``.)
        shown = f"{text}: {subtitle}"
    entry.signals = {**entry.signals, "shown_title": shown[:300]}
    # (A library's whole title is cut to its core, as the records of a library always are; the title of a catalogue or a
    # shop keeps its subtitle, which tells two books of a series apart.)
    if whole:
        entry.title = core_of(text) or entry.title
    elif not keeps_title(cut := core_title(text), text[len(cut):].strip(" :-–—([|")):
        # (A shop's or a catalogue's title keeps its subtitle; one whose core would be no title is written so that
        # nothing cuts it: ``Die 3 ??? - Toteninsel``.)
        entry.title = _whole(text) or entry.title
    else:
        entry.title = text or entry.title
    volume = _VOLUME_AT_END.search(entry.title)
    if volume is not None and volume.start() > 0 and keeps_title(entry.title[: volume.start()]):
        number = float(volume.group(2))
        if entry.series_pos is None or entry.series_pos == number:
            entry.title = entry.title[: volume.start()].strip()
            if entry.series_pos is None and entry.series:
                entry.series_pos = number


def own_subtitle(entries: list[Entry], title: str) -> str:
    """The title a translation adds to the original title it keeps (``Lantern Girl`` in German is ``Lantern Girl - Das
    stille Haus``): what at least two records of it say after the title, as a subtitle or behind a dash, when it is a
    title (not ``Roman``, not ``Ein Krimi``, not advertising). Empty when they say nothing of the kind."""
    said: dict[str, list[str]] = {}
    for entry in entries:
        found: set[str] = set()
        subtitle = entry.signals.get("subtitle")
        if isinstance(subtitle, str) and subtitle:
            found.add(re.split(r"\s+[;|]\s+|\s*;\s*", subtitle, maxsplit=1)[0].strip(" .,"))
        for name in ("full_title", "raw_title"):
            text = entry.signals.get(name)
            dashed = _DASHED.match(text) if isinstance(text, str) and text else None
            if dashed is not None and compare_key(dashed.group("head")) == compare_key(title):
                found.add(re.sub(r"\s*[(\[].*$", "", dashed.group("tail")).strip(" .,"))
        for text in found:
            if text:
                said.setdefault(fold(text), []).append(text)
    best = ""
    for key, texts in sorted(said.items(), key=lambda item: -len(item[1])):
        first = key.split()[:1]
        if (len(texts) < 2 or not keeps_title(texts[0]) or edition_tail(texts[0]) or _FORM_ONLY.match(texts[0])
                or not title_like(texts[0]) or (first and first[0] in _DESCRIBES)):
            continue
        written = max(texts, key=lambda text: (text[:1].isupper(), texts.count(text)))
        best = written[:1].upper() + written[1:]
        break
    return best


__all__ = ["EDITION_WORDS", "NOBODY", "core_of", "keeps_title", "name_of", "names_of", "own_subtitle", "taglines",
           "tidy"]
