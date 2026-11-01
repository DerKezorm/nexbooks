"""Backups of nexbooks: the database, the settings in it and the server's secrets, in one ZIP. **Not the books.**

The books lie as readable files in the folders of the libraries and belong to the server's usual file backup; nexbooks
only reads them and never changes them. A copy of them in every archive would make each one as big as the whole
collection.

* **The database is copied with SQLite's backup API**, never as a file: it runs in WAL mode, and a file copy misses
  what still sits in the ``-wal`` side file.
* **The manifest names the database with its size and sha256**, and the check before a restore compares both with what
  it unpacked: a flipped byte in the archive (the usual damage, a failing disk) is told as damaged, not as an error.
  The manifest itself is read up to ``MANIFEST_MAX`` bytes and no further.
* **Only automatic copies are pruned** (``scheduled`` and ``update``); one made by hand stays until deleted, and so
  does one brought in from elsewhere (``receive``, named ``...-upload.zip``), whatever kind its manifest names.
* **A restore happens at the next start.** ``stage_restore`` checks the archive, makes an ``update`` copy of the
  current state (the way back), unpacks into ``backups/restore-pending/`` and ends the process; Docker starts it
  again and ``apply_pending`` swaps the files before anything opens the database. A pending folder without its
  manifest is a half-written one and is thrown away, never applied. The swap is made on temporary files and ends in
  ``os.replace``; whatever goes wrong before that leaves the live database as it was, the staged folder moves to
  ``restore-failed/``, the log and the operators are told, and the server starts on with what it had.
* **The trial run is the start, rehearsed.** It runs the same steps as the start on a scratch copy (the migrations up to
  today's schema, the end of the stored sign-ins) and demands every table and column nexbooks needs; triggers and views
  that nexbooks does not make are refused before anything runs. An archive that passes cannot stop the next start.

**The pictures and the languages go with it**: the covers of the books, the photos of the authors, the covers of
the books of the sources (``covers/``, ``people/``, ``foreign-covers/``) and the languages the operator added
(``locales/``). The database names each picture by a file it does not hold itself, and nothing makes a picture again by
its own once the database says it is there: a scan reads only what changed and a match is not asked again, so a restore
on a fresh machine without them would show empty covers for good. A picture is named by the start of its own sha256: the
trial run computes it for each one, so a damaged picture is a damaged archive. Pictures are stored as they are (WebP is
compressed already); a restore adds them next to the ones there are (a name is its content, so nothing is overwritten
with something else), and pictures nothing names any more are swept by the scan as usual.

The archive holds ``secret.key``, the key the server seals its own secrets with (OIDC client secret, mail password):
without it a restore on another machine would bring those back unreadable. So an archive is as secret as the database
itself; downloading one asks for the operator's password once more.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import threading
import time
import zipfile
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import text

from .. import __version__, clock, private
from ..config import get_settings
from . import settings_service

logger = logging.getLogger("nexbooks.backups")

FOLDER_NAME = "backups"
MANIFEST = "nexbooks-backup.json"
DATABASE_ENTRY = "database/nexbooks.db"
#: The key the server encrypts its own secrets with (OIDC client secret, mail password): without it a restore on
#: another machine would bring those back unreadable. Whoever holds a backup holds the database anyway.
SECRET_ENTRY = "secret.key"
#: The folders of pictures in the data folder that go into a backup (``services/covers``, ``photos``), and the
#: operator's languages. The covers of the books of the sources (``foreign-covers``, ``discover/pics``) are there to be
#: shown for a day and never go into a backup; an archive of an earlier version may still hold some, which are read as
#: pictures and not put back.
PICTURE_FOLDERS = ("covers", "people")
PICTURE = re.compile(r"^(covers|people|foreign-covers)/([0-9a-f]{20})\.webp$")
#: What of the books of the sources is cleared in the copy of the database: the pictures and the descriptions (shown for
#: a day only), and what Audible and Apple answered.
SHOWN_ONLY_SQL = (
    (
        "UPDATE foreign_books SET cover = '', color = '', "
        "data = json_remove(data, '$.description', '$.text_at', '$.cover_at') WHERE data IS NOT NULL"
    ),
    "UPDATE foreign_books SET cover = '', color = '' WHERE data IS NULL",
    "UPDATE wishlist SET cover = ''",
    "DELETE FROM source_cache WHERE source IN ('audible', 'apple')",
)
LOCALE = re.compile(r"^locales/([a-z]{2,3}(?:-(?:[A-Z]{2}|[A-Z][a-z]{3}))?)\.json$")
#: The largest picture and language file an archive may hold (the scan makes covers of a few dozen kilobytes; the
#: languages are checked against their own limit when they are read).
PICTURE_MAX = 4 * 1024 * 1024
#: The most entries an archive may hold (a picture each for a very large library, and a few more). More is not a backup
#: of ours; it is refused before one of them is read.
MAX_ENTRIES = 250_000
LOCALE_MAX = 512 * 1024
PENDING = "restore-pending"
#: Where a staged restore that could not be applied is put aside, with ``failed.json`` saying when and why.
FAILED = "restore-failed"
MANUAL = "manual"
SCHEDULED = "scheduled"
UPDATE = "update"
AUTOMATIC_KINDS = (SCHEDULED, UPDATE)
SCHEDULES = ("off", "daily", "weekly")
INTERVALS = {"daily": 1, "weekly": 7}
NIGHT = range(3, 6)
CATCH_UP_DAYS = 1
INTERVAL_SECONDS = 3600
NAME = re.compile(r"^nexbooks-\d{4}-\d{2}-\d{2}-\d{6}(-\d+)?(-upload)?\.zip$")
#: The end of the name of an archive brought in from elsewhere: listed as uploaded and never pruned.
UPLOADED = "-upload.zip"
SQLITE_HEADER = b"SQLite format 3\x00"
_CHUNK = 1024 * 1024
#: The manifest is a few hundred bytes of JSON. More than this is read by nobody: an archive with a larger one is
#: refused, at the upload and in the list, so that none can make the server read megabytes at every look.
MANIFEST_MAX = 64 * 1024
#: What reading a damaged ZIP raises: a bad checksum, a broken compressed stream, a cut-off file, a method or a password
#: this reader does not know.
DAMAGE = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)
#: What an archive may unpack to, counted while it is read, never taken from what the archive says about itself: a
#: small ZIP can claim little and inflate to a full disk. The key is a line of text; the whole is what an upload may
#: carry.
SECRET_MAX = 4096
DATABASE_MAX = 16 * 1024**3
RESTORE_MAX = 64 * 1024**3
_lock = threading.Lock()


class BackupError(Exception):
    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


@dataclass
class Manifest:
    version: str
    created: str
    kind: str
    note: str = ""
    accounts: int = 0
    #: Bytes and sha256 of the database inside.
    bytes: int = 0
    sha256: str = ""
    #: How many pictures and languages the archive holds (0 in archives made before they went in).
    pictures: int = 0
    locales: int = 0


class ManifestInvalid(BackupError):
    """The manifest is there and is JSON, but says things of the wrong kind (a count that is a word). Such an archive
    is listed as unusable, never trusted and never a cause of a server error."""


_Text = Annotated[StrictStr, Field(max_length=500)]
_Count = Annotated[StrictInt, Field(ge=0, le=2**62)]


class _ManifestFile(BaseModel):
    """What the manifest says about its own shape, strictly: no value is taken for another type."""

    model_config = ConfigDict(strict=True, extra="forbid")

    version: Annotated[StrictStr, Field(max_length=64)]
    created: Annotated[StrictStr, Field(max_length=64)]
    kind: Annotated[StrictStr, Field(max_length=32)]
    note: _Text = ""
    accounts: _Count = 0
    bytes: _Count = 0
    sha256: Annotated[StrictStr, Field(max_length=64)] = ""
    pictures: _Count = 0
    locales: _Count = 0


@dataclass
class Entry:
    name: str
    size: int
    created: str
    kind: str
    note: str
    accounts: int
    version: str
    uploaded: bool = False


@dataclass
class Brief:
    """What a restore of an archive would do: what it holds, and whether it can be used."""

    name: str
    version: str
    created: str
    kind: str
    accounts: int
    database_ok: bool
    #: The schema version of the database inside (``db.SCHEMA_VERSION``); 0 when it could not be read.
    schema: int
    #: Made by a newer nexbooks: update first, then restore.
    too_new: bool
    #: What a restore brings back to life: devices, keys and connections for e-readers that were valid when the backup
    #: was made and are revoked, blocked or gone on this server now. The backup does not know they were ended since.
    revived_devices: int = 0
    revived_keys: int = 0
    revived_links: int = 0

    @property
    def usable(self) -> bool:
        return self.database_ok


def folder() -> Path:
    return get_settings().data_dir / FOLDER_NAME


def pending_folder() -> Path:
    return folder() / PENDING


def path_of(name: str) -> Path:
    if not NAME.fullmatch(name):
        raise BackupError("not_found", "no such backup")
    path = folder() / name
    if not path.is_file():
        raise BackupError("not_found", "no such backup")
    return path


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d-%H%M%S")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _database_copy(target: Path) -> int:
    """The database through SQLite's backup API. Returns the number of accounts."""
    source = sqlite3.connect(get_settings().database_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        for statement in SHOWN_ONLY_SQL:
            destination.execute(statement)
        destination.commit()
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.commit()
        try:
            return int(destination.execute("SELECT count(*) FROM users").fetchone()[0])
        except sqlite3.DatabaseError:
            return 0
    finally:
        destination.close()
        source.close()


def _reserve(base: Path, stem: str, end: str) -> Path:
    """A name no other process can take: created empty with O_EXCL, the finished archive replaces it. Two processes
    starting at the same second (two containers on one data folder) took the same name before, and on Windows the
    second one failed on the first one's file."""
    number = 1
    while True:
        name = f"{stem}{end}" if number == 1 else f"{stem}-{number}{end}"
        try:
            descriptor = os.open(base / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, private.FILE_MODE)
        except FileExistsError:
            number += 1
            continue
        os.close(descriptor)
        return base / name


def _pictures() -> list[tuple[str, Path]]:
    """Every picture of the data folder that has the shape of one, as ``(entry, path)``; links and odd names stay out"""
    found: list[tuple[str, Path]] = []
    for name in PICTURE_FOLDERS:
        place = get_settings().data_dir / name
        if not place.is_dir():
            continue
        for item in sorted(place.iterdir()):
            entry = f"{name}/{item.name}"
            if PICTURE.fullmatch(entry) and item.is_file() and not item.is_symlink():
                found.append((entry, item))
    return found


def _locales() -> list[tuple[str, Path]]:
    """The languages the operator added, as ``(entry, path)``."""
    directory = get_settings().locales_dir
    if directory is None or not directory.is_dir():
        return []
    return [(f"locales/{item.name}", item) for item in sorted(directory.iterdir())
            if LOCALE.fullmatch(f"locales/{item.name}") and item.is_file() and not item.is_symlink()]


def create(*, kind: str = MANUAL, note: str = "") -> Path:
    """A new backup archive. Returns its path."""
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    private.tighten(base)
    moment = datetime.now(UTC)
    target = _reserve(base, f"nexbooks-{_stamp(moment)}", ".zip")
    name = target.name
    # The working files carry the process and a counter of their own: never shared with another process.
    unique = f"{os.getpid()}-{time.time_ns()}"
    partial = base / f".{name}.{unique}.part"
    database = base / f".{name}.{unique}.db"
    done = False
    try:
        # Both only the owner's before a byte is in them: the archive holds the key and everything else.
        private.new_file(database)
        private.new_file(partial)
        accounts = _database_copy(database)
        manifest = Manifest(version=__version__, created=moment.isoformat(timespec="seconds"), kind=kind, note=note,
                            accounts=accounts, bytes=database.stat().st_size, sha256=_hash_file(database))
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(database, DATABASE_ENTRY)
            key = get_settings().data_dir / "secret.key"
            if key.is_file():
                archive.write(key, SECRET_ENTRY)
            for entry, path in _pictures():
                # A picture the scan swept away meanwhile is simply not there, and one that is no longer what its
                # name says (a damaged disk) stays out: it would make the whole archive damaged.
                found = PICTURE.fullmatch(entry)
                try:
                    info = path.stat()
                    raw = path.read_bytes() if info.st_size <= PICTURE_MAX else b""
                except OSError:
                    continue
                if found is None or not raw or not hashlib.sha256(raw).hexdigest().startswith(found.group(2)):
                    continue
                stamp = time.localtime(max(info.st_mtime, 315532800))[:6]
                archive.writestr(zipfile.ZipInfo(entry, date_time=stamp), raw, compress_type=zipfile.ZIP_STORED)
                manifest.pictures += 1
            for entry, path in _locales():
                try:
                    archive.write(path, entry)
                    manifest.locales += 1
                except FileNotFoundError:
                    continue
            # Last: an archive without its manifest is recognisably incomplete.
            archive.writestr(MANIFEST, json.dumps(asdict(manifest), ensure_ascii=False))
        os.replace(partial, target)
        done = True
    finally:
        partial.unlink(missing_ok=True)
        database.unlink(missing_ok=True)
        if not done:
            target.unlink(missing_ok=True)
    logger.info("Backup made kind=%s accounts=%s bytes=%s", kind, manifest.accounts, manifest.bytes)
    return target


def _manifest(archive: zipfile.ZipFile) -> Manifest:
    """The manifest of an archive. ``BackupError`` when there is none or it is no JSON, ``ManifestInvalid`` when it is
    JSON that does not have the shape (types are checked strictly, see ``_ManifestFile``). ``backup_too_large`` for an
    archive with more than ``MAX_ENTRIES`` entries (every later step walks them)."""
    if len(archive.infolist()) > MAX_ENTRIES:
        raise BackupError("backup_too_large", "the archive holds more entries than a backup of nexbooks can")
    try:
        info = archive.getinfo(MANIFEST)
        if info.file_size > MANIFEST_MAX:
            raise BackupError("backup_invalid", "the manifest of the archive is larger than a manifest can be")
        with archive.open(info) as source:
            data = source.read(MANIFEST_MAX + 1)
        if len(data) > MANIFEST_MAX:
            raise BackupError("backup_invalid", "the manifest of the archive is larger than a manifest can be")
        raw = json.loads(data.decode("utf-8"))
    except (KeyError, ValueError, TypeError) as exc:
        raise BackupError("backup_invalid", "the archive has no readable manifest") from exc
    except DAMAGE as exc:
        raise BackupError("backup_damaged", "the archive is damaged; its manifest cannot be read") from exc
    try:
        checked = _ManifestFile.model_validate(raw)
    except ValidationError as exc:
        raise ManifestInvalid("backup_invalid", "the manifest of the archive is not a manifest") from exc
    return Manifest(**checked.model_dump())


def entries() -> list[Entry]:
    base = folder()
    if not base.is_dir():
        return []
    found = []
    for path in base.iterdir():
        if not NAME.fullmatch(path.name):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _manifest(archive)
        except (zipfile.BadZipFile, BackupError, OSError):
            continue
        found.append(
            Entry(path.name, path.stat().st_size, manifest.created, manifest.kind, manifest.note, manifest.accounts,
                  manifest.version, path.name.endswith(UPLOADED))
        )
    # Newest first; two of the same second in the order of their names, so that which one stays is always the same.
    return sorted(found, key=lambda entry: (entry.created, entry.name), reverse=True)


def remove(name: str) -> None:
    path_of(name).unlink()
    logger.info("Backup deleted")


def prune(keep: int) -> int:
    automatic = [entry for entry in entries() if entry.kind in AUTOMATIC_KINDS and not entry.uploaded]
    removed = 0
    for entry in automatic[max(keep, 1) :]:
        (folder() / entry.name).unlink(missing_ok=True)
        removed += 1
    if removed:
        logger.info("Old automatic backups removed count=%s", removed)
    return removed


def temporary_upload() -> Path:
    """Where an upload lands while it arrives: in the backups folder, only the owner's, under a name no listing
    takes for a backup."""
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    private.tighten(base)
    path = base / f".upload-{os.getpid()}-{time.time_ns()}.part"
    private.new_file(path)
    return path


def receive(received: Path) -> str:
    """An archive brought in from elsewhere, for a move to a new server: if it is one of ours (a ZIP with a manifest
    and a database), it joins the list under a name of its own and waits there to be checked and restored like any
    other. Nothing is unpacked here; the full check is the trial run before a restore."""
    try:
        with zipfile.ZipFile(received) as archive:
            manifest = _manifest(archive)
            if DATABASE_ENTRY not in archive.namelist():
                raise BackupError("backup_invalid", "the archive holds no database")
    except zipfile.BadZipFile as exc:
        raise BackupError("backup_invalid", "not a ZIP archive") from exc
    try:
        moment = datetime.fromisoformat(manifest.created)
    except (TypeError, ValueError) as exc:
        raise BackupError("backup_invalid", "the archive has no readable date") from exc
    target = _reserve(folder(), f"nexbooks-{_stamp(moment)}", UPLOADED)
    name = target.name
    os.replace(received, target)
    logger.info("Backup received name=%s version=%s accounts=%s", name, manifest.version, manifest.accounts)
    return name


# --- Checking and restoring -----------------------------------------------------------------------------------------


class _Tee:
    """A sink that writes what it is given and counts and hashes it on the way, so that the checksum is of what was
    unpacked, not of a second reading."""

    def __init__(self, sink: Any) -> None:
        self.sink = sink
        self.size = 0
        self.hasher = hashlib.sha256()

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        self.hasher.update(chunk)
        if self.sink is not None:
            self.sink.write(chunk)

    def hexdigest(self) -> str:
        return self.hasher.hexdigest()


def _same_as_manifest(tee: _Tee, manifest: Manifest) -> bool:
    """The database as unpacked against the size and the sha256 the manifest names (a field the manifest leaves empty,
    as an archive made elsewhere may, is not compared)."""
    if manifest.sha256 and tee.hexdigest() != manifest.sha256:
        return False
    return not manifest.bytes or tee.size == manifest.bytes


class _Budget:
    """Bytes an unpacking may still write, shared by every entry of one archive."""

    def __init__(self, total: int) -> None:
        self.left = total


def _copy_limited(source: Any, sink: Any, limit: int, budget: _Budget) -> int:
    """Copies at most ``limit`` bytes and no more than the budget holds; ``backup_too_large`` past either."""
    written = 0
    while chunk := source.read(_CHUNK):
        written += len(chunk)
        budget.left -= len(chunk)
        if written > limit or budget.left < 0:
            raise BackupError("backup_too_large", "the archive unpacks to more than a backup may hold")
        if sink is not None:
            sink.write(chunk)
    return written


def _unusable(name: str) -> Brief:
    """What a trial run says of an archive that cannot be trusted at all: nothing in it, and not usable."""
    return Brief(name=name, version="", created="", kind="", accounts=0, database_ok=False, schema=0, too_new=False)


def _rehearse(scratch: Path) -> bool:
    """The start, run on the copy of the database: the steps that bring it to today's schema, the end of the stored
    sign-ins, and a look for everything nexbooks needs. A database that stops any of it would stop every start after the
    restore. Only the kind of the error is logged, never its text (it can quote the database's contents)."""
    from .. import db

    try:
        db.rehearse(scratch)
        _end_restored_sessions(scratch)
    except Exception as exc:  # noqa: BLE001 - whatever goes wrong here means the archive cannot be used
        logger.warning("The trial run of a backup could not start its database error=%s", type(exc).__name__)
        return False
    return True


def check(name: str) -> Brief:
    """The trial run: is the archive whole (the checksum of the manifest agrees with what is inside), can its database
    be opened and started like the real thing, and what does it hold? And which devices and keys a restore would bring
    back to life. ``backup_damaged`` when the archive cannot be read to its end or does not match its manifest."""
    path = path_of(name)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise BackupError("backup_invalid", "not a ZIP archive") from exc
    with archive:
        try:
            manifest = _manifest(archive)
        except ManifestInvalid:
            logger.warning("A backup with a manifest of the wrong shape was looked at")
            return _unusable(name)
        members = {info.filename: info for info in archive.infolist()}
        from .. import db

        budget = _Budget(RESTORE_MAX)
        database_ok = False
        schema = 0
        revived = _Revived()
        if DATABASE_ENTRY in members:
            scratch = folder() / f".check-{os.getpid()}-{time.time_ns()}.db"
            try:
                try:
                    with archive.open(DATABASE_ENTRY) as source, open(scratch, "wb") as raw:
                        tee = _Tee(raw)
                        _copy_limited(source, tee, DATABASE_MAX, budget)
                except DAMAGE as exc:
                    raise BackupError("backup_damaged", "the archive is damaged; nothing was changed") from exc
                if not _same_as_manifest(tee, manifest):
                    raise BackupError("backup_damaged", "the database in the archive is not the one the manifest names")
                with open(scratch, "rb") as head:
                    is_sqlite = head.read(16) == SQLITE_HEADER
                if is_sqlite:
                    connection = sqlite3.connect(scratch)
                    try:
                        whole = connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                        schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    finally:
                        connection.close()
                    # A database this nexbooks cannot open would stop every start after the restore: one from a newer
                    # version, or one without a version. An older one is fine; the start brings it up.
                    database_ok = whole and 1 <= schema <= db.SCHEMA_VERSION
                    if database_ok:
                        database_ok = _rehearse(scratch)
                    if database_ok:
                        revived = _revived(scratch)
            except (sqlite3.DatabaseError, OSError):
                database_ok = False
            finally:
                scratch.unlink(missing_ok=True)
        # An archive of ours holds the three parts, its pictures and its languages. Anything else came from elsewhere.
        if any(not info.is_dir() and info.filename not in (MANIFEST, DATABASE_ENTRY, SECRET_ENTRY)
               and not PICTURE.fullmatch(info.filename) and not LOCALE.fullmatch(info.filename)
               for info in archive.infolist()):
            database_ok = False
        elif database_ok:
            _check_extras(archive, manifest, budget)
    return Brief(
        name=name, version=manifest.version, created=manifest.created, kind=manifest.kind, accounts=manifest.accounts,
        database_ok=database_ok, schema=schema, too_new=schema > db.SCHEMA_VERSION,
        revived_devices=revived.devices, revived_keys=revived.keys, revived_links=revived.links,
    )


def _check_extras(archive: zipfile.ZipFile, manifest: Manifest, budget: _Budget) -> None:
    """The pictures and languages of an archive, read to their end: each picture's name is the start of its sha256, each
    is no larger than a picture may be, and there are as many as the manifest says. ``backup_damaged`` otherwise."""
    pictures = languages = 0
    try:
        for info in archive.infolist():
            found = PICTURE.fullmatch(info.filename)
            if found:
                tee = _Tee(None)
                with archive.open(info) as source:
                    _copy_limited(source, tee, PICTURE_MAX, budget)
                if not tee.hexdigest().startswith(found.group(2)):
                    raise BackupError("backup_damaged", "a picture in the archive is not the one its name says")
                pictures += 1
            elif LOCALE.fullmatch(info.filename):
                with archive.open(info) as source:
                    _copy_limited(source, None, LOCALE_MAX, budget)
                languages += 1
    except DAMAGE as exc:
        raise BackupError("backup_damaged", "the archive is damaged; nothing was changed") from exc
    if pictures != manifest.pictures or languages != manifest.locales:
        raise BackupError("backup_damaged", "the archive does not hold what its manifest names")


@dataclass
class _Revived:
    devices: int = 0
    keys: int = 0
    links: int = 0


#: What is alive: the token hash and when it ends (empty: it does not), by table. The same question to the backup and to
#: this server.
_ALIVE = (
    ("devices", "SELECT token_hash, NULL FROM devices WHERE revoked_at IS NULL"),
    ("keys", "SELECT token_hash, expires_at FROM api_keys WHERE blocked_at IS NULL"),
    ("links", "SELECT token_hash, NULL FROM ereader_links WHERE blocked_at IS NULL"),
)


def _alive(rows: Any) -> set[str]:
    """The hashes among ``(hash, end)`` rows that are not over yet."""
    now = clock.now().astimezone(UTC)
    alive = set()
    for token_hash, end in rows:
        if end is not None:
            try:
                moment = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
            except ValueError:
                continue
            if (moment if moment.tzinfo else moment.replace(tzinfo=UTC)) <= now:
                continue
        alive.add(str(token_hash))
    return alive


def _revived(connection_path: Path) -> _Revived:
    """How many devices, keys and connections for e-readers a restore would bring back to life: valid in the backup,
    not valid on this server any more (revoked, blocked or removed since). A person who locked a lost phone out after
    the backup would find it paired again; the trial run says so, with numbers."""
    from ..db import SessionLocal

    counts: dict[str, int] = {}
    connection = sqlite3.connect(f"file:{connection_path.as_posix()}?mode=ro", uri=True)
    try:
        with SessionLocal() as live:
            for key, query in _ALIVE:
                try:
                    then = _alive(connection.execute(query).fetchall())
                except sqlite3.DatabaseError:
                    continue
                now = _alive(live.execute(text(query)).fetchall())
                counts[key] = len(then - now)
    finally:
        connection.close()
    return _Revived(counts.get("devices", 0), counts.get("keys", 0), counts.get("links", 0))


def stage_restore(name: str) -> Brief:
    """Check, keep the current state as a backup, and lay the archive out for the next start."""
    with _lock:
        brief = check(name)
        from .. import db

        if brief.schema > db.SCHEMA_VERSION:
            raise BackupError("backup_too_new", "the archive comes from a newer nexbooks; nothing was changed")
        if not brief.usable:
            raise BackupError("backup_damaged", "the archive is damaged; nothing was changed")
        create(kind=UPDATE, note=f"before restoring {name}")
        pending = pending_folder()
        shutil.rmtree(pending, ignore_errors=True)
        pending.mkdir(parents=True)
        try:
            budget = _Budget(RESTORE_MAX)
            with zipfile.ZipFile(path_of(name)) as archive:
                manifest = _manifest(archive)
                with archive.open(DATABASE_ENTRY) as source, open(pending / "nexbooks.db", "wb") as raw:
                    tee = _Tee(raw)
                    _copy_limited(source, tee, DATABASE_MAX, budget)
                if not _same_as_manifest(tee, manifest):
                    raise BackupError("backup_damaged", "the archive is damaged; nothing was changed")
                if SECRET_ENTRY in archive.namelist():
                    with archive.open(SECRET_ENTRY) as source, open(pending / "secret.key", "wb") as sink:
                        _copy_limited(source, sink, SECRET_MAX, budget)
                for info in archive.infolist():
                    if not (PICTURE.fullmatch(info.filename) or LOCALE.fullmatch(info.filename)):
                        continue
                    target = pending / info.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    limit = LOCALE_MAX if info.filename.startswith("locales/") else PICTURE_MAX
                    with archive.open(info) as source, open(target, "wb") as sink:
                        _copy_limited(source, sink, limit, budget)
                # Last: the manifest says the pending folder is complete.
                (pending / MANIFEST).write_bytes(json.dumps(asdict(manifest), ensure_ascii=False).encode())
        except DAMAGE as exc:
            shutil.rmtree(pending, ignore_errors=True)
            raise BackupError("backup_damaged", "the archive is damaged; nothing was changed") from exc
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
    logger.info("Backup staged for the next start created=%s", brief.created)
    return brief


def _end_restored_sessions(database: Path) -> None:
    """Sign-ins stored in the backup would come back to life, even ones ended since: everybody signs in anew."""
    connection = sqlite3.connect(database)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_sessions'").fetchone():
            connection.execute("DELETE FROM auth_sessions")
        connection.commit()
    finally:
        connection.close()


def restart_soon(delay: float = 1.5) -> None:
    """End the process shortly, after the answer went out; Docker starts it again (``restart: unless-stopped``)."""

    def stop() -> None:
        time.sleep(delay)
        logger.info("nexbooks stops to restore a backup")
        if os.name == "nt":
            os._exit(3)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except OSError:
            os._exit(0)
        time.sleep(10)
        os._exit(0)

    threading.Thread(target=stop, name="restart-for-restore", daemon=True).start()


def failed_folder() -> Path:
    return folder() / FAILED


def _set_aside(pending: Path, error: BaseException) -> None:
    """A staged restore that could not be applied is moved to ``restore-failed/`` (an earlier one there goes), with a
    note of when and what kind of error, and the server starts on with the database it had."""
    failed = failed_folder()
    shutil.rmtree(failed, ignore_errors=True)
    try:
        os.replace(pending, failed)
    except OSError:
        shutil.rmtree(pending, ignore_errors=True)
        failed.mkdir(parents=True, exist_ok=True)
    private.tighten(failed)
    note = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "error": type(error).__name__, "told": False}
    try:
        (failed / "failed.json").write_text(json.dumps(note), encoding="utf-8")
    except OSError:
        pass
    logger.error("A staged restore could not be applied and was put aside in %s; the database is as it was error=%s",
                 FAILED, type(error).__name__)


