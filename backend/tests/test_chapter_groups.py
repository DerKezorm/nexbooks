"""Which files of a recording are one chapter (``services/chapter_groups``): only when their titles prove it. A chapter put
together wrongly cannot be told from a right one, a chapter too many costs a tap."""

from __future__ import annotations

import pytest

from app.services.chapter_groups import group, label, read_title


def cut(*titles: str) -> list[tuple[int, str]]:
    """The chapters of a row of titles as ``(files, title)``."""
    return [(count, title) for _first, count, title in group(list(titles))]


def test_a_chapter_over_three_files_is_one_chapter_with_the_stem_as_its_title() -> None:
    assert cut("Kapitel 1 (1/3)", "Kapitel 1 (2/3)", "Kapitel 1 (3/3)", "Kapitel 2 (1/2)", "Kapitel 2 (2/2)",
               "Kapitel 3") == [(3, "Kapitel 1"), (2, "Kapitel 2"), (1, "Kapitel 3")]


def test_the_chapters_know_the_first_file_they_start_in() -> None:
    assert group(["Intro", "Kapitel 1 (1/2)", "Kapitel 1 (2/2)", "Kapitel 2"]) == [
        (0, 1, "Intro"), (1, 2, "Kapitel 1"), (3, 1, "Kapitel 2")]


@pytest.mark.parametrize("titles", [
    ("The Harbour (1/3)", "The Harbour (2/3)", "The Harbour (3/3)"),
    ("The Harbour [1/3]", "The Harbour [2/3]", "The Harbour [3/3]"),
    ("The Harbour 1/3", "The Harbour 2/3", "The Harbour 3/3"),
    ("The Harbour - 1/3", "The Harbour - 2/3", "The Harbour - 3/3"),
    ("The Harbour 1 of 3", "The Harbour 2 of 3", "The Harbour 3 of 3"),
    ("The Harbour (1 of 3)", "The Harbour (2 of 3)", "The Harbour (3 of 3)"),
    ("Der Hafen 1 von 3", "Der Hafen 2 von 3", "Der Hafen 3 von 3"),
    ("Der Hafen, 1 von 3", "Der Hafen, 2 von 3", "Der Hafen, 3 von 3"),
    ("The Harbour - Part 1", "The Harbour - Part 2", "The Harbour - Part 3"),
    ("The Harbour Part 1", "The Harbour Part 2", "The Harbour Part 3"),
    ("The Harbour (Part 1 of 3)", "The Harbour (Part 2 of 3)", "The Harbour (Part 3 of 3)"),
    ("The Harbour pt. 1", "The Harbour pt. 2", "The Harbour pt. 3"),
    ("The Harbour, Pt 1", "The Harbour, Pt 2", "The Harbour, Pt 3"),
    ("Der Hafen - Teil 1", "Der Hafen - Teil 2", "Der Hafen - Teil 3"),
    ("Der Hafen (Teil 1 von 3)", "Der Hafen (Teil 2 von 3)", "Der Hafen (Teil 3 von 3)"),
    ("The Harbour - Track 1", "The Harbour - Track 2", "The Harbour - Track 3"),
    ("THE HARBOUR (01/03)", "the harbour (02/03)", "The  Harbour (03/03)"),
    ("THE HARBOUR 1 OF 3", "THE HARBOUR 2 OF 3", "THE HARBOUR 3 OF 3"),
    ("DER HAFEN 1 VON 3", "DER HAFEN 2 VON 3", "DER HAFEN 3 VON 3"),
    ("THE HARBOUR - PART 1", "THE HARBOUR - PT. 2", "THE HARBOUR - TRACK 3"),
    ("DER HAFEN - TEIL 1", "DER HAFEN - TEIL 2", "DER HAFEN - TEIL 3"),
])
def test_the_marks_of_a_part_in_both_languages_and_every_spelling_make_one_chapter(titles: tuple[str, ...]) -> None:
    chapters = cut(*titles)
    assert len(chapters) == 1 and chapters[0][0] == 3
    assert chapters[0][1].lower().replace("  ", " ") in ("the harbour", "der hafen")


def test_spellings_may_change_inside_one_chapter_when_the_stem_and_the_count_hold() -> None:
    assert cut("Der Hafen (1/3)", "Der Hafen - Teil 2", "Der Hafen 3 von 3") == [(3, "Der Hafen")]


