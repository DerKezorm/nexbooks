"""The schema carries its version, and an older database is brought up step by step.

``tests/schema/v<n>.sql`` records the schema of every version. A change to ``models.py`` without a new version fails
``test_the_schema_is_the_recorded_one``. Then:

1. raise ``SCHEMA_VERSION`` in ``app/db.py`` and write the step in ``MIGRATIONS``,
2. record the new schema: ``NEXBOOKS_RECORD_SCHEMA=1 python -m pytest tests/test_schema.py``,
3. ``test_every_older_schema_is_brought_up_to_the_current_one`` then runs the step against the old record.

Version 1 is the first schema of nexbooks: it has no step and no older record.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text

from app import db as database
from app.models import Base
from app.services import backups

RECORDS = Path(__file__).parent / "schema"


def schema_of(connection: sqlite3.Connection) -> dict[str, list[tuple[str, str, int, object, int]]]:
    """Every table with its columns (name, type, not null, default, primary key), indexes left aside."""
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {
        table: [(row[1], row[2], row[3], row[4], row[5]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
        for table in tables
    }


def fresh_schema_sql() -> str:
    """The schema the models make, as SQL, one statement per line: what the records hold."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT sql, name FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            # Tables first, a trigger only once its table is there.
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
        )).all()
        # A full-text table makes tables of its own (``doc_fts_data`` ...) when it is made: they are not recorded.
        virtual = [row[0] for row in connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL TABLE%'"))]
    engine.dispose()
    shadows = {f"{name}_{part}" for name in virtual for part in ("config", "content", "data", "docsize", "idx")}
    return "".join(" ".join(sql.split()) + ";\n" for sql, name in rows if name not in shadows)


#: The notes of the backups ``init_db`` made in a test (``scratch`` catches them).
made: list[str] = []


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """``init_db`` against a database of its own, outside the one the other tests share."""
    path = tmp_path / "nexbooks.db"
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    made.clear()
    monkeypatch.setattr(backups, "create", lambda **kwargs: made.append(kwargs.get("note", "")) or path)
    yield path
    engine.dispose()


def version_of(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def test_the_schema_is_the_recorded_one() -> None:
    record = RECORDS / f"v{database.SCHEMA_VERSION}.sql"
    current = fresh_schema_sql()
    if os.environ.get("NEXBOOKS_RECORD_SCHEMA") == "1":
        RECORDS.mkdir(exist_ok=True)
        record.write_text(current, encoding="utf-8", newline="\n")
    assert record.is_file(), f"No record of schema {database.SCHEMA_VERSION}; see the top of this file."
    assert record.read_text(encoding="utf-8") == current, (
        "models.py changed without a new schema version: raise SCHEMA_VERSION, write the migration, record the schema "
        "(see the top of this file)."
    )


def test_a_new_database_gets_the_current_version(scratch: Path) -> None:
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION
    # A second start changes nothing and makes no backup.
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION
    assert made == []


def test_every_older_schema_is_brought_up_to_the_current_one(scratch: Path) -> None:
    current = fresh_schema_sql()
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(current)
        wanted = schema_of(connection)
    older = sorted(int(path.stem[1:]) for path in RECORDS.glob("v*.sql") if int(path.stem[1:]) < database.SCHEMA_VERSION)
    # Nothing to bring up while version 1 is the only one; the first migration records the schema it starts from.
    for version in older:
        # A file of the round before may still be open on Windows unless every connection is closed (``closing``:
        # sqlite3's own ``with`` only commits).
        for leftover in (scratch, scratch.with_name(scratch.name + "-wal"), scratch.with_name(scratch.name + "-shm")):
            leftover.unlink(missing_ok=True)
        with closing(sqlite3.connect(scratch)) as connection:
            connection.executescript((RECORDS / f"v{version}.sql").read_text(encoding="utf-8"))
            connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
        database.init_db()
        assert version_of(scratch) == database.SCHEMA_VERSION, version
        with closing(sqlite3.connect(scratch)) as connection:
            assert schema_of(connection) == wanted, f"schema {version}, brought up, differs from a new database"


def test_a_step_runs_once_after_a_backup_and_sets_the_version(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database.init_db()
    with sqlite3.connect(scratch) as connection:
        connection.execute("INSERT INTO settings (key, value) VALUES ('kept', '\"yes\"')")
    ran: list[int] = []

    def step(connection: Connection) -> None:
        ran.append(1)
        connection.execute(text('ALTER TABLE users ADD COLUMN "mood_scale" INTEGER DEFAULT 10'))

    monkeypatch.setattr(database, "SCHEMA_VERSION", database.SCHEMA_VERSION + 1)
    monkeypatch.setitem(database.MIGRATIONS, database.SCHEMA_VERSION, step)
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION
    assert ran == [1]
    assert made == [f"before schema {database.SCHEMA_VERSION}"]
    with sqlite3.connect(scratch) as connection:
        assert "mood_scale" in [row[1] for row in connection.execute("PRAGMA table_info(users)")]
        assert connection.execute("SELECT value FROM settings WHERE key = 'kept'").fetchone() == ('"yes"',)
    database.init_db()
    assert ran == [1], "a step runs once"


def test_a_failing_step_leaves_the_database_as_it_was(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database.init_db()
    before = version_of(scratch)

    def broken(connection: Connection) -> None:
        connection.execute(text('ALTER TABLE users ADD COLUMN "half" INTEGER'))
        raise RuntimeError("the step breaks off")

    monkeypatch.setattr(database, "SCHEMA_VERSION", before + 1)
    monkeypatch.setitem(database.MIGRATIONS, before + 1, broken)
    with pytest.raises(RuntimeError):
        database.init_db()
    assert version_of(scratch) == before
    with sqlite3.connect(scratch) as connection:
        assert "half" not in [row[1] for row in connection.execute("PRAGMA table_info(users)")]


def test_a_missing_step_stops_the_start(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database.init_db()
    monkeypatch.setattr(database, "SCHEMA_VERSION", database.SCHEMA_VERSION + 1)
    with pytest.raises(RuntimeError, match="No migration"):
        database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION - 1


def test_a_database_from_a_newer_nexbooks_is_not_touched(scratch: Path) -> None:
    database.init_db()
    with sqlite3.connect(scratch) as connection:
        connection.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
    with pytest.raises(database.SchemaTooNew):
        database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION + 1
    assert made == []


def test_a_database_without_a_version_is_not_taken(scratch: Path) -> None:
    with sqlite3.connect(scratch) as connection:
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")
    with pytest.raises(database.SchemaTooNew):
        database.init_db()


def test_two_starts_at_once_take_a_step_once(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two processes on one data folder (an update with two containers): both read the old version, both want to
    migrate. The step adds a column, so a second run would fail; a step that is not idempotent must never run twice."""
    import threading

    database.init_db()
    ran: list[int] = []

    def step(connection: Connection) -> None:
        ran.append(1)
        connection.execute(text('ALTER TABLE users ADD COLUMN "mood_scale" INTEGER DEFAULT 10'))

    monkeypatch.setattr(database, "SCHEMA_VERSION", database.SCHEMA_VERSION + 1)
    monkeypatch.setitem(database.MIGRATIONS, database.SCHEMA_VERSION, step)
    # Both starts have read the old version before either takes the step: they meet at the backup in between.
    both_read = threading.Barrier(2, timeout=10)
    monkeypatch.setattr(backups, "create", lambda **_kwargs: both_read.wait() and scratch)
    failed: list[BaseException] = []

    def start() -> None:
        try:
            database.init_db()
        except BaseException as exc:  # noqa: BLE001 - the test reports whatever went wrong
            failed.append(exc)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failed == []
    assert ran == [1]
    assert version_of(scratch) == database.SCHEMA_VERSION


def test_switching_to_wal_waits_for_a_start_that_holds_the_database(tmp_path: Path) -> None:
    """Another start is inside its first transaction (``BEGIN IMMEDIATE`` while it makes the tables): SQLite refuses
    the switch to WAL at once instead of waiting, and a start that took that answer broke off with "database is
    locked". The connection setup tries again until the other one is done."""
    import threading
    import time

    path = tmp_path / "busy.db"
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("CREATE TABLE probe (id INTEGER)")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO probe VALUES (1)")
    release = threading.Timer(0.6, lambda: holder.execute("COMMIT"))
    release.start()
    waiting = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=0.1)
    try:
        started = time.monotonic()
        database._pragmas(waiting, None)
        assert time.monotonic() - started >= 0.4, "it waited for the other start"
        assert waiting.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        release.join()
        waiting.close()
        holder.close()


def test_a_database_of_version_one_with_data_is_brought_to_the_libraries_and_keeps_what_it_held(scratch: Path) -> None:
    """The first real step: nexbooks 0.1.0 as it stood (accounts, settings, a waiting job) gains the tables of the
    libraries and the books, a backup is made first, and nothing that was there is touched."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v1.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO users (id, name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
            "email_from_provider, language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
            "signed_in_before, passkey_handle) VALUES (7, 'ada', 'Ada', '', 'operator', 'password', 'hash', '', 0, 'de', "
            "'2026-10-01 10:00:00.000000', 0, '', '', 0, 1, '')")
        connection.execute("INSERT INTO settings (key, value) VALUES ('backup_keep', '5')")
        connection.execute("INSERT INTO jobs (kind, ref, state, attempts, created_at, error) "
                           "VALUES ('probe', 3, 'queued', 0, '2026-10-01 10:00:00.000000', '')")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION
    assert made == [f"before schema {database.SCHEMA_VERSION}"]
    with closing(sqlite3.connect(scratch)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"libraries", "library_members", "works", "editions", "files", "chapters", "people", "credits", "series",
                "library_problems"} <= tables
        assert connection.execute("SELECT name, role FROM users").fetchall() == [("ada", "operator")]
        assert connection.execute("SELECT value FROM settings WHERE key = 'backup_keep'").fetchone() == (5,)
        # What was waiting stays; the step of the search queued the job that fills its index.
        assert connection.execute("SELECT kind, ref FROM jobs").fetchall() == [("probe", 3), ("index", 0)]
        # The new tables work as the models say: a library, a member, a work with an edition and a file.
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO libraries (name, path, kind, created_at) VALUES ('B', '/books', 'both', 'x')")
        connection.execute("INSERT INTO library_members (library_id, user_id, \"right\") VALUES (1, 7, 'manage')")
        connection.execute("INSERT INTO works (title, added_at, updated_at) VALUES ('T', 'x', 'x')")
        connection.execute("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
                           "VALUES (1, 1, 'ebook', 'T', 'x', 'x')")
        connection.execute("INSERT INTO files (library_id, edition_id, role, path, size, mtime_ns, format, seen_at, "
                           "created_at) VALUES (1, 1, 'book', 'a.epub', 1, 1, 'epub', 'x', 'x')")
        # A library that goes takes its editions and files with it; the work stays for the code to settle.
        connection.execute("DELETE FROM libraries WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM editions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM files").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM library_members").fetchone() == (0,)
    database.init_db()
    assert made == [f"before schema {database.SCHEMA_VERSION}"], "a second start changes nothing"


def test_a_database_of_version_two_gains_the_sources_and_keeps_its_books(scratch: Path) -> None:
    """The step to version 3: editions gain three columns (with their defaults), the tables of the sources and of the
    matching appear, and a library with its edition is untouched."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v2.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO libraries (name, path, kind, created_at) VALUES ('B', '/books', 'both', 'x')")
        connection.execute("INSERT INTO works (title, added_at, updated_at) VALUES ('T', 'x', 'x')")
        connection.execute("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
                           "VALUES (1, 1, 'ebook', 'Kept', 'x', 'x')")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION
    assert made == [f"before schema {database.SCHEMA_VERSION}"]
    with closing(sqlite3.connect(scratch)) as connection:
        assert connection.execute("SELECT title, original_language, overlay, grouped_by FROM editions").fetchall() == [
            ("Kept", "", None, "")]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"external_refs", "source_cache", "source_state", "match_queue"} <= tables
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO match_queue (library_id, edition_id, state, created_at, updated_at) "
                           "VALUES (1, 1, 'pending', 'x', 'x')")
        connection.execute("INSERT INTO external_refs (edition_id, source, kind, key) VALUES (1, 'audible', 'edition', "
                           "'B000000001')")
        # An edition that goes takes its refs and its entry with it.
        connection.execute("DELETE FROM editions WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM match_queue").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM external_refs").fetchone() == (0,)


def test_a_database_of_version_five_keeps_its_bookmarks_and_gives_them_an_empty_note(scratch: Path) -> None:
    """The step to version 6: a bookmark can carry a note. The marks that were there keep their place and get none."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v5.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO users (id, name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
            "email_from_provider, language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
            "signed_in_before, passkey_handle) VALUES (7, 'ada', 'Ada', '', 'member', 'password', 'hash', '', 0, 'de', "
            "'2026-10-01 10:00:00.000000', 0, '', '', 0, 1, '')")
        connection.execute("INSERT INTO libraries (name, path, kind, created_at) VALUES ('B', '/books', 'both', 'x')")
        connection.execute("INSERT INTO works (title, added_at, updated_at) VALUES ('T', 'x', 'x')")
        connection.execute("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
                           "VALUES (1, 1, 'audio', 'Kept', 'x', 'x')")
        connection.execute("INSERT INTO bookmarks (user_id, edition_id, locator, fraction, chapter_title, created_at) "
                           "VALUES (7, 1, 'time:12.5', 0.1, 'One', 'x')")
        connection.execute("PRAGMA user_version = 5")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION
    assert made == [f"before schema {database.SCHEMA_VERSION}"]
    with closing(sqlite3.connect(scratch)) as connection:
        assert connection.execute("SELECT locator, fraction, chapter_title, note FROM bookmarks").fetchall() == [
            ("time:12.5", 0.1, "One", "")]


def _a_listener_with_a_place_and_a_mark(connection: sqlite3.Connection) -> None:
    """What a database held before the reading stack: a person, a recording, where they are in it and a mark."""
    connection.execute(
        "INSERT INTO users (id, name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
        "email_from_provider, language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
        "signed_in_before, passkey_handle) VALUES (7, 'ada', 'Ada', '', 'member', 'password', 'hash', '', 0, 'de', "
        "'2026-10-01 10:00:00.000000', 0, '', '', 0, 1, '')")
    connection.execute("INSERT INTO libraries (id, name, path, kind, created_at) VALUES (1, 'B', '/books', 'both', 'x')")
    connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (1, 'T', 'x', 'x')")
    connection.execute("INSERT INTO editions (id, work_id, library_id, kind, title, created_at, updated_at) "
                       "VALUES (1, 1, 1, 'audio', 'Kept', 'x', 'x')")
    connection.execute("INSERT INTO progress (user_id, edition_id, locator, fraction, unit, moved_at, received_at) "
                       "VALUES (7, 1, 'time:599.500', 0.999, 'time', '2026-10-02 10:00:00.000000', "
                       "'2026-10-02 10:00:00.000000')")
    connection.execute("INSERT INTO bookmarks (user_id, edition_id, locator, fraction, chapter_title, created_at, note) "
                       "VALUES (7, 1, 'time:12.5', 0.1, 'One', 'x', 'here')")
    connection.execute("INSERT INTO people (id, name, sort_name, key) VALUES (3, 'Rune Falkner', 'Falkner, Rune', "
                       "'rune falkner')")


def _the_place_the_mark_and_a_finished_pass_after(scratch: Path) -> None:
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        # The place and the mark are as they were; the place's pass is not entered yet (the rule enters it once).
        assert connection.execute("SELECT locator, fraction, pass_done FROM progress").fetchall() == [
            ("time:599.500", 0.999, 0)]
        assert connection.execute("SELECT locator, note FROM bookmarks").fetchall() == [("time:12.5", "here")]
        # The history takes an entry for the edition; its work is the edition's.
        connection.execute("INSERT INTO history (user_id, edition_id, kind, finished_on, source, created_at) "
                           "VALUES (7, 1, 'listened', '2026-10-02', 'auto', 'x')")
        assert connection.execute("SELECT editions.work_id FROM history JOIN editions ON editions.id = "
                                  "history.edition_id").fetchall() == [(1,)]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"person_aliases", "alias_claims", "history", "organize_runs", "organize_moves", "person_info",
                "person_photos"} <= tables
        # A work that goes takes nothing of the history with it; an edition that goes takes its entries.
        connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (2, 'U', 'x', 'x')")
        connection.execute("UPDATE editions SET work_id = 2 WHERE id = 1")
        connection.execute("DELETE FROM works WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM history").fetchone() == (1,)
        connection.execute("DELETE FROM editions WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM history").fetchone() == (0,)


def test_a_database_of_version_eight_with_a_listener_gains_pen_names_and_the_reading_stack(scratch: Path) -> None:
    """Two steps in a row (the pen names of the work on the audiobooks): nothing a person did is lost."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v8.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("PRAGMA user_version = 8")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        assert connection.execute("SELECT count(*) FROM person_aliases").fetchone() == (0,)
    _the_place_the_mark_and_a_finished_pass_after(scratch)


def test_a_database_of_version_nine_keeps_its_pen_names_and_gains_the_reading_stack(scratch: Path) -> None:
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v9.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO person_aliases (person_id, name, key, source, created_at) "
                           "VALUES (3, 'R. Falk', 'r falk', 'isbn', 'x')")
        # The evidence of the pen names belongs to an edition, a split by hand is kept apart, and a recording that
        # went into another one still leads there (schema 9 of the work on the audiobooks).
        connection.execute("INSERT INTO alias_claims (name_a, key_a, name_b, key_b, source, edition_id, apart, "
                           "created_by, created_at) VALUES ('Mara Lind', 'mara lind', 'M. Lind', 'm lind', 'asin', 1, "
                           "0, NULL, 'x')")
        connection.execute("INSERT INTO alias_claims (name_a, key_a, name_b, key_b, source, edition_id, apart, "
                           "created_by, created_at) VALUES ('Rune Falkner', 'rune falkner', 'Odd Name', 'odd name', "
                           "'hand', NULL, 1, 7, 'x')")
        connection.execute("INSERT INTO edition_moves (old_id, new_id, shift_ms, created_at) VALUES (5, 1, 1500, 'x')")
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        assert connection.execute("SELECT person_id, name, source FROM person_aliases").fetchall() == [
            (3, "R. Falk", "isbn")]
        assert connection.execute("SELECT key_a, key_b, edition_id, apart, created_by FROM alias_claims "
                                  "ORDER BY id").fetchall() == [("mara lind", "m lind", 1, 0, None),
                                                                ("rune falkner", "odd name", None, 1, 7)]
        assert connection.execute("SELECT old_id, new_id, shift_ms FROM edition_moves").fetchall() == [(5, 1, 1500)]
    _the_place_the_mark_and_a_finished_pass_after(scratch)


def test_a_database_of_version_ten_keeps_its_members_and_gains_the_tables_of_fetching_books(scratch: Path) -> None:
    """Schema 11: a library keeps its members with the rights they had (``read`` and ``manage``), the right
    ``acquire`` can be given from then on, and the tables of the indexers, Prowlarr and the download programs are there
    and empty."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v10.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute(
            "INSERT INTO users (id, name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
            "email_from_provider, language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
            "signed_in_before, passkey_handle) VALUES (8, 'tom', 'Tom', '', 'member', 'password', 'hash', '', 0, 'de', "
            "'2026-10-01 10:00:00.000000', 0, '', '', 0, 1, '')")
        connection.execute('INSERT INTO library_members (library_id, user_id, "right") VALUES (1, 7, \'read\')')
        connection.execute('INSERT INTO library_members (library_id, user_id, "right") VALUES (1, 8, \'manage\')')
        connection.execute("INSERT INTO settings (key, value) VALUES ('kept', '\"yes\"')")
        connection.execute("PRAGMA user_version = 10")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute('SELECT user_id, "right" FROM library_members ORDER BY user_id').fetchall() == [
            (7, "read"), (8, "manage")]
        assert connection.execute("SELECT value FROM settings WHERE key = 'kept'").fetchone() == ('"yes"',)
        for table in ("indexers", "prowlarr_connections", "download_clients", "path_mappings"):
            assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name = ?", (table,)).fetchone() == (1,)
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,), table  # noqa: S608
        connection.execute('UPDATE library_members SET "right" = \'acquire\' WHERE user_id = 7')
        assert connection.execute('SELECT "right" FROM library_members WHERE user_id = 7').fetchone() == ("acquire",)
        # A path mapping goes with its program.
        connection.execute("INSERT INTO download_clients (id, name, kind, url, created_at, updated_at) "
                           "VALUES (1, 'P', 'sabnzbd', 'http://p.example.com', 'x', 'x')")
        connection.execute("INSERT INTO path_mappings (client_id, remote, local, created_at) VALUES (1, '/a', '/b', 'x')")
        connection.execute("DELETE FROM download_clients WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM path_mappings").fetchone() == (0,)
    _the_place_the_mark_and_a_finished_pass_after(scratch)


def test_a_database_of_version_eleven_keeps_its_indexers_and_gains_searches_blocklist_and_downloads(
        scratch: Path) -> None:
    """Schema 12: an indexer and a program stay as they were, the tables of the searches, the blocklist
    and the downloads are there and empty; a search goes with its person and its releases with the search, a download
    stays when its indexer or program goes."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v11.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO indexers (id, name, kind, url, created_at, updated_at) "
                           "VALUES (3, 'Shelf', 'newznab', 'https://indexer.example.com/api', 'x', 'x')")
        connection.execute("INSERT INTO download_clients (id, name, kind, url, created_at, updated_at) "
                           "VALUES (4, 'P', 'sabnzbd', 'http://p.example.com', 'x', 'x')")
        connection.execute("PRAGMA user_version = 11")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name FROM indexers").fetchall() == [("Shelf",)]
        assert connection.execute("SELECT name FROM download_clients").fetchall() == [("P",)]
        for table in ("release_searches", "search_releases", "blocklist", "downloads"):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,), table  # noqa: S608
        connection.execute("INSERT INTO release_searches (id, user_id, language, state, error, created_at) "
                           "VALUES (1, 7, 'de', 'done', '', 'x')")
        connection.execute("INSERT INTO search_releases (search_id, indexer_id, protocol, title, kind) "
                           "VALUES (1, 3, 'usenet', 'Some.Release-GRP', 'ebook')")
        connection.execute("INSERT INTO downloads (user_id, kind, title, protocol, indexer_id, client_id, state, "
                           "created_at, updated_at) VALUES (7, 'ebook', 'Some.Release-GRP', 'usenet', 3, 4, 'sent', "
                           "'x', 'x')")
        connection.execute("DELETE FROM indexers WHERE id = 3")
        connection.execute("DELETE FROM download_clients WHERE id = 4")
        assert connection.execute("SELECT indexer_id, client_id FROM downloads").fetchall() == [(None, None)]
        assert connection.execute("SELECT indexer_id FROM search_releases").fetchall() == [(None,)]
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM release_searches").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM search_releases").fetchone() == (0,)
        assert connection.execute("SELECT user_id FROM downloads").fetchall() == [(None,)]


