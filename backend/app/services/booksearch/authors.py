"""Who wrote a record: the records of a search in groups, one for each author.

The records are put in groups by the first author a record names: two books of the same title by two people are two
hits, and nothing of one author is ever joined to a book of another. One author is many names, though:

* **spellings** of a name (``authors_compatible``: ``J. K. Ash`` and ``Joanna K. Ash``; ``Süskind`` and ``Suskind``);
* **the same person at a catalogue**: two records whose first author has the same number at Open Library;
* **the last name alone** with a book of the same title (``last_name_only``);
* **transliterations** of a name written in another script (``Lew Tarasow``, ``Leo Tarasov``, ``Lev Tarassow``;
  ``Fjodor Wolkow``, ``Fiódor Volkov``; ``Bram Holter``, ``Bram Hollter``) and a name with or without its
  second family name (``Miguel de Ortega`` and ``Miguel de Ortega Lantera``): the names are alike after the
  usual letters of transliteration are made one (``same_writer``), *and* the two wrote a book of the same title. Alike
  names alone are not enough: two people of similar names write different books.
* **co-authors in the other order** (``Julia Example, Axel Muster`` and ``Axel Muster, Julia Example``): a record
  that names first the second author of another record of the same two goes with the author that leads more of them;
* **a translator credited first** (``_main_author``): a translation at Open Library or a shop that names first a
  person no other record names (``Ulla Muster, Ada Example`` for the German edition of an English book) goes with the
  author. Not a library's record, not one in the author's own language (a comic, a retelling, a guide made after the
  book), not one that says it was made after the book of another.

Without these, one writer of classics stood in six hits of a search, a picture book in two (its author and its
illustrator in both orders), a classic in three (its translators credited first).
"""

from __future__ import annotations

import re
from collections import Counter

from .. import series_names
from ..readers.names import authors_compatible, compare_key, is_company, person_key
from ..readers.text import fold
from ..sources.model import Entry
from . import words
from .titles import names_of

#: Particles of a name that are left out when two names are compared (``Miguel de Ortega``).
PARTICLES = frozenset({"von", "van", "de", "der", "den", "del", "della", "di", "da", "du", "la", "le", "les", "ten",
                       "ter", "zu", "zur", "dos", "af", "el", "al", "y"})
#: The letters transliterations write in several ways, made one (in this order).
_TRANSLIT = (("tsch", "ch"), ("sch", "sh"), ("tch", "ch"), ("ck", "k"), ("ph", "f"), ("kh", "h"), ("ks", "x"),
             ("ij", "i"), ("yj", "i"), ("iy", "i"), ("yi", "i"), ("j", "i"), ("y", "i"), ("w", "v"), ("ie", "i"),
             ("oe", "o"), ("ue", "u"), ("ae", "a"), ("ou", "u"))
_DOUBLE = re.compile(r"(.)\1+")


def translit(word: str) -> str:
    """A folded word with the letters of transliteration made one (``brodskiy``, ``brodskij`` and ``brodskii`` are
    one)."""
    text = fold(word).replace(" ", "")
    for old, new in _TRANSLIT:
        text = text.replace(old, new)
    return _DOUBLE.sub(r"\1", text)


