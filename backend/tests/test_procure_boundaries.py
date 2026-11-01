"""Wishes and downloads keep to their libraries: what a person does not share a library with
is not theirs to read, to lift or to take away, and the data folder of the server is never a place for a download.

Only invented names, example.com, keys made at run time."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import clock
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import (
    BlocklistEntry,
    Download,
    ForeignBook,
    ForeignJob,
    LibraryMember,
    PathMapping,
    ProcureHistory,
    ProcureWish,
)
from app.services.procure import arrival, wishes

from .conftest import DATA_DIR, make_account, new_client
from .follow_helpers import (
    AUDIO,
    audio_world,
    downloads,
    planner_tick,
    round_of_tracking,
    rows,
    sabnzbd,
    wish,
    wish_rows,
    wishes_of,
)
from .library_helpers import books_folder, join, make_library, make_link, run_jobs
from .procure_fakes import FakeNetwork
from .test_procure_tracking import loaded

TOKEN = "tok" + "b" * 13


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The folder the download programs' jobs may be seen in."""
    base = tmp_path / "downloads"
    base.mkdir()
    monkeypatch.setattr(get_settings(), "download_roots", str(base))
    return base


def foreign_book(token: str = TOKEN) -> None:
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:41", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de"))
        db.commit()


def wish_for_book(who: Any, token: str = TOKEN, **body: Any) -> dict[str, Any]:
    answer = who.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                      "language": "de", **body})
    # 201 when a wish was made, 200 when the wish was there already.
    assert answer.status_code in (200, 201), answer.text
    return answer.json()["results"][0]  # type: ignore[no-any-return]


# --- The data folder is never a place for a download ----------------------------------------------------------


def test_the_start_refuses_a_download_root_that_is_the_data_folder_or_in_it_or_holds_it(tmp_path: Path) -> None:
    data = tmp_path / "state"
    data.mkdir()
    for bad in (str(data), str(data / "sub"), str(tmp_path), f"/downloads,{data}"):
        with pytest.raises(ValidationError, match="data folder"):
            Settings(data_dir=data, download_roots=bad)
    elsewhere = tmp_path / "downloads"
    elsewhere.mkdir()
    assert Settings(data_dir=data, download_roots=str(elsewhere)).download_roots == str(elsewhere)


def test_the_image_default_for_the_download_roots_is_only_the_download_folder() -> None:
    assert Settings.model_fields["download_roots"].default == "/downloads"


