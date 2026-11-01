"""The sync of a Kobo (``routers/kobo.py``): a person puts the e-books they want on a list, a Kobo that was pointed at
nexbooks (``api_endpoint`` in its settings file) fetches that list and keeps it, and the place the person reads at on
the Kobo comes back to nexbooks.

**Only the list, never the library.** A Kobo is told about the editions the person put on their list
(``PUT /api/v1/editions/{id}/kobo``) and about nothing else. Only an e-book with an EPUB that is not copy-protected
can be on it, and the EPUB goes out as it lies in the library: nothing is converted. A book the person does not see
any more (a library left, a file gone, a protection found) is told away at the next sync, and every way to it answers
like a book nobody has.

**What the device holds.** The sync of a Kobo is a conversation: the device sends the token of the last answer it took
and is told what has changed since. nexbooks keeps what it told each connection (``KoboHeld``) and works the difference
out against the list as it is now, every time. An answer is never taken as taken until the next request brings its
number: the token holds the number of the answer (``sync_batch`` of the connection), the rows that answer touched wait
for it, and a device that comes back with an older token (the answer got lost) is told again what it missed. A device
that comes with no token (new, reset) is told everything.

**Names.** The device knows a book by a UUID that nexbooks makes from the person and the edition (``name_of``): the same
book is the same name on every Kobo of the person, and nobody else's. Only the list of the person is searched for it, so
a name that is not on the list is no book.

**The place.** Where a person is in a book comes from the Kobo as a share of the book (in percent) and a location of its
own (a KoboSpan, a chapter file). The share becomes ``fraction`` of the place, by the rule of ``progress.save`` (the
newest report stays), and the location is kept beside it (``Progress.kobo``) for as long as nothing moves the place, so
that the Kobo gets back exactly what it reported. A place from the browser has no location; the Kobo is given its share.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import (
    EDITION_EBOOK,
    ROLE_BOOK,
    BookFile,
    Edition,
    EreaderLink,
    KoboHeld,
    KoboListed,
    Progress,
    Work,
)
from . import brakes, ereaders, progress
from . import works as works_service

logger = logging.getLogger("nexbooks.kobo")

#: How many books one person may have on the list.
MAX_LISTED = 1000
#: How many entries one answer of the sync holds; when there are more the answer says so and the device asks again.
PAGE = 30
#: The longest text of a location of the Kobo that is kept, and the biggest picture an answer for a cover is made of.
MAX_LOCATION = 400
MAX_PICTURE = 2000
#: The names of the books come from this: a constant of nexbooks, not a secret (the secret is the connection's).
NAMESPACE = uuid.UUID("6f6d8d4e-2b0c-4f55-9a43-1c7e6a0a5b21")
#: What the device is given for the things it wants a name for and nexbooks has no use of.
EMPTY_ID = "00000000-0000-0000-0000-000000000001"
#: The language of a book whose language nobody knows, and what every book costs.
NO_LANGUAGE = "en"
PRICE = {"CurrencyCode": "USD", "TotalAmount": 0}
LOVE_PRICE = {"TotalAmount": 0}
#: How a device is offered an EPUB: the same file under both names that programs known to work with a real Kobo use.
FORMATS = ("EPUB3", "EPUB")
TOKEN_HEADER = "x-kobo-synctoken"
UUID_TEXT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: Two syncs of one connection at once (a device that asks again while the first answer is being made) take turns.
_locks = [threading.Lock() for _ in range(64)]


def name_of(uid: int, edition_id: int) -> str:
    """The UUID the Kobo knows the book by."""
    return str(uuid.uuid5(NAMESPACE, f"{uid}/{edition_id}"))


def stamp_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return clock.now()


# --- The list -----------------------------------------------------------------------------------------------------


@dataclass
class Want:
    """A book that is on the list and can be handed out now."""

    edition: Edition
    file: BookFile
    name: str
    added_at: datetime
    #: What tells that the book changed for the device: the work (its change number counts every change of the work, its
    #: editions, files and credits) and the file.
    stamp: str


def _handable(row: BookFile) -> bool:
    protected = bool((row.probe or {}).get("drm"))
    return row.format == "epub" and row.role == ROLE_BOOK and row.missing_at is None and not protected


def wanted(db: Session, uid: int, name: str | None = None) -> dict[int, Want]:
    """The books on the person's list that they still see and that still have an EPUB to hand out, by edition; with
    ``name`` only the one with that name."""
    query = (select(KoboListed.edition_id, KoboListed.uuid, KoboListed.added_at, Edition, Work.change_seq)
             .join(Edition, Edition.id == KoboListed.edition_id).join(Work, Work.id == Edition.work_id)
             .where(KoboListed.user_id == uid, Edition.kind == EDITION_EBOOK,
                    *works_service.visible_edition_clause(uid))
             .order_by(KoboListed.added_at, KoboListed.edition_id))
    if name is not None:
        query = query.where(KoboListed.uuid == name)
    rows = db.execute(query).all()
    files: dict[int, BookFile] = {}
    if rows:
        for row in db.scalars(select(BookFile).where(
                BookFile.edition_id.in_([found[0] for found in rows]), BookFile.role == ROLE_BOOK,
                BookFile.format == "epub", BookFile.missing_at.is_(None)).order_by(BookFile.id)):
            if _handable(row):
                files.setdefault(row.edition_id or 0, row)
    found_books: dict[int, Want] = {}
    for edition_id, text, added_at, edition, change in rows:
        file = files.get(edition_id)
        if file is None:
            continue
        stamp = hashlib.sha256(f"{change}/{file.id}/{file.size}/{file.mtime_ns}".encode()).hexdigest()[:16]
        found_books[edition_id] = Want(edition, file, text, added_at, stamp)
    return found_books


def listed_ids(db: Session, uid: int) -> list[int]:
    """The editions on the person's list that they still see, oldest first (what the page of a book needs to know)."""
    return list(db.scalars(
        select(KoboListed.edition_id).join(Edition, Edition.id == KoboListed.edition_id)
        .where(KoboListed.user_id == uid, *works_service.visible_edition_clause(uid))
        .order_by(KoboListed.added_at, KoboListed.edition_id)))


