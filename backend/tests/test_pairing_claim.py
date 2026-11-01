"""Connecting the iPhone app, the app's side: with the secret of the QR code or the code a person typed, the app
fetches its device token once. A wrong secret or code counts like a wrong password; an id nobody made and a wrong secret
answer the same; the comparison is constant-time; two apps at the same moment do not both get a token; nothing secret
reaches the log, and the token is in no answer but the one that hands it over."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.main import app
from app.models import Account, AppPairing, Device, utcnow
from app.services import devices, logs, pairing

from .conftest import TAB, make_account, new_client
from .pairing_helpers import Moving, app_client, claim_code, claim_secret, make

TOKEN = re.compile(r"nbd_[A-Za-z0-9_-]{43}")


def code_of(detail: Any) -> str:
    return str(detail.json()["detail"]["code"])


def test_the_app_fetches_its_token_with_the_secret_of_the_qr_code(client: TestClient, operator: Account) -> None:
    made = make(client)
    answer = claim_secret(app_client(), made, "Ada's iPhone")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == {"token", "account", "server"} and set(body["account"]) == {"name"}
    assert body["account"]["name"] == "tester" and body["server"]["version"]
    assert TOKEN.fullmatch(body["token"])
    assert answer.headers["cache-control"] == "no-store"
    bearer = {"Authorization": f"Bearer {body['token']}"}
    me = app_client().get("/api/v1/auth/me", headers=bearer)
    assert me.status_code == 200 and me.json()["name"] == "tester"
    assert [row["name"] for row in client.get("/api/v1/devices").json()] == ["Ada's iPhone"]
    with SessionLocal() as db:
        row = db.get(AppPairing, made["id"])
        device = db.scalar(select(Device))
        assert row is not None and device is not None
        assert (row.state, row.device_id, row.used_at is not None) == ("used", device.id, True)
        assert device.token_hash == devices.digest(body["token"]), "only the hash of the token is kept"


def test_the_app_fetches_its_token_with_the_code_a_person_typed(client: TestClient, operator: Account) -> None:
    made = make(client)
    typed = made["code"].lower().replace("-", "  ")
    answer = claim_code(app_client(), typed, "Ada's iPhone")
    assert answer.status_code == 200, answer.text
    assert TOKEN.fullmatch(answer.json()["token"]) and answer.json()["account"]["name"] == "tester"
    assert answer.headers["cache-control"] == "no-store"
    for written in ("ABCD2345", "ABCD-2345", "abcd-2345", " AbCd 2345 "):
        assert pairing.clean_code(written) == "ABCD2345", written


def test_a_device_without_a_name_is_an_iphone(client: TestClient, operator: Account) -> None:
    for name in ("", "   "):
        made = make(client)
        assert claim_secret(app_client(), made, name).status_code == 200
    assert [row["name"] for row in client.get("/api/v1/devices").json()] == ["iPhone", "iPhone"]
    made = make(client)
    assert app_client().post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"]}).status_code == 200


def test_a_name_that_cannot_be_kept_is_refused_and_the_code_stays_open(client: TestClient, operator: Account) -> None:
    made = make(client)
    # Whatever the length: a name that cannot be kept is ``device_name_invalid``, never the model's ``invalid_input``.
    for name in ("Phone\nTwo", "Ph\u202eone", "x" * 81, "x" * 100, "x" * 400, "x" * 3000):
        answer = claim_secret(app_client(), made, name)
        assert (answer.status_code, code_of(answer)) == (422, "device_name_invalid"), repr(name[:12])
        by_code = claim_code(app_client(), made["code"], name)
        assert (by_code.status_code, code_of(by_code)) == (422, "device_name_invalid"), repr(name[:12])
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "waiting"
    with SessionLocal() as db:
        assert db.scalar(select(Device.id)) is None
    assert claim_secret(app_client(), made, "Ada's iPhone").status_code == 200


def test_the_longest_name_and_a_name_that_is_long_only_in_spaces_are_kept(client: TestClient, operator: Account) -> None:
    for name, kept in (("n" * 80, "n" * 80), ("Ada" + " " * 120 + "iPhone", "Ada iPhone")):
        made = make(client)
        assert claim_secret(app_client(), made, name).status_code == 200
        assert next(row["name"] for row in client.get("/api/v1/devices").json()) == kept


def test_a_code_is_used_once_and_the_second_try_is_told_so(client: TestClient, operator: Account) -> None:
    made = make(client)
    assert claim_secret(app_client(), made).status_code == 200
    for second in (claim_secret(app_client(), made), claim_code(app_client(), made["code"])):
        assert (second.status_code, code_of(second)) == (410, "pairing_used")
        assert "token" not in second.json()
    with SessionLocal() as db:
        assert db.query(Device).count() == 1


@pytest.mark.parametrize("by_code", [False, True])
def test_two_apps_at_the_same_moment_do_not_both_get_a_token(client: TestClient, operator: Account,
                                                             by_code: bool) -> None:
    made = make(client)
    boxes = [app_client() for _ in range(10)]
    barrier = threading.Barrier(10)

    def fetch(box: TestClient) -> Any:
        barrier.wait()
        return claim_code(box, made["code"]) if by_code else claim_secret(box, made)

    with ThreadPoolExecutor(max_workers=10) as pool:
        answers = list(pool.map(fetch, boxes))
    assert sorted(answer.status_code for answer in answers) == [200] + [410] * 9, [a.text for a in answers]
    assert {code_of(answer) for answer in answers if answer.status_code == 410} == {"pairing_used"}
    with SessionLocal() as db:
        assert db.query(Device).count() == 1, "exactly one token came into being"
        row = db.get(AppPairing, made["id"])
        assert row is not None and row.state == "used" and row.device_id is not None


def test_an_id_nobody_made_and_a_wrong_secret_answer_the_same(client: TestClient, operator: Account) -> None:
    made = make(client)
    nobody = app_client().post("/api/app/1/pairing/" + "A" * 22 + "/claim", json={"secret": "x" * 43, "device_name": "p"})
    wrong = app_client().post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": "x" * 43, "device_name": "p"})
    assert (nobody.status_code, nobody.json()) == (wrong.status_code, wrong.json())
    assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    # A wrong secret takes nothing: the right one still works.
    assert claim_secret(app_client(), made).status_code == 200


@pytest.mark.parametrize("known", [False, True])
def test_a_guess_at_an_unknown_id_and_one_at_a_known_id_cost_the_sender_the_same(client: TestClient, operator: Account,
                                                                                 known: bool) -> None:
    """The brake must not tell whether an id exists either: the sixth wrong guess meets the rest, whichever kind the
    five before were."""
    made = make(client)
    box = app_client()
    ident = made["id"] if known else "A" * 22
    for number in range(5):
        wrong = box.post(f"/api/app/1/pairing/{ident}/claim", json={"secret": f"wrong-{number}"})
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    sixth = box.post(f"/api/app/1/pairing/{ident}/claim", json={"secret": "wrong-5"})
    assert (sixth.status_code, code_of(sixth)) == (429, "too_many_attempts")
    assert int(sixth.headers["retry-after"]) > 0


def test_only_the_right_secret_or_code_learns_that_a_code_ran_out_or_was_used(client: TestClient, operator: Account,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    used = make(client)
    claim_secret(app_client(), used)
    ran_out = make(client)
    moving.pass_by(minutes=11)
    for made, told in ((used, "pairing_used"), (ran_out, "pairing_expired")):
        wrong = app_client().post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": "y" * 43})
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown"), "no word of its state"
        right = claim_secret(app_client(), made)
        assert (right.status_code, code_of(right)) == (410, told)


@pytest.mark.parametrize("odd", ["x" * 200, "\u00fc", "%00", "a b", "claim"])
def test_odd_ids_are_simply_unknown(client: TestClient, operator: Account, odd: str) -> None:
    answer = app_client().post(f"/api/app/1/pairing/{odd}/claim", json={"secret": "z" * 43})
    assert answer.status_code in (404, 422) and "token" not in answer.text
    if answer.status_code == 404:
        assert code_of(answer) in ("pairing_unknown", "not_found")


def test_a_wrong_secret_or_code_rests_the_sender_from_guessing_and_nothing_else(client: TestClient,
                                                                               operator: Account) -> None:
    """Five wrong guesses rest the sender from guessing (also with the right code: a code can be guessed). A typing
    mistake costs no more than that: a device that exists, the sign-in and the right secret of the QR code (which cannot
    be guessed) of the same address go on."""
    token = client.post("/api/v1/devices", json={"name": "script"}).json()["token"]
    made = make(client)
    box = app_client()
    for number in range(3):
        wrong = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": f"wrong-{number}"})
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    for number in range(2):
        wrong = claim_code(box, "ABCD-234" + "56"[number])
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown")
    resting = claim_code(box, made["code"])
    assert resting.status_code == 429 and int(resting.headers["retry-after"]) > 0
    assert code_of(resting) == "too_many_attempts"
    again = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": "wrong-again"})
    assert (again.status_code, code_of(again)) == (429, "too_many_attempts"), "a secret that does not fit rests too"
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "waiting", "nothing was handed out"
    # The same address: a device of the account, the sign-in and the QR code are not at rest.
    assert box.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    login = box.post("/api/v1/auth/login", json={"name": "tester", "password": "correct horse battery"}, headers=TAB)
    assert login.status_code == 200, login.text
    assert claim_secret(app_client(), made).status_code == 200, "the right secret cannot be guessed: not turned away"


def test_wrong_passwords_and_wrong_tokens_do_not_rest_the_claim(client: TestClient, operator: Account) -> None:
    """The other way round: the pairing has a count of its own, nobody's wrong password or token spends it."""
    made = make(client)
    box = app_client()
    for _ in range(5):
        assert box.post("/api/v1/auth/login", json={"name": "nobody", "password": "x" * 14}, headers=TAB
                        ).status_code == 401
    wrong = claim_code(box, "ABCD-2346")
    assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown"), "not at rest"
    assert claim_code(box, made["code"]).status_code == 200


