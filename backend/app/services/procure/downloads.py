"""What a person sees and does with the downloads: the lists "Läuft" and "Braucht dich", and the
actions on them.

* **Who sees what**: a download of a library the person may ask for books in (the right ``acquire``), and a work only
  when they see it. The jobs in the category nobody asked for (``foreign_jobs``) are seen by everybody who may ask for
  books anywhere.
* **Cancel** asks first in the interface: the job leaves the program with its files, and the release is blocked for the
  book when the person says so; the wish goes on searching then, else it waits (``idle``). A program that does not
  answer keeps the job to be taken out later (``removal_pending``, tried every round).
* **Path mapping**: the operator confirms the one place ``look_for`` found for the name of the job, or picks one of
  several; nothing else is ever stored.
* ``importing`` files a finished download and calls ``mark_imported``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import CLIENT_PROTOCOL, OPERATOR, Account, Download, DownloadClient, ForeignJob, Library, PathMapping
from .. import libraries
from . import arrival, blocklist, history, programs, seeding, tracking, wishes
from .clients import ClientError, open_client, remove_own

logger = logging.getLogger("nexbooks.procure")

RUNNING = ("sending", "unsure", "queued", "downloading", "paused", "completed")
CANCELLABLE = ("unsure", "queued", "downloading", "paused", "completed", "problem")


def _allowed(uid: int) -> object:
    """The downloads of the libraries the person may ask for books in, of the works they see (rights in the SQL)."""
    return and_(Download.library_id.in_(libraries.acquirable_library_ids(uid)), wishes.seen_work(uid, Download.work_id))


def _with_books(db: Session, account: Account, rows: list[Download]) -> list[tuple[Download, wishes.BookInfo]]:
    books = wishes.books_for(db, account.id, [(row.id, row.work_id, row.foreign_id) for row in rows])
    for row in rows:
        if row.work_id is None and row.foreign_id is None:
            # An upload of a book nexbooks did not know: it is called by its files until it is filed.
            books[row.id] = wishes.BookInfo(work_id=None, token=None, title=row.title or "")
    return [(row, books[row.id]) for row in rows if row.id in books]


def running(db: Session, account: Account, *, mine: bool = False) -> list[tuple[Download, wishes.BookInfo]]:
    """The downloads that run (and a filed torrent that still seeds), the newest first."""
    query = select(Download).where(_allowed(account.id), Download.state.in_(RUNNING)
                                   | ((Download.state == "imported") & (Download.seed_state == "watching")))
    if mine:
        query = query.where(Download.user_id == account.id)
    return _with_books(db, account, list(db.scalars(query.order_by(Download.created_at.desc(), Download.id.desc())
                                                    .limit(500))))


#: A download under "Braucht dich": one that waits for somebody. What the check turned down asks nothing of anybody (the
#: release is blocked and the search goes on): it stands in the history and on the blocklist.
NEEDS = Download.state == "problem"


def problems(db: Session, account: Account) -> list[tuple[Download, wishes.BookInfo]]:
    return _with_books(db, account, list(db.scalars(select(Download).where(
        _allowed(account.id), NEEDS).order_by(Download.updated_at.desc()).limit(500))))


def foreign_jobs(db: Session, account: Account) -> list[tuple[ForeignJob, DownloadClient]]:
    """The jobs in the category nobody asked for: for the operator, who has the programs, and nobody else. They may be
    anything the operator put there by hand, and the programs are shared by every library."""
    if account.role != OPERATOR or db.scalar(libraries.acquirable_library_ids(account.id).limit(1)) is None:
        return []
    return [(job, client) for job, client in db.execute(
        select(ForeignJob, DownloadClient).join(DownloadClient, DownloadClient.id == ForeignJob.client_id)
        .where(ForeignJob.ignored.is_(False), DownloadClient.enabled.is_(True)).order_by(ForeignJob.id.desc())).all()]


def get(db: Session, account: Account, download_id: int) -> Download:
    """A download of a library the person may ask for books in, of a work they see; 404 for anybody else."""
    row = db.scalar(select(Download).where(Download.id == download_id, _allowed(account.id)))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


# --- Actions ---------------------------------------------------------------------------------------------------------


def _take_out(db: Session, row: Download) -> bool:
    """Takes the job out of the program with its files, when it is in nexbooks's category (an unsure hand-over of a
    torrent the program held already, in another category, is not nexbooks's: the download just ends). False (and the
    job kept to be taken out later) when the program does not answer."""
    if not row.client_ref or row.client_id is None:
        return True
    client = db.get(DownloadClient, row.client_id)
    if client is None:
        return True
    try:
        with open_client(programs.target_of(client)) as opened:
            remove_own(opened, row.client_ref, delete_files=True)
    except ClientError as exc:
        logger.info("Download program %d did not remove download %d: %s", client.id, row.id, exc.code)
        return False
    return True


def cancel(db: Session, account: Account, download_id: int, *, block: bool, reason: str = "by_hand") -> Download:
    """The download is taken away; with ``block`` its release is never taken for the book again and the wish
    searches on."""
    row = get(db, account, download_id)
    if row.state not in CANCELLABLE:
        raise error("not_cancellable", "This download cannot be cancelled now.", 409)
    if block and row.protocol != "upload":
        title, author = wishes.book_text(db, row.user_id, work_id=row.work_id, foreign_id=row.foreign_id)
        key = blocklist.book_key({"authors": [author] if author else [], "title": title})
        blocklist.block(db, account, row, work_id=row.work_id,
                        foreign_id=row.foreign_id if row.work_id is None else None, reason=reason, book=key,
                        library_id=row.library_id)
    moment = clock.now()
    row.state, row.finished_at, row.updated_at = "removed", moment, moment
    row.problem_code, row.problem_values = "", None
    row.removal_pending = not _take_out(db, row) and bool(row.client_ref)
    history.record(db, row, "removed", {"code": "cancelled", "blocked": block})
    db.commit()
    from . import importing

    importing.tidy_after_cancel(db, row)
    wishes.after_cancel(db, row, search_on=block)
    logger.info("Download %d cancelled by a person block=%s", row.id, block)
    return row


def recheck(db: Session, account: Account, download_id: int) -> Download:
    """Looks again at a download that needs somebody: the files, the library, or the job in the program."""
    row = get(db, account, download_id)
    if row.state != "problem":
        raise error("not_a_problem", "This download has no problem.", 409)
    from . import importing

    if row.protocol == "upload" or (row.completed_at is not None and row.problem_code in importing.AFTER_ARRIVAL):
        # A problem of the filing (the library took no file, there was no room, a goal was taken): it goes on from the
        # step it stopped at.
        importing.go_on(db, row)
    elif row.problem_code in tracking.RECHECKED:
        library = db.get(Library, row.library_id) if row.library_id is not None else None
        found = arrival.arrive(row.reported_path or None, tracking.mappings_of(db, row.client_id), library)
        if found.problem:
            row.problem_code, row.problem_values = found.problem, found.values
        else:
            row.state, row.problem_code, row.problem_values = "completed", "", None
            importing.request(db, row)
    elif row.completed_at is None:
        row.state, row.problem_code, row.problem_values, row.missing_count = "downloading", "", None, 0
        tracking.soon()
    row.updated_at = clock.now()
    db.commit()
    return row


def choose_library(db: Session, account: Account, download_id: int, library_id: int) -> Download:
    """Another library for a finished download that cannot go where it was meant to."""
    row = get(db, account, download_id)
    chosen = db.scalar(select(Library).where(Library.id == library_id,
                                             Library.id.in_(libraries.acquirable_library_ids(account.id))))
    if chosen is None:
        raise error("library_not_allowed", "You may not ask for books in this library.", 403)
    if not libraries.kind_allows(chosen.kind, row.kind):
        raise error("library_kind", "This library does not take books of this kind.", 422)
    from ...models import PlaceFile

    if db.scalar(select(PlaceFile.id).where(PlaceFile.download_id == row.id, PlaceFile.state == "placed").limit(1)):
        raise error("already_placed", "Files of this download lie in its library already.", 409)
    if row.library_id != chosen.id and row.place_step:
        # What was judged and planned for the other library (what it has, where the files go) is judged anew.
        row.place_step = ""
        db.execute(update(PlaceFile).where(PlaceFile.download_id == row.id).values(target=""))
    row.library_id = chosen.id
    db.commit()
    if row.state == "problem":
        return recheck(db, account, download_id)
    return row


def manages(db: Session, account: Account, row: Download) -> bool:
    """Whether the person looks after the library the download goes into (the right ``manage``)."""
    return row.library_id is not None and libraries.at_least(
        libraries.right_in(db, account.id, row.library_id), libraries.MANAGE)


def _mapped(db: Session, account: Account, download_id: int) -> Download:
    """The download a path mapping is for: a problem about its path, in a library the operator looks after. The
    operator who does not is told nothing of it, not even its name (the reported path holds the release's)."""
    row = db.get(Download, download_id)
    if (row is None or account.role != OPERATOR or row.state != "problem" or row.problem_code != "path_not_found"
            or not manages(db, account, row)):
        raise error("not_found", "Not found.", 404)
    return row


def mapping_options(db: Session, account: Account, download_id: int) -> dict[str, object]:
    """What the search for the folder of a download found: one place (a proposal), several (a choice), or why not."""
    row = _mapped(db, account, download_id)
    found = arrival.look_for(row.reported_path or None)
    return {"reported": row.reported_path, **found.values(row.reported_path)}


def confirm_mapping(db: Session, account: Account, download_id: int, remote: str, local: str) -> PathMapping:
    """The operator confirms what ``look_for`` found: the proposal itself, or the local place picked among several.
    Anything else is refused, whatever the interface sent."""
    row = _mapped(db, account, download_id)
    if row.client_id is None:
        raise error("not_found", "Not found.", 404)
    found = arrival.look_for(row.reported_path or None)
    pair: dict[str, str] | None = None
    if found.proposal is not None and found.proposal == {"remote": remote, "local": local}:
        pair = found.proposal
    else:
        for place in found.matches:
            candidate = arrival.mapping_for(row.reported_path or "", Path(place))
            if candidate is not None and candidate == {"remote": remote, "local": local}:
                pair = candidate
    if pair is None:
        raise error("mapping_not_offered", "nexbooks did not suggest this mapping.", 409)
    mapping = db.scalar(select(PathMapping).where(PathMapping.client_id == row.client_id,
                                                  PathMapping.remote == pair["remote"],
                                                  PathMapping.local == pair["local"]))
    if mapping is None:
        mapping = PathMapping(client_id=row.client_id, remote=pair["remote"], local=pair["local"],
                              created_at=clock.now())
        db.add(mapping)
        db.commit()
        logger.info("Path mapping confirmed for program %d", row.client_id)
    tracking.soon()
    return mapping


def ignore_foreign(db: Session, account: Account, job_id: int) -> None:
    job = _foreign(db, account, job_id)
    job.ignored = True
    db.commit()


def remove_foreign(db: Session, account: Account, job_id: int) -> bool:
    """The foreign job leaves its program with its files. False when the program did not answer (nothing is changed)."""
    job = _foreign(db, account, job_id)
    client = db.get(DownloadClient, job.client_id)
    if client is None:
        db.delete(job)
        db.commit()
        return True
    try:
        with open_client(programs.target_of(client)) as opened:
            remove_own(opened, job.ref, delete_files=True)
    except ClientError as exc:
        raise error(exc.code, "The download program did not remove the job.", exc.status) from exc
    db.delete(job)
    db.commit()
    return True


def _foreign(db: Session, account: Account, job_id: int) -> ForeignJob:
    if account.role != OPERATOR:
        raise error("forbidden", "Only the operator deals with the jobs nobody asked for.", 403)
    if db.scalar(libraries.acquirable_library_ids(account.id).limit(1)) is None:
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)
    job = db.get(ForeignJob, job_id)
    if job is None:
        raise error("not_found", "Not found.", 404)
    return job


