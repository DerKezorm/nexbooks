"""Watching an author: a person switches "watch" on for an author, and nexbooks wishes the author's new books for
them, by itself.

**What is wished.** Per person and author one watch (``AuthorWatch``): the kind (e-book, audio book, both), the
language and whether more than novels and stories counts (non-fiction, children's books, collections, biographies,
poems). Books are wished in **one language only**: the first of the languages books are fetched in (settings,
"Sprachen", in the operator's order; "Originalsprache" there is the language the work first came out in), or one of
those languages the person chose for the watch. The language of the person's account plays no part. A work the
sources could not place for sure (``unsure``) is never wished. A work is wished when it is **new** in that language:

* **announced**: the day it comes out is after today. This holds when the watch is switched on (the books announced
  then are wished at once and wait for their day, as every wish for a book that is not out does) and at every later
  look. A book announced for a year without a day is not wished before a day is known: it is shown as coming out
  that year, and wished when a later look finds its day (also when that day has passed by then);
* **out since the last look**: at a later look only, a work that is new in the catalogue (its key was not known at
  the last look) and came out on or after the day of that look. A book that was out when the watch was switched on
  (on that day too) is never wished ("Beschaffen" fetches it), and neither is an old book the catalogue only learns
  of now.

**Not wished, but put on the wish list** (with the reason, ``WatchItem.why``): a work new only in another of the
languages books are fetched in (announced in the English original, not yet in German: it waits on the list until it
is announced in German, and is wished then), a work the person has in another language (the German translation of
the English original on their shelf), and a work the shared words of ``book_words`` put in doubt: an adaptation, a
study guide, an edition with additions, a play, a part, a box, a new edition or a new translation. The words are read
in its titles and in what the records of the edition said of it before the core of their title was taken
(``works.marks_of``: ``Teil 1 von 2``, ``1/2``, ``Das Hörspiel``, a library's subtitle ``Zwei Romane in einem Band``,
``Neuausgabe`` or ``Neu übersetzt``, a shop's word for a play); a doubt that only a recording has holds the audio book,
not the e-book.

**New is new in the language.** What counts is the first edition in the language of the watch, not the original: the
German translation of an English novel is new when it is announced, also when the original came out years before. A
later edition in a language the work is out in already is no new book: a paperback, a new edition (nothing is done),
or a new translation, which goes onto the wish list (``translation``: the records name other translators than the
earlier edition's, or say ``Neu übersetzt``). The first edition of a language is the earliest day its records give,
or an earlier year a record gives without a day (as the page of the author reads it, ``view.upcoming``: a day after
today beside an earlier year is a paperback of a book that is out). A new edition whose first one
no record of the language knows looks like a first edition, and is wished (unless its records say what it is).

**The day follows the announcement.** A wish the watch made waits for the day its book is announced for; when the
catalogue says another day later on (put back, or brought forward), the wish follows it, while it still waits (or
found nothing after a day the book did not come).

A book the person has, or already wished (by hand or by this watch, in whatever language), is never wished twice
(``WatchItem`` keeps what was done, also when the person took the wish away, and the unique wish of a book, kind,
language and library holds on top); a book somebody else wished for the library it would go into is not wished
beside it (the page says so, and the watch wishes it when that wish goes). Without the right to acquire (the right
of the person in a library that takes the kind) a new book goes onto the person's wish list.

**When it looks.** After every build of the catalogue of a watched author (``tick`` in the planner's beat, a few
watches at a time), and when a person switches a watch on or changes it. The catalogue of a watched author is built
again when it is a day old, which is as often as the sources' answers change (they are kept a day), one author at a
time and only while the lane of the catalogues is free (a person who looks at a page is never made to wait behind
it), people in turns (the person whose watch asked longest ago first), and only while every source the build asks
has used less than half of its requests of the day (``BUDGET_SHARE``): the rest is for the people. Nothing is asked
for an author nobody watches, and nothing at all with the main switch of the automatic work off: then switching a
watch on saves it and wishes nothing (the answer says so), and its first look comes once the switch is on again.

**Brakes.** At most ``MAX_WATCHES`` watches per person; a day's wishes by this watch are at most half the person's
wishes of the day (``WISH_SHARE``), the rest is theirs by hand, and the wishes of one person are searched in turn
with everybody else's (``procure.wishes.in_turn``); what is wished for a book that is out is searched as a
background search (it keeps to its part of the indexers' limits). A wish refused for a reason that does not pass by
itself (a language no longer fetched, no book of its own) is held with its code and not tried again at every look.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ... import clock
from ...config import get_settings
from ...models import (
    OPERATOR,
    Account,
    AuthorWatch,
    CatalogMove,
    CatalogPerson,
    CatalogWork,
    ForeignBook,
    Job,
    ProcureWish,
    WatchItem,
    Wish,
)
from .. import book_words, timezones
from ..discover import wishlist
from ..procure import automation, budget, formats, search, wishes
from ..sources import config as sources_config
from ..sources import throttle
from . import store
from . import view as view_service
from .view import AuthorView, Card

logger = logging.getLogger("nexbooks.oeuvre")

#: The most authors one person watches.
MAX_WATCHES = 100
#: Watches looked at in one beat of the planner.
BEAT_BATCH = 3
#: The most wishes and list entries one look makes (the rest follows a minute later).
MAX_PER_LOOK = 40
#: The share of a person's wishes of the day that watching may use, and of a source's requests of the day that the
#: builds for watching may use.
WISH_SHARE = 0.5
BUDGET_SHARE = 0.5
#: How long a watch waits before it looks again after it had to stop, and how long before the catalogue of one
#: author is asked for again by the beat (a build that failed must not be asked for every few seconds).
RETRY_SOON = timedelta(minutes=1)
RETRY_NO_INDEXER = timedelta(hours=1)
RETRY_LIST_FULL = timedelta(hours=24)
ASK_AGAIN = timedelta(hours=1)
#: A claim without a result older than this was left by a look that broke off: it is taken back.
STALE_CLAIM = timedelta(minutes=10)
#: The kinds of work that count with and without "more".
SURE_KINDS = ("roman", "erz", "bio", "sach", "lyrik", "kinder", "box")
BASE_KINDS = ("roman", "erz")
WANTED_KINDS = {"ebook": ("ebook",), "audio": ("audio",), "both": ("ebook", "audio")}
#: What a watch saved before it had one language only (``both``) is read as the first language books are fetched in.
OLD_BOTH = "both"
#: The doubts a work may be in (``doubt_of``), apart from ``part`` (which looks at the series).
DOUBTS = ("adaptation", "companion", "edition", "play", "box", "reissue", "translation")
#: A book announced for a year without a day that a look marked (``~`` and its key in ``AuthorWatch.seen``) is wished
#: when a later look finds its day, also when that day passed in the meantime, up to this long ago.
PENDING_DAYS = 366
#: Why a wish was refused, when the reason passes by itself: the look stops and comes again. Every other code holds
#: the work (``held``) instead of trying it again at every look.
PASSING = ("no_indexers", "too_many_wishes_today")
#: The codes that mean the person may not ask for this book in a library: it goes onto the wish list.
NOT_HERE = ("acquire_not_allowed", "library_not_allowed", "library_kind", "library_required")


def local_today() -> date:
    return clock.now().astimezone(timezones.instance_zone()).date()


def fetch_languages(db: Session, original: str) -> list[str]:
    """The languages books are fetched in, the first one first, with the place of "Originalsprache" as ``original``."""
    return formats.allowed_languages(db, original)


def chosen_language(watch: AuthorWatch) -> str:
    """The language the person chose for the watch, or empty for the first of the languages books are fetched in."""
    return "" if watch.language in ("", OLD_BOTH) else watch.language


def target_of(watch: AuthorWatch, allowed: list[str]) -> str:
    """The one language the watch wishes in, of the languages books are fetched in (``allowed``): the person's choice
    while it is one of them, else the first. Empty when no language is fetched."""
    chosen = chosen_language(watch)
    if chosen and chosen in allowed:
        return chosen
    return allowed[0] if allowed else ""


def language_off(db: Session, watch: AuthorWatch, row: CatalogPerson) -> bool:
    """Whether the person chose a language that books are no longer fetched in (the watch then wishes in the first)."""
    chosen = chosen_language(watch)
    return bool(chosen) and chosen not in fetch_languages(db, row.orig_language)


class Languages:
    """The languages books are fetched in, per original language, asked once per look."""

    def __init__(self, db: Session, watch: AuthorWatch, row: CatalogPerson) -> None:
        self.db, self.watch, self.row = db, watch, row
        self._known: dict[str, list[str]] = {}

    def allowed(self, work: CatalogWork) -> list[str]:
        original = work.orig_language or self.row.orig_language or ""
        if original not in self._known:
            self._known[original] = fetch_languages(self.db, original)
        return self._known[original]

    def target(self, work: CatalogWork) -> str:
        return target_of(self.watch, self.allowed(work))


def work_kinds(watch: AuthorWatch) -> frozenset[str]:
    return frozenset(SURE_KINDS if watch.more else BASE_KINDS)


def kinds_wanted(watch: AuthorWatch) -> tuple[str, ...]:
    return WANTED_KINDS.get(watch.kind, WANTED_KINDS["both"])


def is_announced(edition: dict[str, Any]) -> bool:
    return view_service.upcoming(edition, local_today().isoformat())


def _day(value: Any) -> date | None:
    text = str(value or "")
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def year_only(edition: dict[str, Any]) -> int | None:
    """The year a book is announced for when the sources know no day of it (and it is after this year); else None."""
    if _day(edition.get("released")) is not None:
        return None
    year = edition.get("year")
    return year if isinstance(year, int) and year > local_today().year else None


def is_new(edition: dict[str, Any], *, mode: str, since: date | None, known: bool, pending: bool = False,
           first: date | None = None) -> bool:
    """Whether the work is new in this language: announced (always), or at a look after the first one: out on or after
    the day of the last look and not known to that look, or announced for a year at an earlier look and out now. A
    book that came out on the day the watch was switched on (``first``) was out then: it is not new. A work that came
    out in this language before (``earlier``) is not new in it."""
    if earlier(edition):
        return False
    if is_announced(edition):
        return True
    if mode != "beat" or since is None:
        return False
    released = _day(edition.get("released"))
    if released is None:
        return False
    today = local_today()
    if pending and today - timedelta(days=PENDING_DAYS) <= released <= today:
        return True
    if released == since and since == first:
        return False
    return not known and since <= released <= today


def earlier(edition: dict[str, Any]) -> bool:
    """Whether a record of this language gives a year without a day before the year of the first day the records give:
    the work came out in the language before, the day is that of a later edition (as ``view.upcoming`` reads it)."""
    released = str(edition.get("released") or "")
    year = edition.get("year")
    return (len(released) == 10 and released[:4].isdigit() and isinstance(year, int) and not isinstance(year, bool)
            and year < int(released[:4]))


def latest_announced(edition: dict[str, Any]) -> str:
    """The day (or year) a later edition of a work that is out in this language is announced for (a paperback, a new
    translation), when it is after today; else empty."""
    latest = str(edition.get("latest") or "")
    if not latest and earlier(edition):
        latest = str(edition.get("released") or "")
    today = local_today()
    if _day(latest) is not None:
        return latest if latest > today.isoformat() else ""
    return latest if len(latest) == 4 and latest.isdigit() and int(latest) > today.year else ""


def translated_again(edition: dict[str, Any], kind: str) -> bool:
    """Whether the records of this language say it is translated anew (``Neu übersetzt``, or two translations,
    ``works.marks_of``), for the records of this kind."""
    form = "audio" if kind == "audio" else "text"
    marks = edition.get("marks") if isinstance(edition.get("marks"), list) else []
    return f"{form}:translation" in marks or bool(book_words.translation_word(str(edition.get("title") or "")))


def _is_part(work: CatalogWork, part: tuple[int, int | None]) -> bool:
    """``Teil 2`` of a series whose second volume the work is, is the volume, not a part; ``Teil 1 von 2`` is a part."""
    return part[1] is not None or work.series_pos is None or part[0] != work.series_pos


def _mark_doubt(work: CatalogWork, marks: list[str], forms: set[str] | None) -> str:
    """The doubt the marks of an edition say (``works.marks_of``), of the forms asked for (``text``, ``audio``; all
    when ``forms`` is None)."""
    for mark in marks:
        form, _sep, rest = str(mark).partition(":")
        if forms is not None and form not in forms:
            continue
        code, _sep, place = rest.partition(":")
        if code == "part":
            number, _sep, total = place.partition(":")
            if number.isdigit() and _is_part(work, (int(number), int(total) if total.isdigit() else None)):
                return "part"
        elif code in DOUBTS:
            return code
    return ""


def doubt_of(work: CatalogWork, edition: dict[str, Any], kinds: list[str] | None = None) -> str:
    """Why a work may not be a new book of its own, read with the words every part of nexbooks shares
    (``book_words``) in each of its titles and in what the records of this edition said of it beside the core of their
    title (its marks, ``works.marks_of``; of the ``kinds`` asked for, ``ebook`` the printed records, ``audio`` the
    recordings): ``adaptation``, ``companion``, ``edition``, ``play``, ``part``, ``box``, ``reissue``,
    ``translation``. A new edition or a new translation is read in the title of this language only (an English
    anniversary edition says nothing of the German translation). How long ago the work came out in another language
    plays no part (new is new in the language, ``is_new``). Empty when nothing does."""
    titles = [str(item.get("title")) for item in (work.titles or {}).values()
              if isinstance(item, dict) and item.get("title")]
    for title in titles:
        code = book_words.not_the_work(title)
        if code:
            return code
        if book_words.dramatized_word(title):
            return "play"
        part = book_words.part_of(title) or book_words.bare_part(title)
        if part is not None and _is_part(work, part):
            return "part"
        if book_words.box_word(title) or book_words.more_bundle_word(title) or book_words.package_word(title):
            return "box"
    own = str(edition.get("title") or "")
    if book_words.reissue_word(own):
        return "reissue"
    if book_words.translation_word(own):
        return "translation"
    marks = edition.get("marks")
    if isinstance(marks, list):
        forms = None if kinds is None else {"audio" if kind == "audio" else "text" for kind in kinds}
        code = _mark_doubt(work, [str(mark) for mark in marks], forms)
        if code:
            return code
    return ""


def owned_elsewhere(card: Card, language: str) -> str:
    """A language other than ``language`` the person has the work in (any kind: the English original beside a German
    translation announced), or empty."""
    if card.own is None:
        return ""
    return next(iter(sorted({code for codes in card.own.kinds.values() for code in codes
                             if code and code != language})), "")


# ---- The watch of a person -----------------------------------------------------------------------------------------


def find(db: Session, uid: int, row: CatalogPerson) -> AuthorWatch | None:
    return db.scalar(select(AuthorWatch).where(AuthorWatch.user_id == uid, AuthorWatch.catalog_person_id == row.id))


def count_of(db: Session, uid: int) -> int:
    return db.scalar(select(func.count(AuthorWatch.id)).where(AuthorWatch.user_id == uid)) or 0


class TooMany(Exception):
    """The person watches as many authors as they may."""


def save(db: Session, account: Account, row: CatalogPerson, *, kind: str, language: str,
         more: bool) -> tuple[AuthorWatch, bool]:
    """Makes the person's watch of the author, or changes it. Returns the watch and whether it is new. ``TooMany`` when
    it would be one too many. Commits."""
    moment = clock.now()
    found = find(db, account.id, row)
    if found is None:
        if count_of(db, account.id) >= MAX_WATCHES:
            raise TooMany
        watch = AuthorWatch(user_id=account.id, catalog_person_id=row.id, kind=kind, language=language, more=more,
                            created_at=moment, updated_at=moment)
        db.add(watch)
        try:
            db.flush()
        except Exception:
            # Two requests at the same moment: the other made it, this one changes it.
            db.rollback()
            watch = find(db, account.id, row)
            if watch is None:
                raise
            found = watch
        else:
            db.commit()
            return watch, True
    found.kind, found.language, found.more, found.updated_at = kind, language, more, moment
    db.commit()
    return found, False


@dataclass
class Outcome:
    """What a look did: wishes made, books put on the wish list, and why it stopped before the end (a code)."""

    wished: int = 0
    listed: int = 0
    stopped: str = ""
    retry_at: datetime | None = None
    notes: list[str] = field(default_factory=list)
    #: What the look saw of the catalogue: the keys of the works and, with a ``#``, the numbers of their books of the
    #: sources (a work whose key changed with a new build is still known by its book), and with a ``~`` the keys of
    #: the works announced for a year without a day.
    seen: list[str] = field(default_factory=list)


def _items(db: Session, uid: int, cid: int) -> list[WatchItem]:
    return list(db.scalars(select(WatchItem).where(WatchItem.user_id == uid, WatchItem.catalog_person_id == cid)))


def _drop_stale_claims(db: Session, uid: int, cid: int) -> None:
    db.execute(delete(WatchItem).where(WatchItem.user_id == uid, WatchItem.catalog_person_id == cid,
                                       WatchItem.via == "",
                                       WatchItem.created_at < clock.now() - STALE_CLAIM))
    db.commit()


def _claim(db: Session, uid: int, cid: int, key: str, language: str, kind: str,
           foreign_id: int | None) -> WatchItem | None:
    """Takes the work in this language and kind for this look: the unique row decides, so a look that runs at the same
    moment (a person switching on twice, the beat) never does the same thing twice. None when somebody has it
    already."""
    result = db.execute(sqlite_insert(WatchItem).values(
        user_id=uid, catalog_person_id=cid, work_key=key, language=language, kind=kind, via="", foreign_id=foreign_id,
        created_at=clock.now()).on_conflict_do_nothing(
            index_elements=["user_id", "catalog_person_id", "work_key", "language", "kind"]))
    db.commit()
    if not result.rowcount:
        return None
    return db.scalar(select(WatchItem).where(
        WatchItem.user_id == uid, WatchItem.catalog_person_id == cid, WatchItem.work_key == key,
        WatchItem.language == language, WatchItem.kind == kind))


def _release(db: Session, item: WatchItem) -> None:
    """Takes a claim back (the look could not do it): it is tried again at the next look."""
    db.delete(item)
    db.commit()


def _settle(db: Session, item: WatchItem, via: str, why: str = "", wish_id: int | None = None) -> None:
    item = db.get(WatchItem, item.id) or item
    item.via, item.why, item.wish_id = via, why[:32], wish_id
    db.commit()


def eligible(card: Card, watch: AuthorWatch) -> bool:
    """A work of the catalogue (with its book of the sources) the sources placed for sure, of a kind the watch takes."""
    return (card.work is not None and card.book is not None and card.why == "" and card.kind in work_kinds(watch)
            and card.kind != "unsure")


def _to_do(card: Card, watch: AuthorWatch, edition: dict[str, Any], language: str) -> list[str]:
    """The kinds the watch wants that exist in this edition and that the person does not have in its language."""
    return [kind for kind in kinds_wanted(watch)
            if edition.get(kind) is True and not view_service.has(card, kind, language)]


@dataclass
class Pick:
    card: Card
    language: str
    kinds: list[str]
    day: str
    #: Empty for a wish; else why the work goes onto the wish list instead (``language:en``, or a doubt).
    why: str = ""


def _known(watch: AuthorWatch) -> tuple[set[str], date | None]:
    seen = set(watch.seen or []) if isinstance(watch.seen, list) else set()
    return seen, _day(watch.since)


def _first_day(watch: AuthorWatch) -> date | None:
    """The day the watch made its first look (local), when it made one."""
    if watch.primed_at is None:
        return None
    moment = watch.primed_at if watch.primed_at.tzinfo else watch.primed_at.replace(tzinfo=UTC)
    return moment.astimezone(timezones.instance_zone()).date()


def picks(watch: AuthorWatch, languages: Languages, shown: AuthorView, mode: str,
          items: list[WatchItem]) -> list[Pick]:
    """The works to wish (or to list) in this look. A work new in the language of the watch is wished in it, with the
    kinds that exist in it and that the person does not have, unless it is in doubt (``doubt_of``) or announced for a
    year alone (nothing yet). A work out in that language already whose records announce a new translation in it goes
    onto the wish list (``translation``). A work that has no edition in that language and is new in another of the
    languages books are fetched in goes onto the wish list with that language as its reason."""
    seen, since = _known(watch)
    first = _first_day(watch)
    done = {(item.work_key, item.language, item.kind) for item in items}
    done_books = {(item.foreign_id, item.language, item.kind) for item in items if item.foreign_id}
    found: list[Pick] = []
    for card in shown.cards:
        if not eligible(card, watch):
            continue
        work, book = card.work, card.book
        assert work is not None and book is not None
        titles = work.titles if isinstance(work.titles, dict) else {}
        target = languages.target(work)
        if not target:
            continue
        known = work.key in seen or f"#{book.id}" in seen
        edition = titles.get(target)
        if isinstance(edition, dict):
            if not is_new(edition, mode=mode, since=since, known=known, pending=f"~{work.key}" in seen,
                          first=first):
                # Out in this language already: a later edition announced is no new book. A new translation goes
                # onto the wish list, a paperback or a new edition is left alone.
                later = latest_announced(edition)
                again = [kind for kind in _to_do(card, watch, edition, target) if translated_again(edition, kind)]
                if later and again and (work.key, target, "") not in done:
                    found.append(Pick(card, target, again, later[:10], "translation"))
                continue
            day = str(edition.get("released") or "")[:10]
            if _day(day) is None:
                continue
            kinds = [kind for kind in _to_do(card, watch, edition, target)
                     if (work.key, target, kind) not in done and (book.id, target, kind) not in done_books]
            if not kinds:
                continue
            # The doubt of each kind (a play is a recording's, a part may be the recording's alone); a work the person
            # has in another language is a doubt of every kind (the translation of the original they have).
            other = owned_elsewhere(card, target)
            doubts = {kind: f"own:{other}" if other else doubt_of(work, edition, [kind]) for kind in kinds}
            fine = [kind for kind in kinds if not doubts[kind]]
            doubtful = [kind for kind in kinds if doubts[kind]]
            if fine:
                found.append(Pick(card, target, fine, day))
            if doubtful and (work.key, target, "") not in done:
                found.append(Pick(card, target, doubtful, day, doubts[doubtful[0]]))
            continue
        # Not in the language of the watch at all: new in a language after it, the book waits on the wish list.
        for code in languages.allowed(work):
            other = titles.get(code)
            if code == target or not isinstance(other, dict) or (work.key, code, "") in done:
                continue
            if is_new(other, mode=mode, since=since, known=known, pending=f"~{work.key}" in seen,
                      first=first) and _to_do(card, watch, other, code):
                found.append(Pick(card, code, [], str(other.get("released") or "")[:10], f"language:{code}"))
                break
    return found


def _pending(watch: AuthorWatch, languages: Languages, shown: AuthorView) -> list[str]:
    """The works announced in the language of the watch for a year without a day (``~`` and the key): a later look
    wishes them when it finds their day."""
    seen, _since = _known(watch)
    found: list[str] = []
    for card in shown.cards:
        if not eligible(card, watch) or card.work is None:
            continue
        work = card.work
        edition = (work.titles or {}).get(languages.target(work)) if isinstance(work.titles, dict) else None
        if not isinstance(edition, dict) or _day(edition.get("released")) is not None:
            continue
        year = edition.get("year")
        if year_only(edition) is not None or (f"~{work.key}" in seen and isinstance(year, int)
                                              and year >= local_today().year):
            found.append(f"~{work.key}")
    return found


def _wishes_made_today(db: Session, uid: int) -> int:
    zone = timezones.instance_zone()
    start = clock.now().astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return db.scalar(select(func.count(WatchItem.id)).where(
        WatchItem.user_id == uid, WatchItem.via == "wish", WatchItem.created_at >= start)) or 0


def _share_left(db: Session, account: Account) -> int | None:
    """How many wishes watching may still make for the person today; None for no limit (the operator, or none set)."""
    limit = int(get_settings().wishes_per_day)
    if account.role == OPERATOR or limit <= 0:
        return None
    return max(0, int(limit * WISH_SHARE) - _wishes_made_today(db, account.id))


def _code_of(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return str(detail.get("code", "failed"))


def _wished_by_the_person(db: Session, uid: int, foreign_id: int, kind: str) -> int | None:
    """A wish of the person's for this book and kind, in whatever language (one wish per book and kind)."""
    return db.scalar(select(ProcureWish.id).where(ProcureWish.user_id == uid, ProcureWish.foreign_id == foreign_id,
                                                  ProcureWish.kind == kind).limit(1))


