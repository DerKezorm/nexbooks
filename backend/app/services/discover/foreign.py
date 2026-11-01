"""The books of the sources that are not on a shelf: one row each (``ForeignBook``), public data, the same for
everybody.

A row is made when a list, the gaps in a series, a person's authors or the catalogue search name the book, and says
what the sources say: the title, the authors, the day it came (comes) out, the genre, the series, the description and
the **forms** it is known in (an e-book in a language, a recording with its narrator and running time, abridged or a
play). The same book in two sources is two rows (Apple's and Audible's); the page of a book and the cards of a list
put them together by title and author (``family``).

Nothing here says whether anybody owns the book: that is worked out for the person who asks (``owned.py``).
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ... import clock
from ...models import CatalogWork, ForeignBook, Job, ListEntry, ListFetch, ShelfEntry, ShelfFetch, Wish
from .. import genres, jobs
from ..browse import cache as shelf_cache
from ..readers.images import reduce_cover
from ..readers.limits import COVER_MAX_BYTES
from ..readers.names import authors_compatible, person_key, title_keys
from ..sources import apple, http
from ..sources.model import Candidate
from . import markets, pics

logger = logging.getLogger("nexbooks.discover")

#: A book nobody has named for this long (no list, no series, no search) and nobody put on a list is forgotten.
KEEP_DAYS = 30
#: How long a cover and a description of a book of the sources are kept. They are there to be shown: after this many
#: hours they are gone, and fetched again when a list or a page needs them.
PICTURE_HOURS = 24
MAX_FORMS = 12
#: The sources that say which version a recording is. The others name a running time and no version (a library catalogue
#: lists a shortened reading like any other).
SAY_THE_VERSION = frozenset({"audible"})
MAX_FAMILY = 12
#: Covers asked for after one list: a list shows at most this many.
COVERS_PER_UNIT = 60


def new_token() -> str:
    """The name of a book's page: 18 random characters, so that nobody can count up to a book somebody else's gaps
    made."""
    return secrets.token_urlsafe(14)[:18]


def ref_of(candidate: Candidate) -> str:
    return f"{candidate.source}:{candidate.key}"[:80]


def group_key(title: str, authors: list[str]) -> str:
    """What two rows of one book have in common: the title as far as its subtitle, folded, and the first author."""
    main = title_keys(title)[1]
    return f"{main}|{person_key(authors[0]) if authors else ''}"


def form_of(candidate: Candidate, kind: str) -> dict[str, Any]:
    """The form a candidate is: an e-book (its language, pages) or a recording (variant, running time, narrators)."""
    language = candidate.languages[0] if candidate.languages else ""
    if kind == "audio":
        return {"kind": "audio", "variant": candidate.variant, "language": language,
                "minutes": candidate.runtime_min, "narrators": candidate.narrators[:4], "pages": None,
                "source": candidate.source}
    return {"kind": "ebook", "variant": "", "language": language, "minutes": None, "narrators": [],
            "pages": candidate.pages, "source": candidate.source}


def version_of(form: dict[str, Any]) -> str:
    """The version of a recording as its source states it: empty for a source that does not say one (rows of earlier
    versions carry ``unabridged`` there by default)."""
    return str(form.get("variant") or "") if form.get("source") in SAY_THE_VERSION else ""


def merge_forms(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The forms of a book: a new one replaces the old one of the same kind, variant, language and source."""
    kept = [form for form in old if isinstance(form, dict) and not any(
        (form.get("kind"), form.get("variant"), form.get("language"), form.get("source")) ==
        (item.get("kind"), item.get("variant"), item.get("language"), item.get("source")) for item in new)]
    return (kept + new)[:MAX_FORMS]


