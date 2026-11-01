"""EPUB: what is read, and what a hostile file cannot do (a bomb, entities, a lie about a size)."""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.services.readers import ReadError, epub, limits, read_file

from .bookmaker import cover_image, make_epub


def read(folder: Path, name: str) -> dict[str, Any]:
    return read_file(str(folder), name, "epub")


def test_an_epub3_gives_its_metadata(tmp_path: Path) -> None:
    make_epub(tmp_path / "a.epub", title="The Quiet Orchard", authors=("Mara Lindqvist", "Jonas Brandauer"),
              language="de-DE", isbn="978-3-9999-0010-0", asin="B0ABCDEFGH", series="Saga", series_pos=2,
              publisher="Example Press", date="2019-05-01T00:00:00+00:00", description="<p>One <b>day</b>.</p>",
              subjects=("Roman-Thriller", "Spannung"), cover=cover_image(), cover_style="property")
    probe = read(tmp_path, "a.epub")
    assert probe["title"] == "The Quiet Orchard"
    assert probe["authors"] == ["Mara Lindqvist", "Jonas Brandauer"]
    assert probe["language"] == "de"
    assert probe["isbn"] == "9783999900100" and probe["asin"] == "B0ABCDEFGH"
    assert (probe["series"], probe["series_pos"]) == ("Saga", 2.0)
    assert probe["publisher"] == "Example Press" and probe["published"] == "2019-05-01" and probe["year"] == 2019
    assert probe["description"] == "One day."
    assert probe["subjects"] == ["Roman-Thriller", "Spannung"]
    assert probe["cover"] is not None and probe["cover"][0][:4] == b"RIFF"
    assert [c["title"] for c in probe["chapters"]] == ["One", "Two", "Three"]
    assert probe["chapters"][0]["target"] == "text/c0.xhtml"


def test_an_epub2_with_calibre_series_and_an_ncx(tmp_path: Path) -> None:
    make_epub(tmp_path / "b.epub", epub3=False, toc="ncx", calibre_series=True, series="Saga", series_pos=3,
              cover=cover_image(), cover_style="meta", original_title="Alpha Original (englisch)")
    probe = read(tmp_path, "b.epub")
    assert (probe["series"], probe["series_pos"]) == ("Saga", 3.0)
    assert [c["target"] for c in probe["chapters"]] == ["text/c0.xhtml#top", "text/c1.xhtml#top", "text/c2.xhtml#top"]
    assert probe["cover"] is not None
    # A note about the language after the original title is not part of the title.
    assert probe["original_title"] == "Alpha Original"


def test_the_cover_is_found_by_name_when_nothing_declares_it(tmp_path: Path) -> None:
    make_epub(tmp_path / "c.epub", cover=cover_image(), cover_style="name")
    assert read(tmp_path, "c.epub")["cover"] is not None
    make_epub(tmp_path / "d.epub")
    assert read(tmp_path, "d.epub")["cover"] is None


def test_the_pages_of_an_epub_are_estimated_from_the_amount_of_text(tmp_path: Path) -> None:
    make_epub(tmp_path / "small.epub", text_bytes=3_000)
    make_epub(tmp_path / "big.epub", text_bytes=900_000)
    small, big = read(tmp_path, "small.epub")["pages"], read(tmp_path, "big.epub")["pages"]
    assert 1 <= small <= 3
    assert big == round(900_000 / limits.BYTES_PER_PAGE) or abs(big - 900_000 / limits.BYTES_PER_PAGE) < 15


def test_a_title_with_a_series_prefix_stays_as_the_file_has_it(tmp_path: Path) -> None:
    make_epub(tmp_path / "e.epub", title="[Saga 02] • Second", authors=("Mara Lindqvist",))
    assert read(tmp_path, "e.epub")["title"].startswith("[Saga 02]")


def test_a_book_that_names_no_language_has_none(tmp_path: Path) -> None:
    make_epub(tmp_path / "f.epub", language=None)
    assert read(tmp_path, "f.epub")["language"] == ""


# ---- broken and hostile files ---------------------------------------------------------------------------------------


