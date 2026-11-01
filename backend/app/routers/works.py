"""The books: works with their editions, files and chapters, series, people and covers.

Everything here reads, and is open to a signed-in browser, a device and a program with a key (``Reader``); the one
thing that writes (splitting a pen name off a person) is for a signed-in browser. **What a
person sees depends on their libraries**, and the database is asked for exactly that (``services/works.py``): a work,
series, person or cover that lies only in other people's libraries answers 404, like one that does not exist.

The files are listed with their path inside the library, their format, size and checksum, and a file can be downloaded
(``/files/{id}/download``, the library checked again, the place on the disk never taken from the request) and its bytes
read, with ``Range`` and an ``ETag``, for the reader and the players (``/files/{id}/content``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import Account, BrowserAccount, DbSession, Reader
from ..errors import error
from ..models import OPERATOR, Person, Work
from ..services import aliases, brakes, chapter_groups, covers, libraries, people_info, relocate
from ..services import authors as authors_service
from ..services import later as later_service
from ..services import listing as listing_service
from ..services import works as service
from ..services.match import overlay
from ..services.procure import trash, wrong
from ..services.works import EditionView, Filters, WorkView

router = APIRouter(prefix="/api/v1", tags=["books"])

GenreName = Literal["roman", "krimi", "fantasy", "scifi", "klassiker", "sachbuch", "kinder", "lyrik", "none"]


class PersonRef(BaseModel):
    id: int
    name: str
    #: Other names the person is proved to write under (a pen name), for "also ..." beside the name.
    aliases: list[str] = []


class SeriesRef(BaseModel):
    id: int
    name: str
    pos: float | None


class EditionBrief(BaseModel):
    id: int
    library_id: int
    kind: Literal["ebook", "audio"]
    variant: str
    variant_uncertain: bool
    language: str
    #: The language was worked out (from the e-book of the same work, or from the library), not told by a file or a
    #: source or a person: the page says so quietly.
    language_guess: bool = False
    #: The formats of the files of the edition, best for reading first: ``["epub", "azw3", "mobi"]``.
    formats: list[str]
    pages: int
    #: Seconds; 0 for an e-book.
    duration: int
    narrators: list[str]


class WorkBrief(BaseModel):
    id: int
    title: str
    authors: list[PersonRef]
    series: SeriesRef | None
    genre: str
    #: The genre is a guess: no file and no source said it, it is what most of the author's other books in the same
    #: library are. The page says so quietly.
    genre_guess: bool
    #: The main colour of the cover, ``#rrggbb`` (a colour of the genre's palette without a cover).
    color: str
    #: The address of the cover; changes when the cover does. Null: the interface draws a cover.
    cover_url: str | None
    pages: int
    year: int | None
    #: How the spine looks: pattern 0..4, height 0..1, gilt lines.
    pattern: int
    tall: float
    gold: bool
    audio_only: bool
    added_at: datetime
    editions: list[EditionBrief]


class FileOut(BaseModel):
    id: int
    format: str
    #: Inside the library, with ``/``.
    path: str
    size: int
    part: int
    duration: float
    sha256: str
    #: Copy-protected (Adobe or Kindle DRM, a PDF with a password): nexbooks does not open it and does nothing to get
    #: around the protection. The file can still be downloaded.
    protected: bool


class ChapterOut(BaseModel):
    number: int
    title: str
    #: Of a recording: where it starts, in seconds from the beginning of all the parts.
    start: float
    #: Of an e-book: the place in the book (file and anchor, as the package file names it).
    target: str
    depth: int


class DeliveryOut(BaseModel):
    """The release that brought an edition: shown to the people who look after its library, who may send the book away
    when it is not the one wished (``POST /editions/{id}/wrong-book``)."""

    release: str
    indexer: str
    delivered_at: datetime | None
    #: How many days the trash of the library keeps what is sent away (the operator's choice): the page says so before
    #: it asks.
    trash_days: int


class EditionOut(EditionBrief):
    title: str
    subtitle: str
    isbn: str
    asin: str
    publisher: str
    published: str
    library_name: str
    translators: list[str]
    #: Of this edition (the work shows the lead edition's): its authors, series, place in it and genre, the fields a
    #: person corrected by hand (a correction can be taken back), and the sources that know the edition (a match can be
    #: taken back).
    authors: list[str]
    series_name: str
    series_pos: float | None
    genre: str
    genre_guess: bool
    corrected: list[str]
    matched_by: list[str]
    files: list[FileOut]
    chapters: list[ChapterOut]
    #: The caller may move this edition into another library (``/editions/{id}/move``): they manage its library and at
    #: least one other that takes its kind, and nexbooks may write in both.
    can_move: bool = False
    #: The release this edition was fetched from, for a member with ``manage`` in its library (null for anybody else,
    #: and for a book nexbooks did not fetch: one that lay in the library, or that a person uploaded).
    delivery: DeliveryOut | None = None
    #: For a member with ``manage`` in its library: how many days the trash keeps the edition when it is put there
    #: (``POST /editions/{id}/trash``, the page says so before it asks). 0 for anybody else: they may not.
    trash_days: int = 0


class WorkOut(WorkBrief):
    description: str
    original_title: str
    original_language: str
    editions: list[EditionOut]  # type: ignore[assignment]
    #: Recordings of this work whose running times are within 3 percent of each other: probably one recording that lies
    #: twice (a box and a folder of its own). A hint for a person; nothing is merged by it. Each group is a list of
    #: edition ids.
    duplicates: list[list[int]] = []
    #: On the caller's own list for later.
    later: bool = False


class Mine(BaseModel):
    """What the list view adds for the caller: their own place and end in the book, and (for a member who looks after a
    library the book lies in) how sure the matching is and where the book lies."""

    #: How far the caller is in the e-book and in the recording (0 to 1); null: not begun.
    ebook: float | None
    audio: float | None
    #: Whether the caller entered the e-book / the recording as finished.
    ebook_done: bool
    audio_done: bool
    #: When the caller was last in the book (null: never).
    moved_at: datetime | None
    #: ``sure``, ``maybe``, ``open``, ``pending`` or ``decided``; null unless the caller has ``manage`` in a library the
    #: book lies in. Then the names of those libraries, too.
    match: Literal["sure", "maybe", "open", "pending", "decided"] | None
    libraries: list[str]


class WorkRow(WorkBrief):
    """A book of the list view (``extras=list``): the book, and what the caller adds to it."""

    mine: Mine | None = None


class WorkPage(BaseModel):
    items: list[WorkRow]
    total: int
    page: int
    per_page: int


class Stats(BaseModel):
    works: int
    editions: int
    ebook_editions: int
    audio_editions: int
    works_with_ebook: int
    works_with_audio: int
    #: Works per genre key (``none``: no genre) and editions per language.
    genres: dict[str, int]
    languages: dict[str, int]


class SeriesOut(BaseModel):
    id: int
    name: str
    works: list[WorkBrief]


class PenNameOut(BaseModel):
    """Another name the person is known by, proven by a book the caller sees."""

    id: int
    name: str


class PhotoCredit(BaseModel):
    """Who made the photo and under which licence, to be shown under it. Empty strings: the source gave none."""

    source: str
    author: str
    license: str
    license_url: str
    #: The page of the photo (on Commons) or of the author at the source.
    page_url: str


class BioText(BaseModel):
    text: str
    #: The language of the text, and where it comes from (``wikidata``, ``openlibrary``), with the page it is on.
    language: str
    source: str
    url: str


class Bio(BaseModel):
    texts: list[BioText]
    #: The years of birth and death, when Wikidata gives them.
    born: int | None
    died: int | None


class PersonOut(BaseModel):
    id: int
    name: str
    #: The other names (pen names) the person is known by, as far as a book the caller sees proves them.
    aliases: list[str] = []
    #: The same, with the id that ``POST /people/{id}/split`` takes.
    pen_names: list[PenNameOut] = []
    #: Whether the caller may split a pen name off (the operator, or ``manage`` in every library the person's books are
    #: in).
    can_split: bool = False
    #: Whether the caller may join this author with another one (``POST /people/{id}/merge``): the same right.
    can_merge: bool = False
    wrote: list[WorkBrief]
    narrated: list[WorkBrief]
    #: The photo and who made it; a short biography in the caller's language. Null when there is none.
    photo_url: str | None = None
    credit: PhotoCredit | None = None
    bio: Bio | None = None


class SplitIn(BaseModel):
    #: The id of the other name (``PersonOut.pen_names``) that is somebody else.
    alias_id: int = Field(ge=1, le=2**31 - 1)


class MergeIn(BaseModel):
    #: The other author the person is one with (an id from ``GET /authors``).
    other_id: int = Field(ge=1, le=2**31 - 1)
    #: Which of the two names stays: this person's (``this``) or the other's (``other``).
    keep: Literal["this", "other"]


def _edition_brief(view: EditionView) -> EditionBrief:
    edition = view.edition
    return EditionBrief(
        id=edition.id, library_id=edition.library_id, kind=edition.kind,  # type: ignore[arg-type]
        variant=edition.variant, variant_uncertain=edition.variant_uncertain, language=edition.language,
        language_guess=overlay.is_guess(edition.overlay, "language"),
        formats=view.formats, pages=edition.pages, duration=edition.duration, narrators=view.narrators)


def brief_of(view: WorkView) -> WorkBrief:
    # What a work shows comes from the editions the caller sees (``WorkView``), never from the columns of the work.
    work = view.work
    return WorkBrief(
        id=work.id, title=view.title,
        authors=[PersonRef(id=ident, name=name, aliases=view.author_aliases.get(ident, []))
                 for ident, name in view.authors],
        series=SeriesRef(id=view.series_id, name=view.series_name, pos=view.series_pos) if view.series_id else None,
        genre=view.genre, genre_guess=view.genre_guess, color=view.color,
        cover_url=f"/api/v1/works/{work.id}/cover?v={view.cover[:12]}" if view.cover else None,
        pages=view.pages, year=view.year, pattern=work.pattern, tall=work.tall, gold=work.gold,
        audio_only=view.audio_only, added_at=view.added_at, editions=[_edition_brief(v) for v in view.editions])


def _row_of(view: WorkView, extra: listing_service.RowExtra | None) -> WorkRow:
    brief = brief_of(view)
    if extra is None:
        return WorkRow(**brief.model_dump())
    return WorkRow(**brief.model_dump(), mine=Mine(
        ebook=extra.ebook, audio=extra.audio, ebook_done=extra.ebook_done, audio_done=extra.audio_done,
        moved_at=extra.moved_at, match=extra.match,  # type: ignore[arg-type]
        libraries=extra.libraries if extra.managed else []))


def _filters(db: DbSession, uid: int, library: int | None, genre: str | None, kind: str | None,
             language: str | None) -> Filters:
    if library is not None:
        # A library the caller is not a member of is one that does not exist.
        libraries.require_readable(db, uid, library)
    return Filters(library_id=library, genre=genre, kind=kind, language=(language or "").lower() or None)


LibraryParam = Annotated[int | None, Query(ge=1, le=2**31 - 1, description="Only the books of this library")]
GenreParam = Annotated[GenreName | None, Query(description="Only this genre; none: the books with no genre")]
KindParam = Annotated[Literal["ebook", "audio"] | None, Query(description="Only works with an edition of this kind")]
LanguageParam = Annotated[str | None, Query(max_length=8, pattern=r"^[A-Za-z]{2,3}$")]


@router.get("/works", response_model=WorkPage, summary="The books the caller sees, a page at a time")
def listing(account: Reader, db: DbSession, library: LibraryParam = None, genre: GenreParam = None,
            kind: KindParam = None, language: LanguageParam = None,
            sort: Annotated[Literal["shelf", "added", "title"], Query(description="shelf: genre, author, series, "
                                                                       "volume; added: newest first")] = "shelf",
            page: Annotated[int, Query(ge=1, le=100_000)] = 1,
            per_page: Annotated[int, Query(ge=1, le=service.MAX_PER_PAGE)] = 60,
            extras: Annotated[Literal["list"] | None, Query(
                description="list: add what the list view shows besides the book (``mine``): the caller's own place "
                            "and end in it, and for a member who manages a library the book lies in, the matching and "
                            "the library")] = None) -> WorkPage:
    filters = _filters(db, account.id, library, genre, kind, language)
    found, total = service.list_works(db, account.id, filters, sort, page, per_page)
    more = listing_service.rows(db, account.id, found) if extras == "list" else {}
    return WorkPage(items=[_row_of(view, more.get(view.work.id)) for view in found], total=total, page=page,
                    per_page=per_page)


@router.get("/works/stats", response_model=Stats, summary="The numbers above the shelf, for the same filters")
def stats(account: Reader, db: DbSession, library: LibraryParam = None, genre: GenreParam = None,
          kind: KindParam = None, language: LanguageParam = None) -> Stats:
    filters = _filters(db, account.id, library, genre, kind, language)
    return Stats(**service.stats(db, account.id, filters))


#: Two recordings of one work whose running times differ by less than this are probably the same recording twice.
DUPLICATE_TOLERANCE = 0.03


def duplicate_groups(editions: list[EditionOut]) -> list[list[int]]:
    """The recordings of a work that are probably one recording twice: running times within ``DUPLICATE_TOLERANCE`` of
    each other. Only a hint; nothing is joined or removed by it."""
    audio = sorted((e for e in editions if e.kind == "audio" and e.duration > 0), key=lambda e: e.duration)
    groups: list[list[EditionOut]] = []
    for edition in audio:
        if groups and edition.duration - groups[-1][0].duration <= groups[-1][0].duration * DUPLICATE_TOLERANCE:
            groups[-1].append(edition)
        else:
            groups.append([edition])
    return [sorted(e.id for e in group) for group in groups if len(group) >= 2]


def part_language(view: Any, account: Any) -> str:
    """The language a chapter without a name is called in (``Teil 3``) when its edition has none: the language of
    another edition of the work, else the language of the person's account (empty: English)."""
    known = next((item.edition.language for item in view.editions if item.edition.language), "")
    return known or str(getattr(account, "language", "") or "")


