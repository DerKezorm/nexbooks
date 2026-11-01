"""Open Library: free, open data (also available as a dump), the ground floor without Hardcover.

One endpoint does the searching, ``/search.json``: by ISBN, or by title and author, with the fields nexbooks uses asked
for by name (the default answer is huge). It gives works, not editions: a work knows the languages its editions are in
and a cover. The description lies one request further (``/works/<key>.json``) and is fetched only for a book that was
matched; so is the cover picture.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from ..readers.names import clean_authors, core_title
from ..readers.text import clean, html_to_text, isbn13
from . import authorq, cache, http
from .model import MARC_CODES, Candidate, Entry, Query, day_of, language_code

SOURCE = "openlibrary"
BASE = "https://openlibrary.org"
COVERS = "https://covers.openlibrary.org"
FIELDS = ("key,title,subtitle,author_name,first_publish_year,language,isbn,cover_i,subject,number_of_pages_median,"
          "edition_count,publisher")
_WORK = re.compile(r"^/works/(OL\d{1,12}W)$")
_KEY = re.compile(r"^OL\d{1,12}W$")
MAX_ISBNS = 40
MAX_SUBJECTS = 20


def _text(value: Any, limit: int = 300) -> str:
    return clean(value, limit) if isinstance(value, str) else ""


def _texts(value: Any, limit: int = 20) -> list[str]:
    return [_text(item, 200) for item in value[:limit] if _text(item, 200)] if isinstance(value, list) else []


def parse_search(data: Any, asked_isbn: str = "") -> list[Candidate]:
    """The works of a search answer as candidates; whatever is not a work with a title is left out. A work found by an
    ISBN keeps that ISBN first among its own (a work lists hundreds, and only the first ones are kept)."""
    docs = data.get("docs") if isinstance(data, dict) else None
    found: list[Candidate] = []
    for doc in docs[:50] if isinstance(docs, list) else []:
        if not isinstance(doc, dict):
            continue
        match = _WORK.match(str(doc.get("key") or ""))
        title = _text(doc.get("title"))
        if not match or not title:
            continue
        languages = list(dict.fromkeys(code for code in (language_code(value) for value in (doc.get("language") or [])
                                                         if isinstance(value, str)) if code))
        isbns = list(dict.fromkeys(code for code in (isbn13(value) for value in (doc.get("isbn") or [])[:2000]
                                                     if isinstance(value, str | int)) if code))
        if asked_isbn and asked_isbn in isbns:
            isbns.remove(asked_isbn)
            isbns.insert(0, asked_isbn)
        isbns = isbns[:MAX_ISBNS]
        year = doc.get("first_publish_year")
        cover = doc.get("cover_i")
        pages = doc.get("number_of_pages_median")
        publishers = _texts(doc.get("publisher"), 1)
        found.append(Candidate(
            source=SOURCE, key=match.group(1), work_key=match.group(1), title=title,
            subtitle=_text(doc.get("subtitle")),
            authors=clean_authors(_texts(doc.get("author_name"))), languages=languages, isbns=isbns,
            year=year if isinstance(year, int) and 1000 < year < 3000 else None,
            subjects=_texts(doc.get("subject"), MAX_SUBJECTS),
            pages=pages if isinstance(pages, int) and 0 < pages < 20000 else None,
            publisher=publishers[0] if publishers else "",
            cover_url=f"{COVERS}/b/id/{cover}-L.jpg" if isinstance(cover, int) and 0 < cover < 10**10 else "",
            weight=doc.get("edition_count") if isinstance(doc.get("edition_count"), int) else 0))
    return found


def _fetch(db: Session, params: dict[str, str | int], interactive: bool, asked_isbn: str = "") -> list[Candidate]:
    answer = http.request(db, SOURCE, f"{BASE}/search.json?{urlencode(params)}", interactive=interactive)
    if answer.status_code == 404:
        return []
    return parse_search(http.json_of(SOURCE, answer), asked_isbn)


def search(db: Session, query: Query, *, interactive: bool = False) -> list[Candidate]:
    """The works that fit the question: by ISBN when it has one, else by title and the first author. Asked once, then
    kept (a miss for a day, a hit for a month)."""
    author = clean(query.authors[0], 100) if query.authors else ""
    title = core_title(clean(query.title, 200), author)
    key = cache.make_key("search2", query.isbn, title, author, str(query.limit))
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING:
        return [Candidate.from_json(item) for item in kept]
    if not query.isbn and not title:
        return []
    found: list[Candidate] = []
    if query.isbn:
        found = _fetch(db, {"q": f"isbn:{query.isbn}", "fields": FIELDS, "limit": 5}, interactive, query.isbn)
    if title and not any(query.isbn in c.isbns for c in found):
        # No work that lists the ISBN (a German edition Open Library does not know, or a work found some other way): by
        # title and author as well.
        params: dict[str, str | int] = {"title": title, "fields": FIELDS, "limit": min(query.limit, 20)}
        if author:
            params["author"] = author
        more = _fetch(db, params, interactive)
        main = re.split(r"\s*[:(\-]\s", title)[0].strip()
        if not more and main and main != title:
            params["title"] = main
            more = _fetch(db, params, interactive)
        known = {c.key for c in found}
        found += [c for c in more if c.key not in known]
    cache.put(SOURCE, key, [item.to_json() for item in found], hit=bool(found))
    return found


def work_details(db: Session, work_key: str, *, interactive: bool = False) -> dict[str, str]:
    """The description of a work (``{"description": ...}``, empty when it has none). One request, kept."""
    if not _KEY.match(work_key or ""):
        return {}
    key = cache.make_key("work", work_key)
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING:
        return dict(kept)
    answer = http.request(db, SOURCE, f"{BASE}/works/{quote(work_key, safe='')}.json", interactive=interactive)
    details: dict[str, str] = {}
    if answer.status_code != 404:
        data = http.json_of(SOURCE, answer)
        description = data.get("description") if isinstance(data, dict) else None
        if isinstance(description, dict):
            description = description.get("value")
        text = html_to_text(description) if isinstance(description, str) else ""
        if text:
            details["description"] = text
    cache.put(SOURCE, key, details, hit=bool(details))
    return details


def cover_url_ok(url: str) -> bool:
    return http.host_allowed(SOURCE, url)


# ---- The works of an author, and authors by name ----------------------------------------------------

#: The fields of a work asked for the catalogue of an author: the editions in the language asked for come with it
#: (``lang`` puts an edition in that language first).
AUTHOR_FIELDS = ("key,title,author_name,author_key,first_publish_year,language,number_of_pages_median,subject,"
                 "edition_count,cover_i,editions,editions.key,editions.title,editions.language,editions.isbn,"
                 "editions.publish_date")
#: Works of an author asked for at most, and how large that answer may be (a work with its subjects is a few hundred
#: bytes; a prolific author has a few hundred works).
AUTHOR_WORKS = 400
AUTHOR_ANSWER_MAX = 6 * 1024 * 1024
#: Open Library's three letters of a language, for the ``lang`` of a search.
MARC_OF = {code: marc for marc, code in reversed(list(MARC_CODES.items()))}
_EDITION = re.compile(r"^/books/(OL\d{1,12}M)$")


def author_entries_of(data: Any, name: str, language: str) -> list[Entry]:
    """The works of a search answer that are the author's, each with its title in ``language``: the title of an edition
    in that language, or the work's own title when the work is only known in that language."""
    docs = data.get("docs") if isinstance(data, dict) else None
    found: list[Entry] = []
    for doc in docs[:AUTHOR_WORKS] if isinstance(docs, list) else []:
        if not isinstance(doc, dict):
            continue
        match = _WORK.match(str(doc.get("key") or ""))
        work_title = _text(doc.get("title"))
        names = _texts(doc.get("author_name"), 12)
        if not match or not work_title or not any(authorq.is_author(name, item) for item in names):
            continue
        # The numbers of the authors of that name (Open Library writes the names and the keys side by side): who the
        # work is by, when two people share the name.
        keys = doc.get("author_key") if isinstance(doc.get("author_key"), list) else []
        people = [str(keys[index]) for index, item in enumerate(names)
                  if index < len(keys) and authorq.is_author(name, item) and _AUTHOR_KEY.match(str(keys[index]))]
        languages = [code for value in (doc.get("language") or [])[:60] if isinstance(value, str)
                     and (code := language_code(value))]
        block = doc.get("editions")
        editions = block.get("docs") if isinstance(block, dict) else None
        title, isbns, released = "", [], ""
        for edition in editions[:5] if isinstance(editions, list) else []:
            if not isinstance(edition, dict):
                continue
            codes = {language_code(v) for v in (edition.get("language") or []) if isinstance(v, str)}
            if language in codes and _text(edition.get("title")):
                title = _text(edition.get("title"))
                isbns = [c for v in (edition.get("isbn") or [])[:20] if isinstance(v, str | int) and (c := isbn13(v))]
                released = day_of(edition.get("publish_date")) or _year_text(edition.get("publish_date"))
                break
        first = doc.get("first_publish_year")
        first = first if isinstance(first, int) and 1000 < first < 3000 else None
        year = int(released[:4]) if released else None
        if not title and languages == [language]:
            title, year = work_title, first
        if not title:
            continue
        pages = doc.get("number_of_pages_median")
        cover = doc.get("cover_i")
        found.append(Entry(
            source=SOURCE, key=f"{match.group(1)}:{language}", work_key=match.group(1), title=title, language=language,
            authors=clean_authors(names)[:6], year=year, released=released if len(released) == 10 else "",
            isbns=isbns[:10], pages=pages if isinstance(pages, int) and 0 < pages < 20000 else None,
            signals={"subjects": [s.lower() for s in _texts(doc.get("subject"), 40)], "first_year": first,
                     "work_title": work_title},
            authors_count=max(1, len(names)),
            weight=doc.get("edition_count") if isinstance(doc.get("edition_count"), int) else 0,
            cover_url=f"{COVERS}/b/id/{cover}-L.jpg" if isinstance(cover, int) and 0 < cover < 10**10 else "",
            person=" ".join(dict.fromkeys(people))[:120]))
    return found


