"""What the files of a book say, beside the proposals of the sources: only for the people who look after the library, only
names inside the library, a contradiction told from a file that says nothing, and the picture inside the file."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account
from app.services.match import filefacts
from app.services.sources.model import Candidate

from .bookmaker import cover_image
from .conftest import BOOKS_DIR, DATA_DIR, make_account, new_client
from .fake_sources import ol_doc
from .library_helpers import book, books_folder, make_library, recording, scan
from .match_helpers import World, edition_of, entry_for, member

ISBN = "9780000000002"
#: A source that knows the book under the last name of its author only: always a question for a person.
WEAK = ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",), cover=88, year=2019)


def library_with(world: World, **tags: Any) -> int:
    world.only("openlibrary")
    world.fake.ol = [WEAK]
    folder = books_folder("facts")
    book(folder, "Lindqvist/The Quiet Orchard.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",),
         language="en", **tags)
    library = make_library(folder, "Facts")
    member(library)
    scan(library)
    return library


def rows_of(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    [proposal] = answer["proposals"]
    return {row["field"]: row for row in proposal["rows"]}


def test_the_file_says_what_is_written_in_it_and_where_it_lies(client: TestClient, operator: Account,
                                                               world: World) -> None:
    library_with(world, isbn=ISBN, publisher="Orchard House", date="2019-05-01", subjects=("Fiction",),
                 cover=cover_image())
    entry = entry_for("The Quiet Orchard")
    answer = client.get(f"/api/v1/review/{entry.id}/file")
    assert answer.status_code == 200, answer.text
    found = answer.json()
    assert found["kind"] == "ebook" and found["folder"] == "Lindqvist"
    [file] = found["files"]
    assert (file["name"], file["folder"], file["format"]) == ("The Quiet Orchard.epub", "Lindqvist", "epub")
    says = found["says"]
    assert (says["title"], says["authors"], says["isbn"], says["publisher"], says["year"], says["language"]) == (
        "The Quiet Orchard", ["Mara Lindqvist"], ISBN, "Orchard House", 2019, "en")
    assert says["subjects"] == ["Fiction"] and says["pages"] >= 1 and says["narrators"] == []
    # Names inside the library only: nothing of the disk (the folder of the library, the data folder, a drive letter).
    text = answer.text
    assert BOOKS_DIR.replace("\\", "/") not in text.replace("\\\\", "/") and DATA_DIR not in text and "\\" not in text
    rows = rows_of(found)
    # The title is the same, the author fits by the last name only, the language and the year are the same; the source
    # knows no ISBN and no publisher (it is silent, the file is not).
    assert rows["title"]["state"] == "agrees" and rows["authors"]["state"] == "similar"
    assert rows["language"]["state"] == "agrees" and rows["year"]["state"] == "agrees"
    assert rows["isbn"] == {"field": "isbn", "file": ISBN, "source": "", "state": "source_silent", "unit": ""}
    assert rows["publisher"]["state"] == "source_silent"
    assert "narrators" not in rows and "runtime" not in rows


def test_a_file_that_says_nothing_is_told_from_one_that_contradicts(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    library_with(world)
    entry = entry_for("The Quiet Orchard")
    found = client.get(f"/api/v1/review/{entry.id}/file").json()
    rows = rows_of(found)
    # The file names no ISBN: "says nothing", not "contradicts".
    assert "isbn" not in rows or rows["isbn"]["state"] in ("file_silent", "source_silent")
    candidate = Candidate(source="openlibrary", key="OL9W", title="Another Orchard", authors=["Mara Lindqvist"],
                          languages=["fr"], isbns=["9781111111111"], year=2012)
    facts = filefacts.Facts(folder="", files=[], says=filefacts.Says(
        title="The Quiet Orchard", authors=["Mara Lindqvist"], isbn=ISBN, language="en", year=2019))
    by_field = {row.field: row.state for row in filefacts.compare(facts, candidate, "ebook")}
    assert by_field["title"] == "differs" and by_field["authors"] == "agrees" and by_field["isbn"] == "differs"
    assert by_field["language"] == "differs" and by_field["year"] == "differs"
    silent = filefacts.Facts(folder="", files=[], says=filefacts.Says(title="The Quiet Orchard"))
    by_field = {row.field: row.state for row in filefacts.compare(silent, candidate, "ebook")}
    assert by_field["isbn"] == "file_silent" and by_field["language"] == "file_silent"
    assert by_field["authors"] == "file_silent" and by_field["year"] == "file_silent"


def test_a_recording_is_compared_by_narrator_and_running_time(client: TestClient, operator: Account) -> None:
    folder = books_folder("hear")
    for number in (1, 2):
        recording(folder, f"Lindqvist/The Quiet Orchard/{number:02d}.mp3", seconds=150.0, title=f"Part {number}",
                  album="The Quiet Orchard", artist="Mara Lindqvist", narrator="Ben Hollister", track=number)
    library = make_library(folder, "Hear", kind="audio")
    scan(library)
    edition = edition_of("The Quiet Orchard")
    with SessionLocal() as db:
        row = db.get(type(edition), edition.id)
        assert row is not None
        facts = filefacts.facts_of(db, row)
    says = facts.says
    assert says.title == "The Quiet Orchard" and says.narrators == ["Ben Hollister"] and says.authors == [
        "Mara Lindqvist"]
    assert [line.name for line in facts.files] == ["01.mp3", "02.mp3"] and facts.folder == "Lindqvist/The Quiet Orchard"
    assert 295.0 < says.duration < 305.0
    minutes = round(says.duration / 60)
    near = Candidate(source="audible", key="B0AAAAAAAA", title="The Quiet Orchard", authors=["Mara Lindqvist"],
                     narrators=["ben hollister"], runtime_min=minutes)
    far = Candidate(source="audible", key="B0BBBBBBBB", title="The Quiet Orchard", authors=["Mara Lindqvist"],
                    narrators=["Cleo Marsh"], runtime_min=minutes + 300)
    good = {row.field: row for row in filefacts.compare(facts, near, "audio")}
    bad = {row.field: row for row in filefacts.compare(facts, far, "audio")}
    assert good["narrators"].state == "agrees" and good["runtime"].state == "agrees"
    assert good["runtime"].unit == "minutes"
    assert bad["narrators"].state == "differs" and bad["runtime"].state == "differs"


def test_only_a_member_with_manage_reads_what_the_file_says(client: TestClient, operator: Account, world: World) -> None:
    library = library_with(world, cover=cover_image())
    entry = entry_for("The Quiet Orchard")
    reader, outsider = make_account("rita"), make_account("sam")
    member(library, "rita", "read")
    for path in (f"/api/v1/review/{entry.id}/file", f"/api/v1/review/{entry.id}/file-cover"):
        with new_client(reader) as rita:
            assert rita.get(path).status_code == 403, path
        with new_client(outsider) as sam:
            assert sam.get(path).status_code == 404, path
        boss = make_account(f"boss{len(path)}", "operator")
        with new_client(boss) as other:
            assert other.get(path).status_code == 404, path
        with TestClient(client.app, base_url="http://testserver") as nobody:
            assert nobody.get(path).status_code == 401, path
    assert client.get(f"/api/v1/review/{entry.id + 99}/file").status_code == 404
    assert client.get(f"/api/v1/review/{entry.id + 99}/file-cover").status_code == 404


def test_the_picture_inside_the_file_is_served_to_the_ones_who_manage(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    library_with(world, cover=cover_image())
    entry = entry_for("The Quiet Orchard")
    found = client.get(f"/api/v1/review/{entry.id}/file").json()
    assert found["cover_url"] and found["cover_url"].startswith(f"/api/v1/review/{entry.id}/file-cover")
    picture = client.get(found["cover_url"])
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"
    assert picture.headers["x-content-type-options"] == "nosniff"


def test_a_book_without_a_picture_has_no_address_for_it(client: TestClient, operator: Account, world: World) -> None:
    library_with(world)
    entry = entry_for("The Quiet Orchard")
    found = client.get(f"/api/v1/review/{entry.id}/file").json()
    assert found["cover_url"] is None
    assert client.get(f"/api/v1/review/{entry.id}/file-cover").status_code == 404


def test_the_files_it_names_are_read_with_range_by_the_one_who_manages_and_by_nobody_else(
        client: TestClient, operator: Account, world: World) -> None:
    """A client that lets a person read in the book or listen asks for the file by the number the facts name: a part of it
    with ``Range``, for a member of the library; a person outside it gets what a number nobody had gets."""
    library_with(world)
    entry = entry_for("The Quiet Orchard")
    [file] = client.get(f"/api/v1/review/{entry.id}/file").json()["files"]
    part = client.get(f"/api/v1/files/{file['id']}/content", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.headers["content-range"].startswith("bytes 0-99/")
    assert len(part.content) == 100
    outsider = make_account("sam")
    with new_client(outsider) as sam:
        assert sam.get(f"/api/v1/files/{file['id']}/content", headers={"Range": "bytes=0-99"}).status_code == 404