def test_schema_twelve_keeps_a_block_when_its_work_goes_and_allows_one_active_download_of_a_book(
        scratch: Path) -> None:
    """Schema 12: a block outlives the work it was made for (both ids go empty, the key stays), a release
    is blocked once per book, and a book of one kind has one release on its way."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v11.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("PRAGMA user_version = 11")
        connection.commit()
    database.init_db()
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        insert = ("INSERT INTO blocklist (work_id, book_key, kind, title, title_key, protocol, reason, created_at) "
                  "VALUES (?, ?, 'ebook', 'T', ?, 'usenet', 'by_hand', 'x')")
        connection.execute(insert, (1, "ada|t", "a"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, (1, "ada|t", "a"))
        connection.execute("DELETE FROM works WHERE id = 1")
        assert connection.execute("SELECT work_id, foreign_id, book_key FROM blocklist").fetchall() == [
            (None, None, "ada|t")]
        # A second work with the same key waits as the first did: one entry per release and key.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, (None, "ada|t", "a"))
        connection.execute(insert, (None, "ada|t", "b"))
        connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (2, 'T', 'x', 'x')")
        download = ("INSERT INTO downloads (work_id, kind, title, protocol, state, created_at, updated_at) "
                    "VALUES (2, ?, ?, 'usenet', ?, 'x', 'x')")
        connection.execute(download, ("ebook", "one", "queued"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(download, ("ebook", "two", "sending"))
        connection.execute(download, ("audio", "two", "sending"))
        connection.execute(download, ("ebook", "three", "finished"))
        connection.execute(download, ("ebook", "four", "finished"))


def test_a_database_of_version_twelve_keeps_its_downloads_and_gains_wishes_and_tracking(scratch: Path) -> None:
    """Schema 13: a download that was ``sent`` is ``queued`` now and gains the columns tracking writes, the
    tables of the wishes, the foreign jobs and the history are there and empty, a wish goes with its person and keeps no
    download (the download stays), and a book of one kind has one release that is not over, whatever state it is in."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v12.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (5, 'T', 'x', 'x')")
        connection.execute("INSERT INTO downloads (work_id, kind, title, protocol, state, created_at, updated_at) "
                           "VALUES (5, 'ebook', 'Some.Release-GRP', 'usenet', 'sent', 'x', 'x')")
        connection.execute("INSERT INTO blocklist (work_id, book_key, kind, title, title_key, protocol, reason, "
                           "created_at) VALUES (5, 'ada|t', 'ebook', 'Some.Release-GRP', 'somereleasegrp', 'usenet', "
                           "'by_hand', 'x')")
        connection.execute("PRAGMA user_version = 12")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT state, progress, problem_code, missing_count, removal_pending, seed_state "
                                  "FROM downloads").fetchall() == [("queued", None, "", 0, 0, "")]
        # A block gains the library it was made for; an old one has none (and is seen by nobody until a person of the
        # work's libraries blocks it again, as it still turns the release down).
        assert connection.execute("SELECT title_key, library_id FROM blocklist").fetchall() == [("somereleasegrp", None)]
        for table in ("procure_wishes", "foreign_jobs", "procure_history"):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,), table  # noqa: S608
        for state in ("problem", "completed", "paused"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO downloads (work_id, kind, title, protocol, state, created_at, "
                                   "updated_at) VALUES (5, 'ebook', 'Other', 'usenet', ?, 'x', 'x')", (state,))
        # ... for one library: another library may load a release of its own for the same book.
        connection.execute("INSERT INTO libraries (id, name, path, kind, created_at) VALUES (2, 'C', '/c', 'both', 'x')")
        download = ("INSERT INTO downloads (work_id, library_id, kind, title, protocol, state, created_at, updated_at) "
                    "VALUES (5, ?, 'audio', 'Two', 'usenet', 'queued', 'x', 'x')")
        without_library = download.replace("?", "NULL", 1)
        connection.execute(download, (1,))
        connection.execute(download, (2,))
        connection.execute(without_library)
        # A library that is gone (empty) is a place of its own, not a way round the index.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(without_library)
        for library in (1, 2):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(download, (library,))
        connection.execute("DELETE FROM downloads WHERE kind = 'audio'")
        # A job that is over does not stand in the way.
        for state in ("failed", "removed", "imported"):
            connection.execute("INSERT INTO downloads (work_id, kind, title, protocol, state, created_at, updated_at) "
                               "VALUES (5, 'ebook', 'Over', 'usenet', ?, 'x', 'x')", (state,))
        # A wish is one per book, kind and language while it is open.
        wish = ("INSERT INTO procure_wishes (user_id, work_id, kind, language, state, created_at, updated_at) "
                "VALUES (7, 5, 'audio', 'de', ?, 'x', 'x')")
        connection.execute(wish, ("searching",))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(wish, ("not_found",))
        connection.execute(wish, ("done",))
        # ... and for one library: a wish of another library is another wish.
        library_wish = ("INSERT INTO procure_wishes (user_id, work_id, kind, language, library_id, state, created_at, "
                        "updated_at) VALUES (7, 5, 'ebook', 'de', ?, 'searching', 'x', 'x')")
        connection.execute(library_wish, (1,))
        connection.execute(library_wish, (2,))
        for library in (1, 2):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(library_wish, (library,))
        connection.execute("DELETE FROM procure_wishes WHERE kind = 'ebook'")
        connection.execute("UPDATE downloads SET wish_id = 1 WHERE state = 'queued'")
        connection.execute("DELETE FROM procure_wishes WHERE id = 1")
        assert connection.execute("SELECT wish_id FROM downloads WHERE state = 'queued'").fetchone() == (None,)
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM procure_wishes").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM downloads").fetchone() == (4,)


