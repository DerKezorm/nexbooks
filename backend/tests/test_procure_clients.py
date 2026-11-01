"""The download programs of the operator (``/api/v1/procure/clients``): SABnzbd, NZBGet, qBittorrent, Transmission and
Deluge, each tested before it is saved (version, login, the category made and read back), the credentials sealed and
never shown, and every way a program can fail said by a code."""

from __future__ import annotations

import base64
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, DownloadClient, PathMapping
from app.security import decrypt_secret
from app.services.procure import gate, programs

from .conftest import TAB, make_account, new_client
from .procure_fakes import FakeDeluge, FakeNetwork, FakeNzbget, FakeQbittorrent, FakeSabnzbd, FakeTransmission

SECRET = secrets.token_hex(12)
USER = "reader" + secrets.token_hex(2)


@dataclass
class Kit:
    kind: str
    fake: Any
    url: str
    username: str
    secret: str
    protocol: str
    folder: str

    def body(self, **over: Any) -> dict[str, Any]:
        return {"kind": self.kind, "url": self.url, "username": self.username, "secret": self.secret, **over}


def make(kind: str, net: FakeNetwork) -> Kit:
    host = f"{kind}.example.com"
    if kind == "sabnzbd":
        return Kit(kind, net.add(FakeSabnzbd(host, SECRET)), f"http://{host}:8080", "", SECRET, "usenet",
                   "/downloads/complete/nexbooks")
    if kind == "nzbget":
        return Kit(kind, net.add(FakeNzbget(host, USER, SECRET)), f"http://{host}:6789", USER, SECRET, "usenet",
                   "/downloads/dst/nexbooks")
    if kind == "qbittorrent":
        return Kit(kind, net.add(FakeQbittorrent(host, USER, SECRET)), f"http://{host}:8081", USER, SECRET, "torrent",
                   "/downloads/nexbooks")
    if kind == "transmission":
        return Kit(kind, net.add(FakeTransmission(host, USER, SECRET)), f"http://{host}:9091", USER, SECRET, "torrent",
                   "/data/torrents/nexbooks")
    return Kit(kind, net.add(FakeDeluge(host, SECRET)), f"http://{host}:8112", "", SECRET, "torrent",
               "/downloads/deluge")


KINDS = ["sabnzbd", "nzbget", "qbittorrent", "transmission", "deluge"]


def listing(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/api/v1/procure/clients").json()["clients"]  # type: ignore[no-any-return]


def stored() -> list[DownloadClient]:
    with SessionLocal() as db:
        rows = list(db.query(DownloadClient).order_by(DownloadClient.id))
        for row in rows:
            db.expunge(row)
    return rows


# --- Saving, with the test --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_program_is_tested_made_its_category_and_saved(client: TestClient, operator: Account, net: FakeNetwork,
                                                         kind: str) -> None:
    kit = make(kind, net)
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["kind"] == kind and body["protocol"] == kit.protocol and body["state"] == "ready"
    assert body["category"] == "nexbooks" and body["version"] and body["enabled"] is True
    assert body["secret_set"] is True and "secret" not in body
    if kind == "sabnzbd":
        assert [entry["name"] for entry in kit.fake.categories] == ["nexbooks"], "made and found again"
    elif kind == "qbittorrent":
        assert list(kit.fake.categories) == ["nexbooks"] and kit.fake.logins == 1
    elif kind == "deluge":
        assert kit.fake.plugins == ["Label"] and kit.fake.labels == ["nexbooks"]
    assert [item["name"] for item in listing(client)] == [programs.DEFAULT_NAMES[kind]]


