"""What nexbooks takes away after filing a Usenet job, one program or step that breaks never holds the round up, the
hidden folders of nexbooks in a library that are links, and deleting for good only in the browser. Only invented names, example.com, keys made at run time."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import BookFile, Download, DownloadClient, Library, TrashEntry

from .conftest import make_account, new_client
from .filing_helpers import download_roots, ebook_world, finish, german_epub, the_download
from .follow_helpers import BOOK, audio_world, downloads, qbittorrent, round_of_tracking, sabnzbd, wish
from .library_helpers import book, books_folder, join, make_library, make_link, run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def notes_of(client: TestClient) -> list[dict[str, Any]]:
    answer = client.get("/api/v1/procure/problems")
    assert answer.status_code == 200, answer.text
    return answer.json()["notes"]  # type: ignore[no-any-return]


# --- After the filing nexbooks takes away what it knows, never more -------------------------------------------


def test_a_job_reported_in_the_category_folder_itself_leaves_everything_else_there(operator: Any, net: FakeNetwork,
                                                                                  roots: Path) -> None:
    """SABnzbd names the category folder as the storage of the job (the book lies in it directly). What else lies
    there (another program's film, another job's rest) stays, and the person is told."""
    world, sab, job = ebook_world(net)
    shared = roots / "complete"
    shared.mkdir(parents=True, exist_ok=True)
    german_epub(shared)
    film = shared / "Some.Film.2020"
    film.mkdir()
    (film / "film.mkv").write_bytes(b"x" * 1000)
    (shared / "notes.txt").write_text("keep", encoding="utf-8")
    (shared / "other.nfo").write_text("another job's", encoding="utf-8")
    finish(sab, job, shared)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code)
    assert (film / "film.mkv").is_file() and (shared / "notes.txt").is_file() and (shared / "other.nfo").is_file()
    assert notes_of(world.client) == []


def test_a_folder_not_named_like_the_job_is_not_tidied(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "Some.Other.Release"
    folder.mkdir(parents=True)
    german_epub(folder)
    (folder / "release.nfo").write_text("x", encoding="utf-8")
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert (folder / "release.nfo").is_file()
    assert notes_of(world.client) == []


def test_in_the_jobs_own_folder_only_its_rests_go_and_strangers_stay(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / BOOK
    (folder / "Sample").mkdir(parents=True)
    german_epub(folder)
    for name in ("release.nfo", "release.par2", "release.vol00+01.par2", "release.sfv", "release.srr", "cover.jpg",
                 "release.r00", "Sample/sample.jpg"):
        (folder / name).write_bytes(b"x")
    (folder / "film.mkv").write_bytes(b"y" * 100)
    (folder / "readme.txt").write_text("somebody's", encoding="utf-8")
    finish(sab, job, folder)
    assert the_download().state == "imported"
    left = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))
    assert left == ["film.mkv", "readme.txt"], left
    assert notes_of(world.client) == []


def test_the_jobs_own_folder_goes_when_nothing_else_is_in_it(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = ebook_world(net)
    # SABnzbd gives a second job of the same name ".1".
    folder = roots / "complete" / f"{BOOK}.1"
    folder.mkdir(parents=True)
    german_epub(folder)
    (folder / "release.nfo").write_text("x", encoding="utf-8")
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert not folder.exists() and (roots / "complete").is_dir()


def test_a_link_in_the_jobs_folder_keeps_the_folder_and_what_it_leads_to(operator: Any, net: FakeNetwork,
                                                                         roots: Path, tmp_path: Path) -> None:
    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / BOOK
    folder.mkdir(parents=True)
    german_epub(folder)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.nfo").write_text("x", encoding="utf-8")
    make_link(folder / "extra", elsewhere)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    assert (elsewhere / "keep.nfo").is_file() and folder.is_dir()


def test_the_category_is_checked_at_every_hand_over(operator: Any, net: FakeNetwork) -> None:
    """Job folders switched off in SABnzbd after the program was saved: nothing is handed over."""
    sab = sabnzbd(net)
    sab.categories.append({"name": "nexbooks", "dir": "books*", "pp": "3"})
    world = audio_world(net, Item(BOOK, size=2_000_000, categories=(7120,)))
    wish(world, kinds=("ebook",), language="de")
    run_jobs()
    assert sab.jobs == {} and downloads() == []
    [shown] = world.client.get("/api/v1/procure/wishes").json()["wishes"]
    assert (shown["detail"] or {}).get("load_error") == "client_category_unusable", shown


def test_the_names_of_a_job_folder() -> None:
    from app.services.procure import importing

    assert importing.named_like_job(BOOK, BOOK)
    assert importing.named_like_job(BOOK.replace(".", " "), BOOK)
    assert importing.named_like_job(f"{BOOK}.1", BOOK)
    assert importing.named_like_job(BOOK[:30], BOOK)
    assert not importing.named_like_job("complete", BOOK)
    assert not importing.named_like_job("nexbooks", BOOK)
    assert not importing.named_like_job("Rune", BOOK)
    assert not importing.named_like_job("Some.Other.Release", BOOK)
    for name in ("a.nfo", "a.PAR2", "a.vol01+02.par2", "a.sfv", "a.nzb", "a.srr", "a.jpg", "a.r01", "a.part02.rar",
                 "a.7z", "a.001"):
        assert importing.is_job_rest(name), name
    for name in ("film.mkv", "readme.txt", "book.epub", "notes", "a.mp3"):
        assert not importing.is_job_rest(name), name


# --- One program or step that breaks never holds the round up -------------------------------------------------


def test_deeply_nested_json_of_one_program_does_not_stop_the_others(operator: Any, net: FakeNetwork,
                                                                    roots: Path) -> None:
    _world, sab, _job = ebook_world(net)
    qbit = qbittorrent(net)
    info_hash = "ab" * 20
    qbit.put(info_hash, state="downloading", progress=0.5)
    with SessionLocal() as db:
        [first] = list(db.scalars(select(Download)))
        program = db.scalar(select(DownloadClient).where(DownloadClient.kind == "qbittorrent"))
        assert program is not None
        db.add(Download(user_id=first.user_id, library_id=first.library_id, kind="audio", language="de", title="x",
                        format="", size=1, protocol="torrent", info_hash=info_hash, indexer_name="", state="queued",
                        client_id=program.id, client_name=program.name, client_ref=info_hash, category="nexbooks",
                        created_at=first.created_at, updated_at=first.updated_at))
        db.commit()
    deep = b"[" * 200_000 + b"]" * 200_000
    original = sab.handle

    def nested(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("mode") in ("queue", "history"):
            return httpx.Response(200, content=deep, headers={"content-type": "application/json"})
        return original(request)

    sab.handle = nested
    round_of_tracking()
    torrent = next(row for row in downloads() if row.protocol == "torrent")
    assert torrent.state == "downloading" and torrent.progress == 50.0, (torrent.state, torrent.progress)
    with SessionLocal() as db:
        sab_row = db.scalar(select(DownloadClient).where(DownloadClient.kind == "sabnzbd"))
        assert sab_row is not None and sab_row.last_error == "client_wrong_kind"


def test_a_program_that_breaks_otherwise_does_not_stop_the_next(operator: Any, net: FakeNetwork, roots: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import tracking

    _world, _sab, _job = ebook_world(net)
    qbit = qbittorrent(net)
    info_hash = "cd" * 20
    qbit.put(info_hash, state="downloading", progress=0.25)
    with SessionLocal() as db:
        [first] = list(db.scalars(select(Download)))
        program = db.scalar(select(DownloadClient).where(DownloadClient.kind == "qbittorrent"))
        assert program is not None
        db.add(Download(user_id=first.user_id, library_id=first.library_id, kind="audio", language="de", title="x",
                        format="", size=1, protocol="torrent", info_hash=info_hash, indexer_name="", state="queued",
                        client_id=program.id, client_name=program.name, client_ref=info_hash, category="nexbooks",
                        created_at=first.created_at, updated_at=first.updated_at))
        db.commit()
        sab_id = first.client_id
    real_apply = tracking._apply

    def broken(client_id: int, found: Any, asked: list[str]) -> None:
        if client_id == sab_id:
            raise RuntimeError("a fault of nexbooks")
        real_apply(client_id, found, asked)

    monkeypatch.setattr(tracking, "_apply", broken)
    steps: list[str] = []
    monkeypatch.setattr(tracking, "_expire_unsure", lambda: (_ for _ in ()).throw(RuntimeError("broke")))
    monkeypatch.setattr(tracking, "_recheck", lambda: steps.append("recheck"))
    round_of_tracking()
    torrent = next(row for row in downloads() if row.protocol == "torrent")
    assert torrent.progress == 25.0
    assert steps == ["recheck"], "a step that broke held the next one up"


# --- The hidden folders of nexbooks that are links -------------------------------------------------------------


def _shelf_with_a_book() -> tuple[Path, int]:
    folder = books_folder("shelf")
    book(folder, "a/b.epub", title="Ganz Anderes", authors=("Ina Example",), language="de")
    library_id = make_library(folder, "Shelf")
    scan(library_id)
    return folder, library_id


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("other data", encoding="utf-8")
    return outside


def test_a_trash_folder_that_is_a_link_takes_nothing_in(tmp_path: Path) -> None:
    from app.services.procure import hidden, trash

    folder, library_id = _shelf_with_a_book()
    outside = _outside(tmp_path)
    make_link(folder / trash.FOLDER, outside)
    with SessionLocal() as db:
        library = db.get(Library, library_id)
        rows = list(db.scalars(select(BookFile)))
        with pytest.raises(hidden.Unsafe):
            trash.put(db, library, rows, kind="ebook", title="t", author="a", fmt="epub", work_id=None,
                      edition_id=None, download_id=None)
        db.rollback()
    assert (folder / "a" / "b.epub").is_file()
    assert sorted(path.name for path in outside.rglob("*")) == ["keep.txt"]


def test_a_trash_folder_that_is_a_link_gives_nothing_out_and_deletes_nothing(operator: Any, tmp_path: Path) -> None:
    from app.services.procure import trash

    folder, library_id = _shelf_with_a_book()
    anna = make_account("anna")
    join(library_id, anna.id, "manage")
    outside = _outside(tmp_path)
    (outside / "abc123").mkdir()
    (outside / "abc123" / "old.pdf").write_bytes(b"x" * 10)
    make_link(folder / trash.FOLDER, outside)
    moment = clock.now()
    with SessionLocal() as db:
        entry = TrashEntry(library_id=library_id, kind="ebook", title="t", author="a", format="pdf",
                           files=[{"path": "a/old.pdf", "size": 10}], folder="abc123", size=10, created_at=moment,
                           expires_at=moment)
        db.add(entry)
        db.commit()
        entry_id = entry.id
    with new_client(anna) as browser:
        answer = browser.delete(f"/api/v1/procure/trash/{entry_id}")
        assert answer.status_code == 409 and answer.json()["detail"]["code"] == "hidden_folder_link", answer.text
        answer = browser.post(f"/api/v1/procure/trash/{entry_id}/restore")
        assert answer.status_code == 409 and answer.json()["detail"]["code"] == "hidden_folder_link", answer.text
        assert browser.delete("/api/v1/procure/trash").json() == {"deleted": 0}
    with SessionLocal() as db:
        assert trash.sweep(db) == 0
        assert db.get(TrashEntry, entry_id) is not None
    assert (outside / "abc123" / "old.pdf").is_file() and not (folder / "a" / "old.pdf").exists()


def test_an_upload_folder_that_is_a_link_takes_no_upload(operator: Any, tmp_path: Path) -> None:
    from app.services.procure import importing

    folder, library_id = _shelf_with_a_book()
    anna = make_account("anna")
    join(library_id, anna.id, "acquire")
    outside = _outside(tmp_path)
    make_link(folder / importing.UPLOAD_FOLDER, outside)
    with new_client(anna) as browser:
        answer = browser.post("/api/v1/uploads", json={"library_id": library_id, "kind": "ebook"})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "hidden_folder_link", answer.text
    assert sorted(path.name for path in outside.rglob("*")) == ["keep.txt"]


def test_nothing_is_removed_through_a_hidden_folder_that_is_a_link(tmp_path: Path) -> None:
    from app.services.procure import hidden, importing, unpacking

    folder, _library_id = _shelf_with_a_book()
    outside = _outside(tmp_path)
    (outside / "7").mkdir()
    (outside / "7" / "keep.epub").write_bytes(b"x")
    for name in (importing.UPLOAD_FOLDER, unpacking.FOLDER):
        make_link(folder / name, outside)
        importing.remove_upload_folder(folder / name / "7")
        unpacking.remove(folder / name / "7")
        if name == unpacking.FOLDER:
            with pytest.raises(hidden.Unsafe):
                unpacking.unpack([(tmp_path / "x.zip", "zip")], unpacking.folder_for(str(folder), 9))
        assert (outside / "7" / "keep.epub").is_file()
        (folder / name).rmdir() if (folder / name).is_junction() else (folder / name).unlink()
    assert not (outside / "9").exists()


def test_a_hidden_folder_of_its_own_is_used_as_ever(tmp_path: Path) -> None:
    from app.services.procure import hidden

    library = tmp_path / "lib"
    library.mkdir()
    made = hidden.folder(library, ".nexbooks-trash", make=True)
    assert made.is_dir() and hidden.safe(library, ".nexbooks-trash")
    (made / "x").mkdir()
    assert hidden.holds(made / "x", ".nexbooks-trash")
    assert not hidden.holds(made / "x", ".nexbooks-incoming")
    (library / ".nexbooks-unpack").write_text("a file, no folder", encoding="utf-8")
    assert not hidden.safe(library, ".nexbooks-unpack")
    # A link to a folder of the library itself is no hidden folder either: the trash would end up among the books.
    (library / "Author").mkdir()
    make_link(library / ".nexbooks-incoming", library / "Author")
    assert not hidden.safe(library, ".nexbooks-incoming")
    with pytest.raises(hidden.Unsafe):
        hidden.folder(library, ".nexbooks-incoming", make=True)


# --- Deleting for good only in the browser ---------------------------------------------------------------------


def test_a_device_token_does_not_delete_the_trash_for_good(operator: Any) -> None:
    from app.main import app

    folder, library_id = _shelf_with_a_book()
    anna = make_account("anna")
    join(library_id, anna.id, "manage")
    with new_client(anna) as browser:
        token = browser.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    kept = folder / ".nexbooks-trash" / "abc123"
    kept.mkdir(parents=True)
    (kept / "old.pdf").write_bytes(b"x" * 10)
    moment = clock.now()
    with SessionLocal() as db:
        entry = TrashEntry(library_id=library_id, kind="ebook", title="t", author="a", format="pdf",
                           files=[{"path": "a/old.pdf", "size": 10}], folder="abc123", size=10, created_at=moment,
                           expires_at=moment)
        db.add(entry)
        db.commit()
        entry_id = entry.id
    device = TestClient(app, base_url="http://testserver")
    headers = {"Authorization": f"Bearer {token}"}
    for answer in (device.delete("/api/v1/procure/trash", headers=headers),
                   device.delete(f"/api/v1/procure/trash/{entry_id}", headers=headers)):
        assert answer.status_code == 403 and answer.json()["detail"]["code"] == "browser_only", answer.text
    # Reading it is fine with the device.
    assert device.get("/api/v1/procure/trash", headers=headers).status_code == 200
    # Who looks after no library has no trash to empty.
    with new_client(make_account("nobody")) as nobody:
        answer = nobody.delete("/api/v1/procure/trash")
        assert answer.status_code == 403 and answer.json()["detail"]["code"] == "forbidden", answer.text
    with SessionLocal() as db:
        assert db.get(TrashEntry, entry_id) is not None
    assert (kept / "old.pdf").is_file()
    with new_client(anna) as browser:
        assert browser.delete(f"/api/v1/procure/trash/{entry_id}").status_code == 204
    assert not (kept / "old.pdf").exists()


# --- The log names no path, a read starts nothing, a request that makes nothing says so -----------------------


def test_the_path_of_a_book_in_an_exception_stays_out_of_the_log(client: TestClient, operator: Any) -> None:
    import logging

    from app.services import logs

    secret = "Ganz Anderes Buch"
    # The lines of a traceback show the code that raised: the path is made before, so only the words of the exception
    # can carry it.
    path, first, second = f"/books/Ina Example/{secret}.epub", f"/a/{secret}.epub", f"/b/{secret}.epub"
    try:
        raise FileNotFoundError(2, "No such file or directory", path)
    except FileNotFoundError:
        logging.getLogger("nexbooks.procure").exception("probe-path-exception failed")
    try:
        raise OSError(18, "Invalid cross-device link", first, None, second)
    except OSError:
        logging.getLogger("nexbooks.procure").exception("probe-path-rename failed")
    raw = logs.log_file().read_text(encoding="utf-8")
    assert "probe-path-exception failed" in raw and "FileNotFoundError" in raw and "probe-path-rename" in raw
    assert secret not in raw and "Ina Example" not in raw
