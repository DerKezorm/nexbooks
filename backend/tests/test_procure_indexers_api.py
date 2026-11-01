"""The indexers of the operator through the interface (``/api/v1/procure/indexers``): adding with the caps and the
suggested book categories, the key sealed and never shown, the test, the pacing, the pause after a request limit, the
limit of the day, and the rules for an indexer that came from Prowlarr."""

from __future__ import annotations

import logging
import re
import secrets
import sqlite3
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, Indexer
from app.security import decrypt_secret
from app.services.procure import gate, indexers

from .conftest import TAB, FakeClock, make_account, new_client
from .procure_fakes import FakeIndexer, FakeNetwork, error_document, xml_response

KEY = secrets.token_hex(16)
HOST = "indexer.example.com"
URL = f"https://{HOST}/api"


@pytest.fixture
def indexer(net: FakeNetwork) -> FakeIndexer:
    return net.add(FakeIndexer(HOST, KEY))  # type: ignore[no-any-return]


def payload(**over: Any) -> dict[str, Any]:
    return {"name": "Library One", "kind": "newznab", "url": URL, "api_key": KEY, "language": "de", **over}


def add(client: TestClient, **over: Any) -> dict[str, Any]:
    answer = client.post("/api/v1/procure/indexers", json=payload(**over))
    assert answer.status_code == 201, answer.text
    return answer.json()  # type: ignore[no-any-return]


def row(indexer_id: int) -> Indexer:
    with SessionLocal() as db:
        found = db.get(Indexer, indexer_id)
        assert found is not None
        db.expunge(found)
        return found


