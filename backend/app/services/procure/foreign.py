"""Jobs nobody asked nexbooks for, and hand-overs that got no answer.

Once a minute every enabled download program lists its whole category (``category_jobs``):

* A job that belongs to a download of nexbooks (its name is the ``client_ref`` of one) is left to tracking.
* A hand-over that got no answer in time (``unsure``, Usenet, no job name yet) is looked for by the name of the file it
  was sent as: a job of that name is the download's, which then goes on as ``queued`` with the job's name.
* Anything else is a **foreign job**: a row in ``foreign_jobs``, shown under "Braucht dich" until a person ignores it or
  has it removed from the program. nexbooks never takes one over by itself, and never touches it.

A client that cannot be asked keeps its rows as they are. Rows of jobs the program no longer lists go.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata

from sqlalchemy import delete, select

from ... import clock as wall
from ...db import SessionLocal
from ...models import Download, DownloadClient, ForeignJob
from . import loading, programs
from .clients import CategoryJob, ClientError, open_client

logger = logging.getLogger("nexbooks.procure")

LOOK_EVERY_SECONDS = 60.0
#: A shortened job name must still say which release it was.
MIN_NAME = 12

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


def norm(name: str) -> str:
    """A name without its extension, folded to letters and digits: a file name and the job made of it compare equal."""
    text = unicodedata.normalize("NFC", name).casefold()
    text = re.sub(r"\.(nzb|torrent)$", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def same_job(job_name: str, release_title: str, protocol: str) -> bool:
    """Whether a job of the program is the one a release was sent as."""
    sent = norm(loading.file_name(release_title, protocol))
    found = norm(job_name)
    if not sent or not found:
        return False
    if found == sent:
        return True
    return len(found) >= MIN_NAME and (sent.startswith(found) or found.startswith(sent))


def look() -> None:
    """One look at every enabled program."""
    global _next_look
    with _lock:
        _next_look = clock() + LOOK_EVERY_SECONDS
    with SessionLocal() as db:
        clients = [(row.id, programs.target_of(row)) for row in db.scalars(
            select(DownloadClient).where(DownloadClient.enabled.is_(True)).order_by(DownloadClient.id))]
    for client_id, target in clients:
        try:
            with open_client(target) as client:
                listed = client.category_jobs()
        except ClientError as exc:
            logger.info("Download program %d: its category could not be read: %s", client_id, exc.code)
            continue
        _settle(client_id, listed)


def _settle(client_id: int, listed: list[CategoryJob]) -> None:
    moment = wall.now()
    with SessionLocal() as db:
        owned = {row.client_ref.lower() if row.protocol == "torrent" else row.client_ref
                 for row in db.scalars(select(Download).where(Download.client_id == client_id,
                                                                Download.client_ref != ""))}
        unsure = list(db.scalars(select(Download).where(
            Download.client_id == client_id, Download.state == "unsure", Download.client_ref == "")))
        seen: set[str] = set()
        for item in listed:
            ref = item.job.ref
            key = ref.lower()
            if ref in owned or key in owned:
                continue
            mine = next((row for row in unsure if same_job(item.name, row.title, row.protocol)), None)
            if mine is not None:
                mine.client_ref = ref[:128]
                mine.state, mine.updated_at = "queued", moment
                unsure.remove(mine)
                logger.info("Download %d was taken by its program after all", mine.id)
                continue
            seen.add(ref)
            row = db.scalar(select(ForeignJob).where(ForeignJob.client_id == client_id, ForeignJob.ref == ref))
            if row is None:
                db.add(ForeignJob(client_id=client_id, ref=ref[:128], name=item.name[:400],
                                  job_state=item.job.state, first_seen_at=moment, last_seen_at=moment))
                logger.info("A job nexbooks did not ask for is in the category of program %d", client_id)
            else:
                row.name, row.job_state, row.last_seen_at = item.name[:400], item.job.state, moment
        gone = [row.id for row in db.scalars(select(ForeignJob).where(ForeignJob.client_id == client_id))
                if row.ref not in seen]
        if gone:
            db.execute(delete(ForeignJob).where(ForeignJob.id.in_(gone)))
        db.commit()
