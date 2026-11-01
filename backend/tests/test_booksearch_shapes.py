"""Shapes of the answers of the sources that made the catalogue search show the wrong thing (Open Library, the German
National Library, Audible; recorded from the services, the books, people and numbers invented): which spelling of a
title is shown, what a library writes where the title of a comic would be, a spin-off series, a play made of a comic
series, a publisher's line, a name with a slip of the keys, and a name written twice."""

from __future__ import annotations

import importlib
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, ForeignBook
from app.services.booksearch import titles, words
from app.services.booksearch.asked import read
from app.services.sources.model import Entry

from .fake_sources import host_of, params_of
from .fixtures.search.build import ol_doc, product, record
from .test_booksearch import search
from .test_booksearch_cases import isbn, own, shown

build = importlib.import_module("app.services.booksearch.build")

__all__ = ["own"]


# ---- The spelling of a title -----------------------------------------------------------------------------------


GELOBNIS_OL = [ol_doc("OL9001W", "Gelöbnis", ["Fritz Dornbach"], 1958, ["ger"], editions=40,
                      edition=("OL9001M", "Das Gelöbnis: Abgesang auf einen Kriminalroman", "ger"))]
GELOBNIS_DNB = [record(f"9001{n}", "Das Gelöbnis", "Dornbach, Fritz", 1990 + n, subtitle="Roman",
                       isbn=isbn(f"97830000900{n}")) for n in range(3)]
GELOBNIS_AU = [product("B900100001", "Das Gelöbnis", ["Fritz Dornbach"], 420),
               product("B900100002", "Das Gelöbnis", ["Fritz Dornbach"], 410, kind="abridged")]


def test_a_title_is_written_the_way_most_records_write_it_not_the_shortest_or_the_longest_way(
        client: TestClient, operator: Account, own: Any) -> None:
    """Open Library's work says ``Gelöbnis``, its edition ``Das Gelöbnis: Abgesang …``; the library and the shop say
    ``Das Gelöbnis`` five times: that is the title, with its article."""
    own([{"q": "Das Gelöbnis Dornbach", "ol": {"de": "OL", "en": "OL"}, "dnb": "DNB", "audible": {"de": "AU"}}],
        {"OL": GELOBNIS_OL, "DNB": GELOBNIS_DNB, "AU": GELOBNIS_AU})
    first = search(client, "Das Gelöbnis Dornbach")["items"][0]
    assert (first["title"], first["authors"][0]) == ("Das Gelöbnis", "Fritz Dornbach")


def test_a_title_keeps_the_capitals_a_library_writes_not_the_ones_of_many_hands() -> None:
    """``Der Stille Hafen`` at Open Library twice, ``Der stille Hafen`` at the library and the shop: the library's
    capitals."""
    entries = [
        Entry(source="openlibrary", key="/works/OL9002W", work_key="OL9002W", title="Der Stille Hafen",
              authors=["Ilka Brenner"], language="de", signals={"work_level": True}, weight=30),
        Entry(source="openlibrary", key="/books/OL9002M", work_key="OL9002W", title="Der Stille Hafen",
              authors=["Ilka Brenner"], language="de"),
        Entry(source="dnb", key="90021", title="Der stille Hafen", authors=["Brenner, Ilka"], language="de"),
        Entry(source="audible", key="B900200001", title="Der stille Hafen", authors=["Ilka Brenner"], language="de",
              form="audio"),
    ]
    found = build.build(entries, read("Der stille Hafen"), languages=["de"], original=True)
    assert found[0].title == "Der stille Hafen"


# ---- A comic series, its spin-offs, and the plays made of it -----------------------------------------------------


