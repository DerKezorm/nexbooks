"""What the operator reads of the automatic work: the last time it did something, the next time it comes back,
each indexer's reading, the log of the rounds. Only the operator in a browser; no key, no title, no address."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.db import SessionLocal
from app.models import Account, AutoRun
from app.services.procure import autolog, planner, rss

from .auto_helpers import Moving, configure, runs, tick
from .conftest import TAB, make_account, new_client
from .follow_helpers import AUDIO, audio_world, sabnzbd, wish
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"
URL = "/api/v1/procure/automation/status"


def when(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_only_the_operator_in_a_browser_reads_the_state_of_the_automatic_work(client: TestClient,
                                                                              operator: Account) -> None:
    assert client.get(URL).status_code == 200
    with new_client(make_account("rita")) as member:
        assert member.get(URL).status_code == 403
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        assert nobody.get(URL).status_code == 401


def test_a_fresh_instance_has_nothing_to_report_and_the_switch_is_on(client: TestClient, operator: Account) -> None:
    shown = client.get(URL).json()
    assert shown["enabled"] is True and shown["runs"] == [] and shown["indexers"] == []
    assert shown["last_run_at"] is None and shown["next_run_at"] is None
    assert shown["due"] == 0 and shown["waiting_for_a_day"] == 0 and shown["wishes_open"] == 0


def test_the_last_and_the_next_time_come_from_the_readings_of_the_indexers(
        client: TestClient, operator: Account, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    moving = Moving(monkeypatch)
    configure(rss_minutes=15)
    assert client.get(URL).json()["last_run_at"] is not None
    tick()
    shown = client.get(URL).json()
    [line] = shown["indexers"]
    assert line["id"] == world.indexer_id and line["error"] == "" and line["resting_until"] is None
    assert when(shown["last_run_at"]) == when(line["checked_at"])
    # The next time is the reading of the indexer in 15 minutes (the wish waits longer).
    assert when(shown["next_run_at"]) - when(shown["last_run_at"]) == timedelta(minutes=15)
    moving.pass_by(minutes=20)
    again = client.get(URL).json()
    assert when(again["next_run_at"]) <= clock.now(), "overdue: at once"
    # With the main switch off there is no next time.
    configure(enabled=False)
    assert client.get(URL).json()["next_run_at"] is None and client.get(URL).json()["enabled"] is False


def test_the_log_names_the_requests_per_indexer_and_nothing_that_could_give_away_more(
        client: TestClient, operator: Account, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    configure(rss_minutes=60)
    tick()
    shown = client.get(URL).json()
    kinds = {line["kind"] for line in shown["runs"]}
    assert "search" in kinds, kinds
    search = next(line for line in shown["runs"] if line["kind"] == "search")
    assert search["wishes"] == 1 and search["loaded"] == 1
    assert search["indexers"][0]["id"] == world.indexer_id and search["indexers"][0]["name"] == "Shelf"
    assert search["indexers"][0]["requests"] >= 1
    text = client.get(URL).text
    for secret in (world.indexer.api_key, "Falkner", "Laternenstra", AUDIO, "10.99.0.5", "indexer.example.com"):
        assert secret not in text, secret


def test_the_newest_lines_are_kept_and_the_older_ones_go(operator: Account) -> None:
    with SessionLocal() as db:
        for number in range(autolog.KEEP_RUNS + 25):
            db.add(AutoRun(kind="rss", counts={"indexers": {}, "wishes": number}))
        db.commit()
        assert autolog.prune(db) == 25
        assert len(autolog.recent(db, 200)) == 200
    assert len(runs()) == autolog.KEEP_RUNS


def test_a_round_that_did_nothing_leaves_no_line(operator: Account, net: FakeNetwork) -> None:
    sabnzbd(net)
    audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    configure(enabled=False)
    assert planner.tick() is not None and rss.sync() == 0
    assert runs() == []


def test_the_list_is_at_most_as_long_as_asked(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        for number in range(40):
            db.add(AutoRun(kind="search", counts={"indexers": {}, "wishes": number}))
        db.commit()
    assert len(client.get(URL).json()["runs"]) == 30
    assert len(client.get(URL, params={"limit": 5}).json()["runs"]) == 5
    assert len(client.get(URL, params={"limit": 5000}).json()["runs"]) == 40


def test_the_numbers_of_waiting_wishes_are_shown(client: TestClient, operator: Account, net: FakeNetwork,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import ForeignBook

    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    soon = (clock.now() + timedelta(days=30)).date().isoformat()
    with SessionLocal() as db:
        db.add(ForeignBook(token="tok" + "q" * 13, ref="hardcover:5", title="Die Laternenstraße",
                           authors=["Rune Falkner"], language="de", released=soon))
        db.commit()
    world.client.post("/api/v1/procure/wishes", json={"items": [{"book": "tok" + "q" * 13}], "kinds": ["audio"],
                                                      "language": "de"})
    shown = client.get(URL).json()
    assert shown["waiting_for_a_day"] == 1 and shown["wishes_open"] == 1 and shown["due"] == 0
    moving = Moving(monkeypatch)
    moving.pass_by(days=31)
    with SessionLocal() as db:
        assert planner.status(db)["due"] == 1
    assert world is not None
