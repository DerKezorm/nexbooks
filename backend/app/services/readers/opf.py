"""The package file of an EPUB (``content.opf``) and Calibre's ``metadata.opf``: the same XML, read with ``defusedxml``.

``defusedxml`` refuses entity declarations (the "billion laughs" bomb) and external references (a file of the server, an
address); a document type declaration without either is let through, because EPUB 2's navigation file carries one.
Everything read is cut to a length, and a field of an unexpected type is a field that is not there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .limits import DESCRIPTION_MAX, TITLE_MAX, ReadError
from .names import authors_of_field, number_of, series_from_field, split_authors
from .text import clean, html_to_text, isbn13, language_of, year_of

MAX_SUBJECTS = 24
#: Codes a package writes for "no language" or "not said" that ``text.language_of`` lets through as two letters.
NO_LANGUAGE = frozenset({"xx", "zz"})
#: Custom columns of Calibre that hold the title in the original language (the label the library chose).
_ORIGINAL_TITLE_LABELS = frozenset({"orgtitle", "originaltitle", "originaltitel", "origtitle", "original_title"})
_ASIN = re.compile(r"^B0[0-9A-Z]{8}$")


def parse_xml(data: bytes) -> Element:
    """The root of an XML document, or ``ReadError("unreadable")``: broken, with entities, with external references."""
    try:
        return fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except (ParseError, DefusedXmlException, ValueError, RecursionError) as exc:
        raise ReadError("unreadable") from exc


def local(tag: Any) -> str:
    """The name of an element without its namespace; empty for a comment."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def attribute(element: Element, name: str) -> str:
    """An attribute by its local name, whatever namespace prefix it carries (``opf:role`` and ``role`` are one)."""
    for key, value in element.attrib.items():
        if key.rsplit("}", 1)[-1] == name:
            return value
    return ""


def text_of(element: Element, limit: int = 4000) -> str:
    """All the text inside an element, raw (a description may hold markup); at most ``limit`` characters."""
    pieces: list[str] = []
    size = 0
    for piece in element.itertext():
        pieces.append(piece)
        size += len(piece)
        if size > limit * 4:
            break
    return "".join(pieces)[: limit * 4]


@dataclass
class Package:
    """What the package file says about the book. ``manifest`` maps an id to ``(href, media type, properties)``;
    ``spine`` lists the ids in reading order; ``toc_id`` is the id of the NCX the spine names; ``cover_id`` /
    ``cover_properties`` lead to the cover."""

    meta: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    spine: list[str] = field(default_factory=list)
    toc_id: str = ""
    cover_meta_id: str = ""


def _refines(root: Element) -> dict[str, dict[str, str]]:
    """EPUB 3 refinements: ``<meta refines="#id" property="role">aut</meta>`` becomes ``{"id": {"role": "aut"}}``."""
    found: dict[str, dict[str, str]] = {}
    for element in root.iter():
        if local(element.tag) == "meta" and attribute(element, "refines").startswith("#"):
            target = attribute(element, "refines")[1:]
            prop = attribute(element, "property")
            if prop:
                found.setdefault(target, {}).setdefault(prop, clean(text_of(element, 200), 200))
    return found


def parse_package(data: bytes) -> Package:
    """Reads a package file (or a Calibre ``metadata.opf``). ``ReadError`` when it is no such XML."""
    root = parse_xml(data)
    if local(root.tag) != "package":
        raise ReadError("unreadable")
    package = Package()
    metadata = next((child for child in root if local(child.tag) == "metadata"), None)
    if metadata is not None:
        package.meta = _metadata(root, metadata)
        package.cover_meta_id = package.meta.pop("_cover_id", "")
    manifest = next((child for child in root if local(child.tag) == "manifest"), None)
    if manifest is not None:
        for index, item in enumerate(manifest):
            if index > 20_000:
                break
            if local(item.tag) == "item" and attribute(item, "id") and attribute(item, "href"):
                package.manifest[attribute(item, "id")] = (
                    attribute(item, "href"), attribute(item, "media-type").lower(), attribute(item, "properties"))
    spine = next((child for child in root if local(child.tag) == "spine"), None)
    if spine is not None:
        package.toc_id = attribute(spine, "toc")
        for index, ref in enumerate(spine):
            if index > 20_000:
                break
            if local(ref.tag) == "itemref" and attribute(ref, "idref"):
                package.spine.append(attribute(ref, "idref"))
    return package


