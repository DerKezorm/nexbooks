"""The step from schema 15 to schema 16: an edition that goes into another library, with the
journal of its files.

Written out as the tables stood then (``tests/schema/v16.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V16_TABLES = [
    "CREATE TABLE relocations ( id INTEGER NOT NULL, edition_id INTEGER, source_id INTEGER, target_id INTEGER, created_by INTEGER, state VARCHAR(8) NOT NULL, step VARCHAR(8) NOT NULL, error VARCHAR(40) DEFAULT '' NOT NULL, made JSON, created_at DATETIME NOT NULL, started_at DATETIME, finished_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE SET NULL, FOREIGN KEY(source_id) REFERENCES libraries (id) ON DELETE SET NULL, FOREIGN KEY(target_id) REFERENCES libraries (id) ON DELETE SET NULL, FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL )",
    "CREATE TABLE relocation_files ( id INTEGER NOT NULL, relocation_id INTEGER NOT NULL, file_id INTEGER, source VARCHAR(1500) NOT NULL, target VARCHAR(1500) NOT NULL, size BIGINT NOT NULL, mtime_ns BIGINT NOT NULL, state VARCHAR(8) NOT NULL, how VARCHAR(8) DEFAULT '' NOT NULL, PRIMARY KEY (id), FOREIGN KEY(relocation_id) REFERENCES relocations (id) ON DELETE CASCADE, FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL )",
]

V16_INDEXES = [
    "CREATE INDEX ix_relocations_edition ON relocations (edition_id, id)",
    "CREATE INDEX ix_relocations_state ON relocations (state)",
    "CREATE INDEX ix_relocation_files_move ON relocation_files (relocation_id, id)",
    "CREATE UNIQUE INDEX uq_relocations_active ON relocations (edition_id) WHERE state IN ('queued', 'running')",
]

V16_STATEMENTS = [*V16_TABLES, *V16_INDEXES]
