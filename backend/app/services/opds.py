"""The catalogue for reading apps: OPDS 1.2 (Atom), read-only, e-books only (``routers/opds.py``).

**Everything is what the person sees.** The lists come from ``services/works.py`` and ``services/authors.py``, which
start from the libraries the person is a member of; a book in a library of somebody else is not listed, not counted and
not reachable. Audiobooks are not in the catalogue: a work with no e-book is left out, and so are its recordings.
Only files that are on the disk are offered.

**Nothing of the disk leaves.** An address names a work or a file by its number; a file is offered as
``/opds/files/<number>/<number>.<format>``. No folder, no file name and no path of the server is written into a feed,
and every text is cleaned of what XML cannot hold and escaped.

**Pages** hold ``PER_PAGE`` entries and link to the next and the previous one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import AUTHOR, EDITION_EBOOK, KIND_AUDIO, ROLE_BOOK, BookFile, Credit, Edition, Library
from . import authors as authors_service
from . import search
from . import works as works_service
from .libraries import member_library_ids
from .works import MEDIA_TYPES, Filters, WorkView

PER_PAGE = 50
#: Longest description written into a feed.
MAX_SUMMARY = 4000
#: The order a book's formats are offered in: the one most apps open first.
FORMAT_ORDER = ("epub", "azw3", "mobi", "pdf")
ATOM_TYPE = "application/atom+xml"
NAVIGATION = f"{ATOM_TYPE};profile=opds-catalog;kind=navigation"
ACQUISITION = f"{ATOM_TYPE};profile=opds-catalog;kind=acquisition"
SEARCH_TYPE = "application/opensearchdescription+xml"
REL_ACQUISITION = "http://opds-spec.org/acquisition"
REL_IMAGE = "http://opds-spec.org/image"
REL_THUMBNAIL = "http://opds-spec.org/image/thumbnail"
#: What XML 1.0 cannot hold: control characters other than tab, line feed and carriage return, and the surrogates and
#: the two non-characters at the end of the first plane.
_ILLEGAL = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def clean(text: object) -> str:
    return _ILLEGAL.sub("", str(text))


def _text(value: object) -> str:
    return escape(clean(value))


def _attr(value: object) -> str:
    return quoteattr(clean(value))


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Link:
    rel: str
    href: str
    type: str = ""
    title: str = ""
    length: int | None = None

    def xml(self) -> str:
        parts = [f"rel={_attr(self.rel)}", f"href={_attr(self.href)}"]
        if self.type:
            parts.append(f"type={_attr(self.type)}")
        if self.title:
            parts.append(f"title={_attr(self.title)}")
        if self.length is not None:
            parts.append(f"length={_attr(self.length)}")
        return "<link " + " ".join(parts) + "/>"


@dataclass
class Entry:
    id: str
    title: str
    updated: datetime
    links: list[Link] = field(default_factory=list)
    #: The text under a title in a list of folders ("12 books"), or the description of a book.
    summary: str = ""
    content: str = ""
    authors: list[tuple[str, str]] = field(default_factory=list)
    language: str = ""
    issued: str = ""
    category: str = ""

    def xml(self) -> str:
        out = ["<entry>", f"<title>{_text(self.title)}</title>", f"<id>{_text(self.id)}</id>",
               f"<updated>{_stamp(self.updated)}</updated>"]
        for name, href in self.authors:
            uri = f"<uri>{_text(href)}</uri>" if href else ""
            out.append(f"<author><name>{_text(name)}</name>{uri}</author>")
        if self.language:
            out.append(f"<dc:language>{_text(self.language)}</dc:language>")
        if self.issued:
            out.append(f"<dc:issued>{_text(self.issued)}</dc:issued>")
        if self.category:
            out.append(f"<category term={_attr(self.category)} label={_attr(self.category)}/>")
        if self.summary:
            out.append(f'<summary type="text">{_text(self.summary[:MAX_SUMMARY])}</summary>')
        if self.content:
            out.append(f'<content type="text">{_text(self.content)}</content>')
        out.extend(link.xml() for link in self.links)
        out.append("</entry>")
        return "".join(out)


@dataclass
class Feed:
    id: str
    title: str
    #: Where this page lies, as an address of the catalogue.
    href: str
    kind: str
    entries: list[Entry] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    #: The number of entries in all pages, for the paging elements; None: not counted.
    total: int | None = None
    page: int = 1

    def xml(self) -> bytes:
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/" '
                'xmlns:opds="http://opds-spec.org/2010/catalog" '
                'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'),
               f"<id>{_text(self.id)}</id>", f"<title>{_text(self.title)}</title>",
               f"<updated>{_stamp(clock.now())}</updated>", "<author><name>nexbooks</name></author>",
               Link("self", self.href, self.kind).xml()]
        out.extend(link.xml() for link in self.links)
        if self.total is not None:
            out.append(f"<opensearch:totalResults>{self.total}</opensearch:totalResults>")
            out.append(f"<opensearch:itemsPerPage>{PER_PAGE}</opensearch:itemsPerPage>")
            out.append(f"<opensearch:startIndex>{(self.page - 1) * PER_PAGE + 1}</opensearch:startIndex>")
        out.extend(entry.xml() for entry in self.entries)
        out.append("</feed>")
        return "".join(out).encode()


def search_description() -> bytes:
    """The OpenSearch description a reading app reads to learn how to search: one address with the words in it."""
    return ('<?xml version="1.0" encoding="UTF-8"?><OpenSearchDescription '
            'xmlns="http://a9.com/-/spec/opensearch/1.1/"><ShortName>nexbooks</ShortName>'
            "<Description>Search the e-books</Description><InputEncoding>UTF-8</InputEncoding>"
            f'<Url type={_attr(ACQUISITION)} template="/opds/search?q={{searchTerms}}"/>'
            "</OpenSearchDescription>").encode()


# --- Addresses ----------------------------------------------------------------------------------------------------


def paged(path: str, page: int, **query: object) -> str:
    """An address of the catalogue with its page (the first has none) and the other values of the query."""
    pairs = [(key, value) for key, value in query.items() if value not in (None, "")]
    if page > 1:
        pairs.append(("page", page))
    return path + ("?" + "&".join(f"{key}={quote(str(value), safe='')}" for key, value in pairs) if pairs else "")


def cover_href(work_id: int) -> str:
    return f"/opds/works/{work_id}/cover"


def file_href(file: BookFile) -> str:
    return f"/opds/files/{file.id}/{file.id}.{file.format}"


def _start_links(search: bool = True) -> list[Link]:
    links = [Link("start", "/opds", NAVIGATION)]
    if search:
        links.append(Link("search", "/opds/search.xml", SEARCH_TYPE, "nexbooks"))
        links.append(Link("search", "/opds/search?q={searchTerms}", ACQUISITION, "nexbooks"))
    return links


def _page_links(path: str, page: int, total: int, **query: object) -> list[Link]:
    links: list[Link] = []
    last = max(1, -(-total // PER_PAGE))
    if page > 1:
        links.append(Link("first", paged(path, 1, **query), ACQUISITION))
        links.append(Link("previous", paged(path, page - 1, **query), ACQUISITION))
    if page < last:
        links.append(Link("next", paged(path, page + 1, **query), ACQUISITION))
        links.append(Link("last", paged(path, last, **query), ACQUISITION))
    return links


# --- Books --------------------------------------------------------------------------------------------------------


def _format_rank(file: BookFile) -> tuple[int, int]:
    return (FORMAT_ORDER.index(file.format) if file.format in FORMAT_ORDER else len(FORMAT_ORDER), file.id)


def _entries(db: Session, uid: int, views: list[WorkView]) -> list[Entry]:
    """The books of ``views`` that have an e-book file on the disk, as entries; a work without one is left out."""
    ebook_ids = [item.edition.id for view in views for item in view.editions if item.edition.kind == EDITION_EBOOK]
    files: dict[int, list[BookFile]] = {}
    if ebook_ids:
        for row in db.scalars(select(BookFile).where(BookFile.edition_id.in_(ebook_ids), BookFile.role == ROLE_BOOK,
                                                     BookFile.missing_at.is_(None))):
            files.setdefault(row.edition_id or 0, []).append(row)
    found: list[Entry] = []
    for view in views:
        mine = [item for item in view.editions if item.edition.kind == EDITION_EBOOK]
        offered = sorted((row for item in mine for row in files.get(item.edition.id, [])), key=_format_rank)
        if not offered:
            continue
        links: list[Link] = []
        if view.cover:
            links.append(Link(REL_IMAGE, cover_href(view.work.id), "image/webp"))
            links.append(Link(REL_THUMBNAIL, cover_href(view.work.id), "image/webp"))
        for row in offered:
            links.append(Link(REL_ACQUISITION, file_href(row), MEDIA_TYPES.get(row.format, "application/octet-stream"),
                              row.format.upper(), row.size))
        if view.series_id:
            links.append(Link("related", f"/opds/series/{view.series_id}", ACQUISITION, view.series_name))
        language = next((item.edition.language for item in mine if item.edition.language), "")
        title = view.title + (f" ({view.series_name} {view.series_pos:g})" if view.series_id and view.series_pos
                              else "")
        found.append(Entry(
            id=f"urn:nexbooks:work:{view.work.id}", title=title, updated=view.added_at, links=links,
            summary=view.description, authors=[(name, f"/opds/authors/{ident}") for ident, name in view.authors],
            language=language, issued=str(view.year or ""), category=view.genre))
    return found


def _acquisition(db: Session, uid: int, *, id: str, title: str, path: str, views: list[WorkView], total: int,
                 page: int, up: tuple[str, str] | None = ("/opds", NAVIGATION), **query: object) -> Feed:
    links = _start_links() + ([Link("up", up[0], up[1])] if up else []) + _page_links(path, page, total, **query)
    return Feed(id=id, title=title, href=paged(path, page, **query), kind=ACQUISITION,
                entries=_entries(db, uid, views), links=links, total=total, page=page)


def _ebooks() -> Filters:
    return Filters(kind=EDITION_EBOOK)


def _slice(items: list, page: int) -> list:  # type: ignore[type-arg]
    return items[(page - 1) * PER_PAGE: page * PER_PAGE]


# --- The feeds ----------------------------------------------------------------------------------------------------


def root(db: Session, uid: int) -> Feed:
    """Where a reading app starts: the newest books, the authors, the series and one entry for each library."""
    now = clock.now()

    def section(name: str, title: str, text: str, kind: str = ACQUISITION) -> Entry:
        return Entry(id=f"urn:nexbooks:opds:{name}", title=title, updated=now, content=text,
                     links=[Link("subsection" if name != "new" else "http://opds-spec.org/sort/new",
                                 f"/opds/{name}", kind)])

    entries = [section("new", "New", "The newest books", ACQUISITION),
               section("authors", "Authors", "By author", NAVIGATION),
               section("series", "Series", "By series", NAVIGATION)]
    libraries = db.scalars(select(Library).where(Library.id.in_(member_library_ids(uid)),
                                                 Library.kind != KIND_AUDIO).order_by(Library.name, Library.id)).all()
    for library in libraries:
        entries.append(Entry(id=f"urn:nexbooks:library:{library.id}", title=library.name, updated=now,
                             content="All books of this library",
                             links=[Link("subsection", f"/opds/libraries/{library.id}", ACQUISITION)]))
    return Feed(id="urn:nexbooks:opds", title="nexbooks", href="/opds", kind=NAVIGATION, entries=entries,
                links=_start_links())


def new_books(db: Session, uid: int, page: int) -> Feed:
    found, total = works_service.list_works(db, uid, _ebooks(), "added", page, PER_PAGE)
    return _acquisition(db, uid, id="urn:nexbooks:opds:new", title="New", path="/opds/new", views=found, total=total,
                        page=page)


def library_books(db: Session, uid: int, library_id: int, page: int) -> Feed:
    """The e-books of one library, by title. A library the person is not a member of does not exist (404)."""
    from .libraries import require_readable

    library = require_readable(db, uid, library_id)
    found, total = works_service.list_works(db, uid, Filters(library_id=library.id, kind=EDITION_EBOOK), "title", page,
                                            PER_PAGE)
    return _acquisition(db, uid, id=f"urn:nexbooks:library:{library.id}", title=library.name,
                        path=f"/opds/libraries/{library.id}", views=found, total=total, page=page)


def authors(db: Session, uid: int, page: int) -> Feed:
    """The authors with at least one e-book the person sees, by last name, with the number of those books."""
    counts = dict(db.execute(
        select(Credit.person_id, func.count(distinct(Edition.work_id))).join(Edition, Edition.id == Credit.edition_id)
        .where(Credit.role == AUTHOR, Edition.kind == EDITION_EBOOK, *works_service.visible_edition_clause(uid))
        .group_by(Credit.person_id)).all())
    cards = authors_service.cards(db, uid)
    known = sorted((cards[ident].person for ident in counts if ident in cards),
                   key=lambda person: (person.sort_name.casefold(), person.id))
    now = clock.now()
    entries = [Entry(id=f"urn:nexbooks:author:{person.id}", title=person.sort_name or person.name, updated=now,
                     content=f"{counts[person.id]} book{'s' if counts[person.id] != 1 else ''}",
                     links=[Link("subsection", f"/opds/authors/{person.id}", ACQUISITION)])
               for person in _slice(known, page)]
    return Feed(id="urn:nexbooks:opds:authors", title="Authors", href=paged("/opds/authors", page), kind=NAVIGATION,
                entries=entries, links=_start_links() + [Link("up", "/opds", NAVIGATION)]
                + _page_links("/opds/authors", page, len(known)), total=len(known), page=page)


def author_books(db: Session, uid: int, person_id: int, page: int) -> Feed:
    """The e-books of an author the person sees. An author none of whose books the person sees is not there (404)."""
    person, wrote, _narrated = works_service.get_person(db, uid, person_id)
    mine = [view for view in wrote if not view.audio_only]
    return _acquisition(db, uid, id=f"urn:nexbooks:author:{person.id}", title=person.name,
                        path=f"/opds/authors/{person.id}", views=_slice(mine, page), total=len(mine), page=page,
                        up=("/opds/authors", NAVIGATION))


def series_list(db: Session, uid: int, page: int) -> Feed:
    found = works_service.list_series(db, uid, _ebooks())
    now = clock.now()
    entries = [Entry(id=f"urn:nexbooks:series:{series.id}", title=series.name, updated=now,
                     content=f"{count} book{'s' if count != 1 else ''}",
                     links=[Link("subsection", f"/opds/series/{series.id}", ACQUISITION)])
               for series, count in _slice(found, page)]
    return Feed(id="urn:nexbooks:opds:series", title="Series", href=paged("/opds/series", page), kind=NAVIGATION,
                entries=entries, links=_start_links() + [Link("up", "/opds", NAVIGATION)]
                + _page_links("/opds/series", page, len(found)), total=len(found), page=page)


def series_books(db: Session, uid: int, series_id: int, page: int) -> Feed:
    series, found = works_service.get_series(db, uid, series_id)
    mine = [view for view in found if not view.audio_only]
    return _acquisition(db, uid, id=f"urn:nexbooks:series:{series.id}", title=series.name,
                        path=f"/opds/series/{series.id}", views=_slice(mine, page), total=len(mine), page=page,
                        up=("/opds/series", NAVIGATION))


def search_results(db: Session, uid: int, query: str) -> Feed:
    """The e-books the words find (one page: the search holds at most its own limit of hits)."""
    found = [view for view in search.find(db, uid, query) if not view.audio_only] if query.strip() else []
    return Feed(id="urn:nexbooks:opds:search", title="Search", href=paged("/opds/search", 1, q=query.strip()[:200]),
                kind=ACQUISITION, entries=_entries(db, uid, found),
                links=_start_links() + [Link("up", "/opds", NAVIGATION)], total=len(found))

