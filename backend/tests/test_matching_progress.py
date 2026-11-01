"""The matching with the sources, made visible: how many of the books of a library are looked at, whether the matching
runs, which source rests until when, and how many books wait for which source. Only for a member with ``manage``, and only
from the libraries of that member. Invented books and sources throughout."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, Job, MatchEntry
from app.services import jobs
from app.services.match import run as matching
from app.services.sources import throttle

from .conftest import make_account, new_client
from .fake_sources import ol_doc
from .library_helpers import book, books_folder, make_library, scan
from .match_helpers import World, member


def two_books(name: str = "progress") -> int:
    folder = books_folder(name)
    for number in (1, 2):
        book(folder, f"Mara Lindqvist/Buch {number} (2020)/buch{number}.epub", title=f"Buch Nummer {number}",
             authors=("Mara Lindqvist",), language="de")
    return make_library(folder, name.title())


def mine(client: TestClient) -> dict[str, Any]:
    (library,) = client.get("/api/v1/libraries").json()
    return library  # type: ignore[no-any-return]


def test_a_manager_sees_how_many_books_wait_for_the_sources_and_how_many_are_looked_at(
        client: TestClient, operator: Account, world: World) -> None:
    world.only()
    library = two_books()
    member(library)
    scan(library)
    shown = mine(client)
    assert (shown["counts"]["entries"], shown["counts"]["pending"], shown["matching"]) == (2, 2, "off")
    # With a source the matching looks at both.
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Buch Nummer 1", ["Mara Lindqvist"], languages=("ger",), year=2020)]
    with SessionLocal() as db:
        matching.enqueue_pending(db)
        assert db.scalar(select(Job.id).where(Job.kind == "match")) is not None
    assert mine(client)["matching"] == "queued"
    from .library_helpers import run_jobs

    run_jobs()
    after = mine(client)
    assert (after["counts"]["entries"], after["counts"]["pending"], after["matching"]) == (2, 0, "idle")


def test_the_hint_counts_what_needs_a_look_and_never_what_waits() -> None:
    from app.services.match import review

    library = two_books("hint")
    with SessionLocal() as db:
        scan(library)
        assert review.to_look_at(db, [library]) == {}, "pending books need no look"
        for entry in db.scalars(select(MatchEntry)):
            entry.state = "maybe"
        db.commit()
        assert review.to_look_at(db, [library]) == {library: 2}
        first = db.scalars(select(MatchEntry).order_by(MatchEntry.id)).first()
        first.state = "pending"
        db.commit()
        assert review.to_look_at(db, [library]) == {library: 1}
        assert review.progress_of(db, [library]) == [(library, 2, 1)]


def test_a_reader_sees_no_numbers_of_the_matching_and_no_resting_source(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.only("openlibrary")
    library = two_books("readers")
    reader = make_account("reader")
    member(library)
    member(library, "reader", right="read")
    throttle.fail("openlibrary", "server", wait=600)
    scan(library)
    with SessionLocal() as db:
        jobs.enqueue(db, "match", ref=library)
        db.commit()
    assert mine(client)["counts"]["pending"] == 2 and mine(client)["resting"] and mine(client)["matching"] == "queued"
    with new_client(reader) as theirs:
        (seen,) = theirs.get("/api/v1/libraries").json()
    assert seen["my_right"] == "read"
    assert (seen["counts"]["entries"], seen["counts"]["pending"], seen["matching"], seen["resting"]) == (0, 0, "idle", [])


def test_a_resting_source_is_named_with_the_time_it_is_left_alone_until(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = []
    library = two_books("resting")
    member(library)
    # The source rests (an error said so), the books wait for it, and the matching names it.
    throttle.fail("openlibrary", "rate_limited", wait=600)
    scan(library)
    shown = mine(client)
    assert shown["counts"]["pending"] == 2
    (rest,) = shown["resting"]
    assert rest["source"] == "openlibrary" and rest["until"]
    until = clock.now() + timedelta(seconds=600)
    assert abs((_moment(rest["until"]) - until).total_seconds()) < 5


def test_a_source_that_is_switched_off_is_not_named_though_it_rested_before(client: TestClient, operator: Account,
                                                                              world: World) -> None:
    world.only("openlibrary")
    library = two_books("off")
    member(library)
    throttle.fail("openlibrary", "server", wait=600)
    throttle.fail("dnb", "server", wait=600)
    scan(library)
    assert [rest["source"] for rest in mine(client)["resting"]] == ["openlibrary"]


def test_the_limit_of_the_day_rests_a_source_until_midnight(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    library = two_books("limit")
    member(library)
    throttle.limit_reached("openlibrary")
    scan(library)
    (rest,) = mine(client)["resting"]
    moment = _moment(rest["until"])
    assert (moment.hour, moment.minute) == (0, 0) and moment > clock.now()


def test_nothing_rests_that_is_switched_off_or_that_nothing_waits_for(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "Buch Nummer 1", ["Mara Lindqvist"], languages=("ger",), year=2020)]
    library = two_books("quiet")
    member(library)
    scan(library)
    assert mine(client)["counts"]["pending"] == 0 and mine(client)["resting"] == []
    throttle.fail("openlibrary", "server", wait=600)
    assert mine(client)["resting"] == [], "nothing waits, so the page says nothing"


def test_the_source_cards_say_how_many_books_wait_for_each_source(client: TestClient, operator: Account,
                                                                  world: World) -> None:
    world.only("openlibrary")
    library = two_books("cards")
    member(library)
    scan(library)
    matching.WAITING.clear()
    assert {s["id"]: s["waiting"] for s in client.get("/api/v1/sources").json()["sources"]}["openlibrary"] == 0
    matching.WAITING.update({101: ("openlibrary",), 102: ("openlibrary", "audible"), 103: ("dnb",)})
    try:
        waiting = {s["id"]: s["waiting"] for s in client.get("/api/v1/sources").json()["sources"]}
    finally:
        matching.WAITING.clear()
    assert (waiting["openlibrary"], waiting["audible"], waiting["dnb"], waiting["hardcover"]) == (2, 1, 1, 0)


def _moment(text: str):  # type: ignore[no-untyped-def]
    from datetime import datetime

    value = datetime.fromisoformat(text)
    return value.replace(tzinfo=None) if clock.now().tzinfo is None else value


def test_books_that_wait_while_no_source_is_on_say_the_matching_is_off(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.only()
    library = two_books("sources-off")
    member(library)
    scan(library)
    shown = mine(client)
    assert (shown["counts"]["pending"], shown["matching"], shown["resting"]) == (2, "off", [])
    # A source that is on but rests is waited for: the matching is not off.
    world.only("openlibrary")
    throttle.fail("openlibrary", "server", wait=600)
    shown = mine(client)
    assert shown["matching"] == "idle" and [rest["source"] for rest in shown["resting"]] == ["openlibrary"]
    # A source that is ready: the books are matched and nothing is left pending.
    throttle.forget()
    world.fake.ol = []
    with SessionLocal() as db:
        matching.enqueue_pending(db)
    from .library_helpers import run_jobs

    run_jobs()
    assert mine(client)["matching"] == "idle" and mine(client)["counts"]["pending"] == 0