def evaluate(db: Session, watch: AuthorWatch, account: Account, row: CatalogPerson, mode: str) -> Outcome:
    """One look at the catalogue for a watch: wishes (or lists) what is new. ``mode`` is ``prime`` (the first look,
    when the watch is switched on: announced books only), ``settings`` (the watch was changed: announced books only)
    or ``beat`` (a look after a build: announced books and books out since the last look). Does not touch what the
    watch remembers of the look (``run`` does)."""
    out = Outcome()
    uid, cid = account.id, row.id
    _drop_stale_claims(db, uid, cid)
    shown = view_service.view(db, uid, row)
    languages = Languages(db, watch, row)
    keys = [card.work.key for card in shown.cards if card.work is not None][: store.MAX_WORKS]
    books = [f"#{card.book.id}" for card in shown.cards if card.work is not None and card.book is not None]
    out.seen = keys + books[: store.MAX_WORKS] + _pending(watch, languages, shown)[: store.MAX_WORKS]
    items = _items(db, uid, cid)
    follow_days(db, uid, shown, items)
    todo = picks(watch, languages, shown, mode, items)
    if not todo:
        return out
    places = search.targets(db, uid)
    left = _share_left(db, account)
    for pick in todo:
        book = pick.card.book
        work = pick.card.work
        assert book is not None and work is not None
        listed = pick.why if pick.why else ""
        if out.wished + out.listed >= MAX_PER_LOOK:
            out.stopped, out.retry_at = out.stopped or "many", clock.now() + RETRY_SOON
            return out
        for kind in [] if pick.why else pick.kinds:
            if out.wished + out.listed >= MAX_PER_LOOK:
                out.stopped, out.retry_at = out.stopped or "many", clock.now() + RETRY_SOON
                return out
            if kind not in places:
                listed = listed or "-"
                continue
            if left is not None and left <= 0:
                out.stopped, out.retry_at = "share", budget.midnight()
                return out
            claim = _claim(db, uid, cid, work.key, pick.language, kind, book.id)
            if claim is None:
                continue
            had = _wished_by_the_person(db, uid, book.id, kind)
            if had is not None:
                # Wished already, in another language or by hand: one wish per book and kind.
                _settle(db, claim, "held", "wished")
                continue
            try:
                made = wishes.create(db, account, work_id=None, token=book.token, kinds=[kind], language=pick.language,
                                     library_id=None, released=pick.day, background=True)
            except HTTPException as exc:
                db.rollback()
                code = _code_of(exc)
                if code in NOT_HERE:
                    _release(db, claim)
                    listed = listed or ("library" if code == "library_required" else "-")
                    continue
                if code in PASSING:
                    _release(db, claim)
                    out.stopped = "share" if code != "no_indexers" else code
                    out.retry_at = clock.now() + RETRY_NO_INDEXER if code == "no_indexers" else budget.midnight()
                    return out
                logger.info("Watching holds a book it could not wish code=%s", code)
                out.notes.append(code)
                _settle(db, claim, "held", code)
                continue
            result = made[0] if made else None
            if result is not None and result.status == "exists" and result.wish is not None \
                    and result.wish.user_id != uid:
                # Somebody else wished it in the same library: nothing to do now, and the next look tries again (the
                # other person may take their wish away).
                _release(db, claim)
                continue
            if result is not None and result.status == "have":
                _settle(db, claim, "held", "have")
                continue
            mine = result.wish if result is not None and result.status == "created" else None
            _settle(db, claim, "wish", wish_id=mine.id if mine is not None else None)
            if mine is not None:
                out.wished += 1
                if left is not None:
                    left -= 1
        if listed:
            result_ = _list(db, uid, cid, work.key, pick.language, book, "" if listed == "-" else listed)
            if result_ == "full":
                out.stopped, out.retry_at = "list_full", clock.now() + RETRY_LIST_FULL
                return out
            if result_ == "added":
                out.listed += 1
    return out