def edition_out(item: EditionView, chapters: list[Any], language: str = "") -> EditionOut:
    edition = item.edition
    return EditionOut(
        **_edition_brief(item).model_dump(), title=edition.title, subtitle=edition.subtitle, isbn=edition.isbn,
        asin=edition.asin, publisher=edition.publisher, published=edition.published,
        library_name=item.library_name, translators=item.translators, authors=item.authors,
        series_name=edition.series_name, series_pos=edition.series_pos, genre=edition.genre,
        genre_guess=edition.genre_guess,
        corrected=item.corrected, matched_by=item.matched_by,
        files=[FileOut(id=f.id, format=f.format, path=f.path, size=f.size, part=f.part,
                       duration=round(f.duration_ms / 1000, 3), sha256=f.sha256,
                       protected=bool((f.probe or {}).get("drm"))) for f in item.files],
        chapters=[ChapterOut(number=c.number, title=chapter_groups.label(c.title, c.number,
                                                                         edition.language or language)
                             if edition.kind == "audio" else c.title, start=c.start_ms / 1000, target=c.target,
                             depth=c.depth) for c in chapters])


@router.get("/works/{work_id}", response_model=WorkOut, summary="One book with its editions, files and chapters")
def read(work_id: int, account: Reader, db: DbSession) -> WorkOut:
    view = service.get_work(db, account.id, work_id)
    chapters = service.chapters_of(db, [v.edition.id for v in view.editions])
    movable = relocate.movable(db, account.id, [v.edition for v in view.editions])
    brief = brief_of(view)
    language = part_language(view, account)
    editions = [edition_out(item, chapters.get(item.edition.id, []), language) for item in view.editions]
    managed = set(db.scalars(libraries.managed_library_ids(account.id)))
    delivered = wrong.deliveries_of(db, [item.edition.id for item in view.editions
                                         if item.edition.library_id in managed])
    trash_days = trash.days(db) if managed & {item.edition.library_id for item in view.editions} else 0
    for edition in editions:
        edition.can_move = edition.id in movable
        if edition.library_id in managed:
            edition.trash_days = trash_days
        found = delivered.get(edition.id)
        if found is not None:
            edition.delivery = DeliveryOut(release=found.title, indexer=found.indexer_name,
                                           delivered_at=found.imported_at, trash_days=trash_days)
    return WorkOut(**brief.model_dump(exclude={"editions"}), description=view.description,
                   original_title=view.original_title, original_language=view.original_language,
                   editions=editions, duplicates=duplicate_groups(editions),
                   later=later_service.on_list(db, account.id, work_id))


