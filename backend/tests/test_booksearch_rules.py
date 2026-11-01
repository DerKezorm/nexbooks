"""The rules of the catalogue search one by one, on records made by hand (``services/booksearch``): what is typed, what
is taken off a title, who wrote a record, which pile it goes on, which records are one book and which are two, and the
order of the hits. All names and titles are invented."""

from __future__ import annotations

import importlib

import pytest

from app.services.booksearch import titles
from app.services.booksearch.asked import read
from app.services.sources.model import Entry

build = importlib.import_module("app.services.booksearch.build")


def entry(source: str, key: str, title: str, *, authors: tuple[str, ...] = ("Ada Sorrel",), language: str = "de",
          form: str = "text", series: str = "", pos: float | None = None, **signals: object) -> Entry:
    found = Entry(source=source, key=key, title=title, authors=list(authors), language=language, form=form,
                  series=series, series_pos=pos)
    found.signals = dict(signals)
    if "weight" in signals:
        found.weight = int(signals["weight"])  # type: ignore[call-overload]
    if "work_key" in signals:
        found.work_key = str(signals["work_key"])
    return found


def hits(entries: list[Entry], q: str, languages: tuple[str, ...] = ("de", "en"), original: bool = True):
    return build.build(entries, read(q), languages=list(languages), original=original)


# ---- What is typed --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "volume", "base"), [
    ("lantern saga 2", 2.0, ["lantern", "saga"]),
    ("Lantern Saga Band 3", 3.0, ["lantern", "saga"]),
    ("Laternen-Saga #4", 4.0, ["laternen", "saga"]),
    ("2048", None, ["2048"]),
    ("Catch 1000", None, ["catch", "1000"]),
    ("Band 2", None, ["band", "2"]),
])
def test_a_volume_is_the_number_the_words_end_with(typed: str, volume: float | None, base: list[str]) -> None:
    asked = read(typed)
    assert (asked.volume, asked.base) == (volume, base)


def test_the_text_without_the_volume_keeps_how_it_was_typed() -> None:
    assert read("Laternenstraße Band 2").base_text == "Laternenstraße"
    assert read("Laternenstraße").base_text == "Laternenstraße"


# ---- What is taken off a title -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("source", "title", "full", "form", "wanted", "series", "pos"), [
    ("audible", "Mara Quell liest Das Salztor", "", "audio", "Das Salztor", "", None),
    ("dnb", "Die Laternen-Saga 2", "Die Laternen-Saga 2. Das Salztor", "text", "Das Salztor", "Die Laternen-Saga", 2.0),
    ("dnb", "Das Salztor", "Das Salztor. In klarer, moderner Sprache", "text", "Das Salztor", "", None),
    ("dnb", "Das Salztor", "Das Salztor. Sonderausgabe", "text", "Das Salztor", "", None),
    ("openlibrary", "Mira oder Die seltsame Reise ans Meer", "", "text", "Mira", "", None),
    ("openlibrary", "The Salt Gate, Book 2", "", "text", "The Salt Gate", "The Lantern Saga", 2.0),
    ("openlibrary", "Krabbe Roman", "", "text", "Krabbe", "", None),
    ("openlibrary", "Tannhof in einfacher Sprache", "", "text", "Tannhof", "", None),
    ("openlibrary", "Sorrel. Das Salztor", "", "text", "Das Salztor", "", None),
])
def test_what_a_shop_or_a_library_writes_around_a_title_is_taken_off(source: str, title: str, full: str, form: str,
                                                                    wanted: str, series: str, pos: float | None) -> None:
    record = entry(source, "1", title, form=form, series="The Lantern Saga" if "Book 2" in title else "",
                   **({"full_title": full} if full else {}))
    titles.tidy(record, ["Ada Sorrel"])
    assert record.title == wanted
    if series:
        assert (record.series, record.series_pos) == (series, pos)