KIRI_DNB = [
    record("91001", "Kiri no Ken 1", "Hayashi, Ren", 2017, series="Kiri no Ken", position="1",
           subtitle="Atemberaubende Ninja-Action im Kampf gegen grauenhafte Schatten", isbn=isbn("978300009100")),
    record("91002", "Kiri no Ken - Before the Storm 1", "Hayashi, Ren", 2019, series="Kiri no Ken - Before the Storm",
           position="1", isbn=isbn("978300009101")),
    record("91003", "Kiri no Ken 2", "Hayashi, Ren", 2017, series="Kiri no Ken", position="2",
           subtitle="Die beliebte Ninja-Saga jetzt endlich weiter!", isbn=isbn("978300009102")),
    record("91004", "Kiri no Ken - Shinden 1", "Hayashi, Ren", 2024, series="Kiri no Ken - Shinden", position="1",
           subtitle="Die Schriften des Nebels | Nach dem weltweiten Erfolg der Novel jetzt auch als Manga!",
           isbn=isbn("978300009103")),
    # A play made of the series, its first episode: a recording at place 1 with a title the comic does not have.
    record("91005", "Spiegelbild", "Hayashi, Ren", 2018, series="Kiri no Ken", position="1", audio=True),
]
KIRI_AU = [product("B910000001", "Spiegelbild", ["Ren Hayashi"], 72, series="Kiri no Ken", sequence="1",
                   narrators=("Ensemble",))]


def _kiri(own: Any) -> None:
    searches = [{"q": text, "ol": {}, "dnb": "DNB", "audible": {"de": "AU"}}
                for text in ("Kiri no Ken 1", "Kiri no Ken Folge 1")]
    own(searches, {"DNB": KIRI_DNB, "AU": KIRI_AU})


def test_a_comic_asked_for_by_its_series_and_volume_is_that_volume_and_its_title_is_never_advertising(
        client: TestClient, operator: Account, own: Any) -> None:
    _kiri(own)
    body = search(client, "Kiri no Ken 1")
    first = body["items"][0]
    assert (first["title"], first["series"], first["series_pos"], first["group"]) == (
        "Kiri no Ken 1", "Kiri no Ken", 1, "match"), shown(body)
    # What the library writes where the subtitle is to sell the book is no title of any hit.
    assert all(words.title_like(item["title"]) for item in body["items"]), shown(body)
    # A spin-off series is named whole: its first volume is not the first volume of the series.
    spin = next(item for item in body["items"] if item["title"].startswith("Before the Storm"))
    assert spin["series"] == "Kiri no Ken - Before the Storm"
    # A title the library writes beside advertising is taken without it.
    assert any(item["title"] == "Die Schriften des Nebels" for item in body["items"]), shown(body)


def test_a_play_made_of_a_comic_series_is_labelled_a_play_and_comes_first_when_an_episode_is_asked_for(
        client: TestClient, operator: Account, own: Any) -> None:
    _kiri(own)
    body = search(client, "Kiri no Ken 1")
    play = next(item for item in body["items"] if item["title"] == "Spiegelbild")
    assert (play["label"], play["procurable"], play["why_not"]) == ("drama", False, "not_a_book"), shown(body)
    assert body["items"][0]["title"] == "Kiri no Ken 1"
    episode = search(client, "Kiri no Ken Folge 1")["items"][0]
    assert (episode["title"], episode["label"]) == ("Spiegelbild", "drama")


def test_a_reading_of_a_book_of_a_series_stays_a_book(client: TestClient, operator: Account, own: Any) -> None:
    """A shop's recording that runs for hours, at the place of a printed book of the same title, is that book."""
    dnb = [record("91101", "Die Fähre", "Lund, Merit", 2015, series="Nordlicht", position="1",
                  isbn=isbn("978300009110")),
           record("91102", "Der Leuchtturm", "Lund, Merit", 2016, series="Nordlicht", position="2",
                  isbn=isbn("978300009111"))]
    au = [product("B911000001", "Die Fähre", ["Merit Lund"], 610, series="Nordlicht", sequence="1"),
          product("B911000002", "Sturmflut", ["Merit Lund"], 600, series="Nordlicht", sequence="3")]
    own([{"q": "Nordlicht Lund", "ol": {}, "dnb": "DNB", "audible": {"de": "AU"}}], {"DNB": dnb, "AU": au})
    labels = {item["title"]: item["label"] for item in search(client, "Nordlicht Lund")["items"]}
    assert labels == {"Die Fähre": "work", "Der Leuchtturm": "work", "Sturmflut": "work"}, labels


