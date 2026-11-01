"""EPUB: title, authors, language, ISBN, series, cover, table of contents and an estimate of the pages.

An EPUB is a ZIP, and a ZIP is where a file lies about its size. Nothing is ever unpacked to disk, and no entry is read
past a limit: the XML parts of a book are a few kilobytes (``EPUB_MAX_XML``), a cover stops at ``COVER_MAX_BYTES``, the
number of entries at ``EPUB_MAX_ENTRIES``, and an entry that unpacks to hundreds of times what it weighs is a bomb. The
size of the text comes from the headers of the ZIP. Of the text itself only a small sample is read (``TEXT_SAMPLE``
bytes of at most ``TEXT_DOCUMENTS`` chapters from the middle of the book), to tell the language it is written in: a
package that names one language and a text in another is a book a person has to look at (a German book whose package
says Spanish).
"""

from __future__ import annotations

import os
import posixpath
import struct
import unicodedata
import zipfile
from html.parser import HTMLParser
from typing import IO, Any
from urllib.parse import unquote
from xml.etree.ElementTree import Element

from . import images, opf
from .limits import (
    BYTES_PER_PAGE,
    COVER_MAX_BYTES,
    EPUB_MAX_DIRECTORY,
    EPUB_MAX_ENTRIES,
    EPUB_MAX_FILE,
    EPUB_MAX_NAV,
    EPUB_MAX_RATIO,
    EPUB_MAX_XML,
    PAGES_MAX,
    TITLE_MAX,
    TOC_MAX_DEPTH,
    TOC_MAX_ENTRIES,
    ReadError,
)
from .text import clean, clear_sample_language, html_to_text, sample_language

#: How much of the text is read to tell its language: so many bytes of each chapter, from so many chapters at most.
TEXT_SAMPLE = 64 * 1024
TEXT_DOCUMENTS = 4
#: The words that are enough to tell the language.
TEXT_WORDS = 300

#: An entry of this many bytes that unpacks to more than ``EPUB_MAX_RATIO`` times its packed size is not a book.
BOMB_FREE = 8 * 1024 * 1024
#: All entries together may not claim more than this once unpacked, and more than ``TOTAL_FREE`` only at a ratio a book
#: could have.
TOTAL_MAX = 4 * 1024 * 1024 * 1024
TOTAL_FREE = 64 * 1024 * 1024


_EOCD = b"PK"
_EOCD64 = b"PK"
_LOCATOR64 = b"PK"


def _directory_claim(handle: IO[bytes], packed: int) -> tuple[int, int] | None:
    """What the end of the ZIP says about its central directory, read before ``zipfile`` builds a record for every
    entry in it: ``(entries, bytes)``, or None when no end record is found (``zipfile`` then says what is wrong). The
    end record is found the way ``zipfile`` finds it (the last signature in the last 64 KiB and a little), the ZIP64
    record behind its locator when the plain fields are full."""
    tail = min(packed, 65535 + 22 + 20)
    handle.seek(packed - tail)
    data = handle.read(tail)
    start = data.rfind(_EOCD, max(0, len(data) - (65535 + 22)))
    if start < 0 or len(data) - start < 22:
        return None
    _sig, _disk, _cd_disk, _here, total, size, _offset, _comment = struct.unpack("<4s4H2LH", data[start:start + 22])
    if total == 0xFFFF or size == 0xFFFFFFFF or _offset == 0xFFFFFFFF:
        locator = data[max(0, start - 20):start]
        if len(locator) == 20 and locator[:4] == _LOCATOR64:
            record_at = struct.unpack("<Q", locator[8:16])[0]
            if 0 <= record_at <= packed - 56:
                handle.seek(record_at)
                record = handle.read(56)
                if len(record) == 56 and record[:4] == _EOCD64:
                    total, size = struct.unpack("<QQ", record[32:48])
    return total, size


def _entry(archive: zipfile.ZipFile, names: dict[str, str], name: str, limit: int) -> bytes | None:
    """One entry of the archive, read up to ``limit`` bytes. None when it is not there; ``ReadError`` when it is larger
    than ``limit``, packed like a bomb, or encrypted."""
    actual = _lookup(names, name)
    if actual is None:
        return None
    info = archive.getinfo(actual)
    if info.flag_bits & 0x1:
        raise ReadError("encrypted")
    if info.file_size > limit:
        raise ReadError("too_large")
    if info.file_size > BOMB_FREE and info.file_size > info.compress_size * EPUB_MAX_RATIO:
        raise ReadError("zip_bomb")
    try:
        with archive.open(info) as stream:
            data = stream.read(limit + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError, ValueError) as exc:
        raise ReadError("unreadable") from exc
    if len(data) > limit:
        raise ReadError("too_large")
    return data