def test_the_subtitle_is_the_title_when_the_title_is_only_the_series_and_its_number() -> None:
    record = entry("dnb", "1", "Die Laternen-Saga 01", subtitle="Das Salztor", full_title="Die Laternen-Saga 01")
    titles.tidy(record, ["Ada Sorrel"])
    assert (record.title, record.series, record.series_pos) == ("Das Salztor", "Die Laternen-Saga", 1.0)


def test_a_reader_who_is_the_author_stays_in_front() -> None:
    record = entry("audible", "1", "Ada Sorrel liest aus ihrem Leben", form="audio")
    titles.tidy(record, ["Ada Sorrel"])
    assert record.title == "Ada Sorrel liest aus ihrem Leben"


def test_the_years_of_a_life_are_no_part_of_a_name() -> None:
    assert titles.name_of("Sorrel, Ada, (1950-2020)") == "Ada Sorrel"
    assert titles.name_of("Sorrel, Ada (1950- )") == "Ada Sorrel"
    assert titles.name_of("Ada 1950-2020 Sorrel") == "Ada Sorrel"
    assert titles.names_of(entry("dnb", "1", "X", authors=("No Author", "Various", "Ada Sorrel"))) == ["Ada Sorrel"]


# ---- Who wrote it, and what it is ----------------------------------------------------------------------------------


def test_spellings_of_one_name_are_one_author_a_co_author_is_not() -> None:
    groups = build.by_author([
        entry("openlibrary", "1", "A", authors=("J. K. Ash",)), entry("audible", "2", "A", authors=("Joanna K. Ash",)),
        entry("dnb", "3", "A", authors=("Ash, J. K.",)), entry("openlibrary", "4", "A", authors=("Süskind Ada",)),
        entry("openlibrary", "5", "A", authors=("Suskind Ada",)),
        entry("openlibrary", "6", "A", authors=("Finn Hale", "J. K. Ash")), entry("openlibrary", "7", "A",
                                                                                authors=("Ralf Ash",))])
    keys = sorted(sorted(item.key for item in group) for group in groups)
    assert keys == [["1", "2", "3"], ["4", "5"], ["6"], ["7"]]


@pytest.mark.parametrize(("title", "subtitle", "form", "pile"), [
    ("Das Salztor", "Hörspiel", "audio", "drama"),
    ("Das Salztor", "", "audio", "work"),
    ("Das Salztor", "Lektürehilfe", "text", "guide"),
    ("Das Salztor (Wandkalender 2027)", "", "text", "companion"),
    ("Das Salztor (1/2)", "", "audio", "part"),
    ("Die Laternen-Saga 1-3", "", "text", "box"),
    ("Die Laternen-Saga. Sammelband", "", "text", "box"),
    ("Das Salztor", "Roman", "text", "work"),
])
def test_the_pile_a_record_goes_on(title: str, subtitle: str, form: str, pile: str) -> None:
    assert build.pile_of(entry("dnb", "1", title, form=form, subtitle=subtitle, full_title=title)) == pile


# ---- One book, or two ----------------------------------------------------------------------------------------------


def lantern(volume: int, de: str, en: str, *, original: str = "") -> list[Entry]:
    # (The German records are translations, as a library says: ``aus dem Englischen``.)
    found = [entry("dnb", f"d{volume}", de, series="Die Laternen-Saga", pos=volume),
             entry("audible", f"a{volume}", de, form="audio", series="Die Laternen-Saga", pos=volume),
             entry("audible", f"u{volume}", en, language="en", form="audio", series="The Lantern Saga", pos=volume),
             entry("openlibrary", f"OL{volume}W", en, language="", work_level=True, work_key=f"OL{volume}W",
                   weight=100 - volume)]
    found[0].original_title, found[0].original_language = original or en, "en"
    return found


