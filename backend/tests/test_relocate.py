"""Moving an edition into another library.

The rules under test: **the edition stays the same** (its id, and with it the progress, the bookmarks, the history and the
corrections; no scan afterwards sees it gone or new), **every file goes** (all formats of an e-book, all parts of a
recording with the files beside it), **nothing is ever overwritten**, **a move that fails leaves no half edition** (it says
where the files lie and can be tried again), **a scan in between does not take the files of a move for a new book**, and
**only who manages both libraries may**, after confirming it."""

from __future__ import annotations

import errno
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    ROLE_BOOK,
    BookFile,
    Bookmark,
    Edition,
    HistoryEntry,
    MatchEntry,
    Progress,
    Relocation,
    RelocationFile,
    Work,
)
from app.services import jobs, relocate
from app.services.procure import placing

from .bookmaker import cover_image, make_pdf
from .conftest import make_account, new_client
from .library_helpers import age, book, books_folder, join, make_library, recording, run_jobs

AUTHOR = "Ada Feldkamp"
TITLE = "Orchard Of Whispers"


def files_in(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)).replace("\\", "/") for path in folder.rglob("*") if path.is_file())


def folders_in(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)).replace("\\", "/") for path in folder.rglob("*") if path.is_dir())


class World:
    """Two libraries, ``Shelf`` (with the books) and ``Annex`` (empty), both managed by ``owner``."""

    def __init__(self, *, audio: bool = False, kind: str = "both") -> None:
        self.shelf = books_folder("shelf")
        self.annex = books_folder("annex")
        if audio:
            for number in (1, 2):
                recording(self.shelf, f"{AUTHOR}/Moor/0{number}.mp3", 3.0, title=f"Moor {number}", album="Moor",
                          artist=AUTHOR, track=number)
            (self.shelf / AUTHOR / "Moor" / "cover.jpg").write_bytes(cover_image((30, 80, 40), kind="JPEG"))
            (self.shelf / AUTHOR / "Moor" / "metadata.json").write_text(
                json.dumps({"title": "Moor", "authors": [AUTHOR]}), encoding="utf-8")
            for name in ("cover.jpg", "metadata.json"):
                age(self.shelf / AUTHOR / "Moor" / name)
        else:
            book(self.shelf, f"{AUTHOR}/Orchard.epub", title=TITLE, authors=(AUTHOR,), language="en")
            age(make_pdf(self.shelf / AUTHOR / "Orchard.pdf", title=TITLE, author=AUTHOR, pages=3))
        # A book that stays, so that the shelf is no empty mount point after the move.
        book(self.shelf, "Other Author/Stays.epub", title="Stays Here", authors=("Other Author",), language="en")
        self.shelf_id = make_library(self.shelf, "Shelf", scan=True)
        self.annex_id = make_library(self.annex, "Annex", kind=kind, scan=True)
        self.owner = make_account("owner")
        join(self.shelf_id, self.owner.id, "manage")
        join(self.annex_id, self.owner.id, "manage")
        self.client = new_client(self.owner)
        with SessionLocal() as db:
            title = "Moor 1" if audio else TITLE
            row = db.scalar(select(Edition).where(Edition.library_id == self.shelf_id,
                                                  Edition.title.in_((title, "Moor"))))
            assert row is not None, [e.title for e in db.scalars(select(Edition))]
            self.edition, self.work = row.id, row.work_id

    def options(self) -> dict[str, Any]:
        answer = self.client.get(f"/api/v1/editions/{self.edition}/move")
        assert answer.status_code == 200, answer.text
        return answer.json()  # type: ignore[no-any-return]

    def move(self, library_id: int | None = None, *, confirm: bool = True, expect: int = 202) -> dict[str, Any]:
        answer = self.client.post(f"/api/v1/editions/{self.edition}/move",
                                  json={"library_id": library_id or self.annex_id, "confirm": confirm})
        assert answer.status_code == expect, answer.text
        return answer.json()  # type: ignore[no-any-return]

    def state(self, move_id: int) -> dict[str, Any]:
        answer = self.client.get(f"/api/v1/moves/{move_id}")
        assert answer.status_code == 200, answer.text
        return answer.json()  # type: ignore[no-any-return]


