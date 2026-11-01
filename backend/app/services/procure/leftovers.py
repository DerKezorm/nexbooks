"""Folders a Usenet program leaves behind after nexbooks took a failed job out of it.

nexbooks removes a failed job from SABnzbd or NZBGet with its files. The program does not always take the folder it
moved the job into (``_FAILED_<name>``). The sweep runs a minute after a failed job was removed and otherwise every
ten minutes: for each enabled Usenet program, the folder of its category (through the confirmed path mappings, read
only below a root nexbooks may read) is looked at, and a folder ``_FAILED_<name>`` goes when

* its name belongs to a failed download of this program (a program shortens long names, so the release name starts with
  it; at least ``MIN_NAME`` characters),
* it is a direct child of the category folder and lies strictly inside a root,
* it is no link and holds no link, no book, no recording and no archive: only what is useless without them.

Anything else stays, and so does everything outside the category folder. Every removal is a log line.

**The files of a release the check turned down.** A Usenet program takes such a job out of its history, and its files
stay where it finished them: a program deletes what lies in its folder of unfinished jobs, not what it moved to the
finished ones. So the sweep looks at the downloads of Usenet that ended turned down or taken away, whose job has left
the program: their own folder (its name is the release's, strictly inside a download root, in no library and not the
data folder, holding neither, no folder of a category and no folder of another release) loses the books, the recordings,
the archives and the rests of a release (``importing.is_job_rest``). Anything else stays and is left alone from then on.
A download that waits for a person keeps its files: they are what the person looks at. A torrent keeps them for its
program, which seeds them. A row that must wait (the same release is on its way again, its program does not answer)
keeps its turn and takes no room in the round.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections.abc import Collection
from pathlib import Path

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, aliased

from ...db import SessionLocal
from ...models import ACTIVE_DOWNLOADS, CLIENT_PROTOCOL, Download, DownloadClient
from . import arrival, foreign, programs
from .clients import ClientError, open_client

logger = logging.getLogger("nexbooks.procure")

FAILED_PREFIX = "_FAILED_"
MIN_NAME = 12
SWEEP_EVERY_SECONDS = 600.0
AFTER_REMOVAL_SECONDS = 60.0
USEFUL = frozenset({"epub", "azw3", "mobi", "pdf", "mp3", "m4b", "m4a", "aac", "flac", "ogg", "opus", "zip", "rar",
                    "7z", "cbz", "cbr"})

_lock = threading.Lock()
_next_sweep: float | None = None


def clock() -> float:
    return time.monotonic()


def after_removal() -> None:
    """A failed job left its program: look at its folder a minute from now."""
    global _next_sweep
    with _lock:
        soon = clock() + AFTER_REMOVAL_SECONDS
        _next_sweep = soon if _next_sweep is None else min(_next_sweep, soon)


def reset() -> None:
    global _next_sweep
    with _lock:
        _next_sweep = None


def due() -> bool:
    with _lock:
        return _next_sweep is None or clock() >= _next_sweep


def useless(folder: Path) -> bool:
    """No link, no book, no recording and no archive anywhere below."""
    if arrival.is_link(folder) or not folder.is_dir():
        return False
    for root, dirs, names in os.walk(folder, followlinks=False):
        for name in [*dirs, *names]:
            if arrival.is_link(Path(root) / name):
                return False
        for name in names:
            if name.rsplit(".", 1)[-1].lower() in USEFUL:
                return False
    return True


def owner_of(folder_name: str, names: list[tuple[int, str]]) -> int | None:
    """The failed download a ``_FAILED_`` folder belongs to, or None."""
    if not folder_name.upper().startswith(FAILED_PREFIX):
        return None
    name = foreign.norm(folder_name[len(FAILED_PREFIX):])
    if len(name) < MIN_NAME:
        return None
    return next((download_id for download_id, release in names if release.startswith(name)), None)


def clean(category: Path, names: list[tuple[int, str]]) -> int:
    """The ``_FAILED_`` folders of failed downloads in one category folder that hold nothing useful. Returns how many
    went."""
    removed = 0
    try:
        entries = sorted(category.iterdir())
    except OSError:
        return 0
    for entry in entries:
        download_id = owner_of(entry.name, names)
        if download_id is None or not any(arrival.strictly_inside(entry, root) for root in arrival.roots()):
            continue
        found = arrival.resolved(entry)
        if found is None or found.parent != category or not useless(found):
            continue
        try:
            shutil.rmtree(found)
        except OSError:
            logger.info("Download %d: the folder the program left could not be removed", download_id)
            continue
        removed += 1
        logger.info("Download %d: the empty folder the program left after the failure is removed", download_id)
    return removed


#: How many folders of turned-down jobs one sweep looks at.
TURNED_DOWN_PER_SWEEP = 50


def _others_in(db: Session, row: Download, folder: Path) -> bool:
    """Whether a download of another release lies in the folder, or in a folder of its own below it."""
    names = (str(folder), row.source_path)
    return db.scalar(select(Download.id).where(
        Download.id != row.id, Download.title != row.title, Download.source_path != "",
        or_(*[or_(Download.source_path == name, Download.source_path.startswith(f"{name}{os.sep}", autoescape=True))
              for name in names])).limit(1)) is not None


def _own_folder(db: Session, row: Download, categories: Collection[Path]) -> Path | None:
    """The folder of a job that went, when it is the job's alone (see the module)."""
    from . import importing

    source = Path(row.source_path)
    if arrival.is_link(source) or not source.is_dir():
        return None
    real = arrival.resolved(source)
    if real is None or arrival.in_data(real) or arrival.in_a_library(real) or arrival.holds_a_library(real):
        return None
    if not any(arrival.strictly_inside(real, root) for root in arrival.download_roots()):
        return None
    if real in categories or real.name.casefold() == row.category.casefold() or _others_in(db, row, real):
        return None
    return real if importing.named_like_job(real.name, row.title) else None


