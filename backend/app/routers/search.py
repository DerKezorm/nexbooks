"""Searching: the books in the caller's libraries (open to a browser, a device and a program with a key), and the
catalogues of the sources (a signed-in browser only: each one costs the sources a request).

The library search answers with the works as the caller sees them (``services/works.py``), found by the words of the
editions the caller may see (``services/search.py``): what the caller may not see is not found, not counted, and does
not change how long the answer takes.

The search of the catalogues answers with one hit for each book (``services/booksearch``), named the way it is called in
the languages the operator fetches in. The sources are asked side by side; the answer comes after a moment with what is
there (``pending`` and a ``ticket``), and ``GET /search/catalog/{ticket}`` gives it again with what the slower ones
added. A page that gives up on a search says so (``DELETE``): nothing more is asked for it.

What a search costs the sources is kept to what is shown: the cover of a hit is fetched when the page shows it
(``.../covers/{key}``), and the page of a book of the sources is made when the person opens or acquires the hit
(``POST .../books``), not for every hit of every search.

A source that rests after errors, or did not answer, is named in the answer (``resting``): the hits are incomplete then,
and the page says so instead of "nothing found".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Path, Query, Response
from pydantic import BaseModel, Field

from ..deps import BrowserAccount, DbSession, Reader
from ..errors import error
from ..services import brakes, search
from ..services.booksearch import present as booksearch_present
from ..services.readers.text import fold
from .catalog import AuthorHit, foreign_hits
from .works import WorkBrief, brief_of

router = APIRouter(prefix="/api/v1", tags=["search"])


class SearchOut(BaseModel):
    query: str
    items: list[WorkBrief]


class CatalogIn(BaseModel):
    q: str = Field(min_length=1, max_length=search.MAX_QUERY)


SourceName = Literal["hardcover", "openlibrary", "audible", "dnb", "apple", "bnf", "libris", "kb", "nb"]
KNOWN_SOURCES = frozenset(get_args(SourceName))


class CatalogEditionOut(BaseModel):
    """One edition behind a hit, as a library or a shop lists it (shown under "show the editions")."""

    source: SourceName
    form: Literal["ebook", "audio"]
    language: str
    title: str
    subtitle: str
    year: int | None
    isbn: str
    asin: str
    publisher: str
    narrators: list[str]
    minutes: int | None
    #: A recording: ``unabridged``, ``abridged``, ``dramatized``, or empty when the source does not say.
    variant: str
    pages: int | None


class CatalogHitOut(BaseModel):
    """One book (a work): its e-books and recordings, its translations and its editions are one hit."""

    #: The name of the hit that stays the same while the sources answer.
    key: str
    #: The page of the book (``/api/v1/discover/books/{token}``) when it has one (null until the hit is opened:
    #: ``POST /search/catalog/{ticket}/books`` makes it), and the book on the caller's shelves it is, if it is one.
    token: str | None
    owned_work_id: int | None
    #: The title in the first of the languages the operator fetches in that the book has (``language``). A book in none
    #: of them has the title of its own language, ``allowed`` false and an empty ``language``.
    title: str
    language: str
    allowed: bool
    #: Every language the sources know the book in.
    languages: list[str]
    authors: list[str]
    #: The year it first came out.
    year: int | None
    series: str
    series_pos: float | None
    #: ``work`` a book; ``box`` a box or a collection; ``guide`` a study guide; ``companion`` another book about the
    #: book (a calendar, a book for fans, the music or the book of the film); ``drama`` a play made of it; ``part`` a
    #: part, one volume of a book printed in several, or a sample; ``comic`` a comic of the book.
    label: Literal["work", "box", "guide", "companion", "drama", "part", "comic"]
    #: ``match`` the book asked for, ``series`` another volume of its series, ``more`` the rest, ``related`` what is
    #: labelled, ``elsewhere`` a book in none of the languages fetched in.
    group: Literal["match", "series", "more", "related", "elsewhere"]
    #: The forms the sources know it in, in the languages fetched in.
    ebook: bool
    audio: bool
    #: Whether it can be acquired from here ("E-Book", "Hörbuch", "Beides"), and why not: ``not_a_book`` (a box, a
    #: play, a part, a study guide, a book about it, a comic: what is fetched would be the book, or nothing),
    #: ``volume_unknown`` (a book called like its series, whose volume nobody knows: the person picks it).
    procurable: bool
    why_not: Literal["", "not_a_book", "volume_unknown"]
    #: The cover, fetched by nexbooks from its source when it is shown; null when the sources have none.
    cover_url: str | None
    sources: list[SourceName]
    editions: list[CatalogEditionOut]


class RestingSourceOut(BaseModel):
    source: SourceName
    #: When it may be asked again (it rests after errors); null when it did not answer this search and does not rest.
    until: datetime | None


class CatalogOut(BaseModel):
    #: Whether a source may be asked at all (the operator switched one on, and it has its token).
    available: bool
    #: Every source that was asked failed; the interface says the catalogue is not answering.
    failed: bool
    #: Some sources are still being asked: ``GET /search/catalog/{ticket}`` gives the answer with what they add.
    pending: bool = False
    ticket: str | None = None
    items: list[CatalogHitOut]
    #: Authors the sources know by the typed words (their name or a pen name), with the page of each.
    people: list[AuthorHit] = []
    #: The sources that gave nothing for this search: they rest after errors, or did not answer it. The hits are then
    #: incomplete (and none of them is no proof that the book is unknown).
    resting: list[RestingSourceOut] = []


#: Authors named by one search at most.
MAX_AUTHORS = 4


def authors_named(words: str, result: search.CatalogResult) -> list[tuple[str, str]]:
    """The authors the typed words name, as ``(name, the other name that fit)``: the ones Open Library finds by their
    name or a pen name, and the authors of the hits whose name holds the words."""
    wanted = fold(" ".join(words.split()))
    if len(wanted) < 3:
        return []
    found: list[tuple[str, str]] = []
    for person in result.people:
        if wanted in fold(person.name):
            found.append((person.name, ""))
        else:
            alias = next((name for name in person.alternate_names if wanted in fold(name)), "")
            if alias:
                found.append((person.name, alias))
    for shown in result.hits:
        found += [(name, "") for name in shown.hit.authors if wanted in fold(name)]
    unique: dict[str, tuple[str, str]] = {}
    pens = {fold(alias) for _name, alias in found if alias}
    for name, alias in found:
        # A pen name a source credits a book to is the author it is a name of, not a person of its own.
        if fold(name) not in pens:
            unique.setdefault(fold(name), (name, alias))
    return list(unique.values())[:MAX_AUTHORS]


def _hit_out(shown: booksearch_present.Shown) -> CatalogHitOut:
    hit = shown.hit
    return CatalogHitOut(
        key=hit.key, token=shown.token, owned_work_id=shown.owned_work_id, title=hit.title, language=hit.language,
        allowed=hit.allowed, languages=hit.languages, authors=hit.authors[:3], year=hit.work.year,
        series=hit.work.series, series_pos=hit.work.series_pos, label=hit.label,  # type: ignore[arg-type]
        group=hit.group, ebook=hit.ebook, audio=hit.audio, procurable=shown.procurable,  # type: ignore[arg-type]
        why_not=shown.why_not, cover_url=shown.cover_url,  # type: ignore[arg-type]
        sources=[source for source in hit.work.sources if source in KNOWN_SOURCES],  # type: ignore[misc]
        editions=[CatalogEditionOut(**row) for row in hit.editions if row["source"] in KNOWN_SOURCES])


def _out(db: DbSession, uid: int, result: search.CatalogResult) -> CatalogOut:
    # (Typing makes no catalogue of an author: a hit gets its page when somebody opens it.)
    people = foreign_hits(db, uid, result.words, authors_named(result.words, result), make=False) \
        if result.words else []
    return CatalogOut(available=result.available, failed=result.failed, pending=result.pending,
                      ticket=result.ticket or None, people=people, items=[_hit_out(shown) for shown in result.hits],
                      resting=[RestingSourceOut(source=source, until=until)  # type: ignore[arg-type]
                               for source, until in result.resting.items() if source in KNOWN_SOURCES])


@router.get("/search", response_model=SearchOut, summary="Search the books of the caller's libraries")
def library_search(account: Reader, db: DbSession,
                   q: Annotated[str, Query(max_length=search.MAX_QUERY, description="Words; each is the start of a "
                                           "word of the title, the people, the series, the description or an ISBN")],
                   limit: Annotated[int, Query(ge=1, le=search.MAX_HITS)] = 8) -> SearchOut:
    brakes.take("search", account.id)
    found = search.find(db, account.id, q, limit)
    return SearchOut(query=q, items=[brief_of(view) for view in found])


@router.post("/search/catalog", response_model=CatalogOut,
             summary="Ask the catalogues of the sources the operator switched on for the typed words: one hit for "
                     "each book")
def catalog_search(payload: CatalogIn, account: BrowserAccount, db: DbSession) -> CatalogOut:
    brakes.take("catalog_search", account.id)
    return _out(db, account.id, search.catalog(db, account.id, payload.q))


Ticket = Annotated[str, Path(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
#: The key of a hit (``booksearch.Hit.key``).
KEY_PATTERN = r"^[0-9a-f]{20}(~[0-9]{1,2})?$"
HitKey = Annotated[str, Path(min_length=20, max_length=23, pattern=KEY_PATTERN)]


class CatalogBookIn(BaseModel):
    #: The ``key`` of a hit of this search.
    key: str = Field(min_length=20, max_length=23, pattern=KEY_PATTERN)


class CatalogBookOut(BaseModel):
    #: The page of the book (``/api/v1/discover/books/{token}``).
    token: str


@router.get("/search/catalog/{ticket}", response_model=CatalogOut,
            summary="The same search of the catalogues again, with what the sources answered since")
def catalog_search_again(ticket: Ticket, account: BrowserAccount, db: DbSession) -> CatalogOut:
    # (Asked again a few times for each search: the brake of the library search, not the one of a new question.)
    brakes.take("search", account.id)
    result = search.catalog_again(db, account.id, ticket)
    if result is None:
        raise error("not_found", "Not found.", 404)
    return _out(db, account.id, result)


@router.delete("/search/catalog/{ticket}", status_code=204, response_class=Response,
               summary="Stop asking the sources for this search (the page gave up on it)")
def catalog_search_stop(ticket: Ticket, account: BrowserAccount) -> Response:
    brakes.take("search", account.id)
    if not search.catalog_stop(account.id, ticket):
        raise error("not_found", "Not found.", 404)
    return Response(status_code=204)


@router.post("/search/catalog/{ticket}/books", response_model=CatalogBookOut,
             summary="The page of a hit of this search, made now (the person opens the hit or acquires it)")
def catalog_search_book(ticket: Ticket, payload: CatalogBookIn, account: BrowserAccount,
                        db: DbSession) -> CatalogBookOut:
    brakes.take("discover", account.id)
    hit = search.catalog_hit(account.id, ticket, payload.key)
    if hit is None:
        raise error("search_gone", "This search is over. Search again.", 404)
    return CatalogBookOut(token=booksearch_present.open_hit(db, hit).token)


@router.get("/search/catalog/{ticket}/covers/{key}", response_class=Response,
            responses={200: {"content": {"image/webp": {}}}},
            summary="The cover of a hit of this search, fetched from its source when the page shows it")
def catalog_search_cover(ticket: Ticket, key: HitKey, account: BrowserAccount, db: DbSession) -> Response:
    brakes.take("catalog_cover", account.id)
    hit = search.catalog_hit(account.id, ticket, key)
    made = booksearch_present.cover_of(db, hit) if hit is not None else None
    if made is None:
        raise error("not_found", "Not found.", 404)
    return Response(content=made[0], media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})
