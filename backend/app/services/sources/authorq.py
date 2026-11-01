"""What the questions about an author's works have in common, whatever the source: the name in the shape a catalogue
wants it, whether a name a record gives is the author's, and the cache of the answers.

The answers are kept for **a day** (``DAYS``): the catalogue of an author is built again at most once a day, and then
it asks the sources again (a book announced yesterday is there today). What is kept is the cut down answer
(``model.Entry``), never the page a source sent.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..readers.names import authors_compatible, display_name, last_first, sort_name
from ..readers.text import clean
from . import cache
from .model import Entry

#: How long an answer about an author's works is kept.
DAYS = 1
#: The most entries one source gives for one author and language (a prolific author with every edition).
MAX_ENTRIES = 1500
_LIFE_DATES = re.compile(r",?\s*\(?\d{3,4}\s*-\s*(\d{3,4}|\.{2,4})?\)?\s*$")


def name_parts(name: str) -> tuple[str, str]:
    """``(last, first)`` of a name as it reads: ``Edmund Halloran`` is ``("Halloran", "Edmund")``. A name without a
    first name is ``(name, "")``."""
    text = sort_name(clean(name, 200))
    last, _, first = text.partition(",")
    return last.strip(), first.strip()


def without_dates(name: str) -> str:
    """A library's form of a name without the years of the life (``Halloran, Edmund, 1951-``)."""
    return _LIFE_DATES.sub("", clean(name, 200)).strip(" ,")


_BORN = re.compile(r",?\s*\(?(\d{3,4})\s*-\s*(?:\d{3,4}|\.{2,4})?\)?\s*$")


def person_by_years(name: str, given: str) -> str:
    """The years of the life a library writes beside a name (``Mankell, Henning, 1948-2015``) as the number of the
    person (``y1948``) when the name is the author's: two people of one name have other years (the writer and his
    grandfather, the composer). Empty when the record names no years."""
    found = _BORN.search(clean(given, 200))
    return f"y{found.group(1)}" if found and is_author(name, given) else ""


def is_author(name: str, given: str) -> bool:
    """Whether a name a record gives (``Halloran, Edmund, 1951-``, ``E. Halloran``) is the author asked about: the last
    name and the first name or its initials agree, never the last name alone."""
    other = display_name(without_dates(given))
    if not other:
        return False
    if authors_compatible(name, other):
        return True
    return last_first(name) == last_first(other) and bool(last_first(name)[1])


def kept(source: str, key: str, fetch: Callable[[], list[Entry]]) -> list[Entry]:
    """The entries of a question: from the cache when they are there, else asked (``fetch``) and kept for a day. A
    question that fails raises and keeps nothing."""
    found = cache.get(source, key)
    if found is not cache.MISSING and isinstance(found, list):
        entries: list[Entry] = []
        for item in found:
            try:
                entries.append(Entry.from_json(item))
            except (ValueError, TypeError):
                continue
        return entries
    entries = fetch()[:MAX_ENTRIES]
    cache.put(source, key, [entry.to_json() for entry in entries], hit=bool(entries), days=DAYS)
    return entries


__all__ = ["DAYS", "MAX_ENTRIES", "is_author", "kept", "name_parts", "without_dates"]