def _refuse_if_off(db: Session) -> None:
    if not ereaders.allowed(db):
        raise error("ereaders_off", "The operator has switched e-readers off.", 403)


def add(db: Session, uid: int, edition_id: int) -> None:
    """Puts an edition on the person's list. The rights are those of the download: an edition of a library the person is
    not in does not exist. An e-book with an EPUB without copy protection only. Putting it there twice is one entry; a
    book the person removed on the Kobo is sent again."""
    _refuse_if_off(db)
    edition = db.scalar(select(Edition).where(Edition.id == edition_id, *works_service.visible_edition_clause(uid)))
    if edition is None or edition.kind != EDITION_EBOOK:
        raise error("not_found", "Not found.", 404)
    files = list(db.scalars(select(BookFile).where(
        BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK, BookFile.format == "epub",
        BookFile.missing_at.is_(None)).order_by(BookFile.id)))
    if not files:
        raise error("not_epub", "Only a book with an EPUB can go to a Kobo.", 422)
    if not any(_handable(row) for row in files):
        raise error("protected_book", "A copy-protected book cannot go to a Kobo.", 422)
    brakes.take("kobo_list", uid)
    # The limit is kept by the statement that writes the entry (one INSERT ... SELECT that selects nothing once the list
    # is full): counting first and writing after would let a crowd of requests at the same moment all write.
    held = select(func.count()).select_from(KoboListed).where(KoboListed.user_id == uid).scalar_subquery()
    source = select(literal(uid), literal(edition.id), literal(name_of(uid, edition.id)),
                    literal(_now(), type_=KoboListed.added_at.type)).where(held < MAX_LISTED)
    insert = sqlite_insert(KoboListed).from_select(["user_id", "edition_id", "uuid", "added_at"], source)
    db.execute(insert.on_conflict_do_nothing())
    on_list = db.scalar(select(KoboListed.edition_id).where(KoboListed.user_id == uid,
                                                            KoboListed.edition_id == edition.id))
    if on_list is None:
        db.rollback()
        raise error("kobo_list_full", f"The list for the Kobo holds at most {MAX_LISTED} books.", 409, max=MAX_LISTED)
    # A book taken off the Kobo by hand comes back when the person puts it on the list again.
    links = select(EreaderLink.id).where(EreaderLink.account_id == uid)
    db.execute(delete(KoboHeld).where(KoboHeld.link_id.in_(links), KoboHeld.edition_id == edition.id,
                                      KoboHeld.archived.is_(True)))
    db.commit()