def turned_down(*, categories: Collection[Path] = (), waiting: Collection[int] = ()) -> int:
    """Clears the folders of the jobs of Usenet the check turned down or a person took away. ``categories``: the folders
    of the programs' categories, resolved; ``waiting``: the programs that did not say which that is. Returns how many
    files went."""
    from . import importing

    def known(_relative: str, path: Path) -> bool:
        return path.name.rsplit(".", 1)[-1].lower() in USEFUL or importing.is_job_rest(path.name)

    gone = 0
    with SessionLocal() as db:
        # (The same release on its way again: the folder may be its own by now. In the query, so that such rows take no
        # room in the round.)
        twin = aliased(Download)
        again = exists().where(twin.id != Download.id, twin.state.in_(ACTIVE_DOWNLOADS), or_(
            twin.source_path == Download.source_path, twin.title == Download.title))
        rows = list(db.scalars(select(Download).where(
            Download.protocol == "usenet", Download.state.in_(("rejected", "removed")), Download.source_path != "",
            Download.removal_pending.is_(False), ~again,
            or_(Download.client_id.is_(None), Download.client_id.not_in(waiting))
        ).order_by(Download.id).limit(TURNED_DOWN_PER_SWEEP)))
        for row in rows:
            folder = _own_folder(db, row, categories)
            if folder is not None:
                removed, kept = importing.clear_job_folder(folder, known)
                gone += removed
                logger.info("Download %d: %d files of the job it was turned down for went, %d stayed", row.id, removed,
                            kept)
            row.source_path = ""
        db.commit()
    return gone


def sweep() -> int:
    """Looks at the category folder of every enabled Usenet program once, and at the folders of the jobs that were
    turned down. Returns how many folders went."""
    global _next_sweep
    with _lock:
        _next_sweep = clock() + SWEEP_EVERY_SECONDS
    removed = 0
    with SessionLocal() as db:
        found = []
        for row in db.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True))):
            if CLIENT_PROTOCOL.get(row.kind) != "usenet":
                continue
            names = [(item.id, foreign.norm(item.title)) for item in db.scalars(select(Download).where(
                Download.client_id == row.id, Download.state == "failed"))]
            left = db.scalar(select(Download.id).where(
                Download.client_id == row.id, Download.state.in_(("rejected", "removed")),
                Download.source_path != "").limit(1)) is not None
            if names or left:
                found.append((row.id, programs.target_of(row), names, _mappings(db, row.id)))
    categories: set[Path] = set()
    waiting: set[int] = set()
    cleaning = []
    for client_id, target, names, mappings in found:
        try:
            with open_client(target) as client:
                remote = client.download_folder()
        except ClientError as exc:
            logger.info("Download program %d: its category folder is not known: %s", client_id, exc.code)
            waiting.add(client_id)
            continue
        category = arrival.local_path(remote, mappings) if remote else None
        real = arrival.resolved(category) if category is not None else None
        if real is not None:
            categories.add(real)
        if category is None or not category.is_dir() or category.name.casefold() != target.category.casefold():
            continue
        cleaning.append((category, names))
    turned_down(categories=categories, waiting=waiting)
    for category, names in cleaning:
        removed += clean(category, names)
    return removed


def _mappings(db: Session, client_id: int) -> list[tuple[str, str]]:
    from ...models import PathMapping

    rows = db.scalars(select(PathMapping).where(PathMapping.client_id == client_id))
    return [(row.remote, row.local) for row in rows]
