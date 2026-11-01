"""Confirming a device without a camera from the iPhone app: the television asked for a code and
hands its id and its code to the app of the person on the sofa; the app confirms with its own device token. It is the
same decision as typing the code in the browser (one service); the television fetches a token of its own, once, with the
secret that only it knows. Both the id and the code must fit; wrong pairs count like wrong codes typed in the browser; a
right pair is not turned away by what others tried; nothing secret reaches the log."""

from __future__ import annotations

import hmac as real_hmac
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, AppRequest, Device, utcnow
from app.routers.app1 import FEATURES
from app.security import brake
from app.services import apikeys, device_request, devices, logs, pairing

from .app_helpers import app_of, device_token
from .conftest import make_account, new_client, require_second_factor
from .pairing_helpers import Moving, app_client, asked, cancel, confirm, confirm_from_phone, look_up, poll, standing
from .test_device_request import TOKEN, code_of, device_count, rows, typed_from

PATH = "/api/app/1/pairing/request/confirm"


def phone_of(account: Account, name: str = "Ada's iPhone") -> TestClient:
    return app_of(device_token(account, name))


def row_of(made: dict[str, Any]) -> AppRequest:
    return next(row for row in rows() if row.id == made["id"])


def devices_of(account: Account) -> list[str]:
    with new_client(account) as browser:
        return sorted(item["name"] for item in browser.get("/api/v1/devices").json())


def wrong_ones(phone: TestClient, number: int, start: int = 0) -> list[Any]:
    """Wrong pairs for ids nobody made (so that no request is hit more than once)."""
    return [phone.post(PATH, json={"id": f"N{start + each:021d}", "code": "ZZZZ-2222"}) for each in range(number)]


# --- The way ----------------------------------------------------------------------------------------------------------


def test_the_phone_confirms_and_the_television_fetches_a_token_of_its_own(client: TestClient,
                                                                         operator: Account) -> None:
    ada = make_account("ada")
    mine = device_token(ada, "Ada's iPhone")
    phone = app_of(mine)
    box = app_client()
    made = asked(box, "  Living   room TV ")
    assert poll(box, made).json() == {"state": "waiting"}
    done = confirm_from_phone(phone, made)
    assert done.status_code == 200, done.text
    assert done.headers["cache-control"] == "no-store"
    body = done.json()
    assert set(body) == {"state", "device_name", "expires_at"}
    assert (body["state"], body["device_name"]) == ("confirmed", "Living room TV")
    assert datetime.fromisoformat(body["expires_at"]) == datetime.fromisoformat(made["expires_at"])
    assert not TOKEN.search(done.text) and made["secret"] not in done.text
    # Only the decision is made: no device for the television yet, and it is the account of the token that decided.
    decided = row_of(made)
    assert (decided.state, decided.account_id, decided.device_id) == ("confirmed", ada.id, None)
    assert device_count() == 1 and devices_of(ada) == ["Ada's iPhone"]
    # The television fetches, and what it takes home is a token of its own (not the phone's).
    got = poll(box, made)
    assert got.status_code == 200, got.text
    assert got.headers["cache-control"] == "no-store"
    token = got.json()["token"]
    assert TOKEN.fullmatch(token) and token != mine
    assert got.json()["account"] == {"name": "ada"} and got.json()["server"]["version"]
    assert devices_of(ada) == ["Ada's iPhone", "Living room TV"]
    me = app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["name"] == "ada"
    for again in (poll(box, made), confirm_from_phone(phone, made)):
        assert (again.status_code, code_of(again)) == (410, "pairing_used")
    # It is a device in the list like any other: revoked alone, the phone goes on.
    with new_client(ada) as browser:
        television = next(item for item in browser.get("/api/v1/devices").json() if item["name"] == "Living room TV")
        assert browser.delete(f"/api/v1/devices/{television['id']}").status_code == 204
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert phone.get("/api/app/1/me").status_code == 200


@pytest.mark.parametrize("write", [lambda c: c, lambda c: c.lower(), lambda c: c.replace("-", ""),
                                   lambda c: c.replace("-", " ").lower(), lambda c: f"  {c.lower()}  ",
                                   lambda c: c[:2].lower() + c[2:]])
