"""A photo and a short text for an author nobody of the libraries is: from Wikidata
and Wikimedia Commons, as for the authors of the libraries (``services/people_info.py``), but proven by the catalogue.

**Never the photo of a wrong person.** An author of the libraries is proven by a book on a shelf; an author of the
catalogue only by what the sources say. So the item of Wikidata must have **at least two works that are works of the
catalogue** (``wikidata.identify(..., least=2)``), the sure ones known to two sources, under their titles in the
author's language first; one title, however much it says, proves nothing. Otherwise there is no photo and no text,
and the page draws the initials.

A job ``cinfo`` (``ref`` is the author of the catalogue) runs in the background after a build, at most once in
``RECHECK_WITHOUT`` (``RECHECK_WITH_PHOTO`` when a photo was found), only while the photos of authors are switched on
and Wikidata may be asked. What goes out is the name and some titles, as for the authors of the libraries.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import CatalogPerson, CatalogWork
from .. import jobs, people_info, photos
from ..readers.images import reduce_portrait
from ..sources import http, wikidata

logger = logging.getLogger("nexbooks.people")

KIND = "cinfo"
#: Works of the catalogue that must be works of the item.
LEAST_WORKS = 2
RECHECK_WITH_PHOTO = people_info.RECHECK_WITH_PHOTO
RECHECK_WITHOUT = people_info.RECHECK_WITHOUT
MAX_TITLES = people_info.MAX_TITLES


def evidence(db: Session, row: CatalogPerson) -> tuple[list[str], str]:
    """The titles of the sure works of the catalogue that two sources know (the original title first, then the others),
    and the author's language."""
    titles: list[str] = []
    for work in db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id,
                                                     CatalogWork.kind != "unsure").order_by(CatalogWork.weight.desc())):
        if len(work.sources or []) < 2 or not isinstance(work.titles, dict):
            continue
        ordered = sorted(work.titles.items(), key=lambda item: item[0] != work.orig_language)
        for _language, item in ordered:
            title = item.get("title") if isinstance(item, dict) else None
            if isinstance(title, str) and title.strip():
                titles.append(title.strip())
    return list(dict.fromkeys(titles))[:MAX_TITLES], row.orig_language or ""


def due(row: CatalogPerson) -> bool:
    info = row.info if isinstance(row.info, dict) else {}
    checked = info.get("checked_at")
    try:
        moment = datetime.fromisoformat(checked) if isinstance(checked, str) else None
    except ValueError:
        moment = None
    if moment is None:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return clock.now() - moment >= (RECHECK_WITH_PHOTO if info.get("photo") else RECHECK_WITHOUT)


def enqueue(db: Session, row: CatalogPerson) -> bool:
    """A look-up for an author of the catalogue nobody of the libraries is, when it is due and may be made. The caller
    commits."""
    if row.person_id is not None or not people_info.enabled(db) or not due(row):
        return False
    jobs.enqueue(db, KIND, ref=row.id)
    return True


def look_up(db: Session, ident: int) -> None:
    """Looks the author up at Wikidata (see the top) and keeps what is found. Commits."""
    row = db.get(CatalogPerson, ident)
    if row is None or row.person_id is not None or not people_info.enabled(db):
        return
    titles, language = evidence(db, row)
    if len(titles) < LEAST_WORKS:
        return
    found: Any = _ask(lambda: wikidata.identify(db, row.name, titles, language, least=LEAST_WORKS))
    if found is _UNKNOWN:
        db.rollback()
        return
    identity: wikidata.Identity | None = found
    info: dict[str, Any] = {"state": "sure" if identity else "none", "qid": identity.qid if identity else "",
                            "checked_at": clock.now().isoformat()}
    if identity is not None:
        info["bios"] = {"texts": {code: {"text": text, "source": "wikidata",
                                         "url": f"https://www.wikidata.org/wiki/{identity.qid}"}
                                  for code, text in identity.descriptions.items()},
                        "life": {"born": identity.born, "died": identity.died}}
        if identity.image:
            picture = _ask(lambda: wikidata.picture_of(db, identity.image))
            if picture is not _UNKNOWN and picture is not None:
                raw = _ask(lambda: wikidata.fetch_picture(db, picture.thumb_url))
                made = reduce_portrait(raw) if isinstance(raw, bytes) else None
                if made:
                    info["photo"] = photos.save(made)
                    info["credit"] = {"source": "wikidata", "author": picture.author[:200],
                                      "license": picture.license[:80], "license_url": picture.license_url[:300],
                                      "page": picture.page_url[:300]}
    row.info = info
    db.commit()
    people_info.sweep(db)


_UNKNOWN: Any = object()


def _ask(function: Any) -> Any:
    try:
        return function()
    except http.SourceOff:
        return _UNKNOWN
    except http.SourceError as exc:
        if jobs.stopping.is_set():
            raise jobs.Interrupted from exc
        if exc.code in ("rate_limited", "timeout", "unreachable", "server", "busy", "daily_limit"):
            raise jobs.Retry(exc.code) from exc
        logger.info("A source gave no answer about an author of the catalogue code=%s", exc.code)
        return _UNKNOWN


def photo_of(row: CatalogPerson) -> str | None:
    """The photo of the author (a file name), when one was found and its file is there."""
    info = row.info if isinstance(row.info, dict) else {}
    name = info.get("photo") if info.get("state") == "sure" else None
    return name if isinstance(name, str) and photos.path_of(name) is not None else None


def info_job(db: Session, job: Any) -> None:
    look_up(db, int(getattr(job, "ref", 0)))


jobs.register(KIND, info_job)

__all__ = ["KIND", "LEAST_WORKS", "enqueue", "evidence", "look_up", "photo_of"]
