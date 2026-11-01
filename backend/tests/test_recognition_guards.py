"""The guards of the recognition against what a shop or a copy adds to a book: advertising in a title, a word for a part
taken for a series, a folder of a series taken for an author, a release note taken for a description, two volumes of
one series with the same core, a catalogue alone for an e-book, a language that was only worked out, umlauts. Pure
rules, invented books and names throughout."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.match.score import SURE, Wanted, author_state, decide, score
from app.services.readers.names import (
    core_title,
    folder_book,
    is_generic_series,
    parse_name,
    series_from_field,
    split_prefix_series,
    split_series_title,
    split_suffix_series,
    strip_blurb,
    title_is_author,
    top_folder_author,
)
from app.services.readers.text import key_fold, release_text
from app.services.sources.model import Candidate

AUTHOR = "Lena Vorlage"
BLURB_TITLE = ("Das stille Moor – Ein Fall für Hanna: Thriller | Auftakt der neuen Cold-Case-Serie von "
               f"Nr.1-Bestsellerautor {AUTHOR} (German Edition)")


def want(**kw: Any) -> Wanted:
    base: dict[str, Any] = {"kind": "ebook", "title": "Das tiefe Wasser", "authors": [AUTHOR], "language": "de"}
    return Wanted(**{**base, **kw})


def cand(source: str = "dnb", **kw: Any) -> Candidate:
    base: dict[str, Any] = {"source": source, "key": kw.pop("key", "D1"), "title": "Das tiefe Wasser",
                            "authors": [AUTHOR], "languages": ["de"]}
    return Candidate(**{**base, **kw})


# ---- Advertising is no series, no volume, no title ----------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    (BLURB_TITLE, "Das stille Moor – Ein Fall für Hanna: Thriller"),
    (f"Psychothriller | Nervenkitzel pur von Nr.1-Bestsellerautor {AUTHOR}", "Psychothriller"),
    (f"Das Haus: von Nr.1-Bestsellerautor {AUTHOR}", "Das Haus"),
    (f"Das Haus vom Nr. 1 - Bestsellerautorin {AUTHOR}", "Das Haus"),
    (f"The Dark Water from the #1-bestselling author {AUTHOR}", "The Dark Water"),
    (f"The Dark Water, the No.1-bestselling author {AUTHOR}", "The Dark Water"),
    (f"La Maison: par l'auteur du bestseller {AUTHOR}", "La Maison"),
    (f"Spannender Auftakt von NEW-YORK-TIMES-Bestsellerautorin {AUTHOR}", "Spannender Auftakt"),
    ("Das Haus (German Edition)", "Das Haus"),
    ("Der Nr.1-Bestsellerautor aus dem Norden", ""),
    ("Nr. 1 SPIEGEL-Bestseller", ""),
    ("SPIEGEL-Bestsellerautor", ""),
    # A title that only *is* the word stays a title.
    ("Der Bestseller", "Der Bestseller"),
    ("Das Haus", "Das Haus"),
    ("Lantern 451: A Novel", "Lantern 451: A Novel"),
])
def test_what_a_shop_adds_to_sell_a_book_is_cut_off(text: str, expected: str) -> None:
    assert strip_blurb(text) == expected


@pytest.mark.parametrize("title", [
    BLURB_TITLE,
    f"Das Haus | Die Serie von Nr.1-Bestsellerautor {AUTHOR}",
    f"Das Haus – vom Nr. 1 - Bestsellerautorin {AUTHOR}",
    f"Das Haus – Band 1: {AUTHOR} erzählt",
    f"Das Haus Book 1 - {AUTHOR}",
    f"The House: from the #1-bestselling author {AUTHOR}",
    f"The House: the No.1-bestselling author {AUTHOR}",
])
def test_a_clause_of_advertising_is_no_series_with_a_volume(title: str) -> None:
    parts = split_series_title(title, authors=[AUTHOR])
    assert parts.series == "" and parts.pos is None, parts
    assert not title_is_author(parts.title, [AUTHOR])
    assert "bestseller" not in core_title(title).casefold()


def test_a_real_series_in_front_of_a_title_is_still_one() -> None:
    parts = split_series_title("Anselm Quist Bd. 5 - Ahnenland | Thriller")
    assert (parts.series, parts.pos, parts.title) == ("Anselm Quist", 5.0, "Ahnenland")
    assert split_series_title("Die Flut Teil 1 - Das Ende").series == "Die Flut"


@pytest.mark.parametrize("title", [
    f"Bestsellerautor {AUTHOR} (German Edition)", AUTHOR, f"Nr.1-Bestsellerautorin {AUTHOR}", "L. Vorlage",
])
def test_a_title_that_is_only_the_name_of_the_author_is_no_title(title: str) -> None:
    assert title_is_author(title, [AUTHOR])
    # Nothing a book of that "title" is compared with fits it.
    assert score(want(title=title), cand(title=f"Ein anderes Buch von {AUTHOR}")).fatal == "title_differs"


def test_the_subtitle_of_advertising_of_a_source_makes_no_title_of_an_authors_name() -> None:
    """A case seen on a real library, with invented names: the file's title broken by advertising, the source's record of
    another book of the author with advertising in its subtitle. Neither is a series, and the two are not one book."""
    wanted = want(title=BLURB_TITLE)
    other = cand(title="Das tiefe Wasser", subtitle=f"Psychothriller | Nervenkitzel pur von Nr.1-Bestsellerautor {AUTHOR}",
                 isbns=["9783000000017"], year=2021)
    scored = score(wanted, other)
    assert scored.fatal == "title_differs", scored
    assert decide(wanted, [other]).state == "open"


# ---- An e-book is sure only by its ISBN or by two sources ---------------------------------------------------


def test_an_ebook_known_to_one_catalogue_alone_without_an_isbn_is_a_proposal() -> None:
    decision = decide(want(), [cand(year=2021)])
    assert decision.state == "maybe" and decision.reason == "one_source"
    assert decision.best is not None and decision.best.score >= SURE


def test_an_ebook_two_sources_tell_or_its_own_isbn_proves_is_sure() -> None:
    assert decide(want(), [cand(), cand("audible", key="B0EXAMPL01")]).state == "sure"
    assert decide(want(isbn="9783000000017"), [cand(isbns=["9783000000017"])]).state == "sure"


def test_a_recording_keeps_its_own_rule() -> None:
    recording = want(kind="audio", duration=600 * 60)
    decision = decide(recording, [cand("audible", key="B0EXAMPL01", runtime_min=600)])
    assert decision.state == "sure"


# ---- The same core, another subtitle --------------------------------------------------------------------------


VOLUMES = ("Mörderfinder – Die Macht des Täters", "Mörderfinder – Mit den Augen des Opfers",
           "Mörderfinder – Der Ruf der Toten")


def test_another_subtitle_is_another_volume_of_the_series() -> None:
    wanted = want(title=VOLUMES[0], authors=["Kai Ostermann"])
    neighbour = cand(title=VOLUMES[1], authors=["Kai Ostermann"], year=2021)
    scored = score(wanted, neighbour)
    assert "subtitle_differs" in scored.against and scored.capped
    decision = decide(wanted, [neighbour, cand("audible", key="B0EXAMPL01", title=VOLUMES[1],
                                               authors=["Kai Ostermann"])])
    assert decision.state == "maybe" and decision.reason == "subtitle_differs"


def test_two_neighbours_of_one_source_are_two_books_and_none_is_sure() -> None:
    wanted = want(kind="audio", title="Mörderfinder", authors=["Kai Ostermann"], duration=600 * 60)
    first = cand("audible", key="B0EXAMPL01", title=VOLUMES[1], authors=["Kai Ostermann"], runtime_min=480)
    second = cand("audible", key="B0EXAMPL02", title=VOLUMES[2], authors=["Kai Ostermann"], runtime_min=470)
    decision = decide(wanted, [first, second, cand("dnb", key="D2", title=VOLUMES[2], authors=["Kai Ostermann"])])
    assert decision.state != "sure"


def test_the_volume_or_the_running_time_says_it_is_this_book_after_all() -> None:
    wanted = want(title=VOLUMES[0], authors=["Kai Ostermann"], series="Mörderfinder", series_pos=1)
    same = cand(title="Mörderfinder – Die Macht des Täters, Band eins", authors=["Kai Ostermann"], series="Mörderfinder",
                series_pos=1)
    assert "subtitle_differs" not in score(wanted, same).against
    recording = want(kind="audio", title=VOLUMES[0], authors=["Kai Ostermann"], duration=600 * 60)
    proven = cand("audible", key="B0EXAMPL01", title="Mörderfinder – Die Macht des Täters (ungekürzt)",
                  authors=["Kai Ostermann"], runtime_min=600)
    assert decide(recording, [proven]).state == "sure"


@pytest.mark.parametrize("subtitle", ["Roman", "Psychothriller", "Ein Bretagne-Krimi", "Ein Fall für Hanna",
                                      "Kommissar Dupin 3", f"Thriller | von Nr.1-Bestsellerautor {AUTHOR}"])
def test_a_subtitle_that_names_only_the_kind_or_the_series_is_no_other_book(subtitle: str) -> None:
    wanted = want(title="Das tiefe Wasser: Ein Fall für Hanna")
    other = cand(title="Das tiefe Wasser", subtitle=subtitle)
    assert "subtitle_differs" not in score(wanted, other).against


# ---- A word for a part is never a series -----------------------------------------------------------------------


@pytest.mark.parametrize(("name", "pos"), [("Titel - Teil 2", 2.0), ("Titel - Band 3", 3.0), ("Titel - Book 2", 2.0),
                                           ("Titel - Folge 4", 4.0), ("Titel - CD 2", None), ("Titel - Disc 2", None),
                                           ("Titel - Kapitel 3", None)])
def test_a_folder_named_with_a_word_for_a_part_has_no_series(name: str, pos: float | None) -> None:
    found = folder_book(name, depth=2)
    assert (found.title, found.series, found.pos) == ("Titel", "", pos)


@pytest.mark.parametrize("name", ["Teil", "Band", "CD", "Disc", "Folge", "Part", "Book", "Volume", "Tome", "Teil 2"])
def test_a_word_for_a_part_is_a_generic_series(name: str) -> None:
    assert is_generic_series(name)
    assert series_from_field(name)[0] == ""


def test_a_name_with_a_word_for_a_part_in_front_gives_no_series() -> None:
    assert parse_name("Teil 2 - Das Ende").series == ""
    assert split_prefix_series("Band 03 - Das Ende") is None
    assert split_suffix_series("Das Ende (Teil 2)") is None
    assert not is_generic_series("Anselm Quist")


@pytest.mark.parametrize("name", ["01-ada_feldkamp-kapitel_1_-_die_nebelwacht", "03-ada_feldkamp-track_12_-_nebelwacht",
                                  "101-ida_brenner_ada_feldkamp-cd_2_-_nebelwacht"])
def test_the_name_of_a_track_with_its_number_and_a_word_for_a_part_gives_no_series(name: str) -> None:
    assert parse_name(name).series == ""


@pytest.mark.parametrize("name", ["01 Ada Feldkamp Kapitel", "3-ida brenner-track", "12 Nebelwacht CD"])
def test_a_number_in_front_and_a_word_for_a_part_at_the_end_is_a_generic_series(name: str) -> None:
    assert is_generic_series(name)


@pytest.mark.parametrize("name", ["Anselm Quist", "Die 3 Kapitel", "Kapitel der Nacht", "2 Kommissare"])
def test_a_series_with_a_number_or_a_word_for_a_part_somewhere_is_still_one(name: str) -> None:
    assert not is_generic_series(name)


# ---- The top folder is an author only when it looks like one, in doubt nobody ---------------------------------


@pytest.mark.parametrize("path", ["Star Wars/Der Turm/01.mp3", "Spiegel Bestseller/Der Turm/01.mp3",
                                  "Reihe Eins/1.-.Anfang/Audio/01.mp3", "Nebelhaus Saga/Der Turm/01.mp3",
                                  "Beispiel Studios/Der Turm/01.mp3", "Neue Bücher/Der Turm/01.mp3"])
def test_a_folder_of_a_series_a_box_a_shelf_or_a_studio_is_no_author(path: str) -> None:
    assert top_folder_author(path) == ""


def test_a_folder_named_like_a_person_is_the_author() -> None:
    assert top_folder_author("Mara Lindqvist/Der Turm/01.mp3") == "Mara Lindqvist"
    assert top_folder_author("Lindqvist, Mara/02 - Der Turm/01.mp3") == "Mara Lindqvist"


# ---- A release note is no description ----------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Upped by TeamXY - enjoy and support the author - encoded with LAME 3.99",
    "ripped by somebody", "Encoded with LAME 3.100", "www.example.com", "Download at https://example.com/x",
])
def test_the_note_of_whoever_copied_the_files_is_no_description(text: str) -> None:
    assert release_text(text) and release_text(text, strict=True)


def test_a_short_line_without_a_sentence_is_no_description_in_a_comment_but_a_real_text_is() -> None:
    assert release_text("TeamXY 2019", strict=True)
    assert release_text("Kapitel 1", strict=True)
    real = "Ein Mann verschwindet spurlos. Kommissarin Lindqvist ermittelt in einem Fall, der sie an ihre Grenzen bringt."
    assert not release_text(real, strict=True) and not release_text(real)


# ---- A language that was only worked out weighs less ------------------------------------------------------------


def test_a_worked_out_language_is_no_reason_against_a_book_and_weighs_half() -> None:
    english = cand("audible", key="B0EXAMPL01", languages=["en"], runtime_min=10)
    guessed = want(kind="audio", duration=600, language_guess=True)
    assert score(guessed, english).fatal == ""
    said = want(kind="audio", duration=600)
    assert score(said, english).fatal == "language_differs"
    german = cand("audible", key="B0EXAMPL02", languages=["de"], runtime_min=10)
    assert score(said, german).score - score(guessed, german).score == 5


# ---- An umlaut is its spelling with two letters, and nothing else is ------------------------------------------


@pytest.mark.parametrize(("one", "other"), [("Michael", "Michal"), ("Neue Welt", "Neu Welt"), ("Joel", "Jol"),
                                            ("Duell", "Dull"), ("Manuel", "Manul"), ("Noel", "Nol")])
def test_two_different_names_never_have_one_key(one: str, other: str) -> None:
    assert key_fold(one) != key_fold(other)


def test_a_catalogue_that_drops_the_dots_of_an_umlaut_still_names_the_author() -> None:
    assert author_state(["Mara Müller"], ["Mara Muller"]) == "match"
    assert key_fold("Müller") == key_fold("Mueller")


def test_a_name_in_the_field_of_the_author_that_is_the_title_of_the_folder_is_no_author() -> None:
    """A file whose fields are swapped: the title names the author, the author field the title, and the folder says
    which is which. The author is the folder's, the title the folder's."""
    from types import SimpleNamespace

    from app.services.match.run import wanted_from

    edition = SimpleNamespace(kind="ebook", title="Mara Lindqvist", subtitle="", language="de", isbn="", asin="",
                              series_name="", series_pos=None, duration=0, original_title="", overlay=None)
    wanted = wanted_from(edition, ["Wilde Lupinen"], "Mara Lindqvist/Wilde Lupinen (1999)/Wilde Lupinen - x.pdf")
    assert wanted.authors == ["Mara Lindqvist"] and wanted.alt_titles == ["Wilde Lupinen"]
    found = [cand(title="Wilde Lupinen", authors=["Mara Lindqvist"]),
             cand("audible", key="B0EXAMPL01", title="Wilde Lupinen", authors=["Mara Lindqvist"])]
    assert decide(wanted, found).state == "sure"


def test_the_series_is_never_read_out_of_the_subtitle_of_a_source() -> None:
    """``Das Haus`` with the subtitle ``Band 1 - Der Anfang`` is a record of ``Das Haus``; read together, title and
    subtitle would make a series ``Das Haus`` and a book ``Der Anfang``, another book of the author."""
    scored = score(want(title="Der Anfang"), cand(title="Das Haus", subtitle="Band 1 - Der Anfang"))
    assert scored.fatal == "title_differs"


def test_two_records_with_the_same_core_and_other_subtitles_never_confirm_each_other() -> None:
    from app.services.match.score import titles_alike

    one = cand(title=VOLUMES[1], authors=["Kai Ostermann"])
    other = cand("audible", key="B0EXAMPL02", title=VOLUMES[2], authors=["Kai Ostermann"])
    assert not titles_alike(one, other)
    assert titles_alike(one, cand("audible", key="B0EXAMPL03", title=VOLUMES[1], authors=["Kai Ostermann"]))