def test_a_typing_mistake_at_the_code_never_shuts_out_a_device_that_exists(client: TestClient,
                                                                           operator: Account) -> None:
    """Five well-formed wrong codes from one address answered every later request of
    that address with 429, a valid device token included, and with it the whole of the app."""
    token = client.post("/api/v1/devices", json={"name": "script"}).json()["token"]
    box = app_client()
    for _ in range(12):
        assert code_of(claim_code(box, "ABCD-2347")) in ("pairing_unknown", "too_many_attempts")
    for _ in range(3):
        assert box.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_a_text_that_cannot_be_a_code_is_no_guess(client: TestClient, operator: Account) -> None:
    """Wrong length or a letter outside the alphabet names nothing there could be: it costs the sender no attempt."""
    made = make(client)
    box = app_client()
    for odd in ("", "A", "ABCD-234", "ABCD-23456", "ABCD-234O", "ABCD-2341", "ABCD-234L", "ÄBCD-2345", "AB\nCD2345"):
        answer = claim_code(box, odd)
        assert answer.status_code in (422,), odd
        if odd:
            assert code_of(answer) in ("pairing_code", "invalid_input"), odd
    for _ in range(12):
        assert claim_code(box, "x" * 200).status_code == 422
    assert claim_code(box, made["code"]).status_code == 200, "the sender did not rest"


