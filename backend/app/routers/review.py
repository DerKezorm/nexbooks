"""Matching books with their sources, from the side of the people who look after a library: the list of books the
matching
is not sure about, the proposals to take or to search for, and the corrections of a book by hand.

**Only a member with ``manage``** may see the list and decide (the operator is no exception: which book a book is, is
for
those who look after it; they make themselves a member). A library one is not a member of is one that is not there
(404);
a member who may only read gets 403. A book is corrected, joined to another work or split off by a member with
``manage``
in *its* library, and may be joined only to a work the person can see.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import BrowserAccount, DbSession, Reader
from ..errors import error
from ..models import AUTHOR, BookFile, Credit, Edition, MatchEntry, Person, Work
from ..services import brakes, catalog, covers, genres, libraries
from ..services import works as works_service
from ..services.match import apply, filefacts, review
from ..services.match.gap import gap_of, lack_info
from ..services.procure import discard, wrong
from ..services.procure import trash as procure_trash
from ..services.sources import http
from ..services.sources.model import Candidate

router = APIRouter(prefix="/api/v1", tags=["matching"])

#: In English, for whoever uses the API directly; the interface words the codes itself.
WHY = {
    "isbn_match": "The ISBN is the same.",
    "asin_match": "The ASIN is the same.",
    "title_exact": "The title is the same.",
    "title_main": "The title is the same up to the subtitle.",
    "title_part": "The title is part of the other, or the other of it.",
    "title_close": "The title is close.",
    "author_match": "The author is the same.",
    "author_weak": "Only the last name of the author fits.",
    "author_unknown": "The author is not known on one side.",
    "language_ok": "The language fits.",
    "language_missing": "The source does not list the language of the file.",
    "volume_ok": "The volume fits.",
    "year_ok": "The year fits.",
    "runtime_ok": "The running time fits within 5 percent.",
    "runtime_plausible": "The running time is plausible, but it is no proof.",
    "title_series": "The title is the name of the series the book is in.",
    "runtime_differs": "The running time is another.",
    "title_differs": "The title is another.",
    "author_differs": "The author is another.",
    "volume_differs": "It is another volume.",
    "language_differs": "It is in another language.",
    "companion": "It is a companion to the book, not the book.",
    "collection": "It is a collection of books.",
    "isbn_conflict": "The ISBN is the same, but it looks like another book.",
    "subtitle_differs": "The main title is the same, the subtitle names another book.",
    "series_only": "The title is only the name of the series, and the book has a title of its own.",
    "second_source": "A second source knows the same book.",
    "year_name_ok": "The year in the name of the folder fits.",
    "year_off": "The year is another.",
    "no_isbn": "The file has no ISBN.",
    "one_source": "Only one source knows it.",
    "language_unknown": "The language of the file is not known.",
    "no_author": "The file names no author.",
    "no_volume": "The file does not say which volume it is.",
    "no_year": "The file has no year.",
    "no_runtime": "The source does not give a running time.",
}
REASON = {
    "nothing_found": "The sources know nothing under this title.",
    "nothing_fits": "What the sources know does not fit.",
    "low_score": "What fits is not close enough.",
    "ambiguous": "More than one book fits about as well.",
    "sources_disagree": "The sources name different books.",
    "runtime_unproven": "The running time does not prove which recording it is.",
    "possible_translation": "It may be the book in another language; a person has to say.",
    "isbn_conflict": "The ISBN is the same, but the title or the author does not fit it.",
    "one_source": "Only one source knows the book, and the file has no ISBN.",
    "subtitle_differs": "The main title is the same, but the subtitle names another book of the series.",
    "series_only": "The source knows only the series under this title, not the book.",
    "author_weak": "Only the last name of the author fits.",
    "author_unknown": "The author is not known.",
    "language_missing": "The source does not list this language.",
    "unlinked": "A person took the match back.",
    "protected": "The book is copy-protected: nexbooks cannot open it and does nothing to get around the protection.",
    "failed": "Something went wrong in nexbooks while matching; the log says what.",
}


class WhyOut(BaseModel):
    code: str
    text: str


class ProposalOut(BaseModel):
    id: str
    source: Literal["hardcover", "openlibrary", "audible", "dnb"]
    title: str
    subtitle: str
    authors: list[str]
    narrators: list[str]
    year: int | None
    languages: list[str]
    series: str
    series_pos: float | None
    #: A recording's running time in minutes, and what kind of recording the source calls it.
    runtime_min: int | None
    variant: str
    market: str
    score: int
    why: list[WhyOut]
    #: What is missing to be sure of it (grey), and what speaks against it though it could still be the book (red); the
    #: numbers some of them carry (``year_off``: the year of the file and of the source).
    missing: list[WhyOut]
    against: list[WhyOut]
    detail: dict[str, dict[str, int | str]]
    #: What would make this proposal surer, as words (`isbn`, `second_source`, `language`, `author`, `runtime`,
    #: `volume`,
    #: `year`), the same as `gap.missing` of the item for its best proposal; only what would really make it sure. Each
    #: one is enough alone (`lacks_join` `or`) or all are needed together (`and`). `cap` is what holds it back whatever
    #: its points (`author_weak`, `author_unknown`, `language_missing`), empty for nothing.
    lacks: list[str]
    lacks_join: str
    cap: str
    #: It cannot be the book (the last of ``why`` says why); shown for the person to judge.
    fatal: bool
    #: Found by a search a person made, not by the matching.
    found: bool
    #: The address of the picture at nexbooks (fetched from the source when first asked for); null: it has none.
    cover_url: str | None


class TieOut(BaseModel):
    #: What tells two proposals that are about as good apart: ``volume``, ``year``, ``language``, ``variant``,
    #: ``title``,
    #: or ``nothing`` (the file gives no way to tell).
    difference: str
    a: str | None
    b: str | None


class GapOut(BaseModel):
    """What stands between the book and "sure": it is sure from ``sure_at`` points and has ``points``; ``missing`` is
    what the file or the sources lack that would help (``isbn``, ``second_source``, ``language``, ``author``,
    ``runtime``, ``volume``, ``year``); ``tie`` is set when two proposals are about as good."""

    sure_at: int
    points: int
    missing: list[str]
    #: `or`: each of `missing` is enough alone; `and`: all of them are needed.
    join: str
    #: What holds the best proposal back whatever its points (`author_weak`, `author_unknown`, `language_missing`,
    #: `subtitle_differs`, `series_only`).
    cap: str
    tie: TieOut | None


class ReviewItem(BaseModel):
    id: int
    edition_id: int
    work_id: int
    library_id: int
    state: Literal["maybe", "open"]
    reason: str
    reason_text: str
    kind: Literal["ebook", "audio"]
    title: str
    subtitle: str
    authors: list[str]
    language: str
    year: int | None
    series: str
    series_pos: float | None
    #: The running time of the files in minutes, to hold against those of the proposals; null for an e-book.
    runtime_min: int | None
    #: How many days the trash keeps a book that is thrown away from the list.
    trash_days: int
    #: The file (inside the library) the book was read from.
    file: str
    cover_url: str | None
    gap: GapOut | None
    proposals: list[ProposalOut]


class ReviewList(BaseModel):
    total: int
    items: list[ReviewItem]


class FileLineOut(BaseModel):
    id: int
    #: The name of the file and the folder it lies in, inside the library (``/``); never a path on the disk.
    name: str
    folder: str
    format: str
    size: int
    part: int
    #: Of a recording: seconds. Of an e-book: 0.
    duration: float
    pages: int
    chapters: int
    protected: bool


class SaysOut(BaseModel):
    """What the files say about themselves; an empty value is "the file says nothing about it"."""

    title: str
    subtitle: str
    authors: list[str]
    narrators: list[str]
    series: str
    series_pos: float | None
    isbn: str
    asin: str
    publisher: str
    year: int | None
    language: str
    #: The language the text of an e-book is written in, found by reading a sample of it.
    text_language: str
    genre: str
    subjects: list[str]
    pages: int
    #: The pages were counted (a PDF) and not estimated from the amount of text.
    pages_counted: bool
    duration: float
    chapters: int


class CompareRowOut(BaseModel):
    field: Literal["title", "authors", "narrators", "series", "volume", "isbn", "language", "text_language", "year",
                   "runtime", "publisher"]
    file: str
    source: str
    #: ``agrees``; ``similar`` (close, a person looks); ``differs`` (the file contradicts the proposal);
    #: ``file_silent`` (the file says nothing about it); ``source_silent`` (the source does not know it).
    state: Literal["agrees", "similar", "differs", "file_silent", "source_silent"]
    #: ``minutes`` when both values are numbers of minutes.
    unit: Literal["", "minutes"]


class ProposalFactsOut(BaseModel):
    proposal_id: str
    rows: list[CompareRowOut]


class FileFactsOut(BaseModel):
    entry_id: int
    edition_id: int
    kind: Literal["ebook", "audio"]
    #: The folder of the first file, inside the library; empty for the top of the library.
    folder: str
    files: list[FileLineOut]
    says: SaysOut
    #: The picture inside the file (``/review/{id}/file-cover``); null when the file has none.
    cover_url: str | None
    proposals: list[ProposalFactsOut]


class SentAwayOut(BaseModel):
    #: The entry of the trash the files went into (``/procure/trash``): bring them back from there. Null when no file of
    #: the edition was there to move (``missing`` names them).
    trash_id: int | None
    #: ``searching`` (a wish looks for another release now), ``idle`` (the wish is there, but fetching by itself is
    #: switched off), ``have`` (the book has another edition of this kind), ``none`` (no book left to wish for) or
    #: ``failed`` (``code`` says why no wish was made or started).
    replacement: Literal["searching", "idle", "have", "none", "failed"]
    code: str
    #: The page of the book to go to; null when the book is gone with this edition.
    work_id: int | None
    #: The files of the edition that were gone from the disk (names inside the library); the others went to the trash.
    missing: list[str]
    #: How many people had a place, a mark or a finished pass in the edition: all of it is kept with the files and comes
    #: back when they are taken out of the trash.
    people: int


class DiscardedOut(BaseModel):
    #: The entry of the trash the files went into (``/procure/trash``): bring them back from there. Null when no file of
    #: the edition was there to move (``missing`` names them).
    trash_id: int | None
    #: The page of the book to go to; null when the book is gone with this edition.
    work_id: int | None
    #: The files of the edition that were gone from the disk (names inside the library); the others went to the trash.
    missing: list[str]
    #: How many people had a place, a mark or a finished pass in the edition: it is kept with the files.
    people: int


class Remaining(BaseModel):
    to_look_at: int


class ProposalIn(BaseModel):
    proposal_id: str = Field(min_length=3, max_length=160)


class SearchIn(BaseModel):
    title: str = Field(default="", max_length=review.TEXT_MAX)
    author: str = Field(default="", max_length=review.TEXT_MAX)


class CorrectionIn(BaseModel):
    """What a person corrects; a field left out stays, ``null`` takes a correction back (the title too: then the file
    or the source speaks again), an empty text is a correction too (to nothing), except the title: a book is never
    without one."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    subtitle: str | None = Field(default=None, max_length=300)
    authors: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(default=None, max_length=10)
    #: Of a recording: who reads it.
    narrators: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(default=None, max_length=20)
    series: str | None = Field(default=None, max_length=200)
    series_pos: float | None = Field(default=None, gt=0, lt=10000)
    genre: Literal["roman", "krimi", "fantasy", "scifi", "klassiker", "sachbuch", "kinder", "lyrik", ""] | None = None
    language: str | None = Field(default=None, max_length=8, pattern=r"^([A-Za-z]{2,3})?$")
    #: Of a recording: which reading it is. Set by a person when nothing proves it (the running time does not, the file
    #: does not say).
    variant: Literal["unabridged", "abridged", "dramatized"] | None = None


