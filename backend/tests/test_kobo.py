"""The sync of a Kobo under ``/kobo/<secret>``: the address of a connection is its secret, a wrong one is turned away and
braked, the list of a person (``PUT /api/v1/editions/{id}/kobo``) is what a Kobo is told about and nothing else, a book the
person does not see any more is told away and answers like a book nobody has, the place read on the Kobo meets the place
of the browser by the rule of every other report, and the secret reaches no log line, no error and no redirect.

What is checked here are the calls a Kobo makes, written out by hand from the protocol (``call`` and ``sync_all``); nothing
here has been tried against a real device."""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.db import SessionLocal
from app.main import app
from app.models import (
    Account,
    BookFile,
    Edition,
    EreaderLink,
    KoboHeld,
    KoboListed,
    LibraryMember,
    Progress,
    utcnow,
)
from app.routers import kobo as kobo_router
from app.security import brake
from app.services import brakes, kobo, logs, settings_service

from .conftest import PASSWORD, new_client
from .ereader_helpers import Reading, connect
from .reading_helpers import Clock

TAB = {"X-Nexbooks-Client": "tab-test0000"}
#: What a Kobo sends: nothing of ours (no cookie, no tab), a Bearer token it was handed at its sign-in.
DEVICE_HEADERS = {"Authorization": "Bearer made-up-by-the-test", "User-Agent": "Mozilla/5.0 (Linux; Android) Kobo"}
LOCATION = {"Value": "kobo.12.3", "Type": "KoboSpan", "Source": "OEBPS/text/c2.xhtml"}


def device() -> TestClient:
    """A Kobo: it follows no redirect on its own here, so that one would show."""
    return TestClient(app, base_url="http://testserver", follow_redirects=False, headers=DEVICE_HEADERS)


def edition_of(title: str) -> int:
    with SessionLocal() as db:
        return int(db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == "ebook")).one())


def audio_of(title: str) -> int:
    with SessionLocal() as db:
        return int(db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == "audio")).one())


@dataclass
class Kobo:
    """A Kobo that was pointed at nexbooks, and the browser of the person it belongs to."""

    reading: Reading
    app: TestClient
    browser: TestClient
    secret: str

    @property
    def root(self) -> str:
        return f"/kobo/{self.secret}"

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.app.get(self.root + path, **kwargs)

    def put_state(self, name: str, state: dict[str, Any]) -> Any:
        return self.app.put(f"{self.root}/v1/library/{name}/state", json={"ReadingStates": [state]})

    def list(self, title: str) -> str:
        """Puts the e-book on the list; returns the name the Kobo knows it by."""
        edition = edition_of(title)
        assert self.browser.put(f"/api/v1/editions/{edition}/kobo", headers=TAB).status_code == 200
        return kobo.name_of(self.reading.shelf.ada.id, edition)


@pytest.fixture
def ada_kobo(reading: Reading, ada_browser: TestClient) -> Kobo:
    return Kobo(reading, device(), ada_browser, connect(ada_browser, "My Kobo", kind="kobo"))


@pytest.fixture
def ben_kobo(reading: Reading) -> Kobo:
    with new_client(reading.shelf.ben) as browser:
        secret = connect(browser, "Ben's Kobo", kind="kobo")
    return Kobo(reading, device(), new_client(reading.shelf.ben), secret)


def sync_once(box: Kobo, token: str | None = None) -> tuple[list[dict[str, Any]], str, bool]:
    """One call of the sync: the entries, the token of the answer and whether the device is to ask again."""
    answer = box.get("/v1/library/sync", headers={kobo.TOKEN_HEADER: token} if token is not None else {})
    assert answer.status_code == 200, answer.text
    return answer.json(), answer.headers[kobo.TOKEN_HEADER], answer.headers.get("x-kobo-sync") == "continue"