# ---- A publisher's line is no series -----------------------------------------------------------------------------


def test_a_publishers_line_and_a_series_one_source_names_for_one_book_are_no_series_of_the_hit_or_its_wish(
        client: TestClient, operator: Account, own: Any) -> None:
    dnb = [record("93001", "Der Fährmann", "Lindqvist, Theo", 1901, series="Die Originale", position="39",
                  isbn=isbn("978300009300")),
           record("93002", "Der Fährmann", "Lindqvist, Theo", 2005, series="Schattenkreis", position="7",
                  isbn=isbn("978300009301"))]
    # (A shop that names the publisher's line too: two sources, still no series.)
    au = [product("B930000001", "Der Fährmann", ["Theo Lindqvist"], 600, series="Die Originale", sequence="39")]
    own([{"q": "Der Fährmann", "ol": {}, "dnb": "DNB", "audible": {"de": "AU"}}], {"DNB": dnb, "AU": au})
    body = search(client, "Der Fährmann")
    first = body["items"][0]
    assert (first["title"], first["series"], first["series_pos"]) == ("Der Fährmann", "", None), shown(body)
    made = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]})
    assert made.status_code == 200, made.text
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == made.json()["token"]))
        assert row is not None and (row.series, row.series_pos) == ("", None)


# ---- Names -------------------------------------------------------------------------------------------------------


def test_a_name_written_twice_by_a_source_is_written_once() -> None:
    assert titles.name_of("Rune Paul Rune Paul Falkner") == "Rune Paul Falkner"
    assert titles.name_of("R. J R. J. Falkner") == "R. J. Falkner"
    assert titles.name_of("Ilka S. Brenner Brenner") == "Ilka S. Brenner"
    assert titles.name_of("Mara C. Holt Ph.D. Ph.D.") == "Mara C. Holt"
    # A name that only looks alike stays as it is.
    assert titles.name_of("Ford Madox Ford") == "Ford Madox Ford"
    assert titles.name_of("Ma Lin") == "Ma Lin"


def test_the_author_of_a_hit_is_the_spelling_most_records_write_not_a_longer_slip() -> None:
    entries = [Entry(source="audible", key=f"B90060000{n}", title="Nachtgast", authors=[name], language="de",
                     form="audio") for n, name in enumerate(["Edgar Wellmann", "Edgar Wellmann", "Edgar Welllmann"])]
    found = build.build(entries, read("Nachtgast"), languages=["de"], original=True)
    assert found[0].authors[0] == "Edgar Wellmann"


# ---- A slip of the keys -----------------------------------------------------------------------------------------


def test_a_title_and_a_name_typed_with_a_slip_find_the_book_by_the_starts_of_the_words(
        client: TestClient, operator: Account, own: Any) -> None:
    """The library knows no slip of the keys: for ``Laternenstrase Sorel`` it has nothing, and it is asked once more for
    the starts of the words. The book comes first, its author found with the slip too."""
    dnb = [record("94001", "Die Laternenstraße", "Sorrel, Ada", 2013, isbn=isbn("978300009400"), pages=430),
           record("94002", "Die Laternenstraße", "Sorrel, Ada", 2015, isbn=isbn("978300009401"), pages=430),
           record("94003", "Laternenfest", "Sorensen, Pia", 2020, isbn=isbn("978300009402"))]
    near = " ".join(words_start for words_start in ("laternens", "sor"))
    fake = own([{"q": "Laternenstrase Sorel", "ol": {}, "dnb": "", "audible": {}},
                {"q": near, "ol": {}, "dnb": "DNB", "audible": {}}], {"DNB": dnb})
    body = search(client, "Laternenstrase Sorel")
    first = body["items"][0]
    assert (first["title"], first["authors"][0]) == ("Die Laternenstraße", "Ada Sorrel"), shown(body)
    queries = [params_of(request)["query"][0] for request in fake.log if host_of(request) == "services.dnb.de"]
    assert queries == ['woe="laternenstrase" and woe="sorel"', 'woe="laternens*" and woe="sor*"'], queries


