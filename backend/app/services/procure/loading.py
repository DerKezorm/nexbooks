"""Loading a release a person chose from a search: the file from the indexer, the job to a download
program, a row in ``downloads``. Following the job is ``tracking``, filing the book ``importing``.

* **Only the person's own search**, only while they may still ask for books in the library the kind goes into, only
  a book they still see.
* **A release the judgement turned down** is loaded only when asked to (``force``, the interface asks first: "Trotzdem
  laden"); the download keeps the codes it was turned down with. nexbooks checks the file after the download anyway.
* **A second click** on the same release while the first is on its way is answered with ``already_loading``: the
  check and the row that marks it are written in one step under a lock.
* **The file**: an NZB is XML with ``<nzb>`` and at least one ``<file>``, read without DTDs or entities; a torrent is
  bencoded with an ``info`` dictionary (``torrent``), else ``release_file_invalid``. A torrent indexer may answer with a
  magnet link instead. The link lies on whatever host the indexer names (as with the *arr programs), but behind an
  indexer on the internet it never reaches a private address (``release_link_private``); it counts as one request to
  the indexer.
* **The program**: the enabled programs of the protocol by their priority; the next one when one cannot be reached. A
  hand-over that got no answer in time is ``unsure`` (the program may have it, and tracking looks for it). The job
  goes into nexbooks's category; a book that is missing goes to the front of the queue, an upgrade to the back.
* **Seed goal** of a torrent: what the tracker asks for (Torznab ``minimumratio``, ``minimumseedtime``), at least the
  operator's choice in the automatic settings (a ratio of 1, or seven days).
"""

from __future__ import annotations

import io
import logging
import re
import threading
from datetime import timedelta
from typing import Any
from xml.etree.ElementTree import ParseError

import defusedxml
import defusedxml.ElementTree as SafeXml
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import (
    ACTIVE_DOWNLOADS,
    CLIENT_PROTOCOL,
    Account,
    Download,
    DownloadClient,
    Indexer,
    ReleaseSearch,
    SearchRelease,
)
from ...security import decrypt_secret
from .. import libraries, works
from . import automation, blocklist, formats, indexer_admin, indexers, programs, releasefiles, search, torrent
from .clients import ClientError, HandOverUnsure, SeedGoal, open_client
from .indexers import IndexerError

logger = logging.getLogger("nexbooks.procure")

#: Where a download keeps the language of the category its release stood in (``importing.wanted_of``).
CATEGORY_KEY = "_category"

ACTIVE = ACTIVE_DOWNLOADS
SEVEN_DAYS = 7 * 24 * 3600
#: A row ``sending`` this old was left by a server that stopped on the way: it no longer stands in the way.
STALE_SENDING = timedelta(minutes=10)
_lock = threading.Lock()


class LoadError(Exception):
    def __init__(self, code: str, status: int = 502, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.values = values


def nzb_ok(content: bytes) -> bool:
    """Whether ``content`` is an NZB: XML whose root is ``nzb`` with at least one ``file``."""
    try:
        root = SafeXml.parse(io.BytesIO(content), forbid_dtd=True, forbid_entities=True,
                             forbid_external=True).getroot()
    except (ParseError, defusedxml.DefusedXmlException, ValueError, LookupError, RecursionError):
        return False
    if root is None or root.tag.rsplit("}", 1)[-1].lower() != "nzb":
        return False
    return any(child.tag.rsplit("}", 1)[-1].lower() == "file" for child in root)


def file_name(title: str, protocol: str) -> str:
    """A safe file name for the program: the release name without anything a path could make of it."""
    stem = re.sub(r"[^\w.\- ()\[\]]+", "_", title, flags=re.UNICODE).strip(" ._")[:180] or "release"
    return f"{stem}.{'nzb' if protocol == 'usenet' else 'torrent'}"


def seed_goal(db: Session, release: SearchRelease) -> SeedGoal:
    choice = automation.config(db).get("seed")
    ratio, seconds = release.min_ratio, release.min_seed_seconds
    if choice == "ratio":
        ratio = max(ratio or 0.0, 1.0)
    elif choice == "7d":
        seconds = max(seconds or 0, SEVEN_DAYS)
    return SeedGoal(ratio=ratio, seconds=seconds)


def _programs(db: Session, protocol: str) -> list[DownloadClient]:
    rows = db.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True)).order_by(
        DownloadClient.priority, DownloadClient.id))
    return [row for row in rows if CLIENT_PROTOCOL.get(row.kind) == protocol]