@pytest.mark.parametrize("where", ["the data folder itself", "a folder that holds it"])
def test_a_job_reported_inside_the_data_folder_is_a_problem_and_never_a_finished_download(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    secret = Path(DATA_DIR) / "secret.key"
    secret.write_text("not a key", encoding="utf-8")
    # A start would refuse these; a server that got there anyway (an old setting, a changed mount) still reads nothing.
    monkeypatch.setattr(get_settings(), "download_roots",
                        DATA_DIR if where == "the data folder itself" else str(Path(DATA_DIR).parent))
    world, sab, job = loaded(net)
    sab.finish(job, str(secret))
    round_of_tracking()
    [row] = downloads()
    assert (row.state, row.problem_code) == ("problem", "path_not_found")
    assert arrival.visible(secret) is None and arrival.local_path(str(secret), []) is None
    shown = world.client.get("/api/v1/procure/problems").json()["problems"]
    assert [item["code"] for item in shown] == ["path_not_found"]
    assert secret.exists()


def test_a_link_inside_a_download_root_that_leads_into_the_data_folder_shows_nothing(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    secret = Path(DATA_DIR) / "secret.key"
    secret.write_text("not a key", encoding="utf-8")
    make_link(roots / "sneaky", Path(DATA_DIR))
    world, sab, job = loaded(net)
    sab.finish(job, str(roots / "sneaky" / "secret.key"))
    round_of_tracking()
    assert (downloads()[0].state, downloads()[0].problem_code) == ("problem", "path_not_found")
    assert arrival.local_path(str(roots / "sneaky" / "secret.key"), []) is None
    assert world.client.get("/api/v1/procure/problems").json()["problems"][0]["code"] == "path_not_found"


def test_the_search_for_a_place_to_suggest_never_goes_into_the_data_folder(
        operator: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    holder = tmp_path / "holder"
    state = holder / "state"
    (state / "Some.Release.Name").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "data_dir", state)
    monkeypatch.setattr(get_settings(), "download_roots", str(holder))
    found = arrival.look_for("/downloads/complete/Some.Release.Name")
    assert (found.reason, found.proposal, found.matches) == ("nowhere", None, ())
    # The same folder next to the data folder is found, as it should be.
    (holder / "other" / "Some.Release.Name").mkdir(parents=True)
    assert arrival.look_for("/downloads/complete/Some.Release.Name").reason == "proposed"


def test_a_root_in_the_data_folder_is_no_place_to_look_for_a_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "download_roots", DATA_DIR)
    assert arrival.data_folder() not in arrival.roots() and arrival.download_roots() == [arrival.data_folder()]
    assert str(arrival.data_folder()) not in arrival.look_for("/downloads/complete/x").roots


# --- Nothing leaks over the border of a library -----------------------------------------------------------------


def test_two_libraries_wish_the_same_book_each_for_itself_and_see_nothing_of_the_other(
        operator: Any, client: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    foreign_book()
    other = make_library(books_folder("elsewhere"), "Elsewhere")
    sam = make_account("sam")
    join(other, sam.id, "acquire")
    first = wish_for_book(world.client)
    assert first["status"] == "created" and first["wish"]["library"]["name"] == "Books"
    with new_client(sam) as sam_client:
        # Not the other library's wish, not its person, not its name: sam's own wish.
        mine = wish_for_book(sam_client)
        assert mine["status"] == "created" and mine["wish"]["id"] != first["wish"]["id"]
        assert mine["wish"]["library"]["name"] == "Elsewhere" and mine["wish"]["by"] == "sam"
        options = sam_client.get("/api/v1/procure/book-options", params={"book": TOKEN}).json()
        audio = next(item for item in options["kinds"] if item["kind"] == "audio")
        assert (audio["state"], audio["wish_id"]) == ("wished", mine["wish"]["id"])
        assert [item["id"] for item in sam_client.get("/api/v1/procure/wishes").json()["wishes"]] == [
            mine["wish"]["id"]]
        # And again by sam: the wish of his own library is the one that exists.
        again = wish_for_book(sam_client)
        assert again["status"] == "exists" and again["wish"]["id"] == mine["wish"]["id"]
    assert [item["id"] for item in wishes_of(world)] == [first["wish"]["id"]]
    run_jobs()
    # Each library gets its own release: the book goes into both.
    assert sorted(item.library_id for item in downloads()) == sorted([world.library_id, other])
    assert len(sab.jobs) == 2 and {item.state for item in wish_rows()} == {"loading"}
    with new_client(sam) as sam_client:
        shown = sam_client.get("/api/v1/procure/running").json()["downloads"]
        assert [item["library"]["name"] for item in shown] == ["Elsewhere"]


def test_a_torrent_goes_into_its_program_once_and_the_other_library_waits_for_it(operator: Any, net: FakeNetwork) -> None:
    from .follow_helpers import qbittorrent, torrent_world

    qbittorrent(net)
    world, tracker, info_hash = torrent_world(net)
    tracker.items[0].info_hash = info_hash
    foreign_book()
    other = make_library(books_folder("elsewhere"), "Elsewhere")
    sam = make_account("sam")
    join(other, sam.id, "acquire")
    wish(world)
    run_jobs()
    assert [(item.state, item.client_ref) for item in downloads()] == [("queued", info_hash)]
    with new_client(sam) as sam_client:
        mine = wish_for_book(sam_client)["wish"]
        run_jobs()
        [shown] = [item for item in sam_client.get("/api/v1/procure/wishes").json()["wishes"] if item["id"] == mine["id"]]
    assert shown["state"] == "not_found" and shown["detail"]["load_error"] == "release_in_use"
    assert [item.library_id for item in downloads()] == [world.library_id]


def test_a_release_chosen_by_hand_is_not_put_on_a_wish_in_another_language(operator: Any, net: FakeNetwork) -> None:
    from app.services.procure import automation

    sabnzbd(net)
    world = audio_world(net)
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": False})
    german = wish(world)["results"][0]["wish"]
    assert german["state"] == "idle" and german["language"] == "de"
    started = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "xx"})
    assert started.status_code == 202, started.text
    run_jobs()
    found = world.client.get(f"/api/v1/procure/searches/{started.json()['id']}").json()
    release = next(item for item in found["releases"] if item["title"] == AUDIO)
    loaded_now = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release['id']}/load", json={})
    assert loaded_now.status_code == 201, loaded_now.text
    states = {(item.language, item.state) for item in wish_rows()}
    assert states == {("de", "idle"), ("", "loading")}


