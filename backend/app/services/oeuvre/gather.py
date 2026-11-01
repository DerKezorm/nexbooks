"""Asking the sources about an author: which source for which language, and in what order.

* **The language decides the source** (measured): German the German National Library and Audible, French the French
  library, Swedish LIBRIS, Dutch the Dutch library, Norwegian the Norwegian one, English Open Library, Hardcover and
  Audible. Open Library and Hardcover are asked for every language (they know translations, and the work behind
  them). Audible only when the operator switched it on, Hardcover only with the operator's token.
* **The original language first**: the national libraries of the languages the catalogue is built for are asked
  first; the translations among their records name the language they were made from, and that language (when it is
  another) is then asked too, at its own library.
* **Pen names**: the other names of an author (Hardcover's, and the ones proven by the books of the libraries) are
  asked like the name, at every source but Hardcover (it lists those books under the author already).

Everything goes through the one way out (``sources/http.py``): throttled, counted, and only when the source may be
asked. A source that is away is noted, not waited for; the others still give the catalogue.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import jobs
from ..readers.names import compare_key
from ..readers.text import fold
from ..sources import audible, authorq, bnf, config, dnb, hardcover, http, kb, libris, nb, openlibrary
from ..sources.model import Entry
from . import people

logger = logging.getLogger("nexbooks.oeuvre")

#: The most languages a catalogue is built for, and pen names asked about.
MAX_LANGUAGES = 4
MAX_PEN_NAMES = 3


@dataclass
class Gathered:
    entries: list[Entry] = field(default_factory=list)
    #: For each language, the sources that gave records in it (rule 3 of ``works.py``).
    answered: dict[str, set[str]] = field(default_factory=dict)
    #: For each source asked: how many records it gave; and the sources that were away.
    counts: Counter[str] = field(default_factory=Counter)
    away: set[str] = field(default_factory=set)
    #: The other names Hardcover knows the author by (pen names among them).
    alternates: list[str] = field(default_factory=list)
    #: The pen names among them, and the ones the libraries proved: asked like the name.
    pens: list[str] = field(default_factory=list)
    orig_language: str = ""
    languages: list[str] = field(default_factory=list)
    #: How many other people of the name the sources showed (``people.py``): their records are left out, and a work
    #: no number of a source proves is not sure.
    namesakes: int = 0


def pen_names(name: str, alternates: list[str]) -> list[str]:
    """The other names that are names of another person (``Richard Vane``), not spellings of the author's
    (``E. Halloran``, ``Halloran, Edmund``) and not a name in another script: a name of two or three words whose last
    name is not the author's."""
    surname = fold(name).split()[-1] if fold(name).split() else ""
    found: list[str] = []
    for item in alternates:
        folded = fold(item)
        if not folded or surname in folded.split() or not re.fullmatch(r"[a-z]+( [a-z.\-']+){1,2}", folded):
            continue
        if sorted(folded.split()) in [sorted(fold(x).split()) for x in found]:
            continue
        found.append(item)
    return found


def _ask(out: Gathered, source: str, call, via: str) -> None:  # type: ignore[no-untyped-def]
    """Asks one source and puts what it gave into ``out``; a source that may not be asked is passed over, one that
    fails is noted as away."""
    if source in out.away:
        return
    try:
        found = call()
    except http.SourceOff:
        return
    except http.SourceError as exc:
        logger.info("A source was away while the works of an author were gathered source=%s error=%s", source,
                    exc.code)
        out.away.add(source)
        return
    if jobs.stopping.is_set():
        raise jobs.Interrupted
    for entry in found:
        entry.via = via
        out.entries.append(entry)
        if entry.language:
            out.answered.setdefault(entry.language, set()).add(entry.source)
    out.counts[source] += len(found)


def original_language(entries: list[Entry], hints: list[str], weak: list[str] | None = None) -> str:
    """The language the author writes in: what the translations among the records say they were made from; else what
    the books of the libraries say (their original language); else the language in which Open Library's works carry
    their own title (a work is named in the language it was written in); else the language the books of the libraries
    are in; else the language of most records."""
    said = Counter(entry.original_language for entry in entries if entry.original_language)
    if said:
        return said.most_common(1)[0][0]
    hinted = Counter(code for code in hints if code)
    if hinted:
        return hinted.most_common(1)[0][0]
    named = Counter(entry.language for entry in entries if entry.source == "openlibrary" and entry.language
                    and compare_key(str(entry.signals.get("work_title") or "")) == compare_key(entry.title))
    if named:
        return named.most_common(1)[0][0]
    shelf = Counter(code for code in weak or [] if code)
    if shelf:
        return shelf.most_common(1)[0][0]
    languages = Counter(entry.language for entry in entries if entry.language)
    return languages.most_common(1)[0][0] if languages else ""