def _put_extras(pending: Path) -> None:
    """The pictures and languages of a staged restore, put in place before the database: a picture is added where it is
    missing (its name is its content), a language replaces the file of the same name. Each through a temporary file and
    ``os.replace``, so that a stop in between leaves no half a file; a picture added for nothing goes with the scan"""
    settings = get_settings()
    for name in PICTURE_FOLDERS:
        staged = pending / name
        if not staged.is_dir():
            continue
        place = settings.data_dir / name
        place.mkdir(parents=True, exist_ok=True)
        for item in staged.iterdir():
            target = place / item.name
            if PICTURE.fullmatch(f"{name}/{item.name}") and not target.exists():
                temporary = place / f".{item.name}.restoring"
                shutil.copyfile(item, temporary)
                os.replace(temporary, target)
    staged = pending / "locales"
    if staged.is_dir() and settings.locales_dir is not None:
        settings.locales_dir.mkdir(parents=True, exist_ok=True)
        for item in staged.iterdir():
            if LOCALE.fullmatch(f"locales/{item.name}"):
                temporary = settings.locales_dir / f".{item.name}.restoring"
                shutil.copyfile(item, temporary)
                os.replace(temporary, settings.locales_dir / item.name)


def _swap(pending: Path) -> dict[str, Any]:
    """Puts the staged files in place. Everything that can fail is done on temporary files next to the live ones; the
    live database is touched only by the ``os.replace`` at the end. Staged files stay until the end, so a stop in
    between only means the next start does it again."""
    settings = get_settings()
    manifest = json.loads((pending / MANIFEST).read_text(encoding="utf-8"))
    target = settings.database_path
    staged = target.with_name(target.name + ".restoring")
    key = settings.data_dir / "secret.key"
    key_staged = key.with_name(key.name + ".restoring")
    try:
        _put_extras(pending)
        shutil.copyfile(pending / "nexbooks.db", staged)
        _end_restored_sessions(staged)
        # The devices of the app hold markers of the live database: the restored one must not answer them as if it were
        # that one (``app_changes.carry_over``).
        from . import app_changes

        app_changes.carry_over(target, staged)
        if (pending / "secret.key").is_file():
            shutil.copyfile(pending / "secret.key", key_staged)
            try:
                key_staged.chmod(0o600)
            except OSError:
                pass
        os.replace(staged, target)
        # Left behind, SQLite would read the old database's WAL into the restored one.
        for suffix in ("-wal", "-shm", "-journal"):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        if key_staged.is_file():
            os.replace(key_staged, key)
    finally:
        staged.unlink(missing_ok=True)
        key_staged.unlink(missing_ok=True)
    return manifest


