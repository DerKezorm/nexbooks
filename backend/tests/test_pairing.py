"""Connecting the iPhone app, the browser's side: a signed-in person makes a code (a QR code and a short code),
sees whether the app took it, and withdraws it while nobody did. Whatever is secret is shown once and kept only as a
hash; a code lives ten minutes; an account holds five open ones; and nobody sees or withdraws another person's."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, AppPairing, Device
from app.services import pairing, settings_service

from .conftest import make_account, new_client
from .pairing_helpers import ADDRESS, Moving, app_client, claim_code, claim_secret, make


def test_a_person_makes_a_code_with_a_qr_code_a_link_and_a_code_to_type(client: TestClient, operator: Account) -> None:
    made = make(client)
    assert re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}", made["code"])
    assert made["address"] == ADDRESS
    parts = urlsplit(made["link"])
    assert (parts.scheme, parts.netloc, parts.path) == ("nexbooks", "connect", "")
    assert parse_qs(parts.query) == {"address": [ADDRESS], "pairing": [made["id"]], "secret": [made["secret"]]}
    assert made["qr_svg"].startswith("<svg") and "xmlns=" in made["qr_svg"], "an <img> needs the namespace"
    assert len(made["secret"]) >= 40 and len(made["id"]) >= 20
    assert pairing.LIFETIME.total_seconds() == 600


def test_every_person_makes_one_for_themselves_not_only_the_operator(client: TestClient, operator: Account) -> None:
    with new_client(make_account("noor")) as noor:
        made = make(noor)
    claimed = claim_secret(app_client(), made)
    assert claimed.status_code == 200
    assert claimed.json()["account"]["name"] == "noor", "the device speaks for whoever made the code"
    with SessionLocal() as db:
        owner = db.scalar(select(Account).where(Account.name == "noor"))
        assert owner is not None and db.scalar(select(Device.account_id)) == owner.id


def test_the_secret_and_the_code_are_kept_only_as_hashes(client: TestClient, operator: Account) -> None:
    made = make(client)
    plain = made["code"].replace("-", "")
    with SessionLocal() as db:
        row = db.get(AppPairing, made["id"])
        assert row is not None
        assert row.secret_hash == pairing.hash_secret(made["secret"]) and row.code_hash == pairing.hash_code(plain)
        everything = " ".join(str(value) for value in row.__dict__.values())
        assert made["secret"] not in everything and plain not in everything
    # Not in the database file either (a backup is a copy of it): the files are read as bytes.
    from app.db import _settings

    for path in _settings.database_path.parent.glob(_settings.database_path.name + "*"):
        data = path.read_bytes()
        assert made["secret"].encode() not in data and plain.encode() not in data, path.name


def test_the_secret_and_the_code_are_shown_once_and_the_state_holds_neither(client: TestClient,
                                                                            operator: Account) -> None:
    made = make(client)
    state = client.get(f"/api/v1/devices/pairing/{made['id']}")
    assert state.status_code == 200 and state.json() == {"state": "waiting", "device": None}
    claim_secret(app_client(), made, "Ada's iPhone")
    state = client.get(f"/api/v1/devices/pairing/{made['id']}")
    assert state.json()["state"] == "connected" and state.json()["device"]["name"] == "Ada's iPhone"
    for text in (state.text, client.get("/api/v1/devices").text):
        assert made["secret"] not in text and made["code"] not in text and made["code"].replace("-", "") not in text
        assert not re.search(r"nbd_[A-Za-z0-9_-]{12,}", text), "the token is in the answer to the app only"


def test_codes_are_random_and_use_an_alphabet_without_look_alikes() -> None:
    seen = set()
    for _ in range(60):
        code = pairing._new_code()
        assert len(code) == 8 and set(code) <= set(pairing.CODE_ALPHABET)
        seen.add(code)
    assert len(seen) == 60, "a repeat in sixty draws from forty bits means the draw is not random"
    assert not set("01OIL") & set(pairing.CODE_ALPHABET) and len(pairing.CODE_ALPHABET) == 31
    assert pairing.show_code("ABCD2345") == "ABCD-2345"


def test_the_public_address_of_the_settings_goes_before_the_browsers(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://outside.example.com"})
    made = make(client, "http://192.0.2.7:8580")
    assert made["address"] == "https://outside.example.com"
    assert parse_qs(urlsplit(made["link"]).query)["address"] == ["https://outside.example.com"]


@pytest.mark.parametrize("address", ["", "   ", "ftp://books.example.com", "javascript:alert(1)", "books.example.com",
                                      "https://user:pw@books.example.com", "https://books.example.com/path",
                                      "https://books.example.com?x=1", "https://books.example.com#a", "https://",
                                      "https://books.example.com:99999999", "https://a b.example.com"])
def test_an_address_that_is_not_plain_is_refused_and_nothing_is_made(client: TestClient, operator: Account,
                                                                      address: str) -> None:
    answer = client.post("/api/v1/devices/pairing", json={"address": address})
    assert (answer.status_code, answer.json()["detail"]["code"]) == (422, "pairing_address")
    with SessionLocal() as db:
        assert db.scalar(select(AppPairing.id)) is None


def test_no_address_at_all_is_refused(client: TestClient, operator: Account) -> None:
    answer = client.post("/api/v1/devices/pairing", json={})
    assert (answer.status_code, answer.json()["detail"]["code"]) == (422, "pairing_address")


def test_the_address_the_browser_names_must_be_the_one_it_is_at(client: TestClient, operator: Account) -> None:
    """A page cannot put another host into a code: when the browser sent an Origin, the address is that very Origin."""
    other = client.post("/api/v1/devices/pairing", json={"address": "https://evil.example.net"},
                        headers={"Origin": ADDRESS})
    assert (other.status_code, other.json()["detail"]["code"]) == (422, "pairing_address")
    odd = client.post("/api/v1/devices/pairing", json={"address": ADDRESS}, headers={"Origin": "null"})
    assert odd.status_code == 422
    ok = client.post("/api/v1/devices/pairing", json={"address": "https://BOOKS.example.com"},
                     headers={"Origin": ADDRESS})
    assert ok.status_code == 201, "the case of the host is no difference"
    with SessionLocal() as db:
        assert len(db.scalars(select(AppPairing.id)).all()) == 1


def test_the_settings_address_needs_no_origin_to_match(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://outside.example.com"})
    made = client.post("/api/v1/devices/pairing", json={"address": "http://192.0.2.7:8580"},
                       headers={"Origin": "http://192.0.2.7:8580"})
    assert made.status_code == 201 and made.json()["address"] == "https://outside.example.com"


def test_a_code_lives_ten_minutes(client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    made = make(client)
    moving.pass_by(minutes=9, seconds=59)
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "waiting"
    moving.pass_by(seconds=2)
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "expired"
    answer = claim_secret(app_client(), made)
    assert (answer.status_code, answer.json()["detail"]["code"]) == (410, "pairing_expired")
    answer = claim_code(app_client(), made["code"])
    assert (answer.status_code, answer.json()["detail"]["code"]) == (410, "pairing_expired")
    with SessionLocal() as db:
        assert db.scalar(select(Device.id)) is None, "a code nobody used leaves no device behind"


def test_what_ran_out_goes_after_a_while_and_leaves_nothing(client: TestClient, operator: Account,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    ran_out = make(client)
    used = make(client)
    claim_secret(app_client(), used)
    still_open = make(client)
    moving.pass_by(minutes=10, seconds=30)
    with SessionLocal() as db:
        assert pairing.sweep(db) == 0, "it stays for a few minutes: the browser that asks reads 'expired', not 'gone'"
    assert client.get(f"/api/v1/devices/pairing/{ran_out['id']}").json()["state"] == "expired"
    assert client.get(f"/api/v1/devices/pairing/{used['id']}").json()["state"] == "connected"
    moving.pass_by(minutes=10)
    with SessionLocal() as db:
        assert pairing.sweep(db) == 3
        assert db.scalar(select(AppPairing.id)) is None
        assert db.query(Device).count() == 1, "the device of the used code stays: it is a device like any other"
    assert client.get(f"/api/v1/devices/pairing/{ran_out['id']}").status_code == 404
    assert claim_secret(app_client(), still_open).json()["detail"]["code"] == "pairing_unknown"


def test_making_a_code_clears_what_ran_out(client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    old = make(client)
    moving.pass_by(minutes=30)
    make(client)
    with SessionLocal() as db:
        assert db.get(AppPairing, old["id"]) is None
        assert len(db.scalars(select(AppPairing.id)).all()) == 1


def test_an_account_holds_at_most_five_open_codes(client: TestClient, operator: Account,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    moving = Moving(monkeypatch)
    made = [make(client) for _ in range(5)]
    sixth = client.post("/api/v1/devices/pairing", json={"address": ADDRESS})
    assert (sixth.status_code, sixth.json()["detail"]["code"]) == (429, "pairing_limit")
    assert sixth.json()["detail"]["max"] == 5
    with SessionLocal() as db:
        assert len(db.scalars(select(AppPairing.id)).all()) == 5, "the refused one left nothing"
    # Another person has their own five.
    with new_client(make_account("noor")) as noor:
        assert noor.post("/api/v1/devices/pairing", json={"address": ADDRESS}).status_code == 201
    # A used one and a withdrawn one do not count, and neither does one that ran out.
    claim_secret(app_client(), made[0])
    assert client.delete(f"/api/v1/devices/pairing/{made[1]['id']}").status_code == 204
    assert client.post("/api/v1/devices/pairing", json={"address": ADDRESS}).status_code == 201
    assert client.post("/api/v1/devices/pairing", json={"address": ADDRESS}).status_code == 201
    assert client.post("/api/v1/devices/pairing", json={"address": ADDRESS}).status_code == 429
    moving.pass_by(minutes=11)
    assert client.post("/api/v1/devices/pairing", json={"address": ADDRESS}).status_code == 201


def test_many_codes_at_the_same_moment_do_not_get_past_five(client: TestClient, operator: Account) -> None:
    boxes = [new_client(operator) for _ in range(12)]
    barrier = threading.Barrier(12)

    def ask(box: TestClient) -> int:
        barrier.wait()
        return box.post("/api/v1/devices/pairing", json={"address": ADDRESS}).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = sorted(pool.map(ask, boxes))
    assert codes == [201] * 5 + [429] * 7
    with SessionLocal() as db:
        assert len(db.scalars(select(AppPairing.id)).all()) == 5


def test_a_code_nobody_scanned_is_withdrawn_and_is_gone(client: TestClient, operator: Account) -> None:
    made = make(client)
    assert client.delete(f"/api/v1/devices/pairing/{made['id']}").status_code == 204
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").status_code == 404
    assert client.delete(f"/api/v1/devices/pairing/{made['id']}").status_code == 404, "already gone"
    for answer in (claim_secret(app_client(), made), claim_code(app_client(), made["code"])):
        assert (answer.status_code, answer.json()["detail"]["code"]) == (404, "pairing_unknown")
    with SessionLocal() as db:
        assert db.scalar(select(AppPairing.id)) is None and db.scalar(select(Device.id)) is None


def test_a_code_the_app_took_cannot_be_withdrawn_and_its_device_stays(client: TestClient, operator: Account) -> None:
    made = make(client)
    token = claim_secret(app_client(), made).json()["token"]
    assert client.delete(f"/api/v1/devices/pairing/{made['id']}").status_code == 204, "nothing to withdraw, nothing broken"
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "connected"
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_withdrawing_does_not_delete_a_code_the_app_took_between_the_look_and_the_delete(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    """The browser withdraws, the app fetches at that very moment: the pairing was open when it was looked at, and used
    when the delete came. The delete is conditional on the state, so the used pairing (and with it the proof of which
    device it made) stays."""
    made = make(client)
    real = pairing._own

    def look_then_let_the_app_take_it(db: object, account: Account, pairing_id: str) -> AppPairing:
        row = real(db, account, pairing_id)  # type: ignore[arg-type]
        assert claim_secret(app_client(), made).status_code == 200
        return row

    monkeypatch.setattr(pairing, "_own", look_then_let_the_app_take_it)
    assert client.delete(f"/api/v1/devices/pairing/{made['id']}").status_code == 204
    monkeypatch.undo()
    with SessionLocal() as db:
        row = db.get(AppPairing, made["id"])
        assert row is not None and row.state == "used" and row.device_id is not None
        assert db.get(Device, row.device_id) is not None
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "connected"


def test_the_device_the_app_made_stands_in_the_list_and_is_revoked_like_any_other(client: TestClient,
                                                                                   operator: Account) -> None:
    made = make(client)
    token = claim_secret(app_client(), made, "  Ada's   iPhone ").json()["token"]
    listed = client.get("/api/v1/devices").json()
    assert [row["name"] for row in listed] == ["Ada's iPhone"] and listed[0]["prefix"] == token[:8]
    assert client.delete(f"/api/v1/devices/{listed[0]['id']}").status_code == 204
    assert app_client().get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "connected"


def test_another_persons_code_is_neither_seen_nor_withdrawn(client: TestClient, operator: Account) -> None:
    made = make(client)
    with new_client(make_account("noor")) as noor:
        assert noor.get(f"/api/v1/devices/pairing/{made['id']}").status_code == 404
        assert noor.delete(f"/api/v1/devices/pairing/{made['id']}").status_code == 404
        mine = make(noor)
        assert client.get(f"/api/v1/devices/pairing/{mine['id']}").status_code == 404
    assert client.get(f"/api/v1/devices/pairing/{made['id']}").json()["state"] == "waiting", "still there"
    assert claim_secret(app_client(), made).status_code == 200


def test_the_codes_need_a_signed_in_browser_and_a_device_does_not_make_one(client: TestClient,
                                                                           operator: Account) -> None:
    made = make(client)
    token = claim_secret(app_client(), made).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    for method, path in (("post", "/api/v1/devices/pairing"), ("get", f"/api/v1/devices/pairing/{made['id']}"),
                         ("delete", f"/api/v1/devices/pairing/{made['id']}")):
        answer = getattr(app_client(), method)(path, headers=bearer,
                                               **({"json": {"address": ADDRESS}} if method == "post" else {}))
        assert (answer.status_code, answer.json()["detail"]["code"]) == (403, "browser_only"), path
    nobody = app_client()
    nobody.headers["X-Nexbooks-Client"] = "tab-nobody000"
    for method, path in (("post", "/api/v1/devices/pairing"), ("get", f"/api/v1/devices/pairing/{made['id']}"),
                         ("delete", f"/api/v1/devices/pairing/{made['id']}")):
        answer = getattr(nobody, method)(path, **({"json": {"address": ADDRESS}} if method == "post" else {}))
        assert answer.status_code == 401, path


def test_a_new_password_or_signing_out_everywhere_ends_the_codes_made_before(client: TestClient,
                                                                            operator: Account) -> None:
    before = make(client)
    other = make(client)
    assert client.post("/api/v1/auth/logout-all").status_code == 204
    for made in (before, other):
        answer = claim_secret(app_client(), made)
        assert (answer.status_code, answer.json()["detail"]["code"]) == (404, "pairing_unknown")
    with SessionLocal() as db:
        assert db.scalar(select(AppPairing.id)) is None and db.scalar(select(Device.id)) is None


def test_the_operators_sign_out_of_an_account_ends_its_codes(client: TestClient, operator: Account) -> None:
    noor = make_account("noor")
    with new_client(noor) as browser:
        made = make(browser)
    assert client.post(f"/api/v1/accounts/{noor.id}/sign-out").status_code == 204
    assert claim_secret(app_client(), made).json()["detail"]["code"] == "pairing_unknown"


def test_a_code_goes_with_its_account(client: TestClient, operator: Account) -> None:
    noor = make_account("noor")
    with new_client(noor) as browser:
        made = make(browser)
    answer = client.request("DELETE", f"/api/v1/accounts/{noor.id}",
                            json={"confirm_name": "noor", "current_password": "correct horse battery"})
    assert answer.status_code == 204
    with SessionLocal() as db:
        assert db.get(AppPairing, made["id"]) is None
    assert claim_secret(app_client(), made).status_code == 404


def test_the_state_of_a_code_is_asked_by_its_id_only(client: TestClient, operator: Account) -> None:
    assert client.get("/api/v1/devices/pairing/" + "x" * 65).status_code == 422
    assert client.get("/api/v1/devices/pairing/no-such-code").status_code == 404


def test_the_description_of_the_interface_names_the_routes() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/devices/pairing"]) == {"post"}
    assert set(paths["/api/v1/devices/pairing/{pairing_id}"]) == {"get", "delete"}