def author_entries(db: Session, name: str, language: str) -> list[Entry]:
    """The works of an author with their titles in ``language``, by the author's name. Kept for a day."""
    marc = MARC_OF.get(language, "")
    author = clean(name, 100)
    if not author or not marc:
        return []

    def fetch() -> list[Entry]:
        params = {"author": author, "lang": marc, "fields": AUTHOR_FIELDS, "limit": AUTHOR_WORKS}
        answer = http.request(db, SOURCE, f"{BASE}/search.json?{urlencode(params)}", limit=AUTHOR_ANSWER_MAX)
        if answer.status_code == 404:
            return []
        return author_entries_of(http.json_of(SOURCE, answer), author, language)

    return authorq.kept(SOURCE, cache.make_key("author2", author, language), fetch)


def _year_text(value: Any) -> str:
    """The year of a date Open Library writes as text (``1983``, ``May 1983``), as four digits; empty for none."""
    if isinstance(value, list):
        # Open Library lists the dates of an edition (``["1983"]``).
        value = next((item for item in value if isinstance(item, str)), "")
    found = re.search(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)", value if isinstance(value, str) else "")
    return found.group(1) if found else ""


@dataclass
class AuthorHit:
    """An author as Open Library's search for authors knows them."""

    name: str
    key: str
    alternate_names: list[str] = field(default_factory=list)
    work_count: int = 0
    born: str = ""
    died: str = ""


