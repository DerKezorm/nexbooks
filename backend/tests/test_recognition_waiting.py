"""A book is called "open" only when every source it was to be asked at has answered: a source that failed, rests, is at
its daily limit or was switched off meanwhile holds the book back (it stays pending), while the others found nothing.
Against faked services, with invented books."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, MatchEntry
from app.services.match import run as matching
from app.services.sources import config, http, throttle

from .fake_sources import audible_product, dnb_sru, ol_doc
from .library_helpers import book, books_folder, make_library, run_jobs, scan
from .match_helpers import World, edition_of, entries, member

BOOK = {"title": "Die Nebelwacht", "authors": ("Ada Feldkamp",), "language": "de"}


def fresh(name: str = "shelf", **files: dict[str, Any]) -> int:
    folder = books_folder(name)
    for relative, arguments in files.items():
        book(folder, relative, **arguments)
    library = make_library(folder, name.title())
    member(library)
    scan(library)
    return library


def states() -> dict[str, tuple[str, str]]:
    return {title: (state, reason) for title, state, reason, _decision in entries()}


def enqueue() -> int:
    with SessionLocal() as db:
        return matching.enqueue_pending(db)


def knows(titles: dict[str, str]) -> Any:
    """The National Library as one that knows these books (title -> author as ``Last, First``) and nothing else."""

    def answer(params: dict[str, list[str]]) -> bytes:
        asked = re.search(r'tit="([^"]*)"', params.get("query", [""])[0])
        title = asked.group(1) if asked else ""
        return dnb_sru([(str(2_000_000_000 + index), name, titles[name])
                        for index, name in enumerate(titles, start=1) if name == title])

    return answer


# ---- A source that fails --------------------------------------------------------------------------------------------


def test_a_book_is_not_open_while_the_national_library_failed_and_audible_found_nothing(
        client: TestClient, operator: Account, world: World) -> None:
    world.fake.status["services.dnb.de"] = 503
    world.fake.dnb_for = knows({"Die Nebelwacht": "Feldkamp, Ada"})
    fresh(**{"a.epub": BOOK})
    # Audible answered with nothing, Open Library too: but the library, the best source for a German book, did not speak.
    assert world.fake.count("api.audible") > 0 and states() == {"Die Nebelwacht": ("pending", "")}
    # The library is back and knows the book: now it is looked at, and what it says counts.
    del world.fake.status["services.dnb.de"]
    world.advance(3700)
    assert enqueue() == 1
    run_jobs()
    assert states()["Die Nebelwacht"][0] in ("sure", "maybe"), "the library's word was heard"
    assert states()["Die Nebelwacht"][1] != "nothing_found"


def test_the_library_answering_with_nothing_makes_the_book_open(client: TestClient, operator: Account,
                                                                 world: World) -> None:
    world.fake.dnb_for = knows({})
    fresh(**{"a.epub": BOOK})
    assert states() == {"Die Nebelwacht": ("open", "nothing_found")}


def test_a_book_that_is_sure_is_not_held_back_by_a_source_that_failed(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.fake.status["services.dnb.de"] = 503
    world.fake.audible = {"de": [audible_product("B0EXAMPL10", "Die Nebelwacht", ["Ada Feldkamp"], 612, language="german")]}
    # A second source that tells the same book (an e-book is not sure on one catalogue's word).
    world.fake.ol = [ol_doc("OL3W", "Die Nebelwacht", ["Ada Feldkamp"], languages=("ger",), year=2021)]
    fresh(**{"a.epub": dict(BOOK, date="2021-05-01")})
    assert states()["Die Nebelwacht"][0] == "sure"


def test_a_source_switched_off_between_choosing_and_asking_holds_the_book_back(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def off(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise http.SourceOff("off")

    monkeypatch.setattr(matching.dnb, "search", off)
    fresh(**{"a.epub": BOOK})
    assert states() == {"Die Nebelwacht": ("pending", "")}


def test_a_source_the_operator_switched_off_for_good_is_not_waited_for(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    world.settings(source_dnb=False)
    fresh(**{"a.epub": BOOK})
    assert states() == {"Die Nebelwacht": ("open", "nothing_found")}


def test_books_that_wait_for_a_resting_source_are_not_asked_again_until_it_is_back(
        client: TestClient, operator: Account, world: World) -> None:
    world.fake.status["services.dnb.de"] = 503
    fresh(**{"a.epub": BOOK, "b.epub": dict(BOOK, title="Der Nebelturm")})
    assert set(states().values()) == {("pending", "")}
    asked = len(world.fake.log)
    assert enqueue() == 0, "nothing to do while the source rests"
    run_jobs()
    assert len(world.fake.log) == asked
    # The rest is over: they are looked at again.
    world.advance(3700)
    assert enqueue() == 1


def test_waiting_for_a_source_is_no_progress_the_job_could_loop_on(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    """Three books wait for the library; the run looks at each once and ends, it does not go round fifty times."""
    world.fake.status["services.dnb.de"] = 503
    fresh(**{f"{n}.epub": dict(BOOK, title=f"Der Nebel {n}") for n in "abc"})
    audible_asks = world.fake.count("api.audible")
    run_jobs()
    assert world.fake.count("api.audible") == audible_asks


def test_books_that_wait_for_a_source_that_does_not_rest_are_looked_at_once_per_run_not_in_circles(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source that answers "busy" does not rest (it was not at fault): the books wait for it, but a run that found nothing
    new ends instead of looking at them again and again."""
    asked: list[str] = []

    def busy(_db: Any, query: Any, **_kwargs: Any) -> list[Any]:
        asked.append(query.title)
        raise http.SourceError("busy")

    monkeypatch.setattr(matching.dnb, "search", busy)
    fresh(**{f"{n}.epub": dict(BOOK, title=f"Der Nebel {n}") for n in "abc"})
    assert sorted(asked) == ["Der Nebel a", "Der Nebel b", "Der Nebel c"], asked
    assert set(states().values()) == {("pending", "")}


