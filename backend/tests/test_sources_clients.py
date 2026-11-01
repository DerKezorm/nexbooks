"""The four clients: what they ask, and what they make of the answers (recorded in the format of the real services, with
invented books). Parsing is done on the samples in ``tests/fixtures/sources``; the questions are seen at the stand-in."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db import SessionLocal
from app.services.sources import audible, dnb, hardcover, http, openlibrary
from app.services.sources.model import Candidate, Query, isbn13, language_code

from .fake_sources import fixture, hc_doc, hc_edition, ol_doc, params_of
from .match_helpers import World

BOOK = Query(kind="ebook", title="The Quiet Orchard", authors=["Mara Lindqvist"], language="en")


# ---- The shape every source hands over ---------------------------------------------------------------------------


def test_languages_are_read_by_name_by_marc_code_and_by_two_letters() -> None:
    assert [language_code(x) for x in ("German", "ger", "deu", "de", "de-DE", "Spanish; Castilian", "fre", "nope", "")] == [
        "de", "de", "de", "de", "de", "es", "fr", "", ""]


def test_an_isbn_must_have_its_check_digit() -> None:
    assert isbn13("978-3-9999-0001-8") == "9783999900018"
    assert isbn13("3999900016") == "9783999900018"
    assert isbn13("9783999900019") == "" and isbn13("12345") == "" and isbn13(None) == ""


def test_a_candidate_survives_the_cache_and_a_wrong_type_does_not_get_in() -> None:
    c = Candidate(source="audible", key="B0EXAMPL01", title="T", authors=["A"], runtime_min=600, series_pos=2.0,
                  titles={"de": ["Titel"]}, languages=["de"])
    assert Candidate.from_json(c.to_json()) == c
    damaged = {**c.to_json(), "runtime_min": "600", "authors": "A", "series_pos": "2", "weight": True, "titles": []}
    back = Candidate.from_json(damaged)
    assert back.runtime_min is None and back.authors == [] and back.series_pos is None and back.weight == 0
    assert back.titles == {}
    with pytest.raises(ValueError):
        Candidate.from_json({"source": "x", "key": "k"})


# ---- Open Library -----------------------------------------------------------------------------------------------


def test_open_library_works_are_read_from_the_search_answer() -> None:
    found = openlibrary.parse_search(fixture("openlibrary_search.json"))
    assert [c.key for c in found] == ["OL9000001W", "OL9000002W", "OL9000003W"], "records that are no work or have no title are left out"
    orchard = found[0]
    assert orchard.work_key == "OL9000001W" and orchard.title == "The Quiet Orchard" and orchard.subtitle == "A Novel"
    assert orchard.authors == ["Mara Lindqvist"] and orchard.year == 2019 and orchard.pages == 312
    assert orchard.languages == ["en", "de", "fr"]
    assert orchard.isbns == ["9783999900018", "9783999900025"], "an ISBN-10 becomes the same ISBN-13, a bad one is dropped"
    assert orchard.cover_url == "https://covers.openlibrary.org/b/id/8000001-L.jpg"
    assert orchard.subjects == ["Fiction", "Family Saga", "Orchards"] and orchard.publisher == "Example House Press"
    assert orchard.weight == 14


def test_open_library_answers_that_are_not_what_they_should_be_give_nothing() -> None:
    for data in (None, [], {}, {"docs": None}, {"docs": ["x", 3, None]}, {"docs": [{"key": "/works/OL1W", "title": 4}]}):
        assert openlibrary.parse_search(data) == []


def test_open_library_is_asked_by_title_and_author_or_by_isbn(world: World) -> None:
    world.fake.ol = fixture("openlibrary_search.json")["docs"]
    with SessionLocal() as db:
        openlibrary.search(db, BOOK)
        openlibrary.search(db, Query(kind="ebook", title="Other", authors=[], isbn="9783999900018"))
    first, second = (params_of(r) for r in world.fake.log)
    assert first["title"] == ["The Quiet Orchard"] and first["author"] == ["Mara Lindqvist"]
    assert "q" not in first and "fields" in first and "isbn" not in first["fields"][0].split(",")[:0]
    assert second["q"] == ["isbn:9783999900018"] and "title" not in second


def test_open_library_falls_back_to_title_and_author_when_it_knows_no_work_under_the_isbn(world: World) -> None:
    """A German edition Open Library does not list: the ISBN finds nothing, the title and the author do."""
    work = ol_doc("OL1W", "Foxes and Falcons", ["Rune Falkner"])
    world.fake.ol_for = lambda params: [work] if "title" in params else []
    query = Query(kind="ebook", title="Nebelchor", authors=["Rune Falkner"], language="de", isbn="9783999900018")
    with SessionLocal() as db:
        found = openlibrary.search(db, query)
    assert [c.key for c in found] == ["OL1W"]
    kinds = ["q" if "q" in params_of(r) else "title" for r in world.fake.log]
    assert kinds == ["q", "title"], "first the ISBN, then the words"


def test_a_work_the_isbn_finds_without_listing_it_is_no_reason_to_skip_the_words(world: World) -> None:
    """Open Library answered the ISBN with the film's companion book; the book itself is found by title and author."""
    companion = ol_doc("OL2W", "Frevelpfad. Das offizielle Begleitbuch zum Film", ["Rune Falkner"], languages=("ger",))
    book = ol_doc("OL1W", "The Lantern Cipher", ["Rune Falkner"], languages=("eng", "ger"))
    world.fake.ol_for = lambda params: [companion] if "q" in params else [book]
    query = Query(kind="ebook", title="Frevelpfad - The Lantern Cipher", authors=["Rune Falkner"], language="de", isbn="9783999900018")
    with SessionLocal() as db:
        found = openlibrary.search(db, query)
    assert sorted(c.key for c in found) == ["OL1W", "OL2W"]
    assert len(world.fake.log) == 2
    # When the work lists the ISBN, the words are not asked at all.
    world.fake.log.clear()
    mine = ol_doc("OL3W", "Nebelchor", ["Rune Falkner"], isbns=("9783999900018",))
    world.fake.ol_for = lambda params: [mine] if "q" in params else []
    with SessionLocal() as db:
        found = openlibrary.search(db, Query(kind="ebook", title="Nebelchor two", authors=["Rune Falkner"], isbn="9783999900018"))
    assert [c.key for c in found] == ["OL3W"] and len(world.fake.log) == 1


