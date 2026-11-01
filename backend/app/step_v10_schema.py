"""The step from schema 9 to schema 10: the tables
that did not exist before. The new column of ``progress`` is added by the step itself.

Written out as the tables stood then (``tests/schema/v10.sql``), not taken from the models, which move on. The
statements below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V10_STATEMENTS = [
    "CREATE TABLE history ( id INTEGER NOT NULL, user_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, kind VARCHAR(8) NOT NULL, finished_on VARCHAR(10) NOT NULL, source VARCHAR(4) DEFAULT 'hand' NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (user_id, edition_id, finished_on), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    "CREATE TABLE organize_moves ( id INTEGER NOT NULL, run_id INTEGER NOT NULL, file_id INTEGER, edition_id INTEGER, source VARCHAR(1500) NOT NULL, target VARCHAR(1500) NOT NULL, state VARCHAR(8) NOT NULL, reason VARCHAR(32) DEFAULT '' NOT NULL, size BIGINT NOT NULL, mtime_ns BIGINT NOT NULL, PRIMARY KEY (id), FOREIGN KEY(run_id) REFERENCES organize_runs (id) ON DELETE CASCADE, FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE SET NULL, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE SET NULL )",
    "CREATE TABLE organize_runs ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, created_by INTEGER, scheme VARCHAR(300) NOT NULL, state VARCHAR(8) NOT NULL, summary JSON, done INTEGER DEFAULT 0 NOT NULL, total INTEGER DEFAULT 0 NOT NULL, error VARCHAR(40) DEFAULT '' NOT NULL, created_at DATETIME NOT NULL, started_at DATETIME, finished_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL )",
    "CREATE TABLE person_info ( person_id INTEGER NOT NULL, wikidata_id VARCHAR(16) DEFAULT '' NOT NULL, wikidata_state VARCHAR(8) DEFAULT '' NOT NULL, photo VARCHAR(64) DEFAULT '' NOT NULL, photo_source VARCHAR(16) DEFAULT '' NOT NULL, photo_author VARCHAR(200) DEFAULT '' NOT NULL, photo_license VARCHAR(80) DEFAULT '' NOT NULL, photo_license_url VARCHAR(300) DEFAULT '' NOT NULL, photo_page VARCHAR(300) DEFAULT '' NOT NULL, bios JSON, checked_at DATETIME, name_key VARCHAR(200) DEFAULT '' NOT NULL, PRIMARY KEY (person_id), FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE )",
    'CREATE TABLE person_photos ( person_id INTEGER NOT NULL, library_id INTEGER NOT NULL, photo VARCHAR(64) NOT NULL, path VARCHAR(1500) NOT NULL, size BIGINT NOT NULL, mtime_ns BIGINT NOT NULL, PRIMARY KEY (person_id, library_id), FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE, FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE )',
    'CREATE INDEX ix_history_edition ON history (edition_id)',
    'CREATE INDEX ix_history_person ON history (user_id, finished_on)',
    'CREATE INDEX ix_organize_moves_run ON organize_moves (run_id, id)',
    'CREATE INDEX ix_organize_runs_library ON organize_runs (library_id, id)',
]
