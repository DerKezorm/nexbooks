"""SQLite connection, and the schema with its version number.

There is no Alembic. The database carries the version of its schema in SQLite's ``user_version``:

* A new database is made from the models (``create_all``) and gets ``SCHEMA_VERSION``.
* An older one is brought up step by step: ``MIGRATIONS[n]`` takes a database from version ``n - 1`` to ``n``. Before
  the first step a backup is made (the way back if the new version goes wrong); each step runs in a transaction of its
  own and sets the version only when it went through.
* A database from a newer nexbooks than the running one is not touched: the start stops with a message.

Whoever changes a table in ``models.py`` raises ``SCHEMA_VERSION``, writes the step, and records the new schema in
``tests/schema/`` (``test_schema.py`` says how). Version 1 is the first schema; it has no step. Version 2 (the
libraries and their books) is the first one, version 3 (the sources and the matching) the second,
version 4 (the search over the books) the third, version 5 (progress and bookmarks) the fourth,
version 6 (a note on a bookmark) the fifth, version 7 (Discover: the lists of the sources, the books that are
not on a shelf, the wish list) the sixth, version 8 (the generation of reading and the guessed genre, the
recognition of the books on the shelf) the seventh, version 9 (pen names, the work on the audiobooks) the eighth,
version 10 (the reading stack, the photos of authors, tidying the files) the ninth, version 11 (fetching
books: indexers, Prowlarr, download programs, the right to acquire) the tenth, version 14 (checking and
filing what was fetched or uploaded) the thirteenth, version 16 (moving an edition into another library)
the fifteenth, version 17 (what was wished, kept beside the work; the books filed under the wish of another book set
apart) the sixteenth, version 18 (the codes that connect the iPhone app) the seventeenth, version 19 (the
order of changes the iPhone app follows) the eighteenth, version 20 (numbers of works, editions and files that
are never given twice, and what a device of the app was given kept per device) the nineteenth, version
21 (the codes a device without a camera asks for, to be confirmed in the browser) the twentieth, version 22
(the catalogue of an author's works: the authors, their works, what people moved) the twenty-first, version
23 (which device connected a device from the app) the twenty-second, version 24 (the themed
shelves of Browse and how long a pass through a book took) the twenty-third, version 25 (what people had in
an edition sent away as the wrong book, kept with its files in the trash) the twenty-fourth, version 26 (watching an
author: who watches whom, and what the watching did with each work) the twenty-fifth, version 27 (the generation of
the rules a download was last judged by) the twenty-sixth, version 28 (the connections of e-readers and the Kindle
address) the twenty-seventh, version 29 (the checksum KOReader names an e-book by, and the place as KOReader worded it)
the twenty-eighth, version 30 (the list of a person for the Kobo, what each Kobo holds of it, and the place as the Kobo
worded it) the twenty-ninth.

A step that makes a table again (``rebuilds_tables``) runs with the foreign keys off: dropping the old table would
otherwise delete everything that hangs on it. It checks the foreign keys itself before it commits.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings
from .models import Base

logger = logging.getLogger("nexbooks.db")

_settings = get_settings()
_settings.data_dir.mkdir(parents=True, exist_ok=True)

#: How long a write waits for another one to finish. Every writer keeps its transactions short; this is the margin
#: for a slow disk, not a wait anybody should see.
BUSY_SECONDS = 15

#: The version of the schema the models describe.
SCHEMA_VERSION = 31

#: ``MIGRATIONS[n]`` brings a database from version ``n - 1`` to ``n``. Version 1 is the first schema; it has no step.
#: Later steps are written out as the tables stood then, not taken from the models, which move on.
MIGRATIONS: dict[int, Callable[[Connection], None]] = {}


def _step_v2(connection: Connection) -> None:
    """Libraries and the books in them: nothing existed of it before, so the step only makes the tables."""
    from .step_v2_schema import V2_STATEMENTS

    for statement in V2_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[2] = _step_v2


def _step_v3(connection: Connection) -> None:
    """Matching with sources: three columns on the editions, and the tables of the sources and the matching.
    Everything an edition had stays; the matching starts from the editions that are there (``matching.catch_up``)."""
    from .step_v3_schema import V3_STATEMENTS

    for statement in V3_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[3] = _step_v3


def _step_v4(connection: Connection) -> None:
    """The search over the books: what the index holds per edition, the index itself and the trigger that
    keeps it free of deleted editions. The index is filled by a job (``services/search_index.py``) queued here: the
    editions that are there are indexed right after the start, in short transactions, while the app already answers."""
    from .step_v4_schema import V4_STATEMENTS

    for statement in V4_STATEMENTS:
        connection.exec_driver_sql(statement)
    now = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
    connection.execute(
        text("INSERT INTO jobs (kind, ref, payload, state, attempts, created_at, error) "
             "VALUES ('index', 0, NULL, 'queued', 0, :now, '')"),
        {"now": now},
    )


MIGRATIONS[4] = _step_v4


def _step_v5(connection: Connection) -> None:
    """Reading in the browser: where a person is in an edition, and the marks they set. Nothing existed of it
    before, so the step only makes the tables."""
    from .step_v5_schema import V5_STATEMENTS

    for statement in V5_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[5] = _step_v5


def _step_v6(connection: Connection) -> None:
    """Listening in the browser: a bookmark can carry a note. The marks that are there keep everything and
    get an empty one; the progress table holds a recording's place as it is (``time:`` in ``locator``)."""
    connection.exec_driver_sql("ALTER TABLE bookmarks ADD COLUMN note VARCHAR(500) DEFAULT '' NOT NULL")