class JoinIn(BaseModel):
    work_id: int = Field(ge=1, le=2**31 - 1)


class WorkRef(BaseModel):
    work_id: int


def _proposal(entry: MatchEntry, item: dict[str, Any]) -> ProposalOut | None:
    c = item.get("c")
    if not isinstance(c, dict):
        return None
    fatal = str(item.get("fatal") or "")
    codes = [code for code in item.get("why", []) if isinstance(code, str)]
    cover = bool(c.get("cover_url")) or bool(item.get("cover"))
    info = lack_info(item)
    return ProposalOut(
        id=str(item.get("id", ""))[:160], source=c.get("source", "openlibrary"), title=str(c.get("title", "")),
        subtitle=str(c.get("subtitle", "")), authors=[a for a in c.get("authors", []) if isinstance(a, str)][:6],
        narrators=[a for a in c.get("narrators", []) if isinstance(a, str)][:4],
        year=c.get("year") if isinstance(c.get("year"), int) else None,
        languages=[x for x in c.get("languages", []) if isinstance(x, str)][:6], series=str(c.get("series", "")),
        series_pos=c.get("series_pos") if isinstance(c.get("series_pos"), int | float) else None,
        runtime_min=c.get("runtime_min") if isinstance(c.get("runtime_min"), int) else None,
        variant=str(c.get("variant", "")), market=str(c.get("market", "")),
        score=int(item.get("score") or 0), why=[WhyOut(code=code, text=WHY.get(code, "")) for code in codes],
        missing=[WhyOut(code=code, text=WHY.get(code, "")) for code in _codes(item.get("missing"))],
        against=[WhyOut(code=code, text=WHY.get(code, "")) for code in _codes(item.get("against"))],
        detail=_detail(item.get("detail")), lacks=info.words, lacks_join=info.join, cap=info.cap,
        fatal=bool(fatal), found=bool(item.get("found")),
        cover_url=f"/api/v1/review/{entry.id}/cover?id={_quote(str(item.get('id', '')))}" if cover else None)


