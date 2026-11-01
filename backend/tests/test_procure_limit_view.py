"""A search that finds nothing because the indexers are at their limits says so, and shows what the last search found:
the limit of the day in the progress line, the indexers that rest right now, the earlier search with its hits instead of a
new one that cannot ask, and a search a wish still points at is not tidied away."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import Indexer, ProcureWish, ReleaseSearch
from app.services.procure import budget, rations
from app.services.procure import search as search_service

from .conftest import make_account
from .procure_fakes import FakeNetwork
from .procure_search_helpers import FeedIndexer, Item, World, add_indexer, make_world, search

GOOD = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"


def limit_reached(limit: int = 7) -> None:
    """The operator's limit of requests for the day is used up."""
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=limit, used_count=limit, used_day=budget.day()))
        db.commit()


def age(search_id: int, minutes: int) -> datetime:
    """The search finished ``minutes`` ago (the tests do not wait for the clock); the moment, which names the row (the
    ids of deleted rows are given out again)."""
    with SessionLocal() as db:
        moment = clock.now() - timedelta(minutes=minutes, seconds=search_id)
        db.execute(update(ReleaseSearch).where(ReleaseSearch.id == search_id).values(
            finished_at=moment, created_at=moment))
        db.commit()
        return moment


def made_at() -> set[datetime]:
    with SessionLocal() as db:
        return {row.replace(microsecond=0) for row in db.scalars(select(ReleaseSearch.created_at))}


def searches() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(ReleaseSearch)) or 0


def post(world: World, **payload: Any) -> Any:
    return world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de", **payload})