def edition_row(ident: int) -> Edition:
    with SessionLocal() as db:
        row = db.get(Edition, ident)
        assert row is not None
        db.expunge(row)
        return row


def counts() -> tuple[int, int, int]:
    with SessionLocal() as db:
        return (db.scalar(select(func.count(Edition.id))) or 0, db.scalar(select(func.count(Work.id))) or 0,
                db.scalar(select(func.count(BookFile.id)).where(BookFile.role == ROLE_BOOK)) or 0)


def rescan(*library_ids: int) -> None:
    with SessionLocal() as db:
        for ident in library_ids:
            jobs.enqueue(db, "scan", ref=ident)
        db.commit()
    run_jobs()


# --- The edition stays the same ---------------------------------------------------------------------------------------


def test_an_e_book_moves_with_all_its_formats_and_keeps_what_hangs_on_it() -> None:
    world = World()
    client = world.client
    ident = world.edition
    assert client.put(f"/api/v1/editions/{ident}/progress",
                      json={"locator": "epubcfi(/6/4!/4/2)", "fraction": 0.4}).status_code == 200
    assert client.post(f"/api/v1/editions/{ident}/bookmarks",
                       json={"locator": "epubcfi(/6/4!/4/2)", "fraction": 0.4}).status_code in (200, 201)
    assert client.post(f"/api/v1/editions/{ident}/finished", json={}).status_code in (200, 201)
    assert client.patch(f"/api/v1/editions/{ident}", json={"genre": "krimi"}).status_code == 200
    before = counts()
    options = world.options()
    [target] = options["targets"]
    assert (target["id"], target["writable"], target["problem"]) == (world.annex_id, True, "")
    assert sorted(target["preview"]) == [f"{AUTHOR}/{TITLE}.epub", f"{AUTHOR}/{TITLE}.pdf"]
    move = world.move()
    assert (move["state"], move["step"], move["where"]) == ("queued", "place", "source")
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["step"], shown["where"], shown["files"], shown["removed"]) == (
        "done", "done", "target", 2, 2)
    assert files_in(world.annex) == [f"{AUTHOR}/{TITLE}.epub", f"{AUTHOR}/{TITLE}.pdf"]
    # The source lost the files and the folder they made empty; the other book stays.
    assert files_in(world.shelf) == ["Other Author/Stays.epub"]
    assert folders_in(world.shelf) == ["Other Author"]
    row = edition_row(ident)
    assert row.library_id == world.annex_id and row.missing_at is None and row.work_id == world.work
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress).where(Progress.edition_id == ident)) == 1
        assert db.scalar(select(func.count(Bookmark.id)).where(Bookmark.edition_id == ident)) == 1
        assert db.scalar(select(func.count(HistoryEntry.id)).where(HistoryEntry.edition_id == ident)) == 1
        assert db.scalar(select(MatchEntry.library_id).where(MatchEntry.edition_id == ident)) in (None, world.annex_id)
        assert {path for path, in db.execute(select(BookFile.path).where(BookFile.edition_id == ident))} == {
            f"{AUTHOR}/{TITLE}.epub", f"{AUTHOR}/{TITLE}.pdf"}
    assert row.overlay["genre"]["by"] == "hand"
    # The scans of both libraries (the move asks for them, and they are asked again) see nothing gone and nothing new.
    rescan(world.shelf_id, world.annex_id)
    assert counts() == before
    again = edition_row(ident)
    assert again.library_id == world.annex_id and again.missing_at is None
    assert client.get(f"/api/v1/editions/{ident}/progress").json()["fraction"] == pytest.approx(0.4)
    # The book is found in its new library, and the search knows it there.
    assert client.get(f"/api/v1/works?library={world.annex_id}").json()["total"] == 1
    assert TITLE in client.get("/api/v1/search", params={"q": "orchard"}).text


