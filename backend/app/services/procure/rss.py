"""The newest entries of the indexers: a book that came out a minute ago is found without anybody
searching for it.

* **Every indexer in its time** (5, 15, 30 or 60 minutes, the operator's choice): one request for the newest entries
  of the book categories the operator ticked for it (``t=search`` without a query), a second and third page only while a
  page held nothing but entries nexbooks has not seen. An indexer that rests (a request limit, its limit of the day)
  is left out; one with a limit of the day gives at most half of it to this reading (the time between two readings grows
  with a small limit), the rest stays for the searches.
* **Seen once**: an entry is known by a hash of its guid (``rss_seen``, two weeks). The same entry is never judged
  twice.
* **Held against every open wish** with the rules of a search (``search.judge_items``): the language, the core of the
  title, the author as a set, the volume, the formats of the operator, the blocklist. Only wishes that wait count (found
  nothing, not out yet, an indexer's limit, an upgrade); one that is searched at this moment, loads or rests is left
  alone. A wish is cheaply passed over when no word of the entry is a word of one of its authors.
* **The best that fits** is loaded like a release of a search: the entries that fit are kept as a finished search of the
  wish's person (``origin = rss``), the wish is claimed in one statement, and ``wishes.settle`` loads the best,
  tries the
  next of another indexer when one cannot give the file, and waits when every indexer is at its limit.
* The numbers of every reading go to the log of the automatic work (``autolog``): requests and entries per indexer.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ... import clock
from ...db import SessionLocal
from ...models import Account, ForeignBook, Indexer, ProcureWish, ReleaseSearch, RssSeen
from .. import jobs
from ..readers.text import fold
from . import autolog, automation, budget, indexer_admin, indexers, newznab, release_match, search, wishes
from .indexers import IndexerError

logger = logging.getLogger("nexbooks.procure")

PAGES = 3
SEEN_DAYS = 14
#: The share of an indexer's limit of the day that the reading of the newest entries may use.
RSS_SHARE = 0.5
#: The wishes that wait for a release: the reading may take them.
WISH_STATES = ("not_found", "waiting", "capped", "upgrade")
MAX_WISHES = 2000


def item_key(item: newznab.Item) -> str:
    """What an entry is known by: the hash of its guid, else of its name."""
    return item.guid or hashlib.sha256(fold(item.title).encode("utf-8")).hexdigest()[:24]


def interval_of(row: Indexer, minutes: int) -> timedelta:
    """The time between two readings of an indexer: the operator's, and with a limit of the day not shorter than what
    its half of the limit allows (a reading is a request or more)."""
    if row.daily_limit > 0:
        minutes = max(minutes, math.ceil(24 * 60 / max(1.0, row.daily_limit * RSS_SHARE)))
    return timedelta(minutes=minutes)


def due_ids(db: Session, moment: datetime) -> list[int]:
    """The enabled indexers whose time for a reading has come, the one with the lowest priority number first."""
    minutes = automation.config(db)["rss_minutes"]
    found: list[int] = []
    for row in db.scalars(select(Indexer).where(Indexer.enabled.is_(True)).order_by(Indexer.priority, Indexer.id)):
        if row.rss_checked_at is None or moment - row.rss_checked_at >= interval_of(row, minutes):
            found.append(row.id)
    return found


def next_at(db: Session) -> datetime | None:
    """When the next reading of an indexer is due (the soonest of the enabled ones), or None without an indexer."""
    minutes = automation.config(db)["rss_minutes"]
    soonest: datetime | None = None
    for row in db.scalars(select(Indexer).where(Indexer.enabled.is_(True))):
        due = clock.now() if row.rss_checked_at is None else row.rss_checked_at + interval_of(row, minutes)
        if soonest is None or due < soonest:
            soonest = due
    return soonest


def _categories(row: Indexer) -> list[int]:
    chosen = [item["id"] for item in indexer_admin.view_categories(row) if item["on"]]
    return chosen or list(newznab.DEFAULT_CATEGORIES)


class Reading:
    def __init__(self) -> None:
        self.fresh: list[newznab.Item] = []
        self.keys: list[str] = []
        self.requests = 0
        self.error = ""
        #: Whether the error is new (the last reading failed another way or not at all): only that is named in the log.
        self.news = False


def read(indexer_id: int, moment: datetime) -> Reading:
    """One reading of the newest entries of an indexer: what nexbooks has not seen. Never raises for an indexer that
    cannot be asked: the code is kept on the indexer and in the reading."""
    result = Reading()
    with SessionLocal() as db:
        row = db.get(Indexer, indexer_id)
        if row is None or not row.enabled:
            return result
        limit = row.daily_limit
        previous = row.rss_error
        if limit > 0 and budget.used_today(row, moment) >= limit * RSS_SHARE:
            # Half of the day's limit is for this reading, the other half for the searches.
            row.rss_checked_at, row.rss_error = moment, "rss_share_used"
            db.commit()
            result.error = "rss_share_used"
            result.news = previous != result.error
            return result
        categories = _categories(row)
        page = search._page_size(row)
        asked: list[indexers.Client] = []
        keys_seen: set[str] = set()

        def work(client: indexers.Client) -> None:
            asked.append(client)
            for number in range(PAGES):
                if jobs.stopping.is_set():
                    return
                root, namespaces = client.request({"t": "search", "cat": ",".join(map(str, categories)),
                                                   "extended": 1, "limit": page, "offset": number * page})
                feed = newznab.parse_feed(root, namespaces)
                if feed.limits is not None:
                    client.limits = feed.limits
                listed = [(item_key(item), item) for item in feed.releases]
                known = set(db.scalars(select(RssSeen.key).where(
                    RssSeen.indexer_id == indexer_id, RssSeen.key.in_([key for key, _item in listed]))))
                new = [(key, item) for key, item in listed if key not in known and key not in keys_seen]
                for key, item in new:
                    keys_seen.add(key)
                    result.fresh.append(item)
                    result.keys.append(key)
                if feed.items < page or len(new) < len(listed):
                    return

        try:
            indexers.run(db, row, work, background=True)
        except IndexerError as exc:
            result.error = exc.code
        result.requests = sum(client.requests for client in asked)
        result.news = previous != result.error
        row = db.get(Indexer, indexer_id)
        if row is not None:
            row.rss_checked_at, row.rss_error = moment, result.error
            db.commit()
    return result


def _words(texts: list[str]) -> frozenset[str]:
    found: set[str] = set()
    for text in texts:
        for variants in release_match.tokens_of(text):
            found.update(variants)
    found.discard("")
    return frozenset(found)


def _subject(db: Session, row: ProcureWish, owner: Account) -> dict[str, Any] | None:
    accepted = search.accepted_of(db, row.language, search.original_of(db, row.work_id, row.foreign_id))
    try:
        if row.work_id is not None:
            subject = search.subject_of_work(db, owner.id, row.work_id, accepted)
        else:
            book = db.get(ForeignBook, row.foreign_id) if row.foreign_id is not None else None
            if book is None:
                return None
            subject = search.subject_of_foreign(db, book, accepted, owner.id)
    except HTTPException:
        return None
    subject["kinds"] = [row.kind]
    subject["libraries"] = {row.kind: row.library_id}
    return subject


def hold_against_wishes(db: Session, fresh: dict[int, list[newznab.Item]], round_: autolog.Round) -> None:
    """Holds the entries nexbooks has not seen against the open wishes and loads the best release that fits."""
    if not any(fresh.values()):
        return
    rows = list(db.scalars(select(ProcureWish).where(
        ProcureWish.state.in_(WISH_STATES), ProcureWish.handover.is_(None)).order_by(
        ProcureWish.created_at, ProcureWish.id).limit(MAX_WISHES)))
    if not rows:
        return
    item_words = {id(item): _words([item.title]) for items in fresh.values() for item in items}
    moment = clock.now()
    for row in rows:
        if not wishes.may_ask(db, row.user_id, row.library_id) or wishes.language_off(db, row, moment):
            continue
        owner = db.get(Account, row.user_id)
        subject = _subject(db, row, owner) if owner is not None else None
        if subject is None:
            continue
        round_.add("wishes")
        authors = _words([str(name) for name in subject.get("authors") or []])
        items = {indexer_id: [item for item in found if item_words[id(item)] & authors]
                 for indexer_id, found in fresh.items()}
        items = {indexer_id: found for indexer_id, found in items.items() if found}
        if not items:
            continue
        judged = search.judge_items(db, subject, row.language, row.work_id, row.foreign_id, items)
        if not any(verdict.rank is not None for _row, _item, verdict in judged):
            continue
        round_.add("matched")
        progress = [{"id": indexer_row.id, "name": indexer_row.name, "kind": indexer_row.kind, "state": "done",
                     "found": len(items.get(indexer_row.id, [])), "requests": 0, "error": "", "until": None}
                    for indexer_row in {entry[0].id: entry[0] for entry in judged}.values()]
        found = ReleaseSearch(user_id=row.user_id, work_id=row.work_id, foreign_id=row.foreign_id,
                              language=row.language, subject=subject, state="done", progress=progress, origin="rss",
                              created_at=moment, started_at=moment, finished_at=moment)
        db.add(found)
        db.flush()
        search.store_releases(db, found.id, judged)
        db.commit()
        if not wishes.claim(db, row, search_id=found.id):
            # A person or the planner was quicker: their search decides.
            db.delete(db.get(ReleaseSearch, found.id))
            db.commit()
            continue
        try:
            wishes.settle(db, row, found)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Settling a wish with the newest entries failed")
            continue
        if wishes.fresh(db, row) and row.state == "loading":
            round_.add("loaded")


def mark_seen(fresh_keys: dict[int, list[str]], moment: datetime) -> None:
    with SessionLocal() as db:
        for indexer_id, keys in fresh_keys.items():
            for start in range(0, len(keys), 200):
                chunk = keys[start:start + 200]
                db.execute(sqlite_insert(RssSeen).values(
                    [{"indexer_id": indexer_id, "key": key, "seen_at": moment} for key in chunk]
                ).on_conflict_do_nothing())
        db.commit()


def prune(db: Session) -> int:
    removed = db.execute(delete(RssSeen).where(RssSeen.seen_at < clock.now() - timedelta(days=SEEN_DAYS))).rowcount
    db.commit()
    return removed or 0


def has_wishes(db: Session) -> bool:
    """Whether a wish waits for a release that the newest entries could bring (none: nothing is read, and no request is
    spent on entries nobody is waiting for)."""
    return db.scalar(select(ProcureWish.id).where(
        ProcureWish.state.in_(WISH_STATES), ProcureWish.handover.is_(None)).limit(1)) is not None


def sync() -> int:
    """Reads the indexers that are due and holds what is new against the wishes. Returns how many new entries it read.
    With the main switch off, or without a wish that waits for a release, it does nothing."""
    moment = clock.now()
    with SessionLocal() as db:
        if not automation.config(db)["enabled"] or not has_wishes(db):
            return 0
        ids = due_ids(db, moment)
    if not ids:
        return 0
    round_ = autolog.Round("rss")
    fresh: dict[int, list[newznab.Item]] = {}
    keys: dict[int, list[str]] = {}
    for indexer_id in ids:
        if jobs.stopping.is_set():
            break
        reading = read(indexer_id, moment)
        round_.indexer(indexer_id, requests=reading.requests, entries=len(reading.fresh),
                       error=reading.error if reading.news else "")
        if reading.fresh:
            fresh[indexer_id] = reading.fresh
            keys[indexer_id] = reading.keys
    error = ""
    try:
        with SessionLocal() as db:
            hold_against_wishes(db, fresh, round_)
    except Exception:
        logger.exception("Holding the newest entries against the wishes failed")
        error = "internal_error"
    if not error:
        mark_seen(keys, moment)
    round_.finish(error)
    return sum(len(items) for items in fresh.values())


__all__ = ["RSS_SHARE", "due_ids", "has_wishes", "interval_of", "next_at", "prune", "read", "sync"]
