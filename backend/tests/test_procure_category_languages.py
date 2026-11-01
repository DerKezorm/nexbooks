"""The languages of an indexer's categories against the languages books are fetched in (settings, "Sprachen").

Caps in the shape of a real German indexer (``fixtures/procure/newznab_caps_languages.xml``, invented name): the
languages stand in the names of the parents (``Books - DE`` > ``Ebook``) or after a dash (``Audiobook - FR``). A
category of a language that is not fetched is never suggested, never asked by a search, even when it is saved as on,
and the settings say so. A language the operator set by hand stays when the caps are read again.
"""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account, Indexer
from app.services.procure import formats, newznab, search

from .procure_fakes import FakeIndexer, FakeNetwork, fixture

KEY = secrets.token_hex(16)
HOST = "lantern.example.com"
URL = f"https://{HOST}/api"


def recorded_caps() -> dict[str, Any]:
    root, _ = newznab.parse_xml(fixture("newznab_caps_languages.xml"))
    assert root is not None
    found = newznab.parse_caps(root)
    assert found is not None
    return found


@pytest.fixture
def indexer(net: FakeNetwork) -> FakeIndexer:
    fake = FakeIndexer(HOST, KEY)
    fake.caps = fixture("newznab_caps_languages.xml")
    return net.add(fake)  # type: ignore[no-any-return]


def fetch_in(*languages: str) -> None:
    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "languages": list(languages)})


def add(client: TestClient, **over: Any) -> dict[str, Any]:
    answer = client.post("/api/v1/procure/indexers", json={"name": "Lantern Index", "kind": "newznab", "url": URL,
                                                           "api_key": KEY, **over})
    assert answer.status_code == 201, answer.text
    return answer.json()  # type: ignore[no-any-return]


def by_id(found: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in found["categories"]}


def test_a_category_takes_the_language_of_its_name_or_of_the_one_it_stands_under() -> None:
    langs = {item.id: item.lang for item in newznab.book_categories(recorded_caps())}
    assert langs == {7020: "mixed", 7100: "de", 7120: "de", 7300: "fr", 7320: "fr", 3030: "mixed", 3130: "de",
                     3230: "es", 3330: "fr"}


def test_only_categories_of_the_languages_fetched_are_suggested() -> None:
    suggested = {item.id for item in newznab.book_categories(recorded_caps(), ("de",)) if item.suggested}
    assert suggested == {7020, 7100, 7120, 3030, 3130}
    english = {item.id for item in newznab.book_categories(recorded_caps(), ("en",)) if item.suggested}
    assert english == {7020, 3030}, "no German, Spanish or French category for books fetched in English"


def test_setting_up_suggests_by_the_languages_fetched_not_by_the_interface(client: TestClient, operator: Account,
                                                                            indexer: FakeIndexer) -> None:
    fetch_in("de", "original")
    made = add(client, language="fr")
    on = {number for number, item in by_id(made).items() if item["on"]}
    assert on == {7020, 7100, 7120, 3030, 3130}
    assert made["languages"] == ["de"]
    asked = {number: item["asked"] for number, item in by_id(made).items()}
    assert asked[7320] is False and asked[3230] is False and asked[3330] is False
    assert asked[7120] is True and asked[7020] is True


def test_a_category_of_a_language_not_fetched_is_never_asked_even_when_it_is_on(client: TestClient,
                                                                               operator: Account,
                                                                               indexer: FakeIndexer) -> None:
    fetch_in("de")
    made = add(client, categories=[7020, 7120, 7320, 3030, 3330],
               category_langs={"7020": "mixed", "7120": "de", "7320": "fr", "3030": "mixed", "3330": "fr"})
    shown = by_id(made)
    assert shown[7320]["on"] is True and shown[7320]["asked"] is False
    with SessionLocal() as db:
        row = db.get(Indexer, made["id"])
        assert row is not None
        plan = search.plan_for(row, ["ebook", "audio"], ("de",))
    assert set(plan.categories) == {7020, 7120, 3030}
    # A wish in French (the original language of the book, say) asks the French ones, and only then.
    with SessionLocal() as db:
        row = db.get(Indexer, made["id"])
        assert row is not None
        french = search.plan_for(row, ["ebook"], ("fr",))
    assert set(french.categories) == {7020, 7320}


def test_nothing_of_another_language_is_asked_when_no_category_fits(client: TestClient, operator: Account,
                                                                     indexer: FakeIndexer) -> None:
    fetch_in("de")
    made = add(client, categories=[7320], category_langs={"7320": "fr"})
    with SessionLocal() as db:
        row = db.get(Indexer, made["id"])
        assert row is not None
        plan = search.plan_for(row, ["ebook"], ("de",))
    assert plan.categories == []


