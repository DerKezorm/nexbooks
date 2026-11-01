"""The step from schema 29 to schema 30 (the sync of a Kobo): the list of editions a person chose for their Kobo, what the
Kobo behind each connection holds of it, the number of the last answer of the sync on the connections, and the place as
the Kobo worded it, on the progress.

Written out as the tables stood then (``tests/schema/v30.sql``), not taken from the models, which move on. The two tables
are new and start empty; the two columns are on tables that hold rows: 0 and empty for every row there is."""

from __future__ import annotations

# ruff: noqa: E501

V30_STATEMENTS = [
    "ALTER TABLE ereader_links ADD COLUMN sync_batch INTEGER DEFAULT 0 NOT NULL",
    "ALTER TABLE progress ADD COLUMN kobo VARCHAR(1000) DEFAULT '' NOT NULL",
    "CREATE TABLE kobo_listed ( user_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, uuid VARCHAR(36) NOT NULL, added_at DATETIME NOT NULL, PRIMARY KEY (user_id, edition_id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(edition_id) REFERENCES editions (id) ON DELETE CASCADE )",
    "CREATE UNIQUE INDEX uq_kobo_listed_uuid ON kobo_listed (user_id, uuid)",
    "CREATE TABLE kobo_held ( link_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, uuid VARCHAR(36) NOT NULL, stamp VARCHAR(40) DEFAULT '' NOT NULL, state_stamp VARCHAR(40) DEFAULT '' NOT NULL, next_stamp VARCHAR(40), next_state_stamp VARCHAR(40), removing BOOLEAN DEFAULT 0 NOT NULL, batch INTEGER DEFAULT 0 NOT NULL, archived BOOLEAN DEFAULT 0 NOT NULL, PRIMARY KEY (link_id, edition_id), FOREIGN KEY(link_id) REFERENCES ereader_links (id) ON DELETE CASCADE )",
    "CREATE UNIQUE INDEX uq_kobo_held_uuid ON kobo_held (link_id, uuid)",
]
