"""The National Library of the Netherlands (KB, SRU with the ``dcx`` schema, open): the Dutch titles of an author, for
the catalogue of their works (``services/oeuvre``). Measured: complete for a Dutch author (14 of 14, where Open Library
and Hardcover knew 11 each).

``dc:subject`` of the type ``dcx:NUR`` is the Dutch book trade's subject code: the two hundreds are books for children
and young people, the three hundreds fiction for adults, the rest non-fiction. The answer is XML: ``defusedxml``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from sqlalchemy.orm import Session

from ..readers.names import core_title, display_name
from ..readers.text import clean, isbn13
from . import authorq, cache, http, xmlsafe
from .model import Entry, as_written

SOURCE = "kb"
URL = "https://jsru.kb.nl/sru/sru"
PAGE = 200
MAX_PAGES = 5
_PAGES = re.compile(r"(\d{1,5})\s*(?:p\b|pagina)")
_YEAR = re.compile(r"(1[5-9]\d\d|20\d\d)")


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def entry_of(record: Any, name: str) -> Entry | None:
    title = ident = extent = date = ""
    creators: list[str] = []
    nur: list[str] = []
    isbns: list[str] = []
    for node in record.iter():
        tag = _local(node.tag)
        text = clean(node.text or "", 400)
        kind = next((value for key, value in node.attrib.items() if _local(key) == "type"), "")
        if tag == "title" and not title:
            title = text
        elif tag == "recordIdentifier" and not ident:
            ident = text.rsplit("=", 1)[-1][:40]
        elif tag == "identifier" and kind.endswith("ISBN"):
            code = isbn13(text)
            if code:
                isbns.append(code)
        elif tag == "extent" and not extent:
            extent = text
        elif tag == "date" and not date:
            date = text
        elif tag == "creator" and text:
            creators.append(text)
        elif tag == "subject" and kind.endswith("NUR") and text.isdigit():
            nur.append(text)
    if not title or not ident:
        return None
    if creators and not any(authorq.is_author(name, item) for item in creators):
        return None
    pages = _PAGES.search(extent)
    year = _YEAR.search(date)
    return Entry(source=SOURCE, key=ident, title=core_title(title) or title, language="nl",
                 authors=[display_name(authorq.without_dates(creators[0]))] if creators else [],
                 year=int(year.group(1)) if year else None, isbns=isbns[:10],
                 pages=int(pages.group(1)) if pages and 0 < int(pages.group(1)) < 20000 else None,
                 signals={"nur": nur[:4], **as_written(title, core_title(title) or title)},
                 authors_count=max(1, len(creators)),
                 person=next((found for item in creators if (found := authorq.person_by_years(name, item))), ""))


def parse(xml: bytes, name: str) -> tuple[list[Entry], int, int]:
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
        elif tag == "recordData":
            page += 1
            entry = entry_of(node, name)
            if entry is not None:
                found.append(entry)
    return found, page, total


def author_entries(db: Session, name: str) -> list[Entry]:
    """The Dutch books of an author, paged through. Kept for a day."""
    last, first = authorq.name_parts(name)
    if not last:
        return []

    def fetch() -> list[Entry]:
        entries: list[Entry] = []
        start = 1
        words = f"{last} {first}".strip().replace('"', " ")
        for _page in range(MAX_PAGES):
            url = URL + "?" + urlencode({
                "version": "1.2", "operation": "searchRetrieve", "x-collection": "GGC", "recordSchema": "dcx",
                "query": f'dc.creator="{words}" AND dc.language=dut AND dc.type=boek', "maximumRecords": PAGE,
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
