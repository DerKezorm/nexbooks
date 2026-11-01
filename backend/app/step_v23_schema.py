"""The step from schema 22 to schema 23: where a device came from. A device the iPhone app
connected (it confirmed the code of a television) remembers the device that did (``devices.via_device_id``), and so
does the request on its way (``app_requests.confirmed_by``); revoking the phone then takes the television along, and a
device connected that way connects no other.

The two tables are made again rather than given a column with ``ALTER TABLE``, so that a database brought up holds word
for word what a new one holds (an added column would be written at the end of the old statement, after its keys). The
way is the one of schema 20 (``step_v20_schema``): the foreign keys off for the step, the old table moved aside without
the references of the other tables moving with it, every row copied with its number, the indexes made again, the rows
counted and the foreign keys checked before the step may commit. No trigger hangs on these tables. The devices
and requests there are were all made in a browser or with a code of the browser, so the new columns stay empty.

Written out as the tables stood then (``tests/schema/v23.sql``), not taken from the models, which move on."""

from __future__ import annotations

from typing import Any

# ruff: noqa: E501

#: The tables made again, in this order, each with its indexes.
V23_TABLES: dict[str, tuple[str, list[str]]] = {
    "devices": (
        "CREATE TABLE devices ( id INTEGER NOT NULL, uid VARCHAR(32) NOT NULL, account_id INTEGER NOT NULL, name VARCHAR(80) NOT NULL, token_hash VARCHAR(64) NOT NULL, prefix VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, last_used_at DATETIME, revoked_at DATETIME, via_device_id INTEGER, PRIMARY KEY (id), FOREIGN KEY(account_id) REFERENCES users (id) ON DELETE CASCADE, UNIQUE (token_hash), FOREIGN KEY(via_device_id) REFERENCES devices (id) ON DELETE SET NULL )",
        [
            "CREATE INDEX ix_devices_account_id ON devices (account_id)",
            "CREATE INDEX ix_devices_via ON devices (via_device_id)",
            "CREATE UNIQUE INDEX uq_devices_uid ON devices (uid)",
        ],
    ),
    "app_requests": (
        "CREATE TABLE app_requests ( id VARCHAR(32) NOT NULL, secret_hash VARCHAR(64) NOT NULL, code_hash VARCHAR(64) NOT NULL, device_name VARCHAR(80) NOT NULL, state VARCHAR(10) NOT NULL, sender VARCHAR(64) NOT NULL, proxy VARCHAR(64), account_id INTEGER, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, confirmed_at DATETIME, used_at DATETIME, device_id INTEGER, confirmed_by INTEGER, PRIMARY KEY (id), FOREIGN KEY(account_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE SET NULL, FOREIGN KEY(confirmed_by) REFERENCES devices (id) ON DELETE SET NULL )",
        [
            "CREATE INDEX ix_app_requests_account ON app_requests (account_id)",
            "CREATE INDEX ix_app_requests_sender ON app_requests (sender, state)",
            "CREATE UNIQUE INDEX uq_app_requests_code ON app_requests (code_hash)",
        ],
    ),
}
#: The columns that are new: they are not copied, they start empty.
V23_NEW = {"devices": ["via_device_id"], "app_requests": ["confirmed_by"]}


class RebuildFailed(RuntimeError):
    """The tables made again do not hold what the old ones held; the step is rolled back."""


def _rows(connection: Any, table: str) -> int:
    # Names of this module's tables only, never from outside.
    return int(connection.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar() or 0)  # noqa: S608


def _columns(connection: Any, table: str) -> list[str]:
    return [str(row[1]) for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')]


def rebuild(connection: Any) -> None:
    """Runs inside the transaction of the step, with the foreign keys off (``db._take_step`` sees to that). Raises
    ``RebuildFailed`` when anything does not add up; the transaction is then rolled back and the database stays as it
    was."""
    run = connection.exec_driver_sql
    if int(run("PRAGMA foreign_keys").scalar() or 0):
        raise RebuildFailed("the foreign keys must be off while tables are made again")
    if run("SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name IN ('devices', 'app_requests')"
           ).scalar():
        raise RebuildFailed("a trigger hangs on a table this step makes again")
    broken_before = {tuple(row) for row in run("PRAGMA foreign_key_check")}
    counts = {name: _rows(connection, name) for name in V23_TABLES}
    # The old table moves out of the way without the references of the other tables moving with it: they keep naming
    # ``devices``, which is the new table a moment later.
    run("PRAGMA legacy_alter_table = ON")
    try:
        for name, (create, indexes) in V23_TABLES.items():
            old = f"_{name}_v22"
            run(f'ALTER TABLE "{name}" RENAME TO "{old}"')
            run(create)
            before, after = _columns(connection, old), _columns(connection, name)
            if after != before + V23_NEW[name]:
                raise RebuildFailed(f"the columns of {name} are not the ones of schema 22")
            # A table without a number of its own keeps its row numbers too (what the check of the keys names).
            columns = ", ".join(([] if name == "devices" else ["rowid"]) + [f'"{c}"' for c in before])
            run(f'INSERT INTO "{name}" ({columns}) SELECT {columns} FROM "{old}"')  # noqa: S608 - our names
            run(f'DROP TABLE "{old}"')
            for statement in indexes:
                run(statement)
    finally:
        run("PRAGMA legacy_alter_table = OFF")
    for name, count in counts.items():
        if _rows(connection, name) != count:
            raise RebuildFailed(f"{name} lost rows")
    broken = {tuple(row) for row in run("PRAGMA foreign_key_check")} - broken_before
    if broken:
        raise RebuildFailed(f"{len(broken)} rows point at nothing after the tables were made again")
