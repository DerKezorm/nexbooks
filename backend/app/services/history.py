"""The books a person finished: the history behind the reading stack.

**It is the person's own.** Every query here names the person; there is no function that reads the history of
somebody else, so the operator sees no more of another person's reading than anybody does. An entry is shown
only while the person still sees its edition (a library they left, a file that is gone): it stays in the
database and comes back with the edition.

**A pass makes one entry.** An entry is made in two ways, and a pass is entered once:

* *by the rule at the end of the book*: when a report of where the reader is puts the place at the end of an
  e-book (``READ_FRACTION``, 98 %) or of a recording (what is left is less than ``AUDIO_LEFT_SECONDS`` or
  ``AUDIO_LEFT_SHARE`` of its length), the pass is claimed by one statement (``progress.pass_done`` from 0 to 1)
  and entered in the same transaction, so that two devices reporting the end at the same moment make one entry.
  The claim is let go when the place falls back below ``NEW_PASS_FRACTION`` (half of the book): the person reads
  it again, and reaching the end again is a second entry, with its own year;
* *by hand* (``add``): "Finished" on the page of the book, in the reader or in the player. The same day gives
  the same entry (the entry is unique per person, edition and day), so a double click is one entry. The person
  may name a day in the past, for a book finished before nexbooks; such an entry does not claim the pass the person
  is in.

Taking an entry away ("Not finished") keeps the claim of the pass: the rule does not enter the same pass again
at the next report. Only a new pass, or "Finished" by hand, makes a new entry.

A book that is finished, either way, leaves the person's list "for later" (``later``) in the same transaction.

The day is the day in the person's time zone (``services/timezones.py``); nothing here compares the clock of a
device with the server's.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import (
    EDITION_AUDIO,
    HISTORY_LISTENED,
    HISTORY_READ,
    Account,
    Edition,
    HistoryEntry,
    Progress,
    Work,
)
from . import later, timezones, works
from .progress import seconds_of

#: The end of an e-book: this share of it read. The end of a recording: what is left is less than this many seconds or
#: this share of its length, whichever is more.
READ_FRACTION = 0.98
AUDIO_LEFT_SECONDS = 45
AUDIO_LEFT_SHARE = 0.005
#: Back below this share of the book, the pass is over and the next end is a new entry.
NEW_PASS_FRACTION = 0.5
#: A pass is known to begin with a report before this share of the book: only then do its days count.
PASS_START_FRACTION = 0.15

#: How many entries a person may hold. Past this the person is told; nothing is dropped.
MAX_ENTRIES = 5000
LIST_LIMIT = 200
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: The first year a book can have been finished in (a day before it is no day of any reader).
FIRST_YEAR = 1900


def reached_end(edition: Edition, locator: str, fraction: float) -> bool:
    """Whether a place is the end of the edition: for an e-book 98 % of the book, for a recording what is left is under
    45 seconds or half a percent of its length (the credits and the closing words are not listened to by everybody)."""
    if edition.kind == EDITION_AUDIO:
        seconds = seconds_of(locator)
        if seconds is not None and edition.duration > 0:
            return edition.duration - seconds <= max(AUDIO_LEFT_SECONDS, edition.duration * AUDIO_LEFT_SHARE)
        return fraction >= 1 - AUDIO_LEFT_SHARE
    return fraction >= READ_FRACTION


def today(db: Session, uid: int) -> date:
    """Today in the person's time zone."""
    account = db.get(Account, uid)
    zone = timezones.zone_of(account) if account is not None else None
    return clock.now().astimezone(zone).date()


def valid_day(text: str, latest: date) -> date:
    """The day in ``text`` (``YYYY-MM-DD``), when it is a real day between 1900 and ``latest``; 422 else."""
    if not _DAY.match(text or ""):
        raise error("bad_day", "That is not a day.", 422, field="finished_on")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise error("bad_day", "That is not a day.", 422, field="finished_on") from None
    if day.year < FIRST_YEAR or day > latest:
        raise error("bad_day", "That day is not possible.", 422, field="finished_on")
    return day


def _visible(db: Session, uid: int, edition_id: int) -> Edition:
    edition = db.scalar(select(Edition).where(Edition.id == edition_id, *works.visible_edition_clause(uid)))
    if edition is None:
        raise error("not_found", "Not found.", 404)
    return edition


