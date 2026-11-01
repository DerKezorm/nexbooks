"""The background does not eat the budget of the indexers: a wish that has
been looked for in vain for long is looked for less often (once a day after a week, once a week after a month), and the
planner, the newest entries and the search for a better format use at most 70 percent of an indexer's limit of the day,
so that a new wish and "Jetzt suchen" always find room."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, Indexer, ProcureWish
from app.services.procure import budget, wishes

from .auto_helpers import Moving, configure, indexer_row, searching_requests, tick
from .follow_helpers import AUDIO, audio_world, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def aged(days: float) -> ProcureWish:
    return ProcureWish(created_at=NOW - timedelta(days=days))


@pytest.mark.parametrize(("days", "hours", "expected"), [
    (0.1, 6, timedelta(hours=6)),
    (6.9, 6, timedelta(hours=6)),
    (7.0, 6, timedelta(hours=24)),
    (7.0, 1, timedelta(hours=24)),
    (12, 24, timedelta(hours=24)),
    (29.9, 3, timedelta(hours=24)),
    (30.0, 6, timedelta(days=7)),
    (400, 1, timedelta(days=7)),
    (400, 24, timedelta(days=7)),
])
def test_the_time_to_the_next_search_grows_with_the_age_of_the_wish(days: float, hours: int,
                                                                    expected: timedelta) -> None:
    assert wishes.retry_for(aged(days), hours, NOW) == expected


def make_not_found(world: Any, monkeypatch: pytest.MonkeyPatch, days: float) -> Moving:
    """A wish that was made ``days`` ago and found nothing, due again now."""
    sab_world = world
    wish(sab_world)
    run_jobs()
    with SessionLocal() as db:
        db.execute(update(ProcureWish).values(created_at=clock.now() - timedelta(days=days)))
        db.commit()
    moving = Moving(monkeypatch)
    moving.pass_by(hours=7)
    return moving


@pytest.mark.parametrize(("days", "hours"), [(2, 6), (8, 24), (40, 24 * 7)])
def test_an_old_wish_that_finds_nothing_is_searched_less_often(operator: Any, net: FakeNetwork,
                                                              monkeypatch: pytest.MonkeyPatch, days: int,
                                                              hours: int) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    make_not_found(world, monkeypatch, days)
    before = clock.now()
    tick()
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found"
    assert datetime.fromisoformat(shown["next_try_at"]) - before == timedelta(hours=hours) or abs(
        (datetime.fromisoformat(shown["next_try_at"]) - before) - timedelta(hours=hours)) < timedelta(seconds=30)


def test_the_time_chosen_by_the_operator_is_kept_for_the_old_wish_when_it_is_longer(operator: Any, net: FakeNetwork,
                                                                                   monkeypatch: pytest.MonkeyPatch
                                                                                   ) -> None:
    configure(retry_hours=24)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    make_not_found(world, monkeypatch, 10)
    with SessionLocal() as db:
        assert wishes.retime(db) in (0, 1)
        row = db.scalar(select(ProcureWish))
        assert row.next_try_at - row.searched_at == timedelta(hours=24)


def many_wishes(world: Any, first: int, number: int) -> list[int]:
    ids = []
    for count in range(first, first + number):
        token = f"bgd{count:013d}"
        with SessionLocal() as db:
            db.add(ForeignBook(token=token, ref=f"hardcover:{800 + count}", title=f"Nachtwache{count}x",
                               authors=["Rune Falkner"], language="de"))
            db.commit()
        reply = world.client.post("/api/v1/procure/wishes", json={
            "items": [{"book": token}], "kinds": ["audio"], "language": "de"})
        assert reply.status_code == 201, reply.text
        ids.append(reply.json()["results"][0]["wish"]["id"])
        run_jobs()
    return ids


def test_the_planner_stops_at_its_part_of_the_limit_and_a_person_goes_on(operator: Any, net: FakeNetwork,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 10, 16, 0, 5, tzinfo=UTC)
    monkeypatch.setattr("app.services.timezones.instance_zone", lambda: UTC)
    moving = Moving(monkeypatch)
    monkeypatch.setattr(clock, "now", lambda: start + moving.offset)
    configure(rss_minutes=60, retry_hours=6)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    backlog = many_wishes(world, 0, 5)
    assert all(row["state"] == "not_found" for row in wishes_of(world))
    per_search = len(searching_requests(world.indexer)) // 5
    assert per_search >= 1
    # The day of the limit begins now; the wishes fall due.
    moving.pass_by(hours=7)
    limit = per_search * 6
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=limit, used_count=0, used_day=budget.day()))
        db.commit()
    cap = budget.cap_of(limit, background=True)
    for _ in range(12):
        tick()
        run_jobs()
    used = budget.used_today(indexer_row(world.indexer_id))
    assert used <= cap, (used, cap)
    states = [row["state"] for row in wishes_of(world)]
    assert states.count("capped") >= 1, states
    # A new wish of a person still finds room: it is not the planner's.
    [new_id] = many_wishes(world, 50, 1)
    assert next(row for row in wishes_of(world) if row["id"] == new_id)["state"] == "not_found"
    assert budget.used_today(indexer_row(world.indexer_id)) > cap
    # So does "Jetzt suchen" of a wish the planner left capped.
    capped = next(row for row in wishes_of(world) if row["state"] == "capped")
    before = len(searching_requests(world.indexer))
    assert world.client.post(f"/api/v1/procure/wishes/{capped['id']}/search").status_code == 200
    run_jobs()
    assert len(searching_requests(world.indexer)) > before or budget.spent(indexer_row(world.indexer_id))
    assert backlog


def test_the_search_the_planner_starts_is_marked_as_background(operator: Any, net: FakeNetwork,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import Job

    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    with SessionLocal() as db:
        first = db.scalar(select(Job).where(Job.kind == "psearch"))
        assert first is not None and not (first.payload or {}).get("background"), "a new wish is a person's"
    run_jobs()
    moving = Moving(monkeypatch)
    moving.pass_by(hours=7)
    with SessionLocal() as db:
        assert wishes.run_due(db) == 1
        jobs = list(db.scalars(select(Job).where(Job.kind == "psearch", Job.state == "queued")))
        assert len(jobs) == 1 and jobs[0].payload == {"background": True}
    run_jobs()
    wish_id = wishes_of(world)[0]["id"]
    moving.pass_by(hours=1)
    assert world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.kind == "psearch", Job.state == "queued")))
        assert len(jobs) == 1 and not (jobs[0].payload or {}).get("background"), "'Jetzt suchen' is a person's"


def test_good_release_in_the_feed_is_still_taken_in_the_background(operator: Any, net: FakeNetwork,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The background's part is for searching: a release found is loaded, whatever is left of the day."""
    from .follow_helpers import downloads, sabnzbd

    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    moving = Moving(monkeypatch)
    moving.pass_by(hours=7)
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,))]
    tick()
    run_jobs()
    assert len(downloads()) == 1 and len(sab.jobs) == 1


