"""How the names of authors and the titles of books are read: several authors in one field, the same person written
twice, the core of a title without what a shop adds to it, the kind of book at the end of a title, the year in the name of
a folder, the author a path names. All invented names."""

from __future__ import annotations

import pytest

from app.services.readers.names import (
    authors_in_path,
    clean_authors,
    compare_key,
    core_title,
    dedupe_words,
    split_authors,
    split_form_suffix,
    titles_alike,
    year_in_path,
)
from app.services.readers.text import date_of, valid_year, year_of

# ---- Authors -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "expected"), [
    # Two people with a bar between them, one of them written twice (the shape Calibre exports).
    ("Falkner, Rune | Lindqvist, Mara, Mara Lindqvist", ["Rune Falkner", "Mara Lindqvist"]),
    ("Falkner, Rune; Lindqvist, Mara", ["Rune Falkner", "Mara Lindqvist"]),
    ("Rune Falkner & Mara Lindqvist", ["Rune Falkner", "Mara Lindqvist"]),
    ("Rune Falkner und Mara Lindqvist", ["Rune Falkner", "Mara Lindqvist"]),
    ("Rune Falkner and Mara Lindqvist", ["Rune Falkner", "Mara Lindqvist"]),
    ("Falkner, Rune", ["Rune Falkner"]),
    ("Rune Falkner, Mara Lindqvist, Ben Hollister", ["Rune Falkner", "Mara Lindqvist", "Ben Hollister"]),
    ("Falkner, Rune, Lindqvist, Mara", ["Rune Falkner", "Mara Lindqvist"]),
    ("van Dorn, Mia", ["Mia van Dorn"]),
    (["Rune Falkner", "Falkner, Rune"], ["Rune Falkner"]),
    ("R. Falkner / Rune Falkner", ["Rune Falkner"]),
    ("Unknown", []),
])
def test_several_authors_in_one_field_are_several_people_and_one_person_written_twice_is_one(
        field: object, expected: list[str]) -> None:
    assert split_authors(field) == expected


def test_a_first_name_a_careless_record_wrote_twice_is_one_word() -> None:
    assert dedupe_words("Ulf Ulf Svensson") == "Ulf Svensson"
    assert split_authors("Ulf Ulf Svensson") == ["Ulf Svensson"]
    # A double word as the last name is a name: only a repeated word that is not the last one goes.
    assert dedupe_words("Mara Bora Bora") == "Mara Bora Bora"


def test_the_same_author_as_initials_and_in_full_is_the_longer_form() -> None:
    assert clean_authors(["R. Falkner", "Rune Falkner"]) == ["Rune Falkner"]
    assert clean_authors(["Rune Falkner", "Falkner"]) == ["Rune Falkner"]
    assert clean_authors(["Rune Falkner", "Ralf Falkner"]) == ["Rune Falkner", "Ralf Falkner"]


# ---- The core of a title -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "core"), [
    ("Die unbeliebte Dame (Ein Kommissar-Wolff-Krimi 1): Ein Mord, ein Selbstmord", "Die unbeliebte Dame"),
    ("Der Beobachter - Kriminalroman", "Der Beobachter"),
    ("Der Beobachter – Thriller", "Der Beobachter"),
    ("The Quiet Orchard (Unabridged)", "The Quiet Orchard"),
    ("The Quiet Orchard [German Edition]", "The Quiet Orchard"),
    ("The Quiet Orchard: A Novel", "The Quiet Orchard"),
    ("Der stille Obstgarten Roman", "Der stille Obstgarten"),
    ("Der stille Obstgarten ungekürzt", "Der stille Obstgarten"),
    ("Das Haus Roman", "Das Haus"),
    ("Der Fall spannender Thriller", "Der Fall"),
    # What is part of the title stays.
    ("Der Thriller", "Der Thriller"),
    ("Thriller", "Thriller"),
    ("Mein Thriller", "Mein Thriller"),
    ("Moorland 2", "Moorland 2"),
    # A title that would shrink to nothing is left as it is.
    ("(Un)Gleich", "(Un)Gleich"),
])
def test_the_core_of_a_title_is_what_comes_before_the_addition(title: str, core: str) -> None:
    assert core_title(title) == core


def test_a_title_loses_by_author_at_its_end_only_when_the_author_is_the_one_named() -> None:
    assert core_title("The Quiet Orchard by Mara Lindqvist", "Mara Lindqvist") == "The Quiet Orchard"
    assert core_title("Wait by Me", "Mara Lindqvist") == "Wait by Me"


