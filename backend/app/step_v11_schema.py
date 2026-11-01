"""The step from schema 10 to schema 11: the tables that did not exist before.

Written out as the tables stood then (``tests/schema/v11.sql``), not taken from the models, which move on. The
statements below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V11_STATEMENTS = [
    "CREATE TABLE download_clients ( id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, kind VARCHAR(16) NOT NULL, url VARCHAR(1024) NOT NULL, username VARCHAR(256) DEFAULT '' NOT NULL, secret_enc TEXT DEFAULT '' NOT NULL, category VARCHAR(64) DEFAULT 'nexbooks' NOT NULL, priority INTEGER DEFAULT 1 NOT NULL, enabled BOOLEAN DEFAULT 1 NOT NULL, version VARCHAR(40) DEFAULT '' NOT NULL, last_error VARCHAR(64) DEFAULT '' NOT NULL, checked_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id) )",
    "CREATE TABLE indexers ( id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, kind VARCHAR(8) NOT NULL, url VARCHAR(1024) NOT NULL, api_key_enc TEXT DEFAULT '' NOT NULL, enabled BOOLEAN DEFAULT 1 NOT NULL, priority INTEGER DEFAULT 25 NOT NULL, daily_limit INTEGER DEFAULT 0 NOT NULL, categories JSON, category_langs JSON, caps JSON, caps_checked_at DATETIME, paused_until DATETIME, pause_cause VARCHAR(32) DEFAULT '' NOT NULL, last_error VARCHAR(64) DEFAULT '' NOT NULL, api_current INTEGER, api_max INTEGER, grab_current INTEGER, grab_max INTEGER, limits_seen_at DATETIME, used_day VARCHAR(10) DEFAULT '' NOT NULL, used_count INTEGER DEFAULT 0 NOT NULL, grabs INTEGER DEFAULT 0 NOT NULL, prowlarr_indexer_id INTEGER, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id) )",
    'CREATE TABLE path_mappings ( id INTEGER NOT NULL, client_id INTEGER NOT NULL, remote VARCHAR(1000) NOT NULL, local VARCHAR(1000) NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(client_id) REFERENCES download_clients (id) ON DELETE CASCADE )',
    "CREATE TABLE prowlarr_connections ( id INTEGER NOT NULL, url VARCHAR(1024) NOT NULL, api_key_enc TEXT DEFAULT '' NOT NULL, auto_add BOOLEAN DEFAULT 1 NOT NULL, version VARCHAR(40) DEFAULT '' NOT NULL, last_sync_at DATETIME, last_error VARCHAR(64) DEFAULT '' NOT NULL, last_counts JSON, created_at DATETIME NOT NULL, PRIMARY KEY (id) )",
    'CREATE INDEX ix_path_mappings_client_id ON path_mappings (client_id)',
]