def test_a_database_of_version_thirteen_keeps_its_downloads_and_gains_the_filing(scratch: Path) -> None:
    """Schema 14: a download gains the columns of its filing (empty, nothing begun), the tables of the
    files of a filing, the trash and the notes are there and empty; the files go with their download, a note with its
    person and its edition, a trash entry with its library; a download turned down or still being uploaded does not hold
    the place of its book."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v13.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (5, 'T', 'x', 'x')")
        connection.execute("INSERT INTO downloads (work_id, library_id, kind, title, protocol, state, created_at, "
                           "updated_at) VALUES (5, 1, 'ebook', 'Some.Release-GRP', 'usenet', 'completed', 'x', 'x')")
        connection.execute("PRAGMA user_version = 13")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT state, place_step, source_path, decision, edition_id, replaced "
                                  "FROM downloads").fetchall() == [("completed", "", "", None, None, None)]
        for table in ("procure_files", "trash_entries", "procure_notices"):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,), table  # noqa: S608
        connection.execute("INSERT INTO procure_files (download_id, path, size, format, role, state) "
                           "VALUES (1, 'a.epub', 3, 'epub', 'book', 'take')")
        connection.execute("INSERT INTO trash_entries (library_id, kind, folder, size, created_at, expires_at) "
                           "VALUES (1, 'ebook', 'abc', 0, 'x', 'x')")
        connection.execute("INSERT INTO procure_notices (user_id, edition_id, code, created_at) "
                           "VALUES (7, 1, 'upgrade_position', 'x')")
        # Turned down, or still coming: another release of the book may be on its way at the same time.
        for state in ("rejected", "uploading"):
            connection.execute("INSERT INTO downloads (work_id, library_id, kind, title, protocol, state, created_at, "
                               "updated_at) VALUES (5, 1, 'ebook', 'Other', 'upload', ?, 'x', 'x')", (state,))
        connection.execute("DELETE FROM downloads WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM procure_files").fetchone() == (0,)
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM procure_notices").fetchone() == (0,)
        connection.execute("DELETE FROM libraries WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM trash_entries").fetchone() == (0,)


def test_a_database_of_version_fourteen_keeps_its_indexers_and_wishes_and_gains_the_automatic_work(
        scratch: Path) -> None:
    """Schema 15: an indexer gains the count of the releases fetched today and the moment its newest
    entries were read (nothing yet), a search is one of ``search``, a wish has no hand-over that waits, and the tables of
    the entries already seen and of the rounds are there and empty; the entries seen go with their indexer."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v14.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO indexers (id, name, kind, url, created_at, updated_at) "
                           "VALUES (1, 'Shelf', 'newznab', 'https://indexer.example.com/api', 'x', 'x')")
        connection.execute("INSERT INTO release_searches (id, user_id, language, state, created_at) "
                           "VALUES (1, 7, 'de', 'done', 'x')")
        connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (5, 'T', 'x', 'x')")
        connection.execute("INSERT INTO procure_wishes (user_id, work_id, kind, language, state, created_at, "
                           "updated_at) VALUES (7, 5, 'audio', 'de', 'not_found', 'x', 'x')")
        connection.execute("PRAGMA user_version = 14")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT grab_day, grab_count, rss_checked_at, rss_error FROM indexers").fetchall() == [
            ("", 0, None, "")]
        assert connection.execute("SELECT origin FROM release_searches").fetchall() == [("search",)]
        assert connection.execute("SELECT state, handover FROM procure_wishes").fetchall() == [("not_found", None)]
        for table in ("rss_seen", "auto_runs"):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,), table  # noqa: S608
        connection.execute("INSERT INTO rss_seen (indexer_id, key, seen_at) VALUES (1, 'abc', 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO rss_seen (indexer_id, key, seen_at) VALUES (1, 'abc', 'x')")
        connection.execute("DELETE FROM indexers WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM rss_seen").fetchone() == (0,)


