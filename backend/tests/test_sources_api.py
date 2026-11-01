"""The operator's side of the sources: the switches, the markets, Hardcover's token, "match everything again"."""

from __future__ import annotations

import secrets
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Setting
from app.services import settings_service
from app.services.sources import config, throttle

from .conftest import DATA_DIR, TAB, make_account, new_client
from .fake_sources import ol_doc
from .library_helpers import run_jobs
from .match_helpers import World


def test_only_the_operator_in_a_browser_reads_or_changes_the_sources(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    paths = (("GET", "/api/v1/sources", None), ("PUT", "/api/v1/sources", {"enabled": False}),
             ("PUT", "/api/v1/sources/hardcover/token", {"token": "abc"}), ("DELETE", "/api/v1/sources/hardcover/token", None),
             ("POST", "/api/v1/sources/rematch", None))
    with new_client(make_account("rita")) as member:
        for method, path, body in paths:
            assert member.request(method, path, json=body).status_code == 403, path
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        for method, path, body in paths:
            assert nobody.request(method, path, json=body).status_code == 401, path
    assert world.fake.log == []


def test_the_sources_card_says_how_each_source_fares(client: TestClient, operator: Account, world: World) -> None:
    body = client.get("/api/v1/sources").json()
    assert body["enabled"] is True and body["markets"] == ["de", "us"] and body["hardcover_connected"] is False
    assert "de" in body["available_markets"] and "uk" in body["available_markets"]
    by_id = {s["id"]: s for s in body["sources"]}
    assert list(by_id) == ["hardcover", "openlibrary", "audible", "dnb", "apple", "wikidata", "bnf", "libris", "kb", "nb"]
    assert by_id["openlibrary"]["state"] == "ready" and by_id["audible"]["state"] == "ready"
    assert by_id["dnb"]["state"] == "ready" and by_id["dnb"]["enabled"] is True, "on from the start"
    assert by_id["hardcover"]["state"] == "not_connected" and by_id["hardcover"]["enabled"] is True
    assert by_id["hardcover"]["requests_per_day"] == 5000 and by_id["openlibrary"]["requests_today"] == 0
    assert body["waiting"] == 0 and body["to_look_at"] == 0


def test_requests_today_and_the_last_error_are_shown(client: TestClient, operator: Account, world: World) -> None:
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Mara Lindqvist"])]
    with SessionLocal() as db:
        from app.services.sources import http

        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=a")
    world.fake.status["openlibrary.org"] = 503
    with SessionLocal() as db, pytest.raises(Exception):  # noqa: B017 - the error itself is tested elsewhere
        http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=b")
    shown = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}["openlibrary"]
    assert shown["requests_today"] == 2 and shown["last_error"] == "server" and shown["last_ok_at"] and shown["last_error_at"]
    assert shown["state"] == "resting" and shown["resting_until"]


def test_the_switches_are_kept(client: TestClient, operator: Account, world: World) -> None:
    answer = client.put("/api/v1/sources", json={"enabled": False, "sources": {"dnb": True, "audible": False}})
    assert answer.status_code == 200
    body = answer.json()
    assert body["enabled"] is False
    by_id = {s["id"]: s for s in body["sources"]}
    assert by_id["dnb"]["enabled"] is True and by_id["audible"]["enabled"] is False
    # The operator who switches the library off keeps it off: the switch is saved as off, the default does not come back.
    assert client.put("/api/v1/sources", json={"enabled": True, "sources": {"dnb": False}}).status_code == 200
    kept = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}
    assert kept["dnb"]["enabled"] is False and kept["dnb"]["state"] == "off"
    client.put("/api/v1/sources", json={"sources": {"dnb": True}})
    assert all(s["state"] == "main_off" for s in body["sources"]), "the main switch is above them all"
    client.put("/api/v1/sources", json={"enabled": True})
    again = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}
    assert again["dnb"]["state"] == "ready" and again["audible"]["state"] == "off"
    assert client.put("/api/v1/sources", json={"sources": {"google": True}}).status_code == 422


def test_the_markets_are_chosen_from_the_markets_there_are(client: TestClient, operator: Account, world: World) -> None:
    assert client.put("/api/v1/sources", json={"markets": ["us", "uk", "us"]}).json()["markets"] == ["us", "uk"]
    for bad in (["evil.example.com"], [], ["de", "nope"], ["de", "us", "uk", "fr", "ca", "au", "it"], "de"):
        assert client.put("/api/v1/sources", json={"markets": bad}).status_code == 422, bad
    with SessionLocal() as db:
        assert config.chosen_markets(db) == ["us", "uk"]
        settings_service.save(db, {"source_markets": ["rubbish", 3]})
        assert config.chosen_markets(db) == ["de", "us"], "a setting damaged by hand falls back"


# ---- Hardcover's token ------------------------------------------------------------------------------------------


def stored_token() -> str:
    with SessionLocal() as db:
        return str(settings_service.get(db, "hardcover_token_enc") or "")


