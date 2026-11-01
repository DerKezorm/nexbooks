"""Connections for e-readers: a person makes them in a browser, sees them, deletes them; the secret is shown once and only
its hash is kept; the operator switches the feature off and blocks single connections; nothing but a signed-in browser
reaches the ways that make, list and delete them."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, EreaderLink, KoboListed, utcnow
from app.services import apikeys, devices, ereaders

from .conftest import make_account, new_client
from .ereader_helpers import Reading, connect


def test_a_person_makes_lists_and_deletes_a_connection(ada_browser: TestClient) -> None:
    made = ada_browser.post("/api/v1/ereaders/links", json={"name": "  Reader  ", "kind": "opds"})
    assert made.status_code == 201
    body = made.json()
    assert body["username"] == "ada" and body["secret"].startswith("nbo_") and len(body["secret"]) >= 32
    assert body["link"]["name"] == "Reader" and body["link"]["kind"] == "opds"
    assert body["link"]["prefix"] == body["secret"][:8] and body["link"]["blocked"] is False
    listed = ada_browser.get("/api/v1/ereaders").json()
    assert listed["allowed"] is True and [item["id"] for item in listed["links"]] == [body["link"]["id"]]
    # The secret is never in a list again.
    assert body["secret"] not in str(listed)
    assert ada_browser.delete(f"/api/v1/ereaders/links/{body['link']['id']}").status_code == 204
    assert ada_browser.get("/api/v1/ereaders").json()["links"] == []
    assert ada_browser.delete(f"/api/v1/ereaders/links/{body['link']['id']}").status_code == 404


def test_only_the_hash_of_the_secret_is_kept(ada_browser: TestClient) -> None:
    secret = connect(ada_browser)
    with SessionLocal() as db:
        row = db.scalars(select(EreaderLink)).one()
        assert row.token_hash == ereaders.digest(secret) and row.token_hash != secret
        assert secret not in " ".join(str(getattr(row, column.name)) for column in EreaderLink.__table__.columns)


def test_a_person_sees_and_deletes_only_their_own(reading: Reading, ada_browser: TestClient) -> None:
    mine = ada_browser.post("/api/v1/ereaders/links", json={"name": "mine"}).json()["link"]["id"]
    with new_client(reading.shelf.ben) as ben:
        theirs = ben.post("/api/v1/ereaders/links", json={"name": "theirs"}).json()["link"]["id"]
        assert [item["id"] for item in ben.get("/api/v1/ereaders").json()["links"]] == [theirs]
        assert ben.delete(f"/api/v1/ereaders/links/{mine}").status_code == 404
    assert ada_browser.delete(f"/api/v1/ereaders/links/{theirs}").status_code == 404
    with SessionLocal() as db:
        assert len(db.scalars(select(EreaderLink)).all()) == 2


def test_an_account_holds_at_most_twenty_connections(ada_browser: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ereaders, "MAX_LINKS", 2)
    connect(ada_browser, "one")
    connect(ada_browser, "two")
    third = ada_browser.post("/api/v1/ereaders/links", json={"name": "three"})
    assert third.status_code == 409 and third.json()["detail"]["code"] == "too_many_links"


def test_only_the_kinds_with_a_door_can_be_made(ada_browser: TestClient) -> None:
    for kind in ("kindle", "other", "KOBO", ""):
        assert ada_browser.post("/api/v1/ereaders/links", json={"name": "x", "kind": kind}).status_code == 422
    assert ada_browser.post("/api/v1/ereaders/links", json={"name": "", "kind": "opds"}).status_code == 422
    assert ada_browser.get("/api/v1/ereaders").json()["links"] == []


def test_a_device_token_a_key_and_a_connection_never_make_list_or_delete(reading: Reading,
                                                                          ada_browser: TestClient) -> None:
    secret = connect(ada_browser)
    link_id = ada_browser.get("/api/v1/ereaders").json()["links"][0]["id"]
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        _device, device_token = devices.make(db, ada, "phone")
    # A key needs the operator's switch for keys, and a made-up one is no worse than a real one at these ways.
    with SessionLocal() as db:
        from app.services import settings_service

        settings_service.save(db, {"api_keys_allowed": True})
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        _key, key = apikeys.make(db, ada, "dash", "read")
    tab = {"X-Nexbooks-Client": "program-tab"}
    with TestClient(ada_browser.app, base_url="http://testserver", headers=tab) as program:
        for token in (device_token, key, secret):
            bearer = {"Authorization": f"Bearer {token}"}
            calls = [
                program.get("/api/v1/ereaders", headers=bearer),
                program.post("/api/v1/ereaders/links", headers=bearer, json={"name": "x"}),
                program.delete(f"/api/v1/ereaders/links/{link_id}", headers=bearer),
                program.put("/api/v1/ereaders/kindle", headers=bearer, json={"address": "a@" + "kindle.com"}),
                program.post("/api/v1/ereaders/kindle/test", headers=bearer),
            ]
            assert [call.status_code for call in calls] in ([403] * 5, [401] * 5), [call.text for call in calls]
            if token == device_token:
                assert {call.json()["detail"]["code"] for call in calls} == {"browser_only"}
    # The connection is still there, and the address still empty.
    assert len(ada_browser.get("/api/v1/ereaders").json()["links"]) == 1
    assert ada_browser.get("/api/v1/ereaders").json()["kindle"]["address"] == ""


def test_the_operator_switches_the_feature_off_and_the_connections_stay_deletable(
        client: TestClient, operator: Account, reading: Reading) -> None:
    with new_client(reading.shelf.ada) as ada:
        connect(ada)
        assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).json()["ereaders_allowed"] is False
        refused = ada.post("/api/v1/ereaders/links", json={"name": "again"})
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "ereaders_off"
        listed = ada.get("/api/v1/ereaders").json()
        assert listed["allowed"] is False and len(listed["links"]) == 1
        assert ada.delete(f"/api/v1/ereaders/links/{listed['links'][0]['id']}").status_code == 204
        assert client.put("/api/v1/settings", json={"ereaders_allowed": True}).json()["ereaders_allowed"] is True
        assert ada.post("/api/v1/ereaders/links", json={"name": "again"}).status_code == 201


def test_it_is_on_from_the_start(client: TestClient, operator: Account) -> None:
    assert client.get("/api/v1/settings").json()["ereaders_allowed"] is True


def test_the_operator_sees_every_connection_and_blocks_one_and_nobody_else_does(
        client: TestClient, operator: Account, reading: Reading) -> None:
    with new_client(reading.shelf.ada) as ada:
        secret = connect(ada, "kitchen")
        every = client.get("/api/v1/admin/ereaders/links").json()
        assert [(item["account"], item["name"], item["blocked"]) for item in every] == [("ada", "kitchen", False)]
        assert secret not in str(every)
        # A member neither lists nor blocks.
        assert ada.get("/api/v1/admin/ereaders/links").status_code == 403
        assert ada.post(f"/api/v1/admin/ereaders/links/{every[0]['id']}/block").status_code == 403
        blocked = client.post(f"/api/v1/admin/ereaders/links/{every[0]['id']}/block")
        assert blocked.status_code == 200 and blocked.json()["blocked"] is True
        # The person sees it blocked, and cannot delete it: only the operator lifts a block, by deleting the connection.
        mine = ada.get("/api/v1/ereaders").json()["links"]
        assert mine[0]["blocked"] is True
        refused = ada.delete(f"/api/v1/ereaders/links/{mine[0]['id']}")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "link_blocked"
        assert len(ada.get("/api/v1/ereaders").json()["links"]) == 1
        assert ada.delete(f"/api/v1/admin/ereaders/links/{every[0]['id']}").status_code == 403
        assert client.delete(f"/api/v1/admin/ereaders/links/{every[0]['id']}").status_code == 204
        assert ada.get("/api/v1/ereaders").json()["links"] == []
    assert client.post("/api/v1/admin/ereaders/links/999/block").status_code == 404
    assert client.delete("/api/v1/admin/ereaders/links/999").status_code == 404


def test_the_connections_go_with_the_account(reading: Reading, ada_browser: TestClient) -> None:
    connect(ada_browser)
    other = make_account("rita")
    with SessionLocal() as db:
        db.add(EreaderLink(account_id=other.id, kind="opds", name="x", token_hash="h" * 64, prefix="nbo_"))
        db.commit()
        row = db.get(Account, reading.shelf.ada.id)
        assert row is not None
        db.delete(row)
        db.commit()
        assert [link.account_id for link in db.scalars(select(EreaderLink))] == [other.id]


def test_wrong_passwords_of_a_stranger_do_not_lock_the_connections_of_the_account(
        reading: Reading, ada_browser: TestClient) -> None:
    """Whoever knows a name can lock the sign-in with the password. A connection has a secret of its own, so it goes on;
    the account that the operator blocked is another matter, and stops every one."""
    from .ereader_helpers import basic, opds_client
    from .test_kobo import device
    from .test_kosync import keyed

    opds, sync, kobo = connect(ada_browser, "a", "opds"), connect(ada_browser, "b", "kosync"), connect(
        ada_browser, "c", "kobo")
    with new_client() as stranger:
        for attempt in range(5):
            stranger.post("/api/v1/auth/login", json={"name": "ada", "password": f"wrong-{attempt}"})
        with SessionLocal() as db:
            row = db.get(Account, reading.shelf.ada.id)
            assert row is not None and row.locked_until is not None, "the sign-in is locked"

    def states() -> tuple[int, int, int]:
        reader = opds_client()
        return (reader.get("/opds", headers=basic("ada", opds)).status_code,
                reader.get("/kosync/users/auth", headers=keyed("ada", sync)).status_code,
                device().get(f"/kobo/{kobo}/v1/initialization").status_code)

    assert states() == (200, 200, 200)
    with SessionLocal() as db:
        row = db.get(Account, reading.shelf.ada.id)
        assert row is not None
        row.blocked_at = utcnow()
        db.commit()
    assert states() == (401, 401, 401)


def _every_connection_answers(opds: str, sync: str, kobo: str) -> tuple[int, int, int]:
    from .ereader_helpers import basic, opds_client
    from .test_kobo import device
    from .test_kosync import keyed

    reader = opds_client()
    return (reader.get("/opds", headers=basic("ada", opds)).status_code,
            reader.get("/kosync/users/auth", headers=keyed("ada", sync)).status_code,
            device().get(f"/kobo/{kobo}/v1/initialization").status_code)


@pytest.mark.parametrize("how", ["sign-out-everywhere", "new-password", "operator-sign-out", "operator-block"])
def test_what_one_does_after_an_account_was_taken_ends_its_connections_too(
        client: TestClient, operator: Account, reading: Reading, how: str) -> None:
    """A thief with a stolen session makes connections for himself; they must not outlive what the owner does about it.
    The list of books for the Kobo is the person's own and stays."""
    from .conftest import PASSWORD
    from .test_kobo import edition_of

    with new_client(reading.shelf.ada) as ada, new_client(reading.shelf.ben) as ben:
        secrets_ = (connect(ada, "a", "opds"), connect(ada, "b", "kosync"), connect(ada, "c", "kobo"))
        connect(ben, "bens", "opds")
        assert ada.put(f"/api/v1/editions/{edition_of('The Loud Orchard')}/kobo").status_code == 200
        assert _every_connection_answers(*secrets_) == (200, 200, 200)
        if how == "sign-out-everywhere":
            assert ada.post("/api/v1/auth/logout-all").status_code == 204
        elif how == "new-password":
            assert ada.put("/api/v1/auth/password", json={"current": PASSWORD, "new": "a new password 123"}
                           ).status_code == 204
        elif how == "operator-sign-out":
            assert client.post(f"/api/v1/accounts/{reading.shelf.ada.id}/sign-out").status_code == 204
        else:
            assert client.post(f"/api/v1/accounts/{reading.shelf.ada.id}/block",
                               json={"current_password": PASSWORD}).status_code == 204
        assert _every_connection_answers(*secrets_) == (401, 401, 401)
        with SessionLocal() as db:
            mine = db.scalars(select(EreaderLink).where(EreaderLink.account_id == reading.shelf.ada.id)).all()
            others = db.scalars(select(EreaderLink).where(EreaderLink.account_id == reading.shelf.ben.id)).all()
            kept = db.scalar(select(func.count()).select_from(KoboListed).where(KoboListed.user_id == reading.shelf.ada.id))
        assert mine == [] and len(others) == 1, "only the connections of that account go"
        assert kept == 1, "the list for the Kobo is the person's own"


