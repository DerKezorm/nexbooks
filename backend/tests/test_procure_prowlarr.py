"""The Prowlarr of the instance (``/api/v1/procure/prowlarr``): connecting with a test first, the sync as a job that makes
the indexers with book categories indexers of nexbooks, and what must never happen to the indexers when Prowlarr cannot be
read."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, Indexer, Job, ProwlarrConnection
from app.security import decrypt_secret
from app.services.procure import indexers

from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork, FakeProwlarr, json_response

KEY = secrets.token_hex(16)
HOST = "prowlarr.example.com"
BASE = f"http://{HOST}:9696"


@pytest.fixture
def prowlarr(net: FakeNetwork) -> FakeProwlarr:
    return net.add(FakeProwlarr(HOST, KEY))  # type: ignore[no-any-return]


def connect(client: TestClient, **over: Any) -> dict[str, Any]:
    answer = client.put("/api/v1/procure/prowlarr", json={"url": BASE, "api_key": KEY, **over})
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def names() -> dict[str, Indexer]:
    with SessionLocal() as db:
        rows = list(db.query(Indexer).order_by(Indexer.id))
        for found in rows:
            db.expunge(found)
    return {found.name: found for found in rows}


def test_nothing_is_connected_at_first(client: TestClient, operator: Account) -> None:
    body = client.get("/api/v1/procure/prowlarr").json()
    assert body["connected"] is False and body["url"] == "" and body["sync_state"] == "idle" and body["taken"] == 0


def test_only_the_operator_reaches_prowlarr(client: TestClient, operator: Account, prowlarr: FakeProwlarr) -> None:
    from .conftest import TAB, make_account, new_client

    calls = (("GET", "", None), ("PUT", "", {"url": BASE, "api_key": KEY}), ("DELETE", "", None),
             ("POST", "/test", {"url": BASE, "api_key": KEY}), ("POST", "/sync", None))
    with new_client(make_account("rita")) as member:
        for method, path, body in calls:
            assert member.request(method, "/api/v1/procure/prowlarr" + path, json=body).status_code == 403, path
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        for method, path, body in calls:
            assert nobody.request(method, "/api/v1/procure/prowlarr" + path, json=body).status_code == 401, path
    assert prowlarr.requests == []


# --- Testing and connecting -------------------------------------------------------------------------------------------


def test_the_test_says_the_version_and_how_many_indexers_have_book_categories(client: TestClient, operator: Account,
                                                                                prowlarr: FakeProwlarr) -> None:
    answer = client.post("/api/v1/procure/prowlarr/test", json={"url": BASE, "api_key": KEY})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"version": "2.6.5.5623", "indexers": 5, "with_books": 3}
    assert client.get("/api/v1/procure/prowlarr").json()["connected"] is False, "a test saves nothing"
    # The key goes in the header of Prowlarr's own API and nowhere in the address.
    assert all(request.headers["x-api-key"] == KEY and KEY not in str(request.url) for request in prowlarr.requests)


@pytest.mark.parametrize(("what", "code"), [("key", "prowlarr_key_rejected"), ("html", "prowlarr_not_prowlarr"),
                                            ("old", "prowlarr_too_old"), ("missing", "prowlarr_unreachable")])
def test_a_connection_that_does_not_work_is_not_saved(client: TestClient, operator: Account, prowlarr: FakeProwlarr,
                                                       what: str, code: str) -> None:
    url, key = BASE, KEY
    if what == "key":
        key = secrets.token_hex(16)
    elif what == "html":
        prowlarr.overrides["/api/v1/system/status"] = lambda request: httpx.Response(
            200, text="<html>Prowlarr</html>", headers={"content-type": "text/html"})
    elif what == "old":
        prowlarr.status["version"] = "1.7.0.1"
    else:
        url = "http://nowhere.example.com:9696"
    answer = client.put("/api/v1/procure/prowlarr", json={"url": url, "api_key": key})
    assert answer.status_code in (502, 504) and answer.json()["detail"]["code"] == code, answer.text
    assert client.get("/api/v1/procure/prowlarr").json()["connected"] is False
    assert KEY not in answer.text


def test_something_that_answers_as_json_but_is_another_program_is_not_prowlarr(client: TestClient, operator: Account,
                                                                                prowlarr: FakeProwlarr) -> None:
    prowlarr.status["appName"] = "Radarr"
    answer = client.post("/api/v1/procure/prowlarr/test", json={"url": BASE, "api_key": KEY})
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "prowlarr_not_prowlarr"


def test_an_address_that_cannot_be_used_is_refused_before_anything_is_asked(client: TestClient, operator: Account,
                                                                              prowlarr: FakeProwlarr) -> None:
    for url in ("ftp://prowlarr.example.com", "http://u:p@prowlarr.example.com", "prowlarr", "http://prowlarr.example.com?x=1"):
        answer = client.put("/api/v1/procure/prowlarr", json={"url": url, "api_key": KEY})
        assert answer.status_code == 422 and answer.json()["detail"]["code"] == "address_invalid", url
    assert prowlarr.requests == []


def test_connecting_seals_the_key_and_queues_the_sync(client: TestClient, operator: Account, prowlarr: FakeProwlarr,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    body = connect(client)
    assert body["connected"] is True and body["key_set"] is True and body["version"] == "2.6.5.5623"
    assert body["sync_state"] == "queued" and body["taken"] == 0 and "api_key" not in body
    with SessionLocal() as db:
        stored = db.query(ProwlarrConnection).one()
        assert KEY not in stored.api_key_enc
        assert decrypt_secret(stored.api_key_enc, indexers.SECRET_CONTEXT) == KEY
    db_file = sqlite3.connect(get_settings().database_path)
    try:
        assert KEY not in "\n".join(db_file.iterdump())
    finally:
        db_file.close()
    assert KEY not in caplog.text and KEY not in client.get("/api/v1/procure/prowlarr").text


def test_a_key_left_out_stays_and_a_second_connection_is_a_change(client: TestClient, operator: Account,
                                                                   prowlarr: FakeProwlarr) -> None:
    connect(client)
    again = client.put("/api/v1/procure/prowlarr", json={"url": BASE, "auto_add": False})
    assert again.status_code == 200 and again.json()["auto_add"] is False
    with SessionLocal() as db:
        assert db.query(ProwlarrConnection).count() == 1
        assert decrypt_secret(db.query(ProwlarrConnection).one().api_key_enc, indexers.SECRET_CONTEXT) == KEY
    # Without a key and without a connection there is nothing to ask with.
    with SessionLocal() as db:
        db.query(ProwlarrConnection).delete()
        db.commit()
    assert client.put("/api/v1/procure/prowlarr", json={"url": BASE}).status_code == 502


# --- The sync --------------------------------------------------------------------------------------------------------------


def test_the_sync_takes_the_indexers_with_book_categories(client: TestClient, operator: Account,
                                                          prowlarr: FakeProwlarr) -> None:
    connect(client)
    assert run_jobs() == 1
    body = client.get("/api/v1/procure/prowlarr").json()
    assert body["sync_state"] == "idle" and body["last_error"] == "" and body["last_sync_at"] and body["taken"] == 2
    assert body["counts"] == {"total": 5, "added": 2, "updated": 0, "removed": 0, "skipped_categories": 1,
                              "skipped_disabled": 1, "skipped_unsupported": 1, "skipped_limit": 0}
    found = names()
    assert sorted(found) == ["Example Books Usenet", "Example Torrent Library"]
    usenet, torrent = found["Example Books Usenet"], found["Example Torrent Library"]
    assert (usenet.kind, usenet.url, usenet.priority, usenet.daily_limit) == (
        "newznab", f"{BASE}/1/api", 10, 150)
    assert sorted(usenet.categories) == [3030, 7020] and usenet.category_langs == {"3030": "mixed", "7020": "mixed"}
    assert (torrent.kind, torrent.url, torrent.priority, torrent.daily_limit) == (
        "torznab", f"{BASE}/2/api", 40, 240), "ten requests an hour are 240 a day"
    assert torrent.categories == [7120] and torrent.prowlarr_indexer_id == 2
    assert usenet.api_key_enc == "", "they use the key of the connection, no copy of it"
    shown = {item["name"]: item for item in client.get("/api/v1/procure/indexers").json()["indexers"]}
    assert shown["Example Books Usenet"]["via_prowlarr"] is True and shown["Example Books Usenet"]["key_set"] is True


def test_the_sync_runs_by_itself_as_a_job_once_per_request(client: TestClient, operator: Account,
                                                           prowlarr: FakeProwlarr) -> None:
    connect(client)
    connect(client)
    client.post("/api/v1/procure/prowlarr/sync")
    with SessionLocal() as db:
        assert db.query(Job).filter_by(kind="prowlarr_sync", state="queued").count() == 1, "the same sync, queued once"
    assert run_jobs() == 1
    assert client.post("/api/v1/procure/prowlarr/sync").status_code == 202
    assert client.get("/api/v1/procure/prowlarr").json()["sync_state"] == "queued"
    assert run_jobs() == 1


def test_an_indexer_that_is_new_in_prowlarr_is_taken_only_when_the_operator_lets_it_in(client: TestClient,
                                                                                       operator: Account,
                                                                                       prowlarr: FakeProwlarr) -> None:
    connect(client, auto_add=False)
    run_jobs()
    assert names() == {}, "nothing new comes in while new ones are not wanted"
    connect(client, auto_add=True)
    run_jobs()
    assert len(names()) == 2


def test_an_indexer_that_is_there_is_kept_in_step_and_otherwise_left_alone(client: TestClient, operator: Account,
                                                                            prowlarr: FakeProwlarr) -> None:
    connect(client)
    run_jobs()
    number = names()["Example Books Usenet"].id
    tuned = client.patch(f"/api/v1/procure/indexers/{number}", json={"priority": 3, "daily_limit": 9, "enabled": False,
                                                                       "categories": [7020]})
    assert tuned.status_code == 200
    prowlarr.indexers[0]["name"] = "Renamed In Prowlarr"
    prowlarr.indexers[0]["priority"] = 44
    client.post("/api/v1/procure/prowlarr/sync")
    run_jobs()
    after = names()["Renamed In Prowlarr"]
    assert after.id == number
    assert (after.priority, after.daily_limit, after.enabled, after.categories) == (3, 9, False, [7020]), \
        "what the operator tuned stays"
    assert client.get("/api/v1/procure/prowlarr").json()["counts"]["updated"] == 1


def test_the_sync_stops_at_the_most_indexers_nexbooks_holds_and_says_so(client: TestClient, operator: Account,
                                                                        prowlarr: FakeProwlarr) -> None:
    """The limit of 200 is for all indexers, those of Prowlarr included (it was for the ones made by hand only)."""
    with SessionLocal() as db:
        for number in range(indexers.MAX_INDEXERS - 1):
            db.add(Indexer(name=f"Own {number}", kind="newznab", url=f"https://own{number}.example.com/api"))
        db.commit()
    body = connect(client)
    run_jobs()
    with SessionLocal() as db:
        assert db.query(Indexer).count() == indexers.MAX_INDEXERS, "one of the two came in, the other did not"
        assert db.query(Indexer).filter(Indexer.prowlarr_indexer_id.is_not(None)).count() == 1
    state = client.get("/api/v1/procure/prowlarr").json()
    assert body["connected"] and state["counts"]["skipped_limit"] == 1
    assert state["last_error"] == "prowlarr_too_many"
    # Room again: the next sync takes the rest and the message goes.
    with SessionLocal() as db:
        db.delete(db.query(Indexer).filter(Indexer.prowlarr_indexer_id.is_(None)).first())
        db.commit()
    assert client.post("/api/v1/procure/prowlarr/sync").status_code == 202
    run_jobs()
    state = client.get("/api/v1/procure/prowlarr").json()
    assert state["counts"]["skipped_limit"] == 0 and state["last_error"] == "" and state["taken"] == 2


def test_a_new_key_of_prowlarr_ends_the_refused_key_of_its_indexers(client: TestClient, operator: Account,
                                                                    prowlarr: FakeProwlarr) -> None:
    from datetime import timedelta

    from app import clock

    connect(client)
    run_jobs()
    with SessionLocal() as db:
        rows = list(db.query(Indexer))
        assert rows
        for found in rows:
            found.last_error = "indexer_key_rejected"
        rows[-1].paused_until, rows[-1].pause_cause = clock.now() + timedelta(hours=1), "request_limit"
        db.commit()
    before = {item["state"] for item in client.get("/api/v1/procure/indexers").json()["indexers"]}
    assert before == {"key_rejected", "resting"}, before
    fresh = secrets.token_hex(16)
    prowlarr.api_key = fresh
    connect(client, api_key=fresh)
    run_jobs()
    states = {item["state"] for item in client.get("/api/v1/procure/indexers").json()["indexers"]}
    assert states and "key_rejected" not in states and "resting" not in states, states
    with SessionLocal() as db:
        assert all(found.last_error == "" and found.paused_until is None for found in db.query(Indexer))


def test_prowlarr_gets_its_saved_key_only_at_its_own_place(client: TestClient, operator: Account, net: FakeNetwork,
                                                           prowlarr: FakeProwlarr) -> None:
    connect(client)
    run_jobs()
    elsewhere = net.add(FakeProwlarr("else.example.com", KEY))
    for target in ("http://else.example.com:9696", f"http://{HOST}:9999"):
        for method, path in (("post", "/api/v1/procure/prowlarr/test"), ("put", "/api/v1/procure/prowlarr")):
            answer = getattr(client, method)(path, json={"url": target})
            assert answer.status_code == 422 and answer.json()["detail"]["code"] == "key_required", (path, answer.text)
    assert not elsewhere.requests
    with SessionLocal() as db:
        assert db.query(ProwlarrConnection).one().url == BASE
    # The same place keeps the key; a key typed again goes where it is typed.
    assert client.post("/api/v1/procure/prowlarr/test", json={"url": BASE + "/"}).status_code == 200
    assert client.post("/api/v1/procure/prowlarr/test",
                       json={"url": "http://else.example.com:9696", "api_key": KEY}).status_code == 200
    assert elsewhere.requests


def test_a_changed_address_of_prowlarr_reaches_its_indexers(client: TestClient, operator: Account, net: FakeNetwork,
                                                            prowlarr: FakeProwlarr) -> None:
    connect(client)
    run_jobs()
    moved = net.add(FakeProwlarr("moved.example.com", KEY, url_base="/prowlarr"))
    connect(client, url="http://moved.example.com/prowlarr/")
    run_jobs()
    assert {found.url for found in names().values()} == {"http://moved.example.com/prowlarr/1/api",
                                                         "http://moved.example.com/prowlarr/2/api"}
    assert moved.requests, "the new address was asked"


def test_what_prowlarr_no_longer_lists_goes_and_what_is_the_operators_stays(client: TestClient, operator: Account,
                                                                              prowlarr: FakeProwlarr) -> None:
    with SessionLocal() as db:
        db.add(Indexer(name="By Hand", kind="newznab", url="https://hand.example.com/api", categories=[7020]))
        db.commit()
    connect(client)
    run_jobs()
    assert sorted(names()) == ["By Hand", "Example Books Usenet", "Example Torrent Library"]
    # The second indexer of Prowlarr loses its book category; the first is gone.
    prowlarr.indexers = [item for item in prowlarr.indexers if item["id"] != 1]
    prowlarr.indexers[0]["capabilities"] = {"categories": [{"id": 2000, "name": "Movies", "subCategories": []}]}
    client.post("/api/v1/procure/prowlarr/sync")
    run_jobs()
    assert sorted(names()) == ["By Hand"]
    assert client.get("/api/v1/procure/prowlarr").json()["counts"]["removed"] == 2


def test_an_indexer_with_the_name_of_another_gets_its_number_after_it(client: TestClient, operator: Account,
                                                                       prowlarr: FakeProwlarr) -> None:
    with SessionLocal() as db:
        db.add(Indexer(name="Example Books Usenet", kind="newznab", url="https://hand.example.com/api"))
        db.commit()
    connect(client)
    run_jobs()
    assert sorted(names()) == ["Example Books Usenet", "Example Books Usenet (2)", "Example Torrent Library"]


@pytest.mark.parametrize("failure", ["key", "html", "down", "garbage"])
def test_when_prowlarr_cannot_be_read_no_indexer_is_touched(client: TestClient, operator: Account,
                                                              prowlarr: FakeProwlarr, failure: str) -> None:
    """The worst thing a sync can do is to remove every indexer because Prowlarr was away for a minute."""
    connect(client)
    run_jobs()
    before = {name: found.id for name, found in names().items()}
    assert len(before) == 2
    if failure == "key":
        prowlarr.api_key = secrets.token_hex(16)
        code = "prowlarr_key_rejected"
    elif failure == "html":
        prowlarr.overrides["/api/v1/indexer"] = lambda request: httpx.Response(
            200, text="<html>login</html>", headers={"content-type": "text/html"})
        code = "prowlarr_not_prowlarr"
    elif failure == "down":
        prowlarr.overrides["/api/v1/system/status"] = lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("down", request=request))
        code = "prowlarr_unreachable"
    else:
        prowlarr.overrides["/api/v1/indexer"] = lambda request: json_response({"indexers": "none"})
        code = "prowlarr_not_prowlarr"
    client.post("/api/v1/procure/prowlarr/sync")
    run_jobs()
    assert {name: found.id for name, found in names().items()} == before
    body = client.get("/api/v1/procure/prowlarr").json()
    assert body["last_error"] == code and body["connected"] is True
    # It cures itself: the next sync that works clears the error.
    prowlarr.api_key = KEY
    prowlarr.overrides.clear()
    client.post("/api/v1/procure/prowlarr/sync")
    run_jobs()
    assert client.get("/api/v1/procure/prowlarr").json()["last_error"] == ""


def test_disconnecting_takes_the_indexers_of_prowlarr_with_it_and_leaves_the_others(client: TestClient,
                                                                                    operator: Account,
                                                                                    prowlarr: FakeProwlarr) -> None:
    with SessionLocal() as db:
        db.add(Indexer(name="By Hand", kind="newznab", url="https://hand.example.com/api"))
        db.commit()
    connect(client)
    run_jobs()
    client.post("/api/v1/procure/prowlarr/sync")
    assert client.delete("/api/v1/procure/prowlarr").status_code == 204
    assert sorted(names()) == ["By Hand"]
    assert client.get("/api/v1/procure/prowlarr").json()["connected"] is False
    with SessionLocal() as db:
        assert db.query(Job).filter_by(kind="prowlarr_sync", state="queued").count() == 0, "no sync of nothing"
    assert client.delete("/api/v1/procure/prowlarr").status_code == 404
    assert client.post("/api/v1/procure/prowlarr/sync").status_code == 404


def test_a_prowlarr_with_a_url_base_is_asked_with_it(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    based = net.add(FakeProwlarr("based.example.com", KEY, url_base="/prowlarr"))
    answer = client.post("/api/v1/procure/prowlarr/test", json={"url": "http://based.example.com/prowlarr", "api_key": KEY})
    assert answer.status_code == 200, answer.text
    assert {request.url.path for request in based.requests} == {"/prowlarr/api/v1/system/status",
                                                                "/prowlarr/api/v1/indexer"}
    # Without the base the API is not there at all, and the page of the web interface answers 200: not Prowlarr.
    missing = client.post("/api/v1/procure/prowlarr/test", json={"url": "http://based.example.com", "api_key": KEY})
    assert missing.status_code == 502 and missing.json()["detail"]["code"] == "prowlarr_not_prowlarr"