def test_a_database_of_version_fifteen_keeps_its_books_and_gains_the_moves_into_another_library(
        scratch: Path) -> None:
    """Schema 16: the tables of a move into another library are there and empty; a move keeps its
    journal when its edition goes (the edition's place is emptied), and its files go with it."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v15.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("PRAGMA user_version = 15")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT title FROM editions").fetchall() == [("Kept",)]
        assert connection.execute("SELECT locator FROM progress").fetchall() == [("time:599.500",)]
        for table in ("relocations", "relocation_files"):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (0,), table  # noqa: S608
        connection.execute("INSERT INTO relocations (id, edition_id, source_id, target_id, state, step, created_at) "
                           "VALUES (1, 1, 1, 1, 'failed', 'place', 'x')")
        connection.execute("INSERT INTO relocation_files (relocation_id, source, target, size, mtime_ns, state) "
                           "VALUES (1, 'a.mp3', 'b/a.mp3', 1, 1, 'planned')")
        # One move of an edition at a time; a finished one is no bar.
        connection.execute("INSERT INTO relocations (id, edition_id, source_id, target_id, state, step, created_at) "
                           "VALUES (2, 1, 1, 1, 'queued', 'place', 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO relocations (id, edition_id, source_id, target_id, state, step, "
                               "created_at) VALUES (3, 1, 1, 1, 'running', 'place', 'x')")
        connection.execute("DELETE FROM relocations WHERE id = 2")
        connection.execute("DELETE FROM editions WHERE id = 1")
        assert connection.execute("SELECT edition_id, error FROM relocations").fetchall() == [(None, "")]
        connection.execute("DELETE FROM relocations WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM relocation_files").fetchone() == (0,)


def _a_wrong_book_filed_for_a_book_of_the_sources(connection: sqlite3.Connection) -> None:
    """What the gap of the old check left: the wish for "Ruß" (a book of the sources) took "Wir wollen Ruß sehen", the
    edition of that book carries the name of the book of the sources, the wish is done with its work, a turned-down
    release of "Ruß" and a running download hang on it too. Beside it a book filed right ("Die Brandung")."""
    connection.execute("INSERT INTO works (id, title, added_at, updated_at) VALUES (5, 'Wir wollen Ruß sehen', 'x', "
                       "'x'), (6, 'Die Brandung', 'x', 'x')")
    connection.execute("INSERT INTO editions (id, work_id, library_id, kind, title, created_at, updated_at) VALUES "
                       "(5, 5, 1, 'ebook', 'Wir wollen Ruß sehen', 'x', 'x'), "
                       "(6, 6, 1, 'ebook', 'Die Brandung', 'x', 'x')")
    connection.execute("INSERT INTO foreign_books (id, token, ref, title, authors, seen_at, created_at) VALUES "
                       "(1, 'tok1', 'hardcover:11', 'Ruß', '[\"Rune Falkner\"]', 'x', 'x'), "
                       "(2, 'tok2', 'hardcover:12', 'Die Brandung', '[\"Rune Falkner\"]', 'x', 'x')")
    connection.execute("INSERT INTO external_refs (edition_id, source, kind, key, market) VALUES "
                       "(5, 'hardcover', 'work', '11', ''), (5, 'audible', 'edition', 'B0X', 'de'), "
                       "(6, 'hardcover', 'work', '12', '')")
    connection.execute("INSERT INTO procure_wishes (id, user_id, work_id, kind, language, library_id, state, "
                       "created_at, updated_at, done_at) VALUES (1, 7, 5, 'ebook', 'de', 1, 'done', 'x', 'x', 'x'), "
                       "(2, 7, 6, 'ebook', 'de', 1, 'done', 'x', 'x', 'x')")
    for ident, work, foreign, state, wish in ((1, 5, 1, "imported", 1), (2, 5, 1, "rejected", 1),
                                              (3, 5, 1, "queued", None), (4, 6, 2, "imported", 2)):
        connection.execute("INSERT INTO downloads (id, user_id, work_id, foreign_id, library_id, kind, title, "
                           "protocol, state, created_at, updated_at, wish_id) VALUES (?, 7, ?, ?, 1, 'ebook', 'R', "
                           "'usenet', ?, 'x', 'x', ?)", (ident, work, foreign, state, wish))
    for result in ("done", "rejected"):
        connection.execute("INSERT INTO procure_history (user_id, library_id, work_id, foreign_id, kind, book_title, "
                           "result, created_at) VALUES (7, 1, 5, 1, 'ebook', 'Wir wollen Ruß sehen', ?, 'x')",
                           (result,))


def test_a_database_of_version_sixteen_gains_what_was_wished_and_sets_a_wrong_book_apart(scratch: Path) -> None:
    """Schema 17: a wish, a download and a line of the history gain what was wished (nothing yet: nothing is made up).
    Once, a book filed under the wish of another book of the sources is set apart: the name of the book of the sources
    leaves its edition (other names stay), the wish searches again for the book of the sources, the running download
    and the turned-down line go back to it; the work, its edition and the line of what was filed stay. A book filed
    right is left as it is."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v16.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        _a_wrong_book_filed_for_a_book_of_the_sources(connection)
        # A move into another library of schema 16, with its journal: it stays as it was.
        connection.execute("INSERT INTO relocations (id, edition_id, source_id, target_id, state, step, created_at) "
                           "VALUES (1, 6, 1, 1, 'failed', 'place', 'x')")
        connection.execute("INSERT INTO relocation_files (relocation_id, source, target, size, mtime_ns, state) "
                           "VALUES (1, 'a.epub', 'b/a.epub', 1, 1, 'planned')")
        connection.execute("PRAGMA user_version = 16")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        for table in ("procure_wishes", "downloads", "procure_history"):
            assert {row[0] for row in connection.execute(f'SELECT wished FROM "{table}"')} == {None}, table  # noqa: S608
        assert connection.execute("SELECT edition_id, source, key FROM external_refs ORDER BY id").fetchall() == [
            (5, "audible", "B0X"), (6, "hardcover", "12")]
        assert connection.execute("SELECT id, work_id, foreign_id, state, done_at FROM procure_wishes ORDER BY id"
                                  ).fetchall() == [(1, None, 1, "searching", None), (2, 6, None, "done", "x")]
        assert connection.execute("SELECT id, work_id FROM downloads ORDER BY id").fetchall() == [
            (1, 5), (2, 5), (3, None), (4, 6)]
        assert connection.execute("SELECT result, work_id FROM procure_history ORDER BY id").fetchall() == [
            ("done", 5), ("rejected", None)]
        assert connection.execute("SELECT id, title FROM works ORDER BY id").fetchall()[-2:] == [
            (5, "Wir wollen Ruß sehen"), (6, "Die Brandung")]
        assert connection.execute("SELECT edition_id, state FROM relocations").fetchall() == [(6, "failed")]
        assert connection.execute("SELECT source, state FROM relocation_files").fetchall() == [("a.epub", "planned")]