def upsert(db: Session, candidate: Candidate, kind: str, *, market: str = "") -> ForeignBook:
    """The row of a book, made or brought up to date. Does not commit. A cover that was fetched stays; the address of
    a new one is kept so that it is fetched."""
    now = clock.now()
    ref = ref_of(candidate)
    authors = [a for a in candidate.authors[:6] if a]
    language = candidate.languages[0] if candidate.languages else ""
    data: dict[str, Any] = {
        "description": candidate.description, "isbns": candidate.isbns[:20], "asin": candidate.asin,
        "year": candidate.year, "subjects": candidate.subjects[:10], "publisher": candidate.publisher,
        "forms": [form_of(candidate, kind)], "markets": [market] if market else [], "pages": candidate.pages}
    if candidate.description:
        data["text_at"] = now.isoformat()
    db.execute(sqlite_insert(ForeignBook).values(
        token=new_token(), ref=ref, title=candidate.title[:300], title_key=title_keys(candidate.title)[0][:300],
        authors=authors, released=candidate.released, language=language,
        genre=genres.genre_of(candidate.subjects), series=candidate.series[:200], series_pos=candidate.series_pos,
        cover_url=candidate.cover_url[:500], cover="", color="", data=data, seen_at=now, created_at=now,
    ).on_conflict_do_nothing(index_elements=["ref"]))
    row = db.scalar(select(ForeignBook).where(ForeignBook.ref == ref))
    assert row is not None
    previous = row.data if isinstance(row.data, dict) else {}
    merged = dict(previous)
    for name in ("description", "asin", "year", "publisher", "pages"):
        if data.get(name) and not previous.get(name):
            merged[name] = data[name]
            if name == "description":
                merged["text_at"] = now.isoformat()
    merged["isbns"] = list(dict.fromkeys([*(previous.get("isbns") or []), *data["isbns"]]))[:20]
    merged["subjects"] = list(dict.fromkeys([*(previous.get("subjects") or []), *data["subjects"]]))[:10]
    merged["markets"] = list(dict.fromkeys([*(previous.get("markets") or []), *data["markets"]]))[:12]
    merged["forms"] = merge_forms(list(previous.get("forms") or []), data["forms"])
    row.data = merged
    row.title, row.title_key, row.authors = candidate.title[:300], title_keys(candidate.title)[0][:300], authors
    row.released = candidate.released or row.released
    row.language = language or row.language
    row.genre = genres.genre_of(candidate.subjects) or row.genre
    row.series = candidate.series[:200] or row.series
    row.series_pos = candidate.series_pos if candidate.series else row.series_pos
    if candidate.cover_url and candidate.cover_url != row.cover_url:
        row.cover_url = candidate.cover_url[:500]
        row.cover = row.cover if row.cover else ""
    row.seen_at = now
    db.flush()
    return row


def family(db: Session, row: ForeignBook) -> list[ForeignBook]:
    """The row and the other rows of the same book (same title as far as the subtitle, an author that fits): Apple's
    e-book and Audible's recording of one book are one book for a card."""
    mine = [a for a in (row.authors or []) if isinstance(a, str)]
    return [row, *named(db, row.title, mine, MAX_FAMILY - 1, skip=row.id)]


def titles_of(row: ForeignBook, rows: list[ForeignBook]) -> list[dict[str, str]]:
    """The titles of a book with their languages: of its rows, and, for a work of the catalogue of an author, the one it
    came out under in each language."""
    data = row.data if isinstance(row.data, dict) else {}
    named_ones = [(item.title, item.language) for item in rows]
    named_ones += [(str(item.get("text") or ""), str(item.get("language") or "")) for item in data.get("titles") or []
                   if isinstance(item, dict)]
    titles: list[dict[str, str]] = []
    for text, language in named_ones:
        text = " ".join((text or "").split())[:300]
        if text and not any(entry["text"] == text and entry["language"] == (language or "") for entry in titles):
            titles.append({"text": text, "language": language or ""})
    return titles