def _near(a: str, b: str) -> bool:
    """Two transliterated words that differ by one letter at most (and are long enough for that to say little)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:index] + long_[index + 1:] == short for index in range(len(long_)))


def _tokens(name: str) -> list[str]:
    return [translit(word) for word in fold(name).split() if word not in PARTICLES and len(word) > 1 or (
        len(word) == 1 and word.isalpha())]


def same_person(a: str, b: str) -> bool:
    """Two names of one author: ``authors_compatible``, also when one of them is written without its accents
    (``Süskind`` and ``Suskind``)."""
    return authors_compatible(a, b) or authors_compatible(fold(a), fold(b))


def same_writer(a: str, b: str) -> bool:
    """Whether two names may be one writer whose name was written in two ways (see the top): the last names alike after
    transliteration, and the first names too (or one an initial of the other); or the shorter name's words all in the
    longer one, two words at least. Never for an organisation, never for a name of one word."""
    if is_company(a) or is_company(b):
        return False
    one, two = _tokens(a), _tokens(b)
    if len(one) < 2 or len(two) < 2:
        return False
    short, long_ = sorted((one, two), key=len)
    if all(any(_near(word, other) for other in long_) for word in short if len(word) > 1) and _near(
            short[-1], long_[-1]) or (len(short) >= 2 and set(short) <= set(long_)):
        return True
    if not _near(one[-1], two[-1]):
        return False
    first_a, first_b = one[0], two[0]
    return first_a[0] == first_b[0] and (len(first_a) == 1 or len(first_b) == 1 or _near(first_a, first_b)
                                         or first_a[:2] == first_b[:2])


def _first_two(entry: Entry) -> list[str]:
    return names_of(entry)[:2]


def last_name_only(a: str, b: str) -> bool:
    """Whether one of two names is the other's last name alone (a catalogue that credits ``Haushofer`` for a book of
    ``Marlen Haushofer``): one person when the two wrote a book of the same title."""
    one, other = sorted((fold(a).split(), fold(b).split()), key=len)
    return len(one) == 1 and len(other) >= 2 and len(one[0]) >= 4 and one[0] == other[-1]


def _unit(entry: Entry) -> tuple[str, str]:
    """What one source knows as one book: a work of a catalogue with its editions, or a single record."""
    return entry.source, entry.work_key or entry.key


Known = dict[str, tuple[set[str], set[tuple[str, str]]]]


def _known(entries: list[Entry]) -> Known:
    """For each person the records name (any of their first four names): the sources that name them, and the books of
    those sources (``_unit``)."""
    found: Known = {}
    for entry in entries:
        for name in names_of(entry):
            key = person_key(name)
            if key:
                sources, units = found.setdefault(key, (set(), set()))
                sources.add(entry.source)
                units.add(_unit(entry))
    return found


def _languages(entries: list[Entry]) -> tuple[dict[tuple[str, str], set[str]], dict[str, Counter[str]]]:
    """The languages of each book of a source (``_unit``: a work of a catalogue says none, its editions do), and the
    language each person writes in (``Counter`` by ``person_key``): what the translations of the records they lead
    say they were made from, else the languages those records are in."""
    units: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        if entry.language and entry.language != "und":
            units.setdefault(_unit(entry), set()).add(entry.language)
    said: dict[str, Counter[str]] = {}
    seen: dict[str, Counter[str]] = {}
    for entry in entries:
        names = names_of(entry)
        if not names:
            continue
        key = person_key(names[0])
        if entry.original_language:
            said.setdefault(key, Counter())[entry.original_language] += 1
        for language in units.get(_unit(entry), set()):
            seen.setdefault(key, Counter())[language] += 1
    return units, {key: said.get(key) or seen.get(key) or Counter() for key in set(said) | set(seen)}


def _main_author(entry: Entry, names: list[str], known: Known,
                 languages: tuple[dict[tuple[str, str], set[str]], dict[str, Counter[str]]]) -> str:
    """The author a record of several names stands under when its first name is the translator a translation of
    Open Library or a shop credits first (``Ulrich Muster, Ada Example`` for the German edition of an English book):
    the one of its other names that the most sources and books name, when another book names them too, and the record
    is in a language the author does not write in. Empty otherwise: a co-author the answer names elsewhere is a
    co-author (``Julia Example, Axel Muster``), and one credited first on a book in the author's own language made a
    book after it (a comic, a retelling for learners, a screenplay, a guide).

    Never a library's record (a library names a translator as one, and a book first credited to another there is a
    play, a comic or a retelling of it), nor a record that says it was made after the book of another (``nach dem
    Roman von``), nor one whose first name is a list of people (``Muster, Ulla, Probe, Jan``: the makers of a comic) or
    a company (a publisher of study guides)."""
    if entry.source in series_names.LIBRARIES or "," in (entry.authors[0] if entry.authors else "").partition(",")[2]:
        return ""
    if is_company(names[0]) or words.GUIDE.search(fold(names[0])):
        return ""
    said = " ".join(text for key in ("subtitle", "credits") if isinstance(text := entry.signals.get(key), str))
    if words.MADE_AFTER.search(fold(said)):
        return ""
    first = known.get(person_key(names[0]))
    if first is None or len(first[0]) > 1 or first[1] - {_unit(entry)}:
        return ""
    nobody: tuple[set[str], set[tuple[str, str]]] = (set(), set())
    best = max(names[1:], key=lambda name: (len(known.get(person_key(name), nobody)[0]),
                                            len(known.get(person_key(name), nobody)[1])))
    if not known.get(person_key(best), nobody)[1] - {_unit(entry)}:
        return ""
    if len(fold(names[0]).split()) == 1:
        # (A name of one word credited first is a publisher's or a brand's, not a person's: ``Hörbuchverlag``.)
        return best
    units, led = languages
    mine = units.get(_unit(entry), set()) or {words.title_language(entry.title)} - {""}
    theirs = led.get(person_key(best), Counter())
    own = theirs.most_common(1)[0][0] if theirs else ""
    return best if mine and own and own not in mine else ""


def by_author(entries: list[Entry]) -> list[list[Entry]]:
    """The records in groups of one author each (see the top). A book of two authors goes with its first: grouping by
    every author would put the books of the co-author's own series beside the author's, and a title the two share
    would join two books."""
    leads: Counter[str] = Counter(person_key(names[0]) for entry in entries if (names := _first_two(entry)))
    pairs = {(person_key(names[0]), person_key(names[1])) for entry in entries if len(names := _first_two(entry)) == 2}
    known = _known(entries)
    languages = _languages(entries)

    def lead_of(entry: Entry) -> str:
        names = names_of(entry)
        if not names:
            return ""
        if len(names) >= 2:
            first, second = person_key(names[0]), person_key(names[1])
            if (second, first) in pairs and leads[second] > leads[first]:
                return names[1]
            main = _main_author(entry, names, known, languages)
            if main:
                return main
        return names[0]

    chosen = [lead_of(entry) for entry in entries]
    names: list[str] = []
    index_of: dict[str, int] = {}
    titles: list[set[str]] = []
    numbers: list[set[str]] = []
    for entry, name in zip(entries, chosen, strict=True):
        key = person_key(name) if name else ""
        if not key:
            continue
        if key not in index_of:
            index_of[key] = len(names)
            names.append(name)
            titles.append(set())
            numbers.append(set())
        at = index_of[key]
        if compare_key(entry.title):
            titles[at].add(compare_key(entry.title))
        if fold(names_of(entry)[0]) == fold(name):
            # The number of the first author at a catalogue or a library: one person, whatever the spelling.
            if entry.source == "openlibrary" and entry.person:
                numbers[at].add(f"ol:{entry.person.split()[0]}")
            if isinstance(entry.signals.get("gnd"), str) and entry.signals["gnd"]:
                numbers[at].add(f"gnd:{entry.signals['gnd']}")
    parent = list(range(len(names)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            shared = titles[a] & titles[b]
            if (same_person(names[a], names[b]) or bool(numbers[a] & numbers[b])
                    or (shared and same_writer(names[a], names[b]))
                    or (any(len(key.replace(" ", "")) >= 4 for key in shared) and last_name_only(names[a], names[b]))):
                root_a, root_b = find(a), find(b)
                if root_a != root_b:
                    parent[max(root_a, root_b)] = min(root_a, root_b)
    groups: dict[int, list[Entry]] = {}
    for entry, name in zip(entries, chosen, strict=True):
        key = person_key(name) if name else ""
        if key in index_of:
            groups.setdefault(find(index_of[key]), []).append(entry)
    return list(groups.values())


#: The one a book was written after an idea of (folded): a co-author the shops name, the libraries only say so.
_IDEA = re.compile(r"\bnach einer idee von ([a-z][a-z .\-]*?)(?:$| und | thriller| roman)")


def lead_author(members: list[Entry]) -> list[str]:
    """The authors of a group as most sources name them (the first one first): a library that lists fifty records of
    one book does not outvote the shop and the catalogue that name it once each. Of spellings named by as many sources,
    the one most records write (a slip of one record, ``Bram Stocker``, does not win by being longer); then the
    fuller one. The person a record says the book was written after an idea of (``nach einer Idee von Ada Example``),
    when another record names them as an author, comes next: the shops name them, the libraries say so."""
    sources: dict[str, set[str]] = {}
    counted: Counter[str] = Counter()
    others: Counter[str] = Counter()
    named = 0
    every = {fold(name): name for entry in members for name in names_of(entry)}
    ideas: list[str] = []
    for entry in members:
        names = names_of(entry)[:3]
        for key in ("subtitle", "credits"):
            text = entry.signals.get(key)
            for piece in re.split(r"[;:|]", text) if isinstance(text, str) else []:
                ideas += [person for found in _IDEA.finditer(fold(piece))
                          if (person := every.get(found.group(1).strip())) and person not in ideas]
        if not names:
            continue
        named += 1
        sources.setdefault(names[0], set()).add(entry.source)
        counted[names[0]] += 1
        for name in names[1:]:
            others[name] += 1
    if not counted:
        return []
    first = min(counted, key=lambda name: (-len(sources[name]), -counted[name], -len(name), name))
    more = [name for name in ideas if not same_person(name, first)][:1]
    more += [name for name, count in others.most_common(2) if count * 2 > named and not same_person(name, first)
             and name not in more]
    return [first, *more[:2]]


__all__ = ["by_author", "lead_author", "same_person", "same_writer", "translit"]
