"""Searching the indexers for one book: the manual search of a person, as a job that survives a restart.

* **What is looked for** is frozen when the search begins (``subject``): every title the book is known by in the
  libraries the person sees (or the sources know, for a book nexbooks does not hold), the authors, the series and the
  volume, the running time, and the formats there are already in the language looked for (an upgrade must be better).
* **Every enabled indexer at once**, one thread each, one request after the other within one (the pacing, the pauses
  and the limits of the day of ``indexers.run`` hold). The whole search has ``SEARCH_SECONDS``; an indexer that is not
  done by then keeps what it found and is marked ``timeout``.
* **Categories** are the ones the operator chose for the indexer, of the kinds that are looked for, and of them the
  ones in the language looked for or of mixed language (all of a kind when none fits). **``t=book``** only where the
  caps list ``author`` among its parameters, and only answers whose name holds the author count; when it brings none,
  a free text search does: "Author Core title", in the spellings of an umlaut (``ö``, ``oe``, ``o``), at most
  ``MAX_QUERIES`` per indexer and ``MAX_PAGES`` pages of 100.
* **The judgement** (``judge``) runs once every indexer is done, with the formats and places of the operator and the
  blocklist of the book. Each release is kept with its judgement; its link is sealed.
* A restart puts a running search back in the queue (``jobs``); it starts again from the beginning.
* A person's searches are their own. At most ``RUNNING_PER_PERSON`` wait or run at a time; finished ones go after a
  day, and a person keeps at most ``KEEP_PER_PERSON``.
* Log lines: counts per indexer, never a title or a name (``gate`` writes the requests without their values).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import String, delete, func, select, update
from sqlalchemy.orm import Session

from ... import clock
from ...db import SessionLocal
from ...errors import error
from ...models import (
    AUTHOR,
    EDITION_AUDIO,
    EDITION_EBOOK,
    OPERATOR,
    UNABRIDGED,
    Account,
    Credit,
    Edition,
    ForeignBook,
    Indexer,
    Job,
    Library,
    Person,
    ProcureWish,
    ReleaseSearch,
    SearchRelease,
    Work,
)
from ...security import encrypt_secret
from .. import jobs, libraries, works
from ..discover import foreign
from ..readers import names
from ..readers.text import fold
from . import (
    blocklist,
    budget,
    formats,
    indexer_admin,
    indexers,
    judge,
    newznab,
    rations,
    release_match,
    release_name,
    retention,
)
from .indexers import IndexerError

logger = logging.getLogger("nexbooks.procure")

JOB = "psearch"
LANE = "procure"
SEARCH_SECONDS = 150.0
MAX_QUERIES = 4
MAX_PAGES = 2
PAGE = 100
RUNNING_PER_PERSON = 3
KEEP_PER_PERSON = 20
KEEP_HOURS = 24
#: A search that finished this recently is shown again when the same book is looked for again (the indexers have daily
#: limits of requests); "search again" asks for a new one.
FRESH_MINUTES = 15
MAX_RELEASES = 600
MAX_THREADS = 8
LINK_CONTEXT = "release-link"
#: Asked for by a wish that takes every language the operator accepts (the interface cannot send it: the API wants two
#: letters at least).
ANY_LANGUAGE = "*"
KINDS = (EDITION_EBOOK, EDITION_AUDIO)

#: The clock the threads look at before each request; the tests set it.
ticks: Callable[[], float] = time.monotonic
_progress_lock = threading.Lock()


# --- Where a person may ask for books ------------------------------------------------------------------------------


def targets(db: Session, uid: int) -> dict[str, Library]:
    """The library each kind of book would go into for this person: the operator's choice when the person may ask for
    books there, else the first of the person's libraries with the right ``acquire`` that takes the kind **and that
    nexbooks may write in** (one that is mounted read-only is no target; only when there is none, the first that takes
    the kind). A kind without one is missing."""
    config = formats.config(db)
    allowed = list(db.scalars(select(Library).where(Library.id.in_(libraries.acquirable_library_ids(uid)))
                              .order_by(Library.name, Library.id)))
    found: dict[str, Library] = {}
    for kind in KINDS:
        chosen = config["library"].get(kind)
        own = next((row for row in allowed if row.id == chosen and libraries.kind_allows(row.kind, kind)), None)
        fitting = [row for row in allowed if libraries.kind_allows(row.kind, kind)]
        found_row = own or next((row for row in fitting if formats.write_state(row.path)[0]), None) or (
            fitting[0] if fitting else None)
        if found_row is not None:
            found[kind] = found_row
    return found


def must_choose(db: Session, uid: int) -> set[str]:
    """The kinds a person has to choose the library for: the operator chose none (settings, "Ablage"), and more than one
    library nexbooks may write in takes the kind. Then no library is taken by its name: a novel would land in
    "Kinderbücher" because it comes first."""
    config = formats.config(db)
    allowed = list(db.scalars(select(Library).where(Library.id.in_(libraries.acquirable_library_ids(uid)))))
    found: set[str] = set()
    for kind in KINDS:
        chosen = config["library"].get(kind)
        if any(row.id == chosen and libraries.kind_allows(row.kind, kind) for row in allowed):
            continue
        fitting = [row for row in allowed if libraries.kind_allows(row.kind, kind)]
        if sum(1 for row in fitting if formats.write_state(row.path)[0]) > 1:
            found.add(kind)
    return found


# --- What is looked for ----------------------------------------------------------------------------------------------


def _rank_of(config: dict[str, Any], kind: str, fmt: str | None) -> int:
    order = [item["f"] for item in config[kind]]
    return order.index(fmt) if fmt in order else len(order)


def _edition_format(view: works.EditionView) -> str | None:
    """The best format of an edition in the words of the ranking (an e-book by its files, a recording by its codec and
    the bit rate its size over its running time comes to)."""
    edition = view.edition
    if edition.kind == EDITION_EBOOK:
        found = [item for item in view.formats if item in ("epub", "azw3", "mobi", "pdf")]
        return found[0] if found else None
    codecs = [item for item in view.formats if item in ("flac", "m4b", "m4a", "mp3", "ogg", "opus")]
    if not codecs:
        return None
    size = sum(file.size for file in view.files)
    bitrate = int(size * 8 / edition.duration / 1000) if edition.duration > 0 and size > 0 else None
    return judge.audio_format(codecs[0], bitrate)


#: How many titles of other books of the author a book is held against (``release_match.Book.others``).
MAX_OTHERS = 120


def other_titles(db: Session, uid: int | None, authors: Sequence[str], titles: Sequence[str], *,
                 work_id: int | None = None, foreign_ids: Sequence[int] = (), shelves: bool = True) -> list[str]:
    """The titles of other books of the authors: with ``shelves`` the works of the libraries ``uid`` sees (every
    library when None, for the work of the server), and the books of the sources nexbooks met. Titles that are a title
    of the book itself are left out. A name or a file that holds one of them as a whole is that other book (``Wir
    wollen Ruß sehen`` beside ``Ruß``); they never leave the server."""
    keys = {names.person_key(name)[:200] for name in authors if isinstance(name, str) and name.strip()}
    if not keys:
        return []
    own = {names.compare_key(text) for text in titles if text}
    found: list[str] = []

    def add(text: str | None) -> None:
        text = " ".join((text or "").split())[:300]
        if text and names.compare_key(text) not in own and text not in found:
            found.append(text)

    people = select(Person.id).where(Person.key.in_(keys))
    query = (select(Edition.title).join(Credit, Credit.edition_id == Edition.id)
             .where(Credit.person_id.in_(people), Credit.role == AUTHOR, Edition.missing_at.is_(None)))
    if work_id is not None:
        query = query.where(Edition.work_id != work_id)
    if uid is not None:
        query = query.where(Edition.library_id.in_(libraries.member_library_ids(uid)))
    for text in db.scalars(query.distinct().limit(MAX_OTHERS)) if shelves else ():
        add(text)
    # The books of the sources by the last name of an author: the longest run of plain letters in it (the column holds
    # JSON, where a letter with an accent may be written as an escape).
    lasts: set[str] = set()
    for name in authors:
        if isinstance(name, str) and name.strip():
            words = names.display_name(name).split()
            runs = re.findall(r"[a-z]+", words[-1].casefold()) if words else []
            if runs and len(max(runs, key=len)) >= 3:
                lasts.add(max(runs, key=len))
    for last in sorted(lasts)[:3]:
        rows = db.scalars(select(ForeignBook).where(func.lower(ForeignBook.authors.cast(String)).contains(last))
                          .limit(MAX_OTHERS))
        for row in rows:
            if row.id in foreign_ids or len(found) >= MAX_OTHERS:
                continue
            theirs = [name for name in row.authors or [] if isinstance(name, str)]
            if any(names.authors_compatible(mine, other) for mine in authors for other in theirs):
                add(row.title)
    return found[:MAX_OTHERS]


def subject_of_work(db: Session, uid: int, work_id: int, languages: Sequence[str]) -> dict[str, Any]:
    """The book as the person sees it (only the editions of their libraries). What is there already counts for the
    languages looked for."""
    view = works.get_work(db, uid, work_id)
    config = formats.config(db)
    titles: list[dict[str, str]] = []

    def add(text: str, lang: str) -> None:
        text = " ".join((text or "").split())[:300]
        if text and not any(item["text"] == text and item["language"] == lang for item in titles):
            titles.append({"text": text, "language": lang})

    add(view.title, "")
    for edition_view in view.editions:
        edition = edition_view.edition
        add(edition.title, edition.language)
        if edition.original_title:
            add(edition.original_title, edition.original_language)
    if view.original_title:
        add(view.original_title, view.original_language)
    authors = [name for _id, name in view.authors][:6]
    for aliases in view.author_aliases.values():
        authors.extend(aliases[:3])
    have: dict[str, str | None] = {}
    have_by: dict[str, dict[str, str | None]] = {}
    have_languages: dict[str, list[str]] = {}
    duration = None
    for kind in KINDS:
        by_version: dict[str, str | None] = {}
        for edition_view in view.editions:
            edition = edition_view.edition
            if edition.kind != kind or (edition.language and edition.language not in languages):
                continue
            if edition.language and edition.language not in have_languages.setdefault(kind, []):
                have_languages[kind].append(edition.language)
            version = (edition.variant or UNABRIDGED) if kind == EDITION_AUDIO else ""
            fmt = _edition_format(edition_view)
            best = by_version.get(version)
            if fmt is not None and (best is None or _rank_of(config, kind, fmt) < _rank_of(config, kind, best)):
                by_version[version] = fmt
            proven = edition.variant == UNABRIDGED and not edition.variant_uncertain
            if kind == EDITION_AUDIO and edition.duration and proven:
                # Only a recording whose reading is proven unabridged (its words, a source, a person) is the measure
                # of one: a shortened one without a word says nothing.
                duration = max(duration or 0, edition.duration)
        have_by[kind] = by_version
        # The format of the fullest version there is: what the card and the upgrade look at.
        fullest = min(by_version, key=lambda version: judge.VARIANT_ORDER.get(version or UNABRIDGED, 0), default=None)
        have[kind] = by_version[fullest] if fullest is not None else None
    return {
        "title": view.title, "titles": titles[:12], "authors": authors[:8], "series": view.series_name or "",
        "position": view.shown.series_pos, "bundle": False, "duration": duration, "have": have,
        "have_by": have_by, "have_languages": have_languages, "accepted": list(languages),
        "had": {kind: any(e.edition.kind == kind for e in view.editions) for kind in KINDS},
        "others": other_titles(db, uid, authors[:8], [item["text"] for item in titles], work_id=work_id),
    }


def subject_of_foreign(db: Session, row: ForeignBook, languages: Sequence[str],
                       uid: int | None = None) -> dict[str, Any]:
    """A book of the sources: its titles and forms as the sources know them (the same for everybody). With ``uid`` the
    titles of the other books of the author take in the works of the libraries that person sees."""
    rows = foreign.family(db, row)
    titles: list[dict[str, str]] = []
    named = [(item.title, item.language) for item in rows]
    # A work of the catalogue of an author knows its title in every language it came out in.
    data = row.data if isinstance(row.data, dict) else {}
    named += [(str(item.get("text") or ""), str(item.get("language") or "")) for item in data.get("titles") or []
              if isinstance(item, dict)]
    for text, language in named:
        text = " ".join((text or "").split())[:300]
        if text and not any(entry["text"] == text and entry["language"] == (language or "") for entry in titles):
            titles.append({"text": text, "language": language or ""})
    authors = [name for name in (row.authors or []) if isinstance(name, str)][:6]
    duration = None
    for form in foreign.forms_of(rows):
        minutes = form.get("minutes")
        if form.get("kind") == "audio" and isinstance(minutes, int) and minutes > 0 \
                and form.get("variant") in ("", None, UNABRIDGED) \
                and (duration is None or form.get("language") in languages):
            duration = minutes * 60
    return {
        "title": row.title, "titles": titles[:12], "authors": authors, "series": row.series or "",
        "position": row.series_pos, "bundle": False, "duration": duration, "have": {}, "have_by": {},
        "accepted": list(languages), "had": {},
        "others": other_titles(db, uid, authors, [item["text"] for item in titles],
                               foreign_ids=[item.id for item in rows], shelves=uid is not None),
    }


def book_of(subject: dict[str, Any]) -> release_match.Book:
    titles = tuple(release_match.Title(item["text"], item.get("language") or "")
                   for item in subject.get("titles") or [] if item.get("text"))
    name = release_name.parse(subject.get("title") or "")
    others = tuple(str(text) for text in subject.get("others") or [] if isinstance(text, str) and text)
    return release_match.Book(titles=titles, authors=tuple(subject.get("authors") or ()),
                              series=subject.get("series") or "", position=subject.get("position"),
                              bundle=bool(subject.get("bundle")) or name.bundle_range is not None, others=others)


# --- Starting ----------------------------------------------------------------------------------------------------


def language_of(language: str | None) -> str:
    """The one language looked for, when one is asked for; else empty: every language the operator allows
    (``accepted_of``). The account's language is the interface's and plays no part here. A language that is not among
    the languages the indexer categories know (``ja``) is no language to look for either: empty."""
    if language == ANY_LANGUAGE:
        return ""
    chosen = (language or "").strip().lower()[:2]
    return chosen if chosen in newznab.LANGUAGE_WORDS else ""


def original_of(db: Session, work_id: int | None, foreign_id: int | None) -> str:
    """The language the book first came out in, as far as nexbooks knows it: of the work, else of the book of the
    sources."""
    if work_id is not None:
        work = db.get(Work, work_id)
        if work is not None and work.original_language:
            return work.original_language
    if foreign_id is not None:
        book = db.get(ForeignBook, foreign_id)
        if book is not None and book.language:
            return book.language
    return ""


def accepted_of(db: Session, wanted: str, original: str = "") -> list[str]:
    """The languages a search takes: the one looked for, else every language the operator allows (with the book's
    original language when "Originalsprache" is ticked; English when he allows none)."""
    return [wanted] if wanted else formats.allowed_languages(db, original) or ["en"]


def check_language(db: Session, wanted: str, original: str = "") -> None:
    """A language asked for has to be one the operator allows (settings, "Sprachen")."""
    if wanted and wanted not in formats.allowed_languages(db, original):
        raise error("language_not_accepted", "nexbooks does not fetch books in this language.", 422,
                    field="language", language=wanted)


def tidy(db: Session, uid: int) -> None:
    """A person's finished searches older than a day go, and of the rest only the newest ``KEEP_PER_PERSON``; a search
    that a wish still points at stays."""
    moment = clock.now()
    # A search that a wish which is not over still points at is kept: the wish (and the list "Braucht dich") reads it.
    pointed = select(ProcureWish.search_id).where(ProcureWish.search_id.is_not(None), ProcureWish.state != "done")
    db.execute(delete(ReleaseSearch).where(ReleaseSearch.user_id == uid, ReleaseSearch.state.in_(("done", "failed")),
                                           ReleaseSearch.id.not_in(pointed),
                                           ReleaseSearch.created_at < moment - timedelta(hours=KEEP_HOURS)))
    keep = select(ReleaseSearch.id).where(ReleaseSearch.user_id == uid, ReleaseSearch.origin == "search").order_by(
        ReleaseSearch.created_at.desc(), ReleaseSearch.id.desc()).limit(KEEP_PER_PERSON)
    # The searches the automatic work made out of the newest entries (``rss``) go by their age only: a wish that goes
    # on with the next release after a failure reads them.
    db.execute(delete(ReleaseSearch).where(ReleaseSearch.user_id == uid, ReleaseSearch.id.not_in(keep),
                                           ReleaseSearch.id.not_in(pointed), ReleaseSearch.origin == "search",
                                           ReleaseSearch.state.in_(("done", "failed"))))


def _progress_of(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(Indexer).where(Indexer.enabled.is_(True)).order_by(Indexer.priority, Indexer.id))
    return [{"id": row.id, "name": row.name, "kind": row.kind, "state": "waiting", "found": 0, "error": "",
             "until": None, "limit": None} for row in rows]


def _person_of(account: Account | None) -> int | None:
    """Whose share of an indexer's limit of the day a search is held to: nobody's for the operator."""
    return None if account is None or account.role == OPERATOR else account.id


def indexer_rests(db: Session, account: Account | None, subject: dict[str, Any], language: str) -> tuple[
        list[dict[str, Any]], int]:
    """What can be asked right now, read live (a limit may come or go while a dialog stays open): the enabled indexers
    that rest for this person, each with ``id``, ``name``, ``cause`` (``daily_limit``, ``request_limit``, ``api_limit``,
    ``key_rejected``, or ``share_used`` for the person's part of the day's limit), ``until`` and ``limit`` (the
    operator's limit of the day where it is the cause); and how many can be asked (one that has no categories for the
    book, or rests, cannot)."""
    person = _person_of(account)
    languages = languages_of(subject, language)
    kinds = subject.get("kinds") or []
    resting_ones: list[dict[str, Any]] = []
    askable = 0
    rows = db.scalars(select(Indexer).where(Indexer.enabled.is_(True)).order_by(Indexer.priority, Indexer.id))
    for row in rows:
        rest = indexers.resting(row)
        if rest is not None:
            resting_ones.append({"id": row.id, "name": row.name, "cause": rest[1], "until": rest[0],
                                 "limit": row.daily_limit if rest[1] == "daily_limit" and row.daily_limit > 0
                                 else None})
        elif rations.share_left(row, person) == 0:
            resting_ones.append({"id": row.id, "name": row.name, "cause": "share_used", "until": budget.midnight(),
                                 "limit": row.daily_limit})
        elif plan_for(row, kinds, languages).categories:
            askable += 1
    return resting_ones, askable


def earlier_with_hits(db: Session, uid: int, *, wanted: str, work_id: int | None, foreign_id: int | None,
                      kinds: Sequence[str]) -> ReleaseSearch | None:
    """The newest finished search of the person for this book, language and kinds that has releases (hits, whatever
    their judgement). One that was tidied is gone: a search that a wish points at is never tidied."""
    candidates = db.scalars(select(ReleaseSearch).where(
        ReleaseSearch.user_id == uid, ReleaseSearch.state == "done", ReleaseSearch.language == wanted,
        ReleaseSearch.origin == "search",
        ReleaseSearch.work_id == work_id if work_id is not None else ReleaseSearch.foreign_id == foreign_id,
        select(SearchRelease.id).where(SearchRelease.search_id == ReleaseSearch.id).exists())
        .order_by(ReleaseSearch.finished_at.desc(), ReleaseSearch.id.desc()))
    return next((row for row in candidates if (row.subject or {}).get("kinds") == list(kinds)), None)


_start_lock = threading.Lock()


def start(db: Session, account: Account, *, work_id: int | None, token: str | None, language: str | None,
          fresh: bool = False, kinds: Sequence[str] | None = None, by_hand: bool = False,
          background: bool = False) -> ReleaseSearch:
    """A new search, waiting for its turn. The same one when one waits or runs for the same book and language (a
    second click), and, unless a new one is asked for (``fresh``), the one that finished less than ``FRESH_MINUTES`` ago
    for the same book, language and kinds: the indexers have limits of requests for the day. A search a person starts
    by hand (``by_hand``) counts against the person's searches of the day (``rations``); one shown again does not. A
    search of the background (``background``: a wish that comes back by itself) keeps to its part of the indexers'
    limits
    of the day, so that the rest stays for a new wish and for "Jetzt suchen"."""
    if (work_id is None) == (not token):
        raise error("invalid_input", "Name a work or a book of the sources.", 422, field="work_id")
    places = targets(db, account.id)
    if not places:
        raise error("acquire_not_allowed", "You may not ask for books in any library.", 403)
    wanted = language_of(language)
    foreign_row = None
    if work_id is not None:
        original = original_of(db, work_id, None)
        check_language(db, wanted, original)
        subject = subject_of_work(db, account.id, work_id, accepted_of(db, wanted, original))
    else:
        from ..discover.view import by_token

        foreign_row = by_token(db, token or "")
        if foreign_row is None:
            raise error("not_found", "Not found.", 404)
        original = foreign_row.language or ""
        check_language(db, wanted, original)
        subject = subject_of_foreign(db, foreign_row, accepted_of(db, wanted, original), account.id)
    subject["kinds"] = [kind for kind in KINDS if kind in places and (kinds is None or kind in kinds)]
    if not subject["kinds"]:
        raise error("acquire_not_allowed", "You may not ask for books of this kind.", 403)
    subject["libraries"] = {kind: places[kind].id for kind in subject["kinds"]}
    progress = _progress_of(db)
    if not progress:
        raise error("no_indexers", "No indexer is switched on.", 409)
    with _start_lock:
        # A search that waits for a job that is gone is over, and holds no place of the person's three.
        fail_orphans(db)
        open_ones = list(db.scalars(select(ReleaseSearch).where(
            ReleaseSearch.user_id == account.id, ReleaseSearch.state.in_(("queued", "running")))))
        foreign_id = foreign_row.id if foreign_row is not None else None
        same = next((row for row in open_ones if row.language == wanted and row.work_id == work_id
                     and row.foreign_id == foreign_id and (row.subject or {}).get("kinds") == subject["kinds"]), None)
        if same is not None:
            return same
        if not fresh:
            recent = db.scalars(select(ReleaseSearch).where(
                ReleaseSearch.user_id == account.id, ReleaseSearch.state == "done", ReleaseSearch.language == wanted,
                ReleaseSearch.origin == "search",
                ReleaseSearch.work_id == work_id if work_id is not None else ReleaseSearch.foreign_id == foreign_id,
                ReleaseSearch.finished_at >= clock.now() - timedelta(minutes=FRESH_MINUTES))
                .order_by(ReleaseSearch.finished_at.desc(), ReleaseSearch.id.desc()))
            again = next((row for row in recent if (row.subject or {}).get("kinds") == subject["kinds"]), None)
            if again is not None:
                return again
        if not fresh and not background:
            # Nothing can be asked now (every indexer rests, or the person has used the searches of the day): a new
            # search would only end at the limit. The earlier one with hits is shown again, with what it found.
            _, askable = indexer_rests(db, account, subject, wanted)
            if askable == 0 or (by_hand and rations.exhausted("search", account)):
                earlier = earlier_with_hits(db, account.id, wanted=wanted, work_id=work_id, foreign_id=foreign_id,
                                            kinds=subject["kinds"])
                if earlier is not None:
                    return earlier
        if len(open_ones) >= RUNNING_PER_PERSON:
            raise error("too_many_searches", "Wait until one of your searches is done.", 429)
        if by_hand:
            rations.ensure("search", account)
        tidy(db, account.id)
        row = ReleaseSearch(user_id=account.id, work_id=work_id, foreign_id=foreign_row.id if foreign_row else None,
                            language=wanted, subject=subject, state="queued", progress=progress,
                            created_at=clock.now())
        db.add(row)
        db.flush()
        jobs.enqueue(db, JOB, ref=row.id, payload={"background": True} if background else None)
        db.commit()
        if by_hand:
            rations.add("search", account)
    logger.info("Release search %d queued indexers=%d kinds=%s", row.id, len(progress), ",".join(subject["kinds"]))
    return row


# --- Asking ------------------------------------------------------------------------------------------------------


_APART = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ł": "l", "Ł": "L",
                        "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ı": "i", "ß": "ss"})


def _plain(text: str) -> str:
    """The letters without their accents (``La Lumière`` is ``La Lumiere``, ``Åsa`` is ``Asa``): how a name written
    for a scene or a newsgroup is mostly spelled."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_APART))
    return unicodedata.normalize("NFC", "".join(char for char in decomposed if not unicodedata.combining(char)))


