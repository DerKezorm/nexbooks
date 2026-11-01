"""The German National Library (SRU, MARC21 as XML; the data is CC0): ISBN, title, authors, publisher, year, language,
series, the library's subject group and subject headings of German books. Optional, and on from the start for a library
that holds German books: for German titles the library's record is the best there is (Open Library knows few of them).

**What the library's records are like**, and what is done about it (measured against the real service):

* One book is many records: every edition, every printing, the recording and the game of it. Records are *grouped* into
  one candidate per book (the same core title, the same author), with the **earliest year** of any of its editions (a
  record's year is the year of the edition, not of the book) and all their ISBNs.
* Records of recordings and of things that are no book (a game, a calendar) are told by their type and leader; an
  e-book is not looked for in the records of recordings, and merchandise is never taken.
* A search answers up to 100 records a page and the library holds hundreds for a popular author: the answer is paged
  through (``MAX_PAGES``), politely (every page is one request of the throttle).
* The titles are advertising ("Eine unbeliebte Dame (Ein Krimi 1): Ein Mord ..."): the core title is kept
  (``names.core_title``) and the rest read for what it says of the genre. Names are written twice by careless records
  ("Jussi Jussi Adler-Olsen"): repeated words are one.
* The **subject group** (field 082: ``B`` fiction, ``K`` children's and young adult books, digits for non-fiction) and
  the subject headings (650, 655, 689) say what kind of book it is: they become the subjects of the candidate, where
  the genre of the shelf is voted from (``genres.tally``).
* A translation (field 041 ``$h`` names the language it was translated from) is **kept, with its original language**:
  a book in German by a foreign author is one. ``drop_translations`` leaves them out for whoever builds a catalogue
  of an author's own works.

The answer is XML from the internet and is read with ``defusedxml``: a document with a DTD or entities is refused, not
expanded. It is also bounded in size (``http.JSON_MAX``) before it is parsed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from sqlalchemy.orm import Session

from .. import genres
from ..readers.names import authors_compatible, clean_authors, core_title, display_name, person_key, titles_alike
from ..readers.text import clean, isbn13, year_of
from . import authorq, cache, http, xmlsafe
from .model import Candidate, Entry, Query, as_written, language_code

logger = logging.getLogger("nexbooks.sources")

SOURCE = "dnb"
URL = "https://services.dnb.de/sru/dnb"
#: Records a page holds, and pages at most (so at most 300 records of a search; a title and an author find a handful).
PAGE = 100
MAX_PAGES = 3
#: Candidates kept from one search (the best-known books first).
MAX_BOOKS = 12
_ISBN_WORD = re.compile(r"[0-9Xx\-]{10,17}")
#: ISBD punctuation at the end of a field ("Das Gezeitenbuch :", "Mara Lindqvist /").
_TRAILING = re.compile(r"[\s:/;,.=+]+$")
_PAGE_COUNT = re.compile(r"(\d{1,5})\s*(?:S\.|Seiten)")
#: The GND number of a person in ``$0`` (``(DE-588)118540238``).
_GND = re.compile(r"^\(DE-588\)([0-9]{7,10}[0-9X]?(?:-[0-9X])?)$")
_RUNNING_TIME = re.compile(r"\b(\d{2,4})\s*(?:min|minuten)\b", re.IGNORECASE)
#: Words in a record that say it is a recording, or a thing that is no book.
_AUDIO_WORDS = re.compile(r"tontr[aä]ger|h[oö]rbuch|\bcds?\b|mp3|gesprochenes wort|audio", re.IGNORECASE)
_THING_WORDS = re.compile(r"spiel|kalender|poster|postkarte|puzzle|dvd|blu-ray|karten\b|brettspiel|merchandise",
                          re.IGNORECASE)
#: Leader positions 6 of a record that is no text: recordings, music, maps, pictures, films, kits, objects.
_AUDIO_LEADER = frozenset("ij")
_THING_LEADER = frozenset("cdefgkopr")


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _subfields(field_: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for sub in field_:
        if _local(sub.tag) == "subfield":
            found.setdefault(sub.get("code", ""), []).append(clean(sub.text or "", 500))
    return found


def _first(subs: dict[str, list[str]], code: str) -> str:
    values = [v for v in subs.get(code, []) if v]
    return _TRAILING.sub("", values[0]).strip() if values else ""


@dataclass
class Record:
    """One record of the library, cut down to what nexbooks uses."""

    ident: str
    title: str
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    isbns: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    #: The language a translation was made from (041 $h), and the title it has in that language (240).
    translated_from: str = ""
    original_title: str = ""
    year: int | None = None
    publisher: str = ""
    series: str = ""
    position: float | None = None
    #: ``text``, ``audio`` (a recording) or ``thing`` (a game, a calendar: no book).
    form: str = "text"
    subject_group: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    running_time: int | None = None
    pages: int | None = None
    #: The record names a language at all (041 $a), even one nexbooks has no code for.
    language_given: bool = False
    #: The GND number of each author the record names (``(DE-588)…`` in ``$0``): two people of one name have two.
    numbers: dict[str, str] = field(default_factory=dict)
    #: A record of one part of a book or a series (245 ``$n`` and ``$p``: ``Die Krone von Varn``, ``Teil 2.``, ``Der
    #: Silberpass``), and whether it hangs under a record of the whole (773).
    part_number: str = ""
    part_name: str = ""
    host: bool = False
    #: Who made it, as the title page says (245 ``$c``: ``Regie & Schnitt: …``, ``Drehbuch: …``).
    responsibility: str = ""


def _form(leader: str, c007: list[str], fields: dict[str, list[dict[str, list[str]]]]) -> str:
    kind = leader[6] if len(leader) > 6 else "a"
    said = " ".join(
        " ".join(values) for tag, code in (("245", "h"), ("300", "a"), ("336", "a"), ("337", "a"), ("338", "a"))
        for subs in fields.get(tag, []) for values in [subs.get(code, [])])
    first007 = c007[0][:1].lower() if c007 and c007[0] else ""
    if kind in _AUDIO_LEADER or first007 == "s" or _AUDIO_WORDS.search(said):
        return "audio"
    if kind in _THING_LEADER or first007 in ("v", "k", "m", "o", "g", "a", "r") or _THING_WORDS.search(said):
        return "thing"
    return "text"


def _record(record: Any) -> Record | None:
    fields: dict[str, list[dict[str, list[str]]]] = {}
    c007: list[str] = []
    ident = leader = ""
    for node in record:
        name = _local(node.tag)
        if name == "leader":
            leader = node.text or ""
        elif name == "controlfield" and node.get("tag") == "001":
            ident = clean(node.text or "", 40)
        elif name == "controlfield" and node.get("tag") == "007":
            c007.append(node.text or "")
        elif name == "datafield":
            fields.setdefault(node.get("tag", ""), []).append(_subfields(node))
    title = _first(fields["245"][0], "a") if fields.get("245") else ""
    if not ident or not title:
        return None
    authors: list[str] = []
    numbers: dict[str, str] = {}
    for tag in ("100", "700"):
        for subs in fields.get(tag, []):
            relation = " ".join(subs.get("4", [])).lower()
            name = _first(subs, "a")
            # Authors only: a translator or an editor (700 with another relator) is not who wrote the book.
            if name and (tag == "100" or not relation or "aut" in relation):
                authors.append(display_name(name))
                gnd = next((found.group(1) for value in subs.get("0", []) if (found := _GND.match(value or ""))), "")
                if gnd:
                    numbers.setdefault(display_name(name), gnd)
    isbns: list[str] = []
    for subs in fields.get("020", []):
        for value in subs.get("a", []) + subs.get("9", []):
            match = _ISBN_WORD.search(value or "")
            code = isbn13(match.group(0)) if match else ""
            if code and code not in isbns:
                isbns.append(code)
    publisher = year = ""
    for tag in ("264", "260"):
        if fields.get(tag):
            publisher = _first(fields[tag][0], "b")
            year = _first(fields[tag][0], "c")
            break
    series, position = "", None
    for tag in ("830", "490", "800"):
        for subs in fields.get(tag, []):
            # (800 names the series as the title of the whole, in ``$t``.)
            name = _first(subs, "t" if tag == "800" else "a")
            if name:
                series = name
                digits = re.search(r"\d{1,4}(?:[.,]\d{1,2})?", _first(subs, "v"))
                position = float(digits.group(0).replace(",", ".")) if digits else None
                break
        if series:
            break
    languages = [code for subs in fields.get("041", []) for value in subs.get("a", [])
                 if (code := language_code(value))]
    translated = next((code for subs in fields.get("041", []) for value in subs.get("h", [])
                       if (code := language_code(value))), "")
    group = [clean(value, 8).upper() for tag in ("082", "084") for subs in fields.get(tag, [])
             for value in subs.get("a", []) if clean(value, 8)]
    headings = [clean(value, 120) for tag in ("650", "655", "689") for subs in fields.get(tag, [])
                for value in subs.get("a", []) if clean(value, 120)]
    extent = " ".join(" ".join(subs.get("a", []) + subs.get("b", [])) for subs in fields.get("300", []))
    minutes = _RUNNING_TIME.search(extent)
    pages = _PAGE_COUNT.search(extent)
    uniform = _first(fields["240"][0], "a") if fields.get("240") else ""
    head = fields["245"][0] if fields.get("245") else {}
    part_number = next((_TRAILING.sub("", value).strip() for value in head.get("n", []) if value.strip()), "")
    part_name = next((_TRAILING.sub("", value).strip(" /") for value in head.get("p", []) if value.strip()), "")
    return Record(
        ident=ident, title=title, subtitle=_first(fields["245"][0], "b"),
        authors=clean_authors(authors), isbns=isbns, languages=list(dict.fromkeys(languages)),
        translated_from=translated, original_title=uniform, year=year_of(year), publisher=publisher[:200],
        series=series, position=position, form=_form(leader, c007, fields), subject_group=group[:6],
        subjects=list(dict.fromkeys(headings))[:30], running_time=int(minutes.group(1)) if minutes else None,
        pages=int(pages.group(1)) if pages and 0 < int(pages.group(1)) < 20000 else None,
        language_given=any(value.strip() for subs in fields.get("041", []) for value in subs.get("a", [])),
        numbers=numbers, part_number=part_number[:60], part_name=part_name[:300], host=bool(fields.get("773")),
        responsibility=_first(head, "c")[:300])


def _group_subjects(codes: list[str]) -> list[str]:
    """The subject group of the library as words the genre is voted from: ``B`` is fiction, ``K`` children's and young
    adult books, a number (not the 8 of literature) is non-fiction."""
    found: list[str] = []
    for code in codes:
        if code == "B":
            found.append("Belletristik")
        elif code == "K":
            found.append("Kinder- und Jugendliteratur")
        elif code[:1].isdigit() and not code.startswith("8"):
            found.append("Sachbuch")
    return list(dict.fromkeys(found))


_RECORD_DATA = re.compile(rb"<(?:\w+:)?recordData\b[^>]*>.*?</(?:\w+:)?recordData>", re.DOTALL)
_ENDS_WELL = re.compile(rb"</(?:\w+:)?searchRetrieveResponse>\s*$")
_TOTAL = re.compile(rb"numberOfRecords>\s*(\d{1,9})\s*<")


def _records_of(roots: list[Any]) -> tuple[list[Record], int, int]:
    found: list[Record] = []
    page = total = 0
    for root in roots:
        for node in root.iter():
            name = _local(node.tag)
            if name == "numberOfRecords":
                total = int(node.text) if (node.text or "").strip().isdigit() else 0
            elif name == "record" and any(_local(child.tag) in ("datafield", "controlfield") for child in node):
                page += 1
                record = _record(node)
                if record is not None and len(found) < PAGE:
                    found.append(record)
    return found, page, total


def _record_by_record(xml: bytes) -> tuple[list[Record], int, int]:
    """An answer that is whole (it ends as an SRU answer does) but holds a character XML does not allow (a control
    character in a title, or a reference to one): the records are read one by one and the one that cannot be read is
    left out, not the answer with it. Anything else that is wrong with the answer is still a refusal."""
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml or not _ENDS_WELL.search(xml) or b"searchRetrieveResponse" not in xml:
        raise ValueError("not safe XML")
    roots: list[Any] = []
    for block in _RECORD_DATA.findall(xml)[: PAGE * 2]:
        try:
            roots.append(ElementTree.fromstring(block, forbid_dtd=True))
        except (ElementTree.ParseError, DefusedXmlException, UnicodeError):
            logger.info("A record of the National Library could not be read and is left out")
    counted = _TOTAL.search(xml)
    found, page, total = _records_of(roots)
    return found, page, int(counted.group(1)) if counted else total


def parse_page(xml: bytes) -> tuple[list[Record], int, int]:
    """The records of an SRU answer, the number of records the page held, and how many the search found in all.
    ``ValueError`` for XML that is none, or that carries a DTD or entities. A record with a character XML does not allow
    is left out and the rest of the answer is read."""
    if xmlsafe.too_deep(xml):
        raise ValueError("not safe XML")
    try:
        root = ElementTree.fromstring(xml, forbid_dtd=True)
    except DefusedXmlException as exc:
        raise ValueError("not safe XML") from exc
    except (ElementTree.ParseError, UnicodeError) as exc:
        if any(word in str(exc) for word in ("invalid token", "invalid character")):
            return _record_by_record(xml)
        raise ValueError("not safe XML") from exc
    return _records_of([root])


def _candidate(record: Record) -> Candidate:
    return Candidate(
        source=SOURCE, key=record.ident, title=core_title(record.title), subtitle=record.subtitle,
        authors=record.authors, isbns=record.isbns, languages=record.languages, year=record.year,
        series=record.series, series_pos=record.position, publisher=record.publisher,
        original_title=record.original_title, original_language=record.translated_from,
        subjects=_subjects_of([record]))


def _subject_group_of(records: list[Record]) -> list[str]:
    """The subject group of a book: what most of its records say (a school edition or a reader of the same book sits
    in another group and must not make a novel a non-fiction book), the same count going to fiction first."""
    counts: dict[str, int] = {}
    for record in records:
        for word in _group_subjects(record.subject_group):
            counts[word] = counts.get(word, 0) + 1
    order = {"Belletristik": 0, "Kinder- und Jugendliteratur": 1, "Sachbuch": 2}
    return [min(counts, key=lambda word: (-counts[word], order.get(word, 9)))] if counts else []


def _subjects_of(records: list[Record]) -> list[str]:
    """The words the genre is voted from: the subject headings, the subject group (what most of the records say), and
    what the titles say of the kind of book (the part of a title after the core: ``Kriminalroman``, ``Ein
    Bodenstein-Kirchhoff-Krimi 1``)."""
    words: list[str] = []
    for record in records:
        words += record.subjects
        words += _hints(record.title) + _hints(record.subtitle)
    words += _subject_group_of(records)
    return list(dict.fromkeys(word for word in words if word))[:40]


def _hints(text: str) -> list[str]:
    """The parts of a title or subtitle that name a genre: the brackets, and a subtitle that is only a few words."""
    parts = re.findall(r"[\(\[]([^()\[\]]{2,80})[\)\]]", text or "")
    parts.append(re.sub(r"[\(\[].*", "", text or "").strip(" /:;,.-"))
    return [part for part in parts if part and len(part.split()) <= 6 and genres.genre_of_one(part)]


def parse(xml: bytes) -> list[Candidate]:
    """The records of an SRU answer, each as a candidate of its own. ``ValueError`` for XML that is none, or that
    carries a DTD or entities."""
    return [_candidate(record) for record in parse_page(xml)[0]]


def books_of(records: list[Record], query: Query, *, drop_translations: bool = False) -> list[Candidate]:
    """The books the records of a search are: one candidate for the records of one book. What does not fit is left out:
    a record in another language than the one asked for, a recording when an e-book is asked for, a thing that is no
    book; and, when ``drop_translations`` is set, a translation."""
    kept = [record for record in records
            if record.form != "thing"
            and (query.kind == "audio" or record.form == "text")
            and (not query.language or not record.languages or query.language in record.languages)
            and not (drop_translations and record.translated_from)]
    groups: list[list[Record]] = []
    for record in kept:
        for group in groups:
            lead = group[0]
            same_author = not record.authors or not lead.authors or any(
                person_key(a) == person_key(b) or authors_compatible(a, b)
                for a in record.authors for b in lead.authors)
            if same_author and titles_alike(record.title, lead.title):
                group.append(record)
                break
        else:
            groups.append([record])
    groups.sort(key=lambda group: -len(group))
    books: list[Candidate] = []
    for group in groups[:MAX_BOOKS]:
        # The record to speak for the book: a text before a recording, then the earliest.
        ordered = sorted(group, key=lambda r: (r.form != "text", r.year or 9999, r.ident))
        lead = ordered[0]
        years = [r.year for r in group if r.year]
        isbns = list(dict.fromkeys(isbn for r in ordered for isbn in r.isbns))[:60]
        book = _candidate(lead)
        book.isbns = isbns
        book.year = min(years) if years else None
        book.subjects = _subjects_of(group)
        book.weight = len(group)
        book.languages = list(dict.fromkeys(code for r in group for code in r.languages))
        book.original_language = next((r.translated_from for r in group if r.translated_from), "")
        book.original_title = next((r.original_title for r in group if r.original_title), "")
        book.series = next((r.series for r in group if r.series), "")
        book.series_pos = next((r.position for r in group if r.series and r.position is not None), None)
        if genres.genre_of_one(book.subtitle) and len(book.subtitle.split()) <= 4:
            book.subtitle = ""
        books.append(book)
    return books


def _quote(text: str) -> str:
    return '"' + re.sub(r'["\\]', " ", text) + '"'


def _ask(db: Session, cql: str, interactive: bool, pages: int, size: int = PAGE) -> list[Record]:
    """One question, paged through up to ``pages`` pages of ``size`` records (as far as the library found records)."""
    records: list[Record] = []
    start = 1
    for _page in range(pages):
        url = URL + "?" + urlencode({"version": "1.1", "operation": "searchRetrieve", "query": cql,
                                     "recordSchema": "MARC21-xml", "maximumRecords": size, "startRecord": start})
        answer = http.request(db, SOURCE, url, headers={"Accept": "application/xml"}, interactive=interactive)
        if answer.status_code == 404:
            break
        try:
            found, count, total = parse_page(answer.content)
        except ValueError as exc:
            http.throttle.fail(SOURCE, "bad_answer")
            raise http.SourceError("bad_answer") from exc
        known = {r.ident for r in records}
        records += [r for r in found if r.ident not in known]
        start += count
        if count == 0 or start > total:
            break
    return records


def search(db: Session, query: Query, *, interactive: bool = False) -> list[Candidate]:
    author = clean(query.authors[0], 100) if query.authors else ""
    title = core_title(clean(query.title, 200), author)
    key = cache.make_key("search2", query.kind, query.language, query.isbn, title, author)
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING:
        return [Candidate.from_json(item) for item in kept]
    queries: list[tuple[str, int]] = []
    if query.isbn:
        queries.append((f"num={_quote(query.isbn)}", 1))
    if title:
        queries.append((f"tit={_quote(title)}" + (f" and per={_quote(author)}" if author else ""), MAX_PAGES))
    if not queries:
        return []
    records: list[Record] = []
    for cql, pages in queries:
        # (A second question adds to the first one's records; the ISBN settles the book when a record lists it.)
        known = {r.ident for r in records}
        records += [r for r in _ask(db, cql, interactive, pages) if r.ident not in known]
        if query.isbn and any(query.isbn in r.isbns for r in records):
            break
    found = books_of(records, query)
    cache.put(SOURCE, key, [item.to_json() for item in found], hit=bool(found))
    return found


# ---- The works of an author (the catalogue of an author's works) ---------------------------------------------------

#: Pages of an author's records at most: a popular author has hundreds (every edition, every recording).
AUTHOR_PAGES = 10


def entry_of(record: Record, author: str = "") -> Entry | None:
    """A record as an entry of the catalogue of an author's works (``author``: the name asked about, whose GND number
    the entry keeps); a thing that is no book is none."""
    if record.form == "thing":
        return None
    person = next((number for name, number in record.numbers.items() if author and authorq.is_author(author, name)),
                  "")
    return Entry(
        source=SOURCE, key=record.ident, title=core_title(record.title) or record.title,
        # (A language the record names that has no code here is an unknown one, never German by default.)
        language=record.languages[0] if record.languages else ("und" if record.language_given else "de"),
        original_title=record.original_title,
        original_language=record.translated_from, authors=record.authors[:6], year=record.year,
        isbns=record.isbns[:10], pages=record.pages, runtime_min=record.running_time,
        form="audio" if record.form == "audio" else "text", series=record.series, series_pos=record.position,
        signals={"group": record.subject_group[:4], "subjects": record.subjects[:12],
                 **as_written(record.title, core_title(record.title) or record.title, subtitle=record.subtitle,
                              part=f"{record.part_number} {record.part_name}", credits=record.responsibility)},
        authors_count=max(1, len(record.authors)), person=person)


def author_entries(db: Session, name: str) -> list[Entry]:
    """Every record of an author at the library (``per="Last, First"``), paged through, translations included: a book
    in German by a foreign author is the German edition of their book. Records of another author of the same last name
    are left out. Kept for a day."""
    last, first = authorq.name_parts(name)
    if not last:
        return []

    def fetch() -> list[Entry]:
        who = f"{last}, {first}".strip(", ")
        records = _ask(db, f"per={_quote(who)}", False, AUTHOR_PAGES)
        found = []
        for record in records:
            if record.authors and not any(authorq.is_author(name, author) for author in record.authors):
                continue
            entry = entry_of(record, name)
            if entry is not None:
                found.append(entry)
        return found

    return authorq.kept(SOURCE, cache.make_key("author2", last, first), fetch)


# ---- Searching by the words a person typed (the catalogue search) ------------------------------------------------

#: Records a search by words reads: one page (the books a person looks for are among the first ones the library finds).
TEXT_RECORDS = 50
#: The words a search sends at most.
TEXT_WORDS = 6
_TEXT_WORD = re.compile(r"[^\W\d_]{2,}|\d{5,}", re.UNICODE)


def text_words(text: str) -> list[str]:
    """The words of a search the library is asked for: words of two letters or more (a bare number like the ``2`` of
    ``Reihe 2`` finds every record that has a 2 anywhere), at most ``TEXT_WORDS``, none twice."""
    found = [word.casefold() for word in _TEXT_WORD.findall(clean(text, 200))]
    return list(dict.fromkeys(found))[:TEXT_WORDS]


def word_start(word: str) -> str:
    """The start of a word that a word typed with a slip still has (``Feuerkelch`` and ``Feuerkelsch`` both start
    ``feuerk``): three fifths of it, three letters at least; a word of three letters or fewer whole."""
    if len(word) <= 3 or not word.isalpha():
        return word
    return word[: max(3, -(-len(word) * 3 // 5))]


def text_entries(db: Session, text: str, *, volume: float | None = None, interactive: bool = False,
                 starts: bool = False) -> list[Entry]:
    """The records whose fields hold every word of ``text`` (``woe``: title, people, series, subjects), one page, as
    entries of the catalogue (``entry_of``) with the subtitle, the publisher and the place of the record in the answer.
    A ``volume`` is asked for in the title (which holds the series and its number: ``Reihe ; 2``). With ``starts``, the
    words are asked for by their start (``word_start``: a slip of the keys, ``Hary Poter``). Kept for a day."""
    words = text_words(text)
    if starts:
        words = list(dict.fromkeys(word_start(word) for word in words))
    if not words:
        return []
    number = f"{volume:g}" if volume is not None and 0 < volume < 1000 and volume == int(volume) else ""

    def fetch() -> list[Entry]:
        asked = [_quote(word + "*" if starts and word.isalpha() and len(word) >= 3 else word) for word in words]
        cql = " and ".join([*(f"woe={word}" for word in asked), *([f"tit={number}"] if number else [])])
        found: list[Entry] = []
        for index, record in enumerate(_ask(db, cql, interactive, 1, TEXT_RECORDS)):
            entry = entry_of(record)
            if entry is None:
                continue
            entry.signals = {**entry.signals, "rank": index, "subtitle": record.subtitle[:200],
                             "full_title": record.title[:300], "publisher": record.publisher[:120]}
            if record.responsibility:
                entry.signals["credits"] = record.responsibility[:200]
            if not record.languages:
                # (A record that names no language is German by default here; the search reads the title as well.)
                entry.signals["language_assumed"] = True
            gnd = record.numbers.get(entry.authors[0]) if entry.authors else None
            if gnd:
                # (The authority number of the first author: two spellings of one person have the same.)
                entry.signals["gnd"] = gnd
            _as_part(entry, record)
            found.append(entry)
        return found

    return authorq.kept(SOURCE, cache.make_key("text3s" if starts else "text3", " ".join(words), number), fetch)


#: What a library writes as the name of a part that names the recording or the edition, not the book (``CDs.``,
#: ``Gelesen von …``, ``Mit Illustrationen von …``).
_NOT_A_PART_NAME = re.compile(r"^\s*(cds?|mp3|gelesen von|gesprochen von|read by|lesung|h[oö]rbuch|begleitbuch|"
                              r"mit illustrationen|illustriert|mit bildern|mit zeichnungen|illustrated)\b",
                              re.IGNORECASE)
_PART_NUMBER = re.compile(r"(?:teil|band|bd|buch|book|vol|volume|tome|folge)\.?\s*(\d{1,3})", re.IGNORECASE)


def part_number_of(text: str) -> float | None:
    """The number of a part as a library writes it (``Teil 2.``, ``Bd. 2``, ``Pbd. 6 : Teil 2.`` is 2, ``2.`` is 2,
    ``[...]`` is none)."""
    named = _PART_NUMBER.findall(text)
    if named:
        return float(named[-1])
    bare = re.fullmatch(r"\s*(\d{1,3})\s*\.?\s*", text)
    return float(bare.group(1)) if bare else None


def _as_part(entry: Entry, record: Record) -> None:
    """A record of one part (245 ``$n``, ``$p``): with a name of its own it is that book, the whole its series and the
    number its place (``Die Krone von Varn``, ``Teil 2.``, ``Der Silberpass``); with a number and no name it is one
    volume of a book printed in several (``Die Brüder``, ``Bd. 1``), which the search sets apart (``volume_part``)."""
    name = record.part_name.split(" / ", 1)[0].strip(" ,.;:/")
    if _NOT_A_PART_NAME.match(name):
        name = ""
    number = part_number_of(record.part_number) if record.part_number else None
    if name:
        whole = core_title(record.title) or record.title
        entry.title = core_title(name) or name
        entry.series, entry.series_pos = whole[:200], number
        entry.signals = {**entry.signals, "full_title": name[:300], "part_of": record.title[:300]}
    elif record.part_number:
        entry.signals = {**entry.signals, "volume_part": True,
                         "full_title": f"{record.title}. {record.part_number}"[:300]}
        if number is not None:
            entry.signals["volume"] = number