def stop_seeding(db: Session, account: Account, download_id: int) -> Download:
    """"Seeden beenden": the torrent of a filed book leaves the program now."""
    row = get(db, account, download_id)
    if row.state != "imported" or row.protocol != "torrent" or row.seed_state == "done":
        raise error("not_seeding", "This download is not seeding.", 409)
    seeding.finish_now(db, row)
    return row


def mark_imported(db: Session, row: Download, work_id: int | None = None,
                  note: dict[str, object] | None = None, wish_done: bool = True) -> None:
    """The book is filed (``importing``): the download ends, the history says so, the wish is done (not with
    ``wish_done`` False: the files were another book, and the wish goes on)."""
    moment = clock.now()
    row.state, row.imported_at, row.finished_at, row.updated_at = "imported", moment, moment, moment
    row.problem_code, row.problem_values = "", None
    row.seed_state = "watching" if row.protocol == "torrent" else "done"
    history.record(db, row, "done", note)
    if wish_done:
        wishes.complete(db, row, work_id)
    db.commit()


# --- The questions of the check ---------------------------------------------------------------------


DECISIONS = ("keep", "pick", "whole", "take", "block", "acknowledge")


def decide(db: Session, account: Account, download_id: int, action: str, unit: str | None = None) -> Download:
    """What a person says about a download the check left a question about (``runtime``, ``bundle``,
    ``files_unassigned``) or turned down with a note (``language``):

    * ``keep``: the recording goes in as the version the question names (``runtime``: abridged, a play, else the
      unabridged reading); the e-book goes in beside the one the library has (``same_edition``: another edition);
    * ``pick``: the book ``unit`` of a bundle goes in, the rest stays out (``bundle``);
    * ``whole``: the recordings of a bundle are the parts of one book (``bundle``, when the question offers it);
    * ``take``: the files go in as the book wished, whatever their names say (``files_unassigned``; ``partial``, a part
      of the book; ``edition``, an edition with additions; ``pen_name``, another author in the files), or whatever
      language their package names (``language_unclear``: the package says one language, the text or the release
      another); the better format replaces the e-book the library has (``same_edition``: the same edition);
    * ``block``: the release is blocked, its files leave the program, the wish searches on (all three; asks first);
    * ``acknowledge``: the note of a turned-down release is read (``language``)."""
    from . import importing

    row = get(db, account, download_id)
    if action == "acknowledge":
        if row.state != "rejected" or not row.problem_code:
            raise error("not_a_problem", "This download has no note.", 409)
        row.problem_code, row.problem_values = "", None
        db.commit()
        return row
    if row.state != "problem" or row.problem_code not in importing.QUESTIONS:
        raise error("not_a_question", "This download asks nothing.", 409)
    code = row.problem_code
    if action == "block":
        return cancel(db, account, download_id, block=True, reason=importing.QUESTIONS[code])
    if action == "keep" and code == "runtime":
        reason = (row.problem_values or {}).get("reason")
        importing.go_on(db, row, {"variant": reason if reason in ("abridged", "dramatized") else "unabridged"})
    elif action == "pick" and code == "bundle":
        key = importing.unit_of_token(db, row, unit or "")
        if key is None:
            raise error("invalid_input", "No such book in the release.", 422, field="unit")
        importing.go_on(db, row, {"unit": key})
    elif action == "whole" and code == "bundle" and (row.problem_values or {}).get("whole"):
        importing.go_on(db, row, {"whole": True})
    elif action == "take" and code in ("files_unassigned", "partial", "edition", "pen_name"):
        importing.go_on(db, row, {"take": True})
    elif action in ("take", "keep") and code == "same_edition":
        # ``take``: the same edition, the better format replaces the old one; ``keep``: another one, both stay.
        importing.go_on(db, row, {"replace": action == "take"})
    elif action == "take" and code in ("language_unclear", "language_unknown"):
        # The language the person says the files are in: the one the question named, else the wish's.
        said = str((row.problem_values or {}).get("wanted") or row.language or "")[:8]
        importing.go_on(db, row, {"language": said or True})
    else:
        raise error("invalid_input", "This answer does not fit the question.", 422, field="action")
    db.commit()
    logger.info("Download %d: a person answered code=%s action=%s", row.id, code, action)
    return row


