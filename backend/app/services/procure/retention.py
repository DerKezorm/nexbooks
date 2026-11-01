"""How old a Usenet release may be: the news servers of the Usenet programs keep
articles for a number of days, and an older release would fail in the program.

* **Read from the programs** (SABnzbd: the servers that are on, ``retention`` in days; NZBGet: ``ServerN.Retention``
  of the active ones; 0 is unlimited). A release is on the servers while any active server of any enabled Usenet
  program still has it: the longest counts, and one server without a limit, one program that could not be read or
  one without an active server means no limit at all. nexbooks would rather load a release that fails and look for
  another than leave out one that would have worked.
* **The operator's own number** (``retention_days`` in the automatic settings, 0 for none) wins when it is set: a
  provider with a retention the program does not know about.
* The result lives in the setting ``procure_retention``. It is read again when the programs changed or after an hour.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock as wall
from ...db import SessionLocal
from ...models import CLIENT_PROTOCOL, DownloadClient
from .. import settings_service
from . import automation, programs
from .clients import ClientError, longest_retention, open_client

logger = logging.getLogger("nexbooks.procure")

SETTING = "procure_retention"
REFRESH = timedelta(hours=1)
UNKNOWN = "unknown"
_lock = threading.Lock()


def reset() -> None:
    """Nothing is kept in memory; the setting is the memory."""


def stored(db: Session) -> dict[str, object]:
    value = settings_service.get(db, SETTING)
    return value if isinstance(value, dict) else {}


def read_days(db: Session) -> int | None:
    value = stored(db).get("days")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def effective(db: Session) -> int:
    """The days a Usenet release may be old; 0 for no limit."""
    own = automation.config(db).get("retention_days", 0)
    return own if own else (read_days(db) or 0)


def refresh(*, only_when_due: bool = False) -> None:
    """Reads the news servers of every enabled Usenet program again and keeps the result."""
    with SessionLocal() as db:
        rows = [row for row in db.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True))
                                          .order_by(DownloadClient.id)) if CLIENT_PROTOCOL.get(row.kind) == "usenet"]
        fingerprint = hashlib.sha256(json.dumps([(row.id, row.kind, row.url, row.username) for row in rows])
                                     .encode()).hexdigest()[:16]
        known = stored(db)
        read_at = known.get("read_at")
        try:
            moment = datetime.fromisoformat(read_at) if isinstance(read_at, str) else None
        except ValueError:
            moment = None
        if only_when_due and known.get("of") == fingerprint and moment is not None \
                and wall.now() - (moment if moment.tzinfo else moment.replace(tzinfo=wall.now().tzinfo)) < REFRESH:
            return
        targets = [(row.id, programs.target_of(row)) for row in rows]
    values: dict[str, int | str | None] = {}
    for client_id, target in targets:
        try:
            with open_client(target) as client:
                values[str(client_id)] = client.usenet_retention()
        except ClientError as exc:
            logger.info("The retention of a program could not be read: %s", exc.code)
            values[str(client_id)] = UNKNOWN
    days = longest_retention([value if isinstance(value, int) else None for value in values.values()])
    with SessionLocal() as db:
        settings_service.save(db, {SETTING: {"days": days, "clients": values, "read_at": wall.now().isoformat(),
                                             "of": fingerprint}})
    if known.get("days") != days:
        logger.info("Usenet retention read from %d programs: %s", len(values), f"{days} days" if days else "no limit")