def test_what_is_no_epub_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / "plain.epub").write_bytes(b"this is not a zip file at all" * 20)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "plain.epub")
    assert caught.value.code == "unreadable"
    with zipfile.ZipFile(tmp_path / "empty.epub", "w") as archive:
        archive.writestr("readme.txt", "no book here")
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "empty.epub")
    assert caught.value.code == "unreadable"


def test_a_cut_off_epub_is_unreadable(tmp_path: Path) -> None:
    path = make_epub(tmp_path / "cut.epub")
    path.write_bytes(path.read_bytes()[:-60])
    with pytest.raises(ReadError):
        read(tmp_path, "cut.epub")


def test_an_epub_with_too_many_entries_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_epub(tmp_path / "many.epub", extra_entries={f"junk/{n}.txt": b"x" for n in range(60)})
    monkeypatch.setattr(epub, "EPUB_MAX_ENTRIES", 50)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "many.epub")
    assert caught.value.code == "too_many_entries"


def _end_record(total: int, size: int, offset: int, comment: bytes = b"") -> bytes:
    return struct.pack("<4s4H2LH", b"PK", 0, 0, total, total, size, offset, len(comment)) + comment


def _never_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """From here on the ZIP library may not be asked to read a directory: the claim of the end record must be enough."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the central directory was parsed")

    monkeypatch.setattr(epub.zipfile, "ZipFile", refuse)


def test_a_directory_that_claims_too_many_entries_is_refused_before_it_is_parsed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_epub(tmp_path / "many.epub", extra_entries={f"junk/{n}.txt": b"x" for n in range(60)})
    monkeypatch.setattr(epub, "EPUB_MAX_ENTRIES", 50)
    _never_opened(monkeypatch)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "many.epub")
    assert caught.value.code == "too_many_entries"


def test_a_directory_that_claims_a_great_size_is_refused_before_it_is_parsed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The library reads as many records as the directory's size holds, whatever count the end record names."""
    (tmp_path / "big.epub").write_bytes(b"x" * 1000 + _end_record(5, limits.EPUB_MAX_DIRECTORY + 1, 0))
    _never_opened(monkeypatch)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "big.epub")
    assert caught.value.code == "too_large"


@pytest.mark.parametrize(("entries", "size", "code"), [(10**9, 1000, "too_many_entries"),
                                                        (3, 3 * 1024**3, "too_large")])
def test_the_zip64_record_is_read_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: int, size: int,
                                      code: str) -> None:
    """A ZIP64 file puts the true numbers in a record of their own; the plain fields only say that they are full."""
    head = b"x" * 100
    record = struct.pack("<4sQHHLLQQQQ", b"PK", 44, 45, 45, 0, 0, entries, entries, size, 0)
    locator = struct.pack("<4sLQL", b"PK", 0, len(head), 1)
    (tmp_path / "z.epub").write_bytes(head + record + locator + _end_record(0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF))
    _never_opened(monkeypatch)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "z.epub")
    assert caught.value.code == code


def test_an_honest_epub_with_a_comment_in_its_zip_is_still_read(tmp_path: Path) -> None:
    path = make_epub(tmp_path / "c.epub")
    with zipfile.ZipFile(path, "a") as archive:
        archive.comment = b"made by hand"
    assert read(tmp_path, "c.epub")["title"] == "The Quiet Orchard"


def test_an_epub_that_unpacks_to_far_more_than_it_weighs_is_a_bomb(tmp_path: Path) -> None:
    """A few kilobytes that claim 70 MB: a book is never that."""
    make_epub(tmp_path / "bomb.epub", extra_entries={"OEBPS/zeros.bin": bytes(70 * 1024 * 1024)})
    assert (tmp_path / "bomb.epub").stat().st_size < 200_000
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "bomb.epub")
    assert caught.value.code == "zip_bomb"