def _spellings(text: str) -> list[str]:
    """A query as it is, with the umlauts written out (``oe``), without their dots or any accent (``o``, ``e``)."""
    found = [text]
    for other in (text.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                                                 "ß": "ss"})), _plain(text)):
        if other not in found:
            found.append(other)
    return found


def queries(subject: dict[str, Any], languages: Sequence[str]) -> list[str]:
    """The free text queries: "Author Core title" for the titles in the languages looked for first. Each title has its
    spellings; they are taken round by round (every title as it is, then every title in its second spelling ...), so
    that the limit never cuts a title off, the original one least of all; at most ``MAX_QUERIES``."""
    authors = subject.get("authors") or []
    author = names.display_name(authors[0]) if authors else ""
    titles = sorted(subject.get("titles") or [],
                    key=lambda item: (item.get("language") not in (*languages, ""),))
    per_title: list[list[str]] = []
    seen: set[str] = set()
    for item in titles:
        core = names.core_title(item["text"], author)
        key = names.compare_key(core)
        if not core or key in seen:
            continue
        seen.add(key)
        per_title.append(_spellings(f"{author} {core}".strip()))
    found: list[str] = []
    for round_ in range(max((len(row) for row in per_title), default=0)):
        for row in per_title:
            if round_ < len(row) and row[round_] not in found:
                found.append(row[round_])
    return found[:MAX_QUERIES]