def test_the_same_title_is_one_chapter_whatever_the_case_and_the_white_space() -> None:
    assert cut("The Harbour", "the  harbour", "THE HARBOUR ", "The Light") == [(3, "The Harbour"), (1, "The Light")]
    assert cut("Kapitel 01", "Kapitel 1", "Kapitel 2") == [(2, "Kapitel 01"), (1, "Kapitel 2")]


def test_other_stems_are_never_put_together() -> None:
    assert cut("Kapitel 1 (1/2)", "Kapitel 2 (2/2)") == [(1, "Kapitel 1 (1/2)"), (1, "Kapitel 2 (2/2)")]
    assert cut("The Harbour (1/2)", "The Lighthouse (2/2)") == [(1, "The Harbour (1/2)"),
                                                                (1, "The Lighthouse (2/2)")]
    assert cut("The Harbour (1/2)", "The Harbour Road (2/2)") == [(1, "The Harbour (1/2)"),
                                                                  (1, "The Harbour Road (2/2)")]


def test_a_missing_or_jumping_number_ends_the_chapter() -> None:
    assert cut("A Tale (1/3)", "A Tale (3/3)") == [(1, "A Tale (1/3)"), (1, "A Tale (3/3)")]
    assert cut("A Tale (1/4)", "A Tale (2/4)", "A Tale (4/4)") == [(2, "A Tale"), (1, "A Tale (4/4)")]
    assert cut("A Tale - Part 2", "A Tale - Part 4") == [(1, "A Tale - Part 2"), (1, "A Tale - Part 4")]


def test_a_repeated_or_backward_number_is_not_one_chapter() -> None:
    # The same title on every file names no chapter: parts without a name (shown as "Teil 1", "Teil 2").
    assert cut("A Tale (1/3)", "A Tale (1/3)") == [(1, ""), (1, "")]
    assert cut("A Tale (2/3)", "A Tale (1/3)") == [(1, "A Tale (2/3)"), (1, "A Tale (1/3)")]
    assert cut("A Tale (1/3)", "A Tale (2/3)", "A Tale (1/3)") == [(2, "A Tale"), (1, "A Tale (1/3)")]


def test_a_total_that_is_written_must_be_the_same_everywhere() -> None:
    assert cut("A Tale (1/3)", "A Tale (2/4)") == [(1, "A Tale (1/3)"), (1, "A Tale (2/4)")]
    assert cut("A Tale (1/2)", "A Tale (2/2)", "A Tale - Part 3") == [(2, "A Tale"), (1, "A Tale - Part 3")]


def test_a_number_beyond_the_total_is_no_mark_of_a_part() -> None:
    assert read_title("A Tale (4/3)").marked is False
    assert read_title("A Tale (0/3)").marked is False
    assert cut("A Tale (4/3)", "A Tale (5/3)") == [(1, "A Tale (4/3)"), (1, "A Tale (5/3)")]


def test_a_mark_without_a_stem_or_with_a_stem_of_a_word_for_a_division_proves_nothing() -> None:
    # Chapter one of three, chapter two of three: chapters, not parts of "Kapitel".
    assert cut("Kapitel 1/3", "Kapitel 2/3", "Kapitel 3/3") == [(1, "Kapitel 1/3"), (1, "Kapitel 2/3"),
                                                                 (1, "Kapitel 3/3")]
    assert cut("Teil 1", "Teil 2") == [(1, "Teil 1"), (1, "Teil 2")]
    assert cut("Track 1", "Track 2", "Track 3") == [(1, "Track 1"), (1, "Track 2"), (1, "Track 3")]
    assert cut("Band 3 von 5", "Band 4 von 5") == [(1, "Band 3 von 5"), (1, "Band 4 von 5")]
    assert cut("1 of 3", "2 of 3") == [(1, "1 of 3"), (1, "2 of 3")]


def test_a_title_made_of_a_word_for_a_division_alone_is_no_proof_for_the_same_title() -> None:
    assert cut("Track", "Track") == [(1, ""), (1, "")]
    assert cut("Kapitel", "Kapitel") == [(1, ""), (1, "")]
    assert cut("Track", "Track", "Kapitel 3") == [(1, "Track"), (1, "Track"), (1, "Kapitel 3")]


