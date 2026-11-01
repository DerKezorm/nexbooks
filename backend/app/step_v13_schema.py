"""The step from schema 12 to schema 13: the tables of the wishes, the
foreign jobs and the history, the columns of a download that is followed, and the two indexes that keep a book of one
kind and library to one release at a time, now for every state a download that is not over can be in, and the
library the entries of the blocklist were made for.

Written out as the tables stood then (``tests/schema/v13.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V13_TABLES = [
    "CREATE TABLE foreign_jobs ( id INTEGER NOT NULL, client_id INTEGER NOT NULL, ref VARCHAR(128) NOT NULL, name VARCHAR(400) NOT NULL, job_state VARCHAR(12) DEFAULT '' NOT NULL, ignored BOOLEAN DEFAULT 0 NOT NULL, first_seen_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (client_id, ref), FOREIGN KEY(client_id) REFERENCES download_clients (id) ON DELETE CASCADE )",
    "CREATE TABLE procure_history ( id INTEGER NOT NULL, user_id INTEGER, library_id INTEGER, work_id INTEGER, foreign_id INTEGER, kind VARCHAR(8) NOT NULL, book_title VARCHAR(300) DEFAULT '' NOT NULL, book_author VARCHAR(300) DEFAULT '' NOT NULL, release VARCHAR(400) DEFAULT '' NOT NULL, indexer_name VARCHAR(100) DEFAULT '' NOT NULL, result VARCHAR(10) NOT NULL, note JSON, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE SET NULL, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE SET NULL, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE SET NULL )",
    "CREATE TABLE procure_wishes ( id INTEGER NOT NULL, user_id INTEGER NOT NULL, work_id INTEGER, foreign_id INTEGER, kind VARCHAR(8) NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, library_id INTEGER, state VARCHAR(12) NOT NULL, search_id INTEGER, searched_at DATETIME, next_try_at DATETIME, detail JSON, release_date VARCHAR(10) DEFAULT '' NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, done_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE CASCADE, FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE SET NULL )",
]

V13_COLUMNS = [
    'ALTER TABLE downloads ADD COLUMN wish_id INTEGER REFERENCES procure_wishes (id) ON DELETE SET NULL',
    'ALTER TABLE downloads ADD COLUMN progress FLOAT',
    'ALTER TABLE downloads ADD COLUMN remaining_seconds INTEGER',
    "ALTER TABLE downloads ADD COLUMN client_state VARCHAR(64) DEFAULT '' NOT NULL",
    "ALTER TABLE downloads ADD COLUMN problem_code VARCHAR(32) DEFAULT '' NOT NULL",
    'ALTER TABLE downloads ADD COLUMN problem_values JSON',
    "ALTER TABLE downloads ADD COLUMN reported_path VARCHAR(4096) DEFAULT '' NOT NULL",
    'ALTER TABLE downloads ADD COLUMN missing_count INTEGER DEFAULT 0 NOT NULL',
    'ALTER TABLE downloads ADD COLUMN completed_at DATETIME',
    'ALTER TABLE downloads ADD COLUMN finished_at DATETIME',
    "ALTER TABLE downloads ADD COLUMN failed_reason VARCHAR(32) DEFAULT '' NOT NULL",
    "ALTER TABLE downloads ADD COLUMN failed_detail VARCHAR(32) DEFAULT '' NOT NULL",
    'ALTER TABLE downloads ADD COLUMN removal_pending BOOLEAN DEFAULT 0 NOT NULL',
    'ALTER TABLE downloads ADD COLUMN goal_ratio FLOAT',
    'ALTER TABLE downloads ADD COLUMN goal_seconds INTEGER',
    'ALTER TABLE downloads ADD COLUMN imported_at DATETIME',
    "ALTER TABLE downloads ADD COLUMN seed_state VARCHAR(10) DEFAULT '' NOT NULL",
    'ALTER TABLE downloads ADD COLUMN seed_ratio FLOAT',
    'ALTER TABLE downloads ADD COLUMN seed_seconds INTEGER',
    'ALTER TABLE blocklist ADD COLUMN library_id INTEGER REFERENCES libraries (id) ON DELETE SET NULL',
]

V13_DROPS = [
    'DROP INDEX uq_downloads_work_kind',
    'DROP INDEX uq_downloads_foreign_kind',
    "UPDATE downloads SET state = 'queued' WHERE state = 'sent'",
]

V13_INDEXES = [
    'CREATE INDEX ix_blocklist_library ON blocklist (library_id)',
    'CREATE INDEX ix_downloads_wish ON downloads (wish_id)',
    'CREATE INDEX ix_procure_history_library ON procure_history (library_id, created_at)',
    'CREATE INDEX ix_procure_wishes_library ON procure_wishes (library_id)',
    'CREATE INDEX ix_procure_wishes_state ON procure_wishes (state)',
    "CREATE UNIQUE INDEX uq_downloads_foreign_kind ON downloads (foreign_id, kind, coalesce(library_id, 0)) WHERE foreign_id IS NOT NULL AND state IN ('sending', 'unsure', 'queued', 'downloading', 'paused', 'completed', 'problem')",
    "CREATE UNIQUE INDEX uq_downloads_work_kind ON downloads (work_id, kind, coalesce(library_id, 0)) WHERE work_id IS NOT NULL AND state IN ('sending', 'unsure', 'queued', 'downloading', 'paused', 'completed', 'problem')",
    "CREATE UNIQUE INDEX uq_procure_wishes_foreign ON procure_wishes (foreign_id, kind, language, coalesce(library_id, 0)) WHERE foreign_id IS NOT NULL AND state != 'done'",
    "CREATE UNIQUE INDEX uq_procure_wishes_work ON procure_wishes (work_id, kind, language, coalesce(library_id, 0)) WHERE work_id IS NOT NULL AND state != 'done'",
]

V13_STATEMENTS = [*V13_TABLES, *V13_COLUMNS, *V13_DROPS, *V13_INDEXES]
