"""A scan of a folder somebody wants to harm the server with: nothing it holds takes the scan, the server or the data
folder down, and what is good in the folder still arrives."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import BookFile, LibraryProblem
from app.services import jobs, probe, scanner
from app.services.readers import limits

from .bookmaker import cover_image, make_epub, make_mp3
from .library_helpers import age, book, books_folder, make_library, make_link, scan
from .test_scanner import state, titles


def problems() -> dict[str, str]:
    with SessionLocal() as db:
        return {p.path: p.reason for p in db.scalars(select(LibraryProblem))}


def test_a_file_that_hangs_or_crashes_the_reader_is_listed_and_the_scan_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    book(folder, "a-good.epub", title="Good One", authors=("Ada Feldkamp",))
    (folder / "hang.epub").write_bytes(b"x" * 100)
    (folder / "crash.mp3").write_bytes(b"x" * 100)
    book(folder, "z-good.epub", title="Good Two", authors=("Ada Feldkamp",))
    age(folder / "hang.epub")
    age(folder / "crash.mp3")
    library_id = make_library(folder)

    # The file that hangs is given up on by the clock of the helper's waiting, which this test moves by hand when that
    # file is being read: the time the machine takes to start a helper and read a good book (seconds, under load) is no
    # part of what is tested. The limit for the others is long, so that a busy machine never takes it for a hang.
    reading = {"hang": False, "ticks": 0}
    real_clock = clock.monotonic

    def moved() -> float:
        if not reading["hang"]:
            return real_clock()
        reading["ticks"] += 1
        return real_clock() + 10_000.0 * reading["ticks"]

    real_read = probe.Worker.read

    def read(self: probe.Worker, root: str, relative: str, fmt: str) -> dict[str, object]:
        reading["hang"], reading["ticks"] = "hang" in relative, 0
        try:
            return real_read(self, root, relative, fmt)
        finally:
            reading["hang"] = False

    monkeypatch.setattr(clock, "monotonic", moved)
    monkeypatch.setattr(probe.Worker, "read", read)

    class Own:
        def __init__(self) -> None:
            self.worker = probe.Worker(reader="tests.probe_readers:read_hostile", timeout=600,
                                       stop=jobs.stopping.is_set)

        def __enter__(self) -> probe.Worker:
            return self.worker

        def __exit__(self, *_exc: object) -> None:
            self.worker.close()

    monkeypatch.setattr(scanner, "Worker", lambda **_kwargs: Own())
    row = scan(library_id)
    assert titles() == ["Good One", "Good Two"]  # the one after the hang, too
    assert problems() == {"hang.epub": "timeout", "crash.mp3": "crashed"}
    assert row.report["problems"] == 2 and row.report["aborted"] is None


def test_bombs_and_lies_in_a_folder_are_problems_and_the_good_books_arrive(tmp_path: Path) -> None:
    folder = books_folder()
    book(folder, "good/good.epub", title="Good", authors=("Ada Feldkamp",))
    # An EPUB that claims 70 MB and weighs a few KB.
    book(folder, "bomb/bomb.epub", extra_entries={"OEBPS/zeros.bin": bytes(70 * 1024 * 1024)})
    # Entities that expand into entities, and one that reaches for a file.
    book(folder, "laughs/l.epub", doctype='<!DOCTYPE package [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>',
         extra_opf="<dc:subject>&b;</dc:subject>")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    book(folder, "xxe/x.epub", doctype=f'<!DOCTYPE package [<!ENTITY x SYSTEM "{secret.as_uri()}">]>',
         extra_opf="<dc:subject>&x;</dc:subject>")
    # An EPUB cut off, a PDF that is not one, an MP3 of noise, a sidecar that is not JSON, a huge one.
    cut = make_epub(folder / "cut/c.epub")
    cut.write_bytes(cut.read_bytes()[:-50])
    age(cut)
    for name in ("cut/c.pdf", "cut/n.mp3", "cut/n.m4b"):
        (folder / name).write_bytes(os.urandom(2000))
        age(folder / name)
    (folder / "cut/metadata.json").write_text("{not json", encoding="utf-8")
    (folder / "bomb/metadata.json").write_text('{"title": "' + "x" * (limits.SIDECAR_MAX_BYTES + 5) + '"}',
                                              encoding="utf-8")
    age(folder / "cut/metadata.json")
    age(folder / "bomb/metadata.json")
    # A cover too small to be one: the book is read, it has no cover.
    book(folder, "pixels/p.epub", title="No Cover", authors=("Ada Feldkamp",), cover=cover_image(size=(4, 6)),
         cover_style="property")
    row = scan(make_library(folder))
    assert sorted(titles()) == ["Good", "No Cover"]
    found = problems()
    assert found["bomb/bomb.epub"] == "zip_bomb" and found["laughs/l.epub"] == "unreadable"
    assert found["xxe/x.epub"] == "unreadable" and found["cut/c.epub"] == "unreadable"
    assert found["cut/c.pdf"] == found["cut/n.mp3"] == found["cut/n.m4b"] == "unreadable"
    assert found["cut/metadata.json"] == "unreadable" and found["bomb/metadata.json"] == "too_large"
    assert row.report["aborted"] is None
    no_cover = next(w for w in state()["works"] if w["title"] == "No Cover")
    assert no_cover["cover"] == ""
    # Nothing the files held was read into the database or onto the disk.
    with SessionLocal() as db:
        assert "TOPSECRET" not in json.dumps([f.probe for f in db.scalars(select(BookFile))])
    assert not any("TOPSECRET" in p.read_text(errors="ignore") for p in Path(get_settings().data_dir).rglob("*.log"))


def test_a_library_whose_folder_became_a_link_to_somewhere_else_is_not_read() -> None:
    folder = books_folder("honest")
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    elsewhere = Path(tempfile.mkdtemp(prefix="nexbooks-elsewhere-"))
    try:
        make_epub(elsewhere / "spy.epub", title="Spy", authors=("Ada Feldkamp",))
        age(elsewhere / "spy.epub")
        shutil.rmtree(folder)
        make_link(folder, elsewhere)
        row = scan(library_id)
        assert row.report["aborted"] == "offline"
        assert titles() == ["A"]
        with SessionLocal() as db:
            assert all(f.missing_at is None for f in db.scalars(select(BookFile)))
    finally:
        if folder.is_symlink() or folder.is_junction():
            try:
                folder.unlink()
            except OSError:
                folder.rmdir()
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_a_library_that_lies_outside_the_roots_of_today_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = books_folder()
    book(folder, "a.epub", title="A", authors=("Ada Feldkamp",))
    library_id = make_library(folder)
    scan(library_id)
    monkeypatch.setattr(get_settings(), "library_roots", str(Path(tempfile.gettempdir()) / "somewhere-else"))
    assert scan(library_id).report["aborted"] == "offline"
    assert titles() == ["A"]


def test_the_walk_asks_is_link_of_every_entry_and_lists_what_it_skips(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A link the system of the test cannot make is faked: whatever ``is_link`` says is a link is skipped and told."""
    (tmp_path / "keep.epub").write_bytes(b"x")
    (tmp_path / "linked.epub").write_bytes(b"xx")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "inner.epub").write_bytes(b"x")
    (tmp_path / "linkdir").mkdir()
    (tmp_path / "linkdir" / "inside.epub").write_bytes(b"x")
    walked = scanner.walk(tmp_path, "both")
    assert sorted(f.path for f in walked.found) == ["folder/inner.epub", "keep.epub", "linkdir/inside.epub",
                                                    "linked.epub"]
    real = scanner.is_link

    def fake(info: os.stat_result) -> bool:
        # The file of two bytes and the one folder that is empty of size on this system are the "links".
        return real(info) or info.st_size == 2

    monkeypatch.setattr(scanner, "is_link", fake)
    walked = scanner.walk(tmp_path, "both")
    assert "linked.epub" not in [f.path for f in walked.found] and walked.links == ["linked.epub"]


def test_a_name_that_cannot_be_written_in_utf8_is_made_printable() -> None:
    assert scanner._printable("good" + chr(0xDC80) + "name") == "good?name"
    assert scanner._printable("fine") == "fine"


def test_a_great_many_chapters_are_cut_not_stored() -> None:
    folder = books_folder()
    path = make_mp3(folder / "chapters.mp3", 2.0, album="Many", artist="Ada Feldkamp",
                    chapters=[(f"c{n}", n * 0.001) for n in range(400)])
    age(path)
    scan(make_library(folder))
    (work,) = state()["works"]
    assert len(work["editions"][0]["chapters"]) <= limits.AUDIO_MAX_CHAPTERS