def kind_of(edition: Edition) -> str:
    return HISTORY_LISTENED if edition.kind == EDITION_AUDIO else HISTORY_READ


def _insert(db: Session, uid: int, edition: Edition, day: str, source: str, days: int | None = None) -> bool:
    """Writes the entry unless the person has one for this edition and day, or holds ``MAX_ENTRIES`` already. Whether it
    was written. One statement: the check and the write cannot be separated by another request. ``days`` is how long the
    pass took, when it is known."""
    held = (select(func.count()).select_from(HistoryEntry).where(HistoryEntry.user_id == uid).scalar_subquery())
    source_rows = select(literal(uid), literal(edition.id), literal(kind_of(edition)),
                         literal(day), literal(source),
                         literal(clock.now(), type_=HistoryEntry.created_at.type),
                         literal(days, type_=HistoryEntry.days.type)).where(held < MAX_ENTRIES)
    columns = ["user_id", "edition_id", "kind", "finished_on", "source", "created_at", "days"]
    statement = sqlite_insert(HistoryEntry).from_select(columns, source_rows).on_conflict_do_nothing()
    return db.execute(statement).rowcount == 1


def add(db: Session, uid: int, edition_id: int, finished_on: str | None = None) -> tuple[HistoryEntry, bool]:
    """"Finished" by hand. The entry, and whether this call made it (the same day twice is the one entry)."""
    edition = _visible(db, uid, edition_id)
    here = today(db, uid)
    day = valid_day(finished_on, here + timedelta(days=1)).isoformat() if finished_on else here.isoformat()
    made = _insert(db, uid, edition, day, "hand")
    # The pass is entered, so the rule at the end of the book does not enter it a second time - unless the day is a day
    # gone by: that is a book finished earlier (or a pass of long ago), not the pass the person is in, and it must not
    # take the entry of the pass in progress away (a book read again, at 60 %, still gets its entry at the end).
    if day >= here.isoformat():
        db.execute(update(Progress).where(Progress.user_id == uid, Progress.edition_id == edition.id)
                   .values(pass_done=True))
    row = db.scalar(select(HistoryEntry).where(HistoryEntry.user_id == uid, HistoryEntry.edition_id == edition.id,
                                               HistoryEntry.finished_on == day))
    if row is not None:
        later.finished(db, uid, edition.work_id)
    db.commit()
    if row is None:
        raise error("history_limit", "There are too many books in the reading stack.", 409, max=MAX_ENTRIES)
    return row, made


def remove(db: Session, uid: int, entry_id: int) -> None:
    """"Not finished": the person's own entry goes; 404 for another person's, like one that does not exist."""
    gone = db.execute(delete(HistoryEntry).where(HistoryEntry.id == entry_id, HistoryEntry.user_id == uid)).rowcount
    db.commit()
    if not gone:
        raise error("not_found", "Not found.", 404)


def after_report(db: Session, uid: int, edition: Edition, locator: str, fraction: float) -> bool:
    """Called by ``progress.save`` once a report has been taken as the place that stands: enters the pass when the place
    is the end of the book and the pass is not entered yet, and lets the pass go when the person is back before half the
    book. In the transaction of the caller (not committed here). Whether an entry was made."""
    if reached_end(edition, locator, fraction):
        claimed = db.execute(update(Progress).where(
            Progress.user_id == uid, Progress.edition_id == edition.id, Progress.pass_done.is_(False))
            .values(pass_done=True)).rowcount
        if claimed == 1:
            here = today(db, uid)
            began = db.scalar(select(Progress.pass_started_at).where(Progress.user_id == uid,
                                                                      Progress.edition_id == edition.id))
            account = db.get(Account, uid)
            zone = timezones.zone_of(account) if account is not None else None
            days = max(0, (here - began.astimezone(zone).date()).days) if began is not None else None
            later.finished(db, uid, edition.work_id)
            return _insert(db, uid, edition, here.isoformat(), "auto", days)
    elif fraction < NEW_PASS_FRACTION:
        # Back before half of the book: the pass is over, and the next one begins now (when the place is near the start
        # of the book; further in, nobody can say when it began).
        start = clock.now() if fraction < PASS_START_FRACTION else None
        db.execute(update(Progress).where(Progress.user_id == uid, Progress.edition_id == edition.id,
                                          Progress.pass_done.is_(True))
                   .values(pass_done=False, pass_started_at=start))
        if fraction < PASS_START_FRACTION:
            # A pass whose beginning was not seen begins when the person is near the start of the book.
            db.execute(update(Progress).where(Progress.user_id == uid, Progress.edition_id == edition.id,
                                              Progress.pass_done.is_(False), Progress.pass_started_at.is_(None))
                       .values(pass_started_at=start))
    return False


