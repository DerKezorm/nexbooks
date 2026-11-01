"""The step from schema 16 to schema 17 (what was wished, and the books filed under another book's wish).

* **What was wished** is kept on the wish, the download and the line of the history (``wished``: the title, the
  authors, the kind and the language the moment the wish was made), so that the history shows what was asked for even
  when the work is renamed later.
* **The books filed under the wish of another book are set apart** (``heal``): a download for a book of the sources
  that put a book of another title into the library (the title rule of ``release_match``: ``Wir wollen Ruß sehen``
  is no ``Ruß``) hung the book of the sources, its wish and its downloads on that book's work. Once, here (the update
  makes a backup first): the names of the book of the sources leave the editions of that work, the wish searches again
  for the book of the sources, running downloads and lines of the history that were not filed go back to it. The work
  stays as a book of its own.

Written out as the tables stood then (``tests/schema/v17.sql``), not taken from the models, which move on."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import Connection, text

logger = logging.getLogger("nexbooks.db")

V17_STATEMENTS = [
    "ALTER TABLE procure_wishes ADD COLUMN wished JSON",
    "ALTER TABLE downloads ADD COLUMN wished JSON",
    "ALTER TABLE procure_history ADD COLUMN wished JSON",
]

#: The states of a download that is not over (as they were at schema 17).
_ACTIVE = ("sending", "unsure", "queued", "downloading", "paused", "completed", "problem")


def _authors(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [name for name in value or [] if isinstance(name, str)][:6]


def _by_hand(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    return isinstance(value, dict) and bool(value.get("take") or value.get("unit"))


def _ref(ref: str) -> tuple[str, str, str]:
    source, _, key = (ref or "").partition(":")
    return source[:16], "edition" if source in ("audible", "apple") else "work", key[:120]


def _filed_wrongly(foreign_title: str, authors: list[str], work_titles: list[str], titles: list[str]) -> bool:
    """Whether the work holds another book than the book of the sources by the gap the old check had: the title of the
    work (as it reads now) is not the title of the book of the sources as a whole, and that title stood in a title of
    the work or of its editions as whole words (``Ruß`` in ``Wir wollen Ruß sehen``, which the old check let through).
    Titles that have nothing in common (a translation the sources know under another title) are left as they are."""
    from .services.procure import release_match
    from .services.readers import names

    book = release_match.Book(titles=(release_match.Title(foreign_title),), authors=tuple(authors))
    named = [title for title in work_titles if title.strip()]
    if not named or any(release_match.title_is(title, book) for title in named):
        return False
    return any(names.title_fits(title, foreign_title) for title in [*named, *titles] if title.strip())


def heal(connection: Connection) -> int:
    """Sets apart the books filed under the wish of another book of the sources (see the top). Returns how many pairs
    of a book of the sources and a work were set apart. Never removes a work, an edition or a file."""
    pairs: dict[tuple[int, int], None] = {}
    for table in ("downloads", "procure_history"):
        query = f"SELECT foreign_id, work_id FROM {table} WHERE foreign_id IS NOT NULL AND work_id IS NOT NULL"  # noqa: S608
        for foreign_id, work_id in connection.execute(text(query)):
            pairs[(int(foreign_id), int(work_id))] = None
    healed = 0
    for foreign_id, work_id in pairs:
        book = connection.execute(text("SELECT title, authors, ref FROM foreign_books WHERE id = :id"),
                                  {"id": foreign_id}).first()
        if book is None or not (book[0] or "").strip():
            continue
        editions = connection.execute(text(
            "SELECT id, title, original_title FROM editions WHERE work_id = :work"), {"work": work_id}).all()
        work = connection.execute(text("SELECT title, original_title FROM works WHERE id = :work"),
                                  {"work": work_id}).first()
        titles = [str(value or "") for row in editions for value in (row[1], row[2])]
        named = [str(value or "") for value in (work or ())]
        if not editions or not _filed_wrongly(book[0], _authors(book[1]), named, titles):
            continue
        decisions = [row[0] for row in connection.execute(text(
            "SELECT decision FROM downloads WHERE foreign_id = :foreign AND work_id = :work AND decision IS NOT NULL"),
            {"foreign": foreign_id, "work": work_id})]
        if any(_by_hand(value) for value in decisions):
            # A person said these files are the book: that stays.
            continue
        healed += 1
        source, kind, key = _ref(book[2])
        if key:
            connection.execute(text(
                "DELETE FROM external_refs WHERE source = :source AND kind = :kind AND key = :key AND edition_id IN "
                "(SELECT id FROM editions WHERE work_id = :work)"), {"source": source, "kind": kind, "key": key,
                                                                       "work": work_id})
        wishes = [row[0] for row in connection.execute(text(
            "SELECT DISTINCT wish_id FROM downloads WHERE foreign_id = :foreign AND wish_id IS NOT NULL"),
            {"foreign": foreign_id})]
        for wish_id in wishes:
            nested = connection.begin_nested()
            try:
                connection.execute(text(
                    "UPDATE procure_wishes SET work_id = NULL, foreign_id = :foreign, state = 'searching', "
                    "search_id = NULL, done_at = NULL, detail = NULL, next_try_at = NULL, handover = NULL "
                    "WHERE id = :wish AND work_id = :work"), {"foreign": foreign_id, "wish": wish_id, "work": work_id})
                nested.commit()
            except Exception:  # noqa: BLE001 - an open wish of the book of the sources is there already: this one stays
                nested.rollback()
        placeholders = ", ".join(f"'{state}'" for state in _ACTIVE)
        connection.execute(text(
            f"UPDATE downloads SET work_id = NULL WHERE foreign_id = :foreign AND work_id = :work "  # noqa: S608
            f"AND state IN ({placeholders})"), {"foreign": foreign_id, "work": work_id})
        connection.execute(text(
            "UPDATE procure_history SET work_id = NULL WHERE foreign_id = :foreign AND work_id = :work "
            "AND result != 'done'"), {"foreign": foreign_id, "work": work_id})
    if healed:
        logger.info("Books filed under the wish of another book set apart: %d", healed)
    return healed
