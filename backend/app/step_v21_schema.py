"""The step from schema 20 to schema 21: the requests a device makes for
a code of its own, which a signed-in person confirms in the browser.

Written out as the tables stood then (``tests/schema/v21.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V21_TABLES = [
    "CREATE TABLE app_requests ( id VARCHAR(32) NOT NULL, secret_hash VARCHAR(64) NOT NULL, code_hash VARCHAR(64) NOT NULL, device_name VARCHAR(80) NOT NULL, state VARCHAR(10) NOT NULL, sender VARCHAR(64) NOT NULL, proxy VARCHAR(64), account_id INTEGER, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, confirmed_at DATETIME, used_at DATETIME, device_id INTEGER, PRIMARY KEY (id), FOREIGN KEY(account_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE SET NULL )",
]

V21_INDEXES = [
    "CREATE UNIQUE INDEX uq_app_requests_code ON app_requests (code_hash)",
    "CREATE INDEX ix_app_requests_sender ON app_requests (sender, state)",
    "CREATE INDEX ix_app_requests_account ON app_requests (account_id)",
]

V21_STATEMENTS = [*V21_TABLES, *V21_INDEXES]