def remove(db: Session, uid: int, edition_id: int) -> None:
    """Takes an edition off the list (it is told away from the Kobos at their next sync). Nothing for an edition that is
    not on it. Works while e-readers are switched off: taking a book off is never refused."""
    brakes.take("kobo_list", uid)
    db.execute(delete(KoboListed).where(KoboListed.user_id == uid, KoboListed.edition_id == edition_id))
    # What a Kobo took off by hand has nothing left to be told; the rest is told away by the next sync.
    links = select(EreaderLink.id).where(EreaderLink.account_id == uid)
    db.execute(delete(KoboHeld).where(KoboHeld.link_id.in_(links), KoboHeld.edition_id == edition_id,
                                      KoboHeld.archived.is_(True)))
    db.commit()


# --- The token and the batches ---------------------------------------------------------------------------------------


def make_token(batch: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"v": 1, "b": batch}, separators=(",", ":")).encode()).decode()


def read_token(text: str) -> int | None:
    """The number of the answer a token stands for, or None for no token and for anything that is not one: the device
    is then told everything."""
    if not text or len(text) > 200:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except (ValueError, RecursionError):
        return None
    number = data.get("b") if isinstance(data, dict) else None
    if not isinstance(number, int) or isinstance(number, bool) or number < 0 or number > 2**31:
        return None
    return number


def _acknowledge(db: Session, link_id: int, upto: int) -> None:
    """The device took the answers up to ``upto``: what they told is now what it holds. What a later answer told, and
    the device does not know of, is forgotten and told again."""
    for row in list(db.scalars(select(KoboHeld).where(KoboHeld.link_id == link_id))):
        if row.batch:
            if row.batch <= upto:
                if row.removing:
                    db.delete(row)
                    continue
                if row.next_stamp is not None:
                    row.stamp = row.next_stamp
                if row.next_state_stamp is not None:
                    row.state_stamp = row.next_state_stamp
            row.next_stamp = row.next_state_stamp = None
            row.removing = False
            row.batch = 0
        if not row.stamp and not row.archived:
            db.delete(row)


# --- What is told ----------------------------------------------------------------------------------------------------


def _place_stamp(place: Progress | None) -> str:
    if place is None:
        return "-"
    return hashlib.sha256(f"{place.moved_at.isoformat()}/{place.fraction:.6f}".encode()).hexdigest()[:16]


def _entitlement(name: str, created: datetime, modified: datetime, removed: bool) -> dict[str, Any]:
    """A book has one name for every field that asks for one, and it stays when the book changes: a change is told by
    the entry and its ``LastModified``, and the device may address a book by any of the fields."""
    return {
        "Accessibility": "Full", "ActivePeriod": {"From": stamp_text(created)}, "Created": stamp_text(created),
        "CrossRevisionId": name, "Id": name, "IsRemoved": removed, "IsHiddenFromArchive": False, "IsLocked": False,
        "LastModified": stamp_text(modified), "OriginCategory": "Imported", "RevisionId": name, "Status": "Active",
    }


