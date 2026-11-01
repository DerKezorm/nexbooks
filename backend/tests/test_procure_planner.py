"""The planner of the automatic work: wishes that found nothing come back in the time the operator chose, one
at a time and the oldest first; a book that is not out waits for its day (in the time zone of the instance); the main
switch holds everything back; the planner and a person who searches at the same moment do not search twice; one tick at
a time."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, ProcureWish, ReleaseSearch
from app.services import timezones
from app.services.procure import planner, wishes

from .auto_helpers import Moving, configure, searching_requests, tick
from .follow_helpers import AUDIO, audio_world, downloads, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, World

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"


def good() -> Item:
    return Item(AUDIO, size=400_000_000, categories=(3130,))


def searches(origin: str = "search") -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(ReleaseSearch).where(ReleaseSearch.origin == origin)).all())


def waiting_world(net: FakeNetwork) -> tuple[World, Any]:
    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "not_found"
    return world, sab


def books(world: World, count: int, moving: Moving | None = None) -> list[int]:
    """Wishes for books of the sources, one after the other; the ids of the wishes."""
    ids: list[int] = []
    for number in range(count):
        token = f"tok{number:013d}"
        with SessionLocal() as db:
            db.add(ForeignBook(token=token, ref=f"hardcover:{number}", title=f"Die Nacht {number}",
                               authors=["Rune Falkner"], language="de"))
            db.commit()
        reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                                  "language": "de"})
        ids.append(reply.json()["results"][0]["wish"]["id"])
        run_jobs()
        if moving is not None:
            moving.pass_by(minutes=1)
    return ids


def test_a_wish_that_found_nothing_comes_back_in_the_time_the_operator_chose(operator: Any, net: FakeNetwork,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    world, sab = waiting_world(net)
    moving = Moving(monkeypatch)
    configure(rss_minutes=60, retry_hours=3)
    shown = wishes_of(world)[0]
    # The next try stands at the time chosen when the wish was searched (6 hours by default); the choice is a new one.
    with SessionLocal() as db:
        assert wishes.retime(db) == 1
    asked = len(searching_requests(world.indexer))
    moving.pass_by(hours=2, minutes=59)
    tick()
    run_jobs()
    assert len(searching_requests(world.indexer)) == asked, "not before the time"
    world.indexer.items = [good()]
    moving.pass_by(minutes=2)
    tick()
    assert shown["id"] == wishes_of(world)[0]["id"]
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1
    assert len(searching_requests(world.indexer)) > asked


@pytest.mark.parametrize("hours", [1, 3, 6, 12, 24])
def test_every_time_between_tries_the_settings_allow_is_kept(operator: Any, net: FakeNetwork, hours: int,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    world, _sab = waiting_world(net)
    moving = Moving(monkeypatch)
    configure(retry_hours=hours)
    with SessionLocal() as db:
        wishes.retime(db)
        row = db.scalar(select(ProcureWish))
        assert row.next_try_at == row.searched_at + timedelta(hours=hours)
    configure(rss_minutes=60)
    asked = len(searching_requests(world.indexer))
    moving.pass_by(hours=hours - 1, minutes=50)
    assert wishes_of(world)[0]["state"] == "not_found"
    tick()
    run_jobs()
    assert len(searching_requests(world.indexer)) == asked, "no new search before the time"
    moving.pass_by(minutes=15)
    tick()
    run_jobs()
    assert len(searching_requests(world.indexer)) > asked


def test_wishes_that_fall_due_together_are_searched_one_after_the_other_the_oldest_first(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    moving = Moving(monkeypatch)
    configure(rss_minutes=60)
    ids = books(world, 3, moving)
    assert [item["state"] for item in wishes_of(world)] == ["not_found"] * 3
    moving.pass_by(hours=7)
    first = searches()
    tick()
    started = searches() - first
    assert started == 1, "one wish a tick"
    with SessionLocal() as db:
        searching = list(db.scalars(select(ProcureWish.id).where(ProcureWish.state == "searching")))
    assert searching == [ids[0]], "the one that waited longest"
    run_jobs()
    tick()
    run_jobs()
    tick()
    run_jobs()
    assert searches() - first == 3
    # Everybody was searched once, nobody twice.
    with SessionLocal() as db:
        rows = list(db.scalars(select(ProcureWish).order_by(ProcureWish.id)))
        assert [row.state for row in rows] == ["not_found"] * 3
        assert all(row.next_try_at is not None and row.next_try_at > clock.now() for row in rows)


def test_a_book_that_is_not_out_is_searched_on_its_day_of_the_instance(operator: Any, net: FakeNetwork,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    moving = Moving(monkeypatch)
    configure(rss_minutes=60)
    # Auckland is far ahead of the clock the app runs on: its day is over while it is still the day before at 0:00 UTC.
    zone = ZoneInfo("Pacific/Auckland")
    monkeypatch.setattr(timezones, "instance_zone", lambda: zone)
    now_local = clock.now().astimezone(zone)
    day = (now_local.date() + timedelta(days=2)).isoformat()
    token = "tok" + "w" * 13
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:77", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de", series="Nebelchor", series_pos=2.0, released=day))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                              "language": "de"})
    assert reply.json()["results"][0]["wish"]["state"] == "waiting"
    run_jobs()
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "waiting" and sab.jobs == {}
    # The day begins in Auckland: 24 hours before the day's midnight there, it is the day before.
    begins = datetime.fromisoformat(day + "T00:00:00").replace(tzinfo=zone).astimezone(UTC)
    moving.offset = begins - clock.now() - timedelta(minutes=1) + moving.offset
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "waiting", "one minute before the day"
    world.indexer.items = [good()]
    moving.pass_by(minutes=2)
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


def test_a_book_that_gets_its_date_after_the_wish_waits_for_it_when_nothing_is_there(
        operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    token = "tok" + "d" * 13
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:5", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de", series="Nebelchor", series_pos=2.0, released=""))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                              "language": "de"})
    assert reply.json()["results"][0]["wish"]["state"] == "searching", "no date: the normal way"
    soon = (clock.now() + timedelta(days=9)).date().isoformat()
    with SessionLocal() as db:
        db.execute(update(ForeignBook).values(released=soon))
        db.commit()
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "waiting" and shown["release_date"] == soon and shown["next_try_at"] is None


def test_with_the_main_switch_off_nothing_comes_back_by_itself(operator: Any, net: FakeNetwork,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    world, sab = waiting_world(net)
    moving = Moving(monkeypatch)
    world.indexer.items = [good()]
    configure(enabled=False)
    moving.pass_by(hours=30)
    asked = len(world.indexer.requests)
    assert tick() is not None
    run_jobs()
    assert len(world.indexer.requests) == asked and wishes_of(world)[0]["state"] == "not_found" and sab.jobs == {}
    # A person who presses the button is served.
    wish_id = wishes_of(world)[0]["id"]
    assert world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    run_jobs()
    assert sab.jobs == {} and wishes_of(world)[0]["state"] == "idle", "found, but nothing is loaded by itself"
    configure(enabled=True)
    tick()
    run_jobs()
    assert len(sab.jobs) == 1 and wishes_of(world)[0]["state"] == "loading"


def test_the_planner_and_a_person_who_searches_at_the_same_moment_search_once(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    world, _sab = waiting_world(net)
    moving = Moving(monkeypatch)
    configure(rss_minutes=60)
    moving.pass_by(hours=7)
    wish_id = wishes_of(world)[0]["id"]
    before = searches()
    gate = threading.Barrier(2)
    answers: list[int] = []

    def person() -> None:
        gate.wait()
        answers.append(world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code)

    thread = threading.Thread(target=person)
    thread.start()
    gate.wait()
    tick()
    thread.join()
    run_jobs()
    assert answers == [200]
    assert searches() - before == 1, "one search for the wish, whoever came first"
    assert wishes_of(world)[0]["state"] in ("not_found", "loading")


def test_only_one_tick_runs_at_a_time(operator: Any, net: FakeNetwork) -> None:
    waiting_world(net)
    assert planner._lock.acquire(blocking=False)
    try:
        assert planner.tick() is None
    finally:
        planner._lock.release()
    assert planner.tick() is not None


def test_a_claim_is_given_to_one_only(operator: Any, net: FakeNetwork) -> None:
    waiting_world(net)
    with SessionLocal() as one, SessionLocal() as two:
        mine = one.scalar(select(ProcureWish))
        yours = two.scalar(select(ProcureWish))
        assert wishes.claim(one, mine) is True
        assert wishes.claim(two, yours) is False
        assert one.scalar(select(ProcureWish.state)) == "searching"


def test_a_new_time_between_tries_moves_the_next_try_of_the_wishes_that_wait(operator: Any, net: FakeNetwork) -> None:
    _world, _sab = waiting_world(net)
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        searched = row.searched_at
        assert row.next_try_at == searched + timedelta(hours=6)
    from fastapi.testclient import TestClient

    from app.main import app

    from .conftest import TAB

    with TestClient(app, headers=TAB) as client:
        from .conftest import sign_in

        sign_in(client, operator)
        saved = client.get("/api/v1/procure/automation").json()
        saved.pop("retention_read", None)
        assert client.put("/api/v1/procure/automation", json={**saved, "retry_hours": 24}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(ProcureWish.next_try_at)) == searched + timedelta(hours=24)


def test_a_capped_wish_waits_for_the_end_of_the_day_of_the_instance(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, good())
    zone = ZoneInfo("America/New_York")
    monkeypatch.setattr(timezones, "instance_zone", lambda: zone)
    moving = Moving(monkeypatch)
    configure(rss_minutes=60)
    from app.models import Indexer

    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=1))
        db.commit()
    wish(world)
    run_jobs()
    # One request used the day's limit; the wish found the release (the first request found it) and loaded it. A second
    # book finds the indexer at its limit.
    token = "tok" + "c" * 13
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:9", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de", series="Nebelchor", series_pos=2.0))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                              "language": "de"})
    assert reply.status_code == 201
    run_jobs()
    capped = next(item for item in wishes_of(world) if item["book"]["work_id"] is None)
    assert capped["state"] == "capped" and capped["detail"]["code"] == "indexer_daily_limit"
    local_midnight = (clock.now().astimezone(zone) + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                                              microsecond=0)
    assert capped["next_try_at"].startswith(local_midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"))
    tick()
    run_jobs()
    assert next(item for item in wishes_of(world) if item["book"]["work_id"] is None)["state"] == "capped"
    moving.offset = local_midnight.astimezone(UTC) - clock.now() + moving.offset + timedelta(minutes=1)
    tick()
    run_jobs()
    again = next(item for item in wishes_of(world) if item["book"]["work_id"] is None)
    assert again["state"] != "capped", "the day is over, the wish was searched again"
    assert downloads()


def test_a_wish_a_stop_of_the_server_left_claimed_without_a_search_is_searched_by_the_next_tick(
        operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    world.indexer.items = [good()]
    # The server stopped after the claim and before the search was made.
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        assert wishes.claim(db, row)
        assert row.state == "searching" and row.search_id is None
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


def test_a_wish_a_stop_left_between_the_newest_entries_and_the_load_goes_on_with_the_next_tick(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import rss

    world, sab = waiting_world(net)
    world.indexer.items = [good()]
    original = wishes.settle

    def stop(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the server stopped here")

    monkeypatch.setattr(wishes, "settle", stop)
    rss.sync()
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        assert row.state == "searching" and row.search_id is not None, "claimed, with the search of the entries"
    assert sab.jobs == {}
    monkeypatch.setattr(wishes, "settle", original)
    tick()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1 and len(downloads()) == 1


def test_a_search_a_stop_left_running_is_run_again_and_settles_its_wish(operator: Any, net: FakeNetwork) -> None:
    from app.models import Job
    from app.services import jobs

    sab = sabnzbd(net)
    world = audio_world(net, good())
    wish(world)
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.kind == "psearch"))
        job.state = "running"
        db.commit()
    jobs.recover(SessionLocal())
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1 and len(downloads()) == 1
