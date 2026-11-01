"""The wrong book, the rest of it: a recording that lost a part, what the people had in the book (kept with the files and
given back with them), and the search that starts at once."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import (
    ROLE_SIDECAR,
    Account,
    BlocklistEntry,
    BookFile,
    Bookmark,
    Edition,
    HistoryEntry,
    KeptState,
    Library,
    ProcureHistory,
    ProcureWish,
    Progress,
    TrashEntry,
)
from app.services.procure import kept, trash

from .auto_helpers import configure
from .conftest import make_account
from .filing_helpers import download_roots, files_in, finish, hidden_in, library_path, recording
from .follow_helpers import AUDIO, rows
from .library_helpers import age_all, join, run_jobs, scan
from .procure_fakes import FakeNetwork
from .test_wrong_book import delivered, editions, post


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def forget_gone_files(*, library_id: int) -> list[str]:
    """The scan has noticed that files are gone from the disk: their rows are flagged. Returns their names."""
    with SessionLocal() as db:
        names = []
        for row in db.scalars(select(BookFile).where(BookFile.library_id == library_id)):
            if not (Path(db.get(Library, library_id).path) / row.path).exists():  # type: ignore[union-attr]
                row.missing_at = clock.now()
                names.append(row.path)
        db.commit()
    return sorted(names)


def audio_delivered(net: FakeNetwork, roots: Path, parts: int = 2) -> tuple[Any, Edition]:
    """A recording fetched for the wish (in a ``CD 1`` folder) and filed; the person who wished it manages the library."""
    from .filing_helpers import audio_wish

    world, sab, job = audio_wish(net)
    folder = roots / "complete" / AUDIO
    recording(folder / "CD 1", parts=parts)
    finish(sab, job, folder)
    join(world.library_id, world.person.id, "manage")
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    return world, audio


# --- A file that is gone from the disk -----------------------------------------------------------------------


def test_a_part_that_is_gone_from_the_disk_does_not_keep_the_wrong_book_on_the_shelf(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, audio = audio_delivered(net, roots)
    shelf = library_path(world)
    [part] = list(shelf.rglob("02.mp3"))
    part.unlink()
    flagged = forget_gone_files(library_id=world.library_id)
    assert len(flagged) == 1 and flagged[0].endswith("02.mp3")
    answer = post(world, audio)
    assert answer.status_code == 200, answer.text
    sent = answer.json()
    # What is there goes to the trash, what is not is named.
    assert sent["trash_id"] and sent["missing"] == flagged
    assert not [name for name in files_in(shelf) if name.endswith(".mp3")]
    assert [name for name in hidden_in(shelf) if name.endswith("01.mp3")] and len(rows(TrashEntry)) == 1
    assert len(rows(BlocklistEntry)) == 1 and not [e for e in editions() if e.kind == "audio"]
    with SessionLocal() as db:
        assert list(db.scalars(select(BookFile).where(BookFile.edition_id == audio.id))) == []
        [line] = [row for row in db.scalars(select(ProcureHistory)) if row.result == "removed"]
        assert line.note["missing"] == 1 and line.note["trash_id"] == sent["trash_id"]


def test_a_book_whose_files_are_all_gone_is_sent_away_without_a_trash_entry(operator: Any, net: FakeNetwork,
                                                                          roots: Path) -> None:
    world, german = delivered(net, roots)
    shelf = library_path(world)
    folders = []
    for path in [p for p in shelf.rglob("*.epub") if "Laternenstra" in p.name]:
        folders.append(path.parent)
        path.unlink()
    assert len(forget_gone_files(library_id=world.library_id)) == 1
    answer = post(world, german)
    assert answer.status_code == 200, answer.text
    sent = answer.json()
    assert sent["trash_id"] is None and len(sent["missing"]) == 1 and sent["replacement"] == "searching"
    # The folder the book lay in is left empty by the loss: it goes too.
    assert folders and not any(folder.exists() for folder in folders)
    assert rows(TrashEntry) == [] and len(rows(BlocklistEntry)) == 1
    assert [edition.language for edition in editions()] == ["en"]


def book_folder_of(shelf: Path) -> Path:
    [first] = [path for path in shelf.rglob("01.mp3")]
    return first.parent.parent


def test_a_recording_takes_the_files_beside_it_and_its_empty_folders_with_it(operator: Any, net: FakeNetwork,
                                                                           roots: Path) -> None:
    """``metadata.json`` beside the parts and ``cover.jpg`` beside the disc folder belong to the recording alone: they
    go to the trash with it, and no empty folder of it stays on the shelf."""
    from .bookmaker import cover_image

    def beside(folder: Path) -> None:
        (folder / "cover.jpg").write_bytes(cover_image(kind="JPEG"))
        (folder / "CD 1" / "metadata.json").write_text("{}", encoding="utf-8")

    from .filing_helpers import audio_wish

    world, sab, job = audio_wish(net)
    folder = roots / "complete" / AUDIO
    recording(folder / "CD 1", parts=2)
    beside(folder)
    finish(sab, job, folder)
    join(world.library_id, world.person.id, "manage")
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    shelf = library_path(world)
    book = book_folder_of(shelf)
    assert {"cover.jpg"} <= {path.name for path in book.iterdir()}
    assert post(world, audio).status_code == 200
    assert not book.exists(), "no empty folder of the recording stays"
    held = hidden_in(shelf)
    assert [name for name in held if name.endswith("cover.jpg")] and [name for name in held if name.endswith("metadata.json")]
    assert len([name for name in held if name.endswith(".mp3")]) == 2
    with SessionLocal() as db:
        assert db.scalar(select(BookFile.id).where(BookFile.role == ROLE_SIDECAR)) is None


def test_a_folder_another_book_lies_in_keeps_its_files(operator: Any, net: FakeNetwork, roots: Path) -> None:
    """A cover beside the disc folders is the recording's own only while no other book lies below that folder."""
    from .bookmaker import cover_image
    from .filing_helpers import audio_wish

    world, sab, job = audio_wish(net)
    folder = roots / "complete" / AUDIO
    recording(folder / "CD 1", parts=2)
    (folder / "cover.jpg").write_bytes(cover_image(kind="JPEG"))
    finish(sab, job, folder)
    join(world.library_id, world.person.id, "manage")
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    shelf = library_path(world)
    book = book_folder_of(shelf)
    recording(book / "Another Tale", parts=1, title="Another Tale", album="Another Tale", artist="Ida Moor")
    age_all(shelf)
    scan(world.library_id)
    assert len([edition for edition in editions() if edition.kind == "audio"]) == 2
    assert post(world, audio).status_code == 200
    assert (book / "cover.jpg").is_file() and (book / "Another Tale" / "01.mp3").is_file()
    assert not (book / "CD 1").exists()
    assert [name for name in hidden_in(shelf) if name.endswith("cover.jpg")] == []


