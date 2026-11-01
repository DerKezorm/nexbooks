"""Connecting a device that has no camera: the device asks for a code and shows it, a signed-in person types it
in the browser, sees how the device is named and confirms, and only then the device fetches its token with the secret that
only it knows. The code alone gives nothing; the token is made at the moment it is fetched, once; a wrong secret or code
counts against the sender; how many codes wait for a confirmation is limited; nothing secret is kept, logged or answered
but where it must be."""

from __future__ import annotations

import hashlib
import hmac as real_hmac
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import deps
from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, AppRequest, Device, utcnow
from app.routers import app1 as app1_router
from app.security import brake
from app.services import device_request, devices, logs, pairing

from .conftest import TAB, make_account, new_client, require_second_factor
from .pairing_helpers import Moving, app_client, ask, asked, cancel, claim_code, confirm, look_up, make, poll, standing

TOKEN = re.compile(r"nbd_[A-Za-z0-9_-]{43}")
CODE = re.compile(r"^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$")


def code_of(answer: Any) -> str:
    return str(answer.json()["detail"]["code"])


def rows() -> list[AppRequest]:
    with SessionLocal() as db:
        found = list(db.scalars(select(AppRequest).order_by(AppRequest.created_at)))
        db.expunge_all()
    return found


def device_count() -> int:
    with SessionLocal() as db:
        return db.query(Device).count()


