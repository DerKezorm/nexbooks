"""What a folder can hold that is not text, not seen, or not all of it: names that cannot be kept, texts that cannot be
written, a walk that stops at its limit, a folder that lost its books but not every file, and a folder name with the
joint of the keys in it. None of it ends the run, and nothing is marked missing for what was not seen."""

from __future__ import annotations

import io
import json
import os
import shutil
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BookFile, Chapter, Edition, Library, LibraryProblem, Person, Series, Work
from app.services import catalog, scanner
from app.services.readers import audio, sidecar
from app.services.readers.text import clean, html_to_text

from .library_helpers import age, book, books_folder, make_library, scan
from .test_scanner import titles


def invalid_name() -> str:
    """A name that is no text: a lone surrogate where the system keeps names in 16 bits, bytes that are no UTF-8 where it
    keeps them as bytes. (The surrogate is written as a number: the character itself does not survive every tool.)"""
    if os.name == "nt":
        return "bad" + chr(0xD800)
    return os.fsdecode(b"bad\xff")


def problems() -> dict[str, str]:
    with SessionLocal() as db:
        return {p.path: p.reason for p in db.scalars(select(LibraryProblem))}


def has_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(char) <= 0xDFFF for char in text)


# ---- names ----------------------------------------------------------------------------------------------------------


