"""Which files of a recording are one chapter.

A recording without chapter marks inside its files (MP3 parts) has one chapter per file: the title tag, else the name of
the file. Archive recordings often cut a chapter over several files (``Kapitel 1 (1/3)``, ``Kapitel 1 (2/3)``,
``Kapitel 1 (3/3)``, ``Kapitel 2 (1/2)`` ...), and a player that counts files as chapters shows 120 where the book
has 40.

The rule is strict, because a chapter that was put together wrongly cannot be told from a right one, while one
chapter too many only costs a tap: **consecutive files are one chapter only when their titles prove it.**

* The same title (white space, case and leading zeros do not count) and no mark of a part in it.
* The same stem with a mark of a part at its end (``(1/3)``, ``[1/3]``, ``1 von 3``, ``1 of 3``, ``- Teil 2``,
  ``Part 2``, ``pt. 2``, ``Track 2``), the number rising by one from file to file. A total that is written must be
  the same everywhere it is written, and no part may be numbered beyond it. A gap, a repeat or a step back ends the
  chapter.
* A stem that is only a word for a part or a chapter (``Kapitel 1/3`` is chapter one of three, not part one of
  ``Kapitel``) proves nothing.

Everything else stays a chapter of its own, as it was. Files with chapter marks inside are not looked at here: they
end a run (``catalog.build_meta``).

**No names at all.** When every file of the row carries the same title (the author, the series, the book: ``Rune
Falkner`` on each of 20 files), or only names of the book, the titles name no chapter: each file is a chapter of its
own **without a title** (``""``), which the interface and the app call ``Teil 1``, ``Teil 2`` … (``label``). A track
of a disc (``Titel CD1 Track 3``) is no part of a chapter either: the disc is no chapter."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_SEPARATORS = r"[\s(\[,:;\-–—_.]"
_OF = r"(?:/|von|of|aus)"
#: ``(1/3)``, ``1 von 3``, ``- 2 of 3``: which part of how many, at the end of a title.
_MARK_OF = re.compile(
    _SEPARATORS + r"+0*(\d{1,3})\s*" + _OF + r"\s*0*(\d{1,3})[\s)\]]*$", re.IGNORECASE)
#: ``- Teil 2``, ``Part 2``, ``pt. 2``, ``Track 2``, ``(Teil 2 von 3)``.
_MARK_WORD = re.compile(
    _SEPARATORS + r"+(?:teil|part|pt|tl|track|trk)\.?\s*0*(\d{1,3})(?:\s*" + _OF + r"\s*0*(\d{1,3}))?[\s)\]]*$",
    re.IGNORECASE)
_STEM_END = " -–—,:;._([\t"
#: Words that name a part or a kind of division and nothing else: a stem made of them alone (and of no number) names no
#: chapter.
_GENERIC = frozenset({
    "kapitel", "kap", "chapter", "ch", "track", "trk", "teil", "part", "pt", "cd", "disc", "disk", "band", "volume",
    "vol", "book", "buch", "abschnitt", "section", "folge", "episode", "lektion", "lesson", "akt", "act", "szene",
    "scene", "seite", "page", "side", "nr", "no", "number", "titel", "title", "audio", "hoerbuch", "hörbuch",
    "audiobook",
    "unknown", "unbekannt", "untitled", "von", "of", "aus", "und", "and",
    # The same words in other languages.
    "capitulo", "capítulo", "capitolo", "chapitre", "hoofdstuk", "rozdział", "kapitola", "parte", "partie", "deel",
    "część", "cz", "disco", "disque",
    "глава", "часть", "трек", "диск", "том", "книга", "раздел"})
_DIGITS = re.compile(r"\d+")
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class Title:
    """A title of a file, read for the mark of a part."""

    text: str
    #: The title without its mark of a part; the whole title when it has no mark (or the stem proves nothing).
    stem: str
    #: ``stem`` as comparing wants it.
    key: str
    #: Which part, and of how many (0 when the title does not say); both 0 without a mark.
    number: int = 0
    total: int = 0

    @property
    def marked(self) -> bool:
        return self.number > 0


def _words(text: str) -> list[str]:
    """The words of a text for comparing: case folded, every script kept (``fold`` keeps Latin letters only, and a
    title in another script would fold to nothing but its numbers), a number by its value (``Kapitel 01`` is
    ``Kapitel 1``)."""
    return [_DIGITS.sub(lambda found: str(int(found.group())), word)
            for word in _WORD.findall(unicodedata.normalize("NFC", text).casefold())]


def _key(text: str) -> str:
    return " ".join(_words(text))


def _proves_nothing(stem: str) -> bool:
    """No word that names anything: empty, numbers alone, or words for a part or a kind of division and no number
    (``Kapitel``, ``Track``, ``Teil``)."""
    words = _words(stem)
    return not any(not word.isdigit() for word in words) or all(word in _GENERIC for word in words)


#: ``Titel CD1``, ``Disc 2``: the name of a disc, which a ``Track 3`` after it is a track of, not a part of a chapter.
_DISC_STEM = re.compile(r"(?:^|[\s(\[,:;\-–—_.])(?:cd|disc|disk|seite|side)\s*0*\d{1,3}$", re.IGNORECASE)
#: Words that make a name of the book into the name of its series: ``Quist-Reihe`` for the series ``Quist``.
_SERIES_WORDS = frozenset({"reihe", "serie", "series", "saga", "zyklus", "trilogie", "trilogy", "folge", "folgen"})
#: What a chapter without a name is called, by the language of the book (``label``).
PART_WORD = {"de": "Teil", "en": "Part", "fr": "Partie", "es": "Parte", "it": "Parte", "pt": "Parte", "nl": "Deel",
             "sv": "Del", "no": "Del", "da": "Del", "fi": "Osa", "pl": "Część"}


def label(title: str, number: int, language: str) -> str:
    """The title of a chapter as it is shown: its own, or ``Teil 3`` (``Part 3`` …, by the language of the book; English
    when it is not known) for a chapter whose files carry no name of it."""
    if title:
        return title
    return f"{PART_WORD.get((language or '')[:2].lower(), 'Part')} {number}"


def read_title(text: str) -> Title:
    """The title with the mark of a part taken off its end, if it has one a stem can stand on."""
    plain = Title(text=text, stem=text, key=_key(text))
    for pattern in (_MARK_WORD, _MARK_OF):
        found = pattern.search(text)
        if found is None:
            continue
        number = int(found.group(1))
        total = int(found.group(2) or 0)
        stem = text[:found.start()].rstrip(_STEM_END).strip()
        if number < 1 or (total and (total < 2 or number > total)) or _proves_nothing(stem) or _DISC_STEM.search(stem):
            return plain
        return Title(text=text, stem=stem, key=_key(stem), number=number, total=total)
    return plain


def group(titles: list[str], book_titles: Iterable[str] = ()) -> list[tuple[int, int, str]]:
    """Cuts a row of titles (of consecutive files, none with chapters inside) into chapters: ``(index of the first file,
    how many files, title)``. A chapter of one file keeps its title as it is; one of several files takes the stem.

    ``book_titles``: the names of the book itself (its title, the album tag, the folder). A file whose title is the
    name of the book (or that name with a mark of a part: ``Salt Marsh 3 of 12``) is a part of the book and says nothing
    about a chapter, so it stays a chapter of its own, as it would be without this rule."""
    read = [read_title(text) for text in titles]
    book = {key for key in (_key(text) for text in book_titles) if key}

    def of_book(key: str) -> bool:
        if key in book:
            return True
        words = [word for word in key.split() if word not in _SERIES_WORDS]
        return bool(words) and " ".join(words) in book

    same = len({_key(item.text) for item in read}) == 1
    if len(read) >= 2 and (same or all(of_book(_key(item.text)) for item in read)):
        # The same title on every file, or only names of the book: no chapter has a name.
        return [(index, 1, "") for index in range(len(read))]
    out: list[tuple[int, int, str]] = []
    first = 0
    while first < len(read):
        head = read[first]
        last = first
        if of_book(head.key):
            out.append((first, 1, head.text))
            first += 1
            continue
        total = head.total
        while last + 1 < len(read):
            nxt = read[last + 1]
            if head.marked:
                if not nxt.marked or nxt.key != head.key or nxt.number != read[last].number + 1:
                    break
                if total and nxt.total and nxt.total != total:
                    break
                total = total or nxt.total
                if total and nxt.number > total:
                    break
            else:
                if nxt.marked or nxt.key != head.key or _proves_nothing(head.text):
                    break
            last += 1
        count = last - first + 1
        out.append((first, count, head.stem if count > 1 else head.text))
        first = last + 1
    return out
