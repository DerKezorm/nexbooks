"""The search of the catalogues as a library has it (``services/booksearch``): one hit for each book, named the way it is
called in the languages the operator fetches in, the book asked for first, with its editions behind a press.

The sources answer from ``fixtures/search/answers.json`` (the shapes recorded from the services, the books invented;
``search_world.py``). Each search says which hit comes first, how many hits there are for the book asked for (one),
and that nothing was joined that is two books: two books in one hit is worse than two hits for one book.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, ForeignBook
from app.services import booksearch, brakes, settings_service
from app.services.booksearch import ask
from app.services.procure import formats
from app.services.sources import http

from .conftest import make_account, new_client
from .fake_sources import host_of
from .library_helpers import book, books_folder, make_library, scan
from .match_helpers import World, member
from .search_world import SearchSources


@pytest.fixture
def sources(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[SearchSources]:
    fake = SearchSources()
    fake.wait = world.advance
    monkeypatch.setattr(http, "transport", fake)
    world.only("openlibrary", "audible", "dnb")
    world.settings(source_markets=["de", "us"])
    brakes.forget()
    ask.forget()
    yield fake
    ask.forget()


def search(client: TestClient, q: str) -> dict[str, Any]:
    """The answer of a search once every source has answered (asked again by the ticket while it is pending)."""
    answer = client.post("/api/v1/search/catalog", json={"q": q})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    for _time in range(20):
        if not body["pending"]:
            break
        again = client.get(f"/api/v1/search/catalog/{body['ticket']}")
        assert again.status_code == 200, again.text
        body = again.json()
    assert not body["pending"]
    return body


def titles(body: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(item["title"], item["label"], item["group"]) for item in body["items"]]


def of(body: dict[str, Any], title: str, author: str = "") -> list[dict[str, Any]]:
    """The books (not what is labelled) of that title by that author."""
    return [item for item in body["items"] if item["title"] == title and item["label"] == "work"
            and (not author or author in item["authors"])]


def editions(item: dict[str, Any]) -> set[tuple[str, str]]:
    return {(edition["title"], edition["language"]) for edition in item["editions"]}


# ---- One hit for a book, the book asked for first ------------------------------------------------------------------


def test_a_book_and_its_translation_recordings_and_editions_are_one_hit_named_in_german(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "Laternenstraße Sorrel")
    first = body["items"][0]
    assert (first["title"], first["language"], first["authors"]) == ("Die Laternenstraße", "de", ["Ada Sorrel"])
    assert first["group"] == "match" and first["label"] == "work"
    assert first["ebook"] and first["audio"]
    assert first["series"] == "Die Laternen-Saga" and first["series_pos"] == 1 and first["year"] == 2012
    assert set(first["languages"]) >= {"de", "en"} and set(first["sources"]) >= {"openlibrary", "dnb", "audible"}
    # The English work, the German records, the German recording and the English one are this one hit.
    assert len(of(body, "Die Laternenstraße", "Ada Sorrel")) == 1
    assert {("Die Laternenstraße", "de"), ("The Lantern Road", "en")} <= editions(first)
    # The graphic novel of somebody else that names her second is not joined to it.
    assert not any(edition["title"] == "The Lantern Road" and edition["source"] == "openlibrary"
                   for edition in first["editions"] if "Finn Hale" in str(edition))
    hale = [item for item in body["items"] if item["authors"][:1] == ["Finn Hale"]]
    assert len(hale) == 1 and hale[0] is not first


@pytest.mark.parametrize("q", ["lantern saga 2", "Das Salztor", "The Salt Gate"])
def test_the_series_and_its_volume_the_german_title_and_the_original_all_find_volume_two_first(
        client: TestClient, operator: Account, sources: SearchSources, q: str) -> None:
    body = search(client, q)
    first = body["items"][0]
    assert (first["title"], first["series_pos"], first["label"], first["group"]) == ("Das Salztor", 2, "work", "match")
    assert len(of(body, "Das Salztor", "Ada Sorrel")) == 1
    # The other volumes of the series follow, in their order (the shop wrote ", Book 2" behind the title, the reader's
    # name in front of it: still this book).
    series = [item["title"] for item in body["items"] if item["group"] == "series"]
    if q == "lantern saga 2":
        assert series[:2] == ["Die Laternenstraße", "Die letzte Glut"]


def test_a_record_that_names_the_original_title_of_the_series_does_not_join_two_volumes(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    """The German record of volume 2 names the uniform title of volume 1 (``The lantern road``): it stays with volume
    2, and volume 1 stays a book of its own."""
    body = search(client, "lantern saga 2")
    salt = of(body, "Das Salztor", "Ada Sorrel")[0]
    lantern = of(body, "Die Laternenstraße", "Ada Sorrel")[0]
    assert salt["series_pos"] == 2 and lantern["series_pos"] == 1
    assert "Die Laternenstraße" not in {edition["title"] for edition in salt["editions"]}
    assert "Das Salztor" not in {edition["title"] for edition in lantern["editions"]}


def test_a_box_a_play_a_study_guide_a_calendar_and_parts_are_hits_of_their_own_after_the_books_and_named(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "lantern saga 2")
    labels = {(item["title"], item["label"]) for item in body["items"] if item["group"] == "related"}
    assert ("Das Salztor", "drama") in labels
    assert any(label == "box" for _title, label in labels)
    groups = [item["group"] for item in body["items"]]
    # Every book comes before everything that was set apart.
    assert groups.index("related") > max(index for index, group in enumerate(groups) if group in ("match", "series"))
    # The calendar and the study guide are no book of hers.
    drama = [item for item in body["items"] if item["label"] == "drama"]
    assert all(not item["ebook"] for item in drama)
    guide = search(client, "Salztor Lektürehilfe")
    # Asked for by name, a study guide comes first.
    assert guide["items"][0]["label"] == "guide" and guide["items"][0]["authors"] == ["Lena Brück"]
    glut = search(client, "Die letzte Glut")
    assert glut["items"][0]["title"] == "Die letzte Glut" and glut["items"][0]["label"] == "work"
    parts = [item for item in glut["items"] if item["label"] == "part"]
    assert parts and all(item["group"] == "related" for item in parts)


def test_a_box_comes_first_when_a_box_is_asked_for(client: TestClient, operator: Account,
                                                   sources: SearchSources) -> None:
    body = search(client, "Lantern Saga Box")
    assert body["items"][0]["label"] == "box"


def test_an_edition_of_the_catalogue_that_carries_only_the_name_of_the_series_joins_nothing(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "Die letzte Glut")
    glut = of(body, "Die letzte Glut", "Ada Sorrel")[0]
    assert glut["series_pos"] == 3
    assert "Die Laternen-Saga" not in {edition["title"] for edition in glut["editions"]}


def test_a_classic_in_many_languages_is_one_hit(client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "Der stille Garten")
    first = body["items"][0]
    assert (first["title"], first["authors"]) == ("Der stille Garten", ["Ilse Marbach"])
    assert {"de", "en"} <= set(first["languages"])
    assert len([item for item in body["items"] if "Ilse Marbach" in item["authors"] and item["label"] == "work"]) == 1
    # Its translations into languages nobody fetches in are no hits of their own.
    assert not any(item["title"] in ("Le jardin silencieux", "El jardín silencioso") for item in body["items"])


def test_a_crime_novel_with_its_recording_is_one_hit_and_its_play_another(client: TestClient, operator: Account,
                                                                        sources: SearchSources) -> None:
    body = search(client, "Das Moorhaus")
    first = body["items"][0]
    assert (first["title"], first["label"], first["ebook"], first["audio"]) == ("Das Moorhaus", "work", True, True)
    plays = [item for item in body["items"] if item["label"] == "drama"]
    assert len(plays) == 1 and plays[0]["group"] == "related"
    assert len([item for item in body["items"] if item["label"] == "work" and "Jonas Brehm" in item["authors"]]) == 1


def test_one_title_by_two_authors_is_two_hits_the_known_one_first(client: TestClient, operator: Account,
                                                                 sources: SearchSources) -> None:
    body = search(client, "Die Brücke")
    assert [(item["title"], item["authors"]) for item in body["items"][:2]] == [
        ("Die Brücke", ["Klara Wendt"]), ("Die Brücke", ["Paul Ostrander"])]


def test_a_book_for_children(client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "Pim und der Leuchtturm")
    assert len(body["items"]) == 1 and body["items"][0]["authors"] == ["Hanne Voss"]


@pytest.mark.parametrize("q", ["J. K. Ash Kupferglocke", "The Copper Bell"])
def test_a_name_with_initials_and_its_written_out_form_are_one_author(client: TestClient, operator: Account,
                                                                      sources: SearchSources, q: str) -> None:
    body = search(client, q)
    first = body["items"][0]
    assert first["title"] == "Die Kupferglocke" and first["ebook"] and first["audio"]
    assert len(body["items"]) == 1


def test_and_and_the_ampersand_are_one_title(client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "Salt and Ashes")
    assert len(body["items"]) == 1
    assert body["items"][0]["language"] == "en" and body["items"][0]["audio"] and body["items"][0]["ebook"]


def test_a_book_in_none_of_the_languages_fetched_in_stands_last_and_says_where_it_exists(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {formats.KEY: {**formats.config(db), "original": False}})
    body = search(client, "La Maison du Phare")
    [item] = body["items"]
    assert (item["allowed"], item["language"], item["group"], item["languages"]) == (False, "", "elsewhere", ["fr"])
    with SessionLocal() as db:
        settings_service.save(db, {formats.KEY: {**formats.config(db), "original": True}})
    ask.forget()
    body = search(client, "La Maison du Phare")
    # With the original language fetched in, it is a book like any other.
    assert body["items"][0]["allowed"] is True and body["items"][0]["language"] == "fr"


def test_the_title_follows_the_languages_of_the_operator_not_the_one_of_the_person(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {formats.KEY: {**formats.config(db), "languages": ["en", "de"]}})
    body = search(client, "Laternenstraße Sorrel")
    assert (body["items"][0]["title"], body["items"][0]["language"]) == ("The Lantern Road", "en")


# ---- What goes with a hit -------------------------------------------------------------------------------------------


def test_the_page_of_a_hit_knows_every_title_and_form_so_that_fetching_it_looks_in_every_language(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = search(client, "Laternenstraße Sorrel")
    first = body["items"][0]
    # (The page is made when the person opens the hit.)
    token = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]}).json()["token"]
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == token))
        assert row is not None
        assert row.title == "Die Laternenstraße"
        known = {(item["text"], item["language"]) for item in row.data["titles"]}
        assert {("Die Laternenstraße", "de"), ("The Lantern Road", "en")} <= known
        assert {form["kind"] for form in row.data["forms"]} == {"ebook", "audio"}
    page = client.get(f"/api/v1/discover/books/{token}")
    assert page.status_code == 200


def test_a_book_on_the_shelf_says_so_only_to_who_may_see_it(client: TestClient, operator: Account,
                                                           sources: SearchSources) -> None:
    folder = books_folder("search-shelf")
    book(folder, "laterne.epub", title="Die Laternenstraße", authors=("Ada Sorrel",), language="de")
    library = make_library(folder, "Shelf")
    scan(library)
    member(library, "tester", "read")
    first = search(client, "Laternenstraße Sorrel")["items"][0]
    assert first["owned_work_id"] is not None
    rita = make_account("rita")
    with new_client(rita) as other:
        theirs = search(other, "Laternenstraße Sorrel")["items"][0]
    assert theirs["title"] == "Die Laternenstraße" and theirs["owned_work_id"] is None


def test_the_editions_are_the_single_records_never_a_work_of_a_catalogue(client: TestClient, operator: Account,
                                                                        sources: SearchSources) -> None:
    first = search(client, "Laternenstraße Sorrel")["items"][0]
    sources_of = {edition["source"] for edition in first["editions"]}
    assert "dnb" in sources_of and "audible" in sources_of
    recordings = [edition for edition in first["editions"] if edition["form"] == "audio"]
    assert recordings and all(edition["minutes"] for edition in recordings)
    # The German ones first, as German is the first language fetched in.
    assert first["editions"][0]["language"] == "de"


# ---- Fast, and the caller's ----------------------------------------------------------------------------------------


def test_the_first_answer_does_not_wait_for_a_slow_source_and_the_ticket_gives_the_rest(
        client: TestClient, operator: Account, sources: SearchSources, monkeypatch: pytest.MonkeyPatch) -> None:
    """The library answers late: the first answer has what the others gave (the moment it is given is the one when
    every other source has answered, so that the test does not depend on the clock), the ticket gives the rest."""
    import threading

    gate = threading.Event()
    real = sources.handle_request

    def handle(request):  # type: ignore[no-untyped-def]
        if host_of(request) == "services.dnb.de":
            gate.wait(10)
        return real(request)

    def first_wait(run: ask.Run, seconds: float) -> None:
        with run.changed:
            run.changed.wait_for(lambda: all(state != "waiting" for name, state in run.states.items()
                                             if name != "dnb"), timeout=10)

    waiting = ask._wait
    monkeypatch.setattr(sources, "handle_request", handle)
    monkeypatch.setattr(ask, "_wait", first_wait)
    first = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    assert first["pending"] is True and first["ticket"]
    assert first["items"] and first["items"][0]["title"] == "Das Moorhaus"
    assert "dnb" not in first["items"][0]["sources"]
    gate.set()
    monkeypatch.setattr(ask, "_wait", waiting)
    again = client.get(f"/api/v1/search/catalog/{first['ticket']}").json()
    for _time in range(20):
        if not again["pending"]:
            break
        again = client.get(f"/api/v1/search/catalog/{first['ticket']}").json()
    assert again["pending"] is False and "dnb" in again["items"][0]["sources"]
    # The hit keeps its name while the sources answer.
    assert again["items"][0]["key"] == first["items"][0]["key"]


def test_a_ticket_is_only_the_callers(client: TestClient, operator: Account, sources: SearchSources) -> None:
    body = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    ticket = body["ticket"]
    assert client.get(f"/api/v1/search/catalog/{ticket}").status_code == 200
    with new_client(make_account("rita")) as other:
        assert other.get(f"/api/v1/search/catalog/{ticket}").status_code == 404
    assert client.get("/api/v1/search/catalog/unknownticket").status_code == 404
    assert client.get("/api/v1/search/catalog/x").status_code == 422
    with TestClient(client.app, base_url="http://testserver") as nobody:
        assert nobody.get(f"/api/v1/search/catalog/{ticket}").status_code in (401, 403)


def test_the_sources_are_asked_side_by_side_and_only_the_typed_words_go_out(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    search(client, "Laternenstraße Sorrel")
    urls = [str(request.url) for request in sources.log]
    hosts = {host_of(request) for request in sources.log}
    assert {"openlibrary.org", "services.dnb.de", "api.audible.de", "api.audible.com"} <= hosts
    from urllib.parse import unquote_plus

    assert all("laternenstra" in unquote_plus(url).casefold() or "sorrel" in unquote_plus(url).casefold()
               or "authors.json" in url for url in urls if "cover" not in url), urls


def test_nothing_is_asked_when_no_source_may_be(client: TestClient, operator: Account,
                                                sources: SearchSources) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"sources_enabled": False})
    body = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    assert body == {"available": False, "failed": False, "pending": False, "ticket": None, "items": [], "people": [],
                    "resting": []}
    assert sources.log == []


def test_the_run_is_forgotten_after_a_while(client: TestClient, operator: Account, sources: SearchSources,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    body = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    monkeypatch.setattr(ask, "KEEP_SECONDS", -1)
    assert client.get(f"/api/v1/search/catalog/{body['ticket']}").status_code == 404
    assert booksearch.wait_for(operator.id, body["ticket"], 0) is None


def test_a_volume_is_asked_for_in_the_title_at_the_library_and_open_library_gets_the_edition_in_each_language(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    from .fake_sources import params_of

    search(client, "lantern saga 2")
    queries = [params_of(request)["query"][0] for request in sources.log if host_of(request) == "services.dnb.de"]
    assert queries == ['woe="lantern" and woe="saga" and tit=2']
    languages = sorted(params_of(request).get("lang", [""])[0] for request in sources.log
                       if host_of(request) == "openlibrary.org" and request.url.path == "/search.json")
    assert languages == ["eng", "ger"]


def test_asking_again_by_the_ticket_is_slowed_down_like_a_search(client: TestClient, operator: Account,
                                                                 sources: SearchSources,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    body = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    monkeypatch.setitem(brakes.LIMITS, "search", 3)
    brakes.forget()
    codes = [client.get(f"/api/v1/search/catalog/{body['ticket']}").status_code for _time in range(4)]
    assert codes == [200, 200, 200, 429]