def test_a_recording_moves_with_its_parts_and_the_files_beside_it() -> None:
    world = World(audio=True)
    before = counts()
    move = world.move()
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    moved = files_in(world.annex)
    assert len(moved) == 4 and {path.rsplit("/", 1)[-1] for path in moved} == {
        "01.mp3", "02.mp3", "cover.jpg", "metadata.json"}
    assert len({path.rsplit("/", 1)[0] for path in moved}) == 1, "one folder for the recording"
    assert files_in(world.shelf) == ["Other Author/Stays.epub"]
    rescan(world.shelf_id, world.annex_id)
    assert counts() == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Edition.id)).where(Edition.library_id == world.annex_id)) == 1
        assert edition_row(world.edition).library_id == world.annex_id
        sides = db.scalars(select(BookFile.path).where(BookFile.library_id == world.annex_id,
                                                       BookFile.role != ROLE_BOOK)).all()
        assert sorted(path.rsplit("/", 1)[-1] for path in sides) == ["cover.jpg", "metadata.json"]


def test_it_copies_when_the_target_is_another_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    real_link = os.link

    def no_links(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        if "annex" in str(target):
            raise OSError(errno.EXDEV, "another disk")
        real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_links)
    data = (world.shelf / AUTHOR / "Orchard.epub").read_bytes()
    move = world.move()
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    assert (world.annex / AUTHOR / f"{TITLE}.epub").read_bytes() == data
    with SessionLocal() as db:
        assert set(db.scalars(select(RelocationFile.how))) == {"copy"}
    assert not [path for path in files_in(world.annex) if path.rsplit("/", 1)[-1].startswith(".")]


# --- Never overwritten ------------------------------------------------------------------------------------------------


def test_a_goal_that_is_taken_is_never_used() -> None:
    world = World()
    taken = world.annex / AUTHOR / f"{TITLE}.epub"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"somebody else's book")
    move = world.move()
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    assert taken.read_bytes() == b"somebody else's book"
    assert sorted(files_in(world.annex)) == [f"{AUTHOR}/{TITLE} (2).epub", f"{AUTHOR}/{TITLE} (2).pdf",
                                             f"{AUTHOR}/{TITLE}.epub"]


def test_a_goal_taken_while_the_move_runs_stops_it_and_nothing_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    source_files = files_in(world.shelf)
    real = placing.place
    intruder: list[Path] = []

    def first_then_intruder(source: Path, size: int, library: Path, target: str, *, how: str) -> str:
        if intruder:
            # Somebody puts a file at the second goal, just before nexbooks gets there.
            goal = library.joinpath(*target.split("/"))
            goal.write_bytes(b"not nexbooks'")
            intruder.append(goal)
        else:
            intruder.append(library)
        return real(source, size, library, target, how=how)

    monkeypatch.setattr(placing, "place", first_then_intruder)
    move = world.move()
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["step"], shown["error"], shown["where"]) == ("failed", "place", "taken", "source")
    # The stranger's file stays; what nexbooks put there went again; the source is as it was.
    assert intruder[-1].read_bytes() == b"not nexbooks'"
    assert files_in(world.annex) == [str(intruder[-1].relative_to(world.annex)).replace("\\", "/")]
    assert files_in(world.shelf) == source_files
    assert edition_row(world.edition).library_id == world.shelf_id
    # Tried again (the stranger's file still there), it takes another name.
    monkeypatch.setattr(placing, "place", real)
    again = world.client.post(f"/api/v1/moves/{move['id']}/retry", json={"confirm": True})
    assert again.status_code == 202, again.text
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    assert intruder[-1].read_bytes() == b"not nexbooks'"
    assert edition_row(world.edition).library_id == world.annex_id


