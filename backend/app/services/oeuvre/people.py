"""Who wrote a record: the author asked about, or somebody else of the same name.

Open Library, Audible and the German library give each person a number of their own (an author key, an author ASIN, a
GND number). A search by name finds everybody of that name: four of eighteen works of a well-known thriller writer
were somebody else's (a teacher's memoir, a western), and two sources "confirmed" them because each found the other
person under the same name. So, for each source that numbers its people:

* **The author is one of the numbers**: the one whose works are the books of the libraries (``own``), else the one whose
  works the *other* sources know most (the works overlap), else the one with the most works (Open Library's count of
  works for its key, else the records found).
* **Another number is the same person** when its works overlap the author's (a person filed twice, as Open Library does
  now and then) or its years of life are the author's; **else it is somebody else**, and its records go.
* A record without a number (Audible names some authors without one, Hardcover and the other libraries never number
  them) is not known to be the author's or somebody else's (``who`` stays empty).

Whether the name is shared at all (``Sorted.namesakes``) is what the sources showed: another number of the German
library (its numbers are people), authors of the name at Open Library with other years of life, more than one author
of the name with books at Hardcover. Open Library and Audible file one person under two numbers now and then: another
number of theirs alone says nothing of the name (its records go all the same). For a shared name a work no number
proves is not sure (``works.py``, ``proven``): it stands under "Nicht sicher zugeordnet" and is never fetched by itself.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..readers.names import compare_key
from ..sources.model import Entry

#: The sources that number their people (the libraries of the other languages by the years of the life they write
#: beside a name: ``y1948``).
NUMBERED = ("openlibrary", "audible", "dnb", "bnf", "libris", "kb", "nb")
#: The sources whose numbers are people: another number is another person of the name.
PEOPLE_NUMBERS = ("dnb", "bnf", "libris", "kb", "nb")
SAME = "same"


@dataclass
class Namesake:
    """Another author of the name at a source: their number, how many works, the years of their life."""

    number: str
    works: int = 0
    born: str = ""


@dataclass
class Sorted:
    entries: list[Entry] = field(default_factory=list)
    #: How many other people of the name the sources showed.
    namesakes: int = 0
    #: For each source, the number taken for the author.
    numbers: dict[str, str] = field(default_factory=dict)


def _year(text: str) -> str:
    digits = "".join(ch if ch.isdigit() else " " for ch in text or "").split()
    return next((part for part in digits if len(part) == 4), "")


def _keys(entries: list[Entry]) -> set[str]:
    return {key for entry in entries if (key := compare_key(entry.title))}


def sort_out(entries: list[Entry], *, own: list[str] | None = None, known: list[Namesake] | None = None,
             others: int = 0) -> Sorted:
    """The records of the author asked about, without the ones of other people of the name (see the top). ``own``: the
    titles of the books of the libraries; ``known``: the authors of the name Open Library knows (their keys, works and
    years); ``others``: how many more authors of the name a source (Hardcover) knows besides the one it took."""
    out = Sorted(namesakes=max(0, others))
    own_keys = {key for title in own or [] if (key := compare_key(title))}
    by_source: dict[str, list[Entry]] = {}
    for entry in entries:
        by_source.setdefault(entry.source, []).append(entry)
    keys_of_source = {source: _keys(items) for source, items in by_source.items()}
    known_by = {item.number: item for item in known or []}
    gone: set[int] = set()
    # A source's numbers hold within a scope: Audible numbers its people in each market on its own (``de:B0…``), and
    # the records found for a pen name are that name's (another number than the author's own, and the same person).
    scopes: dict[tuple[str, str, str], list[Entry]] = {}
    for source in NUMBERED:
        for entry in by_source.get(source, []):
            first = entry.person.split()[0] if entry.person.split() else ""
            scopes.setdefault((source, first.split(":", 1)[0] if ":" in first else "", entry.via), []).append(entry)
    for (source, scope, _via), items in sorted(scopes.items()):
        clusters: dict[str, list[Entry]] = {}
        for entry in items:
            for number in entry.person.split()[:4]:
                clusters.setdefault(number, []).append(entry)
        if not clusters:
            continue
        elsewhere = set().union(*(keys for name, keys in keys_of_source.items() if name != source))
        keys = {number: _keys(members) for number, members in clusters.items()}

        def score(number: str, keys: dict[str, set[str]] = keys, elsewhere: set[str] = elsewhere,
                  clusters: dict[str, list[Entry]] = clusters) -> tuple[int, int, int, int]:
            listed = known_by.get(number)
            return (len(keys[number] & own_keys), len(keys[number] & elsewhere), listed.works if listed else 0,
                    len(clusters[number]))

        main = max(sorted(clusters), key=score)
        born = _year(known_by[main].born) if main in known_by else ""
        same = {main}
        for number in clusters:
            if number == main:
                continue
            listed = known_by.get(number)
            if keys[number] & keys[main] or (born and listed is not None and _year(listed.born) == born):
                same.add(number)
        others_here = [number for number in clusters if number not in same]
        # Whether the name is shared: the German library's numbers are people (one number, one person); Open Library
        # and Audible file one person under two numbers now and then, so another of their numbers says so only with
        # other years of life (their records go all the same: nothing proves them the author's).
        if source in PEOPLE_NUMBERS:
            out.namesakes += len(others_here)
        elif source == "openlibrary" and born:
            out.namesakes += sum(1 for number in others_here
                                 if number in known_by and _year(known_by[number].born) not in ("", born))
        out.numbers[f"{source}:{scope}" if scope else source] = main
        for entry in items:
            numbers = set(entry.person.split()[:4])
            if not numbers:
                continue
            if numbers & same:
                entry.who = SAME
            else:
                gone.add(id(entry))
    # Open Library knows authors of the name with other years of life: other people (two keys without years may be one
    # person filed twice, and say nothing).
    years = {year for item in known or [] if item.works > 0 and (year := _year(item.born))}
    out.namesakes = max(out.namesakes, out.namesakes + len(years) - 1) if years else out.namesakes
    out.entries = [entry for entry in entries if id(entry) not in gone]
    return out


#: The sources whose number alone proves a work for a name two people share.
PROVING = ("openlibrary", "dnb", "bnf", "libris", "kb", "nb")
#: The sources that know a person's books but mix people up: Hardcover files the books of everybody of a name under one
#: author, Audible puts a book of another person on an author's page now and then (a teacher's memoir under a thriller
#: writer's number, in two markets).
MIXING = ("hardcover", "audible")


def proven(entries: list[Entry]) -> bool:
    """Whether a number of a source proves a work the author's, for a name two people share: Open Library's or the
    German library's number; or Audible's, when a source that does not mix people up knows the work too (a national
    library, Open Library). Hardcover and Audible alone prove nothing then."""
    if any(entry.who == SAME and entry.source in PROVING for entry in entries):
        return True
    return any(entry.who == SAME for entry in entries) and any(entry.source not in MIXING for entry in entries)


def counts(entries: list[Entry]) -> Counter[str]:
    return Counter(entry.who or "unknown" for entry in entries)


__all__ = ["MIXING", "NUMBERED", "PROVING", "SAME", "Namesake", "Sorted", "proven", "sort_out"]