def test_the_wish_that_was_searched_longest_ago_comes_first_when_all_are_due_at_once(
        operator: Any, net: FakeNetwork) -> None:
    """At midnight every wish held back by the limit is due at the same moment. The limit of the day reaches only some of
    them: the next day begins with the ones that were searched longest ago, not with the same few by their number."""
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    ids = many_wishes(world, 0, 4)
    moment = clock.now()
    with SessionLocal() as db:
        for number, wish_id in enumerate(ids, start=1):
            row = db.get(ProcureWish, wish_id)
            # The first wish was searched an hour ago, the last one four hours ago; all wait for the same moment.
            row.state, row.searched_at = "capped", moment - timedelta(hours=number)
            row.next_try_at, row.updated_at = moment - timedelta(minutes=1), moment - timedelta(minutes=1)
        db.commit()
    for expected in (ids[3], ids[2], ids[1], ids[0]):
        with SessionLocal() as db:
            assert wishes.run_due(db) == 1
            taken = [row.id for row in db.scalars(select(ProcureWish)) if row.state == "searching" and row.search_id]
        assert expected in taken, (expected, taken)
        with SessionLocal() as db:
            # Searched, found nothing: it goes to the back (its search is the newest now).
            db.execute(update(ProcureWish).where(ProcureWish.id == expected).values(
                state="not_found", search_id=None, searched_at=clock.now(), next_try_at=clock.now() + timedelta(days=1)))
            db.commit()
        run_jobs()
