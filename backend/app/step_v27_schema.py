"""The step from schema 26 to schema 27: the generation of the rules of the check after the download that a download
was last judged by (``Download.check_rev``). One column on a table that holds rows; every row starts at 0, so that a
question under "Braucht dich" is judged once more by the rules of today (``procure.recheck``)."""

from __future__ import annotations

V27_STATEMENTS = [
    "ALTER TABLE downloads ADD COLUMN check_rev INTEGER DEFAULT 0 NOT NULL",
]
