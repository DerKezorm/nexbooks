"""Brakes per person on fetching books: wishes are served person by
person in turn, an indexer's limit of the day is shared fairly, and each person has a number of wishes and searches a
day; the operator has none. A list of wishes is read without starting anything, and a request that makes no wish says so.
Only invented names, example.com, keys made at run time."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import Indexer, ProcureWish, ReleaseSearch
from app.services.procure import rations, wishes

from .auto_helpers import Moving
from .conftest import make_account, new_client
from .follow_helpers import planner_tick, wish_rows
from .library_helpers import join, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import World, allow_languages, make_world


def _limit(world: World, limit: int) -> None:
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        assert row is not None
        row.daily_limit = limit
        db.commit()


def _used(world: World) -> int:
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        assert row is not None
        return row.used_count


def _search(client: TestClient, world: World) -> dict[str, Any]:
    answer = client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "new": True})
    assert answer.status_code == 202, answer.text
    run_jobs()
    shown = client.get(f"/api/v1/procure/searches/{answer.json()['id']}")
    assert shown.status_code == 200
    return shown.json()  # type: ignore[no-any-return]


# --- (b) a fair share of an indexer's limit ------------------------------------------------------------------------


def test_one_person_never_spends_the_day_of_an_indexer_for_the_next(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    _limit(world, 40)
    errors = []
    for _ in range(15):
        found = _search(world.client, world)
        errors.extend(entry["error"] for entry in found["indexers"] if entry["error"])
    # Alone today: the limit divided by one person plus the place kept for the next.
    assert _used(world) == 20, _used(world)
    assert "indexer_share_used" in errors and "indexer_daily_limit" not in errors
    other = make_account("uwe")
    join(world.library_id, other.id, "acquire")
    with new_client(other) as uwe:
        found = _search(uwe, world)
    assert [entry["error"] for entry in found["indexers"]] == [""], found["indexers"]
    assert _used(world) > 20 and "daily" not in json.dumps(found) and "share" not in json.dumps(found)


def test_the_share_of_a_small_limit_is_never_below_the_floor(operator: Any, net: FakeNetwork,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(net)
    _limit(world, 14)
    monkeypatch.setattr(get_settings(), "indexer_share_min", 10)
    for _ in range(6):
        _search(world.client, world)
    assert _used(world) == 10


def test_a_search_whose_share_is_used_says_until_when(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    _limit(world, 20)
    for _ in range(5):
        found = _search(world.client, world)
    [entry] = found["indexers"]
    assert entry["error"] == "indexer_share_used" and entry["until"], entry
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        assert row is not None and row.last_error == "", "a person's share is no fault of the indexer"


def test_the_operator_is_held_to_the_limit_alone(client: TestClient, operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    _limit(world, 40)
    join(world.library_id, operator.id, "acquire")
    for _ in range(15):
        _search(client, world)
    assert _used(world) == 40


def test_the_share_starts_again_the_next_day(operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    world = make_world(net)
    _limit(world, 20)
    for _ in range(5):
        _search(world.client, world)
    assert _used(world) == 10
    moving.pass_by(days=1)
    _search(world.client, world)
    assert _used(world) == 3


def test_the_share_is_kept_per_indexer_and_person() -> None:
    row = Indexer(id=7, daily_limit=60)
    assert rations.share_left(row, None) is None
    assert rations.share_left(Indexer(id=8, daily_limit=0), 1) is None
    assert rations.share_left(row, 1) == 30
    rations.spend(7, 1, 25)
    assert rations.share_left(row, 1) == 5
    # A second person: 60 / 3 each; the first is past it.
    assert rations.share_left(row, 2) == 20
    rations.spend(7, 2, 1)
    assert rations.share_left(row, 1) == 0
    assert rations.share_left(Indexer(id=9, daily_limit=60), 1) == 30


# --- (c) wishes and searches of a day --------------------------------------------------------------------------------


def test_a_person_has_a_number_of_wishes_a_day(operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    monkeypatch.setattr(get_settings(), "wishes_per_day", 2)
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    body = {"items": [{"work_id": world.work_id}], "kinds": ["audio"], "language": "de"}
    assert world.client.post("/api/v1/procure/wishes", json=body).status_code == 201
    assert world.client.post("/api/v1/procure/wishes", json={**body, "language": "fr"}).status_code == 201
    answer = world.client.post("/api/v1/procure/wishes", json={**body, "language": "it"})
    assert answer.status_code == 429 and answer.json()["detail"]["code"] == "too_many_wishes_today", answer.text
    assert 0 < int(answer.headers["retry-after"]) <= 24 * 3600
    # Taking a wish away does not give it back, nor does a restart forget the wishes in the database.
    first = wish_rows()[0]
    assert world.client.delete(f"/api/v1/procure/wishes/{first.id}").status_code == 204
    assert world.client.post("/api/v1/procure/wishes", json={**body, "language": "it"}).status_code == 429
    rations.forget()
    assert world.client.post("/api/v1/procure/wishes", json={**body, "language": "it"}).status_code == 201
    assert world.client.post("/api/v1/procure/wishes", json={**body, "language": "es"}).status_code == 429
    moving.pass_by(days=1)
    assert world.client.post("/api/v1/procure/wishes", json={**body, "language": "es"}).status_code == 201


def test_the_operator_has_no_number_of_wishes(client: TestClient, operator: Any, net: FakeNetwork,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "wishes_per_day", 1)
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    join(world.library_id, operator.id, "acquire")
    for language in ("de", "fr", "it"):
        answer = client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                             "kinds": ["audio"], "language": language})
        assert answer.status_code == 201, answer.text


def test_a_person_has_a_number_of_searches_by_hand_a_day(operator: Any, net: FakeNetwork,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "searches_per_day", 3)
    world = make_world(net)
    for _ in range(2):
        _search(world.client, world)
    made = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                             "kinds": ["audio"], "language": "de"})
    assert made.status_code == 201
    run_jobs()
    # The search of the wish is the automatic work's: it does not count.
    wish_id = made.json()["results"][0]["wish"]["id"]
    answer = world.client.post(f"/api/v1/procure/wishes/{wish_id}/search")
    assert answer.status_code == 200, answer.text
    run_jobs()
    answer = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "new": True})
    assert answer.status_code == 429 and answer.json()["detail"]["code"] == "too_many_searches_today", answer.text
    assert "retry-after" in answer.headers
    before = wish_rows()[0]
    answer = world.client.post(f"/api/v1/procure/wishes/{wish_id}/search")
    assert answer.status_code == 429 and answer.json()["detail"]["code"] == "too_many_searches_today"
    after = wish_rows()[0]
    assert (after.state, after.search_id) == (before.state, before.search_id), "the refused search left a trace"
    # A search shown again (the same book within minutes, no "new") is no new search.
    assert world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id}).status_code == 202


# --- (a) person by person ------------------------------------------------------------------------------------------


def _due(uid: int, world: World, language: str, minutes_ago: int) -> int:
    moment = clock.now()
    with SessionLocal() as db:
        row = ProcureWish(user_id=uid, work_id=world.work_id, kind="audio", language=language,
                          library_id=world.library_id, state="not_found", detail={"total": 0, "reasons": []},
                          next_try_at=moment - timedelta(minutes=minutes_ago), created_at=moment, updated_at=moment)
        db.add(row)
        db.commit()
        return row.id


def test_the_planner_serves_the_people_in_turn(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    other = make_account("uwe")
    join(world.library_id, other.id, "acquire")
    many = [_due(world.person.id, world, language, 100 - number)
            for number, language in enumerate(("de", "fr", "it", "es", "nl", "sv"))]
    late = _due(other.id, world, "pl", 1)
    planner_tick()
    planner_tick()
    with SessionLocal() as db:
        searched = {row.id for row in db.scalars(select(ProcureWish).where(ProcureWish.search_id.is_not(None)))}
    assert many[0] in searched and late in searched, "a person with many wishes held another's back"
    assert many[1] not in searched


def test_wishes_come_in_turn_person_by_person(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    other = make_account("uwe")
    with SessionLocal() as db:
        moment = clock.now()
        rows = [ProcureWish(id=number, user_id=uid, work_id=None, kind="ebook", language="de",
                            library_id=world.library_id, state="searching", created_at=moment, updated_at=moment,
                            searched_at=moment if uid == world.person.id else None)
                for number, uid in ((1, world.person.id), (2, world.person.id), (3, world.person.id), (4, other.id),
                                    (5, other.id))]
        # Not added: ``in_turn`` only orders them; the time of the last search is read from the database.
        assert [row.id for row in wishes.in_turn(db, rows)] == [1, 4, 2, 5, 3]


def test_kick_starts_the_waiting_wishes_in_turn(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    other = make_account("uwe")
    join(world.library_id, other.id, "acquire")
    moment = clock.now()
    with SessionLocal() as db:
        for uid, language in ((world.person.id, "de"), (world.person.id, "fr"), (world.person.id, "it"),
                              (world.person.id, "es"), (other.id, "nl")):
            db.add(ProcureWish(user_id=uid, work_id=world.work_id, kind="audio", language=language,
                               library_id=world.library_id, state="searching", created_at=moment, updated_at=moment))
        db.commit()
    with SessionLocal() as db:
        wishes.kick(db)
        order = [row.user_id for row in db.scalars(select(ReleaseSearch).order_by(ReleaseSearch.id))]
    # Three places for the first person, one for the second, and the second is not put last.
    assert order.count(world.person.id) == 3 and order.count(other.id) == 1
    assert order.index(other.id) == 1, order


# --- A read starts nothing; a request that makes no wish says so -------------------------------------------------


def test_reading_the_wishes_starts_no_search(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    moment = clock.now()
    with SessionLocal() as db:
        db.add(ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="audio", language="de",
                           library_id=world.library_id, state="searching", created_at=moment, updated_at=moment))
        db.commit()
    assert world.client.get("/api/v1/procure/wishes").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(ReleaseSearch.id)) is None, "a read started a search"
    planner_tick()
    with SessionLocal() as db:
        assert db.scalar(select(ReleaseSearch.id)) is not None


def test_a_request_that_makes_no_wish_says_so(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    body = {"items": [{"work_id": world.work_id}], "kinds": ["audio"], "language": "de"}
    assert world.client.post("/api/v1/procure/wishes", json=body).status_code == 201
    again = world.client.post("/api/v1/procure/wishes", json=body)
    assert again.status_code == 200 and again.json()["results"][0]["status"] == "exists", again.text
    unknown = world.client.post("/api/v1/procure/wishes", json={**body, "items": [{"book": "x" * 16}]})
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "not_found", unknown.text
    mixed = world.client.post("/api/v1/procure/wishes", json={**body, "items": [{"work_id": world.work_id},
                                                                                {"book": "x" * 16}]})
    assert mixed.status_code == 200, mixed.text
    assert [item["status"] for item in mixed.json()["results"]] == ["exists", "error"]
