"""The step from schema 21 to schema 22: the authors the sources know,
their works, and what people moved to another kind.

Written out as the tables stood then (``tests/schema/v22.sql``), not taken from the models, which move on. Nothing of
it existed before, so the step only makes the tables."""

from __future__ import annotations

# ruff: noqa: E501

V22_TABLES = [
    "CREATE TABLE catalog_people ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, token VARCHAR(24) NOT NULL, name VARCHAR(200) NOT NULL, \"key\" VARCHAR(200) NOT NULL, person_id INTEGER, aliases JSON, orig_language VARCHAR(8) DEFAULT '' NOT NULL, languages JSON, sources JSON, born VARCHAR(40) DEFAULT '' NOT NULL, died VARCHAR(40) DEFAULT '' NOT NULL, state VARCHAR(10) DEFAULT '' NOT NULL, built_at DATETIME, wanted_at DATETIME, created_at DATETIME NOT NULL, info JSON, UNIQUE (token), UNIQUE (\"key\"), FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE SET NULL )",
    "CREATE TABLE catalog_works ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, catalog_person_id INTEGER NOT NULL, \"key\" VARCHAR(64) NOT NULL, foreign_id INTEGER, kind VARCHAR(8) NOT NULL, why VARCHAR(16) DEFAULT '' NOT NULL, pen VARCHAR(200) DEFAULT '' NOT NULL, series VARCHAR(200) DEFAULT '' NOT NULL, series_pos FLOAT, year INTEGER, orig_language VARCHAR(8) DEFAULT '' NOT NULL, titles JSON, sources JSON, isbns JSON, refs JSON, pages INTEGER, minutes INTEGER, weight INTEGER DEFAULT 0 NOT NULL, UNIQUE (catalog_person_id, \"key\"), FOREIGN KEY(catalog_person_id) REFERENCES catalog_people (id) ON DELETE CASCADE, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE SET NULL )",
    "CREATE TABLE catalog_moves ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, catalog_person_id INTEGER NOT NULL, work_key VARCHAR(64) NOT NULL, kind VARCHAR(8) NOT NULL, user_id INTEGER, created_at DATETIME NOT NULL, UNIQUE (catalog_person_id, work_key), FOREIGN KEY(catalog_person_id) REFERENCES catalog_people (id) ON DELETE CASCADE, FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL )",
]

V22_INDEXES = [
    "CREATE INDEX ix_catalog_people_person_id ON catalog_people (person_id)",
    "CREATE INDEX ix_catalog_works_foreign ON catalog_works (foreign_id)",
]

V22_STATEMENTS = [*V22_TABLES, *V22_INDEXES]
