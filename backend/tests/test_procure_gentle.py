"""Being gentle with the indexers and with the wishes: nothing is read for
nobody, an indexer that refused the key is left alone until the key is changed, a request limit and a fault are left
alone longer each time, a wish that is taken away meanwhile is no error, and a wish that was claimed once is still read
against the newest entries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, Indexer, ProcureWish, ReleaseSearch
from app.services import timezones
from app.services.procure import handover, indexers, rss, wishes

from .auto_helpers import Moving, configure, reading_requests, tick
from .follow_helpers import AUDIO, SECOND_AUDIO, audio_world, downloads, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"


def book_token(number: int) -> str:
    """The token of an invented book of the sources."""
    return f"gen{number:013d}"


# --- Reading the newest entries only for a wish that waits ---------------------------------------------------------


def test_nothing_is_read_for_nobody(operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    moving = Moving(monkeypatch)
    configure(rss_minutes=15)
    for _ in range(24 * 4):
        moving.pass_by(minutes=15, seconds=1)
        tick()
    assert reading_requests(world.indexer) == [], "no wish waits: no request is spent on the newest entries"
    wish(world)
    run_jobs()
    for _ in range(4):
        moving.pass_by(minutes=16)
        tick()
    assert len(reading_requests(world.indexer)) >= 1, "a wish waits: the newest entries are read"


def test_a_wish_that_is_on_its_way_is_not_read_against_the_newest_entries(operator: Any, net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(downloads()) == 1
    # A better release comes in; the only wish is on its way already, so nobody waits for it.
    world.indexer.items = [Item(SECOND_AUDIO, size=400_000_000, categories=(3130,))]
    moving = Moving(monkeypatch)
    moving.pass_by(hours=1)
    assert rss.sync() == 0 and reading_requests(world.indexer) == []
    with SessionLocal() as db:
        assert db.query(ReleaseSearch).filter(ReleaseSearch.origin == "rss").count() == 0
        assert rss.has_wishes(db) is False
    assert len(downloads()) == 1 and len(sab.jobs) == 1 and wishes_of(world)[0]["state"] == "loading"


def test_a_wish_that_waits_beside_one_on_its_way_leaves_the_one_on_its_way_alone(operator: Any, net: FakeNetwork,
                                                                                monkeypatch: pytest.MonkeyPatch
                                                                                ) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    with SessionLocal() as db:
        db.add(ForeignBook(token=book_token(1), ref="hardcover:55", title="Ganz Anders", authors=["Ida Weiss"],
                           language="de"))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": book_token(1)}],
                                                              "kinds": ["audio"], "language": "de"})
    assert reply.status_code == 201
    run_jobs()
    world.indexer.items = [Item(SECOND_AUDIO, size=400_000_000, categories=(3130,))]
    moving = Moving(monkeypatch)
    moving.pass_by(hours=1)
    rss.sync()
    assert len(reading_requests(world.indexer)) >= 1
    states = {row["book"]["title"]: row["state"] for row in wishes_of(world)}
    assert "loading" in states.values() and len(downloads()) == 1 and len(sab.jobs) == 1


def test_a_wish_that_was_claimed_has_no_hand_over_in_the_database_sense(operator: Any, net: FakeNetwork) -> None:
    """Taking the wish for a search (``claim``) once wrote the JSON text ``null`` for 'no hand-over', which is not an SQL
    NULL: the newest entries then passed the wish by and the planner visited it every tick."""
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    wish_id = wishes_of(world)[0]["id"]
    assert world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    run_jobs()
    with SessionLocal() as db:
        assert db.scalar(select(ProcureWish.handover.is_(None))) is True
        assert rss.has_wishes(db) is True


def test_a_wish_that_was_claimed_is_read_against_the_newest_entries(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    wish_id = wishes_of(world)[0]["id"]
    world.client.post(f"/api/v1/procure/wishes/{wish_id}/search")
    run_jobs()
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,))]
    assert rss.sync() >= 1
    assert len(downloads()) == 1 and len(sab.jobs) == 1


def test_the_planner_does_not_visit_wishes_that_have_no_hand_over(operator: Any, net: FakeNetwork,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    wish_id = wishes_of(world)[0]["id"]
    world.client.post(f"/api/v1/procure/wishes/{wish_id}/search")
    run_jobs()
    visits: list[int] = []
    original = handover._drop
    monkeypatch.setattr(handover, "_drop", lambda db, row: (visits.append(row.id), original(db, row))[1])
    for _ in range(5):
        tick()
    assert visits == []


# --- An indexer that refused the key ------------------------------------------------------------------------------


def test_an_indexer_that_refused_the_key_is_left_alone_until_the_key_is_changed(
        client: TestClient, operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=15, retry_hours=1)
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    right = world.indexer.api_key
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        row.api_key_enc = indexers.seal("a-key-the-indexer-does-not-know")
        db.commit()
    moving = Moving(monkeypatch)
    start = len(world.indexer.requests)
    for _ in range(24 * 60):
        moving.pass_by(minutes=1)
        tick()
        run_jobs()
    asked = len(world.indexer.requests) - start
    assert asked < 8, f"{asked} requests in a day with a key the indexer refuses"
    shown = client.get("/api/v1/procure/indexers").json()["indexers"][0]
    assert shown["state"] == "key_rejected", shown
    # The operator enters the key again: it is asked again at once.
    changed = client.patch(f"/api/v1/procure/indexers/{world.indexer_id}", json={"api_key": right})
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["state"] == "ready"
    before = len(world.indexer.requests)
    moving.pass_by(minutes=20)
    tick()
    run_jobs()
    assert len(world.indexer.requests) > before


def test_a_test_by_hand_asks_an_indexer_that_refused_the_key_and_clears_it_when_it_works(
        client: TestClient, operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wrong = "a-key-the-indexer-does-not-know"
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        row.api_key_enc = indexers.seal(wrong)
        db.commit()
    first = client.post(f"/api/v1/procure/indexers/{world.indexer_id}/test")
    assert first.status_code == 502 and first.json()["detail"]["code"] == "indexer_key_rejected"
    asked = len(world.indexer.requests)
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["state"] == "key_rejected"
    # A person's search does not ask it either.
    wish(world)
    run_jobs()
    assert len(world.indexer.requests) == asked
    # The indexer's end changed: the same key is right now.
    world.indexer.api_key = wrong
    again = client.post(f"/api/v1/procure/indexers/{world.indexer_id}/test")
    assert again.status_code == 200, again.text
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["state"] == "ready"


# --- A request limit and a fault -----------------------------------------------------------------------------------


def broken(world: Any, handler: Any) -> list[httpx.Request]:
    """Every search or feed request goes to ``handler``; returns the list those requests are written to."""
    original = world.indexer.handle
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("t") in ("search", "book"):
            asked.append(request)
            return handler(request)  # type: ignore[no-any-return]
        return original(request)  # type: ignore[no-any-return]

    world.indexer.handle = handle
    return asked


def test_a_request_limit_is_left_alone_at_least_five_minutes_and_longer_each_time(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=15, retry_hours=1)
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    asked = broken(world, lambda request: httpx.Response(429, headers={"Retry-After": "120"}, text="slow down"))
    moving = Moving(monkeypatch)
    times: list[datetime] = []
    seen = 0
    for _ in range(24 * 60):
        moving.pass_by(minutes=1)
        tick()
        run_jobs()
        if len(asked) > seen:
            times.append(clock.now())
            seen = len(asked)
    assert 4 <= len(asked) < 20, len(asked)
    gaps = [(later - earlier).total_seconds() for earlier, later in pairwise(times)]
    assert min(gaps) >= 290, gaps
    assert gaps == sorted(gaps) and gaps[-1] >= 4800, gaps


def test_a_server_error_makes_the_background_wait_longer_each_time_but_a_person_is_served(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=15, retry_hours=1)
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    asked = broken(world, lambda request: httpx.Response(500, text="boom"))
    moving = Moving(monkeypatch)
    for _ in range(24 * 60):
        moving.pass_by(minutes=1)
        tick()
        run_jobs()
    assert len(asked) < 40, len(asked)
    before = len(asked)
    wish_id = wishes_of(world)[0]["id"]
    assert world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    run_jobs()
    assert len(asked) > before, "a person who searches is served though the planner leaves the indexer alone"


def test_retry_after_is_never_below_five_minutes_and_never_above_a_day() -> None:
    assert indexers.retry_after_seconds("2") == 300
    assert indexers.retry_after_seconds("600") == 600
    assert indexers.retry_after_seconds("999999999") == 24 * 3600
    assert indexers.retry_after_seconds(None) == 3600


# --- A wish taken away meanwhile ----------------------------------------------------------------------------------


def take_away(wish_id: int) -> None:
    with SessionLocal() as other:
        row = other.get(ProcureWish, wish_id)
        assert row is not None
        other.delete(row)
        other.commit()


def test_a_wish_that_is_taken_away_while_it_is_worked_on_is_no_error(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    with SessionLocal() as mine:
        row = mine.scalar(select(ProcureWish))
        assert row is not None
        wish_id = row.id
        take_away(wish_id)
        assert wishes.claim(mine, row) is False
        wishes.begin(mine, row)
        assert wishes.fresh(mine, row) is False
        wishes.kick(mine)
        assert wishes.run_due(mine) == 0


def test_searching_a_wish_that_is_taken_away_meanwhile_is_404_not_500(operator: Any, net: FakeNetwork,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    wish_id = wishes_of(world)[0]["id"]
    original = wishes.get

    def get_then_lose_it(db: Any, account: Any, number: int) -> ProcureWish:
        found = original(db, account, number)
        take_away(number)
        return found

    monkeypatch.setattr(wishes, "get", get_then_lose_it)
    answer = world.client.post(f"/api/v1/procure/wishes/{wish_id}/search")
    assert answer.status_code == 404, answer.text


def test_listing_the_wishes_while_one_is_taken_away_is_no_error(operator: Any, net: FakeNetwork,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    wish_id = wishes_of(world)[0]["id"]
    original = wishes.books_for

    def books_then_lose_it(*args: Any, **kwargs: Any) -> Any:
        found = original(*args, **kwargs)
        take_away(wish_id)
        return found

    monkeypatch.setattr(wishes, "books_for", books_then_lose_it)
    answer = world.client.get("/api/v1/procure/wishes")
    assert answer.status_code == 200, answer.text


# --- The day a book comes out ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("released", "waits"), [
    ("2026-10-16", False),
    ("2026-10-17", True),
    ("2026-10-15", False),
    ("2026", False),
    ("2027", True),
    ("2026-12", False),
    ("", False),
    ("nonsense", False),
])
def test_a_book_waits_only_for_a_day_after_today(monkeypatch: pytest.MonkeyPatch, released: str, waits: bool) -> None:
    monkeypatch.setattr(timezones, "instance_zone", lambda: UTC)
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 16, 8, 0, tzinfo=UTC))
    assert bool(wishes._released_after_today(released)) is waits


def test_a_wish_for_a_book_that_comes_out_today_is_searched_today(operator: Any, net: FakeNetwork,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    monkeypatch.setattr(timezones, "instance_zone", lambda: UTC)
    today = clock.now().date().isoformat()
    with SessionLocal() as db:
        db.add(ForeignBook(token=book_token(2), ref="hardcover:56", title="Heute Neu", authors=["Rune Falkner"],
                           language="de", released=today))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": book_token(2)}],
                                                              "kinds": ["audio"], "language": "de"})
    assert reply.status_code == 201
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] != "waiting" and shown["release_date"] == ""


def test_a_wish_waiting_for_its_day_is_due_on_that_day_and_not_before(operator: Any, net: FakeNetwork,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    monkeypatch.setattr(timezones, "instance_zone", lambda: UTC)
    start = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    moving = Moving(monkeypatch)
    monkeypatch.setattr(clock, "now", lambda: start + moving.offset)
    with SessionLocal() as db:
        db.add(ForeignBook(token=book_token(3), ref="hardcover:57", title="Morgen Neu", authors=["Rune Falkner"],
                           language="de", released="2026-10-17"))
        db.commit()
    world.client.post("/api/v1/procure/wishes", json={"items": [{"book": book_token(3)}], "kinds": ["audio"],
                                                      "language": "de"})
    run_jobs()
    assert wishes_of(world)[0]["state"] == "waiting"
    with SessionLocal() as db:
        assert wishes.due_count(db, clock.now()) == 0
        assert wishes.due_count(db, clock.now() + timedelta(hours=3, minutes=59)) == 0
        assert wishes.due_count(db, clock.now() + timedelta(hours=4)) == 1, "00:00 of the day itself"


# --- The log line of a search the planner settled ------------------------------------------------------------------


def test_a_wish_the_planner_settles_before_the_search_says_it_is_done_is_in_the_log(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    """The job that ran the search and the planner look at the wish at the same moment: whoever settles it, the log of the
    automatic work counts the wish and the release it loaded."""
    from .auto_helpers import runs

    sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    monkeypatch.setattr(wishes, "after_search", lambda db, found: None)
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "searching", "nobody has settled it yet"
    tick()
    assert wishes_of(world)[0]["state"] == "loading"
    [line] = [entry for entry in runs() if entry.kind == "search"]
    assert line.counts.get("wishes") == 1 and line.counts.get("loaded") == 1, line.counts
