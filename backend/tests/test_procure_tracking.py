"""Following the downloads: what the programs say becomes the state of a download; a program that cannot be
asked changes nothing; a failure blocks the release and the wish goes on with the next one; a finished download whose
files cannot be reached or whose library takes no file is a problem with a way out; jobs nobody asked for are shown and
never touched; the seed goal of a filed torrent."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import BlocklistEntry, Download, DownloadClient, ForeignJob, PathMapping, ProcureHistory
from app.services.procure import automation, formats, tracking
from app.services.procure import downloads as download_service

from .conftest import make_account, new_client
from .follow_helpers import (
    AUDIO,
    SECOND_AUDIO,
    audio_world,
    downloads,
    qbittorrent,
    round_of_tracking,
    rows,
    sabnzbd,
    torrent_world,
    wish,
    wishes_of,
)
from .library_helpers import join, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, World

HASH = "ab" * 20


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The folder the download programs' jobs may be seen in."""
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setattr(get_settings(), "download_roots", str(base))
    return base


def loaded(net: FakeNetwork, *items: Item) -> tuple[World, Any, str]:
    sab = sabnzbd(net)
    world = audio_world(net, *items)
    wish(world)
    run_jobs()
    [row] = downloads()
    return world, sab, row.client_ref


def the_download() -> Download:
    [row] = downloads()
    return row


def operator_may_acquire(world: World, operator: Any) -> None:
    join(world.library_id, operator.id, "acquire")


def operator_manages(world: World, operator: Any) -> None:
    join(world.library_id, operator.id, "manage")


def test_the_progress_of_a_job_is_written_down_and_shown_in_the_list_of_what_runs(operator: Any,
                                                                                   net: FakeNetwork) -> None:
    world, sab, job = loaded(net)
    sab.progress(job, 40, size_mb=400)
    round_of_tracking()
    row = the_download()
    assert row.state == "downloading" and row.progress == pytest.approx(40, abs=1) and row.remaining_seconds == 150
    shown = world.client.get("/api/v1/procure/running").json()["downloads"]
    assert len(shown) == 1 and shown[0]["phase"] == "downloading" and shown[0]["release"] == AUDIO
    assert shown[0]["mine"] is True and shown[0]["library"]["name"] == "Books" and shown[0]["client"]
    sab.put(job, status="Paused")
    round_of_tracking()
    assert the_download().state == "paused"
    sab.progress(job, 70, size_mb=400)
    sab.put(job, status="Extracting")
    round_of_tracking()
    assert world.client.get("/api/v1/procure/running").json()["downloads"][0]["phase"] == "unpacking"