def sync_all(box: Kobo, token: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """What a device does: asks again for as long as it is told to, with the token of the last answer."""
    found: list[dict[str, Any]] = []
    for _round in range(50):
        entries, token, more = sync_once(box, token)
        found += entries
        if not more:
            return found, token
    raise AssertionError("the sync never ended")


def kinds(entries: list[dict[str, Any]]) -> list[str]:
    return [next(iter(entry)) for entry in entries]


def metadata_of(entry: dict[str, Any]) -> dict[str, Any]:
    body = next(iter(entry.values()))
    return body["BookMetadata"]  # type: ignore[no-any-return]


def titles_in(entries: list[dict[str, Any]]) -> list[str]:
    return sorted(metadata_of(entry)["Title"] for entry in entries if "BookMetadata" in next(iter(entry.values())))


# --- The connection and the way in ---------------------------------------------------------------------------------


def test_a_connection_of_this_kind_is_made_in_the_browser_and_only_its_hash_is_kept(ada_kobo: Kobo,
                                                                                    ada_browser: TestClient) -> None:
    assert ada_kobo.secret.startswith("nbe_") and len(ada_kobo.secret) == 36
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        everything = " ".join(str(value) for value in (link.name, link.prefix, link.token_hash, link.kind))
    import hashlib

    assert link.kind == "kobo" and link.token_hash == hashlib.sha256(ada_kobo.secret.encode()).hexdigest()
    assert ada_kobo.secret not in everything and link.prefix == ada_kobo.secret[:8]
    # The list of the account shows it by its first characters, never the secret.
    shown = ada_browser.get("/api/v1/ereaders")
    assert shown.status_code == 200 and ada_kobo.secret not in shown.text
    assert [(row["kind"], row["prefix"]) for row in shown.json()["links"]] == [("kobo", ada_kobo.secret[:8])]


def test_a_device_token_or_a_key_cannot_make_a_connection_or_a_list(reading: Reading, ada_browser: TestClient) -> None:
    from .app_helpers import app_of, device_token

    phone = app_of(device_token(reading.shelf.ada))
    assert phone.post("/api/v1/ereaders/links", json={"name": "x", "kind": "kobo"}).status_code == 403
    edition = edition_of("The Quiet Orchard")
    assert phone.put(f"/api/v1/editions/{edition}/kobo").status_code == 403
    assert phone.delete(f"/api/v1/editions/{edition}/kobo").status_code == 403
    assert ada_browser.get("/api/v1/ereaders").json()["kobo"] == []


def test_the_first_call_hands_the_device_the_whole_table_with_nexbooks_in_the_place_of_the_store(
        ada_kobo: Kobo) -> None:
    answer = ada_kobo.get("/v1/initialization")
    assert answer.status_code == 200 and answer.headers["x-kobo-apitoken"] == "e30="
    assert answer.headers["content-type"].startswith("application/json") and answer.headers["cache-control"] == "no-store"
    assert "set-cookie" not in answer.headers
    found = answer.json()["Resources"]
    root = "http://testserver" + ada_kobo.root
    assert found["library_sync"] == f"{root}/v1/library/sync"
    assert found["image_url_template"].startswith(f"{root}/image/{{ImageId}}/{{Width}}/{{Height}}/")
    assert found["image_url_quality_template"].count("{") == 5
    # The whole table, the switches as Kobo's store has them: nothing is known about what a device does with less.
    assert set(found) == set(kobo_router.STORE_TABLE) and len(found) > 150
    assert found["use_one_store"] == "True" and found["kobo_onestorelibrary_enabled"] == "False"
    assert found["reading_state"] == f"{root}/v1/library/{{Ids}}/state"
    assert found["delete_entitlement"] == f"{root}/v1/library/{{Ids}}"
    # Nothing of the store's server and of its pictures is left, and the secret goes nowhere but to nexbooks.
    assert "storeapi.kobo.com" not in answer.text and "cdn.kobo.com" not in answer.text
    assert all(value.startswith(root) for value in found.values()
               if isinstance(value, str) and ada_kobo.secret in value)


def test_an_address_that_ends_in_a_slash_in_the_settings_of_the_device_works_and_nothing_is_redirected(
        ada_kobo: Kobo) -> None:
    answer = ada_kobo.get("//v1/initialization")
    assert answer.status_code == 200 and "library_sync" in answer.json()["Resources"]
    name = ada_kobo.list("The Quiet Orchard")
    assert ada_kobo.get("//v1/library/sync").json()[0]["NewEntitlement"]["BookEntitlement"]["Id"] == name
    assert ada_kobo.get("/v1//library///sync").status_code == 200
    # Only there: the rest of the server has no use of it.
    assert "location" not in answer.headers and ada_kobo.app.get("/api//v1/health").status_code == 404


def test_the_benefits_of_the_store_are_empty_with_their_field(ada_kobo: Kobo) -> None:
    assert ada_kobo.get("/v1/user/loyalty/benefits").json() == {"Benefits": {}}


def test_the_reports_of_a_device_about_itself_may_be_large_and_go_nowhere(ada_kobo: Kobo) -> None:
    path = f"{ada_kobo.root}/v1/analytics/event"
    taken = ada_kobo.app.post(path, content=b" " * (200 * 1024))
    assert taken.status_code == 200 and taken.json() == {}
    assert ada_kobo.app.post(path, content=b" " * (2 * 1024 * 1024)).status_code == 413


def test_the_address_follows_the_public_address_the_operator_set(ada_kobo: Kobo) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://books.example.com"})
    found = ada_kobo.get("/v1/initialization").json()["Resources"]
    assert found["library_sync"] == f"https://books.example.com{ada_kobo.root}/v1/library/sync"


def test_the_sign_in_of_the_device_hands_out_tokens_nothing_reads(ada_kobo: Kobo) -> None:
    first = ada_kobo.app.post(f"{ada_kobo.root}/v1/auth/device", json={"UserKey": "abc", "DeviceId": "d1"})
    second = ada_kobo.app.post(f"{ada_kobo.root}/v1/auth/device", content=b"not json")
    assert first.status_code == second.status_code == 200
    body = first.json()
    assert body["UserKey"] == "abc" and body["TokenType"] == "Bearer" and len(body["AccessToken"]) >= 32
    assert body["AccessToken"] != second.json()["AccessToken"] and second.json()["UserKey"]
    again = ada_kobo.app.post(f"{ada_kobo.root}/v1/auth/refresh", json={"RefreshToken": body["RefreshToken"]})
    assert again.status_code == 200 and again.json()["AccessToken"] not in (body["AccessToken"], second.json()["AccessToken"])
    # The token it was handed opens nothing: with it and with a wrong secret in the address, nothing is let in.
    wrong = "nbe_" + secrets.token_urlsafe(24)
    refused = ada_kobo.app.get(f"/kobo/{wrong}/v1/library/sync", headers={"Authorization": "Bearer " + body["AccessToken"]})
    assert refused.status_code == 401


def test_a_wrong_secret_is_a_401_that_names_nothing_and_every_route_asks(ada_kobo: Kobo, ada_browser: TestClient) -> None:
    from .test_routes import api_routes, concrete

    opds_secret = connect(ada_browser, "app", kind="opds")
    sync_secret = connect(ada_browser, "sync", kind="kosync")
    tries = {
        "random": "nbe_" + secrets.token_urlsafe(24),
        "no prefix": secrets.token_urlsafe(27),
        "empty prefix only": "nbe_",
        "the secret of the catalogue": opds_secret,
        "the secret of the sync of KOReader": sync_secret,
        "the password of the account": PASSWORD,
        "upper case": ada_kobo.secret.upper(),
        "a prefix of the right one": ada_kobo.secret[:-1],
        "the right one and more": ada_kobo.secret + "x",
        "too long": "nbe_" + "a" * 300,
    }
    for name, wrong in tries.items():
        brake.forget()
        answer = ada_kobo.app.get(f"/kobo/{wrong}/v1/library/sync")
        assert answer.status_code == 401, name
        assert answer.json()["detail"]["code"] == "unauthorized", name
        assert wrong not in answer.text and "set-cookie" not in answer.headers and "location" not in answer.headers, name
    brake.forget()
    routes = [(method, path) for method, path in api_routes() if path.startswith("/kobo/")]
    assert len(routes) >= 12, "the routes of the Kobo were found"
    wrong = "nbe_" + secrets.token_urlsafe(24)
    for method, path in routes:
        brake.forget()
        address = concrete(path.replace("{token}", wrong))
        answer = ada_kobo.app.request(method, address, json={} if method in ("POST", "PUT", "PATCH") else None)
        assert answer.status_code == 401, f"{method} {path}"
    # (An address that ends differently, with a slash too many, or none at all.)
    for ending in ("", "/", "/v1/library/sync/", "//", "/v1/../v1/initialization"):
        brake.forget()
        answer = ada_kobo.app.get(f"/kobo/{wrong}{ending}")
        assert answer.status_code == 401 and "location" not in answer.headers, ending


def test_a_blocked_deleted_or_switched_off_connection_is_turned_away_at_once(
        client: TestClient, operator: Account, ada_kobo: Kobo, ada_browser: TestClient, reading: Reading) -> None:
    other = Kobo(reading, device(), ada_browser, connect(ada_browser, "second", kind="kobo"))
    links = ada_browser.get("/api/v1/ereaders").json()["links"]
    first, second = links[0]["id"], links[1]["id"]
    assert ada_kobo.get("/v1/initialization").status_code == 200
    assert client.post(f"/api/v1/admin/ereaders/links/{first}/block").status_code == 200
    assert ada_kobo.get("/v1/initialization").status_code == 401
    assert ada_kobo.get("/v1/library/sync").status_code == 401
    assert other.get("/v1/initialization").status_code == 200
    assert ada_browser.delete(f"/api/v1/ereaders/links/{second}", headers=TAB).status_code == 204
    assert other.get("/v1/library/sync").status_code == 401
    # The block stays until the operator deletes the connection; only then may the person make one of that kind.
    assert client.delete(f"/api/v1/admin/ereaders/links/{first}").status_code == 204
    fresh = Kobo(reading, device(), ada_browser, connect(ada_browser, "third", kind="kobo"))
    assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).status_code == 200
    off = fresh.get("/v1/initialization")
    assert off.status_code == 403 and off.json()["detail"]["code"] == "ereaders_off"
    assert client.put("/api/v1/settings", json={"ereaders_allowed": True}).status_code == 200
    assert fresh.get("/v1/initialization").status_code == 200


def test_a_connection_of_another_kind_is_no_way_in_and_this_one_opens_nothing_else(ada_kobo: Kobo) -> None:
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        link.kind = "opds"
        db.commit()
    assert ada_kobo.get("/v1/initialization").status_code == 401
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        link.kind = "kobo"
        db.commit()
    from .ereader_helpers import basic

    assert ada_kobo.app.get("/opds", headers=basic("ada", ada_kobo.secret)).status_code == 401
    assert ada_kobo.app.get("/kosync/users/auth", headers={"x-auth-user": "ada", "x-auth-key": ada_kobo.secret}
                            ).status_code == 401
    assert ada_kobo.app.get("/api/v1/me", headers={"Authorization": "Bearer " + ada_kobo.secret}).status_code == 401


def test_a_blocked_account_has_no_sync_but_a_lock_after_wrong_passwords_does_not_reach_it(reading: Reading, ada_kobo: Kobo) -> None:
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        ada.blocked_at = utcnow()
        db.commit()
    assert ada_kobo.get("/v1/library/sync").status_code == 401
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        ada.blocked_at = None
        ada.locked_until = utcnow() + timedelta(minutes=5)
        db.commit()
    assert ada_kobo.get("/v1/library/sync").status_code == 200, "the lock is for the password"


def test_wrong_secrets_are_braked_but_a_right_one_is_never_turned_away(ada_kobo: Kobo) -> None:
    same = "nbe_" + secrets.token_urlsafe(24)
    # The same wrong secret again and again (a device that was not told) counts once.
    for _ in range(12):
        assert ada_kobo.app.get(f"/kobo/{same}/v1/initialization").status_code == 401
    answers = [ada_kobo.app.get(f"/kobo/nbe_{secrets.token_urlsafe(24)}/v1/initialization").status_code
               for _ in range(8)]
    assert answers[:4] == [401] * 4 and set(answers[5:]) == {429}
    rested = ada_kobo.app.get(f"/kobo/nbe_{secrets.token_urlsafe(24)}/v1/initialization")
    assert rested.status_code == 429 and int(rested.headers["retry-after"]) > 0
    # The connection that exists is looked up first: it still works from the same address.
    assert ada_kobo.get("/v1/initialization").status_code == 200
    # The count is its own: the sign-in with the password still works.
    login = device().post("/api/v1/auth/login", headers=TAB, json={"name": "ada", "password": PASSWORD})
    assert login.status_code == 200, login.text


