"""Cases of the catalogue search found against the real sources (Open Library, the German National Library, Audible;
recorded answers), rebuilt with invented books in the shapes the services answered in:
who is a study guide and who a book, what is one book and what two, what a title is cut to, which language a title is
in, what a hit costs the sources, and the limits of a search. All names and titles are invented."""

from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, CatalogPerson, ForeignBook
from app.services import booksearch, brakes, settings_service
from app.services.booksearch import ask, present, titles
from app.services.booksearch.asked import read
from app.services.procure import formats
from app.services.sources import http
from app.services.sources.model import Entry

from .conftest import make_account, new_client
from .fake_sources import host_of
from .fixtures.search.build import ol_doc, product, record
from .match_helpers import World
from .search_world import SearchSources
from .test_booksearch import search

build = importlib.import_module("app.services.booksearch.build")


class Own(SearchSources):
    """The stand-in of the sources with the answers of one test."""

    def __init__(self, searches: list[dict[str, Any]], sets: dict[str, Any]) -> None:
        super().__init__()
        self.searches, self.sets = searches, sets


@pytest.fixture
def own(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    def install(searches: list[dict[str, Any]], sets: dict[str, Any]) -> Own:
        fake = Own(searches, sets)
        fake.wait = world.advance
        monkeypatch.setattr(http, "transport", fake)
        return fake

    world.only("openlibrary", "audible", "dnb")
    world.settings(source_markets=["de"])
    brakes.forget()
    ask.forget()
    present.forget_covers()
    yield install
    ask.forget()
    present.forget_covers()


@pytest.fixture
def stand_in(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[SearchSources]:
    """The sources with the recorded answers of ``fixtures/search`` (as in ``test_booksearch``)."""
    fake = SearchSources()
    fake.wait = world.advance
    monkeypatch.setattr(http, "transport", fake)
    world.only("openlibrary", "audible", "dnb")
    world.settings(source_markets=["de", "us"])
    brakes.forget()
    ask.forget()
    present.forget_covers()
    yield fake
    ask.forget()
    present.forget_covers()


def shown(body: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(item["title"], item["authors"][:1], item["label"], item["group"], item["series_pos"])
            for item in body["items"]]


def isbn(start: str) -> str:
    """An ISBN-13 of the first twelve digits, with its check digit (a library gives only valid ones)."""
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(start[:12]))
    return start[:12] + str((10 - total % 10) % 10)


def isbns_of(item: dict[str, Any]) -> set[str]:
    return {edition["isbn"] for edition in item["editions"] if edition["isbn"]}


# ---- A study guide, or a book --------------------------------------------------------------------------------------


def test_a_biography_named_like_its_subject_is_a_book_not_a_study_guide(client: TestClient, operator: Account,
                                                                        own: Any) -> None:
    ol = [ol_doc("OL8001W", "Nina Vale", ["Ruth Hagen"], 2015, ["ger"], editions=30,
                 edition=("OL8001M", "Nina Vale", "ger"), subjects=("Biography",)),
          ol_doc("OL8002W", "Zeit der Wellen", ["Nina Vale"], 2002, ["ger"], editions=12,
                 edition=("OL8002M", "Zeit der Wellen", "ger"))]
    own([{"q": "Nina Vale", "ol": {"de": "OL", "en": "OL"}, "dnb": "", "audible": {}}], {"OL": ol})
    body = search(client, "Nina Vale")
    first = body["items"][0]
    assert (first["title"], first["authors"][0], first["label"], first["group"]) == (
        "Nina Vale", "Ruth Hagen", "work", "match"), shown(body)


def test_a_series_credited_as_an_author_does_not_turn_its_books_into_study_guides(client: TestClient,
                                                                                  operator: Account, own: Any) -> None:
    au = [product("B800100001", "Die drei Schatten und der Kristall", ["Karl Kohl"], 300),
          product("B800100002", "Die drei Schatten und das Geisterhaus", ["Karl Kohl"], 310),
          product("B800100003", "Falsche Spur", ["Die drei Schatten", "Ben Nord"], 290)]
    own([{"q": "Die drei Schatten Kristall", "ol": {}, "dnb": "", "audible": {"de": "AU"}}], {"AU": au})
    body = search(client, "Die drei Schatten Kristall")
    items = {item["title"]: item for item in body["items"]}
    assert items["Die drei Schatten und der Kristall"]["label"] == "work"
    assert items["Die drei Schatten und das Geisterhaus"]["label"] == "work"


def test_a_classic_whose_title_is_a_persons_name_comes_first(client: TestClient, operator: Account,
                                                             own: Any) -> None:
    ol = [ol_doc("OL8101W", "Marta Heller", ["Leo Brandt"], 1877, ["ger"], editions=300,
                 edition=("OL8101M", "Marta Heller", "ger")),
          ol_doc("OL8102W", "Die Tage danach", ["Marta Heller"], 2024, ["ger"], editions=2)]
    dnb = [record("81001", "Die Tage danach", "Heller, Marta", 2024)]
    own([{"q": "Marta Heller", "ol": {"de": "OL", "en": "OL"}, "dnb": "DNB", "audible": {}}],
        {"OL": ol, "DNB": dnb})
    body = search(client, "Marta Heller")
    first = body["items"][0]
    assert (first["title"], first["authors"][0], first["label"]) == ("Marta Heller", "Leo Brandt", "work"), shown(body)


def test_a_one_word_name_names_no_person_whose_books_a_title_could_be_about() -> None:
    """``Marbach`` (a brand, a series credited as the author) is no person: a book that carries the word and the title
    of a book by it is a book of its own. With a person's name it is a study guide."""
    def labels(author: str) -> dict[str, str]:
        entries = [
            Entry(source="audible", key="a1", title="Die Glocke", authors=[author], language="de", form="audio"),
            Entry(source="audible", key="a2", title="Mein Marbach und die Glocke", authors=["Ada Sorrel"],
                  language="de", form="audio")]
        found = build.build(entries, read("Marbach Glocke"), languages=["de"], original=True)
        return {hit.title: hit.label for hit in found}

    assert labels("Marbach") == {"Die Glocke": "work", "Mein Marbach und die Glocke": "work"}
    assert labels("Mira Marbach")["Mein Marbach und die Glocke"] == "guide"


# ---- One book, never two in a hit ----------------------------------------------------------------------------------


VARN = [
    # The library's records of a book in three parts: the whole, and each part with its number and its name.
    record("82000", "Die Krone von Varn", "Falk, Oskar", 2001, isbn=isbn("978300008200"), pages=1200),
    record("82001", "Die Krone von Varn", "Falk, Oskar", 2002, part_number="Teil 1.", part_name="Der Eisenweg",
           isbn=isbn("978300008201")),
    record("82002", "Die Krone von Varn", "Falk, Oskar", 2002, part_number="Teil 2.", part_name="Der Silberpass",
           isbn=isbn("978300008202")),
    record("82003", "Die Krone von Varn", "Falk, Oskar", 2002, part_number="Teil 3.", part_name="Die Rückkehr",
           isbn=isbn("978300008203")),
]


def test_the_parts_of_a_book_in_three_are_three_books_and_the_one_asked_for_comes_first(
        client: TestClient, operator: Account, own: Any) -> None:
    au = [product("B800200002", "Der Silberpass", ["Oskar Falk"], 900, series="Die Krone von Varn", sequence="2"),
          product("B800200001", "Der Eisenweg", ["Oskar Falk"], 880, series="Die Krone von Varn", sequence="1")]
    # Open Library's work carries the ISBNs of every part and of the whole: it cannot say which one it is.
    ol = [ol_doc("OL8201W", "The Crown of Varn", ["Oskar Falk"], 1999, ["eng", "ger"], editions=400,
                 edition=("OL8201M", "Die Krone von Varn", "ger"),
                 isbns=(isbn("978300008200"), isbn("978300008201"), isbn("978300008202"), isbn("978300008203")))]
    own([{"q": "Die Krone von Varn Der Silberpass", "ol": {"de": "OL", "en": "OL"}, "dnb": "DNB",
          "audible": {"de": "AU"}}], {"OL": ol, "DNB": VARN, "AU": au})
    body = search(client, "Die Krone von Varn Der Silberpass")
    first = body["items"][0]
    assert (first["title"], first["series_pos"], first["label"]) == ("Der Silberpass", 2, "work"), shown(body)
    # The library's record of the second part is this book.
    assert isbns_of(first) == {isbn("978300008202")}
    # The whole is named like the series: which volume a wish for it would fetch, nobody knows.
    whole = next(item for item in body["items"] if item["title"] == "Die Krone von Varn" and item["label"] == "work")
    assert (whole["procurable"], whole["why_not"]) == (False, "volume_unknown")
    # No hit carries the editions of two parts, or of a part and the whole.
    volumes = [{isbn("978300008200")}, {isbn("978300008201")}, {isbn("978300008202")}, {isbn("978300008203")}]
    for item in body["items"]:
        assert sum(1 for numbers in volumes if numbers & isbns_of(item)) <= 1, (item["title"], isbns_of(item))
    # The page of the hit names the series and the volume, so that what is fetched is that volume.
    made = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]})
    assert made.status_code == 200, made.text
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == made.json()["token"]))
        assert row is not None and (row.series, row.series_pos) == ("Die Krone von Varn", 2)