@dataclass
class Plan:
    categories: list[int]
    ebook_categories: list[int]
    category_kinds: dict[int, str]
    category_langs: dict[int, str]
    book_search: bool
    book_title: bool


def plan_for(row: Indexer, kinds: list[str], languages: Sequence[str]) -> Plan:
    listed = [item for item in indexer_admin.view_categories(row) if item["on"]]
    if not listed:
        listed = [{"id": number, "type": "audio" if 3000 <= number < 4000 else "ebook", "lang": "mixed"}
                  for number in newznab.DEFAULT_CATEGORIES]
    chosen: list[int] = []
    for kind in kinds:
        mine = [item for item in listed if item["type"] == kind]
        # A category of a language the book is not looked for in is never asked, not even when nothing else is left.
        fitting = [item for item in mine if newznab.asked_language(item["lang"], tuple(languages))]
        chosen.extend(item["id"] for item in fitting)
    caps = row.caps or {}
    params = caps.get("book_params") or []
    return Plan(categories=chosen, ebook_categories=[number for number in chosen if 7000 <= number < 8000],
                category_kinds={item["id"]: item["type"] for item in listed},
                category_langs={item["id"]: item["lang"] for item in listed},
                book_search=bool(caps.get("book_search")) and "author" in params, book_title="title" in params)


