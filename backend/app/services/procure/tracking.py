"""Following the downloads in their programs.

* A loop looks every 5 seconds whether a round is due: every 15 seconds, and 5 seconds after a load.
* A round asks each enabled program that has downloads nexbooks loaded and that the program still works on (queued,
  downloading, paused, or a problem the program reported before the download was finished), filtered by the category.
  Anything else in the category is left alone (``foreign`` lists it for a person).
* Downloads are matched by the program's name for the job. What the program says becomes the download's state,
  progress, remaining time, size and the program's own state.
* A download a program does not know twice in a row is the problem ``gone_from_client``. A hand-over the program did not
  answer (``unsure``) is looked for by its name (``foreign.look``) and ends as ``not_taken`` after ten minutes.
* A job the program holds paused for ``PAUSED_FOR`` is the problem ``paused``: nobody else would notice, the wish waits
  for it for ever. It ends by itself when the job goes on.
* A program that cannot be asked keeps its downloads as they are, and gets its error code (``last_error``).
* **Failed** (SABnzbd, NZBGet): the release goes on the blocklist of the book, a line in the history, the job leaves the
  program with its files (kept and tried again every round when the program did not answer), and the wish goes on
  with the next fitting release (``wishes.after_failure``). A ``_FAILED_`` folder a program leaves anyway goes a minute
  later (``leftovers``).
* **Done**: the finished path is turned into a path of this machine (``arrival``). A download whose files nexbooks does
  not see, or whose library does not take a file, is a problem with a way out; it is looked at again every round. One
  that goes on is ``completed`` ("waits to be filed") and its filing is asked for (``importing``).
* Once a minute the torrents that were filed are looked at for their seed goal (``seeding``), the category of every
  program for jobs nobody asked for (``foreign``), and now and then the folders of failed jobs (``leftovers``) and the
  retention of the news servers (``retention``).
* Log lines carry numbers and codes, never titles.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock as wall
from ...db import SessionLocal
from ...models import Download, DownloadClient, PathMapping
from . import arrival, blocklist, foreign, history, leftovers, loading, programs, retention, seeding, wishes
from .clients import ClientError, Job, Target, open_client, remove_own

logger = logging.getLogger("nexbooks.procure")

INTERVAL_SECONDS = 5.0
ROUND_SECONDS = 15.0
SOON_SECONDS = 5.0
#: Rounds in a row a download may be missing before it is a problem.
GONE_AFTER = 2
#: A hand-over no program has taken after this long was not taken.
UNSURE_FOR = timedelta(minutes=10)
#: A job paused in its program for this long waits for a person.
PAUSED_FOR = timedelta(hours=6)
#: A finished download that waits for a person (``problem``) is looked at for this long every round.
RECHECKED = ("path_not_found", "no_write")
#: Problems about a job the program still has: the round asks for them.
OF_THE_JOB = ("client_error", "no_space", "gone_from_client", "stalled", "paused")

_lock = threading.Lock()
_last_round: float | None = None
_soon_at: float | None = None


def clock() -> float:
    return time.monotonic()


def soon() -> None:
    """Ask the programs 5 seconds from now, for example after a load."""
    global _soon_at
    with _lock:
        _soon_at = clock() + SOON_SECONDS


def reset() -> None:
    global _last_round, _soon_at, _last_tidy
    with _lock:
        _last_round = None
        _soon_at = None
        _last_tidy = None
    leftovers.reset()
    foreign.reset()
    seeding.reset()
    retention.reset()


def due() -> bool:
    moment = clock()
    with _lock:
        if _soon_at is not None and moment >= _soon_at:
            return True
        return _last_round is None or moment - _last_round >= ROUND_SECONDS


async def run_forever(stop: asyncio.Event) -> None:
    """The background loop: a round now and then, never two at once."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        if not due():
            continue
        try:
            await asyncio.to_thread(run_round)
        except Exception:
            logger.exception("A round of following the downloads failed")