def gather(db: Session, name: str, *, languages: list[str], aliases: list[str], hints: list[str],
           weak: list[str] | None = None, own: list[str] | None = None) -> Gathered:
    """Every record the sources have about the works of ``name`` in ``languages`` (and in the author's own language),
    without the records of other people of the name (``own``: the titles of the author's books of the libraries)."""
    out = Gathered()
    wanted = list(dict.fromkeys(code for code in languages if code))[:MAX_LANGUAGES] or ["en"]
    libraries_asked: set[tuple[str, str]] = set()

    def ask_library(language: str, who: str) -> None:
        source = config.NATIONAL_LIBRARIES.get(language)
        if source is None or (source, who) in libraries_asked:
            return
        libraries_asked.add((source, who))
        call = {"dnb": lambda: dnb.author_entries(db, who), "bnf": lambda: bnf.author_entries(db, who),
                "libris": lambda: libris.author_entries(db, who, language), "kb": lambda: kb.author_entries(db, who),
                "nb": lambda: nb.author_entries(db, who)}[source]
        _ask(out, source, call, who)

    # 1. The national libraries of the languages wanted: their translations say the author's own language.
    for language in wanted:
        ask_library(language, name)
    said = [entry for entry in out.entries if entry.original_language]
    orig = original_language(said, hints) if said or hints else ""
    if orig and orig not in wanted:
        ask_library(orig, name)
    # Unknown still: English is asked too (Open Library knows most works in English), and the language the works
    # carry their own title in decides.
    every = list(dict.fromkeys([*wanted, orig or "en"]))
    # 2. Open Library for each language, Hardcover once (with its other names of the author), Audible per market.
    for language in every:
        _ask(out, "openlibrary", lambda language=language: openlibrary.author_entries(db, name, language), name)
    if not orig:
        orig = original_language(out.entries, hints, weak)
        if orig and orig not in every:
            ask_library(orig, name)
            _ask(out, "openlibrary", lambda: openlibrary.author_entries(db, name, orig), name)
            every.append(orig)
    names: list[str] = []
    others: list[int] = []

    def from_hardcover() -> list[Entry]:
        entries, alternates, more = hardcover.author_catalog(db, name, every, orig)
        names.extend(alternates)
        others.append(more)
        return entries

    _ask(out, "hardcover", from_hardcover, name)
    out.alternates = names
    chosen = config.chosen_markets(db) if config.availability(db, "audible").ready else []
    markets = list(dict.fromkeys(config.MARKET_OF_LANGUAGE[code] for code in every
                                 if config.MARKET_OF_LANGUAGE.get(code) in chosen))
    for market in markets:
        _ask(out, "audible", lambda market=market: audible.author_entries(db, market, name), name)
    # 3. The pen names, at every source but Hardcover.
    pens = list(dict.fromkeys([*aliases, *pen_names(name, names)]))[:MAX_PEN_NAMES]
    for pen in pens:
        for language in every:
            ask_library(language, pen)
            _ask(out, "openlibrary", lambda language=language, pen=pen: openlibrary.author_entries(db, pen, language),
                 pen)
        for market in markets:
            _ask(out, "audible", lambda market=market, pen=pen: audible.author_entries(db, market, pen), pen)
    # 4. Who of the name each record is by (Open Library's authors of the name say their works and years).
    known: list[people.Namesake] = []
    try:
        known = [people.Namesake(number=hit.key, works=hit.work_count, born=hit.born)
                 for hit in openlibrary.find_authors(db, name) if authorq.is_author(name, hit.name)]
    except http.SourceOff:
        pass
    except http.SourceError as exc:
        logger.info("A source was away while the authors of a name were asked source=openlibrary error=%s", exc.code)
    sorted_out = people.sort_out(out.entries, own=own, known=known, others=sum(others))
    out.entries = sorted_out.entries
    out.namesakes = sorted_out.namesakes
    # (A source whose records were all somebody else's did not answer for the author.)
    out.answered = {}
    for entry in out.entries:
        if entry.language:
            out.answered.setdefault(entry.language, set()).add(entry.source)
    out.orig_language = orig
    out.languages = every
    out.alternates = list(dict.fromkeys([*pens, *names]))[:40]
    out.pens = pens
    return out


__all__ = ["Gathered", "gather", "original_language", "pen_names"]
