"""The order of changes the iPhone app follows, and the library it fetches page by page.

**One number for every change.** Every work carries ``change_seq``; the triggers of ``models.APP_CHANGE_STATEMENTS``
give it the next number of the one counting row (``app_changes``) whenever the work, one of its editions, files,
chapters or credits, a name it shows or the members of a library it lies in change, and a work that is deleted leaves
its number in ``app_gone``. Nothing in the code has to remember to count: whatever writes the tables is caught.

**Nothing is lost between two looks, however many write at once.** SQLite lets one writer at a time, and the number is
taken inside the writer's transaction, so the numbers become visible in their order. A look reads the newest number
*first* (``latest``) and then asks only for what lies between the device's marker and it: whatever was written after
that first read has a higher number and is in the next look. Reading the number after the works would let a change
that arrived in between be skipped for good.

**Removed, but only to who had it, and once.** ``removed`` names works that are gone, or that the person may no longer
see, and only those the asking device was given (``app_seen``, per device): nobody learns the numbers of books in
libraries they were never in. Once the device asks with a marker past the removal, it has heard of it, and the row goes:
else whoever left a library would hear again of every change in it. A work that went is named whenever its removal lies
after the marker, even when a work with the same number is in ``items`` (the app applies ``removed`` first): numbers are
not given twice since schema 20, and this holds even if one ever was. What went is kept 90 days (``KEEP_GONE``); a
device whose marker is older than what has been forgotten (``floor``), or newer than anything there is (a restored
backup, another server), is told ``marker_too_old`` and starts again from nothing.

**Nothing to see, nothing to learn.** A person who is a member of no library gets ``latest`` 0 and nothing else: the
number counts every write on the server, and it would tell them how much others write.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import clock
from ..errors import error
from ..models import ROLE_BOOK, AppChanges, AppGone, AppSeen, BookFile, Device, Edition, LibraryMember, Setting, Work
from . import works as works_service

logger = logging.getLogger("nexbooks.app")

#: How many works one page of the library holds at most.
MAX_LIMIT = 1000
#: How long a removal is kept for devices that were away.
KEEP_GONE = timedelta(days=90)
INSTALLATION_KEY = "installation_id"
#: Set in a restored database before it is put in place: the newest number the server had before (``carry_over``).
RESTORED_KEY = "app_restored_after"


def latest(db: Session) -> tuple[int, int]:
    """The newest number given out and the floor below which nothing is known any more."""
    row = db.execute(select(AppChanges.seq, AppChanges.floor).where(AppChanges.id == 1)).first()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _between_reads() -> None:
    """Called between reading the newest number and reading the works. Nothing happens here; a test writes here, the
    way another request would."""


@dataclass
class Page:
    views: list[works_service.WorkView] = field(default_factory=list)
    #: Bytes of the files of each edition shown, by edition id.
    sizes: dict[int, int] = field(default_factory=dict)
    removed: list[int] = field(default_factory=list)
    marker: int = 0
    latest: int = 0


def _heard(db: Session, device_id: int, visible: Any, after: int) -> None:
    """Forgets the works whose removal the device has heard of: it asks with a marker at or past the removal. A work
    that is gone (and not gone again after the marker), or that is there but not visible and has not changed since the
    marker. A work with the same number that came back stays: its row is the new one's."""
    gone_later = select(AppGone.work_id).where(AppGone.seq > after)
    unseen = select(Work.id).where(Work.change_seq <= after, Work.id.not_in(visible))
    db.execute(delete(AppSeen).where(
        AppSeen.device_id == device_id,
        or_(and_(AppSeen.work_id.not_in(select(Work.id)), AppSeen.work_id.not_in(gone_later)),
            AppSeen.work_id.in_(unseen))))