class Day:
    """The clock of the server, moved by the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.offset = timedelta()
        start = clock.now()
        monkeypatch.setattr(clock, "now", lambda: start + self.offset)

    def pass_by(self, **kwargs: float) -> None:
        self.offset += timedelta(**kwargs)


@pytest.fixture
def day(monkeypatch: pytest.MonkeyPatch) -> Day:
    return Day(monkeypatch)


# --- Who may ---------------------------------------------------------------------------------------------------------


def test_only_the_operator_in_a_browser_reaches_the_indexers(client: TestClient, operator: Account,
                                                              indexer: FakeIndexer) -> None:
    made = add(client)
    calls = (("GET", "/indexers", None), ("POST", "/indexers", payload(name="Two")),
             ("PATCH", f"/indexers/{made['id']}", {"priority": 3}), ("DELETE", f"/indexers/{made['id']}", None),
             ("POST", "/indexers/test", {"kind": "newznab", "url": URL, "api_key": KEY}),
             ("POST", f"/indexers/{made['id']}/test", None))
    before = len(indexer.requests)
    with new_client(make_account("rita")) as member:
        for method, path, body in calls:
            assert member.request(method, "/api/v1/procure" + path, json=body).status_code == 403, path
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        for method, path, body in calls:
            assert nobody.request(method, "/api/v1/procure" + path, json=body).status_code == 401, path
    assert len(indexer.requests) == before, "nobody else makes the server ask an indexer"
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["name"] == "Library One"


# --- Adding ----------------------------------------------------------------------------------------------------------


def test_adding_an_indexer_takes_the_book_categories_out_of_its_caps(client: TestClient, operator: Account,
                                                                      indexer: FakeIndexer) -> None:
    made = add(client)
    assert made["state"] == "ready" and made["kind"] == "newznab" and made["url"] == URL
    on = {item["id"] for item in made["categories"] if item["on"]}
    assert on == {3030, 3130, 7020, 7120}, "German and mixed categories, books and audiobooks, no comics"
    listed = {item["id"]: item for item in made["categories"]}
    assert listed[7120]["lang"] == "de" and listed[7320]["lang"] == "fr" and listed[7020]["lang"] == "mixed"
    assert listed[7030]["on"] is False and listed[7030]["suggested"] is False
    assert listed[3130]["type"] == "audio" and listed[7120]["type"] == "ebook"
    assert made["supported_params"] == {"search": ["q", "group", "limit", "cat"], "book": ["q", "author", "isbn"],
                                        "audio": ["q", "artist", "album"], "book_search": True, "audio_search": True}
    assert indexer.functions() == ["caps"], "adding asks the caps and nothing else"
    assert made["priority"] == 25 and made["daily_limit"] == 0 and made["used_today"] == 0


def test_the_languages_fetched_decide_which_categories_are_suggested(client: TestClient, operator: Account,
                                                                      indexer: FakeIndexer) -> None:
    from app.services.procure import formats

    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "languages": ["en"]})
    english = add(client, name="English", language="de")
    assert {item["id"] for item in english["categories"] if item["on"]} == {3030, 7020}


def test_categories_and_their_languages_can_be_chosen(client: TestClient, operator: Account,
                                                       indexer: FakeIndexer) -> None:
    made = add(client, categories=[7320, 3330], category_langs={"7320": "fr", "3330": "mixed"})
    on = {item["id"]: item["lang"] for item in made["categories"] if item["on"]}
    assert on == {7320: "fr", 3330: "mixed"}


def test_the_key_is_sealed_and_never_shown(client: TestClient, operator: Account, indexer: FakeIndexer,
                                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    made = add(client)
    assert made["key_set"] is True and "api_key" not in made
    stored = row(made["id"])
    assert stored.api_key_enc and KEY not in stored.api_key_enc
    assert decrypt_secret(stored.api_key_enc, indexers.SECRET_CONTEXT) == KEY
    shown = client.get("/api/v1/procure/indexers").text + client.post(
        f"/api/v1/procure/indexers/{made['id']}/test").text + str(made)
    assert KEY not in shown
    # Not in the database file either, whatever table, however it is written.
    client_db = sqlite3.connect(get_settings().database_path)
    try:
        dump = "\n".join(client_db.iterdump())
    finally:
        client_db.close()
    assert KEY not in dump
    assert KEY not in caplog.text
    # The key went out as the query parameter, the one place the specs give it.
    assert all(request.url.params.get("apikey") == KEY for request in indexer.requests)


def test_a_key_that_is_refused_saves_nothing(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    answer = client.post("/api/v1/procure/indexers", json=payload(api_key=secrets.token_hex(16)))
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "indexer_key_rejected"
    assert client.get("/api/v1/procure/indexers").json() == {"indexers": []}


def test_the_text_an_indexer_sends_with_an_error_never_reaches_the_answer(client: TestClient, operator: Account,
                                                                           indexer: FakeIndexer) -> None:
    indexer.overrides["caps"] = lambda request: xml_response(error_document(100, f"Wrong key {KEY} given"))
    answer = client.post("/api/v1/procure/indexers", json=payload(api_key=KEY))
    assert answer.status_code == 502 and KEY not in answer.text


@pytest.mark.parametrize(("over", "field"), [
    ({"url": "ftp://indexer.example.com/api"}, "url"), ({"url": "https://u:p@indexer.example.com/api"}, "url"),
    ({"url": "https://indexer.example.com/api?apikey=x"}, "url"), ({"url": "not an address"}, "url"),
    ({"name": "   "}, "name"), ({"name": "x" * 101}, "name"), ({"priority": 0}, "priority"),
    ({"priority": 51}, "priority"), ({"daily_limit": -1}, "daily_limit"), ({"daily_limit": 100001}, "daily_limit"),
    ({"categories": [0]}, "categories"), ({"categories": [100000]}, "categories"),
    ({"category_langs": {"7020": "klingon"}}, "category_langs"), ({"api_key": "has a space"}, "api_key"),
    ({"api_key": "x" * 600}, "api_key"), ({"kind": "rss"}, "kind"),
])
def test_a_bad_input_is_refused_and_nothing_is_asked(client: TestClient, operator: Account, indexer: FakeIndexer,
                                                      over: dict[str, Any], field: str) -> None:
    answer = client.post("/api/v1/procure/indexers", json=payload(**over))
    assert answer.status_code == 422, answer.text
    assert indexer.requests == []
    assert client.get("/api/v1/procure/indexers").json() == {"indexers": []}


def test_a_name_is_used_once(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    add(client)
    answer = client.post("/api/v1/procure/indexers", json=payload(name="library one"))
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "name_taken"


def test_an_address_that_cannot_be_reached_or_may_not_be_is_said_so(client: TestClient, operator: Account,
                                                                     net: FakeNetwork,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    gone = client.post("/api/v1/procure/indexers", json=payload(url="https://nowhere.example.com/api"))
    assert gone.status_code == 502 and gone.json()["detail"]["code"] == "indexer_unreachable"
    monkeypatch.setattr(gate, "resolver", lambda _host, _port: ["169.254.169.254"])
    net.add(FakeIndexer(HOST, KEY))
    blocked = client.post("/api/v1/procure/indexers", json=payload())
    assert blocked.status_code == 502 and blocked.json()["detail"]["code"] == "indexer_blocked"


def test_an_address_without_a_path_is_the_api_path(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    assert add(client, url=f"https://{HOST}")["url"] == URL


def test_something_that_is_not_an_indexer_is_not_saved(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    net.add(FakeIndexer(HOST, KEY, path="/elsewhere"))
    answer = client.post("/api/v1/procure/indexers", json=payload())
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "indexer_not_newznab"


def test_broken_caps_are_not_a_reason_to_refuse_the_indexer(client: TestClient, operator: Account,
                                                              indexer: FakeIndexer) -> None:
    indexer.overrides["caps"] = lambda request: xml_response(b"<rss><channel/></rss>")
    made = add(client)
    assert {item["id"] for item in made["categories"] if item["on"]} == {7020, 3030}, "Newznab's own two categories"
    assert made["supported_params"]["book_search"] is False


# --- Testing ---------------------------------------------------------------------------------------------------------


def test_the_test_of_an_address_says_what_the_indexer_can_and_saves_nothing(client: TestClient, operator: Account,
                                                                              indexer: FakeIndexer) -> None:
    answer = client.post("/api/v1/procure/indexers/test", json={"kind": "newznab", "url": URL, "api_key": KEY,
                                                                "language": "de"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["book_search"] is True and body["book_params"] == ["q", "author", "isbn"]
    assert body["category_count"] == 16 and body["items"] == 1 and body["kind"] == "newznab"
    assert {item["id"] for item in body["categories"] if item["on"]} == {3030, 3130, 7020, 7120}
    assert indexer.functions() == ["caps", "search"]
    probe = indexer.requests[-1].url.params
    assert probe["limit"] == "1" and set(probe["cat"].split(",")) == {"3030", "3130", "7020", "7120"}
    assert "book" not in indexer.functions(), "t=book is a trap: a plain search in the categories is the probe"
    assert client.get("/api/v1/procure/indexers").json() == {"indexers": []}


def test_the_test_of_an_address_can_use_the_key_of_a_saved_indexer(client: TestClient, operator: Account,
                                                                    indexer: FakeIndexer) -> None:
    made = add(client)
    indexer.requests.clear()
    answer = client.post("/api/v1/procure/indexers/test", json={"indexer_id": made["id"], "kind": "newznab", "url": URL})
    assert answer.status_code == 200, answer.text
    assert all(request.url.params["apikey"] == KEY for request in indexer.requests)


def test_a_feed_of_the_wrong_kind_is_found_by_the_test(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    net.add(FakeIndexer(HOST, KEY, kind="torznab"))
    answer = client.post("/api/v1/procure/indexers/test", json={"kind": "newznab", "url": URL, "api_key": KEY})
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "indexer_wrong_kind"
    right = client.post("/api/v1/procure/indexers/test", json={"kind": "torznab", "url": URL, "api_key": KEY})
    assert right.status_code == 200 and right.json()["kind"] == "torznab"


def test_an_indexer_with_nothing_in_the_categories_is_still_a_working_one(client: TestClient, operator: Account,
                                                                          indexer: FakeIndexer) -> None:
    indexer.empty = True
    answer = client.post("/api/v1/procure/indexers/test", json={"kind": "newznab", "url": URL, "api_key": KEY})
    assert answer.status_code == 200 and answer.json()["items"] == 0


def test_the_test_of_a_saved_indexer_keeps_what_it_found(client: TestClient, operator: Account,
                                                          indexer: FakeIndexer) -> None:
    made = add(client)
    answer = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert answer.status_code == 200
    shown = answer.json()["indexer"]
    assert shown["used_today"] == 2 and shown["state"] == "ready" and shown["caps_checked_at"]


def test_an_indexer_that_is_not_there_is_not_found(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    assert client.post("/api/v1/procure/indexers/999/test").status_code == 404
    assert client.delete("/api/v1/procure/indexers/999").status_code == 404
    assert client.patch("/api/v1/procure/indexers/999", json={"priority": 3}).status_code == 404


# --- Pacing, pauses and limits -----------------------------------------------------------------------------------------


def test_an_indexer_is_asked_at_most_once_in_two_seconds(client: TestClient, operator: Account, indexer: FakeIndexer,
                                                          pace: FakeClock) -> None:
    add(client)
    # The caps, then the next request of the same address: it waits for its turn.
    assert pace.slept == []
    client.post("/api/v1/procure/indexers/test", json={"kind": "newznab", "url": URL, "api_key": KEY})
    assert sum(pace.slept) >= 2.0 * 2 - 0.01, pace.slept
    assert all(wait <= indexers.PACE_SECONDS + 0.001 for wait in pace.slept)


def test_pacing_is_per_address(client: TestClient, operator: Account, net: FakeNetwork, pace: FakeClock) -> None:
    net.add(FakeIndexer("one.example.com", KEY))
    net.add(FakeIndexer("two.example.com", KEY))
    add(client, name="One", url="https://one.example.com/api")
    add(client, name="Two", url="https://two.example.com/api")
    assert pace.slept == []


def test_a_request_limit_puts_the_indexer_to_rest_until_the_time_it_names(client: TestClient, operator: Account,
                                                                           indexer: FakeIndexer, day: Day) -> None:
    made = add(client)
    indexer.overrides["search"] = lambda request: httpx.Response(429, headers={"retry-after": "120"}, content=b"")
    limited = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert limited.status_code == 429 and limited.json()["detail"]["code"] == "indexer_limit_reached"
    # An indexer that asks for two minutes is left alone for five at least (the pause never gets shorter than that).
    assert limited.json()["detail"]["retry_after"] == 300 and limited.headers["retry-after"] == "300"
    shown = client.get("/api/v1/procure/indexers").json()["indexers"][0]
    assert shown["state"] == "resting" and shown["resting_cause"] == "request_limit" and shown["resting_until"]
    # While it rests, nothing is asked.
    asked = len(indexer.requests)
    again = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert again.status_code == 429 and len(indexer.requests) == asked
    day.pass_by(seconds=301)
    del indexer.overrides["search"]
    assert client.post(f"/api/v1/procure/indexers/{made['id']}/test").status_code == 200
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["state"] == "ready"


def test_the_words_request_limit_reached_in_an_error_document_are_a_limit_too(client: TestClient, operator: Account,
                                                                               indexer: FakeIndexer) -> None:
    made = add(client)
    indexer.overrides["search"] = lambda request: xml_response(error_document(500, "Request limit reached"))
    answer = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert answer.status_code == 429 and answer.json()["detail"]["retry_after"] == 3600, "one hour without a hint"


def test_a_pause_is_never_longer_than_a_day(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    made = add(client)
    indexer.overrides["search"] = lambda request: httpx.Response(429, headers={"retry-after": "99999999"}, content=b"")
    answer = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert answer.json()["detail"]["retry_after"] == 24 * 3600


def test_the_limit_of_the_day_stops_the_requests_until_midnight(client: TestClient, operator: Account,
                                                                 indexer: FakeIndexer, day: Day) -> None:
    made = add(client, daily_limit=3)
    assert client.post(f"/api/v1/procure/indexers/{made['id']}/test").status_code == 200
    shown = client.get("/api/v1/procure/indexers").json()["indexers"][0]
    assert shown["used_today"] == 2 and shown["state"] == "ready"
    asked = len(indexer.requests)
    second = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert second.status_code == 429 and second.json()["detail"]["code"] == "indexer_daily_limit"
    assert len(indexer.requests) == asked + 1, "the third request of the day went out, the fourth did not"
    shown = client.get("/api/v1/procure/indexers").json()["indexers"][0]
    assert shown["used_today"] == 3 and shown["state"] == "resting" and shown["resting_cause"] == "daily_limit"
    quiet = len(indexer.requests)
    assert client.post(f"/api/v1/procure/indexers/{made['id']}/test").status_code == 429
    assert len(indexer.requests) == quiet
    # It rests until midnight (UTC), not for a day from now.
    until = clock.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    assert shown["resting_until"].startswith(until.strftime("%Y-%m-%dT00:00:00"))
    day.pass_by(days=1)
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["used_today"] == 0
    assert client.post(f"/api/v1/procure/indexers/{made['id']}/test").status_code == 200


def test_no_limit_of_the_day_means_no_limit(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    made = add(client, daily_limit=0)
    for _ in range(6):
        assert client.post(f"/api/v1/procure/indexers/{made['id']}/test").status_code == 200
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["used_today"] == 12


def test_the_limits_an_indexer_reports_are_kept_and_at_the_limit_it_rests(client: TestClient, operator: Account,
                                                                           indexer: FakeIndexer, day: Day) -> None:
    made = add(client)
    feed = indexer.feed.replace(
        b"<channel>", b'<channel><newznab:apilimits apiCurrent="49" apiMax="50" grabCurrent="3" grabMax="20" '
                      b'apiNextAvailable="Tue, 06 Oct 2026 23:00:00 +0000"/>')
    indexer.feed = feed
    ok = client.post(f"/api/v1/procure/indexers/{made['id']}/test").json()["indexer"]
    assert (ok["api_current"], ok["api_max"], ok["grab_current"], ok["grab_max"]) == (49, 50, 3, 20)
    assert ok["state"] == "ready"
    indexer.feed = feed.replace(b'apiCurrent="49"', b'apiCurrent="50"')
    full = client.post(f"/api/v1/procure/indexers/{made['id']}/test").json()["indexer"]
    assert full["state"] == "resting" and full["resting_cause"] == "api_limit"
    assert full["resting_until"] is not None


def test_an_indexer_that_refuses_the_key_later_is_marked_and_a_new_key_cures_it(client: TestClient, operator: Account,
                                                                                  indexer: FakeIndexer) -> None:
    made = add(client)
    new_key = secrets.token_hex(16)
    indexer.api_key = new_key
    bad = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert bad.status_code == 502 and bad.json()["detail"]["code"] == "indexer_key_rejected"
    shown = client.get("/api/v1/procure/indexers").json()["indexers"][0]
    assert shown["state"] == "key_rejected" and shown["last_error"] == "indexer_key_rejected"
    fixed = client.patch(f"/api/v1/procure/indexers/{made['id']}", json={"api_key": new_key})
    assert fixed.status_code == 200 and fixed.json()["state"] == "ready" and fixed.json()["last_error"] == ""
    assert client.post(f"/api/v1/procure/indexers/{made['id']}/test").status_code == 200


# --- Changing and removing ---------------------------------------------------------------------------------------------


def test_changing_what_does_not_touch_the_connection_asks_nobody(client: TestClient, operator: Account,
                                                                  indexer: FakeIndexer) -> None:
    made = add(client)
    asked = len(indexer.requests)
    changed = client.patch(f"/api/v1/procure/indexers/{made['id']}",
                           json={"name": "Renamed", "priority": 5, "daily_limit": 40, "categories": [7020]})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["name"], body["priority"], body["daily_limit"]) == ("Renamed", 5, 40)
    assert {item["id"] for item in body["categories"] if item["on"]} == {7020}
    assert len(indexer.requests) == asked


def test_a_new_address_or_key_is_asked_before_it_is_taken(client: TestClient, operator: Account, net: FakeNetwork,
                                                          indexer: FakeIndexer) -> None:
    made = add(client)
    moved = net.add(FakeIndexer("new.example.com", KEY))
    changed = client.patch(f"/api/v1/procure/indexers/{made['id']}",
                           json={"url": "https://new.example.com/api", "api_key": KEY})
    assert changed.status_code == 200 and changed.json()["url"] == "https://new.example.com/api"
    assert moved.functions() == ["caps"]
    broken = client.patch(f"/api/v1/procure/indexers/{made['id']}",
                          json={"url": "https://nowhere.example.com/api", "api_key": KEY})
    assert broken.status_code == 502
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["url"] == "https://new.example.com/api"


def test_an_indexer_gets_its_key_only_at_the_place_it_was_saved_for(client: TestClient, operator: Account,
                                                                    net: FakeNetwork, indexer: FakeIndexer) -> None:
    made = add(client)
    elsewhere = net.add(FakeIndexer("else.example.com", KEY))
    other_port = net.add(FakeIndexer(f"{HOST}:9999", KEY))
    for target in ("https://else.example.com/api", f"https://{HOST}:9999/api"):
        change = client.patch(f"/api/v1/procure/indexers/{made['id']}", json={"url": target})
        assert change.status_code == 422 and change.json()["detail"]["code"] == "key_required", change.text
        test = client.post("/api/v1/procure/indexers/test",
                           json={"indexer_id": made["id"], "kind": "newznab", "url": target})
        assert test.status_code == 422 and test.json()["detail"]["code"] == "key_required", test.text
    assert not elsewhere.requests and not other_port.requests, "the saved key was not sent anywhere else"
    assert row(made["id"]).url == URL
    # The same place (another path) keeps the key; a test with a key typed again may go anywhere.
    same = client.post("/api/v1/procure/indexers/test",
                       json={"indexer_id": made["id"], "kind": "newznab", "url": URL + "/"})
    assert same.status_code == 200, same.text
    typed = client.post("/api/v1/procure/indexers/test",
                        json={"indexer_id": made["id"], "kind": "newznab", "url": "https://else.example.com/api",
                              "api_key": KEY})
    assert typed.status_code == 200 and elsewhere.requests


def test_switching_an_indexer_off_stops_the_requests(client: TestClient, operator: Account,
                                                      indexer: FakeIndexer) -> None:
    made = add(client)
    off = client.patch(f"/api/v1/procure/indexers/{made['id']}", json={"enabled": False})
    assert off.json()["state"] == "off"
    asked = len(indexer.requests)
    answer = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "indexer_off"
    assert len(indexer.requests) == asked
    assert client.patch(f"/api/v1/procure/indexers/{made['id']}", json={"enabled": True}).json()["state"] == "ready"


def test_the_list_is_in_the_order_they_are_asked(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    for number, priority in enumerate((30, 5, 12), start=1):
        net.add(FakeIndexer(f"i{number}.example.com", KEY))
        add(client, name=f"Indexer {number}", url=f"https://i{number}.example.com/api", priority=priority)
    assert [item["name"] for item in client.get("/api/v1/procure/indexers").json()["indexers"]] == [
        "Indexer 2", "Indexer 3", "Indexer 1"]


def test_removing_an_indexer(client: TestClient, operator: Account, indexer: FakeIndexer) -> None:
    made = add(client)
    assert client.delete(f"/api/v1/procure/indexers/{made['id']}").status_code == 204
    assert client.get("/api/v1/procure/indexers").json() == {"indexers": []}
    assert client.delete(f"/api/v1/procure/indexers/{made['id']}").status_code == 404


def test_an_indexer_from_prowlarr_cannot_be_removed_or_renamed_but_can_be_tuned(client: TestClient,
                                                                                 operator: Account,
                                                                                 net: FakeNetwork) -> None:
    with SessionLocal() as db:
        db.add(Indexer(name="From Prowlarr", kind="torznab", url="http://prowlarr.example.com/7/api",
                       prowlarr_indexer_id=7, categories=[7020], category_langs={"7020": "mixed"}))
        db.commit()
        number = db.query(Indexer).one().id
    base = f"/api/v1/procure/indexers/{number}"
    gone = client.delete(base)
    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "indexer_from_prowlarr"
    for change in ({"name": "Mine"}, {"url": "http://elsewhere.example.com/api"}, {"kind": "newznab"}, {"api_key": "x"}):
        refused = client.patch(base, json=change)
        assert refused.status_code == 409, change
    tuned = client.patch(base, json={"priority": 2, "daily_limit": 9, "enabled": False, "categories": [7020, 3030]})
    assert tuned.status_code == 200, tuned.text
    assert tuned.json()["via_prowlarr"] is True and tuned.json()["priority"] == 2
    same = client.patch(base, json={"name": "From Prowlarr"})
    assert same.status_code == 200, "saying the same name again is no change"


def test_the_key_of_an_indexer_from_prowlarr_is_the_key_of_the_connection(client: TestClient, operator: Account,
                                                                           net: FakeNetwork) -> None:
    from app.models import ProwlarrConnection
    from app.security import encrypt_secret

    with SessionLocal() as db:
        db.add(ProwlarrConnection(url="http://prowlarr.example.com", api_key_enc=encrypt_secret(KEY, indexers.SECRET_CONTEXT)))
        db.add(Indexer(name="From Prowlarr", kind="newznab", url="http://prowlarr.example.com/7/api",
                       prowlarr_indexer_id=7, categories=[7020]))
        db.commit()
    served = net.add(FakeIndexer("prowlarr.example.com", KEY, path="/7/api"))
    number = client.get("/api/v1/procure/indexers").json()["indexers"][0]["id"]
    assert client.get("/api/v1/procure/indexers").json()["indexers"][0]["key_set"] is True
    assert client.post(f"/api/v1/procure/indexers/{number}/test").status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", served.requests[-1].url.params["apikey"])
    assert served.requests[-1].url.params["apikey"] == KEY