def _codes(value: Any) -> list[str]:
    return [code for code in value if isinstance(code, str)][:10] if isinstance(value, list) else []


def _detail(value: Any) -> dict[str, dict[str, int | str]]:
    """The numbers some reasons carry, as far as they are what they should be (the proposals are a JSON column)."""
    if not isinstance(value, dict):
        return {}
    return {str(key)[:30]: {str(k)[:20]: v for k, v in inner.items() if isinstance(v, int | str)}
            for key, inner in list(value.items())[:10] if isinstance(inner, dict)}


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe=":")


def _items(db: DbSession, entries: list[MatchEntry]) -> list[ReviewItem]:
    if not entries:
        return []
    editions = {e.id: e for e in db.scalars(select(Edition).where(Edition.id.in_([x.edition_id for x in entries])))}
    people: dict[int, list[str]] = {}
    for edition_id, name in db.execute(
            select(Credit.edition_id, Person.name).join(Person, Person.id == Credit.person_id)
            .where(Credit.edition_id.in_(list(editions)), Credit.role == AUTHOR).order_by(Credit.edition_id,
                                                                                         Credit.position)):
        people.setdefault(edition_id or 0, []).append(name)
    firsts: dict[int, str] = {}
    for edition_id, path in db.execute(
            select(BookFile.edition_id, func.min(BookFile.path)).where(
                BookFile.edition_id.in_(list(editions)), BookFile.role == "book", BookFile.missing_at.is_(None))
            .group_by(BookFile.edition_id)):
        firsts[edition_id or 0] = path
    out = []
    for entry in entries:
        edition = editions.get(entry.edition_id)
        if edition is None:
            continue
        proposals = [p for p in (_proposal(entry, item) for item in entry.proposals or [] if isinstance(item, dict))
                     if p is not None]
        out.append(ReviewItem(
            id=entry.id, edition_id=edition.id, work_id=edition.work_id, library_id=edition.library_id,
            state=entry.state if entry.state in ("maybe", "open") else "open",  # type: ignore[arg-type]
            reason=entry.reason, reason_text=REASON.get(entry.reason, ""), kind=edition.kind,  # type: ignore[arg-type]
            title=edition.title, subtitle=edition.subtitle, authors=people.get(edition.id, []),
            language=edition.language, year=int(edition.published[:4]) if edition.published[:4].isdigit() else None,
            series=edition.series_name, series_pos=edition.series_pos,
            runtime_min=round(edition.duration / 60) if edition.kind == "audio" and edition.duration else None,
            trash_days=procure_trash.days(db), file=firsts.get(edition.id, ""),
            cover_url=f"/api/v1/editions/{edition.id}/cover?v={edition.cover[:12]}" if edition.cover else None,
            gap=GapOut(**gap) if (gap := gap_of(entry.reason, [p for p in entry.proposals or []
                                                              if isinstance(p, dict)])) else None,
            proposals=proposals))
    return out


