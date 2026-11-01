"""The step from schema 2 to schema 3: three columns on the editions, and the tables of
the sources and of the matching.

Written out as the tables stood then (``tests/schema/v3.sql``), not taken from the models, which move on. The statements
below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V3_STATEMENTS = [
    "ALTER TABLE editions ADD COLUMN original_language VARCHAR(8) DEFAULT '' NOT NULL",
    "ALTER TABLE editions ADD COLUMN overlay JSON",
    "ALTER TABLE editions ADD COLUMN grouped_by VARCHAR(8) DEFAULT '' NOT NULL",
    'CREATE TABLE external_refs ( id INTEGER NOT NULL, work_id INTEGER, edition_id INTEGER, source VARCHAR(16) NOT NULL, kind VARCHAR(8) NOT NULL, "key" VARCHAR(120) NOT NULL, market VARCHAR(8) DEFAULT \'\' NOT NULL, PRIMARY KEY (id), UNIQUE (edition_id, source, kind, "key", market), FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )',
    "CREATE TABLE match_queue ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, state VARCHAR(8) NOT NULL, reason VARCHAR(40) DEFAULT '' NOT NULL, proposals JSON, fingerprint VARCHAR(64) DEFAULT '' NOT NULL, decision VARCHAR(10) DEFAULT '' NOT NULL, decided_by INTEGER, decided_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (edition_id), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE, FOREIGN KEY(decided_by) REFERENCES users (id) ON DELETE SET NULL )",
    'CREATE TABLE source_cache ( id INTEGER NOT NULL, source VARCHAR(16) NOT NULL, "key" VARCHAR(300) NOT NULL, value JSON, fetched_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (source, "key") )',
    "CREATE TABLE source_state ( source VARCHAR(16) NOT NULL, tokens FLOAT DEFAULT 0 NOT NULL, refilled_at DATETIME, day VARCHAR(10) DEFAULT '' NOT NULL, day_count INTEGER DEFAULT 0 NOT NULL, last_ok_at DATETIME, last_error VARCHAR(40) DEFAULT '' NOT NULL, last_error_at DATETIME, fail_count INTEGER DEFAULT 0 NOT NULL, blocked_until DATETIME, state VARCHAR(16) DEFAULT '' NOT NULL, PRIMARY KEY (source) )",
    'CREATE INDEX ix_external_refs_edition_id ON external_refs (edition_id)',
    'CREATE INDEX ix_external_refs_key ON external_refs (source, kind, "key")',
    'CREATE INDEX ix_external_refs_work_id ON external_refs (work_id)',
    'CREATE INDEX ix_match_queue_library ON match_queue (library_id, state)',
    'CREATE INDEX ix_source_cache_expires ON source_cache (expires_at)',
]