def test_the_progress_line_names_the_limit_of_the_day(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    limit_reached(7)
    found = search(world, language="de")
    line = found["indexers"][0]
    assert line["state"] == "failed" and line["error"] == "indexer_daily_limit"
    assert line["limit"] == 7 and line["until"] is not None
    assert found["releases"] == []


def test_an_indexer_that_is_not_at_a_limit_names_none(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    assert found["indexers"][0]["limit"] is None
    assert found["resting"] == [] and found["can_search_again"] is True and found["searches_used_until"] is None


def test_the_answer_lists_the_indexers_that_rest_right_now(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    limit_reached(7)
    shown = world.client.get(f"/api/v1/procure/searches/{first['id']}").json()
    assert shown["can_search_again"] is False
    assert len(shown["resting"]) == 1
    rest = shown["resting"][0]
    assert rest["id"] == world.indexer_id and rest["name"] == "Shelf" and rest["cause"] == "daily_limit"
    assert rest["limit"] == 7
    assert datetime.fromisoformat(rest["until"]) == budget.midnight()


def test_a_person_whose_share_of_the_limit_is_used_sees_it_named(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=100))
        db.commit()
    rations.spend(world.indexer_id, world.person.id, 100)
    shown = world.client.get(f"/api/v1/procure/searches/{first['id']}").json()
    assert [(item["cause"], item["limit"]) for item in shown["resting"]] == [("share_used", 100)]
    assert shown["can_search_again"] is False
    age(first["id"], 40)
    found = search(world, language="de", new=True)
    line = found["indexers"][0]
    assert line["error"] == "indexer_share_used" and line["limit"] == 100


def test_a_pause_after_a_limit_of_the_indexer_is_a_cause_of_its_own(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    with SessionLocal() as db:
        db.execute(update(Indexer).values(paused_until=clock.now() + timedelta(hours=2), pause_cause="request_limit"))
        db.commit()
    shown = world.client.get(f"/api/v1/procure/searches/{first['id']}").json()
    assert [(item["cause"], item["limit"]) for item in shown["resting"]] == [("request_limit", None)]
    assert shown["can_search_again"] is False


def test_with_every_indexer_resting_the_earlier_search_with_hits_is_shown_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    assert len(first["releases"]) == 1
    age(first["id"], 40)
    limit_reached(7)
    before = searches()
    answer = post(world)
    assert answer.status_code == 202, answer.text
    assert answer.json()["id"] == first["id"] and answer.json()["state"] == "done"
    assert len(answer.json()["releases"]) == 1 and answer.json()["can_search_again"] is False
    assert searches() == before, "a search that cannot ask was made all the same"
    # "Neu suchen" (new) is the caller's own wish: it is not turned into the old search.
    fresh = post(world, new=True)
    assert fresh.status_code == 202 and fresh.json()["id"] != first["id"]


def test_an_indexer_with_no_category_in_the_language_cannot_be_asked_either(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    other = net.add(FeedIndexer("french.example.com", "f" * 32))
    add_indexer(other, name="French only", categories=[7320], langs={"7320": "fr"}, priority=2)
    first = search(world, language="de")
    assert len(first["releases"]) == 1
    age(first["id"], 40)
    limit_reached(7)
    with SessionLocal() as db:
        db.execute(update(Indexer).where(Indexer.name == "French only").values(daily_limit=0, used_count=0))
        db.commit()
    shown = world.client.get(f"/api/v1/procure/searches/{first['id']}").json()
    assert [item["name"] for item in shown["resting"]] == ["Shelf"] and shown["can_search_again"] is False
    answer = post(world)
    assert answer.json()["id"] == first["id"]


def test_tidying_by_number_keeps_the_search_a_wish_points_at(operator: Any, net: FakeNetwork,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_service, "KEEP_PER_PERSON", 1)
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    kept = search(world, language="de")
    with SessionLocal() as db:
        moment = clock.now()
        mark = db.scalar(select(ReleaseSearch.created_at).where(ReleaseSearch.id == kept["id"]))
        db.add(ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="ebook", language="de",
                           state="capped", search_id=kept["id"], created_at=moment, updated_at=moment))
        db.commit()
    for _ in range(3):
        search(world, language="de", new=True)
    with SessionLocal() as db:
        assert mark in set(db.scalars(select(ReleaseSearch.created_at))), "kept by the number, the search was tidied"
        assert db.scalar(select(func.count()).select_from(ReleaseSearch)) <= 3


def test_the_earlier_search_is_not_shown_again_while_an_indexer_can_be_asked(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    age(first["id"], 40)
    answer = post(world)
    assert answer.status_code == 202 and answer.json()["id"] != first["id"]


def test_without_an_earlier_search_with_hits_a_new_one_is_made_and_tells_the_limit(operator: Any,
                                                                                  net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = []
    first = search(world, language="de")
    assert first["releases"] == []
    age(first["id"], 40)
    limit_reached(7)
    found = search(world, language="de")
    assert found["id"] != first["id"]
    assert found["indexers"][0]["error"] == "indexer_daily_limit" and found["indexers"][0]["limit"] == 7


def test_an_earlier_search_of_another_book_or_person_is_not_shown(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    age(first["id"], 40)
    limit_reached(7)
    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).where(ReleaseSearch.id == first["id"]).values(language="fr"))
        db.commit()
    other_language = post(world)
    assert other_language.json()["id"] != first["id"]
    stranger = make_account("oskar")
    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).where(ReleaseSearch.id == first["id"]).values(language="de",
                                                                                         user_id=stranger.id))
        db.commit()
    assert post(world).json()["id"] != first["id"]


def test_a_person_who_used_the_searches_of_the_day_sees_the_earlier_hits(operator: Any, net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "searches_per_day", 1)
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    age(first["id"], 40)
    # The one search of the day is made; a new one is refused, the earlier one is shown.
    refused = post(world, new=True)
    assert refused.status_code == 429 and refused.json()["detail"]["code"] == "too_many_searches_today"
    shown = post(world)
    assert shown.status_code == 202 and shown.json()["id"] == first["id"]
    assert shown.json()["can_search_again"] is False and shown.json()["searches_used_until"] is not None


def test_a_person_who_used_the_searches_of_the_day_and_has_no_earlier_hits_still_gets_the_refusal(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "searches_per_day", 1)
    world = make_world(net)
    world.indexer.items = []
    first = search(world, language="de")
    age(first["id"], 40)
    refused = post(world)
    assert refused.status_code == 429 and refused.json()["detail"]["code"] == "too_many_searches_today"


def test_tidying_keeps_the_search_a_wish_still_points_at(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    kept = search(world, language="de")
    gone = search(world, language="de", new=True)
    marks = {name: age(found["id"], 60 * 30) for name, found in (("kept", kept), ("gone", gone))}
    with SessionLocal() as db:
        moment = clock.now()
        db.add(ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="ebook", language="de",
                           state="capped", search_id=kept["id"], created_at=moment, updated_at=moment))
        db.commit()
    search(world, language="de", new=True)
    present = made_at()
    assert marks["kept"].replace(microsecond=0) in present, "the search a waiting wish points at was tidied away"
    assert marks["gone"].replace(microsecond=0) not in present


def test_a_finished_wish_does_not_hold_a_search_back(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    old = search(world, language="de")
    mark = age(old["id"], 60 * 30)
    with SessionLocal() as db:
        moment = clock.now()
        db.add(ProcureWish(user_id=world.person.id, work_id=world.work_id, kind="ebook", language="de",
                           state="done", search_id=old["id"], created_at=moment, updated_at=moment))
        db.commit()
    search(world, language="de", new=True)
    assert mark.replace(microsecond=0) not in made_at()