def test_a_word_typed_with_a_slip_counts_less_than_one_typed_right() -> None:
    entries = [Entry(source="audible", key="B900700001", title="Der Leuchtturm", authors=["Merit Lund"], language="de",
                     form="audio"),
               Entry(source="audible", key="B900700002", title="Der Leuchturm", authors=["Ole Brink"], language="de",
                     form="audio")]
    found = build.build(entries, read("Leuchturm"), languages=["de"], original=True)
    assert [hit.authors[0] for hit in found] == ["Ole Brink", "Merit Lund"]
    assert found[1].cover == build.NEAR_WEIGHT < 1.0


# ---- One book, one hit -------------------------------------------------------------------------------------------


def _hits(entries: list[Entry], text: str, languages: tuple[str, ...] = ("de",)) -> list[Any]:
    return build.build(entries, read(text), languages=list(languages), original=True)


def test_the_recordings_a_shop_files_as_a_series_of_the_name_of_the_book_are_one_book() -> None:
    """A shop files four recordings of a classic as ``Der Glockenturm`` 1, 2, 6 and 7: one book, not four volumes."""
    entries = [Entry(source="audible", key=f"B90080000{n}", title="Der Glockenturm", authors=["Viktor Haller"],
                     language="de", form="audio", series="Der Glockenturm", series_pos=float(n)) for n in (1, 2, 6, 7)]
    entries.append(Entry(source="dnb", key="90081", title="Der Glockenturm", authors=["Haller, Viktor"], language="de"))
    found = [hit for hit in _hits(entries, "Der Glockenturm") if hit.label == "work"]
    assert [(hit.title, hit.work.series_pos) for hit in found] == [("Der Glockenturm", None)]


def test_a_book_a_catalogue_credits_to_the_last_name_alone_is_the_authors_book() -> None:
    entries = [
        Entry(source="openlibrary", key="/works/OL9009W", work_key="OL9009W", title="Die Mauer",
              authors=["Haberland"], language="de", signals={"work_level": True}, weight=12),
        Entry(source="dnb", key="90091", title="Die Mauer", authors=["Haberland, Merle"], language="de"),
        Entry(source="audible", key="B900900001", title="Die Mauer", authors=["Merle Haberland"], language="de",
              form="audio"),
    ]
    found = [hit for hit in _hits(entries, "Die Mauer") if hit.label == "work"]
    assert [(hit.title, hit.authors[0]) for hit in found] == [("Die Mauer", "Merle Haberland")]
    assert {entry.source for entry in found[0].work.entries} == {"openlibrary", "dnb", "audible"}


def test_what_a_shop_writes_behind_the_dash_of_every_book_of_a_series_is_no_title() -> None:
    """``Vierte Pforte - Glutkuss``, ``Eisenpforte - Glutkuss``: ``Glutkuss`` is what the series is called in German;
    the books are ``Vierte Pforte`` and ``Eisenpforte``."""
    entries = [Entry(source="audible", key=f"B90100000{n}", title=title, authors=["Rhea Vant"], language="de",
                     form="audio", series="Vierte Pforte", series_pos=float(n))
               for n, title in ((1, "Vierte Pforte - Glutkuss"), (2, "Eisenpforte - Glutkuss"),
                                (3, "Sturmpforte - Glutkuss"))]
    found = {hit.title for hit in _hits(entries, "Vierte Pforte")}
    assert found == {"Vierte Pforte", "Eisenpforte", "Sturmpforte"}, found


