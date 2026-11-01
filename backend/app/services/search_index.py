"""What the search index holds: one entry per edition, in ``book_fts`` (rowid = the edition's id).

An entry is the words a person may look for: the title and subtitle, the original title, the people (authors,
narrators, translators), the series, the description, and the ISBN and ASIN. **Every word is written with the library
of its edition in front**: ``l3lxlantern`` for the word ``lantern`` of a book in library 3. A search asks for the words
of the libraries of the caller only (``services/search.py``), so the index never reads a word, or the list of books
that carry it, in another library: what other people have costs the caller nothing, not even time.

The entry is made when the edition is put together (``catalog.assign``: the scan, a match, a correction by hand) and is
written only when its words changed (a digest in ``search_entries``). A job (``index``) goes over all editions once at
the start after the schema step that made the index, and drops the entries of editions that are gone; the trigger of
the table does so for every deleted edition at once.

Case and accents do not count (the tokenizer of the table); ``ß`` is written ``ss`` here, before the text goes in and
before a word of a search goes out, because the tokenizer leaves it alone.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import ROLES_OF_PEOPLE, Credit, Edition, Person, SearchEntry
from . import jobs

logger = logging.getLogger("nexbooks.search")

#: How much of a description goes into the index.
DESCRIPTION_MAX = 3000
#: How many editions one step of the job takes (a short transaction each).
BATCH = 300

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def scope_prefix(library_id: int) -> str:
    """What stands in front of every word of a library."""
    return f"l{int(library_id)}lx"


def prepare(value: str) -> str:
    """Text as it goes into the index and into a search: composed characters, ``ß`` as ``ss``, no control characters (a
    NUL ends a string for SQLite's tokenizers)."""
    return _CONTROL.sub(" ", unicodedata.normalize("NFC", value)).replace("ß", "ss").replace("ẞ", "SS")


def words(value: str) -> list[str]:
    """The words of a text, lower case: runs of letters and digits."""
    return _WORD.findall(prepare(value).casefold())


def scoped(value: str, library_id: int) -> str:
    """A text as the index keeps it: each of its words with the library in front."""
    prefix = scope_prefix(library_id)
    return " ".join(prefix + word for word in words(value))


def _columns(edition: Edition, people: list[str]) -> dict[str, str]:
    library = edition.library_id
    return {
        "title": scoped(f"{edition.title} {edition.subtitle}", library),
        "original": scoped(edition.original_title, library),
        "people": scoped(" ".join(people), library),
        "series": scoped(edition.series_name, library),
        "description": scoped(edition.description[:DESCRIPTION_MAX], library),
        "ids": scoped(f"{edition.isbn} {edition.asin}", library),
    }


def _digest(columns: dict[str, str]) -> str:
    joined = "\x1f".join(columns[name] for name in sorted(columns))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]


def update(db: Session, edition_ids: Iterable[int]) -> int:
    """Brings the entries of these editions up to date, in the caller's transaction (the caller commits). Returns how
    many entries were written. An edition that is not there any more loses its entry."""
    wanted = sorted(set(edition_ids))
    if not wanted:
        return 0
    editions = {e.id: e for e in db.scalars(select(Edition).where(Edition.id.in_(wanted)))}
    people: dict[int, list[str]] = {}
    for edition_id, name in db.execute(
            select(Credit.edition_id, Person.name).join(Person, Person.id == Credit.person_id)
            .where(Credit.edition_id.in_(wanted), Credit.role.in_(ROLES_OF_PEOPLE))
            .order_by(Credit.edition_id, Credit.role, Credit.position)):
        people.setdefault(edition_id or 0, []).append(name)
    known = dict(db.execute(select(SearchEntry.edition_id, SearchEntry.digest).where(
        SearchEntry.edition_id.in_(wanted))).all())
    written = 0
    for edition_id in wanted:
        edition = editions.get(edition_id)
        if edition is None:
            db.execute(text("DELETE FROM book_fts WHERE rowid = :id"), {"id": edition_id})
            continue
        columns = _columns(edition, people.get(edition_id, []))
        digest = _digest(columns)
        if known.get(edition_id) == digest:
            continue
        db.execute(text("DELETE FROM book_fts WHERE rowid = :id"), {"id": edition_id})
        db.execute(
            text("INSERT INTO book_fts (rowid, title, original, people, series, description, ids) "
                 "VALUES (:id, :title, :original, :people, :series, :description, :ids)"),
            {"id": edition_id, **columns})
        if edition_id in known:
            db.execute(SearchEntry.__table__.update().where(SearchEntry.edition_id == edition_id).values(digest=digest))
        else:
            db.add(SearchEntry(edition_id=edition_id, digest=digest))
        written += 1
    db.flush()
    return written


def index_job(db: Session, job: object) -> None:
    """Goes over all editions, in steps: an entry for each that has none or whose words changed, and none for editions
    that are gone. Re-run safely; the server stopping puts it back as it was (``jobs.Interrupted``)."""
    last = 0
    while True:
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        ids = list(db.scalars(select(Edition.id).where(Edition.id > last).order_by(Edition.id).limit(BATCH)))
        if not ids:
            break
        update(db, ids)
        db.commit()
        last = ids[-1]
    db.execute(text("DELETE FROM book_fts WHERE rowid NOT IN (SELECT id FROM editions)"))
    db.commit()
    logger.info("The search index was brought up to date")


def reindex_all() -> None:
    """For the operator's tools and the tests: the job, run now."""
    with SessionLocal() as db:
        index_job(db, None)


jobs.register("index", index_job)
