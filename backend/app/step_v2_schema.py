"""The step from schema 1 to schema 2: libraries, works, editions, files, chapters, people.

Written out as the tables stood then (``tests/schema/v2.sql``), not taken from the models, which move on. The
statements below are that record."""

from __future__ import annotations

# ruff: noqa: E501

V2_STATEMENTS = [
    "CREATE TABLE chapters ( id INTEGER NOT NULL, edition_id INTEGER NOT NULL, number INTEGER NOT NULL, title VARCHAR(300) NOT NULL, start_ms INTEGER DEFAULT 0 NOT NULL, target VARCHAR(500) DEFAULT '' NOT NULL, depth INTEGER DEFAULT 0 NOT NULL, PRIMARY KEY (id), FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    'CREATE TABLE credits ( id INTEGER NOT NULL, person_id INTEGER NOT NULL, work_id INTEGER, edition_id INTEGER, role VARCHAR(12) NOT NULL, position INTEGER DEFAULT 0 NOT NULL, PRIMARY KEY (id), FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )',
    "CREATE TABLE editions ( id INTEGER NOT NULL, work_id INTEGER NOT NULL, library_id INTEGER NOT NULL, kind VARCHAR(8) NOT NULL, variant VARCHAR(12) DEFAULT '' NOT NULL, variant_uncertain BOOLEAN DEFAULT 0 NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, title VARCHAR(300) NOT NULL, subtitle VARCHAR(300) DEFAULT '' NOT NULL, isbn VARCHAR(20) DEFAULT '' NOT NULL, asin VARCHAR(20) DEFAULT '' NOT NULL, publisher VARCHAR(200) DEFAULT '' NOT NULL, published VARCHAR(10) DEFAULT '' NOT NULL, description TEXT DEFAULT '' NOT NULL, genre VARCHAR(16) DEFAULT '' NOT NULL, series_name VARCHAR(200) DEFAULT '' NOT NULL, series_id INTEGER, series_pos FLOAT, original_title VARCHAR(300) DEFAULT '' NOT NULL, title_key VARCHAR(300) DEFAULT '' NOT NULL, shelf_key VARCHAR(500) DEFAULT '' NOT NULL, pages INTEGER DEFAULT 0 NOT NULL, duration INTEGER DEFAULT 0 NOT NULL, cover VARCHAR(64) DEFAULT '' NOT NULL, color VARCHAR(7) DEFAULT '' NOT NULL, missing_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE, FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(series_id) REFERENCES series (id) ON DELETE SET NULL )",
    "CREATE TABLE files ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, edition_id INTEGER, role VARCHAR(8) NOT NULL, path VARCHAR(1500) NOT NULL, size BIGINT NOT NULL, mtime_ns BIGINT NOT NULL, sha256 VARCHAR(64) DEFAULT '' NOT NULL, format VARCHAR(8) NOT NULL, part INTEGER DEFAULT 1 NOT NULL, duration_ms INTEGER DEFAULT 0 NOT NULL, unit VARCHAR(1500) DEFAULT '' NOT NULL, probe JSON, dirty BOOLEAN DEFAULT 0 NOT NULL, missing_at DATETIME, seen_at DATETIME NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (library_id, path), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    'CREATE TABLE libraries ( id INTEGER NOT NULL, name VARCHAR(80) NOT NULL, path VARCHAR(1000) NOT NULL, kind VARCHAR(8) NOT NULL, scan_enabled BOOLEAN DEFAULT 1 NOT NULL, scanned_at DATETIME, retry_at DATETIME, progress_done INTEGER DEFAULT 0 NOT NULL, progress_total INTEGER DEFAULT 0 NOT NULL, report JSON, created_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (name), UNIQUE (path) )',
    'CREATE TABLE library_members ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "right" VARCHAR(8) NOT NULL, PRIMARY KEY (id), UNIQUE (library_id, user_id), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE, FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE )',
    'CREATE TABLE library_problems ( id INTEGER NOT NULL, library_id INTEGER NOT NULL, path VARCHAR(1500) NOT NULL, reason VARCHAR(32) NOT NULL, size BIGINT NOT NULL, mtime_ns BIGINT NOT NULL, seen_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (library_id, path), FOREIGN KEY(library_id) REFERENCES libraries (id) ON DELETE CASCADE )',
    'CREATE TABLE people ( id INTEGER NOT NULL, name VARCHAR(200) NOT NULL, sort_name VARCHAR(200) NOT NULL, "key" VARCHAR(200) NOT NULL, PRIMARY KEY (id), UNIQUE ("key") )',
    'CREATE TABLE series ( id INTEGER NOT NULL, name VARCHAR(200) NOT NULL, "key" VARCHAR(200) NOT NULL, PRIMARY KEY (id), UNIQUE ("key") )',
    'CREATE TABLE works ( id INTEGER NOT NULL, title VARCHAR(300) NOT NULL, original_title VARCHAR(300) DEFAULT \'\' NOT NULL, original_language VARCHAR(8) DEFAULT \'\' NOT NULL, description TEXT DEFAULT \'\' NOT NULL, year INTEGER, genre VARCHAR(16) DEFAULT \'\' NOT NULL, color VARCHAR(7) DEFAULT \'\' NOT NULL, cover VARCHAR(64) DEFAULT \'\' NOT NULL, series_id INTEGER, series_pos FLOAT, pages INTEGER DEFAULT 0 NOT NULL, pattern INTEGER DEFAULT 0 NOT NULL, tall FLOAT DEFAULT (0.5) NOT NULL, gold BOOLEAN DEFAULT 0 NOT NULL, "key" VARCHAR(300) DEFAULT \'\' NOT NULL, main_key VARCHAR(300) DEFAULT \'\' NOT NULL, original_key VARCHAR(300) DEFAULT \'\' NOT NULL, added_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(series_id) REFERENCES series (id) ON DELETE SET NULL )',
    'CREATE INDEX ix_chapters_edition ON chapters (edition_id, number)',
    'CREATE INDEX ix_credits_edition ON credits (edition_id)',
    'CREATE INDEX ix_credits_person ON credits (person_id)',
    'CREATE INDEX ix_credits_work ON credits (work_id)',
    'CREATE INDEX ix_editions_isbn ON editions (isbn)',
    'CREATE INDEX ix_editions_library_id ON editions (library_id)',
    'CREATE INDEX ix_editions_series_id ON editions (series_id)',
    'CREATE INDEX ix_editions_work_id ON editions (work_id)',
    'CREATE INDEX ix_files_dirty ON files (library_id, dirty)',
    'CREATE INDEX ix_files_edition_id ON files (edition_id)',
    'CREATE INDEX ix_files_library_id ON files (library_id)',
    'CREATE INDEX ix_files_unit ON files (library_id, unit)',
    'CREATE INDEX ix_library_members_library_id ON library_members (library_id)',
    'CREATE INDEX ix_library_members_user_id ON library_members (user_id)',
    'CREATE INDEX ix_library_problems_library_id ON library_problems (library_id)',
    'CREATE INDEX ix_works_added_at ON works (added_at)',
    'CREATE INDEX ix_works_key ON works ("key")',
    'CREATE INDEX ix_works_main_key ON works (main_key)',
    'CREATE INDEX ix_works_original_key ON works (original_key)',
    'CREATE INDEX ix_works_series_id ON works (series_id)',
]