def test_a_shops_title_with_the_authors_whole_name_in_front_is_the_book_itself() -> None:
    entries = [Entry(source="audible", key="B901100001", title="Grete Moll, Freiheit", authors=["Grete Moll"],
                     language="de", form="audio"),
               Entry(source="audible", key="B901100002", title="Grete Molls Freiheit", authors=["Grete Moll"],
                     language="de", form="audio"),
               Entry(source="dnb", key="90111", title="Freiheit", authors=["Moll, Grete"], language="de")]
    found = _hits(entries, "Freiheit Grete Moll")
    assert [(hit.title, hit.label, len(hit.work.entries)) for hit in found] == [("Freiheit", "work", 3)]


# ---- What a hit is ---------------------------------------------------------------------------------------------


def test_study_guides_and_teaching_material_are_named_for_what_they_are_whoever_they_credit() -> None:
    """A library credits the writer of the book beside the study guide: the words of the title say what it is."""
    for title in ("Interpretation zu Hartmann, Das stille Meer", "Lehrerheft", "Unterrichtsstunde: Das stille Meer",
                  "Filmanalyse von Das stille Meer", "Das stille Meer als Projekt für den Unterricht",
                  "A literature unit for The Silent Sea"):
        entry = Entry(source="dnb", key="1", title=title, authors=["Hartmann, Kurt"], language="de")
        assert build.pile_of(entry) == "guide", title
    tarot = Entry(source="audible", key="2", title="Das stille Meer Tarot", authors=["Kurt Hartmann"], language="de")
    assert build.pile_of(tarot) == "companion"


def test_an_edition_whose_title_says_what_kind_of_book_it_is_is_no_book_about_the_book() -> None:
    entries = [Entry(source="openlibrary", key=f"/works/OL90{n}W", work_key=f"OL90{n}W", title=title,
                     authors=["Hanno Seer"], language="de", signals={"work_level": True}, weight=5)
               for n, title in ((12, "Siddara"), (13, "Siddara. Eine indische Dichtung"), (14, "Siddara: Ein Journal"))]
    entries.append(Entry(source="dnb", key="90150", title="Das Journal der Lena Falk", authors=["Seer, Hanno"],
                         language="de"))
    labels = {hit.title: hit.label for hit in _hits(entries, "Siddara Journal")}
    assert "companion" in labels.values() and labels["Das Journal der Lena Falk"] == "work", labels
    assert labels.get("Siddara. Eine indische Dichtung", "work") == "work", labels
    assert labels["Siddara"] == "work"


# ---- The year --------------------------------------------------------------------------------------------------


def test_the_year_one_record_alone_gives_a_few_years_too_early_is_a_slip_and_a_classics_first_year_stays() -> None:
    from app.services.oeuvre import works

    def entries(years: list[int]) -> list[Entry]:
        return [Entry(source="dnb", key=str(n), title="T", authors=["A B"], year=year) for n, year in enumerate(years)]

    assert works.first_year_of(2018, entries([2018, 2025, 2025, 2026])) == 2025
    assert works.first_year_of(1838, entries([1838, 2005, 2007, 2007])) == 1838
    assert works.first_year_of(2019, entries([2019, 2020, 2025])) == 2019
    assert works.first_year_of(None, entries([2020])) is None


