"""The languages a book is fetched in: only the ones under Settings, Acquisition,
Languages, never the language of the account (that is the interface's).

The real case: an account without a language of its own ("as the browser"), the interface in German; the dialog chose
the original language of the book (English) for the wish, which then turned down German releases and loaded on, though
the operator allowed German only. Here the same with an invented book.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import SessionLocal
from app.models import Account, ForeignBook, ProcureWish, ReleaseSearch
from app.services.procure import wishes

from .follow_helpers import AUDIO, audio_world, planner_tick, rows, sabnzbd, wish, wish_rows
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE, Item, allow_languages, search

TOKEN = "tok" + "q" * 13


def searches(world: Any) -> int:
    """The requests that looked for this book (the planner's read of the newest entries asks for no book)."""
    return sum(1 for request in world.indexer.requests if request.url.params.get("q"))


def the_wish() -> ProcureWish:
    [row] = wish_rows()
    return row


def formats_body(client: TestClient) -> dict[str, Any]:
    return {key: value for key, value in client.get("/api/v1/procure/formats").json().items()
            if key not in ("libraries", "known_ebook", "known_audio", "known_languages", "samples")}


def foreign_book(language: str = "en") -> None:
    with SessionLocal() as db:
        db.add(ForeignBook(token=TOKEN, ref="hardcover:4711", title=TITLE, authors=[AUTHOR], language=language))
        db.commit()


def test_a_wish_without_a_language_takes_the_allowed_ones_never_the_language_of_the_account(
        operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    sabnzbd(net)
    with SessionLocal() as db:
        db.execute(update(Account).where(Account.id == world.person.id).values(language="fr"))
        db.commit()
    made = wish(world, language=None)
    [result] = made["results"]
    assert result["status"] == "created" and result["wish"]["language"] == ""
    run_jobs()
    [found] = rows(ReleaseSearch)
    assert found.language == "" and found.subject["accepted"] == ["de", "en"]


def test_a_search_without_a_language_takes_the_allowed_ones_never_the_language_of_the_account(
        operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net)
    with SessionLocal() as db:
        db.execute(update(Account).where(Account.id == world.person.id).values(language="fr"))
        db.commit()
    found = search(world)
    assert found["language"] == "" and found["languages"] == ["de", "en"]


def test_a_wish_or_a_search_in_a_language_the_operator_does_not_allow_is_refused(operator: Any,
                                                                                 net: FakeNetwork) -> None:
    world = audio_world(net)
    allow_languages("de", original=False)
    refused = world.client.post("/api/v1/procure/wishes", json={
        "items": [{"work_id": world.work_id}], "kinds": ["audio"], "language": "en"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "language_not_accepted"
    assert wish_rows() == []
    looked = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "en"})
    assert looked.status_code == 422 and looked.json()["detail"]["code"] == "language_not_accepted"
    assert world.client.post("/api/v1/procure/wishes", json={
        "items": [{"work_id": world.work_id}], "kinds": ["audio"], "language": "de"}).status_code == 201


def test_the_dialog_is_offered_only_the_allowed_languages_and_the_original_only_when_ticked(
        operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net)
    foreign_book("en")
    allow_languages("de", original=False)
    options = world.client.get("/api/v1/procure/book-options", params={"book": TOKEN}).json()
    assert options["languages"] == ["de"] and options["original_language"] == "en"
    assert "own_language" not in options
    refused = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": ["ebook"],
                                                                "language": "en"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "language_not_accepted"
    # "Originalsprache" ticked: the original language of this book is allowed as well (and only of this book).
    allow_languages("de", original=True)
    options = world.client.get("/api/v1/procure/book-options", params={"book": TOKEN}).json()
    assert options["languages"] == ["de", "en"]
    made = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": ["ebook"],
                                                             "language": "en"})
    assert made.status_code == 201, made.text
    assert world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": ["ebook"],
                                                             "language": "fr"}).status_code == 422


def test_a_wish_for_every_allowed_language_takes_the_original_when_ticked(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net)
    foreign_book("fr")
    allow_languages("de", original=True)
    made = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": ["ebook"]})
    assert made.status_code == 201, made.text
    run_jobs()
    [found] = rows(ReleaseSearch)
    assert found.subject["accepted"] == ["de", "fr"]


def test_a_wish_whose_language_is_taken_off_the_list_rests_and_says_so(operator: Any, client: TestClient,
                                                                       net: FakeNetwork) -> None:
    world = audio_world(net, Item("Rune Falkner-Die Laternenstrasse-MP3-ENGLISH-2021-EXAMPLE", size=400_000_000,
                                  categories=(3030,)))
    world.indexer.items = []
    wish(world, language="en")
    run_jobs()
    assert the_wish().state == "not_found"
    body = formats_body(client)
    body["languages"], body["original"] = ["de"], False
    assert client.put("/api/v1/procure/formats", json=body).status_code == 200
    row = the_wish()
    assert row.state == "idle" and row.detail == {"reason": "language_off", "language": "en"}
    asked = searches(world)
    # Nothing searches it by itself any more: not the planner, not "Jetzt suchen".
    with SessionLocal() as db:
        db.execute(update(ProcureWish).values(state="not_found", next_try_at=None, detail=None))
        db.commit()
    planner_tick()
    run_jobs()
    assert the_wish().state == "idle" and searches(world) == asked
    now = world.client.post(f"/api/v1/procure/wishes/{row.id}/search")
    assert now.status_code == 422 and now.json()["detail"]["code"] == "language_not_accepted"
    assert searches(world) == asked
    # The person turns it to the languages allowed: it searches again, in German.
    turned = world.client.post(f"/api/v1/procure/wishes/{row.id}/language", json={"language": None})
    assert turned.status_code == 200, turned.text
    assert turned.json()["language"] == "" and turned.json()["state"] == "searching"
    run_jobs()
    assert searches(world) > asked
    refused = world.client.post(f"/api/v1/procure/wishes/{row.id}/language", json={"language": "en"})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "language_not_accepted"


def test_turning_a_wish_to_a_language_another_wish_has_keeps_the_one(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net)
    world.indexer.items = []
    wish(world, language="en")
    wish(world, language=None)
    run_jobs()
    english = next(row for row in wish_rows() if row.language == "en")
    turned = world.client.post(f"/api/v1/procure/wishes/{english.id}/language", json={"language": None})
    assert turned.status_code == 200, turned.text
    assert [row.language for row in wish_rows()] == [""]


def test_only_a_person_who_may_ask_for_books_there_turns_a_wish(operator: Any, net: FakeNetwork) -> None:
    from .conftest import make_account, new_client

    world = audio_world(net)
    world.indexer.items = []
    wish(world, language="en")
    stranger = new_client(make_account("otto"))
    answer = stranger.post(f"/api/v1/procure/wishes/{the_wish().id}/language", json={"language": None})
    assert answer.status_code == 404
    assert the_wish().language == "en"


def test_the_planner_rests_the_wishes_whose_language_is_gone_also_without_a_save(operator: Any,
                                                                                 net: FakeNetwork) -> None:
    world = audio_world(net)
    world.indexer.items = []
    wish(world, language="en")
    run_jobs()
    allow_languages("de", original=False)
    with SessionLocal() as db:
        assert wishes.hold_unallowed(db) == 1
    assert the_wish().detail == {"reason": "language_off", "language": "en"}
    shown = world.client.get("/api/v1/procure/wishes").json()["wishes"][0]
    assert shown["state"] == "idle" and shown["detail"]["reason"] == "language_off"
