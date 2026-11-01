"""Holding a release name against the book that is looked for: is the author in it, the title, the
volume, and which language does it seem to be in.

Lessons of the measurements:

* **The author never by the last name alone.** ``Ida Falkner - Nebelchor`` is no book of Rune Falkner: the last name
  and a first name or its initial, next to each other in either order (``Rune Falkner``, ``Falkner, Rune``,
  ``Falkner Rune``, ``R. Falkner``). A narrator in front of the author (``Ida Leser Rune Falkner-Titel``, ``Ida Leser
  liest Rune Falkner``) is no harm.
* **The title by its core** (``readers.names.core_title``: up to the first bracket, colon or dash, without
  ``Roman`` or ``Thriller``), word by word in the order of the name, spelled in either way (``ä`` as ``a`` or ``ae``,
  ``ß`` as ``ss``), a long word with a letter more or less (a name that lost a letter on the way). A title of one word
  must stand as it is.
* **The title as a whole**: the field of the name the title stands in (up to the next dash, bracket, colon or comma)
  holds nothing else than what may stand beside a title: the author, a reader, the series and a volume, a year, tags,
  marks of a language, words for the kind of book or its edition, and the subtitle the catalogue knows. Any other word
  makes it another book (``Wir wollen Ruß sehen`` is no ``Ruß``). One rule for the
  names of releases and the titles of files (``title_is``).
* **Every title the book is known by** counts (the title of each edition, the original title), each with its language
  when known: a release that holds only the title of one language seems to be in that language.
* **Another volume**: a number the name gives as a volume (``Band 3``, ``Reihe 04``) that is not the one looked for,
  or the name of the series with another number after it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

from .. import book_words, series_names
from ..readers import names
from ..readers.text import fold, key_fold
from . import release_name
from .release_name import _WORD_SPAN, ReleaseName, is_tag, spaced, words_of

_ARTICLES = frozenset({"der", "die", "das", "ein", "eine", "the", "a", "an", "le", "la", "les", "l", "el", "il", "lo",
                       "un", "une", "una", "los", "las", "het", "de", "een", "den", "dem", "des"})


@dataclass(frozen=True)
class Title:
    text: str
    #: The language of this title when known (the language of the edition that carries it).
    language: str = ""


@dataclass(frozen=True)
class Book:
    """The book a release is held against."""

    titles: tuple[Title, ...]
    authors: tuple[str, ...]
    series: str = ""
    position: float | None = None
    #: Whether the book looked for is a bundle itself (``Reihe 1-4``): then a bundle is no reason against a release.
    bundle: bool = False
    #: Titles of other books of the author (the library, the catalogue): a name or a file that holds one of them as a
    #: whole is that book, even where the title looked for stands inside it.
    others: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    author: bool
    title: bool
    #: The language of the title that matched, when only titles of that language matched.
    title_language: str
    #: ``other_volume`` when the name gives another volume of the series.
    other_volume: bool
    #: The volumes the name gives (for the reasons).
    volumes: tuple[float, ...] = field(default=())
    #: The words (from, to) of the name that are the title looked for: a mark of a language in there belongs to the
    #: title (``The Dutch Garden``).
    title_spans: tuple[tuple[int, int], ...] = field(default=())
    #: The title is there, but more words stand with it in its field (``Der Ring der Fünf`` for ``Der Ring``, ``Wir
    #: wollen Ruß sehen`` for ``Ruß``): another book.
    longer: bool = False
    #: The name is the title of another book of the author (``Book.others``).
    other_book: bool = False
    #: The words (from, to) of the name that are an author of the book: a word of a language in there is part of a name
    #: (``Lena French``, ``Tom Deutsch``), no mark.
    author_spans: tuple[tuple[int, int], ...] = field(default=())
    #: The words (from, to) of the name that are the series of the book: a word of a language in there is part of its
    #: name (``Die Deutschstunde-Reihe``), no mark.
    series_spans: tuple[tuple[int, int], ...] = field(default=())
    #: What stands after the title that matched (the words of the name from the end of its place on): where a part
    #: (``Teil 2 von 2``) or a number of another volume (``Erebos 3``) is looked for.
    tail: str = ""
    #: Words outside the title's field that nothing explains (not a tag, the author, a reader, the series, a volume, the
    #: kind of book): ``before`` the title (where the author stands: another author when the author is not found),
    #: ``after`` it (another field: a subtitle of another book, ``Der Räuber Hotzenplotz - Schluss mit der Räuberei``).
    unexplained_before: bool = False
    unexplained_after: bool = False
    #: A number right after the title that is no part of it, no year and no volume the book has (``Erebos 3``,
    #: ``QualityLand 2.0``): perhaps another volume of a series nobody named.
    number_after: float | None = None
    #: The name holds only the end of a title of the book (``Tom Sawyer`` for ``Die Abenteuer des Tom Sawyer``): a
    #: short form, perhaps another book.
    short: bool = False
    #: A field between the author and the title that nothing explains (a title in another language, a subtitle).
    unexplained_between: bool = False
    #: Fields before the title that are a number and nothing else (``Zwergen-Saga - 2 - Die Tenfel``): a volume.
    field_numbers: tuple[float, ...] = ()
    #: The language the little words outside the title and the author are of (``Das letzte Gefecht``, ``Aan de grond
    #: in Londen en Parijs``), empty when they say none (``language_words``).
    words_language: str = ""
    #: The language of the article right before the title (``The Hobbit``: ``en``), empty without one.
    article_language: str = ""
    #: The author stands in a form that is close and not sure (``AB Feldkamp``, ``Feldkamp, Anna Berit``).
    author_close: bool = False
    #: The title wished stands whole with a subtitle after a full stop or ``oder`` in its field (``Das Parfum. Die
    #: Geschichte eines Mörders``, ``Momo oder Die seltsame Geschichte …``): perhaps the book, perhaps another one.
    subtitle_after: bool = False


def _variants(word: str) -> frozenset[str]:
    """A word in every spelling it is compared in: folded, with umlauts written out, and in the spelling old and
    foreign spellings share (``Process`` is ``Prozess``, ``Tolstoy`` is ``Tolstoi``, ``names.spelling_key``)."""
    plain = fold(word).replace(" ", "")
    found = {plain, key_fold(word).replace(" ", "")}
    if len(plain) >= 4 and not plain.isdigit():
        found.add("~" + names.spelling_key(word))
    return frozenset(found - {""})


def _close(x: str, y: str, ratio: float) -> bool:
    """Two spellings of one word: the same first letter, alike letter for letter, and neither one the other with two
    letters or more added (``Bergdoktor`` and ``Bergdoktorin`` are two words)."""
    if x[0] != y[0]:
        return False
    if abs(len(x) - len(y)) >= 2 and (x.startswith(y) or y.startswith(x) or x.endswith(y) or y.endswith(x)):
        return False
    return SequenceMatcher(None, x, y).ratio() >= ratio


def _written(variants: frozenset[str]) -> list[str]:
    """The spellings as written (folded), without the keys of ``names.spelling_key`` and of first names: only these
    are held letter for letter."""
    return [variant for variant in variants if variant[:1] not in "~="]


def _same(a: frozenset[str], b: frozenset[str], loose: bool) -> bool:
    if a & b:
        return True
    if not loose:
        return False
    return any(min(len(x), len(y)) >= 5 and _close(x, y, 0.85) for x in _written(a) for y in _written(b))


def _same_long(a: frozenset[str], b: frozenset[str]) -> bool:
    """A title of one word: the same, or a long word with a letter more or less."""
    return bool(a & b) or any(min(len(x), len(y)) >= 8 and _close(x, y, 0.9) for x in _written(a) for y in _written(b))


def _tokens(text: str) -> list[frozenset[str]]:
    return [variants for word in words_of(text) if (variants := _variants(word))]


def _find(haystack: list[frozenset[str]], needle: list[frozenset[str]], taken: set[int],
          loose: bool = True) -> tuple[int, int] | None:
    """The first place ``needle`` stands in ``haystack`` word by word, without a word in ``taken``."""
    if not needle:
        return None
    for start in range(len(haystack) - len(needle) + 1):
        if any(start + offset in taken for offset in range(len(needle))):
            continue
        if all(_same(haystack[start + offset], needle[offset], loose and len(needle) > 1)
               for offset in range(len(needle))):
            return start, start + len(needle)
    return None


def _joined(tokens: list[frozenset[str]]) -> frozenset[str]:
    """The words written together, in each spelling: ``AchtNacht`` is ``Acht Nacht``."""
    return frozenset({"".join(min(_written(token) or token, key=len) for token in tokens),
                      "".join(max(_written(token) or token, key=len) for token in tokens)})


def find_author(release: list[frozenset[str]], author: str) -> tuple[int, int] | None:
    """Where an author stands in the words of a name: the last name with a first name or an initial next to it, in
    either order; a first name in another spelling (``Leo`` for ``Lew``), the group word of siblings (``Brüder
    Grimm``), the last name without its particle when the first name stands between them (``Schirach, Ferdinand
    von``)."""
    found = _find_author(release, author)
    if found is not None:
        return found
    last, first = names.last_first(author)
    pieces = last.split()
    if len(pieces) > 1 and first:
        # ``von Schirach``: the particle may stand after the first name (``Schirach, Ferdinand von``) or be left out.
        core = " ".join(word for word in pieces if word not in names._PARTICLES) or pieces[-1]
        return _find_author(release, f"{core}, {first}")
    return None


def _find_author(release: list[frozenset[str]], author: str) -> tuple[int, int] | None:
    last, first = names.last_first(author)
    release = [token | {"=" + names.first_name_key(plain)} if (plain := min(
        (variant for variant in token if variant[:1] not in "~="), default="")) else token for token in release]
    # The name's own words in both spellings (``last_first`` writes an umlaut out: ``Mörck`` is ``moerck``, and a
    # release may say ``Morck``).
    spelled: dict[str, frozenset[str]] = {}
    for word in words_of(author):
        for key in _variants(word):
            spelled[key] = spelled.get(key, frozenset()) | _variants(word)
    last_words = [spelled.get(word, _variants(word)) for word in last.split() if _variants(word)]
    first_words = [word for word in first.split() if word]
    if not last_words:
        return None
    taken: set[int] = set()
    while True:
        place = _find(release, last_words, taken, loose=False)
        if place is None:
            return None
        start, end = place
        if not first_words:
            return place
        firsts = [spelled.get(word, _variants(word)) | {"=" + names.first_name_key(word)} for word in first_words]
        initials = {word[0] for word in first_words}
        # ``Brüder Grimm``, ``Brothers Grimm``: the siblings by their last name.
        if start > 0 and release[start - 1] & names.GROUP_WORDS:
            return start - 1, end
        # "First Last": the first names right before.
        before = start - len(firsts)
        if before >= 0 and all(_same(release[before + index], firsts[index], False) for index in range(len(firsts))):
            return before, end
        # "Last, First" or "Last First": right after.
        if end + len(firsts) <= len(release) and all(
                _same(release[end + index], firsts[index], False) for index in range(len(firsts))):
            return start, end + len(firsts)
        # "R. Last" or "R Last": an initial right before, or after for "Last, R.".
        if start > 0 and any(len(item) == 1 and item in initials for item in release[start - 1]):
            return start - 1, end
        if end < len(release) and any(len(item) == 1 and item in initials for item in release[end]):
            return start, end + 1
        # Only the first of several first names ("Jean-Luc" written "Jean Luc" is two words, both checked above).
        if start > 0 and _same(release[start - 1], firsts[0], False):
            return start - 1, end
        if end < len(release) and _same(release[end], firsts[0], False):
            return start, end + 1
        taken.update(range(start, end))


def _author_close(release: list[frozenset[str]], author: str) -> tuple[int, int] | None:
    """Where the author stands in a form that is close and not sure: the last name with the initials written together
    (``AB Feldkamp`` for ``A. B. Feldkamp``), or with first names whose first letters are the initials wished
    (``Feldkamp, Anna Berit``). Another person may have those initials: a question, never a yes."""
    last, first = names.last_first(author)
    firsts = first.split()
    last_words = [_variants(word) for word in last.split() if _variants(word)]
    if not firsts or not last_words:
        return None
    initials = "".join(word[0] for word in firsts)
    taken: set[int] = set()
    while True:
        place = _find(release, last_words, taken, loose=False)
        if place is None:
            return None
        start, end = place
        for index in (start - 1, end):
            if len(initials) >= 2 and 0 <= index < len(release) and initials in release[index]:
                return min(start, index), max(end, index + 1)
        if all(len(word) == 1 for word in firsts):
            size = len(firsts)
            for low in (start - size, end):
                spelled = release[low:low + size] if low >= 0 else []
                if len(spelled) == size and all(any(len(variant) > 1 and variant[0] == firsts[offset]
                                                    for variant in _written(spelled[offset]))
                                                for offset in range(size)):
                    return min(start, low), max(end, low + size)
        taken.update(range(start, end))


def _core_words(title: str, authors: tuple[str, ...]) -> list[frozenset[str]]:
    core = names.core_title(title, authors[0] if authors else "")
    words = _tokens(core)
    while len(words) > 1 and words[0] & _ARTICLES:
        words.pop(0)
    return words


def _title_place(release: list[frozenset[str]], title: str, authors: tuple[str, ...],
                 taken: set[int]) -> tuple[int, int] | None:
    words = _core_words(title, authors)
    if not words:
        return None
    place = _find(release, words, taken)
    if place is not None:
        return place
    if len(words) == 1:
        for index, token in enumerate(release):
            if index not in taken and _same_long(token, words[0]):
                return index, index + 1
    # Written together or apart: "AchtNacht", "Washington-Dekret", "P.S." as "PS".
    if len(words) > 1 or len(next(iter(words[0]))) >= 6:
        wanted = _joined(words)
        for start in range(len(release)):
            if start in taken:
                continue
            for end in range(start + 1, min(len(release), start + len(words) + 3) + 1):
                if any(index in taken for index in range(start, end)):
                    break
                if _joined(release[start:end]) & wanted:
                    return start, end
    return None


#: What stands beside a title and says what kind of book or which edition it is, not more of the title.
_KIND_AFTER = frozenset({"roman", "romane", "krimi", "kriminalroman", "psychothriller", "thriller", "novel", "mystery",
                         "erzahlung", "erzaehlung", "erzahlungen", "erzaehlungen", "novelle", "geschichten",
                         "kurzgeschichten", "kurze", "stories", "jugend", "jugendbuch", "kinderbuch", "sachbuch",
                         "edition", "ausgabe", "auflage", "neuauflage", "sonderausgabe", "sonderedition", "filmausgabe",
                         "taschenbuch", "hardcover", "ebook", "buch", "book", "band", "bd", "teil", "folge", "reihe",
                         "series", "serie", "nr", "no", "vol", "volume", "tome", "ungekurzt", "ungekuerzt",
                         "ungekurzte", "ungekuerzte", "gekurzt", "gekuerzt", "gekurzte", "gekuerzte", "hoerbuch",
                         "horbuch", "audiobook", "abook", "lesung", "autorenlesung", "komplett", "complete", "retail",
                         "repack", "proper", "scan", "ocr", "true", "fassung", "version", "dt", "deutsch", "deutsche",
                         "unabridged", "abridged", "a", "an", "ein", "eine"})
#: An edition word may have one word before it that says which edition (``Neue Fassung``, ``Revised Edition``).
_EDITION_WORDS = re.compile(r"(?:ausgabe|edition|auflage|fassung|version)$")
#: Words that put a reader beside a book: ``Ida Leser liest``, ``gelesen von Ida Leser``, ``read by``, ``lu par``.
_READS = frozenset({"liest", "lesen", "gelesen", "vorgelesen", "spricht", "gesprochen", "sprecher", "sprecherin",
                    "read",
                    "narrated", "lu", "lue", "voorgelezen", "leest"})
_READ_BY = frozenset({"von", "by", "par", "door"})
#: The word before an author (``Der Ring von Rune Falkner``, ``Hörspiel nach Rune Falkner``).
_BY = frozenset({"von", "vom", "by", "de", "par", "door", "di", "nach", "after"})
#: The word between two authors (``Rune Falkner und Ida Leser``).
_AND = frozenset({"und", "and", "et", "y", "e", "en"})
#: What parts the fields of a name (a dash, a bracket, a colon, a comma ...): a title is the whole of its field.
_FIELD_BREAK = "-–—,;:()[]{}|/+&"
#: A volume in a name: a number, ``#3``, ``E003``.
_NUMBER = re.compile(r"#?\d{1,4}|e\d{2,3}")
#: A name of a group or an uploader (``Sh4d0w``): letters and digits mixed in one word.
_GROUPISH = re.compile(r"(?=.*[a-z])(?=.*\d)[a-z\d]{4,}")


def _gap(text: str, spans: list[tuple[int, int]], index: int) -> str:
    """What stands between word ``index - 1`` and word ``index``."""
    return text[spans[index - 1][1] : spans[index][0]]


def _field(text: str, spans: list[tuple[int, int]], place: tuple[int, int]) -> tuple[int, int]:
    """The field of the name a title stands in: the words around it up to the next dash, bracket, colon or comma."""
    start, end = place
    while start > 0 and start - 1 < len(spans) and not any(
            character in _gap(text, spans, start) for character in _FIELD_BREAK):
        start -= 1
    while end < len(spans) and not any(character in _gap(text, spans, end) for character in _FIELD_BREAK):
        end += 1
    return start, end


def _volume_at(folded: list[str], index: int) -> bool:
    return 0 <= index < len(folded) and bool(_NUMBER.fullmatch(folded[index]))


def _leftover(release: ReleaseName, raw_words: list[str], tokens: list[frozenset[str]], place: tuple[int, int],
              taken: set[int], book: Book) -> list[int]:
    """The words of the title's field that are neither the title nor anything that may stand beside a title. Beside a
    title may stand: the author (also a word of the author's name alone, ``von`` or ``nach`` before them, a second
    author after ``und``), a reader and the word that names them, a tag of the scene, a year, a mark of a language, a
    number (a volume), the series (the words known as its name, the words before a volume right before the title
    ``Reihe 03 Titel``, the words between the title and a volume at the end ``Titel Reihe 4``), a word that names the
    kind of book or its edition (``Roman``, ``Thriller``, ``ungekürzt``, ``Hörbuch``, ``Sonderausgabe``, ``Neue
    Fassung``), articles right before the title, a word of the other titles the catalogue knows the book by (its
    subtitle), and whatever follows the first tag after the title (``Titel GERMAN EPUB Gruppe``). What is left makes it
    another book: ``Wir wollen Ruß sehen`` is no ``Ruß``."""
    spans = [found.span() for found in _WORD_SPAN.finditer(release.text)]
    if len(spans) != len(raw_words):
        return []
    first, last = _field(release.text, spans, place)
    # The tags of the scene end the title: what follows them is the release's, not the book's.
    for index in range(place[1], last):
        if is_tag(raw_words[index]) and not _NUMBER.fullmatch(fold(raw_words[index])):
            last = index
            break
    series = {key for word in words_of(book.series) for key in _variants(word)}
    authors = {key for name in book.authors for word in words_of(name) for key in _variants(word)}
    known = {key for title in book.titles for word in words_of(title.text) for key in _variants(word)}
    folded = [fold(word) for word in raw_words]
    allowed: set[int] = set(range(*place))
    for index in range(first, last):
        key = folded[index]
        variants = tokens[index]
        if _EDITION_WORDS.search(key) and len(key) > 4:
            # ``Neue Fassung``, ``Revised Edition``: the word before says which edition (after the title only).
            allowed.add(index)
            if index - 1 >= place[1]:
                allowed.add(index - 1)
        elif index in taken or not key or is_tag(raw_words[index]) or key in _KIND_AFTER or variants & series \
                or variants & authors or variants & known or _NUMBER.fullmatch(key):
            allowed.add(index)
        elif key in _READS:
            # The reader: up to three words before ``liest``, after ``gelesen von``, ``read by``.
            allowed.add(index)
            after = index + 1
            if after < last and folded[after] in _READ_BY:
                allowed.add(after)
                after += 1
            if key in ("liest", "spricht", "leest"):
                allowed.update(range(max(first, index - 3), index))
            else:
                allowed.update(range(after, min(last, after + 3)))
        elif key in _AND and index - 1 in taken:
            # A second author: ``und`` and up to two words, before the title.
            allowed.update(range(index, min(index + 3, place[0])))
    # Articles right before the title (the core is held without them).
    index = place[0] - 1
    while index >= first and tokens[index] & _ARTICLES:
        allowed.add(index)
        index -= 1
    # The series and its volume before the title: ``Reihe 03 Titel``, ``Reihe Band 3 Titel``.
    number = place[0] - 1
    while number > first and (folded[number] in _KIND_AFTER or tokens[number] & _ARTICLES):
        number -= 1
    if number > first and _volume_at(folded, number):
        allowed.update(range(first, number))
    # The series and its volume after the title: ``Titel Reihe 4``, also with the name of an uploader as the last word
    # of the name after it (``Titel Reihe 07 Gruppe``).
    end = last - 1
    if end == len(raw_words) - 1 and end - 1 > place[1] and _volume_at(folded, end - 1):
        end -= 1
    if end > place[1] and _volume_at(folded, end) and end - place[1] <= 5:
        allowed.update(range(place[1], last))
    for index in range(first, last):
        if folded[index] in _BY and index + 1 < last and (index + 1 in taken or tokens[index + 1] & authors):
            allowed.add(index)
    # The name of an uploader as the last word of the name.
    if last == len(raw_words) and _GROUPISH.fullmatch(folded[-1]) and len(raw_words) - 1 not in range(*place):
        allowed.add(len(raw_words) - 1)
    # ``Nebelchor by moewe``: ``by`` and one or two words that are no author of the book, at the end of the name.
    if last == len(raw_words):
        for index in range(max(place[1], last - 3), last - 1):
            if folded[index] == "by" and not any(tokens[later] & authors for later in range(index + 1, last)):
                allowed.update(range(index, last))
                break
    return [index for index in range(first, last) if index not in allowed]


def _series_volumes(release: list[frozenset[str]], series: str, raw_words: list[str],
                    skip: frozenset[int] = frozenset()) -> list[float]:
    """The numbers right after the name of the series (``Reihe 04``, ``Reihe Band 4``, ``Reihe, Folge 4``). Not the
    ``1`` of a boast (``skip``)."""
    if not series:
        return []
    series_words = [variants for variants in _tokens(series)]
    while len(series_words) > 1 and series_words[0] & _ARTICLES:
        series_words.pop(0)
    found: list[float] = []
    taken: set[int] = set()
    while True:
        place = _find(release, series_words, taken)
        if place is None:
            return found
        start, end = place
        taken.update(range(start, end))
        index = end
        while index < len(raw_words) and fold(raw_words[index]) in (
                "band", "bd", "buch", "teil", "folge", "book", "vol", "volume", "nr", "no", "tome", "reihe", "series"):
            index += 1
        if index < len(raw_words) and index not in skip:
            number = re.fullmatch(r"[#eE]?0*(\d{1,3})(?:[.,](5))?", raw_words[index])
            if number is not None:
                value = float(number.group(1) + ("." + number.group(2) if number.group(2) else ""))
                if value > 0 and value not in found:
                    found.append(value)


def article_language(title: str) -> str:
    """The language of the article a title begins with (``The Hobbit``: ``en``), empty without one."""
    words = fold(title or "").split()
    return _ARTICLE_OF.get(words[0], "") if words else ""


def titles_volume(book: Book) -> float | None:
    """The volume the titles of a book name by a word for it (``Faust. Der Tragödie erster Teil``, ``Das erste Buch
    der Träume``), when those that name one name the same; None else."""
    found = {number for title in book.titles if (number := book_words.title_volume(title.text)) is not None}
    return float(found.pop()) if len(found) == 1 else None


def tokens_of(name: str) -> list[frozenset[str]]:
    """The words of a release name in both spellings each, as ``find_author`` reads them."""
    return [_variants(word) or frozenset({""}) for word in words_of(spaced(name))]


def title_words(book: Book) -> list[str]:
    """Every title of the book folded, in both spellings of an umlaut."""
    found: list[str] = []
    for title in book.titles:
        for value in (fold(title.text), key_fold(title.text)):
            if value and value not in found:
                found.append(value)
    return found


def _without_series(title: str, series: str) -> str:
    """``Spiegelwelt und die Kammer der Uhren`` without the name of its series ``Spiegelwelt`` and the word that
    joins them: ``die Kammer der Uhren``; empty when the title does not begin with the series."""
    if not series:
        return ""
    words, wanted = words_of(title), [fold(word) for word in words_of(series)]
    if len(words) <= len(wanted) + 1 or [fold(word) for word in words[: len(wanted)]] != wanted:
        return ""
    rest = words[len(wanted):]
    if fold(rest[0]) in _AND:
        rest = rest[1:]
    return " ".join(rest) if len(rest) >= 2 else ""


def match(release: ReleaseName, book: Book) -> Match:
    raw_words = words_of(release.text)
    tokens = [_variants(word) or frozenset({""}) for word in raw_words]
    taken: set[int] = set()
    author = False
    author_spans: list[tuple[int, int]] = []
    for name in book.authors:
        place = find_author(tokens, name)
        if place is not None:
            author = True
            taken.update(range(*place))
            author_spans.append(place)
    author_close = False
    if not author:
        for name in book.authors:
            place = _author_close(tokens, name)
            if place is not None:
                author_close = True
                taken.update(range(*place))
                author_spans.append(place)
                break
    languages: set[str] = set()
    any_unknown = False
    title = False
    spans: list[tuple[int, int]] = []
    only_longer = False
    best: tuple[int, int] | None = None
    candidates = list(book.titles)
    if book.series and book.position is not None and book.position in _series_volumes(
            tokens, book.series, raw_words, frozenset(release.ad_numbers)):
        # ``Spiegelwelt 2 - Die Kammer der Uhren``: the series and the volume wished, then the title without the
        # name of the series.
        for item in book.titles:
            rest = _without_series(item.text, book.series)
            if rest:
                candidates.append(Title(rest, item.language))
    places: set[tuple[int, int]] = set()
    for candidate in candidates:
        place = _whole_place(release, raw_words, tokens, candidate.text, book, taken, spans)
        if place is None:
            if spans:
                only_longer = True
            continue
        title = True
        places.add(place)
        if best is None or place[1] - place[0] > best[1] - best[0]:
            best = place
        if candidate.language:
            languages.add(candidate.language)
        else:
            any_unknown = True
    other_book = False
    if title and best is not None:
        # Another book of the author whose title holds the one looked for (``Wir wollen Ruß sehen`` for ``Ruß``) and
        # stands in the name as a whole: that book, not the one looked for.
        mine = {_core_key(item.text, book.authors) for item in book.titles}
        for other in book.others:
            if _core_key(other, book.authors) in mine:
                continue
            found = _whole_place(release, raw_words, tokens, other, book, taken, [])
            if found is not None and found[1] - found[0] > best[1] - best[0]:
                other_book = True
                break
        if other_book:
            title = False
            only_longer = True
    title_language = ""
    if title and len(languages) == 1 and not any_unknown:
        title_language = languages.pop()
    volumes = list(release.volumes)
    for value in _series_volumes(tokens, book.series, raw_words, frozenset(release.ad_numbers)):
        if value not in volumes:
            volumes.append(value)
    other = False
    wished_volume = book.position if book.position is not None else titles_volume(book)
    if wished_volume is not None and volumes and not book.bundle:
        # A book of no series known whose title names its volume (``Faust. Der Tragödie erster Teil``): a name that
        # gives another one (``Der Tragödie zweiter Teil``) is that one.
        other = wished_volume not in volumes
    if book.position is None and book.series and volumes and not title:
        # A volume of the series, though nobody knows which one is looked for: the series is named and the title is
        # not.
        other = True
    tail, before, after, number, short, spoken, article = "", False, False, None, False, "", ""
    between, field_numbers = False, ()
    if title and best is not None:
        tail = " ".join(raw_words[best[1]:])
        before, between, after, field_numbers = _unexplained(release, raw_words, tokens, best, taken, book)
        number = _number_after(raw_words, best, book, volumes, release)
        spoken = language_words(release, raw_words, tokens, best, taken | set(range(*best)), book)
        if best[0] > 0 and best[0] - 1 not in taken and places == {best}:
            # (Only where every title that matched stands in that one place:
            # ``Die Laternenstraße - The Lantern Road`` holds the German title as well.)
            article = _ARTICLE_OF.get(fold(raw_words[best[0] - 1]), "")
    elif not title:
        short = any(_short_form(release, raw_words, tokens, item.text, book, taken) for item in book.titles)
    subtitle = not title and not other_book and only_longer and _subtitle_after(release.name, book)
    series_spans: list[tuple[int, int]] = []
    if book.series:
        blocked = set(taken)
        for _round in range(_MAX_PLACES):
            place = _title_place(tokens, book.series, (), blocked)
            if place is None:
                break
            series_spans.append(place)
            blocked.update(range(*place))
    return Match(author=author, title=title, title_language=title_language, other_volume=other,
                 volumes=tuple(volumes), title_spans=tuple(spans), longer=only_longer and not title,
                 other_book=other_book, author_spans=tuple(author_spans), series_spans=tuple(series_spans),
                 tail=tail, unexplained_before=before,
                 unexplained_after=after, number_after=number, short=short, words_language=spoken,
                 unexplained_between=between, field_numbers=field_numbers, article_language=article,
                 author_close=author_close, subtitle_after=subtitle)


#: What stands between a title and its subtitle in one field: a full stop and a space, ``oder``, ``or``, ``ou``.
_SUBTITLE_MARK = "zzsubtitlezz"


def _subtitle_after(name: str, book: Book) -> bool:
    """Whether a title of the book stands in the name followed by a full stop and a space or by ``oder`` (``Das
    Parfum. Die Geschichte eines Mörders``, ``Der Hobbit oder Hin und zurück``) and something more, and what follows is
    no title of another book of the author."""
    marked = fold(re.sub(r"(?<=[^\W\d_])\.\s+(?=\S)", f" {_SUBTITLE_MARK} ", name or ""))
    marked = re.sub(r"(?<![a-z])(?:oder|or|ou)(?![a-z])", _SUBTITLE_MARK, marked)
    for title in book.titles:
        for core in {fold(names.core_title(title.text)), key_fold(names.core_title(title.text))}:
            if not core:
                continue
            found = re.search(rf"(?<![a-z0-9]){re.escape(core)}\s+{_SUBTITLE_MARK}\s+(\S.*)$", marked)
            if found is None:
                continue
            rest = found.group(1)
            if book_words.volumes_named(rest) or book_words.part_of(rest) is not None:
                # ``Faust. Zweiter Teil``: another volume, no subtitle.
                return False
            # A subtitle that is the title of another book of the author is that book.
            return not any(fold(names.core_title(other)) and fold(names.core_title(other)) in rest
                           for other in book.others)
    return False


#: Where a release was copied from or made with (``AUDiBLE``): no word of a book.
_SOURCE_WORDS = frozenset({"audible", "storytel", "bookbeat", "nextory", "spotify", "itunes", "google", "kobo",
                           "amazon", "kindle", "tolino", "thalia", "hugendubel", "librivox", "abook", "aabook"})
#: Words that may stand in a field of their own beside a title and explain nothing about another book: a title before a
#: name, the word of siblings, words of a reader.
_FREE_WORDS = frozenset({"sir", "dame", "dr", "prof", "von", "by", "nach", "und", "and", "mit", "with", "liest",
                         "gelesen", "gesprochen", "sprecher", "sprecherin", "read", "narrated", "lu", "par", "erzahlt",
                         "erzaehlt", "ein", "eine", "a", "an", "der", "die", "das", "the", "folge", "neu", "new",
                         "ubersetzt", "uebersetzt", "translated", "uebersetzung", "ubersetzung", "translation",
                         "illustriert", "illustrated", "hrsg", "herausgegeben"}) | names.GROUP_WORDS


#: Shelves a poster puts a book on, as the first field of a name (``kinder - Autor - Titel``).
_RUBRICS = frozenset({"kinder", "kinderbuch", "kinderbucher", "kinderbuecher", "jugend", "jugendbuch", "krimi",
                      "krimis", "thriller", "roman", "romane", "fantasy", "scifi", "horror", "romance", "sachbuch",
                      "klassiker", "ebook", "ebooks", "hoerbuch", "horbuch", "hoerbuecher", "horbucher", "audiobook"})
#: Houses that publish books, beside the publishers of ``series_names`` (folded).
_MORE_PUBLISHERS = frozenset({"insel", "anaconda", "klett cotta", "kampa", "dumont", "hoffmann und campe", "manesse",
                              "cbt", "arena", "thienemann", "dressler", "beltz", "nikol", "oxford", "oxford world s",
                              "wordsworth", "everyman", "everyman s library", "signet", "aufbau", "s fischer",
                              "kein aber",
                              "marix", "hamburger lesehefte", "dtv premium"})
#: Words beside the name of a publisher that name its line (folded).
_PUBLISHER_LINE = frozenset({"verlag", "verlages", "verlage", "verlagsgruppe", "taschenbuch", "taschenbucher",
                             "taschenbuecher", "tb", "classics", "classic", "paperback", "paperbacks", "books",
                             "press"})
#: Words before a word of an edition that say which one (folded): ``Limitierte Sonderausgabe``, ``25 Jahre …``.
_EDITION_QUALIFIERS = frozenset({"limitierte", "limitiert", "limited", "erweiterte", "uberarbeitete", "ueberarbeitete",
                                 "neue", "neu", "revised", "special", "deluxe", "collectors", "collector", "s", "jahre",
                                 "years", "anniversary", "jubilaums", "jubilaeums", "gebundene", "broschierte"})


def _publisher_only(words: list[str]) -> bool:
    """Whether the words of a field are the name of a publisher and nothing else (a year or its line may stand
    beside it)."""
    kept = [word for word in words if word and not word.isdigit() and word not in _PUBLISHER_LINE]
    name = " ".join(kept)
    return bool(name) and (name in series_names.PUBLISHERS or name in _MORE_PUBLISHERS)


def _edition_only(words: list[str]) -> bool:
    """Whether a field is a word of an edition (``Sonderausgabe``, ``Edition``) with words that say which one before
    it, and nothing else."""
    kept = [word for word in words if word and not word.isdigit()]
    if not kept or not re.search(r"(?:ausgabe|edition|auflage)$", kept[-1]):
        return False
    return all(word in _EDITION_QUALIFIERS for word in kept[:-1])


#: What parts the fields when holding them against each other: not ``&`` or ``+``, which join two names in one field
#: (``Rune Falkner & Ida Leser``, ``Jula & Hegel 01``).
_FIELD_PARTS = "-–—,;:()[]{}|/"


def _fields(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The fields of a name, as places of words: what stands between two dashes, brackets, colons or commas."""
    found: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(spans)):
        if any(character in _gap(text, spans, index) for character in _FIELD_PARTS):
            found.append((start, index))
            start = index
    if spans:
        found.append((start, len(spans)))
    return found