#: Where a download keeps the language of the title of its release (a title known in one language only).
TITLE_KEY = "_title"


def _witnesses(db: Session, release: SearchRelease) -> dict[str, str] | None:
    """The language the category of the release states, whenever the operator's category has one (``3130`` set to
    German), however the judgement came to its language; and the language of its title when that is how it was
    told."""
    found: dict[str, str] = {}
    if release.language_how == "category" and release.language:
        found[CATEGORY_KEY] = release.language
    elif release.indexer_id is not None:
        indexer = db.get(Indexer, release.indexer_id)
        if indexer is not None:
            langs = {item["id"]: item["lang"] for item in indexer_admin.view_categories(indexer)}
            stated = next((langs[number] for number in release.categories or [] if langs.get(number, "mixed")
                           != "mixed"), "")
            if stated:
                found[CATEGORY_KEY] = stated
    if release.language_how == "title" and release.language:
        found[TITLE_KEY] = release.language
    return found or None


def _claim(db: Session, account: Account, found: ReleaseSearch, release: SearchRelease, library_id: int,
           forced: list[str] | None, wish_id: int | None = None) -> tuple[Download, list[int]]:
    """The row that marks the release as on its way, written under the lock together with the check for a second
    click; and the rows a stop of the server left half done, which it ended on the way (their wishes are seen to after
    the lock is let go)."""
    with _lock:
        db.expire_all()
        reaped = _end_half_done(db, STALE_SENDING)
        there = db.scalar(select(Download.id).where(
            Download.work_id == found.work_id if found.work_id is not None else Download.foreign_id == found.foreign_id,
            Download.kind == release.kind, Download.library_id == library_id, Download.state.in_(ACTIVE)))
        if there is not None:
            raise error("already_loading", "A release of this book is on its way already.", 409)
        moment = clock.now()
        row = Download(user_id=account.id, work_id=found.work_id, foreign_id=found.foreign_id, library_id=library_id,
                       kind=release.kind, language=found.language, title=release.title, format=release.format,
                       size=release.size, protocol=release.protocol, info_hash=release.info_hash,
                       indexer_id=release.indexer_id, indexer_name=release.indexer_name, state="sending",
                       forced=forced, wish_id=wish_id, created_at=moment, updated_at=moment,
                       # The language the indexer's category gives the release and the one its title is known in: weak
                       # witnesses after the download when nothing else names one (most recordings carry no tag).
                       decision=_witnesses(db, release))
        db.add(row)
        try:
            db.commit()
        except IntegrityError as exc:
            # The unique index of the book and the kind: another process was quicker than the lock can know.
            db.rollback()
            raise error("already_loading", "A release of this book is on its way already.", 409) from exc
        return row, reaped


def _end_half_done(db: Session, older_than: timedelta | None) -> list[int]:
    """Rows left ``sending`` by a server that stopped on the way (between the row and the hand-over) end as ``failed``
    (``interrupted``, with a line in the history; the release is not blocked, it did not fail). ``older_than`` is how
    old a row must be; none at the start of the server, when no hand-over can be running. Returns their ids."""
    from . import history

    query = select(Download).where(Download.state == "sending")
    if older_than is not None:
        query = query.where(Download.updated_at < clock.now() - older_than)
    stale = list(db.scalars(query))
    moment = clock.now()
    for row in stale:
        row.state, row.failed_reason, row.finished_at, row.updated_at = "failed", "interrupted", moment, moment
        history.record(db, row, "failed", {"code": "interrupted"})
    db.commit()
    if stale:
        logger.info("%d hand-overs that a stop of the server left half done are over", len(stale))
    return [row.id for row in stale]