def test_a_goal_taken_after_the_plan_is_never_taken_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan found the goals free; before the files get there, somebody puts a file at the second one. That file is
    not the move's (it is no second name of the source and not its content): the move stops, the stranger's file stays,
    and what the move had put at the first goal goes again."""
    world = World()
    source_files = files_in(world.shelf)
    real = relocate.plan
    strangers: list[Path] = []

    def planned_then_taken(*args: Any, **kwargs: Any) -> relocate.Plan:
        found = real(*args, **kwargs)
        goal = world.annex.joinpath(*found.goals[-1].target.split("/"))
        goal.parent.mkdir(parents=True, exist_ok=True)
        goal.write_bytes(b"x" * found.goals[-1].file.size)
        strangers.append(goal)
        return found

    move = world.move()
    monkeypatch.setattr(relocate, "plan", planned_then_taken)
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["error"], shown["where"]) == ("failed", "target_taken", "source")
    assert strangers[-1].read_bytes() == b"x" * strangers[-1].stat().st_size
    assert files_in(world.annex) == [str(strangers[-1].relative_to(world.annex)).replace("\\", "/")]
    assert files_in(world.shelf) == source_files
    assert edition_row(world.edition).library_id == world.shelf_id


# --- Failures in the middle -------------------------------------------------------------------------------------------


def test_no_room_in_the_middle_puts_back_what_was_done_and_says_where_the_book_lies(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    source_files = files_in(world.shelf)
    real = placing.place
    calls: list[str] = []

    def full_at_the_second(source: Path, size: int, library: Path, target: str, *, how: str) -> str:
        calls.append(target)
        if len(calls) == 2:
            raise placing.PlaceError("no_space")
        return real(source, size, library, target, how=how)

    monkeypatch.setattr(placing, "place", full_at_the_second)
    move = world.move()
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["error"], shown["where"], shown["left"]) == ("failed", "no_space", "source", 0)
    assert files_in(world.annex) == [] and folders_in(world.annex) == []
    assert files_in(world.shelf) == source_files
    assert edition_row(world.edition).library_id == world.shelf_id
    # The scan of the target finds nothing to take for a new book.
    before = counts()
    rescan(world.annex_id, world.shelf_id)
    assert counts() == before
    monkeypatch.setattr(placing, "place", real)
    assert world.client.post(f"/api/v1/moves/{move['id']}/retry", json={"confirm": True}).status_code == 202
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    assert edition_row(world.edition).library_id == world.annex_id


def test_a_stop_in_the_middle_goes_on_after_the_restart_and_a_scan_between_takes_nothing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(audio=True)
    before = counts()
    real = placing.place
    calls: list[str] = []

    def stop_after_the_first(source: Path, size: int, library: Path, target: str, *, how: str) -> str:
        done = real(source, size, library, target, how=how)
        calls.append(target)
        if len(calls) == 1:
            jobs.stopping.set()
        return done

    monkeypatch.setattr(placing, "place", stop_after_the_first)
    move = world.move()
    jobs.run_pending(10)
    jobs.stopping.clear()
    with SessionLocal() as db:
        row = db.get(Relocation, move["id"])
        assert row is not None and (row.state, row.step) == ("running", "place")
        # The server stopped: the job waits again.
        assert db.scalar(select(func.count()).select_from(RelocationFile).where(
            RelocationFile.relocation_id == row.id, RelocationFile.state == "placed")) == 1
    assert len(files_in(world.annex)) == 1
    # A scan of the target meets the file of the move first: it is no new book.
    from app.services import scanner

    with SessionLocal() as db:
        from app.models import Library

        library = db.get(Library, world.annex_id)
        assert library is not None
        scanner.run(db, library, scanner.Report())
        db.commit()
    assert counts() == before
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    rescan(world.shelf_id, world.annex_id)
    assert counts() == before
    assert edition_row(world.edition).library_id == world.annex_id


def test_a_source_that_cannot_be_taken_away_leaves_the_book_in_the_target_and_says_so(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    before = counts()
    real_unlink = os.unlink

    def locked(path: Any, *args: Any, **kwargs: Any) -> None:
        if "shelf" in str(path) and str(path).endswith(".pdf"):
            raise PermissionError(errno.EACCES, "locked")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", locked)
    move = world.move()
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["step"], shown["error"], shown["where"], shown["left"]) == (
        "failed", "clear", "source_left", "both", 1)
    assert edition_row(world.edition).library_id == world.annex_id
    assert (world.shelf / AUTHOR / "Orchard.pdf").is_file()
    # The file left at the source is the move's: a scan of the source does not take it for a new book.
    rescan(world.shelf_id, world.annex_id)
    assert counts() == before
    monkeypatch.setattr(os, "unlink", real_unlink)
    assert world.client.post(f"/api/v1/moves/{move['id']}/retry", json={"confirm": True}).status_code == 202
    run_jobs()
    assert world.state(move["id"])["state"] == "done"
    assert files_in(world.shelf) == ["Other Author/Stays.epub"]
    rescan(world.shelf_id, world.annex_id)
    assert counts() == before


def test_a_source_is_taken_away_only_while_its_twin_lies_whole_at_the_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the switch a file at its goal is cut short (a disk that failed, somebody's hand): its source is the copy that
    counts and stays; the move stops and says so."""
    world = World()
    real = relocate._switch

    def switched_then_cut(db: Any, row: Any, edition: Any, source: Any, target: Any, files: Any) -> None:
        real(db, row, edition, source, target, files)
        goal = world.annex / AUTHOR / f"{TITLE}.pdf"
        data = goal.read_bytes()
        goal.unlink()
        goal.write_bytes(data[: len(data) // 2])

    monkeypatch.setattr(relocate, "_switch", switched_then_cut)
    move = world.move()
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["step"], shown["error"]) == ("failed", "clear", "target_changed")
    assert (world.shelf / AUTHOR / "Orchard.pdf").is_file()
    assert (world.shelf / AUTHOR / "Orchard.pdf").stat().st_size > (world.annex / AUTHOR / f"{TITLE}.pdf").stat().st_size


def test_a_source_that_changed_is_not_moved() -> None:
    world = World()
    with SessionLocal() as db:
        row = db.scalar(select(BookFile).where(BookFile.edition_id == world.edition, BookFile.format == "pdf"))
        assert row is not None
        row.size += 1
        db.commit()
    move = world.move()
    run_jobs()
    shown = world.state(move["id"])
    assert (shown["state"], shown["error"], shown["where"]) == ("failed", "source_changed", "source")
    assert files_in(world.annex) == []


# --- Who, and only after a yes ----------------------------------------------------------------------------------------


def test_nothing_moves_without_the_confirmation() -> None:
    world = World()
    answer = world.move(confirm=False, expect=422)
    assert answer["detail"]["code"] == "confirm_needed"
    run_jobs()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Relocation.id))) == 0
    assert files_in(world.annex) == [] and edition_row(world.edition).library_id == world.shelf_id


