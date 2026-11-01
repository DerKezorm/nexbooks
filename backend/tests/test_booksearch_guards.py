"""Rules of the catalogue search that no other test held to: two books whose titles have the same core are told apart,
a comic of its own is a book, the page of a hit has a brake, and a cover is kept for a day, not longer. The answers are
the recorded shapes of ``fixtures/search`` (the books invented)."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.models import Account
from app.services import brakes
from app.services.booksearch import present
from app.services.booksearch.asked import read
from app.services.sources.model import Entry

from .search_world import SearchSources
from .test_booksearch import search
from .test_booksearch_cases import _cover_requests, stand_in

build = importlib.import_module("app.services.booksearch.build")

__all__ = ["stand_in"]


def test_two_books_of_one_author_whose_titles_have_the_same_core_show_their_whole_titles() -> None:
    """A shop writes both volumes as ``Glut`` with a subtitle: the hits say which is which, by the whole title a
    record shows."""
    entries = [Entry(source="audible", key=f"B9500000{n}", title="Glut", authors=["Ada Sorrel"], language="de",
                     form="audio", series="Glut", series_pos=float(n), signals={"subtitle": subtitle}, runtime_min=600)
               for n, subtitle in ((1, "Der Wüstenweg"), (2, "Der Bote"))]
    found = build.build(entries, read("Glut Sorrel"), languages=["de"], original=True)
    assert [(hit.title, hit.work.series_pos) for hit in found] == [("Glut: Der Wüstenweg", 1), ("Glut: Der Bote", 2)]


def test_a_comic_no_book_of_the_answer_is_called_like_is_a_book_of_its_own_and_one_of_a_book_is_set_apart() -> None:
    def labels(*more: Entry) -> list[str]:
        comic = Entry(source="openlibrary", key="/works/OL9501W", work_key="OL9501W", title="Kiri no Ken: Der Manga",
                      authors=["Ren Hayashi"], language="de", signals={"work_level": True}, weight=20)
        found = build.build([comic, *more], read("Kiri no Ken"), languages=["de"], original=True)
        return sorted(hit.label for hit in found)

    assert labels() == ["work"]
    novel = Entry(source="dnb", key="95011", title="Kiri no Ken", authors=["Hayashi, Ren"], language="de",
                  isbns=["9783000095011"])
    assert labels(novel) == ["comic", "work"]


def test_the_page_of_a_hit_has_a_brake(client: TestClient, operator: Account, stand_in: SearchSources,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    body = search(client, "Laternenstraße Sorrel")
    monkeypatch.setitem(brakes.LIMITS, "discover", 2)
    brakes.forget()
    codes = [client.post(f"/api/v1/search/catalog/{body['ticket']}/books",
                         json={"key": body["items"][0]["key"]}).status_code for _time in range(3)]
    assert codes == [200, 200, 429]


def test_a_cover_is_kept_for_a_day_and_fetched_anew_after(client: TestClient, operator: Account,
                                                          stand_in: SearchSources,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    moment = [1000.0]
    monkeypatch.setattr(clock, "monotonic", lambda: moment[0])
    body = search(client, "Laternenstraße Sorrel")
    cover = body["items"][0]["cover_url"]
    assert client.get(cover).status_code == 200 and _cover_requests(stand_in) == 1
    moment[0] += present.KEEP_COVER_SECONDS - 60
    assert client.get(cover).status_code == 200 and _cover_requests(stand_in) == 1
    moment[0] += 120
    assert client.get(cover).status_code == 200 and _cover_requests(stand_in) == 2
    assert present.KEEP_COVER_SECONDS == 24 * 3600