def test_a_book_in_two_languages_is_one_hit_and_a_volume_another() -> None:
    found = hits(lantern(1, "Die Laternenstraße", "The Lantern Road") + lantern(2, "Das Salztor", "The Salt Gate"),
                 "Das Salztor")
    books = [hit for hit in found if hit.label == "work"]
    assert [(hit.title, hit.work.series_pos) for hit in books] == [("Das Salztor", 2), ("Die Laternenstraße", 1)]
    assert books[0].group == "match" and books[1].group == "series"
    assert {"de", "en"} <= set(books[0].languages) and books[0].ebook and books[0].audio


def test_a_record_that_names_the_title_of_another_volume_does_not_join_the_two() -> None:
    """A library names the uniform title of the series (the one of volume 1) for volume 2: volume 2 keeps its own."""
    found = hits(lantern(1, "Die Laternenstraße", "The Lantern Road")
                 + lantern(2, "Das Salztor", "The Salt Gate", original="The Lantern Road"), "lantern saga 2")
    books = {hit.title: hit for hit in found if hit.label == "work"}
    assert set(books) == {"Das Salztor", "Die Laternenstraße"}
    assert {entry.key for entry in books["Das Salztor"].work.entries} == {"d2", "a2", "u2", "OL2W"}
    assert found[0].title == "Das Salztor"


def test_the_same_title_at_two_places_of_a_series_is_one_book_numbered_otherwise() -> None:
    found = hits([entry("dnb", "1", "Krabbe", series="Krabbe", pos=1), entry("audible", "2", "Krabbe", form="audio",
                                                                           series="Krabbe", pos=3)], "Krabbe")
    assert len([hit for hit in found if hit.label == "work"]) == 1


def test_one_title_by_two_authors_is_two_hits() -> None:
    found = hits([entry("openlibrary", "1", "Die Brücke", authors=("Klara Wendt",), weight=60),
                  entry("dnb", "2", "Die Brücke", authors=("Wendt, Klara",)),
                  entry("openlibrary", "3", "Die Brücke", authors=("Paul Ostrander",), weight=1)], "Die Brücke")
    assert [(hit.title, hit.authors) for hit in found] == [("Die Brücke", ["Klara Wendt"]),
                                                          ("Die Brücke", ["Paul Ostrander"])]


def test_a_book_about_the_book_of_another_author_is_a_study_guide() -> None:
    found = hits([entry("openlibrary", "1", "Das Salztor", weight=50), entry("dnb", "2", "Das Salztor"),
                  entry("dnb", "3", "Raum und Figur in Ada Sorrels Das Salztor", authors=("Lena Brück",))], "Salztor")
    assert [(hit.title, hit.label) for hit in found] == [("Das Salztor", "work"),
                                                        ("Raum und Figur in Ada Sorrels Das Salztor", "guide")]


def test_a_title_that_names_two_books_of_the_author_is_a_box() -> None:
    found = hits([entry("dnb", "1", "Das Salztor"), entry("dnb", "2", "Die Laternenstraße"),
                  entry("dnb", "3", "Die Laternenstraße / Das Salztor")], "Salztor")
    assert {hit.title: hit.label for hit in found}["Die Laternenstraße / Das Salztor"] == "box"


def test_a_translation_into_a_language_nobody_fetches_in_is_no_hit_but_an_original_is() -> None:
    found = hits([entry("dnb", "1", "Das Salztor", original_title="The Salt Gate"),
                  entry("audible", "2", "Le portail de sel", language="fr", form="audio")], "Salztor portail")
    assert [hit.title for hit in found] == ["Das Salztor"]
    phare = hits([entry("openlibrary", "1", "La Maison du Phare", language="fr", authors=("Odile Ferrand",))],
                 "Maison du Phare", original=False)
    assert [(hit.title, hit.allowed, hit.group, hit.languages) for hit in phare] == [
        ("La Maison du Phare", False, "elsewhere", ["fr"])]


def test_the_title_is_the_one_of_the_first_language_fetched_in() -> None:
    records = lantern(1, "Die Laternenstraße", "The Lantern Road")
    assert hits(records, "Lantern Road", ("de", "en"))[0].title == "Die Laternenstraße"
    records = lantern(1, "Die Laternenstraße", "The Lantern Road")
    assert hits(records, "Lantern Road", ("en", "de"))[0].title == "The Lantern Road"


