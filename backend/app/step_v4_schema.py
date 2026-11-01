"""The step from schema 3 to schema 4: the table of what the index holds per
edition, the full-text index and the trigger that keeps it free of deleted editions.

Written out as the tables stood then (``tests/schema/v4.sql``), not taken from the models, which move on. The statements
below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V4_STATEMENTS = [
    "CREATE TABLE search_entries ( edition_id INTEGER NOT NULL, digest VARCHAR(40) NOT NULL, PRIMARY KEY (edition_id), FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    "CREATE VIRTUAL TABLE IF NOT EXISTS book_fts USING fts5(title, original, people, series, description, ids, tokenize = 'unicode61 remove_diacritics 2', content = '', contentless_delete = 1)",
    "CREATE TRIGGER IF NOT EXISTS editions_search_gone AFTER DELETE ON editions BEGIN DELETE FROM book_fts WHERE rowid = old.id; END",
]
