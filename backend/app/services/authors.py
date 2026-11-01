"""The authors of the library as one person sees them: the grid of photos, the photo of one author and their short
biography.

**Only authors with books the person sees.** The list starts from the credits of the editions the person may see
(``works.visible_edition_clause``), so an author whose books all lie in libraries the person is not a member of
is not in it, is not counted, and has no page and no photo for them: a page, a photo or a count of such an
author answers 404, like one that does not exist. The numbers on a card (the works, the works with a recording)
count the editions of the person's libraries only.

**Photos.** A photo from the author's folder in a library the person is a member of comes first; then the photo
the sources gave (``services/people_info.py``), which is public data. Whose photo it is (author and licence)
goes with it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import AUTHOR, EDITION_AUDIO, Credit, Edition, Person, PersonInfo, PersonPhoto, Work
from . import aliases, people_info, photos
from .discover import owned
from .libraries import member_library_ids
from .works import _first_added, _shown_genre, visible_edition_clause

SORTS = ("name", "count", "recent")
MAX_PER_PAGE = 200


@dataclass
class Card:
    person: Person
    works: int = 0
    audio: int = 0
    added: datetime | None = None
    genres: Counter[str] = field(default_factory=Counter)
    photo: str = ""

    @property
    def genre(self) -> str:
        """The genre most of their books are (the first of equals in the order of the shelf), empty for none."""
        counts = {genre: n for genre, n in self.genres.items() if genre}
        if not counts:
            return ""
        best = max(counts.values())
        return min(genre for genre, n in counts.items() if n == best)


def _rows(db: Session, uid: int) -> list[tuple[int, int, str, datetime | None, bool]]:
    """``(person, work, genre, added, has_recording)`` for every author and every book of theirs the person sees."""
    pairs = (select(Credit.person_id.label("person"), Edition.work_id.label("work"))
             .join(Edition, Edition.id == Credit.edition_id)
             .where(Credit.role == AUTHOR, *visible_edition_clause(uid)).distinct().subquery("pairs"))
    recording = exists().where(Edition.work_id == Work.id, Edition.kind == EDITION_AUDIO,
                               *visible_edition_clause(uid))
    query = (select(pairs.c.person, Work.id, _shown_genre(uid), _first_added(uid), recording)
             .join(Work, Work.id == pairs.c.work))
    return [(int(p), int(w), str(g or ""), a, bool(r)) for p, w, g, a, r in db.execute(query)]


@dataclass
class _Totals:
    works: int = 0
    audio: int = 0
    added: datetime | None = None
    genres: Counter[str] = field(default_factory=Counter)


def _totals(db: Session, uid: int) -> dict[int, _Totals]:
    """The numbers of every author the person sees, by the id of the author (an author object lives in a session)."""
    found: dict[int, _Totals] = {}
    for person, _work, genre, added, recording in _rows(db, uid):
        total = found.setdefault(person, _Totals())
        total.works += 1
        total.audio += 1 if recording else 0
        total.genres[genre] += 1
        if added is not None and (total.added is None or added > total.added):
            total.added = added
    return found


def cards(db: Session, uid: int) -> dict[int, Card]:
    """Every author the person sees, by id, with the numbers of their books the person sees. The numbers are kept
    between two requests, like the shelf of a person (``discover.owned.keep``), until a book, an author or a membership
    changes."""
    totals: dict[int, _Totals] = owned.keep(db, uid, "authors", lambda: _totals(db, uid))
    people = {p.id: p for p in db.scalars(select(Person).where(Person.id.in_(list(totals))))} if totals else {}
    return {person: Card(person=people[person], works=total.works, audio=total.audio, added=total.added,
                         genres=Counter(total.genres)) for person, total in totals.items() if person in people}


def listing(db: Session, uid: int, *, sort: str, genre: str | None, query: str, page: int,
            per_page: int) -> tuple[list[Card], int, dict[str, int]]:
    """A page of the authors the person sees, the number of authors that fit, and the number of authors for each genre
    (before the genre filter, so that the chips keep their numbers)."""
    everyone = list(cards(db, uid).values())
    by_genre: Counter[str] = Counter()
    for card in everyone:
        for name in {g or "none" for g in card.genres}:
            by_genre[name] += 1
    wanted = " ".join(query.casefold().split())
    # A person is found by their other names too (a pen name proved by evidence), but only by a name whose evidence
    # lies in a library the caller is a member of: a search never tells that a name occurs in a book one cannot see.
    other_names = aliases.aliases_of(db, [c.person.id for c in everyone], member_library_ids(uid)) if wanted else {}
    fitting = [c for c in everyone if (not wanted or any(
        wanted in name.casefold() for name in [c.person.name, *other_names.get(c.person.id, [])])) and (
        genre is None or (genre in {g or "none" for g in c.genres}))]
    if sort == "count":
        fitting.sort(key=lambda c: (-c.works, c.person.sort_name.casefold(), c.person.id))
    elif sort == "recent":
        fitting.sort(key=lambda c: (-(c.added.timestamp() if c.added else 0), c.person.sort_name.casefold(),
                                    c.person.id))
    else:
        fitting.sort(key=lambda c: (c.person.sort_name.casefold(), c.person.id))
    total = len(fitting)
    shown = fitting[(page - 1) * per_page: page * per_page]
    for card, photo in zip(shown, [photo_of(db, uid, c.person.id) for c in shown], strict=True):
        card.photo = photo[0] if photo else ""
    return shown, total, dict(by_genre)


def can_see(db: Session, uid: int, person_id: int) -> bool:
    """Whether the person sees any book the author wrote or narrated (the author's page exists for them)."""
    return db.scalar(select(exists().where(
        Credit.person_id == person_id, Credit.edition_id.is_not(None),
        Credit.edition_id.in_(select(Edition.id).where(*visible_edition_clause(uid)))))) is True


def photo_of(db: Session, uid: int, person_id: int) -> tuple[str, str] | None:
    """The photo the person is shown of an author: ``(file name, where it came from)``, ``folder`` or the source's name;
    None for no photo. The photo of a folder counts only for a member of its library."""
    folder = db.scalar(select(PersonPhoto.photo).where(
        PersonPhoto.person_id == person_id, PersonPhoto.library_id.in_(member_library_ids(uid)))
        .order_by(PersonPhoto.library_id).limit(1))
    if folder and photos.path_of(folder) is not None:
        return folder, "folder"
    info = db.get(PersonInfo, person_id)
    if (info is not None and info.photo and info.photo_source in people_info.PHOTO_SOURCES
            and photos.path_of(info.photo) is not None):
        return info.photo, info.photo_source
    return None


def photo_file(db: Session, uid: int, person_id: int) -> tuple[str, Path]:
    """The photo file for a person who may see the author; 404
    for anything else, alike for an author who has no photo and
    one the person does not see."""
    found = photo_of(db, uid, person_id) if can_see(db, uid, person_id) else None
    path = photos.path_of(found[0]) if found else None
    if found is None or path is None:
        raise error("not_found", "Not found.", 404)
    return found[0], path


def photo_url(person_id: int, name: str) -> str:
    return f"/api/v1/people/{person_id}/photo?v={name[:12]}"


@dataclass
class Credit_:
    """Who made the photo, and under which licence."""

    source: str
    author: str
    license: str
    license_url: str
    page_url: str


def credit_of(db: Session, uid: int, person_id: int) -> Credit_ | None:
    """The credit of the photo the person is shown: none for a photo from a folder (the library's own)."""
    found = photo_of(db, uid, person_id)
    if found is None or found[1] == "folder":
        return None
    info = db.get(PersonInfo, person_id)
    assert info is not None
    return Credit_(source=info.photo_source, author=info.photo_author, license=info.photo_license,
                   license_url=info.photo_license_url, page_url=info.photo_page)


def bio_of(db: Session, person_id: int, language: str) -> dict[str, Any] | None:
    """The short biography in the person's language: the description of Wikidata in it (English when there is none) and,
    when a source gave a longer text, that text. ``None`` when nothing is known."""
    info = db.get(PersonInfo, person_id)
    if info is None or not isinstance(info.bios, dict):
        return None
    texts = info.bios.get("texts") if isinstance(info.bios.get("texts"), dict) else {}
    life = info.bios.get("life") if isinstance(info.bios.get("life"), dict) else {}
    base = (language or "en").lower().split("-")[0]
    out: list[dict[str, str]] = []
    for key in (base, "en"):
        item = texts.get(key)
        if isinstance(item, dict) and item.get("source") == "wikidata":
            out.append({"text": str(item.get("text", "")), "language": key, "source": "wikidata",
                        "url": str(item.get("url", ""))})
            break
    for key in (base + "+", "en+"):
        item = texts.get(key)
        if isinstance(item, dict):
            out.append({"text": str(item.get("text", "")), "language": key.rstrip("+"),
                        "source": str(item.get("source", "")), "url": str(item.get("url", ""))})
            break
    if not out:
        return None
    return {"texts": out, "born": life.get("born"), "died": life.get("died")}