def named(db: Session, title: str, authors: list[str], limit: int = MAX_FAMILY, *, skip: int = 0) -> list[ForeignBook]:
    """The rows of the book of this title and these authors (``family`` for a book that has no row of its own, a work
    of a library)."""
    found: list[ForeignBook] = []
    key = group_key(title, authors)
    main = title_keys(title)[1]
    for other in db.scalars(select(ForeignBook).where(ForeignBook.title_key.startswith(main[:40]),
                                                      ForeignBook.id != skip).limit(200)):
        if group_key(other.title, [a for a in (other.authors or []) if isinstance(a, str)]) == key or (
                title_keys(other.title)[1] == main and any(
                    authors_compatible(a, b) for a in authors for b in (other.authors or []) if isinstance(b, str))):
            found.append(other)
        if len(found) >= limit:
            break
    return found


def forms_of(rows: list[ForeignBook]) -> list[dict[str, Any]]:
    """Every form the rows of one book know, the first row speaking first. A form two sources both know (the same kind,
    variant and language) is one form: the first source's, with what only the other one said filled in."""
    forms: list[dict[str, Any]] = []
    for row in rows:
        data = row.data if isinstance(row.data, dict) else {}
        forms = merge_forms(forms, [f for f in data.get("forms") or [] if isinstance(f, dict)])
    one: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for form in forms:
        key = (form.get("kind"), form.get("variant"), form.get("language"))
        if key not in one:
            one[key] = dict(form)
            continue
        for name in ("minutes", "pages", "narrators"):
            if not one[key].get(name) and form.get(name):
                one[key][name] = form[name]
    return list(one.values())


# ---- Covers ----------------------------------------------------------------------------------------------------

#: The share of a source's daily limit the covers fetched ahead in the background may use together with the shelves
#: and lists (the rest stays for what people ask for), and the share a cover fetched because a person is shown it may
#: reach (the lists need the last tenth).
BACKGROUND_SHARE = 0.5
ON_DEMAND_SHARE = 0.9
#: A cover is fetched anew once it is this old, so that the one a page shows never runs out (it goes at
#: ``PICTURE_HOURS``).
RENEW_HOURS = 20
#: How many books at the front of each section of a shelf, and of each list, are fetched ahead; and how many jobs one
#: look queues at most.
FRONT_PER_SECTION = 8
FRONT_PER_LIST = 20
PLAN_PER_LOOK = 40
#: A source that has no picture for a book is not asked again for this long; a source that failed for its own reasons
#: (a pause, a timeout) is asked again after the shorter time.
MISS_HOURS = 24
MISS_PASSING_MINUTES = 30
PASSING_CODES = frozenset({"timeout", "unreachable", "server", "rate_limited", "busy", "daily_limit", "http_error",
                           "off", "called_off"})
#: Covers fetched for a person at one moment: all together, and for each person. More wait for a place a few seconds.
AT_ONCE = 6
AT_ONCE_EACH = 3
WAIT_FOR_PLACE = 5.0


class CoverBusy(Exception):
    """A cover that is not fetched now but may be later (a place, the source's turn or its day is used up)."""

    def __init__(self, retry_after: int = 5) -> None:
        super().__init__("busy")
        self.retry_after = retry_after


class NoCover(Exception):
    """A book that has no cover to show: no address, none the source gives, or one that was tried and failed."""


