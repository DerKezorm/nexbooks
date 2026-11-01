"""A device token that brings a device.

The iPhone app may confirm the code of a television with its own device token. So a token makes a device, and a lost
phone must not leave anything behind that outlives it: the television is connected **through** the phone, revoking the
phone revokes it (and what it brought, if there were such a thing), a device connected that way connects none itself,
and the list says through which device a device came. The app may look at a request before it confirms (the name the
server knows) and take back what it confirmed until the television fetched. And the count against one request holds
also for whoever rests his own counts first."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import Account, AppRequest, Device
from app.routers.app1 import FEATURES
from app.security import brake
from app.services import devices, pairing

from .app_helpers import app_of, device_token
from .conftest import make_account, new_client, require_second_factor
from .pairing_helpers import Moving, app_client, asked, cancel, confirm, confirm_from_phone, poll
from .test_device_request import TOKEN, code_of, device_count, typed_from
from .test_device_request_phone import phone_of, row_of, wrong_ones

PREVIEW = "/api/app/1/pairing/request/preview"


def preview(phone: TestClient, made: dict[str, Any], code: str | None = None, request_id: str | None = None) -> Any:
    return phone.post(PREVIEW, json={"id": made["id"] if request_id is None else request_id,
                                     "code": made["code"] if code is None else code})


def withdraw(phone: TestClient, made: dict[str, Any]) -> Any:
    return phone.delete(f"/api/app/1/pairing/request/{made['id']}/confirm")


def listed(account: Account) -> dict[str, dict[str, Any]]:
    with new_client(account) as browser:
        return {item["name"]: item for item in browser.get("/api/v1/devices").json()}


def revoke_in_browser(account: Account, name: str) -> None:
    with new_client(account) as browser:
        uid = listed(account)[name]["id"]
        assert browser.delete(f"/api/v1/devices/{uid}").status_code == 204


def works(token: str) -> bool:
    answer = app_of(token).get("/api/app/1/me")
    assert answer.status_code in (200, 401), answer.text
    return answer.status_code == 200


def television(phone: TestClient, name: str = "Living room TV") -> tuple[dict[str, Any], str]:
    """A television the phone connected: its request and the token it fetched."""
    box = app_client()
    made = asked(box, name)
    assert confirm_from_phone(phone, made).status_code == 200
    got = poll(box, made)
    assert got.status_code == 200, got.text
    return made, str(got.json()["token"])


# --- Revoking the phone takes what it brought -------------------------------------------------------------------------


def test_revoking_the_phone_revokes_the_television_it_connected_and_nothing_else(client: TestClient,
                                                                                operator: Account) -> None:
    ada, ben = make_account("ada"), make_account("ben")
    lost = device_token(ada, "Ada's iPhone")
    kept = device_token(ada, "Ada's iPad")
    bens = device_token(ben, "Ben's iPhone")
    _made, tv = television(app_of(lost))
    _other, bens_tv = television(app_of(bens), "Ben's TV")
    assert all(works(token) for token in (lost, kept, tv, bens, bens_tv))
    revoke_in_browser(ada, "Ada's iPhone")
    assert not works(lost) and not works(tv), "the phone and the television it brought"
    assert works(kept), "a device made in the browser stays"
    assert works(bens) and works(bens_tv), "another person's stay"
    assert sorted(listed(ada)) == ["Ada's iPad"]


def test_revoking_the_television_alone_leaves_the_phone(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    mine = device_token(ada, "Ada's iPhone")
    _made, tv = television(app_of(mine))
    revoke_in_browser(ada, "Living room TV")
    assert works(mine) and not works(tv)


def test_revoking_the_phone_ends_a_confirmation_the_television_has_not_fetched(client: TestClient,
                                                                              operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    box = app_client()
    made = asked(box)
    assert confirm_from_phone(phone, made).status_code == 200
    revoke_in_browser(ada, "Ada's iPhone")
    # Ended in the same step, not only refused at the fetch: no account holds it any longer.
    ended = row_of(made)
    assert (ended.state, ended.account_id, ended.confirmed_at) == ("waiting", None, None)
    assert poll(box, made).json() == {"state": "expired"}
    assert device_count() == 1 and listed(ada) == {}
    # A confirmation in the browser is no confirmation of the phone: it stays.
    with new_client(ada) as browser:
        other = asked(box)
        assert confirm(browser, other).status_code == 200
    device_token(ada, "Second phone")
    revoke_in_browser(ada, "Second phone")
    assert poll(box, other).json()["state"] == "confirmed"


def test_the_television_fetches_nothing_while_the_phone_that_confirmed_is_revoked(client: TestClient,
                                                                                 operator: Account) -> None:
    """Also when the phone was revoked some other way than the list (the request was not touched): the poll itself
    asks whether the device that confirmed still holds."""
    ada = make_account("ada")
    phone = phone_of(ada)
    box = app_client()
    made = asked(box)
    assert confirm_from_phone(phone, made).status_code == 200
    with SessionLocal() as db:
        db.execute(update(Device).where(Device.name == "Ada's iPhone").values(revoked_at=clock.now()))
        db.commit()
    assert row_of(made).state == "confirmed"
    assert poll(box, made).json() == {"state": "expired"}
    assert device_count() == 1


def test_whatever_a_device_brought_goes_with_it_however_deep(client: TestClient, operator: Account) -> None:
    """None can be made by the app (a device brought by another brings none), but if the database held a longer line,
    revoking its start ends all of it, and only it."""
    ada = make_account("ada")
    first = device_token(ada, "Ada's iPhone")
    _made, tv = television(app_of(first))
    third = device_token(ada, "Third")
    fourth = device_token(ada, "Fourth")
    with SessionLocal() as db:
        ids = {row.name: row.id for row in db.scalars(select(Device).where(Device.account_id == ada.id))}
        db.execute(update(Device).where(Device.id == ids["Third"]).values(via_device_id=ids["Living room TV"]))
        db.execute(update(Device).where(Device.id == ids["Fourth"]).values(via_device_id=ids["Third"]))
        db.commit()
    revoke_in_browser(ada, "Living room TV")
    assert works(first) and not works(tv) and not works(third) and not works(fourth)


def test_a_new_password_and_signing_out_everywhere_end_the_phone_and_the_television(client: TestClient,
                                                                                  operator: Account) -> None:
    ada = make_account("ada")
    mine = device_token(ada, "Ada's iPhone")
    _made, tv = television(app_of(mine))
    with new_client(ada) as browser:
        assert browser.post("/api/v1/auth/logout-all").status_code == 204
    assert not works(mine) and not works(tv)


def test_the_operator_signing_an_account_out_or_blocking_it_ends_both(client: TestClient, operator: Account) -> None:
    ada, ben = make_account("ada"), make_account("ben")
    mine = device_token(ada, "Ada's iPhone")
    _made, tv = television(app_of(mine))
    assert client.post(f"/api/v1/accounts/{ada.id}/sign-out").status_code == 204
    assert not works(mine) and not works(tv)
    bens = device_token(ben, "Ben's iPhone")
    _other, bens_tv = television(app_of(bens), "Ben's TV")
    blocked = client.post(f"/api/v1/accounts/{ben.id}/block", json={"current_password": "correct horse battery"})
    assert blocked.status_code == 204, blocked.text
    assert not works(bens) and not works(bens_tv)


# --- No line of devices -----------------------------------------------------------------------------------------------


def test_a_device_the_phone_connected_connects_none_itself(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    _made, tv = television(phone_of(ada))
    box = app_of(tv)
    made = asked()
    for answer in (confirm_from_phone(box, made), preview(box, made), confirm_from_phone(box, made, code="ZZZZ-2222")):
        assert (answer.status_code, code_of(answer)) == (403, "device_chain")
    assert row_of(made).state == "waiting"
    # Turned away before anything counts: neither the person, nor the request, nor the sender rests.
    assert brake.wait_seconds(f"pair-account:{ada.id}") == 0
    assert code_of(confirm_from_phone(phone_of(ada, "Ada's iPad"), made, code="ZZZZ-2222")) == "pairing_unknown"
    assert box.get("/api/app/1/me").json()["device"] == {"name": "Living room TV", "may_confirm": False}
    assert device_count() == 3


def test_a_device_connected_in_the_browser_may_confirm_and_says_so(client: TestClient, operator: Account) -> None:
    """The browser is the person at it: what it connected is connected through nobody."""
    ada = make_account("ada")
    box = app_client()
    made = asked(box, "Bedroom TV")
    with new_client(ada) as browser:
        assert confirm(browser, made).status_code == 200
    token = poll(box, made).json()["token"]
    assert listed(ada)["Bedroom TV"]["via"] is None
    tv = app_of(token)
    assert tv.get("/api/app/1/me").json()["device"] == {"name": "Bedroom TV", "may_confirm": True}
    assert confirm_from_phone(tv, asked()).status_code == 200
    assert "pairing_request_preview" in FEATURES


def test_the_list_says_through_which_device_a_device_came(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    device_token(ada, "Ada's iPhone")
    phone_id = listed(ada)["Ada's iPhone"]["id"]
    television(phone_of(ada, "Ada's iPhone 2"))
    found = listed(ada)
    assert found["Ada's iPhone"]["via"] is None and found["Ada's iPhone 2"]["via"] is None
    assert found["Living room TV"]["via"] == {"id": found["Ada's iPhone 2"]["id"], "name": "Ada's iPhone 2"}
    assert phone_id != found["Ada's iPhone 2"]["id"]
    with SessionLocal() as db:
        tv = db.scalar(select(Device).where(Device.name == "Living room TV"))
        phone = db.scalar(select(Device).where(Device.name == "Ada's iPhone 2"))
        assert tv is not None and phone is not None and tv.via_device_id == phone.id


# --- Looking first ----------------------------------------------------------------------------------------------------


def test_the_app_sees_the_name_the_server_knows_before_it_confirms_and_nothing_changes(client: TestClient,
                                                                                      operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    box = app_client()
    made = asked(box, "  Den   TV ")
    seen = preview(phone, made, code=made["code"].lower().replace("-", ""))
    assert seen.status_code == 200, seen.text
    assert seen.headers["cache-control"] == "no-store"
    body = seen.json()
    assert set(body) == {"state", "device_name", "requested_at", "expires_at"}
    assert (body["state"], body["device_name"]) == ("waiting", "Den TV")
    row = row_of(made)
    assert datetime.fromisoformat(body["requested_at"]) == row.created_at
    assert datetime.fromisoformat(body["expires_at"]) == datetime.fromisoformat(made["expires_at"])
    assert not TOKEN.search(seen.text) and made["secret"] not in seen.text and made["code"] not in seen.text
    assert (row.state, row.account_id, row.confirmed_by) == ("waiting", None, None)
    assert poll(box, made).json() == {"state": "waiting"}
    # Looking twice is fine and counts nothing; then the confirmation goes through.
    assert preview(phone, made).status_code == 200
    assert brake.wait_seconds(f"pair-account:{ada.id}") == 0
    assert confirm_from_phone(phone, made).status_code == 200


def test_looking_answers_as_confirming_does(client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch,
                                            ) -> None:
    ada, ben = make_account("ada"), make_account("ben")
    phone, bens = phone_of(ada), phone_of(ben, "Ben's iPhone")
    mine, theirs, gone, let_go = asked(), asked(), asked(), asked()
    assert confirm_from_phone(phone, mine).status_code == 200
    assert confirm_from_phone(bens, theirs).status_code == 200
    assert cancel(app_client(), let_go).status_code == 204
    used = preview(phone, mine)
    assert (used.status_code, code_of(used)) == (410, "pairing_used")
    other = preview(phone, theirs)
    assert (other.status_code, code_of(other)) == (404, "pairing_unknown")
    assert other.json() == preview(phone, gone, code="ZZZZ-2222").json() == preview(
        phone, gone, request_id="N" * 22).json()
    assert code_of(preview(phone, let_go)) == "pairing_expired"
    assert code_of(preview(phone, gone, code="hello")) == "pairing_code"
    assert phone.post(PREVIEW, json={"id": gone["id"]}).status_code == 422
    clock_moved = Moving(monkeypatch)
    clock_moved.pass_by(minutes=11)
    assert code_of(preview(phone, gone)) == "pairing_expired"


def test_a_wrong_pair_when_looking_counts_like_one_when_confirming(client: TestClient, operator: Account,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    made = asked()
    people = [phone_of(make_account(f"typist{number}"), f"Phone {number}") for number in range(6)]
    for number, phone in enumerate(people[:5]):
        typed_from(monkeypatch, f"203.0.113.{number}")
        answer = preview(phone, made, code=f"ABCD-23{number + 4}5")
        assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    typed_from(monkeypatch, "203.0.113.77")
    for resting in (preview(people[5], made), confirm_from_phone(people[5], made)):
        assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    assert row_of(made).state == "waiting"
    # And the sender and the person rest for wrong looks as for wrong confirmations.
    brake.forget()
    phone = people[0]
    typed_from(monkeypatch, "203.0.113.200")
    for number in range(5):
        preview(phone, made, request_id=f"N{number:021d}")
    assert code_of(confirm_from_phone(phone, made, request_id="N" * 22)) == "too_many_attempts"


# --- Taking back ------------------------------------------------------------------------------------------------------


def test_the_app_takes_back_what_it_confirmed_until_the_television_fetched(client: TestClient,
                                                                          operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    box = app_client()
    made = asked(box)
    assert confirm_from_phone(phone, made).status_code == 200
    taken = withdraw(phone, made)
    assert taken.status_code == 204 and taken.headers["cache-control"] == "no-store"
    assert poll(box, made).json() == {"state": "expired"}
    assert withdraw(phone, made).status_code == 204, "again and again the same"
    assert device_count() == 1
    assert code_of(confirm_from_phone(phone, made)) == "pairing_expired"
    # Once the television fetched, it is a device in the list: taking back says so and changes nothing.
    second = asked(box)
    assert confirm_from_phone(phone, second).status_code == 200
    token = poll(box, second).json()["token"]
    late = withdraw(phone, second)
    assert (late.status_code, code_of(late)) == (410, "pairing_used")
    assert works(token)


def test_only_the_device_that_confirmed_takes_back_and_nothing_is_counted(client: TestClient,
                                                                         operator: Account) -> None:
    ada, ben = make_account("ada"), make_account("ben")
    phone, ipad, bens = phone_of(ada), phone_of(ada, "Ada's iPad"), phone_of(ben, "Ben's iPhone")
    by_phone, waiting = asked(), asked()
    assert confirm_from_phone(phone, by_phone).status_code == 200
    with new_client(ada) as browser:
        by_browser = asked()
        assert confirm(browser, by_browser).status_code == 200
    for who, made in ((ipad, by_phone), (bens, by_phone), (phone, by_browser), (phone, waiting)):
        answer = withdraw(who, made)
        assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    for number in range(8):
        assert code_of(withdraw(phone, {"id": f"N{number:021d}"})) == "pairing_unknown"
    assert brake.wait_seconds(f"pair-account:{ada.id}") == 0
    wrong = wrong_ones(phone, 1, start=90)[0]
    assert code_of(wrong) == "pairing_unknown", "the sender does not rest either"
    assert row_of(by_phone).state == "confirmed" and row_of(by_browser).state == "confirmed"
    assert confirm_from_phone(phone, waiting).status_code == 200, "nothing rests"
    # A browser cookie is no way in; the device token needs no tab header.
    with new_client(ada) as browser:
        assert browser.delete(f"/api/app/1/pairing/request/{by_phone['id']}/confirm").status_code == 401
    assert row_of(by_phone).state == "confirmed"


def test_taking_back_and_fetching_at_the_same_moment_never_both_win(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    token = device_token(ada, "Ada's iPhone")
    for _round in range(12):
        box = app_client()
        made = asked(box)
        assert confirm_from_phone(app_of(token), made).status_code == 200
        barrier = threading.Barrier(2)

        def take_back(made: dict[str, Any] = made, barrier: threading.Barrier = barrier) -> Any:
            barrier.wait()
            return withdraw(app_of(token), made)

        def fetch(made: dict[str, Any] = made, box: TestClient = box, barrier: threading.Barrier = barrier) -> Any:
            barrier.wait()
            return poll(box, made)

        with ThreadPoolExecutor(2) as pool:
            backing, getting = pool.submit(take_back), pool.submit(fetch)
            back, got = backing.result(), getting.result()
        if got.json().get("token"):
            assert (back.status_code, code_of(back)) == (410, "pairing_used")
        else:
            assert back.status_code == 204 and got.json() == {"state": "expired"}


def test_revoking_the_phone_while_the_television_fetches_leaves_no_television(client: TestClient,
                                                                             operator: Account) -> None:
    ada = make_account("ada")
    for number in range(8):
        token = device_token(ada, f"Phone {number}")
        box = app_client()
        made = asked(box)
        assert confirm_from_phone(app_of(token), made).status_code == 200
        barrier = threading.Barrier(2)

        def revoke(name: str = f"Phone {number}", barrier: threading.Barrier = barrier) -> None:
            with SessionLocal() as db:
                uid = db.scalar(select(Device.uid).where(Device.name == name))
                assert uid is not None
                barrier.wait()
                assert devices.revoke(db, ada.id, uid)

        def fetch(made: dict[str, Any] = made, box: TestClient = box, barrier: threading.Barrier = barrier) -> Any:
            barrier.wait()
            return poll(box, made)

        with ThreadPoolExecutor(2) as pool:
            done, got = pool.submit(revoke), pool.submit(fetch)
            done.result()
            answer = got.result()
        fetched = answer.json().get("token")
        assert fetched is None or not works(fetched), "a television it brought does not outlive the phone"
    assert all(item["via"] is None for item in listed(ada).values())


# --- The count against one request -------------------------------------------------------------------------------


def test_a_resting_sender_still_counts_against_the_request(client: TestClient, operator: Account) -> None:
    """Whoever knows an id must not try its code without end, also not after resting his own sender and account
    first: every wrong pair counts against the request."""
    made = asked()
    mallory = phone_of(make_account("mallory"))
    wrong_ones(mallory, 5)
    assert code_of(wrong_ones(mallory, 1, start=50)[0]) == "too_many_attempts"
    for number in range(5):
        assert code_of(confirm_from_phone(mallory, made, code=f"ABCD-23{number + 2}5")) == "too_many_attempts"
    answer = confirm_from_phone(phone_of(make_account("nora"), "Nora's phone"), made)
    assert (answer.status_code, code_of(answer)) == (429, "too_many_attempts")
    assert row_of(made).state == "waiting"


def test_a_full_window_over_the_server_still_counts_against_the_request(client: TestClient, operator: Account,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pairing, "CODE_GUESSES", 2)
    made = asked()
    for number in range(2):
        typed_from(monkeypatch, f"203.0.113.{number}")
        wrong_ones(phone_of(make_account(f"f{number}"), f"Phone {number}"), 1, start=number)
    mallory = phone_of(make_account("mallory"))
    for number in range(5):
        typed_from(monkeypatch, f"198.51.100.{number}")
        assert code_of(confirm_from_phone(mallory, made, code=f"ABCD-23{number + 2}5")) == "too_many_codes"
    typed_from(monkeypatch, "198.51.100.99")
    answer = confirm_from_phone(phone_of(make_account("nora"), "Nora's phone"), made)
    assert (answer.status_code, code_of(answer)) == (429, "too_many_attempts")


# --- Smaller guards ---------------------------------------------------------------------------------------------


def test_a_full_account_hears_used_or_expired_before_it_hears_too_many_devices(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    done, let_go = asked(), asked()
    assert confirm_from_phone(phone, done).status_code == 200
    assert cancel(app_client(), let_go).status_code == 204
    monkeypatch.setattr(devices, "MAX_DEVICES", 1)
    assert code_of(confirm_from_phone(phone, done)) == "pairing_used"
    assert code_of(confirm_from_phone(phone, let_go)) == "pairing_expired"
    assert code_of(confirm_from_phone(phone, asked())) == "too_many_devices"


def test_a_television_whose_request_reads_confirm_lets_it_go_without_a_tab_header(client: TestClient,
                                                                                 operator: Account) -> None:
    box = app_client()
    for path in ("/api/app/1/pairing/request/confirm", "/api/app/1/pairing/request/preview"):
        answer = box.delete(path, headers={"X-Pairing-Secret": "x" * 43})
        assert answer.status_code == 204, (path, answer.text)
        # The app that confirms or looks speaks for a token: without one, a change needs the tab header.
        assert code_of(box.post(path, json={"id": "x", "code": "ABCD-2345"})) == "client_required"


def test_a_body_to_look_is_limited_before_it_is_read(client: TestClient, operator: Account) -> None:
    phone = phone_of(make_account("ada"))
    answer = phone.post(PREVIEW, content=b'{"id": "' + b"a" * 5000 + b'", "code": "ABCD-2345"}',
                        headers={"Content-Type": "application/json"})
    assert answer.status_code == 413


def test_the_log_says_a_request_rests_without_naming_it(client: TestClient, operator: Account,
                                                        monkeypatch: pytest.MonkeyPatch,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    made = asked()
    caplog.set_level(logging.WARNING, logger="nexbooks.auth")
    for number in range(5):
        typed_from(monkeypatch, f"203.0.113.{number}")
        confirm_from_phone(phone_of(make_account(f"typist{number}"), f"Phone {number}"), made,
                           code=f"ABCD-23{number + 4}5")
    import hashlib

    engaged = [record.getMessage() for record in caplog.records if "Brake engaged" in record.getMessage()]
    assert any(line.endswith("key=pair-request") for line in engaged), engaged
    flat = " ".join(record.getMessage() for record in caplog.records)
    assert hashlib.sha256(made["id"].encode()).hexdigest()[:32] not in flat and made["id"] not in flat


@pytest.mark.parametrize("change", [lambda i: i[:10], lambda i: i[:-1], lambda i: i + "a", lambda i: f" {i}",
                                    lambda i: f"{i} ", lambda i: i.upper() if i.upper() != i else i.lower()])
def test_an_id_that_is_only_like_the_right_one_is_unknown_and_counts(client: TestClient, operator: Account,
                                                                     change: Any) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    for answer in (confirm_from_phone(phone, made, request_id=change(made["id"])),
                   preview(phone, made, request_id=change(made["id"]))):
        assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    assert brake.wait_seconds(f"pair-account:{ada.id}", 2) > 0, "two guesses counted for the person"
    assert row_of(made).state == "waiting"
    assert confirm_from_phone(phone, made).status_code == 200


def test_two_devices_of_one_person_share_the_count_of_the_person(client: TestClient, operator: Account,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    ada = make_account("ada")
    phone, ipad = phone_of(ada), phone_of(ada, "Ada's iPad")
    for number in range(5):
        typed_from(monkeypatch, f"203.0.113.{number}")
        assert code_of(wrong_ones(phone, 1, start=number)[0]) == "pairing_unknown"
    typed_from(monkeypatch, "198.51.100.7")
    resting = wrong_ones(ipad, 1, start=40)[0]
    assert (resting.status_code, code_of(resting)) == (429, "too_many_attempts")
    other = wrong_ones(phone_of(make_account("ben"), "Ben's iPhone"), 1, start=41)[0]
    assert code_of(other) == "pairing_unknown", "another person at the same fresh address"


def test_whoever_must_set_up_a_second_factor_neither_looks_nor_takes_back(client: TestClient,
                                                                         operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    assert confirm_from_phone(phone, made).status_code == 200
    require_second_factor(True)
    for answer in (preview(phone, asked()), withdraw(phone, made)):
        assert (answer.status_code, code_of(answer)) == (403, "second_factor_setup_required")
    require_second_factor(False)
    assert withdraw(phone, made).status_code == 204
    assert row_of(made).state == "waiting" and row_of(made).account_id is None


def test_the_rows_say_who_confirmed(client: TestClient, operator: Account) -> None:
    ada = make_account("ada")
    phone = phone_of(ada)
    made = asked()
    assert confirm_from_phone(phone, made).status_code == 200
    with SessionLocal() as db:
        mine = db.scalar(select(Device.id).where(Device.name == "Ada's iPhone"))
        row = db.get(AppRequest, made["id"])
        assert row is not None and row.confirmed_by == mine