# ---- The daily limit --------------------------------------------------------------------------------------------------


def test_a_source_at_its_daily_limit_rests_until_the_day_is_over(client: TestClient, operator: Account,
                                                                  world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(config.LIMITS, "dnb", config.Limits(rate=1000.0, burst=1000, per_day=2))
    titles = {f"Der Nebel {n}": "Feldkamp, Ada" for n in "abcd"}
    world.fake.dnb_for = knows(titles)
    fresh(**{f"{n}.epub": dict(BOOK, title=f"Der Nebel {n}") for n in "abcd"})
    state = throttle.state_of("dnb")
    assert state.last_error == "daily_limit" and state.blocked_until is not None
    assert (state.blocked_until.hour, state.blocked_until.minute) == (0, 0), "until midnight"
    with SessionLocal() as db:
        assert config.availability(db, "dnb").state == "resting"
    assert "open" not in {state for state, _reason in states().values()}


def test_with_a_small_daily_limit_no_book_is_ever_called_open_wrongly_and_all_are_settled_in_the_end(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scaled-down version of a big library: the library may be asked twice a day, Audible knows none of the
    twelve books, the library knows them all. Day by day nothing is falsely open, and after enough days nothing waits."""
    monkeypatch.setitem(config.LIMITS, "dnb", config.Limits(rate=1000.0, burst=1000, per_day=2))
    monkeypatch.setitem(config.LIMITS, "audible", config.Limits(rate=1000.0, burst=1000, per_day=400))
    titles = {f"Der Nebel {n}": "Feldkamp, Ada" for n in "abcdefghijkl"}
    world.fake.dnb_for = knows(titles)
    fresh(**{f"{n}.epub": dict(BOOK, title=f"Der Nebel {n}") for n in "abcdefghijkl"})
    for _day in range(10):
        assert [t for t, (state, reason) in states().items() if state == "open"] == [], states()
        if not [1 for state, _reason in states().values() if state == "pending"]:
            break
        world.days(1)
        enqueue()
        run_jobs()
    result = states()
    assert len(result) == 12 and {state for state, _reason in result.values()} <= {"sure", "maybe"}, result


def test_the_daily_limit_of_the_library_does_not_stop_a_book_of_another_language(
        client: TestClient, operator: Account, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(config.LIMITS, "dnb", config.Limits(rate=1000.0, burst=1000, per_day=0))
    fresh(**{"a.epub": dict(BOOK, title="The Quiet Orchard", language="en", authors=("Mara Lindqvist",))})
    # An English book is never asked at the national library: nothing waits for it.
    assert states()["The Quiet Orchard"] == ("open", "nothing_found")
    with SessionLocal() as db:
        assert db.scalar(select(MatchEntry.id).where(MatchEntry.state == "pending")) is None
    assert edition_of("The Quiet Orchard").title