def listing(db: Session, uid: int, work_id: int | None = None, *, limit: int = LIST_LIMIT,
            offset: int = 0) -> tuple[list[HistoryEntry], int]:
    """The person's own entries, the newest first, for the editions
    they still see; a page of them and how many there are
    in all."""
    filters: list[Any] = [HistoryEntry.user_id == uid, *works.visible_edition_clause(uid)]
    if work_id is not None:
        filters.append(HistoryEntry.work_id == work_id)
    total = db.scalar(select(func.count()).select_from(HistoryEntry)
                      .join(Edition, Edition.id == HistoryEntry.edition_id).where(*filters)) or 0
    rows = db.scalars(select(HistoryEntry).join(Edition, Edition.id == HistoryEntry.edition_id).where(*filters)
                      .order_by(HistoryEntry.finished_on.desc(), HistoryEntry.id.desc()).limit(limit).offset(offset))
    return list(rows), int(total)


@dataclass
class StackEntry:
    entry: HistoryEntry
    edition: Edition
    view: works.WorkView
    #: What the book weighs on the tower: the pages of the e-book that was read (the work's when the edition has none).
    pages: int
    seconds: int


@dataclass
class Year:
    year: int
    entries: list[StackEntry] = field(default_factory=list)

    @property
    def read(self) -> list[StackEntry]:
        return [e for e in self.entries if e.entry.kind == HISTORY_READ]

    @property
    def listened(self) -> list[StackEntry]:
        return [e for e in self.entries if e.entry.kind == HISTORY_LISTENED]

    @property
    def pages(self) -> int:
        return sum(e.pages for e in self.read)

    @property
    def seconds(self) -> int:
        return sum(e.seconds for e in self.listened)

    @property
    def genre(self) -> str:
        """The genre most of the year's books are, ``""`` when none
        of them has one; a tie goes to the first to finish."""
        counts = Counter(e.view.genre for e in self.entries if e.view.genre)
        if not counts:
            return ""
        best = max(counts.values())
        return next(e.view.genre for e in self.entries if e.view.genre and counts[e.view.genre] == best)

    @property
    def thickest(self) -> StackEntry | None:
        """The book that weighs most (pages; a recording by the pages its length comes to), the first of equals."""
        if not self.entries:
            return None
        return max(self.entries, key=lambda e: (e.pages if e.entry.kind == HISTORY_READ else e.view.pages))


def stack(db: Session, uid: int) -> list[Year]:
    """The person's finished books by the year, the oldest year first and inside a year in the order they were finished
    (the first at the bottom of the tower). Only books of editions the person still sees."""
    rows = db.execute(
        select(HistoryEntry, Edition).join(Edition, Edition.id == HistoryEntry.edition_id)
        .where(HistoryEntry.user_id == uid, *works.visible_edition_clause(uid))
        .order_by(HistoryEntry.finished_on, HistoryEntry.id).limit(MAX_ENTRIES)).all()
    if not rows:
        return []
    ids = list(dict.fromkeys(entry.work_id for entry, _edition in rows))
    found = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(ids)))}
    seen = {v.work.id: v for v in works.views(db, uid, [found[i] for i in ids if i in found])}
    years: dict[int, Year] = {}
    for entry, edition in rows:
        view = seen.get(entry.work_id)
        if view is None:
            continue
        year = int(entry.finished_on[:4])
        pages = edition.pages or view.pages if entry.kind == HISTORY_READ else view.pages
        years.setdefault(year, Year(year)).entries.append(
            StackEntry(entry=entry, edition=edition, view=view, pages=pages, seconds=edition.duration))
    return [years[y] for y in sorted(years)]