def test_a_work_found_by_an_isbn_keeps_that_isbn_first_among_hundreds() -> None:
    def with_check_digit(core: str) -> str:
        total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(core))
        return core + str((10 - total % 10) % 10)

    valid = [with_check_digit(f"97839999{n:04d}") for n in range(1, 400)]
    assert all(isbn13(code) == code for code in valid)
    wanted = valid[-1]
    assert len(valid) > openlibrary.MAX_ISBNS * 2
    doc = ol_doc("OL1W", "T", ["A B"], isbns=tuple(valid))
    [kept] = openlibrary.parse_search({"docs": [doc]}, wanted)
    assert kept.isbns[0] == wanted and len(kept.isbns) <= openlibrary.MAX_ISBNS
    [plain] = openlibrary.parse_search({"docs": [doc]})
    assert wanted not in plain.isbns, "without the asked ISBN the first ones are kept, and this one is lost"


def test_open_library_is_asked_for_the_core_of_the_title_and_not_for_what_a_shop_adds_to_it(world: World) -> None:
    with SessionLocal() as db:
        openlibrary.search(db, Query(kind="ebook", title="Salt Harbor: A Tide Story", authors=["Theo Ravensberg"]))
    titles = [params_of(r)["title"][0] for r in world.fake.log]
    assert titles == ["Salt Harbor"]


def test_open_library_description_comes_from_the_work_page_and_is_plain_text(world: World) -> None:
    world.fake.ol_work = fixture("openlibrary_work.json")
    with SessionLocal() as db:
        details = openlibrary.work_details(db, "OL9000001W")
        again = openlibrary.work_details(db, "OL9000001W")
    assert details == again
    assert details["description"] == "Three sisters inherit an orchard that does not want to be sold.\n\nA story about apples, weather and who stays."
    assert world.fake.count("openlibrary.org", "/works/") == 1
    with SessionLocal() as db:
        assert openlibrary.work_details(db, "../../etc/passwd") == {}
        assert openlibrary.work_details(db, "OL1W/../../x") == {}
    assert world.fake.count("openlibrary.org") == 1, "a key that is none is not asked"


# ---- Audible ----------------------------------------------------------------------------------------------------