_AUTHOR_KEY = re.compile(r"^OL\d{1,12}A$")


def parse_authors(data: Any) -> list[AuthorHit]:
    docs = data.get("docs") if isinstance(data, dict) else None
    found: list[AuthorHit] = []
    for doc in docs[:20] if isinstance(docs, list) else []:
        if not isinstance(doc, dict) or not _AUTHOR_KEY.match(str(doc.get("key") or "")):
            continue
        name = _text(doc.get("name"), 200)
        if not name:
            continue
        count = doc.get("work_count")
        found.append(AuthorHit(name=name, key=str(doc["key"]), alternate_names=_texts(doc.get("alternate_names"), 12),
                               work_count=count if isinstance(count, int) and count >= 0 else 0,
                               born=_text(doc.get("birth_date"), 40), died=_text(doc.get("death_date"), 40)))
    return found


def find_authors(db: Session, words: str, *, interactive: bool = False) -> list[AuthorHit]:
    """Authors whose name (or another name of theirs) fits the words, the ones with most works first. One request, kept
    for a day."""
    text = clean(words, 100)
    if len(text) < 2:
        return []
    key = cache.make_key("authors1", text)
    kept = cache.get(SOURCE, key)
    if kept is not cache.MISSING and isinstance(kept, list):
        return [AuthorHit(**{k: v for k, v in item.items() if k in AuthorHit.__dataclass_fields__})
                for item in kept if isinstance(item, dict) and isinstance(item.get("name"), str)]
    answer = http.request(db, SOURCE, f"{BASE}/search/authors.json?{urlencode({'q': text, 'limit': 10})}",
                          interactive=interactive)
    found = [] if answer.status_code == 404 else parse_authors(http.json_of(SOURCE, answer))
    found.sort(key=lambda hit: -hit.work_count)
    cache.put(SOURCE, key, [asdict(hit) for hit in found], hit=bool(found), days=authorq.DAYS)
    return found