def test_a_round_is_due_every_fifteen_seconds_and_soon_after_a_load(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = [1000.0]
    monkeypatch.setattr(tracking, "clock", lambda: moment[0])
    tracking.reset()
    assert tracking.due() is True
    tracking._last_round = moment[0]
    assert tracking.due() is False
    moment[0] += 14.9
    assert tracking.due() is False
    moment[0] += 0.2
    assert tracking.due() is True
    tracking._last_round = moment[0]
    tracking.soon()
    assert tracking.due() is False
    moment[0] += 5.1
    assert tracking.due() is True


def test_a_finished_download_whose_files_are_seen_waits_to_be_filed(operator: Any, net: FakeNetwork,
                                                                    roots: Path) -> None:
    world, sab, job = loaded(net)
    folder = roots / "complete" / "nexbooks" / AUDIO
    folder.mkdir(parents=True)
    sab.finish(job, str(folder))
    round_of_tracking()
    row = the_download()
    assert row.state == "completed" and row.completed_at is not None and row.reported_path == str(folder)
    assert row.progress == 100.0 and row.problem_code == ""
    [shown] = world.client.get("/api/v1/procure/running").json()["downloads"]
    assert shown["phase"] == "waiting"
    # It is not asked again, and the wish stays on its way.
    requests = len(sab.requests)
    round_of_tracking()
    assert len([r for r in sab.requests[requests:] if r.url.params.get("nzo_ids")]) == 0
    assert wishes_of(world)[0]["state"] == "loading"
    counts = world.client.get("/api/v1/procure/counts").json()
    assert counts["running"] == 1 and counts["needs"] == 0 and counts["seeking"] == 0


def test_a_finished_download_that_nexbooks_does_not_see_is_a_problem_with_a_suggestion(
        operator: Any, client: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = loaded(net)
    operator_manages(world, operator)
    real = roots / "usenet" / "complete" / "nexbooks" / AUDIO
    real.mkdir(parents=True)
    sab.finish(job, f"/downloads/complete/nexbooks/{AUDIO}")
    round_of_tracking()
    row = the_download()
    assert row.state == "problem" and row.problem_code == "path_not_found"
    # The longest end the two paths share is left out: it is /downloads that is /data/usenet here.
    assert row.problem_values["proposal"] == {"remote": "/downloads", "local": str(roots / "usenet")}
    # Everyone who may ask for books sees the problem; the paths only the operator sees.
    shown = world.client.get("/api/v1/procure/problems").json()["problems"]
    assert [item["code"] for item in shown] == ["path_not_found"] and shown[0]["values"] == {"search": "proposed"}
    assert shown[0]["can_map"] is False
    mine = client.get("/api/v1/procure/problems").json()["problems"]
    assert mine[0]["can_map"] is True and mine[0]["values"]["proposal"]["remote"] == "/downloads"
    # The operator confirms what was suggested, nothing else.
    url = f"/api/v1/procure/downloads/{row.id}/mapping"
    assert world.client.get(url).status_code == 403
    options = client.get(url).json()
    assert options["search"] == "proposed" and options["proposal"] == row.problem_values["proposal"]
    refused = client.post(url, json={"remote": "/downloads", "local": str(roots)})  # not what was suggested
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "mapping_not_offered"
    elsewhere = client.post(url, json={"remote": "/downloads", "local": "/etc"})
    assert elsewhere.status_code == 409
    assert rows(PathMapping) == []
    assert client.post(url, json=options["proposal"]).status_code == 201
    [mapping] = rows(PathMapping)
    assert (mapping.remote, mapping.local) == ("/downloads", str(roots / "usenet"))
    round_of_tracking()
    assert the_download().state == "completed"


def test_a_mapping_is_offered_only_for_a_download_that_needs_one(operator: Any, client: Any, net: FakeNetwork,
                                                                 roots: Path) -> None:
    world, _sab, _job = loaded(net)
    operator_may_acquire(world, operator)
    assert client.get(f"/api/v1/procure/downloads/{the_download().id}/mapping").status_code == 404
    assert client.get("/api/v1/procure/downloads/999/mapping").status_code == 404


def test_the_search_for_a_folder_says_when_there_are_two_or_none(operator: Any, client: Any, net: FakeNetwork,
                                                                 roots: Path) -> None:
    world, sab, job = loaded(net)
    operator_manages(world, operator)
    sab.finish(job, f"/downloads/complete/nexbooks/{AUDIO}")
    round_of_tracking()
    url = f"/api/v1/procure/downloads/{the_download().id}/mapping"
    assert client.get(url).json()["search"] == "nowhere"
    for place in ("one", "two"):
        (roots / place / "complete" / "nexbooks" / AUDIO).mkdir(parents=True)
    twice = client.get(url).json()
    assert twice["search"] == "twice" and len(twice["matches"]) == 2 and twice["proposal"] is None
    chosen = str(roots / "two" / "complete" / "nexbooks" / AUDIO)
    answer = client.post(url, json={"remote": "/downloads", "local": str(roots / "two")})
    assert answer.status_code == 201, answer.text
    assert chosen in twice["matches"]


def test_a_library_that_takes_no_file_is_a_problem_until_it_does(operator: Any, net: FakeNetwork, roots: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    world, sab, job = loaded(net)
    folder = roots / "complete" / AUDIO
    folder.mkdir(parents=True)
    sab.finish(job, str(folder))
    real = formats.probe_library
    monkeypatch.setattr(formats, "probe_library", lambda path: {"writable": False, "reason": "no_permission",
                                                                "hardlink": None})
    round_of_tracking()
    row = the_download()
    assert row.state == "problem" and row.problem_code == "no_write"
    assert row.problem_values == {"library_id": world.library_id, "reason": "no_permission"}
    shown = world.client.get("/api/v1/procure/problems").json()["problems"][0]
    assert shown["code"] == "no_write" and shown["library"]["name"] == "Books"
    still = world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck")
    assert still.status_code == 200 and still.json()["state"] == "problem"
    monkeypatch.setattr(formats, "probe_library", real)
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck").json()["state"] == "completed"
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck").status_code == 409


def test_another_library_can_be_chosen_for_a_finished_download(operator: Any, net: FakeNetwork, roots: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from .library_helpers import books_folder, make_library

    world, sab, job = loaded(net)
    other = make_library(books_folder("other"), "Other", "audio")
    folder = roots / "complete" / AUDIO
    folder.mkdir(parents=True)
    sab.finish(job, str(folder))
    real = formats.probe_library

    def probe(path: str) -> dict[str, Any]:
        return real(path) if path.endswith("other") else {"writable": False, "reason": "read_only", "hardlink": None}

    monkeypatch.setattr(formats, "probe_library", probe)
    round_of_tracking()
    row = the_download()
    assert row.problem_code == "no_write"
    url = f"/api/v1/procure/downloads/{row.id}/library"
    assert world.client.post(url, json={"library_id": other}).status_code == 403
    join(other, world.person.id, "acquire")
    assert world.client.post(url, json={"library_id": other}).json()["state"] == "completed"
    assert the_download().library_id == other
    ebooks = make_library(books_folder("ebooks"), "Ebooks", "ebook")
    join(ebooks, world.person.id, "acquire")
    assert world.client.post(url, json={"library_id": ebooks}).status_code == 422


def test_a_failed_job_blocks_the_release_and_the_wish_goes_on_with_the_next(operator: Any, net: FakeNetwork) -> None:
    world, sab, job = loaded(net, Item(AUDIO, size=400_000_000, categories=(3130,)),
                             Item(SECOND_AUDIO, size=410_000_000, categories=(3130,)))
    first = the_download().title
    sab.fail(job, "Aborted, cannot be completed")
    round_of_tracking()
    row = rows(Download)[0]
    assert row.state == "failed" and row.failed_reason == "client_failed" and row.failed_detail == "incomplete"
    assert row.removal_pending is True and row.finished_at is not None
    [block] = rows(BlocklistEntry)
    assert block.title == first and block.reason == "client_failed" and block.by_user_id is None
    [line] = rows(ProcureHistory)
    assert line.result == "failed" and line.note == {"code": "client_failed", "detail": "incomplete"}
    assert line.book_title and line.release == first
    # The wish searches on; the job is taken out of the program with its files at the next round.
    run_jobs()
    second = [item for item in downloads() if item.state == "queued"]
    assert len(second) == 1 and second[0].title != first
    round_of_tracking()
    assert job in sab.removed
    assert rows(Download)[0].removal_pending is False
    assert wishes_of(world)[0]["state"] == "loading"
    lines = world.client.get("/api/v1/procure/history").json()["entries"]
    assert [item["result"] for item in lines] == ["failed"] and lines[0]["note"]["detail"] == "incomplete"
    assert world.client.get("/api/v1/procure/history", params={"result": "done"}).json()["entries"] == []
    assert len(world.client.get("/api/v1/procure/history", params={"result": "bad"}).json()["entries"]) == 1


def test_a_removal_the_program_did_not_answer_is_tried_again_every_round(operator: Any, net: FakeNetwork) -> None:
    _world, sab, job = loaded(net)
    sab.fail(job)
    round_of_tracking()
    assert rows(Download)[0].removal_pending is True
    net.down.add(sab.host)
    round_of_tracking()
    round_of_tracking()
    assert rows(Download)[0].removal_pending is True and job not in sab.removed
    net.down.discard(sab.host)
    round_of_tracking()
    assert job in sab.removed and rows(Download)[0].removal_pending is False


def test_a_wish_gets_only_as_many_downloads_in_a_day_as_the_operator_allows(operator: Any,
                                                                            net: FakeNetwork) -> None:
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "replace_max": 2})
    world, sab, job = loaded(net, Item(AUDIO, size=400_000_000, categories=(3130,)),
                             Item(SECOND_AUDIO, size=410_000_000, categories=(3130,)))
    sab.fail(job)
    round_of_tracking()
    run_jobs()
    second = next(item for item in downloads() if item.state == "queued")
    sab.fail(second.client_ref)
    round_of_tracking()
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found" and shown["detail"]["error"] == "replace_limit"
    assert len([item for item in downloads() if item.state == "queued"]) == 0


def test_with_replacing_switched_off_a_failure_leaves_the_wish_idle(operator: Any, net: FakeNetwork) -> None:
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "replace": False})
    world, sab, job = loaded(net)
    sab.fail(job)
    round_of_tracking()
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "idle" and shown["detail"]["reason"] == "failed"