MIGRATIONS[6] = _step_v6


def _step_v7(connection: Connection) -> None:
    """Discover: nothing existed of it before, so the step only makes the tables."""
    from .step_v7_schema import V7_STATEMENTS

    for statement in V7_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[7] = _step_v7


def _step_v8(connection: Connection) -> None:
    """Recognising the books: the generation each file was read in and each library has
    reached (so that a rule that changed is applied to the books that are there: every file is read again once), the
    libraries' request to read everything again, and the mark of a genre that is a guess. Nothing that is there is
    lost."""
    connection.exec_driver_sql("ALTER TABLE files ADD COLUMN rev INTEGER DEFAULT 0 NOT NULL")
    connection.exec_driver_sql("ALTER TABLE libraries ADD COLUMN rev INTEGER DEFAULT 0 NOT NULL")
    connection.exec_driver_sql("ALTER TABLE libraries ADD COLUMN reread_rev INTEGER DEFAULT 0 NOT NULL")
    connection.exec_driver_sql("ALTER TABLE editions ADD COLUMN genre_guess BOOLEAN DEFAULT 0 NOT NULL")


MIGRATIONS[8] = _step_v8


def _step_v9(connection: Connection) -> None:
    """Pen names: the other names of a person who is proved to be one, and the claims waiting
    for the people they name. Nothing that is there is changed; the keys of people and series (now one for ``Süden``
    and ``Sueden``) are put right by the next run of the scan."""
    from .step_v9_schema import V9_STATEMENTS

    for statement in V9_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[9] = _step_v9


def _step_v10(connection: Connection) -> None:
    """The reading stack, the photos and short biographies of authors, tidying the files: a mark on the place
    a person holds in a book (the pass has been entered in the history), and tables nothing existed of before."""
    from .step_v10_schema import V10_STATEMENTS

    connection.exec_driver_sql("ALTER TABLE progress ADD COLUMN pass_done BOOLEAN DEFAULT 0 NOT NULL")
    for statement in V10_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[10] = _step_v10


def _step_v11(connection: Connection) -> None:
    """Fetching books: the tables of the indexers, the Prowlarr of the instance, the download programs and
    their path mappings. Nothing existed of it before, so the step only makes the tables. The new right ``acquire`` of
    a library needs no change: the column holds a word, and every member keeps ``read`` or ``manage`` as it was."""
    from .step_v11_schema import V11_STATEMENTS

    for statement in V11_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[11] = _step_v11


