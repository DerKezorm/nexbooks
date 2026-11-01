"""A book nexbooks fetched that is not the one wished is sent away from the page of the book: the files go into the trash,
the release is blocked for the book, the history says so, the edition leaves the shelf and a new wish looks on. Only
for a member with ``manage``, only for what came from a release, and everything can be undone from the trash and the
blocklist."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    BlocklistEntry,
    BookFile,
    Download,
    Edition,
    ForeignBook,
    ProcureHistory,
    ProcureWish,
    TrashEntry,
    Work,
)

from .conftest import make_account, new_client
from .filing_helpers import (
    download_roots,
    ebook_world,
    editions,
    files_in,
    finish,
    german_epub,
    hidden_in,
    library_path,
    the_download,
)
from .follow_helpers import BOOK, rows
from .library_helpers import age_all, join, run_jobs
from .procure_fakes import FakeNetwork


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def delivered(net: FakeNetwork, roots: Path, right: str = "manage") -> tuple[Any, Edition]:
    """A German e-book fetched for the wish and filed next to the English one the library had; the person who wished it
    has ``right`` in the library. Returns the world and the new edition."""
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / BOOK
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    join(world.library_id, world.person.id, right)
    [german] = [edition for edition in editions() if edition.language == "de"]
    return world, german


def post(world: Any, edition: Edition) -> Any:
    return world.client.post(f"/api/v1/editions/{edition.id}/wrong-book")


def test_a_wrong_book_goes_to_the_trash_is_blocked_and_searched_again(operator: Any, net: FakeNetwork,
                                                                       roots: Path) -> None:
    world, german = delivered(net, roots)
    shelf = library_path(world)
    before = files_in(shelf)
    assert any("Laternenstra" in name for name in before)
    answer = post(world, german)
    assert answer.status_code == 200, answer.text
    sent = answer.json()
    assert sent["replacement"] == "searching" and sent["work_id"] == world.work_id and sent["trash_id"] > 0
    # The files left the shelf for the hidden trash folder of the library; the English edition stays.
    assert not any("Laternenstra" in name for name in files_in(shelf))
    assert any("Laternenstra" in name for name in hidden_in(shelf))
    assert [edition.language for edition in editions()] == ["en"]
    with SessionLocal() as db:
        [trashed] = list(db.scalars(select(TrashEntry)))
        assert trashed.id == sent["trash_id"] and trashed.kind == "ebook" and trashed.library_id == world.library_id
        [blocked] = list(db.scalars(select(BlocklistEntry)))
        assert blocked.title == BOOK and blocked.reason == "by_hand" and blocked.work_id == world.work_id
        assert blocked.by_user_id == world.person.id
        lines = list(db.scalars(select(ProcureHistory).where(ProcureHistory.result == "removed")))
        assert len(lines) == 1 and lines[0].note == {"code": "wrong_book", "blocked": True, "trash_id": trashed.id}
        assert lines[0].release == BOOK
        # The wish that was done is not reopened; a new one looks for the book again.
        open_wishes = [wish for wish in db.scalars(select(ProcureWish)) if wish.state != "done"]
        assert len(open_wishes) == 1 and open_wishes[0].kind == "ebook" and open_wishes[0].work_id == world.work_id
    # The release that was sent away is not taken again: the only one the indexer has is blocked.
    run_jobs()
    [open_wish] = [wish for wish in rows(ProcureWish) if wish.state != "done"]
    assert open_wish.state == "not_found"
    assert len(rows(Download)) == 1 and rows(Download)[0].state == "imported"


def test_the_files_come_back_from_the_trash_and_the_block_is_lifted_on_the_blocklist(operator: Any, net: FakeNetwork,
                                                                                    roots: Path) -> None:
    world, german = delivered(net, roots)
    shelf = library_path(world)
    entry = post(world, german).json()
    answer = world.client.post(f"/api/v1/procure/trash/{entry['trash_id']}/restore")
    assert answer.status_code == 204, answer.text
    # The scan waits for a file that is still being written; the restored one is as old as the files of a shelf.
    age_all(shelf)
    run_jobs()
    assert any("Laternenstra" in name for name in files_in(shelf))
    assert sorted(edition.language for edition in editions()) == ["de", "en"]
    [blocked] = world.client.get("/api/v1/procure/blocklist").json()["entries"]
    assert world.client.delete(f"/api/v1/procure/blocklist/{blocked['id']}").status_code == 204
    assert rows(BlocklistEntry) == []


def test_only_a_member_with_manage_may_send_a_book_away(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, german = delivered(net, roots, right="acquire")
    shelf = library_path(world)
    # A person who may ask for books but does not look after the library: no.
    assert post(world, german).status_code == 403
    reader, outsider = make_account("rob"), make_account("sam")
    join(world.library_id, reader.id, "read")
    with new_client(reader) as rob:
        assert rob.post(f"/api/v1/editions/{german.id}/wrong-book").status_code == 403
    with new_client(outsider) as sam:
        assert sam.post(f"/api/v1/editions/{german.id}/wrong-book").status_code == 404
    # The operator is no exception: not a member, so the book is not there for him.
    boss = make_account("boss", "operator")
    with new_client(boss) as other:
        assert other.post(f"/api/v1/editions/{german.id}/wrong-book").status_code == 404
    with new_client() as nobody:
        assert nobody.post(f"/api/v1/editions/{german.id}/wrong-book").status_code == 401
    assert any("Laternenstra" in name for name in files_in(shelf)) and not hidden_in(shelf)
    assert rows(TrashEntry) == [] and rows(BlocklistEntry) == []
    assert [edition.language for edition in editions() if edition.id == german.id] == ["de"]


def test_a_book_that_did_not_come_from_a_release_is_not_sent_away(operator: Any, net: FakeNetwork, roots: Path) -> None:
    """The English book was in the library before; a book a person uploaded is theirs: both are ``not_fetched``."""
    world, german = delivered(net, roots)
    english = next(edition for edition in editions() if edition.language == "en")
    assert post(world, english).status_code == 409
    assert post(world, english).json()["detail"]["code"] == "not_fetched"
    with SessionLocal() as db:
        row = db.scalar(select(Download).where(Download.edition_id == german.id))
        assert row is not None
        row.protocol = "upload"
        db.commit()
    answer = post(world, german)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "not_fetched"
    assert rows(TrashEntry) == [] and rows(BlocklistEntry) == []
    assert len(editions()) == 2


def test_a_second_click_finds_nothing_left_to_send(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, german = delivered(net, roots)
    assert post(world, german).status_code == 200
    assert post(world, german).status_code == 404
    assert len(rows(TrashEntry)) == 1 and len(rows(BlocklistEntry)) == 1
    assert len([line for line in rows(ProcureHistory) if line.result == "removed"]) == 1


def test_the_page_of_the_book_names_the_release_to_those_who_look_after_the_library(operator: Any, net: FakeNetwork,
                                                                                    roots: Path) -> None:
    world, _german = delivered(net, roots)
    shown = world.client.get(f"/api/v1/works/{world.work_id}").json()
    by_language = {edition["language"]: edition for edition in shown["editions"]}
    assert by_language["de"]["delivery"]["release"] == BOOK and by_language["en"]["delivery"] is None
    assert "path" not in str(by_language["de"]["delivery"]).lower()
    reader = make_account("rob")
    join(world.library_id, reader.id, "read")
    with new_client(reader) as rob:
        seen = rob.get(f"/api/v1/works/{world.work_id}").json()
        assert all(edition["delivery"] is None for edition in seen["editions"])
    join(world.library_id, world.person.id, "acquire")
    seen = world.client.get(f"/api/v1/works/{world.work_id}").json()
    assert all(edition["delivery"] is None for edition in seen["editions"])


def test_a_book_that_was_alone_in_its_work_takes_the_work_with_it(operator: Any, net: FakeNetwork, roots: Path) -> None:
    """The English book goes first; the German one is the only edition of the work then. Nothing names the book any more,
    so no wish is made, and the answer says so."""
    world, german = delivered(net, roots)
    with SessionLocal() as db:
        english = next(edition for edition in db.scalars(select(Edition)) if edition.language == "en")
        files = list(db.scalars(select(BookFile).where(BookFile.edition_id == english.id)))
        for row in files:
            db.delete(row)
        db.delete(english)
        download = db.scalar(select(Download).where(Download.edition_id == german.id))
        assert download is not None
        download.foreign_id = None
        db.commit()
    answer = post(world, german)
    assert answer.status_code == 200, answer.text
    assert answer.json()["replacement"] == "none" and answer.json()["work_id"] is None
    with SessionLocal() as db:
        assert db.scalar(select(Work.id)) is None
        assert len(list(db.scalars(select(TrashEntry)))) == 1
        # The block waits under the key of the book (its work is gone), as the scan leaves it for a book that comes back.
        [blocked] = list(db.scalars(select(BlocklistEntry)))
        assert blocked.work_id is None and blocked.book_key


def test_a_book_of_the_sources_is_wished_again_when_its_work_goes_with_the_edition(operator: Any, net: FakeNetwork,
                                                                                   roots: Path) -> None:
    world, german = delivered(net, roots)
    token = "tok" + "x" * 13
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:91", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de"))
        db.flush()
        book = db.scalar(select(ForeignBook).where(ForeignBook.token == token))
        english = next(edition for edition in db.scalars(select(Edition)) if edition.language == "en")
        for row in list(db.scalars(select(BookFile).where(BookFile.edition_id == english.id))):
            db.delete(row)
        db.delete(english)
        download = db.scalar(select(Download).where(Download.edition_id == german.id))
        assert download is not None and book is not None
        download.foreign_id = book.id
        db.commit()
    answer = post(world, german)
    assert answer.status_code == 200, answer.text
    assert answer.json()["replacement"] == "searching" and answer.json()["work_id"] is None
    [wish] = [row for row in rows(ProcureWish) if row.state != "done"]
    assert wish.work_id is None and wish.foreign_id is not None and wish.kind == "ebook"


def test_the_block_stays_with_the_book_when_the_work_was_called_otherwise_and_goes(operator: Any, net: FakeNetwork,
                                                                                   roots: Path) -> None:
    """The work is the only thing the block stood on, and its key is not the key of the book of the sources: the release
    would be fetched again."""
    world, german = delivered(net, roots)
    with SessionLocal() as db:
        db.add(ForeignBook(token="tok" + "y" * 13, ref="hardcover:92", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de"))
        db.flush()
        book = db.scalar(select(ForeignBook).where(ForeignBook.ref == "hardcover:92"))
        english = next(edition for edition in db.scalars(select(Edition)) if edition.language == "en")
        for row in list(db.scalars(select(BookFile).where(BookFile.edition_id == english.id))):
            db.delete(row)
        db.delete(english)
        download = db.scalar(select(Download).where(Download.edition_id == german.id))
        assert download is not None and book is not None
        download.foreign_id = book.id
        edition = db.get(Edition, german.id)
        assert edition is not None
        work = db.get(Work, edition.work_id)
        assert work is not None
        work.title = "Die Laternenstraße: Buch 1&2"
        book_id = book.id
        db.commit()
    assert post(world, german).status_code == 200
    [entry] = rows(BlocklistEntry)
    assert entry.foreign_id == book_id


def test_a_book_that_went_in_as_a_book_of_its_own_is_wished_again_under_the_book_that_was_wished(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    """The files named another book and stand in a work of their own: that work goes with them, and the wish is made for
    the work that was wished, which is still there."""
    world, german = delivered(net, roots)
    assert world.client.post(f"/api/v1/editions/{german.id}/split").status_code == 200
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Work)))) == 2
    answer = post(world, german)
    assert answer.status_code == 200, answer.text
    assert answer.json()["replacement"] == "searching" and answer.json()["work_id"] == world.work_id
    with SessionLocal() as db:
        assert [work.id for work in db.scalars(select(Work))] == [world.work_id]
    [wish] = [row for row in rows(ProcureWish) if row.state != "done"]
    assert wish.work_id == world.work_id and wish.kind == "ebook"


def test_when_the_files_cannot_go_to_the_trash_nothing_is_blocked_or_written(operator: Any, net: FakeNetwork,
                                                                             roots: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer says nothing was changed, so the blocklist, the history and the shelf must say it too."""
    from app.services.procure import trash

    world, german = delivered(net, roots)
    shelf = library_path(world)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("read-only")

    monkeypatch.setattr(trash, "put", refuse)
    answer = post(world, german)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "trash_failed"
    assert rows(BlocklistEntry) == [] and rows(TrashEntry) == []
    assert [line for line in rows(ProcureHistory) if line.result == "removed"] == []
    assert sorted(edition.language for edition in editions()) == ["de", "en"]
    assert any("Laternenstra" in name for name in files_in(shelf))
    # Once the trash works again, the same click goes through.
    monkeypatch.undo()
    assert post(world, german).status_code == 200
    assert len(rows(BlocklistEntry)) == 1