def test_a_database_of_version_seventeen_keeps_its_devices_and_gains_the_codes_that_connect_the_app(
        scratch: Path) -> None:
    """Schema 18: the table of the codes is there and empty, and everything that was there stays. A code goes
    with its account; a device that goes (it is revoked, not deleted, but the table allows it) leaves the used code
    standing with no device; two codes cannot share a hash."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v17.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at) "
                           "VALUES (1, 'a' || hex(zeroblob(15)), 7, 'Kept phone', 'h1', 'nbd_', 'x')")
        connection.execute("PRAGMA user_version = 17")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name FROM devices").fetchall() == [("Kept phone",)]
        assert connection.execute("SELECT locator FROM progress").fetchall() == [("time:599.500",)]
        assert connection.execute("SELECT count(*) FROM app_pairings").fetchone() == (0,)
        connection.execute("INSERT INTO app_pairings (id, account_id, secret_hash, code_hash, state, created_at, "
                           "expires_at, device_id) VALUES ('p1', 7, 's1', 'c1', 'used', 'x', 'x', 1)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO app_pairings (id, account_id, secret_hash, code_hash, state, created_at, "
                               "expires_at) VALUES ('p2', 7, 's2', 'c1', 'open', 'x', 'x')")
        connection.execute("DELETE FROM devices WHERE id = 1")
        assert connection.execute("SELECT device_id FROM app_pairings").fetchall() == [(None,)]
        connection.execute("INSERT INTO app_pairings (id, account_id, secret_hash, code_hash, state, created_at, "
                           "expires_at) VALUES ('p3', 7, 's3', 'c3', 'open', 'x', 'x')")
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM app_pairings").fetchone() == (0,)


def test_a_database_of_version_twenty_gains_the_requests_a_device_without_a_camera_makes(scratch: Path) -> None:
    """Schema 21: the table of the requests is there and empty, and everything that was there stays. A request
    goes with the account that confirmed it; a device that goes leaves the used request standing with no device; two
    requests cannot share a code."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v20.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at) "
                           "VALUES (1, 'a' || hex(zeroblob(15)), 7, 'Kept phone', 'h1', 'nbd_', 'x')")
        connection.execute("INSERT INTO app_pairings (id, account_id, secret_hash, code_hash, state, created_at, "
                           "expires_at) VALUES ('old', 7, 's0', 'c0', 'open', 'x', 'x')")
        connection.execute("PRAGMA user_version = 20")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    insert = ("INSERT INTO app_requests (id, secret_hash, code_hash, device_name, state, sender, created_at, expires_at"
              "{extra}) VALUES ('{ident}', 's', '{code}', 'TV', '{state}', 'x', 'x', 'x'{values})")
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name FROM devices").fetchall() == [("Kept phone",)]
        assert connection.execute("SELECT id FROM app_pairings").fetchall() == [("old",)]
        assert connection.execute("SELECT locator FROM progress").fetchall() == [("time:599.500",)]
        assert connection.execute("SELECT count(*) FROM app_requests").fetchone() == (0,)
        connection.execute(insert.format(extra=", account_id, device_id", ident="r1", code="c1", state="used",
                                         values=", 7, 1"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert.format(extra="", ident="r2", code="c1", state="waiting", values=""))
        # An account that is not there cannot confirm.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert.format(extra=", account_id", ident="r9", code="c9", state="confirmed", values=", 99"))
        connection.execute("DELETE FROM devices WHERE id = 1")
        assert connection.execute("SELECT device_id FROM app_requests").fetchall() == [(None,)]
        connection.execute(insert.format(extra=", account_id", ident="r3", code="c3", state="confirmed", values=", 7"))
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM app_requests").fetchone() == (0,)


def test_a_database_of_version_twenty_one_gains_the_catalogue_of_authors_and_keeps_its_people(scratch: Path) -> None:
    """Schema 22: the authors of the catalogue, their works and the moves are there and empty, the people
    and books stay. An author whose person goes keeps the catalogue without the link; works and moves go with their
    author; a work keeps standing when its book of the sources goes; a work's key is once per author; the numbers of
    authors and works are never given twice."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v21.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("PRAGMA user_version = 21")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name FROM people").fetchall() == [("Rune Falkner",)]
        assert connection.execute("SELECT locator FROM progress").fetchall() == [("time:599.500",)]
        for table in ("catalog_people", "catalog_works", "catalog_moves"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # noqa: S608
        connection.execute("INSERT INTO catalog_people (token, name, key, person_id, created_at) "
                           "VALUES ('t1', 'Rune Falkner', 'rune falkner', 3, 'x')")
        connection.execute("INSERT INTO foreign_books (id, token, ref, title, seen_at, created_at) "
                           "VALUES (5, 'f5', 'openlibrary:OL1W', 'Nebelchor', 'x', 'x')")
        connection.execute("INSERT INTO catalog_works (catalog_person_id, key, foreign_id, kind) VALUES (1, 'k1', 5, "
                           "'roman')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO catalog_works (catalog_person_id, key, kind) VALUES (1, 'k1', 'roman')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO catalog_people (token, name, key, created_at) "
                               "VALUES ('t2', 'Other', 'rune falkner', 'x')")
        connection.execute("INSERT INTO catalog_moves (catalog_person_id, work_key, kind, user_id, created_at) "
                           "VALUES (1, 'k1', 'erz', 7, 'x')")
        connection.execute("DELETE FROM foreign_books WHERE id = 5")
        assert connection.execute("SELECT key, foreign_id FROM catalog_works").fetchall() == [("k1", None)]
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT user_id FROM catalog_moves").fetchall() == [(None,)]
        connection.execute("DELETE FROM people WHERE id = 3")
        assert connection.execute("SELECT person_id FROM catalog_people").fetchall() == [(None,)]
        connection.execute("DELETE FROM catalog_people WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM catalog_works").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM catalog_moves").fetchone() == (0,)
        connection.execute("INSERT INTO catalog_people (token, name, key, created_at) VALUES ('t3', 'Ida', 'ida', 'x')")
        assert connection.execute("SELECT id FROM catalog_people").fetchall() == [(2,)]
        # The number of a move is never given twice either (the first move went with its author).
        connection.execute("INSERT INTO catalog_moves (catalog_person_id, work_key, kind, created_at) "
                           "VALUES (2, 'k2', 'erz', 'x')")
        assert connection.execute("SELECT id FROM catalog_moves").fetchall() == [(2,)]


def test_a_database_of_version_twenty_two_keeps_its_devices_and_learns_where_a_device_came_from(scratch: Path) -> None:
    """Schema 23: every device and request stays, with nothing to say where it came from (all
    of them were made in a browser); a device connected through another one says which, and a request confirmed from
    the app says by which device. When that device row went, both forget it and stay."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v22.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at) "
                           "VALUES (1, 'a' || hex(zeroblob(15)), 7, 'Kept phone', 'h1', 'nbd_', 'x')")
        connection.execute("INSERT INTO app_requests (id, secret_hash, code_hash, device_name, state, sender, "
                           "account_id, created_at, expires_at) VALUES ('r1', 's', 'c1', 'TV', 'confirmed', 'x', 7, "
                           "'x', 'x')")
        connection.execute("INSERT INTO app_seen (device_id, work_id) VALUES (1, 5)")
        connection.execute("INSERT INTO app_pairings (id, account_id, secret_hash, code_hash, state, created_at, "
                           "expires_at, device_id) VALUES ('p1', 7, 's0', 'c0', 'used', 'x', 'x', 1)")
        connection.execute("PRAGMA user_version = 22")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name, via_device_id FROM devices").fetchall() == [("Kept phone", None)]
        assert connection.execute("SELECT id, state, confirmed_by FROM app_requests").fetchall() == [
            ("r1", "confirmed", None)]
        assert connection.execute("SELECT locator FROM progress").fetchall() == [("time:599.500",)]
        connection.execute("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at, "
                           "via_device_id) VALUES (2, 'b' || hex(zeroblob(15)), 7, 'TV', 'h2', 'nbd_', 'x', 1)")
        connection.execute("UPDATE app_requests SET confirmed_by = 1")
        # A device that is not there connects nothing.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at, "
                               "via_device_id) VALUES (3, 'c' || hex(zeroblob(15)), 7, 'X', 'h3', 'nbd_', 'x', 99)")
        assert "ix_devices_via" in [row[1] for row in connection.execute("PRAGMA index_list(devices)")]
        # Two requests still cannot share a code, and a device token is still one of a kind.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO app_requests (id, secret_hash, code_hash, device_name, state, sender, "
                               "created_at, expires_at) VALUES ('r2', 's', 'c1', 'TV', 'waiting', 'x', 'x', 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at) "
                               "VALUES (4, 'd' || hex(zeroblob(15)), 7, 'X', 'h1', 'nbd_', 'x')")
        # What pointed at the devices before points at the new table: a device that goes takes its rows along.
        connection.execute("DELETE FROM devices WHERE id = 1")
        assert connection.execute("SELECT name, via_device_id FROM devices").fetchall() == [("TV", None)]
        assert connection.execute("SELECT confirmed_by FROM app_requests").fetchall() == [(None,)]
        assert connection.execute("SELECT count(*) FROM app_seen").fetchone() == (0,)
        assert connection.execute("SELECT device_id FROM app_pairings").fetchall() == [(None,)]
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM devices").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM app_requests").fetchone() == (0,)


