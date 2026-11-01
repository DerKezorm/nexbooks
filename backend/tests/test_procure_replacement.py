"""The replacement after a failure: the next release of the same search at once, no new request to the
indexers while there is one; a new search when none is left; at most as many replacements in 24 hours as the operator
allows; the clock of the window; a release no program took is not tried again at the same hand."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BlocklistEntry, Download, ProcureWish

from .auto_helpers import Moving, configure, searching_requests
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

THIRD = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2020-THIRDGRP"
FOURTH = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2019-FOURTHGRP"


def releases(*titles: str) -> list[Item]:
    return [Item(title, size=400_000_000 + number * 10_000_000, categories=(3130,), days=3 + number)
            for number, title in enumerate(titles)]


def loaded(net: FakeNetwork, *titles: str) -> tuple[Any, Any]:
    sab = sabnzbd(net)
    world = audio_world(net, *releases(*titles))
    wish(world)
    run_jobs()
    return world, sab


def queued() -> list[Download]:
    return [item for item in downloads() if item.state == "queued"]


def test_the_next_release_of_the_same_search_is_taken_at_once_without_a_new_request(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    world, sab = loaded(net, AUDIO, SECOND_AUDIO, THIRD)
    [first] = queued()
    asked = len(searching_requests(world.indexer))
    sab.fail(first.client_ref)
    round_of_tracking()
    # No run of the jobs: the wish did not wait for a search.
    [second] = queued()
    assert second.title != first.title and second.wish_id == first.wish_id
    assert len(searching_requests(world.indexer)) == asked, "no request to the indexer, only the file of the release"
    assert [entry.title for entry in rows(BlocklistEntry)] == [first.title]
    assert wishes_of(world)[0]["state"] == "loading"
    sab.fail(second.client_ref)
    round_of_tracking()
    [third] = queued()
    assert third.title not in (first.title, second.title)
    assert {first.title, second.title, third.title} == {AUDIO, SECOND_AUDIO, THIRD}


def test_with_no_release_left_a_new_search_is_made(operator: Any, net: FakeNetwork) -> None:
    world, sab = loaded(net, AUDIO)
    [first] = queued()
    asked = len(searching_requests(world.indexer))
    sab.fail(first.client_ref)
    round_of_tracking()
    assert queued() == []
    assert wishes_of(world)[0]["state"] == "searching"
    run_jobs()
    assert len(searching_requests(world.indexer)) > asked, "the indexers are asked again"
    assert wishes_of(world)[0]["state"] == "not_found", "the only release is blocked"


@pytest.mark.parametrize("allowed", [1, 2, 3])
def test_a_wish_gets_as_many_replacements_in_a_day_as_the_operator_allows_and_not_one_more(
        operator: Any, net: FakeNetwork, allowed: int) -> None:
    configure(replace_max=allowed)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO, THIRD, FOURTH)
    count = 1
    while True:
        [current] = queued()
        sab.fail(current.client_ref)
        round_of_tracking()
        run_jobs()
        if not queued():
            break
        count += 1
    assert count == allowed, "as many downloads in a day as the operator allows: the first release and the replacements"
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found" and shown["detail"]["error"] == "replace_limit"


def test_the_downloads_of_the_last_24_hours_count_and_not_the_older_ones(operator: Any, net: FakeNetwork,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    configure(replace_max=2)
    moving = Moving(monkeypatch)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO, THIRD)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    [second] = queued()
    moving.pass_by(hours=25)
    sab.fail(second.client_ref)
    round_of_tracking()
    run_jobs()
    assert len(queued()) == 1, "the two failures are a day old: this is the first download of the new day"
    assert wishes_of(world)[0]["state"] == "loading"


def test_a_search_older_than_a_day_is_not_read_again_for_the_next_release(operator: Any, net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    asked = len(searching_requests(world.indexer))
    moving.pass_by(hours=25)
    sab.fail(first.client_ref)
    round_of_tracking()
    assert queued() == [], "the releases of yesterday's search are not trusted"
    run_jobs()
    assert len(searching_requests(world.indexer)) > asked
    assert len(queued()) == 1


def test_a_hand_over_no_program_took_goes_on_with_another_release_not_the_same(operator: Any, net: FakeNetwork,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, *releases(AUDIO, SECOND_AUDIO))
    moving = Moving(monkeypatch)
    sab.add_timeout = True
    wish(world)
    run_jobs()
    [first] = downloads()
    assert first.state == "unsure"
    sab.add_timeout = False
    moving.pass_by(minutes=11)
    round_of_tracking()
    states = {item.id: item for item in downloads()}
    assert states[first.id].state == "failed" and states[first.id].failed_reason == "not_taken"
    other = [item for item in downloads() if item.id != first.id]
    assert len(other) == 1 and other[0].title != first.title and other[0].state == "queued"
    assert rows(BlocklistEntry) == [], "a release no program took is not blocked"


def test_with_replacing_switched_off_the_next_release_is_not_taken(operator: Any, net: FakeNetwork) -> None:
    configure(replace=False)
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    assert queued() == [] and wishes_of(world)[0]["state"] == "idle"
    assert wishes_of(world)[0]["detail"]["reason"] == "failed"


def test_with_the_main_switch_off_a_failure_leaves_the_wish_and_loads_nothing(operator: Any, net: FakeNetwork) -> None:
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    configure(enabled=False)
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    assert queued() == [] and wishes_of(world)[0]["state"] == "idle"


def test_a_release_that_was_blocked_meanwhile_is_not_taken_as_the_next(operator: Any, net: FakeNetwork) -> None:
    from app.services.procure import blocklist

    world, sab = loaded(net, AUDIO, SECOND_AUDIO, THIRD)
    [first] = queued()
    with SessionLocal() as db:
        db.add(BlocklistEntry(work_id=world.work_id, kind="audio", title=SECOND_AUDIO,
                              title_key=blocklist.key_of(SECOND_AUDIO), protocol="usenet", reason="by_hand"))
        db.commit()
    sab.fail(first.client_ref)
    round_of_tracking()
    [second] = queued()
    assert second.title == THIRD, "the one a person blocked in the meantime is skipped"


def test_the_replacement_is_the_wishes_own_and_leaves_no_second_wish(operator: Any, net: FakeNetwork) -> None:
    world, sab = loaded(net, AUDIO, SECOND_AUDIO)
    [first] = queued()
    sab.fail(first.client_ref)
    round_of_tracking()
    with SessionLocal() as db:
        assert len(db.scalars(select(ProcureWish)).all()) == 1
    assert world is not None


def test_a_download_the_check_turns_down_is_replaced_by_the_next_release_at_once(operator: Any, net: FakeNetwork,
                                                                                tmp_path: Any,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from .filing_helpers import audio_wish, download_roots, finish

    roots = download_roots(tmp_path, monkeypatch)
    world, sab, job = audio_wish(net, *releases(AUDIO, SECOND_AUDIO))
    [first] = [item for item in downloads() if item.state == "queued"]
    asked = len(searching_requests(world.indexer))
    folder = roots / "complete" / "nothing"
    folder.mkdir(parents=True)
    (folder / "readme.txt").write_text("no book here", encoding="utf-8")
    finish(sab, job, folder)
    states = {item.id: item for item in downloads()}
    assert states[first.id].state == "rejected"
    [second] = [item for item in downloads() if item.state == "queued"]
    assert second.title != first.title and second.wish_id == first.wish_id
    assert len(searching_requests(world.indexer)) == asked, "the next release of the same search, no new request"
    assert [entry.title for entry in rows(BlocklistEntry)] == [first.title]


def test_a_release_no_program_took_is_searched_for_again_when_it_is_the_only_one(operator: Any, net: FakeNetwork,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, *releases(AUDIO))
    moving = Moving(monkeypatch)
    sab.add_timeout = True
    wish(world)
    run_jobs()
    assert downloads()[0].state == "unsure"
    asked = len(searching_requests(world.indexer))
    sab.add_timeout = False
    moving.pass_by(minutes=11)
    round_of_tracking()
    assert wishes_of(world)[0]["state"] == "searching", "nothing else to take from that search: a new one is made"
    run_jobs()
    assert len(searching_requests(world.indexer)) > asked
    [fresh] = [item for item in downloads() if item.state == "queued"]
    assert fresh.title == AUDIO and rows(BlocklistEntry) == []
