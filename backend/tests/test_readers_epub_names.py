"""EPUB: the addresses of a package and the names in the ZIP. A package writes an address as a URL, a ZIP holds the plain name,
and some files keep their chapters in ``.xml`` or ``.htm`` files, in a package of another name, with a ``%`` in a name."""

from __future__ import annotations

import struct
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from app.services.readers import epub, read_file

from .bookmaker import FILLER

GERMAN = FILLER["de"] * 60


def hand_epub(path: Path, pages: list[tuple[str, str | None, str]], *, opf: str = "OEBPS/content.opf", language: str = "en",
              end_count: int | None = None) -> Path:
    """A book put together by hand. ``pages``: (the address in the manifest, the name of the entry or None for a part the
    book names and does not hold, the media type). The text of every page is German, the package says ``language``."""
    folder = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
    manifest = "".join(f'<item id="p{n}" href="{href}" media-type="{media}"/>' for n, (href, _entry, media) in enumerate(pages))
    spine = "".join(f'<itemref idref="p{n}"/>' for n in range(len(pages)))
    package = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id"><metadata>'
        '<dc:identifier id="id">urn:test:1</dc:identifier><dc:title>The Ladder Of Names</dc:title>'
        '<dc:creator opf:role="aut">Rune Falkner</dc:creator><dc:creator opf:role="bkp"></dc:creator>'
        f"<dc:language>{language}</dc:language></metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>")
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        f'<rootfile full-path="{opf}" media-type="application/oebps-package+xml"/></rootfiles></container>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr(opf, package)
        for _href, entry, _media in pages:
            if entry is not None:
                archive.writestr(folder + entry, f"<html><body><p>{GERMAN}</p></body></html>")
    if end_count is not None:
        data = bytearray(path.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        data[end + 8:end + 10] = struct.pack("<H", end_count)
        path.write_bytes(bytes(data))
    return path


def probe(folder: Path, name: str) -> dict[str, Any]:
    return read_file(str(folder), name, "epub")


def test_a_package_that_escapes_its_addresses_is_found_in_a_zip_of_plain_names(tmp_path: Path) -> None:
    html = "application/xhtml+xml"
    hand_epub(tmp_path / "a.epub", [
        ("Text/cover.xhtml", "Text/cover.xhtml", html),
        ("Text/Rune%20Falkner%20-%20Part%2C%20One_split_001.htm", "Text/Rune Falkner - Part, One_split_001.htm", html),
        ("Text/Rune%20Falkner%20-%20Part%2C%20Two_split_002.htm", "Text/Rune Falkner - Part, Two_split_002.htm", html),
    ])
    found = probe(tmp_path, "a.epub")
    # The text was read, from the parts with the comma in their name: it says German where the package says English.
    assert found["text_language"] == "de"
    assert found["pages"] >= 1


def test_the_pages_are_counted_from_all_the_parts_that_are_there(tmp_path: Path) -> None:
    html = "application/xhtml+xml"
    plain = [(f"Text/p{n}.xhtml", f"Text/p{n}.xhtml", html) for n in range(4)]
    escaped = [(f"Text/p%20{n}%2C.xhtml", f"Text/p {n},.xhtml", html) for n in range(4)]
    hand_epub(tmp_path / "plain.epub", plain)
    hand_epub(tmp_path / "escaped.epub", escaped)
    assert probe(tmp_path, "escaped.epub")["pages"] == probe(tmp_path, "plain.epub")["pages"] > 0


def test_a_name_that_really_has_a_percent_sign_in_it_is_found_as_written(tmp_path: Path) -> None:
    html = "application/xhtml+xml"
    hand_epub(tmp_path / "p.epub", [
        ("Text/a.xhtml", "Text/a.xhtml", html),
        ("Text/100% done.xhtml", "Text/100% done.xhtml", html),
        ("Text/50%2520.xhtml", "Text/50%20.xhtml", html),
    ])
    names = epub._name_index(["OEBPS/Text/a.xhtml", "OEBPS/Text/100% done.xhtml", "OEBPS/Text/50%20.xhtml"])
    assert epub.locate(names, "OEBPS", "Text/100% done.xhtml") == "OEBPS/Text/100% done.xhtml"
    assert epub.locate(names, "OEBPS", "Text/100%25%20done.xhtml") == "OEBPS/Text/100% done.xhtml"
    assert epub.locate(names, "OEBPS", "Text/50%2520.xhtml") == "OEBPS/Text/50%20.xhtml"
    # An entry that is really called "50%20.xhtml" and is named by that address in the package (no escape at all).
    assert epub.locate(names, "OEBPS", "Text/50%20.xhtml") is not None
    assert probe(tmp_path, "p.epub")["text_language"] == "de"


def test_pages_in_xml_files_of_the_manifest_type_are_read_wherever_the_package_lies(tmp_path: Path) -> None:
    hand_epub(tmp_path / "x.epub", [
        ("falkner-one.xml", "falkner-one.xml", "application/xhtml+xml"),
        ("falkner-two.xml", "falkner-two.xml", "application/xml"),
    ], opf="inhalt.opf")
    found = probe(tmp_path, "x.epub")
    assert found["text_language"] == "de" and found["pages"] >= 1
    assert found["title"] == "The Ladder Of Names"
    assert found["authors"] == ["Rune Falkner"]


def test_a_name_in_another_unicode_form_or_case_is_found(tmp_path: Path) -> None:
    composed = "Caf" + chr(0xE9)
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    html = "application/xhtml+xml"
    hand_epub(tmp_path / "u.epub", [(f"Text/{composed}.xhtml", f"Text/{decomposed}.xhtml", html),
                                    ("Text/Two.xhtml", "Text/TWO.xhtml", html)])
    assert probe(tmp_path, "u.epub")["text_language"] == "de"


def test_an_exact_name_is_never_taken_by_the_folded_form_of_another_entry() -> None:
    names = epub._name_index(["OEBPS/A.htm", "OEBPS/a.htm"])
    assert epub.locate(names, "OEBPS", "A.htm") == "OEBPS/A.htm"
    assert epub.locate(names, "OEBPS", "a.htm") == "OEBPS/a.htm"
    assert epub.locate(names, "OEBPS", "b.htm") is None


def test_an_address_that_leaves_the_book_finds_nothing() -> None:
    names = epub._name_index(["OEBPS/a.htm", "x.htm"])
    assert epub.locate(names, "OEBPS", "../../x.htm") is None
    assert epub.locate(names, "OEBPS", "/x.htm") is None
    assert epub.locate(names, "OEBPS", "http://example.com/a.htm") is None


def test_a_zip_whose_end_record_counts_twice_is_read_like_any_other(tmp_path: Path) -> None:
    html = "application/xhtml+xml"
    hand_epub(tmp_path / "e.epub", [("a.xhtml", "a.xhtml", html), ("b.xhtml", "b.xhtml", html)], opf="inhalt.opf", end_count=2 * 6)
    assert probe(tmp_path, "e.epub")["text_language"] == "de"


def test_an_entry_called_like_the_folded_form_of_another_is_found_by_its_own_name() -> None:
    names = epub._name_index(["OEBPS/A.htm", "oebps/a.htm"])
    assert epub.locate(names, "", "oebps/a.htm") == "oebps/a.htm"
    assert epub.locate(names, "", "OEBPS/A.htm") == "OEBPS/A.htm"


def test_pages_typed_as_plain_xml_are_read_for_the_language(tmp_path: Path) -> None:
    hand_epub(tmp_path / "plain.epub", [("one.xml", "one.xml", "application/xml"), ("two.xml", "two.xml", "text/xml")],
              opf="inhalt.opf")
    assert probe(tmp_path, "plain.epub")["text_language"] == "de"
