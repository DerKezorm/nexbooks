"""Filing a finished download or an upload into its library.

**A job** (kind ``pimport``, the download as ``ref``) in the main lane: never at the same time as a scan or a tidying,
the other two that touch a library. It survives a restart: it goes in steps, each written down before the next
begins (``Download.place_step``, the files in ``procure_files``), and a step that stopped in the middle is done again
from what is on the disk (a file at its goal with its size was placed).

1. **Check** (``place_step`` empty): the library takes a file (else ``no_write``); the files are found
   (``incoming``; a dangerous one turns the release down), archives unpacked when the release holds no book outside
   them (``unpacking``), every book and every file beside a recording read by the readers of the scan in their helper
   process, and the books judged against the wish (``checking``). What does not pass is turned down: the release is
   blocked for the book, the files never go into a library and leave the program, the wish searches on, a line in the
   history (and, for another language, a note under "Braucht dich"). What is unclear is a question for a person
   (``runtime``, ``bundle``, ``files_unassigned``): the download waits under "Braucht dich" with its files where they
   are.
2. **Place** (``checked``): the goal of every file by the scheme (``placing``), written down, then each file linked,
   moved or copied there; never over anything.
3. **File** (``placed``): the library learns the files as the scan would have read them (the same readers, the same
   assembly): the edition goes to the work that was wished (a book of the sources gets its work, and the wish list and
   Discover see it on the shelf), an upgrade puts what it replaces into the trash and carries the place of every person
   over, the download is ``imported``, its wish done, a Usenet job leaves its program, a torrent seeds on. The library
   is read once more afterwards, which finds nothing new.

Log lines carry numbers and codes, never titles or paths.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import clock
from ...models import (
    ACTIVE_DOWNLOADS,
    AUTHOR,
    EDITION_AUDIO,
    EDITION_EBOOK,
    NARRATOR,
    ROLE_BOOK,
    ROLE_SIDECAR,
    TRANSLATOR,
    Account,
    BookFile,
    Credit,
    Download,
    DownloadClient,
    Edition,
    ExternalRef,
    ForeignBook,
    Library,
    PlaceFile,
    ProcureNotice,
    ProcureWish,
    Progress,
    Work,
)
from .. import catalog, jobs, kosync, libraries, merge, scanner, search_index, title_shape, works
from ..match import apply as match_apply
from ..match import overlay as match_overlay
from ..readers import names as names_of_titles
from ..readers.text import fold
from . import (
    arrival,
    blocklist,
    checking,
    formats,
    hidden,
    history,
    incoming,
    judge,
    placing,
    programs,
    release_match,
    release_name,
    search,
    trash,
    unpacking,
)
from .clients import ClientError, open_client, remove_imported_own

logger = logging.getLogger("nexbooks.procure")

KIND = "pimport"
#: Reasons of the blocklist for the codes the check turns a release down with.
BLOCK_REASONS = {
    "other_language": "other_language", "other_volume": "other_volume", "author_mismatch": "author_mismatch",
    "format_not_wanted": "format_not_wanted", "dramatized": "dramatized", "not_better": "not_better",
    "other_kind": "not_a_book", "no_book": "not_a_book", "dangerous": "dangerous",
    "archive_encrypted": "damaged", "archive_broken": "damaged", "archive_unsafe": "dangerous",
    "archive_too_large": "damaged", "archive_unsupported": "archive_unsupported", "sample": "sample",
    "companion": "companion", "upload_mixed": "not_a_book", "title_mismatch": "title_mismatch",
    "adaptation": "adaptation", "protected": "protected", "runtime_part": "partial",
}
#: The questions a person answers, and the reason of the blocklist when the person blocks instead.
QUESTIONS = {"runtime": "abridged", "bundle": "bundle", "files_unassigned": "title_mismatch",
             "language_unclear": "other_language", "language_unknown": "other_language", "partial": "partial",
             "edition": "companion", "pen_name": "author_mismatch", "same_edition": "not_better"}
UPLOAD_FOLDER = ".nexbooks-incoming"
#: Key of ``Download.decision`` of an upload whose kind (e-book or recording) is not known yet: the files tell it, and
#: for a ZIP the filing, once it has unpacked it.
KIND_OPEN = "_kind_open"
#: Problems of the filing itself that "Erneut prüfen" lets go on from where it stopped.
AFTER_ARRIVAL = ("place_failed", "no_space")


class Stop(Exception):
    """The filing cannot go on now: the download is a problem with ``code`` and ``values``."""

    def __init__(self, code: str, values: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.values = values or {}


def request(db: Session, row: Download) -> None:
    """The download is to be filed (a job; a no-op while one waits for it). Does not commit."""
    jobs.enqueue(db, KIND, ref=row.id)


def request_due(db: Session) -> int:
    """Every finished download that waits to be filed gets its job (after a restart, a lost job). Returns how many."""
    count = 0
    for download_id in db.scalars(select(Download.id).where(Download.state == "completed",
                                                            Download.place_step != "filed")):
        jobs.enqueue(db, KIND, ref=int(download_id))
        count += 1
    db.commit()
    return count


# --- Where the files are ---------------------------------------------------------------------------------------------


def _source(db: Session, row: Download) -> tuple[Path, str | None]:
    """The folder the files are read from, and the one file in it when the download is a single file."""
    if row.protocol == "upload":
        if row.source_path and not hidden.holds(Path(row.source_path), UPLOAD_FOLDER):
            raise Stop("place_failed", {"reason": "link"})
        found = placing.resolved(Path(row.source_path)) if row.source_path else None
    else:
        from . import tracking

        found = arrival.local_path(row.reported_path or None, tracking.mappings_of(db, row.client_id))
        if found is not None and arrival.below_working_folder(found):
            found = None
    if found is None:
        raise Stop("path_not_found", arrival.look_for(row.reported_path or None).values(row.reported_path or None)
                   if row.protocol != "upload" else {"search": "no_path"})
    row.source_path = str(found)[:4096]
    if found.is_file():
        return found.parent, found.name
    return found, None


def _root_of(row: Download, library: Library, origin: str) -> Path:
    if origin == "unpacked":
        # Nothing comes out of the unpack folder when it is a link (``hidden``).
        if not hidden.safe(library.path, unpacking.FOLDER):
            raise Stop("place_failed", {"reason": "link"})
        return unpacking.folder_for(library.path, row.id)
    path = Path(row.source_path)
    return path.parent if path.is_file() else path


# --- Step 1: check ---------------------------------------------------------------------------------------------------


def _probe_all(db: Session, row: Download, library: Library, found: list[tuple[str, incoming.Item]]) -> None:
    """Reads every file in the helper process of the scan and writes it down (the cover is not kept: the filing reads
    the placed file again)."""
    db.execute(delete(PlaceFile).where(PlaceFile.download_id == row.id))
    with scanner.Worker(stop=jobs.stopping.is_set) as worker:
        for origin, item in found:
            if jobs.stopping.is_set():
                raise jobs.Interrupted
            root = _root_of(row, library, origin)
            probe: dict[str, Any] | None = None
            reason = ""
            try:
                probe = worker.read(str(root), item.path, item.format)
                probe.pop("cover", None)
            except scanner.Stopping:
                raise jobs.Interrupted from None
            except scanner.ProbeFailed as exc:
                reason = exc.code
            except Exception:  # noqa: BLE001 - a file that cannot be read is left out, the rest goes on
                reason = "unreadable"
            unit = catalog.unit_key(item.path, item.format, probe, release=True) \
                if probe is not None and item.role == ROLE_BOOK else ""
            db.add(PlaceFile(download_id=row.id, path=item.path[:1500], origin=origin, size=item.size,
                             format=item.format, role=item.role, unit=unit, probe=probe,
                             state="found" if probe is not None else "drop", reason=reason))
    db.flush()


def _work_subject(db: Session, uid: int | None, work_id: int, accepted: list[str]) -> dict[str, Any] | None:
    if uid is not None:
        try:
            return search.subject_of_work(db, uid, work_id, accepted)
        except HTTPException:
            pass
    work = db.get(Work, work_id)
    if work is None:
        return None
    names = list(db.scalars(select(Credit).join(Edition, Edition.id == Credit.edition_id)
                            .where(Edition.work_id == work_id, Credit.role == AUTHOR).limit(6)))
    from ...models import Person

    people = [db.get(Person, credit.person_id) for credit in names]
    return {"title": work.title, "titles": [{"text": work.title, "language": ""}],
            "authors": list(dict.fromkeys(person.name for person in people if person is not None)),
            "series": "", "position": work.series_pos, "duration": None, "have": {}, "have_by": {}}


def subject_of(db: Session, row: Download, accepted: list[str]) -> dict[str, Any] | None:
    """The book the download is for, as the check needs it; None for an upload of a book nexbooks does not know."""
    if row.work_id is not None:
        return _work_subject(db, row.user_id, row.work_id, accepted)
    if row.foreign_id is not None:
        book = db.get(ForeignBook, row.foreign_id)
        return search.subject_of_foreign(db, book, accepted, row.user_id) if book is not None else None
    return None


def edition_format(db: Session, edition: Edition, config: dict[str, Any]) -> str | None:
    """The best format of an edition in the words of the ranking."""
    files = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK,
                                                   BookFile.missing_at.is_(None))))
    if not files:
        return None
    if edition.kind == EDITION_EBOOK:
        order = [item["f"] for item in config["ebook"]]
        have = [row.format for row in files]
        ranked = sorted(have, key=lambda fmt: order.index(fmt) if fmt in order else len(order))
        return ranked[0] if ranked else None
    seconds = sum(int(row.duration_ms or 0) for row in files) / 1000 or float(edition.duration or 0)
    size = sum(int(row.size or 0) for row in files)
    codecs = [row.format for row in files]
    codec = max(set(codecs), key=codecs.count)
    bitrate = checking.measured_rate(files) if all(isinstance(row.probe, dict) for row in files) else None
    if bitrate is None:
        bitrate = int(size * 8 / seconds / 1000) if seconds > 0 and size > 0 else None
    return judge.audio_format("m4b" if codec in ("m4a", "m4b") else codec, bitrate)


def have_of(db: Session, edition: Edition, config: dict[str, Any]) -> checking.Have | None:
    """What the library has in an edition, as the check compares it with a book that comes (None: it has no file)."""
    fmt = edition_format(db, edition, config)
    if fmt is None:
        return None
    return checking.Have(fmt=fmt, variant=edition.variant or "", language=edition.language or "",
                         narrators=_narrators(db, edition), seconds=float(edition.duration or 0),
                         isbn=edition.isbn or "", translators=_narrators(db, edition, TRANSLATOR),
                         pages=int(edition.pages or 0), publisher=edition.publisher or "", title=edition.title or "",
                         year=(edition.published or "")[:4])


def _narrators(db: Session, edition: Edition, role: str = NARRATOR) -> list[str]:
    """The people of an edition in one role: its readers, or with ``role`` its translators."""
    from ...models import Person

    return list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
        Credit.edition_id == edition.id, Credit.role == role).order_by(Credit.position)))


def same_as(db: Session, edition: Edition, other: Edition, config: dict[str, Any],
            assume: bool | None = None) -> bool:
    """Whether ``other`` is the same version of the book as ``edition`` (the one just filed): see
    ``checking.same_version``. In doubt it is not, unless a person said so (``assume``)."""
    have = have_of(db, other, config)
    own = have_of(db, edition, config)
    mine = checking.Have(fmt=own.fmt if own is not None else "", isbn=edition.isbn or "",
                         translators=_narrators(db, edition, TRANSLATOR), pages=int(edition.pages or 0),
                         publisher=edition.publisher or "", title=edition.title or "",
                         year=(edition.published or "")[:4])
    return have is not None and checking.same_version(
        edition.kind, have, variant=edition.variant or "", language=edition.language or "",
        narrators=_narrators(db, edition), seconds=float(edition.duration or 0), text=mine, assume=assume)


def _rank(config: dict[str, Any], kind: str, fmt: str | None) -> int:
    order = [item["f"] for item in config[kind]]
    return order.index(fmt) if fmt in order else len(order)


def editions_there(db: Session, row: Download, library_id: int, language: str) -> list[Edition]:
    """The editions of the download's work in the library, of its kind and (when one was wished) its language."""
    if row.work_id is None:
        return []
    query = select(Edition).where(Edition.work_id == row.work_id, Edition.library_id == library_id,
                                  Edition.kind == row.kind, Edition.missing_at.is_(None))
    found = list(db.scalars(query.order_by(Edition.id)))
    if language:
        found = [edition for edition in found if edition.language in ("", language)]
    return found


