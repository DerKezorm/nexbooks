"""The catalogue of an author as one person sees it: the works the sources know, set against **the books this person
may see, and nothing else**.

* A work of the catalogue is "on the shelf" only when a book of a library the person is a member of is that work
  (``works.visible_edition_clause``); a book in a library the person is not in never makes it so, and is never
  counted. The numbers ("3 davon im Regal") follow the same rule.
* **Which book is which work**: the names the sources gave the editions, the ISBN and the ASIN first; then the title,
  by the strict rule a release name is held by (``procure.release_match.title_is``): the title of the book as a whole
  is a title of the work as a whole, both ways round. ``Ihr wollt es dunkler`` is no ``Es``, ``Ruß und Rosen`` no
  ``Ruß``; a short title never fits as a part of a longer one. One book is one work and the other way round.
* A book of the person's that the catalogue does not know (the sources missed it) still stands on the page, under
  the kind its genre says.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    AUTHOR,
    CatalogMove,
    CatalogPerson,
    CatalogWork,
    Credit,
    Edition,
    ExternalRef,
    ForeignBook,
    Person,
    PersonAlias,
    Wish,
    Work,
)
from .. import series_names, title_shape
from .. import works as works_service
from ..libraries import member_library_ids
from ..procure import release_match
from ..readers.names import compare_key
from ..works import WorkView, visible_edition_clause


@dataclass
class Own:
    """A book of the person's by the author: the work as they see it and what its editions say."""

    view: WorkView
    titles: list[str] = field(default_factory=list)
    numbers: set[str] = field(default_factory=set)
    refs: set[str] = field(default_factory=set)
    #: The kinds the person has (``ebook``, ``audio``) and in which languages.
    kinds: dict[str, set[str]] = field(default_factory=dict)
    series_pos: float | None = None
    #: Written under another name of the author (a pen name of the libraries).
    pen: str = ""


@dataclass
class Card:
    """One work on the page: from the catalogue (``work``) or only from the person's shelf (``own`` alone)."""

    work: CatalogWork | None
    own: Own | None = None
    book: ForeignBook | None = None
    wished: bool = False
    #: The kind as it stands for the page (what a person moved it to wins), and why it is unsure.
    kind: str = "roman"
    why: str = ""
    moved: bool = False
    #: A book on the person's shelf that may be this work (the same place in its series, or its title is only the
    #: series and a number): nothing asks for the work by itself, a person looks first.
    maybe: Own | None = None


@dataclass
class AuthorView:
    row: CatalogPerson
    cards: list[Card]
    #: The people of the libraries this author is (their own name, their pen names), as far as the person sees them.
    people: list[int] = field(default_factory=list)


def kind_of_genre(genre: str) -> str:
    return {"sachbuch": "sach", "kinder": "kinder", "lyrik": "lyrik"}.get(genre, "roman")


def people_of(db: Session, row: CatalogPerson, library_ids: Any = None) -> tuple[list[int], dict[int, str]]:
    """The people of the libraries the author is: the one linked, the one of the same name, the person a name of theirs
    is a proven pen name of (proven by a book of ``library_ids`` when given: the real name behind a pen name comes only
    from books one may see); and, apart, people whose name is a pen name of the author (``{id: pen name}``)."""
    from .. import aliases as aliases_service

    ids: set[int] = {row.person_id} if row.person_id else set()
    ids.update(db.scalars(select(Person.id).where(Person.key == row.key)))
    for alias in db.scalars(select(PersonAlias).where(PersonAlias.key == row.key)):
        if library_ids is None or alias.id in {found.id for found in aliases_service.alias_rows(
                db, [alias.person_id], library_ids)}:
            ids.add(alias.person_id)

    pens: dict[int, str] = {}
    for name in row.aliases or []:
        if not isinstance(name, str):
            continue
        found = db.scalar(select(Person.id).where(Person.key == aliases_service.key_of(name)))
        if found is not None and found not in ids:
            pens[found] = name
    return sorted(ids), pens