#: The states of a wish whose day follows the announcement: waiting for its day, or not found after a day that was
#: given up (the book did not come then).
FOLLOWING = ("waiting", "not_found")


def follow_days(db: Session, uid: int, shown: AuthorView, items: list[WatchItem]) -> int:
    """The wishes this watch made follow the day their book is announced for now (a book put back from March to
    September is not searched for from March on; one brought forward is searched for on its new day). Only wishes that
    wait for their day, or found nothing after it, and only to a day the catalogue knows. Returns how many moved."""
    wished = {item.wish_id: item for item in items if item.via == "wish" and item.wish_id is not None}
    if not wished:
        return 0
    works = {card.work.key: card.work for card in shown.cards if card.work is not None}
    today = local_today().isoformat()
    moved = 0
    for wish in db.scalars(select(ProcureWish).where(ProcureWish.id.in_(list(wished)), ProcureWish.user_id == uid,
                                                     ProcureWish.state.in_(FOLLOWING))):
        item = wished[wish.id]
        work = works.get(item.work_key)
        titles = work.titles if work is not None and isinstance(work.titles, dict) else {}
        edition = titles.get(item.language)
        day = str(edition.get("released") or "")[:10] if isinstance(edition, dict) else ""
        if _day(day) is None or day == wish.release_date or (wish.state != "waiting" and day <= today):
            continue
        # (One statement that holds only while the wish is as it was read: a search that started meanwhile wins.)
        result = db.execute(ProcureWish.__table__.update().where(
            ProcureWish.id == wish.id, ProcureWish.state == wish.state,
            ProcureWish.release_date == wish.release_date).values(
                release_date=day, state="waiting", next_try_at=None, updated_at=clock.now()))
        moved += result.rowcount or 0
    db.commit()
    if moved:
        logger.info("Watching moved the day of %d wishes to the day their book is announced for now", moved)
    return moved


