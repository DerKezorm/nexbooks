"""The step from schema 28 to schema 29 (the sync of KOReader): the partial MD5 that KOReader names an e-book by, on the
files, and the place as KOReader worded it, on the progress.

Written out as the tables stood then (``tests/schema/v29.sql``), not taken from the models, which move on. Both are
columns on tables that hold rows: empty for every row there is. The checksums of the files that are there are made in the
background after the start (``services/kosync.backfill``), not in this step, which must not read a library."""

from __future__ import annotations

# ruff: noqa: E501

V29_STATEMENTS = [
    "ALTER TABLE files ADD COLUMN partial_md5 VARCHAR(32) DEFAULT '' NOT NULL",
    "CREATE INDEX ix_files_partial_md5 ON files (partial_md5)",
    "ALTER TABLE progress ADD COLUMN koreader VARCHAR(1000) DEFAULT '' NOT NULL",
]