def test_a_job_that_is_gone_twice_in_a_row_is_a_problem_and_comes_back_when_the_program_has_it(
        operator: Any, net: FakeNetwork) -> None:
    _world, sab, job = loaded(net)
    sab.progress(job, 10)
    round_of_tracking()
    entry = sab.jobs[job]
    sab.vanish(job)
    round_of_tracking()
    assert the_download().state == "downloading" and the_download().missing_count == 1
    round_of_tracking()
    row = the_download()
    assert row.state == "problem" and row.problem_code == "gone_from_client"
    sab.jobs[job] = entry
    sab.progress(job, 20)
    sab.put(job, filename=entry[1], cat=entry[0])
    round_of_tracking()
    assert the_download().state == "downloading" and the_download().problem_code == ""


def test_two_single_misses_apart_are_not_two_in_a_row(operator: Any, net: FakeNetwork) -> None:
    _world, sab, job = loaded(net)
    sab.progress(job, 10)
    round_of_tracking()
    entry = sab.jobs[job]
    sab.vanish(job)
    round_of_tracking()
    assert the_download().missing_count == 1
    sab.jobs[job] = entry
    sab.progress(job, 20)
    sab.put(job, filename=entry[1], cat=entry[0])
    round_of_tracking()
    assert the_download().missing_count == 0
    sab.vanish(job)
    round_of_tracking()
    row = the_download()
    assert row.state == "downloading" and row.problem_code == "" and row.missing_count == 1