def test_the_step_to_schema_twenty_three_makes_the_tables_word_for_word_as_a_new_database(scratch: Path) -> None:
    """The two tables are made again (not given a column at the end), so a database brought up from 22 holds the same
    statements as a new one; nothing of them is left behind."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v22.sql").read_text(encoding="utf-8"))
        connection.execute("PRAGMA user_version = 22")
        connection.commit()
    database.init_db()
    current = fresh_schema_sql()
    with closing(sqlite3.connect(scratch)) as connection:
        kept = {" ".join(str(row[0]).split()) + ";" for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name IN ('devices', 'app_requests')")}
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_v22%'")]
    wanted = {line for line in current.splitlines()
              if line.startswith(("CREATE TABLE devices ", "CREATE TABLE app_requests "))
              or " ON devices " in line or " ON app_requests " in line}
    assert kept == wanted and len(wanted) == 8
    assert names == []


def test_a_database_of_version_twenty_three_gains_the_themed_shelves_and_the_days_of_a_pass(scratch: Path) -> None:
    """Schema 24: the shelves and the record of what was fetched are there and empty, the place and the finished
    book stay and gain a column each that earlier rows leave empty. A shelf goes with its book; the place on a shelf is once;
    the fetch is once for a source, a market and a shelf."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v23.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO history (user_id, edition_id, kind, finished_on, source, created_at) "
                           "VALUES (7, 1, 'listened', '2026-10-02', 'auto', 'x')")
        connection.execute("PRAGMA user_version = 23")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT locator, pass_done, pass_started_at FROM progress").fetchall() == [
            ("time:599.500", 0, None)]
        assert connection.execute("SELECT finished_on, days FROM history").fetchall() == [("2026-10-02", None)]
        for table in ("shelf_entries", "shelf_fetches"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # noqa: S608
        connection.execute("INSERT INTO foreign_books (id, token, ref, title, seen_at, created_at) "
                           "VALUES (5, 'f5', 'apple:1', 'Nebelwacht', 'x', 'x')")
        entry = ("INSERT INTO shelf_entries (source, market, shelf, section, rank, foreign_id, fetched_at) "
                 "VALUES ('apple', 'de', 'krimi', 'new', {rank}, 5, 'x')")
        connection.execute(entry.format(rank=1))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(entry.format(rank=1))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO shelf_entries (source, market, shelf, section, rank, foreign_id, fetched_at) "
                               "VALUES ('apple', 'de', 'krimi', 'new', 2, 99, 'x')")
        connection.execute("INSERT INTO shelf_fetches (source, market, shelf) VALUES ('apple', 'de', 'krimi')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO shelf_fetches (source, market, shelf) VALUES ('apple', 'de', 'krimi')")
        connection.execute("DELETE FROM foreign_books WHERE id = 5")
        assert connection.execute("SELECT count(*) FROM shelf_entries").fetchone() == (0,)
        connection.execute("UPDATE progress SET pass_started_at = '2026-10-01 10:00:00.000000'")
        connection.execute("UPDATE history SET days = 3")
        assert connection.execute("SELECT days FROM history").fetchall() == [(3,)]


