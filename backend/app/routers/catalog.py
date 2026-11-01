"""The catalogue of an author's works: every book of an author the sources know, set against what the caller
has, and the authors among the hits of the search.

**Public data, a private view.** What the sources say about an author is the same for everybody. What the caller *has*
("im Regal", the number of works on the shelf) comes from the libraries the caller is a member of and from nothing
else (``services/oeuvre/view.py``): a book in a library the caller is not in never shows as theirs. The page of an
author of the libraries is reached by the number of the person (a person the caller sees no book of answers 404, as
everywhere); any other author by a ``token`` nobody can count up to, handed out by a search or the page of a book.

Nothing here asks a source while the caller waits: a catalogue that is old or was never built is queued for the
background (``state`` says ``building``) and the page asks again a little later.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, select

from .. import clock
from ..deps import Account, BrowserAccount, DbSession, Reader
from ..errors import error
from ..models import MANAGE, OPERATOR, CatalogMove, CatalogPerson, CatalogWork, LibraryMember, Person
from ..services import aliases, authors, brakes, photos, settings_service
from ..services.discover import pics
from ..services.discover import view as discover_view
from ..services.libraries import member_library_ids
from ..services.oeuvre import info as info_service
from ..services.oeuvre import store
from ..services.oeuvre import view as service
from ..services.oeuvre.view import upcoming
from ..services.readers.names import display_name
from ..services.readers.text import clean
from .watching import WatchOut, for_page
from .works import Bio, BioText, PhotoCredit, WorkBrief, brief_of

router = APIRouter(prefix="/api/v1", tags=["authors"])

KindName = Literal["roman", "erz", "bio", "sach", "lyrik", "kinder", "box", "unsure"]
MovableKind = Literal["roman", "erz", "bio", "sach", "lyrik", "kinder", "box"]
#: The covers a look at a page asks for at most (the others come at the next look; a cover is kept a day).


class CatalogEdition(BaseModel):
    """The work in one language."""

    language: str
    title: str
    year: int | None
    #: ``YYYY-MM-DD`` when a source says the day; and whether that day (or the year) is after today.
    released: str
    upcoming: bool
    #: Whether a book (``ebook``) or a recording (``audio``) is known in this language.
    ebook: bool
    audio: bool


class CatalogWorkOut(BaseModel):
    #: The work's key in the catalogue (``own-<id>`` for a book of the caller's the catalogue does not know).
    key: str
    kind: KindName
    #: Why it is unsure: ``comic``, ``anthology``, ``forms``, ``short``, ``sample``, ``about`` (a book about the author
    #: or a book of theirs), ``namesake`` (somebody else of the name may have written it); empty otherwise.
    why: str
    #: A person moved it to its kind.
    moved: bool
    #: The other name it came out under (empty: the author's own).
    pen: str
    series: str
    series_pos: float | None
    year: int | None
    orig_language: str
    editions: list[CatalogEdition]
    #: The sources that know it (``library`` for a book only the caller's shelf knows).
    sources: list[str]
    #: The page of the book of the sources (``/api/v1/discover/books/{token}``): the wish, the wish list and the page go
    #: through it. Null for a book only the caller's shelf knows.
    token: str | None
    #: The book on the caller's shelf this is (only books of the caller's libraries), and what of it they have.
    work: WorkBrief | None
    #: A book on the caller's shelf that may be this work (the same place in its series, or a title that is only the
    #: series and a number): "Alle fehlenden beschaffen" leaves the work out, a person looks first.
    maybe_have: WorkBrief | None = None
    have_ebook: bool
    have_audio: bool
    have_languages: list[str]
    #: On the caller's wish list.
    wished: bool
    color: str
    cover_url: str | None
    pages: int | None
    minutes: int | None


class SourceCount(BaseModel):
    source: str
    works: int


class AuthorCatalogOut(BaseModel):
    token: str
    name: str
    #: The author of the caller's libraries (their page is ``/people/{id}``); null when the caller sees no book of
    #: theirs.
    person_id: int | None
    #: The other names the author writes under, as the sources know them.
    aliases: list[str]
    born: str
    died: str
    orig_language: str
    #: The languages the works came out in, most works first.
    languages: list[str]
    #: ``ready``; ``building`` (never built, the background is on it); ``no_source`` (no source may be asked);
    #: ``main_off`` (the operator's main switch); ``failed`` (no source answered).
    state: Literal["ready", "building", "no_source", "main_off", "failed"]
    #: A newer build is on its way (the page asks again later).
    refreshing: bool
    built_at: datetime | None
    sources: list[SourceCount]
    #: The sources that were away at the last build.
    away: list[str]
    sure: int
    unsure: int
    #: How far the catalogue can be trusted: ``low`` (few sources know the author), ``mid`` (some works are unsure),
    #: ``high``.
    level: Literal["low", "mid", "high"]
    works_total: int
    on_shelf: int
    soon: int
    first_year: int | None
    last_year: int | None
    #: Whether the caller may move a work the sources could not place (``unsure``) to another kind: the operator and
    #: whoever manages a library.
    can_move: bool
    #: The genre most of the books are (for the circle with the initials).
    genre: str
    #: The sources know other people of this name: their books were left out, and a work no source's number of the
    #: author proves stands under "Nicht sicher zugeordnet" (``why`` is ``namesake``).
    common_name: bool = False
    #: For an author nobody of the libraries is: a photo and a short text from Wikidata, only when two works of the
    #: catalogue are works of the item there (``services/oeuvre/info.py``). Null otherwise; the page of an author of
    #: the libraries has its own (``/people/{id}``).
    photo_url: str | None = None
    credit: PhotoCredit | None = None
    bio: Bio | None = None
    #: The caller's watch of this author, or null when they do not watch them.
    watch: WatchOut | None = None
    works: list[CatalogWorkOut]


class MoveIn(BaseModel):
    kind: MovableKind


class AuthorHit(BaseModel):
    name: str
    #: The page: the person of the caller's libraries (``person_id``), else the catalogue (``token``).
    person_id: int | None
    token: str | None
    #: The other name the words found the author by (a pen name), empty when it was the name.
    alias: str
    #: The works the catalogue knows (null: not built yet), how many of them the caller has, how many are to come.
    works: int | None
    on_shelf: int
    soon: int
    photo_url: str | None
    genre: str


class AuthorHits(BaseModel):
    items: list[AuthorHit]


# ---- Putting the page together -------------------------------------------------------------------------------------


def _today() -> str:
    return clock.now().date().isoformat()


def may_move(db: DbSession, account: object) -> bool:
    """The operator, or a person who manages a library: a move holds for everybody (and for what is fetched for
    them), so only whoever looks after what the libraries hold may make one."""
    if getattr(account, "role", "") == OPERATOR:
        return True
    return db.scalar(select(exists().where(LibraryMember.user_id == getattr(account, "id", 0),
                                           LibraryMember.right == MANAGE))) is True


def _edition_list(titles: object, today: str) -> list[CatalogEdition]:
    out = []
    for language, item in (titles.items() if isinstance(titles, dict) else []):
        if not isinstance(item, dict) or not item.get("title"):
            continue
        out.append(CatalogEdition(language=str(language)[:8], title=str(item["title"])[:300],
                                  year=item.get("year") if isinstance(item.get("year"), int) else None,
                                  released=str(item.get("released") or "")[:10], upcoming=upcoming(item, today),
                                  ebook=item.get("ebook") is True, audio=item.get("audio") is True))
    return out


def _work_out(card: service.Card, today: str) -> CatalogWorkOut:
    own = card.own
    brief = brief_of(own.view) if own is not None else None
    have_languages = sorted({code for codes in (own.kinds.values() if own else []) for code in codes if code})
    work = card.work
    if work is None:
        assert own is not None
        view_ = own.view
        language = next(iter(have_languages), "")
        edition = CatalogEdition(language=language, title=view_.title, year=view_.year, released="", upcoming=False,
                                 ebook="ebook" in own.kinds, audio="audio" in own.kinds)
        return CatalogWorkOut(
            key=f"own-{view_.work.id}", kind=card.kind, why="", moved=False, pen=own.pen, series=view_.series_name,  # type: ignore[arg-type]
            series_pos=view_.series_pos, year=view_.year, orig_language=view_.original_language or language,
            editions=[edition], sources=["library"], token=None, work=brief, have_ebook="ebook" in own.kinds,
            have_audio="audio" in own.kinds, have_languages=have_languages, wished=False,
            color=brief.color if brief else "", cover_url=brief.cover_url if brief else None, pages=view_.pages or None,
            minutes=None)
    book = card.book
    cover = brief.cover_url if brief and brief.cover_url else (
        pics.link_of(book) if book is not None else None)
    return CatalogWorkOut(
        key=work.key, kind=card.kind, why=card.why, moved=card.moved, pen=work.pen,  # type: ignore[arg-type]
        series=work.series, series_pos=work.series_pos, year=work.year, orig_language=work.orig_language,
        editions=_edition_list(work.titles, today), sources=[s for s in work.sources or [] if isinstance(s, str)],
        token=book.token if book is not None else None, work=brief,
        maybe_have=brief_of(card.maybe.view) if card.maybe is not None else None,
        have_ebook=bool(own and "ebook" in own.kinds), have_audio=bool(own and "audio" in own.kinds),
        have_languages=have_languages, wished=card.wished,
        color=brief.color if brief else (discover_view.color_of(book) if book is not None else ""),
        cover_url=cover, pages=work.pages, minutes=work.minutes)


def _info_of(row: CatalogPerson, language: str) -> tuple[str | None, PhotoCredit | None, Bio | None]:
    """The photo, its credit and the short text of an author nobody of the libraries is, in the caller's language
    (English when there is none in it)."""
    info = row.info if isinstance(row.info, dict) else {}
    if row.person_id is not None or info.get("state") != "sure":
        return None, None, None
    photo = info_service.photo_of(row)
    raw = info.get("credit") if isinstance(info.get("credit"), dict) else {}
    credit = None if not photo else PhotoCredit(
        source="wikidata", author=str(raw.get("author") or ""), license=str(raw.get("license") or ""),
        license_url=str(raw.get("license_url") or ""), page_url=str(raw.get("page") or ""))
    bios = info.get("bios") if isinstance(info.get("bios"), dict) else {}
    texts = bios.get("texts") if isinstance(bios.get("texts"), dict) else {}
    life = bios.get("life") if isinstance(bios.get("life"), dict) else {}
    base = (language or "en").lower().split("-")[0]
    chosen = next(((key, texts[key]) for key in (base, "en") if isinstance(texts.get(key), dict)), None)
    born, died = life.get("born"), life.get("died")
    bio = Bio(texts=[BioText(text=str(chosen[1].get("text") or "")[:500], language=chosen[0], source="wikidata",
                             url=str(chosen[1].get("url") or "")[:300])] if chosen else [],
              born=born if isinstance(born, int) else None, died=died if isinstance(died, int) else None)
    url = f"/api/v1/catalog/authors/{row.token}/photo?v={photo[:12]}" if photo else None
    return url, credit, bio if bio.texts or bio.born or bio.died else None


def _state(db: DbSession, row: CatalogPerson, waiting: bool) -> str:
    if row.built_at is not None:
        return "ready"
    if waiting:
        return "building"
    if row.state == "failed":
        return "failed"
    return "no_source" if settings_service.get(db, "sources_enabled") else "main_off"


def _level(total: int, unsure: int, row: CatalogPerson) -> str:
    sources = row.sources if isinstance(row.sources, dict) else {}
    answered = sources.get("answered") if isinstance(sources.get("answered"), dict) else {}
    origin = answered.get(row.orig_language) or []
    if total <= 3 or len({s for names in answered.values() for s in names}) <= 1 or len(origin) <= 1:
        return "low"
    return "mid" if unsure else "high"


def page_of(db: DbSession, account: object, row: CatalogPerson) -> AuthorCatalogOut:
    uid = getattr(account, "id", 0)
    waiting = store.request(db, row)
    shown = service.view(db, uid, row)
    today = _today()
    works = [_work_out(card, today) for card in shown.cards]
    sources = row.sources if isinstance(row.sources, dict) else {}
    by = sources.get("by") if isinstance(sources.get("by"), dict) else {}
    catalogued = [w for w in works if w.token is not None]
    unsure = sum(1 for w in catalogued if w.kind == "unsure")
    years = [w.year for w in works if w.year]
    languages: Counter[str] = Counter(e.language for w in works for e in w.editions if e.language)
    genres: Counter[str] = Counter(w.work.genre for w in works if w.work is not None and w.work.genre)
    if not genres and shown.cards:
        genres = Counter(card.book.genre for card in shown.cards if card.book is not None and card.book.genre)
    person = next((ident for ident in shown.people if authors.can_see(db, uid, ident)), None)
    photo_url, credit, bio = _info_of(row, getattr(account, "language", "") or "en")
    namesakes = sources.get("namesakes")
    return AuthorCatalogOut(
        token=row.token, name=row.name, person_id=person,
        common_name=isinstance(namesakes, int) and namesakes > 0, photo_url=photo_url, credit=credit, bio=bio,
        aliases=[a for a in row.aliases or [] if isinstance(a, str)][:6], born=row.born, died=row.died,
        orig_language=row.orig_language, languages=[code for code, _n in languages.most_common()],
        state=_state(db, row, waiting), refreshing=waiting and row.built_at is not None,  # type: ignore[arg-type]
        built_at=row.built_at,
        sources=sorted((SourceCount(source=name, works=int(value.get("works") or 0)) for name, value in by.items()
                        if isinstance(value, dict) and value.get("works")), key=lambda s: -s.works),
        away=[a for a in sources.get("away") or [] if isinstance(a, str)],
        sure=len(catalogued) - unsure, unsure=unsure, level=_level(len(catalogued), unsure, row),  # type: ignore[arg-type]
        works_total=len(works), on_shelf=sum(1 for w in works if w.work is not None),
        soon=sum(1 for w in works if any(e.upcoming for e in w.editions)),
        first_year=min(years) if years else None, last_year=max(years) if years else None,
        can_move=may_move(db, account), genre=genres.most_common(1)[0][0] if genres else "", works=works,
        watch=for_page(db, account, row, shown, genres.most_common(1)[0][0] if genres else ""))


# ---- The routes ------------------------------------------------------------------------------------------------


@router.get("/people/{person_id}/catalog", response_model=AuthorCatalogOut,
            summary="Every work of an author of the caller's libraries the sources know, set against what they have")
def person_catalog(person_id: int, account: Reader, db: DbSession) -> AuthorCatalogOut:
    brakes.take("discover", account.id)
    person = db.get(Person, person_id)
    if person is None or not authors.can_see(db, account.id, person_id):
        raise error("not_found", "Not found.", 404)
    row = store.for_person(db, person)
    db.commit()
    return page_of(db, account, row)


@router.get("/catalog/authors/{token}", response_model=AuthorCatalogOut,
            summary="Every work of an author the sources know, set against what the caller has")
def author_catalog(token: str, account: Reader, db: DbSession) -> AuthorCatalogOut:
    brakes.take("discover", account.id)
    row = store.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    return page_of(db, account, row)


@router.get("/catalog/authors/{token}/photo", summary="The photo of an author of the catalogue, as a WebP picture",
            response_model=None, responses={200: {"content": {"image/webp": {}}}, 304: {"description": "Not changed"}})
def author_photo(token: str, request: Request, account: Reader, db: DbSession,
                 v: Annotated[str | None, Query(max_length=40)] = None) -> Response:
    row = store.by_token(db, token)
    photo = info_service.photo_of(row) if row is not None and row.person_id is None else None
    path = photos.path_of(photo) if photo else None
    if photo is None or path is None:
        raise error("not_found", "Not found.", 404)
    etag = f'"{photo[:20]}"'
    stable = v is not None and photo.startswith(v) and len(v) >= 12
    headers = {"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable" if stable else "private, no-cache",
               "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)


def _move_target(db: DbSession, account: object, token: str, key: str) -> tuple[CatalogPerson, CatalogWork]:
    row = store.by_token(db, token)
    work = None if row is None else db.scalar(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id,
                                                                        CatalogWork.key == key))
    if row is None or work is None:
        raise error("not_found", "Not found.", 404)
    if not may_move(db, account):
        raise error("forbidden", "Only the operator or whoever manages a library can do that.", 403)
    return row, work


@router.put("/catalog/authors/{token}/works/{key}/kind", response_model=AuthorCatalogOut,
            summary="Move a work of the catalogue to another kind; it holds for everybody and every later build")
def move(token: str, key: Annotated[str, Path(max_length=64)], payload: MoveIn, account: BrowserAccount,
         db: DbSession) -> AuthorCatalogOut:
    row, work = _move_target(db, account, token, key)
    if work.kind != "unsure":
        # Only what the sources could not place is moved by hand: a work they placed is theirs to say.
        raise error("not_unsure", "Only a work the sources could not place for sure can be moved.", 409)
    found = db.scalar(select(CatalogMove).where(CatalogMove.catalog_person_id == row.id, CatalogMove.work_key == key))
    if found is None:
        db.add(CatalogMove(catalog_person_id=row.id, work_key=key, kind=payload.kind, user_id=account.id,
                           created_at=clock.now()))
    else:
        found.kind, found.user_id, found.created_at = payload.kind, account.id, clock.now()
    db.commit()
    return page_of(db, account, row)


@router.delete("/catalog/authors/{token}/works/{key}/kind", response_model=AuthorCatalogOut,
               summary="Take back a move: the work stands where the sources put it again")
def unmove(token: str, key: Annotated[str, Path(max_length=64)], account: BrowserAccount,
           db: DbSession) -> AuthorCatalogOut:
    row, _work = _move_target(db, account, token, key)
    found = db.scalar(select(CatalogMove).where(CatalogMove.catalog_person_id == row.id, CatalogMove.work_key == key))
    if found is not None:
        db.delete(found)
        db.commit()
    return page_of(db, account, row)


def counts_of(db: DbSession, uid: int, row: CatalogPerson | None) -> tuple[int | None, int, int]:
    """``(works, on the shelf, to come)`` of an author for the search and the card on a book's page: the works only
    once the catalogue was built."""
    if row is None:
        return None, 0, 0
    shown = service.view(db, uid, row)
    today = _today()
    works = None if row.built_at is None else len(shown.cards)
    soon = sum(1 for card in shown.cards if card.work is not None and any(
        upcoming(item, today) for item in (card.work.titles or {}).values() if isinstance(item, dict)))
    return works, sum(1 for card in shown.cards if card.own is not None), soon


def _photo(db: DbSession, uid: int, person_id: int) -> str | None:
    photo = authors.photo_of(db, uid, person_id)
    return authors.photo_url(person_id, photo[0]) if photo else None


@router.get("/search/people", response_model=AuthorHits,
            summary="The authors of the caller's libraries whose name (or pen name) holds the typed words")
def search_people(account: Reader, db: DbSession,
                  q: Annotated[str, Query(max_length=100, description="Words of the name")],
                  limit: Annotated[int, Query(ge=1, le=8)] = 4) -> AuthorHits:
    brakes.take("search", account.id)
    words = " ".join(q.casefold().split())
    if len(words) < 2:
        return AuthorHits(items=[])
    found, _total, _genres = authors.listing(db, account.id, sort="count", genre=None, query=words, page=1,
                                             per_page=50)
    names = aliases.aliases_of(db, [card.person.id for card in found], member_library_ids(account.id))

    def starts(text: str) -> bool:
        return any(part.startswith(words) for part in text.casefold().split()) or text.casefold().startswith(words)

    ranked = sorted(found, key=lambda card: (not starts(card.person.name), -card.works, card.person.sort_name))
    items = []
    for card in ranked[:limit]:
        alias = "" if words in card.person.name.casefold() else next(
            (name for name in names.get(card.person.id, []) if words in name.casefold()), "")
        row = db.scalar(select(CatalogPerson).where(CatalogPerson.key == card.person.key))
        works, on_shelf, soon = counts_of(db, account.id, row)
        items.append(AuthorHit(name=card.person.name, person_id=card.person.id, token=None, alias=alias, works=works,
                               on_shelf=max(on_shelf, card.works), soon=soon,
                               photo_url=authors.photo_url(card.person.id, card.photo) if card.photo else None,
                               genre=card.genre))
    return AuthorHits(items=items)


def foreign_hits(db: DbSession, uid: int, words: str, hits: list[tuple[str, str]], *,
                 make: bool = True) -> list[AuthorHit]:
    """The authors a source named for the words (``(name, the other name that fit)``), each with its catalogue and what
    the caller has of theirs. ``make``: a catalogue that is not there yet is made (the page of a book, which somebody
    opened); without it (the search, while somebody types) nothing is made: the hit has no token then, and the page
    is made when somebody opens it (``POST /catalog/authors``). Commits."""
    items: list[AuthorHit] = []
    seen: set[str] = set()
    libraries = member_library_ids(uid)
    for name, alias in hits:
        row = store.for_name(db, name, libraries, make=make)
        if row is None:
            text = display_name(clean(name, 200))
            key = aliases.key_of(text)
            if make or not text or len(key) < 2 or key in seen:
                continue
            seen.add(key)
            found = aliases.person_for(db, text, libraries)
            person = found.id if found is not None and authors.can_see(db, uid, found.id) else None
            items.append(AuthorHit(name=found.name if person is not None and found else text, person_id=person,
                                   token=None, alias=alias, works=None, on_shelf=0, soon=0,
                                   photo_url=_photo(db, uid, person) if person else None, genre=""))
            continue
        if row.key in seen:
            continue
        seen.add(row.key)
        db.commit()
        works, on_shelf, soon = counts_of(db, uid, row)
        people, _pens = service.people_of(db, row, libraries)
        person = next((ident for ident in people if authors.can_see(db, uid, ident)), None)
        items.append(AuthorHit(name=row.name, person_id=person, token=row.token, alias=alias, works=works,
                               on_shelf=on_shelf, soon=soon, photo_url=_photo(db, uid, person) if person else None,
                               genre=""))
    return items


class AuthorIn(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=200)]


class AuthorRef(BaseModel):
    #: The page of the author's works (``/catalog/authors/{token}``).
    token: str
    #: The author of the caller's libraries the name leads to (their page is ``/people/{id}``), when there is one.
    person_id: int | None


@router.post("/catalog/authors", response_model=AuthorRef,
             summary="The catalogue of an author by name, made when somebody opens it (the search names authors "
                     "without making one)")
def open_author(payload: AuthorIn, account: Account, db: DbSession) -> AuthorRef:
    brakes.take("discover", account.id)
    libraries = member_library_ids(account.id)
    row = store.for_name(db, payload.name, libraries)
    if row is None:
        raise error("not_found", "Not found.", 404)
    db.commit()
    people, _pens = service.people_of(db, row, libraries)
    person = next((ident for ident in people if authors.can_see(db, account.id, ident)), None)
    return AuthorRef(token=row.token, person_id=person)