def test_what_is_told_after_the_right_secret_or_code_is_no_guess(client: TestClient, operator: Account) -> None:
    made = make(client)
    box = app_client()
    assert claim_secret(box, made).status_code == 200
    for _ in range(12):
        assert code_of(claim_secret(box, made)) == "pairing_used"
        assert code_of(claim_code(box, made["code"])) == "pairing_used"
    fresh = make(client)
    assert claim_secret(box, fresh).status_code == 200, "no rest after twenty-four known answers"


def test_a_web_page_cannot_use_a_code_or_use_up_the_attempts_of_a_visitor(client: TestClient, operator: Account) -> None:
    made = make(client)
    box = app_client()
    for _ in range(12):
        page = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": "guess"},
                        headers={"Origin": "https://evil.example.net"})
        assert (page.status_code, code_of(page)) == (403, "origin_refused")
        page = box.post("/api/app/1/pairing/claim", json={"code": "ABCD-2345"}, headers={"Origin": "null"})
        assert (page.status_code, code_of(page)) == (403, "origin_refused")
    right = claim_secret(box, made)
    assert right.status_code == 200, "the visitor's address was not counted"
    again = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"]},
                     headers={"Origin": "https://evil.example.net"})
    assert again.status_code == 403, "not even the right secret from a page"


def test_the_app_needs_no_tab_header_a_browser_does(client: TestClient, operator: Account) -> None:
    made = make(client)
    browser = app_client()
    browser.cookies.set("nexbooks_session", "something")
    refused = browser.post("/api/app/1/pairing/claim", json={"code": made["code"]})
    assert (refused.status_code, code_of(refused)) == (400, "client_required")
    assert claim_code(app_client(), made["code"]).status_code == 200


