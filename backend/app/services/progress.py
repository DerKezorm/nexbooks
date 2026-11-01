"""Where a person is in a book, and the marks they set, in an e-book and in a recording.

**Both belong to the person alone.** Every query here names the person; there is no function that reads the progress or
the bookmarks of somebody else, so the operator sees no more of another person's reading than anybody does (the promise
of the sign-in page and of SECURITY.md). A person reads and writes only the editions of their libraries: for an edition
they cannot see, the answer is the one for an edition that does not exist.

**Two devices, one place: the newest report stays.** A device says where the reader is and *how long ago* the reader was
there (``age``), not what time its clock shows: a clock that is wrong would otherwise keep every other device out for
good. The server counts back from the moment the report arrived (``moved_at = arrival - age``) and keeps the report with
the later ``moved_at``; at a tie the later arrival wins. Checking and writing is one statement, so two devices that
report at the same moment cannot both think they won.

**A recording keeps its own place.** The place in a recording is a time, ``time:8040.5`` (seconds from the beginning
of all its parts), and the unit is ``time``; the place in an e-book is never one. The e-book and the recording of a book
are two editions with two places, and nothing here moves one when the other moves (they may be different texts: an
abridged recording, a radio play, a translation).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import EDITION_AUDIO, Bookmark, Edition, Progress
from . import works

#: A report older than this is taken as if it were this old: an offline device that comes back after a week still
#: reports, but cannot hide behind an age of a year to win over everything.
MAX_AGE = timedelta(days=7)
#: How many marks a person may set in one edition. Past this the person is told, nothing is dropped.
MAX_BOOKMARKS = 500
MAX_LOCATOR = 1000
MAX_NOTE = 500
LIST_LIMIT = 200
#: The place in a recording: seconds, with at most three places after the point.
TIME_LOCATOR = re.compile(r"time:(\d{1,7})(?:\.(\d{1,3}))?")
#: How far past the length the scanner measured a place may lie: a player that reads the length of the stream itself can
#: count a little longer than the tags said.
TIME_SLACK = 60.0


#: What a name of a device may not hold: control characters, the invisible ones that turn the direction of the text or
#: hide in it (U+202E, U+200B, U+2066 ...), line and paragraph separators, and what is no character at all. The name is
#: shown to the person ("On the iPad you are further on"); a name that turns itself around would say something else.
_NOT_IN_A_NAME = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
DEVICE_NAME_MAX = 80


def device_label(text: str) -> str:
    """The name of a device as it is kept beside a place: without control and direction characters, the spaces
    gathered, at most ``DEVICE_NAME_MAX`` characters. Empty when nothing is left."""
    kept = "".join(char for char in text if unicodedata.category(char) not in _NOT_IN_A_NAME)
    return " ".join(kept.split())[:DEVICE_NAME_MAX].strip()


def seconds_of(locator: str) -> float | None:
    """The seconds of a place in a recording, None for any other place."""
    match = TIME_LOCATOR.fullmatch(locator)
    if not match:
        return None
    return int(match.group(1)) + int((match.group(2) or "0").ljust(3, "0")) / 1000


def check_place(edition: Edition, locator: str, unit: str | None = None) -> None:
    """A place fits its edition: a time inside the length for a recording, anything but a time for an e-book; 422
    else."""
    if edition.kind == EDITION_AUDIO:
        seconds = seconds_of(locator)
        if (unit is not None and unit != "time") or seconds is None or not math.isfinite(seconds) \
                or seconds > max(edition.duration, 0) + TIME_SLACK:
            raise error("bad_place", "This place does not fit this recording.", 422)
    elif unit == "time" or locator.startswith("time:"):
        raise error("bad_place", "This place does not fit this book.", 422)


@dataclass
class Report:
    """What a device reports."""

    locator: str
    fraction: float
    unit: str
    chapter: int
    chapter_count: int
    chapter_title: str
    device_key: str
    device: str
    age: float
    #: The place in the words of KOReader, for a report that came from its sync; empty for any other.
    koreader: str = ""
    #: The same for a report from a Kobo (``services/kobo.py``): its word for the place, as a small JSON text.
    kobo: str = ""


def visible_edition(db: Session, uid: int, edition_id: int) -> Edition:
    """The edition, when the person is a member of its library and it has a file on the disk; 404 else."""
    edition = db.scalar(select(Edition).where(Edition.id == edition_id, *works.visible_edition_clause(uid)))
    if edition is None:
        raise error("not_found", "Not found.", 404)
    return edition


def get(db: Session, uid: int, edition_id: int) -> Progress | None:
    visible_edition(db, uid, edition_id)
    return db.get(Progress, (uid, edition_id))


def listing(db: Session, uid: int, work_id: int | None = None, *, limit: int = LIST_LIMIT,
            offset: int = 0) -> tuple[list[tuple[Progress, int]], int]:
    """The person's own places, newest first, each with the work of its edition; only editions they still see. A page of
    them (``limit`` from ``offset``) and how many there are in all."""
    filters = [Progress.user_id == uid, *works.visible_edition_clause(uid)]
    if work_id is not None:
        filters.append(Edition.work_id == work_id)
    total = db.scalar(select(func.count()).select_from(Progress).join(Edition, Edition.id == Progress.edition_id)
                      .where(*filters)) or 0
    query = (select(Progress, Edition.work_id).join(Edition, Edition.id == Progress.edition_id).where(*filters)
             .order_by(Progress.moved_at.desc(), Progress.edition_id).limit(limit).offset(offset))
    return [(row, work) for row, work in db.execute(query).all()], int(total)


#: How far back a question for the places that changed since a moment reaches before that moment: a report counts from
#: the moment it arrived and is written a little later (it may wait for another writer), so a device that asks with the
#: time of its last answer would miss one that was on its way. It gets that one twice instead.
SINCE_OVERLAP = timedelta(seconds=60)


def changed_since(db: Session, uid: int, since: datetime | None) -> list[tuple[Progress, int]]:
    """The person's own places that arrived after ``since`` (all of them without it), with ``SINCE_OVERLAP``, oldest
    arrival first, each with its work; only editions they still see. For the app (``/api/app/1/progress``)."""
    filters = [Progress.user_id == uid, *works.visible_edition_clause(uid)]
    if since is not None:
        filters.append(Progress.received_at > since - SINCE_OVERLAP)
    query = (select(Progress, Edition.work_id).join(Edition, Edition.id == Progress.edition_id).where(*filters)
             .order_by(Progress.received_at, Progress.edition_id))
    return [(row, work) for row, work in db.execute(query).all()]


def reported_edition(db: Session, uid: int, edition_id: int) -> tuple[Edition, int]:
    """The edition a report is for, and by how many milliseconds its place moves: the edition itself, or, for one that
    went into another one while a player had it open (the parts of a recording put together), the edition it went
    into (``merge.follow``), when the person sees that one. 404 else."""
    edition = db.scalar(select(Edition).where(Edition.id == edition_id, *works.visible_edition_clause(uid)))
    if edition is not None:
        return edition, 0
    if db.get(Edition, edition_id) is None:
        from . import merge

        shift = 0
        current: int | None = edition_id
        for _step in range(5):
            moved = merge.follow(db, current) if current is not None else None
            if moved is None:
                break
            current, more = moved
            shift += more
            found = db.scalar(select(Edition).where(Edition.id == current, *works.visible_edition_clause(uid)))
            if found is not None:
                return found, shift
    raise error("not_found", "Not found.", 404)


def _moved(locator: str, shift_ms: int) -> str:
    seconds = seconds_of(locator)
    if seconds is None or not shift_ms:
        return locator
    return f"time:{seconds + shift_ms / 1000:.3f}"


def save(db: Session, uid: int, edition_id: int, report: Report) -> tuple[bool, Progress]:
    """Keeps the report when it is the newest; returns whether it was, and the place that stands afterwards. A report
    for an edition that went into another one counts for that one (``reported_edition``)."""
    edition, shift = reported_edition(db, uid, edition_id)
    if shift and edition.kind == EDITION_AUDIO:
        report.locator = _moved(report.locator, shift)
        seconds = seconds_of(report.locator)
        if seconds is not None and edition.duration:
            report.fraction = min(seconds / edition.duration, 1.0)
    edition_id = edition.id
    check_place(edition, report.locator, report.unit)
    arrival = clock.now()
    age = min(max(report.age, 0.0), MAX_AGE.total_seconds())
    moved = arrival - timedelta(seconds=age)
    values: dict[str, Any] = {
        "locator": report.locator, "fraction": min(max(report.fraction, 0.0), 1.0), "unit": report.unit,
        "chapter": report.chapter, "chapter_count": report.chapter_count, "chapter_title": report.chapter_title,
        "device_key": report.device_key, "device": device_label(report.device), "moved_at": moved,
        "received_at": arrival, "koreader": report.koreader, "kobo": report.kobo,
    }
    # The pass begins with the first report, when that is near the start of the book (a book finished within
    # a few days of its start is one the person liked; a first report at 92 % says nothing of when they began). A
    # place that is only updated keeps the day its pass began.
    from . import history

    began = moved if values["fraction"] < history.PASS_START_FRACTION else None
    insert = sqlite_insert(Progress).values(user_id=uid, edition_id=edition_id, pass_started_at=began, **values)
    # Not the same place twice in a row: the later report wins at a tie, and an older one is dropped, in one statement.
    statement = insert.on_conflict_do_update(
        index_elements=[Progress.user_id, Progress.edition_id], set_=values,
        where=Progress.moved_at <= insert.excluded.moved_at)
    result = db.execute(statement)
    if result.rowcount:
        # The end of the book enters the pass in the history, in the same transaction as the place.
        history.after_report(db, uid, edition, report.locator, values["fraction"])
    db.commit()
    stored = db.get(Progress, (uid, edition_id), populate_existing=True)
    assert stored is not None
    return bool(result.rowcount), stored


def bookmarks(db: Session, uid: int, edition_id: int) -> list[Bookmark]:
    visible_edition(db, uid, edition_id)
    return list(db.scalars(select(Bookmark).where(Bookmark.user_id == uid, Bookmark.edition_id == edition_id)
                           .order_by(Bookmark.fraction, Bookmark.id)))


def add_bookmark(db: Session, uid: int, edition_id: int, locator: str, fraction: float,
                 chapter_title: str, note: str = "") -> tuple[Bookmark, bool]:
    """A mark at a place. Setting the same place twice (a double click, two devices) gives the one mark and no second;
    the second value says whether this call made it.

    The limit is kept by the statement that writes the mark: one ``INSERT ... SELECT`` that selects nothing once the
    person holds ``MAX_BOOKMARKS`` marks in the edition. Counting first and writing after would let a crowd of requests
    at the same moment all see the same count and all write."""
    check_place(visible_edition(db, uid, edition_id), locator)
    held = (select(func.count()).select_from(Bookmark)
            .where(Bookmark.user_id == uid, Bookmark.edition_id == edition_id).scalar_subquery())
    source = select(literal(uid), literal(edition_id), literal(locator),
                    literal(min(max(fraction, 0.0), 1.0)), literal(chapter_title),
                    literal(clock.now(), type_=Bookmark.created_at.type), literal(note)).where(
        held < MAX_BOOKMARKS)
    columns = ["user_id", "edition_id", "locator", "fraction", "chapter_title", "created_at", "note"]
    insert = sqlite_insert(Bookmark).from_select(columns, source).on_conflict_do_nothing()
    made = db.execute(insert).rowcount == 1
    db.commit()
    row = db.scalar(select(Bookmark).where(Bookmark.user_id == uid, Bookmark.edition_id == edition_id,
                                           Bookmark.locator == locator))
    if row is None:
        # Nothing was written and no mark is there at this place: the limit.
        raise error("bookmark_limit", "There are too many bookmarks in this book.", 409, max=MAX_BOOKMARKS)
    return row, made


def change_note(db: Session, uid: int, bookmark_id: int, note: str) -> Bookmark:
    """The note of one of the person's own marks; 404 for another person's, like one that does not exist, and for a mark
    in an edition the person no longer sees."""
    row = db.scalar(select(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.user_id == uid))
    if row is None:
        raise error("not_found", "Not found.", 404)
    visible_edition(db, uid, row.edition_id)
    row.note = note
    db.commit()
    return row


def remove_bookmark(db: Session, uid: int, bookmark_id: int) -> None:
    """The person's own mark; 404 for another person's, like one that does not exist."""
    gone = db.execute(delete(Bookmark).where(Bookmark.id == bookmark_id, Bookmark.user_id == uid)).rowcount
    db.commit()
    if not gone:
        raise error("not_found", "Not found.", 404)