def _fold(name: str) -> str:
    """What two names have in common when they mean the same entry: slashes, Unicode form and letter case."""
    return unicodedata.normalize("NFC", name.replace("\\", "/")).casefold()


def _name_index(names_in_archive: list[str]) -> dict[str, str]:
    """Every name of the archive by itself, then by its folded form. The exact names go in first, so that a folded form
    never takes the place of another entry's own name."""
    names: dict[str, str] = {}
    for name in names_in_archive:
        names.setdefault(name, name)
    for name in names_in_archive:
        names.setdefault(_fold(name), name)
    return names


def _lookup(names: dict[str, str], name: str) -> str | None:
    """The entry called ``name``: as it is written, then in the folded form (case, Unicode form, slashes)."""
    return names.get(name) or names.get(_fold(name))


def locate(names: dict[str, str], base: str, href: str) -> str | None:
    """The entry of the archive an address in the book means, or None. The address of a package is a URL
    (``My%20Part%2C%20One.htm``), so it is decoded first; a file that really has a ``%`` in its name is found by
    the address as it stands in the package."""
    for decode in (True, False):
        found = resolve(base, href, decode=decode)
        if found is None:
            continue
        actual = _lookup(names, found[0])
        if actual is not None:
            return actual
    return None


def _is_page(media: str) -> bool:
    """Whether the manifest types a part as a page of text: by its media type, never by how its name ends (a book
    keeps its chapters in ``.xml`` or ``.htm`` files)."""
    return "html" in media or media in ("application/xml", "text/xml")


def resolve(base: str, href: str, decode: bool = True) -> tuple[str, str] | None:
    """The place in the archive an address in the book points to, and its anchor: ``href`` is read from the folder
    ``base``. None for an address that leaves the archive (``..``), is absolute or a link to the web. ``decode``: read
    the address as a URL (``%20``); off, it is taken as the name itself."""
    href = unquote(href.strip()) if decode else href.strip()
    path, _, anchor = href.partition("#")
    if not path:
        return None
    if "://" in path or path.startswith(("/", "\\")) or ":" in path.split("/", 1)[0] or "\x00" in href:
        return None
    joined = posixpath.normpath(posixpath.join(base, path))
    if joined.startswith(("..", "/")):
        return None
    return joined, anchor[:200]