def test_a_comic_and_a_film_of_a_novel_are_not_editions_of_it(client: TestClient, operator: Account,
                                                              own: Any) -> None:
    dnb = [record("83001", "Nachtfalter: Morgenlicht", "Rowan, Stella", 2006, isbn=isbn("978300008300"),
                  series="Nachtfalter", position="1", pages=500),
           record("83002", "Nachtfalter: Morgenlicht - Der Comic, Band 1", "Rowan, Stella", 2010,
                  isbn=isbn("978300008301")),
           record("83003", "Nachtfalter: Morgenlicht - Der Comic, Band 2", "Rowan, Stella", 2011,
                  isbn=isbn("978300008302")),
           record("83004", "Nachtfalter - Morgenlicht (DVD)", "Rowan, Stella", 2009, isbn=isbn("978300008303"))]
    au = [product("B800300001", "Morgenlicht", ["Stella Rowan"], 700, series="Nachtfalter", sequence="1")]
    own([{"q": "Nachtfalter Morgenlicht", "ol": {}, "dnb": "DNB", "audible": {"de": "AU"}}], {"DNB": dnb, "AU": au})
    body = search(client, "Nachtfalter Morgenlicht")
    first = body["items"][0]
    assert (first["title"], first["label"]) == ("Morgenlicht", "work"), shown(body)
    assert isbns_of(first) == {isbn("978300008300")}
    labels = {item["label"] for item in body["items"] if item is not first}
    assert "comic" in labels and "companion" in labels, shown(body)