def _catalogued(db: Session, row: Download, subject: dict[str, Any] | None) -> list[ForeignBook]:
    """What the sources know of the book of a download: the rows of its book of the sources, and of a work of a library
    the rows of its title and authors (a wish joins the work once the first form of a book is on the shelf)."""
    from ..discover import foreign

    book = db.get(ForeignBook, row.foreign_id) if row.foreign_id is not None else None
    if book is not None:
        return foreign.family(db, book)
    if subject and subject.get("title"):
        return foreign.named(db, str(subject["title"]), [str(name) for name in subject.get("authors") or []])
    return []


def _runtimes(db: Session, row: Download, subject: dict[str, Any] | None) -> dict[str, list[int]]:
    """The running times the catalogue knows of the book, in seconds, by version; under ``checking.UNSAID`` those of a
    source that does not say which version it lists. Every row speaks for itself: two sources are two witnesses, also
    when they name the same version."""
    from ..discover import foreign

    found: dict[str, list[int]] = {"unabridged": [], "abridged": [], "dramatized": [], checking.UNSAID: []}
    if row.work_id is not None and subject and subject.get("duration"):
        # A recording of the work that is proven unabridged (``search.subject_of_work``).
        found["unabridged"].append(int(subject["duration"]))
    for book in _catalogued(db, row, subject):
        data = book.data if isinstance(book.data, dict) else {}
        for form in data.get("forms") or []:
            minutes = form.get("minutes") if isinstance(form, dict) else None
            if not isinstance(minutes, int) or minutes <= 0 or form.get("kind") != "audio" or (
                    row.language and form.get("language") not in ("", None, row.language)):
                continue
            version = foreign.version_of(form)
            if version in found or not version:
                found[version or checking.UNSAID].append(minutes * 60)
    if not found["unabridged"] and subject and subject.get("authors"):
        found = _audible_runtimes(db, subject, row.language, found)
    return found


#: The Audible market asked for the running time of a book in a language.
MARKETS = {"de": "de", "en": "us", "fr": "fr", "it": "it", "es": "es"}


def _audible_runtimes(db: Session, subject: dict[str, Any], language: str,
                      found: dict[str, list[int]]) -> dict[str, list[int]]:
    from ..readers import names
    from ..sources import audible
    from ..sources.model import Query

    market = MARKETS.get(language or "", "")
    if not market:
        return found
    author = str((subject.get("authors") or [""])[0])
    title = str(subject.get("title") or "")
    try:
        candidates = audible.search(db, Query(kind="audio", title=title, authors=[author], language=language), market)
    except Exception:  # noqa: BLE001 - a catalogue that cannot be asked says nothing; the check goes on without it
        return found
    for candidate in candidates[:10]:
        # The running time of the book wished, never of another book whose title holds its title (``Wir wollen Ruß
        # sehen`` for ``Ruß``): the same title rule as the releases.
        if not candidate.runtime_min or checking.title_against(candidate.title, subject) != "yes":
            continue
        if not any(names.authors_compatible(name, author) for name in candidate.authors):
            continue
        if language and candidate.languages and language not in candidate.languages:
            # A recording in another language that a market lists as well: another length, and no word about this one.
            continue
        version = candidate.variant or checking.UNSAID
        if version in found:
            found[version].append(int(candidate.runtime_min) * 60)
    return found


def _others(db: Session, row: Download, accepted: list[str]) -> list[checking.Other]:
    """The other open wishes of the library and kind: a volume of a bundle may fill one."""
    found: list[checking.Other] = []
    for wish in db.scalars(select(ProcureWish).where(
            ProcureWish.library_id == row.library_id, ProcureWish.kind == row.kind, ProcureWish.state != "done",
            ProcureWish.id != (row.wish_id or 0)).limit(50)):
        if wish.work_id is not None:
            subject = _work_subject(db, wish.user_id, wish.work_id, accepted)
        elif wish.foreign_id is not None:
            book = db.get(ForeignBook, wish.foreign_id)
            subject = search.subject_of_foreign(db, book, accepted, wish.user_id) if book is not None else None
        else:
            subject = None
        if subject is not None:
            found.append(checking.Other(wish_id=wish.id, subject=subject))
    return found