def from_address(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """The next requests come from ``address`` as far as the creation of codes can tell (the sender the server counts)."""
    monkeypatch.setattr(app1_router, "open_sender", lambda _request: f"addr:{address}")


def open_ones() -> list[AppRequest]:
    """What waits for a confirmation and has not run out."""
    from app import clock

    now = clock.now()
    return [row for row in rows() if row.state == "waiting" and row.expires_at > now]


def coming_from(address: str) -> TestClient:
    """A device on a connection from ``address`` (it may write ``X-Forwarded-For`` with every request)."""
    from app.main import app

    return TestClient(app, base_url="http://testserver", client=(address, 50000))


def typed_from(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """The next wrong codes typed in a browser are counted for ``address``."""
    monkeypatch.setattr(deps, "address_keys", lambda _request: [(f"addr:{address}", 5)])


# --- The way ----------------------------------------------------------------------------------------------------------


def test_the_device_asks_a_person_confirms_and_the_device_fetches_its_token_once(client: TestClient,
                                                                                  operator: Account) -> None:
    box = app_client()
    first = ask(box, "  Living   room TV ")
    assert first.status_code == 201 and first.headers["cache-control"] == "no-store"
    made = first.json()
    assert set(made) == {"id", "secret", "code", "expires_at", "poll_seconds"}
    assert CODE.fullmatch(made["code"]) and made["poll_seconds"] == 3
    assert len(made["id"]) == 22 and len(made["secret"]) == 43
    waiting = poll(box, made)
    assert (waiting.status_code, waiting.json()) == (200, {"state": "waiting"})
    assert waiting.headers["cache-control"] == "no-store"
    # The person types the code: how the device is called, nothing yet changes.
    seen = look_up(client, made["code"].lower().replace("-", " "))
    assert seen.status_code == 200, seen.text
    assert set(seen.json()) == {"id", "device_name", "requested_at", "expires_at"}
    assert seen.json()["id"] == made["id"] and seen.json()["device_name"] == "Living room TV"
    assert made["secret"] not in seen.text
    assert device_count() == 0 and [row.state for row in rows()] == ["waiting"]
    assert poll(box, made).json() == {"state": "waiting"}
    # The confirmation ties the request to the account; there is still no device and no token anywhere.
    done = confirm(client, made)
    assert (done.status_code, done.json()) == (200, {"state": "confirmed", "device": None}), done.text
    mine = next(row for row in rows() if row.id == made["id"])
    assert (mine.state, mine.account_id, mine.device_id) == ("confirmed", operator.id, None)
    assert device_count() == 0
    # The device fetches: the token comes with this answer, once.
    got = poll(box, made)
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {"state", "token", "account", "server"} and body["state"] == "confirmed"
    assert body["account"] == {"name": "tester"} and body["server"]["version"]
    assert TOKEN.fullmatch(body["token"]) and got.headers["cache-control"] == "no-store"
    me = app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["name"] == "tester"
    assert [row["name"] for row in client.get("/api/v1/devices").json()] == ["Living room TV"]
    for again in (poll(box, made), poll(app_client(), made)):
        assert (again.status_code, code_of(again)) == (410, "pairing_used") and "token" not in again.text
    shown = standing(client, made).json()
    assert shown["state"] == "connected" and shown["device"]["name"] == "Living room TV"
    # Only the hash of the token is kept, nowhere the token, the secret or the code.
    used = next(row for row in rows() if row.id == made["id"])
    assert used.state == "used" and used.device_id is not None and used.used_at is not None
    with SessionLocal() as db:
        assert db.scalar(select(Device.token_hash)) == devices.digest(body["token"])
    for column in used.__table__.columns:
        value = str(getattr(used, column.name))
        assert body["token"] not in value and made["secret"] not in value and made["code"].replace("-", "") not in value


def test_a_request_nobody_confirmed_leaves_no_device_behind(client: TestClient, operator: Account,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    box = app_client()
    made = asked(box)
    look_up(client, made["code"])
    moving.pass_by(minutes=9)
    assert poll(box, made).json() == {"state": "waiting"}
    moving.pass_by(minutes=2)
    ran_out = poll(box, made)
    assert (ran_out.status_code, ran_out.json()) == (200, {"state": "expired"})
    assert device_count() == 0


def test_the_code_alone_gives_nothing(client: TestClient, operator: Account) -> None:
    """Whoever reads the code off the screen, or knows the id, or even the code of a pairing of the other way, has no
    token: it goes to the secret, and only the device has that."""
    made = asked()
    assert confirm(client, made).status_code == 200
    other_way = make(client)
    stranger = app_client()
    for secret in (made["code"], made["id"], other_way["secret"]):
        answer = poll(stranger, made, secret)
        assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown") and "token" not in answer.text
    for answer in (claim_code(stranger, made["code"]),
                   stranger.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"]})):
        assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    assert device_count() == 0
    # And a code of the other way is nothing at the place where a person types the code of a device.
    brake.forget()
    unknown = look_up(client, other_way["code"])
    assert (unknown.status_code, code_of(unknown)) == (404, "pairing_unknown")
    assert poll(app_client(), made).status_code == 200 and device_count() == 1


def test_the_device_belongs_to_the_account_that_confirmed(client: TestClient, operator: Account) -> None:
    made = asked()
    with new_client(make_account("noor")) as noor:
        assert confirm(client, made).status_code == 200
        # Another person cannot take it, see it or take it back.
        late = confirm(noor, made)
        assert (late.status_code, code_of(late)) == (410, "pairing_used")
        assert look_up(noor, made["code"]).status_code == 410
        assert standing(noor, made).status_code == 404
        assert noor.delete(f"/api/v1/devices/pair-requests/{made['id']}").status_code == 404
        token = poll(app_client(), made).json()["token"]
        assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["name"] == "tester"
        assert noor.get("/api/v1/devices").json() == []


def test_a_request_nobody_confirmed_is_nobodys_to_see(client: TestClient, operator: Account) -> None:
    made = asked()
    assert standing(client, made).status_code == 404
    assert client.delete(f"/api/v1/devices/pair-requests/{made['id']}").status_code == 404
    assert standing(client, {"id": "no-such-request"}).status_code == 404


def test_the_same_person_confirming_twice_is_one_confirmation(client: TestClient, operator: Account) -> None:
    made = asked()
    assert confirm(client, made).status_code == 200
    again = confirm(client, made)
    assert (again.status_code, again.json()["state"]) == (200, "confirmed")
    assert poll(app_client(), made).status_code == 200 and device_count() == 1
    last = confirm(client, made)
    assert (last.status_code, last.json()["state"]) == (200, "connected") and last.json()["device"]["name"]


def test_two_people_confirming_at_the_same_moment_one_wins(client: TestClient, operator: Account) -> None:
    made = asked()
    boxes = [new_client(operator)] + [new_client(make_account(f"person{number}")) for number in range(3)]
    barrier = threading.Barrier(len(boxes))

    def attempt(box: TestClient) -> Any:
        barrier.wait()
        return confirm(box, made)

    try:
        with ThreadPoolExecutor(max_workers=len(boxes)) as pool:
            answers = list(pool.map(attempt, boxes))
    finally:
        for box in boxes:
            box.close()
    assert sorted(answer.status_code for answer in answers) == [200] + [410] * 3, [a.text for a in answers]
    winner = next(row for row in rows() if row.id == made["id"]).account_id
    token = poll(app_client(), made).json()["token"]
    with SessionLocal() as db:
        device = db.scalar(select(Device))
        assert device is not None and device.account_id == winner and device.token_hash == devices.digest(token)


def test_two_polls_at_the_same_moment_do_not_both_get_a_token(client: TestClient, operator: Account) -> None:
    made = asked()
    assert confirm(client, made).status_code == 200
    boxes = [app_client() for _ in range(10)]
    barrier = threading.Barrier(10)

    def fetch(box: TestClient) -> Any:
        barrier.wait()
        return poll(box, made)

    with ThreadPoolExecutor(max_workers=10) as pool:
        answers = list(pool.map(fetch, boxes))
    assert sorted(answer.status_code for answer in answers) == [200] + [410] * 9, [a.text for a in answers]
    assert {code_of(answer) for answer in answers if answer.status_code == 410} == {"pairing_used"}
    assert device_count() == 1, "exactly one token came into being"


def test_the_confirmation_needs_the_id_and_the_code(client: TestClient, operator: Account) -> None:
    made = asked()
    other = asked()
    wrong_code = client.post(f"/api/v1/devices/pair-requests/{made['id']}/confirm", json={"code": "ABCD-2345"})
    assert (wrong_code.status_code, code_of(wrong_code)) == (404, "pairing_unknown")
    mixed = client.post(f"/api/v1/devices/pair-requests/{other['id']}/confirm", json={"code": made["code"]})
    assert (mixed.status_code, code_of(mixed)) == (404, "pairing_unknown"), "the id of one and the code of another"
    nothing = client.post("/api/v1/devices/pair-requests/N" + "x" * 21 + "/confirm", json={"code": made["code"]})
    assert (nothing.status_code, code_of(nothing)) == (404, "pairing_unknown")
    assert all(row.state == "waiting" for row in rows()), "nothing was confirmed"
    assert confirm(client, made).status_code == 200


def test_a_name_is_kept_cleaned_or_refused_and_none_means_apple_tv(client: TestClient, operator: Account) -> None:
    box = app_client()
    assert look_up(client, asked(box, None)["code"]).json()["device_name"] == "Apple TV"
    assert look_up(client, asked(box, "   ")["code"]).json()["device_name"] == "Apple TV"
    assert look_up(client, asked(box, "a" * 80)["code"]).json()["device_name"] == "a" * 80
    html = "<img src=x onerror=alert(1)> TV"
    assert look_up(client, asked(box, html)["code"]).json()["device_name"] == html, "text stays text (the page escapes)"
    before = len(rows())
    for bad in ("x" * 81, "x" * 3000, "TV\nTwo", "T\u202eV"):
        answer = ask(box, bad)
        assert (answer.status_code, code_of(answer)) == (422, "device_name_invalid"), repr(bad[:10])
    assert box.post("/api/app/1/pairing/request", json={"device_name": 5}).status_code == 422
    assert len(rows()) == before, "a request with a name that cannot be kept makes nothing"
    assert box.post("/api/app/1/pairing/request", content=b"{}", headers={"content-type": "application/json"}
                    ).status_code == 201


# --- Running out, and who may still be told ----------------------------------------------------------------------------


def test_a_request_runs_out_after_ten_minutes_also_after_the_confirmation(client: TestClient, operator: Account,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    box = app_client()
    waiting = asked(box)
    confirmed = asked(box)
    assert confirm(client, confirmed).status_code == 200
    moving.pass_by(minutes=10, seconds=1)
    for made in (waiting, confirmed):
        answer = poll(box, made)
        assert (answer.status_code, answer.json()) == (200, {"state": "expired"}), "no token for a confirmed one either"
    ran_out = look_up(client, waiting["code"])
    assert (ran_out.status_code, code_of(ran_out)) == (410, "pairing_expired")
    late = confirm(client, waiting)
    assert (late.status_code, code_of(late)) == (410, "pairing_expired")
    assert standing(client, confirmed).json()["state"] == "expired"
    assert device_count() == 0
    # A while later it is forgotten altogether.
    moving.pass_by(minutes=11)
    assert code_of(look_up(client, waiting["code"])) == "pairing_unknown"
    assert code_of(poll(box, waiting)) == "pairing_unknown"


def test_what_ran_out_goes_after_a_while(client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    asked()
    moving.pass_by(minutes=19)
    with SessionLocal() as db:
        assert device_request.sweep(db) == 0
    moving.pass_by(minutes=2)
    with SessionLocal() as db:
        assert device_request.sweep(db) == 1
    assert rows() == []
    asked()
    moving.pass_by(minutes=21)
    asked()
    assert len(rows()) == 1, "making a new one sweeps too"


def test_only_the_right_secret_learns_that_a_request_was_used(client: TestClient, operator: Account) -> None:
    made = asked()
    assert confirm(client, made).status_code == 200
    assert poll(app_client(), made).status_code == 200
    wrong = poll(app_client(), made, "y" * 43)
    assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown"), "no word of its state"
    right = poll(app_client(), made)
    assert (right.status_code, code_of(right)) == (410, "pairing_used")


def test_an_id_nobody_made_and_a_wrong_secret_answer_the_same(client: TestClient, operator: Account) -> None:
    made = asked()
    nobody = poll(app_client(), {"id": "A" * 22}, "x" * 43)
    wrong = poll(app_client(), made, "x" * 43)
    assert (nobody.status_code, nobody.json()) == (wrong.status_code, wrong.json())
    assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    assert poll(app_client(), made).status_code == 200, "a wrong secret takes nothing"


@pytest.mark.parametrize("odd", ["x" * 200, "ü", "%00", "a b", "request"])
def test_odd_ids_are_simply_unknown(client: TestClient, operator: Account, odd: str) -> None:
    answer = app_client().get(f"/api/app/1/pairing/request/{odd}", headers={"X-Pairing-Secret": "z" * 43})
    assert answer.status_code in (404, 422) and "token" not in answer.text
    if answer.status_code == 404:
        assert code_of(answer) in ("pairing_unknown", "not_found")


def test_an_account_that_is_blocked_or_locked_gets_no_device(client: TestClient, operator: Account) -> None:
    noor = make_account("noor")
    made = asked()
    with new_client(noor) as browser:
        assert confirm(browser, made).status_code == 200
    box = app_client()
    with SessionLocal() as db:
        row = db.get(Account, noor.id)
        assert row is not None
        row.blocked_at = utcnow()
        db.commit()
    for _ in range(3):
        assert poll(box, made).json() == {"state": "expired"}
    with SessionLocal() as db:
        row = db.get(Account, noor.id)
        assert row is not None
        row.blocked_at = None
        db.commit()
        assert db.scalar(select(Device.id)) is None
    assert poll(box, made).json()["state"] == "confirmed", "nothing was taken meanwhile"


def test_when_the_account_holds_too_many_devices_the_person_hears_it_and_the_request_stays(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices, "MAX_DEVICES", 1)
    first = client.post("/api/v1/devices", json={"name": "script"}).json()
    made = asked()
    full = confirm(client, made)
    assert (full.status_code, code_of(full)) == (409, "too_many_devices")
    assert next(row for row in rows() if row.id == made["id"]).state == "waiting"
    assert client.delete(f"/api/v1/devices/{first['device']['id']}").status_code == 204
    assert confirm(client, made).status_code == 200


def test_a_device_that_cannot_be_made_at_the_fetch_leaves_the_request_confirmed(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fetch is one transaction: the request is taken, the device made, both committed together. Should the device not
    be made (the account filled up since the confirmation), nothing was taken."""
    made = asked()
    assert confirm(client, made).status_code == 200
    monkeypatch.setattr(devices, "MAX_DEVICES", 0)
    full = poll(app_client(), made)
    assert (full.status_code, code_of(full)) == (409, "too_many_devices")
    assert next(row for row in rows() if row.id == made["id"]).state == "confirmed"
    monkeypatch.undo()
    assert poll(app_client(), made).status_code == 200


def test_a_device_that_fails_in_the_middle_leaves_the_request_as_it_was(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    made = asked()
    assert confirm(client, made).status_code == 200

    def fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(devices, "add", fails)
    with TestClient(app_client().app, raise_server_exceptions=False) as box:
        assert poll(box, made).status_code == 500
    monkeypatch.undo()
    mine = next(row for row in rows() if row.id == made["id"])
    assert mine.state == "confirmed" and mine.used_at is None and mine.device_id is None
    assert poll(app_client(), made).status_code == 200


def test_signing_everywhere_out_ends_a_confirmation_that_was_not_fetched(client: TestClient, operator: Account) -> None:
    """A confirmation from a browser that may be the one taken over would bring a new device a moment later."""
    made = asked()
    assert confirm(client, made).status_code == 200
    assert client.post("/api/v1/auth/logout-all").status_code == 204
    answer = poll(app_client(), made)
    assert (answer.status_code, answer.json()) == (200, {"state": "expired"})
    assert device_count() == 0 and standing(client, made).status_code == 404


def test_the_operators_sign_out_of_an_account_ends_its_confirmations_too(client: TestClient, operator: Account) -> None:
    noor = make_account("noor")
    made = asked()
    with new_client(noor) as browser:
        assert confirm(browser, made).status_code == 200
    assert client.post(f"/api/v1/accounts/{noor.id}/sign-out").status_code == 204
    assert poll(app_client(), made).json() == {"state": "expired"} and device_count() == 0


def test_a_fetched_request_is_not_touched_by_signing_out_but_its_device_goes(client: TestClient,
                                                                             operator: Account) -> None:
    made = asked()
    confirm(client, made)
    token = poll(app_client(), made).json()["token"]
    assert client.post("/api/v1/auth/logout-all").status_code == 204
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert next(row for row in rows() if row.id == made["id"]).state == "used"


def test_a_request_goes_with_its_account(client: TestClient, operator: Account) -> None:
    noor = make_account("noor")
    made = asked()
    with new_client(noor) as browser:
        assert confirm(browser, made).status_code == 200
    answer = client.request("DELETE", f"/api/v1/accounts/{noor.id}",
                            json={"confirm_name": "noor", "current_password": "correct horse battery"})
    assert answer.status_code == 204
    assert rows() == [] or all(row.account_id is None for row in rows())
    assert poll(app_client(), made).status_code == 404


def test_the_person_can_take_the_confirmation_back_until_the_device_fetched(client: TestClient,
                                                                          operator: Account) -> None:
    box = app_client()
    made = asked(box)
    confirm(client, made)
    assert client.delete(f"/api/v1/devices/pair-requests/{made['id']}").status_code == 204
    assert (poll(box, made).status_code, poll(box, made).json()) == (200, {"state": "expired"})
    assert standing(client, made).status_code == 404
    assert client.delete(f"/api/v1/devices/pair-requests/{made['id']}").status_code == 404
    assert device_count() == 0
    # One the device fetched stays, with its device (that is revoked in the list).
    fetched = asked(box)
    confirm(client, fetched)
    token = poll(box, fetched).json()["token"]
    assert client.delete(f"/api/v1/devices/pair-requests/{fetched['id']}").status_code == 204
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert standing(client, fetched).json()["state"] == "connected"


def test_taking_back_while_the_device_fetches_never_leaves_a_half_made_device(client: TestClient,
                                                                              operator: Account) -> None:
    """The withdrawal changes the request only if it is still confirmed (part of the statement): of a fetch and a
    withdrawal at the same moment either the device gets its token, or nobody does."""

    def race(made: dict[str, Any]) -> tuple[Any, Any]:
        barrier = threading.Barrier(2)

        def fetch() -> Any:
            barrier.wait()
            return poll(app_client(), made)

        def withdraw() -> Any:
            barrier.wait()
            return client.delete(f"/api/v1/devices/pair-requests/{made['id']}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            fetched, withdrawn = pool.submit(fetch), pool.submit(withdraw)
            return fetched.result(), withdrawn.result()

    for _ in range(8):
        made = asked()
        confirm(client, made)
        answer, deleted = race(made)
        assert deleted.status_code in (204, 404) and answer.status_code == 200
        got_token = "token" in answer.json()
        assert got_token == (device_count() > 0)
        with SessionLocal() as db:
            db.query(Device).delete()
            db.commit()


# --- The limits ---------------------------------------------------------------------------------------------------------


def test_a_sender_can_have_only_so_many_codes_open_and_its_oldest_runs_out_for_the_next(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """A television that starts again must not lock itself out: at its limit the sender's oldest code runs out and the new
    one takes its place. Never more than the limit are open."""
    moving = Moving(monkeypatch)
    from_address(monkeypatch, "203.0.113.5")
    box = app_client()
    made = []
    for _ in range(device_request.OPEN_PER_SENDER):
        made.append(asked(box))
        moving.pass_by(seconds=1)
    newest = asked(box)
    assert len(open_ones()) == device_request.OPEN_PER_SENDER
    assert poll(box, made[0]).json() == {"state": "expired"}, "the oldest ran out"
    ran_out = look_up(client, made[0]["code"])
    assert (ran_out.status_code, code_of(ran_out)) == (410, "pairing_expired")
    assert poll(box, made[1]).json() == {"state": "waiting"} and poll(box, newest).json() == {"state": "waiting"}
    moving.pass_by(seconds=1)
    asked(box)
    assert poll(box, made[1]).json() == {"state": "expired"} and len(open_ones()) == device_request.OPEN_PER_SENDER
    from_address(monkeypatch, "203.0.113.6")
    asked(box)
    assert poll(box, made[2]).json() == {"state": "waiting"}, "another sender's request takes nothing of this one's"
    assert len(open_ones()) == device_request.OPEN_PER_SENDER + 1
    # A confirmed code does not wait any more: it does not count and is never the one that runs out.
    from_address(monkeypatch, "203.0.113.5")
    assert confirm(client, made[2]).status_code == 200
    for _ in range(device_request.OPEN_PER_SENDER + 3):
        moving.pass_by(seconds=1)
        asked(box)
    assert poll(box, made[2]).json()["state"] == "confirmed", "a confirmed request is not the one that runs out"
    moving.pass_by(minutes=11)
    assert asked(box)


def test_the_oldest_of_the_sender_goes_first_not_a_stranger_and_not_a_newer_one(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    monkeypatch.setattr(device_request, "OPEN_PER_SENDER", 3)
    box = app_client()
    from_address(monkeypatch, "203.0.113.9")
    stranger = asked(box)
    from_address(monkeypatch, "203.0.113.5")
    mine = []
    for _ in range(4):
        moving.pass_by(seconds=1)
        mine.append(asked(box))
    states = [poll(box, one).json()["state"] for one in (stranger, *mine)]
    assert states == ["waiting", "expired", "waiting", "waiting", "waiting"]


def test_of_many_asking_at_the_same_moment_never_more_than_the_room_is_open(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """Thirty at once from one sender: each gets its code (the oldest ones run out), and never more than the limit are
    open at the end, however the statements of the thirty interleave."""
    from_address(monkeypatch, "203.0.113.7")
    barrier = threading.Barrier(30)

    def attempt(_number: int) -> int:
        barrier.wait()
        return ask(app_client()).status_code

    with ThreadPoolExecutor(max_workers=30) as pool:
        answers = list(pool.map(attempt, range(30)))
    assert answers.count(201) == 30, answers
    assert len(open_ones()) == device_request.OPEN_PER_SENDER


def test_the_whole_server_has_a_wall_and_it_is_told_when_it_opens(client: TestClient, operator: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    monkeypatch.setattr(device_request, "OPEN_TOTAL", 5)
    box = app_client()
    for number in range(5):
        from_address(monkeypatch, f"198.51.100.{number}")
        assert ask(box).status_code == 201
        moving.pass_by(seconds=60 if number == 0 else 1)
    from_address(monkeypatch, "198.51.100.12")
    wall = ask(box)
    assert (wall.status_code, code_of(wall)) == (429, "too_many_requests"), "the server as a whole"
    # The first code was made about a minute ago and lives ten minutes: it ends in about nine.
    assert 535 <= int(wall.headers["retry-after"]) <= 542, wall.headers["retry-after"]
    assert wall.json()["detail"]["retry_after"] == int(wall.headers["retry-after"])
    assert len(open_ones()) == 5
    # A sender that holds as many as it may is not turned away by the wall: its oldest gives the place.
    monkeypatch.setattr(device_request, "OPEN_PER_SENDER", 1)
    from_address(monkeypatch, "198.51.100.3")
    assert ask(box).status_code == 201 and len(open_ones()) == 5
    # Another that has none is turned away, and nothing of the others is touched.
    from_address(monkeypatch, "198.51.100.77")
    assert ask(box).status_code == 429 and len(open_ones()) == 5


def test_a_refusal_by_the_server_wall_takes_nothing_from_the_sender(client: TestClient, operator: Account,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """A sender under its own limit meets the wall of the server: 429, and its codes stay as they were."""
    monkeypatch.setattr(device_request, "OPEN_TOTAL", 3)
    box = app_client()
    from_address(monkeypatch, "203.0.113.1")
    mine = [asked(box), asked(box)]
    from_address(monkeypatch, "203.0.113.2")
    asked(box)
    assert ask(box).status_code == 429
    from_address(monkeypatch, "203.0.113.1")
    monkeypatch.setattr(device_request, "OPEN_PER_SENDER", 5)
    assert ask(box).status_code == 429
    assert [poll(box, one).json()["state"] for one in mine] == ["waiting", "waiting"]


def test_a_made_up_forwarded_for_buys_no_room_without_a_trusted_proxy(client: TestClient, operator: Account,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Anybody can write ``X-Forwarded-For``. From a peer nexbooks was not told to believe, only the peer counts: a header
    with a new address each time does not open a new place (it did: a hundred a peer, three peers filled the server)."""
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    with coming_from("203.0.113.40") as peer:
        for number in range(40):
            answer = peer.post("/api/app/1/pairing/request", json={"device_name": "TV"},
                               headers={"X-Forwarded-For": f"198.51.100.{number}"})
            assert answer.status_code == 201, (number, answer.text)
        assert len(open_ones()) == device_request.OPEN_PER_SENDER, "40 headers, 10 places"
        # The same peer without the header is the same sender.
        assert peer.post("/api/app/1/pairing/request", json={}).status_code == 201
        assert len(open_ones()) == device_request.OPEN_PER_SENDER
    with coming_from("203.0.113.41") as other:
        assert other.post("/api/app/1/pairing/request", json={}).status_code == 201
    assert len(open_ones()) == device_request.OPEN_PER_SENDER + 1


def test_three_peers_with_made_up_headers_cannot_fill_the_server(client: TestClient, operator: Account,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """With the real limits (10 a sender, 300 in all): three peers, each with a different header every time, hold 30 codes,
    not 300, and a television still gets its code."""
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    for peer_address in ("203.0.113.50", "203.0.113.51", "203.0.113.52"):
        with coming_from(peer_address) as peer:
            for number in range(130):
                answer = peer.post("/api/app/1/pairing/request", json={},
                                   headers={"X-Forwarded-For": f"192.0.2.{number % 250}"})
                assert answer.status_code == 201
    assert len(open_ones()) == 3 * device_request.OPEN_PER_SENDER
    assert device_request.OPEN_PER_SENDER == 10 and device_request.OPEN_TOTAL == 300
    with coming_from("203.0.113.99") as television:
        assert television.post("/api/app/1/pairing/request", json={}).status_code == 201


def test_the_real_limits_are_ten_a_sender_and_three_hundred_in_all(client: TestClient, operator: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """No monkeypatch of the limits: 300 open codes are made straight in the table (one a sender), and the next sender is
    turned away; one more from a sender that holds ten gets the place of its oldest."""
    from app import clock

    now = clock.now()
    with SessionLocal() as db:
        for number in range(device_request.OPEN_TOTAL):
            db.add(AppRequest(id=f"r{number:04d}", secret_hash="s", code_hash=f"c{number:04d}", device_name="TV",
                              state="waiting", sender=f"sender-{number}", created_at=now, expires_at=now + device_request.LIFETIME))
        db.commit()
    from_address(monkeypatch, "203.0.113.200")
    wall = ask(app_client())
    assert (wall.status_code, code_of(wall)) == (429, "too_many_requests")
    assert len(open_ones()) == 300
    with SessionLocal() as db:
        for number in range(device_request.OPEN_PER_SENDER):
            db.execute(AppRequest.__table__.update().where(AppRequest.id == f"r{number:04d}").values(
                sender=device_request.Sender.of("addr:203.0.113.200").address, created_at=now))
        db.commit()
    assert ask(app_client()).status_code == 201
    assert len(open_ones()) == 300


def test_with_a_trusted_proxy_the_sender_is_the_one_the_proxy_names(client: TestClient, operator: Account,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", "10.9.0.0/16")
    with coming_from("10.9.0.2") as proxy:
        for number in range(30):
            # Two homes behind the proxy: 198.51.100.5 and 198.51.100.6 (what a sender wrote to the left of them is its own).
            home = 5 + number % 2
            answer = proxy.post("/api/app/1/pairing/request", json={},
                                headers={"X-Forwarded-For": f"203.0.113.{number}, 198.51.100.{home}"})
            assert answer.status_code == 201
    assert len({row.sender for row in open_ones()}) == 2
    assert len(open_ones()) == 2 * device_request.OPEN_PER_SENDER
    # A peer that is not the proxy cannot write the sender: its own address counts.
    with coming_from("203.0.113.60") as stranger:
        for number in range(15):
            stranger.post("/api/app/1/pairing/request", json={}, headers={"X-Forwarded-For": f"198.51.100.{number}"})
    assert len(open_ones()) == 3 * device_request.OPEN_PER_SENDER


def test_where_a_request_came_from_is_kept_as_a_salted_hash_and_only_to_count(client: TestClient, operator: Account,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from_address(monkeypatch, "203.0.113.55")
    box = app_client()
    asked(box)
    asked(box)
    from_address(monkeypatch, "203.0.113.56")
    asked(box)
    found = rows()
    for row in found:
        assert re.fullmatch(r"[0-9a-f]{64}", row.sender)
        assert "203.0.113" not in row.sender and row.proxy is None
    assert found[0].sender == found[1].sender != found[2].sender
    # Another start has another salt: the same address looks different.
    monkeypatch.setattr(device_request, "_SALT", b"another start")
    from_address(monkeypatch, "203.0.113.55")
    asked(box)
    assert rows()[-1].sender != found[0].sender


def test_wrong_secrets_rest_the_sender_from_guessing_and_nothing_else(client: TestClient, operator: Account) -> None:
    """Five wrong secrets, then a quarter of an hour of rest from guessing. A secret that fits is not turned away (it cannot
    be guessed), and neither a device that exists nor the sign-in of the same address are touched."""
    token = client.post("/api/v1/devices", json={"name": "script"}).json()["token"]
    made = asked()
    box = app_client()
    for number in range(5):
        wrong = poll(box, made, f"wrong-{number}")
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    resting = poll(box, made, "wrong-5")
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    assert int(resting.headers["retry-after"]) > 0
    unknown = poll(box, {"id": "A" * 22}, "wrong-6")
    assert (unknown.status_code, code_of(unknown)) == (429, "too_many_attempts"), "an unknown id rests as well"
    assert poll(box, made).json() == {"state": "waiting"}, "the right secret is not turned away by the rest"
    assert box.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    login = box.post("/api/v1/auth/login", json={"name": "tester", "password": "correct horse battery"}, headers=TAB)
    assert login.status_code == 200, login.text
    # The television's mistakes are a count of its own: the person who types at that address waits for nothing.
    seen = look_up(client, made["code"])
    assert seen.status_code == 200, seen.text
    assert confirm(client, made).status_code == 200
    assert poll(box, made).status_code == 200


def test_a_made_up_forwarded_for_buys_no_more_wrong_secrets_than_the_count_of_the_proxy(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The counts of failures (``deps.address_keys``) are meant to tell senders behind a proxy apart, and are bounded: a header
    with a new address every time opens a new small count (5 each) but the proxy as a whole stops at ``UNKNOWN_PROXY_FREE``.
    So there a made-up header cannot make the guessing of a secret cheaper than that, unlike the number of open codes."""
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    made = asked()
    answers = []
    with coming_from("203.0.113.70") as guesser:
        for number in range(deps.UNKNOWN_PROXY_FREE + 6):
            answers.append(poll_with_header(guesser, made, f"wrong-{number}", f"192.0.2.{number}").status_code)
    assert answers.count(404) == deps.UNKNOWN_PROXY_FREE and set(answers[deps.UNKNOWN_PROXY_FREE:]) == {429}, answers


def poll_with_header(box: TestClient, made: dict[str, Any], secret: str, forwarded: str) -> Any:
    return box.get(f"/api/app/1/pairing/request/{made['id']}",
                   headers={"X-Pairing-Secret": secret, "X-Forwarded-For": forwarded})


@pytest.mark.parametrize("known", [False, True])
def test_a_guess_at_an_unknown_id_and_one_at_a_known_id_cost_the_sender_the_same(client: TestClient,
                                                                                 operator: Account,
                                                                                 known: bool) -> None:
    made = asked()
    ident = {"id": made["id"] if known else "A" * 22}
    box = app_client()
    for number in range(5):
        assert code_of(poll(box, ident, f"wrong-{number}")) == "pairing_unknown"
    sixth = poll(box, ident, "wrong-5")
    assert (sixth.status_code, code_of(sixth)) == (429, "too_many_attempts")


def test_the_televisions_count_is_its_own_not_the_typing_nor_the_qr_code_nor_the_sign_in(
        client: TestClient, operator: Account) -> None:
    """Three counts, none touching the others: the television that asks how its request stands (``pairtv``), the person who
    types a code in the browser and the app that fetches with a code (``pair``), and the sign-in."""
    made = asked()
    box = app_client()
    for _ in range(5):
        assert code_of(claim_code(box, "ABCD-2347")) == "pairing_unknown"
    assert code_of(claim_code(box, "ABCD-2347")) == "too_many_attempts", "the app is at rest"
    assert code_of(poll(box, made, "wrong")) == "pairing_unknown", "the television is not"
    assert poll(box, made).json() == {"state": "waiting"}
    for number in range(4):
        assert code_of(poll(box, made, f"wrong-{number}")) == "pairing_unknown"
    assert code_of(poll(box, made, "wrong-5")) == "too_many_attempts", "now the television is at rest"
    assert code_of(claim_code(box, "ABCD-2347")) == "too_many_attempts", "(and the app still)"
    assert poll(box, made).json() == {"state": "waiting"}
    brake.forget()
    other = app_client()
    for _ in range(5):
        assert other.post("/api/v1/auth/login", json={"name": "nobody", "password": "x" * 14}, headers=TAB
                          ).status_code == 401
    assert code_of(poll(other, made, "wrong")) == "pairing_unknown", "wrong passwords spend nothing of the television's count"
    assert code_of(claim_code(other, "ABCD-2347")) == "pairing_unknown", "nor of the app's"


def test_the_mistakes_of_a_television_do_not_keep_a_person_from_typing_and_the_other_way_round(
        client: TestClient, operator: Account) -> None:
    """The same address for the television and the browser: five wrong secrets of the television, and the person still types
    the right code (and wrong ones of the person's do not rest the television)."""
    made = asked()
    box = app_client()
    for number in range(6):
        poll(box, made, f"wrong-{number}")
    assert code_of(poll(box, made, "wrong-x")) == "too_many_attempts"
    assert look_up(client, made["code"]).status_code == 200
    assert confirm(client, made).status_code == 200
    brake.forget()
    third = asked()
    for _ in range(5):
        assert code_of(look_up(client, "ZZZZ-2222")) == "pairing_unknown"
    assert code_of(look_up(client, "ZZZZ-2222")) == "too_many_attempts", "the person is at rest"
    for number in range(5):
        assert poll(box, third, f"wrong-{number}").status_code == 404, "the television's count is not spent by the typing"


def test_a_wrong_secret_that_is_missing_is_a_mistake_and_no_guess(client: TestClient, operator: Account) -> None:
    made = asked()
    box = app_client()
    for _ in range(12):
        answer = box.get(f"/api/app/1/pairing/request/{made['id']}")
        assert (answer.status_code, code_of(answer)) == (422, "invalid_input")
        too_long = poll(box, made, "x" * 201)
        assert too_long.status_code == 422
    assert poll(box, made).status_code == 200


def test_a_web_page_cannot_ask_or_use_up_the_attempts_of_a_visitor(client: TestClient, operator: Account) -> None:
    made = asked()
    box = app_client()
    page = {"Origin": "https://evil.example.net"}
    for _ in range(8):
        answer = box.post("/api/app/1/pairing/request", json={"device_name": "x"}, headers=page)
        assert (answer.status_code, code_of(answer)) == (403, "origin_refused")
        guess = box.get(f"/api/app/1/pairing/request/{made['id']}", headers={**page, "X-Pairing-Secret": "guess"})
        assert (guess.status_code, code_of(guess)) == (403, "origin_refused")
    again = box.get(f"/api/app/1/pairing/request/{made['id']}", headers={**page, "X-Pairing-Secret": made["secret"]})
    assert again.status_code == 403, "not even the right secret from a page"
    assert poll(box, made).status_code == 200, "the visitor's address was not counted"
    assert len(rows()) == 1


def test_the_device_needs_no_tab_header_a_browser_does(client: TestClient, operator: Account) -> None:
    browser = app_client()
    browser.cookies.set("nexbooks_session", "something")
    refused = browser.post("/api/app/1/pairing/request", json={"device_name": "x"})
    assert (refused.status_code, code_of(refused)) == (400, "client_required")
    assert app_client().post("/api/app/1/pairing/request", json={"device_name": "x"}).status_code == 201
    made = asked()
    nobody = TestClient(client.app, base_url="http://testserver")
    nobody.cookies.set("nexbooks_session", "something")
    for method, path, body in (("post", "/api/v1/devices/pair-requests/lookup", {"code": made["code"]}),
                               ("post", f"/api/v1/devices/pair-requests/{made['id']}/confirm", {"code": made["code"]}),
                               ("delete", f"/api/v1/devices/pair-requests/{made['id']}", None)):
        answer = getattr(nobody, method)(path, **({"json": body} if body else {}))
        assert (answer.status_code, code_of(answer)) == (400, "client_required"), path


def test_a_body_is_limited_before_it_is_read(client: TestClient, operator: Account) -> None:
    box = app_client()
    big = box.post("/api/app/1/pairing/request", content=b'{"device_name": "' + b"x" * 5000 + b'"}',
                   headers={"content-type": "application/json"})
    assert (big.status_code, code_of(big)) == (413, "too_large")
    assert rows() == []


# --- The person in the browser ------------------------------------------------------------------------------------------


def test_the_codes_need_a_signed_in_browser_and_a_device_does_not_confirm(client: TestClient,
                                                                         operator: Account) -> None:
    made = asked()
    mine = make(client)
    token = pairing_token(client, mine)
    bearer = {"Authorization": f"Bearer {token}"}
    paths = (("post", "/api/v1/devices/pair-requests/lookup", {"code": made["code"]}),
             ("post", f"/api/v1/devices/pair-requests/{made['id']}/confirm", {"code": made["code"]}),
             ("get", f"/api/v1/devices/pair-requests/{made['id']}", None),
             ("delete", f"/api/v1/devices/pair-requests/{made['id']}", None))
    for method, path, body in paths:
        answer = getattr(app_client(), method)(path, headers=bearer, **({"json": body} if body else {}))
        assert (answer.status_code, code_of(answer)) == (403, "browser_only"), path
    nobody = app_client()
    nobody.headers["X-Nexbooks-Client"] = "tab-nobody000"
    for method, path, body in paths:
        answer = getattr(nobody, method)(path, **({"json": body} if body else {}))
        assert answer.status_code == 401, path
    assert next(row for row in rows() if row.id == made["id"]).state == "waiting"


def pairing_token(client: TestClient, made: dict[str, Any]) -> str:
    return str(app_client().post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"]}).json()["token"])


def test_whoever_must_set_up_a_second_factor_cannot_confirm(client: TestClient, operator: Account) -> None:
    made = asked()
    require_second_factor(True)
    for answer in (look_up(client, made["code"]), confirm(client, made)):
        assert (answer.status_code, code_of(answer)) == (403, "second_factor_setup_required")
    assert next(row for row in rows() if row.id == made["id"]).state == "waiting"
    require_second_factor(False)
    assert confirm(client, made).status_code == 200


def test_a_second_factor_asked_for_after_the_confirmation_stops_the_device_until_it_is_set_up(
        client: TestClient, operator: Account) -> None:
    made = asked()
    confirm(client, made)
    token = poll(app_client(), made).json()["token"]
    require_second_factor(True)
    waiting = app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert (waiting.status_code, code_of(waiting)) == (403, "second_factor_setup_required")


def test_wrong_codes_typed_in_the_browser_rest_the_typist_also_for_the_right_code(client: TestClient,
                                                                                 operator: Account) -> None:
    made = asked()
    token = client.post("/api/v1/devices", json={"name": "script"}).json()["token"]
    for number in range(5):
        wrong = look_up(client, f"ABCD-234{number + 3}")
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    resting = look_up(client, made["code"])
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts") and int(resting.headers["retry-after"]) > 0
    again = confirm(client, made)
    assert (again.status_code, code_of(again)) == (429, "too_many_attempts"), "a code can be guessed: not even the right one"
    assert next(row for row in rows() if row.id == made["id"]).state == "waiting"
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert poll(app_client(), made).json() == {"state": "waiting"}, "the device's right secret is not turned away"


def test_a_person_who_changes_addresses_is_still_one_typist(client: TestClient, operator: Account,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    made = asked()
    for number in range(5):
        typed_from(monkeypatch, f"203.0.113.{number}")
        assert code_of(look_up(client, f"ABCD-234{number + 3}")) == "pairing_unknown"
    typed_from(monkeypatch, "203.0.113.99")
    resting = look_up(client, made["code"])
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    with new_client(make_account("noor")) as noor:
        assert look_up(noor, made["code"]).status_code == 200, "another person at a fresh address is free"


def test_over_all_typists_only_so_many_wrong_codes_pass_and_the_other_way_is_not_touched(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pairing, "CODE_GUESSES", 3)
    made = asked()
    other_way = make(client)
    people = [new_client(make_account(f"typist{number}")) for number in range(3)]
    try:
        for number, person in enumerate(people):
            typed_from(monkeypatch, f"203.0.113.{number}")
            assert code_of(look_up(person, f"ABCD-234{number + 3}")) == "pairing_unknown"
        typed_from(monkeypatch, "203.0.113.50")
        spent = look_up(client, made["code"])
        assert (spent.status_code, code_of(spent)) == (429, "too_many_codes"), "also for the right code"
        assert int(spent.headers["retry-after"]) > 0 and spent.json()["detail"]["retry_after"] > 0
        assert poll(app_client(), made).json() == {"state": "waiting"}, "the secret of the device goes on"
        # The code typed in the app (the other way) has its own count: it is not spent by this, nor does it spend this.
        assert claim_code(app_client(), other_way["code"]).status_code == 200
    finally:
        for person in people:
            person.close()
    pairing.code_watch.forget()
    device_request.lookup_watch.forget()
    for number in range(3):
        assert code_of(claim_code(app_client(), f"ABCD-2{number + 3}45")) in ("pairing_unknown", "too_many_attempts")
    typed_from(monkeypatch, "203.0.113.60")
    assert look_up(client, made["code"]).status_code == 200, "typing in the browser is not stopped by the app's guesses"


def test_a_right_code_gives_its_place_back_and_a_text_that_cannot_be_a_code_costs_none(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pairing, "CODE_GUESSES", 2)
    made = asked()
    for _ in range(6):
        assert look_up(client, made["code"]).status_code == 200
    for odd in ("", "A", "ABCD-234", "ABCD-23456", "ABCD-234O", "ABCD-2341", "ÄBCD-2345", "AB\nCD2345", "x" * 200):
        answer = look_up(client, odd)
        assert answer.status_code == 422 and code_of(answer) in ("pairing_code", "invalid_input"), odd
    assert code_of(look_up(client, "ABCD-2345")) == "pairing_unknown"
    assert look_up(client, made["code"]).status_code == 200


def test_the_code_is_compared_as_hashes_in_constant_time(client: TestClient, operator: Account,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bytes, bytes]] = []

    def spy(first: bytes, second: bytes) -> bool:
        seen.append((first, second))
        return real_hmac.compare_digest(first, second)

    monkeypatch.setattr(device_request, "hmac", SimpleNamespace(compare_digest=spy, new=real_hmac.new))
    made = asked()
    box = app_client()
    poll(box, {"id": "N" * 22}, "nobody")
    poll(box, made, "wrong")
    look_up(client, "ZZZZ-2222")
    assert len(seen) == 3, "one comparison for each guess, whatever it hits"
    assert all(len(first) == len(second) == 64 for first, second in seen)
    assert seen[1][1] == pairing.hash_secret("wrong").encode() and seen[1][0] == pairing.hash_secret(made["secret"]).encode()
    seen.clear()
    assert poll(box, made).json() == {"state": "waiting"}
    assert look_up(client, made["code"]).status_code == 200
    assert confirm(client, made).status_code == 200
    plain = made["code"].replace("-", "").encode()
    flat = {item for pair in seen for item in pair}
    assert pairing.hash_secret(made["secret"]).encode() in flat and pairing.hash_code(made["code"].replace("-", "")).encode() in flat
    assert made["secret"].encode() not in flat and plain not in flat and made["id"].encode() in flat


def test_nothing_secret_is_ever_handed_to_the_log_not_even_to_be_blotted_out(client: TestClient, operator: Account,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The log blots out device tokens it finds in a line (``logs.redact``). This looks one step earlier: what the service
    hands to the logger in the first place."""
    handed: list[str] = []
    monkeypatch.setattr(device_request, "logger", SimpleNamespace(
        info=lambda text, *args: handed.append(text % args), warning=lambda text, *args: handed.append(text % args)))
    box = app_client()
    made = asked(box, "Den's TV")
    look_up(client, made["code"])
    confirm(client, made)
    token = poll(box, made).json()["token"]
    withdrawn = asked(box)
    confirm(client, withdrawn)
    client.delete(f"/api/v1/devices/pair-requests/{withdrawn['id']}")
    assert handed == ["A device request was confirmed by tester", "A device of tester was paired by code: Den's TV",
                      "A device request was confirmed by tester", "A device request was taken back by tester"]
    flat = " ".join(handed)
    for secret in (made["secret"], made["code"], made["code"].replace("-", ""), token, token[4:20],
                   pairing.hash_secret(made["secret"]), devices.digest(token), made["id"]):
        assert secret not in flat, secret[:6]


def test_nothing_secret_reaches_the_log_and_the_token_is_in_one_answer_only(client: TestClient,
                                                                            operator: Account) -> None:
    texts: list[str] = []
    logs.set_mode("trace", 30)
    try:
        box = app_client()
        made = asked(box, "Den's TV")
        texts += [ask(box, "second").text, poll(box, made).text, poll(box, made, "guess-one").text,
                  look_up(client, made["code"]).text, look_up(client, "ZZZZ-2222").text, confirm(client, made).text,
                  standing(client, made).text]
        fetched = poll(box, made)
        token = fetched.json()["token"]
        texts += [standing(client, made).text, client.get("/api/v1/devices").text, poll(box, made).text]
        withdrawn = asked(box)
        confirm(client, withdrawn)
        texts.append(client.delete(f"/api/v1/devices/pair-requests/{withdrawn['id']}").text)
        assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        logs.set_mode(logs.DEFAULT_MODE)
    for handler in logging.getLogger().handlers:
        handler.flush()
    log = "".join(path.read_text(encoding="utf-8", errors="replace")
                  for path in logs.log_file().parent.glob(logs.log_file().name + "*"))
    assert "A device of tester was paired by code: Den's TV" in log
    for secret in (made["secret"], made["code"], made["code"].replace("-", ""), token, token[4:20], withdrawn["secret"],
                   withdrawn["code"], withdrawn["code"].replace("-", ""), pairing.hash_secret(made["secret"]),
                   pairing.hash_code(made["code"].replace("-", "")), devices.digest(token), "guess-one"):
        assert secret not in log, secret[:6]
    for text in texts:
        assert not TOKEN.search(text) and made["secret"] not in text
    assert hashlib.sha256(b"x").hexdigest() not in log


# --- Between the look and the taking --------------------------------------------------------------------------------------


def test_of_two_polls_that_looked_at_the_same_moment_the_second_one_takes_nothing(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The conditions of the taking are part of the statement: should another poll fetch the token between the look and the
    taking (here: right in the middle, exactly), the first one takes nothing, makes no device and hears ``pairing_used``."""
    box = app_client()
    made = asked(box)
    assert confirm(client, made).status_code == 200
    real = device_request._may_pair
    inner: list[Any] = []

    def meanwhile(db: Any, row: Any) -> Any:
        if not inner:
            inner.append(None)  # the other poll comes through here too: once only
            with SessionLocal() as other:
                inner[0] = device_request.poll(other, made["id"], made["secret"])
        return real(db, row)

    monkeypatch.setattr(device_request, "_may_pair", meanwhile)
    answer = poll(box, made)
    assert (answer.status_code, code_of(answer)) == (410, "pairing_used")
    assert inner and inner[0].state == "confirmed" and inner[0].claimed is not None
    assert device_count() == 1, "exactly one token came into being"


def test_a_request_that_runs_out_between_the_look_and_the_taking_gives_nothing(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    box = app_client()
    made = asked(box)
    assert confirm(client, made).status_code == 200
    real = device_request._may_pair

    def late(db: Any, row: Any) -> Any:
        moving.pass_by(minutes=11)
        return real(db, row)

    monkeypatch.setattr(device_request, "_may_pair", late)
    answer = poll(box, made)
    assert (answer.status_code, answer.json()) == (200, {"state": "expired"})
    assert device_count() == 0
    assert next(row for row in rows() if row.id == made["id"]).state == "confirmed"


def test_the_salt_that_hides_where_a_request_came_from_is_new_with_every_start() -> None:
    """Two starts of the server (the module made twice) have two salts, so that the hashes of one start tell nothing of the
    next: nothing ties a request to an address for longer than the server runs."""
    import importlib.util

    salts = []
    for number in range(2):
        spec = importlib.util.spec_from_file_location(f"app.services.device_request_start{number}", device_request.__file__)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # a dataclass looks its module up while it is made
        try:
            spec.loader.exec_module(module)
        finally:
            del sys.modules[spec.name]
        salts.append(module._SALT)
    assert salts[0] != salts[1] and all(len(salt) == 16 for salt in salts)


def test_the_answers_that_carry_a_token_say_no_store_themselves(operator: Account) -> None:
    """The wall around the app puts ``no-store`` on every answer of ``/api``; the routes say it too, so that it holds
    without that wall (a proxy in front of a changed setup, a route moved)."""
    from fastapi import FastAPI

    bare = FastAPI()
    bare.include_router(app1_router.router)
    with TestClient(bare) as box:
        made = box.post("/api/app/1/pairing/request", json={"device_name": "TV"})
        assert made.status_code == 201 and made.headers["cache-control"] == "no-store"
        secret = {"X-Pairing-Secret": made.json()["secret"]}
        waiting = box.get(f"/api/app/1/pairing/request/{made.json()['id']}", headers=secret)
        assert waiting.status_code == 200 and waiting.headers["cache-control"] == "no-store"
        with SessionLocal() as db:
            row = db.get(AppRequest, made.json()["id"])
            assert row is not None
            row.state, row.account_id = "confirmed", operator.id
            db.commit()
        fetched = box.get(f"/api/app/1/pairing/request/{made.json()['id']}", headers=secret)
        assert fetched.status_code == 200 and "token" in fetched.json()
        assert fetched.headers["cache-control"] == "no-store"
        other = box.post("/api/app/1/pairing/request", json={})
        gone = box.delete(f"/api/app/1/pairing/request/{other.json()['id']}", headers={"X-Pairing-Secret": other.json()["secret"]})
        assert gone.status_code == 204 and gone.headers["cache-control"] == "no-store"


def test_the_code_of_a_request_is_never_given_twice_at_the_same_time(client: TestClient, operator: Account,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Should a new code be the one of a request that is still there, another is made (the hash is unique)."""
    wanted = iter(["AAAAAAAA", "AAAAAAAA", "BBBBBBBB"])
    monkeypatch.setattr(device_request, "_new_code", lambda: next(wanted))
    box = app_client()
    first, second = asked(box), asked(box)
    assert (first["code"], second["code"]) == ("AAAA-AAAA", "BBBB-BBBB")


# --- The device lets its request go ---------------------------------------------------------------------------------


def test_the_device_lets_its_request_go_and_the_place_comes_back(client: TestClient, operator: Account,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    from_address(monkeypatch, "203.0.113.5")
    box = app_client()
    made = []
    for _ in range(device_request.OPEN_PER_SENDER):
        made.append(asked(box))
        moving.pass_by(seconds=1)
    gone = cancel(box, made[5])
    assert gone.status_code == 204 and gone.headers["cache-control"] == "no-store" and gone.content == b""
    assert len(open_ones()) == device_request.OPEN_PER_SENDER - 1, "its place came back"
    assert asked(box)
    assert poll(box, made[0]).json() == {"state": "waiting"}, "the newcomer found a free place: no oldest ran out"
    assert poll(box, made[5]).json() == {"state": "expired"}
    ran_out = look_up(client, made[5]["code"])
    assert (ran_out.status_code, code_of(ran_out)) == (410, "pairing_expired")
    for _ in range(3):
        assert cancel(box, made[5]).status_code == 204, "again and again for the right secret"
    assert [row.state for row in rows() if row.id == made[5]["id"]] == ["waiting"]


def test_a_request_the_device_lets_go_after_the_confirmation_gives_nothing(client: TestClient, operator: Account) -> None:
    box = app_client()
    made = asked(box)
    assert confirm(client, made).status_code == 200
    assert cancel(box, made).status_code == 204
    assert poll(box, made).json() == {"state": "expired"} and device_count() == 0
    assert standing(client, made).status_code == 404


def test_a_request_that_was_fetched_is_not_touched_by_letting_it_go(client: TestClient, operator: Account) -> None:
    box = app_client()
    made = asked(box)
    confirm(client, made)
    token = poll(box, made).json()["token"]
    assert cancel(box, made).status_code == 204
    assert next(row for row in rows() if row.id == made["id"]).state == "used"
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert code_of(poll(box, made)) == "pairing_used"


def test_letting_go_is_always_204_and_never_counts_and_only_the_right_secret_lets_go(
        client: TestClient, operator: Account) -> None:
    box = app_client()
    made = asked(box)
    for number in range(40):
        wrong = cancel(box, made, f"wrong-{number}")
        assert wrong.status_code == 204 and wrong.content == b"" and wrong.headers["cache-control"] == "no-store"
    for odd in ({"id": "A" * 22}, {"id": "x"}, {"id": "A" * 200}):
        assert cancel(box, odd, "x" * 43).status_code == 204, "an id the server forgot long ago"
    assert poll(box, made).json() == {"state": "waiting"}, "nothing was let go by a wrong secret"
    # Not a single one of them was counted against the sender: the television and the person still have all their tries.
    for number in range(5):
        assert code_of(poll(box, made, f"wrong-{number}")) == "pairing_unknown"
    assert look_up(client, made["code"]).status_code == 200
    brake.forget()
    assert cancel(box, made).status_code == 204
    assert poll(box, made).json() == {"state": "expired"}
    assert box.delete(f"/api/app/1/pairing/request/{made['id']}").status_code == 422, "no header: a mistake of the program"


def test_a_web_page_cannot_let_a_request_go(client: TestClient, operator: Account) -> None:
    made = asked()
    page = {"Origin": "https://evil.example.net", "X-Pairing-Secret": made["secret"]}
    answer = app_client().delete(f"/api/app/1/pairing/request/{made['id']}", headers=page)
    assert (answer.status_code, code_of(answer)) == (403, "origin_refused")
    browser = app_client()
    browser.cookies.set("nexbooks_session", "something")
    asked_by_tab = browser.delete(f"/api/app/1/pairing/request/{made['id']}", headers={"X-Pairing-Secret": made["secret"]})
    assert (asked_by_tab.status_code, code_of(asked_by_tab)) == (400, "client_required")
    assert poll(app_client(), made).json() == {"state": "waiting"}


def test_features_name_the_request_and_the_description_names_the_routes() -> None:
    from app.main import app

    assert "pairing_request" in app1_router.FEATURES
    paths = app.openapi()["paths"]
    assert set(paths["/api/app/1/pairing/request"]) == {"post"}
    assert set(paths["/api/app/1/pairing/request/{request_id}"]) == {"get", "delete"}
    assert set(paths["/api/v1/devices/pair-requests/lookup"]) == {"post"}
    assert set(paths["/api/v1/devices/pair-requests/{request_id}/confirm"]) == {"post"}
    assert set(paths["/api/v1/devices/pair-requests/{request_id}"]) == {"get", "delete"}