class LaterList(BaseModel):
    items: list[WorkBrief]


@router.get("/later", response_model=LaterList, summary="The caller's own list for later, the newest first")
def read_later(account: Reader, db: DbSession) -> LaterList:
    """Books on the caller's own shelves, the same list as in the apps. A book the caller does not see at the moment
    stays on it and comes back; a book they finish leaves it."""
    rows = later_service.listing(db, account.id)
    found = list(db.scalars(select(Work).where(Work.id.in_([row.work_id for row in rows]))))
    seen = {view.work.id: view for view in service.views(db, account.id, found)}
    return LaterList(items=[brief_of(seen[row.work_id]) for row in rows if row.work_id in seen])


@router.put("/later/{work_id}", status_code=204, summary="Put a book on the caller's list for later")
def add_later(work_id: Annotated[int, Path(ge=1, le=2**62)], account: Account, db: DbSession) -> Response:
    brakes.take("wish", account.id)
    later_service.add(db, account.id, work_id)
    return Response(status_code=204)


@router.delete("/later/{work_id}", status_code=204, summary="Take a book off the caller's list for later")
def remove_later(work_id: Annotated[int, Path(ge=1, le=2**62)], account: Account, db: DbSession) -> Response:
    brakes.take("wish", account.id)
    later_service.remove(db, account.id, work_id)
    return Response(status_code=204)


