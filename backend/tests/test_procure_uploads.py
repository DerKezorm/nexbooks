"""Uploading books from the browser: the right ``acquire``, the limits, the names, the same way into the
library as a download, and an upload dropped while it comes. Invented books only, made here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download, Edition

from .bookmaker import make_epub, make_mp3
from .conftest import TAB, make_account, new_client
from .filing_helpers import editions, files_in, hidden_in, library_path, world_with, zip_of
from .library_helpers import join, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE


def other_book(folder: Path) -> None:
    make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch", authors=("Ida Brandt",), language="de")


def begin(client: Any, library_id: int, **body: Any) -> dict[str, Any]:
    answer = client.post("/api/v1/uploads", json={"library_id": library_id, **body})
    assert answer.status_code == 201, answer.text
    return answer.json()  # type: ignore[no-any-return]


def send(client: Any, upload_id: int, path: str, data: bytes) -> Any:
    return client.put(f"/api/v1/uploads/{upload_id}/files", params={"path": path}, content=data,
                      headers={"content-type": "application/octet-stream"})


def epub_bytes(tmp_path: Path, **kwargs: Any) -> bytes:
    values: dict[str, Any] = {"title": TITLE, "authors": (AUTHOR,), "language": "de", "series": SERIES,
                              "series_pos": 2.0}
    values.update(kwargs)
    return make_epub(tmp_path / "made.epub", **values).read_bytes()


def test_an_uploaded_e_book_is_filed_like_a_download(operator: Any, net: FakeNetwork, tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    assert upload["state"] == "uploading" and upload["limits"]["files"] > 0
    answer = send(world.client, upload["id"], "Die Laternenstrasse.epub", epub_bytes(tmp_path))
    assert answer.status_code == 201, answer.text
    assert world.client.get(f"/api/v1/uploads/{upload['id']}").json()["files"][0]["path"] == \
        "Die Laternenstrasse.epub"
    answer = world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={})
    assert answer.status_code == 202, answer.text
    run_jobs()
    shown = world.client.get(f"/api/v1/uploads/{upload['id']}").json()
    assert shown["phase"] == "done" and shown["work_id"] is not None, shown
    shelf = library_path(world)
    assert "Rune Falkner/Nebelchor/02 - Die Laternenstraße.epub" in files_in(shelf)
    assert not hidden_in(shelf)
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.title == TITLE))
        assert edition is not None and edition.work_id == shown["work_id"]
    history = world.client.get("/api/v1/procure/history").json()["entries"]
    assert history[0]["result"] == "done"


def test_a_recording_in_a_zip_for_a_book_of_the_library_joins_its_work(operator: Any, net: FakeNetwork,
                                                                       tmp_path: Path) -> None:
    world = world_with(net, lambda folder: make_epub(folder / "falkner" / "laternen.epub", title=TITLE,
                                                     authors=(AUTHOR,), language="de"))
    parts = {f"CD 1/{number:02d}.mp3": make_mp3(tmp_path / f"{number}.mp3", seconds=3.0, title=TITLE, album=TITLE,
                                               artist=AUTHOR, track=number).read_bytes() for number in (1, 2)}
    upload = begin(world.client, world.library_id, work_id=world.work_id, kind="audio")
    answer = send(world.client, upload["id"], "book.zip", zip_of(tmp_path / "book.zip", parts).read_bytes())
    assert answer.status_code == 201, answer.text
    assert world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={}).status_code == 202
    run_jobs()
    shown = world.client.get(f"/api/v1/uploads/{upload['id']}").json()
    assert shown["phase"] == "done", shown
    audio = [edition for edition in editions() if edition.kind == "audio"]
    assert len(audio) == 1 and audio[0].work_id == world.work_id


def test_only_who_may_bring_books_into_the_library_uploads(operator: Any, net: FakeNetwork) -> None:
    world = world_with(net, other_book, right="read")
    answer = world.client.post("/api/v1/uploads", json={"library_id": world.library_id})
    assert answer.status_code == 403
    stranger = new_client(make_account("otto"))
    assert stranger.post("/api/v1/uploads", json={"library_id": world.library_id}).status_code == 404
    join(world.library_id, world.person.id, "acquire")
    upload = begin(world.client, world.library_id)
    # Somebody else's upload is not there for anybody else, the operator included.
    for other in (stranger, new_client(operator)):
        assert other.get(f"/api/v1/uploads/{upload['id']}").status_code == 404
        assert send(other, upload["id"], "a.epub", b"x").status_code == 404
        assert other.delete(f"/api/v1/uploads/{upload['id']}").status_code == 404


@pytest.mark.parametrize("name", ["../../escape.epub", "/abs.epub", "C:/drive.epub", ".hidden.epub", "a/../b.epub",
                                  "a\x00b.epub"])
def test_a_name_that_leaves_the_upload_is_refused(operator: Any, net: FakeNetwork, name: str) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    answer = send(world.client, upload["id"], name, b"PK")
    assert answer.status_code in (415, 422), (name, answer.status_code)
    assert world.client.get(f"/api/v1/uploads/{upload['id']}").json()["files"] == []


def test_what_is_no_book_and_what_is_too_large_is_refused_before_it_is_read(operator: Any, net: FakeNetwork,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import uploads

    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    assert send(world.client, upload["id"], "setup.exe", b"MZ").status_code == 415
    assert send(world.client, upload["id"], "notes.txt", b"x").status_code == 415
    monkeypatch.setattr(uploads, "MAX_EBOOK_BYTES", 10)
    answer = send(world.client, upload["id"], "big.epub", b"x" * 11)
    assert answer.status_code == 413 and answer.json()["detail"]["code"] == "too_large"
    assert world.client.get(f"/api/v1/uploads/{upload['id']}").json()["files"] == []
    assert send(world.client, upload["id"], "fits.epub", b"x" * 10).status_code == 201
    # Never over a file of the same name.
    answer = send(world.client, upload["id"], "fits.epub", b"y" * 5)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "file_exists"


def test_an_upload_of_e_books_and_recordings_at_once_is_refused(operator: Any, net: FakeNetwork,
                                                                tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    send(world.client, upload["id"], "a.epub", epub_bytes(tmp_path))
    send(world.client, upload["id"], "b.mp3", make_mp3(tmp_path / "b.mp3", seconds=2.0).read_bytes())
    answer = world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={})
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == "upload_mixed"


def test_an_upload_dropped_while_it_comes_goes_with_its_files(operator: Any, net: FakeNetwork,
                                                              tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    send(world.client, upload["id"], "a.epub", epub_bytes(tmp_path))
    shelf = library_path(world)
    assert hidden_in(shelf)
    assert world.client.delete(f"/api/v1/uploads/{upload['id']}").status_code == 204
    assert not hidden_in(shelf)
    with SessionLocal() as db:
        assert db.scalar(select(Download.id)) is None
    assert world.client.get(f"/api/v1/uploads/{upload['id']}").status_code == 404


def test_no_more_than_three_uploads_are_open_at_once(operator: Any, net: FakeNetwork) -> None:
    world = world_with(net, other_book)
    for _ in range(3):
        begin(world.client, world.library_id)
    answer = world.client.post("/api/v1/uploads", json={"library_id": world.library_id})
    assert answer.status_code == 429


def test_a_change_without_the_tab_header_is_refused(operator: Any, net: FakeNetwork) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    world = world_with(net, other_book)
    bare = TestClient(app)
    bare.cookies = world.client.cookies
    answer = bare.post("/api/v1/uploads", json={"library_id": world.library_id})
    assert answer.status_code == 400 and TAB
