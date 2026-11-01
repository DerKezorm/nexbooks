"""Uploading books from the browser: a person drops the files of a book, from a
computer or a phone, and nexbooks files them **the same way as a download** (the check, the scheme, never over
anything).

* **An upload is a download** with the protocol ``upload``: it shows under "Läuft" while it is filed, under "Braucht
  dich" when something is unclear, in the history when it is done. While the files come it is ``uploading``; ``finish``
  makes it ``completed`` and asks for its filing (``importing``).
* **Who**: a person with the right ``acquire`` in the library the book goes into (the right to bring books into it).
  The library must take files (else ``no_write`` at once, with the reason).
* **Where the files wait**: in a hidden folder of the library, ``.nexbooks-incoming/<upload>``: on the disk the book
  goes to (the filing moves it, it does not copy it), never seen by the scan.
* **Limits**: the formats the scan reads (EPUB, PDF, AZW3, MOBI, M4B, MP3, M4A, FLAC, Ogg, Opus), the files beside a
  recording (a cover, ``metadata.json``, ``metadata.opf``) and a ZIP (a recording in one file); ``MAX_EBOOK_BYTES`` for
  an e-book, ``MAX_FILE_BYTES`` for anything else, ``MAX_TOTAL_BYTES`` and ``MAX_FILES`` for one upload,
  ``MAX_OPEN`` open uploads per person, and the free space of the library less a reserve. A name is a path below the
  upload (a recording's ``CD 1/01.mp3``), checked part by part; two files of one name are refused, never written over.
* An upload nobody finished goes after ``STALE_AFTER`` (with its files).
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import Account, Download, Library, PlaceFile, Work
from .. import libraries, works
from ..organize import scheme as schemes
from ..readers import AUDIO_FORMATS, EBOOK_FORMATS
from . import formats, hidden, importing, incoming, trash

logger = logging.getLogger("nexbooks.procure")

MAX_EBOOK_BYTES = 500 * 1024**2
MAX_FILE_BYTES = 4 * 1024**3
MAX_TOTAL_BYTES = 20 * 1024**3
MAX_FILES = 1000
MAX_OPEN = 3
MAX_NAME = 400
RESERVE = 200 * 1024**2
STALE_AFTER = timedelta(hours=24)
KIND_OPEN = importing.KIND_OPEN
SIDECARS = frozenset({"cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "metadata.json", "metadata.opf"})


def accepted_name(name: str) -> str:
    """What an uploaded file is: ``ebook``, ``audio``, ``sidecar`` or ``zip``; ``invalid_input`` for anything else."""
    lowered = name.rsplit("/", 1)[-1].casefold()
    if lowered in SIDECARS:
        return "sidecar"
    ending = incoming.ending(lowered)
    if ending in EBOOK_FORMATS:
        return "ebook"
    if ending in AUDIO_FORMATS:
        return "audio"
    if ending == "zip":
        return "zip"
    raise error("format_not_accepted", "nexbooks takes e-books, recordings and ZIP files only.", 415)


def clean_name(name: str) -> str:
    """A name below the upload, made safe part by part (``a/b.mp3``); ``invalid_input`` for one that is not a name."""
    text = (name or "").replace("\\", "/").strip()
    if (not text or len(text) > MAX_NAME or "\x00" in text or text.startswith("/") or ":" in text
            or text.endswith("/")):
        raise error("invalid_input", "The name of the file is not valid.", 422, field="path")
    parts = []
    pieces = text.split("/")
    for index, part in enumerate(pieces):
        if part in ("", ".", "..") or part.startswith("."):
            raise error("invalid_input", "The name of the file is not valid.", 422, field="path")
        # The file keeps its ending whatever its name's length: it is the stem that is shortened.
        cleaned = schemes.clean_file(part) if index == len(pieces) - 1 else schemes.clean_part(part)
        if not cleaned or cleaned.startswith("."):
            raise error("invalid_input", "The name of the file is not valid.", 422, field="path")
        parts.append(cleaned)
    if len(parts) > 4:
        raise error("invalid_input", "The name of the file is not valid.", 422, field="path")
    return "/".join(parts)


def _limit_of(kind: str) -> int:
    return MAX_EBOOK_BYTES if kind in ("ebook", "sidecar") else MAX_FILE_BYTES


def create(db: Session, account: Account, *, library_id: int, work_id: int | None, kind: str | None) -> Download:
    """A new upload into a library the person may bring books into, optionally for a book of it (a form it lacks)."""
    library = libraries.require_acquirer(db, account.id, library_id)
    if kind is not None and not libraries.kind_allows(library.kind, kind):
        raise error("library_kind", "This library does not take books of this kind.", 422)
    if work_id is not None:
        works.get_work(db, account.id, work_id)
    open_now = db.scalar(select(func.count(Download.id)).where(Download.user_id == account.id,
                                                               Download.protocol == "upload",
                                                               Download.state == "uploading")) or 0
    if open_now >= MAX_OPEN:
        raise error("too_many_uploads", "Finish or cancel an upload first.", 429)
    probe = formats.probe_library(library.path)
    if not probe["writable"]:
        raise error("no_write", "nexbooks may not write in this library.", 409, reason=probe["reason"])
    try:
        folder = hidden.folder(library.path, importing.UPLOAD_FOLDER, make=True) / secrets.token_hex(8)
    except hidden.Unsafe as exc:
        raise trash.unsafe_error() from exc
    except OSError as exc:
        raise error("no_write", "nexbooks may not write in this library.", 409, reason="write_failed") from exc
    try:
        folder.mkdir()
    except OSError as exc:
        raise error("no_write", "nexbooks may not write in this library.", 409, reason="write_failed") from exc
    moment = clock.now()
    from . import wishes

    wished = wishes.snapshot(db, account.id, work_id=work_id, foreign_id=None, kind=kind or "ebook",
                             language="") if work_id is not None else None
    row = Download(user_id=account.id, work_id=work_id, library_id=library.id, kind=kind or "ebook", language="",
                   title="", protocol="upload", state="uploading", source_path=str(folder), size=0,
                   created_at=moment, updated_at=moment, wished=wished,
                   # No kind said: the files tell it, and for a ZIP only the filing, once it has unpacked it.
                   decision={KIND_OPEN: True} if kind is None else None)
    db.add(row)
    db.commit()
    logger.info("Upload %d begun library=%s", row.id, library.id)
    return row


def get(db: Session, account: Account, upload_id: int) -> Download:
    """An upload of the person's own (an upload is the person's until it is filed; then it is a line of the
    history)."""
    row = db.scalar(select(Download).where(Download.id == upload_id, Download.protocol == "upload",
                                           Download.user_id == account.id))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return row


def folder_of(row: Download) -> Path:
    folder = Path(row.source_path)
    if folder.parent.name != importing.UPLOAD_FOLDER or folder.is_symlink() or not folder.is_dir():
        raise error("not_found", "Not found.", 404)
    if not hidden.holds(folder, importing.UPLOAD_FOLDER):
        raise trash.unsafe_error()
    return folder


def files_of(row: Download) -> list[dict[str, Any]]:
    try:
        folder = folder_of(row)
    except Exception:  # noqa: BLE001 - an upload that is filed has no folder any more
        return []
    found = incoming.walk(folder)
    items = [*found.books, *found.sidecars]
    items += [incoming.Item(path, size, "zip", "archive") for path, size, _kind in found.archives]
    return [{"path": item.path, "size": item.size} for item in sorted(items, key=lambda item: item.path)]


def prepare(db: Session, account: Account, upload_id: int, name: str, declared: int | None) -> tuple[Path, int]:
    """Where a file of an upload is written, and how many bytes it may have. Refuses what is over a limit before a
    byte is read."""
    row = get(db, account, upload_id)
    if row.state != "uploading":
        raise error("upload_closed", "This upload is finished.", 409)
    # The right is looked at again with every file: whoever lost it can no longer fill an upload begun before.
    libraries.require_acquirer(db, account.id, int(row.library_id or 0))
    relative = clean_name(name)
    kind = accepted_name(relative)
    folder = folder_of(row)
    listed = files_of(row)
    if len(listed) >= MAX_FILES:
        raise error("too_many_files", f"An upload holds at most {MAX_FILES} files.", 413, max=MAX_FILES)
    used = sum(item["size"] for item in listed)
    allowed = min(_limit_of(kind), MAX_TOTAL_BYTES - used)
    with _flight_lock:
        # What the files on their way can still take is not free any more: several files sent at once (of one person
        # or of several) may not all be given the same room.
        free = shutil.disk_usage(folder).free - RESERVE - sum(_in_flight.values())
        allowed = min(allowed, free)
        if declared is not None and declared > allowed:
            raise error("too_large", "The file is too large.", 413, max_mb=max(allowed, 0) // (1024 * 1024))
        target = folder.joinpath(*relative.split("/"))
        if os.path.lexists(target):
            raise error("file_exists", "A file of this name is in the upload already.", 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        allowed = max(allowed, 0)
        _in_flight[str(target)] = declared if declared is not None else allowed
    row.updated_at = clock.now()
    db.commit()
    return target, allowed


#: The room the files on their way may still take, by target (``prepare`` books it, ``release`` gives it back).
_in_flight: dict[str, int] = {}
_flight_lock = threading.Lock()


def release(target: Path) -> None:
    """The file on its way has arrived, or has not: its room is free again."""
    with _flight_lock:
        _in_flight.pop(str(target), None)


def received(db: Session, account: Account, upload_id: int, size: int) -> None:
    row = get(db, account, upload_id)
    row.size = int(row.size or 0) + size
    row.updated_at = clock.now()
    db.commit()


def finish(db: Session, account: Account, upload_id: int, language: str = "") -> Download:
    """The files are all there: the upload is filed like a finished download."""
    row = get(db, account, upload_id)
    if row.state != "uploading":
        raise error("upload_closed", "This upload is finished.", 409)
    libraries.require_acquirer(db, account.id, int(row.library_id or 0))
    found = incoming.walk(folder_of(row))
    kinds = found.kinds()
    if not found.books and not found.archives:
        raise error("upload_empty", "The upload holds no book.", 422)
    if len(kinds) > 1:
        raise error("upload_mixed", "An upload holds e-books or recordings, not both.", 422)
    library = db.get(Library, row.library_id) if row.library_id is not None else None
    if library is None:
        raise error("not_found", "Not found.", 404)
    kind = next(iter(kinds)) if kinds else row.kind
    # Only archives and no kind said: the filing reads the kind from what comes out of the archive (never an e-book for
    # lack of an answer), and the library is held against it there.
    open_kind = bool((row.decision or {}).get(KIND_OPEN)) and not kinds
    row.decision = {KIND_OPEN: True} if open_kind else None
    if not open_kind and not libraries.kind_allows(library.kind, kind):
        raise error("library_kind", "This library does not take books of this kind.", 422)
    names = [item.path for item in found.books] or [path for path, _s, _k in found.archives]
    first = names[0].split("/")[0] if names else ""
    row.title = (first.rsplit(".", 1)[0] if "/" not in names[0] else first)[:400] if names else "Upload"
    row.kind, row.language = kind, (language or "")[:8]
    moment = clock.now()
    row.state, row.completed_at, row.updated_at, row.progress = "completed", moment, moment, 100.0
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        raise error("already_loading", "A release of this book is on its way.", 409) from None
    importing.request(db, row)
    db.commit()
    logger.info("Upload %d finished files=%d", row.id, len(found.books) + len(found.archives))
    return row


def cancel(db: Session, account: Account, upload_id: int) -> None:
    """An upload that is still coming is dropped with what came (the interface asks first)."""
    row = get(db, account, upload_id)
    if row.state != "uploading":
        raise error("upload_closed", "This upload is finished.", 409)
    importing.remove_upload_folder(Path(row.source_path))
    db.delete(row)
    db.commit()
    logger.info("Upload %d cancelled", upload_id)


def sweep(db: Session) -> int:
    """Uploads nobody finished for ``STALE_AFTER`` go, with their files."""
    gone = 0
    for row in list(db.scalars(select(Download).where(Download.protocol == "upload", Download.state == "uploading",
                                                      Download.updated_at < clock.now() - STALE_AFTER).limit(100))):
        importing.remove_upload_folder(Path(row.source_path))
        db.delete(row)
        gone += 1
    if gone:
        db.commit()
        logger.info("Unfinished uploads removed count=%s", gone)
    return gone


def result_of(db: Session, row: Download) -> dict[str, Any]:
    """What became of an upload, for its dialog: the work it went into when it is filed."""
    work = db.get(Work, row.work_id) if row.work_id is not None else None
    placed = db.scalar(select(func.count(PlaceFile.id)).where(PlaceFile.download_id == row.id,
                                                             PlaceFile.state == "placed")) or 0
    return {"work_id": work.id if work is not None and row.state == "imported" else None, "placed": int(placed)}


__all__ = [
    "MAX_EBOOK_BYTES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "accepted_name",
    "cancel",
    "clean_name",
    "create",
    "files_of",
    "finish",
    "get",
    "prepare",
    "received",
    "release",
    "result_of",
    "sweep",
]