def _date_of(text: str, fallback: datetime) -> str:
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text or "")
    if match:
        try:
            moment = datetime(int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1), tzinfo=UTC)
            return stamp_text(moment)
        except ValueError:
            pass
    return stamp_text(fallback)


def _words(text: str, limit: int) -> str:
    """A text for the device: no control characters, at most ``limit`` characters."""
    return "".join(char for char in text if char >= " " and char != "\x7f" or char in "\n\t")[:limit]


@dataclass
class About:
    """What the person sees of a book besides the edition: who wrote it, its series and its description."""

    authors: list[str]
    series: str = ""
    number: float | None = None
    description: str = ""


def _plain_metadata(name: str) -> dict[str, Any]:
    """What the metadata of every book holds, of a book that is told away as well: its name, and nothing of what it
    is."""
    return {
        "Categories": [EMPTY_ID], "CoverImageId": name, "CrossRevisionId": name,
        "CurrentDisplayPrice": PRICE, "CurrentLoveDisplayPrice": LOVE_PRICE, "EntitlementId": name, "ExternalIds": [],
        "Genre": EMPTY_ID, "IsEligibleForKoboLove": False, "IsInternetArchive": False, "IsPreOrder": False,
        "IsSocialEnabled": True, "Language": NO_LANGUAGE, "PhoneticPronunciations": {}, "RevisionId": name,
        "Title": name, "WorkId": name,
    }


def _metadata(want: Want, root: str, about: About) -> dict[str, Any]:
    edition, name = want.edition, want.name
    data: dict[str, Any] = {
        **_plain_metadata(name),
        "Description": _words(about.description or edition.description, 4000),
        "DownloadUrls": [{"DrmType": "None", "Format": form, "Size": int(want.file.size),
                          "Url": f"{root}/download/{name}.epub", "Platform": "Generic"} for form in FORMATS],
        "PublicationDate": _date_of(edition.published, want.added_at),
        "Publisher": {"Imprint": "", "Name": _words(edition.publisher, 200)},
        "Title": _words(edition.title, 300),
        "ContributorRoles": [{"Name": _words(who, 200), "Role": "Author"} for who in about.authors],
        "Contributors": [_words(who, 200) for who in about.authors],
    }
    language = re.sub(r"[^a-z]", "", (edition.language or "").lower())[:2]
    if len(language) == 2:
        data["Language"] = language
    if edition.subtitle:
        data["Subtitle"] = _words(edition.subtitle, 300)
    if about.series:
        data["Series"] = {"Id": str(uuid.uuid5(NAMESPACE, f"series/{about.series}")), "Name": _words(about.series, 200),
                          "Number": int(about.number or 0), "NumberFloat": float(about.number or 0.0)}
    return data


