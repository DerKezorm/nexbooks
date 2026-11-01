"""A search the limit of an indexer cuts short brings nothing: it must not wipe out what the search before found, and while
no indexer may be asked the automatic work starts none at all. Only invented books."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from app import clock
from app.db import SessionLocal
from app.models import Indexer, ProcureWish, ReleaseSearch
from app.services.procure import budget, wishes

from .filing_helpers import audio_world
from .follow_helpers import AUDIO, rows, wish
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item

DOUBTFUL = "Rune Falkner - Die Laternenstrasse - Schluss mit der Dunkelheit"


def spent() -> None:
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=10, used_count=10, used_day=budget.day()))
        db.commit()


def due_now() -> None:
    with SessionLocal() as db:
        for row in db.query(ProcureWish).all():
            row.next_try_at = clock.now() - timedelta(minutes=1)
        db.commit()


def doubted_wish(net: FakeNetwork) -> Any:
    world = audio_world(net, Item(DOUBTFUL, size=400_000_000, categories=(3130,)))
    wish(world, kinds=("audio",), language="de")
    run_jobs()
    [row] = rows(ProcureWish)
    assert row.state == "not_found" and row.detail.get("look"), (row.state, row.detail)
    return world


def test_no_search_starts_while_no_indexer_may_be_asked(operator: Any, net: FakeNetwork) -> None:
    doubted_wish(net)
    [before] = rows(ProcureWish)
    searches = len(rows(ReleaseSearch))
    spent()
    due_now()
    with SessionLocal() as db:
        assert wishes.run_due(db) == 0
    [after] = rows(ProcureWish)
    assert after.state == "capped" and after.detail["code"] == "indexer_daily_limit" and after.next_try_at is not None
    assert after.next_try_at > clock.now()
    assert after.detail["look"] == before.detail["look"] and after.detail["total"] == before.detail["total"]
    assert after.search_id == before.search_id
    assert len(rows(ReleaseSearch)) == searches
    with SessionLocal() as db:
        assert wishes.run_due(db) == 0
    assert len(rows(ReleaseSearch)) == searches, "it is not due again before the limit is over"


def capped_wish(net: FakeNetwork) -> None:
    doubted_wish(net)
    spent()
    due_now()
    with SessionLocal() as db:
        wishes.run_due(db)
    [row] = rows(ProcureWish)
    assert row.state == "capped" and row.next_try_at > clock.now()


@pytest.mark.parametrize("limit", [0, 500])
def test_a_limit_taken_away_or_raised_frees_a_waiting_wish_at_once(operator: Any, net: FakeNetwork, limit: int) -> None:
    capped_wish(net)
    searches = len(rows(ReleaseSearch))
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=limit))
        db.commit()
    with SessionLocal() as db:
        assert wishes.run_due(db) == 1
    assert len(rows(ReleaseSearch)) == searches + 1


def test_a_limit_that_still_holds_keeps_the_wish_waiting(operator: Any, net: FakeNetwork) -> None:
    capped_wish(net)
    [before] = rows(ProcureWish)
    with SessionLocal() as db:
        assert wishes.limit_lifted(db) == 0
    [after] = rows(ProcureWish)
    assert after.next_try_at == before.next_try_at and after.detail == before.detail


def test_the_share_of_a_person_ends_with_the_limit_only(operator: Any, net: FakeNetwork) -> None:
    capped_wish(net)
    with SessionLocal() as db:
        [row] = db.query(ProcureWish).all()
        row.detail = {**row.detail, "code": "indexer_share_used"}
        db.execute(update(Indexer).values(used_count=0))
        db.commit()
    with SessionLocal() as db:
        assert wishes.limit_lifted(db) == 0
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=0))
        db.commit()
    with SessionLocal() as db:
        assert wishes.limit_lifted(db) == 1


def test_a_full_count_of_fetches_keeps_the_wish_waiting(operator: Any, net: FakeNetwork) -> None:
    capped_wish(net)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=0, grab_max=5, grab_current=5, limits_seen_at=clock.now(),
                                          grab_paused_until=clock.now() + timedelta(hours=3)))
        db.commit()
    with SessionLocal() as db:
        assert wishes.limit_lifted(db) == 0


def test_an_indexer_switched_off_is_not_waited_for(operator: Any, net: FakeNetwork) -> None:
    capped_wish(net)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(enabled=False))
        db.commit()
    with SessionLocal() as db:
        assert wishes.limit_lifted(db) == 1
    [after] = rows(ProcureWish)
    assert after.next_try_at <= clock.now() and "until" not in after.detail


def test_a_fault_of_the_indexers_leaves_a_wish_as_it_was(operator: Any, net: FakeNetwork) -> None:
    doubted_wish(net)
    [before] = rows(ProcureWish)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(paused_until=clock.now() + timedelta(hours=1), pause_cause="server_error"))
        db.commit()
    due_now()
    with SessionLocal() as db:
        assert wishes.run_due(db) == 0
    [after] = rows(ProcureWish)
    assert after.state == before.state and after.detail == before.detail


def test_a_wish_comes_back_when_an_indexer_may_be_asked_again(operator: Any, net: FakeNetwork) -> None:
    doubted_wish(net)
    due_now()
    with SessionLocal() as db:
        assert wishes.run_due(db) == 1


def test_a_person_who_asks_gets_a_search_all_the_same_and_keeps_what_was_found(operator: Any,
                                                                               net: FakeNetwork) -> None:
    world = doubted_wish(net)
    [before] = rows(ProcureWish)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=1, used_count=1, used_day=budget.day()))
        db.commit()
    reply = world.client.post(f"/api/v1/procure/wishes/{before.id}/search")
    assert reply.status_code in (200, 202), reply.text
    run_jobs()
    [after] = rows(ProcureWish)
    assert after.state == "capped", after.state
    assert after.detail.get("look") == before.detail["look"]
    assert after.detail.get("total") == before.detail["total"]
    assert after.detail.get("reasons") == before.detail["reasons"]
    assert after.search_id == before.search_id
    assert "earlier" not in after.detail


def test_a_search_cut_short_after_it_found_something_says_what_it_found(operator: Any, net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    world = doubted_wish(net)
    [before] = rows(ProcureWish)
    world.indexer.items = [Item(DOUBTFUL, size=400_000_000, categories=(3130,)),
                           Item(f"{DOUBTFUL} und mehr", size=400_000_000, categories=(3130,))]
    monkeypatch.setattr(wishes, "_capped", lambda _found: {"indexer": "Shelf", "until": None,
                                                           "code": "indexer_daily_limit", "limit": 1})
    reply = world.client.post(f"/api/v1/procure/wishes/{before.id}/search")
    assert reply.status_code in (200, 202), reply.text
    run_jobs()
    [after] = rows(ProcureWish)
    assert after.state == "capped" and after.detail["total"] == 2 and after.search_id != before.search_id


def test_a_search_that_found_something_replaces_what_was_known(operator: Any, net: FakeNetwork) -> None:
    world = doubted_wish(net)
    [before] = rows(ProcureWish)
    world.indexer.items = []
    reply = world.client.post(f"/api/v1/procure/wishes/{before.id}/search")
    assert reply.status_code in (200, 202), reply.text
    run_jobs()
    [after] = rows(ProcureWish)
    assert after.detail.get("total") == 0 and "earlier" not in after.detail and after.search_id != before.search_id


def test_every_search_of_a_wish_leaves_a_line(operator: Any, net: FakeNetwork,
                                              caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="nexbooks.procure"):
        doubted_wish(net)
    assert any("found releases=1 passing=0 doubts=1 cut_short=False" in line for line in caplog.messages), caplog.messages


def test_a_wish_that_knew_nothing_stays_as_it_was(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    world.indexer.items = []
    wish(world, kinds=("audio",), language="de")
    run_jobs()
    [row] = rows(ProcureWish)
    assert wishes._earlier(row) is None