def own_books(db: Session, uid: int, people: list[int], pens: dict[int, str]) -> list[Own]:
    """The books the person sees that one of these people wrote, with what their editions say."""
    everyone = [*people, *pens]
    if not everyone:
        return []
    visible = visible_edition_clause(uid)
    credited = select(Credit.edition_id, Credit.person_id).where(
        Credit.person_id.in_(everyone), Credit.role == AUTHOR,
        Credit.edition_id.in_(select(Edition.id).where(*visible)))
    by_edition: dict[int, set[int]] = {}
    for edition_id, person_id in db.execute(credited):
        by_edition.setdefault(int(edition_id), set()).add(int(person_id))
    if not by_edition:
        return []
    work_ids = sorted(set(db.scalars(select(Edition.work_id).where(Edition.id.in_(list(by_edition))))))
    works = list(db.scalars(select(Work).where(Work.id.in_(work_ids)).order_by(Work.id)))
    refs: dict[int, set[str]] = {}
    for source, key, edition_id in db.execute(select(ExternalRef.source, ExternalRef.key, ExternalRef.edition_id)
                                              .where(ExternalRef.edition_id.in_(list(by_edition)))):
        refs.setdefault(int(edition_id or 0), set()).add(f"{source}:{key}")
    found: list[Own] = []
    for view in works_service.views(db, uid, works):
        own = Own(view=view, series_pos=view.series_pos)
        names: list[str] = [view.title, view.original_title]
        writers: set[int] = set()
        for edition_view in view.editions:
            edition = edition_view.edition
            names += [edition.title, edition.original_title]
            for number in (edition.isbn, edition.asin):
                if number:
                    own.numbers.add(number.upper())
            own.refs |= refs.get(edition.id, set())
            own.kinds.setdefault(edition.kind, set()).add(edition.language or "")
            writers |= by_edition.get(edition.id, set())
        own.titles = list(dict.fromkeys(name for name in names if name and name.strip()))
        if writers and writers <= set(pens):
            own.pen = pens[next(iter(writers))]
        found.append(own)
    return found


def _book(text: str, authors: tuple[str, ...]) -> release_match.Book:
    return release_match.Book(titles=(release_match.Title(text),), authors=authors)


def same_title(a: str, b: str, author: str) -> bool:
    """Whether two titles are one title as a whole, by the rule release names are held by, both ways round: neither is
    a part of the other (``Ruß`` and ``Ruß und Rosen`` are two)."""
    authors = (author,)
    return release_match.title_is(a, _book(b, authors)) and release_match.title_is(b, _book(a, authors))


def match(works: list[CatalogWork], books: list[Own], author: str) -> dict[int, Own]:
    """Which book of the person's is which work (by the number of the work): by the numbers and names at the sources
    first, then by the title (``same_title``); a different place in a series is another book. One to one, the surest
    pairs first."""
    pairs: list[tuple[int, int, int, int]] = []
    # A number two works share proves neither (the sources file editions under the wrong work now and then).
    seen: dict[str, int] = {}
    for work in works:
        for number in {str(n).upper() for n in [*(work.isbns or []), *(work.refs or [])] if isinstance(n, str)}:
            seen[number] = seen.get(number, 0) + 1
    book_keys = [{compare_key(title) for title in book.titles} for book in books]
    book_words = [{word for key in keys for word in key.split()} for keys in book_keys]
    for w_index, work in enumerate(works):
        numbers = {str(n).upper() for n in work.isbns or [] if isinstance(n, str) and seen.get(str(n).upper()) == 1}
        refs = {r for r in work.refs or [] if isinstance(r, str) and seen.get(r.upper()) == 1}
        texts = [str(item.get("title") or "") for item in (work.titles or {}).values() if isinstance(item, dict)]
        texts = [text for text in dict.fromkeys(texts) if text]
        keys = {compare_key(text) for text in texts}
        words = {word for key in keys for word in key.split()}
        for b_index, book in enumerate(books):
            if work.series_pos is not None and book.series_pos is not None and work.series_pos != book.series_pos:
                continue
            if numbers & book.numbers or refs & book.refs:
                pairs.append((3, 1, w_index, b_index))
                continue
            # (The strict rule is the judge; titles without a word in common cannot be one title.)
            if not words & book_words[b_index]:
                continue
            exact = bool(keys & book_keys[b_index])
            if any(same_title(mine, theirs, author) for mine in book.titles for theirs in texts):
                pairs.append((2, 1 if exact else 0, w_index, b_index))
    pairs.sort(key=lambda pair: (-pair[0], -pair[1], pair[2], pair[3]))
    taken_w: set[int] = set()
    taken_b: set[int] = set()
    found: dict[int, Own] = {}
    for _score, _exact, w_index, b_index in pairs:
        if w_index in taken_w or b_index in taken_b:
            continue
        taken_w.add(w_index)
        taken_b.add(b_index)
        found[works[w_index].id] = books[b_index]
    return found


