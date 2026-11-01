"""What a person is shown of the hits: whether each stands on their shelf, its cover, whether it can be acquired, and
its page once it is opened.

**On the shelf** is decided by the books the person may see and by nothing else (``discover/owned.py``): the numbers the
sources gave the records (Audible's, Hardcover's, Open Library's), the ISBNs of single editions (a work of Open Library
lists hundreds, some of them wrong: they do not count), and the title in any of its languages with an author that fits
and the same place in a series.

**The page of a hit** (a book of the sources, ``discover/foreign.py``, with every title and form the work has, so that
fetching it looks for it in every language) is made when the person opens the hit or acquires it (``open_hit``), not
for every hit of every search (a search would make some twenty pages and ask for a dozen covers, most of them for
hits nobody looks at). A hit that has a page already (somebody opened it, wished it, a list named it)
shows that page's token, so that a wish for it says so.

**Covers** are asked for by the page that shows the hit (``cover_of``): only what is on the screen, as the browser loads
pictures. Each comes from its source's own host through the one way out, drawn anew, and is kept in memory for a day
(``KEEP_COVER_SECONDS``; covers are there to be shown).

**What can be acquired** from a hit: a book. A box, a play made of the book, a part of it, a study guide, a book about
it, a comic of it are named for what they are and have no buttons: fetching them would fetch the book, or nothing
(``why_not``). A book called like its series, whose place in it nobody knows, has none either (the whole, or any of the
volumes): the person picks the volume.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import ForeignBook
from ..discover import foreign, owned, pics
from ..oeuvre.store import _forms as forms_of_work
from ..readers.images import reduce_cover
from ..readers.limits import COVER_MAX_BYTES
from ..sources import http
from ..sources.model import Candidate, Entry
from .build import Hit

#: Covers kept in memory: how many, and for how long.
MAX_COVERS = 300
KEEP_COVER_SECONDS = 24 * 3600
#: Why a hit cannot be acquired: it is no book of its own, or the volume of its series is not known.
NOT_A_BOOK = "not_a_book"
VOLUME_UNKNOWN = "volume_unknown"


@dataclass
class Shown:
    hit: Hit
    #: The token of the page of the book, when it has one (None until it is opened).
    token: str | None
    owned_work_id: int | None
    cover_url: str | None
    #: Whether "E-Book", "Hörbuch" and "Beides" are offered, and why not.
    procurable: bool = True
    why_not: str = ""


def _speaker(hit: Hit) -> Entry:
    """The record the page of the hit is made from: the one whose cover the hit shows (the cover is fetched from that
    record's source), else the one that speaks for the work."""
    if hit.cover_url:
        for entry in hit.work.entries:
            if entry.cover_url == hit.cover_url:
                return entry
    return hit.work.speaker


def ref_of(hit: Hit) -> str:
    """The name of the page of a hit among the books of the sources (``foreign.ref_of``)."""
    speaker = _speaker(hit)
    return foreign.ref_of(Candidate(source=speaker.source, key=(speaker.work_key or speaker.key)[:70], title=""))


def why_not(hit: Hit) -> str:
    """Why a hit cannot be acquired (empty when it can): see the top."""
    if hit.label != "work":
        return NOT_A_BOOK
    if hit.series_named:
        return VOLUME_UNKNOWN
    return ""


def row_of(db: Session, hit: Hit) -> ForeignBook:
    """The page of a hit, made or brought up to date. Does not commit. It carries the series and the volume of the work
    (a wish made from it looks for that volume), and what the hit is when it cannot be acquired (``search_label``,
    ``search_why_not``: a wish for it is refused)."""
    work = hit.work
    speaker = _speaker(hit)
    subjects: list[str] = []
    for entry in work.entries:
        for name in ("genres", "keywords", "subjects"):
            value = entry.signals.get(name)
            if isinstance(value, list):
                subjects += [item for item in value if isinstance(item, str)]
    edition = work.titles.get(hit.language) if hit.language else work.titles.get(work.orig_language)
    candidate = Candidate(
        source=speaker.source, key=(speaker.work_key or speaker.key)[:70], title=hit.title,
        authors=hit.authors[:6], languages=[hit.language or work.orig_language] if (hit.language or work.orig_language)
        else [], isbns=[isbn for isbn in work.isbns if isbn.isdigit()][:20], year=work.year,
        released=edition.released if edition else "", series=work.series, series_pos=work.series_pos,
        subjects=list(dict.fromkeys(subjects))[:10], pages=work.pages, cover_url=speaker.cover_url)
    row = foreign.upsert(db, candidate, "ebook")
    data: dict[str, Any] = dict(row.data) if isinstance(row.data, dict) else {}
    # (The titles a wish looks for: in the languages fetched in and the one the book was written in; a Korean edition of
    # a German classic only makes the search at the indexers longer.)
    kept = set(hit.fetched) | {work.orig_language, hit.author_language}
    data["titles"] = [{"text": item.title, "language": language} for language, item in work.titles.items()
                      if language != "und" and (language in kept or not hit.fetched)][:12]
    data["forms"] = foreign.merge_forms([], forms_of_work(work))
    reason = why_not(hit)
    if reason:
        data["search_label"], data["search_why_not"] = hit.label, reason
    else:
        data.pop("search_label", None)
        data.pop("search_why_not", None)
    row.data = data
    return row


def owned_of(shelf: owned.Shelf, hit: Hit) -> int | None:
    """The work of the person's shelf the hit is (see the top), or None."""
    work = hit.work
    single = [entry for entry in work.entries if not entry.signals.get("work_level")]
    isbns = list(dict.fromkeys(isbn for entry in single for isbn in entry.isbns if isbn.isdigit()))
    refs = list(dict.fromkeys((entry.source, entry.work_key or entry.key) for entry in work.entries))
    asins = list(dict.fromkeys(entry.asin for entry in work.entries if entry.asin))
    titles = list(dict.fromkeys([hit.title, *(item.title for item in work.titles.values())]))
    for index, title in enumerate(titles):
        found = owned.find(shelf, title=title, authors=hit.authors, isbns=isbns if index == 0 else None,
                           asin=asins[0] if asins and index == 0 else "", refs=refs if index == 0 else None,
                           position=work.series_pos)
        if found is not None:
            return found
    for asin in asins[1:]:
        found = owned.find(shelf, title="", authors=[], asin=asin)
        if found is not None:
            return found
    return None


def present(db: Session, uid: int, hits: list[Hit], ticket: str) -> list[Shown]:
    """The hits as the person is shown them. Reads only: no page is made, no cover is fetched (see the top)."""
    shelf = owned.load(db, uid)
    refs = {id(hit): ref_of(hit) for hit in hits}
    rows = {row.ref: row for row in db.scalars(select(ForeignBook).where(ForeignBook.ref.in_(set(refs.values()))))} \
        if refs else {}
    shown: list[Shown] = []
    for hit in hits:
        row = rows.get(refs[id(hit)])
        if row is not None and row.cover:
            cover: str | None = f"/api/v1/discover/books/{row.token}/cover?v={row.cover[:12]}"
        elif hit.cover_url and http.host_allowed(_speaker(hit).source, hit.cover_url):
            cover = f"/api/v1/search/catalog/{ticket}/covers/{hit.key}"
        else:
            cover = None
        reason = why_not(hit)
        shown.append(Shown(hit=hit, token=row.token if row is not None else None, owned_work_id=owned_of(shelf, hit),
                           cover_url=cover, procurable=not reason, why_not=reason))
    return shown


# ---- Covers ----------------------------------------------------------------------------------------------------

_covers: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()
_covers_lock = threading.Lock()


def _kept(url: str) -> tuple[bytes, str] | None:
    with _covers_lock:
        found = _covers.get(url)
        if found is None:
            return None
        if clock.monotonic() - found[0] > KEEP_COVER_SECONDS:
            _covers.pop(url, None)
            return None
        _covers.move_to_end(url)
        return found[1], found[2]


def cover_of(db: Session, hit: Hit) -> tuple[bytes, str] | None:
    """The cover of a hit as WebP with its main colour, from memory or from the source's own host (a person's request:
    it waits a moment for its turn at most). None when it has none, the source may not be asked, or it is no picture."""
    speaker = _speaker(hit)
    url = hit.cover_url
    if not url or not http.host_allowed(speaker.source, url):
        return None
    found = _kept(url)
    if found is not None:
        return found
    try:
        answer = http.request(db, speaker.source, url, headers={"Accept": "image/*"}, limit=COVER_MAX_BYTES,
                              interactive=True)
    except http.SourceError:
        return None
    made = reduce_cover(answer.content) if answer.status_code == 200 else None
    if made is None:
        return None
    with _covers_lock:
        _covers[url] = (clock.monotonic(), made[0], made[1])
        while len(_covers) > MAX_COVERS:
            _covers.popitem(last=False)
    return made


def open_hit(db: Session, hit: Hit) -> ForeignBook:
    """The page of a hit, made now (the person opens it or acquires it); its cover from memory when the page showed it,
    else asked for in the background. Commits."""
    row = row_of(db, hit)
    if not row.cover:
        found = _kept(hit.cover_url) if hit.cover_url else None
        if found is not None:
            row.cover, row.color = pics.save(found[0]), found[1]
            row.data = {**(row.data if isinstance(row.data, dict) else {}), "cover_at": clock.now().isoformat()}
        else:
            foreign.enqueue_covers(db, [row])
    db.commit()
    return row


def forget_covers() -> None:
    """Every kept cover forgotten (the tests)."""
    with _covers_lock:
        _covers.clear()


__all__ = ["NOT_A_BOOK", "VOLUME_UNKNOWN", "Shown", "cover_of", "forget_covers", "open_hit", "owned_of", "present",
           "ref_of", "row_of", "why_not"]
