"""A release a download program did not take because it did not answer.

A wish that found its release and met a program that did not answer (``client_unreachable``, ``client_timeout``) used to
wait for its next planned search, hours later, although the release was there. Now the wish keeps the release
(``ProcureWish.handover``: the search, the release, when to try again, how many tries are used):

* A few minutes later the release is handed over again through the same door a person's click uses (``loading.load``),
  which checks everything once more: the right of the person, the blocklist, a release of the book on its way already,
  the program. **Nothing is searched again**, and the indexer is not asked for the file again either: the program is
  asked first, and the file an earlier hand-over fetched is kept until a program has it (``releasefiles``).
* The time between two hand-overs doubles every time (3, 6, 12, 24, 48 minutes; ``choose.handover_delay``).
* At most ``TRIES`` more hand-overs; then the wish waits for its normal time as before.
* **Never twice**: a book and kind that has a release on its way (``already_loading``), a wish that is loading or done,
  a search or a release that is gone, a person who may not ask any more: the entry is dropped, nothing is loaded.
* A hand-over that did get a program's answer is not here: it is a download in the state ``unsure`` that tracking looks
  for by name (``foreign.look``) and that ends as ``not_taken`` after ten minutes; the wish then goes on with the next
  release, never with the same one again.
* The entry survives a restart (it is in the database).

Log lines carry numbers and codes, never a title.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import Account, ProcureWish, ReleaseSearch, SearchRelease
from . import autolog, choose, loading, wishes

logger = logging.getLogger("nexbooks.procure")

#: Hand-overs after the first one.
TRIES = 5


def _due(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _drop(db: Session, row: ProcureWish) -> None:
    row.handover = None
    db.commit()


def run(db: Session, round_: autolog.Round | None = None) -> int:
    """Hands over again what is due. Returns how many downloads it started."""
    moment = clock.now()
    started = 0
    for row in list(db.scalars(select(ProcureWish).where(ProcureWish.handover.is_not(None)))):
        entry = row.handover if isinstance(row.handover, dict) else {}
        due = _due(entry.get("due"))
        if due is None:
            _drop(db, row)
            continue
        if due > moment:
            continue
        if row.state in ("loading", "done", "searching") or not wishes.may_ask(db, row.user_id, row.library_id) \
                or not wishes.language_allowed(db, row):
            _drop(db, row)
            continue
        found = db.get(ReleaseSearch, entry.get("search")) if isinstance(entry.get("search"), int) else None
        release = db.get(SearchRelease, entry.get("release")) if isinstance(entry.get("release"), int) else None
        account = db.get(Account, row.user_id)
        if found is None or release is None or release.search_id != found.id or account is None:
            _drop(db, row)
            continue
        tries = int(entry.get("tries") or 0) + 1
        if round_ is not None:
            round_.add("handovers")
        try:
            loading.load(db, account, found.id, release.id, force=False, wish_id=row.id)
        except HTTPException as exc:
            db.rollback()
            code = exc.detail.get("code", "") if isinstance(exc.detail, dict) else ""
            row = db.get(ProcureWish, row.id) or row
            again = code in choose.UNREACHABLE and tries < TRIES
            if again:
                row.handover = {**entry, "tries": tries, "due": (moment + choose.handover_delay(tries)).isoformat()}
            else:
                row.handover = None
            row.detail = {**{k: v for k, v in (row.detail or {}).items() if k != "load_error"}, "load_error": code}
            db.commit()
            logger.info("Wish %d: handing the release over again failed code=%s tries=%d%s", row.id, code, tries,
                        "; trying again later" if again else "; the wish waits for its time")
            continue
        db.refresh(row)
        row.handover = None
        db.commit()
        started += 1
        if round_ is not None:
            round_.add("loaded")
        logger.info("Wish %d: the release was handed over again", row.id)
    return started


__all__ = ["TRIES", "run"]