def test_a_program_that_cannot_be_asked_keeps_its_downloads_as_they_are(operator: Any, net: FakeNetwork) -> None:
    world, sab, job = loaded(net)
    sab.progress(job, 33)
    round_of_tracking()
    net.down.add(sab.host)
    sab.vanish(job)
    for _ in range(3):
        round_of_tracking()
    row = the_download()
    assert row.state == "downloading" and row.missing_count == 0 and row.progress == pytest.approx(33, abs=1)
    with SessionLocal() as db:
        assert db.scalar(select(DownloadClient.last_error)) == "client_unreachable"
    counts = world.client.get("/api/v1/procure/counts").json()
    assert [item["code"] for item in counts["clients_down"]] == ["client_unreachable"]
    net.down.discard(sab.host)
    sab.jobs[job] = ("nexbooks", AUDIO, b"")
    sab.progress(job, 50)
    sab.put(job, filename=AUDIO, cat="nexbooks")
    round_of_tracking()
    assert world.client.get("/api/v1/procure/counts").json()["clients_down"] == []
    assert the_download().progress == pytest.approx(50, abs=1)


def test_a_job_nobody_asked_for_is_shown_and_never_touched(operator: Any, client: Any, net: FakeNetwork) -> None:
    world, sab, job = loaded(net)
    operator_may_acquire(world, operator)
    stranger = sab.foreign_job("Somebody.Elses.Book.EPUB-EXAMPLE")
    round_of_tracking()
    round_of_tracking()
    [foreign] = rows(ForeignJob)
    assert foreign.ref == stranger and foreign.name == "Somebody.Elses.Book.EPUB-EXAMPLE" and not foreign.ignored
    assert stranger in sab.jobs and job in sab.jobs and sab.removed == []
    # The operator's alone (the programs are shared by every library): the person who asked for the book sees nothing.
    assert world.client.get("/api/v1/procure/problems").json()["foreign"] == []
    assert world.client.get("/api/v1/procure/counts").json()["needs"] == 0
    shown = client.get("/api/v1/procure/problems").json()
    assert [item["name"] for item in shown["foreign"]] == ["Somebody.Elses.Book.EPUB-EXAMPLE"]
    assert client.get("/api/v1/procure/counts").json()["needs"] == 1
    # The operator ignores it: it stays in the program and leaves the list.
    assert client.post(f"/api/v1/procure/foreign/{foreign.id}/ignore").status_code == 204
    assert client.get("/api/v1/procure/problems").json()["foreign"] == []
    round_of_tracking()
    assert rows(ForeignJob)[0].ignored is True and stranger in sab.jobs
    # Removing takes it out of the program.
    assert client.delete(f"/api/v1/procure/foreign/{foreign.id}").status_code == 204
    assert stranger in sab.removed and rows(ForeignJob) == []
    # A job that left the program leaves the list.
    again = sab.foreign_job("Another.Stranger.EPUB-EXAMPLE")
    round_of_tracking()
    assert len(rows(ForeignJob)) == 1
    sab.vanish(again)
    round_of_tracking()
    assert rows(ForeignJob) == []