def test_audible_products_are_read_with_running_time_narrators_and_series() -> None:
    found = audible.parse_products(fixture("audible_products.json"), "de")
    assert [c.key for c in found] == ["B0EXAMPL01", "B0EXAMPL02", "B0EXAMPL03", "B0EXAMPL04"]
    reading, abridged, play, english = found
    assert reading.runtime_min == 600 and reading.variant == "unabridged" and reading.languages == ["de"]
    assert reading.narrators == ["Ben Hollister", "Sina Voss"] and reading.authors == ["Ada Feldkamp"]
    assert (reading.series, reading.series_pos) == ("Uhrwerk-Reihe", 2.0) and reading.year == 2018
    assert reading.description == "Eine Uhrmacherin, ein Turm und die Zeit.\n\nZweiter Absatz."
    assert reading.cover_url.endswith("example1024._SL1024_.jpg"), "the largest picture"
    assert reading.market == "de" and reading.asin == "B0EXAMPL01"
    assert abridged.variant == "abridged" and abridged.runtime_min == 283
    assert play.variant == "dramatized" and play.runtime_min == 359, "a play is told by its words, not by the format"
    assert english.languages == ["en"]


def test_audible_pictures_only_from_amazons_image_host() -> None:
    data = {"products": [{"asin": "B0EXAMPL09", "title": "T", "product_images": {"500": "https://evil.example.com/x.jpg"}},
                         {"asin": "B0EXAMPL08", "title": "T", "product_images": {"500": "http://m.media-amazon.com/x.jpg"}}]}
    assert [c.cover_url for c in audible.parse_products(data, "de")] == ["", ""]


def test_audible_is_asked_in_one_market_with_the_words_of_the_question(world: World) -> None:
    world.fake.audible = {"de": fixture("audible_products.json")["products"]}
    query = Query(kind="audio", title="Die Uhrmacherin (ungekürzt)", authors=["Ada Feldkamp"], language="de")
    with SessionLocal() as db:
        found = audible.search(db, query, "de")
        assert audible.search(db, query, "xx") == [], "a market that is none is not a host"
    assert len(found) == 4 and world.fake.hosts() == ["api.audible.de"]
    params = params_of(world.fake.log[0])
    assert params["keywords"] == ["Ada Feldkamp Die Uhrmacherin"], "the noise of a title is not sent"
    assert "contributors" in params["response_groups"][0] and "series" in params["response_groups"][0]


def test_audible_asks_for_exactly_the_product_when_it_has_an_asin(world: World) -> None:
    world.fake.audible = {"de": []}
    query = Query(kind="audio", title="x", authors=[], asin="b0exampl01")
    with SessionLocal() as db:
        audible.search(db, query, "de")
    assert world.fake.log[0].url.path.endswith("/products/B0EXAMPL01")


def test_each_market_has_its_own_host(world: World) -> None:
    from app.services.sources import config

    assert config.market_host("us") == "api.audible.com" and config.market_host("uk") == "api.audible.co.uk"
    with pytest.raises(KeyError):
        config.market_host("evil.example.com/")


# ---- Hardcover --------------------------------------------------------------------------------------------------


def test_hardcover_documents_are_read() -> None:
    found = hardcover.parse_search(fixture("hardcover_search.json")["data"]["s0"])
    assert [c.key for c in found] == ["7001", "7002", "7003"], "a document without an id is left out"
    orchard = found[0]
    assert orchard.authors == ["Mara Lindqvist"] and orchard.year == 2019 and orchard.weight == 4200
    assert (orchard.series, orchard.series_pos) == ("Orchard Tales", 1.0) and orchard.description == "Three sisters and an orchard."
    assert orchard.cover_url == "https://assets.hardcover.app/books/7001/orchard.jpg"
    assert orchard.isbns == ["9783999900018"] and orchard.subjects == ["Fiction", "Family Saga"]


def test_hardcover_editions_are_counted_per_language() -> None:
    orchard = hardcover.parse_search(fixture("hardcover_search.json")["data"]["s0"])[0]
    hardcover.enrich(orchard, fixture("hardcover_editions.json")["data"]["e0"])
    assert orchard.languages[:2] == ["en", "de"], "English has three editions, German three, the most common first"
    assert orchard.titles["de"][0] == "Der stille Obstgarten", "the title most editions carry, not the author's name"
    assert orchard.titles["en"] == ["The Quiet Orchard"] and orchard.titles["fr"] == ["Le verger silencieux"]
    assert "9783999900025" in orchard.isbns and "not-an-isbn" not in orchard.isbns
    assert orchard.asin == "B0ENGLISH1" and orchard.pages == 312


