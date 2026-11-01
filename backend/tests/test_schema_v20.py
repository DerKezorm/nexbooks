"""Schema 20: ``works``, ``editions`` and ``files`` are made again with
``AUTOINCREMENT``, so that a number is never given twice, and ``app_seen`` is kept per device.

The step makes the central tables of everybody's library again. So it is held to more than the columns the other steps
are held to: every row with its number, every foreign key, index, trigger, the full-text table and the views exactly as
in a new database, ``integrity_check`` and ``foreign_key_check``, the counters above every number ever handed out, and a
step that breaks off leaves the database as it was."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app import db as database
from app import step_v20_schema
from app.models import Base
from app.services import backups

RECORDS = Path(__file__).parent / "schema"
#: The tables whose rows must come through with their numbers, and how to read them in a fixed order.
KEPT = {
    "works": "SELECT * FROM works ORDER BY id",
    "editions": "SELECT * FROM editions ORDER BY id",
    # The columns of schema 19: step 29 adds one (empty for the rows that were there).
    "files": "SELECT id, library_id, edition_id, role, path, size, mtime_ns, sha256, format, part, duration_ms, unit, probe, "
             "dirty, missing_at, seen_at, created_at, rev FROM files ORDER BY id",
    "chapters": "SELECT * FROM chapters ORDER BY id",
    "credits": "SELECT * FROM credits ORDER BY id",
    # The columns of schema 19: step 24 adds one to each of these two (empty for the rows that were there).
    "progress": "SELECT user_id, edition_id, locator, fraction, unit, chapter, chapter_count, chapter_title, device_key, "
                "device, moved_at, received_at, pass_done FROM progress ORDER BY user_id, edition_id",
    "bookmarks": "SELECT * FROM bookmarks ORDER BY id",
    "history": "SELECT id, user_id, edition_id, kind, finished_on, source, created_at FROM history ORDER BY id",
    "edition_moves": "SELECT * FROM edition_moves ORDER BY old_id",
    "app_gone": "SELECT * FROM app_gone ORDER BY work_id",
    "app_changes": "SELECT * FROM app_changes",
}


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, list[str]]]:
    path = tmp_path / "nexbooks.db"
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    made: list[str] = []
    monkeypatch.setattr(backups, "create", lambda **kwargs: made.append(kwargs.get("note", "")) or path)
    yield path, made
    engine.dispose()


def _squeezed(sql: str) -> str:
    return " ".join(sql.split())


def everything(connection: sqlite3.Connection) -> set[tuple[str, str, str, str]]:
    """Every table, index, trigger, view and virtual table with its statement, as SQLite keeps it."""
    return {(kind, name, table, _squeezed(sql)) for kind, name, table, sql in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}


def fresh(tmp_path: Path) -> set[tuple[str, str, str, str]]:
    path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with closing(sqlite3.connect(path)) as connection:
        return everything(connection)


def a_library_of_schema_19(path: Path) -> None:
    """Two people, three devices (one revoked), a library with four works and their editions, files, chapters, credits,
    places, marks and finished books; a fifth work that went (the newest, so its number is the one that would come
    again), an edition that went into another one, and the works the devices were given."""
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript((RECORDS / "v19.sql").read_text(encoding="utf-8"))
        run = connection.execute
        for ident, name in ((7, "ada"), (8, "ben")):
            run("INSERT INTO users (id, name, display_name, whats_new_seen, role, sign_in, password_hash, email, "
                "email_from_provider, language, created_at, failed_logins, totp_secret_enc, totp_recovery, "
                "totp_last_step, signed_in_before, passkey_handle) VALUES (?, ?, ?, '', 'member', 'password', 'hash', "
                "'', 0, 'en', '2026-10-01 10:00:00.000000', 0, '', '', 0, 1, '')", (ident, name, name.title()))
        for ident, account, revoked in ((1, 7, None), (2, 7, "2026-10-05 10:00:00.000000"), (3, 8, None)):
            run("INSERT INTO devices (id, uid, account_id, name, token_hash, prefix, created_at, revoked_at) "
                "VALUES (?, ?, ?, 'Phone', ?, 'nbd_', 'x', ?)", (ident, f"u{ident}", account, f"h{ident}", revoked))
        run("INSERT INTO libraries (id, name, path, kind, created_at) VALUES (1, 'Books', '/books', 'both', 'x')")
        run("INSERT INTO library_members (library_id, user_id, \"right\") VALUES (1, 7, 'read'), (1, 8, 'manage')")
        run("INSERT INTO people (id, name, sort_name, key) VALUES (3, 'Mara Lindqvist', 'Lindqvist, Mara', 'mara')")
        for ident in range(1, 6):
            run("INSERT INTO works (id, title, added_at, updated_at) VALUES (?, ?, 'x', 'x')", (ident, f"Work {ident}"))
            run("INSERT INTO editions (id, work_id, library_id, kind, title, duration, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?, 600, 'x', 'x')", (ident, ident, "audio" if ident == 2 else "ebook", f"Work {ident}"))
            run("INSERT INTO files (id, library_id, edition_id, role, path, size, mtime_ns, sha256, format, seen_at, "
                "created_at) VALUES (?, 1, ?, 'book', ?, 10, 1, ?, 'epub', 'x', 'x')",
                (ident * 10, ident, f"a/{ident}.epub", f"{ident:064d}"))
            run("INSERT INTO credits (person_id, work_id, role, position) VALUES (3, ?, 'author', 0)", (ident,))
            run("INSERT INTO book_fts (rowid, title, original, people, series, description, ids) "
                "VALUES (?, ?, '', 'mara', '', '', '')", (ident, f"orchard{ident}"))
        run("INSERT INTO files (id, library_id, edition_id, role, path, size, mtime_ns, format, seen_at, created_at) "
            "VALUES (51, 1, NULL, 'sidecar', 'a/cover.jpg', 3, 1, 'cover', 'x', 'x')")
        run("INSERT INTO chapters (edition_id, number, title, start_ms, target, depth) VALUES (2, 1, 'Gate', 0, '', 0), "
            "(2, 2, 'Wall', 240500, '', 0)")
        run("INSERT INTO progress (user_id, edition_id, locator, fraction, unit, moved_at, received_at) "
            "VALUES (7, 2, 'time:12.500', 0.02, 'time', '2026-10-02 10:00:00.000000', '2026-10-02 10:00:00.000000')")
        run("INSERT INTO bookmarks (user_id, edition_id, locator, fraction, chapter_title, created_at, note) "
            "VALUES (7, 1, 'epubcfi(/6/4)', 0.1, 'One', 'x', 'here')")
        run("INSERT INTO history (user_id, edition_id, kind, finished_on, source, created_at) "
            "VALUES (8, 3, 'read', '2026-10-03', 'hand', 'x')")
        run("INSERT INTO edition_moves (old_id, new_id, shift_ms, created_at) VALUES (9, 1, 0, 'x')")
        run("INSERT INTO app_seen (user_id, work_id) VALUES (7, 1), (7, 5), (8, 2)")
        connection.commit()
        run("PRAGMA foreign_keys = ON")
        # The newest works go (their trigger keeps them in ``app_gone``), with their editions and files; the very newest
        # was never given to a device, so only ``app_gone`` knows its number.
        run("INSERT INTO works (id, title, added_at, updated_at) VALUES (6, 'Work 6', 'x', 'x')")
        run("DELETE FROM works WHERE id IN (5, 6)")
        run("PRAGMA user_version = 19")
        connection.commit()


def rows(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {name: connection.execute(query).fetchall() for name, query in KEPT.items()}


def test_the_step_keeps_every_row_with_its_number_and_makes_what_a_new_database_has(
        scratch: tuple[Path, list[str]], tmp_path: Path) -> None:
    path, made = scratch
    a_library_of_schema_19(path)
    with closing(sqlite3.connect(path)) as connection:
        before = rows(connection)
        fts_before = connection.execute("SELECT rowid FROM book_fts WHERE book_fts MATCH 'mara' ORDER BY rowid").fetchall()
    assert before["works"] and [row[0] for row in before["app_gone"]] == [5, 6]
    database.init_db()
    assert made == [f"before schema {database.SCHEMA_VERSION}"], "a backup first"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (database.SCHEMA_VERSION,)
        assert rows(connection) == before
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        # Tables, indexes, the 22 triggers, the full-text table: word for word what a new database holds.
        assert everything(connection) == fresh(tmp_path)
        triggers = connection.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
        assert triggers == 22
        assert database._foreign_objects(connection) == []
        assert connection.execute("SELECT rowid FROM book_fts WHERE book_fts MATCH 'mara' ORDER BY rowid").fetchall() \
            == fts_before
        # What the devices were given: the rows of a person on each of their devices that is not revoked.
        assert connection.execute("SELECT device_id, work_id FROM app_seen ORDER BY 1, 2").fetchall() == [
            (1, 1), (1, 5), (3, 2)]
        # The counters start above every number handed out: the work that went, the edition that went into another.
        counters = dict(connection.execute("SELECT name, seq FROM sqlite_sequence"))
        assert counters == {"works": 6, "editions": 9, "files": 51}


def test_after_the_step_a_number_is_never_given_twice_and_the_triggers_and_keys_work(
        scratch: tuple[Path, list[str]]) -> None:
    path, _made = scratch
    a_library_of_schema_19(path)
    database.init_db()
    with closing(sqlite3.connect(path)) as connection:
        run = connection.execute
        run("PRAGMA foreign_keys = ON")
        # The newest work goes, a new one comes: a new number, not one of the works that went (5, 6), nor 4.
        run("DELETE FROM works WHERE id = 4")
        assert run("SELECT count(*) FROM editions WHERE work_id = 4").fetchone() == (0,), "the cascade still works"
        assert run("SELECT count(*) FROM files WHERE edition_id = 4").fetchone() == (0,)
        assert run("SELECT count(*) FROM credits WHERE work_id = 4").fetchone() == (0,)
        assert run("SELECT work_id FROM app_gone ORDER BY work_id").fetchall() == [(4,), (5,), (6,)]
        new_work = run("INSERT INTO works (title, added_at, updated_at) VALUES ('New', 'x', 'x')").lastrowid
        assert new_work == 7
        new_edition = run("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
                          "VALUES (7, 1, 'ebook', 'New', 'x', 'x')").lastrowid
        assert new_edition == 10, "above the edition that went into another one (9)"
        new_file = run("INSERT INTO files (library_id, edition_id, role, path, size, mtime_ns, format, seen_at, "
                       "created_at) VALUES (1, 10, 'book', 'n.epub', 1, 1, 'epub', 'x', 'x')").lastrowid
        assert new_file == 52
        # The order of changes still moves: the new work got a number, a title changed gets the next.
        seq = run("SELECT change_seq FROM works WHERE id = 7").fetchone()[0]
        run("UPDATE works SET title = 'Work 1 again' WHERE id = 1")
        assert run("SELECT change_seq FROM works WHERE id = 1").fetchone()[0] > seq
        # The full-text table loses an edition that goes (its trigger came back).
        run("DELETE FROM editions WHERE id = 3")
        assert run("SELECT rowid FROM book_fts WHERE book_fts MATCH 'orchard3'").fetchall() == []
        # A foreign key that points at the new tables still holds.
        with pytest.raises(sqlite3.IntegrityError):
            run("INSERT INTO chapters (edition_id, number, title, start_ms, target, depth) VALUES (99, 1, 'x', 0, '', 0)")
        with pytest.raises(sqlite3.IntegrityError):
            run("INSERT INTO app_seen (device_id, work_id) VALUES (99, 1)")
        # A device that is deleted takes what it was given with it.
        run("DELETE FROM devices WHERE id = 1")
        assert run("SELECT device_id FROM app_seen").fetchall() == [(3,)]
        connection.commit()


def test_a_step_that_breaks_off_leaves_the_database_as_it_was(scratch: tuple[Path, list[str]],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    path, _made = scratch
    a_library_of_schema_19(path)
    with closing(sqlite3.connect(path)) as connection:
        before, schema = rows(connection), everything(connection)
    # The last table fails to fill: the three central ones are made again by then, and must come back as they were.
    monkeypatch.setattr(step_v20_schema, "V20_SEEN_COPY", "INSERT INTO app_seen (device_id, work_id) SELECT nothing")
    with pytest.raises(Exception, match="nothing"):
        database.init_db()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (19,)
        assert rows(connection) == before
        assert everything(connection) == schema
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_v19'").fetchall() == []
    monkeypatch.undo()


def test_the_step_refuses_to_run_with_the_foreign_keys_on(tmp_path: Path) -> None:
    """Dropping the old table with the foreign keys on would delete every edition, file and place that hangs on it."""
    path = tmp_path / "old.db"
    a_library_of_schema_19(path)
    engine = database._schema_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.begin() as connection, pytest.raises(step_v20_schema.RebuildFailed, match="foreign keys"):
            step_v20_schema.rebuild(connection)
    finally:
        engine.dispose()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM editions").fetchone() == (4,)


def test_what_was_broken_before_does_not_stop_the_start_and_is_not_hidden(scratch: tuple[Path, list[str]]) -> None:
    """A mark that points at an edition nobody has (by hand, by an old bug) stays as it was; only what the step itself
    would break stops it."""
    path, _made = scratch
    a_library_of_schema_19(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO bookmarks (user_id, edition_id, locator, fraction, chapter_title, created_at, "
                           "note) VALUES (7, 77, 'epubcfi(/6/8)', 0.2, '', 'x', '')")
        connection.commit()
    database.init_db()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (database.SCHEMA_VERSION,)
        assert [row[0] for row in connection.execute("PRAGMA foreign_key_check")] == ["bookmarks"]


def test_a_rebuild_that_loses_rows_is_rolled_back(scratch: tuple[Path, list[str]],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The step counts the rows of the three tables before and after: a copy that dropped some does not commit."""
    path, _made = scratch
    a_library_of_schema_19(path)
    tables = dict(step_v20_schema.V20_TABLES)
    create, indexes = tables["files"]
    # A table that silently keeps only some rows: a unique constraint on a column that is not unique, ignored on conflict.
    tables["files"] = (create.replace("UNIQUE (library_id, path)", "UNIQUE (library_id) ON CONFLICT IGNORE"), indexes)
    monkeypatch.setattr(step_v20_schema, "V20_TABLES", tables)
    with pytest.raises(step_v20_schema.RebuildFailed, match="files lost rows"):
        database.init_db()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (19,)
        assert connection.execute("SELECT count(*) FROM files").fetchone() == (5,)