def wanted_of(db: Session, row: Download, library: Library, units: list[checking.Unit]) -> checking.Wanted:
    config = formats.config(db)
    accepted = search.accepted_of(db, row.language, search.original_of(db, row.work_id, row.foreign_id))
    subject = subject_of(db, row, accepted)
    wish = db.get(ProcureWish, row.wish_id) if row.wish_id is not None else None
    haves = [item for item in (have_of(db, edition, config)
                               for edition in editions_there(db, row, library.id, row.language)) if item is not None]
    upgrade = wish is not None and (wish.state == "upgrade" or bool((wish.detail or {}).get("have")))
    mine = [unit for unit in units if unit.kind == row.kind]
    decision = {key: value for key, value in (row.decision or {}).items() if not key.startswith("_")}
    name = release_name.parse(row.title or "")
    matched = release_match.match(name, checking.book_of(subject)) if subject else None
    # (A word of a language in the name of the author or the title is no mark: ``Lena French``, ``The Dutch Garden``.)
    code, how = release_name.language_of(name.language_signs, judge.names_of_the_book(matched) if matched else ())
    original = search.original_of(db, row.work_id, row.foreign_id)
    return checking.Wanted(
        kind=row.kind, language=row.language, accepted=accepted, subject=subject, config=config,
        upload=row.protocol == "upload", haves=haves, upgrade=upgrade,
        named_book=bool(matched and matched.title and matched.author),
        named_language=(code or "") if how in ("mark", "words") else "", original=original,
        several_allowed=len(formats.allowed_languages(db, original)) > 1,
        category_language=str((row.decision or {}).get("_category") or "")[:8],
        title_language=str((row.decision or {}).get("_title") or "")[:8],
        named_author=bool(subject) and release_match.match(name, checking.book_of(subject)).author,
        others=_others(db, row, accepted) if len(mine) > 1 and row.protocol != "upload" else [],
        runtimes=_runtimes(db, row, subject) if row.kind == EDITION_AUDIO and subject else {},
        decision=decision)


def check(db: Session, row: Download, library: Library) -> None:
    """Step 1. Leaves the download ``checked``, a problem with a question, or turned down."""
    probe = formats.probe_library(library.path)
    if not probe["writable"]:
        raise Stop("no_write", {"library_id": library.id, "reason": probe["reason"]})
    known = list(db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id)))
    if not known:
        root, only = _source(db, row)
        walked = incoming.walk(root, only)
        if walked.offline:
            raise Stop("path_not_found", {"search": "no_path"})
        if walked.dangerous:
            return turn_down(db, row, library, "dangerous", {"files": len(walked.dangerous)})
        found: list[tuple[str, incoming.Item]] = [("source", item) for item in walked.books + walked.sidecars]
        if not walked.books and walked.archives:
            folder = unpacking.folder_for(library.path, row.id)
            unpacking.remove(folder)
            try:
                unpacking.unpack([(root / path, kind) for path, _size, kind in walked.archives], folder)
            except unpacking.Refused as exc:
                return turn_down(db, row, library, exc.code, {})
            except hidden.Unsafe:
                raise Stop("place_failed", {"reason": "link"}) from None
            inside = incoming.walk(folder)
            if inside.dangerous:
                return turn_down(db, row, library, "dangerous", {"files": len(inside.dangerous)})
            found = [("unpacked", item) for item in inside.books + inside.sidecars]
            if (row.decision or {}).get(KIND_OPEN) and inside.books:
                # An upload of a ZIP without a kind said: what comes out of it tells (never an e-book by default).
                kinds = inside.kinds()
                if len(kinds) > 1:
                    return turn_down(db, row, library, "upload_mixed", {})
                kind = next(iter(kinds))
                if not libraries.kind_allows(library.kind, kind):
                    return turn_down(db, row, library, "other_kind", {})
                row.kind = kind
                row.decision = None
        if not any(item.role == ROLE_BOOK for _origin, item in found):
            return turn_down(db, row, library, "archive_unsupported" if walked.archives else "no_book", {})
        _probe_all(db, row, library, found)
        db.commit()
        known = list(db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id)))
    books = [item for item in known if item.role == ROLE_BOOK and item.probe is not None]
    sidecars = [item for item in known if item.role == ROLE_SIDECAR and item.probe is not None]
    if not books:
        return turn_down(db, row, library, "no_book", {"unreadable": sum(1 for item in known if item.probe is None)})
    whole = bool((row.decision or {}).get("whole"))
    for item in books:
        # A download that waited for a person was grouped by the rules of its day: every check groups it anew.
        item.unit = catalog.unit_key(item.path, item.format, item.probe, release=True)
        if whole and checking.kind_of_format(item.format) == EDITION_AUDIO:
            item.unit = catalog.WHOLE_UNIT
    units = checking.units_of(books, sidecars)
    wanted = wanted_of(db, row, library, units)
    verdict = checking.judge_units(units, wanted)
    if verdict.outcome == "reject":
        return turn_down(db, row, library, verdict.code, verdict.values)
    if verdict.outcome == "ask":
        values = dict(verdict.values)
        if verdict.code == "bundle":
            values["units"] = [{**item, "unit": _unit_token(item["unit"])} for item in values.get("units", [])]
        raise Stop(verdict.code, values)
    by_key = {unit.key: unit for unit in units}
    for item in known:
        key = f"{item.origin}|{item.unit}" if item.role == ROLE_BOOK else ""
        if item.probe is None:
            item.state = "drop"
            continue
        if item.role == ROLE_BOOK:
            if key in verdict.take and checking.file_key(item) not in verdict.drop:
                item.state, item.reason, item.wish_id = "take", "", verdict.take[key]
            else:
                item.state, item.reason = "drop", verdict.drop.get(checking.file_key(item), "bundle_rest")
    for item in known:
        if item.role != ROLE_SIDECAR or item.probe is None:
            continue
        # A file beside a recording goes with it, when the recording's folder is its folder.
        owner = next((unit for key, unit in by_key.items() if key in verdict.take and unit.kind == EDITION_AUDIO
                      and key.split("|", 1)[0] == item.origin
                      and _book_folder(item.path) in (catalog.unit_folder(key.split("|", 1)[1]),
                                                      _book_folder(unit.files[0].path))), None)
        item.state = "take" if owner is not None else "drop"
        item.unit = owner.key.split("|", 1)[1] if owner is not None else ""
        item.wish_id = owner.wish_id if owner is not None else None
    row.decision = {**(row.decision or {}), "_verdict": {"variant": verdict.variant, "proven": verdict.variant_proven,
                                                         "fmt": verdict.fmt, "replaces": verdict.replaces,
                                                         "language": verdict.language}}
    row.place_step = "checked"
    row.check_rev = checking.CHECK_REV
    row.problem_code, row.problem_values = "", None
    db.commit()
    logger.info("Download %d checked: files=%d taken=%d", row.id, len(known),
                sum(1 for item in known if item.state == "take"))


def _unit_token(key: str) -> str:
    """The name a person picks a unit of a bundle by: a short hash, never the path."""
    import hashlib

    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]


def unit_of_token(db: Session, row: Download, token: str) -> str | None:
    for item in db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id, PlaceFile.role == ROLE_BOOK)):
        key = f"{item.origin}|{item.unit}"
        if _unit_token(key) == token:
            return key
    return None


# --- Turning a release down ------------------------------------------------------------------------------------------


def turn_down(db: Session, row: Download, library: Library | None, code: str, values: dict[str, Any]) -> None:
    """The release is not the book (or not as wished): blocked for the book, the files leave, the wish searches on."""
    if row.protocol != "upload":
        title, author = _book_text(db, row)
        key = blocklist.book_key({"authors": [author] if author else [], "title": title})
        blocklist.block(db, None, row, work_id=row.work_id,
                        foreign_id=row.foreign_id if row.work_id is None else None,
                        reason=BLOCK_REASONS.get(code, "not_a_book"), book=key, library_id=row.library_id,
                        language=str(values.get("file") or "") if code == "other_language" else "")
        if row.client_ref:
            row.removal_pending = True
    note = {"code": code, **{k: v for k, v in values.items() if isinstance(v, str | int | float | list) or v is None}}
    history.record(db, row, "rejected", note)
    moment = clock.now()
    row.state, row.finished_at, row.updated_at = "rejected", moment, moment
    row.problem_code = "language" if code == "other_language" else ""
    row.problem_values = {"file": values.get("file", ""), "wanted": values.get("wanted", "")} \
        if code == "other_language" else None
    row.failed_reason = code[:32]
    db.execute(delete(PlaceFile).where(PlaceFile.download_id == row.id))
    db.commit()
    _tidy(row, library, keep_source=row.protocol == "torrent")
    logger.info("Download %d turned down by the check code=%s", row.id, code)
    if row.protocol != "upload":
        from . import wishes

        try:
            wishes.after_failure(db, row)
        except Exception:
            db.rollback()
            logger.exception("The wish of download %d could not go on", row.id)


def _book_text(db: Session, row: Download) -> tuple[str, str]:
    from . import wishes

    return wishes.book_text(db, row.user_id, work_id=row.work_id, foreign_id=row.foreign_id)


def _clear_partials(db: Session, row: Download, library: Library | None) -> None:
    """A copy that stopped in the middle left a hidden partial file at the goal; when the download goes for good, the
    partial files of its own goals go (and only those)."""
    if library is None:
        return
    for target in db.scalars(select(PlaceFile.target).where(PlaceFile.download_id == row.id, PlaceFile.target != "")):
        placing.clear_own_partials(Path(library.path), target)