# ---- A shelf of books by subject --------------------------------------------------------------------

#: What a shelf asks for: the fields of a work and, with ``lang``, the edition in the language of the market (its own
#: title and picture, the day it came out). ``publish_year`` holds the years of all editions: the first year of a work
#: counts only when they back it.
SHELF_FIELDS = ("key,title,author_name,first_publish_year,publish_year,language,cover_i,subject,"
                "number_of_pages_median,edition_count,editions,editions.key,editions.title,editions.language,"
                "editions.cover_i,editions.publish_date,editions.isbn")
#: Books a shelf asks for in one go, and how large that answer may be.
SHELF_BOOKS = 40
SHELF_ANSWER_MAX = 3 * 1024 * 1024
#: The subjects of a book kept for the judging of a shelf (a work lists up to several hundred).
SHELF_SUBJECTS = 250
#: The sorts of Open Library a shelf uses: the books most people want to read or have read, and the newest.
SHELF_SORTS = frozenset({"readinglog", "want_to_read", "already_read", "rating", "trending", "new"})
#: The words a subject query may be built from: a subject is a few words, never a query of its own.
_SUBJECT = re.compile(r"^[A-Za-z0-9 '&.,\-]{2,60}$")
#: A first year counts when another edition came out within this many years of it (one wrong record of an edition
#: makes a book of 1955 a book of 1777).
FIRST_YEAR_BACKED = 10


def subject_key(subject: str) -> str:
    """Open Library's key of a subject (``children's_fiction``): what ``subject_key:`` asks for, the whole subject and
    not a word inside it ("science" is not in "Political science")."""
    return re.sub(r"\s+", "_", subject.strip().casefold())


def subject_query(subjects: tuple[str, ...], awards: tuple[str, ...] = (), exclude: tuple[str, ...] = (),
                  years: tuple[int, int] | None = None) -> str:
    """The question for a shelf: one of the subjects (whole subjects, by their keys), and (when given) one of the
    awards (a phrase: the structured tags are checked on the answer), none of the excluded subjects, and a first year
    inside ``years``. All of it comes out of the table of shelves; ``ValueError`` for a text that is no subject or a
    year that is none (nothing typed by a person ever gets here)."""
    if not subjects or any(not _SUBJECT.match(item) for item in (*subjects, *awards, *exclude)):
        raise ValueError("subject")
    parts = ["(" + " OR ".join(f'subject_key:"{subject_key(item)}"' for item in subjects) + ")"]
    if awards:
        parts.append("(" + " OR ".join(f'subject:"{item}"' for item in awards) + ")")
    parts += [f'-subject_key:"{subject_key(item)}"' for item in exclude]
    if years is not None:
        low, high = years
        if not (1000 <= low <= high <= 2999):
            raise ValueError("years")
        parts.append(f"first_publish_year:[{low} TO {high}]")
    return " ".join(parts)


def backed_year(first: Any, years: Any) -> int | None:
    """The first year of a work when the years of its editions back it: another edition within ``FIRST_YEAR_BACKED``
    years, or it is the only one there is. None otherwise (unknown, rather than wrong)."""
    if not isinstance(first, int) or isinstance(first, bool) or not 1000 < first < 3000:
        return None
    known = sorted(y for y in years if isinstance(y, int) and not isinstance(y, bool) and 1000 < y < 3000) \
        if isinstance(years, list) else []
    if not known:
        return first
    if first < known[0] - 1:
        return None
    near = [y for y in known if first <= y <= first + FIRST_YEAR_BACKED]
    return first if len(near) >= 2 or len(known) == 1 else None