# --- What to ask ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Work:
    client_id: int
    #: ⚠️ With the decrypted secret.
    target: Target = field(repr=False)
    refs: list[str] = field(default_factory=list)
    #: The downloads to take out of the program: download id and the program's name for the job.
    removals: list[tuple[int, str]] = field(default_factory=list)


def _tracked_clause() -> object:
    return (Download.state.in_(("queued", "downloading", "paused", "unsure"))
            | ((Download.state == "problem") & Download.completed_at.is_(None)))


def _work(db: Session) -> list[_Work]:
    by_client: dict[int, list[str]] = {}
    for client_id, ref in db.execute(select(Download.client_id, Download.client_ref).where(
            Download.client_id.is_not(None), Download.client_ref != "", _tracked_clause())).all():
        by_client.setdefault(int(client_id), []).append(ref)
    left: dict[int, list[tuple[int, str]]] = {}
    for download_id, client_id, ref in db.execute(select(Download.id, Download.client_id, Download.client_ref).where(
            Download.removal_pending.is_(True), Download.client_id.is_not(None), Download.client_ref != "")).all():
        left.setdefault(int(client_id), []).append((download_id, ref))
    work: list[_Work] = []
    for client in db.scalars(select(DownloadClient).where(
            DownloadClient.id.in_(sorted(set(by_client) | set(left))), DownloadClient.enabled.is_(True))):
        work.append(_Work(client_id=client.id, target=programs.target_of(client), refs=by_client.get(client.id, []),
                          removals=left.get(client.id, [])))
    return work


def mappings_of(db: Session, client_id: int | None) -> list[tuple[str, str]]:
    if client_id is None:
        return []
    rows = db.scalars(select(PathMapping).where(PathMapping.client_id == client_id))
    return [(row.remote, row.local) for row in rows]


# --- Writing what the program said ----------------------------------------------------------------------------------


def _problem(row: Download, code: str, values: dict | None = None) -> None:
    row.state, row.problem_code, row.problem_values = "problem", code, values or {}


def _arrive(db: Session, row: Download, reported: str | None) -> None:
    """The job is done in the program: can the files be reached, and does the library take a file?"""
    from ...models import Library

    library = db.get(Library, row.library_id) if row.library_id is not None else None
    found = arrival.arrive(reported, mappings_of(db, row.client_id), library)
    if found.problem:
        _problem(row, found.problem, found.values)
    else:
        from . import importing

        row.state, row.problem_code, row.problem_values = "completed", "", None
        importing.request(db, row)


def _follow(db: Session, row: Download, job: Job | None, failed: list[Download]) -> None:
    moment = wall.now()
    if job is None:
        if row.state == "unsure":
            return
        row.missing_count = (row.missing_count or 0) + 1
        if row.missing_count >= GONE_AFTER and row.problem_code != "gone_from_client":
            _problem(row, "gone_from_client")
            row.updated_at = moment
            logger.info("Download %d is gone from its program", row.id)
        return
    before = (row.state, row.progress, row.remaining_seconds, row.problem_code)
    row.missing_count = 0
    row.client_state = job.client_state[:64]
    if job.progress is not None:
        row.progress = job.progress
    row.remaining_seconds = job.remaining_seconds
    if job.size_bytes:
        row.size = job.size_bytes
    if job.state == "completed":
        row.reported_path = (job.path or "")[:4096]
        row.completed_at = row.completed_at or moment
        row.progress = 100.0
        _arrive(db, row, job.path)
    elif job.state == "failed":
        row.state, row.problem_code, row.problem_values = "failed", "", None
        row.failed_reason = job.reason or "client_failed"
        row.failed_detail = job.detail or ""
        row.finished_at = moment
        failed.append(row)
    elif job.state == "problem":
        if job.problem == "path_not_found":
            row.completed_at = row.completed_at or moment
            _arrive(db, row, None)
        else:
            _problem(row, job.problem or "client_error")
    elif job.state == "paused" and (row.problem_code == "paused" or (
            row.state == "paused" and moment - row.updated_at >= PAUSED_FOR)):
        if row.problem_code != "paused":
            logger.info("Download %d stays paused in its program", row.id)
        _problem(row, "paused", {"client": row.client_name, "why": list(job.why)})
    else:
        row.state = job.state
        row.problem_code = "stalled" if job.problem == "stalled" else ""
        row.problem_values = None
    if (row.state, row.progress, row.remaining_seconds, row.problem_code) != before:
        row.updated_at = moment


