"""Names that are one author by how they are written, and nothing else: no evidence of the files is needed for them.

Two people of the library are put together when the names alone carry it (see ``readers.names.name_relation``):

* **swapped**: the same two words the other way round (``Rune Falkner`` and ``Falkner Rune``);
* **peers**: one name in two spellings (``Alexander Brandt`` and ``Alexandre Brandt``, ``Lew Falkner`` and ``Leo
  Falkner``);
* **shorter and longer**: one name gives less of the first name and fits the other (``A.K. Feldkamp`` and ``Ada K.
  Feldkamp``), **but only when there is exactly one longer name it fits**. ``R. Falkner`` beside ``Rune Falkner`` and
  ``Ralf Falkner`` fits two people and is put together with neither. The longer names are counted as people: two names
  that are one person already (or are put together by the first two rules) count once.

Nothing is joined that a person said is two people (``apart``, kept for good), nor two names credited on one book (the
authors of a book are different people), nor a name that is no first-and-last name of a person, nor two names that do
not stand in the same libraries: one person has one name, and a member of the one library would read it off the other.

The pairs are kept as pieces of evidence without a book (``AliasClaim`` with the source ``spelling``) and made anew
whenever the scan has settled (``note_spelling``); ``aliases.recompute`` works out what follows from them, like from any
other evidence. A name that stops fitting (a second longer name came in) is a person of its own again by itself.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, aliased

from .. import clock
from ..models import AUTHOR, AliasClaim, Credit, Edition, Person, PersonAlias
from .readers.names import last_name_buckets, name_relation

logger = logging.getLogger("nexbooks.spelling")

#: The source of the claims this module makes.
SPELLING = "spelling"
#: Names with one last name are compared two by two, so that a last name that many names share costs the square of them,
#: after every scan, and anybody who may add books can make up the names. A last name shared by more names than this
#: is left alone (a real library has few such names, and the authors in them are put together by a person).
MAX_BUCKET = 64


@dataclass(frozen=True)
class Name:
    """A name of the library: the name of a person, or another name that leads to one. ``group`` is the person it
    belongs to."""

    key: str
    name: str
    group: int


def spelling_pairs(names: Sequence[Name], apart: set[frozenset[str]] | None = None,
                   together: set[frozenset[int]] | None = None,
                   shelves: Mapping[int, frozenset[int]] | None = None) -> set[frozenset[str]]:
    """The pairs of keys of ``names`` that are one author by their spelling. ``apart`` are the pairs of keys a person
    said are two people, ``together`` the pairs of groups credited on one book, ``shelves`` the libraries of each group
    (without it, all stand in one)."""
    apart = apart or set()
    together = together or set()
    shelves = shelves or {}
    buckets: dict[str, list[Name]] = {}
    for name in names:
        for bucket in last_name_buckets(name.name):
            buckets.setdefault(bucket, []).append(name)
    found: set[frozenset[str]] = set()
    shorter: dict[str, list[Name]] = {}
    seen: set[frozenset[str]] = set()
    left_alone = [len(members) for members in buckets.values() if len(members) > MAX_BUCKET]
    if left_alone:
        logger.warning("Names left alone by spelling: %s last names are shared by more than %s names (the most: %s)",
                       len(left_alone), MAX_BUCKET, max(left_alone))
    for members in buckets.values():
        if len(members) > MAX_BUCKET:
            continue
        for index, one in enumerate(members):
            for other in members[index + 1:]:
                pair = frozenset((one.key, other.key))
                if len(pair) < 2 or pair in seen:
                    continue
                seen.add(pair)
                if pair in apart or (one.group != other.group and frozenset((one.group, other.group)) in together):
                    continue
                if shelves.get(one.group) != shelves.get(other.group):
                    continue
                relation = name_relation(one.name, other.name)
                if relation in ("swap", "peer"):
                    found.add(pair)
                elif relation == "a_shorter":
                    shorter.setdefault(one.key, []).append(other)
                elif relation == "b_shorter":
                    shorter.setdefault(other.key, []).append(one)
    group_of = {name.key: name.group for name in names}
    parent: dict[int, int] = {}

    def find(group: int) -> int:
        while parent.setdefault(group, group) != group:
            parent[group] = parent[parent[group]]
            group = parent[group]
        return group

    for pair in found:
        one_group, other_group = (group_of[key] for key in pair)
        parent[find(one_group)] = find(other_group)
    # A shorter name that fits one person only is that person's; the people it makes of names that were several before
    # can make another shorter name fit one only, so this goes on until nothing changes.
    for _round in range(6):
        changed = False
        for key in sorted(shorter):
            roots = {find(longer.group) for longer in shorter[key]}
            if len(roots) == 1 and find(group_of[key]) not in roots:
                parent[find(group_of[key])] = next(iter(roots))
                changed = True
        if not changed:
            break
    for key, longer in shorter.items():
        if len({find(name.group) for name in longer}) == 1:
            found.update(frozenset((key, name.key)) for name in longer)
    return found


def _names(db: Session) -> list[Name]:
    """The names of the authors of the library: every person with a book, and the other names that lead to them."""
    authors = select(Credit.person_id).join(Edition, Edition.id == Credit.edition_id).where(
        Credit.role == AUTHOR, Edition.missing_at.is_(None))
    found = [Name(key=row.key, name=row.name, group=row.id) for row in db.scalars(
        select(Person).where(Person.id.in_(authors)).order_by(Person.id))]
    present = {name.group for name in found}
    found.extend(Name(key=row.key, name=row.name, group=row.person_id) for row in db.scalars(
        select(PersonAlias).where(PersonAlias.person_id.in_(present)).order_by(PersonAlias.id)))
    return found


def _together(db: Session) -> set[frozenset[int]]:
    """The pairs of people credited as authors on one book: two people, whatever their names look like."""
    one, other = aliased(Credit), aliased(Credit)
    return {frozenset((first, second)) for first, second in db.execute(
        select(one.person_id, other.person_id).join(other, other.edition_id == one.edition_id).where(
            one.role == AUTHOR, other.role == AUTHOR, one.person_id < other.person_id,
            one.edition_id.is_not(None)).distinct())}


def _shelves(db: Session) -> dict[int, frozenset[int]]:
    """The libraries each author has a book in."""
    found: dict[int, set[int]] = {}
    for person, library in db.execute(
            select(Credit.person_id, Edition.library_id).join(Edition, Edition.id == Credit.edition_id).where(
                Credit.role == AUTHOR, Edition.missing_at.is_(None)).distinct()):
        found.setdefault(person, set()).add(library)
    return {person: frozenset(libraries) for person, libraries in found.items()}


def apart_pairs(db: Session) -> set[frozenset[str]]:
    return {frozenset((key_a, key_b)) for key_a, key_b in db.execute(
        select(AliasClaim.key_a, AliasClaim.key_b).where(AliasClaim.apart.is_(True)))}


def current_pairs(db: Session) -> set[frozenset[str]]:
    """The pairs the names of the library give now."""
    return spelling_pairs(_names(db), apart_pairs(db), _together(db), _shelves(db))


def note_spelling(db: Session) -> bool:
    """Makes the claims of the spelling anew from the names of the library. Returns whether they changed; the caller
    works out what follows (``aliases.recompute``) and commits."""
    names = {name.key: name for name in _names(db)}
    wanted = spelling_pairs(list(names.values()), apart_pairs(db), _together(db), _shelves(db))
    have = {row.id: frozenset((row.key_a, row.key_b)) for row in db.scalars(
        select(AliasClaim).where(AliasClaim.source == SPELLING, AliasClaim.apart.is_(False)))}
    stale = [ident for ident, pair in have.items() if pair not in wanted]
    if stale:
        db.execute(delete(AliasClaim).where(AliasClaim.id.in_(stale)))
    kept = {pair for ident, pair in have.items() if ident not in stale}
    added = 0
    for pair in sorted(wanted - kept, key=sorted):
        key_a, key_b = sorted(pair)
        db.add(AliasClaim(name_a=names[key_a].name[:200], key_a=key_a, name_b=names[key_b].name[:200], key_b=key_b,
                          source=SPELLING, edition_id=None, apart=False, created_at=clock.now()))
        added += 1
    if stale or added:
        db.flush()
        logger.info("Names put together by spelling pairs=%s added=%s gone=%s", len(wanted), added, len(stale))
    return bool(stale or added)