def _list(db: Session, uid: int, cid: int, key: str, language: str, book: ForeignBook, why: str) -> str:
    """Puts the book on the person's wish list (once per work and language: the row with the empty kind), with the
    reason it is not wished. Returns ``added``, ``had`` (done before) or ``full``."""
    claim = _claim(db, uid, cid, key, language, "", book.id)
    if claim is None:
        return "had"
    try:
        wishlist.add(db, uid, book)
    except wishlist.Full:
        db.rollback()
        _release(db, claim)
        return "full"
    _settle(db, claim, "list", why)
    return "added"


def run(db: Session, watch: AuthorWatch, account: Account, row: CatalogPerson, mode: str) -> Outcome:
    """A look, and what the watch remembers of it: only a look that went to the end moves the day and the known works
    on (a look that had to stop is made again, from the same day)."""
    out = evaluate(db, watch, account, row, mode)
    watch = db.get(AuthorWatch, watch.id) or watch
    now = clock.now()
    if out.stopped:
        watch.retry_at = out.retry_at or now + RETRY_SOON
        db.commit()
        return out
    watch.retry_at = None
    if mode in ("prime", "beat"):
        watch.seen = out.seen
        watch.since = local_today().isoformat()
        watch.evaluated_for = row.built_at
        if watch.primed_at is None:
            watch.primed_at = now
    db.commit()
    return out