@router.get("/works/{work_id}/cover", summary="The cover of a book, as a WebP picture", response_model=None,
            responses={200: {"content": {"image/webp": {}}}, 304: {"description": "Not changed"}})
def cover(work_id: int, request: Request, account: Reader, db: DbSession,
          v: Annotated[str | None, Query(max_length=40)] = None) -> Response:
    return cover_response(db, account.id, work_id, request, v)


def cover_response(db: DbSession, uid: int, work_id: int, request: Request, v: str | None) -> Response:
    """The cover as the person sees it, with its ``ETag`` (the start of the picture's name); also for the app."""
    name = service.cover_of(db, uid, work_id)
    path = covers.path_of(name)
    if path is None:
        raise error("not_found", "Not found.", 404)
    etag = f'"{name[:20]}"'
    # The address carries the start of the picture's name: while it is the one asked for, the picture cannot change.
    stable = v is not None and name.startswith(v) and len(v) >= 12
    headers: dict[str, Any] = {"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable" if stable
                               else "private, no-cache", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)


class _Sending(FileResponse):
    """A file on its way to a person. It holds one of that person's places for open files (``brakes.open_stream``) until
    the last byte has gone or the connection broke, however the response ended."""

    def __init__(self, *args: Any, release: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._release = release

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


@router.get("/files/{file_id}/download", summary="Download one file of a book", response_model=None,
            responses={200: {"content": {"application/octet-stream": {}}}})
def download(file_id: int, account: Reader, db: DbSession) -> FileResponse:
    """The file as it lies in the library. The request names the file by its number only: the place on the disk comes
    from the database and is checked against the library folder again. A file of a library the caller is not a member of
    is one that does not exist (404). Too many requests, or too many files open at once for one person, are ``429``
    with ``Retry-After``."""
    brakes.take("stream", account.id)
    row, path = service.file_of(db, account.id, file_id)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    release = brakes.open_stream(account.id)
    try:
        return _Sending(path, media_type=service.MEDIA_TYPES.get(row.format, "application/octet-stream"),
                        filename=path.name, content_disposition_type="attachment", headers=headers, release=release)
    except BaseException:
        release()
        raise


@router.get("/files/{file_id}/content", summary="The bytes of one file of a book, for reading and playing",
            response_model=None, responses={200: {"content": {"application/octet-stream": {}}},
                                            206: {"description": "A part of the file (``Range``)"},
                                            304: {"description": "Not changed"}})
def content(file_id: int, request: Request, account: Reader, db: DbSession) -> Response:
    """The file for the reader in the browser and for the players of apps: the same file as ``download``, with
    ``Range`` (a part, for playing) and an ``ETag`` (asked again with ``If-None-Match``, so that a reload costs a
    question and not the whole book). The request names the file by its number only; the place on the disk comes from
    the database and is checked against the library folder again. A file the caller may not see is one that does not
    exist. Too many requests, or too many files open at once for one person, are ``429`` with ``Retry-After`` (a player
    holds one or two files open, so it never meets it)."""
    return content_response(db, account.id, file_id, request)


def content_response(db: DbSession, uid: int, file_id: int, request: Request, *, name_by_id: bool = False) -> Response:
    """The bytes of a file the person may see, with ``Range``, ``If-Range`` and ``ETag``. ``name_by_id``: the name the
    answer gives the file is its number and format (``12.epub``), not the name it has on the disk (for the app, which
    is never told a path)."""
    brakes.take("stream", uid)
    row, path = service.file_of(db, uid, file_id)
    name = f"{row.id}.{row.format}" if name_by_id else path.name
    return stream_response(uid, request, path, row.format, f"{row.id}", name)


def stream_response(uid: int, request: Request, path: Any, fmt: str, key: str, filename: str) -> Response:
    """A file the caller may read, on its way with ``Range``, ``If-Range`` and an ``ETag`` made from ``key`` and the
    file's time and size. The rights and the path were checked by the caller; this holds one of the person's places for
    open files until the last byte has gone."""
    try:
        info = path.stat()
    except OSError:
        raise error("not_found", "Not found.", 404) from None
    etag = '"' + hashlib.sha256(f"{key}:{info.st_mtime_ns}:{info.st_size}".encode()).hexdigest()[:32] + '"'
    # The bytes are data for the page's own scripts. Opened as a page by itself, the address shows and runs nothing.
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; sandbox"}
    wanted = [part.strip() for part in request.headers.get("if-none-match", "").split(",")]
    if etag in wanted or "*" in wanted:
        return Response(status_code=304, headers=headers)
    release = brakes.open_stream(uid)
    try:
        return _Sending(path, media_type=service.MEDIA_TYPES.get(fmt, "application/octet-stream"),
                        stat_result=info, content_disposition_type="attachment", filename=filename, headers=headers,
                        release=release)
    except BaseException:
        release()
        raise


@router.get("/series/{series_id}", response_model=SeriesOut, summary="A series with the volumes the caller sees")
def read_series(series_id: int, account: Reader, db: DbSession) -> SeriesOut:
    series, found = service.get_series(db, account.id, series_id)
    return SeriesOut(id=series.id, name=series.name, works=[brief_of(view) for view in found])


@router.get("/people/{person_id}", response_model=PersonOut,
            summary="An author or narrator with the books of theirs the caller sees")
def read_person(person_id: int, account: Reader, db: DbSession,
                language: Annotated[str | None, Query(max_length=16, pattern=r"^[A-Za-z]{2,3}([_-][A-Za-z0-9]{2,8})?$",
                                                      description="The language of the biography; the caller's own "
                                                                  "when left out")] = None) -> PersonOut:
    person, wrote, narrated = service.get_person(db, account.id, person_id)
    return _person_out(db, account, person, wrote, narrated, language)


def _person_out(db: DbSession, account: Any, person: Any, wrote: list[Any], narrated: list[Any],
                language: str | None = None) -> PersonOut:
    seen = aliases.alias_rows(db, [person.id], libraries.member_library_ids(account.id))
    photo = authors_service.photo_of(db, account.id, person.id)
    credit = authors_service.credit_of(db, account.id, person.id)
    bio = authors_service.bio_of(db, person.id, language or account.language or "en")
    if wrote:
        # An author seen for the first time is looked up in the background.
        people_info.enqueue_missing(db, [person.id])
    return PersonOut(
        id=person.id, name=person.name, aliases=[row.name for row in seen],
        pen_names=[PenNameOut(id=row.id, name=row.name) for row in seen],
        can_split=bool(seen) and _may_split(db, account, person), can_merge=_may_split(db, account, person),
        wrote=[brief_of(v) for v in wrote], narrated=[brief_of(v) for v in narrated],
        photo_url=authors_service.photo_url(person.id, photo[0]) if photo else None,
        credit=PhotoCredit(source=credit.source, author=credit.author, license=credit.license,
                           license_url=credit.license_url, page_url=credit.page_url) if credit else None,
        bio=Bio(texts=[BioText(**item) for item in bio["texts"]], born=bio["born"], died=bio["died"]) if bio else None)


def _may_split(db: DbSession, account: Any, person: Any) -> bool:
    """The operator, or a person with ``manage`` in every library a split touches (the books of the person and of the
    books that proved the name)."""
    if account.role == OPERATOR:
        return True
    touched = aliases.affected_libraries(db, person)
    return bool(touched) and all(libraries.right_in(db, account.id, ident) == "manage" for ident in touched)


@router.post("/people/{person_id}/split", response_model=PersonOut,
             summary="Says that another name of a person is somebody else: the two are two people again, for good")
def split_person(person_id: int, payload: SplitIn, account: BrowserAccount, db: DbSession) -> PersonOut:
    """The name leaves the person: its books go back to it, and nexbooks never joins the two again by itself (a
    person said so). Only the operator, or whoever manages every library the person's books are in (403 else); a person
    or a name the caller does not see is not found (404). The answer is the person as it stands afterwards."""
    person, _wrote, _narrated = service.get_person(db, account.id, person_id)
    row = next((item for item in aliases.alias_rows(db, [person.id], libraries.member_library_ids(account.id))
                if item.id == payload.alias_id), None)
    if row is None:
        raise error("not_found", "Not found.", 404)
    if not _may_split(db, account, person):
        raise error("forbidden", "Only the operator or whoever manages every library of these books can do that.", 403)
    aliases.split(db, person, row, account.id)
    db.commit()
    person, wrote, narrated = service.get_person(db, account.id, person_id)
    return _person_out(db, account, person, wrote, narrated)


@router.post("/people/{person_id}/merge", response_model=PersonOut,
             summary="Says that two authors are one person, and which name stays; a split takes it back")
def merge_person(person_id: int, payload: MergeIn, account: BrowserAccount, db: DbSession) -> PersonOut:
    """The two authors become one: the books of both are the books of the name that stays, the other name leads to it,
    and a work of the same title is one work. nexbooks keeps it until somebody splits the name off again
    (``POST /people/{id}/split``). Only the operator, or whoever manages every library of the books of both (403
    else); an author the caller sees no book of is not found (404), and an author is not joined with themselves (422).
    The answer is the author that stays."""
    person, _wrote, _narrated = service.get_person(db, account.id, person_id)
    other, _wrote, _narrated = service.get_person(db, account.id, payload.other_id)
    if person.id == other.id:
        raise error("same_person", "That is the same author.", 422)
    if not (_may_split(db, account, person) and _may_split(db, account, other)):
        raise error("forbidden", "Only the operator or whoever manages every library of these books can do that.", 403)
    keep, drop = (person, other) if payload.keep == "this" else (other, person)
    keep_id, drop_id = keep.id, drop.id
    aliases.merge_by_hand(db, keep, drop, account.id)
    if db.get(Person, drop_id) is not None or db.get(Person, keep_id) is None:
        # The evidence in the library does not allow it (a chain of names through a third): nothing is changed.
        db.rollback()
        raise error("cannot_join", "These two cannot be joined.", 409)
    db.commit()
    person, wrote, narrated = service.get_person(db, account.id, keep_id)
    return _person_out(db, account, person, wrote, narrated)