def assign_foreign(db: Session, account: Account, job_id: int, wish_id: int) -> Download:
    """The answer "Einem Buch zuordnen": a job nobody asked for becomes the download of an open wish (the operator, who
    may ask for books in the wish's library). Tracking follows it from the next round and files it like any other."""
    job = _foreign(db, account, job_id)
    client = db.get(DownloadClient, job.client_id)
    if client is None:
        raise error("not_found", "Not found.", 404)
    wish = wishes.get(db, account, wish_id)
    if wish.state in ("done", "loading"):
        raise error("wish_loading", "A release of this book is on its way.", 409)
    moment = clock.now()
    protocol = CLIENT_PROTOCOL.get(client.kind, "usenet")
    row = Download(user_id=account.id, work_id=wish.work_id, foreign_id=wish.foreign_id, library_id=wish.library_id,
                   kind=wish.kind, language=wish.language, title=job.name[:400], protocol=protocol,
                   info_hash=job.ref.lower()[:40] if protocol == "torrent" else "", client_id=client.id,
                   client_name=client.name, client_ref=job.ref, category=client.category, state="queued",
                   wish_id=wish.id, created_at=moment, updated_at=moment,
                   wished=wish.wished if isinstance(wish.wished, dict) else wishes.snapshot(
                       db, account.id, work_id=wish.work_id, foreign_id=wish.foreign_id, kind=wish.kind,
                       language=wish.language))
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        raise error("already_loading", "A release of this book is on its way.", 409) from None
    wish.state, wish.next_try_at, wish.updated_at = "loading", None, moment
    db.delete(job)
    db.commit()
    tracking.soon()
    logger.info("A foreign job became download %d of wish %d", row.id, wish.id)
    return row
