"""The progress sync of KOReader (``routers/kosync.py``): KOReader reports where a person is in a book and asks where
else they were, and nexbooks answers from the place it keeps for the person and the edition (``services/progress.py``).

**How a book is named.** KOReader names a book by a checksum of the file, the *partial MD5*: the MD5 of up to 1024 bytes
read at each of the offsets 0, 1 KiB, 4 KiB, 16 KiB ... 1 GiB (each four times the one before), reading stops at the
first offset past the end of the file. Reading so few bytes is why KOReader can make it on the device. nexbooks makes it
for every EPUB when the file comes or changes (``scanner``, ``procure/importing``) and, for the files that were there
before, in small portions in the background (``backfill``). The other way KOReader can name a book, by the MD5 of the
file's name, is not known here: a device renames files, so the name is no proof of anything.

**A book the connection may not see is a book that is not there.** The document is looked up among the EPUBs of the
libraries the account is a member of; for anything else the answer is the one for a document nobody ever reported, and
a report for it is accepted and dropped, as the official sync server accepts a report for any name.

**What a report becomes.** KOReader says ``percentage`` (0 to 1) and ``progress`` (for a book it reflows, an XPointer
such as ``/body/DocFragment[7]/body/div/p[12]/text().0``). The browser's reader cannot read an XPointer, and nobody can
turn one into the CFI of the browser without rendering the book the way KOReader did. So the report is kept as a place
with no CFI and with the share of the book (``Progress.locator`` empty, ``fraction`` the percentage), which the reader
opens at (``goToFraction``); the XPointer is kept beside it (``Progress.koreader``) for as long as nothing moves the
place, so that another KOReader gets exactly the place the first one was at. Which report stays is decided by the rule
of ``progress.save`` (the newest wins; a report is as new as the moment it arrived).

**What KOReader is told.** KOReader goes to ``progress``, not to ``percentage`` (it only shows the percentage). A place
that came from KOReader is told back as it was reported. A place from the browser or the app has no XPointer: nexbooks
tells the start of the part of the book (``/body/DocFragment[N]``, the ``N``-th item of the spine, which is how KOReader
numbers the parts of an EPUB) that the share of the book falls in, weighing the parts by their size as the browser's
reader does. So KOReader lands at the start of the right chapter (or file, if the book is one file), not at the
word. A book whose parts cannot be read gets no place at all: no place is better than a wrong one.

**The key.** KOReader sends the MD5 of the password as the key, never the password. A connection of this kind keeps the
SHA-256 of that MD5 (see ``ereaders.stored_hash``), so that nothing in the database is a credential and nothing in it
can be turned into the password.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import IO, Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock
from ..db import SessionLocal
from ..models import EDITION_EBOOK, ROLE_BOOK, BookFile, Edition, Library, Progress
from . import jobs, progress, works
from .readers import epub
from .readers.files import open_below
from .readers.limits import ReadError

logger = logging.getLogger("nexbooks.kosync")

#: The offsets KOReader reads at (``util.partialMD5``: ``lshift(1024, 2 * i)`` for i from -1 to 10, where the shift of
#: -2 is taken modulo 32 by the bit library, which makes the first offset 0), and how much it reads at each.
OFFSETS = (0, *(1024 << (2 * step) for step in range(11)))
SAMPLE = 1024
#: A document name as the official server accepts one: letters, digits and the underscore.
DOCUMENT = re.compile(r"^[A-Za-z0-9_]{1,64}$")
#: The device key of a report from KOReader is this and the ``device_id`` the plugin made up.
KEY_PREFIX = "kr-"
KEY_MAX = 40 - len(KEY_PREFIX)
#: The longest place of KOReader that is kept; a longer one is not kept (the share of the book still is).
MAX_POINTER = 1000
#: What the files that wait for their checksum are read in: so many at a time, so many seconds between two portions.
PORTION = 25
PORTION_PAUSE = 3.0
IDLE_PAUSE = 120.0
#: The parts of a book the answers have looked at, by file, so that a pull does not open the archive each time.
_SPINES_KEPT = 64
_spines: OrderedDict[tuple[str, int, int], list[int]] = OrderedDict()
_spines_lock = threading.Lock()


def partial_md5(handle: IO[bytes]) -> str:
    """The partial MD5 of an open file, as KOReader makes it."""
    digest = hashlib.md5(usedforsecurity=False)
    for offset in OFFSETS:
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        handle.seek(offset)
        sample = handle.read(SAMPLE)
        if not sample:
            break
        digest.update(sample)
    return digest.hexdigest()


def partial_md5_of(root: str, relative: str) -> str:
    """The partial MD5 of the file ``relative`` below the library folder ``root`` (opened the safe way)."""
    with open_below(root, relative) as handle:
        return partial_md5(handle)


def wanted(fmt: str, role: str) -> bool:
    """Whether a file gets the checksum: an EPUB that is a book."""
    return fmt == "epub" and role == ROLE_BOOK


def backfill(db: Session, after: int = 0, limit: int = PORTION) -> tuple[int, int]:
    """The checksum of up to ``limit`` EPUBs without one, taken in the order of their numbers from the first one past
    ``after``. Returns how many files were looked at (made, or left: unreadable, or changed on the disk since the scan,
    which the next scan settles) and the number of the last, to go on after; ``(0, 0)`` when none is left past
    ``after``, which is where the next round begins again from the start (a file that could not be read is tried again
    then, once a round, not in a hurry)."""
    rows = db.execute(select(BookFile.id, BookFile.library_id, BookFile.path, BookFile.size, BookFile.mtime_ns).where(
        BookFile.role == ROLE_BOOK, BookFile.format == "epub", BookFile.partial_md5 == "",
        BookFile.missing_at.is_(None), BookFile.id > after).order_by(BookFile.id).limit(limit)).all()
    last = 0
    for file_id, library_id, path, size, mtime_ns in rows:
        last = file_id
        library = db.get(Library, library_id)
        if library is None:
            continue
        try:
            with open_below(library.path, path) as handle:
                info = os.fstat(handle.fileno())
                if (info.st_size, info.st_mtime_ns) != (size, mtime_ns):
                    continue
                found = partial_md5(handle)
        except (ReadError, OSError):
            continue
        db.execute(update(BookFile).where(BookFile.id == file_id, BookFile.partial_md5 == "").values(partial_md5=found))
    db.commit()
    return len(rows), last


def _backfill_alone(after: int) -> tuple[int, int]:
    with SessionLocal() as db:
        return backfill(db, after)


async def run_forever(stop: asyncio.Event) -> None:
    """Gives the EPUBs that were there before the checksum their checksum, a portion at a time, so that a library of
    thousands of books is not read in one go and nothing else waits for it."""
    await asyncio.sleep(20)
    after = 0
    while not stop.is_set():
        pause = IDLE_PAUSE
        try:
            did, after = await asyncio.to_thread(_backfill_alone, after)
            if did:
                pause = PORTION_PAUSE
        except Exception:
            logger.exception("Making the checksums of KOReader failed")
        try:
            await asyncio.wait_for(stop.wait(), pause)
        except TimeoutError:
            continue


# --- Finding the book ----------------------------------------------------------------------------------------------


@dataclass
class Found:
    file: BookFile
    edition: Edition
    library: Library


def locate(db: Session, uid: int, document: str) -> Found | None:
    """The EPUB the document names among the books of the libraries ``uid`` is a member of, or None (nothing by that
    name, or a book the account does not see: the same). The oldest file if several carry the checksum."""
    name = document.casefold()
    if not DOCUMENT.match(name):
        return None
    row = db.execute(
        select(BookFile, Edition, Library).join(Edition, Edition.id == BookFile.edition_id)
        .join(Library, Library.id == BookFile.library_id)
        .where(BookFile.partial_md5 == name, BookFile.role == ROLE_BOOK, BookFile.format == "epub",
               BookFile.missing_at.is_(None), Edition.kind == EDITION_EBOOK, *works.visible_edition_clause(uid))
        .order_by(BookFile.id).limit(1)).first()
    return Found(*row) if row is not None else None


# --- A report ------------------------------------------------------------------------------------------------------


def device_key(device_id: str) -> str:
    """The key a report of KOReader is kept under: its device id reduced to the signs a key may hold."""
    kept = re.sub(r"[^A-Za-z0-9_-]", "", device_id)[:KEY_MAX]
    if not kept:
        kept = hashlib.sha256(device_id.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return KEY_PREFIX + kept


def _pointer(text: str) -> str:
    """The place of KOReader as far as it is kept: a text with no control characters that fits the column."""
    if len(text) > MAX_POINTER or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return text


def store(db: Session, uid: int, document: str, *, pointer: str, percentage: float, device: str,
          device_id: str) -> bool:
    """A report of KOReader for the book it names. Returns False when there is no such book for ``uid`` (nothing is
    kept then). Which report stays is the rule of ``progress.save``."""
    found = locate(db, uid, document)
    if found is None:
        return False
    progress.save(db, uid, found.edition.id, progress.Report(
        locator="", fraction=min(max(percentage, 0.0), 1.0), unit="chapter", chapter=0, chapter_count=0,
        chapter_title="", device_key=device_key(device_id), device=device or "KOReader", age=0.0,
        koreader=_pointer(pointer)))
    return True


# --- The place told back -------------------------------------------------------------------------------------------


def _spine_sizes(found: Found) -> list[int]:
    path = works.book_path(found.library.path, found.file.path)
    if path is None:
        return []
    info = path.stat()
    key = (str(path), info.st_size, info.st_mtime_ns)
    with _spines_lock:
        # Looked at and moved up in one step: another request may have made the memory forget the book in between, and
        # moving up what is gone is an error.
        cached = _spines.get(key)
        if cached is not None:
            _spines.move_to_end(key)
            return cached
    try:
        with open_below(found.library.path, found.file.path) as handle:
            sizes = epub.spine_sizes(handle)
    except (ReadError, OSError):
        sizes = []
    with _spines_lock:
        _spines[key] = sizes
        while len(_spines) > _SPINES_KEPT:
            _spines.popitem(last=False)
    return sizes


def fragment_at(sizes: list[int], fraction: float) -> int | None:
    """The number (from 1) of the part of the book that the share ``fraction`` of the whole falls in, the parts weighed
    by their size and the empty ones passed over; None when no part has any size."""
    total = sum(size for size in sizes if size > 0)
    if total <= 0:
        return None
    target = min(max(fraction, 0.0), 1.0) * total
    running = 0
    last = None
    for index, size in enumerate(sizes):
        if size <= 0:
            continue
        last = index + 1
        if running + size > target:
            return last
        running += size
    return last


def pointer_for(found: Found, fraction: float) -> str:
    """The XPointer of the start of the part of the book the share of it falls in; empty when the parts cannot be
    read."""
    try:
        part = fragment_at(_spine_sizes(found), fraction)
    except OSError:
        part = None
    return f"/body/DocFragment[{part}]" if part is not None else ""


def fetch(db: Session, uid: int, document: str) -> dict[str, Any]:
    """What KOReader is told of its place in the book: the fields of the official server's answer, or an empty answer
    (a book unknown, a place never reported, a place that cannot be put in the words of KOReader)."""
    found = locate(db, uid, document)
    if found is None:
        return {}
    place = db.get(Progress, (uid, found.edition.id))
    if place is None:
        return {}
    pointer = place.koreader or pointer_for(found, place.fraction)
    if not pointer:
        return {}
    key = place.device_key
    return {
        "document": document, "percentage": float(place.fraction), "progress": pointer,
        "device": place.device or "nexbooks", "device_id": key.removeprefix(KEY_PREFIX),
        "timestamp": int(place.moved_at.timestamp()),
    }


def now_stamp() -> int:
    return int(clock.now().timestamp())