@dataclass
class Found:
    items: list[newznab.Item] = field(default_factory=list)
    state: str = "done"
    error: str = ""
    until: str | None = None
    #: The operator's limit of requests for the day, when it is what stopped the indexer (or a person's share of it).
    limit: int | None = None
    #: Requests made to the indexer for this search (the count of the log of the automatic work).
    requests: int = 0


class _Stop(Exception):
    """The search's time is over, or the server stops."""


def _page_size(row: Indexer) -> int:
    caps = row.caps or {}
    limit = caps.get("limit_max")
    return min(PAGE, limit) if isinstance(limit, int) and limit > 0 else PAGE


def _ask(client: indexers.Client, params: dict[str, Any], page: int, deadline: float, found: Found,
         seen: set[str], keep: Callable[[newznab.Item], bool] | None = None,
         good: Callable[[newznab.Item], bool] | None = None) -> int:
    """Asks for the pages of one query and keeps what ``keep`` lets through. Returns how many releases came that
    ``good`` calls an answer (all that were kept, without ``good``)."""
    count = 0
    for number in range(MAX_PAGES):
        if ticks() >= deadline or jobs.stopping.is_set():
            raise _Stop
        query = {**params, "extended": 1, "limit": page, "offset": number * page}
        root, namespaces = client.request(query)
        feed = newznab.parse_feed(root, namespaces)
        if feed.limits is not None:
            client.limits = feed.limits
        for item in feed.releases:
            # The same release found by two queries, or posted twice under one name, is kept once.
            keys = {fold(item.title)} | ({item.guid} if item.guid else set())
            if keys & seen or (keep is not None and not keep(item)):
                continue
            seen.update(keys)
            found.items.append(item)
            if good is None or good(item):
                count += 1
        if feed.items < page:
            break
    return count


