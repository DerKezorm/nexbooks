"""The step from schema 25 to schema 26 (watching an author): who watches whom, and what the watching did with
each work.

Written out as the tables stood then (``tests/schema/v26.sql``), not taken from the models, which move on. Nothing of it
existed before, so the step only makes the tables."""

from __future__ import annotations

# ruff: noqa: E501

V26_TABLES = [
    "CREATE TABLE author_watches ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, catalog_person_id INTEGER NOT NULL, kind VARCHAR(5) DEFAULT 'both' NOT NULL, language VARCHAR(8) DEFAULT '' NOT NULL, more BOOLEAN DEFAULT 0 NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, primed_at DATETIME, seen JSON, since VARCHAR(10) DEFAULT '' NOT NULL, evaluated_for DATETIME, retry_at DATETIME, queued_at DATETIME, UNIQUE (user_id, catalog_person_id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(catalog_person_id) REFERENCES catalog_people (id) ON DELETE CASCADE )",
    "CREATE TABLE author_watch_items ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, catalog_person_id INTEGER NOT NULL, work_key VARCHAR(64) NOT NULL, language VARCHAR(8) NOT NULL, kind VARCHAR(8) DEFAULT '' NOT NULL, via VARCHAR(5) DEFAULT '' NOT NULL, why VARCHAR(32) DEFAULT '' NOT NULL, wish_id INTEGER, foreign_id INTEGER, created_at DATETIME NOT NULL, UNIQUE (user_id, catalog_person_id, work_key, language, kind), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(catalog_person_id) REFERENCES catalog_people (id) ON DELETE CASCADE, FOREIGN KEY(wish_id) REFERENCES procure_wishes (id) ON DELETE SET NULL, FOREIGN KEY(foreign_id) REFERENCES foreign_books (id) ON DELETE SET NULL )",
]

V26_INDEXES = [
    "CREATE INDEX ix_author_watches_author ON author_watches (catalog_person_id)",
    "CREATE INDEX ix_author_watch_items_wish ON author_watch_items (wish_id)",
]

V26_STATEMENTS = [*V26_TABLES, *V26_INDEXES]