def _entry_for(db: DbSession, account: Any, entry_id: int) -> tuple[MatchEntry, Edition]:
    """The entry for a member with ``manage`` in its library; 404 for anybody else who is not a member."""
    entry, edition = review.entry_of(db, entry_id)
    libraries.require_reviewer(db, account.id, entry.library_id)
    return entry, edition


def _remaining(db: DbSession, library_id: int) -> Remaining:
    return Remaining(to_look_at=review.to_look_at(db, [library_id]).get(library_id, 0))


def _source_failed(exc: http.SourceError) -> Any:
    if exc.code == "busy":
        out = error("source_busy", "The source is busy. Try again in a moment.", 429)
        out.headers = {"Retry-After": str(int(exc.values.get("retry_after", 5)))}
        return out
    if exc.code == "off":
        return error("sources_off", "No source is switched on.", 409)
    return error("source_failed", "The sources could not be asked.", 502, reason=exc.code)


@router.get("/libraries/{library_id}/review", response_model=ReviewList,
            summary="The books the matching is not sure about (a member with manage)")
def listing(library_id: int, account: Reader, db: DbSession, limit: int = Query(default=50, ge=1, le=200),
            offset: int = Query(default=0, ge=0, le=1_000_000)) -> ReviewList:
    libraries.require_reviewer(db, account.id, library_id)
    base = review.listing_query(library_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    entries = list(db.scalars(base.order_by(Edition.title_key, MatchEntry.id).limit(limit).offset(offset)))
    return ReviewList(total=total, items=_items(db, entries))


@router.get("/review", response_model=ReviewList,
            summary="The books the matching is not sure about, in every library the caller looks after (manage)")
def listing_all(account: Reader, db: DbSession, limit: int = Query(default=50, ge=1, le=200),
                offset: int = Query(default=0, ge=0, le=1_000_000)) -> ReviewList:
    """One list over all libraries in which the caller has ``manage`` (the note "X books need your look" counts
    them all, so the page shows them all; each entry names its library). Nobody else's library, the operator's
    neither, unless he looks after it. A filter by library is ``/libraries/{id}/review``."""
    base = review.listing_query(libraries.managed_library_ids(account.id))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    entries = list(db.scalars(base.order_by(Edition.title_key, MatchEntry.id).limit(limit).offset(offset)))
    return ReviewList(total=total, items=_items(db, entries))


@router.post("/review/{entry_id}/accept", response_model=Remaining,
             summary="Take a proposal for this book (a member with manage)")
def accept(entry_id: int, payload: ProposalIn, account: BrowserAccount, db: DbSession) -> Remaining:
    entry, edition = _entry_for(db, account, entry_id)
    review.accept(db, entry, edition, payload.proposal_id, account.id)
    return _remaining(db, entry.library_id)


@router.post("/review/{entry_id}/keep", response_model=Remaining,
             summary="Leave this book as the files describe it (a member with manage)")
def keep(entry_id: int, account: BrowserAccount, db: DbSession) -> Remaining:
    entry, _edition = _entry_for(db, account, entry_id)
    review.keep(db, entry, account.id)
    return _remaining(db, entry.library_id)


@router.post("/review/{entry_id}/search", response_model=ReviewItem,
             summary="Look for other books in the sources with a title and an author (a member with manage)")
def search(entry_id: int, payload: SearchIn, account: BrowserAccount, db: DbSession) -> ReviewItem:
    entry, edition = _entry_for(db, account, entry_id)
    brakes.take("review_search", account.id)
    try:
        review.search_more(db, entry, edition, payload.title, payload.author)
    except http.SourceError as exc:
        raise _source_failed(exc) from exc
    return _items(db, [entry])[0]


@router.get("/review/{entry_id}/cover", summary="The cover picture of a proposal, as a WebP picture (fetched from the "
            "source the first time, so only in a browser)",
            response_model=None,
            responses={200: {"content": {"image/webp": {}}}})
def proposal_cover(entry_id: int, account: BrowserAccount, db: DbSession,
                   id: Annotated[str, Query(max_length=160)]) -> Response:
    entry, _edition = _entry_for(db, account, entry_id)
    name = review.proposal_cover(db, entry, id)
    path = covers.path_of(name or "")
    if path is None:
        raise error("not_found", "Not found.", 404)
    return FileResponse(path, media_type="image/webp", headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


def _says_out(says: filefacts.Says) -> SaysOut:
    return SaysOut(title=says.title, subtitle=says.subtitle, authors=says.authors, narrators=says.narrators,
                   series=says.series, series_pos=says.series_pos, isbn=says.isbn, asin=says.asin,
                   publisher=says.publisher, year=says.year, language=says.language, text_language=says.text_language,
                   genre=says.genre, subjects=says.subjects, pages=says.pages, pages_counted=says.pages_counted,
                   duration=says.duration, chapters=says.chapters)


@router.get("/review/{entry_id}/file", response_model=FileFactsOut,
            summary="What the files of a book say about themselves, beside each proposal (a member with manage)")
def file_facts(entry_id: int, account: Reader, db: DbSession) -> FileFactsOut:
    """The files of the book (name and folder inside the library, format, size), what is written in them (title,
    authors, narrators, series, ISBN, publisher, year, language, running time, chapters, pages) and, for every
    proposal, field by field whether the file agrees, is close, contradicts it, or says nothing. Read from what the
    scan kept: no file is opened. To read in the book or to listen, a client fetches the file the usual way
    (``/files/{id}/content``)."""
    entry, edition = _entry_for(db, account, entry_id)
    facts = filefacts.facts_of(db, edition)
    proposals: list[ProposalFactsOut] = []
    for item in entry.proposals or []:
        if not isinstance(item, dict) or not isinstance(item.get("c"), dict):
            continue
        try:
            candidate = Candidate.from_json(item["c"])
        except ValueError:
            continue
        rows = [CompareRowOut(field=row.field, file=row.file, source=row.source, state=row.state,  # type: ignore[arg-type]
                              unit=row.unit)  # type: ignore[arg-type]
                for row in filefacts.compare(facts, candidate, edition.kind)]
        proposals.append(ProposalFactsOut(proposal_id=str(item.get("id", ""))[:160], rows=rows))
    cover = covers.path_of(facts.says.cover_file) is not None
    return FileFactsOut(
        entry_id=entry.id, edition_id=edition.id, kind=edition.kind,  # type: ignore[arg-type]
        folder=facts.folder, says=_says_out(facts.says), proposals=proposals,
        files=[FileLineOut(id=f.id, name=f.name, folder=f.folder, format=f.format, size=f.size, part=f.part,
                           duration=f.duration, pages=f.pages, chapters=f.chapters, protected=f.protected)
               for f in facts.files],
        cover_url=f"/api/v1/review/{entry.id}/file-cover?v={facts.says.cover_file[:12]}" if cover else None)


@router.get("/review/{entry_id}/file-cover", summary="The picture inside the file of a book, as a WebP picture "
            "(a member with manage)", response_model=None, responses={200: {"content": {"image/webp": {}}}})
def file_cover(entry_id: int, account: Reader, db: DbSession,
               v: Annotated[str | None, Query(max_length=40)] = None) -> Response:
    _entry, edition = _entry_for(db, account, entry_id)
    path = covers.path_of(filefacts.facts_of(db, edition).says.cover_file)
    if path is None:
        raise error("not_found", "Not found.", 404)
    return FileResponse(path, media_type="image/webp", headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


# ---- Editions ---------------------------------------------------------------------------------------------------


def _edition_managed(db: DbSession, uid: int, edition_id: int) -> Edition:
    edition = db.get(Edition, edition_id)
    if edition is None:
        raise error("not_found", "Not found.", 404)
    libraries.require_reviewer(db, uid, edition.library_id)
    return edition


@router.get("/editions/{edition_id}/cover", summary="The cover of an edition, as a WebP picture", response_model=None,
            responses={200: {"content": {"image/webp": {}}}, 304: {"description": "Not changed"}})
def edition_cover(edition_id: int, request: Request, account: Reader, db: DbSession,
                  v: Annotated[str | None, Query(max_length=40)] = None) -> Response:
    edition = db.get(Edition, edition_id)
    if edition is None or edition.missing_at is not None:
        raise error("not_found", "Not found.", 404)
    libraries.require_readable(db, account.id, edition.library_id)
    path = covers.path_of(edition.cover)
    if path is None:
        raise error("not_found", "Not found.", 404)
    etag = f'"{edition.cover[:20]}"'
    stable = v is not None and edition.cover.startswith(v) and len(v) >= 12
    headers = {"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable" if stable else "private, no-cache",
               "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)


@router.post("/editions/{edition_id}/unlink", response_model=Remaining,
             summary="Take the match with the sources back (a member with manage)")
def unlink(edition_id: int, account: BrowserAccount, db: DbSession) -> Remaining:
    edition = _edition_managed(db, account.id, edition_id)
    entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == edition.id))
    if entry is None:
        raise error("not_found", "Not found.", 404)
    review.unlink(db, entry, edition, account.id)
    return _remaining(db, edition.library_id)


@router.patch("/editions/{edition_id}", response_model=WorkRef,
              summary="Correct a book by hand: title, subtitle, authors, narrators, series, volume, genre, language, "
                      "the reading of a recording (a member with manage)")
def correct(edition_id: int, payload: CorrectionIn, account: BrowserAccount, db: DbSession) -> WorkRef:
    edition = _edition_managed(db, account.id, edition_id)
    sent = payload.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}
    for name, value in sent.items():
        if name == "genre" and value is not None and value not in ("", *genres.GENRES):
            raise error("invalid_input", "That genre is not one of the genres.", 422, field="genre")
        if name == "language" and isinstance(value, str):
            value = value.lower()
        if name == "variant" and value is not None and edition.kind != "audio":
            raise error("invalid_input", "Only a recording has a kind of reading.", 422, field="variant")
        if name == "narrators" and value is not None and edition.kind != "audio":
            raise error("invalid_input", "Only a recording has narrators.", 422, field="narrators")
        if isinstance(value, str):
            value = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
        if name == "title" and value is not None and not value:
            raise error("invalid_input", "A book needs a title.", 422, field="title")
        if name in ("authors", "narrators") and isinstance(value, list):
            value = [re.sub(r"[\x00-\x1f\x7f]", " ", a).strip() for a in value if a.strip()]
        changes[name] = value
    apply.correct(db, edition, changes)
    # The corrected book is looked at again, unless a person decided on a match for it.
    entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == edition.id))
    if entry is not None and entry.decision == "kept":
        entry.decision, entry.state, entry.reason = "", "pending", ""
    db.commit()
    # What the other books of the author are guessed to be rests on the genres they have: a correction may change one.
    catalog.refresh_guesses(db, edition.library_id)
    from ..services.match import run as matching

    matching.enqueue_pending(db)
    return WorkRef(work_id=edition.work_id)