def _tidy(row: Download, library: Library | None, *, keep_source: bool) -> None:
    """Removes what nexbooks made for the filing: the unpacked folder, and the received files of an upload."""
    if library is not None:
        unpacking.remove(unpacking.folder_for(library.path, row.id))
    if row.protocol == "upload" and row.source_path and not keep_source:
        remove_upload_folder(Path(row.source_path))


def tidy_after_cancel(db: Session, row: Download) -> None:
    """A download a person took away: the journal of its filing, the unpacked files and the received files go."""
    library = db.get(Library, row.library_id) if row.library_id is not None else None
    _clear_partials(db, row, library)
    db.execute(delete(PlaceFile).where(PlaceFile.download_id == row.id))
    db.commit()
    _tidy(row, library, keep_source=False)


def remove_upload_folder(folder: Path) -> None:
    if not hidden.holds(folder, UPLOAD_FOLDER):
        return
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.parent.rmdir()
    except OSError:
        pass


# --- Step 2: place ---------------------------------------------------------------------------------------------------


def _book_values(db: Session, row: Download, wish_id: int | None) -> dict[str, Any]:
    """What nexbooks knows of the book a file goes in as: its title, authors, series, place and year."""
    work_id, foreign_id, uid = row.work_id, row.foreign_id, row.user_id
    if wish_id is not None:
        wish = db.get(ProcureWish, wish_id)
        if wish is not None:
            work_id, foreign_id, uid = wish.work_id, wish.foreign_id, wish.user_id
    if work_id is not None and uid is not None:
        try:
            view = works.get_work(db, uid, work_id)
        except HTTPException:
            view = None
        if view is not None:
            return {"title": view.title, "authors": [name for _id, name in view.authors],
                    "series": view.series_name, "position": view.shown.series_pos, "year": view.shown.year}
    if work_id is None and foreign_id is not None:
        book = db.get(ForeignBook, foreign_id)
        if book is not None:
            return {"title": book.title, "authors": [n for n in book.authors or [] if isinstance(n, str)],
                    "series": book.series, "position": book.series_pos, "year": (book.released or "")[:4]}
    return {}


def _beside(db: Session, row: Download, library: Library) -> str | None:
    """For an upgrade of an e-book: the file of the edition the new format goes next to."""
    for edition in editions_there(db, row, library.id, row.language):
        lead = db.scalar(select(BookFile.path).where(BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK,
                                                     BookFile.missing_at.is_(None)).order_by(BookFile.id).limit(1))
        if lead:
            return str(lead)
    return None


def _book_folder(path: str) -> str:
    """The folder of a recording in a release: the folder of the file without the folders of parts at its end, also
    at the top of the release (a release may be just ``CD 1``, ``CD 2``)."""
    from ..readers.names import strip_part_folders

    return strip_part_folders(path.rsplit("/", 1)[0] if "/" in path else "", release=True)


def _plan_targets(db: Session, row: Download, library: Library, taken: list[PlaceFile]) -> None:
    """Gives every book that has no goal yet its goal. A book that has goals is left as it is (a file of it may be
    placed already), and what it takes is not given to another."""
    root = Path(library.path)
    config = formats.config(db)
    verdict = (row.decision or {}).get("_verdict") or {}
    groups: dict[str, list[PlaceFile]] = {}
    for item in taken:
        groups.setdefault(f"{item.origin}|{item.unit}", []).append(item)
    folders: set[str] = set()
    planned: set[str] = set()
    for items in groups.values():
        if all(item.target for item in items):
            planned.update(item.target for item in items)
            if any(checking.kind_of_format(item.format) == EDITION_AUDIO for item in items if item.role == ROLE_BOOK):
                folders.add(posixpath.commonpath([posixpath.dirname(item.target) for item in items]))
    for _key, items in sorted(groups.items()):
        if all(item.target for item in items):
            continue
        books = [item for item in items if item.role == ROLE_BOOK]
        if not books:
            continue
        unit = checking.units_of(books, [item for item in items if item.role == ROLE_SIDECAR])[0]
        wish_id = books[0].wish_id
        values = placing.values_of(_book_values(db, row, wish_id), unit.meta, row.language)
        parts = placing.render(config["schema"], values)
        if unit.kind == EDITION_EBOOK:
            beside = _beside(db, row, library) if verdict.get("replaces") and wish_id is None else None
            goal = placing.ebook_goal(root, parts, [(str(item.id), item.format, item.size) for item in items],
                                      beside=beside, planned=planned)
            planned.update(goal.targets.values())
        else:
            unit_dir = _book_folder(posixpath.commonpath([item.path for item in books]) + "/file"
                                    if len(books) > 1 else books[0].path)
            goal = placing.audio_goal(root, parts, [(str(item.id), item.path, item.size) for item in items],
                                      unit_dir, taken=folders)
            folders.add(goal.folder)
        for item in items:
            item.target = goal.targets[str(item.id)][:1500]


def place(db: Session, row: Download, library: Library) -> None:
    """Step 2: every file that is taken goes to its goal."""
    taken = list(db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id,
                                                    PlaceFile.state.in_(("take", "placed"))).order_by(PlaceFile.id)))
    if not taken:
        return turn_down(db, row, library, "no_book", {})
    if any(not item.target for item in taken if item.state == "take"):
        try:
            _plan_targets(db, row, library, [item for item in taken])
        except placing.PlaceError as exc:
            raise Stop("place_failed", {"reason": exc.code}) from None
        db.commit()
    root = Path(library.path)
    how = "link" if row.protocol == "torrent" else "move"
    # The room for the whole release, before the first file moves: what must be copied (another disk) together, so that
    # a recording is never left half in the library for lack of room.
    copying = sum(item.size for item in taken if item.state == "take"
                  and placing.crosses_disks(_root_of(row, library, item.origin).joinpath(*item.path.split("/")), root))
    if copying and not placing.room_for(root, copying):
        raise Stop("no_space", {"where": "library"})
    for item in taken:
        if item.state == "placed":
            continue
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        goal = root.joinpath(*item.target.split("/"))
        source_root = _root_of(row, library, item.origin)
        placing.clear_partials(goal.parent)
        left = _left_over(goal, source_root, item, how)
        if left:
            item.state, item.how = "placed", item.how or left
            db.commit()
            continue
        try:
            checked = placing.source_file(source_root, item.path)
            item.how = placing.place(checked, item.size, root, item.target, how=how)
        except placing.PlaceError as exc:
            db.commit()
            if exc.code in ("no_write",):
                raise Stop("no_write", {"library_id": library.id, "reason": "write_failed"}) from None
            if exc.code == "no_space":
                raise Stop("no_space", {"where": "library"}) from None
            raise Stop("place_failed", {"reason": exc.code}) from None
        except jobs.Interrupted:
            db.commit()
            raise
        except Exception:  # noqa: BLE001 - a path that is no path of the folder (``ReadError``)
            db.commit()
            raise Stop("place_failed", {"reason": "outside"}) from None
        item.state = "placed"
        db.commit()
    row.place_step = "placed"
    db.commit()
    logger.info("Download %d placed: files=%d", row.id, len(taken))


def _left_over(goal: Path, source_root: Path, item: PlaceFile, how: str) -> str:
    """What an earlier try that stopped left: the file lies at its goal already, as itself (its size and its content),
    so that it counts as placed. A moved file whose old name is still there (the try stopped between making the new
    name and taking the old one away) gets that name taken away now. Empty when the goal holds something else."""
    if not placing.same_file(goal, item.size):
        return ""
    source = source_root.joinpath(*item.path.split("/"))
    if not os.path.lexists(source):
        return "rename" if how == "move" else ""
    try:
        checked = placing.source_file(source_root, item.path)
    except placing.PlaceError:
        return ""
    same = _same_inode(goal, checked) or (placing.same_file(checked, item.size)
                                          and placing.sha256(goal) == placing.sha256(checked))
    if not same:
        return ""
    if how == "move":
        placing.remove(checked)
        return "rename"
    return "link" if _same_inode(goal, checked) else "copy"


