"""More of checking and filing (invented books only, made here): a sample is no book, a book about the
book is asked about, a copy that stopped leaves nothing behind, the helper that unpacks (a bomb of many small files, a
link, a name twice, too many files), every program ending, and the small rules of placing and uploading that nothing
held before."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download

from .bookmaker import make_epub
from .filing_helpers import (
    download_roots,
    ebook_world,
    files_in,
    finish,
    german_epub,
    hidden_in,
    library_path,
    tar_of,
    the_download,
    world_with,
    zip_of,
)
from .follow_helpers import rows
from .library_helpers import books_folder, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE


@pytest.fixture(autouse=True)
def nothing_on_its_way() -> Any:
    from app.services.procure import uploads

    uploads._in_flight.clear()
    yield
    uploads._in_flight.clear()


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def other_book(folder: Path) -> None:
    make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch", authors=("Ida Brandt",), language="de")


def begin(client: Any, library_id: int, **body: Any) -> dict[str, Any]:
    answer = client.post("/api/v1/uploads", json={"library_id": library_id, **body})
    assert answer.status_code == 201, answer.text
    return answer.json()  # type: ignore[no-any-return]


def send(client: Any, upload_id: int, path: str, data: bytes) -> Any:
    return client.put(f"/api/v1/uploads/{upload_id}/files", params={"path": path}, content=data,
                      headers={"content-type": "application/octet-stream"})


BASE: dict[str, Any] = {"title": TITLE, "authors": (AUTHOR,), "language": "de", "series": SERIES, "series_pos": 2.0}


# --- A sample is no book, a book about the book is asked about -----------------------------------------------------------


@pytest.mark.parametrize("title", [TITLE + " (Leseprobe)", TITLE + " - Leseprobe", TITLE + " - Probekapitel"])
def test_a_sample_is_turned_down_and_its_release_blocked(operator: Any, net: FakeNetwork, roots: Path,
                                                         title: str) -> None:
    from app.models import BlocklistEntry

    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    make_epub(folder / "Book.epub", **{**BASE, "title": title})
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "rejected" and row.failed_reason == "sample", (row.state, row.problem_code, row.failed_reason)
    assert [entry.reason for entry in rows(BlocklistEntry)] == ["sample"]
    assert not [name for name in files_in(library_path(world)) if "Nebelchor" in name]


@pytest.mark.parametrize("title", [TITLE + ": Das Making-of", TITLE + " - Das Begleitbuch", TITLE + " Study Guide"])
def test_a_book_about_the_book_is_asked_about_not_filed(operator: Any, net: FakeNetwork, roots: Path, title: str) -> None:
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    make_epub(folder / "Book.epub", **{**BASE, "title": title})
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "files_unassigned", (row.state, row.problem_code)
    assert not [name for name in files_in(library_path(world)) if "Nebelchor" in name]
    # The person may say it is the book: then it is filed.
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"}).status_code == 200
    run_jobs()
    assert the_download().state == "imported"


def test_the_words_of_a_sample_and_of_a_companion_are_found_in_a_title_not_in_an_ordinary_one() -> None:
    from app.services.procure import release_name

    assert release_name.sample_word("Die Leseprobe") and release_name.companion_word("Das Making-of")
    assert not release_name.sample_word("Die Laternenstraße") and not release_name.companion_word("Nebelchor 2")


# --- A copy that stopped for good leaves nothing behind --------------------------------------------------------------


def test_a_partial_file_of_a_copy_that_stopped_goes_when_the_download_is_taken_away(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import placing

    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    german_epub(folder)
    shelf = library_path(world)
    real = placing.place

    def dying_copy(source: Path, size: int, library: Path, target: str, *, how: str) -> str:
        goal = library.joinpath(*target.split("/"))
        goal.parent.mkdir(parents=True, exist_ok=True)
        (goal.parent / f".{goal.name[:60]}.abcd1234{placing.PARTIAL}").write_bytes(b"half")
        (goal.parent / f".somebody-elses.abcd1234{placing.PARTIAL}").write_bytes(b"not ours")
        raise placing.PlaceError("failed")

    monkeypatch.setattr(placing, "place", dying_copy)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "place_failed"
    assert len(hidden_in(shelf)) == 2
    monkeypatch.setattr(placing, "place", real)
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={"block": False})
    assert answer.status_code == 200, answer.text
    # Its own partial file went, the one of another goal was not touched.
    left = hidden_in(shelf)
    assert len(left) == 1 and Path(left[0]).name.startswith(".somebody-elses.")


def test_only_the_partial_files_of_the_goal_itself_are_cleared(tmp_path: Path) -> None:
    from app.services.procure import placing

    folder = tmp_path / "lib" / "A" / "B"
    folder.mkdir(parents=True)
    ours = folder / f".Book.epub.1a2b3c4d{placing.PARTIAL}"
    theirs = folder / f".Other.epub.1a2b3c4d{placing.PARTIAL}"
    plain = folder / "Book.epub"
    for path in (ours, theirs, plain):
        path.write_bytes(b"x")
    placing.clear_own_partials(tmp_path / "lib", "A/B/Book.epub")
    assert not ours.exists() and theirs.exists() and plain.exists()
    # A folder that does not exist, or the name of a file, is nothing to clear.
    placing.clear_own_partials(tmp_path / "lib", "A/nowhere/Book.epub")
    placing.clear_own_partials(tmp_path / "lib", "A/B/Book.epub/x.epub")


# --- The unpacking helper: a bomb of many small files, a link, a name twice, too many files ----------------------------


def run_worker(kind: str, archive: Path, folder: Path, max_bytes: int = 10**9, max_files: int = 100) -> dict[str, Any]:
    from app.services.procure import unpack_worker

    folder.mkdir(exist_ok=True)
    return unpack_worker.run([kind, str(archive), str(folder), str(max_bytes), str(max_files)])


def test_many_small_files_packed_very_tight_are_a_bomb_as_well(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import unpack_worker

    # Each file is far below the size a single file is looked at for; together they are many times the archive.
    monkeypatch.setattr(unpack_worker, "RATIO_FREE_BYTES", 10**9)
    monkeypatch.setattr(unpack_worker, "TOTAL_FREE_BYTES", 1000)
    members = {f"{number:03d}.epub": b"\0" * 20_000 for number in range(30)}
    archive = zip_of(tmp_path / "bomb.zip", members)
    assert archive.stat().st_size * unpack_worker.MAX_TOTAL_RATIO < 30 * 20_000
    assert run_worker("zip", archive, tmp_path / "out") == {"ok": False, "reason": "too_large"}
    packed = tar_of(tmp_path / "bomb.tar.gz", members)
    assert run_worker("tar", packed, tmp_path / "out2") == {"ok": False, "reason": "too_large"}
    # An archive that is not packed tight is no bomb, however many files it holds.
    monkeypatch.setattr(unpack_worker, "MAX_TOTAL_RATIO", 10**6)
    assert run_worker("zip", archive, tmp_path / "out3")["ok"] is True
    assert run_worker("tar", packed, tmp_path / "out4")["ok"] is True


def test_an_archive_of_ordinary_books_is_not_a_bomb(tmp_path: Path) -> None:
    import random

    noise = random.Random(7)
    archive = zip_of(tmp_path / "books.zip", {f"{number}.mp3": noise.randbytes(40_000) for number in range(5)})
    answer = run_worker("zip", archive, tmp_path / "out")
    assert answer["ok"] is True and answer["files"] == 5


def test_a_link_in_a_zip_is_refused_not_written_as_a_file(tmp_path: Path) -> None:
    import stat
    import zipfile

    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as made:
        info = zipfile.ZipInfo("book.epub")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        made.writestr(info, "/etc/passwd")
    assert run_worker("zip", archive, tmp_path / "out") == {"ok": False, "reason": "unsafe"}
    assert not list((tmp_path / "out").iterdir())


def test_a_name_that_comes_twice_in_an_archive_is_refused_never_written_over(tmp_path: Path) -> None:
    import warnings
    import zipfile

    archive = tmp_path / "twice.zip"
    with zipfile.ZipFile(archive, "w") as made, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        made.writestr("a.epub", b"the first")
        made.writestr("a.epub", b"the second")
    assert run_worker("zip", archive, tmp_path / "out")["ok"] is False
    assert (tmp_path / "out" / "a.epub").read_bytes() == b"the first"


def test_an_archive_with_more_files_than_allowed_is_refused_while_it_is_written(tmp_path: Path) -> None:
    archive = zip_of(tmp_path / "many.zip", {f"{number}.mp3": b"x" * 10 for number in range(5)})
    assert run_worker("zip", archive, tmp_path / "out", max_files=3) == {"ok": False, "reason": "too_many"}
    assert run_worker("zip", archive, tmp_path / "out2", max_files=5)["ok"] is True


def test_the_marks_some_systems_do_not_take_are_swapped_and_a_control_character_is_refused() -> None:
    from app.services.procure import unpack_worker

    assert unpack_worker.safe_parts('CD 1/Wer? Wie: "Warum" <1> a|b*.mp3') == ["CD 1", "Wer_ Wie_ _Warum_ _1_ a_b_.mp3"]
    with pytest.raises(unpack_worker.Refused):
        unpack_worker.safe_parts("a\x07b.mp3")
    with pytest.raises(unpack_worker.Refused):
        unpack_worker.safe_parts("../a.mp3")


# --- Programs and scripts: every ending the list names ---------------------------------------------------------------


@pytest.mark.parametrize("ending", ["exe", "bat", "cmd", "com", "scr", "pif", "lnk", "ps1", "psm1", "vbs", "vbe", "wsf",
                                    "wsh", "msi", "msp", "jar", "sh", "bash", "dll", "cpl", "hta", "reg", "zipx",
                                    "arj", "lzh", "apk", "app", "desktop", "iso", "img"])
def test_every_program_or_script_ending_refuses_a_release_even_in_a_hidden_folder(tmp_path: Path, ending: str) -> None:
    from app.services.procure import incoming

    (tmp_path / ".extras").mkdir()
    (tmp_path / ".extras" / f"setup.{ending}").write_bytes(b"x")
    (tmp_path / "book.epub").write_bytes(b"x")
    assert incoming.walk(tmp_path).dangerous == [f".extras/setup.{ending}"]
    assert incoming.walk(tmp_path, "book.epub").dangerous == []


# --- A job folder is removed only below a download root --------------------------------------------------------------


def test_the_folder_of_a_filed_job_is_left_alone_when_it_lies_outside_the_download_roots(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    # A folder in the place for libraries, but of no library: nexbooks may read it, never clean it up.
    folder = books_folder("releases") / "rel"
    folder.mkdir(parents=True)
    german_epub(folder)
    (folder / "release.nfo").write_text("x", encoding="utf-8")
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert folder.is_dir() and (folder / "release.nfo").is_file()


# --- A link on the way to the goal ---------------------------------------------------------------------------------


def test_a_link_on_the_way_to_a_goal_is_refused_when_the_folders_are_made(tmp_path: Path) -> None:
    from app.services.procure import placing

    from .library_helpers import can_link_files, make_link

    if not can_link_files():
        pytest.skip("this system makes no links (no developer mode on Windows): the Linux run decides")
    outside = tmp_path / "outside"
    outside.mkdir()
    library = tmp_path / "lib"
    library.mkdir()
    make_link(library / "Author", outside)
    with pytest.raises(placing.PlaceError) as refused:
        placing._make_dirs(library, "Author/Book")
    assert refused.value.code == "link"
    assert not (outside / "Book").exists()


def test_a_junction_on_the_way_to_a_goal_is_refused_too(tmp_path: Path) -> None:
    from app.services.procure import placing

    if sys.platform != "win32":
        pytest.skip("a junction is a Windows thing; the test of a link above is the one for Linux")
    outside = tmp_path / "outside"
    outside.mkdir()
    library = tmp_path / "lib"
    library.mkdir()
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(library / "Author"), str(outside)], capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip("no junction could be made here")
    try:
        with pytest.raises(placing.PlaceError) as refused:
            placing._make_dirs(library, "Author/Book")
        assert refused.value.code == "link"
        assert not (outside / "Book").exists()
    finally:
        os.rmdir(library / "Author")


# --- Placing: the partial files, the room, never over a name --------------------------------------------------------------


def test_a_leftover_partial_file_in_the_folder_of_a_goal_goes_before_the_filing(operator: Any, net: FakeNetwork,
                                                                                roots: Path) -> None:
    from app.services.procure import placing

    world, sab, job = ebook_world(net)
    goal_folder = library_path(world) / "Rune Falkner" / "Nebelchor"
    goal_folder.mkdir(parents=True)
    (goal_folder / f".old.epub.1a2b3c4d{placing.PARTIAL}").write_bytes(b"half a book")
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert not hidden_in(library_path(world))


def test_a_copy_without_room_is_refused_before_a_byte_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno
    import shutil
    from collections import namedtuple

    from app.services.procure import placing

    source = tmp_path / "out" / "a.epub"
    source.parent.mkdir()
    source.write_bytes(b"x" * 1000)
    library = tmp_path / "lib"
    library.mkdir()

    def no_link(*_args: Any) -> None:
        raise OSError(errno.EXDEV, "another disk")

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10**9, 10**9, placing.RESERVE + 999))
    with pytest.raises(placing.PlaceError) as refused:
        placing.place(source, 1000, library, "A/a.epub", how="move")
    assert refused.value.code == "no_space" and source.is_file()
    assert not [path for path in library.rglob("*") if path.is_file()]
    # With room it is copied, and the old name goes.
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10**9, 0, placing.RESERVE + 1000))
    assert placing.place(source, 1000, library, "A/a.epub", how="move") == "copy"
    assert (library / "A" / "a.epub").read_bytes() == b"x" * 1000 and not source.exists()


def test_giving_a_name_never_replaces_a_file_that_has_it(tmp_path: Path) -> None:
    from app.services.procure import placing

    source = tmp_path / "new.epub"
    source.write_bytes(b"the new one")
    target = tmp_path / "taken.epub"
    target.write_bytes(b"somebody's own")
    with pytest.raises(placing.PlaceError) as refused:
        placing._name(source, target)
    assert refused.value.code == "taken"
    assert target.read_bytes() == b"somebody's own" and source.read_bytes() == b"the new one"
    free = tmp_path / "free.epub"
    placing._name(source, free)
    assert free.read_bytes() == b"the new one" and not source.exists()


def test_a_partial_file_is_hidden_and_never_taken_for_a_book(tmp_path: Path) -> None:
    from app.services.procure import incoming, placing

    partial = placing._partial(tmp_path, "Die Laternenstraße.epub")
    assert partial.name.startswith(".") and partial.name.endswith(placing.PARTIAL)
    partial.write_bytes(b"half")
    (tmp_path / "book.epub").write_bytes(b"x")
    assert [item.path for item in incoming.walk(tmp_path).books] == ["book.epub"]
    assert len(placing._partial(tmp_path, "x" * 400).name) < 120


# --- Uploads: the size before the first byte, only an open upload is dropped; the operator alone assigns -------------------


def test_the_declared_size_of_a_file_is_held_against_the_limit_before_a_byte_is_read(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import uploads

    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    monkeypatch.setattr(uploads, "MAX_EBOOK_BYTES", 100)
    with SessionLocal() as db:
        with pytest.raises(Exception) as refused:
            uploads.prepare(db, world.person, upload["id"], "big.epub", 101)
        assert getattr(refused.value, "status_code", None) == 413
        target, allowed = uploads.prepare(db, world.person, upload["id"], "fits.epub", 100)
        uploads.release(target)
        assert allowed == 100


def test_an_upload_that_is_filed_cannot_be_dropped_and_its_files_stay(operator: Any, net: FakeNetwork,
                                                                      tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    data = make_epub(tmp_path / "m.epub", title=TITLE, authors=(AUTHOR,), language="de").read_bytes()
    send(world.client, upload["id"], "a.epub", data)
    assert world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={}).status_code == 202
    answer = world.client.delete(f"/api/v1/uploads/{upload['id']}")
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "upload_closed"
    with SessionLocal() as db:
        assert db.scalar(select(Download.id).where(Download.id == upload["id"])) == upload["id"]
    run_jobs()
    assert world.client.get(f"/api/v1/uploads/{upload['id']}").json()["phase"] == "done"
    assert world.client.delete(f"/api/v1/uploads/{upload['id']}").status_code == 409


def test_a_person_without_the_operator_role_cannot_give_a_job_to_a_wish(operator: Any, net: FakeNetwork) -> None:
    world = world_with(net, other_book)
    answer = world.client.post("/api/v1/procure/foreign/1/assign", json={"wish_id": 1})
    # Refused at the door (the operator's routes), before the service says the same a second time.
    assert answer.status_code == 403 and answer.json()["detail"]["code"] == "operator_only", answer.text



# --- What decides whether a recording that comes betters one the library has ---------------------------------------------


def test_a_better_format_betters_only_the_same_version_in_doubt_it_stands_beside() -> None:
    from app.services.procure import checking

    have = checking.Have(fmt="mp3_128", variant="unabridged", language="de", narrators=["Anna Beispiel"], seconds=1000.0)

    def same(**changes: Any) -> bool:
        values: dict[str, Any] = {"variant": "unabridged", "language": "de", "narrators": ["Anna Beispiel"],
                                  "seconds": 1010.0}
        values.update(changes)
        return checking.same_version("audio", have, **values)

    assert same()
    assert same(language="de-DE") and same(language="") and same(narrators=[]) and same(narrators=["Beispiel, Anna"])
    assert not same(variant="abridged") and not same(variant="dramatized")
    assert not same(language="en")
    assert not same(narrators=["Bernd Anderer"])
    assert not same(seconds=1100.0) and not same(seconds=900.0)
    assert not same(seconds=0.0)
    assert not checking.same_version("audio", checking.Have(fmt="mp3_128", seconds=0.0), variant="unabridged",
                                     seconds=1000.0)
    # An e-book has no version, no reader and no running time: its language is all there is.
    book = checking.Have(fmt="pdf", language="de")
    assert checking.same_version("ebook", book, language="de") and checking.same_version("ebook", book)
    assert not checking.same_version("ebook", book, language="en")


def test_the_room_of_a_file_on_its_way_is_given_back_when_it_has_come_or_failed(operator: Any, net: FakeNetwork,
                                                                                tmp_path: Path) -> None:
    from app.services.procure import uploads

    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    data = make_epub(tmp_path / "m.epub", title=TITLE, authors=(AUTHOR,), language="de").read_bytes()
    assert send(world.client, upload["id"], "a.epub", data).status_code == 201
    assert uploads._in_flight == {}
    assert send(world.client, upload["id"], "b.epub", b"").status_code == 422
    assert uploads._in_flight == {}
    assert send(world.client, upload["id"], "a.epub", data).status_code == 409
    assert uploads._in_flight == {}
