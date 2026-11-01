"""The wrong book: a person who looks after a library sees on the page of a book that what nexbooks fetched for them is
not the book wished, and sends it away in one step.

* **What happens**: the files of the edition that are on the disk go into the trash of the library (they can be
  brought back there; a file that is gone from the disk is named and does not hold the book back), the release that
  delivered them is blocked for the book wished, a line in the history says so, the edition is gone from the shelf, and
  a wish for the same kind of book looks for another release at once. **All or nothing**: when the files cannot be
  moved, nothing is blocked, written or removed.
* **What people had in it**: the place, the bookmarks and the finished passes of everybody who read or heard the edition
  go with it by the foreign keys; they are written down first (``kept.py``), kept with the trash entry and given back
  to the edition the files make again when they are taken out of the trash.
* **What it is for**: an edition that came through the fetching (a release from an indexer, not a book a person
  uploaded: that one is theirs). Anything else is ``409 not_fetched``.
* **Who**: a member with ``manage`` in the library of the edition, in a browser; another member of the library gets
  ``403``, a person who is no member ``404``. The files are the edition's own: they are moved the way an upgrade moves
  what it replaces (``trash.put``: no link on the way, nothing outside the library, nothing written over).
* **Back**: the files come back from the trash like any other entry; the block is lifted on the blocklist.
* **The new wish** needs a book to be wished. When the edition was the only one of its work, the work goes with it, and
  the wish is made for the book of the sources the release was fetched for; a book that nobody can name any more gets no
  wish (the answer says so).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...errors import error
from ...models import ROLE_BOOK, ROLE_SIDECAR, Account, BookFile, Download, Edition, Library, ProcureWish, Work
from .. import catalog, libraries
from ..readers import names
from . import blocklist, hidden, history, kept, trash, wishes

logger = logging.getLogger("nexbooks.procure")

#: Two requests for one edition never run together: the second finds the files gone.
_lock = threading.Lock()


def delivery_of(db: Session, edition_id: int) -> Download | None:
    """The newest download that was filed as this edition and came from a release (an upload is not one)."""
    return db.scalar(select(Download).where(
        Download.edition_id == edition_id, Download.state == "imported", Download.protocol != "upload")
        .order_by(Download.id.desc()).limit(1))


def deliveries_of(db: Session, edition_ids: list[int]) -> dict[int, Download]:
    """``delivery_of`` for several editions at once (the newest per edition)."""
    out: dict[int, Download] = {}
    if not edition_ids:
        return out
    for row in db.scalars(select(Download).where(
            Download.edition_id.in_(edition_ids), Download.state == "imported", Download.protocol != "upload")
            .order_by(Download.id)):
        if row.edition_id is not None:
            out[row.edition_id] = row
    return out


def _sidecars(db: Session, edition: Edition, own: list[BookFile]) -> list[BookFile]:
    """The files beside a recording that tell about it (``metadata.json``, a cover), in the folders of its files and in
    the folder of the book above the folders of its parts (``cover.jpg`` beside ``CD 1`` and ``CD 2``), and only where
    no other book keeps its files: the folder is the recording's own then."""
    if edition.kind != "audio":
        return []
    folders = set()
    for row in own:
        folder = row.path.rpartition("/")[0]
        folders.add(folder)
        folders.add(names.strip_part_folders(folder))
    folders.discard("")
    others = {path.rpartition("/")[0] for path in db.scalars(select(BookFile.path).where(
        BookFile.library_id == edition.library_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
        BookFile.edition_id != edition.id))}
    free = {folder for folder in folders
            if not any(other == folder or other.startswith(folder + "/") for other in others)}
    return [side for side in db.scalars(select(BookFile).where(
        BookFile.library_id == edition.library_id, BookFile.role == ROLE_SIDECAR, BookFile.missing_at.is_(None)))
        if side.path.rpartition("/")[0] in free]