def _has_author(subject: dict[str, Any]) -> Callable[[newznab.Item], bool]:
    authors = subject.get("authors") or []

    def keep(item: newznab.Item) -> bool:
        words = release_match.tokens_of(item.title)
        return any(release_match.find_author(words, name) is not None for name in authors)

    return keep


def _has_author_and_title(subject: dict[str, Any]) -> Callable[[newznab.Item], bool]:
    """A release that names the author and the core title of the book: the answer ``t=book`` has to give to count. An
    indexer that goes by the author alone (and has more than a page of the author's books) would otherwise keep us
    from the free text, and the book would not be found."""
    book = book_of(subject)

    def good(item: newznab.Item) -> bool:
        found = release_match.match(release_name.parse(item.title), book)
        return found.author and found.title

    return good


def languages_of(subject: dict[str, Any], language: str) -> list[str]:
    """The languages a search takes: what was frozen with the subject, else the one language looked for."""
    return [item for item in subject.get("accepted") or [] if isinstance(item, str)] or ([language] if language else [])


def ask_indexer(search_id: int, indexer_id: int, subject: dict[str, Any], language: str, deadline: float,
                person: int | None = None, background: bool = False) -> Found:
    """Every query of the plan at one indexer, with its own database session. The line of this indexer in the progress
    is written as soon as it is done, not when the slowest one is. ``person``: whose search it is, held to a share of
    the day's limit (``rations``); None for the operator."""
    found = _ask_indexer(search_id, indexer_id, subject, language, deadline, person, background)
    _report(search_id, indexer_id, **_line(found))
    return found


