"""Fetching books, the person's side: the manual search of the indexers for one book with every release
judged, loading a release, and the blocklist.

Everything here needs the right ``acquire`` (or ``manage``) in a library that takes the kind of book: the operator has
no right of his own (who fetches books for a library is for the people who have the right in it). A search is the
person's own; a release is loaded only out of one's own search.

* ``POST /procure/searches`` starts a search for a work of a library one sees (``work_id``) or for a book of the
  sources (``book``, the token of its page) and answers at once; ``GET /procure/searches/{id}`` shows how far each
  indexer is and, once all are done, the releases with their judgement.
* ``POST /procure/searches/{id}/releases/{release_id}/load`` hands a release to a download program. One that was
  turned down needs ``force`` (the interface asks first).
* ``POST /procure/searches/{id}/releases/{release_id}/block`` puts a release on the blocklist of the book,
  ``GET /procure/blocklist`` lists it, ``DELETE /procure/blocklist/{id}`` takes an entry off again.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from .. import clock
from ..deps import Account, DbSession, Reader
from ..models import Account as Account_
from ..models import Download, ReleaseSearch, SearchRelease
from ..services.procure import blocklist, budget, judge, loading, rations, search

logger = logging.getLogger("nexbooks.procure")

router = APIRouter(prefix="/api/v1/procure", tags=["procure"])

Kind = Literal["ebook", "audio"]


class ReleaseSearchIn(BaseModel):
    work_id: int | None = Field(default=None, ge=1, description="A work of a library the caller sees.")
    book: str | None = Field(default=None, max_length=24, description="The token of a book of the sources.")
    language: str | None = Field(default=None, min_length=2, max_length=8,
                                 description="The language looked for (a code), one the operator allows "
                                             "(``language_not_accepted`` else); by default every language he allows. "
                                             "A language the indexer categories do not know means the same.")
    new: bool = Field(default=False, description="Search again, though the caller's search for this book finished "
                                                 "less than fifteen minutes ago and could be shown again.")


class IndexerProgressOut(BaseModel):
    id: int
    name: str
    kind: Literal["newznab", "torznab"]
    #: waiting, searching, done, failed, timeout, skipped
    state: str
    found: int
    error: str = Field(description="The code of a failure (indexer_limit_reached ...); empty otherwise.")
    until: str | None = Field(default=None, description="Until when a resting indexer rests.")
    limit: int | None = Field(default=None, description="The operator's limit of requests for the day, when it (or a "
                                                         "person's share of it) is what stopped the indexer.")


class RestingOut(BaseModel):
    id: int
    name: str
    cause: Literal["daily_limit", "share_used", "request_limit", "api_limit", "key_rejected", "server_error",
                   "grab_limit"] = Field(description="Why it is left alone: the operator's limit of the day, the "
                                                     "person's share of it, a limit or a pause of the indexer itself, "
                                                     "a refused key.")
    until: datetime = Field(description="Until when.")
    limit: int | None = Field(default=None, description="The limit of the day, for daily_limit and share_used.")


class Reason(BaseModel):
    code: str
    arg: Any = None


class ReleaseOut(BaseModel):
    id: int
    title: str
    indexer: str
    protocol: Literal["usenet", "torrent"]
    kind: Kind
    format: str = Field(description="In the words of the ranking (epub, m4b_aac, mp3_192 ...); empty when unknown.")
    format_how: Literal["name", "estimate", "unknown"]
    tier: int = Field(description="The place of the format in the ranking, 0 first.")
    language: str
    language_sure: bool = Field(description="A mark or the words of the name say it; otherwise it is presumed.")
    language_how: str
    variant: str
    bitrate: int | None
    size: int | None
    age_days: int | None
    seeders: int | None
    peers: int | None
    grabs: int | None
    score: int
    rank: int | None = Field(description="The place among those of its kind that pass, 1 first; null when turned down.")
    reasons: list[Reason]
    blocked_by: int | None = Field(default=None, description="The blocklist entry that turns it down, when one does.")


class ReleaseCardOut(BaseModel):
    kind: Kind
    total: int
    passing: int
    reasons: list[dict[str, Any]] = Field(description="The most frequent reasons: [{code, count}].")
    top: ReleaseOut | None
    goal: str = Field(description="The goal of the ranking for this kind.")
    below_goal: bool = Field(description="The best release is below the goal: nexbooks would look on.")
    have: str | None = Field(description="The best format there is already in this language; null for none.")


class ReleaseSearchOut(BaseModel):
    id: int
    state: Literal["queued", "running", "done", "failed"]
    error: str
    language: str = Field(description="The language looked for; empty when every accepted language is taken.")
    languages: list[str] = Field(description="The languages a release may be in.")
    kinds: list[Kind]
    work_id: int | None
    book: str | None = Field(description="The token of the book of the sources, when the search is for one.")
    title: str
    author: str
    created_at: datetime
    finished_at: datetime | None
    indexers: list[IndexerProgressOut]
    resting: list[RestingOut] = Field(description="The indexers that cannot be asked right now (read live, not at the "
                                                  "time of the search).")
    can_search_again: bool = Field(description="A new search could ask an indexer now: at least one is not resting and "
                                               "the person has searches left today.")
    searches_used_until: datetime | None = Field(
        description="The person has made the searches of the day they may; a new one is possible from this moment.")
    cards: list[ReleaseCardOut]
    releases: list[ReleaseOut]


def _age(release: SearchRelease) -> int | None:
    if release.published_at is None:
        return None
    return max(0, int((clock.now() - release.published_at).total_seconds() // 86400))


def _release(row: SearchRelease, blocked: dict[tuple[str, str], int] | None = None) -> ReleaseOut:
    entry = (blocked or {}).get((blocklist.key_of(row.title), row.protocol))
    return ReleaseOut(blocked_by=entry,
        id=row.id, title=row.title, indexer=row.indexer_name, protocol=row.protocol,  # type: ignore[arg-type]
        kind=row.kind, format=row.format, format_how=row.format_how or "unknown",  # type: ignore[arg-type]
        tier=row.tier, language=row.language, language_sure=row.language_how in judge.SURE_LANGUAGE,
        language_how=row.language_how, variant=row.variant, bitrate=row.bitrate, size=row.size, age_days=_age(row),
        seeders=row.seeders, peers=row.peers, grabs=row.grabs, score=row.score, rank=row.rank,
        reasons=[Reason(code=item.get("code", ""), arg=item.get("arg")) for item in row.reasons or []
                 if isinstance(item, dict)])


def _out(db: DbSession, row: ReleaseSearch, uid: int) -> ReleaseSearchOut:
    from ..services.procure import formats

    subject = row.subject or {}
    kinds = [kind for kind in subject.get("kinds") or [] if kind in ("ebook", "audio")]
    releases = search.releases_of(db, row.id) if row.state == "done" else []
    blocked = blocklist.entries_for(db, work_id=row.work_id, foreign_id=row.foreign_id,
                                    key=blocklist.book_key(subject), uid=uid,
                                    languages=search.languages_of(subject, row.language)) if releases else {}
    config = formats.config(db)
    cards = []
    if row.state == "done":
        for kind in kinds:
            mine = [item for item in releases if item.kind == kind]
            counts: dict[str, int] = {}
            for item in mine:
                for found in item.reasons or []:
                    if isinstance(found, dict) and found.get("code"):
                        counts[found["code"]] = counts.get(found["code"], 0) + 1
            ordered = sorted(counts.items(), key=lambda pair: (
                -pair[1], judge.ORDER.index(pair[0]) if pair[0] in judge.ORDER else 99))[:3]
            top = next((item for item in mine if item.rank == 1), None)
            order = [entry["f"] for entry in config[kind]]
            goal = config["target"][kind]
            below = top is not None and top.tier > (order.index(goal) if goal in order else 0)
            passing = sum(1 for item in mine if item.rank is not None)
            cards.append(ReleaseCardOut(kind=kind, total=len(mine), passing=passing,
                                 reasons=[{"code": code, "count": count} for code, count in ordered],
                                 top=_release(top, blocked) if top is not None else None, goal=goal,
                                 below_goal=below,
                                 have=(subject.get("have") or {}).get(kind)))
    authors = subject.get("authors") or []
    token = None
    if row.foreign_id is not None:
        from ..models import ForeignBook

        book = db.get(ForeignBook, row.foreign_id)
        token = book.token if book is not None else None
    owner = db.get(Account_, uid)
    resting, askable = search.indexer_rests(db, owner, subject, row.language)
    used_up = owner is not None and rations.exhausted("search", owner)
    return ReleaseSearchOut(
        resting=[RestingOut(**item) for item in resting], can_search_again=askable > 0 and not used_up,
        searches_used_until=budget.midnight() if used_up else None,
        id=row.id, state=row.state, error=row.error, language=row.language,
        languages=search.languages_of(subject, row.language), kinds=kinds,  # type: ignore[arg-type]
        work_id=row.work_id, book=token, title=str(subject.get("title") or ""),
        author=str(authors[0]) if authors else "", created_at=row.created_at, finished_at=row.finished_at,
        indexers=[IndexerProgressOut(**{key: entry.get(key) for key in IndexerProgressOut.model_fields})
                  for entry in row.progress or []],
        cards=cards, releases=[_release(item, blocked) for item in releases])


@router.post("/searches", response_model=ReleaseSearchOut, status_code=202,
             summary="Search the indexers for a book (the right to acquire)",
             responses={403: {"description": "acquire_not_allowed"}, 404: {"description": "not_found"},
                        409: {"description": "no_indexers"},
                        422: {"description": "language_not_accepted (a language the operator does not allow)"},
                        429: {"description": "too_many_searches, too_many_searches_today"}})
def start_search(payload: ReleaseSearchIn, account: Account, db: DbSession) -> ReleaseSearchOut:
    row = search.start(db, account, work_id=payload.work_id, token=payload.book, language=payload.language,
                       fresh=payload.new, by_hand=True)
    return _out(db, row, account.id)


@router.get("/searches/{search_id}", response_model=ReleaseSearchOut,
            summary="How far a search is, and the releases it found with their judgement (the caller's own)")
def read_search(search_id: int, account: Reader, db: DbSession) -> ReleaseSearchOut:
    return _out(db, search.get(db, account.id, search_id), account.id)


class KindStatusOut(BaseModel):
    kind: Kind
    library: str = Field(description="The library a book of this kind would go into.")
    have: str | None = Field(description="The best format there is in this language (in the ranking's words).")
    goal: str
    upgrade: bool = Field(description="There is one, below the goal: a better format can be looked for.")


class WorkStatusOut(BaseModel):
    allowed: bool = Field(description="Whether the caller may ask for books of this work at all.")
    kinds: list[KindStatusOut]


@router.get("/works/{work_id}", response_model=WorkStatusOut,
            summary="What the caller may look for of a work: the kinds, where they would go, an upgrade",
            responses={404: {"description": "not_found"}})
def work_status(work_id: int, account: Reader, db: DbSession, language: str | None = None) -> WorkStatusOut:
    from ..services import works
    from ..services.procure import formats

    works.get_work(db, account.id, work_id)
    places = search.targets(db, account.id)
    if not places:
        return WorkStatusOut(allowed=False, kinds=[])
    wanted = search.language_of(language)
    subject = search.subject_of_work(db, account.id, work_id, search.accepted_of(
        db, wanted, search.original_of(db, work_id, None)))
    config = formats.config(db)
    kinds = []
    for kind in ("ebook", "audio"):
        if kind not in places:
            continue
        have = (subject.get("have") or {}).get(kind)
        order = [entry["f"] for entry in config[kind]]
        goal = config["target"][kind]
        upgrade = have is not None and (order.index(have) if have in order else len(order)) > (
            order.index(goal) if goal in order else 0)
        kinds.append(KindStatusOut(kind=kind, library=places[kind].name, have=have, goal=goal,  # type: ignore[arg-type]
                                   upgrade=upgrade))
    return WorkStatusOut(allowed=True, kinds=kinds)


class LoadIn(BaseModel):
    force: bool = Field(default=False, description="Load a release that was turned down (the interface asks first).")


class DownloadOut(BaseModel):
    id: int
    title: str
    kind: Kind
    state: Literal["sending", "unsure", "queued", "downloading", "paused", "completed", "problem", "failed", "removed",
                   "imported"]
    client: str
    protocol: Literal["usenet", "torrent"]
    forced: list[str]
    created_at: datetime


def _download(row: Download) -> DownloadOut:
    return DownloadOut(id=row.id, title=row.title, kind=row.kind, state=row.state,  # type: ignore[arg-type]
                       client=row.client_name, protocol=row.protocol,  # type: ignore[arg-type]
                       forced=list(row.forced or []), created_at=row.created_at)


@router.post("/searches/{search_id}/releases/{release_id}/load", response_model=DownloadOut, status_code=201,
             summary="Hand a release of the search to a download program",
             responses={403: {"description": "acquire_not_allowed"}, 404: {"description": "not_found"},
                        409: {"description": "release_refused, already_loading, search_not_done, no_client, "
                                             "indexer_gone, target_not_writable"},
                        429: {"description": "indexer_limit_reached, indexer_daily_limit"},
                        502: {"description": "release_fetch_failed, release_file_invalid, release_link_private, "
                                             "indexer_*, client_*"},
                        504: {"description": "indexer_timeout"}})
def load_release(search_id: int, release_id: int, payload: LoadIn, account: Account, db: DbSession) -> DownloadOut:
    return _download(loading.load(db, account, search_id, release_id, force=payload.force))


class BlockIn(BaseModel):
    reason: str = Field(default="by_hand", max_length=32, description="by_hand or a code of the judgement.")


class BlockOut(BaseModel):
    id: int
    title: str
    kind: Kind
    protocol: Literal["usenet", "torrent"]
    indexer: str
    reason: str
    by: str = Field(description="Who blocked it; empty when nexbooks did after a check.")
    created_at: datetime
    work_id: int | None
    book: str | None = Field(description="The token of the book of the sources, when the entry is for one.")
    book_title: str
    book_author: str


class BlocklistOut(BaseModel):
    entries: list[BlockOut]


def _entry(db: DbSession, row: Any, title: str, author: str, by: str) -> BlockOut:
    token = None
    if row.foreign_id is not None:
        from ..models import ForeignBook

        book = db.get(ForeignBook, row.foreign_id)
        token = book.token if book is not None else None
    return BlockOut(id=row.id, title=row.title, kind=row.kind, protocol=row.protocol, indexer=row.indexer_name,
                    reason=blocklist.reason_of(row.reason), by=by, created_at=row.created_at, work_id=row.work_id,
                    book=token,
                    book_title=title, book_author=author)


@router.post("/searches/{search_id}/releases/{release_id}/block", response_model=BlockOut, status_code=201,
             summary="Put a release of the search on the blocklist of its book",
             responses={403: {"description": "acquire_not_allowed"}, 404: {"description": "not_found"},
                        422: {"description": "invalid_input"}})
def block_release(search_id: int, release_id: int, payload: BlockIn, account: Account, db: DbSession) -> BlockOut:
    entry = loading.block_release(db, account, search_id, release_id, payload.reason)
    found = search.get(db, account.id, search_id)
    subject = found.subject or {}
    authors = subject.get("authors") or []
    return _entry(db, entry, str(subject.get("title") or ""), str(authors[0]) if authors else "",
                  (account.display_name or account.name) if entry.by_user_id == account.id else "")


@router.get("/blocklist", response_model=BlocklistOut,
            summary="The blocklist of the books the caller sees, newest first (the right to acquire)",
            responses={403: {"description": "acquire_not_allowed"}})
def read_blocklist(account: Reader, db: DbSession) -> BlocklistOut:
    return BlocklistOut(entries=[_entry(db, row, title, author, by)
                                 for row, title, author, by in blocklist.listing(db, account)])


@router.delete("/blocklist/{entry_id}", status_code=204, summary="Take a release off the blocklist again",
               responses={403: {"description": "acquire_not_allowed"}, 404: {"description": "not_found"}})
def unblock(entry_id: int, account: Account, db: DbSession) -> Response:
    loading.unblock_release(db, account, entry_id)
    return Response(status_code=204)