def paused(db: Session) -> bool:
    """Whether the main switch of the automatic work is off: watching then wishes nothing (and looks at nothing)."""
    return not automation.config(db)["enabled"]


def switch_on(db: Session, account: Account, row: CatalogPerson, watch: AuthorWatch) -> Outcome:
    """The look when a person switched a watch on or changed it: when the catalogue is there, the books announced are
    wished at once; when it is not, a build is asked for and the beat makes the first look when it is done. With the
    main switch of the automatic work off nothing is wished (``stopped`` is ``off``): the watch is kept, and its first
    look comes with the beat once the switch is on again."""
    if paused(db):
        return Outcome(stopped="off")
    store.request(db, row)
    db.refresh(row)
    if row.built_at is None:
        return Outcome()
    mode = "prime" if watch.primed_at is None else "settings"
    return run(db, watch, account, row, mode)


# ---- Switching off -------------------------------------------------------------------------------------------------


def remove(db: Session, account: Account, row: CatalogPerson, *, remove_wishes: bool) -> bool:
    """Switches the watch off. With ``remove_wishes`` the wishes it made that nothing has started on yet and the books
    it put on the wish list go too; the others stay (a download that runs is not stopped by it). Returns whether
    there was a watch. Commits."""
    watch = find(db, account.id, row)
    if watch is None:
        return False
    if remove_wishes:
        items = _items(db, account.id, row.id)
        for item in items:
            if item.via == "wish" and item.wish_id is not None:
                wish = db.get(ProcureWish, item.wish_id)
                if wish is not None and wish.user_id == account.id and wish.state in wishes.SEEKING:
                    try:
                        wishes.remove(db, account, wish.id)
                    except HTTPException:
                        # (A wish of a library the person may no longer ask for books in is not theirs to take away.)
                        db.rollback()
            elif item.via == "list" and item.foreign_id is not None:
                db.execute(delete(Wish).where(Wish.user_id == account.id, Wish.foreign_id == item.foreign_id))
        db.execute(delete(WatchItem).where(WatchItem.user_id == account.id, WatchItem.catalog_person_id == row.id))
    db.execute(delete(AuthorWatch).where(AuthorWatch.id == watch.id))
    db.commit()
    return True