def test_the_code_may_be_written_in_any_case_with_or_without_the_dash(client: TestClient, operator: Account,
                                                                      write: Any) -> None:
    phone = phone_of(make_account("ada"))
    made = asked()
    answer = confirm_from_phone(phone, made, code=write(made["code"]))
    assert answer.status_code == 200, answer.text
    assert row_of(made).state == "confirmed"


def test_it_is_the_same_decision_as_in_the_browser_and_the_browser_sees_it(client: TestClient,
                                                                         operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    by_phone, by_browser = asked(), asked()
    assert confirm_from_phone(phone, by_phone).status_code == 200
    with new_client(ada) as browser:
        assert confirm(browser, by_browser).status_code == 200
        # What the browser asks about, it hears the same of a request the phone confirmed.
        assert standing(browser, by_phone).json() == {"state": "confirmed", "device": None}
        first, second = row_of(by_phone), row_of(by_browser)
        assert (first.state, first.account_id, first.device_id) == (second.state, second.account_id, second.device_id)
        assert first.confirmed_at is not None and second.confirmed_at is not None
        assert first.used_at is None and second.used_at is None
        # And the person can take it back there, as long as the television has not fetched.
        assert browser.delete(f"/api/v1/devices/pair-requests/{by_phone['id']}").status_code == 204
    assert poll(app_client(), by_phone).json() == {"state": "expired"}
    assert poll(app_client(), by_browser).json()["state"] == "confirmed"


def test_the_television_that_let_its_request_go_cannot_be_confirmed(client: TestClient, operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    box = app_client()
    made = asked(box)
    assert cancel(box, made).status_code == 204
    answer = confirm_from_phone(phone, made)
    assert (answer.status_code, code_of(answer)) == (410, "pairing_expired")
    assert device_count() == 1


def test_a_request_that_ran_out_is_expired_and_then_gone(client: TestClient, operator: Account,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    phone = phone_of(make_account("ada"))
    in_time = asked()
    moving.pass_by(minutes=9, seconds=59)
    assert confirm_from_phone(phone, in_time).status_code == 200, "the last second before it runs out"
    late = asked()
    moving.pass_by(minutes=10)
    answer = confirm_from_phone(phone, late)
    assert (answer.status_code, code_of(answer)) == (410, "pairing_expired")
    moving.pass_by(minutes=10)
    gone = confirm_from_phone(phone, late)
    assert (gone.status_code, code_of(gone)) == (404, "pairing_unknown"), "swept away: as if it never was"


def test_a_request_the_phone_confirmed_twice_is_used_and_nothing_more_happens(client: TestClient,
                                                                              operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    made = asked()
    assert confirm_from_phone(phone, made).status_code == 200
    again = confirm_from_phone(phone, made)
    assert (again.status_code, code_of(again)) == (410, "pairing_used")
    assert "token" not in again.text and row_of(made).state == "confirmed"
    assert device_count() == 1


def test_a_request_another_person_confirmed_looks_as_if_it_were_not_there(client: TestClient,
                                                                         operator: Account) -> None:
    ada, ben = make_account("ada"), make_account("ben")
    ada_phone, ben_phone = phone_of(ada), phone_of(ben, "Ben's iPhone")
    made = asked()
    assert confirm_from_phone(ada_phone, made).status_code == 200
    other = confirm_from_phone(ben_phone, made)
    assert (other.status_code, code_of(other)) == (404, "pairing_unknown")
    unknown = confirm_from_phone(ben_phone, made, request_id="N" * 22)
    assert other.json() == unknown.json(), "the same answer, to the word"
    assert row_of(made).account_id == ada.id
    # After the television fetched, too.
    poll(app_client(), made)
    assert code_of(confirm_from_phone(ben_phone, made)) == "pairing_unknown"
    assert code_of(confirm_from_phone(ada_phone, made)) == "pairing_used"
    assert devices_of(ben) == ["Ben's iPhone"]


def test_a_stranger_at_the_limit_of_devices_still_hears_nothing_of_a_request_that_is_taken(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer for somebody else's request does not depend on the state of the stranger's own account."""
    ada, ben = make_account("ada"), make_account("ben")
    ada_phone, ben_phone = phone_of(ada), phone_of(ben, "Ben's iPhone")
    made = asked()
    assert confirm_from_phone(ada_phone, made).status_code == 200
    monkeypatch.setattr(devices, "MAX_DEVICES", 1)
    answer = confirm_from_phone(ben_phone, made)
    assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")


def test_a_request_that_is_gone_is_unknown_even_to_an_account_at_the_limit(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    phone = phone_of(make_account("ada"))
    made = asked()
    moving.pass_by(minutes=21)
    monkeypatch.setattr(devices, "MAX_DEVICES", 1)
    answer = confirm_from_phone(phone, made)
    assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    assert [row.id for row in rows()] == [made["id"]], "not swept yet: the answer is the server's own judgement"


def test_a_confirmation_that_comes_between_the_look_and_the_taking_wins_and_the_late_one_hears_so(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The conditions of the taking are part of the statement: should somebody confirm the request right between the
    look and the taking, the late one takes nothing. Another person's confirmation: as if it were not there. The same
    person's (a second tap): used."""
    ada, ben = make_account("ada"), make_account("ben")
    ada_phone = phone_of(ada)
    real = device_request._decide
    meanwhile: list[Account] = []

    def before(db: Any, account: Account, row: Any, **kwargs: Any) -> Any:
        if meanwhile:
            who = meanwhile.pop()
            with SessionLocal() as other:
                found = other.get(Account, who.id)
                assert found is not None
                real(other, found, other.get(AppRequest, row.id), **{**kwargs, "hide_others": True})
        return real(db, account, row, **kwargs)

    monkeypatch.setattr(device_request, "_decide", before)
    stranger = asked()
    meanwhile.append(ben)
    late = confirm_from_phone(ada_phone, stranger)
    assert (late.status_code, code_of(late)) == (404, "pairing_unknown")
    assert row_of(stranger).account_id == ben.id
    twice = asked()
    meanwhile.append(ada)
    again = confirm_from_phone(ada_phone, twice)
    assert (again.status_code, code_of(again)) == (410, "pairing_used")
    assert row_of(twice).account_id == ada.id and device_count() == 1


# --- Both must fit ----------------------------------------------------------------------------------------------------


def test_the_id_and_the_code_must_both_fit_and_every_other_pair_looks_the_same(client: TestClient,
                                                                             operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    made, other = asked(), asked()
    wrong_pairs = (
        confirm_from_phone(phone, made, code="ZZZZ-2222"),            # the right id, a code of nobody
        confirm_from_phone(phone, made, code=other["code"]),          # the right id, the code of another request
        confirm_from_phone(phone, made, request_id=other["id"]),      # the id of another, the right code
        confirm_from_phone(phone, made, request_id="N" * 22),         # an id nobody made
        confirm_from_phone(phone, made, request_id="x" * 200),        # an id that is far too long
    )
    assert [(answer.status_code, code_of(answer)) for answer in wrong_pairs] == [(404, "pairing_unknown")] * 5
    assert len({answer.text for answer in wrong_pairs}) == 1, "one answer for unknown, wrong and somebody else's"
    assert [row.state for row in rows()] == ["waiting", "waiting"], "nothing was decided"
    assert confirm_from_phone(phone, made).status_code == 200
    assert row_of(other).state == "waiting"


def test_a_text_that_cannot_be_a_code_is_refused_and_costs_nothing(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    for odd in ("A", "ABCD-234", "ABCD-23456", "ABCD-234O", "ABCD-2341", "ÄBCD-2345", "AB\nCD2345", "x" * 60, " "):
        answer = confirm_from_phone(phone, made, code=odd)
        assert answer.status_code == 422 and code_of(answer) == "pairing_code", odd
    for _ in range(12):
        assert code_of(confirm_from_phone(phone, made, code="ABCD2")) == "pairing_code"
    assert brake.wait_seconds(f"pair-account:{ada.id}") == 0
    assert code_of(confirm_from_phone(phone, made, code="ZZZZ-2222")) == "pairing_unknown", "still no rest"
    assert confirm_from_phone(phone, made).status_code == 200


@pytest.mark.parametrize("body", [{}, {"id": "x"}, {"code": "ABCD-2345"}, {"id": "", "code": "ABCD-2345"},
                                  {"id": "x", "code": ""}, {"id": 7, "code": "ABCD-2345"}, {"id": ["x"], "code": "y"},
                                  {"id": "x" * 201, "code": "ABCD-2345"}, [1, 2]])
def test_a_body_that_is_not_an_id_and_a_code_is_invalid_input_and_costs_nothing(client: TestClient,
                                                                               operator: Account, body: Any) -> None:
    ada = make_account("ada")
    answer = phone_of(ada).post(PATH, json=body)
    assert answer.status_code == 422 and code_of(answer) == "invalid_input", answer.text
    assert brake.wait_seconds(f"pair-account:{ada.id}") == 0


def test_odd_ids_never_make_a_server_error(client: TestClient, operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    for raw in ('{"id": "\\ud800x", "code": "ABCD-2345"}', '{"id": "a\\u0000b", "code": "ABCD-2345"}',
                '{"id": "\\u00e4\\u00f6", "code": "ABCD-2345"}', '{"id": "ABCD-2345", "code": "ABCD-2345"}',
                '{"id": "../../x", "code": "ABCD-2345"}', "not json at all", ""):
        answer = phone.post(PATH, content=raw, headers={"Content-Type": "application/json"})
        assert answer.status_code in (404, 422), (raw, answer.status_code, answer.text)


# --- The brake --------------------------------------------------------------------------------------------------------


def test_five_wrong_pairs_rest_the_sender_and_a_right_pair_is_still_confirmed(client: TestClient,
                                                                             operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    made = asked()
    first = wrong_ones(phone, 5)
    assert [(answer.status_code, code_of(answer)) for answer in first] == [(404, "pairing_unknown")] * 5
    resting = wrong_ones(phone, 1, start=50)[0]
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    assert int(resting.headers["retry-after"]) > 0 and resting.json()["detail"]["retry_after"] > 0
    # What a guess cannot do, the right pair can: it is not turned away, as the television's right secret is not.
    done = confirm_from_phone(phone, made)
    assert done.status_code == 200, done.text
    assert poll(app_client(), made).json()["state"] == "confirmed"


def test_the_count_is_the_one_of_the_browser_and_is_not_the_televisions_nor_the_sign_in(
        client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    wrong_ones(phone, 5)
    # The typist in a browser at the same address rests too (one count of typed codes, per sender and per person) ...
    with new_client(ada) as browser:
        resting = look_up(browser, made["code"])
        assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    # ... but not the television that asks how it stands, nor the phone's own token.
    assert poll(app_client(), made).json() == {"state": "waiting"}
    assert phone.get("/api/app/1/me").status_code == 200
    # And the other way round: wrong codes typed in the browser rest the phone's wrong pairs the same.
    brake.forget()
    ben = make_account("ben")
    with new_client(ben) as browser:
        for number in range(5):
            assert code_of(look_up(browser, f"ABCD-234{number + 3}")) == "pairing_unknown"
    assert code_of(wrong_ones(phone_of(ben, "Ben's iPhone"), 1)[0]) == "too_many_attempts"


def test_a_person_who_changes_addresses_is_still_one_typist_and_a_right_pair_passes(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    phone = phone_of(make_account("ada"))
    made = asked()
    for number in range(5):
        typed_from(monkeypatch, f"203.0.113.{number}")
        assert code_of(wrong_ones(phone, 1, start=number)[0]) == "pairing_unknown"
    typed_from(monkeypatch, "203.0.113.99")
    resting = wrong_ones(phone, 1, start=60)[0]
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    ben_phone = phone_of(make_account("ben"), "Ben's iPhone")
    assert code_of(wrong_ones(ben_phone, 1, start=70)[0]) == "pairing_unknown", "another person at a fresh address"
    assert confirm_from_phone(phone, made).status_code == 200, "the right pair, also from the one who rests"


def test_over_all_senders_only_so_many_wrong_pairs_pass_and_a_right_pair_does_pass(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pairing, "CODE_GUESSES", 3)
    made = asked()
    people = [phone_of(make_account(f"typist{number}"), f"Phone {number}") for number in range(4)]
    for number, phone in enumerate(people[:3]):
        typed_from(monkeypatch, f"203.0.113.{number}")
        assert code_of(wrong_ones(phone, 1, start=number)[0]) == "pairing_unknown"
    typed_from(monkeypatch, "203.0.113.50")
    spent = wrong_ones(people[3], 1, start=9)[0]
    assert (spent.status_code, code_of(spent)) == (429, "too_many_codes")
    assert int(spent.headers["retry-after"]) > 0 and spent.json()["detail"]["retry_after"] > 0
    typed_from(monkeypatch, "203.0.113.51")
    assert confirm_from_phone(people[3], made).status_code == 200, "a right pair is not a try"
    # It is the watch of the codes typed in the browser (one count of typed codes) and not the QR code's.
    with new_client(make_account("noor")) as browser:
        other = asked()
        assert code_of(look_up(browser, other["code"])) == "too_many_codes"
    assert pairing.code_watch.take()[1] == 0


def test_five_wrong_codes_against_one_request_rest_that_request_also_for_the_right_one(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whoever sees the id (it travels from the television to the phone and stands in the address it asks at) must not
    be able to try the 40 bits of its code without limit: after five wrong ones the request rests, whoever asks."""
    made, other = asked(), asked()
    people = [phone_of(make_account(f"typist{number}"), f"Phone {number}") for number in range(6)]
    for number, phone in enumerate(people[:5]):
        typed_from(monkeypatch, f"203.0.113.{number}")
        answer = confirm_from_phone(phone, made, code=f"ABCD-23{number + 4}5")
        assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    typed_from(monkeypatch, "203.0.113.77")
    resting = confirm_from_phone(people[5], made)
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    assert int(resting.headers["retry-after"]) > 0
    assert row_of(made).state == "waiting"
    # Another request is not touched by it.
    assert confirm_from_phone(people[5], other).status_code == 200


# --- Who may ----------------------------------------------------------------------------------------------------------


def test_only_a_device_token_confirms_not_a_key_a_cookie_or_nobody(client: TestClient, operator: Account) -> None:
    made = asked()
    with SessionLocal() as db:
        row = db.get(Account, operator.id)
        assert row is not None
        _key, key = apikeys.make(db, row, "script", "read")
    body = {"id": made["id"], "code": made["code"]}
    seen = {
        "token_wrong_kind": app_client().post(PATH, json=body, headers={"Authorization": f"Bearer {key}"}),
        "device_token_required": client.post(PATH, json=body),
        "token_invalid": app_client().post(PATH, json=body, headers={"Authorization": "Bearer nbd_" + "q" * 43}),
    }
    for code, answer in seen.items():
        assert (answer.status_code, code_of(answer)) == (401, code), code
    # A program without any token, and without a tab, is a form of another site: turned away at the first wall.
    nobody = app_client().post(PATH, json=body)
    assert (nobody.status_code, code_of(nobody)) == (400, "client_required")
    assert row_of(made).state == "waiting" and device_count() == 0


def test_a_revoked_token_and_a_blocked_account_confirm_nothing(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    token = device_token(ada, "Lost phone")
    made = asked()
    with SessionLocal() as db:
        uid = db.scalar(select(Device.uid).where(Device.name == "Lost phone"))
        assert uid is not None and devices.revoke(db, ada.id, uid)
    answer = app_of(token).post(PATH, json={"id": made["id"], "code": made["code"]})
    assert (answer.status_code, code_of(answer)) == (401, "token_invalid")
    kept = device_token(ada, "Kept phone")
    with SessionLocal() as db:
        row = db.get(Account, ada.id)
        assert row is not None
        row.blocked_at = utcnow()
        db.commit()
    blocked = app_of(kept).post(PATH, json={"id": made["id"], "code": made["code"]})
    assert (blocked.status_code, code_of(blocked)) == (401, "token_invalid")
    assert row_of(made).state == "waiting"


def test_whoever_must_set_up_a_second_factor_cannot_confirm_until_it_is_set_up(client: TestClient,
                                                                             operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    require_second_factor(True)
    answer = confirm_from_phone(phone, made)
    assert (answer.status_code, code_of(answer)) == (403, "second_factor_setup_required")
    assert row_of(made).state == "waiting" and brake.wait_seconds(f"pair-account:{ada.id}") == 0
    require_second_factor(False)
    assert confirm_from_phone(phone, made).status_code == 200


def test_when_the_account_holds_too_many_devices_the_phone_hears_it_and_the_request_stays(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    spare = device_token(ada, "Spare")
    monkeypatch.setattr(devices, "MAX_DEVICES", 2)
    made = asked()
    full = confirm_from_phone(phone, made)
    assert (full.status_code, code_of(full)) == (409, "too_many_devices")
    assert row_of(made).state == "waiting"
    assert brake.wait_seconds(f"pair-account:{ada.id}") == 0
    with SessionLocal() as db:
        uid = db.scalar(select(Device.uid).where(Device.name == "Spare"))
        assert uid is not None and devices.revoke(db, ada.id, uid)
    assert app_of(spare).get("/api/app/1/me").status_code == 401
    assert confirm_from_phone(phone, made).status_code == 200


def test_the_television_hears_that_the_account_was_blocked_after_the_phone_confirmed(client: TestClient,
                                                                                    operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    box = app_client()
    made = asked(box)
    assert confirm_from_phone(phone, made).status_code == 200
    with SessionLocal() as db:
        row = db.get(Account, ada.id)
        assert row is not None
        row.blocked_at = utcnow()
        db.commit()
    assert poll(box, made).json() == {"state": "expired"} and device_count() == 1


def test_signing_everywhere_out_ends_a_confirmation_the_phone_made(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    assert confirm_from_phone(phone, made).status_code == 200
    with new_client(ada) as browser:
        assert browser.post("/api/v1/auth/logout-all").status_code == 204
    assert poll(app_client(), made).json() == {"state": "expired"}


# --- Races ------------------------------------------------------------------------------------------------------------


def test_the_phone_tapped_several_times_at_the_same_moment_confirms_once(client: TestClient,
                                                                        operator: Account) -> None:
    token = device_token(make_account("ada"), "Ada's iPhone")
    phones = [app_of(token) for _ in range(6)]
    made = asked()
    barrier = threading.Barrier(len(phones))

    def tap(phone: TestClient) -> Any:
        barrier.wait()
        return confirm_from_phone(phone, made)

    with ThreadPoolExecutor(max_workers=len(phones)) as pool:
        answers = list(pool.map(tap, phones))
    assert sorted(answer.status_code for answer in answers) == [200] + [410] * 5, [a.text for a in answers]
    assert {code_of(answer) for answer in answers if answer.status_code == 410} == {"pairing_used"}
    assert device_count() == 1


def test_two_people_confirming_the_same_request_at_the_same_moment_one_wins(client: TestClient,
                                                                           operator: Account) -> None:
    made = asked()
    people = [make_account(f"person{number}") for number in range(4)]
    phones = [phone_of(person, f"Phone {number}") for number, person in enumerate(people)]
    barrier = threading.Barrier(len(phones))

    def attempt(phone: TestClient) -> Any:
        barrier.wait()
        return confirm_from_phone(phone, made)

    with ThreadPoolExecutor(max_workers=len(phones)) as pool:
        answers = list(pool.map(attempt, phones))
    assert sorted(answer.status_code for answer in answers) == [200, 404, 404, 404], [a.text for a in answers]
    winner = row_of(made).account_id
    assert winner in {person.id for person in people}
    token = poll(app_client(), made).json()["token"]
    with SessionLocal() as db:
        device = db.scalar(select(Device).where(Device.name == "Living room TV"))
        assert device is not None and device.account_id == winner and device.token_hash == devices.digest(token)


# --- What it says, and what it never says -----------------------------------------------------------------------------


def test_the_log_names_the_account_the_phone_and_the_television_and_nothing_secret(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    handed: list[str] = []
    monkeypatch.setattr(device_request, "logger", SimpleNamespace(
        info=lambda text, *args: handed.append(text % args), warning=lambda text, *args: handed.append(text % args)))
    ada = make_account("ada")
    token = device_token(ada, "Ada's iPhone")
    phone = app_of(token)
    box = app_client()
    made = asked(box, "Den's TV")
    confirm_from_phone(phone, made, code="ZZZZ-2222")
    assert handed == []
    assert confirm_from_phone(phone, made).status_code == 200
    fetched = poll(box, made).json()["token"]
    assert handed == ["A device of ada was confirmed from the app Ada's iPhone: Den's TV",
                      "A device of ada was paired by code: Den's TV"]
    flat = " ".join(handed)
    for secret in (made["secret"], made["code"], made["code"].replace("-", ""), made["id"], token, fetched,
                   token[4:20], pairing.hash_secret(made["secret"]), devices.digest(fetched)):
        assert secret not in flat, secret[:6]


def test_nothing_secret_reaches_the_log_file_not_the_id_the_code_nor_the_tokens(client: TestClient,
                                                                               operator: Account) -> None:
    ada = make_account("ada")
    token = device_token(ada, "Ada's iPhone")
    phone = app_of(token)
    logs.set_mode("trace", 30)
    try:
        box = app_client()
        made = asked(box, "Den's TV")
        texts = [confirm_from_phone(phone, made, code="ZZZZ-2222").text, confirm_from_phone(phone, made, code="x").text,
                 confirm_from_phone(phone, made).text, confirm_from_phone(phone, made).text]
        fetched = poll(box, made).json()["token"]
        texts.append(poll(box, made).text)
    finally:
        logs.set_mode(logs.DEFAULT_MODE)
    for handler in logging.getLogger().handlers:
        handler.flush()
    log = "".join(path.read_text(encoding="utf-8", errors="replace")
                  for path in logs.log_file().parent.glob(logs.log_file().name + "*"))
    assert "A device of ada was confirmed from the app Ada's iPhone: Den's TV" in log
    for secret in (made["secret"], made["code"], made["code"].replace("-", ""), token, fetched, token[4:20],
                   pairing.hash_secret(made["secret"]), pairing.hash_code(made["code"].replace("-", ""))):
        assert secret not in log, secret[:6]
    # The id stands in the address the television asks at (and so in the log of the requests, as it always did); where
    # the phone confirms it travels in the body, and no line about that holds it.
    assert [line for line in log.splitlines() if "confirm" in line and made["id"] in line] == []
    for text in texts:
        assert made["secret"] not in text and made["code"] not in text and not TOKEN.search(text)


def test_the_code_is_compared_as_a_hash_in_constant_time_also_when_there_is_nothing_to_find(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bytes, bytes]] = []

    def spy(first: bytes, second: bytes) -> bool:
        seen.append((first, second))
        return real_hmac.compare_digest(first, second)

    monkeypatch.setattr(device_request, "hmac", SimpleNamespace(compare_digest=spy, new=real_hmac.new))
    phone = phone_of(make_account("ada"))
    made = asked()
    plain = made["code"].replace("-", "")
    confirm_from_phone(phone, made, request_id="N" * 22)
    confirm_from_phone(phone, made, code="ZZZZ-2222")
    assert len(seen) == 2, "one comparison for each guess, whatever it hits"
    assert all(len(first) == len(second) == 64 for first, second in seen)
    assert seen[1] == (pairing.hash_code(plain).encode(), pairing.hash_code("ZZZZ2222").encode())
    seen.clear()
    assert confirm_from_phone(phone, made).status_code == 200
    assert seen == [(pairing.hash_code(plain).encode(), pairing.hash_code(plain).encode())]
    assert all(plain.encode() not in item for pair in seen for item in pair)


def test_the_answers_are_not_cached_and_name_no_secret(client: TestClient, operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    made = asked()
    for answer in (confirm_from_phone(phone, made, code="ZZZZ-2222"), confirm_from_phone(phone, made),
                   confirm_from_phone(phone, made)):
        assert "no-store" in answer.headers.get("cache-control", ""), answer.status_code
        assert made["secret"] not in answer.text and made["code"] not in answer.text


def test_the_answer_says_no_store_itself(client: TestClient, operator: Account) -> None:
    """The wall around the app puts ``no-store`` on every answer of ``/api``; the route says it too, so that it holds
    without that wall (a proxy in front of a changed setup, a route moved)."""
    from fastapi import FastAPI

    from app.routers import app1 as app1_router

    phone = phone_of(make_account("ada"))
    made = asked()
    bare = FastAPI()
    bare.include_router(app1_router.router)
    with TestClient(bare, headers=phone.headers) as box:
        done = confirm_from_phone(box, made)
        assert done.status_code == 200 and done.headers["cache-control"] == "no-store"


def test_a_body_is_limited_before_it_is_read(client: TestClient, operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    big = phone.post(PATH, content=json.dumps({"id": "x", "code": "A" * 6000}).encode(),
                     headers={"Content-Type": "application/json"})
    assert (big.status_code, code_of(big)) == (413, "too_large")
    nobody = app_client().post(PATH, content=json.dumps({"id": "x", "code": "A" * 6000}).encode(),
                               headers={"Content-Type": "application/json"})
    assert nobody.status_code in (400, 413), "also without a token"


def test_features_name_the_confirmation_and_the_description_names_the_route() -> None:
    from app.main import app

    assert "pairing_request_confirm" in FEATURES
    document = app.openapi()
    assert set(document["paths"][PATH]) == {"post"}
    operation = document["paths"][PATH]["post"]
    assert set(operation["responses"]) >= {"200", "401", "403", "404", "409", "410", "422", "429"}
    assert operation["responses"]["200"]["headers"]["Cache-Control"]
