"""Pen names: two names that are one person, on evidence, and nothing without it; and the way back.

A recording whose tags name the author as *X Pseudonym* and an e-book whose package names *X Klarname* are two
people to the scan, two works, two books. They become one only when something proves it, and **only for a name the
files give as an author** (an author field: the creator of an e-book, the authors of a ``metadata.json`` or a
``metadata.opf``, an ``author`` tag of a recording). The artist tag of a recording is no such field: it names a studio,
a publisher or a narrator as often as an author. The evidence (``AliasClaim``) belongs to an edition and lives as long
as what it rests on:

* **The tags** say it (``A schreibt als B``, or an e-book's package ``Last, First (als B)``, see
  ``readers.names.read_people``): evidence of the edition, made anew whenever it is read.
* **Hardcover** knows the other names of the author of the book the edition was matched to (``alternate_names``), and
  the name of the file is one of them. Hardcover's answer counts only when it knows one author of that name: two
  records of the same name are two people, and none of them is asked about (``sources/hardcover.py``).
* **The ISBN or the ASIN of the file** settled a match whose source names another author than the file, with the same
  title. A single book can carry a stranger's ISBN, so this counts only when **two books** of the library say the same
  (``ISBN_BOOKS``).
* **The spelling** says it when the names alone carry it: the same two words turned round, a first name in another
  language, a shorter name that fits one longer name only (``services/spelling.py``); no book is needed, so the evidence
  belongs to no edition. It is made anew whenever the scan has settled (``spelling.note_spelling``).
* **A person** joined two authors by hand (``merge_by_hand``, source ``hand``, no edition): it stands until a person
  splits the name off again.

Evidence of a match goes when the match goes (``clear_match``), and is made anew by every new match of the edition
(``note_match``); evidence of the tags is made anew by every reading of the files (``note_tags``).

**What is, is worked out from the evidence (``recompute``).** The names that are one person are the pairs the evidence
proves, minus the pairs a person said are two people (``apart``, kept for good). A group of more than two names counts
only when one name is the other side of all the pairs a book proves (one author with several pen names); a chain of
names through a third (a studio, a narrator) counts for nothing. The pairs that rest on no book (the spelling, a join by
hand) make their two names one before that is looked at. The person of a group is the one that is there; of several,
the one a person chose to keep when they joined them by hand, else the one the names lead to already, else (for the
same two words turned round) the spelling most books have, else the one with more e-books, then more credits, then the
older (the name printed in the book). Every other name of the group leads to it (``PersonAlias``): its credits are that
person's, and a work of one person with the same title is one work.

**The way back is the same rule.** A name whose evidence is gone (the match was taken back, the book was read again, a
person split them) is a person of its own again: the books of the person it led to are put together anew from their
files, which gives the name its own person, and a book that is no longer by the author of the work it stands in leaves
for a work of its own (``separate_works``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import (
    AUTHOR,
    EDITION_AUDIO,
    EDITION_EBOOK,
    NARRATOR,
    ROLE_BOOK,
    AliasClaim,
    BookFile,
    Credit,
    Edition,
    Person,
    PersonAlias,
    Work,
)
from . import search_index
from .readers.names import authors_compatible, name_relation, person_key, split_authors, titles_alike
from .spelling import SPELLING

logger = logging.getLogger("nexbooks.aliases")

#: Where evidence comes from.
TAGS, ISBN, ASIN, HARDCOVER, HAND = "tags", "isbn", "asin", "hardcover", "hand"
#: The sources of evidence a match makes (taken back with the match).
MATCH_SOURCES = (ISBN, ASIN, HARDCOVER)
#: The sources of evidence that belongs to no book: a person joined two authors by hand, or the names are the same name
#: written differently (``services/spelling.py``).
BOOKLESS_SOURCES = (HAND, SPELLING)
#: An ISBN or an ASIN proves a pen name only when this many books say the same.
ISBN_BOOKS = 2
#: The formats whose author field is the author's: an e-book's package. (A PDF's is the person who made the file.)
_AUTHOR_FIELD_FORMATS = frozenset({"epub", "azw3", "mobi"})


def key_of(name: str) -> str:
    return person_key(name)[:200]


def person_for(db: Session, name: str, library_ids: Any = None) -> Person | None:
    """The person a name leads to: the person with that name, else the person it is an alias of. With ``library_ids``
    (the libraries somebody sees), an alias counts only when a book of those libraries proves it: who types a pen name
    learns the real name only from books they may see."""
    key = key_of(name)
    if not key:
        return None
    row = db.scalar(select(Person).where(Person.key == key))
    if row is not None:
        return row
    alias = db.scalar(select(PersonAlias).where(PersonAlias.key == key))
    if alias is None:
        return None
    if library_ids is not None and alias.id not in {found.id for found in _alias_rows(db, [alias.person_id],
                                                                                         library_ids)}:
        return None
    return db.get(Person, alias.person_id)


def canonical_names(db: Session, names: list[str]) -> list[str]:
    """The names with every alias turned into the name of the person it is an alias of (the others stay)."""
    found = []
    for name in names:
        person = person_for(db, name) if name else None
        found.append(person.name if person is not None else name)
    return found


def aliases_of(db: Session, person_ids: list[int], library_ids: Any = None) -> dict[int, list[str]]:
    """The other names of people, by person. With ``library_ids`` (a list or a subquery of the libraries a person
    sees), only the names whose evidence comes from a book of those libraries: a name out of the tags of a book
    somebody cannot see is not shown to them."""
    found = _alias_rows(db, person_ids, library_ids)
    result: dict[int, list[str]] = {}
    for row in found:
        result.setdefault(row.person_id, []).append(row.name)
    return result


def alias_rows(db: Session, person_ids: list[int], library_ids: Any = None) -> list[PersonAlias]:
    return _alias_rows(db, person_ids, library_ids)


def _alias_rows(db: Session, person_ids: list[int], library_ids: Any) -> list[PersonAlias]:
    if not person_ids:
        return []
    rows = list(db.scalars(select(PersonAlias).where(PersonAlias.person_id.in_(person_ids)).order_by(PersonAlias.id)))
    if library_ids is None or not rows:
        return rows
    keys = {row.key for row in rows}
    seen: set[str] = set()
    for key_a, key_b in db.execute(
            select(AliasClaim.key_a, AliasClaim.key_b).join(Edition, Edition.id == AliasClaim.edition_id).where(
                AliasClaim.apart.is_(False), Edition.library_id.in_(library_ids), Edition.missing_at.is_(None),
                (AliasClaim.key_a.in_(keys)) | (AliasClaim.key_b.in_(keys)))):
        seen.update((key_a, key_b))
    # A name that is the same name written differently, or that a person joined by hand, rests on no book: it is shown
    # with the person to whoever sees a book of that person (it tells nothing about a book one cannot see).
    bookless = {row.person_id for row in rows if row.source in BOOKLESS_SOURCES}
    visible = set(db.scalars(select(Credit.person_id).join(Edition, Edition.id == Credit.edition_id).where(
        Credit.person_id.in_(bookless), Edition.library_id.in_(library_ids), Edition.missing_at.is_(None)))
    ) if bookless else set()
    return [row for row in rows if row.key in seen or (row.source in BOOKLESS_SOURCES and row.person_id in visible)]


# ---- Evidence -------------------------------------------------------------------------------------------------------


def _differ(name_a: str, name_b: str) -> bool:
    key_a, key_b = key_of(name_a), key_of(name_b)
    return bool(key_a and key_b and key_a != key_b and not authors_compatible(name_a, name_b))


def _add(db: Session, edition_id: int, pen: str, other: str, source: str) -> None:
    db.add(AliasClaim(name_a=pen[:200], key_a=key_of(pen), name_b=other[:200], key_b=key_of(other), source=source,
                      edition_id=edition_id, apart=False, created_at=clock.now()))


def note_tags(db: Session, edition: Edition, pairs: list[tuple[str, str]]) -> bool:
    """The evidence the tags of an edition give (``A schreibt als B``: ``B`` is a name of ``A``), made anew. Returns
    whether it changed."""
    wanted = {(other, one) for one, other in pairs if _differ(one, other)}
    have = {(row.name_a, row.name_b) for row in db.scalars(select(AliasClaim).where(
        AliasClaim.edition_id == edition.id, AliasClaim.source == TAGS))}
    if wanted == have:
        return False
    db.execute(delete(AliasClaim).where(AliasClaim.edition_id == edition.id, AliasClaim.source == TAGS))
    for pen, real in sorted(wanted):
        _add(db, edition.id, pen, real, TAGS)
    db.flush()
    return True


def drop_match_evidence(db: Session, edition_id: int) -> bool:
    """The evidence a match of an edition made goes (the match is taken back, or made anew). Returns whether there was
    any."""
    gone = db.execute(delete(AliasClaim).where(AliasClaim.edition_id == edition_id,
                                               AliasClaim.source.in_(MATCH_SOURCES))).rowcount
    return bool(gone)


def named_authors(db: Session, edition: Edition) -> dict[str, str]:
    """The names an edition's files give in an author field, by their key: the creators of an e-book's package (not a
    PDF's), the authors of a ``metadata.json`` or a ``metadata.opf`` beside it, and the ``author`` tag of a recording
    (``authors_field``). The artist of a recording is no author field."""
    from . import scanner

    rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK,
                                                  BookFile.missing_at.is_(None))))
    found: dict[str, str] = {}
    probes: list[dict[str, Any]] = []
    for row in rows:
        probe = row.probe if isinstance(row.probe, dict) else {}
        if row.format in _AUTHOR_FIELD_FORMATS or (edition.kind == EDITION_AUDIO and probe.get("authors_field")):
            probes.append(probe)
    if rows:
        for side in scanner._sidecars_for(db, edition.library_id, rows[0].unit):
            if side.format in ("json", "opf") and isinstance(side.probe, dict):
                probes.append(side.probe)
    for probe in probes:
        for name in split_authors(probe.get("authors") or []):
            if key_of(name):
                found.setdefault(key_of(name), name)
    return found


def note_match(db: Session, edition: Edition, best: Any, support: list[Any] | None = None,
               ranked: list[Any] | None = None) -> None:
    """The sources were asked about an edition: the evidence their answer gives that the file's author and the source's
    author are one person, made anew (the evidence of the answer before goes).

    * Hardcover named the file's author as another name of the author of a book of the same title (``author_aliases``,
      kept on a candidate only when Hardcover knows exactly one author of that name), among the answers that can be the
      book (``ranked``, or the match). The word is about the author: whether the book is then sure does not matter.
    * The ISBN or the ASIN of the file settled the match (``best``, ``certain``), the source has the same title and
      names another author.

    Either way: one author on each side, the file's name in an author field of the files, and none of the narrators.
    ``best`` is None when nothing was settled."""
    drop_match_evidence(db, edition.id)
    # The name as the files give it (the credit may already lead to the person it is another name of).
    named = named_authors(db, edition)
    if len(named) != 1:
        db.flush()
        return
    mine = next(iter(named.values()))
    narrated = list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
        Credit.edition_id == edition.id, Credit.role == NARRATOR)))
    looked_at = [item for item in (ranked if ranked is not None else [best, *(support or [])])
                 if item is not None and not getattr(item, "fatal", "")]
    if best is not None and best not in looked_at:
        looked_at.insert(0, best)
    for scored in looked_at:
        candidate = scored.candidate
        if len(candidate.authors) != 1:
            continue
        theirs = candidate.authors[0]
        if not _differ(mine, theirs):
            continue
        voices = [*candidate.narrators, *narrated]
        if any(authors_compatible(voice, name) or key_of(voice) == key_of(name)
               for voice in voices for name in (mine, theirs)):
            continue
        if candidate.source == "hardcover" and titles_alike(edition.title, candidate.title) and any(
                authors_compatible(alt, mine) or key_of(alt) == key_of(mine) for alt in candidate.author_aliases):
            _add(db, edition.id, mine, theirs, HARDCOVER)
        elif best is not None and scored is best and getattr(scored, "certain", False) and titles_alike(
                edition.title, candidate.title):
            same_asin = bool(candidate.asin and edition.asin and candidate.asin.upper() == edition.asin.upper())
            _add(db, edition.id, mine, theirs, ASIN if same_asin else ISBN)
    db.flush()


def keep_apart(db: Session, name_a: str, name_b: str, by: int | None) -> None:
    """A person said two names are two people: kept for good, and nothing joins them again."""
    key_a, key_b = sorted((key_of(name_a), key_of(name_b)))
    if db.scalar(select(AliasClaim.id).where(AliasClaim.apart.is_(True), AliasClaim.key_a == key_a,
                                             AliasClaim.key_b == key_b)) is None:
        first, second = (name_a, name_b) if key_of(name_a) == key_a else (name_b, name_a)
        db.add(AliasClaim(name_a=first[:200], key_a=key_a, name_b=second[:200], key_b=key_b, source=HAND,
                          edition_id=None, apart=True, created_by=by, created_at=clock.now()))
        db.flush()


# ---- What follows from the evidence ---------------------------------------------------------------------------------


def _proven_pairs(db: Session) -> dict[frozenset[str], dict[str, Any]]:
    """The pairs of names the evidence proves, with the names and which side said what. A pair is ``strong`` when a book
    says it (``real`` is the name of the author, the other the pen name); one the spelling makes or a person made by
    hand rests on no book (``by``) and is the same person either way round (``prefer`` is the name a person chose to
    keep)."""
    rows = db.execute(select(AliasClaim, Edition.missing_at).outerjoin(Edition, Edition.id == AliasClaim.edition_id)
                      ).all()
    apart = {frozenset((row.key_a, row.key_b)) for row, _missing in rows if row.apart}
    pairs: dict[frozenset[str], dict[str, Any]] = {}
    for row, missing in rows:
        if row.apart or missing is not None:
            continue
        if row.edition_id is None and row.source not in BOOKLESS_SOURCES:
            continue
        pair = frozenset((row.key_a, row.key_b))
        if len(pair) < 2 or pair in apart:
            continue
        item = pairs.setdefault(pair, {"names": {}, "books": defaultdict(set), "real": set(), "by": set(),
                                       "prefer": set()})
        item["names"].setdefault(row.key_a, row.name_a)
        item["names"].setdefault(row.key_b, row.name_b)
        if row.edition_id is None:
            item["by"].add(row.source)
            if row.source == HAND:
                item["prefer"].add(row.key_b)
        else:
            item["books"][row.source].add(row.edition_id)
            item["real"].add(row.key_b)
    kept: dict[frozenset[str], dict[str, Any]] = {}
    for pair, item in pairs.items():
        item["strong"] = bool(item["books"].get(TAGS) or item["books"].get(HARDCOVER)
                              or len(item["books"].get(ISBN, set()) | item["books"].get(ASIN, set())) >= ISBN_BOOKS)
        if item["strong"] or item["by"]:
            kept[pair] = item
    return kept


def _source_of(item: dict[str, Any]) -> str:
    if item["strong"]:
        return next((name for name in (TAGS, HARDCOVER, ISBN, ASIN) if item["books"].get(name)), "")
    return next((name for name in BOOKLESS_SOURCES if name in item["by"]), "")


def weight(db: Session, person: Person) -> tuple[int, int, int]:
    """How much a person weighs when two are one: the e-books they are an author of, all their credits, the older id."""
    ebooks = db.scalar(select(func.count(Credit.id)).join(Edition, Edition.id == Credit.edition_id).where(
        Credit.person_id == person.id, Credit.role == AUTHOR, Edition.kind == EDITION_EBOOK)) or 0
    credits = db.scalar(select(func.count(Credit.id)).where(Credit.person_id == person.id)) or 0
    return int(ebooks), int(credits), -person.id


def _author_books(db: Session, person: Person) -> int:
    """How many books the person is an author of: the files that carry the name, when two spellings are one person."""
    return int(db.scalar(select(func.count(func.distinct(Credit.edition_id))).join(
        Edition, Edition.id == Credit.edition_id).where(
        Credit.person_id == person.id, Credit.role == AUTHOR, Edition.missing_at.is_(None))) or 0)


def _centre(keys: set[str], members: list[frozenset[str]], pairs: dict[frozenset[str], dict[str, Any]]
            ) -> set[str] | None:
    """The names among ``keys`` the person of the group is one of, or None when the evidence proves nothing.

    One author with several names: one name (the real one) is on the real side of every pair a book proves; a chain of
    names through a third (a studio, a narrator) proves nothing. The pairs that rest on no book (the same name written
    differently, joined by hand) make the names on both sides one before that is looked at, and are never a chain."""
    strong = [pair for pair in members if pairs[pair]["strong"]]
    if len(keys) <= 2 or not strong:
        return set(keys)
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for pair in members:
        if not pairs[pair]["strong"]:
            one, other = sorted(pair)
            parent[find(one)] = find(other)
    common: set[str] | None = None
    for pair in strong:
        real = {find(key) for key in pairs[pair]["real"] if key in pair}
        common = real if common is None else common & real
        if not common:
            return None
    node = min(common or set())
    return {key for key in keys if find(key) == node}


def _owner(db: Session, node: set[str], keys: set[str], members: list[frozenset[str]],
           pairs: dict[frozenset[str], dict[str, Any]], people: dict[str, Person],
           current: dict[str, int]) -> Person | None:
    """The person the other names of a group lead to: the one a person chose to keep when they joined two by hand; else
    the person the names lead to already; else, for the same two words the other way round, the spelling most of the
    books have; else the heavier."""
    candidates = {key: person for key, person in people.items() if key in node}
    if not candidates:
        owner_id = next((current[key] for key in sorted(keys) if key in current), None)
        owner = db.get(Person, owner_id) if owner_id is not None else None
        return owner if owner is not None and owner.key in keys else None
    chosen = sorted(key for pair in members for key in pairs[pair]["prefer"] if key in candidates)
    if chosen:
        return candidates[chosen[0]]
    led = {current[key] for key in node if key in current}
    if len(led) == 1:
        person = db.get(Person, next(iter(led)))
        if person is not None and person.key in candidates:
            return person
    swapped: set[str] = set()
    for pair in members:
        names = pairs[pair]["names"]
        if SPELLING in pairs[pair]["by"] and name_relation(*(names[key] for key in sorted(pair))) == "swap":
            swapped |= pair & candidates.keys()
    if swapped:
        return max((candidates[key] for key in sorted(swapped)),
                   key=lambda person: (_author_books(db, person), *weight(db, person)))
    return max(candidates.values(), key=lambda person: weight(db, person))


def _wanted(db: Session) -> dict[str, tuple[int, str, str]]:
    """``alias key -> (person id, name, source)``: what the evidence says should lead where now."""
    pairs = _proven_pairs(db)
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for pair in pairs:
        a, b = sorted(pair)
        parent[find(a)] = find(b)
    groups: dict[str, list[frozenset[str]]] = defaultdict(list)
    for pair in pairs:
        groups[find(next(iter(pair)))].append(pair)
    current = {row.key: row.person_id for row in db.scalars(select(PersonAlias))}
    wanted: dict[str, tuple[int, str, str]] = {}
    for found in groups.values():
        members = sorted(found, key=sorted)
        keys = set().union(*members)
        names: dict[str, str] = {}
        for pair in members:
            names.update(pairs[pair]["names"])
        people = {key: person for key in keys if (person := db.scalar(select(Person).where(Person.key == key)))}
        node = _centre(keys, members, pairs)
        if node is None:
            logger.info("Pen names left alone: the evidence links more than two names in a chain")
            continue
        owner = _owner(db, node, keys, members, pairs, people, current)
        if owner is None:
            continue
        for key in keys:
            if key != owner.key:
                source = next((_source_of(pairs[pair]) for pair in members if key in pair), "")
                wanted[key] = (owner.id, names.get(key, key), source)
    return wanted


def recompute(db: Session) -> bool:
    """Brings the other names of people in line with the evidence: names whose evidence is gone are people of their
    own again (their books put together anew, and moved out of works by another author), names newly proven lead to
    their person (the two people become one). Returns whether anything changed; the caller commits."""
    wanted = _wanted(db)
    current = {row.key: row for row in db.scalars(select(PersonAlias))}
    loosened: set[int] = set()
    for key, row in current.items():
        target = wanted.get(key)
        if target is None or target[0] != row.person_id:
            loosened.add(row.person_id)
            db.delete(row)
    db.flush()
    touched: set[int] = set()
    for key, (owner_id, name, source) in sorted(wanted.items()):
        row = current.get(key)
        if row is not None and row.person_id == owner_id:
            continue
        owner = db.get(Person, owner_id)
        if owner is None:
            continue
        other = db.scalar(select(Person).where(Person.key == key))
        if other is not None and other.id != owner.id:
            merge_people(db, owner, other)
        db.add(PersonAlias(person_id=owner.id, name=name[:200], key=key, source=source[:16], created_at=clock.now()))
        db.flush()
        touched.add(owner.id)
        logger.info("Pen name joined source=%s", source)
    if loosened:
        _loosen(db, loosened)
    if touched:
        regroup(db, touched)
    return bool(loosened or touched)


def _loosen(db: Session, person_ids: set[int]) -> None:
    """The books of people that lost another name are put together anew from their files (the name that is no alias
    any more is a person of its own then), and leave works that are not by their author."""
    from .match import apply

    edition_ids = sorted(set(db.scalars(select(Credit.edition_id).where(
        Credit.person_id.in_(person_ids), Credit.edition_id.is_not(None)))))
    for edition_id in edition_ids:
        edition = db.get(Edition, edition_id)
        if edition is not None:
            apply.rebuild(db, edition)
    db.flush()
    separate_works(db, edition_ids)
    from . import people_info

    # The photo and the text of each are their own again (never the ones found for the other name).
    people_info.keep_own(db, person_ids)
    logger.info("Pen names taken back people=%s editions=%s", len(person_ids), len(edition_ids))


def merge_people(db: Session, keep: Person, drop: Person) -> list[int]:
    """The credits of ``drop`` are the credits of ``keep``, the names that led to it lead to ``keep``, and it is gone.
    Returns the editions whose people changed (their search entries are made anew)."""
    if keep.id == drop.id:
        return []
    from . import people_info

    # What was learned about the authors (photo, short text) goes with them, the sure one first.
    people_info.take_over(db, keep.id, drop.id)
    taken = {(c.edition_id, c.work_id, c.role) for c in db.scalars(select(Credit).where(Credit.person_id == keep.id))}
    editions: set[int] = set()
    for credit in list(db.scalars(select(Credit).where(Credit.person_id == drop.id))):
        if credit.edition_id is not None:
            editions.add(credit.edition_id)
        if (credit.edition_id, credit.work_id, credit.role) in taken:
            db.delete(credit)
        else:
            credit.person_id = keep.id
    db.flush()
    for row in db.scalars(select(PersonAlias).where(PersonAlias.person_id == drop.id)):
        row.person_id = keep.id
    db.flush()
    db.delete(drop)
    db.flush()
    search_index.update(db, sorted(editions))
    return sorted(editions)


def _authors_of(db: Session, edition_id: int) -> set[int]:
    return set(db.scalars(select(Credit.person_id).where(Credit.edition_id == edition_id, Credit.role == AUTHOR)))


def separate_works(db: Session, edition_ids: list[int]) -> int:
    """A book that stands in a work whose lead book is by somebody else entirely (no author in common), and that was not
    placed there by a person or a source, leaves: for the work of its own author with the same title, else a work of
    its own. Returns how many books moved."""
    from . import catalog
    from .match import apply

    moved = 0
    works = sorted({edition.work_id for ident in edition_ids if (edition := db.get(Edition, ident)) is not None})
    for work_id in works:
        editions = sorted(db.scalars(select(Edition).where(Edition.work_id == work_id)), key=catalog.edition_order)
        if len(editions) < 2:
            continue
        lead_people = _authors_of(db, editions[0].id)
        if not lead_people:
            continue
        work = db.get(Work, work_id)
        for edition in editions[1:]:
            mine = _authors_of(db, edition.id)
            if not mine or mine & lead_people or edition.grouped_by in ("hand", "auto", "wish"):
                continue
            target = None
            for other_id in sorted(set(db.scalars(select(Credit.work_id).where(
                    Credit.person_id.in_(mine), Credit.role == AUTHOR, Credit.work_id.is_not(None),
                    Credit.work_id != work_id)))):
                other = db.get(Work, other_id)
                if other is not None and work is not None and catalog.same_title(other, work):
                    target = other
                    break
            if target is None:
                now = clock.now()
                target = Work(title=edition.title[:300], added_at=now, updated_at=now)
                db.add(target)
                db.flush()
                from . import genres

                target.pattern, target.tall, target.gold = genres.style_of(target.id)
            apply.move(db, edition, target.id, "")
            moved += 1
    return moved


def regroup(db: Session, person_ids: set[int]) -> int:
    """Works of one person with the same title are one work: the older one stays. Never an edition a person placed (the
    work with such an edition is left alone, and nothing joins it). Returns how many works went into another."""
    from . import catalog

    moved = 0
    for person_id in sorted(person_ids):
        work_ids = sorted(set(db.scalars(select(Credit.work_id).where(
            Credit.person_id == person_id, Credit.role == AUTHOR, Credit.work_id.is_not(None)))))
        kept: list[Work] = []
        for work_id in work_ids:
            work = db.get(Work, work_id)
            if work is None:
                continue
            editions = list(db.scalars(select(Edition).where(Edition.work_id == work.id)))
            if not editions or any(edition.grouped_by == "hand" for edition in editions):
                # A work with an edition a person placed is left as it is, and nothing joins it: the person split or
                # joined it on purpose.
                continue
            target = next((other for other in kept if catalog.same_title(work, other)), None)
            if target is None:
                kept.append(work)
                continue
            for edition in editions:
                edition.work_id = target.id
            db.flush()
            db.execute(delete(Credit).where(Credit.work_id == work.id, Credit.edition_id.is_(None)))
            db.delete(work)
            db.flush()
            catalog.refresh_work(db, target)
            moved += 1
    return moved


# ---- The way back, by hand ------------------------------------------------------------------------------------------


def affected_libraries(db: Session, person: Person) -> set[int]:
    """The libraries whose books a split of ``person`` touches: those of the books credited to the person, and of the
    books whose evidence named one of its other names."""
    found = set(db.scalars(select(Edition.library_id).join(Credit, Credit.edition_id == Edition.id).where(
        Credit.person_id == person.id)))
    keys = [row.key for row in db.scalars(select(PersonAlias).where(PersonAlias.person_id == person.id))]
    if keys:
        found |= set(db.scalars(select(Edition.library_id).join(AliasClaim, AliasClaim.edition_id == Edition.id).where(
            AliasClaim.apart.is_(False), (AliasClaim.key_a.in_(keys)) | (AliasClaim.key_b.in_(keys)))))
    return found


def split(db: Session, person: Person, alias: PersonAlias, by: int | None) -> None:
    """A person says that the other name ``alias`` is somebody else: kept for good, and the two are two people again
    (the books of the name go back to it). The caller commits."""
    keep_apart(db, alias.name, person.name, by)
    # Every name of the group the alias is proven to share with the person (also the names the spelling puts together
    # with it, and the join a person made by hand, which is taken back with this).
    for row in list(db.scalars(select(AliasClaim).where(AliasClaim.apart.is_(False), (
            (AliasClaim.key_a == alias.key) | (AliasClaim.key_b == alias.key))))):
        other_key = row.key_b if row.key_a == alias.key else row.key_a
        other_name = row.name_b if row.key_a == alias.key else row.name_a
        if other_key == person.key or db.scalar(select(PersonAlias.id).where(
                PersonAlias.key == other_key, PersonAlias.person_id == person.id)) is not None:
            keep_apart(db, alias.name, other_name, by)
            if row.source in BOOKLESS_SOURCES and row.edition_id is None:
                db.delete(row)
    db.flush()
    recompute(db)
    logger.info("Pen name split by hand person=%s", person.id)



def merge_by_hand(db: Session, keep: Person, other: Person, by: int | None) -> None:
    """A person says that two people are one author, and which of the two names stays (``keep``): the evidence of that
    is kept until the person splits them again (``split``). A person who says so overrules an earlier split of these two
    names by anybody. The caller commits."""
    if keep.id == other.id:
        return
    db.execute(delete(AliasClaim).where(AliasClaim.apart.is_(True), (
        (AliasClaim.key_a == keep.key) & (AliasClaim.key_b == other.key)) | (
        (AliasClaim.key_a == other.key) & (AliasClaim.key_b == keep.key))))
    if db.scalar(select(AliasClaim.id).where(
            AliasClaim.apart.is_(False), AliasClaim.source == HAND, AliasClaim.edition_id.is_(None), (
            (AliasClaim.key_a == other.key) & (AliasClaim.key_b == keep.key)))) is None:
        db.add(AliasClaim(name_a=other.name[:200], key_a=other.key, name_b=keep.name[:200], key_b=keep.key, source=HAND,
                          edition_id=None, apart=False, created_by=by, created_at=clock.now()))
    db.flush()
    recompute(db)
    logger.info("Authors joined by hand keep=%s other=%s", keep.id, other.id)