def parse_shelf(data: Any, language: str) -> list[Candidate]:
    """The books of a shelf answer, each with the title, the picture and the day of the edition in ``language`` (the
    work's own when it is known in that language alone). A book with no title in that language is left out. The key of
    a book not in English carries its language, so that the titles of two markets are two rows. ``subjects`` holds the
    subjects the answer gave (up to ``SHELF_SUBJECTS``): the shelf judges by them, the row keeps ten."""
    docs = data.get("docs") if isinstance(data, dict) else None
    found: list[Candidate] = []
    for doc in docs[:SHELF_BOOKS * 2] if isinstance(docs, list) else []:
        if not isinstance(doc, dict):
            continue
        match = _WORK.match(str(doc.get("key") or ""))
        work_title = _text(doc.get("title"))
        names = clean_authors(_texts(doc.get("author_name"), 12))
        if not match or not work_title or not names:
            continue
        block = doc.get("editions")
        editions = block.get("docs") if isinstance(block, dict) else None
        title, isbns, released, cover = "", [], "", doc.get("cover_i")
        for edition in editions[:5] if isinstance(editions, list) else []:
            if not isinstance(edition, dict):
                continue
            codes = {language_code(v) for v in (edition.get("language") or []) if isinstance(v, str)}
            if language in codes and _text(edition.get("title")):
                title = _text(edition.get("title"))
                # Libraries end a title with a full stop ("Dracula."); an ellipsis stays.
                if title.endswith(".") and not title.endswith("..") and len(title) > 3:
                    title = title[:-1].rstrip()
                isbns = [c for v in (edition.get("isbn") or [])[:20] if isinstance(v, str | int) and (c := isbn13(v))]
                released = day_of(edition.get("publish_date")) or _year_text(edition.get("publish_date"))
                cover = edition.get("cover_i") if isinstance(edition.get("cover_i"), int) else cover
                break
        first = backed_year(doc.get("first_publish_year"), doc.get("publish_year"))
        if not title:
            continue
        pages = doc.get("number_of_pages_median")
        found.append(Candidate(
            source=SOURCE, key=match.group(1) if language == "en" else f"{match.group(1)}~{language}",
            work_key=match.group(1), title=title, authors=names[:6], languages=[language], isbns=isbns[:10],
            year=first, edition_year=int(released[:4]) if released else None,
            released=released if len(released) == 10 else "", original_title=work_title,
            subjects=_texts(doc.get("subject"), SHELF_SUBJECTS),
            pages=pages if isinstance(pages, int) and 0 < pages < 20000 else None,
            cover_url=f"{COVERS}/b/id/{cover}-L.jpg" if isinstance(cover, int) and 0 < cover < 10**10 else "",
            weight=doc.get("edition_count") if isinstance(doc.get("edition_count"), int) else 0))
    return found


def shelf(db: Session, query: str, *, language: str, sort: str, books: int = SHELF_BOOKS) -> list[Candidate]:
    """The books that fit ``query`` (made by ``subject_query`` out of the table of shelves), in the order of ``sort``,
    with their titles in ``language`` (two letters). One request."""
    marc = MARC_OF.get(language, "")
    if sort not in SHELF_SORTS or not marc:
        raise ValueError("sort or language")
    params: dict[str, str | int] = {"q": f"{query} language:{marc}", "sort": sort, "lang": marc,
                                    "fields": SHELF_FIELDS, "limit": min(books, SHELF_BOOKS)}
    answer = http.request(db, SOURCE, f"{BASE}/search.json?{urlencode(params)}", limit=SHELF_ANSWER_MAX)
    if answer.status_code == 404:
        return []
    return parse_shelf(http.json_of(SOURCE, answer), language)


# ---- Searching by the words a person typed (the catalogue search) ------------------------------------------------

#: The fields of a work for a search by words: the work, its numbers, and the edition in the language asked for.
TEXT_FIELDS = ("key,title,subtitle,author_name,author_key,first_publish_year,language,isbn,cover_i,subject,"
               "number_of_pages_median,edition_count,editions,editions.key,editions.title,editions.language,"
               "editions.isbn,editions.publish_date,editions.publisher")
#: Works a search by words reads (the first ones are what Open Library thinks fits best).
TEXT_WORKS = 20
#: The ISBNs of a work kept for holding the records of other sources to it (a work lists hundreds).
TEXT_ISBNS = 60