def test_an_edition_printed_in_three_volumes_is_no_book_of_three(client: TestClient, operator: Account,
                                                                 own: Any) -> None:
    dnb = [record("84000", "Der Graf von Varenne", "Dumont, Alexis", 2015, isbn=isbn("978300008400"), pages=1400),
           *(record(f"8400{n}", "Der Graf von Varenne", "Dumont, Alexis", 2019, part_number=f"Bd. {n}",
                    part_name="Mit Illustrationen von Jean Example", isbn=isbn(f"97830000840{n}0"))
             for n in (1, 2, 3))]
    own([{"q": "Der Graf von Varenne", "ol": {}, "dnb": "DNB", "audible": {}}], {"DNB": dnb})
    body = search(client, "Der Graf von Varenne")
    first = body["items"][0]
    assert (first["title"], first["label"]) == ("Der Graf von Varenne", "work"), shown(body)
    assert isbns_of(first) == {isbn("978300008400")}
    parts = [item for item in body["items"] if item["label"] == "part"]
    assert len(parts) == 3 and all(len(isbns_of(item)) == 1 for item in parts), shown(body)
    assert all(not item["procurable"] for item in parts)
    # (What the library writes where the name of a part stands names the edition: no title.)
    assert not any(item["title"].startswith("Mit Illustrationen") for item in body["items"]), shown(body)


def test_one_title_at_several_places_of_a_series_with_their_own_numbers_is_several_books(
        client: TestClient, operator: Account, own: Any) -> None:
    """A comic series whose volumes all carry its name (the manga named after a classic): each place is a book."""
    dnb = [record(f"8500{n}", "Kaito", "Aoki, Rin", 2018 + n, isbn=isbn(f"97830000850{n}0"), series="Kaito",
                  position=str(n)) for n in (1, 2, 3)]
    own([{"q": "Kaito", "ol": {}, "dnb": "DNB", "audible": {}}], {"DNB": dnb})
    body = search(client, "Kaito")
    places = [item["series_pos"] for item in body["items"] if item["label"] == "work"]
    assert sorted(places) == [1, 2, 3], shown(body)
    assert all(len(isbns_of(item)) == 1 for item in body["items"])


def test_a_book_called_like_its_series_without_a_place_cannot_be_acquired_from_its_hit(
        client: TestClient, operator: Account, own: Any) -> None:
    """``Sturmzeit`` beside ``Der erste Sturm`` (``Sturmzeit`` 1): the whole, or any of its volumes. A book of the
    series with a title of its own is that book, with a place or without."""
    au = [product("B800400001", "Der erste Sturm", ["Mila Brandt"], 600, series="Sturmzeit", sequence="1"),
          product("B800400002", "Der zweite Sturm", ["Mila Brandt"], 610, series="Sturmzeit", sequence="2"),
          product("B800400003", "Der letzte Sturm", ["Mila Brandt"], 620, series="Sturmzeit"),
          product("B800400004", "Sturmzeit", ["Mila Brandt"], 1800)]
    own([{"q": "Sturmzeit Sturm", "ol": {}, "dnb": "", "audible": {"de": "AU"}}], {"AU": au})
    body = search(client, "Sturmzeit Sturm")
    items = {item["title"]: item for item in body["items"]}
    assert (items["Sturmzeit"]["procurable"], items["Sturmzeit"]["why_not"]) == (False, "volume_unknown"), shown(body)
    for title in ("Der zweite Sturm", "Der letzte Sturm"):
        assert (items[title]["procurable"], items[title]["why_not"]) == (True, ""), title