class _NavParser(HTMLParser):
    """The table of contents of an EPUB 3 navigation document: the ``<nav>`` whose type is ``toc``, its nested lists,
    each ``<a href>`` with its text and depth. ``html.parser`` is used because these documents are XHTML with the
    entities of HTML in them, which an XML parser refuses; it expands nothing and fetches nothing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[tuple[int, str, str]] = []
        self.in_toc = False
        self.nav_depth = 0
        self.list_depth = 0
        self.href = ""
        self.text: list[str] = []
        self.capturing = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "nav":
            kind = values.get("epub:type", values.get("type", ""))
            if not self.in_toc and "toc" in kind.split():
                self.in_toc = True
                self.nav_depth = 1
            elif self.in_toc:
                self.nav_depth += 1
        elif self.in_toc and tag in ("ol", "ul"):
            self.list_depth += 1
        elif self.in_toc and tag == "a" and len(self.entries) < TOC_MAX_ENTRIES:
            self.href = values.get("href", "")
            self.text = []
            self.capturing = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self.in_toc:
            self.nav_depth -= 1
            if self.nav_depth <= 0:
                self.in_toc = False
        elif self.in_toc and tag in ("ol", "ul"):
            self.list_depth = max(0, self.list_depth - 1)
        elif self.in_toc and tag == "a" and self.capturing:
            self.capturing = False
            title = clean(" ".join("".join(self.text).split()), TITLE_MAX)
            if title and self.href:
                self.entries.append((max(0, self.list_depth - 1), title, self.href))

    def handle_data(self, data: str) -> None:
        if self.capturing and len(self.text) < 200:
            self.text.append(data)


def _toc_from_nav(data: bytes, base: str, opf_dir: str) -> list[dict[str, Any]]:
    parser = _NavParser()
    try:
        parser.feed(data.decode("utf-8", "replace"))
        parser.close()
    except Exception:  # noqa: BLE001 - a navigation document that html.parser cannot digest: no table of contents
        return []
    return _chapters(((depth, title, href) for depth, title, href in parser.entries), base, opf_dir)


def _chapters(entries: Any, base: str, opf_dir: str) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for depth, title, href in entries:
        found = resolve(base, href)
        if found is None or len(chapters) >= TOC_MAX_ENTRIES:
            continue
        path, anchor = found
        relative = posixpath.relpath(path, opf_dir or ".")
        target = relative + ("#" + anchor if anchor else "")
        chapters.append({"title": title, "target": target[:500], "depth": min(depth, TOC_MAX_DEPTH)})
    return chapters


def _toc_from_ncx(data: bytes, base: str, opf_dir: str) -> list[dict[str, Any]]:
    root = opf.parse_xml(data)
    entries: list[tuple[int, str, str]] = []
    nav_map = next((child for child in root if opf.local(child.tag) == "navMap"), None)
    if nav_map is None:
        return []
    # Depth first, with a stack of our own: the nesting of a hostile file is not our recursion depth.
    stack: list[tuple[Element, int]] = [(child, 0) for child in reversed(list(nav_map))]
    while stack and len(entries) < TOC_MAX_ENTRIES:
        point, depth = stack.pop()
        if opf.local(point.tag) != "navPoint" or depth > TOC_MAX_DEPTH:
            continue
        label = next((child for child in point if opf.local(child.tag) == "navLabel"), None)
        content = next((child for child in point if opf.local(child.tag) == "content"), None)
        title = clean(opf.text_of(label, 400), TITLE_MAX) if label is not None else ""
        src = opf.attribute(content, "src") if content is not None else ""
        if title and src:
            entries.append((depth, title, src))
        children = [child for child in point if opf.local(child.tag) == "navPoint"]
        stack.extend((child, depth + 1) for child in reversed(children))
    return _chapters(entries, base, opf_dir)


def _cover(package: opf.Package) -> str | None:
    """The id of the manifest item that is the cover: named by its properties, by the ``cover`` meta, or by its name."""
    for ident, (_href, _media, properties) in package.manifest.items():
        if "cover-image" in properties.split():
            return ident
    named = package.cover_meta_id
    if named and named in package.manifest and package.manifest[named][1].startswith("image/"):
        return named
    if named:
        # The meta may name the picture by its file name, or the id of a page that holds it.
        for ident, (href, media, _p) in package.manifest.items():
            if media.startswith("image/") and (ident == named or posixpath.basename(href) == named):
                return ident
    for ident, (href, media, _p) in package.manifest.items():
        if media.startswith("image/") and "cover" in (ident + " " + posixpath.basename(href)).casefold():
            return ident
    return None


def read(handle: IO[bytes]) -> dict[str, Any]:
    """Everything an EPUB says about itself. ``ReadError`` for what is no EPUB or is past a limit."""
    packed = os.fstat(handle.fileno()).st_size
    if packed > EPUB_MAX_FILE:
        raise ReadError("too_large")
    # Before the library builds a record for every entry of the directory: a file of a few megabytes can claim millions
    # of them, and building them costs the time and the memory of a bomb.
    claim = _directory_claim(handle, packed)
    handle.seek(0)
    if claim is not None:
        if claim[0] > EPUB_MAX_ENTRIES:
            raise ReadError("too_many_entries")
        if claim[1] > EPUB_MAX_DIRECTORY:
            raise ReadError("too_large")
    try:
        archive = zipfile.ZipFile(handle)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise ReadError("unreadable") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > EPUB_MAX_ENTRIES:
            raise ReadError("too_many_entries")
        declared = sum(info.file_size for info in infos)
        # A small file that claims to unpack to a great deal: a book never does (images are packed as they are, text
        # 5 to 20 times).
        if declared > TOTAL_MAX or (declared > TOTAL_FREE and declared > packed * EPUB_MAX_RATIO):
            raise ReadError("zip_bomb")
        names = _name_index([info.filename for info in infos])
        package_path = _package_path(archive, names)
        package_data = _entry(archive, names, package_path, EPUB_MAX_XML)
        if package_data is None:
            raise ReadError("unreadable")
        package = opf.parse_package(package_data)
        opf_dir = posixpath.dirname(package_path)
        meta = dict(package.meta)

        # The text of the book, counted by the headers of the archive (see limits.BYTES_PER_PAGE).
        order = [package.manifest[ident] for ident in package.spine if ident in package.manifest] or [
            item for item in package.manifest.values()]
        total = 0
        for href, media, _properties in order:
            if _is_page(media):
                actual = locate(names, opf_dir, href)
                if actual:
                    total += archive.getinfo(actual).file_size
        meta["pages"] = min(PAGES_MAX, max(1, round(total / BYTES_PER_PAGE))) if total else 0
        try:
            meta["text_language"], meta["text_language_clear"] = _text_language(archive, names, order, opf_dir)
        except Exception:  # noqa: BLE001 - the sample is a witness, never a reason for a book to be unreadable
            meta["text_language"], meta["text_language_clear"] = "", ""

        meta["chapters"] = _table_of_contents(archive, names, package, opf_dir)
        meta["cover"] = _read_cover(archive, names, package, opf_dir)
        meta["drm"] = _protected(archive, names, order, opf_dir)
    meta["format"] = "epub"
    return meta


def spine_sizes(handle: IO[bytes]) -> list[int]:
    """The unpacked size of each item of the spine, in reading order, for the items the manifest names (the way a
    reading program numbers the parts of a book: measured with KOReader, an item the manifest names and the archive does
    not hold counts as a part, of no size), or an empty list for anything that cannot be read. Only the package file is
    unpacked, within its limit; the sizes come from the headers of the archive."""
    try:
        packed = os.fstat(handle.fileno()).st_size
        if packed > EPUB_MAX_FILE:
            return []
        claim = _directory_claim(handle, packed)
        handle.seek(0)
        if claim is not None and (claim[0] > EPUB_MAX_ENTRIES or claim[1] > EPUB_MAX_DIRECTORY):
            return []
        with zipfile.ZipFile(handle) as archive:
            infos = archive.infolist()
            if len(infos) > EPUB_MAX_ENTRIES:
                return []
            names = _name_index([info.filename for info in infos])
            package_path = _package_path(archive, names)
            data = _entry(archive, names, package_path, EPUB_MAX_XML)
            if data is None:
                return []
            package = opf.parse_package(data)
            opf_dir = posixpath.dirname(package_path)
            sizes: list[int] = []
            for ident in package.spine:
                item = package.manifest.get(ident)
                if item is not None:
                    actual = locate(names, opf_dir, item[0])
                    sizes.append(archive.getinfo(actual).file_size if actual is not None else 0)
            return sizes
    except (ReadError, zipfile.BadZipFile, OSError, ValueError, EOFError, KeyError):
        return []


def _text_language(archive: zipfile.ZipFile, names: dict[str, str], order: list[tuple[str, str, str]],
                   opf_dir: str) -> tuple[str, str]:
    """The language the text of the book is written in (``text.sample_language``), from a sample out of the middle of
    the book (the front holds the title page, the imprint and the table of contents); empty when it cannot be told.
    Beside it the language when the sample says it beyond doubt (``text.clear_sample_language``): only that one may
    overrule the package.
    Never more than ``TEXT_SAMPLE`` bytes of a chapter are unpacked, and an encrypted chapter is skipped."""
    chapters = [href for href, media, _properties in order if _is_page(media)]
    if not chapters:
        return "", ""
    start = len(chapters) // 3
    words: list[str] = []
    for href in (chapters[start:] + chapters[:start])[:TEXT_DOCUMENTS]:
        actual = locate(names, opf_dir, href)
        if not actual:
            continue
        try:
            info = archive.getinfo(actual)
            if info.flag_bits & 0x1:
                continue
            with archive.open(info) as stream:
                data = stream.read(TEXT_SAMPLE)
            words.extend(html_to_text(data.decode("utf-8", "replace"), TEXT_SAMPLE).split())
        except Exception:  # noqa: BLE001, S112 - a damaged chapter (zlib, lzma, a broken entry) is left out of the sample
            continue
        if len(words) >= TEXT_WORDS:
            break
    sample = " ".join(words[:TEXT_WORDS])
    return sample_language(sample), clear_sample_language(sample)


#: What an EPUB may encrypt without being copy-protected: the fonts, scrambled so that they stay with the book.
_FONT_SCRAMBLING = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")
#: Files that only a protected book carries: Adobe's and Apple's rights files, the licence of Readium LCP.
_PROTECTION_FILES = ("META-INF/rights.xml", "META-INF/license.lcpl", "META-INF/sinf.xml")
#: The rights file of a Kobo book lies in the root of the archive and is made of a ``kdrm`` element.
_KOBO_RIGHTS = "rights.xml"
_KOBO_RIGHTS_MAX = 64 * 1024
#: How much of a chapter says whether it is markup, and how many chapters are looked at.
_MARKUP_SAMPLE = 2048
_MARKUP_DOCUMENTS = 4


def _protected(archive: zipfile.ZipFile, names: dict[str, str], order: list[tuple[str, str, str]] | None = None,
               opf_dir: str = "") -> bool:
    """Whether the book is copy-protected (Adobe, Kobo, Apple, Readium LCP or another DRM): it carries a rights file or
    a licence, or encrypts something other than its fonts, or **its chapters are not markup** (a protection that names
    itself nowhere shows there: an encrypted chapter is noise, and a reader that opened it would show noise).
    nexbooks cannot open such a book and says so; it does nothing to get around the protection."""
    if any(name in names or name.casefold() in names for name in _PROTECTION_FILES):
        return True
    try:
        rights = _entry(archive, names, _KOBO_RIGHTS, _KOBO_RIGHTS_MAX)
        if rights is not None and b"kdrm" in rights.lower():
            return True
    except ReadError:
        pass
    try:
        data = _entry(archive, names, "META-INF/encryption.xml", EPUB_MAX_XML)
        root = opf.parse_xml(data) if data is not None else None
    except ReadError:
        root = None
    if root is not None:
        for element in root.iter():
            method = opf.local(element.tag) == "EncryptionMethod"
            if method and opf.attribute(element, "Algorithm") not in _FONT_SCRAMBLING:
                return True
    return _chapters_are_noise(archive, names, order or [], opf_dir)


def _chapters_are_noise(archive: zipfile.ZipFile, names: dict[str, str], order: list[tuple[str, str, str]],
                        opf_dir: str) -> bool:
    """Whether one of the chapters looked at (from the middle of the book, as for the language) is not markup."""
    chapters = [href for href, media, _properties in order if _is_page(media)]
    start = len(chapters) // 3
    for href in (chapters[start:] + chapters[:start])[:_MARKUP_DOCUMENTS]:
        actual = locate(names, opf_dir, href)
        if not actual:
            continue
        try:
            info = archive.getinfo(actual)
            if info.flag_bits & 0x1:
                return True
            with archive.open(info) as stream:
                head = stream.read(_MARKUP_SAMPLE)
        except Exception:  # noqa: BLE001, S112 - a damaged chapter is a damaged book, not a protected one
            continue
        if not _is_markup(head):
            return True
    return False


def _is_markup(head: bytes) -> bool:
    """Whether the start of a chapter is text with markup in it (an empty chapter is no evidence either way)."""
    if not head.strip(b"\x00 \t\r\n"):
        return True
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = head.decode("utf-16", "replace")
    else:
        if b"\x00" in head:
            return False
        text = head.decode("utf-8-sig", "replace")
    controls = sum(1 for char in text if char < " " and char not in "\t\r\n")
    broken = text.count(chr(0xFFFD))
    return "<" in text and controls <= len(text) // 50 and broken <= len(text) // 20


def _package_path(archive: zipfile.ZipFile, names: dict[str, str]) -> str:
    container = _entry(archive, names, "META-INF/container.xml", EPUB_MAX_XML)
    if container is not None:
        root = opf.parse_xml(container)
        for element in root.iter():
            if opf.local(element.tag) == "rootfile":
                actual = locate(names, "", opf.attribute(element, "full-path"))
                if actual is not None:
                    return actual
    # No container, or one that points nowhere: the first package file there is.
    for name in names.values():
        if name.casefold().endswith(".opf"):
            return name
    raise ReadError("unreadable")


def _table_of_contents(
    archive: zipfile.ZipFile, names: dict[str, str], package: opf.Package, opf_dir: str
) -> list[dict[str, Any]]:
    for href, _media, properties in package.manifest.values():
        if "nav" in properties.split():
            actual = locate(names, opf_dir, href)
            data = _entry(archive, names, actual, EPUB_MAX_NAV) if actual else None
            if actual and data is not None:
                chapters = _toc_from_nav(data, posixpath.dirname(actual), opf_dir)
                if chapters:
                    return chapters
    ncx = package.manifest.get(package.toc_id) if package.toc_id else None
    if ncx is None:
        ncx = next((item for item in package.manifest.values() if item[1] == "application/x-dtbncx+xml"), None)
    if ncx is not None:
        actual = locate(names, opf_dir, ncx[0])
        data = _entry(archive, names, actual, EPUB_MAX_NAV) if actual else None
        if actual and data is not None:
            try:
                return _toc_from_ncx(data, posixpath.dirname(actual), opf_dir)
            except ReadError:
                return []
    return []


def _read_cover(
    archive: zipfile.ZipFile, names: dict[str, str], package: opf.Package, opf_dir: str
) -> tuple[bytes, str] | None:
    ident = _cover(package)
    if ident is None:
        return None
    actual = locate(names, opf_dir, package.manifest[ident][0])
    if actual is None:
        return None
    try:
        data = _entry(archive, names, actual, COVER_MAX_BYTES)
    except ReadError:
        return None
    return images.reduce_cover(data) if data else None


__all__ = ["locate", "read", "resolve"]