def _unexplained(release: ReleaseName, raw_words: list[str], tokens: list[frozenset[str]], place: tuple[int, int],
                 taken: set[int], book: Book) -> tuple[bool, bool, bool, tuple[float, ...]]:
    """Whether a field before the title (where an author stands) and a field after it (where a subtitle stands) hold a
    word that nothing explains. Explained are: tags of the scene and what follows the first of them, years, numbers,
    the author, the series, the words of the book's titles, words of a reader, the kind of book (``Roman``, ``Ein
    Bretagne-Krimi``), the group or an uploader at the end. A field that holds a volume number is the series.

    Returns ``(before, between, after, numbers)``: ``between`` for a field before the title after the author (``Aldous
    Huxley - Heerlijke Nieuwe Wereld (Brave New World)``), ``numbers`` the fields before the title that are a number
    and nothing else (``Zwergen-Saga - 2 - Titel``)."""
    spans = [found.span() for found in _WORD_SPAN.finditer(release.text)]
    if len(spans) != len(raw_words):
        return False, False, False, ()
    folded = [fold(word) for word in raw_words]
    first, last = _field(release.text, spans, place)
    tag_from = next((index for index in range(place[1], len(raw_words)) if is_tag(raw_words[index])
                     and not _NUMBER.fullmatch(folded[index])), len(raw_words))
    # Words that are reasons of their own (a bundle, an adaptation, an edition) explain themselves here.
    own = {word for text in (*release.bundle_words, release.adaptation, release.edition, "gesamtausgabe")
           for word in text.split()}
    group = fold(release.group)
    series = {key for word in words_of(book.series) for key in _variants(word)}
    authors = {key for name in book.authors for word in words_of(name) for key in _variants(word)}
    known = {key for title in book.titles for word in words_of(title.text) for key in _variants(word)}
    before = between = after = False
    numbers: list[float] = []
    author_end = max((index + 1 for index in taken if index < first), default=0)
    fields = _fields(release.text, spans)
    for number_of_field, (start, end) in enumerate(fields):
        if start >= first and end <= last:
            continue
        following = fields[number_of_field + 1] if number_of_field + 1 < len(fields) else None
        if end <= first and following is not None and following[1] <= first and any(
                _NUMBER.fullmatch(folded[index]) for index in range(*following)):
            # ``Kommissar Feltussel - Band 03 - Titel``: the series, its volume in the next field.
            continue
        if end <= first:
            side = "before"
        elif start >= last and start < tag_from:
            side = "after"
        else:
            continue
        words = [index for index in range(start, min(end, tag_from if side == "after" else end))]
        if not words:
            continue
        if side == "before" and len(words) == 1 and re.fullmatch(r"0*\d{1,2}", folded[words[0]]):
            numbers.append(float(folded[words[0]]))
        if any(_NUMBER.fullmatch(folded[index]) for index in words) and side == "before":
            continue
        loose = []
        # A second author joined to the first (``Rune Falkner & Ida Leser``): up to three words after ``&`` or ``und``.
        joined: set[int] = set()
        if any(index in taken for index in words):
            # A reader in front of the author in the author's field (``Ida Leser Rune Falkner-Titel``).
            first_author = min(index for index in words if index in taken)
            joined.update(range(max(words[0], first_author - 3), first_author))
            for index in words:
                if (folded[index] in _AND or "&" in _gap(release.text, spans, index)) and index > words[0]:
                    joined.update(range(index, min(index + 3, words[-1] + 1)))
        if any(folded[index] in ("pseudo", "pseudonym", "pseud", "alias", "aka") for index in words):
            # ``(pseudo van IM Lindqvist)``: the name behind a pen name.
            continue
        for index in words:
            key = folded[index]
            if index in joined:
                continue
            if (index in taken or not key or is_tag(raw_words[index]) or key in _KIND_AFTER or key in _FREE_WORDS
                    or key in own or (group and key == group) or key in _SOURCE_WORDS
                    or re.search(r"(?:ausgabe|edition|auflage|fassung|version)$", key)
                    or _NUMBER.fullmatch(key) or tokens[index] & (series | authors | known)
                    or (index == len(raw_words) - 1 and _GROUPISH.fullmatch(key))):
                continue
            loose.append(index)
        if not loose:
            continue
        if side == "before" and start == 0 and len(words) == 1 and folded[words[0]] in _RUBRICS:
            # ``kinder - Ende, Michael - …``: the shelf a poster put the book on, in front of the name.
            continue
        if side == "after" and _publisher_only([folded[index] for index in words]):
            # ``(Verlag)``, ``(Verlag Taschenbuch)``, ``(2006, Verlag)``, ``… Classics``: the house that published it,
            # by its name (``series_names.PUBLISHERS``).
            continue
        if side == "after" and _edition_only([folded[index] for index in words]):
            # ``Limitierte Sonderausgabe``, ``25 Jahre Jubiläumsausgabe``: an edition of the book.
            continue
        piece = " ".join(raw_words[index] for index in words if not _NUMBER.fullmatch(folded[index]))
        if side == "after" and not names.subtitle_key(f"Titel: {piece}", series=book.series,
                                                      authors=list(book.authors)):
            # ``Ein Bretagne-Krimi``, ``A Novel``, ``Ein Fall für Hanna``: the kind of book, no other book.
            continue
        if any(fold(word) in _READS for word in words_of(piece)):
            # ``gelesen von Ida Leser``: a reader.
            continue
        if side == "before":
            before = True
            between = between or (author_end > 0 and start >= author_end)
        else:
            after = True
    return before, between, after, tuple(numbers)