@dataclass
class Sent:
    #: The entry of the trash the files went to; ``None`` when no file of the edition was there to move.
    trash_id: int | None
    #: ``searching`` (a wish looks for another release now), ``idle`` (the wish is there, but fetching by itself is
    #: switched off: it searches when somebody presses "Jetzt suchen"), ``have`` (the book has another edition of this
    #: kind, no wish was made), ``none`` (no book left to wish for), ``failed`` (the wish could not be made or started;
    #: ``code`` says why).
    replacement: str
    code: str
    work_id: int | None
    #: The files of the edition that were not there (gone from the disk since the last scan): they could not go to the
    #: trash, the edition went all the same. Their names inside the library.
    missing: list[str] = field(default_factory=list)
    #: How many people had a place, a mark or a finished pass in the edition: it is kept with the files and comes back
    #: with them.
    people: int = 0


def send_away(db: Session, account: Account, edition_id: int) -> Sent:
    """Sends the edition away as described in the module. ``404`` for an edition the caller does not see, ``403`` for a
    member without ``manage``, ``409 not_fetched`` for an edition that did not come from a release."""
    edition = db.get(Edition, edition_id)
    if edition is None:
        raise error("not_found", "Not found.", 404)
    library = libraries.require_reviewer(db, account.id, edition.library_id)
    with _lock:
        db.expire_all()
        edition = db.get(Edition, edition_id)
        if edition is None:
            raise error("not_found", "Not found.", 404)
        download = delivery_of(db, edition.id)
        if download is None:
            raise error("not_fetched", "This book did not come from a release nexbooks fetched.", 409)
        return _send(db, account, library, edition, download)


def _send(db: Session, account: Account, library: Library, edition: Edition, download: Download) -> Sent:
    rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK)))
    if not rows:
        raise error("not_found", "Not found.", 404)
    # A file that is gone from the disk cannot be moved, and the book that is wrong should not stay for that: what is
    # there goes to the trash, what is not is named in the answer.
    own = [row for row in rows if row.missing_at is None]
    gone = [row for row in rows if row.missing_at is not None]
    work_id, kind, language = edition.work_id, edition.kind, download.language
    wish = db.get(ProcureWish, download.wish_id) if download.wish_id is not None else None
    if wish is not None:
        language = wish.language
    title, author = wishes.book_text(db, account.id, work_id=download.work_id or work_id,
                                     foreign_id=download.foreign_id if download.work_id is None else None)
    fmt = rows[0].format
    states = kept.snapshot(db, edition.id)
    # Everything that only writes rows comes first, and the files move last: a failure before the move changes nothing
    # (the transaction is rolled back), and after it only the removal of the edition is left to do.
    wished_work = download.work_id if download.work_id is not None else work_id
    key = blocklist.book_key({"authors": [author] if author else [], "title": title})
    # ⚠️ The history line is written (and flushed) before the release is blocked, on purpose. SQLite's driver does not
    # begin a transaction by itself before a ``SAVEPOINT``; the block makes one, and releasing it then commits: a
    # failure of the move would roll back the line but not the block, which would stay for a book that is still there.
    # After an ``INSERT`` the transaction is open, the savepoint nests in it, and the rollback takes both.
    line = history.record(db, download, "removed")
    db.flush()
    blocked = blocklist.block(db, account, download, work_id=wished_work,
                              foreign_id=download.foreign_id if download.work_id is None and wished_work is None
                              else None, reason="by_hand", book=key, library_id=download.library_id or library.id)
    blocked_id = blocked.id
    entry = None
    if own:
        try:
            entry = trash.put(db, library, own + _sidecars(db, edition, own), kind=kind, title=title or edition.title,
                              author=author, fmt=fmt, work_id=work_id, edition_id=None, download_id=download.id)
        except hidden.Unsafe as exc:
            db.rollback()
            raise trash.unsafe_error() from exc
        except OSError as exc:
            db.rollback()
            logger.info("The files of edition %d could not be moved to the trash", edition.id)
            raise error("trash_failed", "The files could not be moved to the trash. Nothing was changed.", 409) from exc
    for row in gone:
        db.delete(row)
    # The folders of the parts that were gone are left empty; the ones of the files that moved are tidied by the move.
    trash.drop_empty_folders(library, [row.path.rpartition("/")[0] for row in gone])
    note: dict[str, Any] = {"code": "wrong_book", "blocked": True}
    if entry is not None:
        note["trash_id"] = entry.id
    if gone:
        note["missing"] = len(gone)
    line.note = note
    kept.keep(db, library_id=library.id, trash_id=entry.id if entry is not None else None,
              paths=[row.path for row in rows], states=states, days=trash.days(db))
    others = db.scalar(select(func.count(Edition.id)).where(Edition.work_id == work_id, Edition.id != edition.id)) or 0
    foreign_id = download.foreign_id if download.foreign_id is not None else (wish.foreign_id if wish else None)
    wished_id = download.work_id
    db.delete(edition)
    db.flush()
    work = db.get(Work, work_id)
    if work is not None:
        if others:
            catalog.refresh_work(db, work)
        else:
            db.delete(work)
    db.commit()
    catalog.forget_top_authors(library.id)
    logger.info("A fetched book was sent away edition=%d download=%d trash=%s missing=%d", edition.id, download.id,
                entry.id if entry is not None else "-", len(gone))
    catalog.refresh_guesses(db, library.id)
    # The book to look for is the one wished: its work when it is still there (the files may have gone in as a book of
    # their own), else the work the edition stood in, else the book of the sources.
    still_there = wished_id is not None and db.get(Work, wished_id) is not None
    target = wished_id if still_there else (work_id if others else None)
    if target is None and foreign_id is not None:
        # The work went with the edition, and with it what the block stood on. Its key need not find the book: the
        # work could be called otherwise than the book of the sources (``Buch 1&2``), and the release would come again.
        blocklist.attach(db, blocked_id, foreign_id)
        db.commit()
    replacement, code = _search_on(db, account, library, kind=kind, language=language, work_id=target,
                                   foreign_id=foreign_id if target is None else None)
    return Sent(trash_id=entry.id if entry is not None else None, replacement=replacement, code=code, work_id=target,
                missing=[row.path for row in gone][:50], people=kept.people_in(states))