def test_foreign_jobs_are_for_nobody_but_the_operator_with_the_right_to_ask_for_books(operator: Any, net: FakeNetwork) -> None:
    _world, sab, _job = loaded(net)
    sab.foreign_job("Somebody.Elses.Book.EPUB-EXAMPLE")
    round_of_tracking()
    [foreign] = rows(ForeignJob)
    nobody = make_account("nobody")
    with new_client(nobody) as client:
        assert client.get("/api/v1/procure/problems").status_code == 403
        assert client.post(f"/api/v1/procure/foreign/{foreign.id}/ignore").status_code == 403
        assert client.delete(f"/api/v1/procure/foreign/{foreign.id}").status_code == 403
    assert rows(ForeignJob)[0].ignored is False


def test_a_hand_over_without_an_answer_is_found_again_by_its_name(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    sab.take_then_timeout = True
    wish(world)
    run_jobs()
    row = the_download()
    assert row.state == "unsure" and row.client_ref == ""
    [shown] = wishes_of(world)
    assert shown["state"] == "loading"
    round_of_tracking()
    row = the_download()
    assert row.state == "queued" and row.client_ref in sab.jobs
    assert rows(ForeignJob) == []


def test_a_hand_over_no_program_took_ends_after_ten_minutes_and_the_wish_searches_again(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    sab.add_timeout = True
    wish(world)
    run_jobs()
    assert the_download().state == "unsure"
    round_of_tracking()
    assert the_download().state == "unsure"
    later = clock.now() + timedelta(minutes=11)
    with monkeypatch.context() as moved:
        moved.setattr(clock, "now", lambda: later)
        round_of_tracking()
    row = the_download()
    assert row.state == "failed" and row.failed_reason == "not_taken"
    assert rows(BlocklistEntry) == []
    sab.add_timeout = False
    run_jobs()
    assert any(item.state == "queued" for item in downloads())


def test_cancelling_takes_the_job_away_and_the_wish_waits(operator: Any, net: FakeNetwork) -> None:
    world, sab, job = loaded(net)
    row = the_download()
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={})
    assert answer.status_code == 200 and answer.json()["state"] == "removed"
    assert job in sab.removed and rows(BlocklistEntry) == []
    [shown] = wishes_of(world)
    assert shown["state"] == "idle" and shown["detail"]["reason"] == "cancelled"
    [line] = rows(ProcureHistory)
    assert line.result == "removed" and line.note == {"code": "cancelled", "blocked": False}
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={}).status_code == 409