def _line(found: Found) -> dict[str, Any]:
    """The line of the progress for an indexer that is done."""
    return {"state": found.state, "found": len(found.items), "requests": found.requests,
            "error": found.error or ("indexer_timeout" if found.state == "timeout" else ""), "until": found.until,
            "limit": found.limit}


def _ask_indexer(search_id: int, indexer_id: int, subject: dict[str, Any], language: str, deadline: float,
                 person: int | None = None, background: bool = False) -> Found:
    found = Found()
    languages = languages_of(subject, language)
    with SessionLocal() as db:
        row = db.get(Indexer, indexer_id)
        if row is None or not row.enabled:
            found.state, found.error = "skipped", "indexer_off"
            return found
        plan = plan_for(row, subject.get("kinds") or [], languages)
        if not plan.categories:
            found.state, found.error = "skipped", "indexer_no_book_categories"
            return found
        _report(search_id, indexer_id, state="searching")
        page = _page_size(row)
        free = list(plan.categories)
        seen: set[str] = set()

        asked: list[indexers.Client] = []

        def work(client: indexers.Client) -> None:
            nonlocal free
            asked.append(client)
            try:
                if plan.book_search and plan.ebook_categories:
                    authors = subject.get("authors") or []
                    titles = subject.get("titles") or []
                    params: dict[str, Any] = {"t": "book", "cat": ",".join(map(str, plan.ebook_categories)),
                                              "author": names.display_name(authors[0]) if authors else ""}
                    core = names.core_title(titles[0]["text"]) if titles else ""
                    if plan.book_title:
                        params["title"] = core
                    else:
                        params["q"] = core
                    if _ask(client, params, page, deadline, found, seen, _has_author(subject),
                            _has_author_and_title(subject)):
                        free = [number for number in free if number not in plan.ebook_categories]
                if free:
                    for text in queries(subject, languages):
                        _ask(client, {"t": "search", "q": text, "cat": ",".join(map(str, free))}, page, deadline,
                             found, seen)
            except _Stop:
                found.state = "timeout" if not jobs.stopping.is_set() else "stopped"

        try:
            indexers.run(db, row, work, person=person, background=background)
        except IndexerError as exc:
            found.state, found.error = "failed", exc.code
            until = exc.values.get("until")
            found.until = until if isinstance(until, str) else None
            if exc.code in ("indexer_daily_limit", "indexer_share_used") and row.daily_limit > 0:
                found.limit = row.daily_limit
            if found.until is None and exc.code == "indexer_daily_limit":
                found.until = budget.midnight().isoformat()
            if found.until is None and row.paused_until is not None and row.paused_until > clock.now():
                found.until = row.paused_until.isoformat()
        found.requests = sum(client.requests for client in asked)
        logger.info("Release search %d: indexer %d %s with %d releases after %d requests", search_id, indexer_id,
                    found.state, len(found.items), found.requests)
    return found