# ---- What a title is cut to ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("source", "title", "full", "wanted"), [
    # (A word in brackets inside a word: not the start of a subtitle.)
    ("audible", "Bis(s) zum Morgenlicht", "", "Biss zum Morgenlicht"),
    # (What stands before the mark is no title by itself: the whole title.)
    ("dnb", "Detektiv Kenta", "Detektiv Kenta - 5. Film", "Detektiv Kenta"),
    ("dnb", "Das Geld im 21. Jahrhundert", "Das Geld im 21. Jahrhundert", "Das Geld im 21. Jahrhundert"),
    ("openlibrary", "Die 3 ??? - Toteninsel", "", "Die 3 ??? Toteninsel"),
    ("dnb", "Die Tierschule 01", "Die Tierschule 01", "Die Tierschule 01"),
    # (The series and its number in front, its name behind a dash.)
    ("dnb", "Lantern Saga", "Lantern Saga, Band 2: Lantern Saga - Das Salztor", "Das Salztor"),
])
def test_a_title_is_never_cut_to_what_is_no_title(source: str, title: str, full: str, wanted: str) -> None:
    record_ = Entry(source=source, key="1", title=title, authors=["Ada Sorrel"], language="de")
    if full:
        record_.signals = {"full_title": full}
    titles.tidy(record_, ["Ada Sorrel"])
    assert record_.title == wanted


@pytest.mark.parametrize(("cut", "rest", "keeps"), [
    ("Bis", "(s) zum Morgenlicht", False), ("Film", "", False), ("Jahrhundert", "", False), ("01", "", False),
    ("Die 3", "??? Toteninsel", False), ("Es", "Roman", True), ("Das Salztor", "", True), ("Rache", "", True),
])
def test_what_is_left_of_a_title_is_a_title_only_when_it_is_one(cut: str, rest: str, keeps: bool) -> None:
    assert titles.keeps_title(cut, rest) is keeps


# ---- One writer, written in several ways ---------------------------------------------------------------------------


def test_one_writer_written_in_two_scripts_is_one_hit_and_the_classic_comes_first(
        client: TestClient, operator: Account, own: Any) -> None:
    ol = [ol_doc("OL8601W", "Krieg im Winter", ["Lev Tarasov"], 1869, ["eng", "ger", "rus"], editions=900,
                 edition=("OL8601M", "Krieg im Winter", "ger")),
          # Books of the same title by others that Open Library gives first.
          ol_doc("OL8602W", "Krieg im Winter", ["Paul Ostrander"], 2019, ["ger"], editions=2,
                 edition=("OL8602M", "Krieg im Winter", "ger")),
          ol_doc("OL8603W", "Krieg im Winter", ["Jana Feld"], 2021, ["ger"], editions=1,
                 edition=("OL8603M", "Krieg im Winter", "ger"))]
    ol = [ol[1], ol[2], ol[0]]
    dnb = [record("86001", "Krieg im Winter", "Tarasow, Lew", 2010, translated="rus", isbn=isbn("978300008600")),
           record("86002", "Krieg im Winter", "Tarassow, Lew N.", 2016, translated="rus", isbn=isbn("978300008601"))]
    au = [product("B800600001", "Krieg im Winter", ["Leo Tarassow"], 3600)]
    own([{"q": "Krieg im Winter", "ol": {"de": "OL", "en": "OL"}, "dnb": "DNB", "audible": {"de": "AU"}}],
        {"OL": ol, "DNB": dnb, "AU": au})
    body = search(client, "Krieg im Winter")
    first = body["items"][0]
    assert first["title"] == "Krieg im Winter" and "taras" in first["authors"][0].casefold(), shown(body)
    writer = [item for item in body["items"] if "taras" in item["authors"][0].casefold() and item["label"] == "work"]
    assert len(writer) == 1, shown(body)
    assert first["ebook"] and first["audio"]


def test_two_people_of_alike_names_who_wrote_different_books_stay_two() -> None:
    entries = [Entry(source="dnb", key="1", title="Die Brücke", authors=["Taraskow, Lew"], language="de"),
               Entry(source="dnb", key="2", title="Das Meer", authors=["Tarasov, Lev"], language="de")]
    from app.services.booksearch.authors import by_author

    assert len(by_author(entries)) == 2


# ---- The language of a title ---------------------------------------------------------------------------------------


