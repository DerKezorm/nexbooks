"""Looking into the files of a download that waits for a person: what the files say, the bytes to read or to hear (with
``Range``), and nothing else. The rights are those of the library the book goes into, the files are the download's own
(never a path from the request), and a download that asks nothing, or whose files are gone, shows nothing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings

from .conftest import BOOKS_DIR, DATA_DIR, make_account, new_client
from .fake_sources import audible_product
from .filing_helpers import audio_wish, download_roots, ebook_world, finish, german_epub, recording, the_download
from .library_helpers import join
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE, Item

GERMAN = ("Der Regen fiel auf die alte Straße, und die Laternen waren noch nicht an. Sie ging mit dem Hund nach Hause, "
          "weil es schon spät war und der Wind über die Felder kam. Im Haus war es still, nur die Uhr in der Küche "
          "schlug, als er die Tür hinter sich schloss und sich an den Tisch setzte.")
UNMARKED = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.EPUB-EXAMPLE"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def a_question(net: FakeNetwork, roots: Path) -> tuple[Any, Path]:
    """A download of an e-book whose package says Spanish and whose text is German: a question for a person."""
    world, sab, job = ebook_world(net, Item(UNMARKED, size=2_000_000, categories=(7120,)))
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    book = german_epub(folder, language="nl", prose=GERMAN, text_bytes=9000)
    finish(sab, job, folder)
    row = the_download()
    assert (row.state, row.problem_code) == ("problem", "language_unclear"), (row.state, row.problem_code)
    return world, book


def test_a_person_who_may_ask_for_the_book_sees_what_the_files_say_and_reads_them(operator: Any, net: FakeNetwork,
                                                                                roots: Path) -> None:
    world, book = a_question(net, roots)
    row = the_download()
    look = world.client.get(f"/api/v1/procure/downloads/{row.id}/look")
    assert look.status_code == 200, look.text
    found = look.json()
    assert found["kind"] == "ebook" and found["download_id"] == row.id
    [file] = found["files"]
    assert (file["name"], file["format"], file["protected"]) == (book.name, "epub", False)
    assert found["says"]["language"] == "nl" and found["says"]["text_language"] == "de"
    assert found["says"]["title"] == TITLE and found["says"]["authors"] == [AUTHOR]
    # Names inside the download only: nothing of the disk.
    text = look.text.replace("\\\\", "/")
    for private in (str(roots).replace("\\", "/"), BOOKS_DIR.replace("\\", "/"), DATA_DIR.replace("\\", "/")):
        assert private not in text
    content = world.client.get(f"/api/v1/procure/downloads/{row.id}/files/{file['id']}/content")
    assert content.status_code == 200 and content.content == book.read_bytes()
    assert content.headers["x-content-type-options"] == "nosniff" and "sandbox" in content.headers["content-security-policy"]
    part = world.client.get(f"/api/v1/procure/downloads/{row.id}/files/{file['id']}/content", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == book.read_bytes()[:10]
    assert part.headers["content-range"].startswith("bytes 0-9/")


def test_a_recording_in_parts_is_listed_in_order_with_its_time_and_plays_with_range(operator: Any, net: FakeNetwork,
                                                                                   roots: Path, world: Any) -> None:
    # The running time of the three parts (60 s) is an abridged one against the catalogue, so the check asks.
    world.fake.audible = {"de": [audible_product("B000000001", TITLE, [AUTHOR], 3),
                                 audible_product("B000000002", TITLE, [AUTHOR], 1, kind="abridged")]}
    people, sab, job = audio_wish(net)
    folder = roots / "complete" / "short"
    parts = recording(folder, parts=3, seconds=20.0)
    finish(sab, job, folder)
    row = the_download()
    assert (row.state, row.problem_code) == ("problem", "runtime")
    found = people.client.get(f"/api/v1/procure/downloads/{row.id}/look").json()
    assert found["kind"] == "audio"
    assert [file["name"] for file in found["files"]] == [path.name for path in parts]
    assert [file["part"] for file in found["files"]] == [1, 2, 3]
    assert all(19 <= file["duration"] <= 21 for file in found["files"]) and 55 <= found["says"]["duration"] <= 65
    assert [chapter["start"] for chapter in found["chapters"]] == pytest.approx([0, 20, 40], abs=1.5)
    second = found["files"][1]
    ranged = people.client.get(f"/api/v1/procure/downloads/{row.id}/files/{second['id']}/content",
                               headers={"Range": "bytes=10-19"})
    assert ranged.status_code == 206 and ranged.content == parts[1].read_bytes()[10:20]


def test_the_files_of_a_download_that_asks_nothing_are_not_shown(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, _book = a_question(net, roots)
    row = the_download()
    answered = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "block"})
    assert answered.status_code == 200, answered.text
    assert world.client.get(f"/api/v1/procure/downloads/{row.id}/look").status_code in (404, 409)


def test_a_download_that_is_no_longer_waiting_shows_nothing_even_when_its_files_are_still_listed(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import Download

    world, _book = a_question(net, roots)
    row = the_download()
    [file] = world.client.get(f"/api/v1/procure/downloads/{row.id}/look").json()["files"]
    with SessionLocal() as db:
        db.execute(update(Download).where(Download.id == row.id).values(state="completed", problem_code=""))
        db.commit()
    assert world.client.get(f"/api/v1/procure/downloads/{row.id}/look").status_code == 409
    assert world.client.get(f"/api/v1/procure/downloads/{row.id}/files/{file['id']}/content").status_code == 409


def test_nobody_without_the_right_in_the_library_sees_the_files(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, _book = a_question(net, roots)
    row = the_download()
    [file] = world.client.get(f"/api/v1/procure/downloads/{row.id}/look").json()["files"]
    reader = make_account("lena")
    join(world.library_id, reader.id, "read")
    stranger = make_account("karl")
    for person in (reader, stranger):
        with new_client(person) as client:
            assert client.get(f"/api/v1/procure/downloads/{row.id}/look").status_code in (403, 404)
            assert client.get(f"/api/v1/procure/downloads/{row.id}/files/{file['id']}/content").status_code in (403, 404)
    # A person who looks after the library (manage) may, as one who may ask for books.
    keeper = make_account("mona")
    join(world.library_id, keeper.id, "manage")
    with new_client(keeper) as client:
        assert client.get(f"/api/v1/procure/downloads/{row.id}/look").status_code == 200
    # Nobody is signed in: nothing.
    with new_client() as client:
        assert client.get(f"/api/v1/procure/downloads/{row.id}/files/{file['id']}/content").status_code == 401


def test_a_file_number_of_another_download_or_of_the_library_is_not_a_file_of_this_one(operator: Any, net: FakeNetwork,
                                                                                    roots: Path) -> None:
    world, _book = a_question(net, roots)
    row = the_download()
    [file] = world.client.get(f"/api/v1/procure/downloads/{row.id}/look").json()["files"]
    assert world.client.get(f"/api/v1/procure/downloads/{row.id + 50}/files/{file['id']}/content").status_code == 404
    assert world.client.get(f"/api/v1/procure/downloads/{row.id}/files/{file['id'] + 500}/content").status_code == 404
    # The number of a file of the library is not read through this way.
    library_file = world.client.get(f"/api/v1/works/{world.work_id}").json()["editions"][0]["files"][0]["id"]
    assert world.client.get(f"/api/v1/procure/downloads/{row.id}/files/{library_file + 1000}/content").status_code == 404


def test_a_file_that_is_gone_or_became_a_link_is_not_served(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, book = a_question(net, roots)
    row = the_download()
    [file] = world.client.get(f"/api/v1/procure/downloads/{row.id}/look").json()["files"]
    url = f"/api/v1/procure/downloads/{row.id}/files/{file['id']}/content"
    assert world.client.get(url).status_code == 200
    secret = roots / "secret.epub"
    secret.write_bytes(b"not a book of this download")
    book.unlink()
    try:
        os.symlink(secret, book)
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic link can be made here")
    assert world.client.get(url).status_code == 404
    book.unlink()
    assert world.client.get(url).status_code == 404
    assert get_settings() is not None
