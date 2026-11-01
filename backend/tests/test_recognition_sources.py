"""The clients as the recognition of the books needs them: the German National Library with its many records for one book,
its recordings and games, its translations and its subject group, paged through; Audible with its categories and its
advertising titles. Recorded in the format of the real services, with invented books."""

from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.services import genres
from app.services.sources import audible, dnb, http
from app.services.sources.model import Query

from .fake_sources import audible_product, fixture, params_of
from .match_helpers import World

EBOOK = Query(kind="ebook", title="Die Nebelwacht", authors=["Ada Feldkamp"], language="de")
AUDIO = Query(kind="audio", title="Die Nebelwacht", authors=["Ada Feldkamp"], language="de")


def books(query: Query, *, drop_translations: bool = False) -> list:
    records, _page, _total = dnb.parse_page(fixture("dnb_sru_books.xml"))
    return dnb.books_of(records, query, drop_translations=drop_translations)


# ---- The German National Library: one book from many records ---------------------------------------------------------


def test_the_records_of_one_book_are_one_candidate_with_the_earliest_year_of_any_edition() -> None:
    found = {c.title: c for c in books(EBOOK)}
    fog = found["Die Nebelwacht"]
    # Three records of the book (2021, 2018 as the advertising title, 2024): the book is from 2018, not from the printing.
    assert fog.year == 2018
    assert fog.isbns == ["9783999911021", "9783999911014", "9783999911038"]
    assert fog.weight == 3
    assert fog.authors == ["Ada Feldkamp"], "a name written twice by a careless record is one word"
    assert fog.series == "Wolff-Reihe" and fog.series_pos == 1.0
    assert fog.languages == ["de"]


def test_a_recording_and_a_game_are_not_the_book_when_a_book_is_asked_for() -> None:
    fog = next(c for c in books(EBOOK) if c.title == "Die Nebelwacht")
    assert "9783999911045" not in fog.isbns, "the ISBN of the recording"
    assert fog.year != 2015 and fog.year != 2016, "the game of 2015 and the recording of 2016 are not editions of the book"
    assert not any("Kartenspiel" in c.subtitle for c in books(EBOOK))


def test_a_recording_is_one_of_the_records_of_the_book_when_a_recording_is_asked_for() -> None:
    fog = next(c for c in books(AUDIO) if c.title == "Die Nebelwacht")
    assert "9783999911045" in fog.isbns and fog.year == 2016
    assert not any("Spielkarten" in c.subtitle or "Kartenspiel" in c.subtitle for c in books(AUDIO))


def test_a_record_in_another_language_is_left_out() -> None:
    assert not any(c.title == "The Fog Watch" for c in books(EBOOK))
    assert any(c.title == "The Fog Watch" for c in books(Query(kind="ebook", title="x", language="en")))
    assert any(c.title == "The Fog Watch" for c in books(Query(kind="ebook", title="x")))


def test_a_translation_into_german_is_a_book_in_german_with_its_original_language() -> None:
    path = next(c for c in books(EBOOK) if c.title == "Der Wolkenpfad")
    assert path.original_language == "en" and path.original_title == "The Cloud Path"
    assert path.authors == ["Dana Example"], "the translator is no author"


def test_translations_can_be_left_out_for_whoever_builds_the_catalogue_of_an_authors_own_works() -> None:
    assert not any(c.title == "Der Wolkenpfad" for c in books(EBOOK, drop_translations=True))
    assert any(c.title == "Die Nebelwacht" for c in books(EBOOK, drop_translations=True))


def test_the_subject_group_and_the_headings_give_the_genre() -> None:
    found = {c.title: c for c in books(EBOOK)}
    # B (fiction) with the heading "Kriminalroman" and the kind of book the titles name: crime.
    assert genres.winner(genres.tally(found["Die Nebelwacht"].subjects, 2.0)) == "krimi"
    # K: a book for children and young adults. A number: non-fiction.
    assert genres.winner(genres.tally(found["Die kleine Laterne"].subjects, 2.0)) == "kinder"
    assert genres.winner(genres.tally(found["Das Gartenjahr"].subjects, 2.0)) == "sachbuch"
    # B alone is a novel; the kind of book "Roman" in the subtitle is no more than that.
    assert genres.winner(genres.tally(found["Der Wolkenpfad"].subjects, 2.0)) == "roman"


