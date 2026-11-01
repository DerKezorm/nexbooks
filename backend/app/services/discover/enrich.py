"""What else the sources know about a book on its page: the form it has that the list did not name (the recording of
an e-book, the e-book of a recording), a description, the series. Asked in the background, once, when someone opens
the page (``view.book_view`` queues it), through the same catalogues the matching asks
(``services/match/run.lookup``), politely and never while a person waits.

Only a candidate that is the same book is taken: the same title as far as its subtitle, an author that fits (never the
last name alone). Whatever fits is added to the forms of the book; nothing else changes.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ... import clock
from ...models import ForeignBook
from .. import jobs
from ..match import run as matching
from ..match.score import Wanted
from ..readers.names import authors_compatible, title_keys
from ..sources.model import Candidate, Query
from . import foreign

logger = logging.getLogger("nexbooks.discover")

#: The forms a book can have, in the order they are looked for.
KINDS = ("audio", "ebook")
MAX_NEW_FORMS = 6


def same_book(row: ForeignBook, candidate: Candidate) -> bool:
    """Whether a candidate of a source is the book of a row: the same title as far as the subtitle and an author that
    fits."""
    if title_keys(candidate.title)[1] != title_keys(row.title)[1]:
        return False
    theirs = [a for a in candidate.authors if a]
    mine = [a for a in (row.authors or []) if isinstance(a, str)]
    return bool(theirs and mine and any(authors_compatible(a, b) for a in mine for b in theirs))


def enrich_job(db: Session, job: Any) -> None:
    """The handler of the job ``fenrich``: the book ``job.ref``."""
    row = db.get(ForeignBook, job.ref)
    if row is None or row.enriched_at is not None:
        return
    family = foreign.family(db, row)
    known = {form.get("kind") for form in foreign.forms_of(family)}
    authors = [a for a in (row.authors or []) if isinstance(a, str)]
    data = dict(row.data) if isinstance(row.data, dict) else {}
    forms = list(data.get("forms") or [])
    asked = answered = 0
    for kind in KINDS:
        if kind in known:
            continue
        wanted = Wanted(kind=kind, title=row.title, authors=authors, language=row.language)
        result = matching.lookup(db, wanted, override=Query(kind=kind, title=row.title, authors=authors,
                                                            language=row.language), stop_when_certain=False)
        asked += result.asked
        answered += len(result.answered)
        fitting = [c for c in result.candidates if same_book(row, c)]
        added = 0
        for candidate in fitting:
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            if kind == "audio" and candidate.source != "audible":
                continue
            forms = foreign.merge_forms(forms, [foreign.form_of(candidate, kind)])
            if not data.get("description") and candidate.description:
                data["description"], data["text_at"] = candidate.description, clock.now().isoformat()
            if candidate.isbns:
                data["isbns"] = list(dict.fromkeys([*(data.get("isbns") or []), *candidate.isbns]))[:20]
            added += 1
            if added >= MAX_NEW_FORMS:
                break
    if asked == 0:
        # No source may be asked now: it is tried again when someone opens the page and a source is on.
        db.rollback()
        return
    if answered == 0 and getattr(job, "attempts", 0) < jobs.ATTEMPTS:
        raise jobs.Retry("no source answered")
    # On the last try with no answer the book is marked as looked at all the same, so that no visit queues it for ever.
    data["forms"] = forms
    row.data = data
    row.enriched_at = clock.now()
    db.commit()


jobs.register("fenrich", enrich_job)
