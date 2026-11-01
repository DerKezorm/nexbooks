"""What nexbooks learns about an author from the internet: a photo and a short biography.

**In the background, politely, and only what is certain.** A job ``pinfo`` looks up one author (``ref`` is the
person) at *Wikidata and Wikimedia Commons* (``sources/wikidata.py``): the item of the author, proven by a book of
theirs that nexbooks holds, and a picture under a free licence with its author and licence kept. No other source
gives a photo: a picture without an author and a licence is not kept.

What goes out is the name of the author and the titles of some of their books, never a file. A source that is
switched off (its own switch, the main switch of the sources, or the switch of the authors' photos) is not
asked, and nothing is marked as looked at then: the author is tried when the source is on. An author the source is
not sure of has no photo; the interface draws a circle with their initials.

The photos that lie in the authors' folders are read by ``services/portraits.py`` and win over these for the
people who may see that library.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import AUTHOR, Credit, Edition, Person, PersonInfo, Work
from . import jobs, photos, settings_service
from .readers.images import reduce_portrait
from .sources import config, http, wikidata

logger = logging.getLogger("nexbooks.people")

KIND = "pinfo"
#: The sources a photo is kept from: the ones that name its author and its licence.
PHOTO_SOURCES = ("wikidata",)
#: How long an author who was looked up is left alone: a sure one with a photo for long, one without a photo sooner (a
#: source may know them tomorrow).
RECHECK_WITH_PHOTO = timedelta(days=180)
RECHECK_WITHOUT = timedelta(days=30)
#: Authors queued for a look-up by one visit of a page.
QUEUE_PER_VISIT = 40
#: The most titles of an author's books that go out.
MAX_TITLES = 12


def enabled(db: Session) -> bool:
    """Whether authors are looked up at all, and some source that does it may be asked now."""
    if not settings_service.get(db, "author_info"):
        return False
    return bool(config.ready_sources(db, ("wikidata",)))


def evidence(db: Session, person_id: int) -> tuple[list[str], str]:
    """The titles of the books nexbooks holds of an author (their own and the original titles), the most-used first,
    and the
    language most of their books are in. Public data: it does not depend on who asks."""
    rows = db.execute(
        select(Edition.title, Edition.original_title, Edition.language, Work.title, Work.original_title)
        .join(Credit, Credit.edition_id == Edition.id).join(Work, Work.id == Edition.work_id)
        .where(Credit.person_id == person_id, Credit.role == AUTHOR).limit(200)).all()
    titles: dict[str, int] = {}
    languages: dict[str, int] = {}
    for edition_title, edition_original, language, work_title, work_original in rows:
        for title in (edition_original, work_original, edition_title, work_title):
            if title and title.strip():
                titles[title.strip()] = titles.get(title.strip(), 0) + 1
        if language:
            languages[language] = languages.get(language, 0) + 1
    ordered = [title for title, _n in sorted(titles.items(), key=lambda item: (-item[1], len(item[0])))]
    language = max(languages, key=lambda code: languages[code]) if languages else ""
    return ordered[:MAX_TITLES], language


def enqueue_missing(db: Session, person_ids: list[int]) -> int:
    """A job for each author that was not looked up yet or whose
    look-up is old (at most ``QUEUE_PER_VISIT``). Nothing is
    queued while no source may be asked. Returns how many were queued."""
    if not person_ids or not enabled(db):
        return 0
    now = clock.now()
    known = {row.person_id: row for row in db.scalars(select(PersonInfo).where(PersonInfo.person_id.in_(person_ids)))}
    queued = 0
    for person_id in person_ids:
        info = known.get(person_id)
        if info is not None and info.checked_at is not None:
            wait = RECHECK_WITH_PHOTO if info.photo else RECHECK_WITHOUT
            if now - info.checked_at < wait:
                continue
        jobs.enqueue(db, KIND, ref=person_id)
        queued += 1
        if queued >= QUEUE_PER_VISIT:
            break
    db.commit()
    return queued


def _info(db: Session, person_id: int) -> PersonInfo:
    info = db.get(PersonInfo, person_id)
    if info is None:
        info = PersonInfo(person_id=person_id)
        db.add(info)
    return info


def _bios(identity: wikidata.Identity | None) -> dict[str, Any]:
    """``{"life": {born, died}, "texts": {language: {text, source, url}}}``: the descriptions of Wikidata (short, one
    per language)."""
    texts: dict[str, dict[str, str]] = {}
    life: dict[str, int | None] = {}
    if identity is not None:
        for language, text in identity.descriptions.items():
            texts[language] = {"text": text, "source": "wikidata", "url": f"https://www.wikidata.org/wiki/{identity.qid}"}
        life = {"born": identity.born, "died": identity.died}
    return {"life": life, "texts": texts}


#: What ``asked`` answers when a source could not be asked or did not answer (None is an answer: "nobody found").
_UNKNOWN: Any = object()


def _asked(function: Any) -> Any:
    """The answer of one question to a source; ``_UNKNOWN`` when the source may not be asked now or does not answer. A
    source that is busy, slow or down makes the job try again later; the server stopping stops the job."""
    try:
        return function()
    except http.SourceOff:
        return _UNKNOWN
    except http.SourceError as exc:
        if jobs.stopping.is_set():
            raise jobs.Interrupted from exc
        if exc.code in ("rate_limited", "timeout", "unreachable", "server", "busy", "daily_limit"):
            raise jobs.Retry(exc.code) from exc
        logger.info("A source gave no answer about an author code=%s", exc.code)
        return _UNKNOWN


def look_up(db: Session, person_id: int) -> None:
    """Looks up one author at the sources (see the top of this
    module) and keeps what is found. Nothing is marked as looked
    at unless a source really answered."""
    person = db.get(Person, person_id)
    if person is None or not enabled(db):
        return
    titles, language = evidence(db, person_id)
    if not titles:
        return
    info = _info(db, person_id)
    identity: wikidata.Identity | None = None
    photo_bytes: bytes | None = None
    credit: dict[str, str] = {}
    source = ""
    state = info.wikidata_state
    answered = False

    if config.availability(db, "wikidata").ready:
        found = _asked(lambda: wikidata.identify(db, person.name, titles, language))
        if found is not _UNKNOWN:
            answered = True
            identity = found
            state = "sure" if identity else "none"
        if identity and identity.image:
            picture = _asked(lambda: wikidata.picture_of(db, identity.image if identity else ""))
            if picture is not _UNKNOWN and picture is not None:
                raw = _asked(lambda: wikidata.fetch_picture(db, picture.thumb_url))
                made = reduce_portrait(raw) if isinstance(raw, bytes) else None
                if made:
                    photo_bytes, source = made, "wikidata"
                    credit = {"author": picture.author, "license": picture.license,
                              "license_url": picture.license_url, "page": picture.page_url}
    if not answered:
        # No source could be asked: nothing is marked, the author is tried again at a later visit.
        db.rollback()
        return
    info.wikidata_id = identity.qid if identity else ""
    info.wikidata_state = state
    bios = _bios(identity)
    info.bios = bios if bios["texts"] else None
    if photo_bytes is not None:
        info.photo = photos.save(photo_bytes)
        info.photo_source = source
        info.photo_author = credit.get("author", "")[:200]
        info.photo_license = credit.get("license", "")[:80]
        info.photo_license_url = credit.get("license_url", "")[:300]
        info.photo_page = credit.get("page", "")[:300]
    info.checked_at = clock.now()
    info.name_key = person.key
    db.commit()
    sweep(db)


def drop_unlicensed(db: Session) -> int:
    """Removes what earlier versions kept from sources that name neither the author of a photo nor its licence (Open
    Library, Hardcover): the photo, its file and the text of those sources. The authors are looked up again at a
    visit of a page, and only Wikimedia Commons can give them a photo now. Commits. Returns how many authors lost
    a photo."""
    dropped = 0
    for info in db.scalars(select(PersonInfo)):
        changed = False
        if info.photo and info.photo_source not in PHOTO_SOURCES:
            info.photo, info.photo_source = "", ""
            info.photo_author = info.photo_license = info.photo_license_url = info.photo_page = ""
            dropped += 1
            changed = True
        texts = info.bios.get("texts") if isinstance(info.bios, dict) else None
        if isinstance(texts, dict):
            kept = {key: item for key, item in texts.items()
                    if isinstance(item, dict) and item.get("source") == "wikidata"}
            if len(kept) != len(texts):
                info.bios = {**info.bios, "texts": kept} if kept else None
                changed = True
        if changed:
            info.checked_at = None
    db.commit()
    if dropped:
        sweep(db)
        logger.info("Photos of authors from sources without a licence were removed count=%d", dropped)
    return dropped


def sweep(db: Session) -> None:
    """Removes the photos nothing refers to any more (the authors of the libraries', their folders' and the authors' of
    the catalogue)."""
    from ..models import CatalogPerson, PersonPhoto

    live = set(db.scalars(select(PersonInfo.photo).where(PersonInfo.photo != "")))
    live |= set(db.scalars(select(PersonPhoto.photo)))
    for info in db.scalars(select(CatalogPerson.info).where(CatalogPerson.info.is_not(None))):
        if isinstance(info, dict) and isinstance(info.get("photo"), str):
            live.add(info["photo"])
    photos.sweep(live)


def _proven(info: PersonInfo | None) -> bool:
    """Whether a look-up found the person for sure: Wikidata proved them by a book, or a source gave a photo (which it
    does only for an author one of whose works nexbooks holds)."""
    return info is not None and (info.wikidata_state == "sure" or bool(info.photo))


def take_over(db: Session, keep_id: int, drop_id: int) -> None:
    """Two people turned out to be one (a pen name, two spellings): what was learned about the one that goes is kept for
    the one that stays, never mixed. The photo and the text of the person that stays win when that person was found for
    sure; else the ones of the other, when that one was. Otherwise the person that stays is looked up again, with the
    books of both names as the proof. The photos from the authors' folders go along for the libraries the person that
    stays has none in. Call before the person that goes is deleted; the caller commits."""
    from ..models import PersonPhoto

    if keep_id == drop_id:
        return
    keep, drop = db.get(PersonInfo, keep_id), db.get(PersonInfo, drop_id)
    if drop is not None:
        if not _proven(keep) and _proven(drop):
            if keep is not None:
                db.delete(keep)
                db.flush()
            drop.person_id = keep_id
        else:
            db.delete(drop)
            if keep is not None and not _proven(keep):
                keep.checked_at = None
        db.flush()
    held = set(db.scalars(select(PersonPhoto.library_id).where(PersonPhoto.person_id == keep_id)))
    for row in list(db.scalars(select(PersonPhoto).where(PersonPhoto.person_id == drop_id))):
        if row.library_id in held:
            db.delete(row)
        else:
            row.person_id = keep_id
    db.flush()


def keep_own(db: Session, person_ids: set[int]) -> None:
    """People who lost another name (a split by hand, evidence that is gone): what was found for a name that is not
    theirs any more goes, so that each of the two has their own photo and text or none, never the other's. The photo
    and the text go when they were found for another name (``name_key``); a photo from the folder of another name goes,
    and the folders of those libraries are read again (the photo finds its own person). Who has nothing is looked up
    again at the next visit of a page. The caller commits."""
    from ..models import PersonPhoto
    from .readers.names import person_key

    libraries: set[int] = set()
    for person_id in sorted(person_ids):
        person = db.get(Person, person_id)
        if person is None:
            continue
        info = db.get(PersonInfo, person_id)
        if info is not None and info.name_key != person.key:
            db.delete(info)
        for row in list(db.scalars(select(PersonPhoto).where(PersonPhoto.person_id == person_id))):
            if person_key(row.path.split("/", 1)[0])[:200] != person.key:
                libraries.add(row.library_id)
                db.delete(row)
    db.flush()
    for library_id in sorted(libraries):
        jobs.enqueue(db, "portraits", ref=library_id)


def info_job(db: Session, job: Any) -> None:
    """The handler of the job ``pinfo``: the author ``job.ref``."""
    look_up(db, job.ref)


jobs.register(KIND, info_job)