def _see_to_wishes(db: Session, ids: list[int], *, except_wish: int | None = None) -> None:
    """The wish of each ended hand-over searches again (the one that is being loaded just now goes on as it is)."""
    from . import wishes

    for download_id in ids:
        row = db.get(Download, download_id)
        if row is not None and (except_wish is None or row.wish_id != except_wish):
            wishes.after_interrupted(db, row)


def reap_sending(db: Session, *, older_than: timedelta | None = None) -> int:
    """Ends the hand-overs a stop of the server left half done, and sees to their wishes. Returns how many."""
    ids = _end_half_done(db, older_than)
    _see_to_wishes(db, ids)
    return len(ids)


def load(db: Session, account: Account, search_id: int, release_id: int, *, force: bool,
         wish_id: int | None = None) -> Download:
    found = search.get(db, account.id, search_id)
    if found.state != "done":
        raise error("search_not_done", "The search is not done yet.", 409)
    release = db.get(SearchRelease, release_id)
    if release is None or release.search_id != found.id:
        raise error("not_found", "Not found.", 404)
    codes = [item.get("code") for item in release.reasons or [] if isinstance(item, dict)]
    if codes and not force:
        raise error("release_refused", "nexbooks turned this release down.", 409, reasons=codes)
    places = search.targets(db, account.id)
    library = places.get(release.kind)
    if wish_id is not None:
        # A wish names the library it goes into, when the person may still use it.
        from ...models import Library, ProcureWish

        chosen = db.get(ProcureWish, wish_id)
        wished = db.get(Library, chosen.library_id) if chosen is not None and chosen.library_id else None
        allowed = set(db.scalars(libraries.acquirable_library_ids(account.id)))
        if wished is not None and wished.id in allowed and libraries.kind_allows(wished.kind, release.kind):
            library = wished
    if library is None:
        raise error("acquire_not_allowed", "You may not ask for books of this kind.", 403)
    # Known before anything is fetched: a library that is mounted read-only takes no book (the same probe as "Ordnen").
    writable, why = formats.write_state(library.path)
    if not writable:
        raise error("target_not_writable", _MESSAGES["target_not_writable"], 409, library_id=library.id, reason=why)
    if found.work_id is not None:
        works.get_work(db, account.id, found.work_id)
    elif found.foreign_id is None:
        raise error("not_found", "Not found.", 404)
    indexer = db.get(Indexer, release.indexer_id) if release.indexer_id is not None else None
    if indexer is None:
        raise error("indexer_gone", "The indexer of this release is gone.", 409)
    candidates = _programs(db, release.protocol)
    if not candidates:
        raise error("no_client", "No download program for this kind of release is switched on.", 409,
                    protocol=release.protocol)
    link = decrypt_secret(release.link_enc, search.LINK_CONTEXT) if release.link_enc else ""
    if not link:
        raise error("release_file_invalid", "The indexer gave no link for this release.", 502)
    row, reaped = _claim(db, account, found, release, library.id, codes or None, wish_id)
    _see_to_wishes(db, reaped, except_wish=wish_id)
    try:
        _hand_over(db, row, indexer, release, link, candidates, urgent=not (found.subject or {}).get("have", {}).get(
            release.kind))
    except Exception:
        db.rollback()
        gone = db.get(Download, row.id)
        if gone is not None:
            db.delete(gone)
            db.commit()
        raise
    if codes:
        logger.info("Release loaded although turned down download=%d reasons=%s", row.id, ",".join(codes))
    # The book is wished from now on, whoever asked: a release loaded by hand counts as a wish that is on its way.
    from . import wishes

    wishes.attach(db, account, found, row, library.id)
    tracking_soon()
    return row


