"""The door before the sign-in (``middleware.SignInGate``): a request that brings no credential to a route that wants a
sign-in is answered before the framework, a thread or the database is asked; a sender whose wrong guesses keep coming
is turned away for credentials the database does not know, and **never** for one it does (a session, a device token, an
API key), whatever the door has seen before: not after a restart, not after a night, not behind a shared address. Open
routes, the files of the app and routes nobody knows are not the door's business."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import clock, middleware
from app.db import SessionLocal
from app.errors import WRONG_CREDENTIAL
from app.main import app
from app.models import Account
from app.security import brake
from app.services import apikeys, devices, settings_service

from .conftest import PASSWORD, make_account, new_client


class Spy:
    """The app behind the door: notes every request that reaches it and says 401 like a route without a sign-in. It
    also puts on the request what the routes put on a wrong credential (``deps.wrong_credential``)."""

    def __init__(self) -> None:
        self.reached: list[str] = []

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        self.reached.append(scope["path"])
        scope.setdefault("state", {})[WRONG_CREDENTIAL] = True
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})


@pytest.fixture
def door() -> tuple[TestClient, Spy, middleware.SignInGate]:
    spy = Spy()
    gate = middleware.SignInGate(spy, routes=lambda: app.routes)
    return TestClient(gate, base_url="http://testserver"), spy, gate


def guess(client: TestClient, n: int) -> None:
    for i in range(n):
        client.get("/api/v1/works", headers={"Cookie": f"nexbooks_session=guess{i}x"})


def stranger_client() -> TestClient:
    return TestClient(app, base_url="http://testserver", headers={"X-Nexbooks-Client": "guess-tab"})


def close_the_door(stranger: TestClient) -> None:
    """A stranger's wrong cookies, through the whole app, until its next unknown credential is refused at the door."""
    for n in range(middleware.FAIL_LIMIT):
        assert stranger.get("/api/v1/works", headers={"Cookie": f"nexbooks_session=guess{n}"}).status_code == 401
    assert stranger.get("/api/v1/works", headers={"Cookie": "nexbooks_session=more"}).status_code == 429


def test_the_door_knows_the_routes_of_the_app_and_which_want_a_sign_in() -> None:
    table = middleware.route_table(app.routes)
    assert len(table) > 150
    gate = middleware.SignInGate(Spy(), routes=lambda: app.routes)
    assert gate.wants_sign_in("GET", "/api/v1/works")
    assert gate.wants_sign_in("GET", "/api/v1/files/7/content")
    assert gate.wants_sign_in("DELETE", "/api/v1/backups/x.zip")
    for method, path in (("POST", "/api/v1/auth/login"), ("GET", "/api/v1/health"), ("GET", "/api/v1/me"),
                         ("GET", "/api/v1/invite/abc"), ("GET", "/api/v1/nothing"), ("PATCH", "/api/v1/works")):
        assert not gate.wants_sign_in(method, path), (method, path)


def test_a_door_that_finds_no_route_says_so_as_the_app_starts(caplog: pytest.LogCaptureFixture) -> None:
    """It fails open (every route still asks for the sign-in), so a quiet door that protects nothing would go unseen."""
    message = "no route that wants a sign-in"
    gate = middleware.SignInGate(Spy(), routes=list)
    with caplog.at_level(logging.WARNING, logger="nexbooks.api"), TestClient(gate):
        pass
    assert [r for r in caplog.records if message in r.getMessage()]
    caplog.clear()
    whole = middleware.SignInGate(Spy(), routes=lambda: app.routes)
    with caplog.at_level(logging.WARNING, logger="nexbooks.api"), TestClient(whole):
        pass
    assert not [r for r in caplog.records if message in r.getMessage()]


def test_without_a_credential_the_app_is_not_reached(door: tuple[TestClient, Spy, middleware.SignInGate]) -> None:
    client, spy, _gate = door
    answer = client.get("/api/v1/works")
    assert answer.status_code == 401
    assert answer.json() == {"detail": {"code": "sign_in_required", "message": "Sign in first."}}
    assert client.get("/api/v1/files/1/content").status_code == 401
    assert spy.reached == []
    # Open routes, unknown ones and everything outside the interface go on to the app.
    for path in ("/api/v1/health", "/api/v1/me", "/api/v1/nothing", "/", "/assets/x.js"):
        client.get(path)
    assert spy.reached == ["/api/v1/health", "/api/v1/me", "/api/v1/nothing", "/", "/assets/x.js"]
    # "Bearer" with nothing after it is a wrong token for the routes (it counts against the sender): theirs to say.
    spy.reached.clear()
    client.get("/api/v1/works", headers={"Authorization": "Bearer "})
    assert spy.reached == ["/api/v1/works"]