def _fail(db: Session, row: Download) -> None:
    """A download failed in its program: the release is blocked for the book, the history says so, the job is taken out
    of the program (Usenet; a torrent that failed stays for its owner to see)."""
    title, author = wishes.book_text(db, row.user_id, work_id=row.work_id, foreign_id=row.foreign_id)
    key = blocklist.book_key({"authors": [author] if author else [], "title": title})
    blocklist.block(db, None, row, work_id=row.work_id, foreign_id=row.foreign_id if row.work_id is None else None,
                    reason="client_failed", book=key, library_id=row.library_id)
    history.record(db, row, "failed", {"code": row.failed_reason, "detail": row.failed_detail})
    if row.protocol == "usenet" and row.client_ref:
        row.removal_pending = True
    logger.info("Download %d failed in its program reason=%s", row.id, row.failed_reason)


def _apply(client_id: int, found: dict[str, Job], asked: list[str]) -> None:
    """Writes what the program said about the downloads it was asked about."""
    failed: list[Download] = []
    with SessionLocal() as db:
        client = db.get(DownloadClient, client_id)
        if client is None:
            return
        client.last_error, client.checked_at = "", wall.now()
        rows = list(db.scalars(select(Download).where(
            Download.client_id == client_id, Download.client_ref.in_(asked), _tracked_clause())))
        for row in rows:
            key = row.client_ref.lower() if row.protocol == "torrent" else row.client_ref
            _follow(db, row, found.get(key), failed)
        db.flush()
        for row in failed:
            _fail(db, row)
        db.commit()
        failures = [row.id for row in failed]
    for download_id in failures:
        with SessionLocal() as db:
            row = db.get(Download, download_id)
            if row is not None:
                try:
                    wishes.after_failure(db, row)
                except Exception:
                    db.rollback()
                    logger.exception("The wish of download %d could not go on", download_id)


def _client_failed(client_id: int, code: str) -> None:
    with SessionLocal() as db:
        client = db.get(DownloadClient, client_id)
        if client is not None:
            client.last_error, client.checked_at = code, wall.now()
            db.commit()


def _settle_removals(done: list[int]) -> None:
    if not done:
        return
    with SessionLocal() as db:
        for row in db.scalars(select(Download).where(Download.id.in_(done))):
            row.removal_pending = False
        db.commit()


def _expire_unsure() -> None:
    """A hand-over no program has taken for ten minutes was not taken: the download ends, the release is not blocked
    (it did not fail), the wish searches again."""
    gone: list[int] = []
    with SessionLocal() as db:
        for row in db.scalars(select(Download).where(Download.state == "unsure")):
            if wall.now() - row.updated_at >= UNSURE_FOR:
                row.state, row.failed_reason, row.finished_at = "failed", "not_taken", wall.now()
                history.record(db, row, "failed", {"code": "not_taken"})
                gone.append(row.id)
        db.commit()
    for download_id in gone:
        with SessionLocal() as db:
            row = db.get(Download, download_id)
            if row is not None:
                wishes.after_failure(db, row)
        logger.info("Download %d was not taken by its program", download_id)