def _metadata(root: Element, metadata: Element) -> dict[str, Any]:
    refines = _refines(root)
    titles: list[tuple[str, str]] = []
    authors: list[str] = []
    translators: list[str] = []
    narrators: list[str] = []
    identifiers: list[tuple[str, str]] = []
    subjects: list[str] = []
    dates: list[tuple[str, str]] = []
    out: dict[str, Any] = {}
    series, series_pos = "", None
    collection: dict[str, tuple[str, str]] = {}
    group_positions: dict[str, str] = {}
    languages: list[str] = []
    for index, element in enumerate(metadata):
        if index > 400:
            break
        name = local(element.tag)
        value = text_of(element, 1000 if name != "description" else DESCRIPTION_MAX)
        ident = attribute(element, "id")
        if name == "title":
            kind = refines.get(ident, {}).get("title-type", "")
            titles.append((clean(value, TITLE_MAX), kind))
        elif name in ("creator", "contributor"):
            role = (attribute(element, "role") or refines.get(ident, {}).get("role", "")).lower()
            who = clean(value, 200)
            if not who:
                continue
            if role in ("trl",):
                translators.append(who)
            elif role in ("nrt",):
                narrators.append(who)
            elif role in ("", "aut") and (name == "creator" or role == "aut"):
                authors.append(who)
        elif name == "language":
            # The first language the package names as a language: an empty one, ``und`` or ``mul`` says nothing.
            code = language_of(value)
            if code and code not in NO_LANGUAGE:
                out.setdefault("language", code)
                if code not in languages:
                    languages.append(code)
        elif name == "identifier":
            identifiers.append((clean(value, 100), attribute(element, "scheme").lower()))
        elif name == "publisher":
            out.setdefault("publisher", clean(value, 200))
        elif name == "date":
            dates.append((clean(value, 40), attribute(element, "event").lower()))
        elif name == "description":
            out.setdefault("description", html_to_text(value))
        elif name == "subject":
            subject = clean(value, 80)
            if subject and len(subjects) < MAX_SUBJECTS:
                subjects.append(subject)
        elif name == "meta":
            key = attribute(element, "name")
            content = attribute(element, "content")
            prop = attribute(element, "property")
            if key == "cover":
                out["_cover_id"] = clean(content, 200)
            elif key == "calibre:series" and content:
                series = clean(content, 200)
            elif key == "calibre:series_index":
                series_pos = number_of(content)
            elif key.startswith("calibre:user_metadata:#"):
                label = key.rsplit("#", 1)[-1].casefold()
                if label in _ORIGINAL_TITLE_LABELS:
                    original = _original_title(_custom_value(content))
                    if original:
                        out.setdefault("original_title", original)
            elif prop == "belongs-to-collection":
                collection[ident] = (clean(value, 200), refines.get(ident, {}).get("collection-type", "series"))
                group_positions[ident] = refines.get(ident, {}).get("group-position", "")
            elif prop == "calibre:series" and value.strip():
                series = clean(value, 200)
            elif prop == "calibre:series_index":
                series_pos = number_of(value)
            elif prop.startswith("calibre:user_metadata") and not content:
                pass
    main = next((t for t, kind in titles if kind == "main" and t), "") or next((t for t, _k in titles if t), "")
    out["title"] = main
    out["subtitle"] = next((t for t, kind in titles if kind == "subtitle" and t), "")
    out["authors"], out["pen_names"] = authors_of_field(authors, titles=[main])
    out["translators"] = split_authors(translators, any_role=True)
    out["narrators"] = split_authors(narrators, any_role=True)
    out["subjects"] = subjects
    out["language"] = out.get("language", "")
    if len(languages) > 1:
        # A package that names more than one language (a bilingual book, or a template that kept another).
        out["languages"] = languages[:4]
    if not series:
        for ident, (name, kind) in collection.items():
            if kind == "series" and name:
                series = name
                series_pos = number_of(group_positions.get(ident, "")) if series_pos is None else series_pos
                break
    if series and series_pos is None:
        series, series_pos = series_from_field(series)
    out["series"], out["series_pos"] = series, series_pos
    isbn, asin = "", ""
    for value, scheme in identifiers:
        if not isbn and (scheme.startswith("isbn") or value.lower().startswith(("isbn:", "urn:isbn:"))
                         or value.replace("-", "").isdigit()):
            isbn = isbn13(value)
        if not asin and (scheme in ("asin", "mobi-asin") or scheme.startswith("amazon") or scheme == ""):
            candidate = value.replace("asin:", "").strip().upper()
            if _ASIN.match(candidate):
                asin = candidate
    out["isbn"], out["asin"] = isbn, asin
    published = next((d for d, event in dates if d and event != "modification"), "") or next(
        (d for d, _e in dates if d), "")
    out["published"] = published[:10] if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?", published) else ""
    out["year"] = year_of(published)
    return out


#: A custom column for the original title often carries the language in brackets: "The Lost Seal (amerikanisch)".
_ORIGINAL_NOTE = re.compile(r"\s*\(([^()]{1,30})\)\s*$")


def _original_title(value: str) -> str:
    """The original title without the note about its language; empty when nothing else is left."""
    value = clean(value, TITLE_MAX)
    return _ORIGINAL_NOTE.sub("", value).strip() if _ORIGINAL_NOTE.search(value) else value


def _custom_value(content: str) -> str:
    """The value of a Calibre custom column, which is stored as JSON in a ``meta`` element."""
    if not content or len(content) > 200_000:
        return ""
    try:
        parsed = json.loads(content)
    except (ValueError, RecursionError):
        return ""
    if isinstance(parsed, dict):
        value = parsed.get("#value#")
        return clean(value, TITLE_MAX) if isinstance(value, str) else ""
    return ""
