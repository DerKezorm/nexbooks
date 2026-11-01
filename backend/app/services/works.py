"""Reading the books: works, editions, files, chapters, series and people, **as one person may see them**.

Every query here starts from the libraries the person is a member of (``libraries.member_library_ids``) and asks the
database for exactly that: a count, a list, a cover, a series, an author. Nothing is filtered afterwards, so nothing a
person may not see is counted, named or timed.

**A work is seen with the editions the person may see, and with nothing else.** Its title, description, genre, series
and place in it, original title, year, pages, cover, colour, authors and the date it came are worked out from *those*
editions (``catalog.summarize``, the same rule the server uses for all of them); the columns of the work itself are
the server's summary and never leave it. In the SQL (filter by genre, the numbers per genre, the order of the shelf, the
members of a series) the same editions are asked for the same fields: ``_first_visible``. A book in two libraries
shows, to a member of only one, what that one holds.

Editions that are *missing* (no file on the disk any more) are not shown: the work is still known to nexbooks (its
progress and history stay), but there is nothing to read or hear.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..errors import error
from ..models import (
    AUTHOR,
    EDITION_AUDIO,
    EDITION_EBOOK,
    NARRATOR,
    ROLE_BOOK,
    TRANSLATOR,
    BookFile,
    Chapter,
    Credit,
    Edition,
    ExternalRef,
    Library,
    Person,
    Series,
    Work,
)
from . import aliases, covers, genres
from .catalog import Shown, edition_order, summarize
from .libraries import member_library_ids
from .match import overlay
from .readers.names import title_keys

MAX_PER_PAGE = 200
SORTS = ("shelf", "added", "title")


def _visible(edition: Any, uid: int, library_id: int | None = None) -> list[ColumnElement[bool]]:
    """Conditions that make an ``edition`` (``Edition`` or an alias of it) one the person sees: in a library they are a
    member of, with a file on the disk. ``library_id`` narrows it to one library (which must be one of theirs: the
    caller checks)."""
    clauses = [edition.library_id.in_(member_library_ids(uid)), edition.missing_at.is_(None)]
    if library_id is not None:
        clauses.append(edition.library_id == library_id)
    return clauses


def visible_edition_clause(uid: int, library_id: int | None = None) -> list[ColumnElement[bool]]:
    return _visible(Edition, uid, library_id)


def seen_work(uid: int, column: Any) -> ColumnElement[bool]:
    """A clause for a column that holds a work or nothing (a wish, a download, a line of the history, a blocked
    release): the rows of a book of the sources or without a book always, the rows of a work only when the person sees
    it in one of their libraries. Rights in the SQL, as everywhere."""
    return or_(column.is_(None), column.in_(select(Edition.work_id).where(*visible_edition_clause(uid))))


def _first_visible(uid: int, attribute: str, where: Callable[[Any], ColumnElement[bool]] | None = None,
                   before: Callable[[Any], ColumnElement[Any]] | None = None) -> Any:
    """A scalar subquery for the enclosing ``Work``: ``attribute`` of the first edition the person sees (e-books before
    recordings, then the oldest), optionally the first of those for which ``where`` holds, and the ones for which
    ``before`` is the smaller first. This is ``catalog.summarize`` in SQL, over the editions of the person."""
    edition = aliased(Edition)
    conditions = [edition.work_id == Work.id, *_visible(edition, uid)]
    if where is not None:
        conditions.append(where(edition))
    order = [before(edition)] if before is not None else []
    return (select(getattr(edition, attribute)).where(*conditions)
            .order_by(*order, case((edition.kind == EDITION_EBOOK, 0), else_=1), edition.id).limit(1).scalar_subquery())


def _shown_genre(uid: int) -> Any:
    # A genre somebody said comes before a guess, whatever the order of the editions (``catalog.summarize``).
    return func.coalesce(_first_visible(uid, "genre", lambda e: e.genre != "", lambda e: e.genre_guess), "")


def _series_edition(uid: int, attribute: str) -> Any:
    return _first_visible(uid, attribute, lambda e: e.series_id.is_not(None))


def _first_added(uid: int) -> Any:
    edition = aliased(Edition)
    return select(func.min(edition.created_at)).where(
        edition.work_id == Work.id, *_visible(edition, uid)).scalar_subquery()


@dataclass
class Filters:
    library_id: int | None = None
    genre: str | None = None
    kind: str | None = None
    language: str | None = None


def _edition_filters(uid: int, filters: Filters) -> list[ColumnElement[bool]]:
    clauses = _visible(Edition, uid, filters.library_id)
    if filters.kind:
        clauses.append(Edition.kind == filters.kind)
    if filters.language:
        clauses.append(Edition.language == filters.language)
    return clauses


def _work_ids(uid: int, filters: Filters) -> Select[tuple[int]]:
    """The ids of the works the person sees under ``filters``: a work with an edition in their libraries that fits."""
    query = select(Work.id).where(Work.id.in_(select(Edition.work_id).where(*_edition_filters(uid, filters))))
    if filters.genre is not None:
        query = query.where(_shown_genre(uid) == ("" if filters.genre == "none" else filters.genre))
    return query


def work_query(uid: int, filters: Filters) -> Select[tuple[Work]]:
    return select(Work).where(Work.id.in_(_work_ids(uid, filters)))


@dataclass
class EditionView:
    edition: Edition
    formats: list[str] = field(default_factory=list)
    files: list[BookFile] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    library_name: str = ""
    #: Of this edition (not of the work): who wrote it, the fields a person corrected by hand, the sources that know it.
    authors: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)
    matched_by: list[str] = field(default_factory=list)


@dataclass
class WorkView:
    """A work as one person sees it: only the editions of their libraries, and only what those say."""

    work: Work
    shown: Shown
    authors: list[tuple[int, str]] = field(default_factory=list)
    #: The other names of the authors who have any (pen names, on proof), by the person's number.
    author_aliases: dict[int, list[str]] = field(default_factory=dict)
    editions: list[EditionView] = field(default_factory=list)
    series_name: str = ""
    cover: str = ""
    color: str = ""

    @property
    def title(self) -> str:
        return self.shown.title

    @property
    def description(self) -> str:
        return self.shown.description

    @property
    def original_title(self) -> str:
        return self.shown.original_title

    @property
    def original_language(self) -> str:
        return self.shown.original_language

    @property
    def genre(self) -> str:
        return self.shown.genre

    @property
    def genre_guess(self) -> bool:
        return self.shown.genre_guess

    @property
    def series_id(self) -> int | None:
        return self.shown.series_id

    @property
    def series_pos(self) -> float | None:
        return self.shown.series_pos

    @property
    def year(self) -> int | None:
        return self.shown.year

    @property
    def pages(self) -> int:
        return self.shown.pages

    @property
    def added_at(self) -> datetime:
        return self.shown.added

    @property
    def audio_only(self) -> bool:
        return not any(view.edition.kind == EDITION_EBOOK for view in self.editions)


def views(db: Session, uid: int, works: list[Work], *, detail: bool = False) -> list[WorkView]:
    """The works with what the person sees of them, in as few queries as the page needs. A work of which the person sees
    no edition (it went between two queries) is left out."""
    if not works:
        return []
    work_ids = [work.id for work in works]
    editions = db.scalars(select(Edition).where(Edition.work_id.in_(work_ids), *_visible(Edition, uid))
                          .order_by(Edition.id)).all()
    edition_ids = [edition.id for edition in editions]
    of_work: dict[int, list[Edition]] = {}
    for edition in editions:
        of_work.setdefault(edition.work_id, []).append(edition)
    formats: dict[int, set[str]] = {}
    files_of: dict[int, list[BookFile]] = {}
    people: dict[int, dict[str, list[tuple[int, str]]]] = {}
    if edition_ids:
        file_query = select(BookFile).where(BookFile.edition_id.in_(edition_ids), BookFile.role == ROLE_BOOK,
                                            BookFile.missing_at.is_(None)).order_by(BookFile.part, BookFile.path)
        for row in db.scalars(file_query):
            formats.setdefault(row.edition_id or 0, set()).add(row.format)
            if detail:
                files_of.setdefault(row.edition_id or 0, []).append(row)
        for edition_id, role, person_id, name in db.execute(
                select(Credit.edition_id, Credit.role, Person.id, Person.name)
                .join(Person, Person.id == Credit.person_id)
                .where(Credit.edition_id.in_(edition_ids), Credit.role.in_((AUTHOR, NARRATOR, TRANSLATOR)))
                .order_by(Credit.edition_id, Credit.position)):
            people.setdefault(edition_id or 0, {}).setdefault(role, []).append((person_id, name))
    series_ids = {edition.series_id for edition in editions if edition.series_id}
    series_names = dict(db.execute(select(Series.id, Series.name).where(Series.id.in_(series_ids))).all()) \
        if series_ids else {}
    library_names: dict[int, str] = {}
    sources: dict[int, list[str]] = {}
    if detail and editions:
        library_names = dict(db.execute(select(Library.id, Library.name).where(
            Library.id.in_({edition.library_id for edition in editions}))).all())
        for edition_id, source in db.execute(
                select(ExternalRef.edition_id, ExternalRef.source).where(ExternalRef.edition_id.in_(edition_ids))
                .distinct().order_by(ExternalRef.edition_id, ExternalRef.source)):
            sources.setdefault(edition_id or 0, []).append(source)
    author_ids = {ident for by_role in people.values() for ident, _name in by_role.get(AUTHOR, [])}
    # Another name of a person only when it comes from a book the caller sees (a name out of the tags of a book in
    # somebody else's library is not shown).
    others = aliases.aliases_of(db, sorted(author_ids), member_library_ids(uid))
    found: list[WorkView] = []
    for work in works:
        mine = sorted(of_work.get(work.id, []), key=edition_order)
        shown = summarize(mine)
        if shown is None:
            continue
        view = WorkView(work=work, shown=shown, series_name=series_names.get(shown.series_id or 0, ""))
        # The authors of the first edition that names any, the same rule as for the title.
        named = next((people[e.id][AUTHOR] for e in mine if people.get(e.id, {}).get(AUTHOR)), [])
        view.authors = list(dict(named).items())
        view.author_aliases = {ident: others[ident] for ident, _name in view.authors if ident in others}
        for edition in mine:
            edition_formats = sorted(formats.get(edition.id, set()),
                                     key=lambda f: ("epub", "azw3", "mobi", "pdf").index(f)
                                     if f in ("epub", "azw3", "mobi", "pdf") else 9)
            view.editions.append(EditionView(
                edition=edition, formats=edition_formats, files=files_of.get(edition.id, []),
                narrators=[name for _id, name in people.get(edition.id, {}).get(NARRATOR, [])],
                translators=[name for _id, name in people.get(edition.id, {}).get(TRANSLATOR, [])],
                library_name=library_names.get(edition.library_id, ""),
                authors=[name for _id, name in people.get(edition.id, {}).get(AUTHOR, [])],
                corrected=overlay.hand_fields(edition.overlay) if detail else [],
                matched_by=sources.get(edition.id, [])))
        view.cover = shown.cover
        view.color = shown.color or genres.palette_color(shown.genre, title_keys(shown.title)[0] or str(work.id))
        found.append(view)
    return found


def list_works(
    db: Session, uid: int, filters: Filters, sort: str, page: int, per_page: int
) -> tuple[list[WorkView], int]:
    ids = _work_ids(uid, filters)
    total = db.scalar(select(func.count()).select_from(ids.subquery())) or 0
    query = work_query(uid, filters)
    if sort == "added":
        query = query.order_by(_first_added(uid).desc(), Work.id.desc())
    elif sort == "title":
        query = query.order_by(_first_visible(uid, "title_key"), Work.id)
    else:
        query = query.order_by(_first_visible(uid, "shelf_key"), Work.id)
    rows = db.scalars(query.limit(per_page).offset((page - 1) * per_page)).all()
    return views(db, uid, list(rows)), total


def get_work(db: Session, uid: int, work_id: int) -> WorkView:
    """One work with everything the person sees of it; 404 when they see no edition of it."""
    work = db.scalar(select(Work).where(Work.id == work_id, Work.id.in_(
        select(Edition.work_id).where(*_visible(Edition, uid)))))
    found = views(db, uid, [work], detail=True) if work is not None else []
    if not found:
        raise error("not_found", "Not found.", 404)
    return found[0]


#: What a browser is told a downloaded file is.
MEDIA_TYPES = {
    "epub": "application/epub+zip", "pdf": "application/pdf", "azw3": "application/vnd.amazon.ebook",
    "mobi": "application/x-mobipocket-ebook", "mp3": "audio/mpeg", "m4b": "audio/mp4", "m4a": "audio/mp4",
    "flac": "audio/flac", "ogg": "audio/ogg", "opus": "audio/ogg",
}


def book_path(library_path: str, relative: str) -> Path | None:
    """The file ``relative`` (as the scan stored it, with ``/``) inside the library folder, or None when it is not a
    plain file inside it: a path that climbs out (``..``), an absolute one, a link, or a file that is gone."""
    if not relative or "\x00" in relative or relative.startswith(("/", "\\")) or ".." in relative.split("/"):
        return None
    base = Path(library_path)
    target = base.joinpath(*relative.split("/"))
    try:
        if target.is_symlink() or not target.is_file():
            return None
        return target if target.resolve(strict=True).is_relative_to(base.resolve(strict=True)) else None
    except (OSError, RuntimeError, ValueError):
        return None


def file_of(db: Session, uid: int, file_id: int) -> tuple[BookFile, Path]:
    """A book file the person may download, with its place on the disk. The path comes from the database and is checked
    against the library folder again; the request names nothing but the number. 404 for anything else, alike for a file
    that does not exist and one in a library the person is not in."""
    row = db.scalar(select(BookFile).where(
        BookFile.id == file_id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None),
        BookFile.edition_id.in_(select(Edition.id).where(*_visible(Edition, uid)))))
    library = db.get(Library, row.library_id) if row is not None else None
    path = book_path(library.path, row.path) if row is not None and library is not None else None
    if row is None or path is None:
        raise error("not_found", "Not found.", 404)
    return row, path


def chapters_of(db: Session, edition_ids: list[int]) -> dict[int, list[Chapter]]:
    result: dict[int, list[Chapter]] = {}
    if edition_ids:
        for chapter in db.scalars(select(Chapter).where(Chapter.edition_id.in_(edition_ids))
                                  .order_by(Chapter.edition_id, Chapter.number)):
            result.setdefault(chapter.edition_id, []).append(chapter)
    return result


def cover_names(db: Session, uid: int, work_ids: list[int]) -> dict[int, str]:
    """The cover file of each of these works as the person sees it (an edition of theirs), for those that have one on
    the disk: one query, no more of the work than its cover. A work the person does not see is left out."""
    if not work_ids:
        return {}
    of_work: dict[int, list[Edition]] = {}
    for edition in db.scalars(select(Edition).where(Edition.work_id.in_(work_ids), *_visible(Edition, uid))
                              .order_by(Edition.id)):
        of_work.setdefault(edition.work_id, []).append(edition)
    found: dict[int, str] = {}
    for work_id, mine in of_work.items():
        shown = summarize(mine)
        if shown is not None and covers.path_of(shown.cover) is not None:
            found[work_id] = shown.cover
    return found


def cover_of(db: Session, uid: int, work_id: int) -> str:
    """The cover file name of a work as the person sees it (an edition of theirs), or 404."""
    view = get_work(db, uid, work_id)
    path = covers.path_of(view.cover)
    if path is None:
        raise error("not_found", "Not found.", 404)
    return view.cover


def stats(db: Session, uid: int, filters: Filters) -> dict[str, Any]:
    """Numbers for the line above the shelf: works, editions, works with a recording, and the works per genre."""
    visible = _edition_filters(uid, filters)
    ids = _work_ids(uid, filters).subquery()
    work_count = db.scalar(select(func.count()).select_from(ids)) or 0
    edition_rows = db.execute(
        select(Edition.kind, func.count(Edition.id)).where(*visible, Edition.work_id.in_(select(ids.c.id)))
        .group_by(Edition.kind)).all()
    by_kind = {kind: number for kind, number in edition_rows}
    audio_works = db.scalar(select(func.count(Edition.work_id.distinct())).where(
        *visible, Edition.kind == EDITION_AUDIO, Edition.work_id.in_(select(ids.c.id)))) or 0
    ebook_works = db.scalar(select(func.count(Edition.work_id.distinct())).where(
        *visible, Edition.kind == EDITION_EBOOK, Edition.work_id.in_(select(ids.c.id)))) or 0
    per_work = select(Work.id.label("id"), _shown_genre(uid).label("genre")).where(
        Work.id.in_(select(ids.c.id))).subquery()
    by_genre = {genre or "none": number for genre, number in db.execute(
        select(per_work.c.genre, func.count()).group_by(per_work.c.genre))}
    languages = {language or "none": number for language, number in db.execute(
        select(Edition.language, func.count(Edition.work_id.distinct())).where(*visible)
        .group_by(Edition.language))}
    return {
        "works": work_count,
        "editions": sum(by_kind.values()),
        "ebook_editions": by_kind.get(EDITION_EBOOK, 0),
        "audio_editions": by_kind.get(EDITION_AUDIO, 0),
        "works_with_ebook": ebook_works,
        "works_with_audio": audio_works,
        "genres": by_genre,
        "languages": languages,
    }


def get_series(db: Session, uid: int, series_id: int) -> tuple[Series, list[WorkView]]:
    """A series with the volumes the person sees: a work belongs to it when the editions *they see* put it there."""
    series = db.get(Series, series_id)
    position = _series_edition(uid, "series_pos")
    works = db.scalars(select(Work).where(
        Work.id.in_(select(Edition.work_id).where(*_visible(Edition, uid))),
        _series_edition(uid, "series_id") == series_id)
        .order_by(case((position.is_(None), 1), else_=0), position, _first_visible(uid, "title_key"), Work.id)).all()
    found = views(db, uid, list(works)) if series is not None else []
    if series is None or not found:
        raise error("not_found", "Not found.", 404)
    return series, found


def list_series(db: Session, uid: int, filters: Filters) -> list[tuple[Series, int]]:
    """The series the person sees, with the number of their volumes under ``filters``, by name: a work belongs to a
    series when the editions *the person sees* put it there (``get_series``)."""
    shown = _series_edition(uid, "series_id")
    ids = _work_ids(uid, filters)
    per_work = select(Work.id.label("id"), shown.label("series_id")).where(Work.id.in_(ids)).subquery()
    counts = dict(db.execute(select(per_work.c.series_id, func.count()).where(per_work.c.series_id.is_not(None))
                             .group_by(per_work.c.series_id)).all())
    found = db.scalars(select(Series).where(Series.id.in_(list(counts)))).all() if counts else []
    return sorted(((series, counts[series.id]) for series in found), key=lambda item: (item[0].name.casefold(),
                                                                                       item[0].id))


def get_person(db: Session, uid: int, person_id: int) -> tuple[Person, list[WorkView], list[WorkView]]:
    """A person with the works they wrote and the works they narrated that the caller sees, by the editions the caller
    sees."""
    person = db.get(Person, person_id)

    def works_with(role: str) -> list[Work]:
        credited = select(Credit.edition_id).where(Credit.person_id == person_id, Credit.role == role,
                                                   Credit.edition_id.is_not(None))
        return list(db.scalars(select(Work).where(Work.id.in_(
            select(Edition.work_id).where(*_visible(Edition, uid), Edition.id.in_(credited))))
            .order_by(_first_visible(uid, "shelf_key"), Work.id)).all())

    wrote = views(db, uid, works_with(AUTHOR))
    narrated = views(db, uid, works_with(NARRATOR))
    if person is None or not (wrote or narrated):
        raise error("not_found", "Not found.", 404)
    return person, wrote, narrated


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