def _file_of(db: Session, indexer: Indexer, release: SearchRelease, link: str) -> tuple[bytes, str, str]:
    """The file of the release: the one kept from an earlier hand-over, else fetched from the indexer (which counts as a
    release it gave). Returns ``(content, magnet, info_hash)`` after the file was checked; kept for a hand-over that has
    to come again until a program has the release."""
    key = releasefiles.key_of(release.indexer_id, release.protocol, release.size, release.title)
    kept = releasefiles.get(key)
    if kept is not None:
        content, magnet = kept
    else:
        try:
            content, magnet = indexers.run(db, indexer, lambda client: client.fetch(link, release.protocol), grab=True)
        except IndexerError as exc:
            values = {name: value for name, value in exc.values.items() if name in ("status", "retry_after", "until")}
            raise error(exc.code, _MESSAGES.get(exc.code, "The indexer did not give the release."), exc.status,
                        **values) from exc
        except Exception as exc:  # noqa: BLE001 - any fault while fetching is a failed fetch, see below
            # A fault nobody foresaw (a real indexer once sent a header that could not be taken) is a failed fetch,
            # not a traceback through the planner every round. Only its kind is logged: its text may hold the link
            # and so the key.
            logger.warning("Fetching a release failed unexpectedly kind=%s", type(exc).__name__)
            raise error("release_fetch_failed", _MESSAGES["release_fetch_failed"], 502) from None
        indexer.grabs = (indexer.grabs or 0) + 1
        db.commit()
    info_hash = release.info_hash
    try:
        if release.protocol == "usenet":
            if not nzb_ok(content):
                raise error("release_file_invalid", _MESSAGES["release_file_invalid"], 502)
        elif content:
            try:
                info_hash = torrent.read_torrent(content).info_hash
            except torrent.TorrentInvalid as exc:
                raise error("release_file_invalid", _MESSAGES["release_file_invalid"], 502) from exc
        else:
            info_hash = torrent.magnet_hash(magnet) or ""
            if not info_hash:
                raise error("release_file_invalid", _MESSAGES["release_file_invalid"], 502)
    except Exception:
        releasefiles.drop(key)
        raise
    if kept is None:
        releasefiles.put(key, content, magnet)
    return content, magnet, info_hash


def _hand_over(db: Session, row: Download, indexer: Indexer, release: SearchRelease, link: str,
               candidates: list[DownloadClient], *, urgent: bool) -> None:
    """The program is asked first, the indexer second: a program that does not answer costs the indexer nothing. The
    file
    is fetched when a program has answered (and kept until one has the release), so a hand-over that comes again later
    does not ask the indexer for it again."""
    key = releasefiles.key_of(release.indexer_id, release.protocol, release.size, release.title)
    name = file_name(release.title, release.protocol)
    info_hash = release.info_hash
    last: ClientError | None = None
    for program in candidates:
        target = programs.target_of(program)
        try:
            with open_client(target) as client:
                # Also at every hand-over, not only when the program is saved: a program changed since (job folders
                # switched off, a sorter) would put the job where nexbooks cannot tell its files from others'. It is
                # also the question whether the program answers at all.
                client.check_category()
                content, magnet, info_hash = _file_of(db, indexer, release, link)
                goal = seed_goal(db, release)
                row.goal_ratio, row.goal_seconds = goal.ratio, goal.seconds
                if release.protocol == "usenet":
                    ref = client.add_nzb(content, name, urgent)
                else:
                    ref = client.add_torrent(content=content or None, magnet=magnet or None, file_name=name,
                                             info_hash=info_hash, urgent=urgent, seed=goal)
        except HandOverUnsure as exc:
            releasefiles.drop(key)
            _record(db, row, program, exc.ref, "unsure", info_hash)
            logger.warning("Hand-over to %s unsure download=%d", program.kind, row.id)
            return
        except ClientError as exc:
            last = exc
            logger.warning("Hand-over to %s failed download=%d code=%s", program.kind, row.id, exc.code)
            if exc.code in ("client_unreachable", "client_blocked"):
                continue
            releasefiles.drop(key)
            break
        releasefiles.drop(key)
        _record(db, row, program, ref, "queued", info_hash)
        logger.info("Release handed to %s download=%d", program.kind, row.id)
        return
    assert last is not None
    raise error(last.code, _MESSAGES.get(last.code, "The download program refused the release."), last.status,
                **{name: value for name, value in last.values.items() if name in ("reason", "category")})


