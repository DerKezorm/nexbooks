"""Newznab and Torznab: what an indexer answers, read safely. XML only, parsed with defusedxml.

What the specs, the indexers of the operator and the measurements of the book search teach:

* **The key travels as the query parameter ``apikey``**, the one exception to the rule of headers: the specs and every
  indexer accept nothing else. The gate masks it in each log line; no answer body is ever logged (a feed holds download
  links with keys).
* **The error document** ``<error code description/>`` comes with HTTP 200 by the spec; Prowlarr and Jackett send it
  with 400, 401, 410 or 429. It is read at any status. Its description is never logged or shown: some indexers repeat
  the key in it.
* **Parsed without DTDs, entities or external references**; answers over 10 MB are refused by the gate.
* **Caps may be missing or broken**: then the defaults apply (a plain text search, page size 100, the book categories
  7020 and 3030).
* **The book search**: ``supportedParams`` of ``book-search`` (and of ``audio-search``) are kept as the indexer lists
  them. ``t=book`` is a trap (some indexers ignore the author and answer with a hundred books of the title word), so
  the search asks it only where the indexer lists ``author``, and a free text search elsewhere.
* **Categories come from the caps**, not from a guess: the indexers put the books of a language where they like (7120
  for German e-books, 3130 for German audiobooks at one of them). ``book_categories`` sorts what the caps list into
  e-books and audiobooks and suggests the ones that fit.
"""

from __future__ import annotations

import email.utils
import hashlib
import io
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.etree.ElementTree import Element, ParseError

import defusedxml
import defusedxml.ElementTree as SafeXml

logger = logging.getLogger("nexbooks.procure")

KINDS = ("newznab", "torznab")
NEWZNAB_NS = "http://www.newznab.com/dtd/2010/feeds/attributes"
TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
MAX_CATEGORIES = 1000
#: What is asked when the caps are missing or broken: Newznab's e-book and audiobook categories.
DEFAULT_CATEGORIES = (7020, 3030)
DEFAULT_CAPS: dict[str, Any] = {
    "search_params": ["q"],
    "book_search": False,
    "book_params": [],
    "audio_search": False,
    "audio_params": [],
    "limit_max": 100,
    "limit_default": 100,
    "categories": [],
}
LIMIT_TEXT = "request limit reached"
PAGE_LIMIT = 50


# --- Errors ------------------------------------------------------------------------------------------------------