def test_the_options_of_a_book_name_no_wish_of_a_library_the_person_has_no_right_in(
        operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    foreign_book()
    wish_for_book(world.client)
    other = make_library(books_folder("elsewhere"), "Elsewhere")
    sam = make_account("sam")
    join(other, sam.id, "acquire")
    with new_client(sam) as sam_client:
        options = sam_client.get("/api/v1/procure/book-options", params={"book": TOKEN}).json()
        audio = next(item for item in options["kinds"] if item["kind"] == "audio")
        assert (audio["state"], audio["wish_id"]) == ("free", None)


def failed_in_other_library(net: FakeNetwork) -> tuple[Any, Any, int, Any]:
    """rita (acquire in Books and in Third) asks for the work of Books in Third; bob has acquire in Third only; the
    download fails."""
    sab = sabnzbd(net)
    world = audio_world(net)
    third = make_library(books_folder("third"), "Third")
    join(third, world.person.id, "acquire")
    bob = make_account("bob")
    join(third, bob.id, "acquire")
    wish(world, library_id=third)
    run_jobs()
    [row] = downloads()
    sab.fail(row.client_ref)
    round_of_tracking()
    return world, sab, third, bob


def test_the_history_of_a_library_names_no_work_the_person_does_not_see(operator: Any, net: FakeNetwork) -> None:
    world, _sab, _third, bob = failed_in_other_library(net)
    assert rows(ProcureHistory)
    mine = world.client.get("/api/v1/procure/history").json()["entries"]
    assert mine and mine[0]["book_title"]
    with new_client(bob) as bob_client:
        assert bob_client.get("/api/v1/procure/history").json()["entries"] == []
        counts = bob_client.get("/api/v1/procure/counts").json()
        assert (counts["seeking"], counts["running"], counts["needs"]) == (0, 0, 0)
        assert bob_client.get("/api/v1/procure/wishes").json()["wishes"] == []
        assert bob_client.get("/api/v1/procure/running").json()["downloads"] == []


def test_the_numbers_for_the_head_count_only_what_the_person_sees(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    third = make_library(books_folder("third"), "Third")
    join(third, world.person.id, "acquire")
    bob = make_account("bob")
    join(third, bob.id, "acquire")
    wish(world, library_id=third)
    run_jobs()
    [row] = downloads()
    figures = ("seeking", "running", "needs")

    def counted(who: Any) -> tuple[int, ...]:
        answer = who.get("/api/v1/procure/counts").json()
        return tuple(answer[name] for name in figures)

    # The wish waits for its next try (it is counted as sought) while its download runs.
    with SessionLocal() as db:
        db.query(ProcureWish).update({"state": "not_found"})
        db.commit()
    with new_client(bob) as bob_client:
        assert counted(bob_client) == (0, 0, 0)
    assert counted(world.client) == (1, 1, 0)
    with SessionLocal() as db:
        db.query(Download).filter(Download.id == row.id).update({"state": "problem", "problem_code": "no_write"})
        db.commit()
    with new_client(bob) as bob_client:
        assert counted(bob_client) == (0, 0, 0)
    assert counted(world.client) == (1, 0, 1)


def test_a_person_without_sight_of_the_work_cannot_cancel_its_download_or_take_its_wish_away(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    third = make_library(books_folder("third"), "Third")
    join(third, world.person.id, "acquire")
    bob = make_account("bob")
    join(third, bob.id, "acquire")
    wish(world, library_id=third)
    run_jobs()
    [row] = downloads()
    [mine] = wish_rows()
    with new_client(bob) as bob_client:
        assert bob_client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={}).status_code == 404
        assert bob_client.post(f"/api/v1/procure/downloads/{row.id}/recheck").status_code == 404
        assert bob_client.post(f"/api/v1/procure/wishes/{mine.id}/search").status_code == 404
        assert bob_client.delete(f"/api/v1/procure/wishes/{mine.id}").status_code == 404
    assert downloads()[0].state == "queued" and sab.removed == [] and len(wish_rows()) == 1


def test_a_block_is_seen_and_lifted_only_by_the_people_of_the_library_that_made_it(
        operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    foreign_book()
    other = make_library(books_folder("elsewhere"), "Elsewhere")
    sam = make_account("sam")
    join(other, sam.id, "acquire")
    colleague = make_account("colleague")
    join(world.library_id, colleague.id, "acquire")
    third = make_library(books_folder("third"), "Third")
    tina = make_account("tina")
    join(third, tina.id, "acquire")
    wish_for_book(world.client)
    run_jobs()
    [row] = downloads()
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={"block": True}).status_code == 200
    [entry] = rows(BlocklistEntry)
    assert entry.library_id == world.library_id and entry.foreign_id is not None
    shown = world.client.get("/api/v1/procure/blocklist").json()["entries"]
    assert [item["id"] for item in shown] == [entry.id] and shown[0]["by"] == "rita"
    with new_client(colleague) as mate:
        seen = mate.get("/api/v1/procure/blocklist").json()["entries"]
        assert [item["id"] for item in seen] == [entry.id] and seen[0]["by"] == "rita"
        assert mate.get("/api/v1/procure/counts").json()["blocklist"] == 1
    for outsider in (sam, tina):
        with new_client(outsider) as who:
            assert who.get("/api/v1/procure/blocklist").json()["entries"] == []
            assert who.get("/api/v1/procure/counts").json()["blocklist"] == 0
            assert who.delete(f"/api/v1/procure/blocklist/{entry.id}").status_code == 404
    assert len(rows(BlocklistEntry)) == 1
    with new_client(sam) as who:
        # The release stays turned down for the book, but sam is given no entry to lift.
        started = who.post("/api/v1/procure/searches", json={"book": TOKEN, "language": "de"})
        assert started.status_code == 202
        run_jobs()
        found = who.get(f"/api/v1/procure/searches/{started.json()['id']}").json()
        release = next(item for item in found["releases"] if item["title"] == AUDIO)
        assert any(reason["code"] == "blocklisted" for reason in release["reasons"]) and release["blocked_by"] is None
    own = world.client.post("/api/v1/procure/searches", json={"book": TOKEN, "language": "de"})
    run_jobs()
    found = world.client.get(f"/api/v1/procure/searches/{own.json()['id']}").json()
    assert next(item for item in found["releases"] if item["title"] == AUDIO)["blocked_by"] == entry.id
    assert world.client.delete(f"/api/v1/procure/blocklist/{entry.id}").status_code == 204


# --- The jobs nobody asked for are the operator's ------------------------------------------------------------


def test_the_jobs_nobody_asked_for_are_seen_and_dealt_with_only_by_the_operator(
        operator: Any, client: Any, net: FakeNetwork) -> None:
    world, sab, _job = loaded(net)
    join(world.library_id, operator.id, "acquire")
    mine = sab.foreign_job("Somebody.Elses.Book.EPUB-EXAMPLE")
    other = sab.foreign_job("Another.Strangers.Book.EPUB-EXAMPLE")
    round_of_tracking()
    first, second = rows(ForeignJob)
    colleague = make_account("colleague")
    join(world.library_id, colleague.id, "manage")
    for person in (colleague, world.person):
        with new_client(person) as who:
            assert who.get("/api/v1/procure/problems").json()["foreign"] == []
            assert who.get("/api/v1/procure/counts").json()["needs"] == 0
            assert who.post(f"/api/v1/procure/foreign/{first.id}/ignore").status_code == 403
            assert who.delete(f"/api/v1/procure/foreign/{first.id}").status_code == 403
    assert mine in sab.jobs and other in sab.jobs and not rows(ForeignJob)[0].ignored
    assert [item["id"] for item in client.get("/api/v1/procure/problems").json()["foreign"]] == [second.id, first.id]
    assert client.get("/api/v1/procure/counts").json()["needs"] == 2
    assert client.post(f"/api/v1/procure/foreign/{first.id}/ignore").status_code == 204
    assert client.delete(f"/api/v1/procure/foreign/{second.id}").status_code == 204
    assert other not in sab.jobs and mine in sab.jobs


def test_the_service_itself_refuses_a_member_the_jobs_nobody_asked_for(operator: Any, net: FakeNetwork) -> None:
    from fastapi import HTTPException

    from app.models import Account
    from app.services.procure import downloads as download_service

    world, sab, _job = loaded(net)
    sab.foreign_job("Somebody.Elses.Book.EPUB-EXAMPLE")
    round_of_tracking()
    [job] = rows(ForeignJob)
    with SessionLocal() as db:
        member = db.get(Account, world.person.id)
        assert download_service.foreign_jobs(db, member) == []
        for action in (download_service.ignore_foreign, download_service.remove_foreign):
            with pytest.raises(HTTPException) as refused:
                action(db, member, job.id)
            assert refused.value.status_code == 403


def test_an_operator_who_may_ask_for_books_nowhere_deals_with_no_foreign_job(operator: Any, client: Any,
                                                                             net: FakeNetwork) -> None:
    _world, sab, _job = loaded(net)
    sab.foreign_job("Somebody.Elses.Book.EPUB-EXAMPLE")
    round_of_tracking()
    [job] = rows(ForeignJob)
    assert client.get("/api/v1/procure/problems").status_code == 403
    assert client.delete(f"/api/v1/procure/foreign/{job.id}").status_code == 403
    assert rows(ForeignJob)


# --- A download in another language is not the wish's ----------------------------------------------------------


def test_a_download_in_another_language_or_for_another_library_is_not_adopted_by_a_wish(
        operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    other = make_library(books_folder("elsewhere"), "Elsewhere")
    moment = clock.now()
    with SessionLocal() as db:
        db.add(Download(work_id=world.work_id, library_id=world.library_id, kind="audio", language="de",
                        title="German Release", protocol="usenet", state="queued", created_at=moment,
                        updated_at=moment))
        english = ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="audio", language="en",
                              library_id=world.library_id, state="searching", created_at=moment, updated_at=moment)
        elsewhere = ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="audio", language="de",
                                library_id=other, state="searching", created_at=moment, updated_at=moment)
        german = ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="audio", language="de",
                             library_id=world.library_id, state="searching", created_at=moment, updated_at=moment)
        db.add_all([english, elsewhere, german])
        db.commit()
        assert wishes._adopt_running(db, english) is False
        assert wishes._adopt_running(db, elsewhere) is False
        assert wishes._adopt_running(db, german) is True and german.state == "loading"