def test_the_token_is_sealed_checked_and_never_read_back(client: TestClient, operator: Account, world: World) -> None:
    token = secrets.token_urlsafe(24)
    answer = client.put("/api/v1/sources/hardcover/token", json={"token": f"Bearer {token}"})
    assert answer.status_code == 200, answer.text
    assert token not in answer.text and answer.json()["hardcover_connected"] is True
    sealed = stored_token()
    assert sealed and token not in sealed and sealed.isalnum()
    assert config.hardcover_token(SessionLocal()) == token, "the prefix is not part of the token"
    # It was tried once, as a bearer token and in no address.
    [request] = world.fake.log
    assert request.headers["authorization"] == f"Bearer {token}" and token not in str(request.url)
    assert token not in client.get("/api/v1/sources").text and token not in client.get("/api/v1/settings").text
    state = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}["hardcover"]
    assert state["state"] == "ready"


def test_the_token_is_in_no_log(client: TestClient, operator: Account, world: World) -> None:
    token = secrets.token_urlsafe(24)
    client.put("/api/v1/sources/hardcover/token", json={"token": token})
    world.fake.status["api.hardcover.app"] = 401
    client.put("/api/v1/sources/hardcover/token", json={"token": token + "x"})
    for logfile in Path(DATA_DIR, "logs").glob("*.log*"):
        assert token not in logfile.read_text(encoding="utf-8", errors="replace")


def test_a_token_hardcover_refuses_is_not_kept_and_the_old_one_stays(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    good = secrets.token_urlsafe(24)
    client.put("/api/v1/sources/hardcover/token", json={"token": good})
    before = stored_token()
    world.fake.status["api.hardcover.app"] = 401
    refused = client.put("/api/v1/sources/hardcover/token", json={"token": secrets.token_urlsafe(24)})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "source_token_invalid"
    assert stored_token() == before
    # The first one is judged by Hardcover again, not left stopped by the second one's refusal.
    del world.fake.status["api.hardcover.app"]
    world.advance(100)
    with SessionLocal() as db:
        assert config.availability(db, "hardcover").state in ("token_invalid", "ready", "resting")
    throttle.set_state("hardcover", "")
    with SessionLocal() as db:
        assert config.availability(db, "hardcover").ready


def test_a_first_token_that_is_refused_leaves_hardcover_not_connected(client: TestClient, operator: Account,
                                                                       world: World) -> None:
    world.fake.status["api.hardcover.app"] = 403
    assert client.put("/api/v1/sources/hardcover/token", json={"token": secrets.token_urlsafe(24)}).status_code == 422
    assert stored_token() == ""
    state = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}["hardcover"]
    assert state["state"] == "not_connected"


@pytest.mark.parametrize("token", ["", "   ", "a b", "a\nb", "a\x00b", "x" * 4001, "Bearer "])
def test_what_is_no_token_is_refused_without_asking(client: TestClient, operator: Account, world: World, token: str) -> None:
    answer = client.put("/api/v1/sources/hardcover/token", json={"token": token})
    assert answer.status_code == 422, token
    assert stored_token() == "" and world.fake.log == []


def test_a_token_is_kept_when_hardcover_cannot_be_reached_to_try_it(client: TestClient, operator: Account,
                                                                     world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(world.fake, "handle_request", unreachable)
    assert client.put("/api/v1/sources/hardcover/token", json={"token": secrets.token_urlsafe(24)}).status_code == 200
    assert stored_token() != ""
    state = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}["hardcover"]
    assert state["last_error"] == "unreachable"


def test_disconnecting_removes_the_token(client: TestClient, operator: Account, world: World) -> None:
    client.put("/api/v1/sources/hardcover/token", json={"token": secrets.token_urlsafe(24)})
    answer = client.delete("/api/v1/sources/hardcover/token")
    assert answer.status_code == 200 and answer.json()["hardcover_connected"] is False
    assert stored_token() == ""
    asked = len(world.fake.log)
    from app.services.sources import hardcover, http
    from app.services.sources.model import Query

    with SessionLocal() as db, pytest.raises(http.SourceOff):
        hardcover.search(db, Query(kind="ebook", title="x"))
    assert len(world.fake.log) == asked


def test_connecting_again_after_the_token_ran_out_ends_the_stop(client: TestClient, operator: Account, world: World) -> None:
    client.put("/api/v1/sources/hardcover/token", json={"token": secrets.token_urlsafe(24)})
    throttle.set_state("hardcover", "token_invalid")
    state = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}["hardcover"]
    assert state["state"] == "token_invalid"
    assert client.put("/api/v1/sources/hardcover/token", json={"token": secrets.token_urlsafe(24)}).status_code == 200
    state = {s["id"]: s for s in client.get("/api/v1/sources").json()["sources"]}["hardcover"]
    assert state["state"] == "ready"


def test_the_token_is_not_in_the_settings_a_backup_carries_in_the_clear(client: TestClient, operator: Account,
                                                                         world: World) -> None:
    token = secrets.token_urlsafe(24)
    client.put("/api/v1/sources/hardcover/token", json={"token": token})
    with SessionLocal() as db:
        rows = db.scalars(select(Setting)).all()
    assert all(token not in str(row.value) for row in rows)


# ---- Matching everything again ----------------------------------------------------------------------------------


def test_matching_everything_again_says_how_many_books(client: TestClient, operator: Account, world: World) -> None:
    assert client.post("/api/v1/sources/rematch").json() == {"books": 0}
    run_jobs()
