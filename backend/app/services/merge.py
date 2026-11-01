"""Editions that turn out to be one: what hangs on them goes to the one that stays.

The scan reads the folders better from time to time (the parts of a book in ``Audio`` and ``CD 2`` folders were once two
books, the album ``Todesrache MP3CD`` once another than ``Todesrache``). A recording that was put together as several
editions then becomes one. The edition that stays is the one a person worked on (progress, marks, a decision, a
correction by hand), else the one with the most files, else the oldest. What the others held is moved to it:

* **Progress and marks** of every person. A place in a recording is a time from the beginning of *all* the parts, so it
  moves by the running time of the parts that now come before the edition it was kept in. Where a person has a place in
  both, the newer one stays; at the same moment, the one further on in the book.
* **Where the edition went** (``EditionMove``): a player that still has the old edition open goes on reporting places
  for it, and they reach the edition that stays, moved like the rest (``services/progress.py``).
* **The books a person finished** (the history of the reading stack): an entry goes with its edition, unless the person
  has one for the edition that stays on the same day already.
* **A decision and the corrections by hand**, where the edition that stays has none of its own.

Nothing of this happens for an edition that still holds files of its own (they belong to another unit and will be put
together by their own turn): it is left as it is.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..models import (
    EDITION_AUDIO,
    ROLE_BOOK,
    BookFile,
    Bookmark,
    Edition,
    EditionMove,
    HistoryEntry,
    MatchEntry,
    OrganizeMove,
    Progress,
)
from .match import overlay as match_overlay

#: The place in a recording: seconds from the beginning of all its parts, as ``services/progress.py`` writes it (not
#: imported
#: from there: that module needs the catalogue).
_TIME = re.compile(r"time:(\d{1,7})(?:\.(\d{1,3}))?")


def seconds_of(locator: str) -> float | None:
    found = _TIME.fullmatch(locator)
    if not found:
        return None
    return int(found.group(1)) + int((found.group(2) or "0").ljust(3, "0")) / 1000


def user_work(db: Session, edition_id: int) -> int:
    """How much a person put into an edition: places, marks, finished books, a decision, corrections by hand. Zero for
    none."""
    edition = db.get(Edition, edition_id)
    if edition is None:
        return 0
    count = db.scalar(select(func.count()).select_from(Progress).where(Progress.edition_id == edition_id)) or 0
    count += db.scalar(select(func.count()).select_from(Bookmark).where(Bookmark.edition_id == edition_id)) or 0
    count += db.scalar(select(func.count()).select_from(HistoryEntry).where(HistoryEntry.edition_id == edition_id)) or 0
    count += db.scalar(select(func.count()).select_from(MatchEntry).where(
        MatchEntry.edition_id == edition_id, MatchEntry.decision != "")) or 0
    count += len(match_overlay.hand_fields(edition.overlay))
    count += 1 if edition.grouped_by == "hand" else 0
    return int(count)


def survivor_of(db: Session, candidates: dict[int, int]) -> int:
    """The edition that stays of several: most worked on by people, then most files, then the oldest. ``candidates``
    maps an edition id to how many of the files of the unit it holds."""
    if len(candidates) == 1:
        return next(iter(candidates))
    work = {ident: user_work(db, ident) for ident in candidates}
    return max(candidates, key=lambda ident: (work[ident], candidates[ident], -ident))


def take_over_by_hand(db: Session, survivor: Edition, others: list[Edition]) -> None:
    """The corrections by hand of the editions that go are laid under the ones of the edition that stays (its own
    win). Done before the overlay is applied to the unit."""
    merged = dict(match_overlay.without_source(survivor.overlay))
    for other in others:
        for name, item in match_overlay.without_source(other.overlay).items():
            merged.setdefault(name, item)
    if merged != match_overlay.without_source(survivor.overlay):
        kept_sources = {name: item for name, item in (survivor.overlay or {}).items()
                        if isinstance(item, dict) and item.get("by") == match_overlay.SOURCE and name not in merged}
        survivor.overlay = {**kept_sources, **merged}


def _moved(locator: str, shift_ms: int) -> str:
    seconds = seconds_of(locator)
    if seconds is None:
        return locator
    return f"time:{seconds + shift_ms / 1000:.3f}"


def _chapter_at(chapters: list[dict[str, Any]], seconds: float) -> tuple[int, int, str]:
    """``(number, count, title)`` of the chapter a time lies in (the first chapter when there are none before it)."""
    number = 1
    for index, chapter in enumerate(chapters, start=1):
        if int(chapter.get("start_ms") or 0) / 1000 <= seconds:
            number = index
        else:
            break
    title = str(chapters[number - 1].get("title") or "") if chapters else ""
    return (number if chapters else 0), len(chapters), title


def shift_places(db: Session, edition: Edition, shift_ms: int, chapters: list[dict[str, Any]]) -> None:
    """Moves the places in a recording by ``shift_ms`` (the parts that now come before it)."""
    if shift_ms <= 0 or edition.kind != EDITION_AUDIO:
        return
    total = max(float(edition.duration or 0), 0.0)
    for place in db.scalars(select(Progress).where(Progress.edition_id == edition.id)):
        moved = _moved(place.locator, shift_ms)
        seconds = seconds_of(moved)
        if seconds is None:
            continue
        place.locator = moved
        place.fraction = min(seconds / total, 1.0) if total else place.fraction
        place.chapter, place.chapter_count, place.chapter_title = _chapter_at(chapters, seconds)
    # The marks move in two steps: all of them out of the way first, then each to its new place. Moved one by one, a
    # mark whose new place is the old place of another one (10 s and 70 s, moved by 60 s) would find it taken.
    marks = [(mark, _moved(mark.locator, shift_ms)) for mark in db.scalars(select(Bookmark).where(
        Bookmark.edition_id == edition.id)) if seconds_of(mark.locator) is not None]
    for mark, _moved_to in marks:
        mark.locator = f"moving:{mark.id}"
    db.flush()
    for mark, moved in marks:
        seconds = seconds_of(moved)
        mark.locator = moved
        mark.fraction = min(seconds / total, 1.0) if total and seconds is not None else mark.fraction
    db.flush()


def relabel_places(db: Session, edition: Edition, chapters: list[dict[str, Any]]) -> int:
    """After the chapters of a recording were cut anew (files that are one chapter put together): the chapter each saved
    place lies in, by the time it keeps. **Nothing else of a place changes** (its time, its fraction, when and where it
    was saved), and the title a bookmark carries is a note of its own. Returns how many places were relabelled."""
    if edition.kind != EDITION_AUDIO:
        return 0
    changed = 0
    for place in db.scalars(select(Progress).where(Progress.edition_id == edition.id)):
        seconds = seconds_of(place.locator)
        if seconds is None:
            continue
        label = _chapter_at(chapters, seconds)
        if label != (place.chapter, place.chapter_count, place.chapter_title):
            place.chapter, place.chapter_count, place.chapter_title = label
            changed += 1
    return changed


def absorb(db: Session, survivor: Edition, other: Edition, shift_ms: int, chapters: list[dict[str, Any]]) -> None:
    """Moves what hangs on ``other`` to ``survivor`` and removes ``other``. The caller has seen that no file is left
    on it."""
    audio = survivor.kind == EDITION_AUDIO
    gone: list[Any] = []
    for place in list(db.scalars(select(Progress).where(Progress.edition_id == other.id))):
        gone.append(place)
        locator = _moved(place.locator, shift_ms) if audio else place.locator
        seconds = seconds_of(locator) if audio else None
        mine = db.get(Progress, (place.user_id, survivor.id))
        if mine is not None and not _later(place, locator, mine):
            continue
        values: dict[str, Any] = {
            "locator": locator, "unit": place.unit, "device_key": place.device_key, "device": place.device,
            "moved_at": place.moved_at, "received_at": place.received_at,
            "fraction": place.fraction, "chapter": place.chapter, "chapter_count": place.chapter_count,
            "chapter_title": place.chapter_title, "pass_done": place.pass_done,
        }
        if seconds is not None:
            total = float(survivor.duration or 0)
            values["fraction"] = min(seconds / total, 1.0) if total else place.fraction
            values["chapter"], values["chapter_count"], values["chapter_title"] = _chapter_at(chapters, seconds)
        if mine is None:
            db.add(Progress(user_id=place.user_id, edition_id=survivor.id, **values))
        else:
            for name, value in values.items():
                setattr(mine, name, value)
    for mark in list(db.scalars(select(Bookmark).where(Bookmark.edition_id == other.id))):
        gone.append(mark)
        locator = _moved(mark.locator, shift_ms) if audio else mark.locator
        if db.scalar(select(Bookmark.id).where(Bookmark.user_id == mark.user_id, Bookmark.edition_id == survivor.id,
                                               Bookmark.locator == locator)) is not None:
            continue
        seconds = seconds_of(locator) if audio else None
        total = float(survivor.duration or 0)
        fraction = min(seconds / total, 1.0) if seconds is not None and total else mark.fraction
        db.add(Bookmark(user_id=mark.user_id, edition_id=survivor.id, locator=locator, fraction=fraction,
                        chapter_title=mark.chapter_title, created_at=mark.created_at, note=mark.note))
    # A player that still has ``other`` open is heard by the edition that stays (and so is one that has an edition open
    # that went into ``other`` before).
    db.merge(EditionMove(old_id=other.id, new_id=survivor.id, shift_ms=int(shift_ms) if audio else 0,
                         created_at=clock.now()))
    for earlier in db.scalars(select(EditionMove).where(EditionMove.new_id == other.id)):
        earlier.new_id = survivor.id
        earlier.shift_ms = int(earlier.shift_ms or 0) + (int(shift_ms) if audio else 0)
    for entry in list(db.scalars(select(HistoryEntry).where(HistoryEntry.edition_id == other.id))):
        if db.scalar(select(HistoryEntry.id).where(HistoryEntry.user_id == entry.user_id,
                                                   HistoryEntry.edition_id == survivor.id,
                                                   HistoryEntry.finished_on == entry.finished_on)) is not None:
            gone.append(entry)
        else:
            entry.edition_id = survivor.id
    # The journal of a tidying names the book its files belonged to.
    db.execute(update(OrganizeMove).where(OrganizeMove.edition_id == other.id).values(edition_id=survivor.id))
    theirs = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == other.id))
    mine_entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == survivor.id))
    if theirs is not None:
        gone.append(theirs)
    if theirs is not None and theirs.decision and mine_entry is not None and not mine_entry.decision:
        for name in ("state", "reason", "proposals", "decision", "decided_by", "decided_at"):
            setattr(mine_entry, name, getattr(theirs, name))
    db.flush()
    for row in gone:
        db.delete(row)
    db.flush()
    db.delete(other)
    db.flush()


def _later(place: Progress, locator: str, mine: Progress) -> bool:
    """Whether the place of the edition that goes (at ``locator`` once moved) wins over the place ``mine`` the person
    has in the edition that stays: the newer one wins; at the same moment, the one further on in the book."""
    if place.moved_at != mine.moved_at:
        return place.moved_at > mine.moved_at
    theirs_at, mine_at = seconds_of(locator), seconds_of(mine.locator)
    if theirs_at is not None and mine_at is not None:
        return theirs_at > mine_at
    return float(place.fraction or 0) > float(mine.fraction or 0)


def follow(db: Session, edition_id: int) -> tuple[int, int] | None:
    """Where an edition that went into another one is now: ``(edition, milliseconds its places move by)``, or None."""
    move = db.get(EditionMove, edition_id)
    if move is None:
        return None
    return int(move.new_id), int(move.shift_ms or 0)


def files_left(db: Session, edition_id: int) -> int:
    """How many book files still name the edition."""
    return int(db.scalar(select(func.count(BookFile.id)).where(
        BookFile.edition_id == edition_id, BookFile.role == ROLE_BOOK)) or 0)