def test_a_database_of_version_twenty_four_keeps_its_trash_and_gains_the_states_kept_with_it(scratch: Path) -> None:
    """Schema 25 (the wrong book): what people had in an edition that was sent away is kept with the trash entry of its
    files; the row goes with the entry, and with the library."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v24.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("INSERT INTO trash_entries (id, library_id, kind, folder, size, created_at, expires_at) "
                           "VALUES (4, 1, 'ebook', 'abc', 10, 'x', 'x')")
        connection.execute("PRAGMA user_version = 24")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    _the_place_the_mark_and_a_finished_pass_after(scratch)
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT id FROM trash_entries").fetchall() == [(4,)]
        assert connection.execute("SELECT count(*) FROM kept_states").fetchone() == (0,)
        connection.execute("INSERT INTO kept_states (library_id, trash_id, paths, states, created_at, expires_at) "
                           "VALUES (1, 4, '[\"a.epub\"]', '{}', 'x', 'x')")
        connection.execute("INSERT INTO kept_states (library_id, trash_id, paths, states, created_at, expires_at) "
                           "VALUES (1, NULL, '[\"b.epub\"]', '{}', 'x', 'x')")
        connection.execute("DELETE FROM trash_entries WHERE id = 4")
        assert connection.execute("SELECT paths FROM kept_states").fetchall() == [('["b.epub"]',)]
        connection.execute("DELETE FROM libraries WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM kept_states").fetchone() == (0,)


def test_a_database_of_version_twenty_five_gains_watching_and_keeps_its_people(scratch: Path) -> None:
    """Schema 26: who watches whom, and what the watching did with each work, are there and empty, the people
    and books stay. A watch is once per person and author; an item is once per person, author, work, language and kind;
    both go with their person and with their author; an item keeps standing when its book of the sources goes; the numbers
    are never given twice."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v25.sql").read_text(encoding="utf-8"))
        _a_listener_with_a_place_and_a_mark(connection)
        connection.execute("PRAGMA user_version = 25")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name FROM people").fetchall() == [("Rune Falkner",)]
        assert connection.execute("SELECT locator FROM progress").fetchall() == [("time:599.500",)]
        for table in ("author_watches", "author_watch_items"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # noqa: S608
        connection.execute("INSERT INTO catalog_people (token, name, key, created_at) VALUES ('t1', 'Rune Falkner', "
                           "'rune falkner', 'x')")
        connection.execute("INSERT INTO foreign_books (id, token, ref, title, seen_at, created_at) "
                           "VALUES (5, 'f5', 'openlibrary:OL1W', 'Nebelchor', 'x', 'x')")
        watch = ("INSERT INTO author_watches (user_id, catalog_person_id, kind, language, more, created_at, updated_at) "
                 "VALUES ({user}, {author}, 'both', 'de', 0, 'x', 'x')")
        item = ("INSERT INTO author_watch_items (user_id, catalog_person_id, work_key, language, kind, via, foreign_id, "
                "created_at) VALUES (7, {author}, 'k1', 'de', '{kind}', 'wish', 5, 'x')")
        connection.execute(watch.format(user=7, author=1))
        connection.execute(item.format(author=1, kind="ebook"))
        # The same person watches an author once, and the same work in a language and a kind is done once.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(watch.format(user=7, author=1))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(item.format(author=1, kind="ebook"))
        connection.execute(item.format(author=1, kind="audio"))
        # An author that is not there cannot be watched.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(watch.format(user=7, author=99))
        # The book of the sources goes: what was done stays, without the book.
        connection.execute("DELETE FROM foreign_books WHERE id = 5")
        assert connection.execute("SELECT foreign_id FROM author_watch_items").fetchall() == [(None,), (None,)]
        # The author goes: watch and items go with it.
        connection.execute("INSERT INTO catalog_people (token, name, key, created_at) VALUES ('t2', 'Ida', 'ida', 'x')")
        connection.execute(watch.format(user=7, author=2))
        connection.execute(item.format(author=2, kind="ebook").replace(", 5, 'x')", ", NULL, 'x')"))
        connection.execute("DELETE FROM catalog_people WHERE id = 1")
        assert connection.execute("SELECT catalog_person_id FROM author_watches").fetchall() == [(2,)]
        assert connection.execute("SELECT catalog_person_id FROM author_watch_items").fetchall() == [(2,)]
        # The person goes: so does everything of theirs.
        connection.execute("DELETE FROM users WHERE id = 7")
        assert connection.execute("SELECT count(*) FROM author_watches").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM author_watch_items").fetchone() == (0,)
        # The number of a watch is never given twice (the first two went with their author and their person).
        connection.execute("INSERT INTO users (id, name, display_name, whats_new_seen, role, sign_in, password_hash, "
                           "email, email_from_provider, language, created_at, failed_logins, totp_secret_enc, "
                           "totp_recovery, totp_last_step, signed_in_before, passkey_handle) VALUES (8, 'bo', 'Bo', '', "
                           "'member', 'password', 'hash', '', 0, 'de', 'x', 0, '', '', 0, 1, '')")
        connection.execute(watch.format(user=8, author=2))
        assert connection.execute("SELECT id FROM author_watches").fetchall() == [(3,)]


def test_a_database_of_version_twenty_six_gains_the_generation_of_the_check_at_zero(scratch: Path) -> None:
    """Schema 27: every download that is there starts at generation 0, so that the questions that are open are judged once
    more by the rules of today; a new one starts at 0 as well (the check writes its generation)."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v26.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO downloads (kind, title, protocol, state, created_at, updated_at, problem_code) "
                           "VALUES ('audio', 'Die Nebelbahn', 'usenet', 'problem', 'x', 'x', 'runtime')")
        connection.execute("PRAGMA user_version = 26")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        assert connection.execute("SELECT title, problem_code, check_rev FROM downloads").fetchall() == [
            ("Die Nebelbahn", "runtime", 0)]
        connection.execute("INSERT INTO downloads (kind, title, protocol, state, created_at, updated_at) "
                           "VALUES ('ebook', 'Laternenstraße', 'usenet', 'queued', 'x', 'x')")
        assert connection.execute("SELECT check_rev FROM downloads ORDER BY id DESC LIMIT 1").fetchone() == (0,)


def test_a_database_of_version_twenty_seven_keeps_its_people_and_gains_the_connections_of_e_readers(scratch: Path) -> None:
    """Schema 28: every account that is there has no Kindle address, and the table of connections starts empty and goes
    with the account that made them."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v27.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO users (name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
                           "language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
                           "email_from_provider, signed_in_before, passkey_handle) "
                           "VALUES ('ada', '', '', 'member', 'password', 'x', '', '', '2026-10-01 10:00:00', 0, '', '', 0, "
                           "0, 0, '')")
        connection.execute("PRAGMA user_version = 27")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name, kindle_address FROM users").fetchall() == [("ada", "")]
        assert connection.execute("SELECT count(*) FROM ereader_links").fetchone() == (0,)
        connection.execute("INSERT INTO ereader_links (account_id, kind, name, token_hash, prefix, created_at) "
                           "VALUES (1, 'opds', 'Reader', 'h', 'nbo_', '2026-10-01 10:00:00')")
        connection.execute("DELETE FROM users WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM ereader_links").fetchone() == (0,)


