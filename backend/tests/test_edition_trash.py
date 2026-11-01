"""One edition of a book goes into the trash from the page of the book, whatever brought it to the shelf: the files go
into the trash of the library, the place of the people who read it is kept with them, the history says so, and nothing is
blocked or looked for. Only for a member with ``manage``; everything comes back from the trash."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BlocklistEntry, Edition, ProcureHistory, ProcureWish, TrashEntry, Work

from .conftest import make_account, new_client
from .filing_helpers import files_in, hidden_in
from .library_helpers import age_all, book, books_folder, join, make_library, recording, run_jobs, scan
from .match_helpers import member


def shelf() -> tuple[Path, int, dict[str, int]]:
    """An e-book and a recording of one book, read from the disk (no release, no upload). Returns the folder, the library
    and the editions by kind."""
    folder = books_folder("trash-shelf")
    book(folder, "Ada Feldkamp/Die Nebelwacht (2019)/nebelwacht.epub", title="Die Nebelwacht",
         authors=("Ada Feldkamp",), language="de")
    recording(folder, "Ada Feldkamp/Die Nebelwacht (2019)/Audio/01.mp3", seconds=30.0, album="Die Nebelwacht",
              artist="Ada Feldkamp")
    library = make_library(folder, "Shelf")
    member(library)
    scan(library)
    with SessionLocal() as db:
        found = {edition.kind: edition.id for edition in db.scalars(select(Edition))}
    return folder, library, found


def test_an_edition_read_from_the_disk_goes_to_the_trash_and_comes_back_with_the_place(
        client: TestClient, operator: Account) -> None:
    folder, _library, found = shelf()
    assert client.put(f"/api/v1/editions/{found['ebook']}/progress",
                      json={"locator": "epubcfi(/6/2!/4/2)", "fraction": 0.4}).status_code == 200
    answer = client.post(f"/api/v1/editions/{found['ebook']}/trash")
    assert answer.status_code == 200, answer.text
    done = answer.json()
    assert done["trash_id"] > 0 and done["people"] == 1 and done["missing"] == []
    with SessionLocal() as db:
        assert [edition.kind for edition in db.scalars(select(Edition))] == ["audio"]
        work = db.scalar(select(Work))
        assert work is not None and done["work_id"] == work.id
        [line] = list(db.scalars(select(ProcureHistory)))
        assert line.result == "removed" and line.note == {"code": "trashed", "trash_id": done["trash_id"]}
        assert line.book_title == "Die Nebelwacht" and line.work_id == work.id
        # Nothing is said to be another book: no block, no wish.
        assert not list(db.scalars(select(BlocklistEntry))) and not list(db.scalars(select(ProcureWish)))
    assert not any(name.endswith(".epub") for name in files_in(folder))
    assert any(name.endswith("nebelwacht.epub") for name in hidden_in(folder))
    assert client.post(f"/api/v1/procure/trash/{done['trash_id']}/restore").status_code == 204
    age_all(folder)
    run_jobs()
    with SessionLocal() as db:
        ebook = db.scalar(select(Edition).where(Edition.kind == "ebook"))
        assert ebook is not None
        back = ebook.id
    assert client.get(f"/api/v1/editions/{back}/progress").json()["fraction"] == 0.4


def test_the_only_edition_takes_its_book_with_it(client: TestClient, operator: Account) -> None:
    _folder, _library, found = shelf()
    assert client.post(f"/api/v1/editions/{found['ebook']}/trash").json()["work_id"] is not None
    last = client.post(f"/api/v1/editions/{found['audio']}/trash").json()
    assert last["work_id"] is None
    with SessionLocal() as db:
        assert not list(db.scalars(select(Edition))) and not list(db.scalars(select(Work)))
        assert len(list(db.scalars(select(TrashEntry)))) == 2


def test_only_a_member_with_manage_puts_an_edition_in_the_trash(client: TestClient, operator: Account) -> None:
    folder, library, found = shelf()
    before = files_in(folder)
    asker, reader, outsider = make_account("ann"), make_account("rob"), make_account("sam")
    join(library, asker.id, "acquire")
    join(library, reader.id, "read")
    address = f"/api/v1/editions/{found['ebook']}/trash"
    with SessionLocal() as db:
        work_id = db.scalar(select(Work.id))
    # Only who may is told how long the trash keeps it (the page offers it by that).
    assert {e["trash_days"] for e in client.get(f"/api/v1/works/{work_id}").json()["editions"]} == {30}
    with new_client(asker) as ann, new_client(reader) as rob, new_client(outsider) as sam:
        assert {e["trash_days"] for e in rob.get(f"/api/v1/works/{work_id}").json()["editions"]} == {0}
        assert ann.post(address).status_code == 403
        assert rob.post(address).status_code == 403
        assert sam.post(address).status_code == 404
    # The operator is no exception: not a member, so the book is not there for her.
    boss = make_account("boss", "operator")
    with new_client(boss) as other:
        assert other.post(address).status_code == 404
    with new_client() as nobody:
        assert nobody.post(address).status_code == 401
    assert client.post("/api/v1/editions/999999/trash").status_code == 404
    assert files_in(folder) == before and not hidden_in(folder)
    with SessionLocal() as db:
        assert not list(db.scalars(select(TrashEntry))) and not list(db.scalars(select(ProcureHistory)))
        assert len(list(db.scalars(select(Edition)))) == 2