def _same_inode(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


# --- Step 3: file ----------------------------------------------------------------------------------------------------


def _read_placed(row: Download, library: Library, placed: list[PlaceFile]) -> dict[int, dict[str, Any]]:
    """The placed files read again where they lie now, as the scan reads them (with the cover)."""
    probes: dict[int, dict[str, Any]] = {}
    with scanner.Worker(stop=jobs.stopping.is_set) as worker:
        for item in placed:
            try:
                probes[item.id] = scanner._store_probe(worker.read(library.path, item.target, item.format))
            except scanner.Stopping:
                raise jobs.Interrupted from None
            except Exception:  # noqa: BLE001 - what was read before stands in
                probes[item.id] = dict(item.probe or {})
    return probes


def _foreign_ref(book: ForeignBook) -> tuple[str, str, str]:
    source, _, key = book.ref.partition(":")
    kind = "edition" if source in ("audible", "apple") else "work"
    return source[:16], kind, key[:120]


def _home_of_foreign(db: Session, book: ForeignBook, library_id: int) -> int | None:
    """The work a book of the sources was filed into before (its name at the source on one of its editions)."""
    source, kind, key = _foreign_ref(book)
    return db.scalar(select(Edition.work_id).join(ExternalRef, ExternalRef.edition_id == Edition.id).where(
        ExternalRef.source == source, ExternalRef.kind == kind, ExternalRef.key == key,
        Edition.missing_at.is_(None)).order_by(Edition.library_id != library_id, Edition.work_id).limit(1))


def _home_of_downloads(db: Session, row: Download) -> int | None:
    """The work the other downloads for the same book of the sources were filed into (the first one filed): an e-book
    and a recording of one wish are one book, whatever their files call themselves."""
    if row.foreign_id is None:
        return None
    return db.scalar(select(Edition.work_id).join(Download, Download.edition_id == Edition.id).where(
        Download.foreign_id == row.foreign_id, Download.id != row.id, Download.state == "imported",
        Edition.missing_at.is_(None)).order_by(Download.id).limit(1))


def _to_work(db: Session, edition: Edition, work_id: int | None, rows: list[BookFile], how: str = "wish") -> None:
    """The edition goes under the work that was wished, unless it holds files that came another way. ``how``: ``wish``
    (the files were checked as the book wished) or ``hand`` (a person said which book they are); either way no
    grouping takes it away from that work again (``catalog.leave_foreign_work``)."""
    if work_id is None or db.get(Work, work_id) is None:
        return
    if edition.work_id == work_id:
        _placed(edition, how)
        return
    own = {row.id for row in rows}
    other = db.scalar(select(func.count(BookFile.id)).where(BookFile.edition_id == edition.id,
                                                            BookFile.role == ROLE_BOOK, BookFile.id.not_in(own)))
    if other:
        logger.info("Edition %d holds other files and stays in its work", edition.id)
        return
    match_apply.move(db, edition, work_id, how)


def _how(row: Download) -> str:
    """``hand`` when a person said which book the files are (an upload for a book, a download taken by hand), else
    ``wish``: the files were checked as the book wished. Nothing automatic moves such an edition again."""
    decision = row.decision or {}
    return "hand" if row.protocol == "upload" or decision.get("take") or decision.get("unit") else "wish"


def _placed(edition: Edition, how: str) -> None:
    """The edition stands in its work because a wish or a person put it there (a person's word is never lowered)."""
    if edition.grouped_by != "hand":
        edition.grouped_by = how


def _variant(db: Session, row: Download, edition: Edition) -> None:
    verdict = (row.decision or {}).get("_verdict") or {}
    chosen = (row.decision or {}).get("variant")
    if edition.kind != EDITION_AUDIO:
        return
    if chosen:
        match_apply.correct(db, edition, {"variant": chosen})
    elif verdict.get("proven") and verdict.get("variant") in ("unabridged", "abridged", "dramatized"):
        # (Into the overlay as well: the next time the book is put together only the overlay is left of what the check
        # proved, and what the files say would take its place.)
        edition.overlay = match_overlay.with_guess(edition.overlay, "variant", verdict["variant"]) or None
        edition.variant, edition.variant_uncertain = verdict["variant"], False


def _upgrade(db: Session, row: Download, library: Library, edition: Edition, new_fmt: str,
             new_paths: frozenset[str] = frozenset()) -> dict[str, Any] | None:
    """Puts what the new format betters into the trash and carries the places of the people over. Returns what was
    replaced."""
    config = formats.config(db)
    kind = edition.kind
    worse_rank = _rank(config, kind, new_fmt)
    title, author = _book_text(db, row)
    replaced: dict[str, Any] | None = None
    if kind == EDITION_EBOOK:
        before = _reading_format(db, edition, new_paths)
        old = [file for file in db.scalars(select(BookFile).where(
            BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None)))
            if _rank(config, kind, file.format) > worse_rank and file.format != new_fmt]
        if old:
            fmt = min((file.format for file in old), key=lambda f: _rank(config, kind, f))
            entry = trash.put(db, library, old, kind=kind, title=title, author=author, fmt=fmt,
                              work_id=edition.work_id, edition_id=edition.id, download_id=row.id)
            replaced = {"from": fmt, "to": new_fmt, "trash_id": entry.id}
        # Other e-book editions of the work in the library (of the language) that are worse go too.
        for other in editions_there(db, row, library.id, row.language):
            if other.id == edition.id or other.kind != kind or not same_as(
                    db, edition, other, config, assume=(row.decision or {}).get("replace") is True):
                continue
            other_fmt = edition_format(db, other, config)
            if other_fmt is None or _rank(config, kind, other_fmt) <= worse_rank:
                continue
            files = list(db.scalars(select(BookFile).where(BookFile.edition_id == other.id,
                                                           BookFile.missing_at.is_(None))))
            entry = trash.put(db, library, files, kind=kind, title=title, author=author, fmt=other_fmt,
                              work_id=other.work_id, edition_id=None, download_id=row.id)
            replaced = replaced or {"from": other_fmt, "to": new_fmt, "trash_id": entry.id}
            if not merge.files_left(db, other.id):
                merge.absorb(db, edition, other, 0, [])
        after = _reading_format(db, edition)
        if before and after and before != after:
            _carry_places(db, row, library, edition, before, after, replaced)
        return replaced
    # A recording: the worse recordings of the work in the library go into the trash; the places move to the new one.
    for other in editions_there(db, row, library.id, row.language):
        if other.id == edition.id or not same_as(db, edition, other, config):
            continue
        other_fmt = edition_format(db, other, config)
        if other_fmt is None or _rank(config, kind, other_fmt) <= worse_rank:
            continue
        files = list(db.scalars(select(BookFile).where(BookFile.edition_id == other.id, BookFile.missing_at.is_(None))))
        folders = {catalog._unit_dir(file.path) for file in files if file.role == ROLE_BOOK}
        beside = [side for side in db.scalars(select(BookFile).where(
            BookFile.library_id == library.id, BookFile.role == ROLE_SIDECAR, BookFile.missing_at.is_(None)))
            if catalog._dir_of(side.path) in folders and side not in files]
        old_seconds = float(other.duration or 0)
        entry = trash.put(db, library, files + beside, kind=kind, title=title, author=author, fmt=other_fmt,
                          work_id=other.work_id, edition_id=None, download_id=row.id)
        replaced = replaced or {"from": other_fmt, "to": new_fmt, "trash_id": entry.id}
        db.flush()
        if not merge.files_left(db, other.id):
            chapters = [{"title": c.title, "start_ms": c.start_ms}
                        for c in works.chapters_of(db, [edition.id]).get(edition.id, [])]
            moved = [spot.user_id for spot in db.scalars(select(Progress).where(Progress.edition_id == other.id))]
            merge.absorb(db, edition, other, 0, chapters)
            _rescale(db, edition, old_seconds, moved, chapters)
    return replaced


def _rescale(db: Session, edition: Edition, old_seconds: float, people: list[int],
             chapters: list[dict[str, Any]]) -> None:
    """A place in a recording that came from one of another length moves by the share of the whole (the parts are cut
    differently, the same text is read)."""
    new_seconds = float(edition.duration or 0)
    if not people or old_seconds <= 0 or new_seconds <= 0 or abs(new_seconds / old_seconds - 1) < 0.005:
        return
    ratio = new_seconds / old_seconds
    for place in db.scalars(select(Progress).where(Progress.edition_id == edition.id, Progress.user_id.in_(people))):
        seconds = merge.seconds_of(place.locator)
        if seconds is None:
            continue
        moved = min(seconds * ratio, new_seconds)
        place.locator = f"time:{moved:.3f}"
        place.fraction = min(moved / new_seconds, 1.0)
        place.chapter, place.chapter_count, place.chapter_title = merge._chapter_at(chapters, moved)


def _reading_format(db: Session, edition: Edition, skip: frozenset[str] = frozenset()) -> str | None:
    """The format the reader opens of an e-book (EPUB first, as ``READ_ORDER`` in the interface); ``skip`` leaves the
    files of these paths out (the ones just filed: what did the reader open before?)."""
    formats_there = [fmt for fmt, path in db.execute(select(BookFile.format, BookFile.path).where(
        BookFile.edition_id == edition.id, BookFile.role == ROLE_BOOK, BookFile.missing_at.is_(None)))
        if path not in skip]
    known = [fmt for fmt in formats_there if fmt in catalog.EBOOK_ORDER]
    return min(known, key=lambda fmt: catalog.EBOOK_ORDER[fmt]) if known else None


