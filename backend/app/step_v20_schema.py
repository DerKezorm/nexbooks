"""The step from schema 19 to schema 20: numbers that are never given twice, and what a device
was given, kept per device.

**A number is never given twice.** ``works``, ``editions`` and ``files`` had no ``AUTOINCREMENT``: when the newest row was
deleted, the next one got its number. An app that held the old book then saw a "changed title" where another book had
come, and kept files, places and bookmarks under a number that now meant something else. SQLite cannot add
``AUTOINCREMENT`` to a table that is there, so the three tables are made again (the way the SQLite documentation
describes, "Making Other Kinds Of Table Schema Changes"): with the foreign keys off for the step (else dropping the old
table would delete everything that hangs on it), the triggers and views put aside and made again exactly as they were,
every row copied with its number, the indexes made again, and the foreign keys checked before the step may commit.
The counter of each table starts above every number that was ever handed out and still known somewhere (works that
went, works a device was given, editions that went into another one).

**What a device was given** (``app_seen``) is kept per device instead of per person: the rows of a person go to each of
their devices that is not revoked.

Written out as the tables stood then (``tests/schema/v20.sql``), not taken from the models, which move on."""

from __future__ import annotations

from typing import Any

# ruff: noqa: E501

#: The tables made again, in this order (a table before the ones that point at it), each with its indexes.
V20_TABLES: dict[str, tuple[str, list[str]]] = {
    'works': (
        'CREATE TABLE works ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, title VARCHAR(300) NOT NULL, original_title VARCHAR(300) DEFAULT \'\' NOT NULL, original_language VARCHAR(8) DEFAULT \'\' NOT NULL, description TEXT DEFAULT \'\' NOT NULL, year INTEGER, genre VARCHAR(16) DEFAULT \'\' NOT NULL, color VARCHAR(7) DEFAULT \'\' NOT NULL, cover VARCHAR(64) DEFAULT \'\' NOT NULL, series_id INTEGER, series_pos FLOAT, pages INTEGER DEFAULT 0 NOT NULL, pattern INTEGER DEFAULT 0 NOT NULL, tall FLOAT DEFAULT (0.5) NOT NULL, gold BOOLEAN DEFAULT 0 NOT NULL, "key" VARCHAR(300) DEFAULT \'\' NOT NULL, main_key VARCHAR(300) DEFAULT \'\' NOT NULL, original_key VARCHAR(300) DEFAULT \'\' NOT NULL, added_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, change_seq INTEGER DEFAULT 0 NOT NULL, FOREIGN KEY(series_id) REFERENCES series (id) ON DELETE SET NULL )',
        [
            'CREATE INDEX ix_works_added_at ON works (added_at)',
            'CREATE INDEX ix_works_change_seq ON works (change_seq)',
            'CREATE INDEX ix_works_key ON works ("key")',
            'CREATE INDEX ix_works_main_key ON works (main_key)',
            'CREATE INDEX ix_works_original_key ON works (original_key)',
            'CREATE INDEX ix_works_series_id ON works (series_id)',
        ],
    ),
    'editions': (
        "CREATE TABLE editions ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, work_id INTEGER NOT NULL, library_id INTEGER NOT NULL, kind VARCHAR(8) NOT NULL, variant VARCHAR(12) DEFAULT '' NOT NULL, variant_uncertain BOOLEAN DEFAULT 0 NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, title VARCHAR(300) NOT NULL, subtitle VARCHAR(300) DEFAULT '' NOT NULL, isbn VARCHAR(20) DEFAULT '' NOT NULL, asin VARCHAR(20) DEFAULT '' NOT NULL, publisher VARCHAR(200) DEFAULT '' NOT NULL, published VARCHAR(10) DEFAULT '' NOT NULL, description TEXT DEFAULT '' NOT NULL, genre VARCHAR(16) DEFAULT '' NOT NULL, series_name VARCHAR(200) DEFAULT '' NOT NULL, series_id INTEGER, series_pos FLOAT, original_title VARCHAR(300) DEFAULT '' NOT NULL, title_key VARCHAR(300) DEFAULT '' NOT NULL, shelf_key VARCHAR(500) DEFAULT '' NOT NULL, pages INTEGER DEFAULT 0 NOT NULL, duration INTEGER DEFAULT 0 NOT NULL, cover VARCHAR(64) DEFAULT '' NOT NULL, color VARCHAR(7) DEFAULT '' NOT NULL, missing_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, original_language VARCHAR(8) DEFAULT '' NOT NULL, overlay JSON, grouped_by VARCHAR(8) DEFAULT '' NOT NULL, genre_guess BOOLEAN DEFAULT 0 NOT NULL, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE, FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(series_id) REFERENCES series (id) ON DELETE SET NULL )",
        [
            'CREATE INDEX ix_editions_isbn ON editions (isbn)',
            'CREATE INDEX ix_editions_library_id ON editions (library_id)',
            'CREATE INDEX ix_editions_series_id ON editions (series_id)',
            'CREATE INDEX ix_editions_work_id ON editions (work_id)',
        ],
    ),
    'files': (
        "CREATE TABLE files ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, library_id INTEGER NOT NULL, edition_id INTEGER, role VARCHAR(8) NOT NULL, path VARCHAR(1500) NOT NULL, size BIGINT NOT NULL, mtime_ns BIGINT NOT NULL, sha256 VARCHAR(64) DEFAULT '' NOT NULL, format VARCHAR(8) NOT NULL, part INTEGER DEFAULT 1 NOT NULL, duration_ms INTEGER DEFAULT 0 NOT NULL, unit VARCHAR(1500) DEFAULT '' NOT NULL, probe JSON, dirty BOOLEAN DEFAULT 0 NOT NULL, missing_at DATETIME, seen_at DATETIME NOT NULL, created_at DATETIME NOT NULL, rev INTEGER DEFAULT 0 NOT NULL, UNIQUE (library_id, path), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
        [
            'CREATE INDEX ix_files_dirty ON files (library_id, dirty)',
            'CREATE INDEX ix_files_edition_id ON files (edition_id)',
            'CREATE INDEX ix_files_library_id ON files (library_id)',
            'CREATE INDEX ix_files_unit ON files (library_id, unit)',
        ],
    ),
    'app_seen': (
        'CREATE TABLE app_seen ( device_id INTEGER NOT NULL, work_id INTEGER NOT NULL, PRIMARY KEY (device_id, work_id), FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE )',
        [
        ],
    ),
}