def _search_on(db: Session, account: Account, library: Library, *, kind: str, language: str, work_id: int | None,
               foreign_id: int | None) -> tuple[str, str]:
    """A new wish for the book and the kind, in the library; the answer is what became of it."""
    token: str | None = None
    if work_id is None:
        if foreign_id is None:
            return "none", ""
        from ...models import ForeignBook

        book = db.get(ForeignBook, foreign_id)
        if book is None:
            return "none", ""
        token = book.token
    try:
        made = wishes.create(db, account, work_id=work_id, token=token, kinds=[kind], language=language or None,
                             library_id=library.id)
    except HTTPException as exc:
        db.rollback()
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else ""
        return "failed", str(code or exc.status_code)[:40]
    status = made[0].status if made else "none"
    wish = made[0].wish if made else None
    if status in ("created", "exists") and wish is not None:
        if status == "exists" and wish.state in ("upgrade", "not_found"):
            # A wish that had found a book (a format below its goal, waiting for a better one) or found nothing and
            # waits hours for its next try would search only then, and still speak of the book that is gone: it
            # searches now.
            wish.detail = None
            db.commit()
            try:
                wishes.search_now(db, account, wish.id)
            except HTTPException as exc:
                db.rollback()
                code = exc.detail.get("code") if isinstance(exc.detail, dict) else ""
                return "failed", str(code or exc.status_code)[:40]
        # With the main switch off nothing is searched until somebody presses "Jetzt suchen": the answer says so.
        return ("idle" if wish.state == "idle" else "searching"), ""
    if status == "have":
        return "have", ""
    return "failed", made[0].code if made else ""


__all__ = ["Sent", "deliveries_of", "delivery_of", "send_away"]