def _carry_places(db: Session, row: Download, library: Library, edition: Edition, before: str, after: str,
                  replaced: dict[str, Any] | None) -> None:
    """The reader opens another format now: a place in the old one (a page of a PDF, a CFI of a MOBI) does not lead
    anywhere in the new one. Every person's place is carried over by its share of the book, and the people who may ask
    for books in the library are told (a note of their own)."""
    from ...models import LibraryMember

    acquirers = set(db.scalars(select(LibraryMember.user_id).where(
        LibraryMember.library_id == library.id, LibraryMember.right.in_(("acquire", "manage")))))
    for place in db.scalars(select(Progress).where(Progress.edition_id == edition.id)):
        if not place.locator:
            continue
        page = place.locator[5:] if place.locator.startswith("page:") else ""
        old = {"locator": place.locator, "unit": place.unit, "chapter": place.chapter,
               "chapter_count": place.chapter_count, "chapter_title": place.chapter_title}
        place.locator, place.unit, place.koreader, place.kobo = "", "chapter", "", ""
        place.chapter, place.chapter_count, place.chapter_title = 0, 0, ""
        if place.user_id in acquirers and place.fraction > 0:
            db.add(ProcureNotice(
                user_id=place.user_id, edition_id=edition.id, download_id=row.id,
                trash_id=(replaced or {}).get("trash_id"), code="upgrade_position",
                detail={"from": before, "to": after, "fraction": round(float(place.fraction), 4),
                        "page": int(page) if page.isdigit() else None, "pages": int(edition.pages or 0) or None,
                        "old": old},
                created_at=clock.now()))


def file(db: Session, row: Download, library: Library) -> None:
    """Step 3: the library learns the placed files; the download is done."""
    placed = list(db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id, PlaceFile.state == "placed")
                             .order_by(PlaceFile.id)))
    if not placed:
        return turn_down(db, row, library, "no_book", {})
    probes = _read_placed(row, library, placed)
    need = scanner.READ_REV + int(library.reread_rev or 0)
    moment = clock.now()
    root = Path(library.path)
    rows_by_unit: dict[str, list[BookFile]] = {}
    wish_of_unit: dict[str, int | None] = {}
    for item in placed:
        probe = probes[item.id]
        goal = root.joinpath(*item.target.split("/"))
        info = os.lstat(goal)
        existing = db.scalar(select(BookFile).where(BookFile.library_id == library.id, BookFile.path == item.target))
        digest = placing.sha256(goal) if item.role == ROLE_BOOK else ""
        partial = kosync.partial_md5_of(str(root), item.target) if kosync.wanted(item.format, item.role) else ""
        unit = catalog.unit_key(item.target, item.format, probe) if item.role == ROLE_BOOK else ""
        duration = round(float(probe.get("duration") or 0) * 1000) if item.role == ROLE_BOOK else 0
        if existing is None:
            existing = BookFile(library_id=library.id, path=item.target, created_at=moment)
            db.add(existing)
        existing.size, existing.mtime_ns, existing.sha256, existing.format = info.st_size, info.st_mtime_ns, digest, \
            item.format
        existing.partial_md5 = partial
        existing.role, existing.duration_ms, existing.unit, existing.probe = item.role, duration, unit, probe
        existing.dirty, existing.missing_at, existing.seen_at, existing.rev = item.role == ROLE_BOOK, None, moment, need
        item.mtime_ns, item.sha256 = info.st_mtime_ns, digest
        if item.role == ROLE_BOOK:
            rows_by_unit.setdefault(unit, []).append(existing)
            wish_of_unit.setdefault(unit, item.wish_id)
    db.flush()
    verdict = (row.decision or {}).get("_verdict") or {}
    main: Edition | None = None
    others: list[tuple[int, Edition]] = []
    stranger = False
    renamed = ""
    fitting: bool | None = None
    for unit, rows in rows_by_unit.items():
        edition = scanner._assign_unit(db, library, unit)
        db.flush()
        if edition is None:
            continue
        wish_id = wish_of_unit.get(unit)
        if wish_id is None:
            main = main or edition
            if main is edition:
                # Before the edition joins the work wished (whose titles it would bring along).
                fitting = None if row.protocol == "upload" else fits_wish(db, row, edition)
                if is_the_wish(row, fitting):
                    # The book wished as it is before the edition joins it (it would bring its own title along).
                    wished = _wished_subject(db, row)
                    _attach(db, row, library, edition, rows)
                    renamed = _name_after_wish(db, edition, wished, fitting is None and row.protocol != "upload")
                    _language_as_answered(db, row, edition)
                    _language_of_the_wish(db, row, edition)
                else:
                    # The reading of the files says another book: it stays a book of its own, and the wish, the
                    # book of the sources and the work wished stay apart from it.
                    stranger = True
        else:
            wish = db.get(ProcureWish, wish_id)
            if wish is not None:
                _to_work(db, edition, wish.work_id, rows)
            others.append((wish_id, edition))
    replaced = None
    if main is not None and not stranger:
        _variant(db, row, main)
        if verdict.get("replaces") and row.protocol != "upload":
            try:
                replaced = _upgrade(db, row, library, main, str(verdict.get("fmt") or ""),
                                    frozenset(item.target for item in placed))
            except hidden.Unsafe:
                # The trash folder is a link: nothing goes there, the old format stays, the person is told.
                raise Stop("place_failed", {"reason": "link"}) from None
        db.flush()
        main = db.get(Edition, main.id)
    if main is not None:
        row.edition_id = main.id
        if row.work_id is None and not stranger:
            row.work_id = main.work_id
        work = db.get(Work, main.work_id)
        if work is not None:
            catalog.refresh_work(db, work)
        search_index.update(db, [main.id])
    row.replaced = replaced
    if replaced and main is not None and row.user_id is not None and db.scalar(select(ProcureNotice.id).where(
            ProcureNotice.user_id == row.user_id, ProcureNotice.download_id == row.id,
            ProcureNotice.code == "upgrade_position")) is None:
        # The person who wished it is told every time an old format goes into the trash, not only when a place in it
        # moved: "Altes Format zurückholen" is the way back.
        db.add(ProcureNotice(user_id=row.user_id, edition_id=main.id, download_id=row.id,
                             trash_id=replaced.get("trash_id"), code="upgrade_replaced",
                             detail={"from": replaced["from"], "to": replaced["to"]}, created_at=clock.now()))
    note: dict[str, Any] = {"library": library.name}
    if main is not None and main.title:
        # What the book went in as (the history shows it beside what was wished).
        note["filed_as"] = main.title[:300]
    if replaced:
        note["upgrade"] = {"from": replaced["from"], "to": replaced["to"]}
    if renamed:
        # The files called the book something that is no title of it ("Lantern Saga 2"): it went in as the book wished.
        note["file_title"] = renamed[:300]
    if (row.decision or {}).get("variant"):
        note["variant"] = row.decision["variant"]
    if others:
        note["also"] = len(others)
    if stranger:
        note["other_book"] = True
    elif fitting is False:
        # A person said to take files of another title: they went in as the book wished, and the history says so.
        note["differs"] = True
    from . import downloads as download_service

    row.place_step = "filed"
    download_service.mark_imported(db, row, row.work_id, note=note, wish_done=not stranger)
    if stranger:
        _stranger_filed(db, row)
    for wish_id, edition in others:
        wish = db.get(ProcureWish, wish_id)
        if wish is not None and wish.state != "done":
            wish.state, wish.done_at, wish.updated_at, wish.detail = "done", clock.now(), clock.now(), None
            if wish.work_id is None:
                wish.work_id, wish.foreign_id = edition.work_id, None
    jobs.enqueue(db, "scan", ref=library.id)
    db.commit()
    logger.info("Download %d filed: edition=%s replaced=%s also=%d", row.id, row.edition_id, bool(replaced),
                len(others))
    if main is not None and not stranger:
        # Another wish for the same book (another language that fits, another person) is done as well.
        from . import wishes

        wishes.met_elsewhere(db, {main.work_id})
    _after_filing(db, row, library)


def is_the_wish(row: Download, fitting: bool | None) -> bool:
    """Whether an edition just filed is the book the download was for (``fitting``: ``fits_wish``, by the title rule of
    the check): only then does it go under the work wished and take the wish, the book of the sources and the downloads
    of it along. An upload, a book a person said to take, a book nexbooks does not know and an edition whose files name
    no title are the book; one whose files name another title as a whole is not (the check keeps such a book out, this
    is the last guard: a book the recognition calls another never renames the work wished)."""
    if row.protocol == "upload" or _how(row) == "hand":
        return True
    return fitting is not False