def _step_v12(connection: Connection) -> None:
    """Searching and judging releases: the searches of a person with what they found, the blocklist and
    the downloads handed to a program. Nothing existed of it before, so the step only makes the tables."""
    from .step_v12_schema import V12_STATEMENTS

    for statement in V12_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[12] = _step_v12


def _step_v13(connection: Connection) -> None:
    """Wishes and following the downloads: the wishes of the people, the jobs nobody asked for and the
    history are new tables; a download gets the columns tracking writes, a download that was ``sent`` is now ``queued``
    until tracking has looked at it, and the two indexes that keep a book of one kind to one release are made again for
    all the states of a download that is not over."""
    from .step_v13_schema import V13_STATEMENTS

    for statement in V13_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[13] = _step_v13


def _step_v14(connection: Connection) -> None:
    """Checking and filing: the files of a download as the filing sees them, the trash of what an upgrade
    replaced and the notes for one person are new tables; a download gets the columns of its filing."""
    from .step_v14_schema import V14_STATEMENTS

    for statement in V14_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[14] = _step_v14


def _step_v15(connection: Connection) -> None:
    """The automatic work: when the newest entries of an indexer were read, the releases fetched today,
    the origin of a search, the hand-over that waits, the entries already seen and the log of the rounds."""
    from .step_v15_schema import V15_STATEMENTS

    for statement in V15_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[15] = _step_v15


def _step_v16(connection: Connection) -> None:
    """An edition that goes into another library: the move and the journal of its files are new
    tables; nothing that was there changes."""
    from .step_v16_schema import V16_STATEMENTS

    for statement in V16_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[16] = _step_v16


def _step_v17(connection: Connection) -> None:
    """What was wished, kept on the wish, the download and the history; and once, the books filed under the wish of
    another book of the sources set apart (``step_v17_schema.heal``)."""
    from .step_v17_schema import V17_STATEMENTS, heal

    for statement in V17_STATEMENTS:
        connection.exec_driver_sql(statement)
    heal(connection)


MIGRATIONS[17] = _step_v17


def _step_v18(connection: Connection) -> None:
    """Connecting the iPhone app: the codes a browser makes and the app fetches a device token with. Nothing
    existed of it before, so the step only makes the table."""
    from .step_v18_schema import V18_STATEMENTS

    for statement in V18_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[18] = _step_v18


def _step_v19(connection: Connection) -> None:
    """The surface of the iPhone app: a number on every work that moves at every change, the row that counts,
    the works that went and the works the devices of a person were given, and the triggers that move the number. The
    works that are there are numbered before the triggers come, so the numbering is no change."""
    from .step_v19_schema import V19_STATEMENTS

    for statement in V19_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[19] = _step_v19


def _step_v20(connection: Connection) -> None:
    """Numbers that are never given twice: ``works``, ``editions`` and ``files``
    are made again with ``AUTOINCREMENT``, every row with its number, the triggers, views and indexes as they were; and
    ``app_seen`` is kept per device. ``step_v20_schema.rebuild`` checks what it did before the step may commit."""
    from .step_v20_schema import rebuild

    rebuild(connection)


_step_v20.rebuilds_tables = True  # type: ignore[attr-defined]
MIGRATIONS[20] = _step_v20


def _step_v21(connection: Connection) -> None:
    """Connecting a device that has no camera: the requests it makes for a code of its own and a person
    confirms in the browser. Nothing existed of it before, so the step only makes the table."""
    from .step_v21_schema import V21_STATEMENTS

    for statement in V21_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[21] = _step_v21


def _step_v22(connection: Connection) -> None:
    """The catalogue of an author's works: the authors the sources know, their works and what people moved
    to another kind. Nothing existed of it before, so the step only makes the tables."""
    from .step_v22_schema import V22_STATEMENTS

    for statement in V22_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[22] = _step_v22