def test_a_flood_of_requests_is_braked_per_account(ada_kobo: Kobo, ben_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(brakes.LIMITS, "kobo", 3)
    assert [ada_kobo.get("/v1/initialization").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert ben_kobo.get("/v1/initialization").status_code == 200


def test_it_is_no_way_for_a_browser(ada_kobo: Kobo, ben_kobo: Kobo, ada_browser: TestClient) -> None:
    # The cookie of a signed-in browser is read by nothing here: it neither opens a door nor changes whose it is.
    with new_client(ada_kobo.reading.shelf.ben) as ben_browser:
        as_ben = ben_browser.get(ada_kobo.root + "/v1/library/sync")
        assert as_ben.status_code == 200
        ada_kobo.list("The Quiet Orchard")
        assert titles_in(ben_browser.get(ada_kobo.root + "/v1/library/sync").json()) == ["The Quiet Orchard"]
        assert ben_browser.get("/kobo/nbe_" + secrets.token_urlsafe(24) + "/v1/library/sync").status_code == 401
    assert ada_browser.get("/kobo").status_code == 404
    # Nothing is set, and no page on another site is let in: there is no CORS.
    answer = ada_kobo.app.get(ada_kobo.root + "/v1/initialization", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in answer.headers and "set-cookie" not in answer.headers
    # A change needs no tab (the device has none) but only the secret in the address makes it one.
    edition = edition_of("The Quiet Orchard")
    name = kobo.name_of(ada_kobo.reading.shelf.ada.id, edition)
    assert ada_kobo.app.put(f"{ada_kobo.root}/v1/library/{name}/state", json={}).status_code == 200
    assert ada_kobo.app.put(f"/kobo/nbe_{secrets.token_urlsafe(24)}/v1/library/{name}/state", json={}).status_code == 401


def test_every_other_route_answers_empty_and_nothing_is_ever_redirected(ada_kobo: Kobo) -> None:
    paths = ["", "/", "/v1/user/profile", "/v1/user/wishlist", "/v1/products/1234/nextread", "/v1/deals", "/x/y/z/",
             "/v1/library/sync/", "/v1/initialization/", "//v1//library", "/v1/library/..%2f..%2fx"]
    for path in paths:
        for method in ("get", "post", "put", "delete", "patch"):
            answer = getattr(ada_kobo.app, method)(ada_kobo.root + path)
            assert answer.status_code < 300 or answer.status_code in (404, 405), (method, path, answer.status_code)
            assert "location" not in answer.headers, (method, path)
    for path in ("/v1/user/profile", "/v1/deals", "/x/y/z/", ""):
        assert ada_kobo.get(path).json() == {}
    assert ada_kobo.get("/v1/library/tags").json() == []
    tests = ada_kobo.app.post(f"{ada_kobo.root}/v1/analytics/gettests", json={})
    assert tests.status_code == 200 and tests.json()["Tests"] == {}


# --- The list --------------------------------------------------------------------------------------------------------


def test_an_empty_list_is_an_empty_sync_with_a_token_that_asks_for_nothing_more(ada_kobo: Kobo) -> None:
    entries, token, more = sync_once(ada_kobo)
    assert entries == [] and not more and token
    assert sync_once(ada_kobo, token) == ([], token, False)
    assert ada_kobo.browser.get("/api/v1/ereaders").json()["kobo"] == []


def test_a_book_on_the_list_is_told_once_with_what_the_device_needs(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    entries, token, more = sync_once(ada_kobo)
    assert kinds(entries) == ["NewEntitlement"] and not more
    body = entries[0]["NewEntitlement"]
    entitlement, meta, state = body["BookEntitlement"], body["BookMetadata"], body["ReadingState"]
    assert entitlement["Id"] == name == meta["EntitlementId"] == meta["CoverImageId"] == state["EntitlementId"]
    assert entitlement["IsRemoved"] is False and entitlement["Status"] == "Active"
    assert meta["Title"] == "The Quiet Orchard" and meta["Contributors"] == ["Mara Lindqvist"]
    assert meta["Language"] == "en" and meta["Description"] == "A garden at the end of the world."
    assert meta["Series"]["Name"] == "Orchard Tales" and meta["Series"]["NumberFloat"] == 1.0
    downloads = meta["DownloadUrls"]
    assert [download["Format"] for download in downloads] == ["EPUB3", "EPUB"]
    assert {download["Url"] for download in downloads} == {f"http://testserver{ada_kobo.root}/download/{name}.epub"}
    assert {download["DrmType"] for download in downloads} == {"None"}
    download = downloads[0]
    # One name for all that asks for one, and the prices the store's own answers hold.
    assert entitlement["RevisionId"] == entitlement["CrossRevisionId"] == meta["RevisionId"] == meta["WorkId"] == name
    assert meta["CurrentDisplayPrice"] == {"CurrencyCode": "USD", "TotalAmount": 0}
    assert meta["CurrentLoveDisplayPrice"] == {"TotalAmount": 0}
    with SessionLocal() as db:
        size = db.scalars(select(BookFile.size).join(Edition, Edition.id == BookFile.edition_id).where(
            Edition.title == "The Quiet Orchard", BookFile.format == "epub")).one()
    assert download["Size"] == size
    assert state["StatusInfo"]["Status"] == "ReadyToRead" and "ProgressPercent" not in state["CurrentBookmark"]
    # Taken: nothing more. A device that asks again with the token it was given hears nothing, and one that comes with no
    # token (a reset) is told everything again.
    assert sync_once(ada_kobo, token) == ([], token, False)
    assert kinds(sync_once(ada_kobo)[0]) == ["NewEntitlement"]
    # The browser knows it is there.
    assert ada_kobo.browser.get("/api/v1/ereaders").json()["kobo"] == [edition_of("The Quiet Orchard")]


def test_a_book_of_no_known_language_has_one_all_the_same(ada_kobo: Kobo) -> None:
    ada_kobo.list("The Quiet Orchard")
    with SessionLocal() as db:
        db.execute(text("UPDATE editions SET language = '' WHERE title = 'The Quiet Orchard'"))
        db.commit()
    assert metadata_of(sync_once(ada_kobo)[0][0])["Language"] == "en"


def test_a_book_nobody_has_read_keeps_the_moment_it_came_on_the_list_however_often_it_is_told(
        ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    name = ada_kobo.list("The Quiet Orchard")
    with SessionLocal() as db:
        listed = kobo.stamp_text(db.scalars(select(KoboListed.added_at)).one())

    def told() -> dict[str, Any]:
        state = sync_once(ada_kobo)[0][0]["NewEntitlement"]["ReadingState"]
        return state  # type: ignore[no-any-return]

    first = told()
    tick.advance(seconds=3 * 3600)
    # A device that lost its token is told the book again, and the state does not look newer than it was.
    assert told() == first and state_of(ada_kobo, name) == first
    assert {first["Created"], first["LastModified"], first["PriorityTimestamp"]} == {listed}
    assert {first["StatusInfo"]["LastModified"], first["Statistics"]["LastModified"]} == {listed}
    assert first["CurrentBookmark"]["LastModified"] == listed


def test_the_name_of_a_book_is_the_persons_own_and_the_same_on_every_kobo(reading: Reading, ada_kobo: Kobo,
                                                                          ben_kobo: Kobo) -> None:
    ada_name = ada_kobo.list("The Quiet Orchard")
    with new_client(reading.shelf.ben) as ben_browser:
        edition = edition_of("The Quiet Orchard")
        assert ben_browser.put(f"/api/v1/editions/{edition}/kobo", headers=TAB).status_code == 200
    ben_name = kobo.name_of(reading.shelf.ben.id, edition)
    assert ada_name != ben_name
    second = Kobo(reading, device(), ada_kobo.browser, connect(ada_kobo.browser, "Kobo Libra", kind="kobo"))
    assert metadata_of(sync_once(second)[0][0])["EntitlementId"] == ada_name
    # The name of one person means nothing to another.
    assert ben_kobo.get(f"/v1/library/{ada_name}/metadata").status_code == 404
    assert ben_kobo.get(f"/v1/library/{ben_name}/metadata").status_code == 200


def test_putting_a_book_on_the_list_twice_is_one_entry_and_taking_it_off_is_told(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    assert ada_kobo.list("The Quiet Orchard") == name
    with SessionLocal() as db:
        assert len(db.scalars(select(KoboListed)).all()) == 1
    entries, token = sync_all(ada_kobo)
    assert kinds(entries) == ["NewEntitlement"]
    edition = edition_of("The Quiet Orchard")
    assert ada_kobo.browser.delete(f"/api/v1/editions/{edition}/kobo", headers=TAB).json() == {"listed": False}
    gone, token = sync_all(ada_kobo, token)
    assert kinds(gone) == ["ChangedEntitlement"]
    removed = gone[0]["ChangedEntitlement"]
    assert removed["BookEntitlement"]["IsRemoved"] is True and removed["BookEntitlement"]["Id"] == name
    # The book goes with its name only: nothing of what it was, in the fields of any other book.
    meta = removed["BookMetadata"]
    assert "Quiet" not in json.dumps(gone) and "Mara" not in json.dumps(gone) and meta["Title"] == name
    assert "DownloadUrls" not in meta and meta["CurrentDisplayPrice"]["TotalAmount"] == 0
    assert meta["Language"] == "en" and removed["BookEntitlement"]["RevisionId"] == name == meta["RevisionId"]
    assert sync_all(ada_kobo, token) == ([], token)
    # Taking off a book that is not there is the same answer, and puts nothing on the list.
    assert ada_kobo.browser.delete(f"/api/v1/editions/{edition}/kobo", headers=TAB).status_code == 200
    assert ada_kobo.browser.get("/api/v1/ereaders").json()["kobo"] == []
    # Put there again, it is a new book to the device.
    ada_kobo.list("The Quiet Orchard")
    assert kinds(sync_all(ada_kobo, token)[0]) == ["NewEntitlement"]


def test_only_an_epub_that_is_not_protected_can_be_put_on_the_list(reading: Reading, ada_kobo: Kobo,
                                                                   ada_browser: TestClient) -> None:
    def put(edition: int) -> Any:
        return ada_browser.put(f"/api/v1/editions/{edition}/kobo", headers=TAB)

    assert put(audio_of("The Quiet Orchard")).status_code == 404
    locked = put(edition_of("The Locked Door"))
    assert locked.status_code == 422 and locked.json()["detail"]["code"] == "protected_book"
    assert put(99999).status_code == 404
    assert put(edition_of("Kite Season")).status_code == 200
    with SessionLocal() as db:
        assert [row.edition_id for row in db.scalars(select(KoboListed))] == [edition_of("Kite Season")]
    # A book with a file other than an EPUB only.
    with SessionLocal() as db:
        file = db.scalars(select(BookFile).join(Edition, Edition.id == BookFile.edition_id).where(
            Edition.title == "The Loud Orchard", BookFile.format == "epub")).one()
        file.format = "mobi"
        db.commit()
    other = put(edition_of("The Loud Orchard"))
    assert other.status_code == 422 and other.json()["detail"]["code"] == "not_epub"


def test_a_book_of_a_library_the_person_is_not_in_does_not_exist_to_the_list(reading: Reading, ada_kobo: Kobo) -> None:
    secret_edition = edition_of("A Secret Shelf")
    stranger = ada_kobo.browser.put(f"/api/v1/editions/{secret_edition}/kobo", headers=TAB)
    nobody = ada_kobo.browser.put("/api/v1/editions/987654/kobo", headers=TAB)
    assert stranger.status_code == nobody.status_code == 404 and stranger.json() == nobody.json()
    assert "Secret" not in stranger.text
    # Taking one off that was never there is harmless and shows nothing either.
    assert ada_kobo.browser.delete(f"/api/v1/editions/{secret_edition}/kobo", headers=TAB).json() == {"listed": False}
    with SessionLocal() as db:
        assert db.scalars(select(KoboListed)).all() == []


def test_the_list_is_limited_by_the_statement_that_writes_it(ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kobo, "MAX_LISTED", 2)
    ada_kobo.list("The Quiet Orchard")
    ada_kobo.list("Kite Season")
    third = ada_kobo.browser.put(f"/api/v1/editions/{edition_of('The Loud Orchard')}/kobo", headers=TAB)
    assert third.status_code == 409 and third.json()["detail"]["code"] == "kobo_list_full"
    # A book that is there is not refused, and the list did not grow.
    assert ada_kobo.browser.put(f"/api/v1/editions/{edition_of('Kite Season')}/kobo", headers=TAB).status_code == 200
    with SessionLocal() as db:
        assert len(db.scalars(select(KoboListed)).all()) == 2


def test_the_list_is_braked_per_person_and_switched_off_with_e_readers(
        client: TestClient, operator: Account, ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(brakes.LIMITS, "kobo_list", 2)
    edition = edition_of("The Quiet Orchard")
    codes = [ada_kobo.browser.put(f"/api/v1/editions/{edition}/kobo", headers=TAB).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    brakes.forget()
    assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).status_code == 200
    off = ada_kobo.browser.put(f"/api/v1/editions/{edition}/kobo", headers=TAB)
    assert off.status_code == 403 and off.json()["detail"]["code"] == "ereaders_off"
    # Taking a book off is never refused.
    assert ada_kobo.browser.delete(f"/api/v1/editions/{edition}/kobo", headers=TAB).status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(KoboListed)).all() == []


# --- A book the person does not see any more ----------------------------------------------------------------------------


def test_a_book_the_person_no_longer_sees_is_told_away_and_answers_like_one_nobody_has(
        reading: Reading, ben_kobo: Kobo) -> None:
    edition = edition_of("A Secret Shelf")
    name = kobo.name_of(reading.shelf.ben.id, edition)
    assert ben_kobo.browser.put(f"/api/v1/editions/{edition}/kobo", headers=TAB).status_code == 200
    entries, token = sync_all(ben_kobo)
    assert titles_in(entries) == ["A Secret Shelf"]
    unknown = "0" * 8 + "-0000-4000-8000-" + "0" * 12
    reachable = {
        "metadata": f"/v1/library/{name}/metadata", "state": f"/v1/library/{name}/state",
        "file": f"/download/{name}.epub", "cover": f"/image/{name}/200/300/false/image.jpg"}
    for what, path in reachable.items():
        assert ben_kobo.get(path).status_code in (200, 404), what
    assert ben_kobo.get(reachable["metadata"]).status_code == 200 and ben_kobo.get(reachable["file"]).status_code == 200
    with SessionLocal() as db:
        db.execute(delete(LibraryMember).where(LibraryMember.library_id == reading.shelf.private,
                                               LibraryMember.user_id == reading.shelf.ben.id))
        db.commit()
    gone, token = sync_all(ben_kobo, token)
    assert kinds(gone) == ["ChangedEntitlement"] and gone[0]["ChangedEntitlement"]["BookEntitlement"]["IsRemoved"]
    assert "Secret" not in json.dumps(gone)
    for what, path in reachable.items():
        answer = ben_kobo.get(path)
        stranger = ben_kobo.get(path.replace(name, unknown))
        assert answer.status_code == stranger.status_code == 404, what
        assert answer.text == stranger.text and "Secret" not in answer.text, what
    assert ben_kobo.app.put(f"{ben_kobo.root}/v1/library/{name}/state", json={"ReadingStates": []}).status_code == 404
    assert ben_kobo.app.delete(f"{ben_kobo.root}/v1/library/{name}").status_code == 404
    assert sync_all(ben_kobo, token) == ([], token)
    with SessionLocal() as db:
        # (Nothing of the book was kept for the device once it was told away.)
        assert db.scalars(select(KoboHeld)).all() == []


def test_a_book_whose_edition_is_gone_is_told_away_too(reading: Reading, ada_kobo: Kobo) -> None:
    name = ada_kobo.list("Kite Season")
    entries, token = sync_all(ada_kobo)
    assert titles_in(entries) == ["Kite Season"]
    with SessionLocal() as db:
        db.execute(text("PRAGMA foreign_keys = ON"))
        db.execute(delete(Edition).where(Edition.title == "Kite Season"))
        db.commit()
    gone, token = sync_all(ada_kobo, token)
    assert kinds(gone) == ["ChangedEntitlement"] and gone[0]["ChangedEntitlement"]["BookEntitlement"]["Id"] == name


def test_a_book_whose_file_is_gone_or_protected_is_told_away(reading: Reading, ada_kobo: Kobo) -> None:
    ada_kobo.list("Kite Season")
    ada_kobo.list("The Quiet Orchard")
    entries, token = sync_all(ada_kobo)
    assert len(entries) == 2
    with SessionLocal() as db:
        for title, change in (("Kite Season", {"missing_at": utcnow()}), ("The Quiet Orchard", {"probe": {"drm": True}})):
            file = db.scalars(select(BookFile).join(Edition, Edition.id == BookFile.edition_id).where(
                Edition.title == title, BookFile.format == "epub")).one()
            for key, value in change.items():
                setattr(file, key, value)
        db.commit()
    gone, _token = sync_all(ada_kobo, token)
    assert kinds(gone) == ["ChangedEntitlement"] * 2 and all(
        entry["ChangedEntitlement"]["BookEntitlement"]["IsRemoved"] for entry in gone)


def test_a_book_that_changed_is_told_as_changed_and_the_place_is_not_repeated(reading: Reading, ada_kobo: Kobo) -> None:
    name = ada_kobo.list("Kite Season")
    _entries, token = sync_all(ada_kobo)
    with SessionLocal() as db:
        db.execute(text("UPDATE editions SET title = 'Kite Season Revised' WHERE title = 'Kite Season'"))
        db.commit()
    changed, token = sync_all(ada_kobo, token)
    assert kinds(changed) == ["ChangedEntitlement"]
    assert changed[0]["ChangedEntitlement"]["BookMetadata"]["Title"] == "Kite Season Revised"
    # The name stays: the device may ask for a book by any field that names it, and every way to it looks up this one.
    assert changed[0]["ChangedEntitlement"]["BookEntitlement"]["RevisionId"] == name
    assert changed[0]["ChangedEntitlement"]["BookMetadata"]["RevisionId"] == name
    assert sync_all(ada_kobo, token) == ([], token)


# --- The answers of the sync: pages, lost answers ---------------------------------------------------------------------


def test_a_long_list_comes_in_pages_and_the_device_is_told_to_ask_again(ada_kobo: Kobo,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kobo, "PAGE", 2)
    for title in ("The Quiet Orchard", "Kite Season", "The Loud Orchard"):
        ada_kobo.list(title)
    first, token, more = sync_once(ada_kobo)
    assert len(first) == 2 and more
    second, token, more = sync_once(ada_kobo, token)
    assert len(second) == 1 and not more
    assert sorted(titles_in(first + second)) == ["Kite Season", "The Loud Orchard", "The Quiet Orchard"]
    assert sync_once(ada_kobo, token) == ([], token, False)


def test_an_answer_the_device_did_not_take_is_told_again(ada_kobo: Kobo) -> None:
    _nothing, start, _more = sync_once(ada_kobo)
    ada_kobo.list("Kite Season")
    first, lost, _more = sync_once(ada_kobo, start)
    assert kinds(first) == ["NewEntitlement"]
    # The device never got it and comes back with the token it had: it hears the same again.
    again, kept, _more = sync_once(ada_kobo, start)
    assert kinds(again) == ["NewEntitlement"] and titles_in(again) == titles_in(first)
    assert kept != lost
    # The second answer is the one it took: nothing more. The token of the lost one is older than that, and it changes
    # nothing the device holds now.
    assert sync_once(ada_kobo, kept) == ([], kept, False)
    old_again, _token, _more = sync_once(ada_kobo, lost)
    assert old_again == []


def test_a_removal_the_device_did_not_take_is_told_again(ada_kobo: Kobo) -> None:
    ada_kobo.list("Kite Season")
    _entries, token = sync_all(ada_kobo)
    assert ada_kobo.browser.delete(f"/api/v1/editions/{edition_of('Kite Season')}/kobo", headers=TAB).status_code == 200
    first, _lost, _more = sync_once(ada_kobo, token)
    again, kept, _more = sync_once(ada_kobo, token)
    assert kinds(first) == kinds(again) == ["ChangedEntitlement"]
    assert sync_once(ada_kobo, kept) == ([], kept, False)


@pytest.mark.parametrize("token", ["", "x", "not base64 at all!", "e30=", "eyJiIjogLTF9", "eyJiIjogInoifQ==", "A" * 500,
                                   "W10=", "bnVsbA=="])
def test_a_token_that_is_none_is_a_new_device(ada_kobo: Kobo, token: str) -> None:
    ada_kobo.list("Kite Season")
    sync_all(ada_kobo)
    assert kinds(sync_once(ada_kobo, token)[0]) == ["NewEntitlement"]


def test_a_token_from_the_future_is_taken_as_the_newest_answer_and_no_more(ada_kobo: Kobo) -> None:
    forged = kobo.make_token(10**9)
    # With nothing to tell, the answer does not carry the forged number on: it would make the next answer count as taken
    # before the device has seen it.
    nothing, first, _more = sync_once(ada_kobo, forged)
    assert nothing == [] and kobo.read_token(first) == 0
    ada_kobo.list("Kite Season")
    entries, token, _more = sync_once(ada_kobo, first)
    assert kinds(entries) == ["NewEntitlement"] and kobo.read_token(token) == 1
    # The device never took that answer and comes back with the token it had: it is told again.
    assert kinds(sync_once(ada_kobo, first)[0]) == ["NewEntitlement"]
    with SessionLocal() as db:
        assert db.scalars(select(EreaderLink.sync_batch)).one() == 2


def test_two_syncs_at_once_make_one_entry(ada_kobo: Kobo, reading: Reading) -> None:
    from concurrent.futures import ThreadPoolExecutor

    ada_kobo.list("Kite Season")
    with ThreadPoolExecutor(4) as pool:
        answers = list(pool.map(lambda _n: sync_once(ada_kobo, None), range(4)))
    assert all(kinds(entries) == ["NewEntitlement"] for entries, _token, _more in answers)
    with SessionLocal() as db:
        assert len(db.scalars(select(KoboHeld)).all()) == 1


def test_each_connection_holds_its_own_state(reading: Reading, ada_kobo: Kobo) -> None:
    ada_kobo.list("Kite Season")
    second = Kobo(reading, device(), ada_kobo.browser, connect(ada_kobo.browser, "Kobo Libra", kind="kobo"))
    first_entries, first_token = sync_all(ada_kobo)
    assert kinds(first_entries) == ["NewEntitlement"]
    # The other Kobo has not been told anything yet.
    assert kinds(sync_all(second)[0]) == ["NewEntitlement"]
    assert sync_all(ada_kobo, first_token) == ([], first_token)


# --- Taking a book off the device by hand --------------------------------------------------------------------------


def test_a_book_taken_off_the_device_is_not_sent_to_it_again_until_it_is_put_on_the_list_anew(
        reading: Reading, ada_kobo: Kobo) -> None:
    name = ada_kobo.list("Kite Season")
    other = Kobo(reading, device(), ada_kobo.browser, connect(ada_kobo.browser, "Kobo Libra", kind="kobo"))
    _entries, token = sync_all(ada_kobo)
    assert ada_kobo.app.delete(f"{ada_kobo.root}/v1/library/{name}").status_code == 204
    assert sync_all(ada_kobo, token) == ([], token)
    # It stays on the list, and another Kobo of the person is still told.
    assert ada_kobo.browser.get("/api/v1/ereaders").json()["kobo"] == [edition_of("Kite Season")]
    assert kinds(sync_all(other)[0]) == ["NewEntitlement"]
    # The person puts it there again: this device gets it.
    ada_kobo.list("Kite Season")
    assert kinds(sync_all(ada_kobo, token)[0]) == ["NewEntitlement"]


def test_a_book_taken_off_the_device_that_is_not_on_the_list_is_not_a_book(ada_kobo: Kobo) -> None:
    for name in ("x", "0" * 8 + "-0000-4000-8000-" + "0" * 12, "a" * 5000):
        assert ada_kobo.app.delete(f"{ada_kobo.root}/v1/library/{name}").status_code == 404, name[:20]
    # A book of the library that is not on the list is not one either.
    other = kobo.name_of(ada_kobo.reading.shelf.ada.id, edition_of("Kite Season"))
    assert ada_kobo.app.delete(f"{ada_kobo.root}/v1/library/{other}").status_code == 404
    # (An address that climbs out is an address of no route, and is answered empty.)
    assert ada_kobo.app.delete(f"{ada_kobo.root}/v1/library/%2e%2e%2f%2e%2e%2fetc%2fpasswd").status_code in (200, 404)
    with SessionLocal() as db:
        assert db.scalars(select(KoboHeld)).all() == []


# --- The file and the cover ------------------------------------------------------------------------------------------


def test_the_file_goes_out_as_it_lies_in_the_library_with_the_checks_of_a_download(reading: Reading,
                                                                                    ada_kobo: Kobo) -> None:
    name = ada_kobo.list("Kite Season")
    answer = ada_kobo.get(f"/download/{name}.epub")
    assert answer.status_code == 200 and answer.content[:2] == b"PK"
    assert answer.headers["content-disposition"].startswith("attachment")
    assert "Kite%20Season.epub" in answer.headers["content-disposition"]
    assert answer.headers["x-content-type-options"] == "nosniff"
    with SessionLocal() as db:
        row = db.scalars(select(BookFile).join(Edition, Edition.id == BookFile.edition_id).where(
            Edition.title == "Kite Season", BookFile.format == "epub")).one()
        path = db.execute(text("SELECT path FROM libraries WHERE id = :id"), {"id": row.library_id}).scalar_one()
    from pathlib import Path

    assert answer.content == (Path(path) / row.path).read_bytes()
    part = ada_kobo.get(f"/download/{name}.epub", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == answer.content[:10]
    assert ada_kobo.get(f"/download/{name}.epub", headers={"If-None-Match": answer.headers["etag"]}).status_code == 304
    # Not by the number of a file, not by any name that is not on the list, not without the ending.
    for odd in (f"{name}", f"{name}.pdf", f"{name}.epub.epub", "1.epub", f"x{name}.epub", f"{name.upper()}.epub",
                ".epub", "x" * 300 + ".epub"):
        assert ada_kobo.get(f"/download/{odd}").status_code == 404, odd
    # A book of the library that is not on the list is not reachable by its name.
    other = kobo.name_of(reading.shelf.ada.id, edition_of("The Quiet Orchard"))
    assert ada_kobo.get(f"/download/{other}.epub").status_code == 404


def test_the_file_is_one_of_the_open_files_of_the_person(ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    name = ada_kobo.list("Kite Season")
    monkeypatch.setitem(brakes.LIMITS, "stream", 2)
    codes = [ada_kobo.get(f"/download/{name}.epub").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_a_head_on_a_file_or_a_cover_tells_what_a_get_would_bring_and_holds_no_place_for_open_files(
        ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    file = ada_kobo.app.head(f"{ada_kobo.root}/download/{name}.epub")
    whole = ada_kobo.get(f"/download/{name}.epub")
    assert file.status_code == whole.status_code == 200 and file.content == b""
    assert file.headers["content-type"] == whole.headers["content-type"] == "application/epub+zip"
    assert file.headers["content-length"] == str(len(whole.content))
    cover = ada_kobo.app.head(f"{ada_kobo.root}/image/{name}/100/150/false/image.jpg")
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/jpeg"
    # A book that is not there is not there for a HEAD either.
    assert ada_kobo.app.head(f"{ada_kobo.root}/download/{'0' * 8}.epub").status_code == 404
    monkeypatch.setitem(brakes.LIMITS, "stream", 10_000)
    for _ in range(40):
        assert ada_kobo.app.head(f"{ada_kobo.root}/download/{name}.epub").status_code == 200


def test_the_cover_is_a_jpeg_no_bigger_than_the_device_asked_for(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    short = ada_kobo.get(f"/image/{name}/60/90/false/image.jpg")
    assert short.status_code == 200 and short.headers["content-type"] == "image/jpeg" and short.content[:2] == b"\xff\xd8"
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(short.content)) as picture:
        assert picture.width <= 60 and picture.height <= 90
    # The second form of the address the device is handed (quality and grey scale), the same picture.
    both = ada_kobo.get(f"/image/{name}/60/90/80/false/image.jpg")
    assert both.status_code == 200 and both.content == short.content
    # A picture is never enlarged, and a huge size is a size the picture has.
    huge = ada_kobo.get(f"/image/{name}/99999/99999/false/image.jpg")
    assert huge.status_code == 200
    assert huge.headers["etag"] == ada_kobo.get(f"/image/{name}/2000/2000/false/image.jpg").headers["etag"]
    with Image.open(BytesIO(huge.content)) as picture:
        assert picture.width <= kobo.MAX_PICTURE and picture.height <= kobo.MAX_PICTURE
    assert ada_kobo.get(f"/image/{name}/60/90/false/image.jpg", headers={"If-None-Match": short.headers["etag"]}
                        ).status_code == 304
    for odd in ("/image/{n}/abc/90/false/image.jpg", "/image/{n}/60/-1/false/image.jpg",
                "/image/{n}/600000/90/false/image.jpg", "/image/x/60/90/false/image.jpg"):
        assert ada_kobo.get(odd.format(n=name)).status_code == 404, odd
    # (Another file name at the end is a route nobody has: the empty answer.)
    assert ada_kobo.get(f"/image/{name}/60/90/false/other.jpg").json() == {}


def test_a_book_with_no_cover_has_no_picture(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("Kite Season")
    assert ada_kobo.get(f"/image/{name}/60/90/false/image.jpg").status_code == 404


# --- The place -------------------------------------------------------------------------------------------------------


def state_of(box: Kobo, name: str) -> dict[str, Any]:
    answer = box.get(f"/v1/library/{name}/state")
    assert answer.status_code == 200, answer.text
    [found] = answer.json()
    return found  # type: ignore[no-any-return]


def reading_at(name: str, percent: float, *, location: dict[str, str] | None = LOCATION, status: str = "Reading",
               content: float = 40.0) -> dict[str, Any]:
    bookmark: dict[str, Any] = {"ProgressPercent": percent, "ContentSourceProgressPercent": content,
                                "LastModified": "2026-10-13T12:00:00Z"}
    if location is not None:
        bookmark["Location"] = location
    return {"EntitlementId": name, "CurrentBookmark": bookmark, "StatusInfo": {"Status": status},
            "Statistics": {"SpentReadingMinutes": 3}}


def test_a_place_reported_by_the_kobo_is_the_place_the_browser_opens_at_and_comes_back_as_it_was(
        reading: Reading, ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    answer = ada_kobo.put_state(name, reading_at(name, 42.5))
    assert answer.status_code == 200
    assert answer.json() == {"RequestResult": "Success", "UpdateResults": [{
        "EntitlementId": name, "CurrentBookmarkResult": {"Result": "Success"}, "StatisticsResult": {"Result": "Success"},
        "StatusInfoResult": {"Result": "Success"}}]}
    place = ada_kobo.browser.get(f"/api/v1/editions/{edition}/progress").json()
    assert place["locator"] == "" and place["fraction"] == pytest.approx(0.425) and place["device"] == "My Kobo"
    assert place["device_key"].startswith("kb-")
    with SessionLocal() as db:
        row = db.get(Progress, (reading.shelf.ada.id, edition))
        assert row is not None and json.loads(row.kobo) == {"v": LOCATION["Value"], "t": "KoboSpan",
                                                           "s": LOCATION["Source"], "c": 40.0}
        assert row.koreader == ""
    got = state_of(ada_kobo, name)
    assert got["CurrentBookmark"]["ProgressPercent"] == pytest.approx(42.5)
    assert got["CurrentBookmark"]["ContentSourceProgressPercent"] == 40.0
    assert got["CurrentBookmark"]["Location"] == {**LOCATION}
    assert got["StatusInfo"]["Status"] == "Reading" and got["EntitlementId"] == name


def test_a_whole_percentage_is_told_as_a_whole_number(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    ada_kobo.put_state(name, reading_at(name, 50.0, content=25.0))
    bookmark = state_of(ada_kobo, name)["CurrentBookmark"]
    assert isinstance(bookmark["ProgressPercent"], int) and isinstance(bookmark["ContentSourceProgressPercent"], int)
    ada_kobo.put_state(name, reading_at(name, 50.25, content=25.5))
    bookmark = state_of(ada_kobo, name)["CurrentBookmark"]
    assert bookmark["ProgressPercent"] == 50.25 and bookmark["ContentSourceProgressPercent"] == 25.5


def test_one_state_that_names_no_book_is_the_state_of_the_book_in_the_address(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    state = reading_at(name, 33.0)
    del state["EntitlementId"]
    assert ada_kobo.put_state(name, state).status_code == 200
    place = ada_kobo.browser.get(f"/api/v1/editions/{edition}/progress").json()
    assert place["fraction"] == pytest.approx(0.33)


def test_two_states_that_name_no_book_are_not_taken_for_one(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    state = reading_at(name, 33.0)
    del state["EntitlementId"]
    answer = ada_kobo.app.put(f"{ada_kobo.root}/v1/library/{name}/state", json={"ReadingStates": [state, state]})
    assert answer.status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(Progress)).all() == []


def test_a_place_from_the_browser_comes_to_the_kobo_as_a_share_without_the_old_location(
        reading: Reading, ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    ada_kobo.put_state(name, reading_at(name, 20))
    tick.advance(seconds=30)
    saved = ada_kobo.browser.put(f"/api/v1/editions/{edition}/progress", headers=TAB, json={
        "locator": "epubcfi(/6/4!/4/2/8)", "fraction": 0.7, "device_key": "tab-abcdefgh", "device": "Firefox"})
    assert saved.json()["applied"] is True
    got = state_of(ada_kobo, name)
    assert got["CurrentBookmark"]["ProgressPercent"] == pytest.approx(70.0)
    assert "Location" not in got["CurrentBookmark"]
    with SessionLocal() as db:
        assert db.scalars(select(Progress.kobo)).one() == ""


def test_an_older_report_does_not_take_the_place_of_a_newer_one(reading: Reading, ada_kobo: Kobo,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    tick.advance(seconds=30)
    ada_kobo.browser.put(f"/api/v1/editions/{edition}/progress", headers=TAB, json={
        "locator": "epubcfi(/6/4)", "fraction": 0.8, "device_key": "tab-abcdefgh", "device": "Firefox"})
    tick.advance(seconds=-20)
    assert ada_kobo.put_state(name, reading_at(name, 30)).status_code == 200
    place = ada_kobo.browser.get(f"/api/v1/editions/{edition}/progress").json()
    assert place["fraction"] == pytest.approx(0.8) and place["device"] == "Firefox"
    tick.advance(seconds=60)
    ada_kobo.put_state(name, reading_at(name, 90))
    assert ada_kobo.browser.get(f"/api/v1/editions/{edition}/progress").json()["fraction"] == pytest.approx(0.9)


def test_a_place_the_kobo_reported_is_not_told_back_to_it_but_to_the_other_kobo(reading: Reading,
                                                                                 ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    second = Kobo(reading, device(), ada_kobo.browser, connect(ada_kobo.browser, "Kobo Libra", kind="kobo"))
    _entries, first_token = sync_all(ada_kobo)
    _entries, second_token = sync_all(second)
    ada_kobo.put_state(name, reading_at(name, 55))
    assert sync_all(ada_kobo, first_token) == ([], first_token)
    told, second_token = sync_all(second, second_token)
    assert kinds(told) == ["ChangedReadingState"]
    state = told[0]["ChangedReadingState"]["ReadingState"]
    assert state["EntitlementId"] == name and state["CurrentBookmark"]["ProgressPercent"] == pytest.approx(55.0)
    assert sync_all(second, second_token) == ([], second_token)


def test_a_new_book_comes_with_the_place_the_person_is_at(reading: Reading, ada_kobo: Kobo) -> None:
    edition = edition_of("The Quiet Orchard")
    ada_kobo.browser.put(f"/api/v1/editions/{edition}/progress", headers=TAB, json={
        "locator": "epubcfi(/6/4)", "fraction": 0.3, "device_key": "tab-abcdefgh", "device": "Firefox"})
    ada_kobo.list("The Quiet Orchard")
    [entry] = sync_all(ada_kobo)[0]
    state = entry["NewEntitlement"]["ReadingState"]
    assert state["StatusInfo"]["Status"] == "Reading" and state["CurrentBookmark"]["ProgressPercent"] == pytest.approx(30.0)


def test_finished_on_the_kobo_is_the_end_of_the_book(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    ada_kobo.put_state(name, {"EntitlementId": name, "StatusInfo": {"Status": "Finished"}})
    place = ada_kobo.browser.get(f"/api/v1/editions/{edition_of('The Quiet Orchard')}/progress").json()
    assert place["fraction"] == 1.0
    assert state_of(ada_kobo, name)["StatusInfo"]["Status"] == "Finished"


def test_a_place_the_koreader_reports_replaces_the_location_of_the_kobo(reading: Reading, ada_kobo: Kobo,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import kosync

    tick = Clock(monkeypatch)
    name = ada_kobo.list("The Quiet Orchard")
    ada_kobo.put_state(name, reading_at(name, 20))
    with SessionLocal() as db:
        assert json.loads(db.scalars(select(Progress.kobo)).one())["v"] == LOCATION["Value"]
        document = db.scalars(select(BookFile.partial_md5).join(Edition, Edition.id == BookFile.edition_id).where(
            Edition.title == "The Quiet Orchard", BookFile.format == "epub")).one()
        tick.advance(seconds=30)
        assert kosync.store(db, reading.shelf.ada.id, document, pointer="/body/DocFragment[2]", percentage=0.6,
                            device="KOReader", device_id="e2e0kr")
    with SessionLocal() as db:
        row = db.scalars(select(Progress)).one()
        assert row.kobo == "" and row.koreader == "/body/DocFragment[2]"
    got = state_of(ada_kobo, name)
    assert got["CurrentBookmark"]["ProgressPercent"] == pytest.approx(60.0) and "Location" not in got["CurrentBookmark"]
    # And the Kobo reports again: the location of KOReader goes.
    tick.advance(seconds=30)
    ada_kobo.put_state(name, reading_at(name, 70))
    with SessionLocal() as db:
        row = db.scalars(select(Progress)).one()
        assert row.koreader == "" and json.loads(row.kobo)["s"] == LOCATION["Source"]


@pytest.mark.parametrize("state", [
    None, [], "x", 5, {"ReadingStates": "x"}, {"ReadingStates": [5, None, "x", []]}, {"ReadingStates": []},
    {"ReadingStates": [{"EntitlementId": "someone else"}]},
    {"ReadingStates": [{"EntitlementId": "{name}", "CurrentBookmark": 5}]},
    {"ReadingStates": [{"EntitlementId": "{name}", "CurrentBookmark": {"ProgressPercent": "50"}}]},
    {"ReadingStates": [{"EntitlementId": "{name}", "CurrentBookmark": {"ProgressPercent": True}}]},
    {"ReadingStates": [{"EntitlementId": "{name}", "CurrentBookmark": {"ProgressPercent": None}}]},
    {"ReadingStates": [{"EntitlementId": "{name}", "CurrentBookmark": {"ProgressPercent": float("nan")}}]},
    {"ReadingStates": [{"EntitlementId": "{name}", "CurrentBookmark": {"ProgressPercent": float("inf")}}]},
], ids=lambda value: json.dumps(value, default=str)[:60])
def test_a_report_that_is_not_one_changes_nothing(ada_kobo: Kobo, state: Any) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    body = json.loads(json.dumps(state, default=str).replace("{name}", name).replace("NaN", "null")
                      .replace("Infinity", "null")) if state is not None else None
    answer = ada_kobo.app.put(f"{ada_kobo.root}/v1/library/{name}/state", json=body)
    assert answer.status_code == 200 and answer.json()["RequestResult"] == "Success"
    with SessionLocal() as db:
        assert db.scalars(select(Progress)).all() == []


def test_a_percentage_outside_the_book_is_its_start_or_its_end_and_a_hostile_location_is_dropped(
        ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")

    def fraction() -> float:
        return float(ada_kobo.browser.get(f"/api/v1/editions/{edition}/progress").json()["fraction"])

    ada_kobo.put_state(name, reading_at(name, 250.0, status="Reading"))
    assert fraction() == 1.0
    ada_kobo.put_state(name, reading_at(name, -5.0))
    assert fraction() == 0.0
    for odd in ({"Value": "x" * 500, "Type": "KoboSpan", "Source": "a.xhtml"},
                {"Value": "a\x00b", "Type": "KoboSpan", "Source": "a.xhtml"},
                {"Value": 5, "Type": "KoboSpan", "Source": "a.xhtml"}, {"Value": "a"}, "text", 5, []):
        assert ada_kobo.put_state(name, reading_at(name, 33, location=odd)).status_code == 200  # type: ignore[arg-type]
        assert fraction() == pytest.approx(0.33)
        with SessionLocal() as db:
            assert db.scalars(select(Progress.kobo)).one() == ""


def test_a_body_that_is_not_json_or_too_big_is_taken_for_nothing_or_turned_down(ada_kobo: Kobo) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    path = f"{ada_kobo.root}/v1/library/{name}/state"
    for raw in (b"not json", b"", b"[" * 20000, b'{"ReadingStates": [' * 1500):
        answer = ada_kobo.app.put(path, content=raw)
        assert answer.status_code == 200, raw[:20]
    with SessionLocal() as db:
        assert db.scalars(select(Progress)).all() == []
    big = ada_kobo.app.put(path, content=b" " * (33 * 1024))
    assert big.status_code == 413
    huge = ada_kobo.app.post(f"{ada_kobo.root}/v1/auth/device", content=b" " * (40 * 1024))
    assert huge.status_code == 413
    # A body with no length given is counted while it arrives.
    def pieces() -> Any:
        for _ in range(40):
            yield b" " * 1024

    assert ada_kobo.app.put(path, content=pieces()).status_code == 413
    # A big body for a secret that does not exist is turned down by the size alone, before anything is looked up.
    assert ada_kobo.app.put(f"/kobo/nbe_{secrets.token_urlsafe(24)}/v1/library/x/state",
                            content=b" " * (40 * 1024)).status_code == 413


# --- The secret does not leave ---------------------------------------------------------------------------------------


def test_the_secret_is_in_no_answer_but_the_one_with_the_addresses(ada_kobo: Kobo, ben_kobo: Kobo,
                                                                    caplog: pytest.LogCaptureFixture) -> None:
    name = ada_kobo.list("The Quiet Orchard")
    wrong = "nbe_" + secrets.token_urlsafe(24)
    calls: list[Callable[[], Any]] = [
        lambda: ada_kobo.get("/v1/library/tags"), lambda: ada_kobo.get(f"/v1/library/{name}/state"),
        lambda: ada_kobo.get("/v1/library/" + "0" * 8 + "-0000-4000-8000-" + "0" * 12 + "/metadata"),
        lambda: ada_kobo.get("/download/nothing.epub"), lambda: ada_kobo.get("/image/x/1/1/false/image.jpg"),
        lambda: ada_kobo.get("/v1/user/profile"), lambda: ada_kobo.get("/anything/at/all/"),
        lambda: ada_kobo.get(""), lambda: ada_kobo.get("/"),
        lambda: ada_kobo.app.put(f"{ada_kobo.root}/v1/library/{name}/state", content=b" " * 40000),
        lambda: ada_kobo.app.delete(f"{ada_kobo.root}/v1/library/nothing"),
        lambda: ada_kobo.app.post(f"{ada_kobo.root}/v1/auth/device", json={"UserKey": "u"}),
        lambda: ada_kobo.app.get(f"/kobo/{wrong}/v1/library/sync"),
        lambda: ada_kobo.app.get(f"/kobo/{ada_kobo.secret[:-3]}/v1/library/sync"),
    ]
    caplog.set_level(logging.DEBUG)
    for call in calls:
        answer = call()
        shown = answer.text + " ".join(f"{key}: {value}" for key, value in answer.headers.items())
        for secret in (ada_kobo.secret, wrong, ben_kobo.secret):
            assert secret not in shown, answer.request.url.path
    # The answers that must hold it: the addresses the device is handed (the sync and the metadata hand out the address of
    # the file).
    assert ada_kobo.secret in ada_kobo.get("/v1/initialization").text
    assert ada_kobo.secret in json.dumps(ada_kobo.get("/v1/library/sync").json())
    assert ada_kobo.secret in json.dumps(ada_kobo.get(f"/v1/library/{name}/metadata").json())
    assert ada_kobo.secret not in caplog.text and wrong not in caplog.text


@pytest.mark.parametrize("slow", [False, True], ids=["normal", "slow"])
def test_the_secret_does_not_reach_the_log_file(ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch, slow: bool) -> None:
    """The line of an ordinary request and the line of a slow one are two places that print the address."""
    from app import middleware

    logs.set_mode("detailed")
    if slow:
        monkeypatch.setattr(middleware, "SLOW_MS", 0)
    name = ada_kobo.list("Kite Season")
    wrong = "nbe_" + secrets.token_urlsafe(24)
    ada_kobo.get("/v1/initialization")
    ada_kobo.get("/v1/library/sync")
    ada_kobo.get(f"/download/{name}.epub")
    ada_kobo.get("/v1/nothing/here")
    ada_kobo.app.get(f"/kobo/{wrong}/v1/library/sync")
    ada_kobo.app.get(f"/kobo/{secrets.token_urlsafe(24)}/v1/library/sync")
    raw = logs.log_file().read_text(encoding="utf-8")
    assert ("Slow request" in raw) is slow and "/kobo/" in raw
    assert ada_kobo.secret not in raw and wrong not in raw and ada_kobo.secret[4:] not in raw


def test_an_error_inside_the_sync_does_not_carry_the_secret_into_the_log(ada_kobo: Kobo, monkeypatch: pytest.MonkeyPatch,
                                                                         caplog: pytest.LogCaptureFixture) -> None:
    """An unhandled error is logged with the path of its request; the traceback and the line are both cleaned."""
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"failed for {ada_kobo.root}/v1/library/sync")

    monkeypatch.setattr(kobo, "sync", broken)
    logs.set_mode("detailed")
    failing = TestClient(app, base_url="http://testserver", raise_server_exceptions=False)
    answer = failing.get(ada_kobo.root + "/v1/library/sync")
    assert answer.status_code == 500 and ada_kobo.secret not in answer.text
    raw = logs.log_file().read_text(encoding="utf-8")
    assert "Unhandled error" in raw and ada_kobo.secret not in raw


def test_the_masking_covers_the_address_of_a_kobo_wherever_it_stands() -> None:
    for secret in ("nbe_" + secrets.token_urlsafe(24), secrets.token_urlsafe(24)):
        for line in (f"GET /kobo/{secret}/v1/library/sync -> 200 in 3ms", f"Slow request: PUT /kobo/{secret}/x?y=1 -> 404",
                     f"Traceback ... '/kobo/{secret}'", f"GET /kobo/{secret} -> 401"):
            assert secret not in logs.redact(line), line
    assert logs.redact("GET /kobo/nbe_" + "a" * 32 + "/x") == "GET /kobo/nbe_…/x"


def test_the_checks_of_the_log_know_the_start_of_a_secret_of_a_kobo() -> None:
    from app.services import ereaders

    assert ereaders.PREFIXES["kobo"] == "nbe_" and "nbe_" in logs._FEED_KEY.pattern


# --- The operator ----------------------------------------------------------------------------------------------------


def test_the_operator_sees_and_blocks_the_connections_of_a_kobo_and_nothing_of_the_list(
        client: TestClient, operator: Account, ada_kobo: Kobo) -> None:
    ada_kobo.list("Kite Season")
    every = client.get("/api/v1/admin/ereaders/links").json()
    assert [(row["kind"], row["account"], row["blocked"]) for row in every] == [("kobo", "ada", False)]
    assert ada_kobo.secret not in json.dumps(every) and "Kite" not in json.dumps(every)
    assert client.post(f"/api/v1/admin/ereaders/links/{every[0]['id']}/block").json()["blocked"] is True
    assert ada_kobo.get("/v1/library/sync").status_code == 401
    # The operator who is no member of the library does not see the book on the person's list.
    assert "Kite" not in client.get("/api/v1/ereaders").text


def test_a_deleted_connection_takes_what_it_held_and_a_deleted_person_takes_the_list(reading: Reading,
                                                                                      ada_kobo: Kobo) -> None:
    ada_kobo.list("Kite Season")
    sync_all(ada_kobo)
    link = ada_kobo.browser.get("/api/v1/ereaders").json()["links"][0]["id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(KoboHeld)).all()) == 1
    assert ada_kobo.browser.delete(f"/api/v1/ereaders/links/{link}", headers=TAB).status_code == 204
    with SessionLocal() as db:
        assert db.scalars(select(KoboHeld)).all() == [] and len(db.scalars(select(KoboListed)).all()) == 1


def test_a_place_with_a_lone_surrogate_keeps_the_share_and_drops_the_text_that_cannot_be_sent(
        reading: Reading, ada_kobo: Kobo) -> None:
    """A Kobo (or anybody with its secret) reports a location that holds half of a character. The share of the book is
    kept; the location is not, and the sync and the state of the book go on answering."""
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    body = ('{"ReadingStates":[{"EntitlementId":"' + name + '","CurrentBookmark":{"ProgressPercent":40.0,"Location":'
            '{"Value":"\\ud800","Type":"KoboSpan","Source":"a.xhtml"}},"StatusInfo":{"Status":"Reading"}}]}')
    put = ada_kobo.app.put(f"{ada_kobo.root}/v1/library/{name}/state", content=body,
                           headers={"content-type": "application/json"})
    assert put.status_code == 200
    with SessionLocal() as db:
        row = db.get(Progress, (reading.shelf.ada.id, edition))
        assert row is not None and row.kobo == "" and row.fraction == pytest.approx(0.4)
    assert ada_kobo.get(f"/v1/library/{name}/state").status_code == 200
    sync_once(ada_kobo)


def test_a_place_already_kept_with_a_lone_surrogate_does_not_stop_the_sync(reading: Reading, ada_kobo: Kobo) -> None:
    """A row from before the check: read, it is as if there were no location."""
    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    assert ada_kobo.put_state(name, reading_at(name, 40.0)).status_code == 200
    with SessionLocal() as db:
        row = db.get(Progress, (reading.shelf.ada.id, edition))
        assert row is not None
        row.kobo = '{"v":"\\ud800","t":"KoboSpan","s":"a.xhtml","c":1.0}'
        db.commit()
    got = ada_kobo.get(f"/v1/library/{name}/state")
    assert got.status_code == 200 and "Location" not in got.json()[0]["CurrentBookmark"]
    sync_once(ada_kobo)


def test_a_sync_that_waited_for_another_counts_on_from_the_answer_of_that_one(reading: Reading, ada_kobo: Kobo) -> None:
    """Two syncs of one Kobo at the same moment take turns, and the second must start from what the first left: it had
    read the connection before it waited (when it was let in), and a number read then is out of date. Two answers with one
    number would make a device with the older token look as if it had seen the newer answer."""
    ada_kobo.list("The Quiet Orchard")
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        link_id, uid = link.id, link.account_id
        assert link.sync_batch == 0
        with SessionLocal() as other:
            other_link = other.get(EreaderLink, link_id)
            assert other_link is not None
            other_link.sync_batch = 5
            other.commit()
        kobo.sync(db, uid, link_id, "", f"http://testserver/kobo/{ada_kobo.secret}")
    with SessionLocal() as db:
        assert db.get(EreaderLink, link_id).sync_batch == 6  # type: ignore[union-attr]


def test_two_deletes_of_a_book_at_the_same_moment_make_one_row(reading: Reading, ada_kobo: Kobo,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """The Kobo removes a book from its own list: a second request for the same book that came in just before the first
    one wrote its row must find that row after waiting its turn, not write a second one."""
    import threading

    name = ada_kobo.list("The Quiet Orchard")
    edition = edition_of("The Quiet Orchard")
    with SessionLocal() as db:
        link_id = db.scalars(select(EreaderLink.id)).one()

    class Other:
        """The lock of the connection, taken just after another request wrote the row."""

        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.done = False

        def __enter__(self) -> None:
            self.lock.acquire()
            if not self.done:
                self.done = True
                with SessionLocal() as other:
                    other.add(KoboHeld(link_id=link_id, edition_id=edition, uuid=name, archived=True))
                    other.commit()

        def __exit__(self, *_args: object) -> None:
            self.lock.release()

    monkeypatch.setattr(kobo, "_locks", [Other() for _ in range(64)])
    gone = ada_kobo.app.delete(f"{ada_kobo.root}/v1/library/{name}")
    assert gone.status_code == 204, gone.text
    with SessionLocal() as db:
        rows = db.scalars(select(KoboHeld).where(KoboHeld.link_id == link_id)).all()
        assert [(row.edition_id, row.archived) for row in rows] == [(edition, True)]
