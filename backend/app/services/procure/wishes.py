"""Wishes: what people ask nexbooks to get, and what becomes of it until a download takes over.

* **A wish** is one book in one kind (e-book or audio book) and one language, for a library the person may ask for books
  in (the right ``acquire``). The book is a work of a library or a book of the sources. There is one open wish per book,
  kind, language and library, whoever asked and whoever asks at the same moment (a unique index decides). Two libraries
  may each wish the same book, and neither sees the other's wish: a wish is a thing of its library. Each wish loads its
  own release (the book goes into each library), so two downloads of one book may run, one for each library.
* **The flow**: a new wish is searched at once (a search of the person's own, ``search``), and when the search is done
  the best release that passes is loaded, as long as the main switch of the automatic work is on. Without a release
  that passes the wish waits (``not_found``, with the reasons and the next try), for the day the book comes out
  (``waiting``), or for the day the limit of an indexer is over (``capped``). A wish for a format below the goal of a
  book that is there already is an ``upgrade``. With the main switch off a wish is ``idle`` until a person searches.
* **Searching** needs a free place of the person (three searches at a time): a wish that has to wait for one is
  ``searching`` without a search and starts when one is free (``kick``).
* **Who sees it**: a person with the right ``acquire`` in the library the wish goes into; a work only when they see it.
  Anyone of them may search again and remove it (the interface asks first). A wish of a person who has lost the right in
  its library rests (``idle``, ``no_right``) until they have it again or somebody takes the wish away.
* A download that failed in its program leads to the next fitting release (``after_failure``): at once the next
  release of the search the failed one came from, a new search when none is left; a download a person cancelled leaves
  the wish ``idle`` or, with "block and go on", searching. **At most ``replace_max`` downloads of a wish in 24 hours**,
  whatever started them (the first search, the replacement, the planner coming back, the newest entries): a wish that
  has used them waits (``replace_limit``) until the first of them is a day old (``window_end``).
* **The main switch off** stops every way out by itself: a wish that would be searched rests (``idle``,
  ``main_off``) instead (``begin``), and nothing is loaded. "Jetzt suchen" of a person is the one search that goes on.
* **The automatic work** comes back to a wish after the time the operator chose
  (``run_due``): one wish at a time, person by person in turn (``in_turn``), on the day the book comes out, and after
  the day of an indexer's limit. A wish that found nothing for a week is looked for once a day, after a month once a
  week (``retry_for``); the searches of the planner are background and keep to their part of the limits of the day. A
  wish for a format below the goal goes on until the goal is reached (``complete``, ``end_reached_upgrades``). The
  wish is taken by one claim (``claim``) so that the planner and a person who searches at the same moment do not search
  twice.
* Log lines carry numbers and codes, never titles.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import (
    ACTIVE_DOWNLOADS,
    Account,
    Download,
    Edition,
    ForeignBook,
    Indexer,
    Library,
    ProcureWish,
    ReleaseSearch,
    Work,
)
from .. import libraries, timezones, works
from ..discover import pics
from . import automation, blocklist, budget, choose, formats, judge, rations, search

logger = logging.getLogger("nexbooks.procure")

KINDS = ("ebook", "audio")
#: The states of the list "Gesucht": a wish that waits or is looked for. A wish that ``loading`` shows under "Läuft".
SEEKING = ("searching", "idle", "not_found", "waiting", "capped", "upgrade")
MAX_ITEMS = 100
#: How long the next try of a wish whose search broke off waits.
BROKEN_RETRY = timedelta(hours=1)
#: How long a wish may have been looked for in vain before it is looked for less often (once a day, once a week).
DAILY_AFTER = timedelta(days=7)
WEEKLY_AFTER = timedelta(days=30)
#: The window in which ``replace_max`` downloads of a wish are allowed.
WINDOW = timedelta(hours=24)
_lock = threading.RLock()


def fresh(db: Session, row: ProcureWish) -> bool:
    """Reads the wish again. False when it is gone (somebody took it away while this was busy with it)."""
    try:
        db.refresh(row)
    except InvalidRequestError:
        return False
    return True


def retry_for(row: ProcureWish, hours: int, moment: datetime) -> timedelta:
    """The time to the next search of a wish that found nothing: the operator's, and longer for a wish that has been
    looked for in vain for long (a week old: once a day at most, a month old: once a week), so that old wishes do not
    use up the limits of the indexers for ever. The newest entries (RSS) catch a release that comes out meanwhile."""
    base = timedelta(hours=hours)
    age = moment - row.created_at
    if age >= WEEKLY_AFTER:
        return max(base, timedelta(days=7))
    if age >= DAILY_AFTER:
        return max(base, timedelta(hours=24))
    return base


def window_end(db: Session, row: ProcureWish, moment: datetime, limit: int) -> datetime | None:
    """When the wish may load again: None while it has fewer than ``limit`` downloads in the last 24 hours, else the
    moment the oldest of those that count is a day old."""
    made = sorted(db.scalars(select(Download.created_at).where(Download.wish_id == row.id,
                                                              Download.created_at >= moment - WINDOW)))
    if len(made) < limit:
        return None
    return made[len(made) - limit] + WINDOW


def _wait_for_the_window(row: ProcureWish, keep: dict[str, Any], until: datetime, moment: datetime,
                         total: int = 0) -> None:
    row.state = "upgrade" if keep else "not_found"
    row.detail = {**keep, "total": total, "reasons": [], "error": "replace_limit"}
    row.next_try_at, row.updated_at = until, moment


# --- The book of a wish ----------------------------------------------------------------------------------------------


@dataclass
class BookInfo:
    work_id: int | None
    token: str | None
    title: str
    authors: list[str] = field(default_factory=list)
    series: str = ""
    position: float | None = None
    color: str = ""
    cover_url: str | None = None
    genre: str = ""
    released: str = ""
    original_language: str = ""


def books_for(db: Session, uid: int, items: list[tuple[int, int | None, int | None]]) -> dict[int, BookInfo]:
    """The books the person sees, by the key of the item: ``(key, work_id, foreign_id)``. A work only when one of their
    libraries holds it."""
    found: dict[int, BookInfo] = {}
    work_ids = {work_id for _key, work_id, _foreign in items if work_id is not None}
    if work_ids:
        seen = set(db.scalars(select(Edition.work_id).where(Edition.work_id.in_(work_ids),
                                                            *works.visible_edition_clause(uid))))
        views = {view.work.id: view for view in works.views(
            db, uid, list(db.scalars(select(Work).where(Work.id.in_(seen)))))} if seen else {}
        for key, work_id, _foreign in items:
            view = views.get(work_id) if work_id is not None else None
            if view is not None:
                found[key] = BookInfo(
                    work_id=view.work.id, token=None, title=view.title, authors=[name for _id, name in view.authors],
                    series=view.series_name, position=view.series_pos, color=view.color,
                    cover_url=f"/api/v1/works/{view.work.id}/cover?v={view.cover[:12]}" if view.cover else None,
                    genre=view.genre, original_language=view.original_language)
    foreign_ids = {foreign_id for _key, _work, foreign_id in items if foreign_id is not None}
    if foreign_ids:
        books = {book.id: book for book in db.scalars(select(ForeignBook).where(ForeignBook.id.in_(foreign_ids)))}
        for key, work_id, foreign_id in items:
            book = books.get(foreign_id) if foreign_id is not None and work_id is None else None
            if book is not None:
                found[key] = BookInfo(
                    work_id=None, token=book.token, title=book.title,
                    authors=[name for name in book.authors or [] if isinstance(name, str)], series=book.series,
                    position=book.series_pos, color=book.color or "", genre=book.genre, released=book.released,
                    cover_url=pics.link_of(book),
                    original_language=book.language)
    return found


def _books_of(db: Session, uid: int, rows: list[ProcureWish]) -> dict[int, BookInfo]:
    return books_for(db, uid, [(row.id, row.work_id, row.foreign_id) for row in rows])


def book_text(db: Session, uid: int | None, *, work_id: int | None, foreign_id: int | None) -> tuple[str, str]:
    """The title and the first author of a book as the person sees it (for the history, which keeps them); empty for a
    work the person does not see (any more)."""
    if work_id is not None and uid is not None:
        try:
            view = works.get_work(db, uid, work_id)
        except HTTPException:
            return "", ""
        return view.title, view.authors[0][1] if view.authors else ""
    if foreign_id is not None:
        book = db.get(ForeignBook, foreign_id)
        if book is not None:
            return book.title, next((name for name in book.authors or [] if isinstance(name, str)), "")
    return "", ""


def snapshot(db: Session, uid: int | None, *, work_id: int | None, foreign_id: int | None, kind: str,
             language: str) -> dict[str, Any] | None:
    """What is wished, as it reads now (``ProcureWish.wished``): the title, up to three authors, the kind and the
    language. None when the book is not to be seen (nothing is made up)."""
    title, authors = "", []
    if work_id is not None and uid is not None:
        try:
            view = works.get_work(db, uid, work_id)
        except HTTPException:
            view = None
        if view is not None:
            title, authors = view.title, [name for _id, name in view.authors]
    elif foreign_id is not None:
        book = db.get(ForeignBook, foreign_id)
        if book is not None:
            title, authors = book.title, [name for name in book.authors or [] if isinstance(name, str)]
    if not title:
        return None
    return {"title": title[:300], "authors": [name[:200] for name in authors[:3]], "kind": kind,
            "language": language or ""}


def wished_of(db: Session, download: Download) -> dict[str, Any] | None:
    """What a download was for: its own record, else its wish's, else the book of the sources it is for. Never the
    work as it reads now (it may have been renamed after a wrong book went into it)."""
    if isinstance(download.wished, dict):
        return download.wished
    wish = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if wish is not None and isinstance(wish.wished, dict):
        return wish.wished
    if download.foreign_id is not None:
        return snapshot(db, None, work_id=None, foreign_id=download.foreign_id, kind=download.kind,
                        language=download.language)
    return None


seen_work = works.seen_work


def may_ask(db: Session, uid: int, library_id: int | None) -> bool:
    """Whether the person has the right ``acquire`` in the library."""
    if library_id is None:
        return False
    return db.scalar(select(Library.id).where(Library.id == library_id,
                                              Library.id.in_(libraries.acquirable_library_ids(uid)))) is not None


def _released_after_today(released: str) -> str:
    """The date a book comes out when that is after today (a year alone counts from its first day), else empty."""
    text = (released or "").strip()
    today = clock.now().astimezone(timezones.instance_zone()).date()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
        elif len(text) == 4:
            day = date(int(text), 1, 1)
        else:
            return ""
    except ValueError:
        return ""
    return day.isoformat() if day > today else ""


# --- Making wishes ---------------------------------------------------------------------------------------------------


@dataclass
class Made:
    """What became of one kind of one book in a request: ``created``, ``exists`` (somebody wished it already), ``have``
    (the book has that kind, no better one is looked for) or ``error`` with ``code``."""

    kind: str
    status: str
    wish: ProcureWish | None = None
    code: str = ""


def _library_for(db: Session, uid: int, kind: str, places: dict[str, Library], library_id: int | None) -> Library:
    if library_id is None:
        place = places.get(kind)
        if place is None:
            raise error("acquire_not_allowed", "You may not ask for books of this kind.", 403)
        return place
    chosen = db.scalar(select(Library).where(Library.id == library_id,
                                             Library.id.in_(libraries.acquirable_library_ids(uid))))
    if chosen is None:
        raise error("library_not_allowed", "You may not ask for books in this library.", 403)
    if not libraries.kind_allows(chosen.kind, kind):
        raise error("library_kind", "This library does not take books of this kind.", 422)
    return chosen


def _ticked(db: Session, kind: str) -> list[str]:
    return [entry["f"] for entry in formats.config(db)[kind] if entry["on"]]


def _upgrade_of(db: Session, uid: int, work_id: int, kind: str, languages: list[str]) -> tuple[bool, dict[str, str]]:
    """Whether the work has this kind in the language wished, and whether a better format than the one it has is looked
    for; with the format it has and the goal. Nothing of the kind in that language is no upgrade, it is a new wish."""
    subject = search.subject_of_work(db, uid, work_id, languages)
    config = formats.config(db)
    have = (subject.get("have") or {}).get(kind)
    if have is None:
        # Nothing of this kind in the language wished (a book that has an e-book in English has none in German yet).
        return False, {}
    order = [entry["f"] for entry in config[kind]]
    goal = config["target"][kind]
    better = have is not None and (order.index(have) if have in order else len(order)) > (
        order.index(goal) if goal in order else 0)
    return better, {"have": have or "", "goal": goal}


def create(db: Session, account: Account, *, work_id: int | None, token: str | None, kinds: list[str],
           language: str | None, library_id: int | None, released: str = "",
           background: bool | None = None) -> list[Made]:
    """Wishes for a book: one per kind. A kind somebody wished already shows that wish; a kind the book has needs a
    format below the goal (an upgrade). A request that makes nothing and finds no wish for the book is an error.

    ``released``: the day the book comes out in the language wished, when it is not the day of the book of the sources
    (a translation that is announced; watching an author knows it); ``background``: the first search is one the
    automatic work makes by itself (it keeps to its part of the limits of the day, see ``begin``)."""
    if (work_id is None) == (not token):
        raise error("invalid_input", "Name a work or a book of the sources.", 422, field="work_id")
    wanted = [kind for kind in KINDS if kind in kinds]
    if not wanted:
        raise error("invalid_input", "Name a kind of book.", 422, field="kinds")
    places = search.targets(db, account.id)
    if not places:
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)
    code = search.language_of(language)
    book_row: ForeignBook | None = None
    release_date = ""
    if work_id is not None:
        works.get_work(db, account.id, work_id)
        original = search.original_of(db, work_id, None)
    else:
        from ..discover.view import by_token

        book_row = by_token(db, token or "")
        if book_row is None:
            raise error("not_found", "Not found.", 404)
        # A hit of the catalogue search that is no book of its own (a box, a play, a part, a study guide, a comic) or a
        # book of a series whose volume nobody knows: what would be fetched is the book, another volume, or nothing.
        why_not = book_row.data.get("search_why_not") if isinstance(book_row.data, dict) else None
        if why_not == "not_a_book":
            raise error("not_a_book", "This is no book of its own. Ask for the book itself.", 422)
        if why_not == "volume_unknown":
            raise error("volume_unknown", "Which volume of the series this is, is not known. Pick the volume.", 422)
        release_date = _released_after_today(released or book_row.released)
        original = book_row.language or ""
    search.check_language(db, code, original)
    accepted = search.accepted_of(db, code, original)
    if db.scalar(select(Indexer.id).where(Indexer.enabled.is_(True)).limit(1)) is None:
        raise error("no_indexers", "No indexer is switched on.", 409)
    rations.ensure("wish", account, db)
    automatic = automation.config(db)["enabled"]
    results: list[Made] = []
    open_choice = search.must_choose(db, account.id) if library_id is None else set()
    for kind in wanted:
        if kind in open_choice:
            raise error("library_required", "Several libraries take this kind of book: name the one it goes into.",
                        422, field="library_id")
        place = _library_for(db, account.id, kind, places, library_id)
        upgrade: dict[str, str] = {}
        if work_id is not None:
            better, upgrade = _upgrade_of(db, account.id, work_id, kind, accepted)
            if upgrade and not better:
                results.append(Made(kind, "have"))
                continue
        row = _open_wish(db, work_id, book_row.id if book_row is not None else None, kind, code, place.id)
        if row is not None:
            results.append(Made(kind, "exists", row))
            continue
        moment = clock.now()
        state = "upgrade" if upgrade else "waiting" if release_date else "searching" if automatic else "idle"
        detail = upgrade or ({"reason": "main_off"} if state == "idle" else None)
        row = ProcureWish(user_id=account.id, work_id=work_id, foreign_id=book_row.id if book_row else None,
                          kind=kind, language=code, library_id=place.id, state=state, detail=detail,
                          release_date=release_date, created_at=moment, updated_at=moment,
                          wished=snapshot(db, account.id, work_id=work_id,
                                          foreign_id=book_row.id if book_row else None, kind=kind, language=code))
        try:
            with db.begin_nested():
                db.add(row)
        except IntegrityError:
            row = _open_wish(db, work_id, book_row.id if book_row else None, kind, code, place.id)
            if row is None:
                raise
            results.append(Made(kind, "exists", row))
            continue
        results.append(Made(kind, "created", row))
    db.commit()
    rations.add("wish", account, sum(1 for made in results if made.status == "created"))
    for made in results:
        if made.status == "created" and made.wish is not None:
            logger.info("Wish %d made kind=%s state=%s", made.wish.id, made.kind, made.wish.state)
    for made in results:
        wish = made.wish
        if made.status == "created" and wish is not None and wish.state in ("searching", "upgrade") and automatic:
            begin(db, wish, background)
    return results


def _open_wish(db: Session, work_id: int | None, foreign_id: int | None, kind: str, language: str,
               library_id: int) -> ProcureWish | None:
    """The open wish for the book, kind and language in this library (the caller has the right there, so the wish is
    one they see); a wish in another library is not theirs to know."""
    book = ProcureWish.work_id == work_id if work_id is not None else ProcureWish.foreign_id == foreign_id
    return db.scalar(select(ProcureWish).where(book, ProcureWish.kind == kind, ProcureWish.language == language,
                                               ProcureWish.library_id == library_id, ProcureWish.state != "done"))


# --- Searching -------------------------------------------------------------------------------------------------------


def _owner(db: Session, row: ProcureWish) -> Account | None:
    return db.get(Account, row.user_id)


def begin(db: Session, row: ProcureWish, background: bool | None = None) -> None:
    """Searches for the wish now, when its person has a free place; else it waits (``kick`` starts it). With the main
    switch off nothing is searched: the wish rests (``idle``, ``main_off``) until the switch is on again.

    ``background``: the search is one the automatic work makes by itself (a wish that comes back), which keeps to its
    part of the limits of the day; by default a wish that was searched before counts as one, a new wish does not."""
    with _lock:
        if not fresh(db, row) or row.state == "done":
            return
        account = _owner(db, row)
        if account is None:
            return
        moment = clock.now()
        if _rests(db, row, moment) or language_off(db, row, moment):
            return
        if not automation.config(db)["enabled"]:
            keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
            row.state, row.search_id, row.next_try_at, row.updated_at = "idle", None, None, moment
            row.detail = {**keep, "reason": "main_off"}
            db.commit()
            return
        place = db.get(Library, row.library_id) if row.library_id is not None else None
        if place is not None and not formats.write_state(place.path)[0]:
            # Nothing would be loaded: no search is made (it would cost the indexers' limits for nothing). The wish
            # waits and says why; it looks again in an hour.
            keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
            row.state, row.search_id, row.updated_at = "upgrade" if keep else "not_found", None, moment
            row.detail = {**keep, "total": 0, "reasons": [], "load_error": "target_not_writable"}
            row.next_try_at = moment + BROKEN_RETRY
            db.commit()
            logger.info("Wish %d waits, nexbooks may not write into its library", row.id)
            return
        if background is None:
            background = row.searched_at is not None
        earlier = _earlier(row)
        try:
            found = search.start(db, account, work_id=row.work_id, token=_token_of(db, row),
                                 language=row.language or search.ANY_LANGUAGE, fresh=True, kinds=[row.kind],
                                 background=background)
        except HTTPException as exc:
            code = exc.detail.get("code", "") if isinstance(exc.detail, dict) else ""
            if code == "too_many_searches":
                row.state, row.search_id, row.updated_at = "searching", None, moment
                db.commit()
                return
            row.state, row.search_id, row.updated_at = "not_found", None, moment
            row.detail = {"total": 0, "reasons": [], "error": code or "search_failed"}
            row.next_try_at = moment + BROKEN_RETRY
            db.commit()
            logger.info("Wish %d could not search code=%s", row.id, code)
            return
        row.state, row.search_id, row.searched_at, row.updated_at = "searching", found.id, moment, moment
        if row.detail and "have" not in row.detail:
            row.detail = None
        if earlier:
            row.detail = {**(row.detail or {}), "earlier": earlier}
        db.commit()


def _earlier(row: ProcureWish) -> dict[str, Any] | None:
    """What the last search of a wish found, kept while the next one runs: a search the limit of an indexer cut short
    brings nothing, and must not wipe it out (``_settle_once``)."""
    detail = row.detail or {}
    if isinstance(detail.get("earlier"), dict):
        return detail["earlier"]
    if row.search_id is None or not (detail.get("total") or detail.get("look")):
        return None
    return {"search": row.search_id, "total": detail.get("total"), "reasons": detail.get("reasons") or [],
            **({"look": detail["look"]} if detail.get("look") else {})}


def _rests(db: Session, row: ProcureWish, moment: Any) -> bool:
    """A wish of a person who has lost the right to ask for books in its library does not search or load: it rests
    (``idle``, ``no_right``) until the right is back or a colleague searches it (that takes it over). Commits when it
    rests."""
    if may_ask(db, row.user_id, row.library_id):
        return False
    keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
    row.state, row.search_id, row.next_try_at, row.updated_at = "idle", None, None, moment
    row.detail = {**keep, "reason": "no_right"}
    db.commit()
    return True


def language_allowed(db: Session, row: ProcureWish) -> bool:
    """Whether the language of the wish is one the operator allows now (settings, "Sprachen"); a wish for every
    allowed language always is."""
    return not row.language or row.language in formats.allowed_languages(
        db, search.original_of(db, row.work_id, row.foreign_id))


def language_off(db: Session, row: ProcureWish, moment: Any = None) -> bool:
    """A wish in a language the operator no longer allows does not go on by itself: it rests (``idle``,
    ``language_off``) and says so, until a person turns it to the languages allowed (``relanguage``) or takes it away.
    Commits when it rests."""
    if row.state in ("done", "loading") or language_allowed(db, row):
        return False
    keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
    row.state, row.search_id, row.next_try_at, row.updated_at = "idle", None, None, moment or clock.now()
    row.detail = {**keep, "reason": "language_off", "language": row.language}
    # A release found before is not handed over in a language no longer allowed.
    row.handover = None
    db.commit()
    logger.info("Wish %d rests, its language is no longer allowed", row.id)
    return True


def hold_unallowed(db: Session) -> int:
    """Every open wish in a language that is not allowed any more rests (``language_off``); the planner and a change of
    the languages call it. Returns how many."""
    held = 0
    for row in list(db.scalars(select(ProcureWish).where(
            ProcureWish.language != "", ProcureWish.state.in_(SEEKING)))):
        if (row.detail or {}).get("reason") == "language_off":
            continue
        if language_off(db, row):
            held += 1
    return held


def relanguage(db: Session, account: Account, wish_id: int, language: str | None) -> ProcureWish:
    """The wish looks for another language: one the operator allows, or none (every language allowed). A wish of the
    same book, kind and library in that language exists already: this one goes, that one stays and is the answer."""
    row = get(db, account, wish_id)
    if row.state in ("done", "loading"):
        raise error("wish_loading", "A release of this book is on its way.", 409)
    code = search.language_of(language)
    search.check_language(db, code, search.original_of(db, row.work_id, row.foreign_id))
    other = (_open_wish(db, row.work_id, row.foreign_id, row.kind, code, row.library_id)
             if row.library_id is not None else None)
    if other is not None and other.id != row.id:
        remove(db, account, row.id)
        return other
    moment = clock.now()
    keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
    row.language, row.updated_at, row.next_try_at, row.search_id = code, moment, None, None
    row.state = "upgrade" if keep else "waiting" if row.release_date else "searching"
    row.detail = keep or None
    db.commit()
    logger.info("Wish %d turned to another language", row.id)
    if row.state in ("searching", "upgrade") and automation.config(db)["enabled"]:
        begin(db, row)
    return row


def _token_of(db: Session, row: ProcureWish) -> str | None:
    if row.foreign_id is None:
        return None
    return db.scalar(select(ForeignBook.token).where(ForeignBook.id == row.foreign_id))


def kick(db: Session, uid: int | None = None) -> int:
    """Starts the wishes that wait for a search of their person's, and settles the ones whose search is over without the
    wish having noticed (a restart in between). Returns how many it started. A person whose three places are taken is
    not asked again for the rest of their wishes in the same call."""
    started = 0
    full: set[int] = set()
    settled: dict[int, tuple[ReleaseSearch, list[ProcureWish]]] = {}
    query = select(ProcureWish).where(ProcureWish.state == "searching").order_by(ProcureWish.id)
    if uid is not None:
        query = query.where(ProcureWish.user_id == uid)
    for row in in_turn(db, list(db.scalars(query))):
        if row.search_id is None:
            if row.user_id in full:
                continue
            before = row.search_id
            begin(db, row)
            if row.state == "searching" and row.search_id is None:
                full.add(row.user_id)
            started += 1 if row.search_id != before else 0
            continue
        found = db.get(ReleaseSearch, row.search_id)
        if found is None or found.state == "failed":
            row.state, row.search_id, row.updated_at = "not_found", None, clock.now()
            row.detail = {"total": 0, "reasons": [], "error": "search_failed"}
            row.next_try_at = clock.now() + BROKEN_RETRY
            db.commit()
        elif found.state == "done":
            _settle(db, row, found)
            db.commit()
            if row.state != "searching" or row.search_id != found.id:
                settled.setdefault(found.id, (found, []))[1].append(row)
    # A search that was over before the job that ran it came to say so (the planner looks at the same moment) still gets
    # its line in the log of the automatic work.
    for found, rows in settled.values():
        try:
            _log_search(db, found, rows)
        except Exception:
            db.rollback()
            logger.exception("Writing the log line of search %d failed", found.id)
    return started


def after_search(db: Session, found: ReleaseSearch) -> None:
    """A search is done: the wishes that waited for it go on, and the log of the automatic work gets its line (the
    requests the indexers were asked, the wishes that were settled, the releases that were loaded)."""
    try:
        rows = list(db.scalars(select(ProcureWish).where(ProcureWish.search_id == found.id,
                                                         ProcureWish.state == "searching")))
        for row in rows:
            _settle(db, row, found)
        db.commit()
        if rows:
            _log_search(db, found, rows)
        kick(db, found.user_id)
    except Exception:
        db.rollback()
        logger.exception("Settling the wishes of search %d failed", found.id)


def _log_search(db: Session, found: ReleaseSearch, rows: list[ProcureWish]) -> None:
    from . import autolog

    round_ = autolog.Round("search")
    for entry in found.progress or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), int):
            round_.indexer(entry["id"], requests=int(entry.get("requests") or 0), entries=int(entry.get("found") or 0),
                           error=str(entry.get("error") or ""))
    round_.add("wishes", len(rows))
    for row in rows:
        db.refresh(row)
        if row.state == "loading":
            round_.add("loaded")
    round_.finish()


def _reasons_of(releases: list[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in releases:
        for found in item.reasons or []:
            if isinstance(found, dict) and found.get("code"):
                counts[found["code"]] = counts.get(found["code"], 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    return [{"code": code, "count": count} for code, count in ordered]


def _doubtful(releases: list[Any]) -> int:
    """How many releases only doubts stand against (``judge.ASK``): nexbooks loads none of them by itself, a person may
    after a look ("Braucht dich")."""
    return sum(1 for item in releases if item.reasons and all(
        isinstance(found, dict) and found.get("code") in judge.ASK for found in item.reasons))


def _found_nothing(keep: dict[str, Any], mine: list[Any]) -> dict[str, Any]:
    """The detail of a wish whose search found nothing that passes: the reasons, and ``look`` when releases with doubts
    only are there for a person to look at."""
    detail = {**keep, "total": len(mine), "reasons": _reasons_of(mine)}
    look = _doubtful(mine)
    if look:
        detail["look"] = look
    return detail


def _capped(found: ReleaseSearch) -> dict[str, Any] | None:
    for entry in found.progress or []:
        if isinstance(entry, dict) and entry.get("error") in ("indexer_daily_limit", "indexer_limit_reached",
                                                              "indexer_share_used"):
            return {"indexer": entry.get("name") or "", "until": entry.get("until"), "code": entry.get("error"),
                    "limit": entry.get("limit")}
    return None


def _settle(db: Session, row: ProcureWish, found: ReleaseSearch, *, avoid: frozenset[str] = frozenset()) -> None:
    """What the search found decides the wish: load the best release, or wait. Once: the job that ran the search and a
    person's request for the list both come here when a search is done, and the second finds the wish settled."""
    with _lock:
        if not fresh(db, row) or row.state != "searching" or row.search_id != found.id:
            return
        _settle_once(db, row, found, avoid)
        # Written before the lock is let go: the next one to come reads the wish as it stands.
        db.commit()