def test_the_subject_group_is_what_most_of_the_records_of_a_book_say() -> None:
    """A school edition of a novel sits in the group for non-fiction: it must not make the novel a non-fiction book."""
    def record(ident: str, group: str) -> dnb.Record:
        return dnb.Record(ident=ident, title="Der Vorleser", authors=["Ada Feldkamp"], subject_group=[group],
                          languages=["de"])

    found = dnb.books_of([record("1", "B"), record("2", "B"), record("3", "B"), record("4", "370")], EBOOK)
    assert len(found) == 1 and "Belletristik" in found[0].subjects and "Sachbuch" not in found[0].subjects
    # Equal counts go to fiction first.
    found = dnb.books_of([record("1", "370"), record("2", "B")], EBOOK)
    assert "Belletristik" in found[0].subjects and "Sachbuch" not in found[0].subjects


def test_the_advertising_of_a_title_is_cut_to_its_core() -> None:
    records, _page, _total = dnb.parse_page(fixture("dnb_sru_books.xml"))
    ads = next(r for r in records if r.ident == "2000000002")
    assert dnb._candidate(ads).title == "Die Nebelwacht"
    assert dnb._hints(ads.title) == ["Ein Wolff-Krimi 1"]


def test_a_search_pages_through_the_answer_up_to_its_limit(world: World) -> None:
    world.settings(source_dnb=True)

    def page(start: int, count: int, total: int) -> bytes:
        records = "".join(
            f'<record><recordData><record xmlns="http://www.loc.gov/MARC21/slim"><leader>00000nam a2200000 c 4500</leader>'
            f'<controlfield tag="001">{3000000 + start + i}</controlfield><controlfield tag="007">tu</controlfield>'
            f'<datafield tag="041"><subfield code="a">ger</subfield></datafield>'
            f'<datafield tag="100"><subfield code="a">Feldkamp, Ada</subfield></datafield>'
            f'<datafield tag="245"><subfield code="a">Die Nebelwacht Teil {start + i}</subfield></datafield>'
            f'<datafield tag="264"><subfield code="c">{2000 + i}</subfield></datafield>'
            f"</record></recordData></record>" for i in range(count))
        return (f'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>{total}</numberOfRecords>'
                f"<records>{records}</records></searchRetrieveResponse>").encode()

    world.fake.dnb_pages = {1: page(1, 2, 6), 3: page(3, 2, 6), 5: page(5, 2, 6)}
    with SessionLocal() as db:
        found = dnb.search(db, EBOOK)
    assert len(world.fake.urls("services.dnb.de")) == 3, "all three pages were asked, no more"
    assert [params_of(r)["startRecord"][0] for r in world.fake.log] == ["1", "3", "5"]
    assert len(found) == 6, "the records of six different titles are six books, none of them cut off at the first page"


def test_a_search_stops_at_the_page_limit_however_much_the_library_has(world: World) -> None:
    world.settings(source_dnb=True)
    body = (b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>900</numberOfRecords><records>'
            b'<record><recordData><record xmlns="http://www.loc.gov/MARC21/slim"><leader>00000nam a2200000 c 4500</leader>'
            b'<controlfield tag="001">1</controlfield><datafield tag="245"><subfield code="a">X</subfield></datafield>'
            b"</record></recordData></record></records></searchRetrieveResponse>")
    world.fake.dnb = body
    with SessionLocal() as db:
        dnb.search(db, EBOOK)
    assert len(world.fake.urls("services.dnb.de")) == 3, "three pages of a hundred records at most, however much the library has"


def test_a_search_asks_for_the_core_title_not_for_the_advertising(world: World) -> None:
    world.settings(source_dnb=True)
    with SessionLocal() as db:
        dnb.search(db, Query(kind="ebook", title="Die Nebelwacht (Ein Wolff-Krimi 1): Ein Mord im Nebel",
                             authors=["Ada Feldkamp"], language="de"))
    assert params_of(world.fake.log[0])["query"] == ['tit="Die Nebelwacht" and per="Ada Feldkamp"']


