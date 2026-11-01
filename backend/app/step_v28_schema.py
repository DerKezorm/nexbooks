"""The step from schema 27 to schema 28 (connections for e-readers): the table of the connections a person makes for an
e-reader or a reading app, and the Kindle address on the account.

Written out as the tables stood then (``tests/schema/v28.sql``), not taken from the models, which move on. Nothing of the
table existed before, so the step makes it; the address is one column on a table that holds rows, empty for every
account."""

from __future__ import annotations

# ruff: noqa: E501

V28_STATEMENTS = [
    "ALTER TABLE users ADD COLUMN kindle_address VARCHAR(255) DEFAULT '' NOT NULL",
    "CREATE TABLE ereader_links ( id INTEGER NOT NULL, account_id INTEGER NOT NULL, kind VARCHAR(8) NOT NULL, name VARCHAR(100) NOT NULL, token_hash VARCHAR(64) NOT NULL, prefix VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, last_used_at DATETIME, blocked_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(account_id) REFERENCES users (id) ON DELETE CASCADE, UNIQUE (token_hash) )",
    "CREATE INDEX ix_ereader_links_account_id ON ereader_links (account_id)",
]
