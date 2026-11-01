"""Loading the best of the releases that pass: the one place where a wish takes a release out of a
finished search, whether the search was one of the indexers or the reading of their newest entries, and whether it
is the first release of the wish or the next one after a failure.

* The releases are tried **best first**. An indexer that cannot give the file (its limit of the day, a pause after a
  request limit, no answer, a file that is no NZB) costs one try, and the next release of **another indexer** goes on:
  the others keep working while one rests. At most ``MAX_TRIES`` tries.
* A download program that does not answer ends the tries (another release would meet the same program); the release is
  kept for a new hand-over (``handover``), which asks the indexer for the file and nothing else.
* A second release of the book and kind is never loaded while one is on its way: ``already_loading`` ends the tries and
  the caller adopts what runs.
* Log lines carry numbers and codes, never a title.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ... import clock
from ...models import Account, Download, ReleaseSearch, SearchRelease
from . import loading

logger = logging.getLogger("nexbooks.procure")

MAX_TRIES = 3
#: A failure of the indexer of a release: the next release of another indexer may go on.
INDEXER_FAULTS = frozenset({
    "indexer_limit_reached", "indexer_daily_limit", "indexer_unreachable", "indexer_timeout", "indexer_blocked",
    "indexer_key_rejected", "indexer_off", "indexer_gone", "indexer_http_error", "indexer_switched_off",
    "indexer_answer_too_large", "release_fetch_failed", "release_file_invalid", "release_link_private",
})
#: The indexer's limit: the wish is ``capped`` when nothing else could be tried.
LIMITS = frozenset({"indexer_limit_reached", "indexer_daily_limit"})
#: The program did not answer: the same release is handed over again later.
UNREACHABLE = frozenset({"client_unreachable", "client_timeout"})
#: The time to the next hand-over of a release a program did not take: three minutes at first, twice as long every time,
#: an hour at most.
HANDOVER_FIRST = timedelta(minutes=3)
HANDOVER_LAST = timedelta(hours=1)


def handover_delay(tries: int) -> timedelta:
    """How long to wait before the hand-over that follows ``tries`` tries."""
    return min(HANDOVER_LAST, HANDOVER_FIRST * 2 ** max(0, min(tries, 10)))


@dataclass
class Outcome:
    download: Download | None = None
    #: The code of the last failure.
    code: str = ""
    #: The release the last failure was about, and what the indexer said about its limit.
    release_id: int | None = None
    indexer: str = ""
    until: str | None = None
    tried: int = 0
    #: The codes of all the failures, in order.
    codes: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return self.download is not None

    @property
    def only_limits(self) -> bool:
        return bool(self.codes) and all(code in LIMITS for code in self.codes)


def _code(exc: HTTPException) -> tuple[str, dict[str, Any]]:
    if isinstance(exc.detail, dict):
        return str(exc.detail.get("code", "load_failed")), exc.detail
    return "load_failed", {}


def load_best(db: Session, account: Account, found: ReleaseSearch, passing: list[SearchRelease], *,
              wish_id: int | None) -> Outcome:
    """Loads the first release of ``passing`` (best first) that goes through; see the module text."""
    outcome = Outcome()
    skipped: set[int | None] = set()
    #: The language of a release whose indexer was at its limit: a release of another (a lower) language is not taken
    #: instead; the wish waits for the limit to be over (``capped``).
    waiting_for: str | None = None
    for release in passing:
        if release.indexer_id in skipped:
            continue
        if waiting_for is not None and release.language != waiting_for:
            continue
        if outcome.tried >= MAX_TRIES:
            break
        outcome.tried += 1
        try:
            outcome.download = loading.load(db, account, found.id, release.id, force=False, wish_id=wish_id)
            return outcome
        except HTTPException as exc:
            db.rollback()
            code, values = _code(exc)
            outcome.code, outcome.release_id = code, release.id
            outcome.indexer = release.indexer_name
            until = values.get("until")
            outcome.until = until if isinstance(until, str) else None
            wait = values.get("retry_after")
            if outcome.until is None and isinstance(wait, int) and wait > 0:
                outcome.until = (clock.now() + timedelta(seconds=wait)).isoformat()
            outcome.codes.append(code)
            if code in INDEXER_FAULTS:
                # Another release of the same indexer would meet the same fault.
                skipped.add(release.indexer_id)
                if code in LIMITS and waiting_for is None:
                    waiting_for = release.language
                logger.info("Release not loaded, the indexer cannot give it code=%s", code)
                continue
            break
    return outcome


__all__ = ["INDEXER_FAULTS", "LIMITS", "MAX_TRIES", "UNREACHABLE", "Outcome", "handover_delay", "load_best"]
