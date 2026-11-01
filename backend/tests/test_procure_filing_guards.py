"""Checking and filing, one test each (invented books only, made here): an upload of a recording as a ZIP,
long names that keep their ending, a filing that stopped between the new name and the old one, a release a program
reports inside a library, rights that are looked at with every file, two files of one name sent at once, the room for a
whole release, and the names that are no danger."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download, PlaceFile

from .bookmaker import make_epub, make_mp3
from .conftest import make_account, new_client
from .filing_helpers import (
    audio_wish,
    book_files,
    download_roots,
    ebook_world,
    editions,
    files_in,
    finish,
    german_epub,
    hidden_in,
    library_path,
    recording,
    the_download,
    world_with,
    zip_of,
)
from .library_helpers import age_all, books_folder, join, make_library, run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE

PLACED = "Rune Falkner/Nebelchor/02 - Die Laternenstraße"


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


def mp3_parts(tmp_path: Path, count: int = 2, **tags: Any) -> dict[str, bytes]:
    values: dict[str, Any] = {"title": "Ein Hörbuch", "album": "Ein Hörbuch", "artist": "Ida Brandt"}
    values.update(tags)
    return {f"CD 1/{number:02d}.mp3": make_mp3(tmp_path / f"{number}.mp3", seconds=3.0, track=number,
                                              **values).read_bytes() for number in range(1, count + 1)}


# --- A ZIP of a recording from the page of the library -------------------------------------------------------------


def test_a_zip_of_a_recording_without_a_kind_said_is_a_recording_not_an_e_book(operator: Any, net: FakeNetwork,
                                                                              tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    answer = send(world.client, upload["id"], "book.zip", zip_of(tmp_path / "book.zip", mp3_parts(tmp_path)).read_bytes())
    assert answer.status_code == 201, answer.text
    assert world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={}).status_code == 202
    run_jobs()
    shown = world.client.get(f"/api/v1/uploads/{upload['id']}").json()
    assert shown["phase"] == "done" and shown["kind"] == "audio", shown
    audio = [edition for edition in editions() if edition.kind == "audio"]
    assert len(audio) == 1
    assert [name for name in files_in(library_path(world)) if name.endswith(".mp3")]


def test_a_zip_of_a_recording_goes_into_a_library_of_recordings(operator: Any, net: FakeNetwork,
                                                                tmp_path: Path) -> None:
    folder = books_folder("only-audio")
    library = make_library(folder, "Recordings", kind="audio")
    person = make_account("anna")
    join(library, person.id, "acquire")
    client = new_client(person)
    upload = begin(client, library)
    assert send(client, upload["id"], "book.zip", zip_of(tmp_path / "book.zip", mp3_parts(tmp_path)).read_bytes()
                ).status_code == 201
    answer = client.post(f"/api/v1/uploads/{upload['id']}/finish", json={})
    assert answer.status_code == 202, answer.text
    run_jobs()
    assert client.get(f"/api/v1/uploads/{upload['id']}").json()["phase"] == "done"
    assert [name for name in files_in(folder) if name.endswith(".mp3")]


def test_a_zip_that_holds_an_e_book_and_a_recording_is_turned_down_not_guessed(operator: Any, net: FakeNetwork,
                                                                               tmp_path: Path) -> None:
    world = world_with(net, other_book)
    members = {**mp3_parts(tmp_path), "story.epub": make_epub(tmp_path / "e.epub", title="Eine Geschichte",
                                                               authors=("Ida Brandt",), language="de").read_bytes()}
    upload = begin(world.client, world.library_id)
    send(world.client, upload["id"], "both.zip", zip_of(tmp_path / "both.zip", members).read_bytes())
    assert world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={}).status_code == 202
    run_jobs()
    shown = world.client.get(f"/api/v1/uploads/{upload['id']}").json()
    assert shown["phase"] == "rejected" and shown["problem"] == "upload_mixed", shown
    assert not [name for name in files_in(library_path(world)) if "Brandt" in name or name.endswith(".mp3")]
    assert not hidden_in(library_path(world))


def test_a_zip_with_the_kind_said_keeps_it(operator: Any, net: FakeNetwork, tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id, kind="audio")
    send(world.client, upload["id"], "book.zip", zip_of(tmp_path / "book.zip", mp3_parts(tmp_path)).read_bytes())
    world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={})
    run_jobs()
    shown = world.client.get(f"/api/v1/uploads/{upload['id']}").json()
    assert shown["phase"] == "done" and shown["kind"] == "audio"


# --- A long name keeps its ending ----------------------------------------------------------------------------------


@pytest.mark.parametrize("stem", ["Kapitel " * 40, "ä" * 150, "日本語の長い名前" * 30, "a b " * 80])
def test_a_long_file_name_is_shortened_at_the_stem_never_at_the_ending(stem: str) -> None:
    from app.services.organize import scheme

    for ending in ("mp3", "epub", "m4b", "flac"):
        name = scheme.clean_file(f"{stem}.{ending}")
        assert name.endswith(f".{ending}"), name
        assert len(name) <= scheme.MAX_PART and len(name.encode("utf-8")) <= scheme.MAX_BYTES, len(name)
        assert name == name.encode("utf-8").decode("utf-8") and not name.startswith(".")
    # A short name stays as it is; a name Windows keeps for itself is made harmless; nothing left is nothing.
    assert scheme.clean_file("01 Anfang.mp3") == "01 Anfang.mp3"
    assert scheme.clean_file("con.mp3") == "_con.mp3"
    assert scheme.clean_file("...mp3") == "" or scheme.clean_file("...mp3").endswith("mp3")


def test_the_parts_of_a_long_named_recording_keep_their_ending_and_the_book_stays_in_the_library(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "rec"
    folder.mkdir(parents=True)
    long = "Kapitel " + "sehr langer Titel des Kapitels " * 5
    for number in (1, 2):
        make_mp3(folder / f"{number:02d} {long}.mp3", seconds=4.0, track=number, title=TITLE,
                 album=f"{TITLE} (ungekürzt)", artist=AUTHOR, series=SERIES, series_pos="2")
    finish(sab, job, folder)
    assert the_download().state == "imported"
    shelf = library_path(world)
    placed = [name for name in files_in(shelf) if name.startswith(PLACED)]
    assert len(placed) == 2 and all(name.endswith(".mp3") for name in placed), placed
    # The scan that follows finds the book whole: nothing is missing, nothing was a file without an ending.
    scan(world.library_id)
    assert not [row for row in book_files() if row.missing_at is not None]
    assert len([row for row in book_files() if row.role == "book" and row.path.startswith(PLACED)]) == 2


def test_an_e_book_with_a_long_name_is_taken_by_an_upload(operator: Any, net: FakeNetwork, tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    data = make_epub(tmp_path / "m.epub", title=TITLE, authors=(AUTHOR,), language="de").read_bytes()
    for length in (118, 125, 190, 300):
        name = f"{length} " + ("Ein sehr langer Titel " * 20)[: length - 5] + ".epub"
        answer = send(world.client, upload["id"], name, data)
        assert answer.status_code == 201, (length, answer.text)
        assert answer.json()["path"].endswith(".epub") and len(answer.json()["path"]) <= 120
    assert all(item["path"].endswith(".epub") for item in
               world.client.get(f"/api/v1/uploads/{upload['id']}").json()["files"])


# --- A filing that stopped between the new name and the old one ---------------------------------------------------


def test_a_filing_that_stopped_after_the_new_name_went_on_when_looked_at_again(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import placing

    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    book = german_epub(folder)
    real = placing._remove
    monkeypatch.setattr(placing, "_remove", lambda path: (_ for _ in ()).throw(RuntimeError("the process died")))
    finish(sab, job, folder)
    row = the_download()
    shelf = library_path(world)
    # The file lies in the library, and the old name is still there: the case the filing could not get out of.
    assert row.state == "problem" and row.problem_code == "place_failed"
    assert (shelf / f"{PLACED}.epub").is_file() and book.is_file()
    monkeypatch.setattr(placing, "_remove", real)
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck")
    assert answer.status_code == 200, answer.text
    run_jobs()
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)
    assert not book.exists()
    assert files_in(shelf).count(f"{PLACED}.epub") == 1 and f"{PLACED} (2).epub" not in files_in(shelf)


def test_a_copy_that_stopped_before_the_old_name_went_is_recognised_by_its_content(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    from app.services.procure import importing

    world, _sab, _job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    book = german_epub(folder)
    goal = library_path(world) / f"{PLACED}.epub"
    goal.parent.mkdir(parents=True)
    goal.write_bytes(book.read_bytes())
    item = PlaceFile(download_id=1, path="Rune Falkner - Die Laternenstrasse.epub", origin="source",
                     size=book.stat().st_size, format="epub", role="book", target=f"{PLACED}.epub")
    # The same size and the same bytes at the goal and at the source: one file, placed; the old name goes. Another
    # content of the same size is somebody else's file and counts for nothing.
    assert importing._left_over(goal, folder, item, "move") == "rename"
    assert not book.exists() and goal.is_file()
    book.write_bytes(b"x" * item.size)
    assert importing._left_over(goal, folder, item, "move") == ""
    assert book.is_file()


def test_a_goal_that_was_taken_is_planned_anew_when_looked_at_again(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import importing

    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    german_epub(folder)
    real = importing._plan_targets
    shelf = library_path(world)

    def planned_and_taken(db: Any, row: Any, library: Any, taken: list[PlaceFile]) -> None:
        real(db, row, library, taken)
        for item in taken:
            goal = shelf.joinpath(*item.target.split("/"))
            goal.parent.mkdir(parents=True, exist_ok=True)
            goal.write_bytes(b"somebody's own file, put there after the plan")

    monkeypatch.setattr(importing, "_plan_targets", planned_and_taken)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_values["reason"] == "taken", (row.state, row.problem_values)
    monkeypatch.setattr(importing, "_plan_targets", real)
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck").status_code == 200
    run_jobs()
    assert the_download().state == "imported"
    assert (shelf / f"{PLACED}.epub").read_bytes() == b"somebody's own file, put there after the plan"
    assert (shelf / f"{PLACED} (2).epub").is_file()


# --- A program that reports a folder of a library ------------------------------------------------------------------


def test_a_folder_a_program_reports_inside_a_library_is_no_release(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = ebook_world(net)
    shelf = library_path(world)
    existing = shelf / "Some Author" / "Some Book"
    german_epub(existing, name="mine.epub")
    age_all(shelf)
    finish(sab, job, existing)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "path_not_found", (row.state, row.problem_code)
    # Nothing of the library moved.
    assert (existing / "mine.epub").is_file()
    assert "Some Author/Some Book/mine.epub" in files_in(shelf) and not [name for name in files_in(shelf) if "Nebelchor" in name]


def test_the_search_for_a_place_leaves_the_libraries_alone(operator: Any, net: FakeNetwork, roots: Path) -> None:
    from app.services.procure import arrival

    world, _sab, _job = ebook_world(net)
    shelf = library_path(world)
    (shelf / "Rune Falkner" / "release-folder").mkdir(parents=True)
    outside = books_folder("releases") / "complete" / "release-folder"
    outside.mkdir(parents=True)
    found = arrival.look_for("/data/complete/release-folder")
    assert found.reason == "proposed" and found.proposal is not None
    assert found.proposal["local"].startswith(str(outside.parent.parent)), found.proposal
    assert not arrival.in_a_library(outside) and arrival.in_a_library(shelf / "Rune Falkner")


# --- The right is looked at with every file ------------------------------------------------------------------------


def test_who_lost_the_right_cannot_fill_or_finish_an_upload_begun_before(operator: Any, net: FakeNetwork,
                                                                         tmp_path: Path) -> None:
    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    data = make_epub(tmp_path / "m.epub", title=TITLE, authors=(AUTHOR,), language="de").read_bytes()
    assert send(world.client, upload["id"], "a.epub", data).status_code == 201
    join(world.library_id, world.person.id, "read")
    assert send(world.client, upload["id"], "b.epub", data).status_code == 403
    assert world.client.post(f"/api/v1/uploads/{upload['id']}/finish", json={}).status_code == 403
    run_jobs()
    assert not [name for name in files_in(library_path(world)) if "Laternen" in name]
    with SessionLocal() as db:
        assert db.scalar(select(Download.state).where(Download.id == upload["id"])) == "uploading"
    # What came can still be dropped by the one who sent it.
    assert world.client.delete(f"/api/v1/uploads/{upload['id']}").status_code == 204
    assert not hidden_in(library_path(world))


# --- Two files of one name at the same moment ----------------------------------------------------------------------


def test_two_files_of_one_name_sent_at_once_leave_the_first_alone(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import uploads

    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    real = uploads.prepare
    state: dict[str, Any] = {}

    def prepared_twice(db: Any, account: Any, upload_id: int, name: str, declared: int | None) -> Any:
        target, allowed = real(db, account, upload_id, name, declared)
        # The race: the other request made the file between the check and the opening.
        target.write_bytes(b"the first file, whole")
        state["target"] = target
        return target, allowed

    monkeypatch.setattr(uploads, "prepare", prepared_twice)
    answer = send(world.client, upload["id"], "same.epub", b"the second")
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "file_exists", answer.text
    assert state["target"].read_bytes() == b"the first file, whole"


def test_two_files_of_one_name_really_at_once_never_give_a_server_error(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import uploads

    world = world_with(net, other_book)
    upload = begin(world.client, world.library_id)
    data = make_epub(tmp_path / "m.epub", title=TITLE, authors=(AUTHOR,), language="de", text_bytes=200_000).read_bytes()
    real = uploads.prepare
    barrier = threading.Barrier(2, timeout=10)

    results: list[int] = []
    lock = threading.Lock()

    def waiting(*args: Any, **kwargs: Any) -> Any:
        # Both pass the check of the name before either opens the file.
        try:
            with lock:
                outcome = real(*args, **kwargs)
        except Exception:
            barrier.abort()
            raise
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return outcome

    def go() -> None:
        results.append(send(new_client(world.person), upload["id"], "same.epub", data).status_code)

    monkeypatch.setattr(uploads, "prepare", waiting)
    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 500 not in results and min(results) in (201, 409), results
    assert 201 in results
    listed = world.client.get(f"/api/v1/uploads/{upload['id']}").json()["files"]
    assert [item["size"] for item in listed] == [len(data)]


def test_the_room_of_files_on_their_way_is_not_given_twice(operator: Any, net: FakeNetwork,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from collections import namedtuple

    from app.services.procure import uploads

    world = world_with(net, other_book)
    first = begin(world.client, world.library_id)
    second = begin(world.client, world.library_id)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10**12, 0, uploads.RESERVE + 1000))
    with SessionLocal() as db:
        _target, allowed = uploads.prepare(db, world.person, first["id"], "one.epub", 700)
        assert allowed == 1000
        # 700 of the 1000 are promised to the file on its way: 400 cannot be promised again.
        with pytest.raises(Exception) as refused:
            uploads.prepare(db, world.person, second["id"], "two.epub", 400)
        assert getattr(refused.value, "status_code", None) == 413
        uploads.release(_target)
        _other, allowed = uploads.prepare(db, world.person, second["id"], "two.epub", 400)
        assert allowed == 1000
        uploads.release(_other)


# --- Room for the whole release ------------------------------------------------------------------------------------


def test_a_recording_that_does_not_fit_is_not_begun(operator: Any, net: FakeNetwork, roots: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from collections import namedtuple

    from app.services.procure import placing

    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "rec"
    parts = recording(folder, parts=3, seconds=4.0)
    size = parts[0].stat().st_size
    usage = namedtuple("usage", "total used free")
    # Room for one part and the reserve, not for three: the first part must not go in alone.
    monkeypatch.setattr(placing, "crosses_disks", lambda _source, _library: True)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10**12, 0, placing.RESERVE + size + 10))
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "no_space", (row.state, row.problem_code)
    assert not [name for name in files_in(library_path(world)) if name.endswith(".mp3")]
    assert all(path.is_file() for path in parts)
    # Once there is room, "Erneut prüfen" files the whole recording.
    monkeypatch.undo()
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck").status_code == 200
    run_jobs()
    assert the_download().state == "imported"
    assert len([name for name in files_in(library_path(world)) if name.endswith(".mp3")]) == 3


# --- Names with a mark some systems do not take, are no danger -------------------------------------------------------


def test_a_release_with_a_name_that_has_a_question_mark_is_a_book_not_a_danger(operator: Any, net: FakeNetwork,
                                                                               roots: Path, tmp_path: Path) -> None:
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    book = german_epub(tmp_path).read_bytes()
    zip_of(folder / "book.zip", {"Wer? Wie: Warum.epub": book, "extra/Fragen *.txt": b"x"})
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.failed_reason)
    assert [name for name in files_in(library_path(world)) if name.endswith(".epub") and "Nebelchor" in name]
    assert not hidden_in(library_path(world))


# --- The list of what runs says "geprüft" while the files are read and "eingeordnet" while they are put in place -----------


def test_the_list_of_what_runs_tells_the_check_from_the_filing_and_from_the_wait(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import Job
    from app.services.procure import importing

    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "rel"
    folder.mkdir(parents=True)
    german_epub(folder)
    # The filing job is held back: the download is done and waits for its turn.
    monkeypatch.setattr(importing, "request", lambda _db, _row: None)
    sab.finish(job, str(folder))
    from .follow_helpers import round_of_tracking

    round_of_tracking()

    def phase() -> str:
        [shown] = world.client.get("/api/v1/procure/running").json()["downloads"]
        return str(shown["phase"])

    row = the_download()
    assert row.state == "completed" and phase() == "waiting"
    # Its job runs: the files are being read and held against the wish.
    with SessionLocal() as db:
        db.add(Job(kind=importing.KIND, ref=row.id, state="running"))
        db.commit()
    assert phase() == "checking"
    # Checked, about to be put in place.
    with SessionLocal() as db:
        db.get(Download, row.id).place_step = "checked"  # type: ignore[union-attr]
        db.commit()
    assert phase() == "filing"