def _report(search_id: int, indexer_id: int, **values: Any) -> None:
    """One indexer's line of the progress, written at once (each thread with its own session)."""
    with _progress_lock, SessionLocal() as db:
        row = db.get(ReleaseSearch, search_id)
        if row is None:
            return
        progress = [dict(entry) for entry in row.progress or []]
        for entry in progress:
            if entry.get("id") == indexer_id:
                entry.update(values)
        row.progress = progress
        db.commit()


# --- The job -----------------------------------------------------------------------------------------------------


def search_job(db: Session, job: Any) -> None:
    row = db.get(ReleaseSearch, job.ref)
    if row is None or row.state in ("done", "failed"):
        return
    # A search that a stopped server left half done begins again.
    db.execute(delete(SearchRelease).where(SearchRelease.search_id == row.id))
    row.state, row.started_at, row.error = "running", clock.now(), ""
    row.progress = [{**entry, "state": "waiting", "found": 0, "error": "", "until": None, "limit": None}
                    for entry in row.progress or []]
    db.commit()
    subject = dict(row.subject or {})
    deadline = ticks() + SEARCH_SECONDS
    results: dict[int, Found] = {}
    ids = [entry["id"] for entry in row.progress or []]
    owner = db.get(Account, row.user_id)
    # The operator's searches are held to the day's limit alone; everybody else's to a share of it too.
    person = None if owner is None or owner.role == OPERATOR else owner.id
    background = bool(isinstance(job.payload, dict) and job.payload.get("background"))
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_THREADS, len(ids)))) as pool:
        futures = {pool.submit(ask_indexer, row.id, indexer_id, subject, row.language, deadline, person,
                                 background): indexer_id
                   for indexer_id in ids}
        done, late = wait(futures, timeout=SEARCH_SECONDS + 10)
        for future in done:
            indexer_id = futures[future]
            try:
                results[indexer_id] = future.result()
            except Exception:
                logger.exception("Release search %d: indexer %d broke off", row.id, indexer_id)
                results[indexer_id] = Found(state="failed", error="internal_error")
        for future in late:
            results[futures[future]] = Found(state="timeout", error="indexer_timeout")
    if jobs.stopping.is_set():
        raise jobs.Interrupted
    db.expire_all()
    row = db.get(ReleaseSearch, job.ref)
    if row is None:
        return
    progress = []
    for entry in row.progress or []:
        found = results.get(entry["id"], Found(state="skipped"))
        progress.append({**entry, **_line(found)})
    row.progress = progress
    _keep(db, row, results)
    row.state, row.finished_at = "done", clock.now()
    db.commit()
    logger.info("Release search %d done with %d releases", row.id,
                db.scalar(select(func.count()).select_from(SearchRelease).where(SearchRelease.search_id == row.id)))
    # A wish that waited for this search goes on: the best release that fits is loaded.
    from . import wishes

    wishes.after_search(db, row)


def _keep(db: Session, row: ReleaseSearch, results: dict[int, Found]) -> None:
    """Judges every release and keeps it with the judgement."""
    judged = judge_items(db, row.subject or {}, row.language, row.work_id, row.foreign_id,
                         {indexer_id: found.items for indexer_id, found in results.items()})
    store_releases(db, row.id, judged)