def test_an_edition_whose_title_is_german_is_not_taken_for_english_nor_the_work_for_german(
        client: TestClient, operator: Account, own: Any) -> None:
    """Open Library files the German edition of an English work under English now and then (``Das Geheimnis von
    Penmoor`` in the answer for English), and names no language for the work's own title (``The Penmoor Mystery``, of
    a work with editions in both): the German title is German, the English one English."""
    ol_en = [ol_doc("OL8701W", "The Penmoor Mystery", ["Aurelia Penhallow"], 1931, ["eng", "ger"], editions=200,
                    edition=("OL8701M", "Das Geheimnis von Penmoor", "eng"))]
    ol_de = [ol_doc("OL8701W", "The Penmoor Mystery", ["Aurelia Penhallow"], 1931, ["eng", "ger"], editions=200)]
    dnb = [record(f"8700{n}", title, "Penhallow, Aurelia", 1990 + n) for n, title in enumerate(
        ("Mord im Pfarrhaus", "Das Haus am Moor", "Tod auf der Fähre", "Die Uhr von Penmoor"), start=1)]
    own([{"q": "Das Geheimnis von Penmoor", "ol": {"de": "OLDE", "en": "OLEN"}, "dnb": "DNB", "audible": {}}],
        {"OLDE": ol_de, "OLEN": ol_en, "DNB": dnb})
    body = search(client, "Das Geheimnis von Penmoor")
    first = body["items"][0]
    assert (first["title"], first["language"]) == ("Das Geheimnis von Penmoor", "de"), [
        (item["title"], item["language"]) for item in body["items"]]
    assert not any(item["title"] == "The Penmoor Mystery" and item["language"] == "de" for item in body["items"])
    assert {(edition["title"], edition["language"]) for edition in first["editions"]} == {
        ("Das Geheimnis von Penmoor", "de")}


def test_a_book_in_none_of_the_languages_fetched_in_is_shown_in_the_one_its_author_writes_in(
        client: TestClient, operator: Account, own: Any) -> None:
    """``Don Ramiro de la Mancha``, recorded in Spanish, with a work at the catalogue that names French (its French
    editions). The library names the author shorter (``Ortega, Miguel``) and says its German translation was made from
    Spanish: one author, who writes in Spanish, so the title of the recordings is the Spanish one, not French."""
    with SessionLocal() as db:
        settings_service.save(db, {formats.KEY: {**formats.config(db), "languages": ["de", "en"], "original": True}})
    ol = [ol_doc("OL8801W", "Don Ramiro de la Mancha", ["Miguel Ortega Lantera"], 1605, ["fre"], editions=3)]
    au = [product(f"B80080000{n}", "Don Ramiro de la Mancha", ["Miguel Ortega Lantera"], 2000 + n,
                  language="spanish", narrators=(f"Reader {n}",)) for n in (1, 2, 3)]
    dnb = [record("88001", "Der Ritter Ramiro", "Ortega, Miguel", 2004, translated="spa",
                  isbn=isbn("978300008800")),
           record("88002", "Don Ramiro de la Mancha", "Ortega, Miguel", 2010, language="spa",
                  isbn=isbn("978840008801"))]
    own([{"q": "Don Ramiro", "ol": {"de": "OL", "en": "OL"}, "dnb": "DNB", "audible": {"de": "AU"}}],
        {"OL": ol, "AU": au, "DNB": dnb})
    body = search(client, "Don Ramiro")
    ramiro = [item for item in body["items"] if item["title"] == "Don Ramiro de la Mancha"]
    assert [item["language"] for item in ramiro] == ["es"], [
        (item["title"], item["authors"][:1], item["language"], item["languages"]) for item in body["items"]]


# ---- What can be acquired from a hit -------------------------------------------------------------------------------


def test_only_a_book_can_be_acquired_from_its_hit_and_its_page_says_why_not(client: TestClient, operator: Account,
                                                                            stand_in: SearchSources) -> None:
    body = search(client, "lantern saga 2")
    for item in body["items"]:
        if item["label"] == "work":
            assert (item["procurable"], item["why_not"]) == (True, ""), item["title"]
        else:
            assert (item["procurable"], item["why_not"]) == (False, "not_a_book"), (item["title"], item["label"])
    drama = next(item for item in body["items"] if item["label"] == "drama")
    made = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": drama["key"]})
    assert made.status_code == 200
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == made.json()["token"]))
        assert row is not None
        assert (row.data["search_label"], row.data["search_why_not"]) == ("drama", "not_a_book")


# ---- What a search costs the sources -------------------------------------------------------------------------------


def _pages() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(ForeignBook)) or 0)


def _cover_requests(fake: SearchSources) -> int:
    return sum(1 for request in fake.log if "cover" in host_of(request) or host_of(request) == "archive.org")


def test_a_search_makes_no_page_and_fetches_no_cover_until_a_hit_is_shown_or_opened(
        client: TestClient, operator: Account, stand_in: SearchSources) -> None:
    body = search(client, "Laternenstraße Sorrel")
    first = body["items"][0]
    assert _pages() == 0 and _cover_requests(stand_in) == 0
    assert first["token"] is None
    assert first["cover_url"] == f"/api/v1/search/catalog/{body['ticket']}/covers/{first['key']}"
    # The page shows the hit: its cover is fetched, once.
    for _time in range(2):
        cover = client.get(first["cover_url"])
        assert cover.status_code == 200 and cover.headers["content-type"] == "image/webp"
    assert _cover_requests(stand_in) == 1 and _pages() == 0
    # The person opens it: the page of the book is made, with the cover the page showed.
    made = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]})
    assert made.status_code == 200
    token = made.json()["token"]
    assert _pages() == 1 and client.get(f"/api/v1/discover/books/{token}").status_code == 200
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == token))
        assert row is not None and row.cover
    # Opened again, and asked again: the same page.
    again = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]})
    assert again.json()["token"] == token and _pages() == 1
    assert client.get(f"/api/v1/search/catalog/{body['ticket']}").json()["items"][0]["token"] == token


