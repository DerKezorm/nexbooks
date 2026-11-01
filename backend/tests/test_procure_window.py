"""At most as many downloads of a wish in 24 hours as the operator allows: the
number counts every download of the wish, whoever started it (the first search, the replacement after a failure, the
planner coming back, the newest entries), and a wish that has used them waits until the first of them is a day old."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Download, ProcureWish
from app.services.procure import wishes

from .auto_helpers import Moving, configure, searching_requests, tick
from .follow_helpers import AUDIO, SECOND_AUDIO, audio_world, downloads, round_of_tracking, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .test_procure_replacement import FOURTH, THIRD, loaded, queued, releases


def fail_all(sab: Any) -> None:
    for job in list(sab.jobs):
        if sab.slots.get(job, {}).get("status") != "Failed":
            sab.fail(job)


def test_a_wish_that_always_fails_never_has_more_downloads_in_a_day_than_allowed(operator: Any, net: FakeNetwork,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60, retry_hours=6, replace_max=3)
    sab = sabnzbd(net)
    titles = [AUDIO, SECOND_AUDIO, THIRD, FOURTH] + [f"Rune Falkner-Die Laternenstrasse-MP3CD-DE-{2000 + n}-GRP{n}"
                                                    for n in range(14)]
    world = audio_world(net, *releases(*titles))
    wish(world)
    run_jobs()
    moving = Moving(monkeypatch)
    for _ in range(3 * 24 * 3):
        fail_all(sab)
        round_of_tracking()
        moving.pass_by(minutes=20)
        tick()
        run_jobs()
    made = sorted(row.created_at for row in downloads())
    assert len(made) >= 6, "the wish went on in the following days"
    for index, start in enumerate(made):
        in_a_day = [moment for moment in made[index:] if moment < start + timedelta(hours=24)]
        assert len(in_a_day) <= 3, (index, len(in_a_day))
    assert world is not None


def test_a_wish_that_used_its_downloads_waits_for_the_first_to_be_a_day_old(operator: Any, net: FakeNetwork,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    configure(replace_max=2, retry_hours=1)
    moving = Moving(monkeypatch)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO, THIRD, FOURTH)
    [first] = queued()
    started = first.created_at
    sab.fail(first.client_ref)
    round_of_tracking()
    [second] = queued()
    moving.pass_by(hours=2)
    sab.fail(second.client_ref)
    round_of_tracking()
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found" and shown["detail"]["error"] == "replace_limit"
    assert shown["next_try_at"] is not None and shown["next_try_at"].startswith((started + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M")), "the first of the two is a day old then"
    # The planner comes back every hour: not before the window is over.
    asked = len(searching_requests(world.indexer))
    for _ in range(20):
        moving.pass_by(hours=1)
        tick()
        run_jobs()
    assert len(downloads()) == 2 and queued() == []
    assert len(searching_requests(world.indexer)) == asked, "no search either: it waits for the end of the window"
    moving.pass_by(hours=4)
    tick()
    run_jobs()
    assert len(queued()) == 1, "the window is over: the wish loads again"
    assert len(downloads()) == 3


def test_the_planner_coming_back_does_not_load_past_the_number(operator: Any, net: FakeNetwork,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The window also holds a wish the chain of replacements is over for: the planner finds a release and the wish has
    used its downloads, so nothing is loaded (the releases wait in the search)."""
    configure(replace_max=1, retry_hours=1)
    moving = Moving(monkeypatch)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    assert queued() == [], "one download in a day"
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        row.state, row.next_try_at = "not_found", clock.now() - timedelta(minutes=1)
        db.commit()
    moving.pass_by(minutes=5)
    tick()
    run_jobs()
    assert queued() == [] and len(downloads()) == 1
    assert wishes_of(world)[0]["detail"]["error"] == "replace_limit"


def test_a_person_who_loads_a_release_by_hand_is_not_held_to_the_number(operator: Any, net: FakeNetwork) -> None:
    configure(replace_max=1)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    assert queued() == []
    found = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    assert found.status_code == 202
    run_jobs()
    shown = world.client.get(f"/api/v1/procure/searches/{found.json()['id']}").json()
    by_hand = next(item for item in shown["releases"] if item["title"] == SECOND_AUDIO)
    answer = world.client.post(f"/api/v1/procure/searches/{found.json()['id']}/releases/{by_hand['id']}/load", json={})
    assert answer.status_code in (200, 201, 202), answer.text
    assert len(queued()) == 1


def test_with_replacing_switched_off_a_person_who_searches_again_is_served(operator: Any, net: FakeNetwork) -> None:
    """Replacing off: the number is not asked for (the field is greyed out in the settings). The wish rests after a
    failure; a person who presses "Jetzt suchen" gets the next release."""
    configure(replace=False, replace_max=1)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    assert queued() == [] and wishes_of(world)[0]["state"] == "idle"
    wish_id = wishes_of(world)[0]["id"]
    assert world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    run_jobs()
    assert len(queued()) == 1 and len(downloads()) == 2


def test_window_end_is_the_moment_the_oldest_that_counts_is_a_day_old(operator: Any, net: FakeNetwork,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    world, sab = loaded(net, AUDIO, SECOND_AUDIO, THIRD)
    moving = Moving(monkeypatch)
    for _ in range(2):
        [current] = queued()
        sab.fail(current.client_ref)
        round_of_tracking()
        moving.pass_by(hours=3)
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        made = sorted(item.created_at for item in db.scalars(select(Download)))
        assert len(made) == 3
        assert wishes.window_end(db, row, clock.now(), 4) is None, "fewer than the number: free"
        assert wishes.window_end(db, row, clock.now(), 3) == made[0] + timedelta(hours=24)
        assert wishes.window_end(db, row, clock.now(), 2) == made[1] + timedelta(hours=24)
        assert wishes.window_end(db, row, clock.now(), 1) == made[2] + timedelta(hours=24)
    assert world is not None


def test_a_new_time_between_tries_does_not_move_the_end_of_the_window(operator: Any, net: FakeNetwork) -> None:
    configure(replace_max=1, retry_hours=24)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["detail"]["error"] == "replace_limit"
    ends = shown["next_try_at"]
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        row.searched_at = clock.now()
        db.commit()
    configure(retry_hours=1)
    with SessionLocal() as db:
        assert wishes.retime(db) == 0
        assert db.scalar(select(ProcureWish)).next_try_at.isoformat().startswith(ends[:16])