def _recheck() -> None:
    """A finished download that waits for a person goes on by itself when the files are there now, or the library takes
    a file (the operator confirmed a mapping, a folder got its right).

    A download whose files are still not to be seen costs nothing here: the path is only looked at. The search below the
    roots for a place to suggest (seconds on a big tree) is kept per path and made again at most once in ten minutes,
    when the roots change, and for one path in a round."""
    searches = 1
    with SessionLocal() as db:
        for row in db.scalars(select(Download).where(Download.state == "problem",
                                                     Download.problem_code.in_(RECHECKED))):
            before = (row.state, row.problem_code)
            seen = arrival.local_path(row.reported_path or None, mappings_of(db, row.client_id))
            if row.problem_code == "path_not_found" and (seen is None or arrival.below_working_folder(seen)):
                if searches and arrival.kept_lookout(row.reported_path or None) is None:
                    searches -= 1
                    found = arrival.lookout_for(row.reported_path or None)
                    if found is not None:
                        row.problem_values = found.values(row.reported_path or None)
                continue
            _arrive(db, row, row.reported_path or None)
            if (row.state, row.problem_code) != before:
                row.updated_at = wall.now()
        db.commit()


# --- A round ---------------------------------------------------------------------------------------------------------


def run_round() -> None:
    """Asks every program once, then looks at what else is due. Every program and every step on its own: one that
    breaks (an answer nexbooks cannot read, a fault of its own) is written down and the round goes on with the
    others."""
    global _last_round, _soon_at
    with _lock:
        _last_round = clock()
        _soon_at = None
    with SessionLocal() as db:
        work = _work(db)
    for item in work:
        try:
            _ask(item)
        except Exception as exc:  # noqa: BLE001 - one program never holds the others up
            logger.warning("Download program %d: its part of the round broke off (%s)", item.client_id,
                           type(exc).__name__)
    _step("unsure", _expire_unsure)
    _step("sending", _reap_sending)
    _step("recheck", _recheck)
    if foreign.due():
        _step("foreign", foreign.look)
    if leftovers.due():
        _step("leftovers", leftovers.sweep)
    if seeding.due():
        _step("seeding", seeding.look)
    _step("retention", lambda: retention.refresh(only_when_due=True))
    _step("tidy", _now_and_then)


def _ask(item: _Work) -> None:
    """One program's part of a round: what its downloads do, and the jobs to take out of it."""
    if item.refs:
        try:
            with open_client(item.target) as client:
                found = client.jobs(item.refs)
        except ClientError as exc:
            logger.info("Download program %d could not be asked: %s", item.client_id, exc.code)
            _client_failed(item.client_id, exc.code)
            return
        _apply(item.client_id, found, item.refs)
    done: list[int] = []
    for download_id, ref in item.removals:
        try:
            with open_client(item.target) as client:
                remove_own(client, ref, delete_files=True)
        except ClientError as exc:
            logger.debug("Download program %d did not remove download %d: %s", item.client_id, download_id,
                         exc.code)
            break
        done.append(download_id)
    _settle_removals(done)
    if done:
        leftovers.after_removal()


def _reap_sending() -> None:
    with SessionLocal() as db:
        loading.reap_sending(db, older_than=loading.STALE_SENDING)


def _step(name: str, work: Callable[[], object]) -> None:
    """One step of the round that is not a program's: a step that breaks is written down (its kind, never its words:
    they may hold a path), and the next one runs."""
    try:
        work()
    except Exception as exc:  # noqa: BLE001 - the round goes on
        logger.warning("The step %s of following the downloads broke off (%s)", name, type(exc).__name__)


#: How often the trash and the uploads nobody finished are looked at.
TIDY_SECONDS = 3600.0
_last_tidy: float | None = None


def _now_and_then() -> None:
    """Once an hour: the trash entries whose time is over go, and so do uploads nobody finished; a finished download
    that lost its filing job gets it again."""
    global _last_tidy
    moment = clock()
    with _lock:
        if _last_tidy is not None and moment - _last_tidy < TIDY_SECONDS:
            return
        _last_tidy = moment
    from . import importing, trash, uploads

    with SessionLocal() as db:
        try:
            trash.sweep(db)
            uploads.sweep(db)
            importing.request_due(db)
        except Exception:
            db.rollback()
            logger.exception("Tidying the trash and the uploads failed")
