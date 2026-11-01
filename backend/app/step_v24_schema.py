"""The step from schema 23 to schema 24: the books on the themed shelves and when
each part of a shelf was fetched, and two columns that say how long a pass through a book took (the books a person liked
are the ones they finished within a few days).

Written out as the tables stood then (``tests/schema/v24.sql``), not taken from the models, which move on. The two
columns are added to tables that hold rows; nothing is filled in, because nobody can say when an earlier pass began."""

from __future__ import annotations

# ruff: noqa: E501

V24_TABLES = [
    "CREATE TABLE shelf_entries ( id INTEGER NOT NULL, source VARCHAR(16) NOT NULL, market VARCHAR(8) NOT NULL, shelf VARCHAR(16) NOT NULL, section VARCHAR(8) NOT NULL, rank INTEGER NOT NULL, foreign_id INTEGER NOT NULL, award VARCHAR(80) DEFAULT '' NOT NULL, topics JSON, fetched_at DATETIME NOT NULL, evidence FLOAT DEFAULT 0 NOT NULL, series_key VARCHAR(160) DEFAULT '' NOT NULL, PRIMARY KEY (id), UNIQUE (source, market, shelf, section, rank), FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE CASCADE )",
    "CREATE TABLE shelf_fetches ( source VARCHAR(16) NOT NULL, market VARCHAR(8) NOT NULL, shelf VARCHAR(16) NOT NULL, fetched_at DATETIME, state VARCHAR(8) DEFAULT '' NOT NULL, count INTEGER DEFAULT 0 NOT NULL, error VARCHAR(40) DEFAULT '' NOT NULL, wanted_at DATETIME, PRIMARY KEY (source, market, shelf) )",
]

V24_INDEXES = [
    "CREATE INDEX ix_shelf_entries_foreign ON shelf_entries (foreign_id)",
    "CREATE INDEX ix_shelf_entries_shelf ON shelf_entries (market, shelf, section)",
]

V24_COLUMNS = [
    "ALTER TABLE progress ADD COLUMN pass_started_at DATETIME",
    "ALTER TABLE history ADD COLUMN days INTEGER",
]

V24_STATEMENTS = [*V24_TABLES, *V24_INDEXES, *V24_COLUMNS]