def test_a_folder_whose_name_is_no_text_is_listed_and_the_rest_is_read() -> None:
    folder = books_folder()
    book(folder, "Good/good.epub", title="Good", authors=("Ada Feldkamp",))
    bad = invalid_name()
    book(folder, f"{bad}/hidden.epub", title="Hidden", authors=("Ada Feldkamp",))
    book(folder, f"{bad}/deeper/more.epub", title="More", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    row = scan(library_id)
    # The run was not ended by it: the good book is there, the report is that of a run, and the folder is on the list.
    assert row.report["aborted"] is None and titles() == ["Good"]
    listed = problems()
    assert list(listed.values()) == ["bad_name"] and len(listed) == 1
    only = next(iter(listed))
    assert only.startswith("bad") and not has_surrogate(only)
    assert row.report["problems"] == 1
    # Again: the same line, once; the good book is not read again.
    row = scan(library_id)
    assert list(problems().values()) == ["bad_name"] and row.report["added"] == 0 and titles() == ["Good"]
    # Renamed, the folder is read and the line goes.
    for entry in list(folder.iterdir()):
        if entry.name != "Good":
            entry.rename(folder / "Mended")
    scan(library_id)
    assert titles() == ["Good", "Hidden", "More"] and problems() == {}


def test_a_book_file_whose_name_is_no_text_is_listed_not_skipped_in_silence() -> None:
    folder = books_folder()
    book(folder, "good.epub", title="Good", authors=("Ada Feldkamp",))
    bad = invalid_name()
    book(folder, f"{bad}.epub", title="Bad Name", authors=("Ada Feldkamp",))
    # A file nexbooks would not have read anyway is no problem of the library.
    stray = folder / f"{bad}.txt"
    stray.write_text("notes", encoding="utf-8")
    row = scan(make_library(folder))
    assert row.report["aborted"] is None and titles() == ["Good"] and row.report["problems"] == 1
    assert list(problems().values()) == ["bad_name"]


def test_a_name_with_a_control_character_is_no_name_either() -> None:
    assert scanner.valid_name("A Book.epub") and scanner.valid_name("Müller, Hans - Über.epub")
    assert not scanner.valid_name("a\nb.epub") and not scanner.valid_name("a" + chr(31) + "b")
    assert not scanner.valid_name("x" + chr(0xD800)) and not scanner.valid_name("tab\there")


# ---- texts that cannot be written -----------------------------------------------------------------------------------

BAD = "x" + chr(0xD800) + "y"
FIELDS = ("title", "subtitle", "authors", "narrators", "series", "genres", "tags", "publisher", "description", "isbn",
          "asin", "language", "publishedDate", "publishedYear", "chapters")


def poisoned(field: str) -> dict[str, Any]:
    """A ``metadata.json`` whose one field holds a lone surrogate (what JSON says with an escape)."""
    data: dict[str, Any] = {"title": "Plain", "authors": ["Ada Feldkamp"], "narrators": ["Ben Hollister"],
                            "genres": ["Roman"], "series": ["Saga #2"], "description": "Text", "publisher": "House",
                            "language": "en", "asin": "B0ABCDEFGH", "isbn": "9783999900100",
                            "chapters": [{"title": "One", "start": 0.0}]}
    value: Any = BAD
    if field in ("authors", "narrators", "genres", "tags", "series"):
        value = [BAD]
    if field == "chapters":
        value = [{"title": BAD, "start": 1.0}]
    if field == "publishedYear":
        value = "19" + chr(0xD800) + "99"
    data[field] = value
    return data


def walk_out(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in list(value) + list(value.values()) for text in walk_out(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in walk_out(item)]
    return []


@pytest.mark.parametrize("field", FIELDS)
def test_the_reader_of_metadata_json_leaves_no_lone_surrogate_in_any_field(field: str) -> None:
    raw = json.dumps(poisoned(field)).encode("utf-8")  # the escape \ud800 is plain ASCII in the file
    assert b"ud800" in raw
    out = sidecar.read_json(io.BytesIO(raw))
    assert not any(has_surrogate(text) for text in walk_out(out)), field


@pytest.mark.parametrize("field", FIELDS)
def test_a_surrogate_in_any_field_of_metadata_json_does_not_end_the_run(field: str) -> None:
    folder = books_folder()
    book(folder, "Only/only.epub", title="Only", authors=("Ada Feldkamp",))
    (folder / "Only" / "metadata.json").write_text(json.dumps(poisoned(field)), encoding="utf-8")
    age(folder / "Only" / "metadata.json")
    book(folder, "Other/other.epub", title="Other", authors=("Ada Feldkamp",))
    row = scan(make_library(folder))
    assert row.report["aborted"] is None and "Other" in titles() and len(titles()) == 2, field
    with SessionLocal() as db:
        every = [str(value) for model in (Work, Edition, Person, Series, Chapter) for record in db.scalars(select(model))
                 for value in vars(record).values() if isinstance(value, str)]
        every += [json.dumps(record.probe) for record in db.scalars(select(BookFile))]
    assert not any(has_surrogate(text) for text in every), field


def test_the_tags_of_a_recording_with_a_lone_surrogate_come_out_clean() -> None:
    from types import SimpleNamespace

    tags = {key: [BAD] for key in ("title", "album", "artist", "albumartist", "composer", "narrator", "genre", "date",
                                    "description", "series", "series_part", "tracknumber", "asin", "isbn", "language",
                                    "publisher")}
    out = audio._finish("mp3", 12.0, tags, [], None)
    assert not any(has_surrogate(text) for text in walk_out(out)) and out["album"] == "xy"
    # The chapters of a container come out of the extractors of the formats.
    stub = SimpleNamespace(tags={"©nam": [BAD], "©ART": [BAD]}, chapters=[SimpleNamespace(start=2.0, title=BAD)])
    _tags, chapters, _cover = audio._mp4(stub)
    assert chapters == [{"title": "xy", "start": 2.0}]


def test_clean_and_html_to_text_take_lone_surrogates_out() -> None:
    assert clean(BAD) == "xy" and clean("A" + chr(0xDC80) * 3 + "B") == "AB"
    assert html_to_text(f"<p>one {BAD}</p><p>two {BAD}</p>") == "one xy\n\ntwo xy"
    assert html_to_text(f"plain {BAD} text") == "plain xy text"


def test_xml_that_names_a_lone_surrogate_is_refused_not_kept() -> None:
    from app.services.readers import opf
    from app.services.readers.limits import ReadError

    xml = (b'<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           b"<dc:title>a&#xD800;b</dc:title></metadata></package>")
    with pytest.raises(ReadError):
        opf.parse_package(xml)


# ---- a walk that stopped at its limit ---------------------------------------------------------------------------------


def test_a_walk_that_stops_at_its_limit_marks_nothing_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    for number in range(1, 5):
        book(folder, f"book{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    assert scan(library_id).report["aborted"] is None and len(titles()) == 4
    monkeypatch.setattr(scanner, "MAX_ENTRIES", 2)
    row = scan(library_id)
    assert row.report["aborted"] == "truncated" and row.report["missing"] == 0
    with SessionLocal() as db:
        assert all(edition.missing_at is None for edition in db.scalars(select(Edition)))
        assert all(file.missing_at is None for file in db.scalars(select(BookFile)))
    monkeypatch.undo()
    row = scan(library_id)
    assert row.report["aborted"] is None and row.report["missing"] == 0 and len(titles()) == 4


def test_what_a_truncated_walk_did_see_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    for number in range(1, 4):
        book(folder, f"book{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    monkeypatch.setattr(scanner, "MAX_ENTRIES", 2)
    row = scan(make_library(folder))
    assert row.report["aborted"] == "truncated" and len(titles()) == 2


def test_a_truncated_walk_keeps_the_problems_of_the_files_it_did_not_see(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    book(folder, "a-good.epub", title="Good", authors=("Ada Feldkamp",))
    (folder / "z-broken.epub").write_bytes(b"not an epub" * 50)
    age(folder / "z-broken.epub")
    library_id = make_library(folder)
    assert scan(library_id).report["problems"] == 1 and problems() == {"z-broken.epub": "unreadable"}
    real_walk = scanner.walk

    def cut_short(root: object, kind: str) -> scanner.Walked:
        walked = real_walk(root, kind)  # type: ignore[arg-type]
        walked.found = [item for item in walked.found if item.path != "z-broken.epub"]
        walked.truncated = True
        return walked

    monkeypatch.setattr(scanner, "walk", cut_short)
    row = scan(library_id)
    assert row.report["aborted"] == "truncated"
    # The walk did not see the broken file: it is not known to be gone, its line stays.
    assert problems() == {"z-broken.epub": "unreadable"}
    monkeypatch.undo()
    assert scan(library_id).report["aborted"] is None and problems() == {"z-broken.epub": "unreadable"}


# ---- a folder with a stray file and no book ---------------------------------------------------------------------------


def test_a_folder_with_a_stray_file_but_no_book_is_as_empty_as_one_with_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    for number in range(1, 4):
        book(folder, f"Dir{number}/book{number}.epub", title=f"Book {number}", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    # The share goes: what is left is the mount point with a note in it.
    for number in range(1, 4):
        shutil.rmtree(folder / f"Dir{number}")
    (folder / "readme.txt").write_text("not mounted", encoding="utf-8")
    row = scan(library_id)
    assert row.report["aborted"] == "empty" and row.report["missing"] == 0
    with SessionLocal() as db:
        assert all(edition.missing_at is None for edition in db.scalars(select(Edition)))
    # A file beside the books (a sidecar) is no book either.
    (folder / "metadata.json").write_text(json.dumps({"title": "Alone"}), encoding="utf-8")
    age(folder / "metadata.json")
    row = scan(library_id)
    assert row.report["aborted"] == "empty" and row.report["missing"] == 0
    # The books come back: the run goes on as before.
    book(folder, "Dir1/book1.epub", title="Book 1", authors=("Ada Feldkamp",))
    row = scan(library_id)
    assert row.report["aborted"] is None and row.report["missing"] == 2


# ---- the joint of the keys ------------------------------------------------------------------------------------------


def test_a_bar_in_a_folder_name_is_part_of_the_name_not_of_the_key() -> None:
    for path, fmt in (("Ada | Buch/barbuch.epub", "epub"), ("Ada | Buch/CD 1/part.mp3", "mp3"),
                      ("A|B|C/x.m4b", "m4b")):
        unit = catalog.unit_key(path, fmt, {"album": "Album"})
        want = path.rsplit("/", 1)[0]
        if fmt == "mp3":
            want = "Ada | Buch"
        assert catalog.unit_folder(unit) == want, path


def test_the_files_beside_a_book_in_a_folder_with_a_bar_in_its_name_still_speak_for_it() -> None:
    """The rows are made by hand: Windows cannot make such a folder, the key must not care where it runs."""
    folder = books_folder()
    library_id = make_library(folder)
    unit = catalog.unit_key("Ada | Buch/barbuch.epub", "epub", None)
    with SessionLocal() as db:
        library = db.get(Library, library_id)
        assert library is not None
        db.add(BookFile(library_id=library_id, path="Ada | Buch/barbuch.epub", format="epub", role="book", unit=unit,
                        size=1, mtime_ns=1))
        db.add(BookFile(library_id=library_id, path="Ada | Buch/metadata.json", format="json", role="sidecar",
                        size=1, mtime_ns=1))
        db.add(BookFile(library_id=library_id, path="Ada | Buch/cover.jpg", format="cover", role="sidecar",
                        size=1, mtime_ns=1))
        db.commit()
        beside = scanner._sidecars_for(db, library_id, unit)
        assert sorted(row.path for row in beside) == ["Ada | Buch/cover.jpg", "Ada | Buch/metadata.json"]

