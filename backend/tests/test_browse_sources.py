"""The two ways the shelves reach the sources: Apple's chart of one genre and Open Library's search for a shelf.
What they ask, from which host, what they make of a hostile or odd answer. No service is reached: the answers are the
fixtures, and the questions are built out of the fixed table of shelves, never out of a text a person typed."""

from __future__ import annotations

import pytest

from app.services.browse import shelves
from app.services.sources import apple, config, http, openlibrary

from .fake_sources import fixture, shelf_docs

# ---- Apple's chart of one genre -----------------------------------------------------------------------------------


def test_the_address_of_a_genre_chart_is_built_from_a_country_and_a_number() -> None:
    assert apple.genre_url("de", 9032) == "https://itunes.apple.com/de/rss/toppaidebooks/genre=9032/limit=100/json"
    assert apple.genre_url("us", "10063", 25).endswith("/genre=10063/limit=25/json")
    assert apple.genre_url("us", 9032, 500).endswith("/limit=100/json"), "no more than the feed gives"


@pytest.mark.parametrize("country", ["", "d", "deu", "DE", "d/", "de/../x", "de?x=1", "de#"])
def test_a_country_that_is_none_makes_no_address(country: str) -> None:
    with pytest.raises(ValueError):
        apple.genre_url(country, 9032)


@pytest.mark.parametrize("genre", ["", "x", "9032/", "9032?x", "9", "1234567", "9032 ", "../9032", "-9032"])
def test_a_genre_that_is_no_number_makes_no_address(genre: str) -> None:
    with pytest.raises(ValueError):
        apple.genre_url("de", genre)


def test_the_host_of_the_older_feed_is_the_sources_own_and_no_other() -> None:
    assert "itunes.apple.com" in config.HOSTS["apple"]
    assert http.host_allowed("apple", apple.genre_url("de", 9032))
    assert not http.host_allowed("apple", "https://itunes.apple.com.evil.example/de/rss/x")
    assert not http.host_allowed("apple", "http://itunes.apple.com/de/rss/x")
    assert not http.host_allowed("openlibrary", apple.genre_url("de", 9032))


def test_the_books_of_the_chart_are_read_in_order_with_their_words() -> None:
    found = apple.parse_genre(fixture("apple_genre.json"), "")
    assert [c.title for c in found] == ["Nebelwacht", "Der Leuchtturmwaerter", "Die Salzwiese", "Orbit Null", "Kommende Dinge",
                                        "Das Raetsel von Kalmar", "Fremdes Bild"]
    first = found[0]
    assert first.key == "1800000001" and first.authors == ["Ilse Brandt"] and first.released == "2026-09-20"
    assert first.description.startswith("Ein Kommissar am Deich") and first.subjects == ["Polizeiromane", "Mysteries & Thrillers"]
    assert first.cover_url.startswith("https://is1-ssl.mzstatic.com/") and "0x170bb" in first.cover_url, "the largest picture"


def test_a_day_apple_does_not_know_is_no_day_and_a_foreign_picture_is_none() -> None:
    found = {c.title: c for c in apple.parse_genre(fixture("apple_genre.json"))}
    assert found["Das Raetsel von Kalmar"].released == "" and found["Das Raetsel von Kalmar"].year is None
    assert found["Fremdes Bild"].cover_url == ""


@pytest.mark.parametrize("answer", [None, [], "text", 3, {}, {"feed": None}, {"feed": {"entry": None}}, {"feed": {"entry": "x"}},
                                    {"feed": {"entry": [None, 3, [], {"id": None}, {"id": {"attributes": {"im:id": "x"}}}]}}])
def test_an_answer_that_is_no_chart_gives_no_books(answer: object) -> None:
    assert apple.parse_genre(answer) == []


def test_a_chart_with_one_book_is_a_chart_with_one_book() -> None:
    one = fixture("apple_genre.json")["feed"]["entry"][0]
    assert [c.title for c in apple.parse_genre({"feed": {"entry": one}})] == ["Nebelwacht"]


def test_at_most_a_hundred_books_are_read() -> None:
    entry = fixture("apple_genre.json")["feed"]["entry"][0]
    many = {"feed": {"entry": [{**entry, "id": {"label": "x", "attributes": {"im:id": str(1000000 + n)}}} for n in range(250)]}}
    assert len(apple.parse_genre(many)) == apple.GENRE_SIZE


