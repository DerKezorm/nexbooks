"""The step from schema 11 to schema 12: the tables that did not exist
before.

Written out as the tables stood then (``tests/schema/v12.sql``), not taken from the models, which move on. The
statements below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V12_STATEMENTS = [
    "CREATE TABLE blocklist ( id INTEGER NOT NULL, work_id INTEGER, foreign_id INTEGER, book_key VARCHAR(300) DEFAULT '' NOT NULL, kind VARCHAR(8) NOT NULL, title VARCHAR(400) NOT NULL, title_key VARCHAR(400) NOT NULL, protocol VARCHAR(8) NOT NULL, info_hash VARCHAR(40) DEFAULT '' NOT NULL, indexer_name VARCHAR(100) DEFAULT '' NOT NULL, reason VARCHAR(32) NOT NULL, by_user_id INTEGER, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE SET NULL, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE SET NULL, FOREIGN KEY(by_user_id) REFERENCES users (id) ON DELETE SET NULL )",
    "CREATE TABLE downloads ( id INTEGER NOT NULL, user_id INTEGER, work_id INTEGER, foreign_id INTEGER, library_id INTEGER, kind VARCHAR(8) NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, title VARCHAR(400) NOT NULL, format VARCHAR(12) DEFAULT '' NOT NULL, size BIGINT, protocol VARCHAR(8) NOT NULL, info_hash VARCHAR(40) DEFAULT '' NOT NULL, indexer_id INTEGER, indexer_name VARCHAR(100) DEFAULT '' NOT NULL, client_id INTEGER, client_name VARCHAR(100) DEFAULT '' NOT NULL, client_ref VARCHAR(128) DEFAULT '' NOT NULL, category VARCHAR(64) DEFAULT '' NOT NULL, state VARCHAR(16) NOT NULL, forced JSON, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE SET NULL, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE SET NULL, FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE SET NULL, FOREIGN KEY(indexer_id) REFERENCES indexers (id) ON DELETE SET NULL, FOREIGN KEY(client_id) REFERENCES download_clients (id) ON DELETE SET NULL )",
    "CREATE TABLE release_searches ( id INTEGER NOT NULL, user_id INTEGER NOT NULL, work_id INTEGER, foreign_id INTEGER, language VARCHAR(8) NOT NULL, subject JSON, state VARCHAR(8) NOT NULL, progress JSON, error VARCHAR(64) DEFAULT '' NOT NULL, created_at DATETIME NOT NULL, started_at DATETIME, finished_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE CASCADE )",
    "CREATE TABLE search_releases ( id INTEGER NOT NULL, search_id INTEGER NOT NULL, indexer_id INTEGER, indexer_name VARCHAR(100) DEFAULT '' NOT NULL, protocol VARCHAR(8) NOT NULL, title VARCHAR(400) NOT NULL, size BIGINT, published_at DATETIME, seeders INTEGER, peers INTEGER, grabs INTEGER, categories JSON, guid VARCHAR(24) DEFAULT '' NOT NULL, info_hash VARCHAR(40) DEFAULT '' NOT NULL, link_enc TEXT DEFAULT '' NOT NULL, min_ratio FLOAT, min_seed_seconds INTEGER, kind VARCHAR(8) NOT NULL, format VARCHAR(12) DEFAULT '' NOT NULL, format_how VARCHAR(10) DEFAULT '' NOT NULL, tier INTEGER DEFAULT 0 NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, language_how VARCHAR(10) DEFAULT '' NOT NULL, variant VARCHAR(12) DEFAULT '' NOT NULL, bitrate INTEGER, reasons JSON, score INTEGER DEFAULT 0 NOT NULL, rank INTEGER, PRIMARY KEY (id), FOREIGN KEY(search_id) REFERENCES release_searches (id) ON DELETE CASCADE, FOREIGN KEY(indexer_id) REFERENCES indexers (id) ON DELETE SET NULL )",
    'CREATE INDEX ix_blocklist_book ON blocklist (book_key)',
    'CREATE INDEX ix_blocklist_foreign ON blocklist (foreign_id)',
    'CREATE INDEX ix_blocklist_name ON blocklist (title_key)',
    'CREATE INDEX ix_blocklist_work ON blocklist (work_id)',
    'CREATE INDEX ix_downloads_foreign ON downloads (foreign_id)',
    'CREATE INDEX ix_downloads_state ON downloads (state)',
    'CREATE INDEX ix_downloads_work ON downloads (work_id)',
    'CREATE INDEX ix_release_searches_person ON release_searches (user_id, created_at)',
    'CREATE INDEX ix_search_releases_search ON search_releases (search_id, kind, rank)',
    "CREATE UNIQUE INDEX uq_blocklist_foreign ON blocklist (foreign_id, title_key, protocol) WHERE foreign_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_blocklist_orphan ON blocklist (book_key, title_key, protocol) WHERE work_id IS NULL AND foreign_id IS NULL",
    "CREATE UNIQUE INDEX uq_blocklist_work ON blocklist (work_id, title_key, protocol) WHERE work_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_downloads_foreign_kind ON downloads (foreign_id, kind) WHERE foreign_id IS NOT NULL AND state IN ('sending', 'sent', 'unsure')",
    "CREATE UNIQUE INDEX uq_downloads_work_kind ON downloads (work_id, kind) WHERE work_id IS NOT NULL AND state IN ('sending', 'sent', 'unsure')",
]