def test_titles_are_alike_by_their_core_without_the_article_and_at_least_nine_tenths_of_the_letters() -> None:
    assert titles_alike("Der Beobachter - Kriminalroman", "Beobachter")
    assert titles_alike("Das Gezeitenbuch", "Gezeitenbuch")
    assert titles_alike("The Quiet Orchard", "Quiet Orchard (Unabridged)")
    assert titles_alike("Der stille Obstgarten", "Der stille Obstgarten Roman")
    # Nine tenths of the letters, not the words.
    assert titles_alike("Das Gezeitenbuch der Insel", "Das Gezeitenbuch der Inseln")
    assert not titles_alike("Der stille Obstgarten", "Der laute Obstgarten")
    # A volume that differs is another book however alike the letters.
    assert not titles_alike("Moorland 2", "Moorland 3")
    assert compare_key("Der Beobachter") == compare_key("Beobachter")


# ---- The kind of book at the end of a title ------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "head", "form"), [
    ("Der Beobachter - Kriminalroman", "Der Beobachter", "Kriminalroman"),
    ("Ahnenland | Thriller", "Ahnenland", "Thriller"),
    ("Ahnenland: Ein Thriller", "Ahnenland", "Ein Thriller"),
    ("Ahnenland (Roman)", "Ahnenland", "Roman"),
    ("Lantern 451: A Novel", "Lantern 451", "A Novel"),
    ("Der Beobachter - Historischer Roman", "Der Beobachter", "Historischer Roman"),
])
def test_the_kind_of_book_at_the_end_of_a_title_is_taken_off(title: str, head: str, form: str) -> None:
    assert split_form_suffix(title) == (head, form)


@pytest.mark.parametrize("title", [
    "Der Beobachter", "Der Beobachter - Die Rueckkehr", "Das Haus: Eine Geschichte der Wissenschaft",
    "Der Beobachter (Band 2)", "Roman", "Thriller",
])
def test_an_addition_that_is_a_subtitle_stays_in_the_title(title: str) -> None:
    assert split_form_suffix(title) == (title, "")


# ---- A path ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "year"), [
    ("Mara Lindqvist/Auris (2019)/Auris.epub", 2019),
    ("Mara Lindqvist/Auris/Auris (2022).epub", 2022),
    ("Mara Lindqvist/Auris (2019)/Auris (2022).epub", 2022),
    ("Mara Lindqvist/Auris/Auris.epub", None),
    ("Mara Lindqvist/Auris (0101)/Auris.epub", None),
    ("Mara Lindqvist/Auris (3019)/Auris.epub", None),
])
def test_the_year_a_name_puts_in_brackets_is_found_in_the_file_name_first(path: str, year: int | None) -> None:
    assert year_in_path(path) == year


@pytest.mark.parametrize(("path", "authors"), [
    ("Dan Example/Das verlorene Zeichen (2009)/Das verlorene Zeichen - Dan Example.pdf", ["Dan Example"]),
    ("Example, Dan/Titel/Titel.pdf", ["Dan Example"]),
    ("Dan Example - Titel.pdf", ["Dan Example"]),
    ("Titel - Dan Example.pdf", ["Dan Example"]),
    ("Titel.pdf", []),
    # A folder speaks only when it is written "Last, First", and only at the top of the library (a title, a series or a
    # shelf looks like a name too often).
    ("Alte Titel/Der Weg/Titel.pdf", []),
    ("Einzelbuecher/Alte Titel/Der Weg/Titel.pdf", []),
    ("Example, Dan/Der Weg/Titel.pdf", ["Dan Example"]),
    ("Einzelbuecher/Example, Dan/Der Weg/Titel.pdf", []),
    ("Dan Example/Titel.pdf", []),
])
def test_the_names_in_a_path_that_look_like_an_author_are_found(path: str, authors: list[str]) -> None:
    assert authors_in_path(path) == authors


# ---- Years -------------------------------------------------------------------------------------------------------------


def test_a_year_no_book_can_have_is_no_year() -> None:
    assert not valid_year(101) and not valid_year(1449) and valid_year(1450)
    assert valid_year(2030, this_year=2029) and not valid_year(2031, this_year=2029)
    assert not valid_year(None) and not valid_year(True)
    # Calibre's mark for a date it does not know.
    assert year_of("0101-01-01T00:00:00+00:00") is None
    assert year_of("2105") is None
    assert year_of("2003-02-24T23:00:00+00:00") == 2003
    assert date_of("0101-01-01") == "" and date_of("2017-05-24") == "2017-05-24" and date_of("2017") == "2017"