def test_only_who_manages_both_libraries_may_move_the_operator_included() -> None:
    world = World()
    reader = make_account("reader")
    join(world.shelf_id, reader.id, "read")
    join(world.annex_id, reader.id, "manage")
    half = make_account("half")
    join(world.shelf_id, half.id, "manage")
    join(world.annex_id, half.id, "read")
    outside = make_account("outside")
    join(world.shelf_id, outside.id, "manage")
    stranger = make_account("stranger")
    from .library_helpers import operator_account

    ops = operator_account()
    # The operator made both libraries and so manages them; taken out of them, the role alone is no right.
    from app.models import LibraryMember

    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=ops.id).delete()
        db.commit()
    body = {"library_id": world.annex_id, "confirm": True}
    for account, options, post in ((reader, 403, 403), (half, 200, 403), (outside, 200, 404), (stranger, 404, 404),
                                   (ops, 404, 404)):
        with new_client(account) as person:
            assert person.get(f"/api/v1/editions/{world.edition}/move").status_code == options, account.name
            assert person.post(f"/api/v1/editions/{world.edition}/move", json=body).status_code == post, account.name
    # The one who manages the source but only reads the target is not offered it.
    with new_client(half) as person:
        assert person.get(f"/api/v1/editions/{world.edition}/move").json()["targets"] == []
        work = person.get(f"/api/v1/works/{world.work}").json()
        assert [edition["can_move"] for edition in work["editions"]] == [False]
    work = world.client.get(f"/api/v1/works/{world.work}").json()
    assert [edition["can_move"] for edition in work["editions"]] == [True]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Relocation.id))) == 0
    move = world.move()
    for account in (reader, stranger, ops):
        with new_client(account) as person:
            if account is reader:
                # Who manages the target sees how a move into it goes; who sees neither library does not.
                assert person.get(f"/api/v1/moves/{move['id']}").status_code == 200
                continue
            assert person.get(f"/api/v1/moves/{move['id']}").status_code == 404
            assert person.post(f"/api/v1/moves/{move['id']}/retry", json={"confirm": True}).status_code == 404