def apply_pending() -> bool:
    """At the start, before anything opens the database: swap in a staged backup. Returns whether one was. When it
    cannot be done, the staged folder is put aside, the database stays as it is, and the start goes on: a restore that
    fails must not turn into a server that never starts."""
    pending = pending_folder()
    if not pending.is_dir():
        return False
    if not (pending / MANIFEST).is_file() or not (pending / "nexbooks.db").is_file():
        logger.warning("An incomplete restore was found and thrown away")
        shutil.rmtree(pending, ignore_errors=True)
        return False
    try:
        manifest = _swap(pending)
    except Exception as exc:  # noqa: BLE001 - nothing may keep the server from starting
        _set_aside(pending, exc)
        return False
    shutil.rmtree(pending, ignore_errors=True)
    logger.info("Backup restored created=%s", manifest.get("created"))
    return True


FAILED_TEXTS = {
    "de": ("Wiederherstellen gescheitert",
           "Die Sicherung ließ sich beim Neustart nicht einspielen. Der Server läuft mit den Daten von vorher weiter."),
    "en": ("Restore failed",
           "The backup could not be put in place at the restart. The server went on with the data it had."),
}


def report_failed_restore() -> bool:
    """After the start: if a staged restore was put aside and nobody was told yet, tell the operators once (a push to
    their devices and, with a mail server, a mail). Returns whether a notice went out."""
    marker = failed_folder() / "failed.json"
    try:
        note = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(note, dict) or note.get("told"):
        return False
    from . import tell

    tell.operator(tell.Notice(
        texts=FAILED_TEXTS, url="/settings?tab=backup", tag="restore-failed",
        mail_subject="nexbooks: the restore of a backup failed",
        mail_body=("The backup you chose could not be put in place when nexbooks restarted. The server went on with "
                   "the data it had, and the staged files were put aside in the backups folder (restore-failed)."
                   "\n\nThe backups and the log: {where}\n"),
    ))
    note["told"] = True
    try:
        marker.write_text(json.dumps(note), encoding="utf-8")
    except OSError:
        pass
    return True