#: The little words of a language that a title of another one hardly has. ``_SHARED`` are words of two or more of them;
#: they count half for each.
_LITTLE_WORDS: dict[str, frozenset[str]] = {
    "de": frozenset({"der", "das", "und", "ein", "eine", "einer", "einem", "mit", "zum", "zur", "dem", "des", "nicht",
                     "ist", "auf", "aus", "wenn", "sie", "fur", "fuer", "uber", "ueber", "unter", "nach", "vom"}),
    "en": frozenset({"the", "and", "of", "with", "from", "for", "this", "that", "into", "about", "novel", "stories"}),
    "nl": frozenset({"het", "een", "van", "aan", "voor", "niet", "naar", "bij", "uit", "zijn", "deel", "boek", "met",
                     "ook", "dat"}),
    "fr": frozenset({"le", "les", "et", "du", "des", "une", "pour", "avec", "dans", "sur", "aux", "au"}),
    "es": frozenset({"el", "los", "las", "del", "una", "con", "por", "para", "y"}),
    "it": frozenset({"il", "lo", "gli", "della", "dello", "nel", "per", "di"}),
    "sv": frozenset({"och", "ett", "av", "pa", "till", "fran"}),
}
_SHARED = {"de": ("nl", "fr", "es"), "en": ("nl", "fr", "sv"), "die": ("de",), "la": ("fr", "es", "it"),
           "un": ("fr", "it"), "in": ("de", "en", "nl", "it")}
