"""The step from schema 13 to schema 14: the files of a download as the filing sees
them, the trash of the books an upgrade replaced, the notes the filing leaves one person, and the columns of a download
that is filed.

Written out as the tables stood then (``tests/schema/v14.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V14_TABLES = [
    "CREATE TABLE procure_files ( id INTEGER NOT NULL, download_id INTEGER NOT NULL, path VARCHAR(1500) NOT NULL, origin VARCHAR(8) DEFAULT 'source' NOT NULL, size BIGINT NOT NULL, format VARCHAR(8) NOT NULL, role VARCHAR(8) NOT NULL, unit VARCHAR(1500) DEFAULT '' NOT NULL, probe JSON, state VARCHAR(8) NOT NULL, reason VARCHAR(32) DEFAULT '' NOT NULL, target VARCHAR(1500) DEFAULT '' NOT NULL, how VARCHAR(8) DEFAULT '' NOT NULL, wish_id INTEGER, mtime_ns BIGINT DEFAULT 0 NOT NULL, sha256 VARCHAR(64) DEFAULT '' NOT NULL, PRIMARY KEY (id), FOREIGN KEY(download_id) REFERENCES downloads (id) ON DELETE CASCADE, FOREIGN KEY(wish_id) REFERENCES procure_wishes (id) ON DELETE SET NULL )",
    "CREATE TABLE trash_entries ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, work_id INTEGER, edition_id INTEGER, kind VARCHAR(8) NOT NULL, title VARCHAR(300) DEFAULT '' NOT NULL, author VARCHAR(300) DEFAULT '' NOT NULL, format VARCHAR(12) DEFAULT '' NOT NULL, files JSON, folder VARCHAR(64) NOT NULL, size BIGINT NOT NULL, download_id INTEGER, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE SET NULL, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE SET NULL, FOREIGN KEY(download_id) REFERENCES downloads (id) ON DELETE SET NULL )",
    "CREATE TABLE procure_notices ( id INTEGER NOT NULL, user_id INTEGER NOT NULL, edition_id INTEGER, download_id INTEGER, trash_id INTEGER, code VARCHAR(32) NOT NULL, detail JSON, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE, FOREIGN KEY(download_id) REFERENCES downloads (id) ON DELETE SET NULL, FOREIGN KEY(trash_id) REFERENCES trash_entries (id) ON DELETE SET NULL )",
]

V14_COLUMNS = [
    "ALTER TABLE downloads ADD COLUMN place_step VARCHAR(10) DEFAULT '' NOT NULL",
    "ALTER TABLE downloads ADD COLUMN source_path VARCHAR(4096) DEFAULT '' NOT NULL",
    "ALTER TABLE downloads ADD COLUMN decision JSON",
    "ALTER TABLE downloads ADD COLUMN edition_id INTEGER REFERENCES editions (id) ON DELETE SET NULL",
    "ALTER TABLE downloads ADD COLUMN replaced JSON",
]

V14_INDEXES = [
    "CREATE INDEX ix_procure_files_download ON procure_files (download_id, id)",
    "CREATE INDEX ix_procure_notices_person ON procure_notices (user_id, id)",
    "CREATE INDEX ix_trash_entries_library ON trash_entries (library_id, created_at)",
]

V14_STATEMENTS = [*V14_TABLES, *V14_COLUMNS, *V14_INDEXES]