def saved_langs(indexer_id: int) -> dict[str, str]:
    with SessionLocal() as db:
        row = db.get(Indexer, indexer_id)
        assert row is not None
        return dict(row.category_langs or {})


def test_only_the_languages_set_apart_from_the_caps_are_saved_and_they_stay(client: TestClient, operator: Account,
                                                                            indexer: FakeIndexer) -> None:
    fetch_in("de")
    made = add(client, categories=[7020, 7120, 3130], category_langs={"7020": "mixed", "7120": "de", "3130": "en"})
    assert saved_langs(made["id"]) == {"3130": "en"}, "what the caps state needs nothing saved"
    tested = client.post(f"/api/v1/procure/indexers/{made['id']}/test")
    assert tested.status_code == 200, tested.text
    shown = {item["id"]: item for item in tested.json()["indexer"]["categories"] if item["on"]}
    assert {number: item["lang"] for number, item in shown.items()} == {7020: "mixed", 7120: "de", 3130: "en"}
    assert shown[3130]["proposed"] == "de" and tested.json()["indexer"]["proposals"] == 1
    taken = client.post(f"/api/v1/procure/indexers/{made['id']}/proposals")
    assert taken.status_code == 200, taken.text
    assert {item["id"]: item["lang"] for item in taken.json()["categories"] if item["on"]}[3130] == "de"
    assert taken.json()["proposals"] == 0 and saved_langs(made["id"]) == {}


def test_only_the_operator_takes_the_proposals_of_an_indexer(client: TestClient, operator: Account,
                                                             indexer: FakeIndexer) -> None:
    from .conftest import make_account, new_client

    fetch_in("de")
    made = add(client, categories=[3130], category_langs={"3130": "en"})
    with new_client(make_account("mira")) as member:
        refused = member.post(f"/api/v1/procure/indexers/{made['id']}/proposals")
    with new_client() as nobody:
        unknown = nobody.post(f"/api/v1/procure/indexers/{made['id']}/proposals")
    assert refused.status_code == 403 and unknown.status_code == 401
    assert saved_langs(made["id"]) == {"3130": "en"}
    assert client.post("/api/v1/procure/indexers/999999/proposals").status_code == 404


def test_an_indexer_saved_by_an_older_version_takes_the_languages_of_its_caps_once(client: TestClient,
                                                                                    operator: Account,
                                                                                    indexer: FakeIndexer) -> None:
    from app.services import settings_service
    from app.services.procure import indexer_admin

    fetch_in("de")
    untouched = add(client, name="Untouched", categories=[7020, 7120, 3130])
    changed = add(client, name="Changed", categories=[7020, 7120, 3130])
    with SessionLocal() as db:
        # What an older version saved by itself: every chosen category by its own name (7120 "Ebook" as mixed).
        db.get(Indexer, untouched["id"]).category_langs = {"7020": "mixed", "7120": "mixed", "3130": "de"}
        db.get(Indexer, changed["id"]).category_langs = {"7020": "mixed", "7120": "mixed", "3130": "en"}
        settings_service.save(db, {indexer_admin.LANGS_REV_KEY: 0})
        db.commit()
        assert indexer_admin.adopt_stated_languages(db) == 1
        assert indexer_admin.adopt_stated_languages(db) == 0, "once"
    assert saved_langs(untouched["id"]) == {}
    assert saved_langs(changed["id"]) == {"7120": "mixed", "3130": "en"}, "a changed one keeps what it has"
    card = {item["id"]: item for item in client.get("/api/v1/procure/indexers").json()["indexers"][1]["categories"]}
    assert card[7120]["lang"] == "mixed" and card[7120]["proposed"] == "de"


def test_the_test_of_an_address_marks_the_categories_not_asked(client: TestClient, operator: Account,
                                                               indexer: FakeIndexer) -> None:
    fetch_in("de", "en")
    answer = client.post("/api/v1/procure/indexers/test", json={"kind": "newznab", "url": URL, "api_key": KEY})
    assert answer.status_code == 200, answer.text
    found = {item["id"]: item for item in answer.json()["categories"]}
    assert found[7320]["asked"] is False and found[7320]["suggested"] is False and found[7320]["on"] is False
    assert found[7120]["asked"] is True and found[7120]["on"] is True
    assert answer.json()["languages"] == ["de", "en"]