def test_a_database_of_version_twenty_eight_keeps_its_files_and_places_and_gains_the_checksum_and_the_place_of_koreader(
        scratch: Path) -> None:
    """Schema 29: a file that is there has no partial checksum yet (the background run makes it), a place that is there
    has no word of KOReader, and the checksum can be looked up by an index."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v28.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO users (name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
                           "language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
                           "email_from_provider, signed_in_before, passkey_handle, kindle_address) "
                           "VALUES ('ada', '', '', 'member', 'password', 'x', '', '', '2026-10-01 10:00:00', 0, '', '', 0, "
                           "0, 0, '', '')")
        connection.execute("INSERT INTO libraries (name, path, kind, created_at) VALUES ('B', '/books', 'both', 'x')")
        connection.execute("INSERT INTO works (title, added_at, updated_at) VALUES ('T', 'x', 'x')")
        connection.execute("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
                           "VALUES (1, 1, 'ebook', 'T', 'x', 'x')")
        connection.execute("INSERT INTO files (library_id, edition_id, role, path, size, mtime_ns, format, seen_at, "
                           "created_at) VALUES (1, 1, 'book', 'a.epub', 1, 1, 'epub', 'x', 'x')")
        connection.execute("INSERT INTO progress (user_id, edition_id, locator, moved_at, received_at) "
                           "VALUES (1, 1, 'epubcfi(/6/4)', '2026-10-01 10:00:00', '2026-10-01 10:00:00')")
        connection.execute("PRAGMA user_version = 28")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        assert connection.execute("SELECT path, partial_md5 FROM files").fetchall() == [("a.epub", "")]
        assert connection.execute("SELECT locator, koreader FROM progress").fetchall() == [("epubcfi(/6/4)", "")]
        plan = connection.execute("EXPLAIN QUERY PLAN SELECT id FROM files WHERE partial_md5 = 'x'").fetchall()
        assert any("ix_files_partial_md5" in str(row) for row in plan)


def test_a_database_of_version_twenty_nine_keeps_its_connections_and_places_and_gains_the_list_for_the_kobo(
        scratch: Path) -> None:
    """Schema 30: a connection that is there has had no answer of the sync (the number 0), a place that is there has no
    word of a Kobo, the two tables start empty, and both go with what they hang on (the person, the connection)."""
    with closing(sqlite3.connect(scratch)) as connection:
        connection.executescript((RECORDS / "v29.sql").read_text(encoding="utf-8"))
        connection.execute("INSERT INTO users (name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
                           "language, created_at, failed_logins, totp_secret_enc, totp_recovery, totp_last_step, "
                           "email_from_provider, signed_in_before, passkey_handle, kindle_address) "
                           "VALUES ('ada', '', '', 'member', 'password', 'x', '', '', '2026-10-01 10:00:00', 0, '', '', 0, "
                           "0, 0, '', '')")
        connection.execute("INSERT INTO ereader_links (account_id, kind, name, token_hash, prefix, created_at) "
                           "VALUES (1, 'opds', 'Reader', 'h', 'nbo_', '2026-10-01 10:00:00')")
        connection.execute("INSERT INTO libraries (name, path, kind, created_at) VALUES ('B', '/books', 'both', 'x')")
        connection.execute("INSERT INTO works (title, added_at, updated_at) VALUES ('T', 'x', 'x')")
        connection.execute("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
                           "VALUES (1, 1, 'ebook', 'T', 'x', 'x')")
        connection.execute("INSERT INTO progress (user_id, edition_id, locator, moved_at, received_at) "
                           "VALUES (1, 1, 'epubcfi(/6/4)', '2026-10-01 10:00:00', '2026-10-01 10:00:00')")
        connection.execute("PRAGMA user_version = 29")
        connection.commit()
    database.init_db()
    assert version_of(scratch) == database.SCHEMA_VERSION == 31
    assert made == ["before schema 31"]
    with closing(sqlite3.connect(scratch)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT name, sync_batch FROM ereader_links").fetchall() == [("Reader", 0)]
        assert connection.execute("SELECT locator, kobo FROM progress").fetchall() == [("epubcfi(/6/4)", "")]
        assert connection.execute("SELECT count(*) FROM kobo_listed").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM kobo_held").fetchone() == (0,)
        connection.execute("INSERT INTO kobo_listed (user_id, edition_id, uuid, added_at) VALUES (1, 1, 'u', 'x')")
        connection.execute("INSERT INTO kobo_held (link_id, edition_id, uuid) VALUES (1, 1, 'u')")
        assert connection.execute("SELECT stamp, state_stamp, next_stamp, removing, batch, archived FROM kobo_held"
                                  ).fetchall() == [("", "", None, 0, 0, 0)]
        # A name is one book to a person, and to a connection.
        for statement in ("INSERT INTO kobo_listed (user_id, edition_id, uuid, added_at) VALUES (1, 2, 'u', 'x')",
                          "INSERT INTO kobo_held (link_id, edition_id, uuid) VALUES (1, 2, 'u')"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
        connection.execute("DELETE FROM ereader_links WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM kobo_held").fetchone() == (0,)
        connection.execute("DELETE FROM users WHERE id = 1")
        assert connection.execute("SELECT count(*) FROM kobo_listed").fetchone() == (0,)