def test_the_answer_of_the_door_is_the_answer_of_the_routes(client: TestClient) -> None:
    """Through the whole app: the same 401 a route gives (``test_routes`` checks every route for the status)."""
    with TestClient(app, base_url="http://testserver") as bare:
        answer = bare.get("/api/v1/works")
    assert answer.status_code == 401
    assert answer.json()["detail"]["code"] == "sign_in_required"
    assert answer.headers["cache-control"] == "no-store" and "x-request-id" in answer.headers


def test_a_sender_whose_guesses_keep_coming_waits_for_credentials_nobody_has(
        door: tuple[TestClient, Spy, middleware.SignInGate], monkeypatch: pytest.MonkeyPatch) -> None:
    client, spy, gate = door
    now = [1000.0]
    monkeypatch.setattr(clock, "monotonic", lambda: now[0])
    guess(client, middleware.FAIL_LIMIT)
    reached = len(spy.reached)
    refused = client.get("/api/v1/works", headers={"Cookie": "nexbooks_session=another"})
    assert refused.status_code == 429 and refused.json()["detail"]["code"] == "too_many_attempts"
    assert 1 <= int(refused.headers["retry-after"]) <= middleware.FAIL_WINDOW
    assert len(spy.reached) == reached, "the refused request reached the app"
    # A route without a sign-in is not the door's.
    assert client.post("/api/v1/auth/login", headers={"Cookie": "nexbooks_session=x"}).status_code == 401
    assert len(spy.reached) == reached + 1
    # Another sender is not held up by this one.
    assert gate.closed_for(["addr:192.0.2.9"], now[0]) == 0
    # A minute later the sender may try again.
    now[0] += middleware.FAIL_WINDOW + 1
    assert client.get("/api/v1/works", headers={"Cookie": "nexbooks_session=later"}).status_code == 401


def test_a_credential_the_database_has_is_never_turned_away(operator: Account) -> None:
    """A session, a device token and an API key of the database go through a closed door, also one that never saw them
    (``forget_gate`` is a restart): the door keeps no list of credentials that could be lost, it asks the database."""
    tom = make_account("tom")
    with SessionLocal() as db:
        settings_service.save(db, {"api_keys_allowed": True})
        _device, device_token = devices.make(db, db.get(Account, tom.id), "phone")  # type: ignore[arg-type]
        _key, api_key = apikeys.make(db, db.get(Account, tom.id), "script", "read")  # type: ignore[arg-type]
    with new_client(tom) as person, stranger_client() as stranger:
        for _restart in range(2):
            middleware.forget_gate()
            close_the_door(stranger)
            assert person.get("/api/v1/works").status_code == 200, "a signed-in session was turned away"
            assert stranger.get("/api/v1/works", headers={"Authorization": f"Bearer {device_token}"}).status_code == 200
            assert stranger.get("/api/v1/works", headers={"Authorization": f"Bearer {api_key}"}).status_code == 200
        # The ones the database does not have are turned away.
        assert stranger.get("/api/v1/works", headers={"Authorization": f"Bearer nbd_{'a' * 43}"}).status_code == 429
        assert stranger.get("/api/v1/works", headers={"Authorization": f"Bearer nbk_{'a' * 43}"}).status_code == 429


def test_a_session_that_ended_is_a_guess_again(operator: Account) -> None:
    """Signed out, the cookie names nothing in the database: for a closed door it is any other made-up cookie."""
    tom = make_account("tom")
    with new_client(tom) as person, stranger_client() as stranger:
        cookie = person.cookies.get("nexbooks_session")
        assert person.get("/api/v1/works").status_code == 200
        assert person.post("/api/v1/auth/logout").status_code in (200, 204)
        close_the_door(stranger)
        assert stranger.get("/api/v1/works", headers={"Cookie": f"nexbooks_session={cookie}"}).status_code == 429


def test_a_real_person_signs_in_and_reads_while_a_stranger_at_the_same_address_guesses(operator: Account) -> None:
    """Through the whole app: tom's session from before goes on, and a new sign-in after the door closed works."""
    tom = make_account("tom")
    with new_client(tom) as before, stranger_client() as stranger:
        assert before.get("/api/v1/works").status_code == 200
        close_the_door(stranger)
        assert before.get("/api/v1/works").status_code == 200
    with TestClient(app, base_url="http://testserver", headers={"X-Nexbooks-Client": "late-tab"}) as late:
        assert late.post("/api/v1/auth/login", json={"name": "tom", "password": PASSWORD}).status_code == 200
        assert late.get("/api/v1/works").status_code == 200