class IndexerError(Exception):
    """An answer nexbooks cannot use. ``code`` is what the interface words (``indexer_key_rejected`` ...); ``status`` is
    the HTTP status the API answers with; ``retry_after`` (seconds) is set for a request limit."""

    def __init__(self, code: str, status: int = 502, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.values = values

    @property
    def retry_after(self) -> int | None:
        value = self.values.get("retry_after")
        return value if isinstance(value, int) else None


ERRORS = (
    (502, "indexer_key_rejected"), (429, "indexer_limit_reached"), (429, "indexer_daily_limit"),
    (409, "indexer_off"), (502, "indexer_error"), (502, "indexer_switched_off"), (502, "indexer_not_newznab"),
    (502, "indexer_wrong_kind"), (502, "indexer_unreachable"), (502, "indexer_http_error"),
    (502, "indexer_answer_too_large"), (502, "indexer_blocked"), (504, "indexer_timeout"),
)


# --- XML -----------------------------------------------------------------------------------------------------------


def local_name(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _namespace(tag: object) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[1:].split("}", 1)[0].strip().rstrip("/").lower()
    return ""


def _children(element: Element, name: str) -> list[Element]:
    return [child for child in element if local_name(child.tag) == name]


def _child(element: Element | None, name: str) -> Element | None:
    if element is None:
        return None
    return next((child for child in element if local_name(child.tag) == name), None)


def int_text(value: str | None) -> int | None:
    text = (value or "").strip()
    return int(text) if re.fullmatch(r"-?\d{1,18}", text) else None


def _text(value: str | None, limit: int = 200) -> str:
    return unicodedata.normalize("NFC", (value or "")).strip()[:limit]


def parse_xml(body: bytes, content_type: str = "") -> tuple[Element | None, set[str]]:
    """The root and the namespaces in use, or None for anything that is not safe, well-formed XML (a page of HTML, a
    DTD, an entity, an external reference)."""
    if "html" in content_type:
        return None, set()
    if not body.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return None, set()
    namespaces: set[str] = set()
    try:
        iterator = SafeXml.iterparse(io.BytesIO(body), events=("start-ns",), forbid_dtd=True, forbid_entities=True,
                                     forbid_external=True)
        for _event, value in iterator:
            namespaces.add(value[1].strip().rstrip("/").lower())
        root = iterator.root
    except (ParseError, defusedxml.DefusedXmlException, LookupError, ValueError, RecursionError) as exc:
        logger.info("Indexer answer is not usable XML: %s", type(exc).__name__)
        return None, set()
    if root is None or local_name(root.tag).lower() == "html":
        return None, set()
    return root, namespaces


def detect_kind(root: Element, namespaces: set[str]) -> str | None:
    """Torznab or Newznab, from the enclosure types first, then from the namespaces in use; None when it cannot be
    told."""
    types = [(enclosure.get("type") or "").lower() for enclosure in root.iter()
             if local_name(enclosure.tag) == "enclosure"]
    torrent = any("bittorrent" in value for value in types)
    nzb = any("x-nzb" in value for value in types)
    if torrent != nzb:
        return "torznab" if torrent else "newznab"
    used = namespaces | {_namespace(element.tag) for element in root.iter()}
    torznab, newznab = TORZNAB_NS in used, NEWZNAB_NS in used
    if torznab != newznab:
        return "torznab" if torznab else "newznab"
    return None


def _params(element: Element | None) -> list[str]:
    if element is None or element.get("supportedParams") is None:
        return []
    return [part.strip().lower()[:32] for part in (element.get("supportedParams") or "").split(",")
            if part.strip()][:32]


def _available(element: Element | None) -> bool:
    return element is not None and (element.get("available") or "").strip().lower() == "yes"


def parse_caps(root: Element) -> dict[str, Any] | None:
    """The caps as nexbooks keeps them, or None when the document is no caps document."""
    if local_name(root.tag) != "caps":
        return None
    searching = _child(root, "searching")
    search = _child(searching, "search")
    book = _child(searching, "book-search")
    # Torznab names it "audio-search" or "music-search" (Jackett writes both).
    audio = _child(searching, "audio-search")
    if audio is None:
        audio = _child(searching, "music-search")
    limits = _child(root, "limits")
    categories: list[dict[str, Any]] = []
    holder = _child(root, "categories")
    for category in _children(holder, "category") if holder is not None else []:
        category_id = int_text(category.get("id"))
        if category_id is None or len(categories) >= MAX_CATEGORIES:
            continue
        subcats = [{"id": sub_id, "name": _text(sub.get("name"))}
                   for sub in _children(category, "subcat")[:MAX_CATEGORIES]
                   if (sub_id := int_text(sub.get("id"))) is not None]
        categories.append({"id": category_id, "name": _text(category.get("name")), "subcats": subcats})
    limit_max = int_text(limits.get("max")) if limits is not None else None
    limit_default = int_text(limits.get("default")) if limits is not None else None
    return {
        "search_params": _params(search) or ["q"],
        "book_search": _available(book),
        "book_params": _params(book) if _available(book) else [],
        "audio_search": _available(audio),
        "audio_params": _params(audio) if _available(audio) else [],
        "limit_max": limit_max if limit_max is not None and limit_max > 0 else None,
        "limit_default": limit_default if limit_default is not None and limit_default > 0 else None,
        "categories": categories,
    }


# --- Book categories out of the caps -------------------------------------------------------------------------------

#: The languages an indexer names in its categories, by the words it uses (names and codes, as people write them).
LANGUAGE_WORDS: dict[str, tuple[str, ...]] = {
    "de": ("deutsch", "german", "allemand", "alemán", "aleman", "tedesco", "duits", "ger"),
    "en": ("english", "englisch", "anglais", "inglés", "ingles", "inglese", "engels", "eng"),
    "fr": ("french", "französisch", "franzoesisch", "francais", "français", "francés", "frances", "francese", "fra"),
    "es": ("spanish", "spanisch", "español", "espanol", "espagnol", "spagnolo", "spaans", "esp"),
    "it": ("italian", "italienisch", "italiano", "italien", "italiaans", "ita"),
    "nl": ("dutch", "niederländisch", "niederlaendisch", "nederlands", "néerlandais", "neerlandais", "holländisch"),
    "sv": ("swedish", "schwedisch", "svenska", "suédois", "suedois"),
    "no": ("norwegian", "norwegisch", "norsk", "norvégien"),
    "pt": ("portuguese", "portugiesisch", "português", "portugues", "portugais"),
    "pl": ("polish", "polnisch", "polski", "polonais"),
}
#: The languages the operator may give a category.
LANGUAGES = ("mixed", *LANGUAGE_WORDS)
#: What a book category is not: comics, magazines, technical books and the like are listed but not suggested.
_NOT_BOOKS = re.compile(r"comic|manga|magazin|mag\b|mags\b|zeitschrift|zeitung|newspaper|technical|fachbuch|"
                        r"textbook|educational|sheet|partitur|ebook\s*other|other", re.IGNORECASE)
_EBOOK_WORDS = re.compile(r"e-?book|epub|book|buch|bücher|buecher|livre|libro|libri|boek|bok|foreign",
                          re.IGNORECASE)
_AUDIO_WORDS = re.compile(r"audio-?book|hörbuch|hoerbuch|livre audio|audiolibro|luisterboek|ljudbok|lydbok|"
                          r"audiobook", re.IGNORECASE)


#: A name is cut into parts at these (a hyphen, a slash, a bar, a colon, a bracket ...): ``Ebook-DE``, ``Books/FR``,
#: ``Audiobooks (EN)``.
_PARTS = re.compile(r"[-/|:>,()\[\]{}–—]+")
_WORD = re.compile(r"[^\W\d_]+")


def _code_in(name: str) -> str | None:
    """The language a two-letter code in a category name states (``E-Book DE``, ``Hörbücher DE``, ``Audiobooks (EN)``).
    A code counts as a word of its own and only where it can be nothing else: the last word of a part of the name (a
    part is what stands between hyphens, slashes and brackets), a part by itself, or a word in capitals anywhere
    (``Ebook DE Krimi``). Never as a piece of a word, and never a ``de`` in the middle: that is the preposition of
    ``Livres de poche`` or ``Libros de bolsillo``. A name with two codes (``EN/DE Ebooks``) says none."""
    found: set[str] = set()
    for part in _PARTS.split(name):
        words = _WORD.findall(part)
        for position, word in enumerate(words):
            code = word.casefold()
            if code not in LANGUAGE_WORDS:
                continue
            if position == len(words) - 1 or word.isupper():
                found.add(code)
    return found.pop() if len(found) == 1 else None


def language_of(name: str) -> str:
    """The language a category name states (``de``), or ``mixed`` when it names none."""
    words = re.findall(r"[^\W\d_]+", name.casefold())
    for code, names in LANGUAGE_WORDS.items():
        if any(word in names for word in words):
            return code
    return _code_in(name) or "mixed"


def _is_ebook_category(category_id: int) -> bool:
    return 7000 <= category_id < 8000


def _is_audio_category(category_id: int, name: str) -> bool:
    if not 3000 <= category_id < 4000:
        return False
    # 3030 is Newznab's audiobook category; indexers repeat it per language at 3130, 3330 ...
    return category_id % 100 == 30 or bool(_AUDIO_WORDS.search(name))


def _inherited(category_id: int, name: str, parents: dict[int, int], names: dict[str, str] | dict[int, str]) -> str:
    """The language of a category: the one its name states, else the one of the category it stands under (``7120
    Ebook`` under ``7100 Books - DE`` is German), the one it is listed under first, then the one of its hundred."""
    own = language_of(name)
    if own != "mixed":
        return own
    for parent in (parents.get(category_id), category_id - category_id % 100):
        if parent is None or parent == category_id or parent % 1000 == 0 or parent not in names:
            continue
        found = language_of(str(names[parent]))
        if found != "mixed":
            return found
    return "mixed"


@dataclass(frozen=True)
class BookCategory:
    id: int
    name: str
    #: ``ebook`` or ``audio``.
    type: str
    #: ``de``, ``en`` ... or ``mixed``.
    lang: str
    #: Whether nexbooks suggests it (selected beforehand in the dialog).
    suggested: bool


def book_categories(caps: dict[str, Any] | None, prefer: tuple[str, ...] = ()) -> list[BookCategory]:
    """What the caps list for books: the categories from 7000 to 7999 and the audiobook ones of the 3000 series,
    sub-categories included, not the parent "Books". Each carries the language its name states. A category is suggested
    when it holds books (not comics, magazines, technical books) and its language is mixed or one of ``prefer``: the
    languages the operator fetches books in (settings, "Sprachen"). Without ``prefer`` every book category is."""
    found: dict[int, BookCategory] = {}
    candidates: list[tuple[int, str]] = []
    #: The category a sub-category is listed under, and the names of all of them.
    parents: dict[int, int] = {}
    names: dict[int, str] = {}
    for category in (caps or {}).get("categories") or []:
        candidates.append((category["id"], category["name"]))
        names[category["id"]] = category["name"]
        for sub in category.get("subcats") or []:
            candidates.append((sub["id"], sub["name"]))
            names[sub["id"]] = sub["name"]
            parents[sub["id"]] = category["id"]
    for category_id, name in candidates:
        # The parents ("Books" 7000, "Audio" 3000) hold everything of their series: not a category to ask.
        if category_id in found or category_id % 1000 == 0:
            continue
        if _is_ebook_category(category_id):
            kind = "ebook"
        elif _is_audio_category(category_id, name):
            kind = "audio"
        else:
            continue
        books = kind == "audio" or (not _NOT_BOOKS.search(name)
                                    and bool(_EBOOK_WORDS.search(name) or category_id % 100 == 20))
        found[category_id] = BookCategory(category_id, name, kind, _inherited(category_id, name, parents, names),
                                          books)
    ordered = sorted(found.values(), key=lambda item: (item.type, item.id))
    wanted = set(prefer)
    chosen: list[BookCategory] = []
    for kind in ("ebook", "audio"):
        group = [item for item in ordered if item.type == kind]
        suggested = [item for item in group if item.suggested and (item.lang == "mixed" or item.lang in wanted)]
        if not suggested and not wanted:
            # Nobody said which languages are fetched: the books of every language the indexer has. With languages
            # given, a category of another language is never suggested (it is never asked either, ``search.plan_for``).
            suggested = [item for item in group if item.suggested]
        names = {item.id for item in suggested}
        chosen.extend(BookCategory(item.id, item.name, item.type, item.lang, item.id in names) for item in group)
    return chosen


def asked_language(lang: str, allowed: tuple[str, ...] | list[str] | set[str]) -> bool:
    """Whether a category of language ``lang`` may be asked at all: a mixed one always, one of a language only when
    books are fetched in that language."""
    return lang == "mixed" or lang in allowed


def only_deviations(caps: dict[str, Any] | None, langs: dict[str, str]) -> dict[str, str]:
    """The languages of the categories that are worth saving: those the operator set apart from what the caps state (a
    category whose language comes from its name, or from the name of the one it stands under, needs nothing saved, and
    follows the caps when they are read again). Without caps that list categories every language is kept."""
    stated = {item.id: item.lang for item in book_categories(caps)}
    if not stated:
        return dict(langs)
    return {key: value for key, value in langs.items() if not (key.isdigit() and stated.get(int(key)) == value)}


def legacy_langs(caps: dict[str, Any] | None, categories: list[int]) -> dict[str, str]:
    """The languages versions before the language of a parent counted saved for ``categories``: each by its own name
    only (``7120 Ebook`` under ``Books - DE`` was ``mixed``)."""
    names = {}
    for category in (caps or {}).get("categories") or []:
        names[category["id"]] = category["name"]
        for sub in category.get("subcats") or []:
            names[sub["id"]] = sub["name"]
    return {str(number): language_of(names.get(number, "")) for number in categories}


def default_categories(caps: dict[str, Any] | None, prefer: tuple[str, ...] = ()) -> list[int]:
    """The ids ``book_categories`` suggests, or Newznab's own two when the caps list none."""
    suggested = [item.id for item in book_categories(caps, prefer) if item.suggested]
    return suggested or list(DEFAULT_CATEGORIES)


# --- Feeds -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiLimits:
    """``newznab:apilimits`` of a feed: requests and grabs used and allowed in 24 hours, and when each counter goes
    down. Every value may be missing."""

    api_current: int | None = None
    api_max: int | None = None
    grab_current: int | None = None
    grab_max: int | None = None
    api_next_at: str | None = None
    grab_next_at: str | None = None


@dataclass(frozen=True)
class Item:
    """One release of a feed. ⚠️ ``link`` carries the key or a passkey: in memory only, never in a repr, an answer or a
    log line; it is sealed before it is kept."""

    title: str
    size: int | None
    published_at: datetime | None
    categories: tuple[int, ...]
    seeders: int | None = None
    peers: int | None = None
    grabs: int | None = None
    #: A key of the item's guid (a hash: a guid is often a link, and no link is kept in the clear).
    guid: str = ""
    link: str = field(default="", repr=False, compare=False)
    #: Torznab's ``infohash``, lower case.
    info_hash: str = ""
    #: The seed goal the tracker asks for (Torznab ``minimumratio``, ``minimumseedtime`` in seconds).
    min_ratio: float | None = None
    min_seed_seconds: int | None = None
    #: What some indexers say of the book with ``extended=1`` (the attributes ``author`` and ``booktitle``): a voice
    #: beside the name, empty when the indexer says nothing.
    author: str = ""
    book_title: str = ""


@dataclass(frozen=True)
class Feed:
    #: The kind the feed looks like, None when it cannot be told.
    kind: str | None
    #: ``<item>`` elements, with or without a usable title.
    items: int
    limits: ApiLimits | None = None
    releases: tuple[Item, ...] = ()
    #: What ``newznab:response total`` says there is in all; None without it.
    total: int | None = None


def api_limits(holder: Element) -> ApiLimits | None:
    """The ``apilimits`` element directly below ``holder``, attribute names in any case; None without a usable one."""
    element = next((child for child in holder if local_name(child.tag).lower() == "apilimits"), None)
    if element is None:
        return None
    values = {local_name(name).lower(): (value or "").strip() for name, value in element.attrib.items()}

    def number(name: str) -> int | None:
        found = int_text(values.get(name))
        return found if found is not None and found >= 0 else None

    limits = ApiLimits(api_current=number("apicurrent"), api_max=number("apimax"),
                       grab_current=number("grabcurrent"), grab_max=number("grabmax"),
                       api_next_at=values.get("apinextavailable") or None,
                       grab_next_at=values.get("grabnextavailable") or None)
    return None if limits == ApiLimits() else limits


#: Items read of one feed at most: a page is 100, an indexer that answers with more is cut.
MAX_ITEMS = 500
TITLE_MAX = 400
_LINK_SCHEMES = ("http://", "https://", "magnet:")
_INFO_HASH = re.compile(r"[0-9a-fA-F]{40}")
_TRAILING_ZONE = re.compile(r"\s+[A-Z]{2,5}$")


def parse_date(value: str | None) -> datetime | None:
    """An RFC 2822 date (``pubDate``, ``usenetdate``) or ISO 8601 as UTC; None for anything else."""
    text = (value or "").strip()[:80]
    if not text:
        return None
    for candidate in (text, _TRAILING_ZONE.sub("", text)):
        try:
            parsed = email.utils.parsedate_to_datetime(candidate)
        except (TypeError, ValueError, IndexError):
            continue
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _attributes(item: Element) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for child in item:
        if local_name(child.tag) == "attr" and child.get("name"):
            values = found.setdefault((child.get("name") or "").strip().lower()[:40], [])
            if len(values) < 50:
                values.append((child.get("value") or "").strip()[:500])
    return found


def _first_int(values: list[str] | None) -> int | None:
    for value in values or []:
        number = int_text(value)
        if number is not None:
            return number
    return None


def _first_float(values: list[str] | None) -> float | None:
    for value in values or []:
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number) and number >= 0:
            return number
    return None


def _child_text(element: Element, name: str) -> str:
    found = _child(element, name)
    return (found.text or "").strip() if found is not None else ""


def _link(item: Element, attributes: dict[str, list[str]]) -> str:
    """The enclosure's address, else ``link``, else Torznab's ``magneturl``: only http, https and magnet links."""
    enclosure = _child(item, "enclosure")
    candidates = [enclosure.get("url") if enclosure is not None else None, _child_text(item, "link"),
                  *(attributes.get("magneturl") or [])]
    for candidate in candidates:
        text = (candidate or "").strip()
        if len(text) <= 4000 and text.lower().startswith(_LINK_SCHEMES) and not any(
                character.isspace() for character in text):
            return text
    return ""


def _item(item: Element, kind: str | None) -> Item | None:
    title = " ".join(_text(_child_text(item, "title"), TITLE_MAX).split())
    if not title:
        return None
    attributes = _attributes(item)
    size = _first_int(attributes.get("size"))
    if size is None:
        enclosure = _child(item, "enclosure")
        length = int_text(enclosure.get("length")) if enclosure is not None else None
        size = length if length is not None and length > 0 else int_text(_child_text(item, "size"))
    categories: list[int] = []
    for value in attributes.get("category", []):
        number = int_text(value)
        if number is not None and number not in categories:
            categories.append(number)
    for element in _children(item, "category"):
        number = int_text(element.text)
        if number is not None and number not in categories:
            categories.append(number)
    published = parse_date((attributes.get("usenetdate") or [""])[0]) if kind != "torznab" else None
    published = published or parse_date(_child_text(item, "pubDate"))
    seeders = _first_int(attributes.get("seeders"))
    leechers = _first_int(attributes.get("leechers"))
    peers = _first_int(attributes.get("peers"))
    if peers is None and seeders is not None and leechers is not None:
        peers = seeders + leechers
    guid = _child_text(item, "guid")
    info_hash = next((value.lower() for value in attributes.get("infohash") or [] if _INFO_HASH.fullmatch(value)), "")
    seed_time = _first_int(attributes.get("minimumseedtime"))
    return Item(
        title=title,
        size=size if size is not None and size >= 0 else None,
        published_at=published,
        categories=tuple(categories[:20]),
        seeders=seeders if seeders is not None and seeders >= 0 else None,
        peers=peers if peers is not None and peers >= 0 else None,
        grabs=_first_int(attributes.get("grabs")),
        guid=hashlib.sha256(guid.encode("utf-8")).hexdigest()[:24] if guid else "",
        link=_link(item, attributes),
        info_hash=info_hash,
        min_ratio=_first_float(attributes.get("minimumratio")),
        min_seed_seconds=seed_time if seed_time is not None and seed_time >= 0 else None,
        author=_stated(attributes.get("author"), 200),
        book_title=_stated(attributes.get("booktitle"), TITLE_MAX),
    )


def _stated(values: list[str] | None, limit: int) -> str:
    """The first value of an attribute that says something, as plain text on one line."""
    for value in values or []:
        text = " ".join(_text(value, limit).split())
        if text:
            return text
    return ""


def parse_feed(root: Element, namespaces: set[str]) -> Feed:
    if local_name(root.tag) != "rss":
        raise IndexerError("indexer_not_newznab")
    channel = _child(root, "channel")
    if channel is None:
        raise IndexerError("indexer_not_newznab")
    kind = detect_kind(root, namespaces)
    elements = _children(channel, "item")
    releases = tuple(found for element in elements[:MAX_ITEMS] if (found := _item(element, kind)) is not None)
    response = _child(channel, "response")
    total = int_text(response.get("total")) if response is not None else None
    return Feed(kind=kind, items=len(elements), limits=api_limits(channel) or api_limits(root), releases=releases,
                total=total if total is not None and total >= 0 else None)
