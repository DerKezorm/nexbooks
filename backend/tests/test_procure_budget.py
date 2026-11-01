"""The day of the indexers: the limit of requests per day starts again at midnight of the instance's time
zone; searches and the reading of the newest entries count, the releases fetched are counted apart; an indexer at its limit
keeps its wishes waiting while the others go on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import Indexer, ProcureWish
from app.services import timezones
from app.services.procure import budget, rss

from .auto_helpers import Moving, indexer_row, tick
from .follow_helpers import AUDIO, SECOND_AUDIO, audio_world, downloads, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import FeedIndexer, Item, add_indexer

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"


def good(title: str = AUDIO) -> Item:
    return Item(title, size=400_000_000, categories=(3130,))


def at(zone: str, *moment: int) -> datetime:
    return datetime(*moment, tzinfo=ZoneInfo(zone)).astimezone(UTC)  # type: ignore[arg-type]


@pytest.mark.parametrize(("zone", "now", "day", "ends"), [
    ("UTC", at("UTC", 2026, 10, 16, 23, 30), "2026-10-16", at("UTC", 2026, 10, 17)),
    ("Pacific/Auckland", at("UTC", 2026, 10, 16, 23, 30), "2026-10-17", at("Pacific/Auckland", 2026, 10, 18)),
    ("America/New_York", at("UTC", 2026, 10, 17, 3, 59), "2026-10-16", at("America/New_York", 2026, 10, 17)),
    ("America/New_York", at("UTC", 2026, 10, 17, 4, 1), "2026-10-17", at("America/New_York", 2026, 10, 18)),
    ("Europe/Berlin", at("Europe/Berlin", 2026, 3, 28, 23, 59), "2026-03-28", at("Europe/Berlin", 2026, 3, 29)),
    ("Europe/Berlin", at("Europe/Berlin", 2026, 10, 24, 23, 59), "2026-10-24", at("Europe/Berlin", 2026, 10, 25)),
])
def test_the_day_is_the_day_of_the_instances_time_zone(monkeypatch: pytest.MonkeyPatch, zone: str, now: datetime,
                                                       day: str, ends: datetime) -> None:
    monkeypatch.setattr(timezones, "instance_zone", lambda: ZoneInfo(zone))
    assert budget.day(now) == day
    assert budget.midnight(now) == ends


def test_the_longest_and_the_shortest_day_of_the_change_of_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timezones, "instance_zone", lambda: ZoneInfo("Europe/Berlin"))
    spring = budget.midnight(at("Europe/Berlin", 2026, 3, 29, 1, 0)) - at("Europe/Berlin", 2026, 3, 29, 0, 0)
    autumn = budget.midnight(at("Europe/Berlin", 2026, 10, 25, 0, 30)) - at("Europe/Berlin", 2026, 10, 25, 0, 0)
    assert spring == timedelta(hours=23) and autumn == timedelta(hours=25)


def test_the_count_of_the_day_starts_again_at_midnight_of_the_zone_not_of_greenwich(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    zone = ZoneInfo("Pacific/Auckland")
    monkeypatch.setattr(timezones, "instance_zone", lambda: zone)
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    moving = Moving(monkeypatch)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=50))
        db.commit()
    wish(world)
    run_jobs()
    used = budget.used_today(indexer_row())
    assert used >= 1
    ends = budget.midnight()
    moving.offset = ends - clock.now() + moving.offset - timedelta(minutes=1)
    assert budget.used_today(indexer_row()) == used, "one minute before midnight of Auckland"
    moving.pass_by(minutes=2)
    assert budget.used_today(indexer_row()) == 0, "a new day there, though it is a few hours before 0:00 UTC"


def test_requests_and_releases_fetched_are_counted_apart(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, good())
    wish(world)
    run_jobs()
    assert len(downloads()) == 1
    row = indexer_row()
    asked = [request for request in world.indexer.requests if not request.url.path.startswith("/get/")]
    assert budget.used_today(row) == len(asked) and budget.grabs_today(row) == 1 and row.grabs == 1
    shown = world.client.get("/api/v1/procure/indexers")
    assert shown.status_code in (200, 403)


def test_the_listing_of_the_operator_shows_both_counts_and_the_end_of_the_day(operator: Any, net: FakeNetwork,
                                                                              client: Any) -> None:
    sabnzbd(net)
    world = audio_world(net, good())
    wish(world)
    run_jobs()
    listed = client.get("/api/v1/procure/indexers").json()["indexers"][0]
    assert listed["used_today"] == budget.used_today(indexer_row()) and listed["grabs_today"] == 1
    assert listed["day_ends_at"].endswith("Z") or "+" in listed["day_ends_at"]
    assert listed["rss_checked_at"] is None and listed["rss_error"] == ""
    assert world is not None


def test_a_limit_used_up_by_the_search_does_not_stop_the_file_of_the_release_it_found(operator: Any,
                                                                                      net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, good())
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=1))
        db.commit()
    wish(world)
    run_jobs()
    row = indexer_row()
    assert budget.spent(row) and budget.used_today(row) == 1
    assert len(downloads()) == 1 and len(sab.jobs) == 1 and budget.grabs_today(row) == 1


def test_a_wish_held_by_the_limit_of_the_day_names_the_limit(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=5, used_count=5, used_day=budget.day()))
        db.commit()
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "capped"
    assert shown["detail"]["code"] == "indexer_daily_limit" and shown["detail"]["limit"] == 5


def test_an_indexer_at_its_limit_keeps_the_wish_waiting_until_midnight_and_asks_nothing_more(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    moving = Moving(monkeypatch)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=1))
        db.commit()
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "capped" and shown["detail"]["code"] == "indexer_daily_limit"
    until = datetime.fromisoformat(shown["next_try_at"])
    assert until == budget.midnight()
    asked = len(world.indexer.requests)
    # Some time passes, but not into the next day (whatever the hour the test runs at).
    moving.pass_by(seconds=min(3 * 3600, (budget.midnight() - clock.now()).total_seconds() / 2))
    tick()
    run_jobs()
    assert len(world.indexer.requests) == asked, "not asked again today"
    world.indexer.items = [good()]
    moving.offset = until - clock.now() + moving.offset + timedelta(minutes=1)
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading", "the day is over: searched, found, loaded"


def test_the_other_indexers_go_on_while_one_is_at_its_limit(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    second.items = [good()]
    add_indexer(second, name="Second", priority=5)
    with SessionLocal() as db:
        db.execute(update(Indexer).where(Indexer.name == "Shelf").values(daily_limit=1, used_count=1,
                                                                         used_day=budget.day()))
        db.commit()
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "loading" and len(sab.jobs) == 1, "found at the second indexer"
    assert not [request for request in world.indexer.requests if request.url.params.get("t") == "search"]


def test_a_release_whose_indexer_cannot_give_the_file_is_replaced_by_one_of_another_indexer(
        operator: Any, net: FakeNetwork) -> None:
    import hashlib

    sab = sabnzbd(net)
    world = audio_world(net, good())
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    second.items = [good(SECOND_AUDIO)]
    add_indexer(second, name="Second", priority=30)
    # The better indexer says "too many downloads today" when the file is asked for.
    guid = hashlib.sha256(AUDIO.encode()).hexdigest()[:16]
    world.indexer.files[guid] = httpx.Response(429, headers={"retry-after": "7200"}, content=b"")
    wish(world)
    run_jobs()
    [row] = downloads()
    assert row.title == SECOND_AUDIO and row.indexer_name == "Second" and len(sab.jobs) == 1
    first = indexer_row(world.indexer_id)
    assert first.paused_until is not None and first.pause_cause == "request_limit"
    assert wishes_of(world)[0]["state"] == "loading"


def test_when_every_indexer_refuses_the_file_the_wish_waits_for_the_end_of_the_limit(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    import hashlib

    sab = sabnzbd(net)
    world = audio_world(net, good())
    guid = hashlib.sha256(AUDIO.encode()).hexdigest()[:16]
    world.indexer.files[guid] = httpx.Response(429, headers={"retry-after": "7200"}, content=b"")
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "capped" and shown["detail"]["code"] == "indexer_limit_reached"
    assert shown["detail"]["indexer"] == "Shelf" and shown["detail"]["until"]
    assert downloads() == [] and sab.jobs == {}
    until = datetime.fromisoformat(shown["next_try_at"])
    assert timedelta(minutes=110) < until - clock.now() < timedelta(minutes=130)


def test_the_count_of_grabs_of_the_indexer_holds_back_the_files_until_it_is_over(operator: Any,
                                                                                  net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, good())
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    second.items = [good(SECOND_AUDIO)]
    add_indexer(second, name="Second", priority=30)
    soon = clock.now() + timedelta(hours=2)
    with SessionLocal() as db:
        db.execute(update(Indexer).where(Indexer.name == "Shelf").values(grab_paused_until=soon))
        db.commit()
    wish(world)
    run_jobs()
    [row] = downloads()
    assert row.title == SECOND_AUDIO, "the first indexer's grabs are used up: its release is not fetched"
    assert world.indexer.fetched == [] and len(sab.jobs) == 1


def test_apilimits_with_all_grabs_used_hold_the_indexer_back_until_the_time_it_names(operator: Any,
                                                                                      net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    feed = world.indexer.feed
    del feed
    from app.services.procure import indexers, newznab

    # The time the indexer names lies ahead of the clock of the test, whatever day the test runs on (a date written into the
    # test was in the past from 17 October 2026, 23:00 UTC on, and the pause then fell back to the unknown reset).
    named = (clock.now() + timedelta(hours=3)).replace(microsecond=0)
    limits = newznab.ApiLimits(api_current=3, api_max=100, grab_current=20, grab_max=20,
                               grab_next_at=format_datetime(named))
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        client = indexers.Client(indexers.target_of(db, row))
        client.limits = limits
        indexers._record(db, row, client, None)
        row = db.get(Indexer, world.indexer_id)
        assert row.grab_paused_until == named
        assert indexers.resting(row, grab=True) is not None and indexers.resting(row) is None
        limits = newznab.ApiLimits(grab_current=2, grab_max=20)
        client.limits = limits
        indexers._record(db, row, client, None)
        assert db.get(Indexer, world.indexer_id).grab_paused_until is None


def test_the_reading_of_the_newest_entries_counts_with_the_requests(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    before = budget.used_today(indexer_row())
    assert rss.sync() == 1
    assert budget.used_today(indexer_row()) == before + 1 and budget.grabs_today(indexer_row()) == 0


def test_nothing_is_left_of_a_wish_with_the_state_capped_when_the_limit_is_raised(operator: Any, net: FakeNetwork,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    moving = Moving(monkeypatch)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=1))
        db.commit()
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "capped"
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=0))
        db.commit()
    # The planner waits for the time named; "Jetzt suchen" does not wait (a person decides).
    wish_id = wishes_of(world)[0]["id"]
    assert world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    run_jobs()
    assert wishes_of(world)[0]["state"] == "not_found"
    moving.pass_by(minutes=1)
    with SessionLocal() as db:
        assert db.scalar(select(ProcureWish.state)) == "not_found"


def test_the_zone_of_the_instance_is_the_one_the_machine_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert timezones.detect_zone() == ZoneInfo("Asia/Tokyo")
    monkeypatch.setenv("TZ", ":America/Chicago")
    assert timezones.detect_zone() == ZoneInfo("America/Chicago")
    # A name that is no zone changes nothing about the answer being a zone of some kind.
    monkeypatch.setenv("TZ", "Not/AZone")
    assert timezones.detect_zone().utcoffset(datetime(2026, 10, 16, tzinfo=UTC)) is not None


def test_an_indexer_that_refuses_the_files_is_given_up_for_the_search_not_release_by_release(
        operator: Any, net: FakeNetwork) -> None:
    import hashlib

    sab = sabnzbd(net)
    third = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2020-THIRDGRP"
    fourth = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2019-FOURTHGRP"
    world = audio_world(net, good(AUDIO), good(SECOND_AUDIO), good(third))
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    second.items = [good(fourth)]
    add_indexer(second, name="Second", priority=40)
    for title in (AUDIO, SECOND_AUDIO, third):
        guid = hashlib.sha256(title.encode()).hexdigest()[:16]
        world.indexer.files[guid] = httpx.Response(429, headers={"retry-after": "7200"}, content=b"")
    wish(world)
    run_jobs()
    [row] = downloads()
    assert row.title == fourth and row.indexer_name == "Second" and len(sab.jobs) == 1
    assert len(world.indexer.fetched) == 1, "the first refusal was enough: the other releases of that indexer were left"


def test_the_requests_an_indexer_has_used_up_do_not_hold_back_the_files_but_a_request_limit_does(
        operator: Any, net: FakeNetwork) -> None:
    from app.services.procure import indexers

    sabnzbd(net)
    world = audio_world(net, good())
    soon = clock.now() + timedelta(hours=2)
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        row.paused_until, row.pause_cause = soon, "api_limit"
        db.commit()
        row = db.get(Indexer, world.indexer_id)
        assert indexers.resting(row) is not None and indexers.resting(row, grab=True) is None
        row.pause_cause = "request_limit"
        db.commit()
        row = db.get(Indexer, world.indexer_id)
        assert indexers.resting(row, grab=True) is not None