def test_two_clicks_at_once_send_it_away_once(operator: Any, net: FakeNetwork, roots: Path) -> None:
    import threading

    from .conftest import new_client

    world, german = delivered(net, roots)
    results: list[int] = []

    def click() -> None:
        with new_client(world.person) as client:
            results.append(client.post(f"/api/v1/editions/{german.id}/wrong-book").status_code)

    threads = [threading.Thread(target=click) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 404, 404, 404]
    assert len(rows(TrashEntry)) == 1 and len(rows(BlocklistEntry)) == 1


# --- What the people had in the book -------------------------------------------------------------------------


def give_states(edition: Edition, *people: int) -> None:
    with SessionLocal() as db:
        for uid in people:
            db.add(Progress(user_id=uid, edition_id=edition.id, locator="epubcfi(/6/4)", fraction=0.9, unit="chapter",
                            chapter=4, chapter_count=5, chapter_title="Four"))
            db.add(Bookmark(user_id=uid, edition_id=edition.id, locator=f"mark-{uid}", fraction=0.2, note="a note"))
            db.add(HistoryEntry(user_id=uid, edition_id=edition.id, kind="read", finished_on="2026-10-01",
                                source="hand"))
        db.commit()


def states_of_german() -> dict[int, tuple[Any, ...]]:
    """For each person the place, the mark and the finished pass they have in the German edition now."""
    with SessionLocal() as db:
        [german] = [e for e in db.scalars(select(Edition)) if e.language == "de"]
        marks = {row.user_id: (row.locator, row.note) for row in db.scalars(select(Bookmark).where(
            Bookmark.edition_id == german.id))}
        passes = {row.user_id: row.finished_on for row in db.scalars(select(HistoryEntry).where(
            HistoryEntry.edition_id == german.id))}
        return {row.user_id: (row.locator, row.fraction, row.chapter_title, marks.get(row.user_id),
                              passes.get(row.user_id))
                for row in db.scalars(select(Progress).where(Progress.edition_id == german.id))}


