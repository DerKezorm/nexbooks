"""What the people who look after a library do with its list of books that are not sure: take a proposal, look for
others,
leave a book as it is, take a match back. The rights are checked by the routes (``manage`` in the library of the book);
here is what happens.

**Nothing a browser sends is trusted as data about a book.** A proposal is taken *by its id*, out of the proposals the
server kept for that entry; the candidate behind it was made by the server from a source's answer. The address of a
cover
is never taken from a request.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import Edition, MatchEntry
from .. import catalog, covers
from ..readers.images import reduce_cover
from ..readers.limits import COVER_MAX_BYTES
from ..sources import http
from ..sources.model import Candidate
from . import apply
from .run import complete, lookup, proposal_of, query_of, wanted_of
from .score import Wanted, score

logger = logging.getLogger("nexbooks.match")

#: What is asked for and shown when a person looks for others.
FOUND_MAX = 8
PROPOSALS_MAX = 14
TEXT_MAX = 200


def listing_query(library_id: int | Any) -> Any:
    """The entries a person has to look at in a library (or in the libraries of a subquery of ids): not sure, and not
    decided."""
    where = MatchEntry.library_id == library_id if isinstance(library_id, int) else MatchEntry.library_id.in_(
        library_id)
    return (select(MatchEntry).join(Edition, Edition.id == MatchEntry.edition_id)
            .where(where, MatchEntry.state.in_(("maybe", "open")), MatchEntry.decision == "",
                   Edition.missing_at.is_(None)))


def to_look_at(db: Session, library_ids: list[int]) -> dict[int, int]:
    """How many entries need a look, per library."""
    if not library_ids:
        return {}
    rows = db.execute(
        select(MatchEntry.library_id, func.count(MatchEntry.id)).join(Edition, Edition.id == MatchEntry.edition_id)
        .where(MatchEntry.library_id.in_(library_ids), MatchEntry.state.in_(("maybe", "open")),
               MatchEntry.decision == "", Edition.missing_at.is_(None)).group_by(MatchEntry.library_id)).all()
    return {library_id: number for library_id, number in rows}


def progress_of(db: Session, library_ids: list[int]) -> list[tuple[int, int, int]]:
    """``(library, entries, waiting)``: how many books have a place in the matching, and how many of them are not looked
    at yet (pending, and no person decided on them)."""
    if not library_ids:
        return []
    rows = db.execute(
        select(MatchEntry.library_id, func.count(MatchEntry.id),
               func.sum(case(((MatchEntry.state == "pending") & (MatchEntry.decision == ""), 1), else_=0)))
        .join(Edition, Edition.id == MatchEntry.edition_id)
        .where(MatchEntry.library_id.in_(library_ids), Edition.missing_at.is_(None))
        .group_by(MatchEntry.library_id)).all()
    return [(library_id, int(total), int(waiting or 0)) for library_id, total, waiting in rows]


def entry_of(db: Session, entry_id: int) -> tuple[MatchEntry, Edition]:
    entry = db.get(MatchEntry, entry_id)
    edition = db.get(Edition, entry.edition_id) if entry is not None else None
    if entry is None or edition is None:
        raise error("not_found", "Not found.", 404)
    return entry, edition


#: The states a person may decide on: the ones on the list.
LISTED = ("maybe", "open")


def changed() -> Any:
    return error("entry_changed", "That book has changed in the meantime. Look at the list again.", 409)


def claim(db: Session, entry: MatchEntry, how: str, by: int | None, **values: Any) -> None:
    """A person's decision, taken **in one step with the check** that the entry is still what they looked at: on the
    list (not sure) and not decided. A job that settled the book meanwhile, or another person's decision, makes this
    fail (409) instead of being overwritten: the check is the ``WHERE`` of the ``UPDATE``, so nothing can come
    between."""
    now = clock.now()
    taken = db.execute(update(MatchEntry).where(
        MatchEntry.id == entry.id, MatchEntry.decision == "", MatchEntry.state.in_(LISTED)).values(
        decision=how, decided_by=by, decided_at=now, updated_at=now, **values)).rowcount
    if taken != 1:
        db.rollback()
        raise changed()
    db.refresh(entry)


def decided(entry: MatchEntry, how: str, by: int | None) -> None:
    entry.decision, entry.decided_by, entry.decided_at, entry.updated_at = how, by, clock.now(), clock.now()


def find_proposal(entry: MatchEntry, proposal_id: str) -> dict[str, Any]:
    for item in entry.proposals or []:
        if isinstance(item, dict) and item.get("id") == proposal_id:
            return item
    raise error("proposal_gone", "That proposal is not there any more. Look at the list again.", 404)


def accept(db: Session, entry: MatchEntry, edition: Edition, proposal_id: str, by: int) -> None:
    """A person took a proposal: it goes into effect as if it were sure, and nothing later changes the decision."""
    item = find_proposal(entry, proposal_id)
    try:
        candidate = Candidate.from_json(item.get("c") or {})
    except ValueError as exc:
        raise error("proposal_gone", "That proposal is not there any more. Look at the list again.", 404) from exc
    scored = score(wanted_of(db, edition), candidate)
    db.commit()
    complete(db, scored, interactive=True)
    cover = None
    if not edition.cover:
        cover = apply.fetch_cover(db, candidate, interactive=True)
    # The sources were asked with no write held; now the claim and the effect are one step.
    claim(db, entry, "accepted", by, state="sure", reason="")
    apply.apply_match(db, edition, scored, [], cover=cover)
    apply.note_evidence(db, edition, scored, [])
    db.commit()
    catalog.refresh_guesses(db, edition.library_id)
    logger.info("Match taken edition=%s source=%s by=%s", edition.id, candidate.source, by)


def keep(db: Session, entry: MatchEntry, by: int) -> None:
    """"Leave it as it is": the book stays as the files describe it and drops off the list. Only a book that is on the
    list: one that is waiting for the matching, or is sure, is not left anywhere (409)."""
    claim(db, entry, "kept", by)
    db.commit()


def unlink(db: Session, entry: MatchEntry, edition: Edition, by: int) -> None:
    """Takes a match back: what the sources gave goes, the book is as the files describe it, and it is not matched
    again by
    itself (a person said so)."""
    apply.clear_match(db, edition)
    entry.state, entry.reason, entry.proposals = "open", "unlinked", None
    decided(entry, "unlinked", by)
    db.commit()
    catalog.refresh_guesses(db, edition.library_id)


def search_more(db: Session, entry: MatchEntry, edition: Edition, title: str, author: str) -> int:
    """Looks for other books with the words a person typed (title and author), in every source that is ready, and adds
    what
    it finds to the proposals. Returns how many were found. ``SourceError`` when no source could be asked."""
    if entry.decision or entry.state not in LISTED:
        raise changed()
    base = wanted_of(db, edition)
    typed = Wanted(kind=base.kind, title=title.strip()[:TEXT_MAX] or base.title,
                   authors=[author.strip()[:TEXT_MAX]] if author.strip() else base.authors, language=base.language,
                   duration=base.duration, year=base.year)
    db.commit()
    result = lookup(db, typed, interactive=True, override=query_of(typed), stop_when_certain=False)
    if not result.asked:
        raise http.SourceError("off")
    if not result.answered:
        # The first failure, with what it says (how long to wait, for a source that is busy).
        raise next(iter(result.failed.values()))
    scored = sorted((score(typed, c) for c in result.candidates),
                    key=lambda s: (s.fatal != "", -s.score, -s.candidate.weight))[:FOUND_MAX]
    # The entry may have changed while the sources were asked (a decision, a new matching): it is read anew, and what is
    # found is written only to an entry that is still on the list and undecided, in the same step as that check.
    db.refresh(entry)
    known = {item.get("id") for item in entry.proposals or [] if isinstance(item, dict)}
    fresh = [proposal_of(s, found=True) for s in scored if f"{s.candidate.source}:{s.candidate.key}:"
             f"{s.candidate.market}" not in known]
    taken = db.execute(update(MatchEntry).where(
        MatchEntry.id == entry.id, MatchEntry.decision == "", MatchEntry.state.in_(LISTED)).values(
        proposals=[*fresh, *(entry.proposals or [])][:PROPOSALS_MAX], updated_at=clock.now())).rowcount
    if taken != 1:
        db.rollback()
        raise changed()
    db.commit()
    db.refresh(entry)
    return len(fresh)


def proposal_cover(db: Session, entry: MatchEntry, proposal_id: str) -> str | None:
    """The cover of a proposal as a stored picture (fetched from the source's own host the first time, then kept), or
    None. The name is kept in the proposal."""
    item = find_proposal(entry, proposal_id)
    name = str(item.get("cover") or "")
    if name and covers.path_of(name) is not None:
        return name
    try:
        candidate = Candidate.from_json(item.get("c") or {})
    except ValueError:
        return None
    if not candidate.cover_url or not http.host_allowed(candidate.source, candidate.cover_url):
        return None
    db.commit()
    try:
        answer = http.request(db, candidate.source, candidate.cover_url, headers={"Accept": "image/*"},
                              limit=COVER_MAX_BYTES, interactive=True)
    except http.SourceError:
        return None
    made = reduce_cover(answer.content) if answer.status_code == 200 else None
    if made is None:
        return None
    stored = covers.save(made[0])
    # The proposals are a JSON column: a changed copy, so that the change is seen.
    entry.proposals = [{**p, "cover": stored} if isinstance(p, dict) and p.get("id") == proposal_id else p
                       for p in entry.proposals or []]
    db.commit()
    return stored