def test_only_a_wrong_credential_counts_as_a_wrong_guess(operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes mark ``sign_in_required``, ``token_invalid`` and ``key_invalid``. A key at a route for devices
    (``token_wrong_kind``), keys that are switched off (``api_off``) and a web page asking with a key
    (``origin_refused``) are mistakes of a program, not guesses: a script with the wrong header does not shut its own
    address out."""
    tom = make_account("tom")
    with SessionLocal() as db:
        _key, api_key = apikeys.make(db, db.get(Account, tom.id), "script", "read")  # type: ignore[arg-type]
    wrong_kind = {"Authorization": f"Bearer {api_key}"}
    more = middleware.FAIL_LIMIT + 5
    with stranger_client() as script:
        # A key at a route that wants a device or a browser.
        for _ in range(more):
            assert script.get("/api/v1/auth/me", headers=wrong_kind).status_code == 401
        # Keys switched off: the same key at a route for programs.
        for _ in range(more):
            assert script.get("/api/v1/works", headers=wrong_kind).status_code == 401
        # Keys switched on, a key with an Origin.
        with SessionLocal() as db:
            settings_service.save(db, {"api_keys_allowed": True})
        for _ in range(more):
            answer = script.get("/api/v1/works", headers={**wrong_kind, "Origin": "https://example.com"})
            assert answer.status_code == 403
        # Nothing of it was counted: a credential nobody has still gets the route's 401, not the door's 429.
        assert script.get("/api/v1/works", headers={"Cookie": "nexbooks_session=nobody"}).status_code == 401
    # The three kinds of guess do count (the brake of the routes for tokens, five tries, is out of the way here).
    monkeypatch.setattr(brake, "FREE", 10_000)
    for header, value in (("Cookie", "nexbooks_session={}"), ("Authorization", "Bearer nbd_{}"),
                          ("Authorization", "Bearer nbk_{}")):
        middleware.forget_gate()
        with stranger_client() as stranger:
            codes = [stranger.get("/api/v1/works", headers={header: value.format(f"x{n:040d}")}).status_code
                     for n in range(middleware.FAIL_LIMIT + 2)]
            assert codes[middleware.FAIL_LIMIT - 1] == 401 and codes[-1] == 429, (header, value, codes)


def test_an_open_route_makes_nothing_known(operator: Account) -> None:
    """A made-up cookie that an open route answered with 200 is still a made-up cookie at a route that wants a sign-in."""
    with stranger_client() as stranger:
        close_the_door(stranger)
        fake = {"Cookie": "nexbooks_session=minted"}
        assert stranger.get("/api/v1/health", headers=fake).status_code == 200
        assert stranger.get("/api/v1/works", headers=fake).status_code == 429


def test_without_trusted_proxies_a_made_up_forwarded_for_does_not_open_a_new_count(operator: Account) -> None:
    """As for the sign-in brake: behind a proxy nexbooks was not told to believe, the proxy as a whole is the sender."""
    with stranger_client() as stranger:
        codes = [stranger.get("/api/v1/works", headers={"Cookie": f"nexbooks_session=x{i}",
                                                       "X-Forwarded-For": f"198.51.100.{i}"}).status_code
                 for i in range(60)]
        assert codes[middleware.FAIL_LIMIT - 1] == 401 and codes[-1] == 429, codes
        # A known session of another person behind the same proxy goes on.
        tom = make_account("tom")
        with new_client(tom) as person:
            assert person.get("/api/v1/works", headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 200


def test_where_the_database_cannot_answer_the_request_goes_on(door: tuple[TestClient, Spy, middleware.SignInGate],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Never turn away on doubt: a busy or missing database means the route (which says what is true) decides."""
    client, spy, _gate = door
    guess(client, middleware.FAIL_LIMIT)
    reached = len(spy.reached)

    def broken(_self: Any) -> sqlite3.Connection:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(middleware.CredentialLookup, "_open", broken)
    assert client.get("/api/v1/works", headers={"Cookie": "nexbooks_session=anybody"}).status_code == 401
    assert len(spy.reached) == reached + 1


def test_the_counts_of_the_door_stay_bounded(door: tuple[TestClient, Spy, middleware.SignInGate],
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    _client, _spy, gate = door
    monkeypatch.setattr(middleware, "MAX_SENDERS", 50)
    for n in range(500):
        gate._note_fail(f"addr:198.51.100.{n}", 10.0)
    assert len(gate._fails) == 50
    for _ in range(500):
        gate._note_fail("addr:203.0.113.1", 10.0)
    assert len(gate._fails["addr:203.0.113.1"]) == middleware.FAIL_LIMIT