#: An article at the start of a field after the title is a title of that language (``The Stand - Das letzte
#: Gefecht``).
_ARTICLE_OF = {"der": "de", "das": "de", "die": "de", "ein": "de", "eine": "de", "the": "en", "het": "nl", "een": "nl",
               "le": "fr", "les": "fr", "el": "es", "il": "it"}


def language_words(release: ReleaseName, raw_words: list[str], tokens: list[frozenset[str]], place: tuple[int, int],
                   taken: set[int], book: Book) -> str:
    """The language the little words of a name outside the title, the author, the series and the words of the book's
    titles are of: two words of one language (a shared word counts half, one of them its own), or a field after the
    title that begins with an article of the language. Empty when they say none, or two."""
    known = {key for title in book.titles for word in words_of(title.text) for key in _variants(word)}
    known |= {key for word in words_of(book.series) for key in _variants(word)}
    spans = [found.span() for found in _WORD_SPAN.finditer(release.text)]
    if len(spans) != len(raw_words):
        return ""
    score: dict[str, float] = {}
    own: dict[str, int] = {}
    for index, word in enumerate(raw_words):
        key = fold(word)
        if index in taken or tokens[index] & known or not key or (is_tag(word) and len(key) > 3):
            continue
        for code, little in _LITTLE_WORDS.items():
            if key in little:
                score[code] = score.get(code, 0) + 1
                own[code] = own.get(code, 0) + 1
        for code in _SHARED.get(key, ()):
            score[code] = score.get(code, 0) + 0.5
    found = {code for code, value in score.items() if value >= 2 and own.get(code)}
    if place[1] < len(raw_words) and any(character in _gap(release.text, spans, place[1]) for character in "-–—:("):
        article = _ARTICLE_OF.get(fold(raw_words[place[1]]))
        if article and place[1] not in taken and not tokens[place[1]] & known:
            found.add(article)
    return next(iter(found)) if len(found) == 1 else ""