def test_a_block_of_the_operator_is_not_undone_by_deleting_the_connection_and_making_another(
        client: TestClient, operator: Account, reading: Reading) -> None:
    """The operator blocked a connection for good. The person cannot delete it, and cannot make a new one of the kind
    while it is there; a connection of another kind is no such door. Signing out everywhere does not delete it either.
    The operator deletes it, and the person may make one again."""
    from .conftest import PASSWORD
    from .ereader_helpers import basic, opds_client

    with new_client(reading.shelf.ada) as ada:
        old = connect(ada, "kitchen", "opds")
        link = ada.get("/api/v1/ereaders").json()["links"][0]["id"]
        assert client.post(f"/api/v1/admin/ereaders/links/{link}/block").status_code == 200
        again = ada.post("/api/v1/ereaders/links", json={"name": "kitchen", "kind": "opds"})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "kind_blocked"
        assert ada.post("/api/v1/ereaders/links", json={"name": "sync", "kind": "kosync"}).status_code == 201
        assert ada.post("/api/v1/auth/logout-all").status_code == 204
        assert ada.put("/api/v1/auth/password", json={"current": PASSWORD, "new": "a new password 123"}
                       ).status_code == 204
        mine = ada.get("/api/v1/ereaders").json()["links"]
        assert [(item["kind"], item["blocked"]) for item in mine] == [("opds", True)], "the block stays, the rest goes"
        assert ada.post("/api/v1/ereaders/links", json={"name": "x", "kind": "opds"}).status_code == 409
        assert opds_client().get("/opds", headers=basic("ada", old)).status_code == 401
        assert client.delete(f"/api/v1/admin/ereaders/links/{link}").status_code == 204
        fresh = connect(ada, "kitchen", "opds")
    assert opds_client().get("/opds", headers=basic("ada", fresh)).status_code == 200