def test_the_places_marks_and_passes_of_everybody_come_back_with_the_files(operator: Any, net: FakeNetwork,
                                                                         roots: Path) -> None:
    world, german = delivered(net, roots)
    rob = make_account("rob")
    join(world.library_id, rob.id, "read")
    give_states(german, world.person.id, rob.id)
    shelf = library_path(world)
    sent = post(world, german).json()
    # Gone with the edition, but written down with the files: two people, kept with the trash entry.
    assert sent["people"] == 2
    with SessionLocal() as db:
        assert db.scalar(select(Progress.user_id)) is None and db.scalar(select(Bookmark.id)) is None
        assert db.scalar(select(HistoryEntry.id)) is None
    [note] = rows(KeptState)
    assert note.trash_id == sent["trash_id"] and note.library_id == world.library_id
    answer = world.client.post(f"/api/v1/procure/trash/{sent['trash_id']}/restore")
    assert answer.status_code == 204, answer.text
    [note] = rows(KeptState)
    assert note.trash_id is None, "the notes wait for the scan now"
    age_all(shelf)
    run_jobs()
    assert rows(KeptState) == []
    one = ("epubcfi(/6/4)", 0.9, "Four")
    assert states_of_german() == {
        world.person.id: (*one, (f"mark-{world.person.id}", "a note"), "2026-10-01"),
        rob.id: (*one, (f"mark-{rob.id}", "a note"), "2026-10-01")}