def test_a_body_with_nothing_in_it_is_refused_before_anything_is_looked_at(client: TestClient,
                                                                           operator: Account) -> None:
    made = make(client)
    box = app_client()
    for path, body in ((f"/api/app/1/pairing/{made['id']}/claim", {}), ("/api/app/1/pairing/claim", {}),
                       (f"/api/app/1/pairing/{made['id']}/claim", {"secret": ""}),
                       ("/api/app/1/pairing/claim", {"code": ""}), ("/api/app/1/pairing/claim", {"code": 31415926})):
        answer = box.post(path, json=body)
        assert (answer.status_code, code_of(answer)) == (422, "invalid_input"), (path, body)
    assert claim_secret(box, made).status_code == 200, "none of that counted"


def test_the_account_must_still_be_in_good_standing(client: TestClient, operator: Account) -> None:
    noor = make_account("noor")
    with new_client(noor) as browser:
        made = make(browser)
    with SessionLocal() as db:
        row = db.get(Account, noor.id)
        assert row is not None
        row.blocked_at = utcnow()
        db.commit()
    box = app_client()
    for _ in range(8):
        blocked = claim_secret(box, made)
        assert (blocked.status_code, code_of(blocked)) == (404, "pairing_unknown")
    with SessionLocal() as db:
        row = db.get(Account, noor.id)
        assert row is not None
        row.blocked_at = None
        row.locked_until = utcnow() + timedelta(minutes=15)
        db.commit()
    assert code_of(claim_code(box, made["code"])) == "pairing_unknown"
    with SessionLocal() as db:
        row = db.get(Account, noor.id)
        assert row is not None
        row.locked_until = None
        db.commit()
        assert db.scalar(select(Device.id)) is None
    assert claim_secret(box, made).status_code == 200, "no rest: the sender had the right secret all along"


