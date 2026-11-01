"""The step from schema 8 to schema 9 (pen names): the other names of a person, the evidence they rest on, and where
an edition went that became part of another.

Written out as the tables stood then (``tests/schema/v9.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V9_STATEMENTS = [
    "CREATE TABLE person_aliases ( id INTEGER NOT NULL, person_id INTEGER NOT NULL, name VARCHAR(200) NOT NULL, key VARCHAR(200) NOT NULL, source VARCHAR(16) DEFAULT '' NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE, UNIQUE (key) )",
    "CREATE INDEX ix_person_aliases_person_id ON person_aliases (person_id)",
    "CREATE TABLE alias_claims ( id INTEGER NOT NULL, name_a VARCHAR(200) NOT NULL, key_a VARCHAR(200) NOT NULL, name_b VARCHAR(200) NOT NULL, key_b VARCHAR(200) NOT NULL, source VARCHAR(16) DEFAULT '' NOT NULL, edition_id INTEGER, apart BOOLEAN DEFAULT 0 NOT NULL, created_by INTEGER, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE, FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL )",
    "CREATE INDEX ix_alias_claims_edition_id ON alias_claims (edition_id)",
    "CREATE TABLE edition_moves ( old_id INTEGER NOT NULL, new_id INTEGER NOT NULL, shift_ms INTEGER DEFAULT 0 NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (old_id), FOREIGN KEY(new_id) REFERENCES editions (id) ON DELETE CASCADE )",
    "CREATE INDEX ix_edition_moves_new_id ON edition_moves (new_id)",
]