def test_a_marked_title_and_an_unmarked_one_are_two_chapters() -> None:
    assert cut("Kapitel 1", "Kapitel 1 (2/2)") == [(1, "Kapitel 1"), (1, "Kapitel 1 (2/2)")]
    assert cut("Kapitel 1 (1/2)", "Kapitel 1") == [(1, "Kapitel 1 (1/2)"), (1, "Kapitel 1")]


def test_a_year_or_a_big_number_at_the_end_is_no_mark_of_a_part() -> None:
    assert cut("Summer 2023", "Summer 2024") == [(1, "Summer 2023"), (1, "Summer 2024")]
    assert cut("Chapter 12", "Chapter 13") == [(1, "Chapter 12"), (1, "Chapter 13")]
    assert read_title("Report 2023/2024").marked is False


def test_nothing_in_nothing_out() -> None:
    assert group([]) == []
    assert cut("Only one (1/1)") == [(1, "Only one (1/1)")]


def test_the_name_of_the_book_is_no_chapter_and_so_no_proof_of_one() -> None:
    names = ["Salt Marsh", "Ada Feldkamp/Salt Marsh"]
    # The title tag of every file holds the title of the book: nothing is put together, and no part has a name.
    assert [(c, t) for _f, c, t in group(["Salt Marsh", "salt  marsh", "Salt Marsh"], names)] == [
        (1, ""), (1, ""), (1, "")]
    # The parts of the book itself, numbered: parts, not a chapter.
    assert [(c, t) for _f, c, t in group(["Salt Marsh (1/3)", "Salt Marsh (2/3)", "Salt Marsh (3/3)"], names)] == [
        (1, "Salt Marsh (1/3)"), (1, "Salt Marsh (2/3)"), (1, "Salt Marsh (3/3)")]
    # Chapters of that book are still looked at.
    assert [(c, t) for _f, c, t in group(["Prolog", "Kapitel 1 (1/2)", "Kapitel 1 (2/2)"], names)] == [
        (1, "Prolog"), (2, "Kapitel 1")]


def test_titles_in_other_scripts_are_compared_by_their_words_not_by_their_numbers_alone() -> None:
    assert cut("Глава 1 (1/2)", "Глава 1 (2/2)", "Глава 2") == [(2, "Глава 1"), (1, "Глава 2")]
    assert cut("第一章 (1/2)", "第一章 (2/2)") == [(2, "第一章")]
    # Two other words with the same number are two chapters.
    assert cut("Глава 1 (1/2)", "Раздел 1 (2/2)") == [(1, "Глава 1 (1/2)"), (1, "Раздел 1 (2/2)")]
    assert cut("第一章 (1/2)", "第二章 (2/2)") == [(1, "第一章 (1/2)"), (1, "第二章 (2/2)")]
    assert cut("Глава", "Глава") == [(1, ""), (1, "")]


def test_a_stem_of_numbers_alone_proves_nothing() -> None:
    assert cut("1 (1/2)", "1 (2/2)") == [(1, "1 (1/2)"), (1, "1 (2/2)")]
    assert cut("12", "12") == [(1, ""), (1, "")]
    assert cut("", "") == [(1, ""), (1, "")]


def test_umlauts_and_case_are_the_same_words() -> None:
    assert cut("Ärger im Hafen (1/2)", "ärger IM hafen (2/2)") == [(2, "Ärger im Hafen")]
    assert cut("Ärger im Hafen (1/2)", "Arger im Hafen (2/2)") == [(1, "Ärger im Hafen (1/2)"),
                                                                    (1, "Arger im Hafen (2/2)")]


def test_a_chapter_without_a_name_is_called_a_part_in_the_language_of_the_book() -> None:
    assert label("", 3, "de") == "Teil 3"
    assert label("", 2, "fr-FR") == "Partie 2"
    assert label("", 1, "NL") == "Deel 1"
    # A language nexbooks has no word for, or none known: English.
    assert label("", 4, "") == "Part 4" and label("", 4, "xx") == "Part 4"
    # A chapter with a name keeps it.
    assert label("Prolog", 1, "de") == "Prolog"
