"""A rough running time for a recording of a book that no catalogue gives one for: from the pages of the book.

It decides nothing. It stands on the card of a question about the running time (``Fassung nicht belegt``), so that a
person can hold the length that came against what a book of that size takes to read aloud, where Audible is not used.
The pages come from an e-book of the same work the caller sees, else from what the sources know of the book.
"""

from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import EDITION_AUDIO, EDITION_EBOOK, Download, Edition, ForeignBook
from .. import works
from ..discover import foreign
from . import wishes

#: Seconds of a reading aloud for one page of a book: 1.7 minutes. A page of a novel holds about 250 to 300 words and a
#: reader speaks about 160 words in a minute; layouts differ, so the result is only a guide, never a proof.
SECONDS_PER_PAGE = 102


def _pages_of_sources(db: Session, row: Download, uid: int) -> int:
    book = db.get(ForeignBook, row.foreign_id) if row.foreign_id is not None else None
    if book is not None:
        rows = foreign.family(db, book)
    else:
        title, author = wishes.book_text(db, uid, work_id=row.work_id, foreign_id=row.foreign_id)
        rows = foreign.named(db, title, [author] if author else []) if title else []
    found: list[int] = []
    for item in rows:
        data = item.data if isinstance(item.data, dict) else {}
        values = [data.get("pages"), *(form.get("pages") for form in data.get("forms") or []
                                       if isinstance(form, dict) and form.get("kind") == EDITION_EBOOK)]
        found.extend(int(value) for value in values if isinstance(value, int) and not isinstance(value, bool)
                     and value > 0)
    return round(statistics.median(found)) if found else 0


def pages_of(db: Session, row: Download, uid: int) -> int:
    """The pages of the book of a download: the longest e-book of its work the person sees, else the sources' median
    (0 when nothing says)."""
    if row.work_id is not None:
        own = db.scalar(select(func.max(Edition.pages)).where(
            Edition.work_id == row.work_id, Edition.kind == EDITION_EBOOK, Edition.missing_at.is_(None),
            Edition.pages > 0, *works.visible_edition_clause(uid)))
        if own:
            return int(own)
    return _pages_of_sources(db, row, uid)


def for_card(db: Session, row: Download, uid: int, values: dict[str, Any]) -> dict[str, Any]:
    """What a card of a question about the running time adds to ``values`` when the catalogue gives no unabridged time:
    ``pages`` and ``estimate`` (seconds). Nothing when it has one, or the book is no recording, or no page is known."""
    if row.kind != EDITION_AUDIO or values.get("unabridged"):
        return {}
    pages = pages_of(db, row, uid)
    return {"pages": pages, "estimate": pages * SECONDS_PER_PAGE} if pages else {}
