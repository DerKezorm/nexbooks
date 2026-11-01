"""A source that is not asked, or does not answer, is named in the answer of a search: the person is told that the hits
are incomplete, and never that there are none while a source rested.

The sources answer from ``fixtures/search/answers.json`` (the shapes recorded from the services, the books invented).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.models import Account
from app.services import brakes
from app.services.booksearch import ask, present
from app.services.sources import http, throttle

from .fake_sources import host_of
from .match_helpers import World
from .search_world import SearchSources
from .test_booksearch import search


@pytest.fixture
def sources(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[SearchSources]:
    fake = SearchSources()
    fake.wait = world.advance
    monkeypatch.setattr(http, "transport", fake)
    world.only("openlibrary", "audible", "dnb")
    world.settings(source_markets=["de"])
    brakes.forget()
    ask.forget()
    present.forget_covers()
    yield fake
    ask.forget()
    present.forget_covers()


def until_of(body: dict, source: str) -> datetime | None:  # type: ignore[type-arg]
    found = [item for item in body["resting"] if item["source"] == source]
    assert found, body["resting"]
    return datetime.fromisoformat(found[0]["until"]) if found[0]["until"] else None


def test_a_source_that_rests_is_named_with_the_moment_it_may_be_asked_again(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    throttle.fail("dnb", "rate_limited", wait=90)
    body = search(client, "Das Moorhaus")
    assert [item["source"] for item in body["resting"]] == ["dnb"]
    until = until_of(body, "dnb")
    assert until is not None and 85 <= (until - clock.now()).total_seconds() <= 90
    # The others answered: the hits are there, and nothing was asked of the library that rests.
    assert body["items"] and body["items"][0]["title"] == "Das Moorhaus"
    assert "dnb" not in body["items"][0]["sources"]
    assert not any(host_of(request) == "services.dnb.de" for request in sources.log)
    assert body["failed"] is False


def test_a_search_while_every_source_rests_says_so_and_is_no_answer_of_nothing(
        client: TestClient, operator: Account, sources: SearchSources) -> None:
    for source in ("openlibrary", "audible", "dnb"):
        throttle.fail(source, "server")
    body = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    assert body["available"] is True and body["pending"] is False and body["items"] == []
    assert sorted(item["source"] for item in body["resting"]) == ["audible", "dnb", "openlibrary"]
    assert all(item["until"] for item in body["resting"])
    assert sources.log == []


def test_a_source_that_did_not_answer_the_search_is_named_without_a_rest_after_one_timeout(
        client: TestClient, operator: Account, sources: SearchSources, world: World,
        monkeypatch: pytest.MonkeyPatch) -> None:
    real = sources.handle_request

    def handle(request: httpx.Request) -> httpx.Response:
        if host_of(request) == "openlibrary.org":
            raise httpx.ReadTimeout("too slow", request=request)
        return real(request)

    monkeypatch.setattr(sources, "handle_request", handle)
    body = search(client, "Das Moorhaus")
    assert [item["source"] for item in body["resting"]] == ["openlibrary"]
    assert until_of(body, "openlibrary") is None, "one timeout makes no source rest"
    assert body["items"] and "openlibrary" not in body["items"][0]["sources"]
    # A second search soon after runs out of time again: now the source rests, and the next search says until when.
    world.advance(throttle.SOFT_SAME + 5)
    second = search(client, "Die Brücke")
    assert until_of(second, "openlibrary") is not None
    third = search(client, "Das Salztor")
    assert until_of(third, "openlibrary") is not None
    asked = [request for request in sources.log if host_of(request) == "openlibrary.org"]
    search(client, "Pim und der Leuchtturm")
    assert [request for request in sources.log if host_of(request) == "openlibrary.org"] == asked


def test_a_search_with_every_source_answering_names_none(client: TestClient, operator: Account,
                                                         sources: SearchSources) -> None:
    assert search(client, "Das Moorhaus")["resting"] == []
