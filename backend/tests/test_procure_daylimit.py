"""The limit of the day holds when requests run at the same moment: every
request takes its place in the count before it is made, in one statement; no run overwrites the count another one wrote;
the background (the planner, the newest entries, a better format) keeps to 70 percent of the limit so that a new wish and
"Jetzt suchen" always find room; the releases fetched stop at the number the indexer names for itself."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy import update

from app.db import SessionLocal
from app.models import Indexer
from app.services.procure import budget, indexers, rations
from app.services.procure.indexers import IndexerError

from .auto_helpers import indexer_row
from .follow_helpers import AUDIO, audio_world
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item


def set_limit(limit: int, **more: Any) -> None:
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=limit, **more))
        db.commit()


def run_many(indexer_id: int, *, runs: int, each: int, background: bool = False, person: int | None = None) -> list[int]:
    """``runs`` threads, each making up to ``each`` requests through ``indexers.run`` at the same moment; how many each
    made."""
    made: list[int] = []
    gate = threading.Barrier(runs)
    lock = threading.Lock()

    def one() -> None:
        count = 0
        with SessionLocal() as db:
            row = db.get(Indexer, indexer_id)
            assert row is not None

            def work(client: indexers.Client) -> None:
                nonlocal count
                for _ in range(each):
                    try:
                        client.request({"t": "caps"})
                    except IndexerError:
                        return
                    count += 1

            how: dict[str, Any] = {}
            if background:
                how["background"] = True
            if person is not None:
                how["person"] = person
            gate.wait()
            try:
                indexers.run(db, row, work, **how)
            except IndexerError:
                pass
        with lock:
            made.append(count)

    threads = [threading.Thread(target=one) for _ in range(runs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return made


def test_runs_at_the_same_moment_never_ask_for_more_than_the_limit(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(10)
    made = run_many(world.indexer_id, runs=4, each=8)
    row = indexer_row(world.indexer_id)
    assert sum(made) == 10, made
    assert budget.used_today(row) == 10
    assert len(world.indexer.requests) <= 10


def test_no_run_loses_the_count_of_another(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(0)
    made = run_many(world.indexer_id, runs=4, each=5)
    assert sum(made) == 20
    assert budget.used_today(indexer_row(world.indexer_id)) == 20, "an indexer without a limit is counted all the same"


def test_a_request_that_is_made_is_counted_before_the_answer(operator: Any, net: FakeNetwork) -> None:
    """A run that breaks off on the way has counted what it asked, and nothing it did not ask."""
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(5)
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)

        def work(client: indexers.Client) -> None:
            client.request({"t": "caps"})
            client.request({"t": "caps"})
            raise IndexerError("indexer_timeout", 504)

        with pytest.raises(IndexerError):
            indexers.run(db, row, work)
    assert budget.used_today(indexer_row(world.indexer_id)) == 2


def test_the_background_stops_at_seventy_percent_and_a_person_goes_on(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(10)
    background = run_many(world.indexer_id, runs=3, each=6, background=True)
    assert sum(background) == 7, background
    assert budget.used_today(indexer_row(world.indexer_id)) == 7
    person = run_many(world.indexer_id, runs=1, each=6)
    assert sum(person) == 3, "the last three requests of the day are for a person"
    assert budget.spent(indexer_row(world.indexer_id))
    assert sum(run_many(world.indexer_id, runs=1, each=1)) == 0


def test_a_small_limit_keeps_one_request_for_a_person(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(2)
    assert sum(run_many(world.indexer_id, runs=1, each=3, background=True)) == 1
    assert sum(run_many(world.indexer_id, runs=1, each=3)) == 1, "the second request of the day is a person's"


def test_a_limit_of_one_is_still_the_planners_to_use(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(1)
    assert sum(run_many(world.indexer_id, runs=1, each=3, background=True)) == 1


def test_the_background_rests_when_its_part_is_used_and_the_indexer_says_so(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(10)
    run_many(world.indexer_id, runs=1, each=7, background=True)
    row = indexer_row(world.indexer_id)
    background = indexers.resting(row, background=True)
    assert background is not None and background[1] == "daily_limit"
    assert indexers.resting(row) is None, "a person is still served"


def test_a_persons_share_holds_when_their_searches_run_together(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(40)
    rations.forget()
    made = run_many(world.indexer_id, runs=4, each=20, person=world.person.id)
    assert sum(made) == 20, "a person alone has the half of 40; four searches together do not pass it"
    assert budget.used_today(indexer_row(world.indexer_id)) == 20


def test_the_releases_fetched_stop_at_the_number_the_indexer_names(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    set_limit(0, grab_max=3)
    gate = threading.Barrier(5)
    results: list[bool] = []
    lock = threading.Lock()

    def one() -> None:
        with SessionLocal() as db:
            row = db.get(Indexer, world.indexer_id)
            assert row is not None
            gate.wait()
            try:
                indexers.run(db, row, lambda client: client.fetch(f"https://{world.indexer.host}/get/abc?apikey=x", "usenet"),
                             grab=True)
                good = True
            except IndexerError:
                good = False
        with lock:
            results.append(good)

    threads = [threading.Thread(target=one) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    row = indexer_row(world.indexer_id)
    assert results.count(True) == 3, results
    assert budget.grabs_today(row) == 3
    assert len(world.indexer.fetched) == 3
    assert budget.used_today(row) == 0, "the releases fetched are counted apart from the requests"


def test_without_a_number_from_the_indexer_nothing_holds_the_fetches_back(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        assert row is not None
        for _ in range(4):
            indexers.run(db, row, lambda client: client.fetch(f"https://{world.indexer.host}/get/abc?apikey=x", "usenet"),
                         grab=True)
    assert budget.grabs_today(indexer_row(world.indexer_id)) == 4