def test_cancelling_with_a_block_never_takes_that_release_again_and_searches_on(operator: Any,
                                                                                net: FakeNetwork) -> None:
    world, _sab, _job = loaded(net, Item(AUDIO, size=400_000_000, categories=(3130,)),
                             Item(SECOND_AUDIO, size=410_000_000, categories=(3130,)))
    first = the_download().title
    world.client.post(f"/api/v1/procure/downloads/{the_download().id}/cancel", json={"block": True})
    [block] = rows(BlocklistEntry)
    assert block.title == first and block.reason == "by_hand" and block.by_user_id == world.person.id
    run_jobs()
    queued = [item for item in downloads() if item.state == "queued"]
    assert len(queued) == 1 and queued[0].title != first


def test_a_cancel_of_a_program_that_does_not_answer_is_kept_to_be_done_later(operator: Any, net: FakeNetwork) -> None:
    world, sab, job = loaded(net)
    net.down.add(sab.host)
    row = the_download()
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={}).status_code == 200
    assert the_download().state == "removed" and the_download().removal_pending is True
    net.down.discard(sab.host)
    round_of_tracking()
    assert job in sab.removed and the_download().removal_pending is False


def test_only_who_may_ask_for_books_cancels_a_download(operator: Any, net: FakeNetwork) -> None:
    world, sab, _job = loaded(net)
    row = the_download()
    reader = make_account("reader")
    outsider = make_account("outsider")
    join(world.library_id, reader.id, "read")
    for person in (reader, outsider):
        with new_client(person) as client:
            for action, body in (("cancel", {}), ("recheck", None), ("stop-seeding", None)):
                reply = client.post(f"/api/v1/procure/downloads/{row.id}/{action}", json=body)
                assert reply.status_code in (403, 404), (action, reply.status_code)
            assert client.post(f"/api/v1/procure/downloads/{row.id}/library", json={"library_id": 1}).status_code in (
                403, 404)
    assert the_download().state == "queued" and sab.removed == []


def test_a_torrent_is_followed_by_its_hash_and_finishes_with_a_path(operator: Any, net: FakeNetwork,
                                                                    roots: Path) -> None:
    qbit = qbittorrent(net)
    world, _tracker, info_hash = torrent_world(net)
    wish(world)
    run_jobs()
    row = the_download()
    assert row.state == "queued" and row.client_ref == info_hash and row.protocol == "torrent"
    qbit.put(info_hash, name="x", progress=0.5, state="downloading")
    round_of_tracking()
    assert the_download().state == "downloading" and the_download().progress == 50.0
    folder = roots / "torrents" / "nexbooks" / "Some.Audiobook"
    folder.mkdir(parents=True)
    qbit.finish(info_hash, str(folder))
    round_of_tracking()
    row = the_download()
    assert row.state == "completed" and row.reported_path == str(folder)


def seed_ready(net: FakeNetwork, roots: Path, where: Path | None = None,
               seed: str | None = "ratio") -> tuple[Any, str, Download]:
    qbit, info_hash, row, _world = seed_world(net, roots, where, seed)
    return qbit, info_hash, row


def seed_world(net: FakeNetwork, roots: Path, where: Path | None = None,
               seed: str | None = "ratio") -> tuple[Any, str, Download, World]:
    """A filed torrent. Seeding is off from the start, so these tests choose a goal (``seed``); ``None`` leaves the
    setting as it is."""
    if seed is not None:
        with SessionLocal() as db:
            automation.save(db, {**automation.config(db), "seed": seed})
    qbit = qbittorrent(net)
    world, _tracker, info_hash = torrent_world(net, extra={"minimumratio": "1.5", "minimumseedtime": "172800"})
    wish(world)
    run_jobs()
    folder = where or (roots / "torrents" / "nexbooks" / "Some.Audiobook")
    folder.mkdir(parents=True, exist_ok=True)
    qbit.finish(info_hash, str(folder))
    round_of_tracking()
    with SessionLocal() as db:
        row = db.scalars(select(Download)).one()
        download_service.mark_imported(db, row)
        db.refresh(row)
        db.expunge(row)
    return qbit, info_hash, row, world