def test_a_library_that_does_not_take_the_kind_or_cannot_be_written_is_no_target(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(kind="audio")
    assert world.options()["targets"] == []
    assert world.move(expect=422)["detail"]["code"] == "move_kind"
    assert world.move(world.shelf_id, expect=422)["detail"]["code"] == "move_same"
    third = books_folder("third")
    third_id = make_library(third, "Third", scan=True)
    join(third_id, world.owner.id, "manage")
    from app.services.procure import formats

    real = formats.write_state
    monkeypatch.setattr(formats, "write_state",
                        lambda path: (False, "read_only") if "third" in str(path) else real(path))
    [shown] = world.options()["targets"]
    assert (shown["name"], shown["writable"], shown["write_problem"], shown["preview"]) == (
        "Third", False, "read_only", [])
    assert world.move(third_id, expect=409)["detail"]["code"] == "move_target_not_writable"
    work = world.client.get(f"/api/v1/works/{world.work}").json()
    assert [edition["can_move"] for edition in work["editions"]] == [False]


def test_two_presses_at_once_make_one_move(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    answers: list[int] = []
    gate = threading.Barrier(2)
    # Both presses get past the look whether a move runs before either writes one: only the database can keep them apart.
    real_busy = relocate._busy
    between = threading.Barrier(2)

    def both_look_first(*args: Any) -> bool:
        found = real_busy(*args)
        try:
            between.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        return found

    monkeypatch.setattr(relocate, "_busy", both_look_first)

    def press(client: Any) -> None:
        gate.wait()
        answers.append(client.post(f"/api/v1/editions/{world.edition}/move",
                                   json={"library_id": world.annex_id, "confirm": True}).status_code)

    with new_client(world.owner) as other:
        threads = [threading.Thread(target=press, args=(client,)) for client in (world.client, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert sorted(answers) == [202, 409], answers
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Relocation.id))) == 1
    monkeypatch.setattr(relocate, "_busy", real_busy)
    # While it waits, the next press is refused too, and the tidying of either library waits for it.
    assert world.move(expect=409)["detail"]["code"] == "move_busy"
    with SessionLocal() as db:
        assert relocate.busy_library(db, world.shelf_id) and relocate.busy_library(db, world.annex_id)
        from fastapi import HTTPException

        from app.models import Library
        from app.services import organize, settings_service

        settings_service.save(db, {"organize_enabled": True})
        for ident in (world.shelf_id, world.annex_id):
            library = db.get(Library, ident)
            assert library is not None
            with pytest.raises(HTTPException) as refused:
                organize.preview(db, library, "{Author}/{Title}", world.owner.id)
            assert refused.value.status_code == 409 and refused.value.detail["code"] == "organize_busy"
    run_jobs()
    with SessionLocal() as db:
        assert not relocate.busy_library(db, world.shelf_id)
    assert world.options()["latest"]["state"] == "done"