def test_what_a_person_did_after_the_files_came_back_is_not_written_over(operator: Any, net: FakeNetwork,
                                                                       roots: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan makes the edition, then the notes are given back: a place somebody has by then stays, and a person who
    left the instance gets nothing."""
    world, german = delivered(net, roots)
    gone = make_account("gus")
    join(world.library_id, gone.id, "read")
    give_states(german, world.person.id, gone.id)
    shelf = library_path(world)
    sent = post(world, german).json()
    with SessionLocal() as db:
        db.delete(db.get(Account, gone.id))
        db.commit()
    assert world.client.post(f"/api/v1/procure/trash/{sent['trash_id']}/restore").status_code == 204
    age_all(shelf)
    with monkeypatch.context() as patch:
        patch.setattr(kept, "reattach", lambda db, library_id: 0)
        run_jobs()
    assert len(rows(KeptState)) == 1
    with SessionLocal() as db:
        [new] = [e for e in db.scalars(select(Edition)) if e.language == "de"]
        db.add(Progress(user_id=world.person.id, edition_id=new.id, locator="start", fraction=0.01, unit="chapter"))
        db.commit()
    with SessionLocal() as db:
        assert kept.reattach(db, world.library_id) == 1
    assert rows(KeptState) == []
    assert states_of_german() == {world.person.id: ("start", 0.01, "", (f"mark-{world.person.id}", "a note"),
                                                    "2026-10-01")}


def test_nothing_is_kept_when_nobody_had_anything(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, german = delivered(net, roots)
    sent = post(world, german).json()
    assert sent["people"] == 0 and rows(KeptState) == []


def test_the_notes_go_with_the_trash_entry_when_it_is_deleted_for_good(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    world, german = delivered(net, roots)
    give_states(german, world.person.id)
    entry = post(world, german).json()["trash_id"]
    assert len(rows(KeptState)) == 1
    assert world.client.delete(f"/api/v1/procure/trash/{entry}").status_code == 204
    assert rows(KeptState) == []


def test_the_notes_go_with_the_trash_entry_when_its_time_is_over(operator: Any, net: FakeNetwork,
                                                                roots: Path) -> None:
    world, german = delivered(net, roots)
    give_states(german, world.person.id)
    post(world, german)
    assert len(rows(KeptState)) == 1
    with SessionLocal() as db:
        for entry in db.scalars(select(TrashEntry)):
            entry.expires_at = clock.now() - timedelta(minutes=1)
        db.commit()
    with SessionLocal() as db:
        assert trash.sweep(db) == 1
    assert rows(TrashEntry) == [] and rows(KeptState) == []


def test_notes_that_wait_for_files_that_never_come_are_swept_when_their_time_is_over(operator: Any, net: FakeNetwork,
                                                                                   roots: Path) -> None:
    world, german = delivered(net, roots)
    give_states(german, world.person.id)
    sent = post(world, german).json()
    assert world.client.post(f"/api/v1/procure/trash/{sent['trash_id']}/restore").status_code == 204
    [note] = rows(KeptState)
    assert note.trash_id is None and note.expires_at > clock.now() + timedelta(days=kept.WAIT_DAYS - 1)
    with SessionLocal() as db:
        db.get(KeptState, note.id).expires_at = clock.now() - timedelta(minutes=1)  # type: ignore[union-attr]
        db.commit()
    with SessionLocal() as db:
        trash.sweep(db)
    assert rows(KeptState) == []


def test_the_page_tells_how_long_the_trash_keeps_what_is_sent_away(operator: Any, net: FakeNetwork,
                                                                  roots: Path) -> None:
    world, _german = delivered(net, roots)
    shown = world.client.get(f"/api/v1/works/{world.work_id}").json()
    by_language = {edition["language"]: edition for edition in shown["editions"]}
    assert by_language["de"]["delivery"]["trash_days"] == trash.DEFAULT_DAYS


# --- The search starts at once -------------------------------------------------------------------------------


def test_a_wish_waiting_for_a_better_format_searches_at_once_and_forgets_the_book_that_is_gone(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    """A recording of a low quality leaves the wish in ``upgrade`` (its goal is better); after the wrong book that wish
    must not wait for its next try, and must not go on saying the book has a quality it no longer has."""
    world, audio = audio_delivered(net, roots)
    [wish] = rows(ProcureWish)
    assert wish.state == "upgrade" and wish.detail
    sent = post(world, audio).json()
    assert sent["replacement"] == "searching"
    [wish] = [row for row in rows(ProcureWish) if row.state != "done"]
    assert wish.state == "searching" and wish.search_id is not None and not wish.detail


def test_with_fetching_by_itself_switched_off_the_answer_says_nothing_searches(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    world, german = delivered(net, roots)
    configure(enabled=False)
    sent = post(world, german).json()
    assert sent["replacement"] == "idle"
    [wish] = [row for row in rows(ProcureWish) if row.state != "done"]
    assert wish.state == "idle" and wish.search_id is None


def test_what_a_person_had_goes_from_the_notes_when_their_account_is_deleted(client: Any, operator: Any,
                                                                           net: FakeNetwork, roots: Path) -> None:
    from .conftest import PASSWORD

    world, german = delivered(net, roots)
    rob = make_account("rob")
    join(world.library_id, rob.id, "read")
    give_states(german, world.person.id, rob.id)
    post(world, german)
    [note] = rows(KeptState)
    assert {item["user_id"] for item in note.states["progress"]} == {world.person.id, rob.id}
    answer = client.request("DELETE", f"/api/v1/accounts/{rob.id}", json={"confirm_name": "rob",
                                                                          "current_password": PASSWORD})
    assert answer.status_code == 204, answer.text
    [note] = rows(KeptState)
    assert all({item["user_id"] for item in part} == {world.person.id} for part in note.states.values())
    with SessionLocal() as db:
        kept.forget_person(db, world.person.id)
        db.commit()
    assert rows(KeptState) == []