# ---- What a watch says about itself --------------------------------------------------------------------------------


@dataclass
class Line:
    key: str
    title: str
    language: str
    #: ``YYYY-MM-DD``, or the year alone for a book announced for a year.
    day: str
    #: ``wished`` (a wish, also one the person made), ``listed`` (on the wish list, ``why`` says why when the person
    #: may ask for books), ``year`` (announced for a year: wished when the day is known), ``held`` (not wished, the
    #: code in ``why``), ``covered`` (somebody else wished it for the library it would go into: the person's watch
    #: wishes it when that wish goes) or ``open`` (announced; the look for it is still to come).
    state: str
    kinds: list[str]
    why: str = ""


@dataclass
class NotYet:
    key: str
    #: The title in the language it is announced in.
    title: str
    #: The language of the watch it is not announced in yet, and the one it is.
    language: str
    announced_in: str
    listed: bool


@dataclass
class Summary:
    announced: list[Line] = field(default_factory=list)
    #: Works announced only in another of the languages books are fetched in.
    not_yet: list[NotYet] = field(default_factory=list)
    wished: int = 0
    listed: int = 0


def _title(titles: dict[str, Any], language: str, fallback: str) -> str:
    item = titles.get(language)
    if isinstance(item, dict) and item.get("title"):
        return str(item["title"])
    return fallback