@router.post("/editions/{edition_id}/join", response_model=WorkRef,
             summary="Put a book under another work of the caller's (a member with manage)")
def join(edition_id: int, payload: JoinIn, account: BrowserAccount, db: DbSession) -> WorkRef:
    edition = _edition_managed(db, account.id, edition_id)
    # The work must be one the caller sees; else it is not there.
    works_service.get_work(db, account.id, payload.work_id)
    work = db.get(Work, payload.work_id)
    if work is None:
        raise error("not_found", "Not found.", 404)
    apply.join(db, edition, work)
    db.commit()
    catalog.refresh_guesses(db, edition.library_id)
    # A wish for the book that the joined edition fulfils is done (else it would look on for the kind it has now).
    from ..services.procure import wishes as wish_service

    wish_service.met_elsewhere(db, {edition.work_id})
    return WorkRef(work_id=edition.work_id)


@router.post("/editions/{edition_id}/split", response_model=WorkRef,
             summary="Make a book a work of its own again (a member with manage)")
def split(edition_id: int, account: BrowserAccount, db: DbSession) -> WorkRef:
    edition = _edition_managed(db, account.id, edition_id)
    work = apply.split(db, edition)
    db.commit()
    catalog.refresh_guesses(db, edition.library_id)
    return WorkRef(work_id=work.id)



