"""Audible is off from the start: the operator is asked once whether to use it, and until then (and after "without
Audible") nothing goes to its hosts. Recognising books, the lists of Discover and the check after a download go on
without it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, ListEntry, Setting
from app.services import jobs, settings_service
from app.services.sources import config

from .conftest import make_account, new_client
from .discover_helpers import Disc
from .fake_sources import audible_product, ol_doc
from .library_helpers import books_folder, make_library, recording, scan
from .match_helpers import World, edition_of, entry_for


def sources(client: TestClient) -> dict[str, object]:
    answer = client.get("/api/v1/sources")
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def audible_state(body: dict[str, object]) -> dict[str, object]:
    return next(item for item in body["sources"] if item["id"] == "audible")  # type: ignore[attr-defined,union-attr]


# ---- The switch and the question ---------------------------------------------------------------------------------------


def test_audible_is_off_and_not_chosen_from_the_start(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"sources_enabled": True})  # the tests start with the main switch off
    with SessionLocal() as db:
        assert settings_service.get(db, "source_audible") is False
        assert config.audible_decided(db) is False
        assert config.availability(db, "audible").state == "off"
        assert settings_service.get(db, "source_openlibrary") is True
    body = sources(client)
    assert body["audible_decided"] is False
    assert audible_state(body)["enabled"] is False and audible_state(body)["state"] == "off"


def test_the_choice_to_use_it_or_not_is_made_once_and_stays(client: TestClient, operator: Account) -> None:
    on = client.put("/api/v1/sources", json={"sources": {"audible": True}}).json()
    assert on["audible_decided"] is True and audible_state(on)["enabled"] is True
    off = client.put("/api/v1/sources", json={"sources": {"audible": False}}).json()
    assert off["audible_decided"] is True and audible_state(off)["enabled"] is False
    # Another switch does not decide it.
    with SessionLocal() as db:
        db.delete(db.get(Setting, "source_audible"))
        db.commit()
    other = client.put("/api/v1/sources", json={"sources": {"dnb": False}}).json()
    assert other["audible_decided"] is False


def test_a_switch_an_earlier_version_saved_counts_as_the_choice_it_was(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        db.add(Setting(key="source_audible", value=True))
        db.commit()
    body = sources(client)
    assert body["audible_decided"] is True and audible_state(body)["enabled"] is True


def test_the_answer_of_the_sources_for_a_member_is_the_operators_alone(client: TestClient, operator: Account) -> None:
    with new_client(make_account("reader")) as reader:
        assert reader.get("/api/v1/sources").status_code in (401, 403)


# ---- Nothing goes to Audible, and everything else goes on ------------------------------------------------------------------


def test_a_recording_is_matched_without_audible_and_stays_unproven(client: TestClient, operator: Account,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(monkeypatch, audible=False)
    world.fake.ol = [ol_doc("OL1W", "Die Uhrmacherin", ["Ada Feldkamp"], languages=("ger",), year=2019)]
    world.fake.audible = {"de": [audible_product("B0EXAMPL01", "Die Uhrmacherin", ["Ada Feldkamp"], 10)]}
    folder = books_folder("audio")
    recording(folder, "uhr/uhr.m4b", 600.0, title="Die Uhrmacherin", album="Die Uhrmacherin", artist="Ada Feldkamp")
    scan(make_library(folder, "Audio"))
    row = entry_for("Die Uhrmacherin")
    # The book may be right, but which reading it is nothing proves: a person looks, the guess is not written down.
    assert row.state != "sure" and row.reason in ("runtime_unproven", "low_score", "one_source"), (row.state, row.reason)
    edition = edition_of("Die Uhrmacherin")
    assert edition.variant_uncertain is True and edition.asin == ""
    assert world.fake.count("audible") == 0


def test_the_lists_of_discover_come_from_apple_and_the_new_and_coming_ones_from_the_themed_shelves(
        monkeypatch: pytest.MonkeyPatch, operator: Account) -> None:
    """Without Audible the new and the coming books are those of the shelves of Browse: the open
    sources, by the rules of the shelves. Audible is asked for nothing."""
    disc = Disc(monkeypatch, audible=False)
    disc.fetch("de")
    with SessionLocal() as db:
        found = {(entry.list, entry.source) for entry in db.scalars(select(ListEntry))}
    assert ("best", "apple") in found and not any(source == "audible" for _list, source in found)
    assert disc.fake.count("audible") == 0
    with new_client(make_account("reader")) as reader:
        best = reader.get("/api/v1/discover/lists/best", params={"market": "de"}).json()
        assert best["state"] == "ready" and best["items"]
        for name in ("new", "soon"):
            body = reader.get(f"/api/v1/discover/lists/{name}", params={"market": "de"}).json()
            assert body["state"] in ("fetching", "no_source") and body["items"] == [], "the shelves are not there yet"
            assert "audible" not in {a["source"] for a in body["availability"]}
        jobs.run_pending(1000)
        new = reader.get("/api/v1/discover/lists/new", params={"market": "de"}).json()
        assert new["state"] in ("ready", "no_source")
        assert set(new["sources"]) <= {"apple", "openlibrary"}
    assert disc.fake.count("audible") == 0


def test_with_audible_chosen_its_lists_come_back(monkeypatch: pytest.MonkeyPatch, operator: Account) -> None:
    disc = Disc(monkeypatch, audible=True)
    disc.fetch("de")
    with SessionLocal() as db:
        assert "audible" in {entry.source for entry in db.scalars(select(ListEntry).where(ListEntry.list == "best"))}