def test_a_wish_waits_while_a_release_in_another_language_of_its_book_is_on_its_way(
        operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    moment = clock.now()
    with SessionLocal() as db:
        db.add(Download(work_id=world.work_id, library_id=world.library_id, kind="audio", language="en",
                        title="English Release", protocol="usenet", state="queued", created_at=moment,
                        updated_at=moment))
        db.commit()
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found" and shown["detail"]["load_error"] == "other_language_running"
    assert shown["next_try_at"] is not None and sab.jobs == {}
    assert [item.wish_id for item in downloads()] == [None]


# --- The search below the roots is kept ----------------------------------------------------------------------------


def stuck(world: Any, how_many: int) -> None:
    moment = clock.now()
    with SessionLocal() as db:
        for number in range(how_many):
            db.add(Download(user_id=world.person.id, library_id=world.library_id, kind="audio", title=f"Stuck {number}",
                            protocol="usenet", state="problem", problem_code="path_not_found", completed_at=moment,
                            reported_path=f"/elsewhere/complete/Nothing.Here.{number}", client_ref=f"ref{number}",
                            created_at=moment, updated_at=moment))
        db.commit()


def test_the_search_for_a_missing_folder_is_made_once_per_path_and_one_per_round_and_again_after_ten_minutes(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net)
    arrival.forget_lookouts()
    stuck(world, 3)
    calls: list[str] = []
    real = arrival.look_for
    monkeypatch.setattr(arrival, "look_for", lambda reported, *a, **k: (calls.append(reported or ""), real(reported))[1])
    for number in range(1, 4):
        round_of_tracking()
        assert len(calls) == number, "one search at most in a round"
    for _ in range(3):
        round_of_tracking()
    assert len(calls) == 3, "nothing is searched again while the answer is kept"
    # The folder of the programs changes: every path is asked again, one in a round.
    other = roots.parent / "moved"
    other.mkdir()
    monkeypatch.setattr(get_settings(), "download_roots", str(other))
    for number in range(4, 7):
        round_of_tracking()
        assert len(calls) == number
    round_of_tracking()
    assert len(calls) == 6
    # Ten minutes later the kept answers are old: one more, in the next round.
    later = arrival.clock() + 601
    monkeypatch.setattr(arrival, "clock", lambda: later)
    round_of_tracking()
    assert len(calls) == 7


def test_pressing_look_again_does_not_search_the_folders_again_and_again(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net)
    arrival.forget_lookouts()
    stuck(world, 1)
    calls: list[str] = []
    real = arrival.look_for
    monkeypatch.setattr(arrival, "look_for", lambda reported, *a, **k: (calls.append(reported or ""), real(reported))[1])
    [row] = downloads()
    for _ in range(3):
        assert world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck").status_code == 200
    assert len(calls) == 1


def test_a_stuck_download_that_turns_up_goes_on_without_a_search(operator: Any, net: FakeNetwork, roots: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    _world, sab, job = loaded(net)
    reported = roots / "complete" / "Not.Yet.There"
    sab.finish(job, str(reported))
    arrival.forget_lookouts()
    round_of_tracking()
    assert downloads()[0].problem_code == "path_not_found"
    reported.mkdir(parents=True)
    monkeypatch.setattr(arrival, "look_for", lambda *a, **k: pytest.fail("no search needed"))
    round_of_tracking()
    assert downloads()[0].state == "completed"


# --- A hand-over a stop of the server left half done -------------------------------------------------------------


def stale_sending(world: Any, minutes: int) -> int:
    with SessionLocal() as db:
        wish_row = db.scalar(select(ProcureWish))
        assert wish_row is not None
        moment = clock.now() - timedelta(minutes=minutes)
        row = Download(user_id=world.person.id, work_id=world.work_id, library_id=world.library_id, kind="audio",
                       language="de", title="Left.Half.Done", protocol="usenet", state="sending", wish_id=wish_row.id,
                       created_at=moment, updated_at=moment)
        db.add(row)
        wish_row.state, wish_row.search_id = "loading", None
        db.commit()
        return row.id


def test_a_hand_over_left_half_done_ends_as_failed_and_its_wish_searches_again(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    wish(world)
    run_jobs()
    with SessionLocal() as db:
        db.query(Download).delete()
        db.commit()
        sab.jobs.clear()
    stale = stale_sending(world, 20)
    round_of_tracking()
    row = next(item for item in downloads() if item.id == stale)
    assert (row.state, row.failed_reason) == ("failed", "interrupted")
    assert [line.note for line in rows(ProcureHistory)] == [{"code": "interrupted"}]
    assert rows(BlocklistEntry) == []
    [shown] = wishes_of(world)
    assert shown["state"] == "searching"
    # Reading the list starts nothing; the planner starts the wish.
    planner_tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1
    assert world.client.get("/api/v1/procure/running").json()["downloads"][0]["phase"] == "queued"


def test_a_hand_over_left_half_done_is_found_at_the_start_of_the_server(operator: Any, net: FakeNetwork) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    from .conftest import TAB

    sabnzbd(net)
    world = audio_world(net)
    wish(world)
    run_jobs()
    with SessionLocal() as db:
        db.query(Download).delete()
        db.commit()
    young = stale_sending(world, 1)
    with TestClient(app, base_url="http://testserver", headers=TAB):
        pass
    assert [(item.state, item.failed_reason) for item in downloads() if item.id == young] == [("failed", "interrupted")]


def test_a_hand_over_that_is_still_young_is_left_alone_but_none_is_running_at_the_start(
        operator: Any, net: FakeNetwork) -> None:
    from app.services.procure import loading

    sabnzbd(net)
    world = audio_world(net)
    wish(world)
    run_jobs()
    with SessionLocal() as db:
        db.query(Download).delete()
        db.commit()
    young = stale_sending(world, 1)
    round_of_tracking()
    assert [item.state for item in downloads() if item.id == young] == ["sending"]
    with SessionLocal() as db:
        assert loading.reap_sending(db) == 1
    assert [item.state for item in downloads() if item.id == young] == ["failed"]


# --- Who may open what -----------------------------------------------------------------------------------


def test_the_lists_of_what_runs_and_what_needs_somebody_are_for_people_who_may_ask_for_books(
        operator: Any, client: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    reader = make_account("reader")
    outsider = make_account("outsider")
    join(world.library_id, reader.id, "read")
    for who in (new_client(reader), new_client(outsider), client):
        with who:
            for path in ("running", "problems", "history", "wishes", "blocklist"):
                answer = who.get(f"/api/v1/procure/{path}")
                assert answer.status_code == 403 and answer.json()["detail"]["code"] == "acquire_not_allowed", path
    assert world.client.get("/api/v1/procure/running").status_code == 200
    assert world.client.get("/api/v1/procure/problems").status_code == 200


def path_problem(world: Any) -> int:
    moment = clock.now()
    with SessionLocal() as db:
        row = Download(user_id=world.person.id, work_id=world.work_id, library_id=world.library_id, kind="audio",
                       title="Some.Book.GERMAN", protocol="usenet", state="problem", problem_code="path_not_found",
                       completed_at=moment, reported_path="/downloads/complete/Some.Book.GERMAN", client_ref="n1",
                       created_at=moment, updated_at=moment)
        db.add(row)
        db.commit()
        return row.id


def test_a_path_mapping_is_for_an_operator_who_looks_after_the_library_of_the_download(
        operator: Any, client: Any, net: FakeNetwork, roots: Path) -> None:
    sabnzbd(net)
    world = audio_world(net)
    download_id = path_problem(world)
    url = f"/api/v1/procure/downloads/{download_id}/mapping"
    # An operator who is no member of the library is told nothing of the download, not its name either.
    assert client.get(url).status_code == 404
    assert client.post(url, json={"remote": "/downloads", "local": str(roots)}).status_code == 404
    # Asking for books there is not looking after it.
    join(world.library_id, operator.id, "acquire")
    assert client.get(url).status_code == 404
    assert client.post(url, json={"remote": "/downloads", "local": str(roots)}).status_code == 404
    shown = client.get("/api/v1/procure/problems").json()["problems"]
    assert [item["can_map"] for item in shown] == [False]
    join(world.library_id, operator.id, "manage")
    assert client.get(url).status_code == 200
    assert [item["can_map"] for item in client.get("/api/v1/procure/problems").json()["problems"]] == [True]
    # A member is no operator.
    assert world.client.get(url).status_code == 403
    assert rows(PathMapping) == []


def test_a_member_who_manages_the_library_cannot_confirm_a_mapping(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    world, sab, job = loaded(net)
    join(world.library_id, world.person.id, "manage")
    real = roots / "usenet" / "complete" / "nexbooks" / AUDIO
    real.mkdir(parents=True)
    sab.finish(job, f"/downloads/complete/nexbooks/{AUDIO}")
    round_of_tracking()
    row = downloads()[0]
    assert row.state == "problem"
    proposal = row.problem_values["proposal"]
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/mapping", json=proposal)
    assert answer.status_code == 403
    assert rows(PathMapping) == []
    round_of_tracking()
    assert downloads()[0].state == "problem"


# --- Said once -----------------------------------------------------------------------------------------------------


def test_a_torrent_that_stays_because_its_files_lie_in_a_library_is_said_once_and_stops_nothing_by_hand(
        operator: Any, net: FakeNetwork, roots: Path, caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch) -> None:
    from .library_helpers import BOOKS_DIR
    from .test_procure_tracking import seed_world

    # The library lies below a download folder: the files are in both, and the library wins.
    monkeypatch.setattr(get_settings(), "download_roots", str(Path(BOOKS_DIR).parent))
    inside = Path(BOOKS_DIR) / "procure" / "seeding-kept"
    qbit, info_hash, row, world = seed_world(net, roots, inside)
    qbit.put(info_hash, ratio=2.0, seeding_time=999_999, state="stoppedUP", max_ratio=1.5)
    with caplog.at_level(logging.INFO, logger="nexbooks.procure"):
        for _ in range(4):
            round_of_tracking()
    said = [item for item in caplog.records if "the torrent stays" in item.getMessage()]
    assert len(said) == 1
    assert downloads()[0].seed_state == "kept" and info_hash in qbit.torrents and qbit.removed == []
    assert world.client.get("/api/v1/procure/running").json()["downloads"] == []
    # A person may still end it: the program lets it go, and the files in the library are not touched.
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/stop-seeding").status_code == 200
    assert info_hash not in qbit.torrents and qbit.removed == [(info_hash, "false")]
    assert downloads()[0].seed_state == "done"


# --- A wish of a person who lost the right rests ---------------------------------------------------------------------


def test_a_wish_of_a_person_who_lost_the_right_rests_and_a_colleague_takes_it_over(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    colleague = make_account("colleague")
    join(world.library_id, colleague.id, "acquire")
    wish(world)
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=world.person.id).delete()
        db.commit()
    run_jobs()
    [row] = wish_rows()
    assert (row.state, row.detail["reason"]) == ("idle", "no_right")
    assert downloads() == [] and sab.jobs == {}
    with new_client(colleague) as mate:
        [shown] = mate.get("/api/v1/procure/wishes").json()["wishes"]
        assert shown["state"] == "idle" and shown["detail"]["reason"] == "no_right" and shown["by"] == "rita"
        assert mate.post(f"/api/v1/procure/wishes/{shown['id']}/search").status_code == 200
    run_jobs()
    [row] = wish_rows()
    assert row.user_id == colleague.id and row.state == "loading" and len(sab.jobs) == 1
    assert downloads()[0].user_id == colleague.id


def test_a_resting_wish_wakes_when_the_right_is_back(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    wish(world)
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=world.person.id).delete()
        db.commit()
    run_jobs()
    assert wish_rows()[0].detail["reason"] == "no_right"
    with SessionLocal() as db:
        assert wishes.run_due(db) == 0
    assert wish_rows()[0].state == "idle"
    join(world.library_id, world.person.id, "acquire")
    planner_tick()
    run_jobs()
    [row] = wish_rows()
    assert row.state == "loading" and row.user_id == world.person.id and len(sab.jobs) == 1


# --- The rights over every route of the downloads, in one table -------------------------------------------------------


def test_the_rights_over_the_routes_of_wishes_downloads_and_foreign_jobs(
        operator: Any, client: Any, net: FakeNetwork) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    from .conftest import TAB

    world, sab, job = loaded(net)
    sab.finish(job, "/downloads/complete/Nowhere")
    sab.foreign_job("Somebody.Elses.Book.EPUB-EXAMPLE")
    round_of_tracking()
    [download] = downloads()
    assert download.problem_code == "path_not_found"
    [foreign] = rows(ForeignJob)
    other = make_library(books_folder("elsewhere"), "Elsewhere")
    people: dict[str, Any] = {}
    for name, right, place in (("reader", "read", world.library_id), ("elsewhere", "acquire", other),
                               ("colleague", "acquire", world.library_id), ("manager", "manage", world.library_id)):
        person = make_account(name)
        join(place, person.id, right)
        people[name] = new_client(person)
    people["outsider"] = new_client(make_account("outsider"))
    people["operator"] = client
    people["anonymous"] = TestClient(app, base_url="http://testserver", headers=TAB)
    join(world.library_id, operator.id, "manage")
    people["operator_manages"] = client
    reads = ("running", "problems", "history", "wishes", "blocklist")
    expected_reads = {"reader": 403, "elsewhere": 200, "colleague": 200, "manager": 200, "outsider": 403,
                      "anonymous": 401}
    for name, who in people.items():
        if name in ("operator", "operator_manages"):
            continue
        for path in reads:
            assert who.get(f"/api/v1/procure/{path}").status_code == expected_reads[name], (name, path)
    mapping = f"/api/v1/procure/downloads/{download.id}/mapping"
    assert {name: people[name].get(mapping).status_code for name in
            ("reader", "elsewhere", "colleague", "manager", "outsider", "anonymous")} == {
        "reader": 403, "elsewhere": 403, "colleague": 403, "manager": 403, "outsider": 403, "anonymous": 401}
    ignore = f"/api/v1/procure/foreign/{foreign.id}/ignore"
    assert {name: people[name].post(ignore).status_code for name in
            ("reader", "elsewhere", "colleague", "manager", "outsider", "anonymous")} == {
        "reader": 403, "elsewhere": 403, "colleague": 403, "manager": 403, "outsider": 403, "anonymous": 401}
    assert not rows(ForeignJob)[0].ignored
    assert client.get(mapping).status_code == 200
    assert client.post(ignore).status_code == 204


def test_a_suggested_mapping_reaches_down_to_the_download_root_of_nexbooks(
        operator: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SABnzbd reports ``/data/downloads/...`` for what nexbooks sees below its download root
    ``/downloads``. Taking off every name both paths end in alike gave ``/data`` to the folder above the root (``/`` in
    the container), which read like a way to the top of the disk. The suggestion stops at the root: ``/data/downloads``
    to ``/downloads``. It is no wider for that: it maps fewer paths, and this one still to the same place."""
    root = tmp_path / "downloads"
    job = root / "complete" / "nexbooks" / "Some.Release.Name"
    job.mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "download_roots", str(root))
    reported = "/data/downloads/complete/nexbooks/Some.Release.Name"
    found = arrival.look_for(reported)
    assert found.reason == "proposed"
    assert found.proposal == {"remote": "/data/downloads", "local": str(root)}
    assert arrival.map_remote(reported, [(found.proposal["remote"], found.proposal["local"])]) == job
    # A path the program reports outside its downloads is not taken in by the suggestion.
    assert arrival.map_remote("/data/other/file", [(found.proposal["remote"], found.proposal["local"])]) is None
    # Picked among several places, the same rule.
    (tmp_path / "downloads" / "again" / "Some.Release.Name").mkdir(parents=True)
    assert arrival.mapping_for(reported, job) == {"remote": "/data/downloads", "local": str(root)}
