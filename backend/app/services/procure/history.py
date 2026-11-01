"""The history of what became of the downloads: one line per download that ended, kept when the download
goes, with the title and the author as they were.

Lines are written by tracking (a failure) and by the person who takes a download away; filing adds
``done`` and ``rejected``. Who sees a line: a person with the right ``acquire`` in the library it went into, and a
work only when they see it (the line keeps the title as it was, so it must not be shown to anybody who does not see the
book).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import Account, Download, ProcureHistory
from .. import libraries
from . import wishes

logger = logging.getLogger("nexbooks.procure")

MAX_LISTED = 500
RESULTS = ("done", "rejected", "failed", "removed", "joined")


def record(db: Session, download: Download, result: str, note: dict[str, Any] | None = None) -> ProcureHistory:
    """The line for a download that ended. Does not commit."""
    title, author = wishes.book_text(db, download.user_id, work_id=download.work_id, foreign_id=download.foreign_id)
    row = ProcureHistory(user_id=download.user_id, library_id=download.library_id, work_id=download.work_id,
                         foreign_id=download.foreign_id, kind=download.kind, book_title=title[:300],
                         book_author=author[:300], release=download.title[:400], indexer_name=download.indexer_name,
                         result=result, note=note, wished=wishes.wished_of(db, download), created_at=clock.now())
    db.add(row)
    return row


#: The kinds a record of what was wished may name.
_KINDS = ("ebook", "audio")


def wished_of(db: Session, row: ProcureHistory) -> dict[str, Any] | None:
    """What a line was wished for: its record (``wished``), else for a line from before the book of the sources it was
    for, else nothing (what the work is called today may be another book's name)."""
    found = row.wished if isinstance(row.wished, dict) else None
    if found is None and row.foreign_id is not None:
        found = wishes.snapshot(db, None, work_id=None, foreign_id=row.foreign_id, kind=row.kind, language="")
    if not found or not isinstance(found.get("title"), str) or not found["title"]:
        return None
    authors = [name for name in found.get("authors") or [] if isinstance(name, str)][:3]
    kind = found.get("kind") if found.get("kind") in _KINDS else row.kind
    language = found.get("language") if isinstance(found.get("language"), str) else ""
    return {"title": found["title"][:300], "authors": authors, "kind": kind, "language": language[:8]}


def listing(db: Session, account: Account, result: str | None = None) -> list[ProcureHistory]:
    """The lines the person sees, the newest first; ``result`` ``done`` or ``bad`` (everything else) narrows them."""
    query = select(ProcureHistory).where(ProcureHistory.library_id.in_(libraries.acquirable_library_ids(account.id)),
                                         wishes.seen_work(account.id, ProcureHistory.work_id))
    if result == "done":
        query = query.where(ProcureHistory.result == "done")
    elif result == "bad":
        query = query.where(ProcureHistory.result != "done")
    return list(db.scalars(query.order_by(ProcureHistory.created_at.desc(), ProcureHistory.id.desc())
                           .limit(MAX_LISTED)))