def view(db: Session, uid: int, row: CatalogPerson) -> AuthorView:
    """The page of the author for a person."""
    works = list(db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id)
                            .order_by(CatalogWork.year, CatalogWork.id)))
    moves = {move.work_key: move.kind for move in db.scalars(
        select(CatalogMove).where(CatalogMove.catalog_person_id == row.id))}
    people, pens = people_of(db, row, member_library_ids(uid))
    books = own_books(db, uid, people, pens)
    matched = match(works, books, row.name)
    foreign_ids = [work.foreign_id for work in works if work.foreign_id]
    rows = {book.id: book for book in db.scalars(select(ForeignBook).where(ForeignBook.id.in_(foreign_ids)))} \
        if foreign_ids else {}
    wished = set(db.scalars(select(Wish.foreign_id).where(Wish.user_id == uid, Wish.foreign_id.in_(foreign_ids)))) \
        if foreign_ids else set()
    cards: list[Card] = []
    for work in works:
        moved = moves.get(work.key)
        cards.append(Card(work=work, own=matched.get(work.id), book=rows.get(work.foreign_id or 0),
                          wished=(work.foreign_id or 0) in wished, kind=moved or work.kind,
                          why="" if moved else work.why, moved=moved is not None))
    used = {id(own) for own in matched.values()}
    left = [book for book in books if id(book) not in used]
    for card in cards:
        if card.work is not None and card.own is None:
            card.maybe = maybe_own(card.work, left)
    for book in left:
        cards.append(Card(work=None, own=book, kind=kind_of_genre(book.view.genre)))
    return AuthorView(row=row, cards=cards, people=people)


def upcoming(edition: dict, today: str) -> bool:  # type: ignore[type-arg]
    """Whether the work comes out in this language after today: its day, or its year when no day is known. A day after
    today beside an earlier year is a new edition of a book that is out (a paperback), not a book to come."""
    released = str(edition.get("released") or "")
    year = edition.get("year")
    year = year if isinstance(year, int) else None
    if len(released) == 10:
        return released > today and (year is None or year >= int(released[:4]))
    return year is not None and year > int(today[:4])


def maybe_own(work: CatalogWork, books: list[Own]) -> Own | None:
    """A book of the person's that no title rule took for ``work`` but that may be it: its title is only the series
    and the number of the work (``Nora Nachtfalter 2``), or it stands at the place of the work in the same series. A
    book "Alle fehlenden beschaffen" would else fetch a second time."""
    if work.series_pos is None:
        return None
    titles = [str(item.get("title") or "") for item in (work.titles or {}).values() if isinstance(item, dict)]
    for book in books:
        series = book.view.series_name or ""
        same_series = bool(work.series and series) and series_names.key(work.series) == series_names.key(series)
        if book.series_pos == work.series_pos and same_series:
            return book
        if any(title_shape.series_shorthand(title, series=[work.series, series], position=work.series_pos,
                                            wished=wished)
               for title in book.titles for wished in titles or [""]):
            return book
    return None


def has(card: Card, kind: str, language: str | None = None) -> bool:
    if card.own is None:
        return False
    languages = card.own.kinds.get(kind, set())
    return bool(languages) and (language is None or language in languages or "" in languages)


__all__ = ["AuthorView", "Card", "Own", "has", "match", "own_books", "people_of", "same_title", "upcoming", "view"]