class _Flights:
    """One fetch of a cover at a time per book: a second call waits and finds the cover there."""

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.held: dict[int, list[Any]] = {}

    @contextmanager
    def of(self, row_id: int) -> Iterator[None]:
        with self.guard:
            entry = self.held.setdefault(row_id, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self.guard:
                entry[1] -= 1
                if entry[1] == 0:
                    self.held.pop(row_id, None)


_flights = _Flights()
_places = threading.BoundedSemaphore(AT_ONCE)
_people: dict[int, threading.BoundedSemaphore] = {}
_people_guard = threading.Lock()


def _source_of(row: ForeignBook) -> str:
    return row.ref.split(":", 1)[0]


def _data(row: ForeignBook) -> dict[str, Any]:
    return dict(row.data) if isinstance(row.data, dict) else {}


def _recent(value: Any, *, hours: float = 0, minutes: float = 0) -> bool:
    """Whether the time kept as text is less than that long ago (an unreadable or missing one is not)."""
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return False
    return moment is not None and clock.now() - moment < timedelta(hours=hours, minutes=minutes)


def missed_lately(row: ForeignBook) -> bool:
    """Whether a source was asked for the cover of the book lately and gave none: it is not asked again at once."""
    miss = _data(row).get("cover_miss")
    if not isinstance(miss, dict):
        return False
    if miss.get("code") in PASSING_CODES:
        return _recent(miss.get("at"), minutes=MISS_PASSING_MINUTES)
    return _recent(miss.get("at"), hours=MISS_HOURS)


def note_miss(row: ForeignBook, code: str) -> None:
    row.data = {**_data(row), "cover_miss": {"at": clock.now().isoformat(), "code": code[:20]}}


def fresh_cover(row: ForeignBook) -> bool:
    """Whether the book has a cover that is young enough to show for the rest of its day."""
    return bool(row.cover) and _recent(_data(row).get("cover_at"), hours=RENEW_HOURS)


def within_share(source: str, share: float) -> bool:
    """Whether the source was asked less often today than ``share`` of its limit."""
    from ..sources import config, throttle

    limits = config.LIMITS.get(source)
    return limits is not None and throttle.state_of(source).day_count < share * limits.per_day


def fetch_cover(db: Session, row: ForeignBook, *, interactive: bool = False) -> bool:
    """Fetches the cover of a book from its source's own host, through the one way out, and stores it drawn anew.
    False when there is none (no address, the host is not the source's, the source has no such picture, it is no
    picture or too large). ``http.SourceOff`` and ``http.SourceError`` are the caller's."""
    source = _source_of(row)
    urls = [row.cover_url] if row.cover_url else []
    if source == "apple" and urls:
        urls = list(dict.fromkeys([apple.bigger(urls[0]), urls[0]]))
    for url in urls:
        if not http.host_allowed(source, url):
            continue
        answer = http.request(db, source, url, headers={"Accept": "image/*"}, limit=COVER_MAX_BYTES,
                              interactive=interactive, picture=True)
        made = reduce_cover(answer.content) if answer.status_code == 200 else None
        if made is None:
            continue
        webp, color = made
        name = pics.save(webp)
        row.cover, row.color = name, color
        data = _data(row)
        data.pop("cover_miss", None)
        row.data = {**data, "cover_at": clock.now().isoformat()}
        db.execute(update(Wish).where(Wish.foreign_id == row.id).values(cover=name))
        return True
    return False


def _person_places(who: int) -> threading.BoundedSemaphore:
    with _people_guard:
        if len(_people) > 2000:
            _people.clear()
        return _people.setdefault(who, threading.BoundedSemaphore(AT_ONCE_EACH))


@contextmanager
def _place(who: int) -> Iterator[None]:
    """One of the few places for fetching covers for a person, or ``CoverBusy`` after a short wait."""
    mine = _person_places(who)
    if not mine.acquire(timeout=WAIT_FOR_PLACE):
        raise CoverBusy
    try:
        if not _places.acquire(timeout=WAIT_FOR_PLACE):
            raise CoverBusy
        try:
            yield
        finally:
            _places.release()
    finally:
        mine.release()


def cover_for(db: Session, row: ForeignBook, who: int) -> str:
    """The cover of a book for a person who is shown it: the stored one, else fetched now (once for two who ask
    together, within the turn and the day of the source), stored and returned by its name. ``NoCover`` when there is
    none, ``CoverBusy`` when the source or the places are busy. Commits what it stored."""
    if row.cover and pics.path_of(row.cover) is not None:
        return row.cover
    if not row.cover_url or missed_lately(row):
        raise NoCover
    with _place(who), _flights.of(row.id):
        db.refresh(row)
        if row.cover and pics.path_of(row.cover) is not None:
            return row.cover
        if missed_lately(row):
            raise NoCover
        if not within_share(_source_of(row), ON_DEMAND_SHARE):
            raise CoverBusy(60)
        try:
            found = fetch_cover(db, row, interactive=True)
        except http.SourceOff as exc:
            db.rollback()
            raise NoCover from exc
        except http.SourceError as exc:
            db.rollback()
            if exc.code in ("busy", "daily_limit", "rate_limited"):
                raise CoverBusy(int(exc.values.get("retry_after") or 30)) from exc
            note_miss(row, exc.code)
            db.commit()
            raise NoCover from exc
        if not found:
            note_miss(row, "none")
            db.commit()
            raise NoCover
        db.commit()
        shelf_cache.forget()
        return row.cover


def cover_job(db: Session, job: Any) -> None:
    """The handler of the job ``fcover``: the cover of the book ``job.ref`` fetched ahead, or fetched anew when it is
    about to run out. Nothing when a person's look fetched it meanwhile, when the source was asked too often today
    for the background, or when it gave no picture a short while ago."""
    row = db.get(ForeignBook, job.ref)
    if row is None or not row.cover_url:
        return
    with _flights.of(row.id):
        db.refresh(row)
        if fresh_cover(row) or missed_lately(row) or not within_share(_source_of(row), BACKGROUND_SHARE):
            return
        try:
            found = fetch_cover(db, row)
        except http.SourceOff:
            db.rollback()
            return
        except http.SourceError as exc:
            db.rollback()
            # A refused address or a picture too large will not be better the next time; the rest may.
            if exc.code in ("refused", "redirect", "too_large", "bad_answer"):
                note_miss(row, exc.code)
                db.commit()
                raise jobs.GiveUp(exc.code) from exc
            raise jobs.Retry(exc.code) from exc
        if found:
            db.commit()
            # The shelves kept in memory show the cover at the next look.
            shelf_cache.forget()
        else:
            note_miss(row, "none")
            db.commit()


jobs.register("fcover", cover_job)


def enqueue_covers(db: Session, rows: list[ForeignBook]) -> int:
    """A job for each book that has an address for a cover and needs one (none, or one about to run out), at most
    ``COVERS_PER_UNIT``."""
    queued = 0
    for row in rows:
        if not row.cover_url or fresh_cover(row) or missed_lately(row) or queued >= COVERS_PER_UNIT:
            continue
        jobs.enqueue(db, "fcover", ref=row.id)
        queued += 1
    return queued


def plan_front_covers(db: Session) -> int:
    """Queues the covers of the books people see first, in the lane of Discover: the first books of each section of a
    shelf and of each list, in the markets someone looked at in the last fortnight, the foremost of all first. A
    cover that is about to run out is queued again. Per source no more than half of its day (``BACKGROUND_SHARE``) is
    spent, counting what is queued already, and ``PLAN_PER_LOOK`` jobs at most are queued at once. Commits. Returns
    how many were queued."""
    from ..sources import config, throttle

    since = clock.now() - timedelta(days=markets.WANTED_DAYS)
    # A unit is only ever fetched for a market someone looked at, so a market fetched lately is one in use as well.
    wanted: set[str] = set()
    for fetches in (ShelfFetch, ListFetch):
        wanted |= {m for (m,) in db.execute(select(fetches.market).where(
            or_(fetches.wanted_at >= since, fetches.fetched_at >= since)).distinct())}
    front: list[tuple[int, int, int]] = []
    if wanted:
        for rank, fid in db.execute(select(ShelfEntry.rank, ShelfEntry.foreign_id).where(
                ShelfEntry.market.in_(wanted), ShelfEntry.rank <= FRONT_PER_SECTION)):
            front.append((rank, 0, fid))
        for rank, fid in db.execute(select(ListEntry.rank, ListEntry.foreign_id).where(
                ListEntry.market.in_(wanted | {""}), ListEntry.rank <= FRONT_PER_LIST)):
            front.append((rank, 1, fid))
    front.sort()
    ids = list(dict.fromkeys(fid for _rank, _kind, fid in front))
    if not ids:
        return 0
    books = {row.id: row for row in db.scalars(select(ForeignBook).where(ForeignBook.id.in_(ids)))}
    # A book everybody has with a cover of their own in a library needs none from a source.
    from . import view

    own = view.owned_with_cover_by_all(db, [row for row in books.values() if row.cover_url])
    waiting = set(db.scalars(select(Job.ref).where(Job.kind == "fcover", Job.state.in_(("queued", "running")))))
    spent: dict[str, int] = {}
    for row in db.scalars(select(ForeignBook).where(ForeignBook.id.in_(waiting))) if waiting else []:
        spent[_source_of(row)] = spent.get(_source_of(row), 0) + 1
    ready: dict[str, bool] = {}
    queued = 0
    for fid in ids:
        row = books.get(fid)
        if row is None or queued >= PLAN_PER_LOOK or not row.cover_url or fid in own:
            continue
        if fresh_cover(row) or missed_lately(row):
            continue
        source = _source_of(row)
        limits = config.LIMITS.get(source)
        if limits is None or fid in waiting:
            continue
        if source not in ready:
            ready[source] = config.availability(db, source).ready
        if not ready[source]:
            continue
        if throttle.state_of(source).day_count + spent.get(source, 0) >= BACKGROUND_SHARE * limits.per_day:
            continue
        jobs.enqueue(db, "fcover", ref=fid)
        spent[source] = spent.get(source, 0) + 1
        queued += 1
    db.commit()
    return queued


# ---- Forgetting ------------------------------------------------------------------------------------------------


def _older(stamp: Any, cutoff: Any) -> bool:
    """Whether a time kept as text is before ``cutoff``; a missing or unreadable one counts as old."""
    try:
        return not isinstance(stamp, str) or datetime.fromisoformat(stamp) < cutoff
    except ValueError:
        return True


def expire_pictures(db: Session) -> int:
    """The cover and the description of a book of the sources are kept for ``PICTURE_HOURS`` and no longer: they are
    there to be shown. What is older goes (the cover with its colour and its file, the text, and the cover on the
    wish lists); the facts of the book stay (title, authors, series, day, forms). Both come again when a list is
    fetched or a page is opened. A cover or a text of an earlier version, with no time kept, is old. Returns how many
    books lost something."""
    cutoff = clock.now() - timedelta(hours=PICTURE_HOURS)
    touched = 0
    for row in db.scalars(select(ForeignBook).where(or_(
            ForeignBook.cover != "", func.json_extract(ForeignBook.data, "$.description").is_not(None)))):
        data = dict(row.data) if isinstance(row.data, dict) else {}
        changed = False
        if row.cover and _older(data.get("cover_at"), cutoff):
            db.execute(update(Wish).where(Wish.foreign_id == row.id).values(cover=""))
            row.cover, row.color = "", ""
            data.pop("cover_at", None)
            changed = True
        if data.get("description") and _older(data.get("text_at"), cutoff):
            data.pop("description", None)
            data.pop("text_at", None)
            changed = True
        if changed:
            row.data = data
            touched += 1
    db.commit()
    if touched:
        shelf_cache.forget()
    pics.sweep(db.scalars(select(ForeignBook.cover).where(ForeignBook.cover != "")))
    return touched


def prune(db: Session) -> int:
    """Forgets the books nothing names any more: not in a list, not on anybody's wish list, not a work of the catalogue
    of an author and not named for ``KEEP_DAYS``. Their covers go with them. Returns how many books went."""
    cutoff = clock.now() - timedelta(days=KEEP_DAYS)
    listed = select(ListEntry.foreign_id)
    shelved = select(ShelfEntry.foreign_id)
    wished = select(Wish.foreign_id)
    catalogued = select(CatalogWork.foreign_id).where(CatalogWork.foreign_id.is_not(None))
    gone = db.execute(delete(ForeignBook).where(ForeignBook.seen_at < cutoff, ForeignBook.id.not_in(listed),
                                                ForeignBook.id.not_in(shelved), ForeignBook.id.not_in(wished),
                                                ForeignBook.id.not_in(catalogued))).rowcount or 0
    db.commit()
    if gone:
        shelf_cache.forget()
    pics.sweep(db.scalars(select(ForeignBook.cover).where(ForeignBook.cover != "")))
    return int(gone)