def _number_after(raw_words: list[str], place: tuple[int, int], book: Book, volumes: list[float],
                  release: ReleaseName) -> float | None:
    """A number right after the title (``Erebos 3``, ``QualityLand 2.0``, ``… sein Tod 03``) that is no year, no rate,
    no part of a title of the book, and not the volume wished: perhaps another volume of a series nobody named, or of
    the series wished when the name does not say the series (``Die Nacht 3`` for the second volume)."""
    if place[1] >= len(raw_words):
        return None
    word = raw_words[place[1]]
    found = re.fullmatch(r"0*(\d{1,2})(?:[.,](\d))?", word)
    if found is None or place[1] in release.ad_numbers:
        return None
    if place[1] + 1 < len(raw_words) and re.fullmatch(r"(?:1[5-9]|20)\d\d", raw_words[place[1] + 1]) and re.search(
            rf"(?<!\d){re.escape(word)}\s*[-/.]\s*{re.escape(raw_words[place[1] + 1])}", release.text):
        # ``(05-2021)``: a month and a year. ``Nebelchor 2 (2021)`` is a number and the year apart.
        return None
    value = float(found.group(1) + ("." + found.group(2) if found.group(2) else ""))
    if value <= 0 or value >= 100 or value == (book.position if book.position is not None else titles_volume(book)):
        return None
    if any(re.search(rf"(?<!\d)0*{int(value)}(?!\d)", fold(title.text)) for title in book.titles):
        return None
    if int(value) in (16, 24, 32, 44, 48, 64, 96) and place[1] + 1 < len(raw_words) and re.fullmatch(
            r"(?i)bits?|khz|kbps|k", raw_words[place[1] + 1]):
        return None
    return value