def test_hardcover_searches_go_in_one_request_and_editions_in_another(world: World) -> None:
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], users=50)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English"), hc_edition("Der stille Obstgarten", "German")]}
    queries = [Query(kind="ebook", title=f"Title {n}", authors=["Mara Lindqvist"]) for n in range(7)]
    with SessionLocal() as db:
        hardcover.prefetch(db, queries)
    # Seven searches are two requests (five and two).
    assert world.fake.count("api.hardcover.app") == 2
    bodies = [json.loads(r.content) for r in world.fake.log]
    assert [len(b["variables"]) for b in bodies] == [5, 2]
    assert all(b["query"].startswith("query(") and "mutation" not in b["query"] for b in bodies)
    with SessionLocal() as db:
        found = hardcover.search(db, queries[0])
        again = hardcover.search(db, queries[0])
    # The search was kept: only the editions of the likeliest book are asked, once.
    assert world.fake.count("api.hardcover.app") == 3
    assert found[0].languages == again[0].languages == ["en", "de"]


def test_what_a_person_typed_is_only_ever_a_variable_in_hardcovers_questions(world: World) -> None:
    world.connect_hardcover()
    hostile = 'x" }) { id } } mutation { delete_everything'
    with SessionLocal() as db:
        hardcover.search(db, Query(kind="ebook", title=hostile, authors=['") injected']))
    body = json.loads(world.fake.log[0].content)
    assert hostile not in body["query"] and "injected" not in body["query"] and "mutation" not in body["query"]
    assert hostile in body["variables"]["q0"]


def test_hardcover_picks_the_book_of_the_author_for_its_editions(world: World) -> None:
    world.connect_hardcover()
    world.fake.hc = [hc_doc(1, "The Quiet Orchard", ["Hanna Lindqvist"], users=5000),
                     hc_doc(2, "The Quiet Orchard", ["Mara Lindqvist"], users=30)]
    world.fake.hc_editions = {"1": [hc_edition("A", "Swedish")], "2": [hc_edition("The Quiet Orchard", "English")]}
    with SessionLocal() as db:
        found = hardcover.search(db, BOOK)
    by_key = {c.key: c for c in found}
    assert by_key["2"].languages == ["en"] and by_key["1"].languages == []
    editions = [json.loads(r.content)["variables"] for r in world.fake.log[1:]]
    assert editions == [{"id0": 2}], "the more popular book of another Lindqvist is not the one asked about"


def test_hardcover_says_whether_its_token_is_accepted(world: World) -> None:
    world.connect_hardcover()
    with SessionLocal() as db:
        assert hardcover.check_token(db) is True
    world.fake.status["api.hardcover.app"] = 401
    with SessionLocal() as db:
        assert hardcover.check_token(db) is False
    world.fake.status["api.hardcover.app"] = 503
    world.advance(10000)
    from app.services.sources import throttle

    throttle.set_state("hardcover", "")
    with SessionLocal() as db, pytest.raises(http.SourceError):
        hardcover.check_token(db)


# ---- The German National Library --------------------------------------------------------------------------------


def test_dnb_marc_records_are_read() -> None:
    found = dnb.parse(fixture("dnb_sru.xml"))
    assert [c.key for c in found] == ["1000000001", "1000000002"], "a record without an identifier is left out"
    book = found[0]
    assert book.title == "Der stille Obstgarten" and book.subtitle == "Roman"
    assert book.authors == ["Mara Lindqvist"], "the translator is no author"
    assert book.isbns == ["9783999900025"] and book.languages == ["de"] and book.year == 2021
    assert (book.series, book.series_pos) == ("Obstgarten-Reihe", 1.0) and book.publisher == "Beispiel Verlag"
    assert found[1].authors == []


def test_dnb_xml_with_entities_or_a_doctype_is_refused_not_expanded() -> None:
    with pytest.raises(ValueError):
        dnb.parse(fixture("dnb_entities.xml"))
    with pytest.raises(ValueError):
        dnb.parse(b"<a><b>")
    with pytest.raises(ValueError):
        dnb.parse(b"not xml at all")