def judge_items(db: Session, subject: dict[str, Any], language: str, work_id: int | None, foreign_id: int | None,
                items: dict[int, list[newznab.Item]]) -> list[tuple[Indexer, newznab.Item, judge.Verdict]]:
    """The releases (by the number of the indexer that gave them) held against the book of ``subject``: each with its
    verdict, the best first. A search and the reading of the newest entries judge the same way."""
    book = book_of(subject)
    config = formats.config(db)
    languages = languages_of(subject, language)
    blocked = blocklist.blocked_for(db, work_id=work_id, foreign_id=foreign_id, key=blocklist.book_key(subject),
                                    languages=languages)
    wanted = judge.Wanted(book=book, language=languages[0] if languages else "en",
                          accepted=tuple(languages) if len(languages) > 1 else (),
                          have={k: v for k, v in (subject.get("have") or {}).items()},
                          have_by={k: dict(v) for k, v in (subject.get("have_by") or {}).items()},
                          duration=subject.get("duration"), retention_days=retention.effective(db),
                          have_languages={str(k): tuple(v) for k, v in (subject.get("have_languages") or {}).items()
                                          if isinstance(v, list)})
    kinds = set(subject.get("kinds") or KINDS)
    indexer_rows = {item.id: item for item in db.scalars(select(Indexer).where(Indexer.id.in_(list(items))))}
    moment = clock.now()
    judged: list[tuple[Indexer, newznab.Item, judge.Verdict]] = []
    for indexer_id, found_items in items.items():
        indexer_row = indexer_rows.get(indexer_id)
        if indexer_row is None:
            continue
        plan = plan_for(indexer_row, list(kinds), languages)
        protocol = "torrent" if indexer_row.kind == "torznab" else "usenet"
        for item in found_items:
            name = release_name.parse(item.title)
            matched = release_match.match(name, book)
            category_kind = next((plan.category_kinds.get(number) or ("ebook" if 7000 <= number < 8000 else "audio")
                                  for number in item.categories if 3000 <= number < 4000 or 7000 <= number < 8000),
                                 None)
            category_language = next((plan.category_langs[number] for number in item.categories
                                      if plan.category_langs.get(number, "mixed") != "mixed"), "mixed")
            offer = judge.Offer(title=item.title, size=item.size, published_at=item.published_at, protocol=protocol,
                                seeders=item.seeders, peers=item.peers, grabs=item.grabs,
                                priority=indexer_row.priority, category_kind=category_kind,
                                category_language=category_language,
                                blocklisted=blocklist.matches(blocked, item.title, protocol, item.info_hash),
                                stated_author=item.author, stated_title=item.book_title)
            verdict = judge.judge(name, offer, matched, wanted, config, moment)
            if verdict.kind not in kinds:
                continue
            judged.append((indexer_row, item, verdict))
    judge.rank([verdict for _row, _item, verdict in judged], [item.size for _row, item, _verdict in judged])
    judged.sort(key=lambda entry: (entry[2].rank is None, entry[2].rank or 0, entry[2].kind))
    return judged


def store_releases(db: Session, search_id: int, judged: list[tuple[Indexer, newznab.Item, judge.Verdict]]) -> None:
    for indexer_row, item, verdict in judged[:MAX_RELEASES]:
        db.add(SearchRelease(
            search_id=search_id, indexer_id=indexer_row.id, indexer_name=indexer_row.name,
            protocol="torrent" if indexer_row.kind == "torznab" else "usenet", title=item.title, size=item.size,
            published_at=item.published_at, seeders=item.seeders, peers=item.peers, grabs=item.grabs,
            categories=list(item.categories), guid=item.guid, info_hash=item.info_hash,
            link_enc=encrypt_secret(item.link, LINK_CONTEXT) if item.link else "", min_ratio=item.min_ratio,
            min_seed_seconds=item.min_seed_seconds, kind=verdict.kind, format=verdict.format or "",
            format_how=verdict.format_how, tier=verdict.tier, language=verdict.language,
            language_how=verdict.language_how, variant=verdict.variant, bitrate=verdict.bitrate,
            reasons=verdict.reasons, score=verdict.score, rank=verdict.rank))


def fail_orphans(db: Session) -> None:
    """A search that waits or runs without a job that waits or runs is over: it failed (the job was given up after its
    tries, or lost). Without this it would stay ``running`` for ever, hold one of the person's places, and the dialog
    would turn until the search is a day old."""
    alive = select(Job.ref).where(Job.kind == JOB, Job.state.in_(("queued", "running")))
    changed = db.execute(update(ReleaseSearch).where(
        ReleaseSearch.state.in_(("queued", "running")), ReleaseSearch.id.not_in(alive))
        .values(state="failed", error="search_failed", finished_at=clock.now())).rowcount
    db.commit()
    if changed:
        logger.warning("Release searches without a job set to failed count=%d", changed)


def _register() -> None:
    jobs.register(JOB, search_job, settle=fail_orphans)
    assert jobs.LANES[LANE] == (JOB,)


_register()


# --- Reading -----------------------------------------------------------------------------------------------------


def get(db: Session, uid: int, search_id: int) -> ReleaseSearch:
    """The person's own search; 404 for anybody else's."""
    row = db.get(ReleaseSearch, search_id)
    if row is None or row.user_id != uid:
        raise error("not_found", "Not found.", 404)
    return row


def releases_of(db: Session, search_id: int) -> list[SearchRelease]:
    return list(db.scalars(select(SearchRelease).where(SearchRelease.search_id == search_id).order_by(
        SearchRelease.rank.is_(None), SearchRelease.rank, SearchRelease.score.desc(), SearchRelease.id)))


__all__ = ["JOB", "LANE", "Plan", "accepted_of", "book_of", "check_language", "fail_orphans", "get", "language_of",
           "languages_of", "original_of",
           "plan_for", "queries", "releases_of", "search_job", "start", "subject_of_foreign", "subject_of_work",
           "targets"]