def _short_form(release: ReleaseName, raw_words: list[str], tokens: list[frozenset[str]], title: str, book: Book,
                taken: set[int]) -> bool:
    """Whether a field of the name is the end of ``title`` (two words or more) and nothing else: ``Tom Sawyer`` for
    ``Die Abenteuer des Tom Sawyer``. Not the name of the series alone."""
    words = _core_words(title, book.authors)
    if len(words) < 3:
        return False
    spans = [found.span() for found in _WORD_SPAN.finditer(release.text)]
    if len(spans) != len(raw_words):
        return False
    series = [fold(word) for word in words_of(book.series)]
    for start, end in _fields(release.text, spans):
        field_tokens = [tokens[index] for index in range(start, end) if index not in taken
                        and not is_tag(raw_words[index])]
        while field_tokens and field_tokens[0] & _ARTICLES:
            field_tokens.pop(0)
        size = len(field_tokens)
        if size < 2 or size >= len(words):
            continue
        if all(_same(field_tokens[offset], words[len(words) - size + offset], True) for offset in range(size)):
            if [fold(raw_words[index]) for index in range(start, end)] == series:
                continue
            return True
    return False


def _core_key(title: str, authors: tuple[str, ...]) -> str:
    return " ".join(min(token, key=len) for token in _core_words(title, authors))


