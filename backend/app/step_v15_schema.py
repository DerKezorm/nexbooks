"""The step from schema 14 to schema 15: what an indexer's newest entries were read
last and how many releases were fetched today, the origin of a search, the release a hand-over still waits for, the
entries already seen, and the log of the rounds.

Written out as the tables stood then (``tests/schema/v15.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V15_TABLES = [
    "CREATE TABLE rss_seen ( indexer_id INTEGER NOT NULL, key VARCHAR(32) NOT NULL, seen_at DATETIME NOT NULL, PRIMARY KEY (indexer_id, key), FOREIGN KEY(indexer_id) REFERENCES indexers (id) ON DELETE CASCADE )",
    "CREATE TABLE auto_runs ( id INTEGER NOT NULL, kind VARCHAR(10) NOT NULL, started_at DATETIME NOT NULL, finished_at DATETIME, counts JSON, error VARCHAR(64) DEFAULT '' NOT NULL, PRIMARY KEY (id) )",
]

V15_COLUMNS = [
    "ALTER TABLE indexers ADD COLUMN grab_day VARCHAR(10) DEFAULT '' NOT NULL",
    "ALTER TABLE indexers ADD COLUMN grab_count INTEGER DEFAULT 0 NOT NULL",
    "ALTER TABLE indexers ADD COLUMN rss_checked_at DATETIME",
    "ALTER TABLE indexers ADD COLUMN rss_error VARCHAR(64) DEFAULT '' NOT NULL",
    "ALTER TABLE indexers ADD COLUMN grab_paused_until DATETIME",
    "ALTER TABLE release_searches ADD COLUMN origin VARCHAR(8) DEFAULT 'search' NOT NULL",
    "ALTER TABLE procure_wishes ADD COLUMN handover JSON",
]

V15_INDEXES = [
    "CREATE INDEX ix_rss_seen_at ON rss_seen (seen_at)",
    "CREATE INDEX ix_auto_runs_kind ON auto_runs (kind, id)",
]

V15_STATEMENTS = [*V15_TABLES, *V15_COLUMNS, *V15_INDEXES]