def test_a_filed_torrent_leaves_the_program_at_its_goal_with_its_files(operator: Any, net: FakeNetwork,
                                                                       roots: Path) -> None:
    qbit, info_hash, row = seed_ready(net, roots)
    assert row.state == "imported" and row.seed_state == "watching"
    assert row.goal_ratio == 1.5 and row.goal_seconds == 172800
    qbit.put(info_hash, ratio=0.4, seeding_time=100)
    round_of_tracking()
    assert info_hash in qbit.torrents and downloads()[0].seed_ratio == pytest.approx(0.4)
    qbit.put(info_hash, ratio=1.6, seeding_time=100, state="stoppedUP", max_ratio=1.5)
    round_of_tracking()
    assert info_hash not in qbit.torrents and qbit.removed == [(info_hash, "true")]
    assert downloads()[0].seed_state == "done"
    # The book is filed already: the wish is done and a line in the history says so.
    assert [line.result for line in rows(ProcureHistory)] == ["done"]


def test_a_torrent_whose_files_lie_in_a_library_is_never_taken_away(operator: Any, net: FakeNetwork, roots: Path,
                                                                    tmp_path: Path) -> None:
    from .library_helpers import BOOKS_DIR

    inside = Path(BOOKS_DIR) / "procure" / "seeding"
    qbit, info_hash, _row = seed_ready(net, roots, inside)
    qbit.put(info_hash, ratio=2.0, seeding_time=999_999, state="stoppedUP", max_ratio=1.5)
    round_of_tracking()
    assert info_hash in qbit.torrents and qbit.removed == []