# ---- Open Library's search for a shelf ------------------------------------------------------------------------------


def test_the_question_for_a_shelf_asks_for_whole_subjects_by_their_keys() -> None:
    """A word inside a subject is not asked for: ``subject:"science"`` would find "Political science" (and with it the
    wrong books on the shelf of science, live at Open Library 122,409 books against 43,848)."""
    query = openlibrary.subject_query(("thriller", "detective and mystery stories", "children's fiction"),
                                      awards=("Edgar Award",), exclude=("juvenile fiction",), years=(2017, 2026))
    assert query == ('(subject_key:"thriller" OR subject_key:"detective_and_mystery_stories" OR '
                     "subject_key:\"children's_fiction\") (subject:\"Edgar Award\") "
                     '-subject_key:"juvenile_fiction" first_publish_year:[2017 TO 2026]')
    assert openlibrary.subject_query(("thriller",)) == '(subject_key:"thriller")'
    assert openlibrary.subject_key("Self-help") == "self-help"


@pytest.mark.parametrize("text", ['x" OR subject:"y', "a\nb", "x)", "(x", "a;b", "", "x" * 61, "<script>", "a\\b", "a:b*"])
def test_a_text_that_is_no_subject_never_goes_into_a_question(text: str) -> None:
    with pytest.raises(ValueError):
        openlibrary.subject_query((text,))
    with pytest.raises(ValueError):
        openlibrary.subject_query(("thriller",), awards=(text,))
    with pytest.raises(ValueError):
        openlibrary.subject_query(("thriller",), exclude=(text,))


