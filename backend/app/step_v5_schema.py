"""The step from schema 4 to schema 5: where a person is in an edition, and the marks
they set.

Written out as the tables stood then (``tests/schema/v5.sql``), not taken from the models, which move on. The statements
below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V5_STATEMENTS = [
    "CREATE TABLE bookmarks ( id INTEGER NOT NULL, user_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, locator VARCHAR(1000) NOT NULL, fraction FLOAT DEFAULT 0 NOT NULL, chapter_title VARCHAR(300) DEFAULT '' NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (user_id, edition_id, locator), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    "CREATE TABLE progress ( user_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, locator VARCHAR(1000) NOT NULL, fraction FLOAT DEFAULT 0 NOT NULL, unit VARCHAR(8) DEFAULT 'chapter' NOT NULL, chapter INTEGER DEFAULT 0 NOT NULL, chapter_count INTEGER DEFAULT 0 NOT NULL, chapter_title VARCHAR(300) DEFAULT '' NOT NULL, device_key VARCHAR(40) DEFAULT '' NOT NULL, device VARCHAR(80) DEFAULT '' NOT NULL, moved_at DATETIME NOT NULL, received_at DATETIME NOT NULL, PRIMARY KEY (user_id, edition_id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    "CREATE INDEX ix_bookmarks_person ON bookmarks (user_id, edition_id)",
    "CREATE INDEX ix_progress_edition ON progress (edition_id)",
]
