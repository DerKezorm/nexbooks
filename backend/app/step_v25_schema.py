"""The step from schema 24 to schema 25 (the wrong book): what people had in an edition that
was sent away is kept with the trash entry of its files.

Written out as the table stood then (``tests/schema/v25.sql``), not taken from the models, which move on. Nothing of it
existed before, so the step only makes the table."""

from __future__ import annotations

# ruff: noqa: E501

V25_TABLES = [
    "CREATE TABLE kept_states ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, trash_id INTEGER, paths JSON, states JSON, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(trash_id) REFERENCES trash_entries (id) ON DELETE CASCADE )",
]

V25_INDEXES = [
    "CREATE INDEX ix_kept_states_library ON kept_states (library_id)",
]

V25_STATEMENTS = [*V25_TABLES, *V25_INDEXES]