def _line_of(card: Card, watch: AuthorWatch, edition: dict[str, Any], language: str, mine: list[WatchItem],
             title: str, covered: set[tuple[int, str, str]] | None = None) -> Line | None:
    """The line of a work announced in the language of the watch, or None when the watch has nothing to do with it
    (no kind it wants, or the person has them). ``covered``: the books, languages and kinds somebody else wished for
    the library they would go into for the person."""
    work = card.work
    assert work is not None
    day = str(edition.get("released") or "")[:10]
    kinds = [item.kind for item in mine if item.via == "wish" and item.kind]
    if any(item.via == "wish" for item in mine) or any(item.via == "held" and item.why == "wished" for item in mine):
        return Line(work.key, title, language, day, "wished", kinds)
    listing = next((item for item in mine if item.via == "list"), None)
    if listing is not None:
        return Line(work.key, title, language, day, "listed", [], listing.why)
    held = next((item for item in mine if item.via == "held" and item.why != "have"), None)
    if held is not None:
        return Line(work.key, title, language, day, "held", [], held.why)
    to_do = _to_do(card, watch, edition, language)
    if not to_do:
        return None
    book_id = card.book.id if card.book is not None else None
    if covered and book_id is not None and all((book_id, language, kind) in covered for kind in to_do):
        return Line(work.key, title, language, day, "covered", to_do)
    if _day(day) is None:
        year = edition.get("year")
        return Line(work.key, title, language, str(year) if isinstance(year, int) else "", "year", [])
    return Line(work.key, title, language, day, "open", [])


def summarize(db: Session, watch: AuthorWatch, account: Account, shown: AuthorView) -> Summary:
    """What the watch has to say on the page of the author: the books announced in its language (and what became of
    them), and the ones announced only in another of the languages books are fetched in."""
    row = shown.row
    languages = Languages(db, watch, row)
    seen, _since = _known(watch)
    items = _items(db, account.id, row.id)
    by_key: dict[str, list[WatchItem]] = {}
    for item in items:
        by_key.setdefault(item.work_key, []).append(item)
    out = Summary(wished=sum(1 for item in items if item.via == "wish"),
                  listed=sum(1 for item in items if item.via == "list"))
    covered = _covered(db, account.id, [card.book.id for card in shown.cards if card.book is not None])
    for card in shown.cards:
        if not eligible(card, watch):
            continue
        work = card.work
        assert work is not None
        titles = work.titles if isinstance(work.titles, dict) else {}
        target = languages.target(work)
        if not target:
            continue
        fallback = next((str(item.get("title")) for item in titles.values()
                         if isinstance(item, dict) and item.get("title")), "")
        edition = titles.get(target)
        if isinstance(edition, dict):
            pending = f"~{work.key}" in seen and _day(edition.get("released")) is None
            mine = [item for item in by_key.get(work.key, []) if item.language == target]
            # (``is_announced`` leaves out a later edition already: a day beside an earlier year of the language.)
            if is_announced(edition) or pending:
                line = _line_of(card, watch, edition, target, mine, _title(titles, target, fallback), covered)
                if line is not None:
                    out.announced.append(line)
            elif later := latest_announced(edition):
                # A later edition of a work out in this language: shown when it went onto the wish list only.
                listing = next((item for item in mine if item.via == "list"), None)
                if listing is not None:
                    out.announced.append(Line(work.key, _title(titles, target, fallback), target, later[:10], "listed",
                                              [], listing.why))
            continue
        for code in languages.allowed(work):
            other = titles.get(code)
            if code != target and isinstance(other, dict) and is_announced(other) and _to_do(card, watch, other, code):
                listed = any(item.via == "list" and item.language == code for item in by_key.get(work.key, []))
                out.not_yet.append(NotYet(work.key, _title(titles, code, fallback), target, code, listed))
                break
    out.announced.sort(key=lambda line: (_order(line.day), line.title))
    return out


def _covered(db: Session, uid: int, book_ids: list[int]) -> set[tuple[int, str, str]]:
    """The books of the sources, languages and kinds that somebody else wished for the library the book would go into
    for this person (``search.targets``): the person's watch makes no wish of its own beside it."""
    places = search.targets(db, uid)
    if not book_ids or not places:
        return set()
    found: set[tuple[int, str, str]] = set()
    for foreign_id, language, kind, library_id in db.execute(select(
            ProcureWish.foreign_id, ProcureWish.language, ProcureWish.kind, ProcureWish.library_id).where(
            ProcureWish.foreign_id.in_(book_ids), ProcureWish.user_id != uid, ProcureWish.state != "done")):
        place = places.get(kind)
        if foreign_id is not None and place is not None and library_id == place.id:
            found.add((int(foreign_id), language, kind))
    return found


def _order(day: str) -> str:
    """Days in order, a year alone after the days of that year, nothing last."""
    return day if len(day) == 10 else f"{day}-13" if day else "9999"


def next_announced(db: Session, watches: list[AuthorWatch], accounts: dict[int, Account]) -> dict[int, Line | None]:
    """The next book announced in the watch's language, for each watch, without the person's shelf (the strip of the
    watched authors): the earliest day (a year alone after the days of that year). Works the sources placed for sure,
    of a kind the watch takes, and not in doubt."""
    ids = list({watch.catalog_person_id for watch in watches})
    works = list(db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id.in_(ids)))) if ids else []
    moves: dict[tuple[int, str], str] = {(move.catalog_person_id, move.work_key): move.kind for move in db.scalars(
        select(CatalogMove).where(CatalogMove.catalog_person_id.in_(ids)))} if ids else {}
    people = {row.id: row for row in db.scalars(select(CatalogPerson).where(CatalogPerson.id.in_(ids)))} if ids else {}
    by_author: dict[int, list[CatalogWork]] = {}
    for work in works:
        by_author.setdefault(work.catalog_person_id, []).append(work)
    found: dict[int, Line | None] = {}
    for watch in watches:
        account = accounts.get(watch.user_id)
        row = people.get(watch.catalog_person_id)
        best: Line | None = None
        if account is not None and row is not None:
            languages = Languages(db, watch, row)
            for work in by_author.get(row.id, []):
                moved = moves.get((row.id, work.key))
                kind = moved or work.kind
                if (work.why and not moved) or kind not in work_kinds(watch):
                    continue
                titles = work.titles if isinstance(work.titles, dict) else {}
                code = languages.target(work)
                item = titles.get(code) if code else None
                # (A book in doubt for every kind the watch wants is no next book; a play beside a novel is.)
                if not isinstance(item, dict) or not is_announced(item) or all(
                        doubt_of(work, item, [kind]) for kind in kinds_wanted(watch)):
                    continue
                day = str(item.get("released") or "")[:10]
                if _day(day) is None:
                    day = str(item.get("year") or "")
                if best is None or _order(day) < _order(best.day):
                    best = Line(work.key, str(item.get("title") or ""), code, day, "open", [])
        found[watch.id] = best
    return found