def _record(db: Session, row: Download, program: DownloadClient, ref: str, state: str, info_hash: str) -> None:
    row.client_id, row.client_name, row.client_ref = program.id, program.name, (ref or "")[:128]
    row.category, row.state, row.info_hash, row.updated_at = program.category, state, info_hash, clock.now()
    db.commit()


def tracking_soon() -> None:
    """Ask the programs a few seconds from now: the person is looking at "Läuft"."""
    from . import tracking

    tracking.soon()


def block_release(db: Session, account: Account, search_id: int, release_id: int, reason: str) -> Any:
    found = search.get(db, account.id, search_id)
    release = db.get(SearchRelease, release_id)
    if release is None or release.search_id != found.id:
        raise error("not_found", "Not found.", 404)
    if release.kind not in search.targets(db, account.id):
        raise error("acquire_not_allowed", "You may not ask for books of this kind.", 403)
    if found.work_id is not None:
        works.get_work(db, account.id, found.work_id)
    place = search.targets(db, account.id).get(release.kind)
    entry = blocklist.block(db, account, release, work_id=found.work_id, foreign_id=found.foreign_id, reason=reason,
                            book=blocklist.book_key(found.subject), library_id=place.id if place else None)
    reasons = [item for item in release.reasons or [] if item.get("code") != "blocklisted"]
    release.reasons = [{"code": "blocklisted"}, *reasons]
    release.rank = None
    # The others of its kind that pass move up.
    passing = [item for item in search.releases_of(db, found.id) if item.kind == release.kind and item.rank is not None
               and item.id != release.id]
    for place, item in enumerate(sorted(passing, key=lambda item: item.rank or 0), start=1):
        item.rank = place
    db.commit()
    return entry


def unblock_release(db: Session, account: Account, entry_id: int) -> None:
    blocklist.unblock(db, account, entry_id)


_MESSAGES = {
    "target_not_writable": "nexbooks may not write into the library this release would go into.",
    "release_link_private": "The link of this release leads into a private network or to a blocked address, and "
                            "the indexer itself is not in one; nexbooks does not follow it.",
    "release_fetch_failed": "The release file could not be fetched from the indexer.",
    "release_file_invalid": "The indexer did not deliver a usable NZB or torrent file.",
    "indexer_key_rejected": "The indexer did not accept the API key.",
    "indexer_limit_reached": "The request limit of the indexer is reached.",
    "indexer_daily_limit": "The limit of requests for today is reached.",
    "indexer_off": "The indexer is switched off.",
    "indexer_unreachable": "The indexer cannot be reached.",
    "indexer_timeout": "The indexer did not answer in time.",
    "indexer_blocked": "nexbooks may not ask this address.",
    "client_unreachable": "The download program cannot be reached.",
    "client_auth_failed": "The download program did not accept the credentials.",
    "client_refused": "The download program refused the release.",
    "client_category_failed": "The category could not be used in the download program.",
    "client_timeout": "The download program did not answer in time.",
    "client_blocked": "nexbooks may not ask this address.",
}

__all__ = ["block_release", "file_name", "load", "nzb_ok", "seed_goal", "unblock_release"]
