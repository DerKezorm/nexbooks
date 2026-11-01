"""The check after the download: what is turned down, what is asked, what stays out, and never through a
link or over a file. Invented books only, made here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import BlocklistEntry, Download

from .bookmaker import make_epub, make_pdf
from .filing_helpers import (
    download_roots,
    ebook_world,
    editions,
    files_in,
    finish,
    german_epub,
    hidden_in,
    history,
    library_path,
    place_files,
    tar_of,
    the_download,
    zip_of,
)
from .follow_helpers import rows
from .library_helpers import can_link_files, make_link, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE

PLACED = "Rune Falkner/Nebelchor/02 - Die Laternenstraße"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def release(roots: Path, name: str = "release") -> Path:
    folder = roots / "complete" / name
    folder.mkdir(parents=True)
    return folder


def on_shelf(world: Any) -> list[str]:
    return [name for name in files_in(library_path(world)) if name.startswith(PLACED)]


def turned_down(code: str, reason: str) -> Download:
    row = rows(Download)[0]
    assert row.state == "rejected", (row.state, row.problem_code, row.failed_reason)
    assert row.failed_reason == code
    [block] = rows(BlocklistEntry)
    assert block.reason == reason
    assert history()[0].result == "rejected" and history()[0].note["code"] == code
    return row


def test_an_epub_and_a_pdf_put_the_epub_in_and_leave_the_pdf_out(operator: Any, net: FakeNetwork,
                                                                  roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder)
    make_pdf(folder / "Rune Falkner - Die Laternenstrasse.pdf", title=TITLE, author=AUTHOR)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert on_shelf(world) == [f"{PLACED}.epub"]
    assert {item.reason for item in place_files() if item.state == "drop"} == {"format_not_wanted"}


def test_a_release_of_a_format_that_is_not_ticked_is_turned_down(operator: Any, net: FakeNetwork,
                                                                 roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots)
    make_pdf(folder / "Rune Falkner - Die Laternenstrasse.pdf", title=TITLE, author=AUTHOR)
    finish(sab, job, folder)
    turned_down("format_not_wanted", "format_not_wanted")
    assert on_shelf(world) == []


def test_a_book_of_another_author_is_turned_down(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder, authors=("Ida Brandt",), title="Ein ganz anderes Buch")
    finish(sab, job, folder)
    turned_down("author_mismatch", "author_mismatch")
    assert on_shelf(world) == []


def test_the_same_title_by_another_author_than_the_release_named_is_a_question(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    """The name of the release said the author wished, the file says another with the title wished: a pen name, a
    translator or a reader where the author belongs, or another book. Two voices: a person decides."""
    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder, authors=("Ida Brandt",))
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "pen_name", (row.state, row.problem_code)
    assert row.problem_values["found"] == ["Ida Brandt"] and row.problem_values["wanted"] == [AUTHOR]
    assert on_shelf(world) == []
    blocked = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "block"})
    assert blocked.status_code == 200, blocked.text
    run_jobs()
    [block] = rows(BlocklistEntry)
    assert block.reason == "author_mismatch"
    assert on_shelf(world) == []


def test_the_same_title_by_another_author_in_a_release_that_names_none_is_another_book(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    from .procure_search_helpers import Item

    world, sab, job = ebook_world(net, Item("Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", size=2_000_000,
                                            categories=(7120,)))
    folder = release(roots)
    german_epub(folder, authors=("Ida Brandt",))
    finish(sab, job, folder)
    turned_down("author_mismatch", "author_mismatch")
    assert on_shelf(world) == []


def test_another_volume_of_the_series_is_turned_down(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder, title="Das Nebelhaus", series_pos=3.0)
    finish(sab, job, folder)
    turned_down("other_volume", "other_volume")


def test_a_file_of_the_author_with_another_title_asks_and_goes_in_when_taken(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder, title="Ein Titel aus dem Verlag", series=None, series_pos=None)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "files_unassigned"
    assert row.problem_values["title"] == "Ein Titel aus dem Verlag"
    assert on_shelf(world) == []
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "pick"})
    assert answer.status_code == 422
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    german = [edition for edition in editions() if edition.language == "de"]
    assert german and german[0].work_id == world.work_id


def test_a_bundle_takes_the_volume_wished_and_leaves_the_rest_out(operator: Any, net: FakeNetwork,
                                                                  roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots, "Rune.Falkner.Nebelchor.Band.1-3.GERMAN.EPUB")
    for number, title in ((1, "Der Anfang"), (2, TITLE), (3, "Das Ende")):
        make_epub(folder / f"0{number} {title}.epub", title=title, authors=(AUTHOR,), language="de", series=SERIES,
                  series_pos=float(number))
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert on_shelf(world) == [f"{PLACED}.epub"]
    assert len([item for item in place_files() if item.reason == "bundle_rest"]) == 2


def test_a_bundle_that_does_not_say_which_volume_is_which_asks_and_takes_the_one_picked(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots, "Rune.Falkner.Nebelchor.Band.1-2.GERMAN.EPUB")
    for number in (1, 2):
        make_epub(folder / f"Teil {number}.epub", title=f"Teil {number}", authors=(AUTHOR,), language="de")
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "bundle"
    units = row.problem_values["units"]
    assert [unit["title"] for unit in units] == ["Teil 1", "Teil 2"]
    assert all(len(unit["unit"]) == 16 and "/" not in unit["unit"] for unit in units)
    shown = world.client.get("/api/v1/procure/problems").json()["problems"][0]
    assert [unit["title"] for unit in shown["values"]["units"]] == ["Teil 1", "Teil 2"]
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide",
                               json={"action": "pick", "unit": "0" * 16})
    assert answer.status_code == 422
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide",
                               json={"action": "pick", "unit": units[1]["unit"]})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    assert on_shelf(world) == [f"{PLACED}.epub"]


def test_a_zip_is_unpacked_in_a_process_of_its_own_and_its_book_goes_in(operator: Any, net: FakeNetwork,
                                                                        roots: Path, tmp_path: Path) -> None:
    world, sab, job = ebook_world(net)
    made = german_epub(tmp_path)
    folder = release(roots)
    # A copy in the folders a Mac or a NAS adds is left out: it is no second book.
    zip_of(folder / "book.zip", {"inner/book.epub": made.read_bytes(), "inner/readme.txt": b"x",
                                 "@eaDir/book.epub": made.read_bytes()})
    finish(sab, job, folder)
    assert the_download().state == "imported", the_download().failed_reason
    assert on_shelf(world) == [f"{PLACED}.epub"]
    # The folder it was unpacked into is gone again.
    assert not hidden_in(library_path(world))


@pytest.mark.parametrize(("make", "code", "reason"), [
    ("encrypted", "archive_encrypted", "damaged"),
    ("bomb", "archive_too_large", "damaged"),
    ("climbs", "archive_unsafe", "dangerous"),
    ("tar_link", "archive_unsafe", "dangerous"),
    ("rar", "archive_unsupported", "archive_unsupported"),
    ("rar_and_zip", "archive_unsupported", "archive_unsupported"),
    ("broken", "archive_broken", "damaged"),
])
def test_an_archive_that_cannot_be_unpacked_safely_turns_the_release_down(operator: Any, net: FakeNetwork,
                                                                          roots: Path, tmp_path: Path, make: str,
                                                                          code: str, reason: str) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots)
    book = german_epub(tmp_path).read_bytes()
    if make == "encrypted":
        zip_of(folder / "book.zip", {"book.epub": book}, encrypted=True)
    elif make == "bomb":
        zip_of(folder / "book.zip", {"book.epub": b"\0" * (40 * 1024 * 1024)})
    elif make == "climbs":
        zip_of(folder / "book.zip", {"../../outside.epub": book})
    elif make == "tar_link":
        tar_of(folder / "book.tar.gz", {"book.epub": book}, link="link.epub")
    elif make == "rar":
        (folder / "book.rar").write_bytes(b"Rar!\x1a\x07\x00" + bytes(100))
    elif make == "rar_and_zip":
        (folder / "part.rar").write_bytes(b"Rar!\x1a\x07\x00" + bytes(100))
        zip_of(folder / "book.zip", {"book.epub": book})
    else:
        (folder / "book.zip").write_bytes(b"PK\x03\x04" + bytes(60))
    finish(sab, job, folder)
    turned_down(code, reason)
    assert on_shelf(world) == []
    assert not (roots / "outside.epub").exists() and not (roots / "complete" / "outside.epub").exists()
    assert not hidden_in(library_path(world))


def test_a_program_in_a_release_turns_it_down_whatever_else_it_holds(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder)
    # Also in a folder that is left out of the books (hidden, a NAS's own): looked for everywhere.
    (folder / ".extras").mkdir()
    (folder / ".extras" / "install.exe").write_bytes(b"MZ")
    finish(sab, job, folder)
    turned_down("dangerous", "dangerous")
    assert on_shelf(world) == []


def test_a_link_in_a_release_is_never_followed(operator: Any, net: FakeNetwork, roots: Path, tmp_path: Path) -> None:
    if not can_link_files():
        pytest.skip("this system makes no links (no developer mode on Windows)")
    world, sab, job = ebook_world(net)
    outside = tmp_path / "secret"
    outside.mkdir()
    german_epub(outside, name="secret.epub")
    folder = release(roots)
    make_link(folder / "book.epub", outside / "secret.epub")
    finish(sab, job, folder)
    turned_down("no_book", "not_a_book")
    assert on_shelf(world) == [] and (outside / "secret.epub").exists()


def test_a_file_at_the_goal_is_never_written_over(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = ebook_world(net)
    shelf = library_path(world)
    (shelf / "Rune Falkner" / "Nebelchor").mkdir(parents=True)
    (shelf / f"{PLACED}.epub").write_bytes(b"somebody's own file")
    folder = release(roots)
    german_epub(folder)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert (shelf / f"{PLACED}.epub").read_bytes() == b"somebody's own file"
    assert (shelf / f"{PLACED} (2).epub").exists()


def test_a_library_that_takes_no_file_is_a_problem_that_goes_on_once_it_does(operator: Any, net: FakeNetwork,
                                                                             roots: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import formats

    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder)
    real = formats.probe_library
    monkeypatch.setattr(formats, "probe_library", lambda path: {"writable": False, "reason": "read_only",
                                                                "hardlink": None})
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "no_write"
    monkeypatch.setattr(formats, "probe_library", real)
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck")
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    assert on_shelf(world) == [f"{PLACED}.epub"]


def test_a_library_that_takes_no_file_can_be_swapped_for_another_and_the_book_goes_there(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import formats

    from .library_helpers import books_folder, join, make_library

    world, sab, job = ebook_world(net)
    other = books_folder("other")
    second = make_library(other, "Other")
    join(second, world.person.id, "acquire")
    shelf = str(library_path(world))
    real = formats.probe_library
    monkeypatch.setattr(formats, "probe_library", lambda path: {"writable": False, "reason": "read_only",
                                                                "hardlink": None} if path == shelf else real(path))
    folder = release(roots)
    german_epub(folder)
    finish(sab, job, folder)
    row = the_download()
    assert row.problem_code == "no_write"
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/library", json={"library_id": second})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported" and the_download().library_id == second
    assert [name for name in files_in(other) if name.endswith(".epub")] == [f"{PLACED}.epub"]
    assert on_shelf(world) == []


def test_a_download_moved_to_another_library_is_planned_anew_there(operator: Any, net: FakeNetwork, roots: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import placing

    from .library_helpers import books_folder, join, make_library

    world, sab, job = ebook_world(net)
    other = books_folder("other")
    (other / "Rune Falkner" / "Nebelchor").mkdir(parents=True)
    (other / f"{PLACED}.epub").write_bytes(b"somebody's own file")
    second = make_library(other, "Other")
    join(second, world.person.id, "acquire")
    shelf = library_path(world)
    real = placing.place

    def refuse_in_the_shelf(source: Any, size: int, library: Path, target: str, *, how: str) -> str:
        if Path(library) == shelf:
            raise placing.PlaceError("no_write")
        return real(source, size, library, target, how=how)

    monkeypatch.setattr(placing, "place", refuse_in_the_shelf)
    folder = release(roots)
    german_epub(folder)
    finish(sab, job, folder)
    row = the_download()
    assert row.problem_code == "no_write" and row.place_step == "checked"
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/library", json={"library_id": second})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    assert (other / f"{PLACED}.epub").read_bytes() == b"somebody's own file"
    assert (other / f"{PLACED} (2).epub").exists()


def test_a_filing_stopped_in_the_middle_goes_on_from_what_lies_on_the_disk(operator: Any, net: FakeNetwork,
                                                                           roots: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import jobs
    from app.services.procure import importing, placing

    world, sab, job = ebook_world(net)
    folder = release(roots)
    german_epub(folder)
    # (Another text: two files the same to the byte would be one, ``duplicates.tidy``.)
    make_epub(folder / "Rune Falkner - Die Laternenstrasse.mobi.epub", title=TITLE, authors=(AUTHOR,),
              language="de", series=SERIES, series_pos=2.0, description="Eine andere Datei desselben Buchs.")
    real = placing.place
    calls: list[str] = []

    def stop_after_first(*args: Any, **kwargs: Any) -> str:
        if calls:
            jobs.stopping.set()
            raise jobs.Interrupted
        calls.append("one")
        return real(*args, **kwargs)

    monkeypatch.setattr(placing, "place", stop_after_first)
    try:
        finish(sab, job, folder)
    finally:
        jobs.stopping.clear()
    row = the_download()
    assert row.state == "completed" and row.place_step == "checked"
    assert len([item for item in place_files() if item.state == "placed"]) == 1
    monkeypatch.setattr(placing, "place", real)
    with SessionLocal() as db:
        importing.request_due(db)
    run_jobs()
    assert the_download().state == "imported"
    names = on_shelf(world)
    assert len(names) == 2 and not hidden_in(library_path(world))