def via_of_wishes(db: Session, ids: list[int]) -> dict[int, str]:
    """The name of the author whose watching made each of these wishes (``{wish id: name}``); a wish nobody's watching
    made is not in it."""
    if not ids:
        return {}
    rows = db.execute(select(WatchItem.wish_id, CatalogPerson.name).join(
        CatalogPerson, CatalogPerson.id == WatchItem.catalog_person_id).where(
            WatchItem.wish_id.in_(ids), WatchItem.via == "wish"))
    return {int(wish_id): name for wish_id, name in rows if wish_id is not None}


def via_of_list(db: Session, uid: int) -> dict[int, tuple[str, str]]:
    """The name of the author whose watching put each book on the person's wish list and why it is not wished
    (``{book of the sources: (name, why)}``)."""
    rows = db.execute(select(WatchItem.foreign_id, CatalogPerson.name, WatchItem.why).join(
        CatalogPerson, CatalogPerson.id == WatchItem.catalog_person_id).where(
            WatchItem.user_id == uid, WatchItem.via == "list", WatchItem.foreign_id.is_not(None)))
    return {int(foreign_id): (name, why or "") for foreign_id, name, why in rows if foreign_id is not None}


def few_sources(db: Session) -> bool:
    """Whether neither of the sources that know most announcements (Audible, Hardcover) may be asked: the catalogue
    then knows few books to come."""
    return not sources_config.ready_sources(db, ("audible", "hardcover"))


# ---- The beat ------------------------------------------------------------------------------------------------------


def _lane_free(db: Session) -> bool:
    return db.scalar(select(func.count(Job.id)).where(Job.kind.in_(("catalog", "cinfo")),
                                                      Job.state.in_(("queued", "running")))) == 0


def budget_left(db: Session) -> bool:
    """Whether every source a build asks has used less than ``BUDGET_SHARE`` of its requests of the day."""
    for source in sources_config.ready_sources(db, store.SOURCES):
        if throttle.state_of(source).day_count >= sources_config.LIMITS[source].per_day * BUDGET_SHARE:
            return False
    return True


def _due_for_a_look(db: Session, now: datetime) -> list[tuple[AuthorWatch, CatalogPerson, Account]]:
    query = (select(AuthorWatch, CatalogPerson, Account)
             .join(CatalogPerson, CatalogPerson.id == AuthorWatch.catalog_person_id)
             .join(Account, Account.id == AuthorWatch.user_id)
             .where(CatalogPerson.built_at.is_not(None), Account.blocked_at.is_(None),
                    or_(AuthorWatch.primed_at.is_(None), AuthorWatch.evaluated_for.is_(None),
                        AuthorWatch.evaluated_for != CatalogPerson.built_at),
                    or_(AuthorWatch.retry_at.is_(None), AuthorWatch.retry_at <= now))
             .order_by(AuthorWatch.id).limit(BEAT_BATCH))
    return [(watch, row, account) for watch, row, account in db.execute(query).all()]


def _ask_for_a_build(db: Session, now: datetime) -> bool:
    """Asks for the build of one watched catalogue that is due, when the lane is free and the sources have room. People
    take turns: of those who have a catalogue due, the one whose watches asked longest ago, and of theirs the watch
    that waited longest. Returns whether one was asked for."""
    if not _lane_free(db) or not store.can_ask(db) or not budget_left(db):
        return False
    rows = db.execute(select(AuthorWatch, CatalogPerson)
                      .join(CatalogPerson, CatalogPerson.id == AuthorWatch.catalog_person_id)
                      .join(Account, Account.id == AuthorWatch.user_id).where(Account.blocked_at.is_(None))).all()
    last: dict[int, datetime] = {}
    for watch, _row in rows:
        if watch.queued_at is not None:
            last[watch.user_id] = max(last.get(watch.user_id, watch.queued_at), watch.queued_at)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    candidates = []
    for watch, row in rows:
        queued = watch.queued_at
        if queued is not None and now - queued < ASK_AGAIN:
            continue
        if not store.due(row) or store.building(db, row):
            continue
        candidates.append((last.get(watch.user_id, epoch), queued or epoch, watch.id, watch, row))
    if not candidates:
        return False
    candidates.sort(key=lambda item: item[:3])
    _turn, _queued, _id, watch, row = candidates[0]
    store.request(db, row)
    db.execute(AuthorWatch.__table__.update().where(AuthorWatch.catalog_person_id == row.id).values(queued_at=now))
    db.commit()
    return True


def tick(db: Session) -> dict[str, int]:
    """One beat: the watches whose catalogue was built since their last look are looked at (a few), then one catalogue
    that is due is asked for. With the main switch of the automatic work off, nothing happens."""
    done = {"looked": 0, "asked": 0}
    if paused(db):
        return done
    now = clock.now()
    for watch, row, account in _due_for_a_look(db, now):
        mode = "prime" if watch.primed_at is None else "beat"
        try:
            run(db, watch, account, row, mode)
        except Exception:
            db.rollback()
            logger.exception("Looking at the catalogue of a watched author failed")
            found = db.get(AuthorWatch, watch.id)
            if found is not None:
                found.retry_at = now + RETRY_NO_INDEXER
                db.commit()
        done["looked"] += 1
    if _ask_for_a_build(db, now):
        done["asked"] = 1
    return done


__all__ = ["MAX_WATCHES", "Outcome", "TooMany", "doubt_of", "fetch_languages", "few_sources", "find", "language_off",
           "next_announced", "owned_elsewhere", "paused", "remove", "run", "save", "summarize", "switch_on",
           "target_of", "tick", "via_of_list", "via_of_wishes"]