def test_a_wish_made_from_a_hit_looks_for_its_titles_in_the_languages_fetched_in_and_its_own_only(
        client: TestClient, operator: Account, own: Any) -> None:
    dnb = [record("96001", "Die Burg", "Vogt, Kara", 1926, isbn=isbn("978300009600")),
           record("96002", "Die Burg", "Vogt, Kara", 2004, isbn=isbn("978300009601")),
           record("96003", "Seong", "Vogt, Kara", 2015, language="kor", translated="ger", original="Die Burg",
                  isbn=isbn("978300009602")),
           record("96004", "Shiro", "Vogt, Kara", 2016, language="jpn", translated="ger", original="Die Burg",
                  isbn=isbn("978300009603"))]
    own([{"q": "Die Burg Vogt", "ol": {}, "dnb": "DNB", "audible": {}}], {"DNB": dnb})
    body = search(client, "Die Burg Vogt")
    first = body["items"][0]
    assert first["title"] == "Die Burg" and {"ko", "ja"} <= set(first["languages"]), shown(body)
    made = client.post(f"/api/v1/search/catalog/{body['ticket']}/books", json={"key": first["key"]})
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == made.json()["token"]))
        assert row is not None
        assert {item["language"] for item in row.data["titles"]} == {"de"}


def test_a_series_a_library_credits_as_the_author_is_no_name_typed_with_a_slip() -> None:
    """``Golemm`` is ``Golem`` with a slip: the book of that title comes first, not an episode of a series the library
    credits as its author (``Graf vs Golem``)."""
    entries = [Entry(source="dnb", key="97001", title="Gefangen im Turm", authors=["Graf vs Golem"], language="de",
                     form="audio", series="Graf vs. Golem", series_pos=8.0),
               Entry(source="audible", key="B970000001", title="Golem", authors=["Kira Stahl"], language="de",
                     form="audio", runtime_min=500),
               Entry(source="openlibrary", key="/works/OL9701W", work_key="OL9701W", title="Golem",
                     authors=["Kira Stahl"], language="de", signals={"work_level": True}, weight=40)]
    found = build.build(entries, read("Golemm"), languages=["de"], original=True)
    assert found[0].title == "Golem"
    assert not build._a_person("Graf vs Golem") and build._a_person("Kira Stahl")


def test_the_volume_of_the_series_of_the_typed_name_comes_before_the_same_volume_of_a_spin_off() -> None:
    """``Nordlicht 2``: volume 2 of ``Nordlicht`` (few records), not volume 2 of ``Nordlicht - Anfänge`` (many)."""
    entries = [Entry(source="dnb", key="98001", title="Der Silbersee", authors=["Lund, Merit"], language="de",
                     series="Nordlicht", series_pos=2.0, isbns=["9783000098001"]),
               Entry(source="dnb", key="98000", title="Die Fähre", authors=["Lund, Merit"], language="de",
                     series="Nordlicht", series_pos=1.0, isbns=["9783000098000"])]
    entries += [Entry(source="dnb", key=f"9801{n}", title="Der Eisweg", authors=["Lund, Merit"], language="de",
                      series="Nordlicht - Anfänge", series_pos=2.0, isbns=[f"978300009801{n}"]) for n in range(4)]
    entries.append(Entry(source="openlibrary", key="/works/OL9801W", work_key="OL9801W", title="Der Eisweg",
                         authors=["Merit Lund"], language="de", signals={"work_level": True}, weight=60,
                         isbns=["9783000098010"]))
    found = build.build(entries, read("Nordlicht 2"), languages=["de"], original=True)
    assert found[0].title == "Der Silbersee", [(hit.title, hit.volume_fits) for hit in found]


def test_an_episode_asked_for_comes_before_a_book_many_know() -> None:
    entries = [Entry(source="dnb", key=f"9902{n}", title="Kiri no Ken 01", authors=["Hayashi, Ren"], language="de",
                     series="Kiri no Ken", series_pos=1.0, isbns=[f"978300009902{n}"]) for n in range(5)]
    entries.append(Entry(source="dnb", key="99030", title="Spiegelbild", authors=["Hayashi, Ren"], language="de",
                         form="audio", series="Kiri no Ken", series_pos=1.0))
    found = build.build(entries, read("Kiri no Ken Folge 1"), languages=["de"], original=True)
    assert (found[0].title, found[0].label) == ("Spiegelbild", "drama"), [(hit.title, hit.label) for hit in found]