def test_an_entry_that_is_a_bomb_costs_the_cover_and_nothing_else(tmp_path: Path) -> None:
    """A cover that unpacks to 9 MB of zeros is not read; the book is."""
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as inner:
        inner.writestr("x", bytes(10))
    make_epub(tmp_path / "bombcover.epub", cover=bytes(9 * 1024 * 1024), cover_style="property")
    probe = read(tmp_path, "bombcover.epub")
    assert probe["title"] == "The Quiet Orchard" and probe["cover"] is None


def test_a_package_file_larger_than_the_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_epub(tmp_path / "bigopf.epub", description="x" * 5000)
    monkeypatch.setattr(epub, "EPUB_MAX_XML", 1024)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "bigopf.epub")
    assert caught.value.code == "too_large"


def test_entities_in_the_package_file_are_refused(tmp_path: Path) -> None:
    """The "billion laughs": entities that expand into entities."""
    bomb = ('<!DOCTYPE package [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>')
    make_epub(tmp_path / "laughs.epub", doctype=bomb, extra_opf="<dc:subject>&c;</dc:subject>")
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "laughs.epub")
    assert caught.value.code == "unreadable"


def test_an_external_entity_is_not_read(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET-CONTENT", encoding="utf-8")
    entity = f'<!DOCTYPE package [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
    make_epub(tmp_path / "xxe.epub", doctype=entity, extra_opf="<dc:subject>&x;</dc:subject>")
    with pytest.raises(ReadError):
        read(tmp_path, "xxe.epub")


def test_a_document_type_without_entities_is_let_through(tmp_path: Path) -> None:
    make_epub(tmp_path / "doctype.epub", doctype='<!DOCTYPE package PUBLIC "-//x//y" "http://127.0.0.1:9/x.dtd">')
    assert read(tmp_path, "doctype.epub")["title"] == "The Quiet Orchard"


def test_an_address_in_the_table_of_contents_cannot_leave_the_book() -> None:
    assert epub.resolve("OEBPS", "text/c1.xhtml#a") == ("OEBPS/text/c1.xhtml", "a")
    assert epub.resolve("OEBPS", "../c.xhtml") == ("c.xhtml", "")
    assert epub.resolve("OEBPS", "../../etc/passwd") is None
    assert epub.resolve("", "../x") is None
    assert epub.resolve("OEBPS", "/etc/passwd") is None
    assert epub.resolve("OEBPS", "http://example.com/x") is None
    assert epub.resolve("OEBPS", "file:///etc/passwd") is None
    assert epub.resolve("OEBPS", "#only-an-anchor") is None


def test_a_cover_that_is_a_picture_bomb_is_not_a_cover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.readers import images

    make_epub(tmp_path / "pixels.epub", cover=cover_image(size=(400, 600)), cover_style="property")
    monkeypatch.setattr(images, "COVER_MAX_PIXELS", 1000)
    probe = read(tmp_path, "pixels.epub")
    assert probe["cover"] is None and probe["title"] == "The Quiet Orchard"


def test_a_very_deep_ncx_is_cut_not_followed(tmp_path: Path) -> None:
    depth = 3000
    points = "".join(
        f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>L{n}</text></navLabel><content src="text/c0.xhtml"/>'
        for n in range(depth)) + "</navPoint>" * depth
    ncx = ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
           + points + "</navMap></ncx>")
    make_epub(tmp_path / "deep.epub", epub3=False, toc="ncx")
    with zipfile.ZipFile(tmp_path / "deep.epub") as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["OEBPS/toc.ncx"] = ncx.encode()
    with zipfile.ZipFile(tmp_path / "deep.epub", "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    try:
        probe = read(tmp_path, "deep.epub")
    except ReadError:
        return  # refused is fine, too
    assert len(probe["chapters"]) <= limits.TOC_MAX_ENTRIES
    assert max((c["depth"] for c in probe["chapters"]), default=0) <= limits.TOC_MAX_DEPTH


def test_an_epub_larger_than_the_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_epub(tmp_path / "large.epub")
    monkeypatch.setattr(epub, "EPUB_MAX_FILE", 100)
    with pytest.raises(ReadError) as caught:
        read(tmp_path, "large.epub")
    assert caught.value.code == "too_large"