# --- Schedule -------------------------------------------------------------------------------------------------------


def schedule(db: Any) -> str:
    value = settings_service.get(db, "backup_schedule")
    return value if value in SCHEDULES else "off"


def keep(db: Any) -> int:
    value = settings_service.get(db, "backup_keep")
    return value if isinstance(value, int) and 1 <= value <= 365 else 7


def due(every: str, *, now: datetime | None = None) -> bool:
    """Whether a scheduled copy is due: in the night, or a day late at any hour for a server that sleeps at night."""
    if every not in INTERVALS:
        return False
    moment = now or datetime.now().astimezone()
    last = next((entry for entry in entries() if entry.kind == SCHEDULED), None)
    if last is None:
        return moment.hour in NIGHT
    try:
        previous = datetime.fromisoformat(last.created)
    except ValueError:
        return True
    days = (moment - previous).total_seconds() / 86400
    interval = INTERVALS[every] - 0.25
    return days >= interval and (moment.hour in NIGHT or days >= interval + CATCH_UP_DAYS)


def run_job() -> None:
    from ..db import SessionLocal

    with _lock:
        with SessionLocal() as db:
            every = schedule(db)
            count = keep(db)
        for leftover in folder().glob(".*") if folder().is_dir() else []:
            if leftover.is_file() and time.time() - leftover.stat().st_mtime > 6 * 3600:
                leftover.unlink(missing_ok=True)
        if due(every):
            create(kind=SCHEDULED)
            prune(count)


async def run_forever(stop: asyncio.Event) -> None:
    """The hourly look, first an hour after the start."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(run_job)
        except Exception:
            logger.exception("Backup job failed")