def test_the_list_says_which_address_a_kobo_would_be_handed(ada_browser: TestClient, client: TestClient,
                                                           operator: Account) -> None:
    """Behind a proxy that nexbooks does not know of, a page seen over https is answered with the address http: the
    person's page can tell, and warns."""
    assert ada_browser.get("/api/v1/ereaders").json()["kobo_base"] == "http://testserver"
    assert client.put("/api/v1/settings", json={"public_url": "https://books.example.com"}).status_code == 200
    assert ada_browser.get("/api/v1/ereaders").json()["kobo_base"] == "https://books.example.com"


def test_a_crowd_of_requests_at_the_limit_cannot_make_more_connections_than_the_limit(
        reading: Reading, ada_browser: TestClient) -> None:
    """The limit is kept by the statement that writes the connection, not by a count made before it: twelve requests at the
    same moment with three places left end with exactly three more connections."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    room = 3
    for number in range(ereaders.MAX_LINKS - room):
        connect(ada_browser, f"n{number}")
    start = threading.Barrier(12)

    def make(index: int) -> int:
        with new_client(reading.shelf.ada) as browser:
            start.wait()
            return browser.post("/api/v1/ereaders/links", json={"name": f"m{index}", "kind": "opds"}).status_code

    with ThreadPoolExecutor(12) as pool:
        codes = list(pool.map(make, range(12)))
    with SessionLocal() as db:
        held = len(db.scalars(select(EreaderLink.id).where(EreaderLink.account_id == reading.shelf.ada.id)).all())
    assert held == ereaders.MAX_LINKS
    assert sorted(codes) == [201] * room + [409] * (12 - room)