def _title_in(subject: dict[str, Any], language: str) -> str:
    """The title of the book wished in the language of the edition, else its title."""
    for item in subject.get("titles") or []:
        if language and item.get("language") == language and item.get("text"):
            return str(item["text"])
    return str(subject.get("title") or "")


def _wished_subject(db: Session, row: Download) -> dict[str, Any] | None:
    return subject_of(db, row, search.accepted_of(db, row.language,
                                                  search.original_of(db, row.work_id, row.foreign_id)))


def _language_as_answered(db: Session, row: Download, edition: Edition) -> None:
    """A person said which language the files are in ("Ist Deutsch, einordnen"): that is the language of the edition,
    kept like a person's correction (``src`` ``wish``; taken back on the book's page, kept when the library is read
    again), whatever the package of the file says. Never over a language a person set by hand."""
    said = (row.decision or {}).get("language")
    if not isinstance(said, str) or not said or edition.language == said:
        return
    if "language" in match_overlay.hand_fields(edition.overlay):
        return
    corrected = match_overlay.with_hand(edition.overlay, {"language": said})
    corrected["language"] = {**corrected["language"], "src": "wish"}
    edition.overlay = corrected
    db.flush()
    match_apply.rebuild(db, edition)
    db.flush()
    logger.info("Edition %d took the language a person named", edition.id)


def _language_of_the_wish(db: Session, row: Download, edition: Edition) -> None:
    """A book whose files name no language passed the check as the book wished, in the language wished (the name of the
    release, its folder or a person said it): that is its language, kept like a person's answer, so that a recording of
    another market a catalogue knows by the same title does not take it away. So does a language the check held against
    a package or a tag that said a wrong one."""
    said = str(((row.decision or {}).get("_verdict") or {}).get("language") or "")
    own = edition.language and not match_overlay.is_guess(edition.overlay, "language")
    if not said and (not row.language or own):
        return
    if "language" in match_overlay.hand_fields(edition.overlay) or edition.language == (said or None):
        return
    corrected = match_overlay.with_hand(edition.overlay, {"language": said or row.language})
    corrected["language"] = {**corrected["language"], "src": "wish"}
    edition.overlay = corrected
    db.flush()
    match_apply.rebuild(db, edition)
    db.flush()
    logger.info("Edition %d took the language wished: its files name none", edition.id)


def _name_after_wish(db: Session, edition: Edition, subject: dict[str, Any] | None, nameless: bool = False) -> str:
    """An edition filed for a wish whose files carry no title of the book (only the series and a number, ``Lantern Saga
    2``; the title with the name of whoever put the file up; ``nameless``: the check found the title says nothing of
    the book) takes the title wished, kept like a person's correction (``src`` ``wish``; a person can take it back on
    the book's page). Never a title a person set. Returns the title of the files when it was replaced, else empty."""
    title = (edition.title or "").strip()
    if not title or subject is None or "title" in match_overlay.hand_fields(edition.overlay):
        return ""
    wished = names_of_titles.article_in_front(" ".join(_title_in(subject, edition.language).split()))[:300]
    # The edition is the book wished already (the check said so, or a person did): only its title is in question. A
    # title of another book never has one of the shapes below.
    if not wished or fold(wished) == fold(title):
        return ""
    position = subject.get("position") if subject.get("position") is not None else edition.series_pos
    series = [name for name in (str(subject.get("series") or ""), edition.series_name) if name]
    if not (nameless or title_shape.series_shorthand(title, series=series, position=position, wished=wished)
            or title_shape.with_uploader(title, wished, [str(name) for name in subject.get("authors") or []])):
        return ""
    corrected = match_overlay.with_hand(edition.overlay, {"title": wished})
    corrected["title"] = {**corrected["title"], "src": "wish"}
    edition.overlay = corrected
    db.flush()
    match_apply.rebuild(db, edition)
    db.flush()
    logger.info("Edition %d took the title wished: its files carry no title of the book", edition.id)
    return title


def fits_wish(db: Session, row: Download, edition: Edition) -> bool | None:
    """Whether the title of an edition is the title of the book the download was for (``checking.titles_against``);
    None when that cannot be said (the files name no title or none of a book, nexbooks does not know the book)."""
    if not (edition.title or "").strip():
        return None
    accepted = search.accepted_of(db, row.language, search.original_of(db, row.work_id, row.foreign_id))
    subject = subject_of(db, row, accepted)
    if subject is None:
        return None
    alone = db.scalar(select(func.count(Edition.id)).where(Edition.work_id == edition.work_id)) == 1
    if alone:
        # The reading put the edition into a work of its own a moment ago: its own title is no other book of the
        # author (``Nebelchor 2`` is not held against ``Nebelchor 2``).
        own = {fold(edition.title or ""), fold(edition.original_title or "")} - {""}
        subject = {**subject, "others": [item for item in subject.get("others") or [] if fold(item) not in own]}
    matched = release_match.match(release_name.parse(row.title or ""), checking.book_of(subject))
    found = checking.titles_against(edition.title, edition.original_title or "", subject,
                                    named=matched.title and matched.author)
    return None if found == "unknown" else found == "yes"


def _stranger_filed(db: Session, row: Download) -> None:
    """A book that is not the one wished went in as a book of its own: the release is blocked for the book wished and
    the wish searches on."""
    from . import wishes

    if row.protocol != "upload":
        title, author = _book_text(db, row)
        key = blocklist.book_key({"authors": [author] if author else [], "title": title})
        blocklist.block(db, None, row, work_id=row.work_id, foreign_id=row.foreign_id if row.work_id is None else None,
                        reason="title_mismatch", book=key, library_id=row.library_id)
        db.commit()
    wishes.after_interrupted(db, row)
    logger.info("Download %d filed as a book of its own: the files name another book", row.id)


def _attach(db: Session, row: Download, library: Library, edition: Edition, rows: list[BookFile]) -> None:
    """The edition goes to the work wished; a book of the sources gets its work (one, never two)."""
    if row.work_id is not None:
        _to_work(db, edition, row.work_id, rows, _how(row))
        return
    if row.foreign_id is None:
        return
    book = db.get(ForeignBook, row.foreign_id)
    if book is None:
        return
    home = _home_of_foreign(db, book, library.id) or _home_of_downloads(db, row)
    if home is not None:
        _to_work(db, edition, home, rows, _how(row))
    else:
        _placed(edition, _how(row))
    source, kind, key = _foreign_ref(book)
    if key and not db.scalar(select(ExternalRef.id).where(ExternalRef.edition_id == edition.id,
                                                          ExternalRef.source == source, ExternalRef.kind == kind,
                                                          ExternalRef.key == key, ExternalRef.market == "")):
        db.add(ExternalRef(edition_id=edition.id, source=source, kind=kind, key=key, market=""))
    # Every open wish and running download of the same book of the sources is now one of the work, and what was blocked
    # for the book stays blocked.
    db.flush()
    blocklist.to_work(db, row.foreign_id, edition.work_id)
    for wish in db.scalars(select(ProcureWish).where(ProcureWish.foreign_id == row.foreign_id,
                                                     ProcureWish.state != "done")):
        try:
            with db.begin_nested():
                wish.work_id, wish.foreign_id = edition.work_id, None
        except Exception:  # noqa: BLE001 - a wish of the work exists already: this one stays as it is
            logger.debug("Wish %d stays with its book of the sources", wish.id)
    for other in db.scalars(select(Download).where(Download.foreign_id == row.foreign_id, Download.id != row.id,
                                                   Download.state.in_(ACTIVE_DOWNLOADS), Download.work_id.is_(None))):
        try:
            with db.begin_nested():
                other.work_id = edition.work_id
        except Exception:  # noqa: BLE001 - one of the work runs already: this one keeps its book of the sources
            logger.debug("Download %d stays with its book of the sources", other.id)


def _after_filing(db: Session, row: Download, library: Library) -> None:
    """What follows outside the transaction: the Usenet job leaves its program and what nexbooks knows of it goes from
    its folder, the unpacked and received files go."""
    _tidy(row, library, keep_source=False)
    if row.protocol != "usenet" or not row.client_ref or row.client_id is None:
        return
    client = db.get(DownloadClient, row.client_id)
    if client is None:
        return
    category: str | None = None
    try:
        with open_client(programs.target_of(client)) as opened:
            remove_imported_own(opened, row.client_ref)
            category = opened.download_folder()
    except ClientError as exc:
        logger.info("Download program %d kept the filed job %d: %s", client.id, row.id, exc.code)
        return
    from . import tracking

    shared = arrival.local_path(category, tracking.mappings_of(db, client.id)) if category else None
    _tidy_job_folder(db, row, shared)