def test_a_recording_a_shop_files_under_a_series_and_the_book_without_one_are_one_hit() -> None:
    entries = [Entry(source="audible", key="B990400001", title="Der Heiler", authors=["Nora Lorr"], language="de",
                     form="audio", series="Familie Lorr", series_pos=1.0, runtime_min=900),
               Entry(source="audible", key="B990400002", title="Der Heiler", authors=["Nora Lorr"], language="de",
                     form="audio", runtime_min=880),
               Entry(source="openlibrary", key="/works/OL9904W", work_key="OL9904W", title="Der Heiler",
                     authors=["Nora Lorr"], language="de", signals={"work_level": True}, weight=50)]
    found = [hit for hit in _hits(entries, "Der Heiler") if hit.label == "work"]
    assert [(hit.title, len(hit.work.entries)) for hit in found] == [("Der Heiler", 3)], [
        (hit.title, hit.work.series_pos) for hit in found]


def test_a_classic_in_two_volumes_with_its_whole_edition_and_its_translations_is_the_book_first() -> None:
    """``fixtures/search/whole_and_volumes.json``: a classic printed whole and in two volumes, with recordings, a play,
    boxes and translations (a search recorded from the sources, names and titles invented). The whole book comes
    first and stays a book: a translation that carries its German title must not join it to one of its volumes."""
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "search" / "whole_and_volumes.json"
    entries = [Entry.from_json(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    found = _hits(entries, "Der Bergwald")
    first = found[0]
    assert (first.title, first.authors[0], first.label) == ("Der Bergwald", "Hanno Seer", "work"), [
        (hit.title, hit.label) for hit in found[:4]]
    assert len(first.work.entries) > 20


def test_the_lines_of_publishers_the_catalogues_name_as_series_are_lines_and_series_of_books_are_not() -> None:
    from app.services import series_names

    for line in ("Die Originale", "Literarisches Gold", "Meister des Schreckens", "Hanno Seer, Werke",
                 "Classic Mindful Literature Series", "Pitsogul segye munhak chŏnjip", "Gesammelte Werke"):
        assert series_names.is_line(line), line
    for series in ("Nordlicht", "Die Laternen-Saga", "Kiri no Ken", "Familie Lorr", "Goldene Pforte", "Literaturkrimi"):
        assert not series_names.is_line(series), series


def test_a_recording_a_shop_files_under_a_series_is_the_same_book_as_the_one_without() -> None:
    """``fixtures/search/one_book_two_shelves.json``: the shop files one recording of a novel under the family saga it
    begins and the others under none; Open Library has the book once with its German edition and once as a German work
    of its own (a search recorded from the sources, names and titles invented). One book, one hit."""
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "search" / "one_book_two_shelves.json"
    entries = [Entry.from_json(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    found = [hit for hit in _hits(entries, "Der Wundarzt") if hit.label == "work"]
    assert [hit.title for hit in found].count("Der Wundarzt") == 1, [(hit.title, hit.work.series_pos) for hit in found]
    assert found[0].title == "Der Wundarzt" and len(found[0].work.entries) >= 4


def test_a_title_printed_in_capitals_is_written_the_way_another_record_writes_it_or_with_capitals_at_its_start() -> None:
    def title(*spellings: tuple[str, str]) -> str:
        entries = [Entry(source=source, key=f"{source}-{n}", title=text, authors=["Ilka Brenner"], language="de",
                         form="audio" if source == "audible" else "text")
                   for n, (source, text) in enumerate(spellings)]
        return build.build(entries, read(spellings[-1][1]), languages=["de"], original=True)[0].title

    assert title(("dnb", "DER GAST IM NEBELHAUS"), ("dnb", "DER GAST IM NEBELHAUS"),
                 ("openlibrary", "Der Gast im Nebelhaus")) == "Der Gast im Nebelhaus"
    assert title(("audible", "NACHTGAST"), ("dnb", "NACHTGAST")) == "Nachtgast"
