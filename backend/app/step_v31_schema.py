"""The step from schema 30 to schema 31 (the list "for later" of the apps): a new table, which starts empty, and the trigger
that takes an entry along when its work loses its last edition to another work.

Written out as they stood then (``tests/schema/v31.sql``), not taken from the models, which move on."""

from __future__ import annotations

# ruff: noqa: E501

V31_STATEMENTS = [
    "CREATE TABLE later ( user_id INTEGER NOT NULL, work_id INTEGER NOT NULL, added_at DATETIME NOT NULL, PRIMARY KEY (user_id, work_id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(work_id) REFERENCES works (id) ON DELETE CASCADE )",
    "CREATE INDEX ix_later_person ON later (user_id, added_at)",
    "CREATE INDEX ix_later_work ON later (work_id)",
    "CREATE TRIGGER IF NOT EXISTS later_follows_the_book AFTER UPDATE OF work_id ON editions WHEN OLD.work_id IS NOT NEW.work_id AND NOT EXISTS (SELECT 1 FROM editions WHERE work_id = OLD.work_id) BEGIN UPDATE OR IGNORE later SET work_id = NEW.work_id WHERE work_id = OLD.work_id; END",
]