def settle(db: Session, row: ProcureWish, found: ReleaseSearch) -> None:
    """A search that was made out of the newest entries decides the wish (``rss``)."""
    _settle(db, row, found)


def _until(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _passing_of(db: Session, row: ProcureWish, found: ReleaseSearch, mine: list[Any],
                avoid: frozenset[str]) -> tuple[list[Any], int]:
    """The releases of the wish's kind that pass, best first, without what is blocked by now, what ``avoid`` names, and
    the torrents a program has for another library; with how many of the last kind there were."""
    passing = sorted((item for item in mine if item.rank is not None), key=lambda item: item.rank or 0)
    blocked = blocklist.blocked_for(db, work_id=row.work_id, foreign_id=row.foreign_id,
                                    key=blocklist.book_key(found.subject or {}),
                                    languages=search.accepted_of(db, row.language, search.original_of(
                                        db, row.work_id, row.foreign_id)))
    passing = [item for item in passing if item.title not in avoid
               and not blocklist.matches(blocked, item.title, item.protocol, item.info_hash)]
    # A torrent is in its program once: the program refuses the same one a second time (it is there for another
    # library's wish, or still seeds). Such a release is not taken; the next that passes is, else the wish waits.
    in_use = _torrents_in_use(db, row.library_id)
    left = [item for item in passing if not (item.info_hash and item.info_hash.lower() in in_use)]
    return left, len(passing) - len(left)


def _settle_once(db: Session, row: ProcureWish, found: ReleaseSearch, avoid: frozenset[str] = frozenset()) -> None:
    moment = clock.now()
    if not language_allowed(db, row):
        # Its language was taken away while the search ran: nothing of it is loaded.
        language_off(db, row, moment)
        return
    mine = [item for item in search.releases_of(db, found.id) if item.kind == row.kind]
    passing, skipped = _passing_of(db, row, found, mine, avoid)
    keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
    earlier = (row.detail or {}).get("earlier")
    # (What became of a search, in numbers: the wish keeps only the last answer.)
    logger.info("Wish %d: search %d found releases=%d passing=%d doubts=%d cut_short=%s", row.id, found.id, len(mine),
                len(passing), _doubtful(mine), _capped(found) is not None)
    row.searched_at, row.updated_at = moment, moment
    config = automation.config(db)
    retry = retry_for(row, config["retry_hours"], moment)
    if passing:
        if not config["enabled"]:
            row.state, row.detail = "idle", {**keep, "reason": "main_off", "passing": len(passing)}
            return
        account = _owner(db, row)
        if _rests(db, row, moment):
            return
        until = window_end(db, row, moment, config["replace_max"]) if config["replace"] else None
        if until is not None:
            # The wish has had as many downloads in the last day as it may: it waits until the first is a day old.
            _wait_for_the_window(row, keep, until, moment, len(mine))
            logger.info("Wish %d waits, it has had its downloads of the day", row.id)
            return
        if account is None:
            result = choose.Outcome(code="not_found")
        else:
            result = choose.load_best(db, account, found, passing, wish_id=row.id)
        row = db.get(ProcureWish, row.id) or row
        if result.loaded:
            if not fresh(db, row):
                return
            row.state, row.detail, row.next_try_at, row.handover = "loading", keep or None, None, None
            row.updated_at = moment
            return
        code = result.code
        if code == "already_loading":
            if _adopt_running(db, row):
                return
            # A release in another language is on its way for this book and library: this wish waits for it.
            code = "other_language_running"
        elif result.only_limits and result.tried:
            # Every indexer that had a release is at its limit: the wish waits for the day of the limit to be over.
            row.state, row.next_try_at = "capped", _until(result.until)
            row.detail = {**keep, "indexer": result.indexer, "until": result.until, "code": result.code}
            logger.info("Wish %d waits, the indexers are at their limits", row.id)
            return
        row.state = "upgrade" if keep else "not_found"
        row.detail = {**keep, "total": len(mine), "reasons": [], "load_error": code}
        row.next_try_at = moment + retry
        row.searched_at, row.updated_at = moment, moment
        if code in choose.UNREACHABLE and result.release_id is not None:
            # The program did not answer: the same release is handed over again in a few minutes, no new search.
            row.handover = {"search": found.id, "release": result.release_id,
                            "due": (moment + choose.handover_delay(0)).isoformat(), "tries": 0}
        logger.info("Wish %d: the best release could not be loaded code=%s", row.id, code)
        return
    capped = _capped(found)
    if capped is not None:
        # An indexer was cut short by its limit and nothing passed: the wish waits for the end of the limit, whatever
        # the others brought (it comes back at the moment the indexer can be asked again).
        row.state, row.next_try_at = "capped", _until(capped.get("until"))
        row.detail = {**keep, **capped, "total": len(mine), "reasons": _reasons_of(mine)}
        if isinstance(earlier, dict) and not mine:
            # Cut short before it brought anything: what the search before found still stands, its doubts too.
            row.detail.update({key: earlier[key] for key in ("total", "reasons", "look") if key in earlier})
            if db.get(ReleaseSearch, earlier.get("search")) is not None:
                row.search_id = earlier["search"]
        return
    if skipped:
        row.state = "upgrade" if keep else "not_found"
        row.detail = {**keep, "total": len(mine), "reasons": [], "load_error": "release_in_use"}
        row.next_try_at = moment + retry
        return
    # Nothing passed. A book of the sources that is not out yet waits for its day instead (the date may have come to
    # the sources after the wish was made).
    day = ""
    if row.foreign_id is not None:
        book = db.get(ForeignBook, row.foreign_id)
        day = _released_after_today(book.released) if book is not None else ""
    if day:
        row.state, row.release_date, row.next_try_at = "waiting", day, None
        row.detail = {**keep, "total": len(mine), "reasons": _reasons_of(mine)}
        return
    row.state = "upgrade" if keep else "not_found"
    row.detail = _found_nothing(keep, mine)
    row.next_try_at = moment + retry
    if row.detail.get("look"):
        logger.info("Wish %d: only releases with doubts, a person may look at them", row.id)


def _torrents_in_use(db: Session, library_id: int | None) -> set[str]:
    """The info hashes of the torrents that are in a program for another library: on their way, or filed and still
    seeding. (One for the wish's own library is its own download, which ``_adopt_running`` finds.)"""
    on_the_way = Download.state.in_(ACTIVE_DOWNLOADS)
    seeding = (Download.state == "imported") & (Download.seed_state != "done")
    return {value.lower() for value in db.scalars(select(Download.info_hash).where(
        Download.protocol == "torrent", Download.info_hash != "", Download.library_id != library_id,
        on_the_way | seeding))}


def _adopt_running(db: Session, row: ProcureWish) -> bool:
    """A release of the book, the kind, the language and the library of the wish is on its way already (another
    person's, or a second round of this wish): the wish is that one's now, not a wish that failed. A download in
    another language is not: it would fill the wish with a book the person did not ask for."""
    book = Download.work_id == row.work_id if row.work_id is not None else Download.foreign_id == row.foreign_id
    active = db.scalar(select(Download).where(
        book, Download.kind == row.kind, Download.language == row.language, Download.library_id == row.library_id,
        Download.state.in_(ACTIVE_DOWNLOADS)).order_by(Download.id.desc()).limit(1))
    if active is None:
        return False
    if active.wish_id is None:
        active.wish_id = row.id
    row.state, row.next_try_at, row.updated_at = "loading", None, clock.now()
    row.detail = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")} or None
    return True


def claim(db: Session, row: ProcureWish, *, search_id: int | None = None) -> bool:
    """Takes the wish for a search: ``searching`` (with the search that is already made, else without one), in one
    statement that holds only while nobody else changed the wish since ``row`` was read. The planner and a person who
    presses "Jetzt suchen" at the same moment both ask; one of them gets it. Returns whether this call did."""
    moment = clock.now()
    earlier = _earlier(row)
    changed = db.execute(update(ProcureWish).where(
        ProcureWish.id == row.id, ProcureWish.state == row.state, ProcureWish.updated_at == row.updated_at,
        ProcureWish.state != "done").values(state="searching", search_id=search_id, handover=None, updated_at=moment,
                                            detail={**(row.detail or {}), "earlier": earlier} if earlier
                                            else row.detail)).rowcount
    db.commit()
    return bool(changed) and fresh(db, row)


def search_now(db: Session, account: Account, wish_id: int) -> ProcureWish:
    """"Jetzt suchen": the wish is searched again at once (also when the main switch is off). Not for a wish that is
    ``loading`` or waits for the day its book comes out."""
    row = get(db, account, wish_id)
    if row.state == "loading":
        raise error("wish_loading", "A release of this book is on its way.", 409)
    if not language_allowed(db, row):
        language_off(db, row)
        raise error("language_not_accepted", "nexbooks does not fetch books in this language.", 422,
                    field="language", language=row.language)
    if row.state == "waiting":
        raise error("wish_waiting", "The book is not out yet.", 409, release_date=row.release_date)
    if row.state == "searching" and row.search_id is not None:
        found = db.get(ReleaseSearch, row.search_id)
        if found is not None and found.state in ("queued", "running"):
            return row
    # Before the wish is taken: a person over the day's searches does not leave it waiting for a search either.
    rations.ensure("search", account)
    was_idle = row.state == "idle"
    with _lock:
        if not fresh(db, row):
            raise error("not_found", "Not found.", 404)
        if row.state == "loading":
            raise error("wish_loading", "A release of this book is on its way.", 409)
        if row.state == "searching" and row.search_id is not None:
            found = db.get(ReleaseSearch, row.search_id)
            if found is not None and found.state in ("queued", "running"):
                return row
        if not claim(db, row):
            # The planner took it a moment ago: its search is the search of the person too.
            if not fresh(db, row):
                raise error("not_found", "Not found.", 404)
            return row
        if not may_ask(db, row.user_id, row.library_id):
            # The person who asked has lost the right here: whoever searches it now is its person.
            row.user_id = account.id
        if (row.detail or {}).get("reason") == "no_right":
            row.detail = None
        db.commit()
    begin_forced(db, row, was_idle, by=account)
    if not fresh(db, row):
        raise error("not_found", "Not found.", 404)
    return row


def begin_forced(db: Session, row: ProcureWish, was_idle: bool, *, by: Account | None = None) -> None:
    """Like ``begin``, for a person who asked (``by``, whose searches of the day it counts against): the main switch
    does not hold this one back."""
    account = _owner(db, row)
    if account is None:
        return
    moment = clock.now()
    if language_off(db, row, moment):
        return
    earlier = _earlier(row)
    try:
        found = search.start(db, account, work_id=row.work_id, token=_token_of(db, row),
                             language=row.language or search.ANY_LANGUAGE, fresh=True, kinds=[row.kind])
        if by is not None:
            rations.add("search", by)
    except HTTPException as exc:
        code = exc.detail.get("code", "") if isinstance(exc.detail, dict) else ""
        if code == "too_many_searches":
            row.state, row.search_id, row.updated_at = "searching", None, moment
            db.commit()
            return
        raise
    row.state = "searching"
    row.search_id, row.searched_at, row.updated_at = found.id, moment, moment
    if earlier:
        row.detail = {**(row.detail or {}), "earlier": earlier}
    db.commit()


# --- Seeing and removing ---------------------------------------------------------------------------------------------


def visible_clause(uid: int) -> Any:
    return ProcureWish.library_id.in_(libraries.acquirable_library_ids(uid))


def get(db: Session, account: Account, wish_id: int) -> ProcureWish:
    """A wish of a library the person may ask for books in (and a work they see); 404 for anybody else."""
    row = db.scalar(select(ProcureWish).where(ProcureWish.id == wish_id, visible_clause(account.id),
                                              seen_work(account.id, ProcureWish.work_id)))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def listing(db: Session, account: Account, *, mine: bool = False) -> list[tuple[ProcureWish, BookInfo]]:
    """The wishes the person sees, the newest first, with their books."""
    query = select(ProcureWish).where(visible_clause(account.id), ProcureWish.state != "done")
    if mine:
        query = query.where(ProcureWish.user_id == account.id)
    rows = list(db.scalars(query.order_by(ProcureWish.created_at.desc(), ProcureWish.id.desc()).limit(1000)))
    books = _books_of(db, account.id, rows)
    return [(row, books[row.id]) for row in rows if row.id in books]


def remove(db: Session, account: Account, wish_id: int) -> None:
    """The wish goes. What it found or loaded stays as it is; a download it had goes on, only without a wish."""
    row = get(db, account, wish_id)
    db.execute(update(Download).where(Download.wish_id == row.id).values(wish_id=None))
    db.delete(row)
    db.commit()
    logger.info("Wish %d removed", wish_id)


def counts(db: Session, account: Account) -> dict[str, int]:
    """For the head of the page: the wishes that seek, the downloads that run, what needs somebody."""
    allowed = libraries.acquirable_library_ids(account.id)
    seeking = db.scalar(select(func.count(ProcureWish.id)).where(
        ProcureWish.library_id.in_(allowed), seen_work(account.id, ProcureWish.work_id),
        ProcureWish.state.in_(SEEKING))) or 0
    running = db.scalar(select(func.count(Download.id)).where(
        Download.library_id.in_(allowed), seen_work(account.id, Download.work_id),
        Download.state.in_(("sending", "unsure", "queued", "downloading", "paused", "completed")))) or 0
    needs = db.scalar(select(func.count(Download.id)).where(
        Download.library_id.in_(allowed), seen_work(account.id, Download.work_id),
        Download.state == "problem")) or 0
    from ...models import ProcureNotice

    # (A note of an edition held twice stays when it was read, so that it is not made again: it counts no more.)
    needs += db.scalar(select(func.count(ProcureNotice.id)).where(
        ProcureNotice.user_id == account.id, func.json_extract(ProcureNotice.detail, "$.read").is_(None))) or 0
    return {"seeking": seeking, "running": running, "needs": needs}


# --- What downloads do to a wish -------------------------------------------------------------------------------------


def attach(db: Session, account: Account, found: ReleaseSearch, download: Download, library_id: int) -> None:
    """A release was loaded: the book's open wish is on its way now, and one is made when there is none (a release
    chosen by hand from the table of a search)."""
    row = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if row is None:
        book = ProcureWish.work_id == found.work_id if found.work_id is not None else \
            ProcureWish.foreign_id == found.foreign_id
        row = db.scalar(select(ProcureWish).where(
            book, ProcureWish.kind == download.kind, ProcureWish.language == download.language,
            ProcureWish.library_id == library_id, ProcureWish.state != "done").order_by(ProcureWish.id).limit(1))
    moment = clock.now()
    if row is None:
        row = ProcureWish(user_id=account.id, work_id=found.work_id, foreign_id=found.foreign_id, kind=download.kind,
                          language=found.language, library_id=library_id, state="loading", created_at=moment,
                          updated_at=moment,
                          wished=snapshot(db, account.id, work_id=found.work_id, foreign_id=found.foreign_id,
                                          kind=download.kind, language=found.language))
        try:
            with db.begin_nested():
                db.add(row)
        except IntegrityError:
            row = None
    if row is not None:
        row.state, row.next_try_at, row.updated_at = "loading", None, moment
        row.detail = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")} or None
        db.flush()
        download.wish_id = row.id
    if download.wished is None:
        download.wished = (row.wished if row is not None and isinstance(row.wished, dict) else None) or snapshot(
            db, account.id, work_id=found.work_id, foreign_id=found.foreign_id, kind=download.kind,
            language=download.language)
    db.commit()


def after_cancel(db: Session, download: Download, *, search_on: bool) -> None:
    """A person took a download away: the wish stays, idle, or goes on searching when it was asked to."""
    row = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if row is None or row.state == "done":
        return
    if search_on:
        row.state, row.search_id, row.updated_at = ("upgrade" if (row.detail or {}).get("have") else "searching",
                                                    None, clock.now())
        db.commit()
        begin(db, row, background=False)
    else:
        row.state, row.detail, row.updated_at = "idle", {**(row.detail or {}), "reason": "cancelled"}, clock.now()
        db.commit()


def after_failure(db: Session, download: Download) -> str:
    """A download failed in its program: ``replace`` when the next fitting release is looked for (taken at once from the
    search the failed one came from, else a new search), ``limit`` when the wish has used its tries of 24 hours, ``off``
    when replacing is switched off, ``none`` when no wish is left."""
    row = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if row is None or row.state == "done":
        return "none"
    config = automation.config(db)
    moment = clock.now()
    keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
    if not config["replace"] or not config["enabled"]:
        row.state, row.detail, row.updated_at = "idle", {**keep, "reason": "failed"}, moment
        db.commit()
        return "off"
    until = window_end(db, row, moment, config["replace_max"])
    if until is not None:
        # As many downloads in the last day as the wish may have (the one that failed counts): it waits for the window.
        row.search_id = None
        _wait_for_the_window(row, keep, until, moment)
        db.commit()
        return "limit"
    if _next_best(db, row, download, keep):
        return "replace"
    row.state, row.search_id, row.updated_at = "upgrade" if keep else "searching", None, moment
    row.detail = keep or None
    db.commit()
    begin(db, row, background=False)
    return "replace"


#: How old the search a replacement reads may be: the releases are what the indexers had then.
REPLACE_FROM_SEARCH = timedelta(hours=24)


def _next_best(db: Session, row: ProcureWish, download: Download, keep: dict[str, Any]) -> bool:
    """The next release that passes in the search the failed one came from, loaded at once; False when there is none
    left (or the search is too old), and a new search is the way on. The release that failed is on the blocklist
    already; one the program never took (``not_taken``) is not tried again at the same hand."""
    found = db.get(ReleaseSearch, row.search_id) if row.search_id is not None else None
    if found is None or found.state != "done" or found.finished_at is None \
            or found.finished_at < clock.now() - REPLACE_FROM_SEARCH:
        return False
    if not language_allowed(db, row):
        # The search was made with the languages of its day: a wish whose language is no longer allowed rests.
        return False
    if (found.work_id, found.foreign_id) != (row.work_id, row.foreign_id):
        return False
    mine = [item for item in search.releases_of(db, found.id) if item.kind == row.kind]
    left, _skipped = _passing_of(db, row, found, mine, frozenset({download.title}))
    if not left:
        return False
    with _lock:
        if not fresh(db, row) or row.state == "done":
            return True
        row.state, row.search_id, row.detail, row.updated_at = "searching", found.id, keep or None, clock.now()
        db.commit()
        _settle_once_locked(db, row, found, frozenset({download.title}))
    logger.info("Wish %d goes on with the next release of its search", row.id)
    return True


def _settle_once_locked(db: Session, row: ProcureWish, found: ReleaseSearch, avoid: frozenset[str]) -> None:
    _settle_once(db, row, found, avoid)
    db.commit()


def after_interrupted(db: Session, download: Download) -> None:
    """A hand-over a stop of the server left half done is over: the wish searches again (no try is counted against
    it, it did not fail), or rests when the main switch is off."""
    row = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if row is None or row.state == "done":
        return
    keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
    moment = clock.now()
    row.search_id, row.next_try_at, row.updated_at = None, None, moment
    if not automation.config(db)["enabled"]:
        row.state, row.detail = "idle", {**keep, "reason": "main_off"}
        db.commit()
        return
    # ``searching`` without a search: the next ``kick`` (each tick of the planner, a search done) starts it.
    row.state, row.detail = "searching", keep or None
    db.commit()


def _language_below(db: Session, row: ProcureWish) -> dict[str, str] | None:
    """For a wish in "the languages allowed" (no language of its own): ``{"have_language", "goal_language"}`` when the
    work has the kind only in a language ranked below the first allowed one, else None. Such a wish is met only by the
    highest language: it goes on as an upgrade over the language, up and never down."""
    if row.work_id is None or row.language:
        return None
    accepted = search.accepted_of(db, row.language, search.original_of(db, row.work_id, None))
    if len(accepted) < 2:
        return None
    try:
        subject = search.subject_of_work(db, row.user_id, row.work_id, accepted)
    except HTTPException:
        return None
    there = [code for code in (subject.get("have_languages") or {}).get(row.kind) or [] if code in accepted]
    if not there:
        return None
    best = min(accepted.index(code) for code in there)
    if best == 0:
        return None
    return {"have_language": accepted[best], "goal_language": accepted[0]}


def _below_goal(db: Session, row: ProcureWish) -> dict[str, str] | None:
    """``{"have", "goal"}`` when the work has the kind in the language wished at a format below the goal of the ranking
    (an upgrade is looked for), else None. A book the person cannot see any more has no upgrade."""
    if row.work_id is None:
        return None
    try:
        better, info = _upgrade_of(db, row.user_id, row.work_id, row.kind, search.accepted_of(
            db, row.language, search.original_of(db, row.work_id, None)))
    except HTTPException:
        return None
    language = _language_below(db, row)
    if language is not None:
        return {**(info or {}), **language}
    return info if better and info else None


def complete(db: Session, download: Download, work_id: int | None = None) -> None:
    """The book is filed (filing calls it): the wish is done, and a wish of a book of the sources
    gets its work. A book filed in a format below the goal of the ranking is not done: the wish goes on as an upgrade,
    searched in the time the operator chose, until the goal is reached."""
    row = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if row is None:
        return
    moment = clock.now()
    if work_id is not None and row.work_id is None:
        row.work_id, row.foreign_id = work_id, None
    row.updated_at, row.handover = moment, None
    again = _below_goal(db, row)
    if again is not None:
        row.state, row.search_id, row.done_at, row.detail = "upgrade", None, None, again
        row.next_try_at = moment + retry_for(row, automation.config(db)["retry_hours"], moment)
    else:
        row.state, row.done_at, row.detail, row.next_try_at = "done", moment, None, None
    db.commit()


#: The wishes that still look for their book (an upgrade has it already, a download of one is on its way).
STILL_LOOKING = ("searching", "idle", "not_found", "waiting", "capped")
#: How many open wishes one look at the whole server takes at most (the oldest first).
MET_BATCH = 500


def met_elsewhere(db: Session, work_ids: Any = None) -> int:
    """``_met_elsewhere``; a failure of it is logged and fails nothing else (a scan, a filing, a join)."""
    try:
        return _met_elsewhere(db, work_ids)
    except Exception:
        # The wishes are looked at again by the planner every hour.
        db.rollback()
        logger.exception("Looking whether wishes are met another way failed")
        return 0


def _met_elsewhere(db: Session, work_ids: Any = None) -> int:
    """Wishes whose work got the kind wished in a language that fits another way (two books joined by a person, "Gehört
    zu", a reading of the library, another download): the wish is done and looks no further; a release it found and
    could not hand over yet is not handed over (``handover``). A download already on its way goes on (it is filed or
    turned away as a book that is there). ``work_ids``: only the wishes of these works; None for every open wish.
    Commits. Returns how many were done."""
    query = select(ProcureWish).where(ProcureWish.state.in_(STILL_LOOKING), ProcureWish.work_id.is_not(None))
    if work_ids is not None:
        ids = sorted({int(ident) for ident in work_ids if ident})
        if not ids:
            return 0
        query = query.where(ProcureWish.work_id.in_(ids))
    done = 0
    for row in list(db.scalars(query.order_by(ProcureWish.id).limit(MET_BATCH))):
        assert row.work_id is not None
        try:
            better, have = _upgrade_of(db, row.user_id, row.work_id, row.kind, search.accepted_of(
                db, row.language, search.original_of(db, row.work_id, None)))
        except HTTPException:
            # A work the person does not see (any more): not for this look to decide.
            continue
        if not have:
            continue
        if have.get("have") not in _ticked(db, row.kind):
            # A format the operator does not take (a PDF) meets no wish.
            continue
        moment = clock.now()
        row.updated_at, row.search_id, row.handover = moment, None, None
        higher = _language_below(db, row)
        if higher is not None:
            # A wish for "the languages allowed" has its book in a lower one: it goes on for the first.
            better, have = True, {**have, **higher}
        if better:
            # As ``complete``: a format below the goal, the wish goes on for a better one.
            row.state, row.done_at, row.detail = "upgrade", None, {**have, "reason": "met_elsewhere"}
            row.next_try_at = moment + retry_for(row, automation.config(db)["retry_hours"], moment)
        else:
            row.state, row.done_at, row.next_try_at = "done", moment, None
            row.detail = {"reason": "met_elsewhere"}
        done += 1
        logger.info("Wish %d is met another way%s", row.id, ", an upgrade is looked for" if better else "")
    if done:
        db.commit()
    return done


# --- The wishes that come due ----------------------------------------------------------------------------------------

#: How many wishes that are due a call looks at to find one it may search (the others are upgrades that are over).
DUE_BATCH = 25


def finish_upgrade(db: Session, row: ProcureWish) -> None:
    """The goal of the ranking is reached (or was lowered, or the book is gone from the person's sight): the search
    for a better format ends and the wish is done."""
    moment = clock.now()
    row.state, row.done_at, row.updated_at, row.detail, row.next_try_at = "done", moment, moment, None, None
    row.search_id, row.handover = None, None
    db.commit()
    logger.info("Wish %d reached the goal of the ranking", row.id)


def end_reached_upgrades(db: Session) -> int:
    """The wishes that look for a better format of a book that has the goal by now end. Returns how many."""
    ended = 0
    for row in list(db.scalars(select(ProcureWish).where(ProcureWish.state == "upgrade"))):
        if _below_goal(db, row) is None:
            finish_upgrade(db, row)
            ended += 1
    return ended


def resume_main_off(db: Session) -> int:
    """The main switch is on again: the wishes that rested because it was off search (``kick`` starts them, each
    person three at a time). Returns how many."""
    resumed = 0
    for row in list(db.scalars(select(ProcureWish).where(ProcureWish.state == "idle"))):
        if (row.detail or {}).get("reason") != "main_off":
            continue
        keep = {key: value for key, value in (row.detail or {}).items() if key in ("have", "goal")}
        row.state, row.detail, row.search_id, row.updated_at = "searching", keep or None, None, clock.now()
        resumed += 1
    if resumed:
        db.commit()
        logger.info("%d wishes that waited for the main switch go on", resumed)
    return resumed


def retime(db: Session) -> int:
    """The operator chose another time between two tries: the wishes that wait for the next try are due that long after
    their last search. Returns how many changed."""
    hours = automation.config(db)["retry_hours"]
    moment = clock.now()
    changed = 0
    for row in list(db.scalars(select(ProcureWish).where(
            ProcureWish.state.in_(("not_found", "upgrade")), ProcureWish.next_try_at.is_not(None),
            ProcureWish.searched_at.is_not(None)))):
        if (row.detail or {}).get("error") in ("search_failed", "replace_limit"):
            continue
        later = row.searched_at + retry_for(row, hours, moment)
        if later != row.next_try_at:
            row.next_try_at = later
            changed += 1
    if changed:
        db.commit()
    return changed


#: Before any search: a person none of whose wishes was searched yet comes first.
NEVER = datetime(1970, 1, 1, tzinfo=UTC)


def _last_searched(db: Session, people: set[int]) -> dict[int, datetime]:
    """When a wish of each person was last searched (the person who waited longest for a search comes first)."""
    if not people:
        return {}
    rows = db.execute(select(ProcureWish.user_id, func.max(ProcureWish.searched_at)).where(
        ProcureWish.user_id.in_(people)).group_by(ProcureWish.user_id)).all()
    return {int(uid): moment for uid, moment in rows if moment is not None}


def in_turn(db: Session, rows: list[ProcureWish]) -> list[ProcureWish]:
    """The wishes person by person, in turn: the first of each person, then the
    second of each, and so on, the person whose wish was searched longest ago first; each person's own in the order
    they came. One person with five hundred wishes never pushes another's wish back."""
    mine: dict[int, list[ProcureWish]] = {}
    for row in rows:
        mine.setdefault(row.user_id, []).append(row)
    last = _last_searched(db, set(mine))
    order = sorted(mine, key=lambda uid: (last.get(uid, NEVER), mine[uid][0].id))
    turned: list[ProcureWish] = []
    for place in range(max((len(items) for items in mine.values()), default=0)):
        turned.extend(mine[uid][place] for uid in order if place < len(mine[uid]))
    return turned


def due_clause(moment: Any) -> Any:
    today = budget.day(moment)
    return or_(
        (ProcureWish.state == "waiting") & (ProcureWish.release_date != "") & (ProcureWish.release_date <= today),
        (ProcureWish.state.in_(("not_found", "upgrade", "capped")) & ProcureWish.next_try_at.is_not(None)
         & (ProcureWish.next_try_at <= moment)),
        (ProcureWish.state == "capped") & ProcureWish.next_try_at.is_(None)
        & (ProcureWish.updated_at <= moment - timedelta(hours=1)))


def due_count(db: Session, moment: Any = None) -> int:
    return db.scalar(select(func.count(ProcureWish.id)).where(due_clause(moment or clock.now()))) or 0


def next_due_at(db: Session) -> Any:
    """When the next wish is due (the earliest ``next_try_at`` or the first day a book comes out), or None."""
    tries = db.scalar(select(func.min(ProcureWish.next_try_at)).where(
        ProcureWish.state.in_(("not_found", "upgrade", "capped")), ProcureWish.next_try_at.is_not(None)))
    return tries


#: Why an indexer rests, as the code a wish says it with.
_LIMITS = {"daily_limit": "indexer_daily_limit", "api_limit": "indexer_limit_reached",
           "request_limit": "indexer_limit_reached"}


def _indexers_rest(db: Session) -> dict[str, Any] | None:
    """Whether no enabled indexer may be asked by the background now (a limit, a pause): a search would end at once, and
    the wish would lose what it knew for nothing. Says when the first may be asked again, which is the one that decides
    the wait."""
    from . import indexers

    rows = list(db.scalars(select(Indexer).where(Indexer.enabled.is_(True))))
    rests = [(indexers.resting(row, background=True), row) for row in rows]
    if not rows or any(rest is None for rest, _row in rests):
        return None
    (until, cause), row = min(((rest, row) for rest, row in rests if rest is not None), key=lambda item: item[0][0])
    return {"indexer": row.name, "until": until.isoformat(), "code": _LIMITS.get(cause, ""), "limit": row.daily_limit}


def _waits_for_the_limit(row: ProcureWish, rest: dict[str, Any]) -> None:
    """A wish that falls due while no indexer may be asked waits for the first that may, and keeps its last search and
    what it found: no search was made that could say otherwise."""
    row.state, row.next_try_at, row.updated_at = "capped", _until(rest["until"]), clock.now()
    row.detail = {**(row.detail or {}), **rest}


def limit_lifted(db: Session) -> int:
    """A wish that waits for the limit of an indexer that no longer holds (the limit was raised or taken away, the
    indexer was switched off or removed) is due now, not when the day of the old limit ends. The share of one person
    is not seen from the indexer: it only ends with the limit. Returns how many wishes came free."""
    from . import indexers

    moment = clock.now()
    freed = 0
    for row in db.scalars(select(ProcureWish).where(ProcureWish.state == "capped", ProcureWish.next_try_at > moment)):
        detail = row.detail if isinstance(row.detail, dict) else {}
        name = detail.get("indexer")
        if not name:
            continue
        held = db.scalar(select(Indexer).where(Indexer.name == name))
        if held is not None and held.enabled and (
                indexers.resting(held, background=True) is not None or indexers.resting(held, grab=True) is not None
                or (detail.get("code") == "indexer_share_used" and held.daily_limit > 0)):
            continue
        row.next_try_at, row.updated_at = moment, moment
        row.detail = {key: value for key, value in detail.items() if key not in ("until", "code", "limit")}
        freed += 1
    if freed:
        db.commit()
        logger.info("%d wishes no longer wait for a limit", freed)
    return freed


def run_due(db: Session) -> int:
    """Lets one wish that is due search again: the book came out, the day of an indexer's limit is over, or the time of
    the next try is here (the main switch holds it back, and so does an hour no indexer may be asked in). One per call,
    the one that waited longest: the indexers have limits of their own, and a pile of wishes that fall due at once is
    searched one after the other. Returns how many it started."""
    hold_unallowed(db)
    for resting in list(db.scalars(select(ProcureWish).where(ProcureWish.state == "idle"))):
        if (resting.detail or {}).get("reason") == "no_right" and may_ask(db, resting.user_id, resting.library_id):
            keep = {key: value for key, value in (resting.detail or {}).items() if key in ("have", "goal")}
            resting.state, resting.detail, resting.updated_at = "searching", keep or None, clock.now()
            db.commit()
    enabled = bool(automation.config(db)["enabled"])
    if enabled:
        resume_main_off(db)
    kick(db)
    if not enabled:
        return 0
    limit_lifted(db)
    rest = _indexers_rest(db)
    moment = clock.now()
    today = budget.day(moment)
    # Of each person the wish that was searched longest ago (a wish never searched: by its creation). When the limit of
    # the day only reaches some of them, the next day begins with the others, so that none starves behind the same few.
    # Then person by person in turn (``in_turn``).
    waited = func.row_number().over(partition_by=ProcureWish.user_id, order_by=(
        func.coalesce(ProcureWish.searched_at, ProcureWish.created_at), ProcureWish.id)).label("place")
    picked = select(ProcureWish.id, waited).where(due_clause(moment)).subquery()
    ids = select(picked.c.id).where(picked.c.place <= DUE_BATCH)
    rows = in_turn(db, list(db.scalars(select(ProcureWish).where(ProcureWish.id.in_(ids)).order_by(
        func.coalesce(ProcureWish.searched_at, ProcureWish.created_at), ProcureWish.id))))
    if rest is not None:
        for row in rows:
            if rest["code"] and row.state in ("not_found", "upgrade", "capped"):
                _waits_for_the_limit(row, rest)
        db.commit()
        return 0
    for row in rows:
        if row.state == "upgrade" and _below_goal(db, row) is None:
            finish_upgrade(db, row)
            continue
        was_waiting = row.state == "waiting"
        if not claim(db, row):
            continue
        if was_waiting and row.release_date and row.release_date <= today:
            row.release_date = ""
            db.commit()
        begin(db, row, background=True)
        return 1
    return 0


__all__ = [
    "BookInfo",
    "Made",
    "after_cancel",
    "after_failure",
    "after_interrupted",
    "after_search",
    "attach",
    "begin",
    "book_text",
    "books_for",
    "claim",
    "complete",
    "counts",
    "create",
    "due_clause",
    "due_count",
    "end_reached_upgrades",
    "finish_upgrade",
    "fresh",
    "get",
    "hold_unallowed",
    "kick",
    "language_allowed",
    "language_off",
    "listing",
    "may_ask",
    "met_elsewhere",
    "next_due_at",
    "relanguage",
    "remove",
    "resume_main_off",
    "retime",
    "retry_for",
    "run_due",
    "search_now",
    "seen_work",
    "settle",
    "visible_clause",
    "window_end",
]
