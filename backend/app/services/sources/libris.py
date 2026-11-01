"""LIBRIS, the Swedish union catalogue of the National Library of Sweden (``xsearch``, JSON, open): the Swedish titles
of an author, for the catalogue of their works (``services/oeuvre``). Measured: complete for a Swedish author (34 of
34), but a popular author has well over a thousand records, nearly all translations into other languages: only the
Swedish ones are kept.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..readers.names import core_title
from ..readers.text import clean, isbn13
from . import authorq, cache, http
from .model import Entry, as_written, language_code

SOURCE = "libris"
URL = "https://libris.kb.se/xsearch"
PAGE = 200
MAX_PAGES = 8
_YEAR = re.compile(r"(1[5-9]\d\d|20\d\d)")
_ID = re.compile(r"/bib/([A-Za-z0-9]{1,40})$")


def _one(value: Any) -> str:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str) and item.strip()), "")
    return clean(value, 400) if isinstance(value, str) else ""


def _all(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [clean(item, 400) for item in items[:12] if isinstance(item, str) and item.strip()]


def entry_of(record: Any, name: str, language: str) -> Entry | None:
    """One record as an entry, or None when it is in another language, of another author, or no book."""
    if not isinstance(record, dict):
        return None
    if language not in {language_code(item) for item in _all(record.get("language"))}:
        return None
    creators = _all(record.get("creator"))
    if creators and not any(authorq.is_author(name, item) for item in creators):
        return None
    kind = _one(record.get("type")).lower()
    if kind and kind not in ("text", "book", "ebook"):
        return None
    written = _one(record.get("title"))
    title = re.split(r"\s+[:/]\s+", written)[0].strip()
    ident = _ID.search(_one(record.get("identifier")))
    if not title or ident is None:
        return None
    years = [int(found.group(1)) for item in _all(record.get("date")) if (found := _YEAR.search(item))]
    isbns = [code for item in _all(record.get("isbn")) if (code := isbn13(re.sub(r"[^0-9Xx]", "", item)))]
    return Entry(source=SOURCE, key=ident.group(1), title=core_title(title) or title, language=language,
                 authors=[authorq.without_dates(creators[0])] if creators else [], year=min(years) if years else None,
                 isbns=isbns[:10], form="text", signals=as_written(written, core_title(title) or title),
                 person=next((found for item in creators if (found := authorq.person_by_years(name, item))), ""))


def author_entries(db: Session, name: str, language: str = "sv") -> list[Entry]:
    """The Swedish records of an author, paged through. Kept for a day."""
    last, first = authorq.name_parts(name)
    if not last:
        return []

    def fetch() -> list[Entry]:
        entries: list[Entry] = []
        start = 1
        words = re.sub(r"[()\"]", " ", f"{last} {first}").strip()
        for _page in range(MAX_PAGES):
            url = URL + "?" + urlencode({"query": f"forf:({words})", "format": "json", "n": PAGE, "start": start})
            answer = http.request(db, SOURCE, url)
            if answer.status_code == 404:
                break
            data = http.json_of(SOURCE, answer)
            block = data.get("xsearch") if isinstance(data, dict) else None
            items = block.get("list") if isinstance(block, dict) else None
            items = items if isinstance(items, list) else []
            entries += [entry for item in items[:PAGE] if (entry := entry_of(item, name, language)) is not None]
            total = block.get("records") if isinstance(block, dict) else 0
            start += PAGE
            if not items or not isinstance(total, int) or start > total:
                break
        return entries

    return authorq.kept(SOURCE, cache.make_key("author2", last, first, language), fetch)