@pytest.mark.parametrize("kind", KINDS)
def test_the_credentials_are_sealed_and_never_shown_or_logged(client: TestClient, operator: Account, net: FakeNetwork,
                                                               caplog: pytest.LogCaptureFixture, kind: str) -> None:
    caplog.set_level(logging.DEBUG)
    kit = make(kind, net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    row = stored()[0]
    assert row.secret_enc and SECRET not in row.secret_enc
    assert decrypt_secret(row.secret_enc, programs.SECRET_CONTEXT) == SECRET
    shown = client.get("/api/v1/procure/clients").text + client.post(f"/api/v1/procure/clients/{made['id']}/test").text
    basic = base64.b64encode(f"{USER}:{SECRET}".encode()).decode()
    for text in (shown, caplog.text, str(made)):
        assert SECRET not in text and basic not in text
    db_file = sqlite3.connect(get_settings().database_path)
    try:
        dump = "\n".join(db_file.iterdump())
    finally:
        db_file.close()
    assert SECRET not in dump and basic not in dump


@pytest.mark.parametrize(("kind", "reason"), [("sabnzbd", "wrong_key"), ("nzbget", "wrong_password"),
                                              ("qbittorrent", "wrong_password"), ("transmission", "wrong_password"),
                                              ("deluge", "wrong_password")])
def test_credentials_that_are_refused_save_nothing(client: TestClient, operator: Account, net: FakeNetwork, kind: str,
                                                   reason: str) -> None:
    kit = make(kind, net)
    answer = client.post("/api/v1/procure/clients", json=kit.body(secret=secrets.token_hex(12)))
    assert answer.status_code == 502, answer.text
    assert answer.json()["detail"]["code"] == "client_auth_failed" and answer.json()["detail"]["reason"] == reason
    assert listing(client) == []
    if kind == "qbittorrent":
        assert kit.fake.logins == 1, "one try only: five failures ban the address of nexbooks for an hour"


def test_sabnzbd_tells_the_nzb_key_from_a_wrong_key(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    answer = client.post("/api/v1/procure/clients", json=kit.body(secret=kit.fake.nzb_key))
    assert answer.json()["detail"] == {"code": "client_auth_failed", "message": answer.json()["detail"]["message"],
                                       "reason": "nzb_key"}


def test_sabnzbd_that_refuses_the_host_name_says_so(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.fake.host_refused = True
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_host_refused"


def test_transmission_that_refuses_the_address_says_so(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("transmission", net)
    kit.fake.whitelist_refuses = True
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_address_refused"


@pytest.mark.parametrize("kind", KINDS)
def test_another_program_at_the_address_is_the_wrong_kind(client: TestClient, operator: Account, net: FakeNetwork,
                                                           kind: str) -> None:
    other = "qbittorrent" if kind != "qbittorrent" else "sabnzbd"
    stranger = make(other, net)
    answer = client.post("/api/v1/procure/clients", json={"kind": kind, "url": stranger.url, "username": stranger.username,
                                                          "secret": stranger.secret})
    assert answer.status_code == 502, answer.text
    assert answer.json()["detail"]["code"] in ("client_wrong_kind", "client_auth_failed",
                                                  "client_address_refused"), \
        "a stranger that does not know the credentials is not told apart from a wrong password"
    assert listing(client) == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_web_page_at_the_address_is_the_wrong_kind(client: TestClient, operator: Account, net: FakeNetwork,
                                                      kind: str) -> None:
    class Page:
        host = "page.example.com"

        def handle(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>Welcome</body></html>", headers={"content-type": "text/html"})

    net.add(Page())
    answer = client.post("/api/v1/procure/clients", json={"kind": kind, "url": "http://page.example.com", "username": "u",
                                                          "secret": "s"})
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_wrong_kind", answer.text


@pytest.mark.parametrize("kind", KINDS)
def test_an_address_that_nobody_answers_is_unreachable(client: TestClient, operator: Account, net: FakeNetwork,
                                                        kind: str) -> None:
    answer = client.post("/api/v1/procure/clients", json={"kind": kind, "url": "http://nobody.example.com:1",
                                                          "username": "u", "secret": "s"})
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_unreachable"


def test_a_program_that_does_not_answer_in_time_is_a_timeout(client: TestClient, operator: Account,
                                                               net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.fake.handle = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))  # type: ignore
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 504 and answer.json()["detail"]["code"] == "client_timeout"


def test_an_address_the_gate_never_reaches_is_blocked(client: TestClient, operator: Account, net: FakeNetwork,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    kit = make("sabnzbd", net)
    monkeypatch.setattr(gate, "resolver", lambda _host, _port: ["169.254.169.254"])
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_blocked"
    assert kit.fake.requests == []


def test_a_category_that_is_not_there_after_creating_it_is_a_failure(client: TestClient, operator: Account,
                                                                      net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.fake.ignore_create = True
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_category_failed"
    assert answer.json()["detail"]["category"] == "nexbooks" and listing(client) == []


def test_sabnzbd_with_sorting_or_without_job_folders_cannot_follow_the_category(client: TestClient, operator: Account,
                                                                                  net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    kit.fake.sorters = [{"name": "all", "is_active": 1, "sort_cats": ["*"]}]
    sorting = client.post("/api/v1/procure/clients", json=kit.body())
    assert sorting.status_code == 502
    assert sorting.json()["detail"]["code"] == "client_category_unusable" and sorting.json()["detail"]["reason"] == "sorting"
    kit.fake.sorters = []
    kit.fake.categories = [{"name": "nexbooks", "dir": "nexbooks*", "pp": "3"}]
    folders = client.post("/api/v1/procure/clients", json=kit.body())
    assert folders.json()["detail"]["reason"] == "no_job_folders"
    kit.fake.categories = [{"name": "nexbooks", "dir": "nexbooks", "pp": "3"}]
    assert client.post("/api/v1/procure/clients", json=kit.body()).status_code == 201


def test_a_category_that_is_there_already_is_used_as_it_is(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("qbittorrent", net)
    kit.fake.categories = {"nexbooks": {"name": "nexbooks", "savePath": "/custom"}}
    assert client.post("/api/v1/procure/clients", json=kit.body()).status_code == 201
    assert kit.fake.categories == {"nexbooks": {"name": "nexbooks", "savePath": "/custom"}}


def test_qbittorrent_gets_no_origin_and_no_referer(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("qbittorrent", net)
    assert client.post("/api/v1/procure/clients", json=kit.body()).status_code == 201
    assert all("origin" not in request.headers and "referer" not in request.headers for request in kit.fake.requests)


def test_qbittorrent_without_a_user_and_a_password_does_not_log_in(client: TestClient, operator: Account,
                                                                    net: FakeNetwork) -> None:
    """qBittorrent can let the local network in without a login; then nexbooks does not try one."""
    fake = net.add(FakeQbittorrent("open.example.com", "x", "y"))
    fake.open_access = True
    answer = client.post("/api/v1/procure/clients", json={"kind": "qbittorrent", "url": "http://open.example.com",
                                                          "username": "", "secret": ""})
    assert answer.status_code == 201, answer.text
    assert fake.logins == 0 and list(fake.categories) == ["nexbooks"]


# --- Naming and fields ------------------------------------------------------------------------------------------------


def test_the_name_defaults_to_the_program_and_is_used_once(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    first = client.post("/api/v1/procure/clients", json=kit.body()).json()
    assert first["name"] == "SABnzbd"
    again = client.post("/api/v1/procure/clients", json=kit.body())
    assert again.status_code == 409 and again.json()["detail"]["code"] == "name_taken"
    assert client.post("/api/v1/procure/clients", json=kit.body(name="sabnzbd")).status_code == 409
    assert client.post("/api/v1/procure/clients", json=kit.body(name="Second")).status_code == 201


@pytest.mark.parametrize(("over", "field"), [
    ({"url": "ftp://sabnzbd.example.com"}, "url"), ({"url": "http://u:p@sabnzbd.example.com"}, "url"),
    ({"category": "../etc"}, "category"), ({"category": "x" * 80}, "category"),
    ({"category": "-lead"}, "category"), ({"priority": 0}, "priority"), ({"priority": 51}, "priority"),
    ({"name": "x" * 101}, "name"), ({"kind": "aria2"}, "kind"), ({"secret": "x" * 600}, "secret"),
])
def test_a_bad_input_is_refused_and_the_program_is_not_asked(client: TestClient, operator: Account, net: FakeNetwork,
                                                             over: dict[str, Any], field: str) -> None:
    kit = make("sabnzbd", net)
    answer = client.post("/api/v1/procure/clients", json=kit.body(**over))
    assert answer.status_code == 422, answer.text
    assert kit.fake.requests == [] and listing(client) == []


def test_at_most_twenty_programs(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    with SessionLocal() as db:
        for number in range(programs.MAX_CLIENTS):
            db.add(DownloadClient(name=f"P{number}", kind="sabnzbd", url=f"http://p{number}.example.com"))
        db.commit()
    answer = client.post("/api/v1/procure/clients", json=kit.body())
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "too_many_clients"


# --- Testing a program ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_test_of_an_address_says_version_category_and_folder_and_saves_nothing(client: TestClient,
                                                                                    operator: Account,
                                                                                    net: FakeNetwork, kind: str) -> None:
    kit = make(kind, net)
    answer = client.post("/api/v1/procure/clients/test", json=kit.body())
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["version"] and body["category"] == "nexbooks" and body["folder"] == kit.folder
    # NZBGet had the category in its configuration; Transmission has none; the others are made by the first test.
    assert body["category_existed"] is (kind == "nzbget")
    assert body["has_categories"] is (kind != "transmission")
    assert listing(client) == []
    second = client.post("/api/v1/procure/clients/test", json=kit.body()).json()
    assert second["category_existed"] is (kind != "transmission"), "the category the first test made"


def test_nzbget_without_the_category_says_to_make_it_there_and_writes_nothing(client: TestClient, operator: Account,
                                                                              net: FakeNetwork) -> None:
    """NZBGet's categories are in its configuration, which nexbooks does not write: a missing one is a failed test with
    the sentence to make it, never a category that was 'made'."""
    kit = make("nzbget", net)
    kit.fake.config = [item for item in kit.fake.config if item["Value"] != "nexbooks"]
    answer = client.post("/api/v1/procure/clients/test", json=kit.body())
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_category_missing", answer.text
    assert answer.json()["detail"]["category"] == "nexbooks"
    saved = client.post("/api/v1/procure/clients", json=kit.body())
    assert saved.status_code == 502 and listing(client) == []
    assert all(json.loads(request.content).get("method") in ("version", "config") for request in kit.fake.requests),         "only read calls were made"
    # A category by another name is no help; the operator makes it, and then it goes.
    kit.fake.config.append({"Name": "Category7.Name", "Value": "nexbooks"})
    assert client.post("/api/v1/procure/clients/test", json=kit.body()).status_code == 200


def test_nzbget_files_into_the_own_folder_of_the_category_when_it_has_one(client: TestClient, operator: Account,
                                                                          net: FakeNetwork) -> None:
    kit = make("nzbget", net)
    kit.fake.config.append({"Name": "Category2.DestDir", "Value": "/books/usenet"})
    body = client.post("/api/v1/procure/clients/test", json=kit.body()).json()
    assert body["folder"] == "/books/usenet"


def test_transmission_has_no_categories_and_the_test_does_not_pretend(client: TestClient, operator: Account,
                                                                      net: FakeNetwork) -> None:
    kit = make("transmission", net)
    body = client.post("/api/v1/procure/clients/test", json=kit.body()).json()
    assert body["has_categories"] is False and body["category_existed"] is False
    assert body["folder"] == kit.folder, "the jobs go to a folder of nexbooks below the download folder"
    made = client.post("/api/v1/procure/clients", json=kit.body())
    assert made.status_code == 201 and made.json()["has_categories"] is False
    assert client.get("/api/v1/procure/clients").json()["clients"][0]["has_categories"] is False


@pytest.mark.parametrize("kind", KINDS)
def test_the_test_can_use_the_secret_of_a_saved_program(client: TestClient, operator: Account, net: FakeNetwork,
                                                          kind: str) -> None:
    kit = make(kind, net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    answer = client.post("/api/v1/procure/clients/test", json={"client_id": made["id"], "kind": kind, "url": kit.url,
                                                               "username": kit.username})
    assert answer.status_code == 200, answer.text


def test_the_state_of_a_saved_program_follows_its_tests(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    kit.fake.api_key = secrets.token_hex(12)
    assert client.post(f"/api/v1/procure/clients/{made['id']}/test").status_code == 502
    shown = listing(client)[0]
    assert shown["state"] == "login" and shown["last_error"] == "client_auth_failed"
    kit.fake.api_key = SECRET
    kit.fake.version = "4.6.0"
    assert client.post(f"/api/v1/procure/clients/{made['id']}/test").status_code == 200
    shown = listing(client)[0]
    assert shown["state"] == "ready" and shown["version"] == "4.6.0" and shown["last_error"] == ""
    kit.fake.handle = lambda request: (_ for _ in ()).throw(httpx.ConnectError("down", request=request))  # type: ignore
    assert client.post(f"/api/v1/procure/clients/{made['id']}/test").status_code == 502
    assert listing(client)[0]["state"] == "unreachable"


# --- Changing and removing --------------------------------------------------------------------------------------------


def test_changing_what_does_not_touch_the_connection_asks_nobody(client: TestClient, operator: Account,
                                                                   net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    asked = len(kit.fake.requests)
    changed = client.patch(f"/api/v1/procure/clients/{made['id']}", json={"name": "Usenet", "priority": 4,
                                                                          "enabled": False})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["name"], changed.json()["priority"], changed.json()["enabled"]) == ("Usenet", 4, False)
    assert len(kit.fake.requests) == asked


def test_a_new_address_is_tested_and_needs_the_secret_again(client: TestClient, operator: Account,
                                                            net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    sab_two = net.add(FakeSabnzbd("sabnzbd-new.example.com", SECRET))
    without = client.patch(f"/api/v1/procure/clients/{made['id']}", json={"url": "http://sabnzbd-new.example.com:8080"})
    assert without.status_code == 422 and without.json()["detail"]["code"] == "key_required", without.text
    assert not sab_two.requests, "the saved secret was not sent to the new place"
    changed = client.patch(f"/api/v1/procure/clients/{made['id']}",
                           json={"url": "http://sabnzbd-new.example.com:8080", "secret": SECRET})
    assert changed.status_code == 200, changed.text
    assert sab_two.categories, "the category was made in the new place"
    assert decrypt_secret(stored()[0].secret_enc, programs.SECRET_CONTEXT) == SECRET
    broken = client.patch(f"/api/v1/procure/clients/{made['id']}",
                          json={"url": "http://nowhere.example.com", "secret": SECRET})
    assert broken.status_code == 502
    assert listing(client)[0]["url"] == "http://sabnzbd-new.example.com:8080", "a failed change changes nothing"


def test_the_same_place_keeps_the_secret_and_another_port_does_not(client: TestClient, operator: Account,
                                                                    net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    base = kit.body()["url"]
    same = client.patch(f"/api/v1/procure/clients/{made['id']}", json={"url": base + "/", "category": "nexbooks2"})
    assert same.status_code == 200, same.text
    asked = len(kit.fake.requests)
    other_port = base.rsplit(":", 1)[0] + ":9999" if base.count(":") > 1 else base + ":9999"
    refused = client.patch(f"/api/v1/procure/clients/{made['id']}", json={"url": other_port})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "key_required"
    assert len(kit.fake.requests) == asked


def test_a_test_with_another_address_does_not_send_the_saved_secret(client: TestClient, operator: Account,
                                                                     net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    elsewhere = net.add(FakeSabnzbd("sabnzbd-else.example.com", SECRET))
    body = {"client_id": made["id"], "kind": "sabnzbd", "url": "http://sabnzbd-else.example.com:8080"}
    refused = client.post("/api/v1/procure/clients/test", json=body)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "key_required", refused.text
    assert not elsewhere.requests
    assert client.post("/api/v1/procure/clients/test", json={**body, "secret": SECRET}).status_code == 200
    same = {**body, "url": kit.body()["url"]}
    assert client.post("/api/v1/procure/clients/test", json=same).status_code == 200


def test_a_new_secret_is_tested_and_sealed(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    new = secrets.token_hex(12)
    kit.fake.api_key = new
    wrong = client.patch(f"/api/v1/procure/clients/{made['id']}", json={"secret": secrets.token_hex(12)})
    assert wrong.status_code == 502
    assert decrypt_secret(stored()[0].secret_enc, programs.SECRET_CONTEXT) == SECRET
    right = client.patch(f"/api/v1/procure/clients/{made['id']}", json={"secret": new})
    assert right.status_code == 200 and right.json()["state"] == "ready"
    assert decrypt_secret(stored()[0].secret_enc, programs.SECRET_CONTEXT) == new


def test_removing_a_program_takes_its_path_mappings_along(client: TestClient, operator: Account,
                                                            net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    with SessionLocal() as db:
        db.add(PathMapping(client_id=made["id"], remote="/downloads/complete", local="/data/usenet/complete"))
        db.commit()
    assert client.delete(f"/api/v1/procure/clients/{made['id']}").status_code == 204
    assert listing(client) == []
    assert client.get("/api/v1/procure/path-mappings").json() == {"mappings": []}
    assert client.delete(f"/api/v1/procure/clients/{made['id']}").status_code == 404


def test_path_mappings_are_listed_with_the_program_and_can_be_removed(client: TestClient, operator: Account,
                                                                        net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    with SessionLocal() as db:
        db.add(PathMapping(client_id=made["id"], remote="/downloads/complete", local="/data/usenet/complete"))
        db.commit()
    shown = client.get("/api/v1/procure/path-mappings").json()["mappings"]
    assert [(m["client"], m["remote"], m["local"]) for m in shown] == [("SABnzbd", "/downloads/complete",
                                                                         "/data/usenet/complete")]
    assert client.delete(f"/api/v1/procure/path-mappings/{shown[0]['id']}").status_code == 204
    assert client.get("/api/v1/procure/path-mappings").json() == {"mappings": []}
    assert client.delete(f"/api/v1/procure/path-mappings/{shown[0]['id']}").status_code == 404


def test_the_programs_come_in_the_order_they_are_asked(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    for kind, priority in (("sabnzbd", 3), ("nzbget", 1), ("qbittorrent", 2)):
        client.post("/api/v1/procure/clients", json=make(kind, net).body(priority=priority))
    assert [item["kind"] for item in listing(client)] == ["nzbget", "qbittorrent", "sabnzbd"]


def test_only_the_operator_reaches_the_programs(client: TestClient, operator: Account, net: FakeNetwork) -> None:
    kit = make("sabnzbd", net)
    made = client.post("/api/v1/procure/clients", json=kit.body()).json()
    asked = len(kit.fake.requests)
    calls: tuple[tuple[str, str, Any], ...] = (
        ("GET", "/clients", None), ("POST", "/clients", kit.body(name="Two")),
        ("PATCH", f"/clients/{made['id']}", {"priority": 2}), ("DELETE", f"/clients/{made['id']}", None),
        ("POST", "/clients/test", kit.body()), ("POST", f"/clients/{made['id']}/test", None),
        ("GET", "/path-mappings", None), ("DELETE", "/path-mappings/1", None))
    with new_client(make_account("rita")) as member:
        for method, path, body in calls:
            assert member.request(method, "/api/v1/procure" + path, json=body).status_code == 403, path
    with TestClient(client.app, base_url="http://testserver", headers=TAB) as nobody:
        for method, path, body in calls:
            assert nobody.request(method, "/api/v1/procure" + path, json=body).status_code == 401, path
    assert len(kit.fake.requests) == asked