@pytest.mark.parametrize("head", [
    '<!DOCTYPE record SYSTEM "http://127.0.0.1/record.dtd">',
    "<!DOCTYPE record>",
    "<!DOCTYPE record []>",
    '<!DOCTYPE record PUBLIC "-//EXAMPLE//DTD//EN" "http://127.0.0.1/record.dtd">',
])
def test_dnb_xml_with_a_doctype_alone_is_refused_also_when_it_declares_no_entity(head: str) -> None:
    """A DTD is no use in an answer of the library, and it is where the attacks begin: it is refused as such, not only
    when it declares an entity."""
    with pytest.raises(ValueError):
        dnb.parse((head + "<record><controlfield tag=\"001\">1</controlfield></record>").encode("utf-8"))


def test_dnb_xml_that_is_hostile_makes_an_error_of_the_source_and_nothing_else(world: World) -> None:
    world.settings(source_dnb=True)
    world.fake.dnb = fixture("dnb_entities.xml")
    with SessionLocal() as db, pytest.raises(http.SourceError) as raised:
        dnb.search(db, BOOK)
    assert raised.value.code == "bad_answer"


def test_dnb_asks_by_title_when_the_isbn_finds_nothing(world: World) -> None:
    world.settings(source_dnb=True)
    answers = [b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><records/></searchRetrieveResponse>', fixture("dnb_sru.xml")]

    def next_answer(request: httpx.Request) -> httpx.Response:
        world.fake.log.append(request)
        return httpx.Response(200, content=answers[min(len(world.fake.log) - 1, 1)], request=request)

    world.fake.handle_request = next_answer  # type: ignore[method-assign]
    with SessionLocal() as db:
        found = dnb.search(db, Query(kind="ebook", title="Der stille Obstgarten", authors=["Mara Lindqvist"], isbn="9783999900018"))
    assert len(found) == 2
    assert [params_of(r)["query"][0].split("=")[0] for r in world.fake.log] == ["num", "tit"]


def test_dnb_is_asked_in_cql_with_the_words_quoted(world: World) -> None:
    world.settings(source_dnb=True)
    with SessionLocal() as db:
        found = dnb.search(db, Query(kind="ebook", title='Der "stille" Obstgarten', authors=["Mara Lindqvist"]))
    assert len(found) == 2
    params = params_of(world.fake.log[0])
    assert params["query"] == ['tit="Der  stille  Obstgarten" and per="Mara Lindqvist"']
    assert params["recordSchema"] == ["MARC21-xml"] and params["operation"] == ["searchRetrieve"]
    assert world.fake.hosts() == ["services.dnb.de"]


def test_audible_asks_by_the_fields_title_and_author_when_the_free_text_finds_nothing(world: World) -> None:
    world.fake.audible = {"de": []}
    world.fake.audible_fields = {"de": fixture("audible_products.json")["products"]}
    query = Query(kind="audio", title="Die Uhrmacherin (ungekürzt)", authors=["Ada Feldkamp"], language="de")
    with SessionLocal() as db:
        found = audible.search(db, query, "de")
        again = audible.search(db, query, "de")
    assert len(found) == 4 and len(again) == 4
    assert len(world.fake.log) == 2, "the free text, then the fields; the second ask is kept"
    free, fields = params_of(world.fake.log[0]), params_of(world.fake.log[1])
    assert "keywords" in free and "title" not in free
    assert fields["title"] == ["Die Uhrmacherin"] and fields["author"] == ["Ada Feldkamp"] and "keywords" not in fields


def test_audible_does_not_ask_by_the_fields_when_the_free_text_found_something(world: World) -> None:
    world.fake.audible = {"de": fixture("audible_products.json")["products"]}
    world.fake.audible_fields = {"de": []}
    query = Query(kind="audio", title="Die Uhrmacherin", authors=["Ada Feldkamp"], language="de")
    with SessionLocal() as db:
        assert len(audible.search(db, query, "de")) == 4
    assert len(world.fake.log) == 1


def test_audible_does_not_ask_by_the_fields_without_an_author_or_with_an_asin(world: World) -> None:
    world.fake.audible = {"de": []}
    world.fake.audible_fields = {"de": fixture("audible_products.json")["products"]}
    with SessionLocal() as db:
        assert audible.search(db, Query(kind="audio", title="Die Uhrmacherin", authors=[], language="de"), "de") == []
        assert audible.search(db, Query(kind="audio", title="x", authors=["A B"], asin="B0EXAMPL01"), "de") == []
    assert len(world.fake.log) == 2