def _step_v23(connection: Connection) -> None:
    """Where a device came from: the device that connected it from the app, on the device
    and on the request on its way. The two tables are made again (``step_v23_schema.rebuild``); the new columns stay
    empty for what is there."""
    from .step_v23_schema import rebuild

    rebuild(connection)


_step_v23.rebuilds_tables = True  # type: ignore[attr-defined]
MIGRATIONS[23] = _step_v23


def _step_v24(connection: Connection) -> None:
    """The themed shelves of Browse and how long a pass through a book took: two tables that held nothing
    before, and a column each on ``progress`` and ``history`` that earlier rows leave empty."""
    from .step_v24_schema import V24_STATEMENTS

    for statement in V24_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[24] = _step_v24


def _step_v25(connection: Connection) -> None:
    """What people had in an edition that was sent away as the wrong book: kept with the
    trash entry of its files. Nothing existed of it before, so the step only makes the table."""
    from .step_v25_schema import V25_STATEMENTS

    for statement in V25_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[25] = _step_v25


def _step_v26(connection: Connection) -> None:
    """Watching an author: who watches whom, and what the watching did with each work. Nothing existed of it before,
    so the step only makes the tables."""
    from .step_v26_schema import V26_STATEMENTS

    for statement in V26_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[26] = _step_v26


def _step_v27(connection: Connection) -> None:
    """The generation of the rules of the check a download was last judged by: one column, 0 for what is there (so that
    a question that is open is judged once more by the rules of today)."""
    from .step_v27_schema import V27_STATEMENTS

    for statement in V27_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[27] = _step_v27


def _step_v28(connection: Connection) -> None:
    """The connections a person makes for an e-reader or a reading app (a new table), and the Kindle address on the
    account (one column, empty for what is there)."""
    from .step_v28_schema import V28_STATEMENTS

    for statement in V28_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[28] = _step_v28


def _step_v29(connection: Connection) -> None:
    """The partial MD5 of a file (empty for what is there, made in the background) and the place in the words of
    KOReader on the progress (empty for what is there)."""
    from .step_v29_schema import V29_STATEMENTS

    for statement in V29_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[29] = _step_v29


def _step_v30(connection: Connection) -> None:
    """The sync of a Kobo: the list of a person, what each connection holds of it (both new, so empty), the number of
    the last answer on the connections (0 for those there are) and the place as the Kobo worded it on the progress
    (empty for what is there)."""
    from .step_v30_schema import V30_STATEMENTS

    for statement in V30_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[30] = _step_v30


def _step_v31(connection: Connection) -> None:
    """The list "for later" of the apps: a new table, so empty, and the trigger that takes an entry along with its
    book."""
    from .step_v31_schema import V31_STATEMENTS

    for statement in V31_STATEMENTS:
        connection.exec_driver_sql(statement)


MIGRATIONS[31] = _step_v31


# No pool with an upper bound: with the default pool the sixteenth concurrent request would block the event
# loop waiting for a connection. Opening a SQLite connection costs a fraction of a millisecond.
engine = create_engine(
    f"sqlite:///{_settings.database_path}",
    connect_args={"check_same_thread": False, "timeout": BUSY_SECONDS},
    poolclass=NullPool,
    # A failed statement names its values in the exception, and exceptions reach the log: never texts or hashes.
    hide_parameters=True,
)


def _wal(cursor: Any) -> None:
    """WAL mode, tried again while another connection holds the database: SQLite does not always wait by itself for
    a change of the journal mode."""
    deadline = time.monotonic() + BUSY_SECONDS
    while True:
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if ("locked" not in str(exc) and "busy" not in str(exc)) or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