def page(db: Session, uid: int, device_id: int, after: int, limit: int) -> Page:
    """What changed for the person after ``after``: at most ``limit`` works and removals, oldest first, and the marker
    to ask with next. ``marker`` below ``latest`` means there is more. 410 ``marker_too_old`` for a marker the server
    cannot answer for (older than what it forgot, or newer than anything it gave out). ``device_id`` is the asking
    device: ``removed`` names only what it was given."""
    if db.scalar(select(LibraryMember.library_id).where(LibraryMember.user_id == uid).limit(1)) is None:
        if after:
            raise error("marker_too_old", "Start again from the beginning.", 410, latest=0)
        db.execute(delete(AppSeen).where(AppSeen.device_id == device_id))
        db.commit()
        return Page()
    top, floor = latest(db)
    if after > top or (after and after < floor):
        raise error("marker_too_old", "Start again from the beginning.", 410, latest=top)
    _between_reads()
    limit = max(1, min(limit, MAX_LIMIT))
    visible = select(Edition.work_id).where(*works_service.visible_edition_clause(uid))
    if after:
        _heard(db, device_id, visible, after)
    else:
        # From nothing: the device forgets everything it does not get now, and so does the server.
        db.execute(delete(AppSeen).where(AppSeen.device_id == device_id))
    found: list[tuple[int, int, bool]] = [
        (int(seq), int(ident), False) for ident, seq in db.execute(
            select(Work.id, Work.change_seq).where(Work.change_seq > after, Work.change_seq <= top,
                                                   Work.id.in_(visible))
            .order_by(Work.change_seq).limit(limit + 1))]
    if after:
        seen = select(AppSeen.work_id).where(AppSeen.device_id == device_id)
        found += [(int(seq), int(ident), True) for ident, seq in db.execute(
            select(Work.id, Work.change_seq).where(Work.change_seq > after, Work.change_seq <= top,
                                                   Work.id.in_(seen), Work.id.not_in(visible))
            .order_by(Work.change_seq).limit(limit + 1))]
        # Every removal of a work the device had, also when a work of the same number is visible now: the app applies
        # ``removed`` before ``items`` and so forgets the old one before it takes the new one.
        found += [(int(seq), int(ident), True) for ident, seq in db.execute(
            select(AppGone.work_id, AppGone.seq).where(AppGone.seq > after, AppGone.seq <= top,
                                                       AppGone.work_id.in_(seen))
            .order_by(AppGone.seq).limit(limit + 1))]
    found.sort()
    taken = found[:limit]
    result = Page(latest=top, marker=taken[-1][0] if len(found) > limit else top)
    shown_ids = [ident for _seq, ident, gone in taken if not gone]
    result.removed = sorted({ident for _seq, ident, gone in taken if gone})
    if shown_ids:
        rows = {work.id: work for work in db.scalars(select(Work).where(Work.id.in_(shown_ids)))}
        ordered = [rows[ident] for ident in shown_ids if ident in rows]
        result.views = works_service.views(db, uid, ordered)
        edition_ids = [view.edition.id for item in result.views for view in item.editions]
        if edition_ids:
            result.sizes = {int(edition): int(size or 0) for edition, size in db.execute(
                select(BookFile.edition_id, func.sum(BookFile.size)).where(
                    BookFile.edition_id.in_(edition_ids), BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None))
                .group_by(BookFile.edition_id))}
        # Whatever was handed out counts as seen, also a work that went between the two reads: a later look names it
        # in ``removed``.
        db.execute(sqlite_insert(AppSeen).values([{"device_id": device_id, "work_id": ident} for ident in shown_ids])
                   .on_conflict_do_nothing())
    db.commit()
    return result


def prune(db: Session, now: datetime | None = None) -> int:
    """Forgets removals older than ``KEEP_GONE`` and raises the floor to the newest of them: a device that last looked
    before it starts again. Returns how many went."""
    cut = int(((now or clock.now()) - KEEP_GONE).timestamp())
    # A revoked device asks nothing any more (its row stays for the list of devices; what it was given goes).
    db.execute(delete(AppSeen).where(AppSeen.device_id.in_(select(Device.id).where(Device.revoked_at.is_not(None)))))
    newest = db.scalar(select(func.max(AppGone.seq)).where(AppGone.gone_at < cut))
    if newest is None:
        db.commit()
        return 0
    old = select(AppGone.work_id).where(AppGone.gone_at < cut)
    db.execute(delete(AppSeen).where(AppSeen.work_id.in_(old), AppSeen.work_id.not_in(select(Work.id))))
    gone = db.execute(delete(AppGone).where(AppGone.gone_at < cut)).rowcount
    db.execute(sqlite_insert(AppChanges).values(id=1, seq=int(newest), floor=int(newest)).on_conflict_do_update(
        index_elements=[AppChanges.id], set_={"floor": func.max(AppChanges.floor, int(newest))}))
    db.commit()
    return int(gone or 0)


def installation_id(db: Session) -> str:
    """A random name of this nexbooks, made once: the app tells two servers apart by it (and a database restored from
    another server keeps the name of that one, which is right: it is that one's library)."""
    found = db.get(Setting, INSTALLATION_KEY)
    if found is not None and isinstance(found.value, str) and found.value:
        return found.value
    db.execute(sqlite_insert(Setting).values(key=INSTALLATION_KEY, value=secrets.token_urlsafe(16))
               .on_conflict_do_nothing())
    db.commit()
    row = db.get(Setting, INSTALLATION_KEY, populate_existing=True)
    assert row is not None
    return str(row.value)


def carry_over(live: Path, restored: Path) -> None:
    """Before a restored database replaces the live one (``backups._swap``): the newest number the live one gave out is
    written into the restored one, which takes it up at the start (``after_restore``). Devices hold markers of the
    live database; without this, a restored one that counts on from a lower number would answer them from a state
    they never had. Nothing happens when the live database knows no numbers."""
    try:
        source = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
        try:
            row = source.execute("SELECT seq FROM app_changes WHERE id = 1").fetchone()
        finally:
            source.close()
    except sqlite3.Error:
        return
    if not row:
        return
    target = sqlite3.connect(restored)
    try:
        target.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (RESTORED_KEY, str(int(row[0]))))
        target.commit()
    finally:
        target.close()


def after_restore(db: Session) -> bool:
    """At the start, after the migrations: a database that was restored (``carry_over``) moves its count past every
    number the server gave out before, and sets the floor there, so every device starts again. Returns whether it
    did."""
    row = db.get(Setting, RESTORED_KEY)
    if row is None:
        return False
    try:
        before = int(row.value)
    except (TypeError, ValueError):
        before = 0
    top, _floor = latest(db)
    jump = max(top, before) + 1
    db.execute(sqlite_insert(AppChanges).values(id=1, seq=jump, floor=jump).on_conflict_do_update(
        index_elements=[AppChanges.id], set_={"seq": jump, "floor": jump}))
    db.execute(delete(AppSeen))
    db.delete(row)
    db.commit()
    logger.info("The order of changes of the app starts again after a restore")
    return True