#: How many places of a title in a name are looked at (a title may stand twice: as the series and as the book).
_MAX_PLACES = 6


def _whole_place(release: ReleaseName, raw_words: list[str], tokens: list[frozenset[str]], title: str, book: Book,
                 taken: set[int], seen: list[tuple[int, int]]) -> tuple[int, int] | None:
    """The last place where ``title`` stands in the name as a whole title (``_leftover`` finds nothing beside it in
    its field), or None: a title that is also the name of its series stands first as the series and last as the book
    (``Dörlinberve-Zyklus 01 - Dörlinberve``). Every place it stands in at all goes into ``seen`` (a mark of a language
    there belongs to the title)."""
    blocked = set(taken)
    found: tuple[int, int] | None = None
    for _round in range(_MAX_PLACES):
        place = _title_place(tokens, title, book.authors, blocked)
        if place is None:
            return found
        if place not in seen:
            seen.append(place)
        if not _leftover(release, raw_words, tokens, place, taken, book):
            found = place
        blocked.update(range(*place))
    return found


def names_an_author(text: str, book: Book) -> bool | None:
    """Whether a name of an author an indexer states (``newznab.Item.author``) is an author of ``book``: None when it
    states none or the book has none, else true or false by the rule a release name is read by (``find_author``)."""
    words = words_of(" ".join((text or "").split())[:200])
    if not words or not book.authors:
        return None
    tokens = [_variants(word) or frozenset({""}) for word in words]
    return any(find_author(tokens, name) is not None for name in book.authors)


def names_another_book(text: str, book: Book) -> bool:
    """Whether a title an indexer states (``newznab.Item.book_title``) is another book of the author as a whole
    (``Book.others``) and no title of ``book``. A title it states in another language or with a subtitle is no voice."""
    title = " ".join((text or "").split())[:300]
    if not title or not book.others or title_is(title, book):
        return False
    return any(title_is(title, Book(titles=(Title(other),), authors=book.authors)) for other in book.others)


def title_is(text: str, book: Book) -> bool:
    """Whether a title (of a file, of a catalogue entry) is the title of ``book`` as a whole, by the same rule a release
    name is held by: ``Wir wollen Ruß sehen`` is no ``Ruß``, ``Der Ring der Fünf`` no ``Der Ring``, ``Die
    Laternenstraße: Ein Krimi`` is ``Die Laternenstraße``."""
    return match(release_name.parse(text), replace(book, others=())).title


__all__ = ["Book", "Match", "Title", "find_author", "match", "title_is", "title_words", "tokens_of"]
