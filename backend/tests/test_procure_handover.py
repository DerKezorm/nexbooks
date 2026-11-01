"""Hand-overs that got no answer: a program that was not there when the release was found gets it again a few
minutes later, no search is made again, and a book is never loaded twice; a hand-over the program may have taken is
looked for by its name and ends after ten minutes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import Indexer, ProcureWish
from app.services.procure import choose, handover, releasefiles, rss

from .auto_helpers import Moving, configure, searching_requests, tick
from .follow_helpers import (
    AUDIO,
    SECOND_AUDIO,
    audio_world,
    downloads,
    round_of_tracking,
    rows,
    sabnzbd,
    wish,
    wishes_of,
)
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item


def the_wish() -> ProcureWish:
    [row] = rows(ProcureWish)
    return row  # type: ignore[no-any-return]


def down_world(net: FakeNetwork) -> tuple[Any, Any]:
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)),
                        Item(SECOND_AUDIO, size=410_000_000, categories=(3130,)))
    net.down.add(sab.host)
    wish(world)
    run_jobs()
    return world, sab


def test_a_program_that_was_not_there_gets_the_release_again_and_nothing_is_searched_again(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    world, sab = down_world(net)
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found" and shown["detail"]["load_error"] == "client_unreachable"
    assert shown["handing_over_again"] is True
    assert downloads() == []
    pending = the_wish().handover
    assert pending is not None and pending["tries"] == 0
    asked = len(searching_requests(world.indexer))
    moving = Moving(monkeypatch)
    net.down.discard(sab.host)
    moving.pass_by(minutes=2)
    tick()
    assert downloads() == [], "not before the time"
    moving.pass_by(minutes=2)
    tick()
    [row] = downloads()
    assert row.state == "queued" and row.title == AUDIO and len(sab.jobs) == 1
    assert len(searching_requests(world.indexer)) == asked, "only the file of the release was asked for"
    assert the_wish().handover is None and wishes_of(world)[0]["state"] == "loading"
    assert wishes_of(world)[0]["handing_over_again"] is False
    from .auto_helpers import runs

    assert any(run.kind == "handover" and run.counts.get("loaded") == 1 for run in runs())


def test_a_program_that_stays_away_is_asked_five_more_times_and_then_the_wish_waits_for_its_time(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    world, sab = down_world(net)
    monkeypatch.setattr(rss, "sync", lambda: 0)  # this is about the hand-over, not about the newest entries
    moving = Moving(monkeypatch)
    tries = []
    for _ in range(handover.TRIES):
        pending = the_wish().handover
        if pending is not None:
            moving.offset = datetime.fromisoformat(pending["due"]) - clock.now() + moving.offset + timedelta(seconds=1)
        else:
            moving.pass_by(minutes=10)
        tick()
        pending = the_wish().handover
        tries.append(pending["tries"] if pending else None)
    assert tries == [*range(1, handover.TRIES), None], tries
    assert the_wish().state == "not_found" and downloads() == [] and sab.jobs == {}
    assert world is not None


def test_the_time_between_two_hand_overs_doubles(operator: Any, net: FakeNetwork,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    down_world(net)
    moving = Moving(monkeypatch)
    waits = []
    for _ in range(handover.TRIES - 1):
        pending = the_wish().handover
        assert pending is not None
        due = datetime.fromisoformat(pending["due"])
        waits.append(round((due - clock.now()).total_seconds() / 60))
        moving.offset = due - clock.now() + moving.offset + timedelta(seconds=1)
        tick()
    assert waits == [3, 6, 12, 24], waits
    assert [choose.handover_delay(number) for number in range(8)] == [
        timedelta(minutes=3), timedelta(minutes=6), timedelta(minutes=12), timedelta(minutes=24),
        timedelta(minutes=48), timedelta(hours=1), timedelta(hours=1), timedelta(hours=1)]


def test_a_program_that_is_away_costs_the_indexer_no_file(operator: Any, net: FakeNetwork,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The program is asked before the indexer: a day without the program is a day without a single release fetched."""
    configure(rss_minutes=60, retry_hours=6)
    world, _sab = down_world(net)
    moving = Moving(monkeypatch)
    for _ in range(24 * 60 // 3):
        moving.pass_by(minutes=3)
        tick()
        run_jobs()
    assert world.indexer.fetched == [], world.indexer.fetched
    with SessionLocal() as db:
        row = db.scalar(select(Indexer))
        assert row is not None and row.grab_count == 0 and row.grabs == 0


def test_a_file_fetched_before_the_program_went_away_is_kept_and_used_again(operator: Any, net: FakeNetwork,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """The program answers the first question and is gone at the next: the file was fetched once and is kept, so the
    hand-overs that come again do not fetch it again, and the program gets it as soon as it is back."""
    configure(rss_minutes=60)
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    original = sab.handle

    def drops_after_the_question(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("mode") in ("addfile", "addurl") or request.method == "POST":
            raise httpx.ConnectError("away", request=request)
        return original(request)

    sab.handle = drops_after_the_question  # type: ignore[method-assign]
    wish(world)
    run_jobs()
    assert len(world.indexer.fetched) == 1, "fetched once"
    assert releasefiles.prune() == 0
    assert any(releasefiles.folder().iterdir()), "the file waits for the program"
    moving = Moving(monkeypatch)
    for _ in range(handover.TRIES - 1):
        moving.pass_by(minutes=70)
        tick()
    assert len(world.indexer.fetched) == 1, "not fetched again"
    sab.handle = original  # type: ignore[method-assign]
    moving.pass_by(minutes=70)
    wishes_row = the_wish()
    if wishes_row.handover is None:
        # The tries were used up: the planner searches again, the file is still kept.
        with SessionLocal() as db:
            db.execute(update(ProcureWish).values(handover={
                "search": wishes_row.search_id, "release": 1, "due": clock.now().isoformat(), "tries": 0}))
            db.commit()
    tick()
    assert len(world.indexer.fetched) == 1, "the kept file went to the program"
    assert len(sab.jobs) == 1 and not any(releasefiles.folder().iterdir()), "and it is gone when the program has it"


def test_a_book_is_never_loaded_twice_a_release_on_its_way_ends_the_hand_over(operator: Any, net: FakeNetwork,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    world, sab = down_world(net)
    net.down.discard(sab.host)
    # A person loads another release of the book by hand while the wish waits for its hand-over.
    wish_id = wishes_of(world)[0]["id"]
    search_id = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"}).json()["id"]
    run_jobs()
    releases = world.client.get(f"/api/v1/procure/searches/{search_id}").json()["releases"]
    second = next(item for item in releases if item["title"] == SECOND_AUDIO)
    assert world.client.post(f"/api/v1/procure/searches/{search_id}/releases/{second['id']}/load",
                             json={"force": False}).status_code == 201
    moving = Moving(monkeypatch)
    moving.pass_by(minutes=5)
    tick()
    assert [item.title for item in downloads()] == [SECOND_AUDIO] and len(sab.jobs) == 1
    assert the_wish().handover is None and the_wish().id == wish_id


def test_a_wish_that_is_gone_or_done_or_of_a_person_without_the_right_takes_nothing(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import LibraryMember

    configure(rss_minutes=60)
    world, sab = down_world(net)
    net.down.discard(sab.host)
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=world.person.id).delete()
        db.commit()
    moving = Moving(monkeypatch)
    moving.pass_by(minutes=5)
    tick()
    assert downloads() == [] and sab.jobs == {} and the_wish().handover is None


def test_with_the_main_switch_off_nothing_is_handed_over_again(operator: Any, net: FakeNetwork,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    world, sab = down_world(net)
    net.down.discard(sab.host)
    configure(enabled=False)
    moving = Moving(monkeypatch)
    moving.pass_by(minutes=5)
    tick()
    assert downloads() == [] and sab.jobs == {}
    assert world is not None


def test_a_hand_over_the_program_may_have_taken_is_never_made_a_second_time_while_it_is_unsure(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)),
                        Item(SECOND_AUDIO, size=410_000_000, categories=(3130,)))
    sab.take_then_timeout = True
    wish(world)
    run_jobs()
    [row] = downloads()
    assert row.state == "unsure" and row.client_ref == "" and len(sab.jobs) == 1
    moving = Moving(monkeypatch)
    for _ in range(3):
        moving.pass_by(minutes=2)
        tick()
    assert len(downloads()) == 1 and len(sab.jobs) == 1, "nothing else is loaded while the first is unsure"
    # The program has it: the next look finds the job by its name.
    round_of_tracking()
    [row] = downloads()
    assert row.state == "queued" and row.client_ref in sab.jobs and len(sab.jobs) == 1
    tick()
    assert len(downloads()) == 1 and len(sab.jobs) == 1


def test_the_pending_hand_over_is_in_the_database_and_survives_a_restart(operator: Any, net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    world, sab = down_world(net)
    from app.services.procure import planner, tracking

    # A restart forgets everything in memory; the entry is in the table.
    planner.reset()
    tracking.reset()
    net.down.discard(sab.host)
    moving = Moving(monkeypatch)
    moving.pass_by(minutes=4)
    tick()
    assert len(downloads()) == 1 and world is not None
    with SessionLocal() as db:
        assert db.scalar(select(ProcureWish.handover)) is None


@pytest.mark.parametrize("state", ["done", "searching", "loading"])
def test_a_wish_that_is_not_waiting_any_more_gives_up_its_pending_hand_over(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    from sqlalchemy import update

    configure(rss_minutes=60)
    world, sab = down_world(net)
    net.down.discard(sab.host)
    with SessionLocal() as db:
        db.execute(update(ProcureWish).values(state=state))
        db.commit()
    moving = Moving(monkeypatch)
    moving.pass_by(minutes=5)
    handover.run(SessionLocal())
    assert downloads() == [] and sab.jobs == {} and the_wish().handover is None
    assert world is not None


def test_a_person_who_may_only_read_gets_nothing_handed_over_and_the_wish_keeps_its_reason(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    from .library_helpers import join

    configure(rss_minutes=60)
    world, sab = down_world(net)
    net.down.discard(sab.host)
    join(world.library_id, world.person.id, "read")
    moving = Moving(monkeypatch)
    moving.pass_by(minutes=5)
    handover.run(SessionLocal())
    row = the_wish()
    assert downloads() == [] and row.handover is None
    assert row.detail["load_error"] == "client_unreachable", "the reason of the wish is not overwritten"