@event.listens_for(engine, "connect")
def _pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    # The wait first: switching to WAL needs the database to itself for a moment, and two first starts on an empty
    # folder asked at the same time (one of them broke off with "database is locked").
    cursor.execute(f"PRAGMA busy_timeout={BUSY_SECONDS * 1000}")
    _wal(cursor)
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # What is deleted is overwritten, not left in free pages: a deleted token hash or secret must be gone from the file.
    cursor.execute("PRAGMA secure_delete=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


class SchemaTooNew(RuntimeError):
    """The database was written by a newer nexbooks; this one does not know its tables."""


def schema_version(connection: Connection) -> int:
    return int(connection.execute(text("PRAGMA user_version")).scalar() or 0)


def _set_version(connection: Connection, version: int) -> None:
    # PRAGMA takes no bound parameters; the value is an int from this module, never from outside.
    connection.execute(text(f"PRAGMA user_version = {int(version)}"))


def _schema_engine(url: Any = None) -> Engine:
    """An engine for changing the schema, on the same file. Python's sqlite3 opens no transaction before ``CREATE`` or
    ``ALTER`` and so commits each one at once; here every transaction begins with an explicit ``BEGIN``, so a step that
    breaks off leaves nothing behind (SQLAlchemy's recipe for pysqlite). Only for the schema: the everyday engine
    keeps the driver's way, which holds no read lock while a request only reads."""
    schema = create_engine(url or engine.url, poolclass=NullPool, connect_args={"timeout": BUSY_SECONDS})

    @event.listens_for(schema, "connect")
    def _no_driver_transactions(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA secure_delete=ON")
        dbapi_connection.execute(f"PRAGMA busy_timeout={BUSY_SECONDS * 1000}")

    @event.listens_for(schema, "begin")
    def _begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return schema


def _has_tables(connection: Connection) -> bool:
    found = connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type = 'table'")).scalar()
    return bool(found)


def init_db() -> None:
    """Makes a new database, or brings an older one up to ``SCHEMA_VERSION``."""
    schema = _schema_engine()
    try:
        _init(schema)
    finally:
        schema.dispose()


def _init(schema: Engine, *, backup: bool = True) -> None:
    # Read under the write lock: of two processes starting at once, the second waits here and sees what the first did.
    with schema.begin() as connection:
        fresh = not _has_tables(connection)
        current = schema_version(connection)
        if fresh:
            # One transaction: a start that breaks off in between leaves no tables without a version behind.
            Base.metadata.create_all(connection)
            _set_version(connection, SCHEMA_VERSION)
    if fresh:
        logger.info("Database created schema=%s", SCHEMA_VERSION)
        return
    check_version(current)
    if current < SCHEMA_VERSION:
        if backup:
            # A schema change is the moment a backup is worth most: the way back if the new version goes wrong.
            from .services import backups

            backups.create(kind=backups.UPDATE, note=f"before schema {SCHEMA_VERSION}")
        for target in range(current + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS.get(target)
            if step is None:
                raise RuntimeError(f"No migration to schema {target}.")
            if _take_step(schema, target, step):
                logger.info("Database migrated schema=%s", target)


def _take_step(schema: Engine, target: int, step: Callable[[Connection], None]) -> bool:
    """One step in a transaction of its own; False when another process took it meanwhile. A step that makes tables
    again runs with the foreign keys off, which SQLite lets change only outside a transaction: set on the connection
    before it begins, and back on afterwards (the connection is not kept, ``NullPool``)."""
    rebuilds = bool(getattr(step, "rebuilds_tables", False))
    with schema.connect() as connection:
        raw = connection.connection.dbapi_connection
        assert raw is not None
        if rebuilds:
            raw.execute("PRAGMA foreign_keys=OFF")
        try:
            with connection.begin():
                # Read again under the lock: another process may have taken this step since the first read. A step
                # that is not idempotent (an added column, moved rows) must never run twice.
                if schema_version(connection) >= target:
                    return False
                step(connection)
                _set_version(connection, target)
        finally:
            if rebuilds:
                raw.execute("PRAGMA foreign_keys=ON")
    return True


class NotOurs(RuntimeError):
    """A database that is not a nexbooks database, or holds things nexbooks never made."""


_REFERENCE: tuple[dict[str, frozenset[str]], dict[str, str]] | None = None


def _squeezed(sql: object) -> str:
    """A statement as SQLite kept it, without the differences that mean nothing (spaces, line breaks, case)."""
    return " ".join(str(sql or "").split()).lower()


def _reference() -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """What a database of today holds, made the way a new one is: ``(tables with their columns, the statements of
    its triggers, views and virtual tables by name)``. Made once."""
    global _REFERENCE
    if _REFERENCE is None:
        reference = create_engine("sqlite://", poolclass=NullPool)
        # A memory database is gone with its connection: made and read on one.
        with reference.connect() as connection:
            Base.metadata.create_all(connection)
            names = [row[0] for row in
                     connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")]
            tables = {
                name: frozenset(row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{name}")'))
                for name in names if not name.startswith("sqlite_")
            }
            code = {
                row[0]: _squeezed(row[1]) for row in connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') "
                    "OR (type = 'table' AND sql LIKE 'CREATE VIRTUAL%')")
            }
        reference.dispose()
        _REFERENCE = (tables, code)
    return _REFERENCE


def _foreign_objects(plain: sqlite3.Connection) -> list[str]:
    """What the database holds that code of nexbooks did not make and that is more than data: triggers and views (they
    would run at every write of the app, and name anything), and virtual tables (a module of SQLite stands behind
    each one). One of ours by name counts only when its statement is ours, too: a trigger named like the one that keeps
    the search index, with another body, is the first thing somebody would try. Plain tables of data nexbooks does not
    know are not in the list: nothing reads them."""
    _tables, own = _reference()
    query = "SELECT name, type, sql FROM sqlite_master WHERE type IN ('trigger', 'view', 'table')"
    found = []
    for name, kind, sql in plain.execute(query):
        if kind == "table" and not str(sql or "").lstrip().upper().startswith("CREATE VIRTUAL"):
            continue
        if own.get(name) != _squeezed(sql):
            found.append(name)
    return found


def rehearse(path: Path) -> None:
    """The start, run on a copy of a database that is about to be restored: the steps that would bring it up to
    ``SCHEMA_VERSION`` and then a look whether it holds every table and column nexbooks needs. Raises when anything of
    it fails (a database that is not nexbooks's, one with a step that cannot run on it); the file is changed, so only
    ever hand it a scratch copy.

    Triggers, views and virtual tables that nexbooks does not make are refused before anything is done: a trigger would
    run at every write of the app, and the restore would give it the keys to everything.
    """
    uri = f"file:{path.as_posix()}?mode=ro"
    plain = sqlite3.connect(uri, uri=True)
    try:
        if _foreign_objects(plain):
            raise NotOurs("triggers, views or virtual tables that nexbooks did not make")
        if plain.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0:
            raise NotOurs("no tables")
    finally:
        plain.close()
    schema = _schema_engine(f"sqlite:///{path.as_posix()}")
    try:
        _init(schema, backup=False)
    finally:
        schema.dispose()
    plain = sqlite3.connect(uri, uri=True)
    try:
        for name, columns in _reference()[0].items():
            found = {row[1] for row in plain.execute(f'PRAGMA table_info("{name}")')}
            if not columns <= found:
                raise NotOurs("a table or a column is missing")
    finally:
        plain.close()


def check_version(version: int) -> None:
    """A schema this nexbooks can open: made by nexbooks (at least 1), and not by a newer one. ``SchemaTooNew`` else."""
    if version > SCHEMA_VERSION:
        raise SchemaTooNew(
            f"The database has schema {version}, this nexbooks knows up to {SCHEMA_VERSION}. "
            "Start the newer version again, or restore a backup made with this one."
        )
    if version < 1:
        raise SchemaTooNew("The database has no schema version. It was not made by nexbooks.")