def test_an_edition_of_a_catalogue_named_like_a_series_of_several_volumes_joins_nothing() -> None:
    records = lantern(1, "Die Laternen-Saga", "The Lantern Road") + lantern(2, "Das Salztor", "The Salt Gate")
    records.append(entry("openlibrary", "OL2W:de", "Die Laternen-Saga", work_key="OL2W"))
    found = hits(records, "lantern saga 2")
    salt = next(hit for hit in found if hit.work.series_pos == 2 and hit.label == "work")
    assert "OL2W:de" not in {item.key for item in salt.work.entries}


# ---- The order ------------------------------------------------------------------------------------------------------


def test_the_volume_asked_for_comes_first_then_the_other_volumes_then_the_rest() -> None:
    records = (lantern(1, "Die Laternenstraße", "The Lantern Road") + lantern(2, "Das Salztor", "The Salt Gate")
               + lantern(3, "Die letzte Glut", "The Last Ember")
               + [entry("openlibrary", "x", "Lantern Saga Notes 2", authors=("Theo Brand",), weight=3)])
    found = hits(records, "lantern saga 2")
    assert [(hit.title, hit.group) for hit in found] == [
        ("Das Salztor", "match"), ("Die Laternenstraße", "series"), ("Die letzte Glut", "series"),
        ("Lantern Saga Notes 2", "more")]


def test_the_series_without_a_volume_starts_with_its_first_volume_unless_a_book_has_that_title() -> None:
    records = lantern(1, "Die Laternenstraße", "The Lantern Road") + lantern(2, "Das Salztor", "The Salt Gate")
    for item in records:
        item.weight = 500 if item.series_pos == 2 else item.weight
    assert hits(records, "Laternen Saga")[0].title == "Die Laternenstraße"


def test_what_is_set_apart_comes_after_the_books_unless_it_is_asked_for() -> None:
    records = [entry("dnb", "1", "Das Salztor"), entry("openlibrary", "2", "Das Salztor", weight=40),
               entry("audible", "3", "Das Salztor", form="audio", subtitle="Hörspiel")]
    found = hits(records, "Das Salztor")
    assert [(hit.label, hit.group) for hit in found] == [("work", "match"), ("drama", "related")]
    found = hits(records, "Das Salztor Hörspiel")
    assert found[0].label == "drama"


def test_a_hit_that_holds_too_little_of_the_words_is_left_out() -> None:
    found = hits([entry("dnb", "1", "Das Salztor"), entry("dnb", "2", "Der Wald", authors=("Paul Ostrander",))],
                 "Salztor Laterne")
    assert [hit.title for hit in found] == ["Das Salztor"]


def test_a_short_title_in_two_languages_is_one_book() -> None:
    """``Krabbe`` in German and in English: too short to join books of many authors by itself, but the author of a
    search is one already."""
    found = hits([entry("dnb", "1", "Krabbe", original_title="Krabbe"),
                  entry("openlibrary", "OL9W", "Krabbe", language="en", work_level=True, work_key="OL9W", weight=20)],
                 "Krabbe")
    assert [(hit.title, sorted(hit.languages)) for hit in found] == [("Krabbe", ["de", "en"])]


def test_a_title_no_record_carries_comes_after_the_ones_they_carry() -> None:
    """A library names the uniform title of the series as the original of volume 2: in English the book has no title
    of its own then, and the German one is shown, also when English comes first."""
    records = [entry("dnb", "d2", "Das Salztor", series="Die Laternen-Saga", pos=2),
               entry("audible", "a2", "Das Salztor", form="audio", series="Die Laternen-Saga", pos=2)]
    records[0].original_title, records[0].original_language = "The Lantern Road", "en"
    assert hits(records, "Salztor", ("en", "de"))[0].title == "Das Salztor"
