"""The manual search through the interface: a search as a job, the progress per indexer, the categories
and queries per language, ``t=book`` only with the author, the releases with their judgement and rank, the rights."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, Indexer, Job, ReleaseSearch, SearchRelease
from app.services import jobs
from app.services.procure import search as search_service

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, join, make_library, run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import (
    AUTHOR,
    TORRENT_HOST,
    FeedIndexer,
    Item,
    add_indexer,
    allow_languages,
    by_title,
    codes,
    make_world,
    search,
)

GOOD = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"


def test_a_search_finds_judges_and_ranks_the_releases_of_the_book(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [
        Item(GOOD),
        Item("Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.GERMAN.EPUB-EXAMPLE"),
        Item("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", size=400_000_000, categories=(3130,)),
        Item("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.PDF-EXAMPLE"),
    ]
    found = search(world, language="de")
    assert found["state"] == "done", found
    rows = by_title(found)
    good = rows[GOOD]
    assert good["rank"] == 1 and good["reasons"] == [] and good["kind"] == "ebook" and good["format"] == "epub"
    assert good["language"] == "de" and good["language_sure"] is True and good["age_days"] == 3
    assert codes(rows["Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.GERMAN.EPUB-EXAMPLE"]) == ["other_volume"]
    assert codes(rows["Rune.Falkner.-.Die.Laternenstrasse.GERMAN.PDF-EXAMPLE"]) == ["format_not_wanted"]
    audio = rows["Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE"]
    assert audio["kind"] == "audio" and audio["rank"] == 1, audio
    assert found["author"] == AUTHOR and found["title"]
    assert found["indexers"] == [{"id": world.indexer_id, "name": "Shelf", "kind": "newznab", "state": "done",
                                  "found": 4, "error": "", "until": None, "limit": None}]
    cards = {card["kind"]: card for card in found["cards"]}
    assert cards["ebook"]["top"]["title"] == GOOD and cards["ebook"]["passing"] == 1 and cards["ebook"]["total"] == 3
    assert cards["ebook"]["reasons"] == [{"code": "other_volume", "count": 1}, {"code": "format_not_wanted", "count": 1}]
    # The best audiobook is an MP3 below the goal FLAC: nexbooks would look on.
    assert cards["audio"]["below_goal"] is True and cards["audio"]["goal"] == "flac"
    # Passing releases first, by their rank.
    assert [item["rank"] for item in found["releases"]][:2] == [1, 1]


def test_a_search_is_queued_with_every_indexer_waiting_and_answers_at_once(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    answer = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    assert answer.status_code == 202
    shown = answer.json()
    assert shown["state"] == "queued" and shown["releases"] == [] and shown["cards"] == []
    assert [entry["state"] for entry in shown["indexers"]] == ["waiting"]
    assert world.indexer.requests == []
    with SessionLocal() as db:
        assert db.scalar(select(Job.state).where(Job.kind == "psearch", Job.ref == shown["id"])) == "queued"
    assert search_service.LANE in jobs.LANES and jobs.LANES[search_service.LANE] == ("psearch",)


def test_a_second_click_gets_the_same_search_and_a_person_has_three_at_most(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    body = {"work_id": world.work_id, "language": "de"}
    first = world.client.post("/api/v1/procure/searches", json=body).json()
    again = world.client.post("/api/v1/procure/searches", json=body).json()
    assert first["id"] == again["id"]
    for language in ("en", "fr"):
        assert world.client.post("/api/v1/procure/searches",
                                 json={**body, "language": language}).status_code == 202
    fourth = world.client.post("/api/v1/procure/searches", json={**body, "language": "es"})
    assert fourth.status_code == 429 and fourth.json()["detail"]["code"] == "too_many_searches"


def test_the_categories_are_those_of_the_language_and_the_kinds(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    with SessionLocal() as db:
        db.execute(update(Indexer).values(categories=[7020, 7120, 7320, 3030, 3130, 3330],
                                          category_langs={"7120": "de", "7320": "fr", "3130": "de", "3330": "fr"}))
        db.commit()
    search(world, language="de")
    asked = {request.url.params.get("cat") for request in world.indexer.requests
             if request.url.params.get("t") == "search"}
    assert asked == {"7020,7120,3030,3130"}
    world.indexer.requests.clear()
    search(world, language="fr")
    asked = {request.url.params.get("cat") for request in world.indexer.requests
             if request.url.params.get("t") == "search"}
    assert asked == {"7020,7320,3030,3330"}


def test_the_free_text_is_author_and_core_title_in_the_spellings_of_an_umlaut(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net, title="Die Laternenstraße: Ein Nebelchor-Krimi")
    search(world, language="de")
    queries = [request.url.params.get("q") for request in world.indexer.requests
               if request.url.params.get("t") == "search"]
    assert queries == ["Rune Falkner Die Laternenstraße", "Rune Falkner Die Laternenstrasse"]
    assert all(request.url.params.get("limit") == "100" for request in world.indexer.requests
               if request.url.params.get("t") == "search")


def test_t_book_counts_only_with_the_author_and_spares_the_free_text_of_the_e_books(operator: Any,
                                                                                   net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.book_items = [Item("Holly.Stranger.-.Laternen.im.Wind.GERMAN.EPUB-EXAMPLE"),
                                Item(GOOD)]
    world.indexer.items = [Item("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                categories=(3130,))]
    found = search(world, language="de")
    book_requests = [request for request in world.indexer.requests if request.url.params.get("t") == "book"]
    assert len(book_requests) == 1
    assert book_requests[0].url.params.get("author") == AUTHOR
    assert book_requests[0].url.params.get("cat") == "7020,7120"
    titles = set(by_title(found))
    # The other author of t=book is not kept at all.
    assert titles == {GOOD, "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE"}
    free = {request.url.params.get("cat") for request in world.indexer.requests
            if request.url.params.get("t") == "search"}
    assert free == {"3030,3130"}


def test_t_book_without_an_answer_of_the_author_leaves_it_to_the_free_text(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.book_items = [Item("Holly.Stranger.-.Laternen.im.Wind.GERMAN.EPUB-EXAMPLE")]
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    assert set(by_title(found)) == {GOOD}
    free = {request.url.params.get("cat") for request in world.indexer.requests
            if request.url.params.get("t") == "search"}
    assert free == {"7020,7120,3030,3130"}


def test_without_author_in_the_caps_t_book_is_never_asked(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        assert row is not None
        row.caps = {**(row.caps or {}), "book_params": ["q", "isbn"]}
        db.commit()
    world.indexer.items = [Item(GOOD)]
    search(world, language="de")
    assert "book" not in world.indexer.functions()


def test_more_pages_are_read_up_to_two(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(f"Rune.Falkner.-.Die.Laternenstrasse.Kopie.{number}.GERMAN.EPUB-EXAMPLE")
                           for number in range(250)]
    found = search(world, language="de")
    offsets = [request.url.params.get("offset") for request in world.indexer.requests
               if request.url.params.get("t") == "search" and "ae" not in (request.url.params.get("q") or "x")]
    assert offsets[:2] == ["0", "100"] and "200" not in offsets
    assert len(found["releases"]) == 200


def test_a_resting_and_a_refusing_indexer_say_so_and_the_others_go_on(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    resting = net.add(FeedIndexer("resting.example.com", "r" * 32))
    refusing = net.add(FeedIndexer("refusing.example.com", "s" * 32))
    resting_id = add_indexer(resting, name="Resting", priority=2)
    refusing_id = add_indexer(refusing, name="Refusing", priority=3)
    refusing.api_key = "other"
    until = clock.now() + timedelta(hours=2)
    with SessionLocal() as db:
        db.execute(update(Indexer).where(Indexer.id == resting_id).values(paused_until=until,
                                                                          pause_cause="request_limit"))
        db.commit()
    found = search(world, language="de")
    states = {entry["name"]: entry for entry in found["indexers"]}
    assert states["Resting"]["state"] == "failed" and states["Resting"]["error"] == "indexer_limit_reached"
    assert states["Resting"]["until"] is not None
    assert states["Refusing"]["state"] == "failed" and states["Refusing"]["error"] == "indexer_key_rejected"
    assert states["Shelf"]["state"] == "done" and states["Shelf"]["found"] == 1
    assert resting.requests == []
    assert [entry["name"] for entry in found["indexers"]] == ["Resting", "Refusing", "Shelf"]
    assert refusing_id


def test_an_indexer_that_is_switched_off_is_not_asked(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(enabled=False))
        db.commit()
    answer = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "no_indexers"


def test_the_time_of_a_search_runs_out_and_the_indexer_says_so(operator: Any, net: FakeNetwork,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    monkeypatch.setattr(search_service, "SEARCH_SECONDS", 0.0)
    found = search(world, language="de")
    assert found["state"] == "done"
    assert found["indexers"][0]["state"] == "timeout" and found["indexers"][0]["error"] == "indexer_timeout"


def test_a_search_a_stopped_server_left_runs_again_from_the_start(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    answer = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    search_id = answer.json()["id"]
    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).values(state="running"))
        db.execute(update(Job).values(state="running"))
        db.add(SearchRelease(search_id=search_id, protocol="usenet", title="Left.Over-EXAMPLE", kind="ebook"))
        db.commit()
        jobs.recover(db)
    run_jobs()
    shown = world.client.get(f"/api/v1/procure/searches/{search_id}").json()
    assert shown["state"] == "done" and [item["title"] for item in shown["releases"]] == [GOOD]


def test_the_link_of_a_release_is_sealed_and_never_shown(operator: Any, net: FakeNetwork,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD, link=f"https://{world.indexer.host}/get/abc?apikey={world.indexer.api_key}")]
    with caplog.at_level(logging.DEBUG):
        found = search(world, language="de")
    assert world.indexer.api_key not in str(found) and "/get/" not in str(found)
    with SessionLocal() as db:
        sealed = db.scalar(select(SearchRelease.link_enc))
    assert sealed and world.indexer.api_key not in sealed and "/get/" not in sealed
    assert world.indexer.api_key not in caplog.text and "Laternen" not in caplog.text and "Falkner" not in caplog.text


# --- Who may --------------------------------------------------------------------------------------------------------


def test_only_who_may_acquire_searches_and_only_for_a_book_they_see(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    reader = make_account("lena")
    join(world.library_id, reader.id, "read")
    as_reader = new_client(reader).post("/api/v1/procure/searches", json={"work_id": world.work_id})
    assert as_reader.status_code == 403 and as_reader.json()["detail"]["code"] == "acquire_not_allowed"
    stranger = make_account("oskar")
    assert new_client(stranger).post("/api/v1/procure/searches", json={"work_id": world.work_id}).status_code == 403
    # Who may acquire in another library does not see this book.
    other_folder = books_folder("other")
    book(other_folder, "x.epub", title="Ein anderes Buch", authors=("Ida Leser",))
    other = make_library(other_folder, "Other")
    scan(other)
    join(other, stranger.id, "acquire")
    hidden = new_client(stranger).post("/api/v1/procure/searches", json={"work_id": world.work_id})
    assert hidden.status_code == 404
    # The operator has no right of his own.
    assert new_client(operator).post("/api/v1/procure/searches", json={"work_id": world.work_id}).status_code == 403


def test_a_search_is_the_persons_own(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    tom = make_account("tom")
    join(world.library_id, tom.id, "manage")
    other = new_client(tom)
    assert other.get(f"/api/v1/procure/searches/{found['id']}").status_code == 404
    release = found["releases"][0]["id"]
    loaded = other.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/load", json={})
    assert loaded.status_code == 404


@pytest.mark.parametrize("payload", [{}, {"work_id": 1, "book": "abc"}])
def test_a_search_names_one_book(operator: Any, net: FakeNetwork,
                                                      payload: dict[str, Any]) -> None:
    world = make_world(net)
    answer = world.client.post("/api/v1/procure/searches", json=payload)
    assert answer.status_code == 422


def test_a_book_of_the_sources_is_searched_by_its_token(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    with SessionLocal() as db:
        row = ForeignBook(token="tok" + "x" * 13, ref="hardcover:1", title="Die Brandung", authors=[AUTHOR],
                          language="de", series="Nebelchor", series_pos=3.0,
                          data={"forms": [{"kind": "audio", "variant": "unabridged", "language": "de",
                                           "minutes": 600}]})
        db.add(row)
        db.commit()
    world.indexer.items = [Item("Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.GERMAN.EPUB-EXAMPLE"),
                           Item("Rune Falkner-Die Brandung-MP3CD-DE-2021-EXAMPLE", size=1_000_000_000,
                                categories=(3130,))]
    answer = world.client.post("/api/v1/procure/searches", json={"book": "tok" + "x" * 13, "language": "de"})
    assert answer.status_code == 202, answer.text
    run_jobs()
    found = world.client.get(f"/api/v1/procure/searches/{answer.json()['id']}").json()
    assert found["book"] == "tok" + "x" * 13 and found["work_id"] is None
    rows = by_title(found)
    assert rows["Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.GERMAN.EPUB-EXAMPLE"]["rank"] == 1
    # The running time of the sources gives the bit rate: 1 GB over 10 hours is some 222 kbit/s.
    audio = rows["Rune Falkner-Die Brandung-MP3CD-DE-2021-EXAMPLE"]
    assert audio["format"] == "mp3_192" and audio["format_how"] == "estimate" and audio["bitrate"] == 222
    unknown = world.client.post("/api/v1/procure/searches", json={"book": "nothere", "language": "de"})
    assert unknown.status_code == 404


def test_old_searches_go_after_a_day(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    search(world, language="de")
    search(world, language="fr")
    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).where(ReleaseSearch.language == "de").values(
            created_at=clock.now() - timedelta(hours=30)))
        db.commit()
    search(world, language="en")
    with SessionLocal() as db:
        assert sorted(db.scalars(select(ReleaseSearch.language))) == ["en", "fr"]


def test_a_torrent_indexer_gives_seeders_and_peers(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    tracker.items = [Item(GOOD, seeders=12, peers=15, info_hash="a" * 40)]
    found = search(world, language="de")
    torrent = next(item for item in found["releases"] if item["protocol"] == "torrent")
    assert (torrent["seeders"], torrent["peers"], torrent["indexer"]) == (12, 15, "Tracker")