@pytest.mark.parametrize("years", [(2026, 2017), (0, 2026), (1000, 3000), (-5, 5)])
def test_years_that_are_none_make_no_question(years: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        openlibrary.subject_query(("thriller",), years=years)


def test_every_subject_and_prize_of_the_table_is_a_subject_the_question_takes() -> None:
    for shelf in shelves.SHELVES:
        openlibrary.subject_query(shelf.subjects, awards=shelf.awards, exclude=shelf.exclude, years=(1800, 2026))
    assert {name for shelf in shelves.SHELVES for name in shelf.awards} <= set(shelves.AWARDS), "a prize without a first year"


def test_the_books_of_a_shelf_carry_the_title_and_picture_of_the_edition_in_the_language() -> None:
    german = {c.title: c for c in openlibrary.parse_shelf(fixture("openlibrary_shelf.json"), "de")}
    assert german["Der stille Pier"].key == "OL1001W~de" and german["Der stille Pier"].work_key == "OL1001W"
    assert german["Der stille Pier"].cover_url.endswith("/b/id/75001-L.jpg")
    assert german["Der stille Pier"].languages == ["de"] and german["Der stille Pier"].year == 2022
    assert german["Nebelläufer"].released == "2026-03-05" and german["Nebelläufer"].edition_year == 2026
    assert german["Die Salztöchter"].released == "" and german["Die Salztöchter"].edition_year == 2025
    english = {c.title: c for c in openlibrary.parse_shelf(fixture("openlibrary_shelf.json"), "en")}
    assert english["The Quiet Pier"].key == "OL1001W", "English keeps the plain name of the work"
    assert english["The Quiet Pier"].cover_url.endswith("/b/id/76001-L.jpg")


def test_a_book_with_no_edition_in_the_language_is_left_out() -> None:
    assert openlibrary.parse_shelf(fixture("openlibrary_shelf.json"), "fr") == []


def test_a_full_stop_at_the_end_of_a_title_goes_and_an_ellipsis_stays() -> None:
    docs = fixture("openlibrary_shelf.json")["docs"][:2]
    docs[0]["editions"]["docs"][0]["title"] = "Der stille Pier."
    docs[1]["editions"]["docs"][0]["title"] = "Und dann..."
    found = {c.title for c in openlibrary.parse_shelf({"docs": docs}, "de")}
    assert found == {"Der stille Pier", "Und dann..."}


@pytest.mark.parametrize("answer", [None, [], "x", {}, {"docs": None}, {"docs": "x"}, {"docs": [None, 3, {}, {"key": "x"}]},
                                    {"docs": [{"key": "/works/OL1W", "title": "Ohne Autor"}]}])
def test_an_answer_that_is_no_search_gives_no_books(answer: object) -> None:
    assert openlibrary.parse_shelf(answer, "de") == []


def test_a_sort_or_a_language_that_is_none_is_not_asked() -> None:
    with pytest.raises(ValueError):
        openlibrary.shelf(None, '(subject:"x")', language="de", sort="random")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        openlibrary.shelf(None, '(subject:"x")', language="zz", sort="readinglog")  # type: ignore[arg-type]


# ---- The table of shelves ------------------------------------------------------------------------------------------------


def test_the_table_has_thirteen_shelves_in_three_departments() -> None:
    assert [s.key for s in shelves.SHELVES] == ["krimi", "romane", "fantasy", "scifi", "klassiker", "bio", "geschichte",
                                                "natur", "gesellschaft", "leben", "reisen", "kinder", "jugend"]
    assert [sum(1 for s in shelves.SHELVES if s.dept == d) for d in shelves.DEPARTMENTS] == [5, 6, 2]
    assert len({s.key for s in shelves.SHELVES}) == 13 and len(shelves.INDEX_OF) == 13


def test_every_shelf_has_a_genre_for_its_covers_and_something_to_ask() -> None:
    from app.services import genres

    for shelf in shelves.SHELVES:
        assert shelf.genre in genres.GENRES, shelf.key
        assert shelf.subjects and shelf.apple, shelf.key
        assert genres.genre_of([shelf.hint]), f"the word of {shelf.key} gives its books a genre"
        assert all(section in shelves.SECTIONS for section in shelf.sections)


def test_the_biographies_have_the_six_topics_of_the_mock_up() -> None:
    assert [t.key for t in shelves.BY_KEY["bio"].topics] == ["musik", "politik", "wissenschaft", "kunst", "sport", "abenteuer"]


@pytest.mark.parametrize(("shelf", "text", "topics"), [
    ("bio", "Biography Musicians Jazz", ["musik"]),
    ("bio", "Eine Dirigentin; Komponistin und Sängerin", ["musik"]),
    ("bio", "Mountaineers, expedition to the polar north", ["abenteuer"]),
    ("bio", "Prime ministers and presidents; physics", ["politik", "wissenschaft"]),
    ("bio", "A quiet story about gardening", []),
    ("krimi", "Polizeiromane", ["ermittler"]),
    ("krimi", "Detective and mystery stories", ["ermittler"]),
    ("reisen", "Wandern in den Alpen", ["europa", "wandern"]),
    ("natur", "Die Sprache der Bäume", []),
    ("natur", "Das Klima der Erde", ["klima"]),
])
def test_topics_are_found_by_the_start_of_a_word_in_the_subjects_and_the_title(shelf: str, text: str, topics: list[str]) -> None:
    assert shelves.topics_of(shelves.BY_KEY[shelf], text) == topics


def test_a_word_inside_another_word_is_not_a_topic() -> None:
    """"art" is not in "party", "sport" not in "transport"."""
    assert shelves.topics_of(shelves.BY_KEY["bio"], "A party of one") == []
    assert shelves.topics_of(shelves.BY_KEY["bio"], "Transport of goods") == []


def test_the_mini_search_of_the_tests_answers_what_the_question_asks() -> None:
    """The stand-in of Open Library is checked against itself: it keeps what fits every group of subjects and the years, and
    no excluded subject; a book of the wrong language is not there."""
    docs = fixture("openlibrary_shelf.json")["docs"]
    query = openlibrary.subject_query(("thriller",), awards=("Edgar Award",), years=(1900, 1970))
    found = shelf_docs(docs, {"q": [query + " language:ger"], "limit": ["50"]})
    assert [d["title"] for d in found] == ["A Case of Salt"], "'The Long Goodbye Road' is a mystery, not a thriller"
    assert shelf_docs(docs, {"q": [query + " language:fre"], "limit": ["50"]}) == []
    assert len(shelf_docs(docs, {"q": ['(subject_key:"thriller") language:ger'], "limit": ["3"]})) == 3
    science = [{"subject": ["Political science"], "first_publish_year": 2000, "language": ["eng"]}]
    assert shelf_docs(science, {"q": ['(subject:"science")'], "limit": ["5"]}), "a phrase is found inside a subject"
    assert not shelf_docs(science, {"q": ['(subject_key:"science")'], "limit": ["5"]}), "a key is the whole subject"
