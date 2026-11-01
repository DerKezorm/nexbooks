"""The National Library of Norway (Nasjonalbiblioteket, JSON, open): the Norwegian titles of an author (Bokmål and
Nynorsk), for the catalogue of their works (``services/oeuvre``). Measured: complete for a Norwegian author (27 of 27,
where Open Library knew 14 and Hardcover 23).

The search takes the name as ``name:"Last, First"`` (``creators:`` finds little); a page holds at most 100 books and is
counted from 0. Whom a book is meant for lies only in a record of its own per book (MODS): not asked, one request per
book would be too many.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..readers.names import core_title, display_name
from ..readers.text import clean, isbn13
from . import authorq, cache, http
from .model import Entry, as_written

SOURCE = "nb"
URL = "https://api.nb.no/catalog/v1/items"
PAGE = 100
MAX_PAGES = 6
LANGUAGES = ("nob", "nno")
_YEAR = re.compile(r"(1[5-9]\d\d|20\d\d)")
_ID = re.compile(r"^[A-Za-z0-9]{8,64}$")


def entry_of(item: Any, name: str) -> Entry | None:
    if not isinstance(item, dict) or not _ID.match(str(item.get("id") or "")):
        return None
    meta = item.get("metadata")
    if not isinstance(meta, dict):
        return None
    written = clean(meta.get("title"), 400) if isinstance(meta.get("title"), str) else ""
    title = re.split(r"\s+[:/]\s+", written)[0].strip()
    if not title:
        return None
    creators = [clean(c, 200) for c in (meta.get("creators") or [])[:12] if isinstance(c, str)] \
        if isinstance(meta.get("creators"), list) else []
    if creators and not any(authorq.is_author(name, c) for c in creators):
        return None
    origin = meta.get("originInfo") if isinstance(meta.get("originInfo"), dict) else {}
    year = _YEAR.search(str(origin.get("issued") or ""))
    identifiers = meta.get("identifiers") if isinstance(meta.get("identifiers"), dict) else {}
    isbns = [code for value in (identifiers.get("isbn13") or [])[:10] if (code := isbn13(value))] \
        if isinstance(identifiers.get("isbn13"), list) else []
    pages = meta.get("pageCount")
    return Entry(source=SOURCE, key=str(item["id"])[:64], title=core_title(title) or title, language="no",
                 authors=[display_name(creators[0])] if creators else [], year=int(year.group(1)) if year else None,
                 isbns=isbns, pages=pages if isinstance(pages, int) and 0 < pages < 20000 else None,
                 authors_count=max(1, len(creators)), signals=as_written(written, core_title(title) or title),
                 person=next((found for item in creators if (found := authorq.person_by_years(name, item))), ""))


def author_entries(db: Session, name: str) -> list[Entry]:
    """The Norwegian books of an author, in both written forms, paged through. Kept for a day."""
    last, first = authorq.name_parts(name)
    if not last:
        return []

    def fetch() -> list[Entry]:
        entries: list[Entry] = []
        who = f"{last}, {first}".strip(", ").replace('"', " ")
        for language in LANGUAGES:
            for page in range(MAX_PAGES):
                url = URL + "?" + urlencode([("q", f'name:"{who}"'), ("filter", "mediatype:bøker"),
                                             ("filter", f"languages:{language}"), ("size", str(PAGE)),
                                             ("page", str(page))])
                answer = http.request(db, SOURCE, url)
                if answer.status_code == 404:
                    break
                data = http.json_of(SOURCE, answer)
                embedded = data.get("_embedded") if isinstance(data, dict) else None
                items = embedded.get("items") if isinstance(embedded, dict) else None
                items = items if isinstance(items, list) else []
                entries += [entry for item in items[:PAGE] if (entry := entry_of(item, name)) is not None]
                if len(items) < PAGE:
                    break
        return entries

    return authorq.kept(SOURCE, cache.make_key("author2", last, first), fetch)