@router.post("/editions/{edition_id}/wrong-book", response_model=SentAwayOut,
             summary="Send away a book that nexbooks fetched and that is not the one wished (a member with manage, in "
                     "a browser)",
             responses={404: {"description": "not_found"},
                        409: {"description": "not_fetched, trash_failed, hidden_folder_link"}})
def wrong_book(edition_id: int, account: BrowserAccount, db: DbSession) -> SentAwayOut:
    """The files go into the trash of the library (``/procure/trash`` brings them back), the release that delivered them
    is put on the blocklist for the book wished (the blocklist lifts it), the history says so, and a new wish looks for
    another release. Only for an edition that came from a release; the interface asks first."""
    sent = wrong.send_away(db, account, edition_id)
    return SentAwayOut(trash_id=sent.trash_id, replacement=sent.replacement,  # type: ignore[arg-type]
                       code=sent.code, work_id=sent.work_id, missing=sent.missing, people=sent.people)


@router.post("/editions/{edition_id}/trash", response_model=DiscardedOut,
             summary="Put one edition of a book in the trash of its library, whatever brought it there (a member with "
                     "manage, in a browser)",
             responses={404: {"description": "not_found"}, 409: {"description": "trash_failed, hidden_folder_link"}})
def trash_edition(edition_id: int, account: BrowserAccount, db: DbSession) -> DiscardedOut:
    """The files go into the trash of the library (``/procure/trash`` brings them back, with the place, the bookmarks
    and the finished passes of the people who read or heard the edition), and the history says so. Nothing is blocked
    and nothing is looked for. The interface asks first."""
    done = discard.discard(db, account, edition_id)
    return DiscardedOut(trash_id=done.trash_id, work_id=done.work_id, missing=done.missing, people=done.people)