def test_the_cover_and_the_page_of_a_hit_are_only_the_callers(client: TestClient, operator: Account,
                                                              stand_in: SearchSources) -> None:
    body = search(client, "Laternenstraße Sorrel")
    first = body["items"][0]
    with new_client(make_account("rita")) as other:
        assert other.get(first["cover_url"]).status_code == 404
        answer = other.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]})
        assert answer.status_code == 404 and answer.json()["detail"]["code"] == "search_gone"
        assert other.delete(f"/api/v1/search/catalog/{body['ticket']}").status_code == 404
    assert client.post(f"/api/v1/search/catalog/{body['ticket']}/books",
                       json={"key": "0" * 20}).status_code == 404
    assert client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": "nope"}).status_code == 422
    assert client.get(f"/api/v1/search/catalog/{body['ticket']}/covers/{'0' * 20}").status_code == 404
    assert _pages() == 0


def test_the_covers_of_a_search_have_a_brake(client: TestClient, operator: Account, stand_in: SearchSources,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    body = search(client, "Laternenstraße Sorrel")
    monkeypatch.setitem(brakes.LIMITS, "catalog_cover", 2)
    brakes.forget()
    codes = [client.get(body["items"][0]["cover_url"]).status_code for _time in range(3)]
    assert codes == [200, 200, 429]


def _gate_on(stand_in: SearchSources, monkeypatch: pytest.MonkeyPatch, host: str) -> threading.Event:
    gate = threading.Event()
    real = stand_in.handle_request

    def handle(request):  # type: ignore[no-untyped-def]
        if host_of(request) == host:
            gate.wait(10)
        return real(request)

    monkeypatch.setattr(stand_in, "handle_request", handle)
    return gate


def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first answer does not wait for the source held back (the moment it is given is when the others answered)."""
    def first_wait(run: ask.Run, seconds: float) -> None:
        with run.changed:
            run.changed.wait_for(lambda: all(state != "waiting" for name, state in run.states.items()
                                             if name != "dnb"), timeout=10)

    monkeypatch.setattr(ask, "_wait", first_wait)


def test_a_search_the_page_gave_up_on_asks_nothing_more(client: TestClient, operator: Account,
                                                        stand_in: SearchSources,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate_on(stand_in, monkeypatch, "services.dnb.de")
    _quick(monkeypatch)
    first = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    assert first["pending"] is True
    assert client.delete(f"/api/v1/search/catalog/{first['ticket']}").status_code == 204
    gate.set()
    again = client.get(f"/api/v1/search/catalog/{first['ticket']}").json()
    assert again["pending"] is False and again["failed"] is False
    assert "dnb" not in again["items"][0]["sources"]
    run = booksearch.wait_for(operator.id, first["ticket"], 0)
    assert run is not None and run.states["dnb"] == "called_off"


def test_a_new_search_of_the_person_calls_off_the_last_one_and_a_question_waiting_for_its_turn_sends_nothing(
        client: TestClient, operator: Account, stand_in: SearchSources,
        monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate_on(stand_in, monkeypatch, "services.dnb.de")
    _quick(monkeypatch)
    first = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    run = booksearch.wait_for(operator.id, first["ticket"], 0)
    assert run is not None and run.states["dnb"] == "waiting"
    other_first = client.post("/api/v1/search/catalog", json={"q": "Laternenstraße Sorrel"}).json()
    # A new search of the person: the one before asks nothing more, what it waited for is called off.
    assert run.called_off and run.states["dnb"] == "called_off"
    run = booksearch.wait_for(operator.id, other_first["ticket"], 0)
    assert run is not None and not run.called_off
    # A question of the run that waits for its turn now: called off before anything is sent.
    sent = len(stand_in.log)
    with pytest.raises(http.SourceError) as stopped:
        http.calls_off.check = lambda: run.called_off
        try:
            ask.call_off(run)
            with SessionLocal() as db:
                http.request(db, "openlibrary", "https://openlibrary.org/search.json?q=x", interactive=True)
        finally:
            http.calls_off.check = None
    assert stopped.value.code == "called_off" and len(stand_in.log) == sent
    # The person's next search calls the one before off.
    third = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    newer = booksearch.wait_for(operator.id, third["ticket"], 0)
    assert newer is not None and not newer.called_off
    gate.set()
    # Another person's search calls nothing of this person's off.
    with new_client(make_account("rita")) as other:
        second = client.post("/api/v1/search/catalog", json={"q": "Laternenstraße Sorrel"}).json()
        theirs = other.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    mine = booksearch.wait_for(operator.id, second["ticket"], 0)
    assert mine is not None and not mine.called_off and theirs["ticket"]


def test_a_person_keeps_a_few_runs_and_everybody_a_few_hundred(client: TestClient, operator: Account,
                                                               stand_in: SearchSources,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    assert ask.MAX_PER_PERSON <= 10 and ask.MAX_RUNS <= 1000
    monkeypatch.setattr(ask, "MAX_RUNS", 4)
    monkeypatch.setattr(ask, "MAX_PER_PERSON", 2)
    brakes.forget()
    tickets = [client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()["ticket"]
               for _time in range(3)]
    kept = [booksearch.wait_for(operator.id, ticket, 0) is not None for ticket in tickets]
    assert kept == [False, True, True]
    for name in ("rita", "sven"):
        with new_client(make_account(name)) as other:
            for _time in range(2):
                other.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"})
    # Six runs of three people: the oldest go.
    assert len(ask._runs) == 4


def test_the_hits_are_built_once_for_each_state_of_the_answers(client: TestClient, operator: Account,
                                                               stand_in: SearchSources,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    body = search(client, "Laternenstraße Sorrel")
    calls: list[int] = []
    real = booksearch.build

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(booksearch, "build", counted)
    for _time in range(3):
        assert client.get(f"/api/v1/search/catalog/{body['ticket']}").status_code == 200
    client.get(body["items"][0]["cover_url"])
    assert len(calls) == 0


# ---- The limits of a search ----------------------------------------------------------------------------------------


def test_the_first_answer_comes_after_a_moment_even_when_a_source_hangs(client: TestClient, operator: Account,
                                                                        stand_in: SearchSources,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate_on(stand_in, monkeypatch, "services.dnb.de")
    started = time.monotonic()
    try:
        first = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    finally:
        took = time.monotonic() - started
        gate.set()
    assert first["pending"] is True and first["items"]
    assert took < ask.FIRST_WAIT + 2 and ask.FIRST_WAIT <= 3
    # (The search ends before the test does: nothing it still asks reaches the next test.)
    for _time in range(20):
        if not client.get(f"/api/v1/search/catalog/{first['ticket']}").json()["pending"]:
            break


def test_the_library_of_german_books_is_asked_only_when_german_is_fetched_in(client: TestClient, operator: Account,
                                                                             stand_in: SearchSources) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {formats.KEY: {**formats.config(db), "languages": ["en"]}})
    search(client, "Laternenstraße Sorrel")
    assert "services.dnb.de" not in {host_of(request) for request in stand_in.log}


def test_a_search_asks_three_markets_of_the_shop_at_most(client: TestClient, operator: Account,
                                                         stand_in: SearchSources, world: World) -> None:
    world.settings(source_markets=["de", "us", "fr", "it", "es"])
    with SessionLocal() as db:
        settings_service.save(db, {formats.KEY: {**formats.config(db), "languages": ["de", "en", "fr", "it", "es"]}})
    search(client, "Laternenstraße Sorrel")
    markets = {host_of(request) for request in stand_in.log if host_of(request).startswith("api.audible.")}
    assert len(markets) == ask.MAX_MARKETS == 3


def test_a_source_switched_off_while_it_is_asked_is_no_failure(client: TestClient, operator: Account,
                                                               stand_in: SearchSources,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def off(*args: Any, **kwargs: Any) -> Any:
        raise http.SourceOff("openlibrary")

    for name in ("text_entries",):
        monkeypatch.setattr(ask.openlibrary, name, off)
        monkeypatch.setattr(ask.dnb, name, off)
    monkeypatch.setattr(ask.audible, "search", off)
    body = client.post("/api/v1/search/catalog", json={"q": "Das Moorhaus"}).json()
    assert body["failed"] is False and body["items"] == []
    run = booksearch.wait_for(operator.id, body["ticket"], 0)
    assert run is not None and {state for name, state in run.states.items() if name != "people"} == {"off"}


FIRST_NAMES = ("Ada", "Ben", "Cora", "Dirk", "Eva", "Finn", "Gina", "Hugo", "Jonas", "Lotte")
LAST_NAMES = ("Brandt", "Keller", "Lorenz", "Mohr", "Nagel", "Pohl")


def _many(count: int, *, author: str = "", title: str = "Das Salztor") -> list[Entry]:
    """``count`` records of one title: by as many people (``author`` empty), or all by ``author``."""
    names = [f"{first} {last}" for last in LAST_NAMES for first in FIRST_NAMES]
    return [Entry(source="dnb", key=str(index), title=title, authors=[author or names[index]], language="de")
            for index in range(count)]


def test_an_answer_has_at_most_so_many_hits_and_a_hit_so_many_editions() -> None:
    found = build.build(_many(60), read("Das Salztor"), languages=["de"], original=True)
    assert len(found) == build.MAX_HITS == 24
    one = build.build(_many(60, author="Ada Sorrel"), read("Das Salztor"), languages=["de"], original=True)
    assert len(one) == 1 and len(one[0].editions) == build.MAX_EDITIONS == 40


def test_typing_names_authors_but_makes_no_catalogue_of_them(client: TestClient, operator: Account,
                                                             own: Any) -> None:
    ol = [ol_doc("OL8901W", "Der Nebelhof", ["Greta Lindqvist"], 2011, ["ger"], editions=40,
                 edition=("OL8901M", "Der Nebelhof", "ger"))]
    own([{"q": "Greta Lindqvist", "ol": {"de": "OL", "en": "OL"}, "dnb": "", "audible": {}}], {"OL": ol})
    body = search(client, "Greta Lindqvist")
    assert [person["name"] for person in body["people"]] == ["Greta Lindqvist"]
    assert body["people"][0]["token"] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CatalogPerson)) == 0


# ---- What a record is ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "pile"), [
    ('Mündlichkeit in literarischen Texten am Beispiel von Ada Sorrels "Das Salztor"', "guide"),
    ("Das Salztor. Eine Hausarbeit", "guide"),
    ('Kindheit in "Das Salztor" and "Die Laternenstraße" - A comparison', "guide"),
    ("Das Salztor", "work"),
    ("Nachtfalter: Morgenlicht - Der Comic, Band 1", "comic"),
])
def test_a_paper_about_a_book_is_set_apart_like_a_study_guide(title: str, pile: str) -> None:
    assert build.pile_of(Entry(source="dnb", key="1", title=title, authors=["Hanna Example"], language="de")) == pile


def test_every_hit_of_an_answer_has_a_name_of_its_own() -> None:
    """Two hits that would be named alike (one work in two piles of one author, say) are told apart, so that a page that
    keeps what it shows of a hit by its name never shows it at another."""
    entries = [Entry(source="dnb", key=str(index), title="Das Salztor", authors=["Ada Sorrel"], language="de",
                     series="Die Laternen-Saga", series_pos=2.0) for index in range(3)]
    found = build.build(entries, read("Das Salztor"), languages=["de"], original=True)
    twin = build.Hit(work=found[0].work, label=found[0].label, authors=found[0].authors, title=found[0].title,
                     language="de", allowed=True, languages=["de"], place=found[0].place)
    build.unique_keys([found[0], twin])
    assert found[0].key != twin.key and twin.key.startswith(found[0].base_key)


def test_a_library_record_whose_subtitle_is_the_title_of_the_recording_is_the_same_book() -> None:
    """The records of one search as the sources gave them (``fixtures/search/one_book_two_names.json``, names, titles and
    numbers invented): the library names the book by its one-word original name and puts the German one under it
    (``HUMANUM``, ``Eine kurze Geschichte der Zeiten``); the catalogue and the shop know it as ``Humanum`` in English and
    as ``Eine kurze Geschichte der Zeiten`` in German. One book, named in German."""
    found = build.build(_recorded("one_book_two_names.json"), read("Humanum Eine kurze Geschichte der Zeiten"),
                        languages=["de"], original=False)
    first = found[0]
    assert first.title == "Eine kurze Geschichte der Zeiten"
    assert {entry.source for entry in first.work.entries} == {"dnb", "audible", "openlibrary"}
    assert not any(hit.title.casefold() == "humanum" for hit in found)


def _recorded(name: str) -> list[Entry]:
    """The records one search got from the sources, recorded, with names, titles and numbers invented."""
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "search" / name
    return [Entry.from_json(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def test_a_work_of_the_catalogue_whose_isbns_belong_to_several_volumes_joins_none_of_them() -> None:
    """``fixtures/search/three_parts.json``: a book in three parts, recorded by the library, the shop and the catalogue,
    whose work at the catalogue lists the ISBNs of every part. Asked for the first part, it comes first, and the second
    and the third are hits of their own: the catalogue's numbers join no part to another."""
    found = build.build(_recorded("three_parts.json"), read("Krone von Varn Eisenweg"), languages=["de"],
                        original=False)
    first = found[0]
    assert first.title == "Der Eisenweg"
    assert not {"Der Silberpass", "Die Rückkehr"} & {entry.title for entry in first.work.entries}
    assert {"Der Silberpass", "Die Rückkehr"} <= {hit.title for hit in found if hit.label == "work"}


def test_two_spellings_of_one_person_with_one_authority_number_are_one_author() -> None:
    from app.services.booksearch.authors import by_author

    def entry(key: str, name: str, title: str, gnd: str) -> Entry:
        found = Entry(source="dnb", key=key, title=title, authors=[name], language="de")
        found.signals = {"gnd": gnd}
        return found

    same = [entry("1", "Wolkow, Fjodor", "Die Brücke", "118000001"),
            entry("2", "Volkov, Theodor", "Das Meer", "118000001")]
    assert len(by_author(same)) == 1
    apart = [entry("1", "Wolkow, Fjodor", "Die Brücke", "118000001"),
             entry("2", "Volkov, Theodor", "Das Meer", "118000002")]
    assert len(by_author(apart)) == 2
