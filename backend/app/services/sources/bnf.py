"""The French National Library (catalogue.bnf.fr, SRU, UNIMARC as XML; the data is open): the French titles of an
author, for the catalogue of their works (``services/oeuvre``). Measured: complete for French authors (66 of 66).

What a record says and what is taken from it:

* ``200 $a`` the title, ``101 $a`` the language, ``101 $c`` the language a translation was made from (a translation is
  kept: it is the French edition of a book written in another language), ``500 $a`` the title it has in that language;
* ``100 $a`` positions 9 to 12 the year, 17 to 19 the readers it is meant for (``a`` to ``e``: children and young
  people, ``k`` and ``m``: adults), ``105 $a`` position 11 the literary form (``a`` novel, ``f`` stories, ``y`` not
  literature, others: poems, plays, letters ...), ``215 $a`` the extent (the pages);
* ``700``/``701`` the author (last name ``$a``, first name ``$b``): a record of another author of the same last name
  is left out.

The answer is XML from the internet: read with ``defusedxml`` (no DTD, no entities) and bounded in size.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from sqlalchemy.orm import Session

from ..readers.names import clean_authors, core_title
from ..readers.text import clean, isbn13
from . import authorq, cache, http, xmlsafe
from .model import Entry, as_written, language_code

SOURCE = "bnf"
URL = "https://catalogue.bnf.fr/api/SRU"
PAGE = 50
MAX_PAGES = 12
_PAGES = re.compile(r"(\d{1,5})\s*p\.")
_YEAR = re.compile(r"(1[5-9]\d\d|20\d\d)")
#: The fields whose characters are read by their place (100: the dates and the readers, 105: the form).
_CODED = frozenset({"100", "105"})


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _fields(record: Any) -> tuple[str, dict[str, list[dict[str, list[str]]]]]:
    ident = ""
    fields: dict[str, list[dict[str, list[str]]]] = {}
    for node in record:
        name = _local(node.tag)
        if name == "controlfield" and node.get("tag") == "001":
            ident = clean(node.text or "", 40)
        elif name == "datafield":
            subs: dict[str, list[str]] = {}
            coded = node.get("tag", "") in _CODED
            for sub in node:
                if _local(sub.tag) == "subfield":
                    # A coded field counts its characters by place: its spaces are kept.
                    value = (sub.text or "")[:80] if coded else clean(sub.text or "", 400)
                    subs.setdefault(sub.get("code", ""), []).append(value)
            fields.setdefault(node.get("tag", ""), []).append(subs)
    return ident, fields


def _first(fields: dict[str, list[dict[str, list[str]]]], tag: str, code: str) -> str:
    for subs in fields.get(tag, []):
        for value in subs.get(code, []):
            if value:
                return value
    return ""


def entry_of(record: Any, name: str) -> Entry | None:
    """One record as an entry, or None when it is none of the author's, has no title or is no text."""
    ident, fields = _fields(record)
    title = _first(fields, "200", "a")
    if not ident or not title:
        return None
    people = []
    numbers = []
    for tag in ("700", "701"):
        for subs in fields.get(tag, []):
            last = (subs.get("a") or [""])[0]
            first = (subs.get("b") or [""])[0]
            if last:
                people.append(f"{first} {last}".strip())
                # (``$f`` holds the years of the life: ``1951-....``.)
                numbers.append(authorq.person_by_years(name, f"{last}, {first}, {(subs.get('f') or [''])[0]}"))
    if people and not any(authorq.is_author(name, person) for person in people):
        return None
    general = _first(fields, "100", "a")
    coded = _first(fields, "105", "a")
    extent = " ".join(" ".join(subs.get("a", [])) for subs in fields.get("215", []))
    pages = _PAGES.search(extent)
    year_text = general[9:13] if len(general) >= 13 else ""
    year = int(year_text) if year_text.isdigit() and 1450 < int(year_text) < 2200 else None
    if year is None:
        found = _YEAR.search(_first(fields, "210", "d") or _first(fields, "214", "d"))
        year = int(found.group(1)) if found else None
    isbns = [code for subs in fields.get("010", []) for value in subs.get("a", []) if (code := isbn13(value))]
    language = language_code(_first(fields, "101", "a")) or "fr"
    return Entry(
        source=SOURCE, key=ident, title=core_title(title) or title, language=language,
        original_language=language_code(_first(fields, "101", "c")), original_title=_first(fields, "500", "a"),
        authors=clean_authors(people)[:6], year=year, isbns=isbns[:10],
        pages=int(pages.group(1)) if pages and 0 < int(pages.group(1)) < 20000 else None,
        # (Field 225 is the publisher's collection, "J'ai lu", not a series of the author: it is left out.)
        signals={"audience": general[17:20].strip() if len(general) >= 20 else "",
                 "litcode": coded[11] if len(coded) > 11 else "", "form": _first(fields, "200", "e").lower()[:80],
                 **as_written(title, core_title(title) or title, subtitle=_first(fields, "200", "e"),
                              part=_first(fields, "200", "h"))},
        authors_count=max(1, len(people)), person=next((number for number in numbers if number), ""))


def parse(xml: bytes, name: str) -> tuple[list[Entry], int, int]:
    """The entries of an answer, the records the page held and the records found in all. ``ValueError`` for XML that is
    none, or that carries a DTD or entities."""
    if xmlsafe.too_deep(xml):
        raise ValueError("not safe XML")
    try:
        root = ElementTree.fromstring(xml, forbid_dtd=True)
    except (DefusedXmlException, ElementTree.ParseError, UnicodeError) as exc:
        raise ValueError("not safe XML") from exc
    found: list[Entry] = []
    page = total = 0
    for node in root.iter():
        tag = _local(node.tag)
        if tag == "numberOfRecords" and (node.text or "").strip().isdigit():
            total = int((node.text or "0").strip())
        elif tag == "record" and node.get("type") == "Bibliographic":
            page += 1
            entry = entry_of(node, name)
            if entry is not None:
                found.append(entry)
    return found, page, total


def author_entries(db: Session, name: str) -> list[Entry]:
    """The French records of an author (and the French editions of their books in other languages), paged through.
    Kept for a day."""
    last, first = authorq.name_parts(name)
    if not last:
        return []

    def fetch() -> list[Entry]:
        entries: list[Entry] = []
        start = 1
        words = f"{last} {first}".strip().replace('"', " ")
        for _page in range(MAX_PAGES):
            url = URL + "?" + urlencode({
                "version": "1.2", "operation": "searchRetrieve", "recordSchema": "unimarcXchange",
                "query": f'bib.author all "{words}" and bib.doctype all "a"', "maximumRecords": PAGE,
                "startRecord": start})
            answer = http.request(db, SOURCE, url, headers={"Accept": "application/xml"})
            if answer.status_code == 404:
                break
            try:
                found, count, total = parse(answer.content, name)
            except ValueError as exc:
                http.throttle.fail(SOURCE, "bad_answer")
                raise http.SourceError("bad_answer") from exc
            entries += found
            start += count
            if count == 0 or start > total:
                break
        return entries

    return authorq.kept(SOURCE, cache.make_key("author2", last, first), fetch)
