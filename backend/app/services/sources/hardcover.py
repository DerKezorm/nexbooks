"""Hardcover (GraphQL): works, editions in many languages, series with the place in them, trends.

Optional, with **a token of the operator's own** (the instance's, never one that comes with nexbooks): a read-only token
from hardcover.app/account/api, kept sealed. Hardcover's own sign-in by device code is not built in: nexbooks has no
description of it to build on, so the token is how an instance is connected (the interface says so). The API is a
beta: a
token can expire, and a request that is refused for it makes the source stop (``http``) and the interface offer
"connect again".

Only questions are asked (``query``), never a ``mutation``; the text of a question is fixed here and what a person
typed is only ever a *variable*. The free plan allows 60 requests a minute, a burst of 10 and 5,000 a day
(``config.LIMITS``). Several searches go in one request (``prefetch``: GraphQL aliases).

The data is noisy (editions filed under the wrong book, an author's name as a title): the editions of a book are
*counted* per language, not trusted one by one.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from ..readers.names import authors_compatible, clean_authors, compare_key, core_title, titles_alike
from ..readers.text import clean, html_to_text, isbn13
from . import authorq, cache, config, http
from .model import Candidate, Entry, Query, day_of, language_code

SOURCE = "hardcover"
URL = "https://api.hardcover.app/v1/graphql"
PER_PAGE = 10
#: Searches in one request.
BUNDLE = 5
MAX_EDITIONS = 300

_SEARCH = 'search(query:$q%s, query_type:"Book", per_page:' + str(PER_PAGE) + "){ results }"
_EDITIONS = ("editions(where:{book_id:{_eq:$id%s}}, limit:" + str(MAX_EDITIONS) + "){ title isbn_13 asin pages "
             "edition_format language{language} reading_format{format} }")
#: What kind of book it is, asked in the same request as its editions: fiction or non-fiction (``literary_type_id``)
#: and the
#: genres readers gave it.
_KIND = "books(where:{id:{_eq:$id%s}}, limit:1){ literary_type_id cached_tags }"
#: Hardcover's ``literary_type_id``: 1 is fiction, 2 non-fiction.
FICTION, NON_FICTION = 1, 2


def _text(value: Any, limit: int = 300) -> str:
    return clean(value, limit) if isinstance(value, str) else ""


#: What Hardcover's GraphQL server (Hasura) says in the ``code`` of an error when it refuses the token.
TOKEN_ERROR_CODES = frozenset({"invalid-jwt", "jwt-invalid-claims", "invalid-headers", "unauthenticated",
                               "unauthorized"})


def token_refused(errors: list[Any]) -> bool:
    """Whether the errors of an answer say, unmistakably, that the token is no good (expired, not valid, not known).
    A message that merely contains the word "token" or "expired" says nothing of the kind (a field called ``token``)."""
    for item in errors:
        if not isinstance(item, dict):
            continue
        extensions = item.get("extensions")
        code = extensions.get("code") if isinstance(extensions, dict) else None
        if isinstance(code, str) and code.lower() in TOKEN_ERROR_CODES:
            return True
        message = str(item.get("message", "")).lower()
        if "unable to verify token" in message or "could not verify jwt" in message or (
                "jwt" in message and any(word in message for word in ("expired", "invalid", "malformed"))):
            return True
    return False


def _post(db: Session, query: str, variables: dict[str, Any], interactive: bool) -> dict[str, Any]:
    token = config.hardcover_token(db)
    answer = http.request(db, SOURCE, URL, method="POST", headers={"Authorization": f"Bearer {token}"},
                          json_body={"query": query, "variables": variables}, interactive=interactive)
    data = http.json_of(SOURCE, answer)
    if not isinstance(data, dict):
        http.throttle.fail(SOURCE, "bad_answer")
        raise http.SourceError("bad_answer")
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        if token_refused(errors):
            http.throttle.set_state(SOURCE, "token_invalid")
            http.throttle.fail(SOURCE, "token_invalid")
            raise http.SourceError("token_invalid")
        http.throttle.fail(SOURCE, "bad_answer")
        raise http.SourceError("bad_answer")
    body = data.get("data")
    return body if isinstance(body, dict) else {}


def _hits(result: Any) -> list[dict[str, Any]]:
    """The documents of one search: ``results.hits[].document``."""
    hits = result.get("results", {}).get("hits") if isinstance(result, dict) and isinstance(
        result.get("results"), dict) else None
    if not isinstance(hits, list):
        return []
    return [hit["document"] for hit in hits[:PER_PAGE]
            if isinstance(hit, dict) and isinstance(hit.get("document"), dict)]


def candidate_of(doc: dict[str, Any]) -> Candidate | None:
    ident = doc.get("id")
    title = _text(doc.get("title"))
    if not title or not isinstance(ident, int | str) or not str(ident).isdigit():
        return None
    featured = doc.get("featured_series")
    series, position = "", None
    if isinstance(featured, dict):
        inner = featured.get("series")
        series = _text(inner.get("name"), 200) if isinstance(inner, dict) else ""
        raw = featured.get("position")
        position = float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None
    names = doc.get("author_names")
    year = doc.get("release_year")
    image = doc.get("image")
    picture = image.get("url") if isinstance(image, dict) else None
    users = doc.get("users_count")
    isbns = list(dict.fromkeys(code for code in (isbn13(v) for v in (doc.get("isbns") or [])[:200]
                                                 if isinstance(v, str | int)) if code))
    return Candidate(
        source=SOURCE, key=str(ident), work_key=str(ident), title=title, subtitle=_text(doc.get("subtitle")),
        authors=clean_authors([_text(n, 200) for n in names[:12] if _text(n, 200)]) if isinstance(names, list) else [],
        year=year if isinstance(year, int) and 1000 < year < 3000 else None, series=series, series_pos=position,
        description=html_to_text(doc.get("description")) if isinstance(doc.get("description"), str) else "",
        isbns=isbns, cover_url=picture if isinstance(picture, str) and http.host_allowed(SOURCE, picture) else "",
        weight=users if isinstance(users, int) and not isinstance(users, bool) else 0,
        subjects=[_text(g, 100) for g in (doc.get("genres") or [])[:20] if _text(g, 100)]
        if isinstance(doc.get("genres"), list) else [])


def parse_search(result: Any) -> list[Candidate]:
    return [c for c in (candidate_of(doc) for doc in _hits(result)) if c is not None]


def enrich(candidate: Candidate, editions: Any) -> None:
    """Puts what the editions of a book say onto it: the languages it exists in, its ISBNs and ASIN, and the titles it
    has
    in each language (counted: the title most editions of a language carry is the book's title there)."""
    if not isinstance(editions, list):
        return
    languages: Counter[str] = Counter()
    titles: dict[str, Counter[str]] = {}
    isbns = list(candidate.isbns)
    for edition in editions[:MAX_EDITIONS]:
        if not isinstance(edition, dict):
            continue
        language = edition.get("language")
        code = language_code(language.get("language")) if isinstance(language, dict) else ""
        if code:
            languages[code] += 1
            title = _text(edition.get("title"), 300)
            if title:
                titles.setdefault(code, Counter())[title] += 1
        isbn = isbn13(edition.get("isbn_13"))
        if isbn and isbn not in isbns:
            isbns.append(isbn)
        asin = _text(edition.get("asin"), 20)
        if asin and not candidate.asin:
            candidate.asin = asin
        pages = edition.get("pages")
        if isinstance(pages, int) and 0 < pages < 20000 and not candidate.pages:
            candidate.pages = pages
    candidate.languages = [code for code, _n in languages.most_common()]
    candidate.isbns = isbns[:60]
    candidate.titles = {code: [t for t, _n in counts.most_common(5)] for code, counts in titles.items()}


def apply_kind(candidate: Candidate, rows: Any) -> None:
    """Puts what the kind of the book says onto the candidate as subjects the genre is voted from: non-fiction is
    "Sachbuch", fiction is "Fiction" (a novel, which says little), and the genres readers gave it."""
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if row is None:
        return
    words = list(candidate.subjects)
    kind = row.get("literary_type_id")
    if kind == NON_FICTION:
        words.append("Sachbuch")
    elif kind == FICTION:
        words.append("Fiction")
    words += _tags(row.get("cached_tags"))
    candidate.subjects = list(dict.fromkeys(words))[:30]


def _key(query: Query) -> str:
    author = clean(query.authors[0], 100) if query.authors else ""
    return cache.make_key("search", query.isbn, core_title(clean(query.title, 200), author), author)


def _words(query: Query) -> str:
    """What is typed into the search: the title and the first author, or the ISBN."""
    if query.isbn:
        return query.isbn
    author = clean(query.authors[0], 100) if query.authors else ""
    return f"{core_title(clean(query.title, 200), author)} {author}".strip()


def prefetch(db: Session, queries: list[Query]) -> None:
    """Asks the searches that are not kept yet, up to ``BUNDLE`` in one request, and keeps the answers. A search that
    fails is simply not kept: ``search`` meets the same wall and says so."""
    open_ = {}
    for query in queries:
        key = _key(query)
        if _words(query) and cache.get(SOURCE, key) is cache.MISSING:
            open_.setdefault(key, query)
    items = list(open_.items())
    for start in range(0, len(items), BUNDLE):
        chunk = items[start : start + BUNDLE]
        declared = ",".join(f"$q{i}:String!" for i in range(len(chunk)))
        body = " ".join(f"s{i}: " + (_SEARCH % i) for i in range(len(chunk)))
        variables = {f"q{i}": _words(q) for i, (_k, q) in enumerate(chunk)}
        data = _post(db, f"query({declared}){{ {body} }}", variables, False)
        for i, (key, _query) in enumerate(chunk):
            found = parse_search(data.get(f"s{i}"))
            cache.put(SOURCE, key, [c.to_json() for c in found], hit=bool(found))


def search(db: Session, query: Query, *, interactive: bool = False) -> list[Candidate]:
    """The books that fit the question, the best-known one of the author enriched with what its editions say."""
    key = _key(query)
    kept = cache.get(SOURCE, key)
    if kept is cache.MISSING:
        if not _words(query):
            return []
        data = _post(db, f"query($q0:String!){{ s0: {_SEARCH % 0} }}", {"q0": _words(query)}, interactive)
        found = parse_search(data.get("s0"))
        cache.put(SOURCE, key, [c.to_json() for c in found], hit=bool(found))
    else:
        found = [Candidate.from_json(item) for item in kept]
    if not found:
        return []
    try:
        if mark_pen_names(db, query, found, interactive=interactive):
            cache.put(SOURCE, key, [c.to_json() for c in found], hit=True)
    except http.SourceError:
        # The pen name is a nicety on top of the search: what the search found stands.
        pass
    # The editions of the one book that is most likely the one asked for: by the author when one is known.
    fitting = [c for c in found if not query.authors or any(authors_compatible(a, b) for a in query.authors
                                                            for b in c.authors)]
    top = max(fitting or found, key=lambda c: c.weight)
    if not top.languages:
        eds_key = cache.make_key("editions", top.work_key)
        kept = cache.get(SOURCE, eds_key)
        if kept is cache.MISSING:
            data = _post(db, f"query($id0:Int!){{ e0: {_EDITIONS % 0} k0: {_KIND % 0} }}", {"id0": int(top.work_key)},
                         interactive)
            raw, rows = data.get("e0"), data.get("k0")
            editions = [e for e in raw if isinstance(e, dict)][:MAX_EDITIONS] if isinstance(raw, list) else []
            kind = [row for row in rows if isinstance(row, dict)][:1] if isinstance(rows, list) else []
            cache.put(SOURCE, eds_key, {"editions": editions, "kind": kind}, hit=bool(editions))
        elif isinstance(kept, dict):
            editions, kind = kept.get("editions"), kept.get("kind")
        else:
            # (A list: kept by an older version, which did not ask for the kind of the book.)
            editions, kind = kept, []
        enrich(top, editions)
        apply_kind(top, kind)
    return found


#: The other names an author has at Hardcover (the text is fixed; the name is only ever a variable).
_ALTERNATES = "query($name:String!){ authors(where:{name:{_eq:$name}}, limit:3){ name alternate_names } }"
MAX_ALTERNATES = 40


def author_alternates(db: Session, name: str, *, interactive: bool = False) -> list[str]:
    """The other names Hardcover knows an author by (its pen names and spellings); empty when it knows none, and empty
    when it knows **more than one author of that name**: two records of one name are two people (or one noisy record
    beside the real one), and whose other names are whose cannot be told; they are never put together. Kept like
    every answer."""
    key = cache.make_key("alternates2", name)
    kept = cache.get(SOURCE, key)
    if kept is cache.MISSING:
        data = _post(db, _ALTERNATES, {"name": clean(name, 200)}, interactive)
        found: list[str] = []
        rows = data.get("authors")
        rows = rows if isinstance(rows, list) else []
        for row in rows[:1] if len(rows) == 1 else []:
            names = row.get("alternate_names") if isinstance(row, dict) else None
            if isinstance(names, list):
                found += [_text(item, 200) for item in names[:MAX_ALTERNATES] if _text(item, 200)]
        found = list(dict.fromkeys(found))
        cache.put(SOURCE, key, found, hit=bool(found))
        return found
    return [item for item in kept if isinstance(item, str)] if isinstance(kept, list) else []


def mark_pen_names(db: Session, query: Query, found: list[Candidate], *, interactive: bool = False) -> bool:
    """When the books found fit the title but none of them the author, and Hardcover names the file's author as
    another name of the author of such a book, the book carries that name (``author_aliases``): the proof that the
    file's author is the author of the book under a pen name. Only the best-known books with a title that fits are
    asked about. Returns whether a candidate was marked."""
    if not query.authors or not found or any(c.author_aliases for c in found):
        return False
    if any(authors_compatible(a, b) for a in query.authors for c in found for b in c.authors):
        return False
    wanted = core_title(clean(query.title, 200), query.authors[0])
    changed = False
    for candidate in sorted((c for c in found if titles_alike(wanted, c.title)), key=lambda c: -c.weight)[:3]:
        for name in candidate.authors[:2]:
            hits = [alternate for alternate in author_alternates(db, name, interactive=interactive)
                    if any(authors_compatible(alternate, mine) for mine in query.authors)]
            if hits:
                candidate.author_aliases = hits[:3]
                changed = True
                break
    return changed


def check_token(db: Session) -> bool:
    """Whether the token is accepted: one small question. ``False`` when Hardcover refuses it; a source that cannot be
    reached raises."""
    try:
        _post(db, "query{ me { id } }", {}, True)
    except http.SourceError as exc:
        if exc.code in ("token_invalid",):
            return False
        raise
    return True


# ---- Lists for Discover: what is read now, the books of a series, the books of an author --------------------------

#: What is asked of a book in these questions (the text is fixed; a name or a number is only ever a variable).
_BOOK_FIELDS = ("id title release_date release_year users_count cached_tags image{url} contributions{author{name}} "
                "book_series{position series{name}}")
_TRENDING = "query{ books_trending(from:\"%s\", to:\"%s\", limit:%d, offset:0){ ids } }"
_BOOKS = "query($ids:[Int!]){ books(where:{id:{_in:$ids}}){ " + _BOOK_FIELDS + " } }"
_SERIES_OF = "query($id:Int!){ books(where:{id:{_eq:$id}}){ book_series{ position series{ id name } } } }"
# Hardcover lists in a series every edition and every translation as a book of its own (``canonical_id`` names the book
# it is a copy of) and the boxed sets (``compilation``). The question leaves them out, and ``series_books`` leaves out
# what is left of them all the same.
_SERIES = ("query($id:Int!){ book_series(where:{series_id:{_eq:$id}, book:{canonical_id:{_is_null:true}, "
           "compilation:{_eq:false}}}, order_by:{position:asc}, limit:80){ position "
           "book{ " + _BOOK_FIELDS + " canonical_id compilation } } }")
_AUTHOR = ("query($name:String!){ books(where:{contributions:{author:{name:{_eq:$name}}}, "
           "canonical_id:{_is_null:true}, compilation:{_eq:false}}, order_by:{users_count:desc}, limit:24){ "
           + _BOOK_FIELDS + " } }")
TRENDING_DAYS = 30
TRENDING_SIZE = 40


def _tags(value: Any) -> list[str]:
    """The genres in a book's ``cached_tags`` (``{"Genre": [{"tag": "Fantasy"}]}``)."""
    genres = value.get("Genre") if isinstance(value, dict) else None
    return [_text(g.get("tag"), 100) for g in genres[:10] if isinstance(g, dict) and _text(g.get("tag"), 100)
            ] if isinstance(genres, list) else []


def book_of(book: Any) -> Candidate | None:
    """A book of the ``books`` table as a candidate (``None`` for anything that is no book with a number and title)."""
    if not isinstance(book, dict):
        return None
    ident, title = book.get("id"), _text(book.get("title"))
    if not isinstance(ident, int) or isinstance(ident, bool) or ident <= 0 or not title:
        return None
    authors = []
    for credit in (book.get("contributions") or [])[:12] if isinstance(book.get("contributions"), list) else []:
        author = credit.get("author") if isinstance(credit, dict) else None
        name = _text(author.get("name"), 200) if isinstance(author, dict) else ""
        if name and name not in authors:
            authors.append(name)
    series, position = "", None
    links = book.get("book_series")
    first = links[0] if isinstance(links, list) and links and isinstance(links[0], dict) else None
    if first is not None:
        inner = first.get("series")
        series = _text(inner.get("name"), 200) if isinstance(inner, dict) else ""
        raw = first.get("position")
        position = float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None
    image = book.get("image")
    picture = image.get("url") if isinstance(image, dict) else None
    users = book.get("users_count")
    year = book.get("release_year")
    released = day_of(book.get("release_date"))
    return Candidate(
        source=SOURCE, key=str(ident), work_key=str(ident), title=title, authors=authors, series=series,
        series_pos=position, released=released,
        year=year if isinstance(year, int) and 1000 < year < 3000 else (int(released[:4]) if released else None),
        cover_url=picture if isinstance(picture, str) and http.host_allowed(SOURCE, picture) else "",
        weight=users if isinstance(users, int) and not isinstance(users, bool) else 0,
        subjects=_tags(book.get("cached_tags")))


def _books(data: dict[str, Any], key: str = "books") -> list[Candidate]:
    rows = data.get(key)
    return [c for c in (book_of(row) for row in rows[:100]) if c is not None] if isinstance(rows, list) else []


def trending(db: Session, today: Any) -> list[Candidate]:
    """What is read now, most first: the books Hardcover counts as trending over the last 30 days. Two questions: the
    numbers of the books, then the books. ``today`` is a ``date`` (the caller's clock, so that a test can set it)."""
    begin = today.fromordinal(today.toordinal() - TRENDING_DAYS)
    data = _post(db, _TRENDING % (begin.isoformat(), today.isoformat(), TRENDING_SIZE), {}, False)
    trend = data.get("books_trending")
    ids = trend.get("ids") if isinstance(trend, dict) else None
    numbers = ids if isinstance(ids, list) else []
    ids = [i for i in numbers[:TRENDING_SIZE] if isinstance(i, int) and not isinstance(i, bool) and i > 0]
    if not ids:
        return []
    found = {c.key: c for c in _books(_post(db, _BOOKS, {"ids": ids}, False))}
    return [found[str(i)] for i in ids if str(i) in found]


def series_of(db: Session, book: int) -> tuple[int, str] | None:
    """The series a book of Hardcover is in, ``(number, name)``, or ``None``."""
    data = _post(db, _SERIES_OF, {"id": book}, False)
    rows = data.get("books")
    links = rows[0].get("book_series") if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    for link in links[:5] if isinstance(links, list) else []:
        inner = link.get("series") if isinstance(link, dict) else None
        if isinstance(inner, dict) and isinstance(inner.get("id"), int) and _text(inner.get("name"), 200):
            return inner["id"], _text(inner.get("name"), 200)
    return None


#: A title that names a pack of books, not a book (a box, a collection, "Books 1-3").
_PACK = re.compile(
    r"\b(?:box(?:ed)?[ -]?set|omnibus|bundle|collection|\d+[- ]books?|"
    r"(?:books?|b[uü]e?cher|b[aä]e?nde?) \d+\s*(?:-|to|bis)\s*\d+|"
    r"gesamtausgabe|sammelband|schuber|coffret|cofanetto)\b", re.IGNORECASE)
#: A place that is not a whole number from 1 on (0, 0.5, 2.5) is a volume of the series only when readers say so: at
#: least this many, and at least this part of the readers of the best known volume. The copies of a book that Hardcover
#: files under a place of their own have none, or a handful.
SIDE_VOLUME_MIN = 5
SIDE_VOLUME_SHARE = 0.02


def volumes_of(found: list[Candidate]) -> list[Candidate]:
    """The volumes of a series out of the rows Hardcover gives for it: **one for each place, the one most readers have
    on their shelves** (the others are the same book as an edition or a translation), none that is a pack of books,
    and a side volume (a place that is not a whole number from 1 on) only with readers of its own. In the order of the
    places."""
    best: dict[float, Candidate] = {}
    for candidate in found:
        position = candidate.series_pos
        if position is None or not 0 <= position < 10_000 or _PACK.search(candidate.title):
            continue
        kept = best.get(position)
        if kept is None or candidate.weight > kept.weight:
            best[position] = candidate
    if not best:
        return []
    floor = max(SIDE_VOLUME_MIN, SIDE_VOLUME_SHARE * max(c.weight for c in best.values()))
    return [c for place, c in sorted(best.items()) if (place >= 1 and place == int(place)) or c.weight >= floor]


def series_books(db: Session, series: int) -> list[Candidate]:
    """The volumes of a series with their place in it, in order of the place (see ``volumes_of``)."""
    data = _post(db, _SERIES, {"id": series}, False)
    rows = data.get("book_series")
    found: list[Candidate] = []
    for link in rows[:80] if isinstance(rows, list) else []:
        if not isinstance(link, dict):
            continue
        book = link.get("book")
        if isinstance(book, dict) and (book.get("canonical_id") is not None or book.get("compilation") is True):
            continue
        candidate = book_of(book)
        position = link.get("position")
        if candidate is None or not isinstance(position, int | float) or isinstance(position, bool):
            continue
        candidate.series_pos = float(position)
        found.append(candidate)
    return volumes_of(found)


def author_books(db: Session, name: str) -> list[Candidate]:
    """The best-known books of an author, by the exact name Hardcover has."""
    return _books(_post(db, _AUTHOR, {"name": clean(name, 200)}, False))


# ---- The works of an author for the catalogue ---------------------------------------------------------

#: An author by name: the documents of the search say the number of the author, how many books they have and their
#: other names.
_AUTHOR_SEARCH = 'query($q:String!){ search(query:$q, query_type:"Author", per_page:5){ results } }'
#: The books an author wrote, a hundred at a time (Hardcover gives no more in one answer), each with its editions in the
#: languages asked for (the text is fixed; the number, the languages and the offset are variables).
_CONTRIBUTIONS = (
    'query($id:Int!,$l:[String!],$o:Int!){ contributions(where:{author_id:{_eq:$id}, contributable_type:{_eq:"Book"}},'
    " limit:100, offset:$o, order_by:{id:asc}){ contribution book { id title release_date release_year compilation"
    " pages literary_type_id book_category_id users_count cached_tags image{url} canonical_id"
    " book_series{position series{name}} editions(where:{language:{language:{_in:$l}}}, limit:20)"
    "{ title isbn_13 release_date language{language} } } } }")
#: Pages of a hundred books at most.
CONTRIBUTION_PAGES = 12


def language_names(code: str) -> list[str]:
    """The names Hardcover gives a language (``German``; ``Spanish; Castilian`` beside ``Spanish``)."""
    from .model import LANGUAGE_NAMES

    names = [" ".join(part.capitalize() for part in name.split(" ")) for name, value in LANGUAGE_NAMES.items()
             if value == code]
    return names + {"no": ["Norwegian Bokmål", "Norwegian Nynorsk"]}.get(code, [])


def _author_id(db: Session, name: str) -> tuple[int, list[str], int] | None:
    """The number of the author of that name with the most books, their other names, and how many other authors of
    that name have books at Hardcover (a name two people share); None when Hardcover knows no author of exactly that
    name."""
    data = _post(db, _AUTHOR_SEARCH, {"q": clean(name, 200)}, False)
    hits = _hits(data.get("search"))
    fitting = [doc for doc in hits if authors_compatible(name, _text(doc.get("name"), 200))
               and str(doc.get("id") or "").isdigit()]
    if not fitting:
        return None

    def books(doc: dict[str, Any]) -> int:
        count = doc.get("books_count")
        return count if isinstance(count, int) and not isinstance(count, bool) else 0

    best = max(fitting, key=books)
    others = sum(1 for doc in fitting if doc is not best and books(doc) > 0)
    names = best.get("alternate_names")
    return (int(best["id"]), [_text(n, 200) for n in names[:40] if _text(n, 200)] if isinstance(names, list) else [],
            others)


def contribution_entries(rows: Any, languages: list[str], original: str = "") -> list[Entry]:
    """The books of the contributions as entries: one for each language asked for in which the book has editions (the
    title most of those editions carry); and the book's own title as English when no English edition says its language
    (English editions at Hardcover often say none), as long as the author writes in English (``original``, when
    known) and the title is not the one of another language."""
    found: list[Entry] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("contribution") not in (None, "Author"):
            continue
        book = row.get("book")
        if not isinstance(book, dict) or book.get("canonical_id") is not None:
            continue
        ident, title = book.get("id"), _text(book.get("title"))
        if not isinstance(ident, int) or isinstance(ident, bool) or not title:
            continue
        by_language: dict[str, Counter[str]] = {}
        isbns: dict[str, list[str]] = {}
        days: dict[str, list[str]] = {}
        for edition in book.get("editions") or [] if isinstance(book.get("editions"), list) else []:
            if not isinstance(edition, dict):
                continue
            language = edition.get("language")
            code = language_code(language.get("language")) if isinstance(language, dict) else ""
            text = _text(edition.get("title"))
            if code in languages and text:
                by_language.setdefault(code, Counter())[text] += 1
                if (isbn := isbn13(edition.get("isbn_13"))):
                    isbns.setdefault(code, []).append(isbn)
                if (day := day_of(edition.get("release_date"))):
                    days.setdefault(code, []).append(day)
        fallback = False
        others = {compare_key(text) for counts in by_language.values() for text in counts}
        if ("en" in languages and "en" not in by_language and original in ("", "en")
                and compare_key(title) not in others):
            by_language["en"] = Counter({title: 1})
            fallback = True
        series, position = "", None
        links = book.get("book_series")
        first = links[0] if isinstance(links, list) and links and isinstance(links[0], dict) else None
        if first is not None:
            inner = first.get("series")
            series = _text(inner.get("name"), 200) if isinstance(inner, dict) else ""
            raw = first.get("position")
            position = float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None
        year = book.get("release_year")
        image = book.get("image")
        picture = image.get("url") if isinstance(image, dict) else None
        pages = book.get("pages")
        users = book.get("users_count")
        book_day = day_of(book.get("release_date"))
        book_year = year if isinstance(year, int) and 1000 < year < 3000 else None
        for code, counts in by_language.items():
            # The year and day of the book are the first edition's; in a language whose editions say their own day,
            # that day (a translation comes later).
            own = fallback and code == "en"
            day = min(days[code]) if days.get(code) else (book_day if own else "")
            # (The latest edition of the language as well: a paperback or a new translation that is announced.)
            last = max(days[code]) if days.get(code) else ""
            found.append(Entry(
                source=SOURCE, key=f"{ident}:{code}", work_key=str(ident), title=counts.most_common(1)[0][0],
                language=code, year=int(day[:4]) if day else (book_year if own else None),
                released=day,
                isbns=list(dict.fromkeys(isbns.get(code, [])))[:10],
                pages=pages if isinstance(pages, int) and 0 < pages < 20000 else None, series=series,
                series_pos=position, compilation=book.get("compilation") is True,
                signals={"lit": book.get("literary_type_id") if isinstance(book.get("literary_type_id"), int) else None,
                         "cat": book.get("book_category_id") if isinstance(book.get("book_category_id"), int) else None,
                         "genres": [g.lower() for g in _tags(book.get("cached_tags"))],
                         **({"last_day": last} if last and last != day else {})},
                weight=users if isinstance(users, int) and not isinstance(users, bool) else 0,
                cover_url=picture if isinstance(picture, str) and http.host_allowed(SOURCE, picture) else ""))
    return found


def author_catalog(db: Session, name: str, languages: list[str],
                   original: str = "") -> tuple[list[Entry], list[str], int]:
    """The books of an author with their titles in ``languages``, the other names Hardcover knows the author by
    (``original``: the language the author writes in, when known), and how many other authors of the name it knows.
    Kept for a day."""
    wanted = sorted({code for code in languages if code})
    key = cache.make_key("author1", name, ",".join(wanted), original)
    kept = cache.get(SOURCE, key)
    if isinstance(kept, dict):
        entries = []
        for item in kept.get("entries") or []:
            try:
                entries.append(Entry.from_json(item))
            except (ValueError, TypeError):
                continue
        known_names = [n for n in kept.get("names") or [] if isinstance(n, str)]
        others = kept.get("others")
        if not isinstance(others, int) or isinstance(others, bool):
            # (Kept by an older version, without the other authors of the name: those are asked once; when Hardcover
            # does not answer now, the books still count, the other authors stay unknown.)
            try:
                again = _author_id(db, name)
            except (http.SourceOff, http.SourceError):
                return entries, known_names, 0
            others = again[2] if again is not None else 0
            cache.put(SOURCE, key, {**kept, "others": others}, hit=bool(entries), days=authorq.DAYS)
        return entries, known_names, others
    found = _author_id(db, name)
    entries: list[Entry] = []
    names: list[str] = []
    others = 0
    if found is not None:
        ident, names, others = found
        variables = [n for code in wanted for n in language_names(code)]
        for page in range(CONTRIBUTION_PAGES):
            data = _post(db, _CONTRIBUTIONS, {"id": ident, "l": variables, "o": page * 100}, False)
            rows = data.get("contributions")
            entries += contribution_entries(rows, wanted, original)
            if not isinstance(rows, list) or len(rows) < 100:
                break
    cache.put(SOURCE, key, {"entries": [e.to_json() for e in entries], "names": names, "others": others},
              hit=bool(entries), days=authorq.DAYS)
    return entries, names, others