def _is_text(text: str) -> bool:
    """Whether a text can go out as UTF-8: a lone surrogate (``\\ud800`` in JSON) cannot, and an answer that holds one
    cannot be made at all."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _location(place: Progress | None) -> dict[str, Any] | None:
    if place is None or not place.kobo:
        return None
    try:
        data = json.loads(place.kobo)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    value, kind, source = data.get("v"), data.get("t"), data.get("s")
    if not all(isinstance(item, str) and _is_text(item) for item in (value, kind, source)):
        # Also a row from before the check at the writing: a text no answer can carry would make the whole sync fail.
        return None
    return {"Value": value, "Type": kind, "Source": source, "Content": data.get("c")}


def _percent(value: float) -> int | float:
    """A share as the device is told it: a whole number without a fraction."""
    return int(value) if float(value).is_integer() else value


def reading_state(name: str, place: Progress | None, added_at: datetime) -> dict[str, Any]:
    """The place of the person in a book, in the words of the Kobo. A book nobody has read yet carries the moment it
    was put on the list and never the moment of the answer: it is told again to a device that lost its token, and a
    state that looks new could be taken over what the person read on that device."""
    created = stamp_text(added_at)
    moment = stamp_text(place.moved_at) if place is not None else created
    fraction = float(place.fraction) if place is not None else 0.0
    status = "Finished" if fraction >= 0.99 else "Reading" if fraction > 0 else "ReadyToRead"
    bookmark: dict[str, Any] = {"LastModified": moment}
    if place is not None and fraction > 0:
        bookmark["ProgressPercent"] = _percent(round(fraction * 100, 4))
        found = _location(place)
        if found is not None:
            content = found.pop("Content", None)
            bookmark["ContentSourceProgressPercent"] = _percent(content) if isinstance(content, (int, float)) else 0
            bookmark["Location"] = found
    return {
        "EntitlementId": name, "Created": created, "LastModified": moment, "PriorityTimestamp": moment,
        "StatusInfo": {"LastModified": moment, "Status": status, "TimesStartedReading": 1 if fraction > 0 else 0},
        "Statistics": {"LastModified": moment}, "CurrentBookmark": bookmark,
    }


def _about(db: Session, uid: int, wants: list[Want]) -> dict[int, About]:
    """For each edition, what the person sees of it besides the edition itself."""
    ids = {want.edition.work_id for want in wants}
    found: dict[int, About] = {}
    for view in works_service.views(db, uid, list(db.scalars(select(Work).where(Work.id.in_(ids))))):
        for item in view.editions:
            names = item.authors or [name for _ident, name in view.authors]
            found[item.edition.id] = About(names, view.series_name, view.series_pos, view.description)
    return found


def _book_entries(db: Session, uid: int, wants: list[Want],
                  root: str) -> dict[int, tuple[dict[str, Any], dict[str, Any]]]:
    """The entitlement and the metadata of each of these books."""
    about = _about(db, uid, wants)
    found: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for want in wants:
        found[want.edition.id] = (_entitlement(want.name, want.added_at, _now(), False),
                                  _metadata(want, root, about.get(want.edition.id, About([]))))
    return found


# --- The sync --------------------------------------------------------------------------------------------------------


@dataclass
class Answer:
    entries: list[dict[str, Any]]
    token: str
    more: bool


def sync(db: Session, uid: int, link_id: int, token_text: str, root: str) -> Answer:
    """What the device has to be told now: the entries, the token of this answer, and whether there is more (the device
    asks again at once). Everything is worked out from the list as it is at this moment."""
    with _locks[link_id % len(_locks)]:
        return _sync(db, uid, link_id, token_text, root)


def _sync(db: Session, uid: int, link_id: int, token_text: str, root: str) -> Answer:
    # Read anew, whatever this session read before it waited for its turn: the answer of the sync before has moved the
    # count on, and counting on from an older number would hand out one number twice (or take it back).
    link = db.get(EreaderLink, link_id, populate_existing=True)
    if link is None:
        raise error("not_found", "Not found.", 404)
    seen = read_token(token_text)
    if seen is None:
        db.execute(delete(KoboHeld).where(KoboHeld.link_id == link_id))
        seen = 0
    else:
        seen = min(seen, link.sync_batch)
        _acknowledge(db, link_id, seen)
    db.flush()
    want = wanted(db, uid)
    held = {row.edition_id: row for row in db.scalars(select(KoboHeld).where(KoboHeld.link_id == link_id))}
    places = {row.edition_id: row for row in db.scalars(select(Progress).where(
        Progress.user_id == uid, Progress.edition_id.in_(list(want) or [0])))}
    # In the order they are told: what goes, what comes, what changed.
    removals = sorted(edition_id for edition_id, row in held.items() if not row.archived and edition_id not in want)
    for edition_id, row in list(held.items()):
        if row.archived and edition_id not in want:
            db.delete(row)
            del held[edition_id]
    news = [edition_id for edition_id in want if edition_id not in held]
    changes = [edition_id for edition_id, row in held.items()
               if edition_id in want and not row.archived and row.stamp != want[edition_id].stamp]
    states = [edition_id for edition_id, row in held.items()
              if edition_id in want and not row.archived and edition_id not in changes
              and row.state_stamp != _place_stamp(places.get(edition_id))]
    todo = [("gone", edition_id) for edition_id in removals] + [("new", edition_id) for edition_id in news] \
        + [("changed", edition_id) for edition_id in changes] + [("state", edition_id) for edition_id in states]
    more = len(todo) > PAGE
    todo = todo[:PAGE]
    if not todo:
        db.commit()
        return Answer([], make_token(seen), False)
    batch = link.sync_batch + 1
    link.sync_batch = batch
    made = _book_entries(db, uid, [want[edition_id] for kind, edition_id in todo if kind in ("new", "changed")], root)
    entries: list[dict[str, Any]] = []
    for kind, edition_id in todo:
        row = held.get(edition_id)
        if kind == "gone":
            assert row is not None
            entries.append({"ChangedEntitlement": {"BookEntitlement": _entitlement(row.uuid, _now(), _now(), True),
                                                   "BookMetadata": _plain_metadata(row.uuid)}})
            row.removing, row.batch = True, batch
            continue
        book = want[edition_id]
        place = places.get(edition_id)
        state = reading_state(book.name, place, book.added_at)
        if kind in ("new", "changed"):
            entitlement, metadata = made[edition_id]
            body = {"BookEntitlement": entitlement, "BookMetadata": metadata}
            if kind == "new":
                entries.append({"NewEntitlement": {**body, "ReadingState": state}})
                row = KoboHeld(link_id=link_id, edition_id=edition_id, uuid=book.name)
                db.add(row)
            else:
                entries.append({"ChangedEntitlement": body})
                if row.state_stamp != _place_stamp(place):
                    entries.append({"ChangedReadingState": {"ReadingState": state}})
            assert row is not None
            row.next_stamp, row.next_state_stamp, row.batch = book.stamp, _place_stamp(place), batch
            continue
        assert row is not None
        entries.append({"ChangedReadingState": {"ReadingState": state}})
        row.next_state_stamp, row.batch = _place_stamp(place), batch
    db.commit()
    return Answer(entries, make_token(batch), more)


# --- Asking for one book ---------------------------------------------------------------------------------------------


def book_named(db: Session, uid: int, name: str) -> Want | None:
    """The book of the person's list with this name that they can still be handed, or None (a name nobody has, a book
    not on the list, a book they do not see any more: all the same)."""
    if not UUID_TEXT.match(name):
        return None
    return next(iter(wanted(db, uid, name).values()), None)


def metadata_of(db: Session, uid: int, want: Want, root: str) -> dict[str, Any]:
    about = _about(db, uid, [want]).get(want.edition.id, About([]))
    return _metadata(want, root, about)


def state_of(db: Session, uid: int, want: Want) -> dict[str, Any]:
    return reading_state(want.name, db.get(Progress, (uid, want.edition.id)), want.added_at)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def put_state(db: Session, uid: int, link_id: int, want: Want, body: Any) -> bool:
    """A reading state the Kobo reported. Returns whether the body held a state for this book; a state of another book,
    or no state, changes nothing. One state that names no book is this book's: the address names it. The place is kept
    by the rule of ``progress.save``."""
    states = body.get("ReadingStates") if isinstance(body, dict) else None
    if not isinstance(states, list):
        logger.info("A report of a Kobo held no reading states")
        return False
    named = [item for item in states if isinstance(item, dict) and item.get("EntitlementId") == want.name]
    state = next(iter(named), None)
    if state is None and len(states) == 1 and isinstance(states[0], dict) and states[0].get("EntitlementId") is None:
        state = states[0]
    if state is None:
        logger.info("A report of a Kobo held no reading state of this book")
        return False
    bookmark = state.get("CurrentBookmark")
    status = state.get("StatusInfo")
    finished = isinstance(status, dict) and status.get("Status") == "Finished"
    percent = _number(bookmark.get("ProgressPercent")) if isinstance(bookmark, dict) else None
    if percent is None and finished:
        percent = 100.0
    if percent is None:
        logger.info("A report of a Kobo held no share of the book")
        return True
    fraction = 1.0 if finished else min(max(percent / 100.0, 0.0), 1.0)
    kept = ""
    location = bookmark.get("Location") if isinstance(bookmark, dict) else None
    if isinstance(location, dict) and not finished:
        value, kind, source = location.get("Value"), location.get("Type"), location.get("Source")
        content = _number(bookmark.get("ContentSourceProgressPercent")) if isinstance(bookmark, dict) else None
        if all(isinstance(item, str) and item for item in (value, kind, source)) \
                and len(value) + len(kind) + len(source) <= MAX_LOCATION and all(
                    _is_text(item) for item in (value, kind, source)) and not any(
                    ord(char) < 32 for item in (value, kind, source) for char in item):
            kept = json.dumps({"v": value, "t": kind, "s": source, "c": content if content is not None else 0.0},
                              separators=(",", ":"))
    link = db.get(EreaderLink, link_id)
    label = progress.device_label(link.name if link is not None else "") or "Kobo"
    stored_ok, place = progress.save(db, uid, want.edition.id, progress.Report(
        locator="", fraction=fraction, unit="chapter", chapter=0, chapter_count=0, chapter_title="",
        device_key=f"kb-{link_id}", device=label, age=0.0, kobo=kept))
    if stored_ok:
        # The place that stands is the device's own: it is not told its own place back.
        db.execute(update(KoboHeld).where(KoboHeld.link_id == link_id, KoboHeld.edition_id == want.edition.id)
                   .values(state_stamp=_place_stamp(place), next_state_stamp=None))
        db.commit()
    return True


def archive(db: Session, uid: int, link_id: int, name: str) -> bool:
    """The person took the book off the device by hand: it is not sent to this connection again until the person puts it
    on the list anew. Returns whether the name is a book of the list."""
    if not UUID_TEXT.match(name):
        return False
    edition_id = db.scalar(
        select(KoboListed.edition_id).join(Edition, Edition.id == KoboListed.edition_id)
        .where(KoboListed.user_id == uid, KoboListed.uuid == name, *works_service.visible_edition_clause(uid)))
    if edition_id is None:
        return False
    with _locks[link_id % len(_locks)]:
        # Read when it is this request's turn: two requests for one book at the same moment both found no row before
        # the lock, and the second one wrote a row that was there already.
        row = db.get(KoboHeld, (link_id, edition_id), populate_existing=True)
        if row is None:
            db.add(KoboHeld(link_id=link_id, edition_id=edition_id, uuid=name, archived=True))
        else:
            row.archived, row.removing, row.stamp, row.state_stamp = True, False, "", ""
            row.next_stamp = row.next_state_stamp = None
            row.batch = 0
        db.commit()
    return True


# --- Covers ----------------------------------------------------------------------------------------------------------


def cover_jpeg(db: Session, uid: int, want: Want, width: int, height: int) -> tuple[bytes, str]:
    """The cover of the book as a JPEG no bigger than ``width`` by ``height`` (a picture is never enlarged), and the tag
    that names it. 404 for a book without a cover."""
    from PIL import Image

    from . import covers

    name = works_service.cover_of(db, uid, want.edition.work_id)
    path = covers.path_of(name)
    if path is None:
        raise error("not_found", "Not found.", 404)
    width, height = min(max(width, 16), MAX_PICTURE), min(max(height, 16), MAX_PICTURE)
    try:
        with Image.open(Path(path)) as picture:
            picture = picture.convert("RGB")
            picture.thumbnail((width, height))
            out = io.BytesIO()
            picture.save(out, "JPEG", quality=85)
    except (OSError, ValueError):
        raise error("not_found", "Not found.", 404) from None
    return out.getvalue(), f'"{name[:20]}-{width}x{height}"'
