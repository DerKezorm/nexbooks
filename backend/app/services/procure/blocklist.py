"""The blocklist: releases never taken for a book again.

* An entry counts for its book: a work of a library (``work_id``) or a book of the sources (``foreign_id``). A work the
  scanner deletes does not take its entries with it: they lose both ids and keep the ``book_key`` (the author, the core
  of the title and what tells a volume from the next, folded). When the book is looked for again, under a new work, the
  entries that wait under its key attach to it. An entry of a book of the sources counts for a work under the same key
  too (the same book, before it came into a library); an entry of a work belongs to that work alone.
* A release is blocked when an entry of its book has its name with the same protocol (the name folded: dots, spaces
  and case do not matter), or the info hash of a torrent.
* **Who sees and changes it**: whoever may ask for books in the library the release was blocked for (the right
  ``acquire`` there), like the history, and for a work only when they see it; the name of who blocked it comes with
  that, so it is shown only to people of the same library. An entry applies to its book in every library (a release
  that is a different book, or damaged, is that for everybody), but is lifted only by the people of the library that
  made it. The operator has no right of his own here, as everywhere with books.
* Blocking asks first and unblocking asks first, in the interface (the API does what it is told).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import Account, BlocklistEntry, Download, ForeignBook, SearchRelease
from .. import book_words, libraries, works
from ..readers import names
from ..readers.text import fold

logger = logging.getLogger("nexbooks.procure")

MAX_LISTED = 500
#: The reasons a person may give; nexbooks's own checks give the codes of the judgement.
REASONS = ("by_hand", "other_language", "other_volume", "bundle", "abridged", "dramatized", "format_not_wanted",
           "author_mismatch", "title_mismatch", "sample", "soundtrack", "companion", "not_a_book", "damaged", "fake",
           "client_failed", "not_better", "dangerous", "archive_unsupported", "adaptation", "partial",
           "protected")


#: A release turned down for its language is blocked for the wishes that do not take that language only: the reason
#: keeps the language it was in (``other_language:en``), and a wish in that language looks at it again.
LANGUAGE_REASON = "other_language"


def reason_of(stored: str) -> str:
    """The reason of an entry as it is shown: ``other_language`` without the language it keeps."""
    return stored.split(":", 1)[0]


def _language_of_reason(stored: str) -> str:
    head, _, code = stored.partition(":")
    return code if head == LANGUAGE_REASON else ""


def key_of(title: str) -> str:
    return fold(title).replace(" ", "")[:400]


#: The number at the end of a bracket in a title: ``Die Nebelbahn (Nebel-Saga 2)``. A year has four digits.
_NUMBER_IN_BRACKET = re.compile(r"[(\[][^()\[\]]*\s#?0*(\d{1,3})\s*[)\]]")


def _volume_in(title: str) -> str:
    """The number of the volume a title names, empty when it names none, or several."""
    volume = book_words.title_volume(title)
    if volume is None:
        found = {int(number) for number in _NUMBER_IN_BRACKET.findall(title)}
        volume = next(iter(found)) if len(found) == 1 else None
    return str(volume) if volume is not None else ""


def book_key(subject: dict[str, Any] | None) -> str:
    """What a book is known by when its work is gone: the first author, the core of the title, and what tells it from
    the next volume of a series with that core (``Band 2``, ``(Reihe 2)``, ``: Das Ende``), folded. What a shop adds to
    a title is not part of it. Empty when the subject names no author or no title (then nothing waits for it)."""
    subject = subject or {}
    authors = subject.get("authors") or []
    author = str(authors[0]) if authors else ""
    title = str(subject.get("title") or "")
    if not author or not title:
        return ""
    title = names.article_in_front(title)
    core = names.compare_key(title, author)
    subtitle = names.subtitle_key(names.strip_noise(title), authors=[author])
    which = [part for part in (_volume_in(title), subtitle) if part and part != core]
    return "|".join((names.person_key(author), core, *which))[:300]


@dataclass
class Blocked:
    names: set[tuple[str, str]] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)


def _of_book(work_id: int | None, foreign_id: int | None, key: str) -> Any:
    """The entries that count for a book: those of its work (or book of the sources), and those under its key that
    belong to no work: the work they were made for may be gone, and a book of the sources is the same book as the work
    it becomes. An entry of another work counts for that work, even with the key of this one."""
    own = BlocklistEntry.work_id == work_id if work_id is not None else BlocklistEntry.foreign_id == foreign_id
    if not key:
        return own
    return or_(own, and_(BlocklistEntry.book_key == key, BlocklistEntry.work_id.is_(None)))


def to_work(db: Session, foreign_id: int, work_id: int) -> None:
    """The entries of a book of the sources go with it to the work it became (a wish that joined the work asks for
    those); one the work has already is not made twice. Does not commit."""
    have = {(row.title_key, row.protocol) for row in db.scalars(select(BlocklistEntry).where(
        BlocklistEntry.work_id == work_id))}
    for row in db.scalars(select(BlocklistEntry).where(BlocklistEntry.foreign_id == foreign_id)):
        if (row.title_key, row.protocol) in have:
            db.execute(delete(BlocklistEntry).where(BlocklistEntry.id == row.id))
        else:
            row.work_id, row.foreign_id = work_id, None
            have.add((row.title_key, row.protocol))
    db.flush()


def adopt(db: Session, *, work_id: int | None, foreign_id: int | None, key: str) -> None:
    """The entries that wait under the key of the book attach to its work (or book of the sources); one the book has
    already is not made twice. Does not commit."""
    if not key or (work_id is None and foreign_id is None):
        return
    waiting = list(db.scalars(select(BlocklistEntry).where(
        BlocklistEntry.book_key == key, BlocklistEntry.work_id.is_(None), BlocklistEntry.foreign_id.is_(None))))
    if not waiting:
        return
    own = BlocklistEntry.work_id == work_id if work_id is not None else BlocklistEntry.foreign_id == foreign_id
    have = {(row.title_key, row.protocol) for row in db.scalars(select(BlocklistEntry).where(own))}
    for row in waiting:
        if (row.title_key, row.protocol) in have:
            db.execute(delete(BlocklistEntry).where(BlocklistEntry.id == row.id))
        elif work_id is not None:
            row.work_id = work_id
        else:
            row.foreign_id = foreign_id
        have.add((row.title_key, row.protocol))
    db.flush()


def attach(db: Session, entry_id: int, foreign_id: int) -> None:
    """An entry whose work is gone goes to the book of the sources it was fetched for; one the book has already is not
    made twice. Does not commit."""
    # (Read anew: the database took the work off the entry when the work went.)
    row = db.get(BlocklistEntry, entry_id, populate_existing=True)
    if row is None or row.work_id is not None or row.foreign_id is not None:
        return
    if db.scalar(select(BlocklistEntry.id).where(BlocklistEntry.foreign_id == foreign_id,
                                                 BlocklistEntry.title_key == row.title_key,
                                                 BlocklistEntry.protocol == row.protocol)) is not None:
        db.delete(row)
    else:
        row.foreign_id = foreign_id
    db.flush()


def blocked_for(db: Session, *, work_id: int | None, foreign_id: int | None, key: str = "",
                languages: Iterable[str] = ()) -> Blocked:
    """What turns releases of the book down. With ``languages`` (those the wish or search takes) a release blocked
    because it was in one of them does not count: it was turned down for another wish."""
    found = Blocked()
    if work_id is None and foreign_id is None:
        return found
    adopt(db, work_id=work_id, foreign_id=foreign_id, key=key)
    taken = set(languages)
    for row in db.scalars(select(BlocklistEntry).where(_of_book(work_id, foreign_id, key))):
        if _language_of_reason(row.reason) in taken:
            continue
        found.names.add((row.title_key, row.protocol))
        if row.info_hash:
            found.hashes.add(row.info_hash)
    return found


def entries_for(db: Session, *, work_id: int | None, foreign_id: int | None, key: str = "",
                uid: int | None = None, languages: Iterable[str] = ()) -> dict[tuple[str, str], int]:
    """The entries of a book by their name and protocol: which one turns a release down. With ``uid`` only the entries
    that person sees (the others still turn the release down, but are not theirs to lift); ``languages`` as in
    ``blocked_for``."""
    if work_id is None and foreign_id is None:
        return {}
    query = select(BlocklistEntry).where(_of_book(work_id, foreign_id, key))
    if uid is not None:
        query = query.where(_seen(uid))
    taken = set(languages)
    return {(row.title_key, row.protocol): row.id for row in db.scalars(query)
            if _language_of_reason(row.reason) not in taken}


def matches(blocked: Blocked, title: str, protocol: str, info_hash: str = "") -> bool:
    return (key_of(title), protocol) in blocked.names or bool(info_hash and info_hash.lower() in blocked.hashes)


def _may_acquire(db: Session, uid: int) -> bool:
    return db.scalar(libraries.acquirable_library_ids(uid).limit(1)) is not None


def _seen(uid: int) -> Any:
    """The entries a person sees: those of a library they may ask for books in, and of a work only when they see it."""
    return and_(BlocklistEntry.library_id.in_(libraries.acquirable_library_ids(uid)),
                works.seen_work(uid, BlocklistEntry.work_id))


def _require_right(db: Session, uid: int) -> None:
    if not _may_acquire(db, uid):
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)


def listing(db: Session, account: Account) -> list[tuple[BlocklistEntry, str, str, str]]:
    """The entries the person sees, newest first, each with the title and the author of its book and the name of who
    blocked it."""
    _require_right(db, account.id)
    rows = list(db.scalars(select(BlocklistEntry).where(_seen(account.id))
                           .order_by(BlocklistEntry.created_at.desc(), BlocklistEntry.id.desc()).limit(MAX_LISTED)))
    out = []
    for row in rows:
        title, author = _book_of(db, account.id, row)
        by = ""
        if row.by_user_id is not None:
            person = db.get(Account, row.by_user_id)
            by = (person.display_name or person.name) if person is not None else ""
        out.append((row, title, author, by))
    return out


def count(db: Session, account: Account) -> int:
    """How many entries the person sees (for the head of the page)."""
    if not _may_acquire(db, account.id):
        return 0
    return db.scalar(select(func.count(BlocklistEntry.id)).where(_seen(account.id))) or 0


def _book_of(db: Session, uid: int, row: BlocklistEntry) -> tuple[str, str]:
    if row.work_id is not None:
        try:
            view = works.get_work(db, uid, row.work_id)
        except HTTPException:
            # A work the person no longer sees shows without its title.
            return "", ""
        return view.title, (view.authors[0][1] if view.authors else "")
    book = db.get(ForeignBook, row.foreign_id) if row.foreign_id is not None else None
    if book is None:
        return "", ""
    return book.title, next((name for name in book.authors or [] if isinstance(name, str)), "")


def get(db: Session, account: Account, entry_id: int) -> BlocklistEntry:
    _require_right(db, account.id)
    row = db.scalar(select(BlocklistEntry).where(BlocklistEntry.id == entry_id, _seen(account.id)))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def block(db: Session, account: Account | None, release: SearchRelease | Download, *, work_id: int | None,
          foreign_id: int | None, reason: str, book: str = "", library_id: int | None = None,
          language: str = "") -> BlocklistEntry:
    """The release blocked for its book; the entry that is there already when it is blocked (also when another request
    makes it at the same moment: the unique index decides). ``language``: the language a release turned down for its
    language was in (two letters); it is blocked for the wishes that do not take it. Does not commit."""
    if reason not in REASONS:
        raise error("invalid_input", "The reason is not valid.", 422, field="reason")
    if reason == LANGUAGE_REASON and len(language) == 2 and language.isalpha():
        reason = f"{LANGUAGE_REASON}:{language.lower()}"
    key = key_of(release.title)
    clause = BlocklistEntry.work_id == work_id if work_id is not None else BlocklistEntry.foreign_id == foreign_id

    def there() -> BlocklistEntry | None:
        return db.scalar(select(BlocklistEntry).where(clause, BlocklistEntry.title_key == key,
                                                      BlocklistEntry.protocol == release.protocol))

    found = there()
    if found is not None:
        return found
    row = BlocklistEntry(work_id=work_id, foreign_id=foreign_id if work_id is None else None, kind=release.kind,
                         book_key=book, title=release.title, title_key=key, protocol=release.protocol,
                         info_hash=release.info_hash, indexer_name=release.indexer_name, reason=reason,
                         by_user_id=account.id if account is not None else None, library_id=library_id,
                         created_at=clock.now())
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        found = there()
        if found is None:
            raise
        return found
    logger.info("Release blocked entry=%d reason=%s", row.id, reason)
    return row


def unblock(db: Session, account: Account, entry_id: int) -> None:
    row = get(db, account, entry_id)
    db.delete(row)
    db.commit()
    logger.info("Release unblocked entry=%d", entry_id)


__all__ = ["LANGUAGE_REASON", "REASONS", "Blocked", "adopt", "block", "blocked_for", "book_key", "count", "entries_for",
           "get", "key_of", "listing", "matches", "reason_of", "to_work", "unblock"]
