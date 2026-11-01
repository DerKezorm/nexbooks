"""The Prowlarr of the instance: a read-only client and the sync that makes each of its indexers with book categories an
indexer of nexbooks.

What was measured on Prowlarr 2.6 and holds here:

* ⚠️ The key travels only in the ``X-Api-Key`` header of the calls to Prowlarr's own API (a URL ends up in logs, a header
  does not). Prowlarr's per-indexer endpoints ``/{id}/api`` take the key as the parameter ``apikey``, as every Torznab
  endpoint does; that is the indexer client's business.
* A wrong or missing key is 401 with an empty body. An unknown API path is 404. Everything outside ``/api/`` is the web
  interface with 200: a 200 proves nothing, ``system/status`` counts only as JSON with ``appName`` "Prowlarr".
* The address carries Prowlarr's URL base; without it the API is not found at all.
* Version 1.8.6 is the oldest release with every field read here.

**The sync** (a job of the queue, every 15 minutes and on the operator's request): each Prowlarr indexer with a category
for books (7000 series) or audiobooks becomes an indexer of nexbooks with the address ``{prowlarr}/{id}/api``, Torznab
for ``torrent`` and Newznab for ``usenet``, its name, its priority and a daily limit from its query limit. An indexer
with none is left out and counted. A new one is taken only while it is switched on in Prowlarr and the operator lets
new ones in (``auto_add``); one that is there is kept in step (name, kind, address) and otherwise left to the operator.
nexbooks holds at most ``MAX_INDEXERS`` indexers, those of Prowlarr included: a new one over that is left out and
counted (``skipped_limit``), and the connection says so (``prowlarr_too_many``). One Prowlarr no longer lists, or that
has no book category any more, goes. **Indexers from Prowlarr cannot be removed one by one**: they come and go with
Prowlarr. They use the key of the connection, so a new key there reaches them all.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Self

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import SessionLocal
from ...models import Indexer, Job, ProwlarrConnection
from ...security import decrypt_secret
from .. import jobs
from . import formats, gate, indexers, newznab

logger = logging.getLogger("nexbooks.procure")

APP_NAME = "Prowlarr"
STATUS_PATH = "/api/v1/system/status"
INDEXER_PATH = "/api/v1/indexer"
#: The oldest Prowlarr with every field read here.
MINIMUM_VERSION = (1, 8, 6)
JOB = "prowlarr_sync"
#: A request to Prowlarr costs the trackers nothing; Prowlarr's own apps sync every six hours.
INTERVAL_SECONDS = 900
KEY_MAX = 512
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
COUNTS = ("total", "added", "updated", "removed", "skipped_categories", "skipped_disabled", "skipped_unsupported",
          "skipped_limit")
#: The code a sync leaves on the connection when it had to leave indexers out because nexbooks holds no more.
LIMIT_CODE = "prowlarr_too_many"


class ProwlarrError(Exception):
    """An answer of Prowlarr nexbooks cannot use. ``code`` is what the interface words."""

    def __init__(self, code: str, status: int = 502, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.values = values


ERRORS = (
    (502, "prowlarr_unreachable"), (502, "prowlarr_key_rejected"), (502, "prowlarr_not_prowlarr"),
    (502, "prowlarr_too_old"), (502, "prowlarr_http_error"), (502, "prowlarr_blocked"), (504, "prowlarr_timeout"),
)


def version_tuple(value: str) -> tuple[int, ...]:
    found = _VERSION.match(value or "")
    return tuple(int(part) for part in found.groups()) if found else ()


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _name(value: Any, limit: int = 100) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


@dataclass(frozen=True)
class ProwlarrIndexer:
    id: int
    name: str
    enable: bool
    #: ``torrent``, ``usenet`` or anything Prowlarr sends.
    protocol: str
    priority: int | None
    #: Every category of the tree, as the caps of an indexer list them (``id``, ``name``, ``subcats``).
    categories: list[dict[str, Any]]
    query_limit: int | None
    #: 0 for a day, 1 for an hour.
    limits_unit: int


def _tree(capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    """Prowlarr's category tree in the shape of the caps: a list of categories with their sub-categories flat."""
    found: list[dict[str, Any]] = []

    def flat(entry: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for sub in map(_dict, _list(entry.get("subCategories"))):
            number = _int(sub.get("id"))
            if number is not None:
                out.append({"id": number, "name": _name(sub.get("name"))})
            out.extend(flat(sub))
        return out

    for entry in list(map(_dict, _list(capabilities.get("categories"))))[:newznab.MAX_CATEGORIES]:
        number = _int(entry.get("id"))
        if number is not None:
            found.append({"id": number, "name": _name(entry.get("name")),
                          "subcats": flat(entry)[:newznab.MAX_CATEGORIES]})
    return found


def parse_indexer(value: Any) -> ProwlarrIndexer | None:
    data = _dict(value)
    number = _int(data.get("id"))
    if number is None or number <= 0:
        return None
    fields: dict[str, Any] = {}
    for item in map(_dict, _list(data.get("fields"))):
        if item.get("name") in ("baseSettings.queryLimit", "baseSettings.limitsUnit") and "value" in item:
            fields[str(item["name"])] = item["value"]
    unit = _int(fields.get("baseSettings.limitsUnit"))
    return ProwlarrIndexer(
        id=number,
        name=_name(data.get("name")) or f"Prowlarr {number}",
        enable=data.get("enable") is True,
        protocol=_name(data.get("protocol"), 16).lower(),
        priority=_int(data.get("priority")),
        categories=_tree(_dict(data.get("capabilities"))),
        query_limit=_int(fields.get("baseSettings.queryLimit")),
        limits_unit=unit if unit in (0, 1) else 0,
    )


class Client:
    """Use as ``with Client(url, key) as prowlarr: ...``."""

    def __init__(self, url: str, api_key: str) -> None:
        self.url = url.rstrip("/")
        self._key = api_key
        self._session: gate.Session | None = None

    def __enter__(self) -> Self:
        self._session = gate.Session(self.url, label="prowlarr",
                                     headers={"X-Api-Key": self._key, "Accept": "application/json"})
        self._session.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._session is not None:
            self._session.__exit__(*exc)
            self._session = None

    def _json(self, path: str) -> Any:
        if self._session is None:
            raise RuntimeError("A Prowlarr client is used outside of 'with'")
        # A header cannot carry anything but plain characters.
        if not self._key.isascii() or not self._key.isprintable():
            raise ProwlarrError("prowlarr_key_rejected")
        try:
            answer = self._session.request("GET", self._session.url(path))
        except gate.GateError as exc:
            raise ProwlarrError({"timeout": "prowlarr_timeout", "refused": "prowlarr_blocked",
                                 "host_not_allowed": "prowlarr_blocked", "redirect": "prowlarr_blocked"}.get(
                                     exc.code, "prowlarr_unreachable"), 504 if exc.code == "timeout" else 502) from exc
        status = answer.status_code
        if status == 401:
            raise ProwlarrError("prowlarr_key_rejected")
        if 300 <= status < 400 or status == 404:
            raise ProwlarrError("prowlarr_not_prowlarr")
        if not 200 <= status < 300:
            raise ProwlarrError("prowlarr_http_error", status=status)
        if answer.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ProwlarrError("prowlarr_not_prowlarr")
        try:
            return answer.json()
        except (ValueError, RecursionError) as exc:
            raise ProwlarrError("prowlarr_not_prowlarr") from exc

    def status(self) -> str:
        """The version, after the checks that this is Prowlarr and a new enough one."""
        data = self._json(STATUS_PATH)
        if not isinstance(data, dict) or data.get("appName") != APP_NAME:
            raise ProwlarrError("prowlarr_not_prowlarr")
        version = _name(data.get("version"), 40)
        parsed = version_tuple(version)
        if not parsed or parsed < MINIMUM_VERSION:
            raise ProwlarrError("prowlarr_too_old", version=version or "?")
        return version

    def indexers(self) -> list[ProwlarrIndexer]:
        data = self._json(INDEXER_PATH)
        if not isinstance(data, list):
            raise ProwlarrError("prowlarr_not_prowlarr")
        return [item for item in map(parse_indexer, data[:2000]) if item is not None]


# --- What an indexer of Prowlarr becomes -------------------------------------------------------------------------


def prefer_languages(db: Session) -> tuple[str, ...]:
    """The languages the operator accepts (Formats and place): the book categories of those and the mixed ones are
    taken."""
    return formats.accepted_languages(db)


def endpoint(base_url: str, indexer_id: int) -> str:
    return f"{base_url.rstrip('/')}/{indexer_id}/api"


def _daily(indexer: ProwlarrIndexer) -> int:
    if indexer.query_limit is None or indexer.query_limit <= 0:
        return 0
    per_day = indexer.query_limit * 24 if indexer.limits_unit == 1 else indexer.query_limit
    return min(per_day, indexers.DAILY_LIMIT_MAX)


def wanted_categories(indexer: ProwlarrIndexer, prefer: tuple[str, ...]) -> tuple[list[int], dict[str, str]]:
    """The book categories of a Prowlarr indexer that are asked, and the language of each."""
    listed = newznab.book_categories({"categories": indexer.categories}, prefer)
    chosen = [item for item in listed if item.suggested]
    return [item.id for item in chosen], {str(item.id): item.lang for item in chosen}


@dataclass
class Outcome:
    counts: dict[str, int]


def sync(db: Session, connection: ProwlarrConnection) -> Outcome:
    """Reads Prowlarr and writes its indexers; raises ``ProwlarrError`` (recorded on the connection as well). Nothing is
    changed when Prowlarr cannot be read."""
    key = decrypt_secret(connection.api_key_enc, indexers.SECRET_CONTEXT)
    try:
        with Client(connection.url, key) as prowlarr:
            version = prowlarr.status()
            listed = prowlarr.indexers()
    except ProwlarrError as exc:
        logger.info("The sync with Prowlarr failed: %s", exc.code)
        connection.last_sync_at = clock.now()
        connection.last_error = exc.code
        db.commit()
        raise
    prefer = prefer_languages(db)
    counts = dict.fromkeys(COUNTS, 0)
    mine = {row.prowlarr_indexer_id: row
            for row in db.scalars(select(Indexer).where(Indexer.prowlarr_indexer_id.is_not(None)))
            if row.prowlarr_indexer_id is not None}
    present: set[int] = set()
    moment = clock.now()
    for item in listed:
        counts["total"] += 1
        if item.protocol not in ("torrent", "usenet"):
            counts["skipped_unsupported"] += 1
            continue
        categories, langs = wanted_categories(item, prefer)
        if not categories:
            counts["skipped_categories"] += 1
            continue
        present.add(item.id)
        url, kind = endpoint(connection.url, item.id), "torznab" if item.protocol == "torrent" else "newznab"
        row = mine.get(item.id)
        if row is not None:
            changed = False
            for name, value in (("name", item.name), ("kind", kind), ("url", url)):
                if getattr(row, name) != value:
                    if name == "name":
                        value = _free_name(db, value, row.id)
                    setattr(row, name, value)
                    changed = True
            if changed:
                row.updated_at = moment
                counts["updated"] += 1
            continue
        if not item.enable:
            counts["skipped_disabled"] += 1
            continue
        if not connection.auto_add:
            continue
        if (db.scalar(select(func.count(Indexer.id))) or 0) >= indexers.MAX_INDEXERS:
            counts["skipped_limit"] += 1
            continue
        priority = indexers.PRIORITY_DEFAULT
        if item.priority is not None and 1 <= item.priority <= 50:
            priority = item.priority
        db.add(Indexer(name=_free_name(db, item.name, None), kind=kind, url=url, priority=priority,
                       daily_limit=_daily(item), categories=categories, category_langs=langs,
                       prowlarr_indexer_id=item.id, created_at=moment, updated_at=moment))
        db.flush()
        counts["added"] += 1
    for number, row in mine.items():
        if number not in present:
            logger.info("An indexer of Prowlarr goes: it is gone or has no book category any more")
            db.delete(row)
            counts["removed"] += 1
    connection.version = version
    connection.last_sync_at = moment
    connection.last_error = LIMIT_CODE if counts["skipped_limit"] else ""
    connection.last_counts = counts
    db.commit()
    logger.info("Prowlarr synced: %d in Prowlarr, %d added, %d changed, %d removed, %d left out", counts["total"],
                counts["added"], counts["updated"], counts["removed"],
                counts["skipped_categories"] + counts["skipped_disabled"] + counts["skipped_unsupported"])
    return Outcome(counts)


def _free_name(db: Session, name: str, ignore_id: int | None) -> str:
    """A name no other indexer has: Prowlarr's, else with its number after it."""
    base = name[:90]
    candidate, number = base, 1
    while db.scalar(select(Indexer.id).where(Indexer.name == candidate, Indexer.id != (ignore_id or 0))) is not None:
        number += 1
        candidate = f"{base} ({number})"
    return candidate


def check(url: str, key: str) -> dict[str, Any]:
    """The test of an address and a key: Prowlarr's version, how many indexers it has and how many of them have book
    categories."""
    with Client(url, key) as prowlarr:
        version = prowlarr.status()
        listed = prowlarr.indexers()
    prefer: tuple[str, ...] = ()
    with_books = sum(1 for item in listed
                     if item.protocol in ("torrent", "usenet") and wanted_categories(item, prefer)[0])
    return {"version": version, "indexers": len(listed), "with_books": with_books}


# --- The job -------------------------------------------------------------------------------------------------------

def _handler(db: Session, job: Job) -> None:
    connection = db.get(ProwlarrConnection, job.ref)
    if connection is None:
        return
    try:
        sync(db, connection)
    except ProwlarrError:
        # Kept on the connection (``last_error``); the next round tries again.
        return


jobs.register(JOB, _handler)


def request_sync(db: Session, connection: ProwlarrConnection) -> None:
    """Puts a sync in the queue (a sync that waits already is the same sync)."""
    jobs.enqueue(db, JOB, ref=connection.id)
    db.commit()


def sync_state(db: Session, connection: ProwlarrConnection) -> str:
    """``idle``, ``queued`` or ``running``."""
    found = db.scalars(select(Job.state).where(Job.kind == JOB, Job.ref == connection.id,
                                               Job.state.in_(("queued", "running")))).all()
    return "running" if "running" in found else "queued" if found else "idle"


def drop_jobs(db: Session, connection_id: int) -> None:
    db.execute(delete(Job).where(Job.kind == JOB, Job.ref == connection_id, Job.state == "queued"))


def _queue_due() -> None:
    with SessionLocal() as db:
        for connection in db.scalars(select(ProwlarrConnection)):
            request_sync(db, connection)


async def run_forever(stop: asyncio.Event) -> None:
    """Queues the sync now and then, as long as there is a Prowlarr."""
    waited = 60.0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), waited)
            return
        except TimeoutError:
            pass
        waited = float(INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_queue_due)
        except Exception:
            logger.exception("Queueing the sync with Prowlarr failed")
