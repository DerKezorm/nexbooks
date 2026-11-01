"""After a book was filed: a torrent's seed goal, and taking the finished torrent out of its program.

Once a minute, from the tracking round, every enabled torrent program is asked about nexbooks's filed torrents that were
not dealt with yet (``seed_state`` is not ``done``).

* **The goal** is what the tracker asked for (a ratio, seconds of seeding) at least what the operator chose in the
  automatic settings (a ratio of 1, or seven days); it was kept on the download when it was loaded. With the choice "no"
  nothing is seeded: the torrent leaves the program once the book is filed.
* **Transmission and Deluge have no seed time of their own** that means what it should: when the goal is reached,
  nexbooks stops the torrent itself. qBittorrent keeps its own limits (they came with the torrent).
* **At its goal** is a torrent the program stopped at a limit of its own, or one with the ratio or the seconds of the
  goal. A torrent started by force never is.
* **Taking it out** (with its files) only for a torrent whose files nexbooks sees below a download root and not inside
  any library, so that a book that lies in a library is never taken away; a torrent whose files nexbooks does not see
  stays (``seed_state`` ``kept``: the goal is reached, the program keeps it; one line in the log, then silence) and is
  looked at again later. A person may still end it ("Seeden beenden"). The book in the library is a link or a copy: it
  stays.
* Filing is the work of the importer; this module only looks at downloads in the state ``imported``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock as wall
from ...db import SessionLocal
from ...models import CLIENT_PROTOCOL, Download, DownloadClient, Library, PathMapping
from . import arrival, automation, programs
from .clients import ClientError, Seeding, Target, open_client, remove_own

logger = logging.getLogger("nexbooks.procure")

LOOK_EVERY_SECONDS = 60.0
#: The programs whose seed time nexbooks watches itself.
OWN_SEED_TIME = frozenset({"transmission", "deluge"})

_lock = threading.Lock()
_next_look: float | None = None


def clock() -> float:
    return time.monotonic()


def reset() -> None:
    global _next_look
    with _lock:
        _next_look = None


def due() -> bool:
    with _lock:
        return _next_look is None or clock() >= _next_look


@dataclass(frozen=True)
class _Torrent:
    download_id: int
    ref: str
    goal_ratio: float | None
    goal_seconds: int | None


@dataclass(frozen=True)
class _Client:
    id: int
    kind: str
    target: Target = field(repr=False)
    mappings: list[tuple[str, str]] = field(default_factory=list)
    torrents: tuple[_Torrent, ...] = ()


def _clients(db: Session) -> list[_Client]:
    found: list[_Client] = []
    for row in db.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True)).order_by(DownloadClient.id)):
        if CLIENT_PROTOCOL.get(row.kind) != "torrent":
            continue
        torrents = tuple(_Torrent(item.id, item.client_ref.lower(), item.goal_ratio, item.goal_seconds)
                         for item in db.scalars(select(Download).where(
                             Download.client_id == row.id, Download.protocol == "torrent",
                             Download.state == "imported", Download.seed_state != "done",
                             Download.client_ref != "")))
        if torrents:
            mappings = [(m.remote, m.local) for m in db.scalars(select(PathMapping).where(
                PathMapping.client_id == row.id))]
            found.append(_Client(row.id, row.kind, programs.target_of(row), mappings, torrents))
    return found


def at_goal(item: _Torrent, info: Seeding, nothing_to_seed: bool) -> bool:
    if info.forced or not info.done:
        return False
    if nothing_to_seed:
        return True
    if item.goal_ratio is not None and info.ratio is not None and info.ratio >= item.goal_ratio - 0.001:
        return True
    if item.goal_seconds is not None and info.seeding_seconds is not None and info.seeding_seconds >= item.goal_seconds:
        return True
    return info.goal_reached


def _in_a_library(db: Session, path: object) -> bool:
    for library in db.scalars(select(Library)):
        if arrival.inside(path, library.path):  # type: ignore[arg-type]
            return True
    return False


def look() -> None:
    """One look at the filed torrents of every enabled torrent program."""
    global _next_look
    with _lock:
        _next_look = clock() + LOOK_EVERY_SECONDS
    with SessionLocal() as db:
        clients = _clients(db)
        nothing_to_seed = automation.config(db)["seed"] == "no"
    for item in clients:
        try:
            with open_client(item.target) as client:
                infos = client.seeding([torrent.ref for torrent in item.torrents])
                _handle(client, item, infos, nothing_to_seed)
        except ClientError as exc:
            logger.info("Download program %d: its torrents could not be looked at: %s", item.id, exc.code)


def _handle(client, item: _Client, infos: dict[str, Seeding], nothing_to_seed: bool) -> None:  # type: ignore[no-untyped-def]
    for torrent in item.torrents:
        info = infos.get(torrent.ref)
        with SessionLocal() as db:
            row = db.get(Download, torrent.download_id)
            if row is None or row.state != "imported" or row.seed_state == "done":
                continue
            if info is None:
                # The program does not have it any more (a person took it away): nothing left to look at.
                row.seed_state, row.finished_at = "done", row.finished_at or wall.now()
                db.commit()
                continue
            row.seed_ratio, row.seed_seconds = info.ratio, info.seeding_seconds
            row.seed_state = row.seed_state or "watching"
            if not at_goal(torrent, info, nothing_to_seed):
                row.seed_state = "watching"
                db.commit()
                continue
            if item.kind in OWN_SEED_TIME and not info.stopped:
                try:
                    client.stop(torrent.ref)
                except ClientError as exc:
                    logger.info("Download %d: the program did not stop the torrent: %s", row.id, exc.code)
                    db.commit()
                    continue
            local = arrival.local_path(info.path, item.mappings)
            if local is None or _in_a_library(db, local) or not any(
                    arrival.strictly_inside(local, root) for root in arrival.download_roots()):
                if row.seed_state != "kept":
                    # Said once, when it begins: the same look comes back every minute until a person ends it.
                    logger.info("Download %d: the torrent stays, its files are not below a download folder", row.id)
                row.seed_state = "kept"
                db.commit()
                continue
            try:
                remove_own(client, torrent.ref, delete_files=True)
            except ClientError as exc:
                logger.info("Download %d: the program did not remove the torrent: %s", row.id, exc.code)
                db.commit()
                continue
            row.seed_state = "done"
            db.commit()
            logger.info("Download %d: the torrent reached its goal and left the program", row.id)


def finish_now(db: Session, row: Download) -> None:
    """"Seeden beenden": the torrent of a filed book leaves its program now, with its files when nexbooks sees them
    below a download folder (a book in a library is never taken away). A program that does not answer is an error."""
    from ...errors import error

    client_row = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
    if client_row is None:
        row.seed_state = "done"
        db.commit()
        return
    found = db.scalars(select(PathMapping).where(PathMapping.client_id == client_row.id))
    mappings = [(m.remote, m.local) for m in found]
    try:
        with open_client(programs.target_of(client_row)) as client:
            info = client.seeding([row.client_ref.lower()]).get(row.client_ref.lower())
            files = False
            if info is not None:
                local = arrival.local_path(info.path, mappings)
                files = local is not None and not _in_a_library(db, local) and any(
                    arrival.strictly_inside(local, root) for root in arrival.download_roots())
                if client_row.kind in OWN_SEED_TIME and not info.stopped:
                    client.stop(row.client_ref.lower())
            # A torrent that is not in nexbooks's category (any more) is another's: it stays where it is.
            remove_own(client, row.client_ref.lower(), delete_files=files)
    except ClientError as exc:
        raise error(exc.code, "The download program did not answer.", exc.status) from exc
    row.seed_state, row.updated_at = "done", wall.now()
    db.commit()