#: What a Usenet job leaves beside its book that is of no use without it: the parts of the release itself (checksums,
#: repair files, the NZB, the scene's notes, pictures of the cover), and the archives it came in.
JOB_RESTS = frozenset({"nfo", "par2", "sfv", "nzb", "srr", "srs", "md5", "url", "jpg", "jpeg", "png", "gif", "webp",
                       "rar", "7z", "zip"})
_ARCHIVE_PART = re.compile(r"\.(?:r\d{2,3}|\d{3}|part\d+\.rar)$", re.IGNORECASE)
#: A folder the program names after a job: the release name, shortened (``MIN_NAME`` letters and digits at least), or
#: with a number at its end for a second job of the same name (``.1``).
MIN_NAME = 12


def is_job_rest(name: str) -> bool:
    """A file of a release that is of no use without its book: ``JOB_RESTS`` by its ending, or a part of an archive."""
    lowered = name.casefold()
    if "." in lowered and lowered.rsplit(".", 1)[-1] in JOB_RESTS:
        return True
    return bool(_ARCHIVE_PART.search(lowered))


def named_like_job(folder_name: str, release_title: str) -> bool:
    """Whether a folder is the job's own: its name is the release's (as a program writes it)."""
    from . import foreign

    folder, release = foreign.norm(folder_name), foreign.norm(release_title)
    if not folder or not release:
        return False
    if folder == release:
        return True
    # A second job of the same name: ``<name>.1``; a shortened name: the start of the release's.
    trimmed = re.sub(r"\d{1,2}$", "", folder) if re.search(r"\.\d{1,2}$", folder_name) else folder
    return trimmed == release or (len(folder) >= MIN_NAME and release.startswith(folder))


def _tidy_job_folder(db: Session, row: Download, category: Path | None) -> None:
    """After a Usenet job was filed: nexbooks takes away what it knows of the job, never more.

    * The folder is the job's own only when its name is the release's, it lies strictly inside a download root, and it
      is neither the category folder of the program nor a root, nor in a library or the data folder, nor holds one. A
      program that reports anything else (the category folder itself, a folder of several jobs, a single file in it)
      shares the folder with others: nothing is taken away there, and the person gets a note under "Braucht dich".
    * In the job's own folder go the files the filing read (as they were) and the rests of a release (``JOB_RESTS``);
      anything else stays (a film, a text, anything nexbooks does not know), and so does every link. A folder goes only
      when it is empty then."""
    source = Path(row.source_path) if row.source_path else None
    if source is None:
        return
    folder = source if os.path.isdir(source) and not arrival.is_link(source) else source.parent
    real = arrival.resolved(folder)
    if real is None or arrival.is_link(folder) or arrival.in_data(real) or arrival.in_a_library(real) \
            or arrival.holds_a_library(real):
        return
    download_roots = arrival.download_roots()
    if not any(arrival.strictly_inside(real, root) for root in download_roots):
        return
    shared_folder = arrival.resolved(category) if category is not None else None
    if real == shared_folder or not named_like_job(real.name, row.title):
        _note_folder_kept(db, row)
        return
    sizes = {item.path: item.size for item in db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id,
                                                                                 PlaceFile.origin == "source"))}

    def known(relative: str, path: Path) -> bool:
        return (relative in sizes and _size_of(path) == sizes[relative]) or is_job_rest(path.name)

    removed, kept = clear_job_folder(real, known)
    logger.info("Download %d: %d files of the job went from its folder, %d stayed", row.id, removed, kept)


def clear_job_folder(real: Path, known: Callable[[str, Path], bool]) -> tuple[int, int]:
    """Takes the files ``known`` says are the job's out of its own folder (relative path, path), and the folders that
    are empty then. Returns how many went and how many stayed."""
    files: list[Path] = []
    folders: list[Path] = []
    kept = 0
    # Walked by hand: a link or a junction (``os.walk`` goes into a junction) is never gone into, and keeps its folder.
    for current, dirs, names in os.walk(real, followlinks=False):
        here = Path(current)
        inner = [name for name in dirs if not arrival.is_link(here / name)]
        kept += len(dirs) - len(inner)
        dirs[:] = inner
        folders.extend(here / name for name in inner)
        for name in names:
            path = here / name
            if arrival.is_link(path) or not path.is_file():
                kept += 1
            elif known(path.relative_to(real).as_posix(), path):
                files.append(path)
            else:
                kept += 1
    removed = 0
    for path in files:
        try:
            path.unlink()
            removed += 1
        except OSError:
            kept += 1
    for inner_folder in sorted(folders, key=lambda path: len(path.parts), reverse=True):
        try:
            inner_folder.rmdir()
        except OSError:
            pass
    try:
        real.rmdir()
    except OSError:
        pass
    return removed, kept


def _size_of(path: Path) -> int | None:
    try:
        return os.lstat(path).st_size
    except OSError:
        return None


def _note_folder_kept(db: Session, row: Download) -> None:
    """The program put the job into a folder it shares with others: nothing is taken away there. The log says so;
    nobody has anything to decide."""
    logger.warning("Download %d: the program put the job into a folder of several jobs; nothing was removed there",
                   row.id)


# --- The job ---------------------------------------------------------------------------------------------------------


def _problem(db: Session, row: Download, code: str, values: dict[str, Any]) -> None:
    db.rollback()
    fresh = db.get(Download, row.id)
    if fresh is None:
        return
    fresh.state, fresh.problem_code, fresh.problem_values = "problem", code, values
    if code in QUESTIONS:
        # (A question carries the generation of the rules that asked it: ``recheck`` asks again when they are newer.)
        fresh.check_rev = checking.CHECK_REV
    fresh.updated_at = clock.now()
    db.commit()
    logger.info("Download %d waits for a person code=%s", fresh.id, code)


def import_job(db: Session, job: Any) -> None:
    """The handler of the job ``pimport``: the filing of the download ``job.ref`` as far as it goes."""
    row = db.get(Download, job.ref)
    if row is None or row.state != "completed":
        return
    library = db.get(Library, row.library_id) if row.library_id is not None else None
    if library is None:
        _problem(db, row, "no_write", {"library_id": None, "reason": "library_gone"})
        return
    try:
        if row.place_step == "":
            check(db, row, library)
            db.refresh(row)
            if row.state != "completed":
                return
        if row.place_step == "checked":
            place(db, row, library)
            db.refresh(row)
            if row.state != "completed":
                return
        if row.place_step == "placed":
            file(db, row, library)
    except Stop as stop:
        _problem(db, row, stop.code, stop.values)


def settle(db: Session) -> None:
    """When a filing job is given up (it failed for good, a fault of nexbooks): its download is a problem a person can
    look at again ("Erneut prüfen"), never a job started over and over."""
    from ...models import Job

    waiting = select(Job.ref).where(Job.kind == KIND, Job.state.in_(("queued", "running")))
    failed = select(Job.ref).where(Job.kind == KIND, Job.state == "failed")
    moment = clock.now()
    for row in db.scalars(select(Download).where(Download.state == "completed", Download.id.in_(failed),
                                                 Download.id.not_in(waiting))):
        row.state, row.problem_code, row.problem_values = "problem", "place_failed", {"reason": "failed"}
        row.updated_at = moment
        logger.warning("Download %d could not be filed: the job was given up", row.id)
    db.commit()


jobs.register(KIND, import_job, settle)


# --- What a person decides -------------------------------------------------------------------------------------------


def go_on(db: Session, row: Download, decision: dict[str, Any] | None = None) -> None:
    """The download goes on: with what a person said it is checked again from the files it has (they are not read a
    second time); without, it goes on from the step it stopped at (the library takes files now, there is room). Does
    not commit."""
    if decision:
        kept = {key: value for key, value in (row.decision or {}).items()
                if not key.startswith("_") or key == "_category"}
        row.decision = {**kept, **decision}
        row.place_step = ""
    elif row.place_step == "checked" and (row.problem_values or {}).get("reason") == "taken":
        _forget_goals(db, row)
    row.state, row.problem_code, row.problem_values = "completed", "", None
    row.updated_at = clock.now()
    request(db, row)


def _forget_goals(db: Session, row: Download) -> None:
    """A goal that was taken in the meantime: the books of which no file lies in the library yet are planned anew (a
    book with a file placed keeps its goals; it goes on from there)."""
    items = list(db.scalars(select(PlaceFile).where(PlaceFile.download_id == row.id,
                                                    PlaceFile.state.in_(("take", "placed")))))
    started = {f"{item.origin}|{item.unit}" for item in items if item.state == "placed"}
    for item in items:
        if item.state == "take" and f"{item.origin}|{item.unit}" not in started:
            item.target = ""


def owner_of(db: Session, row: Download) -> Account | None:
    return db.get(Account, row.user_id) if row.user_id is not None else None


__all__ = ["KIND", "QUESTIONS", "Stop", "check", "file", "go_on", "import_job", "place", "request", "request_due",
           "turn_down", "unit_of_token"]