#: Where the rows of ``app_seen`` go: each row of a person to every device of theirs that is not revoked.
V20_SEEN_COPY = (
    'INSERT INTO app_seen (device_id, work_id) SELECT d.id, s.work_id FROM "_app_seen_v19" s '
    "JOIN devices d ON d.account_id = s.user_id AND d.revoked_at IS NULL"
)

#: The highest number of each table that was handed out and is still known somewhere: the counter starts above it.
V20_HIGHEST = {
    "works": "SELECT max(coalesce((SELECT max(id) FROM works), 0), coalesce((SELECT max(work_id) FROM app_gone), 0), "
             "coalesce((SELECT max(work_id) FROM app_seen), 0))",
    "editions": "SELECT max(coalesce((SELECT max(id) FROM editions), 0), "
                "coalesce((SELECT max(old_id) FROM edition_moves), 0))",
    "files": "SELECT coalesce((SELECT max(id) FROM files), 0)",
}


class RebuildFailed(RuntimeError):
    """The tables made again do not hold what the old ones held; the step is rolled back."""


def _rows(connection: Any, table: str) -> int:
    # Names of this module's tables only, never from outside.
    return int(connection.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar() or 0)  # noqa: S608


def _columns(connection: Any, table: str) -> list[str]:
    return [str(row[1]) for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')]


def rebuild(connection: Any) -> None:
    """Runs inside the transaction of the step, with the foreign keys off (``db._init`` sees to that: a pragma that
    cannot change inside a transaction). Raises ``RebuildFailed`` when anything does not add up; the transaction is then
    rolled back and the database stays as it was."""
    run = connection.exec_driver_sql
    if int(run("PRAGMA foreign_keys").scalar() or 0):
        raise RebuildFailed("the foreign keys must be off while tables are made again")
    # Whatever was wrong before (by hand, by an old bug) is not this step's doing and does not stop the start; only
    # what is new is.
    broken_before = {tuple(row) for row in run("PRAGMA foreign_key_check")}
    highest = {name: int(run(query).scalar() or 0) for name, query in V20_HIGHEST.items()}
    counts = {name: _rows(connection, name) for name in V20_TABLES}
    # The triggers and views name the tables: they are put aside before the tables move and made again afterwards,
    # exactly as they were (the triggers of the order of changes must not run while the rows are copied, either).
    kept = [(str(kind), str(name), str(sql)) for kind, name, sql in run(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL ORDER BY rowid")]
    for kind, name, _sql in kept:
        run(f'DROP {"TRIGGER" if kind == "trigger" else "VIEW"} "{name}"')
    # The old table moves out of the way without the references of the other tables moving with it: they keep naming
    # ``works``, which is the new table a moment later.
    run("PRAGMA legacy_alter_table = ON")
    try:
        for name, (create, indexes) in V20_TABLES.items():
            old = f"_{name}_v19"
            run(f'ALTER TABLE "{name}" RENAME TO "{old}"')
            run(create)
            if name == "app_seen":
                run(V20_SEEN_COPY)
            else:
                columns = ", ".join(f'"{column}"' for column in _columns(connection, name))
                if _columns(connection, name) != _columns(connection, old):
                    raise RebuildFailed(f"the columns of {name} are not the ones of schema 19")
                run(f'INSERT INTO "{name}" ({columns}) SELECT {columns} FROM "{old}"')  # noqa: S608 - our names
            run(f'DROP TABLE "{old}"')
            for statement in indexes:
                run(statement)
    finally:
        run("PRAGMA legacy_alter_table = OFF")
    for name, top in highest.items():
        if not run("SELECT 1 FROM sqlite_sequence WHERE name = ?", (name,)).first():
            run("INSERT INTO sqlite_sequence (name, seq) VALUES (?, 0)", (name,))
        run("UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = ?", (top, name))
    for _kind, _name, sql in kept:
        run(sql)
    for name in ("works", "editions", "files"):
        if _rows(connection, name) != counts[name]:
            raise RebuildFailed(f"{name} lost rows")
    made = {str(row[0]) for row in run("SELECT name FROM sqlite_master WHERE type IN ('trigger', 'view')")}
    if made != {name for _kind, name, _sql in kept}:
        raise RebuildFailed("a trigger or a view did not come back")
    broken = {tuple(row) for row in run("PRAGMA foreign_key_check")} - broken_before
    if broken:
        raise RebuildFailed(f"{len(broken)} rows point at nothing after the tables were made again")