def text_entries_of(data: Any, language: str) -> list[Entry]:
    """The works of an answer to a search by words: for each work an entry with its own title (the title it has in the
    language it was written in) and, when the answer holds an edition in ``language``, an entry with that edition's
    title. Both carry the work: they are one work in two languages."""
    docs = data.get("docs") if isinstance(data, dict) else None
    found: list[Entry] = []
    for rank, doc in enumerate(docs[:TEXT_WORKS] if isinstance(docs, list) else []):
        if not isinstance(doc, dict):
            continue
        match = _WORK.match(str(doc.get("key") or ""))
        title = _text(doc.get("title"))
        names = clean_authors(_texts(doc.get("author_name"), 12))
        if not match or not title or not names:
            continue
        work = match.group(1)
        keys = doc.get("author_key") if isinstance(doc.get("author_key"), list) else []
        people = " ".join(str(key) for key in keys[:6] if _AUTHOR_KEY.match(str(key)))
        languages = list(dict.fromkeys(code for value in (doc.get("language") or [])[:60] if isinstance(value, str)
                                       and (code := language_code(value))))
        isbns = list(dict.fromkeys(code for value in (doc.get("isbn") or [])[:2000] if isinstance(value, str | int)
                                   and (code := isbn13(value))))[:TEXT_ISBNS]
        first = doc.get("first_publish_year")
        first = first if isinstance(first, int) and 1000 < first < 3000 else None
        pages = doc.get("number_of_pages_median")
        pages = pages if isinstance(pages, int) and 0 < pages < 20000 else None
        cover = doc.get("cover_i")
        cover_url = f"{COVERS}/b/id/{cover}-L.jpg" if isinstance(cover, int) and 0 < cover < 10**10 else ""
        weight = doc.get("edition_count") if isinstance(doc.get("edition_count"), int) else 0
        subjects = [s.lower() for s in _texts(doc.get("subject"), 40)]
        signals: dict[str, Any] = {"subjects": subjects, "first_year": first, "work_title": title, "rank": rank,
                                   "work_level": True, "languages": languages[:12]}
        # The work's own title: in its one language when it has only one, else in the language it was written in,
        # which the building works out (an empty language).
        found.append(Entry(
            source=SOURCE, key=work, work_key=work, title=title, language=languages[0] if len(languages) == 1 else "",
            authors=names[:6], year=first, isbns=isbns, pages=pages, signals=signals,
            authors_count=max(1, len(names)), weight=weight, cover_url=cover_url, person=people[:120]))
        block = doc.get("editions")
        editions = block.get("docs") if isinstance(block, dict) else None
        for edition in editions[:1] if isinstance(editions, list) else []:
            if not isinstance(edition, dict):
                continue
            codes = [code for value in (edition.get("language") or []) if isinstance(value, str)
                     and (code := language_code(value))]
            text = _text(edition.get("title"))
            if language not in codes or not text:
                continue
            released = day_of(edition.get("publish_date")) or _year_text(edition.get("publish_date"))
            numbers = [c for v in (edition.get("isbn") or [])[:20] if isinstance(v, str | int) and (c := isbn13(v))]
            ident = _EDITION.match(str(edition.get("key") or ""))
            publishers = _texts(edition.get("publisher"), 1)
            found.append(Entry(
                source=SOURCE, key=f"{work}:{ident.group(1) if ident else language}", work_key=work, title=text,
                language=language, authors=names[:6], year=int(released[:4]) if released else None,
                released=released if len(released) == 10 else "", isbns=numbers[:10], pages=pages,
                signals={"subjects": subjects, "first_year": first, "work_title": title, "rank": rank,
                         "publisher": publishers[0] if publishers else ""},
                authors_count=max(1, len(names)), weight=weight, cover_url=cover_url, person=people[:120]))
    return found


def text_entries(db: Session, text: str, language: str, *, interactive: bool = False) -> list[Entry]:
    """The works that fit the words a person typed (Open Library's own search over titles, authors and subjects), with
    the edition in ``language`` where there is one. Kept for a day."""
    words = clean(text, 200)
    if len(words) < 2:
        return []
    marc = MARC_OF.get(language, "")

    def fetch() -> list[Entry]:
        params: dict[str, str | int] = {"q": words, "fields": TEXT_FIELDS, "limit": TEXT_WORKS}
        if marc:
            params["lang"] = marc
        answer = http.request(db, SOURCE, f"{BASE}/search.json?{urlencode(params)}", interactive=interactive)
        if answer.status_code == 404:
            return []
        return text_entries_of(http.json_of(SOURCE, answer), language)

    return authorq.kept(SOURCE, cache.make_key("text4", words.casefold(), language), fetch)