def test_when_the_account_holds_too_many_devices_nothing_is_taken(client: TestClient, operator: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(devices, "MAX_DEVICES", 1)
    first = client.post("/api/v1/devices", json={"name": "script"}).json()
    made = make(client)
    full = claim_secret(app_client(), made)
    assert (full.status_code, code_of(full)) == (409, "too_many_devices")
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "waiting"
    assert client.delete(f"/api/v1/devices/{first['device']['id']}").status_code == 204
    assert claim_secret(app_client(), made).status_code == 200


def test_a_device_that_cannot_be_made_in_the_middle_leaves_the_code_open(client: TestClient, operator: Account,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The claim is one transaction: if anything after taking the code fails, the code is open as it was."""
    made = make(client)

    def fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(devices, "add", fails)
    with TestClient(app_client().app, raise_server_exceptions=False) as box:
        assert claim_secret(box, made).status_code == 500
    monkeypatch.undo()
    with SessionLocal() as db:
        row = db.get(AppPairing, made["id"])
        assert row is not None and row.state == "open" and row.used_at is None and row.device_id is None
    assert claim_secret(app_client(), made).status_code == 200


def test_the_secret_and_the_code_are_compared_in_constant_time(client: TestClient, operator: Account,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Both are compared as hashes with ``hmac.compare_digest``, also when there is no such pairing (an id nobody made
    takes as long as a wrong secret). A comparison with ``==`` or one that is skipped fails here."""
    seen: list[tuple[bytes, bytes]] = []
    import hmac as real

    def spy(first: bytes, second: bytes) -> bool:
        seen.append((first, second))
        return real.compare_digest(first, second)

    monkeypatch.setattr(pairing, "hmac", SimpleNamespace(compare_digest=spy))
    made = make(client)
    plain = made["code"].replace("-", "")
    box = app_client()
    claim_secret(box, {"id": "N" * 22, "secret": "nobody"})
    claim_secret(box, {"id": made["id"], "secret": "wrong"})
    claim_code(box, "ZZZZ-2222")
    claim_code(box, "AAAA-2222")
    assert len(seen) == 4, "one comparison for each guess, whatever it hits"
    assert all(len(first) == len(second) == 64 for first, second in seen)
    assert pairing.hash_secret("wrong").encode() == seen[1][1] and pairing.hash_secret(made["secret"]).encode() == seen[1][0]
    seen.clear()
    box = app_client()
    assert claim_secret(box, made).status_code == 200
    assert seen and all(first == second for first, second in seen)
    assert pairing.hash_secret(made["secret"]).encode() in {first for first, _second in seen}
    assert made["secret"].encode() not in {item for pair in seen for item in pair}, "never the secret itself"
    seen.clear()
    second = make(client)
    assert claim_code(app_client(), second["code"]).status_code == 200
    assert pairing.hash_code(second["code"].replace("-", "")).encode() in {first for first, _x in seen}
    assert plain.encode() not in {item for pair in seen for item in pair}


def test_nothing_secret_reaches_the_log_and_the_token_is_in_one_answer_only(client: TestClient,
                                                                            operator: Account) -> None:
    texts: list[str] = []
    logs.set_mode("trace", 30)
    try:
        made = make(client)
        texts.append(client.get(f"/api/v1/devices/pairing/{made['id']}").text)
        box = app_client()
        for guess in ("guess-one", "guess-two"):
            texts.append(box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": guess}).text)
        texts.append(claim_code(box, "ZZZZ-2222").text)
        texts.append(claim_code(box, "no").text)
        claimed = claim_secret(app_client(), made, "Ada's iPhone")
        token = claimed.json()["token"]
        texts.append(client.get(f"/api/v1/devices/pairing/{made['id']}").text)
        texts.append(client.get("/api/v1/devices").text)
        texts.append(claim_secret(app_client(), made).text)
        other = make(client)
        other_token = claim_code(app_client(), other["code"]).json()["token"]
        texts.append(client.delete(f"/api/v1/devices/pairing/{other['id']}").text)
        # A request with the token in its header: it must not reach the log either.
        assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        logs.set_mode(logs.DEFAULT_MODE)
    for handler in logging.getLogger().handlers:
        handler.flush()
    log = "".join(path.read_text(encoding="utf-8", errors="replace")
                  for path in logs.log_file().parent.glob(logs.log_file().name + "*"))
    assert "A device of tester was paired: Ada's iPhone" in log, "the operator can tell which device it was"
    for secret in (made["secret"], made["code"], made["code"].replace("-", ""), token, token[4:20], other_token,
                   other["secret"], other["code"], other["code"].replace("-", ""), pairing.hash_secret(made["secret"]),
                   pairing.hash_code(made["code"].replace("-", "")), devices.digest(token), "guess-one", "guess-two"):
        assert secret not in log, secret[:6]
    # Apart from the answer that hands it over, no answer holds a token or the secret.
    for text in texts:
        assert not TOKEN.search(text) and made["secret"] not in text and made["code"] not in text


def test_the_comparison_failing_one_character_in_gets_no_better_answer(client: TestClient, operator: Account) -> None:
    """Secrets that agree with the right one to the last character but one are as wrong as any other."""
    made = make(client)
    box = app_client()
    last = made["secret"][-1]
    almost = made["secret"][:-1] + ("A" if last != "A" else "B")
    answer = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": almost})
    assert (answer.status_code, code_of(answer)) == (404, "pairing_unknown")
    longer = box.post(f"/api/app/1/pairing/{made['id']}/claim", json={"secret": made["secret"] + "A"})
    assert code_of(longer) == "pairing_unknown"


def test_a_cleared_clock_cannot_revive_a_used_code(client: TestClient, operator: Account,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    made = make(client)
    assert claim_secret(app_client(), made).status_code == 200
    start = clock.now()
    monkeypatch.setattr(clock, "now", lambda: start - timedelta(days=1))
    assert code_of(claim_secret(app_client(), made)) == "pairing_used"


def test_nobody_who_has_not_signed_in_may_make_the_server_read_a_big_body(client: TestClient, operator: Account) -> None:
    """The two addresses of the app need no token, so their bodies are small: a few KB at most, checked before reading."""
    made = make(client)
    box = app_client()
    big = "x" * 5000
    for path, body in ((f"/api/app/1/pairing/{made['id']}/claim", {"secret": big}), ("/api/app/1/pairing/claim", {"code": big}),
                       (f"/api/app/1/pairing/{made['id']}/claim", {"secret": "s", "device_name": big})):
        answer = box.post(path, json=body)
        assert (answer.status_code, code_of(answer)) == (413, "too_large"), path
    # Without a declared length, too: counted while it arrives.
    streamed = box.post("/api/app/1/pairing/claim", content=iter([b'{"code": "', b"x" * 3000, b"x" * 3000, b'"}']),
                        headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413
    # Nothing of that counted, and the normal size still works.
    assert claim_secret(box, made).status_code == 200


# ---- Over all senders together ------------------------------------------------------------------------------------


def from_address(number: int) -> TestClient:
    """The app, from an address of its own (a net with many addresses: no sender is at rest on its own)."""
    return TestClient(app, base_url="http://testserver", client=(f"203.0.113.{number % 250 + 1}", 40000 + number))


@pytest.fixture
def still_clock(monkeypatch: pytest.MonkeyPatch) -> Moving:
    """The brake's clock, held still and moved by hand: nothing here depends on how long the machine takes."""
    moment = {"now": 1000.0}
    monkeypatch.setattr(clock, "monotonic", lambda: moment["now"])
    mover = Moving(monkeypatch)
    original = mover.pass_by

    def pass_by(**kwargs: float) -> None:
        original(**kwargs)
        moment["now"] += timedelta(**kwargs).total_seconds()

    mover.pass_by = pass_by  # type: ignore[method-assign]
    return mover


def test_over_all_addresses_only_thirty_wrong_codes_pass_in_ten_minutes(client: TestClient, operator: Account,
                                                                        still_clock: Moving) -> None:
    """Five tries per address is nothing against a net with many addresses; forty bits are not enough for that. So the
    wrong codes of everybody together are counted: the thirty-first meets the rest, the right code too."""
    made = make(client)
    assert pairing.CODE_GUESSES == 30 and pairing.CODE_WINDOW == 600
    for number in range(pairing.CODE_GUESSES):
        wrong = claim_code(from_address(number), "ABCD-2348")
        assert (wrong.status_code, code_of(wrong)) == (404, "pairing_unknown"), number
    late = claim_code(from_address(99), "ABCD-2348")
    assert (late.status_code, code_of(late)) == (429, "too_many_codes")
    assert 0 < int(late.headers["retry-after"]) <= 600 and late.json()["detail"]["retry_after"] == int(late.headers["retry-after"])
    right = claim_code(from_address(100), made["code"])
    assert (right.status_code, code_of(right)) == (429, "too_many_codes"), "also the right code: it must not tell"
    with SessionLocal() as db:
        assert db.scalar(select(Device.id)) is None
    # The QR code does not rest: its secret cannot be guessed.
    assert claim_secret(from_address(101), made).status_code == 200


def test_the_rest_over_all_addresses_ends_with_the_window(client: TestClient, operator: Account,
                                                          still_clock: Moving) -> None:
    made = make(client)
    for number in range(pairing.CODE_GUESSES):
        claim_code(from_address(number), "ABCD-2348")
    assert code_of(claim_code(from_address(50), made["code"])) == "too_many_codes"
    still_clock.pass_by(seconds=pairing.CODE_WINDOW - 1)
    assert code_of(claim_code(from_address(51), made["code"])) == "too_many_codes", "one second before the window ends"
    still_clock.pass_by(seconds=2)
    # The made code has run out in the meantime (ten minutes): a fresh one, and the window has room again.
    fresh = make(client)
    assert claim_code(from_address(52), fresh["code"]).status_code == 200


def test_a_right_code_and_a_known_answer_do_not_use_up_the_window(client: TestClient, operator: Account,
                                                                  still_clock: Moving,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a code that names nothing there is counts. Whoever shows a right code (also one that was used or ran out) is
    no guesser, and a text that cannot be a code never gets so far."""
    monkeypatch.setattr(devices, "MAX_DEVICES", 1000)
    for number in range(pairing.CODE_GUESSES + 10):
        made = make(client)
        assert claim_code(from_address(number), made["code"]).status_code == 200
        assert code_of(claim_code(from_address(number), made["code"])) == "pairing_used"
        assert code_of(claim_code(from_address(number), "ABCD-234")) == "pairing_code"
    last = make(client)
    for number in range(pairing.CODE_GUESSES):
        assert code_of(claim_code(from_address(number), "ABCD-2349")) == "pairing_unknown"
    assert code_of(claim_code(from_address(7), "ABCD-2349")) == "too_many_codes"
    assert claim_secret(from_address(7), last).status_code == 200


def test_many_wrong_codes_at_the_same_moment_do_not_get_past_the_window(client: TestClient, operator: Account,
                                                                        still_clock: Moving) -> None:
    barrier = threading.Barrier(60)

    def guess(number: int) -> int:
        box = from_address(number)
        barrier.wait()
        return int(claim_code(box, "ABCD-2342").status_code)

    with ThreadPoolExecutor(max_workers=60) as pool:
        codes = list(pool.map(guess, range(60)))
    assert sorted(codes) == [404] * pairing.CODE_GUESSES + [429] * (60 - pairing.CODE_GUESSES)
