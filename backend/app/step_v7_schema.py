"""The step from schema 6 to schema 7: the books of the sources that are not on a shelf, the lists the
sources publish, when they were fetched, and the wish list.

Written out as the tables stood then (``tests/schema/v7.sql``), not taken from the models, which move on. The statements
below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V7_STATEMENTS = [
    "CREATE TABLE foreign_books ( id INTEGER NOT NULL, token VARCHAR(24) NOT NULL, ref VARCHAR(80) NOT NULL, title VARCHAR(300) NOT NULL, title_key VARCHAR(300) DEFAULT '' NOT NULL, authors JSON, released VARCHAR(10) DEFAULT '' NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, genre VARCHAR(16) DEFAULT '' NOT NULL, series VARCHAR(200) DEFAULT '' NOT NULL, series_pos FLOAT, cover_url VARCHAR(500) DEFAULT '' NOT NULL, cover VARCHAR(64) DEFAULT '' NOT NULL, color VARCHAR(7) DEFAULT '' NOT NULL, data JSON, enriched_at DATETIME, seen_at DATETIME NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (token), UNIQUE (ref) )",
    "CREATE INDEX ix_foreign_books_title ON foreign_books (title_key)",
    "CREATE INDEX ix_foreign_books_seen ON foreign_books (seen_at)",
    "CREATE TABLE list_fetches ( source VARCHAR(16) NOT NULL, market VARCHAR(8) NOT NULL, unit VARCHAR(10) NOT NULL, fetched_at DATETIME, state VARCHAR(8) DEFAULT '' NOT NULL, count INTEGER DEFAULT 0 NOT NULL, error VARCHAR(40) DEFAULT '' NOT NULL, wanted_at DATETIME, PRIMARY KEY (source, market, unit) )",
    "CREATE TABLE lists_cache ( id INTEGER NOT NULL, source VARCHAR(16) NOT NULL, market VARCHAR(8) DEFAULT '' NOT NULL, list VARCHAR(10) NOT NULL, rank INTEGER NOT NULL, ref VARCHAR(80) NOT NULL, foreign_id INTEGER NOT NULL, title VARCHAR(300) NOT NULL, author VARCHAR(300) DEFAULT '' NOT NULL, cover_url VARCHAR(500) DEFAULT '' NOT NULL, released VARCHAR(10) DEFAULT '' NOT NULL, kind VARCHAR(8) DEFAULT 'ebook' NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, genre VARCHAR(16) DEFAULT '' NOT NULL, fetched_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (source, market, list, rank), FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE CASCADE )",
    "CREATE INDEX ix_lists_cache_foreign ON lists_cache (foreign_id)",
    "CREATE INDEX ix_lists_cache_list ON lists_cache (list, market)",
    "CREATE TABLE wishlist ( id INTEGER NOT NULL, user_id INTEGER NOT NULL, foreign_id INTEGER NOT NULL, title VARCHAR(300) NOT NULL, author VARCHAR(300) DEFAULT '' NOT NULL, cover VARCHAR(64) DEFAULT '' NOT NULL, added_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (user_id, foreign_id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE CASCADE )",
    "CREATE INDEX ix_wishlist_person ON wishlist (user_id, added_at)",
]