def test_a_library_below_a_download_folder_is_still_never_cleaned(operator: Any, net: FakeNetwork, roots: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from .library_helpers import BOOKS_DIR

    monkeypatch.setattr(get_settings(), "download_roots", str(Path(BOOKS_DIR).parent))
    inside = Path(BOOKS_DIR) / "procure" / "seeding-below"
    qbit, info_hash, _row = seed_ready(net, roots, inside)
    qbit.put(info_hash, ratio=2.0, seeding_time=999_999, state="uploading")
    round_of_tracking()
    assert info_hash in qbit.torrents and qbit.removed == []


def test_files_that_moved_out_of_every_download_folder_are_never_deleted(operator: Any, net: FakeNetwork, roots: Path,
                                                                        tmp_path: Path) -> None:
    qbit, info_hash, _row = seed_ready(net, roots)
    elsewhere = tmp_path / "elsewhere" / "Some.Audiobook"
    elsewhere.mkdir(parents=True)
    qbit.put(info_hash, ratio=2.0, seeding_time=999_999, state="uploading", content_path=str(elsewhere))
    round_of_tracking()
    assert info_hash in qbit.torrents and qbit.removed == []


def test_the_goal_of_the_download_counts_even_when_the_program_has_no_limit_of_its_own(operator: Any,
                                                                                      net: FakeNetwork,
                                                                                      roots: Path) -> None:
    qbit, info_hash, _row = seed_ready(net, roots)
    qbit.put(info_hash, ratio=1.6, seeding_time=100, state="uploading")
    round_of_tracking()
    assert info_hash not in qbit.torrents and downloads()[0].seed_state == "done"


def test_a_torrent_the_owner_forced_to_run_stays_even_beyond_the_goal(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    qbit, info_hash, _row = seed_ready(net, roots)
    qbit.put(info_hash, ratio=5.0, seeding_time=999_999, state="forcedUP")
    round_of_tracking()
    assert info_hash in qbit.torrents and qbit.removed == []


def test_with_the_seed_choice_no_the_torrent_leaves_once_the_book_is_filed(operator: Any, net: FakeNetwork,
                                                                           roots: Path) -> None:
    qbit, info_hash, _row = seed_ready(net, roots, seed="no")
    qbit.put(info_hash, ratio=0.0, state="uploading")
    round_of_tracking()
    assert info_hash not in qbit.torrents


def test_stopping_the_seeding_by_hand_takes_the_torrent_away_and_only_for_who_may(operator: Any, net: FakeNetwork,
                                                                                  roots: Path) -> None:
    qbit, info_hash, row, world = seed_world(net, roots)
    with new_client(make_account("nobody")) as nobody:
        assert nobody.post(f"/api/v1/procure/downloads/{row.id}/stop-seeding").status_code in (403, 404)
    assert info_hash in qbit.torrents
    [shown] = world.client.get("/api/v1/procure/running").json()["downloads"]
    assert shown["phase"] == "seeding" and shown["goal_ratio"] == 1.5
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/stop-seeding").status_code == 200
    assert info_hash not in qbit.torrents and downloads()[0].seed_state == "done"
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/stop-seeding").status_code == 409


def test_a_stalled_download_is_a_hint_not_a_problem(operator: Any, net: FakeNetwork, roots: Path) -> None:
    qbit = qbittorrent(net)
    world, _tracker, info_hash = torrent_world(net)
    wish(world)
    run_jobs()
    qbit.put(info_hash, state="stalledDL", progress=0.2)
    round_of_tracking()
    row = the_download()
    assert row.state == "downloading" and row.problem_code == "stalled"
    shown = world.client.get("/api/v1/procure/running").json()["downloads"][0]
    assert shown["stalled"] is True and world.client.get("/api/v1/procure/problems").json()["problems"] == []
    qbit.put(info_hash, state="downloading", progress=0.3)
    round_of_tracking()
    assert the_download().problem_code == ""


def test_the_folders_a_program_leaves_after_a_failure_go_when_they_hold_nothing_useful(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = loaded(net)
    category = roots / "complete" / "nexbooks"
    category.mkdir(parents=True)
    sab.misc["complete_dir"] = str(roots / "complete")
    sab.categories.append({"name": "nexbooks", "dir": "nexbooks", "pp": "3"})
    empty = category / f"_FAILED_{AUDIO}"
    empty.mkdir()
    (empty / "readme.nfo").write_text("x")
    useful = category / f"_FAILED_{AUDIO}-2"
    useful.mkdir()
    (useful / "book.m4b").write_bytes(b"x")
    stranger = category / "_FAILED_Somebody.Elses.Download.That.Is.Not.Ours"
    stranger.mkdir()
    sab.fail(job)
    round_of_tracking()
    round_of_tracking()
    assert not empty.exists()
    assert useful.exists() and stranger.exists()


def test_the_news_servers_set_how_old_a_release_may_be_and_the_operator_may_say_otherwise(
        operator: Any, net: FakeNetwork) -> None:
    from app.services.procure import retention

    sab = sabnzbd(net)
    sab.servers = [{"enable": 1, "retention": 100}]
    retention.refresh()
    with SessionLocal() as db:
        assert retention.effective(db) == 100
        automation.save(db, {**automation.config(db), "retention_days": 30})
        assert retention.effective(db) == 30
        automation.save(db, {**automation.config(db), "retention_days": 0})
    sab.servers = [{"enable": 1, "retention": 0}]
    retention.refresh()
    with SessionLocal() as db:
        assert retention.effective(db) == 0
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,), days=200))
    sab.servers = [{"enable": 1, "retention": 100}]
    retention.refresh()
    answer = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    run_jobs()
    found = world.client.get(f"/api/v1/procure/searches/{answer.json()['id']}").json()
    assert [item["code"] for item in found["releases"][0]["reasons"]] == ["older_than_retention"]