# ---- Audible ---------------------------------------------------------------------------------------------------------


def test_audible_hands_over_the_categories_and_the_keywords_as_the_subjects_of_a_product() -> None:
    product = audible_product("B0EXAMPL01", "Die Nebelwacht (Ein Wolff-Krimi 1): Ein Mord im Nebel", ["Ada Feldkamp"], 612)
    product["subtitle"] = "Ein Mord im Nebel"
    product["category_ladders"] = [{"ladder": [{"id": "1", "name": "Krimis & Thriller"}, {"id": "2", "name": "Psychothriller"}]}]
    product["thesaurus_subject_keywords"] = ["Ermittler", "Nebel"]
    found = audible.parse_products({"products": [product]}, "de")
    assert len(found) == 1
    assert found[0].title == "Die Nebelwacht", "the advertising is cut to the core title"
    assert found[0].subtitle == "Ein Mord im Nebel"
    assert "Krimis & Thriller" in found[0].subjects and "Psychothriller" in found[0].subjects
    assert "Ermittler" in found[0].subjects
    assert "Ein Wolff-Krimi 1" in found[0].subjects, "the words in brackets name the kind of book"
    assert genres.winner(genres.tally(found[0].subjects, 2.0)) == "krimi"


def test_audible_names_written_twice_are_one(world: World) -> None:
    product = audible_product("B0EXAMPL02", "Titel", ["Ada Ada Feldkamp", "Ada Feldkamp"], 100)
    found = audible.parse_products({"products": [product]}, "de")
    assert found[0].authors == ["Ada Feldkamp"]


def test_audible_is_asked_for_the_categories(world: World) -> None:
    world.only("audible")
    with SessionLocal() as db:
        audible.search(db, AUDIO, "de")
    assert "category_ladders" in params_of(world.fake.log[0])["response_groups"][0]
    assert params_of(world.fake.log[0])["keywords"] == ["Ada Feldkamp Die Nebelwacht"]


def test_a_source_error_of_the_library_is_an_error_of_the_source_and_nothing_else(world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = b"<searchRetrieveResponse><records><record><!DOCTYPE x></record></records></searchRetrieveResponse>"
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        dnb.search(db, EBOOK)
    assert raised.value.code == "bad_answer"


# ---- Hardcover ---------------------------------------------------------------------------------------------------------


def test_hardcover_hands_over_what_kind_of_book_it_is_with_the_editions_in_one_request(world: World) -> None:
    from app.services.sources import hardcover

    from .fake_sources import hc_doc, hc_edition

    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7001, "Das Gartenjahr", ["Ada Feldkamp"], users=50)]
    world.fake.hc_editions = {"7001": [hc_edition("Das Gartenjahr", "German")]}
    world.fake.hc_kinds = {"7001": [{"literary_type_id": 2, "cached_tags": {"Genre": [{"tag": "Gardening"}]}}]}
    with SessionLocal() as db:
        found = hardcover.search(db, Query(kind="ebook", title="Das Gartenjahr", authors=["Ada Feldkamp"], language="de"))
        again = hardcover.search(db, Query(kind="ebook", title="Das Gartenjahr", authors=["Ada Feldkamp"], language="de"))
    assert found[0].subjects == ["Sachbuch", "Gardening"] and again[0].subjects == found[0].subjects
    assert genres.winner(genres.tally(found[0].subjects, 2.0)) == "sachbuch"
    assert world.fake.count("api.hardcover.app") == 2, "the search, and the editions with the kind in one request, kept"


def test_hardcover_fiction_says_fiction_and_nothing_more(world: World) -> None:
    from app.services.sources import hardcover

    from .fake_sources import hc_doc, hc_edition

    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7002, "Die Nebelwacht", ["Ada Feldkamp"])]
    world.fake.hc_editions = {"7002": [hc_edition("Die Nebelwacht", "German")]}
    world.fake.hc_kinds = {"7002": [{"literary_type_id": 1, "cached_tags": None}]}
    with SessionLocal() as db:
        found = hardcover.search(db, EBOOK)
    assert found[0].subjects == ["Fiction"] and genres.winner(genres.tally(found[0].subjects, 2.0)) == "roman"
