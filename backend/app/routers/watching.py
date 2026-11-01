"""Watching an author: a person switches "watch" on for an author, and nexbooks wishes the author's new
books for them as they are announced and come out (``services/oeuvre/watch.py`` says how).

**The person's own.** A watch is read and changed by the person who made it, and by nobody else (the operator
included). Whom a person watches is no secret of the catalogue (it is public data), but it is theirs. The wishes a
watch makes are ordinary wishes, seen by everybody who may ask for books in their library; without the right to
acquire the books go onto the person's wish list. Each answer says which: ``made.wished`` and ``made.listed``.

Nothing here asks a source while the caller waits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import Account, DbSession, Reader
from ..errors import error
from ..models import Account as AccountRow
from ..models import AuthorWatch, CatalogPerson
from ..services import authors, brakes
from ..services.libraries import member_library_ids
from ..services.oeuvre import info as info_service
from ..services.oeuvre import store
from ..services.oeuvre import view as service
from ..services.oeuvre import watch as watching
from ..services.procure import search

router = APIRouter(prefix="/api/v1", tags=["authors"])

Kind = Literal["ebook", "audio", "both"]
FormKind = Literal["ebook", "audio"]


class WatchIn(BaseModel):
    kind: Kind = Field(default="both", description="What is wished: the e-book, the audio book, or both.")
    language: str | None = Field(
        default=None, max_length=8, pattern=r"^([a-z]{2,8})?$",
        description="The one language books are wished in: one of the languages books are fetched in (settings, "
                    "``languages`` of the answer). Left out or empty: the first of them, in the operator's order. "
                    "``422 language_not_accepted`` for any other.")
    more: bool = Field(default=False, description="Also non-fiction, children's books, collections, biographies and "
                                                  "poems (by default novels and stories only).")


class AnnouncedOut(BaseModel):
    key: str = Field(description="The work's key in the catalogue.")
    title: str
    language: str
    date: str = Field(description="``YYYY-MM-DD`` when the source says the day, ``YYYY`` when it says the year only.")
    state: Literal["wished", "listed", "year", "held", "covered", "open"] = Field(
        description="``wished`` (a wish for fetching it, also one the caller made), ``listed`` (on the wish list, "
                    "``why`` says why when the caller may ask for books), ``year`` (announced for a year without a "
                    "day: wished when the day is known), ``held`` (not wished, the code in ``why``), ``covered`` "
                    "(somebody else wished it for the library it would go into for the caller: wished for the caller "
                    "when that wish goes) or ``open`` (announced, nothing done yet: the look for it is still to come).")
    kinds: list[FormKind] = Field(description="The kinds wished.")
    why: str = Field(default="", description="Why it is listed or held: ``adaptation``, ``companion``, ``edition``, "
                                             "``play``, ``part``, ``box``, ``reissue``, ``translation``, "
                                             "``library``, "
                                             "``language:<code>`` (announced in that language only), ``own:<code>`` "
                                             "(the caller has it in that language), or the code of the refused wish.")


class NotYetOut(BaseModel):
    key: str
    #: The title in the language it is announced in.
    title: str
    #: The language of the watch it is not announced in yet, and the one it is announced in.
    language: str
    announced_in: str
    #: It is on the caller's wish list (put there by the watch) until it is announced in ``language``.
    listed: bool


class MadeOut(BaseModel):
    wished: int
    listed: int
    #: Why the look stopped before the end: ``no_indexers``, ``share`` (the part of a day's wishes watching may
    #: use), ``list_full``, ``many``, ``off`` (the main switch of the automatic work is off: nothing was wished);
    #: empty when it did not.
    stopped: str


class WatchOut(BaseModel):
    token: str
    name: str
    #: The author of the caller's libraries (their page is ``/people/{id}``), when the caller sees a book of theirs.
    person_id: int | None
    photo_url: str | None
    genre: str
    kind: Kind
    #: The language the caller chose, or empty for the first of ``languages``.
    language: str
    #: The languages books are fetched in for this author, the first one first (the choice of the watch), the one it
    #: wishes in now (``target``, empty when no language is fetched), and whether the language the caller chose is no
    #: longer one of them (the watch then wishes in the first).
    languages: list[str]
    target: str
    language_off: bool
    more: bool
    #: Neither Audible nor Hardcover may be asked: the catalogue knows few books to come.
    sources_few: bool
    #: The main switch of the automatic work is off: the watch wishes nothing until it is on again.
    paused: bool
    #: ``waiting`` while the catalogue of the author is not built (the first look comes when it is); else
    #: ``watching``.
    state: Literal["waiting", "watching"]
    #: The caller may ask for books (the right to acquire in a library): their books are wished, else listed.
    can_acquire: bool
    #: When the watch was switched on, and the build of the catalogue it looked at last.
    created_at: datetime
    checked_at: datetime | None
    #: Everything the watch wished and listed so far.
    wished: int
    listed: int
    #: The books announced in the watch's language, soonest first (only on the page of the author), the next one of
    #: them, and the books announced only in another of the languages books are fetched in.
    announced: list[AnnouncedOut]
    next: AnnouncedOut | None
    not_yet: list[NotYetOut]
    #: What the look this answer follows made (switching on or changing the watch); null for any other answer.
    made: MadeOut | None = None


class WatchesOut(BaseModel):
    items: list[WatchOut]
    limit: int


def _line(line: watching.Line) -> AnnouncedOut:
    kinds = [k for k in line.kinds if k in ("ebook", "audio")]
    return AnnouncedOut(key=line.key, title=line.title, language=line.language, date=line.day, state=line.state,  # type: ignore[arg-type]
                        kinds=kinds, why=line.why)  # type: ignore[arg-type]


def _photo(db: DbSession, uid: int, row: CatalogPerson, person: int | None) -> str | None:
    if person is not None:
        photo = authors.photo_of(db, uid, person)
        return authors.photo_url(person, photo[0]) if photo else None
    info = row.info if isinstance(row.info, dict) else {}
    photo_name = info_service.photo_of(row) if info.get("state") == "sure" else None
    return f"/api/v1/catalog/authors/{row.token}/photo?v={photo_name[:12]}" if photo_name else None


def _person_of(db: DbSession, uid: int, row: CatalogPerson) -> int | None:
    people, _pens = service.people_of(db, row, member_library_ids(uid))
    return next((ident for ident in people if authors.can_see(db, uid, ident)), None)


def build(db: DbSession, account: AccountRow, row: CatalogPerson, watch: AuthorWatch, *,
          shown: service.AuthorView | None = None, made: watching.Outcome | None = None,
          soon: watching.Line | None = None, genre: str = "", full: bool = True) -> WatchOut:
    """The watch as the caller sees it. ``full``: with the books announced and what became of them (the page of the
    author); otherwise only the next one (the strip of the authors one watches)."""
    summary = watching.Summary()
    if full:
        summary = watching.summarize(db, watch, account, shown or service.view(db, account.id, row))
        soon = summary.announced[0] if summary.announced else None
    person = _person_of(db, account.id, row)
    languages = watching.fetch_languages(db, row.orig_language)
    return WatchOut(
        token=row.token, name=row.name, person_id=person, photo_url=_photo(db, account.id, row, person), genre=genre,
        kind=watch.kind, language=watching.chosen_language(watch), languages=languages,  # type: ignore[arg-type]
        target=watching.target_of(watch, languages), language_off=watching.language_off(db, watch, row),
        more=watch.more, sources_few=watching.few_sources(db), paused=watching.paused(db),
        state="watching" if row.built_at is not None else "waiting",
        can_acquire=bool(search.targets(db, account.id)),
        created_at=watch.created_at, checked_at=watch.evaluated_for if watch.primed_at is not None else None,
        wished=summary.wished if full else 0, listed=summary.listed if full else 0,
        announced=[_line(line) for line in summary.announced],
        next=_line(soon) if soon is not None else None,
        not_yet=[NotYetOut(key=item.key, title=item.title, language=item.language, announced_in=item.announced_in,
                           listed=item.listed) for item in summary.not_yet],
        made=None if made is None else MadeOut(wished=made.wished, listed=made.listed, stopped=made.stopped))


def for_page(db: DbSession, account: AccountRow, row: CatalogPerson, shown: service.AuthorView,
             genre: str) -> WatchOut | None:
    """The caller's watch of the author for the page of the author's works, or null when they do not watch."""
    watch = watching.find(db, account.id, row)
    return None if watch is None else build(db, account, row, watch, shown=shown, genre=genre)


@router.get("/watching", response_model=WatchesOut,
            summary="The authors the caller watches, with the next book announced of each")
def list_watches(account: Reader, db: DbSession) -> WatchesOut:
    brakes.take("discover", account.id)
    rows = list(db.execute(select(AuthorWatch, CatalogPerson).join(
        CatalogPerson, CatalogPerson.id == AuthorWatch.catalog_person_id).where(
            AuthorWatch.user_id == account.id).order_by(AuthorWatch.created_at.desc(), AuthorWatch.id.desc())).all())
    soon = watching.next_announced(db, [watch for watch, _row in rows], {account.id: account})
    items = [build(db, account, row, watch, soon=soon.get(watch.id), full=False) for watch, row in rows]
    return WatchesOut(items=items, limit=watching.MAX_WATCHES)


@router.put("/watching/authors/{token}", response_model=WatchOut,
            summary="Watch an author: new books are wished as they are announced and come out (or listed)",
            responses={404: {"description": "not_found"}, 409: {"description": "too_many_watches"},
                       422: {"description": "language_not_accepted (no language books are fetched in), "
                                             "invalid_input"}})
def watch_author(token: str, payload: WatchIn, account: Account, db: DbSession) -> WatchOut:
    """Switches the watch on, or changes it (the kind, the language, ``more``). When the catalogue of the author is
    built, the books announced are wished (or listed) at once, nothing that is out already; when it is not, it is
    built and the first look follows. With the main switch of the automatic work off the watch is saved and nothing is
    wished (``made.stopped`` is ``off``, ``paused`` is true); the first look comes when the switch is on again."""
    brakes.take("wish", account.id)
    row = store.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    language = payload.language or ""
    if language and language not in watching.fetch_languages(db, row.orig_language):
        raise error("language_not_accepted", "nexbooks does not fetch books in this language.", 422,
                    field="language", language=language)
    try:
        watch, _new = watching.save(db, account, row, kind=payload.kind, language=language, more=payload.more)
    except watching.TooMany:
        raise error("too_many_watches", "You watch as many authors as you may.", 409,
                    limit=watching.MAX_WATCHES) from None
    made = watching.switch_on(db, account, row, watch)
    db.refresh(watch)
    return build(db, account, row, watch, made=made)


@router.delete("/watching/authors/{token}", status_code=204,
               summary="Stop watching an author; the wishes made so far stay, or go with it",
               responses={404: {"description": "not_found"}})
def unwatch_author(token: str, account: Account, db: DbSession,
                   wishes: Annotated[Literal["keep", "remove"], Query(
                       description="``keep`` (the default): the wishes and wish list entries the watch made stay. "
                                   "``remove``: those nothing has started on go too.")] = "keep") -> Response:
    brakes.take("wish", account.id)
    row = store.by_token(db, token)
    if row is None:
        raise error("not_found", "Not found.", 404)
    watching.remove(db, account, row, remove_wishes=wishes == "remove")
    return Response(status_code=204)


__all__ = ["WatchOut", "for_page", "router"]
