"""One list of the words that say a book is a play, a comic, a study guide, a companion, a box or a part, for every place
that reads a title: the names of releases, the titles of files and the search of the catalogues. A word added to the list
reaches all of them, and a word one of them knew alone is known to the others. All titles are invented."""

from __future__ import annotations

import pytest

from app.services import book_words
from app.services.booksearch import words as search_words
from app.services.procure import release_name
from app.services.readers.text import fold

#: What the search reads: the folded title. What a release is read by: the folded text with a space on both sides.


def search_has(pattern: object, text: str) -> bool:
    return bool(pattern.search(fold(text)))  # type: ignore[attr-defined]


def release_has(pattern: object, text: str) -> bool:
    return bool(pattern.search(f" {fold(text)} "))  # type: ignore[attr-defined]


#: Every word of the shared pieces, written the way a title carries it, with the place that reads it on each side.
PLAY = ["Hörspiel", "Das Nebelhaus - Krimihörspiel", "Hoerspiel", "dramatisiert", "Dramatised", "Dramatisation",
        "full cast", "Ensemble Cast", "Multicast", "GraphicAudio", "Audio Theatre", "Audio Drama", "radio play",
        "Bühnenfassung", "Radiofassung", "in voller Besetzung", "Filmhörspiel"]
COMIC = ["Der Leuchtturm Comic", "Comics", "Comicroman", "Comicfassung", "Graphic Novel", "Manga", "bande dessinée",
         "fumetto"]
STUDY = ["Lektürehilfe", "Lektuerehilfe", "Lektüreschlüssel", "Interpretation", "Interpretationshilfe", "Textanalyse",
         "Erläuterungen", "Zusammenfassung", "Summary of", "Analysis of", "Study Guide", "Reading Guide", "SparkNotes",
         "CliffsNotes", "Workbook", "Arbeitsheft", "Unterrichtsmaterial", "Abiturwissen"]
COMPANION = ["Begleitbuch", "Fanbuch", "Making of", "Das Buch zum Film", "Filmbuch"]
BOX = ["Boxset", "Box Set", "Boxed Set", "Sammelband", "Sammelbox", "Gesamtausgabe", "Omnibus"]


@pytest.mark.parametrize("title", PLAY)
def test_a_play_is_a_play_to_the_release_and_to_the_search(title: str) -> None:
    assert release_has(book_words.DRAMATIZED, title), title
    assert search_has(search_words.DRAMA, title), title


@pytest.mark.parametrize("title", COMIC)
def test_a_comic_is_a_comic_to_the_release_and_to_the_search(title: str) -> None:
    assert book_words.adaptation_word(title), title
    assert search_has(search_words.COMIC, title), title


@pytest.mark.parametrize("title", STUDY)
def test_a_book_to_study_by_is_one_to_the_release_and_to_the_search(title: str) -> None:
    assert book_words.companion_word(title), title
    assert search_has(search_words.GUIDE, title), title


@pytest.mark.parametrize("title", ["Begleitbuch", "Fanbuch", "Making of"])
def test_a_book_around_the_book_is_one_to_the_release_and_to_the_search(title: str) -> None:
    assert book_words.companion_word(title), title
    assert search_has(search_words.COMPANION, title), title


@pytest.mark.parametrize("title", ["Das Buch zum Film", "Filmbuch"])
def test_the_book_of_a_film_is_one_to_the_release_and_to_the_search(title: str) -> None:
    assert book_words.edition_word(title), title
    assert search_has(search_words.COMPANION, title), title


def test_an_edition_for_school_is_an_edition_to_the_release_and_a_guide_to_the_search() -> None:
    assert book_words.edition_word("Die Laternenstraße Schullektüre")
    assert search_has(search_words.GUIDE, "Die Laternenstraße Schullektüre")


@pytest.mark.parametrize("title", BOX)
def test_a_box_is_a_box_to_the_release_and_to_the_search(title: str) -> None:
    assert release_has(release_name._BUNDLE_WORDS, title), title
    assert search_has(search_words.BOX, title), title


@pytest.mark.parametrize("title", ["Teil 2 von 3", "Part 1 of 3", "Deel 2 van 3", "Teil 1 aus 3"])
def test_a_part_with_the_number_of_parts_is_a_part_to_the_release_and_to_the_search(title: str) -> None:
    assert book_words.sure_part(title) is not None, title
    assert search_has(search_words.PART, title), title


def test_what_only_the_search_needs_stays_with_the_search() -> None:
    # A calendar or a leaflet is a thing about the book to a search of catalogues, and no word of the names of releases.
    assert search_has(search_words.COMPANION, "Wandkalender zum Buch")
    assert not book_words.companion_word("Wandkalender zum Buch")
    assert search_has(search_words.PART, "Partie 1 de 3")
    assert book_words.sure_part("Partie 1 de 3") is None


def test_what_only_a_release_needs_stays_with_the_release() -> None:
    assert book_words.companion_word("Königs Erläuterungen") and book_words.companion_word("Abitur")
    assert not search_has(search_words.GUIDE, "Abitur")
    assert release_has(release_name._BUNDLE_WORDS, "Megapack")
    assert not search_has(search_words.BOX, "Megapack")


def test_a_bare_part_at_the_end_a_new_edition_and_novels_bound_together_are_named() -> None:
    assert book_words.bare_part("Der tiefe Hof 1/2") == (1, 2)
    assert book_words.bare_part("Der Hof (2 von 3)") == (2, 3)
    assert book_words.bare_part("1 von 2") == (1, 2)
    # (Not a part: a date, a fraction in the middle, more than twelve parts, the second number before the first.)
    for text in ("24/7", "Station 11/22", "Die 1/2 Wahrheit", "Der Hof 1/20", "Der Hof 3/2", "Der Hof 1/1"):
        assert book_words.bare_part(text) is None, text
    assert book_words.reissue_word("Der Rostgarten. Neuausgabe") and book_words.reissue_word("Jubiläumsausgabe")
    assert book_words.reissue_word("The Rust Garden: Anniversary Edition")
    assert not book_words.reissue_word("Die neue Ausgabe des Lebens")
    assert book_words.more_bundle_word("Zwei Romane in einem Band") and book_words.more_bundle_word("Three Novels in One")
    assert not book_words.more_bundle_word("Zwei Romane")


@pytest.mark.parametrize("name", ["COMIC_WORDS", "PLAY_WORDS", "STUDY_WORDS", "COMPANION_WORDS", "FILM_BOOK_WORDS",
                                  "SCHOOL_WORDS", "BOX_WORDS", "REISSUE_WORDS", "PART_UNITS", "PART_LINKS"])
def test_every_piece_is_built_into_a_pattern_that_compiles(name: str) -> None:
    import re

    pieces = getattr(book_words, name)
    assert pieces and all(isinstance(piece, str) and piece for piece in pieces)
    re.compile(book_words.pattern(pieces))
