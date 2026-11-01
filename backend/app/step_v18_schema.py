"""The step from schema 17 to schema 18: the codes a browser makes so that the app
can fetch a device token.

Written out as the tables stood then (``tests/schema/v18.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V18_TABLES = [
    "CREATE TABLE app_pairings ( id VARCHAR(32) NOT NULL, account_id INTEGER NOT NULL, secret_hash VARCHAR(64) NOT NULL, code_hash VARCHAR(64) NOT NULL, state VARCHAR(8) NOT NULL, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, used_at DATETIME, device_id INTEGER, PRIMARY KEY (id), FOREIGN KEY(account_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE SET NULL )",
]

V18_INDEXES = [
    "CREATE UNIQUE INDEX uq_app_pairings_code ON app_pairings (code_hash)",
    "CREATE INDEX ix_app_pairings_account ON app_pairings (account_id, state)",
]

V18_STATEMENTS = [*V18_TABLES, *V18_INDEXES]
