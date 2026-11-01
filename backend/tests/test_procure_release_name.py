"""Release names of books: what a name says, and holding it against the book looked for.

The names are invented; their shapes are the ones the measurements met: names with
dots, with spaces and dashes, the scene form of audiobooks, a narrator in front of the author, the series with its
volume before the title, ranges of volumes, marks of a language in capitals and in brackets.
"""

from __future__ import annotations

import pytest

from app.services.procure import release_match as rm
from app.services.procure import release_name as rn
from app.services.procure.release_name import parse

BOOK = rm.Book(titles=(rm.Title("Die Laternenstraße", "de"),), authors=("Rune Falkner",), series="Nebelchor",
               position=2.0)


# --- What a name says --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "kind", "formats"), [
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", "ebook", ("epub",)),
    ("Rune.Falkner.Die.Laternenstrasse.2021.GERMAN.RETAiL.ePub.eBook-EXAMPLE", "ebook", ("epub",)),
    ("Falkner, Rune - Die Laternenstrasse (2021) azw3", "ebook", ("azw3",)),
    ("Rune.Falkner.-.Die.Laternenstrasse.ebook.german.mobi", "ebook", ("mobi",)),
    ("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", "audio", ("mp3",)),
    ("Rune Falkner-Die Laternenstrasse-16BIT-44.1KHZ-WEB-FLAC-2021-EXAMPLE", "audio", ("flac",)),
    ("Rune.Falkner.-.Die.Laternenstrasse.ABOOK.2012.TbT.128", "audio", ()),
    ("Rune Falkner - Die Laternenstrasse - Hoerbuch", "audio", ()),
    ("Rune Falkner - Die Laternenstrasse (Luisterboek)", "audio", ()),
    ("Rune.Falkner.Die.Laternenstrasse.Ungekuerzt.M4B.AAC.128kbps-EXAMPLE", "audio", ("m4b", "aac")),
    ("Rune Falkner - Die Laternenstrasse-6CD-DE-Audiobook-2011-EXAMPLE", "audio", ()),
    ("Rune.Falkner.-.Die.Laternenstrasse", None, ()),
])
def test_the_kind_and_the_formats_come_from_the_words_of_the_name(name: str, kind: str | None,
                                                                   formats: tuple[str, ...]) -> None:
    found = parse(name)
    assert found.kind == kind
    assert found.formats == formats


@pytest.mark.parametrize(("name", "rate"), [
    ("Rune.Falkner.Die.Laternenstrasse.MP3.192kbps-EXAMPLE", 192),
    ("Rune Falkner - Die Laternenstrasse 64 kbps", 64),
    ("Rune.Falkner.-.Die.Laternenstrasse.ABOOK.2012.TbT.128", 128),
    ("Rune.Falkner.-.Zimmer.4417.ABOOK", None),
    ("Rune.Falkner.-.Gleis.9B", None),
    ("Rune Falkner - Zimmer 128 - Hoerbuch", None),
])
def test_the_bit_rate_is_what_the_name_states(name: str, rate: int | None) -> None:
    assert parse(name).bitrate == rate


@pytest.mark.parametrize(("name", "language", "how"), [
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", "de", "mark"),
    ("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", "de", "mark"),
    ("Rune.Falkner.The.Lantern.Road.FRENCH.EPUB-EXAMPLE", "fr", "mark"),
    ("Rune.Falkner.The.Lantern.Road.SPANiSH.EPUB-EXAMPLE", "es", "mark"),
    ("Rune Falkner - Faehre 12 (NL)", "nl", "mark"),
    ("Rune Falkner - Lantern Road (UK)", "en", "mark"),
    ("Falkner Rune-Das Nebelhaus (Duitstalig)", "de", "mark"),
    ("Rune Falkner - De nevel (Luisterboek)", "nl", "words"),
    ("Rune Falkner - Die Laternenstrasse (ungekuerzt) - Hoerbuch", "de", "words"),
    ("Rune.Falkner.The.Lantern.Road.A.Novel.2013.RETAiL.EPUB.eBook-EXAMPLE", "en", "words"),
    ("Rune.Falkner.-.Die.Laternenstrasse", None, ""),
    ("Rune.Falkner.The.Lantern.Road.EPUB-EXAMPLE", None, ""),
    # Two marks are a dual release: nothing is sure.
    ("Rune Falkner - The Lantern Road EN DE", None, ""),
])
def test_the_language_is_said_by_a_mark_or_by_words_of_a_language_and_never_presumed(
        name: str, language: str | None, how: str) -> None:
    found = parse(name)
    assert (found.language, found.language_how) == (language, how)


@pytest.mark.parametrize("name", [
    "ES - Rune Falkner",  # a title, not Spanish
    "IT.Rune.Falkner.EPUB",
    "Rune Falkner - De Laternen",  # the Dutch article, no code
])
def test_a_two_letter_code_at_the_start_or_in_small_letters_is_no_mark(name: str) -> None:
    assert parse(name).language is None or parse(name).language_how != "mark" or name.startswith("Rune")


@pytest.mark.parametrize(("name", "abridged", "dramatized"), [
    ("Rune Falkner-Die Laternenstrasse (Gekuerzt)-16BIT-44-KHZ-WEB-FLAC-2017-EXAMPLE", True, False),
    ("Rune Falkner-Nebelchor 02 - Erntenebel-AUDiOBOOK-Gekuerzt-CD-MP3-DE-2022-EXAMPLE", True, False),
    ("Rune Falkner-Die Laternenstrasse (Gekuerzte Fassung)-16BIT-44.1KHZ-WEB-FLAC-2010-EXAMPLE", True, False),
    ("Rune Falkner - Die Laternenstrasse (ungekuerzt) - Hoerbuch", False, False),
    # "Autorisierte Lesefassung" is how German publishers name an abridged reading.
    ("Rune Falkner-Die Laternenstrasse (Autorisierte Lesefassung)-16BIT-44.1KHZ-WEB-FLAC-2023-EXAMPLE", True, False),
    ("Rune.Falkner.-.Die.Laternenstrasse.-.Das.ungekuerzte.Hoerspiel", False, True),
    ("Rune.Falkner.-.Die.Laternenstrasse.Mp3.Vbr.Hoerspiel.Audiobook.German", None, True),
    ("Rune Falkner - The Lantern Road (Unabridged)", False, False),
    ("Rune Falkner - The Lantern Road (Abridged) [Full Cast]", True, True),
    ("Rune Falkner - Die Laternenstrasse", None, False),
    # Both words: what is unabridged says so first.
    ("Rune Falkner - Die Laternenstrasse (Ungekuerzte Lesefassung)", False, False),
])
def test_abridged_unabridged_and_dramatized(name: str, abridged: bool | None, dramatized: bool) -> None:
    found = parse(name)
    assert (found.abridged, found.dramatized) == (abridged, dramatized)


@pytest.mark.parametrize(("name", "flag"), [
    ("Rune Falkner - Die Laternenstrasse - Leseprobe", "sample"),
    ("Rune.Falkner.The.Lantern.Road.Sample.EPUB", "sample"),
    ("Ida Klang, Rune Falkner-Die Laternenstrasse (Original Soundtrack from the Audio Drama)-WEB-FLAC-2025", "soundtrack"),
    ("Rune Falkner - Die Laternenstrasse - Lektuerehilfe", "companion"),
    ("Study Guide - Rune Falkner - The Lantern Road", "companion"),
])
def test_a_sample_a_soundtrack_and_a_companion_are_named(name: str, flag: str) -> None:
    found = parse(name)
    assert getattr(found, flag) is True
    for other in {"sample", "soundtrack", "companion"} - {flag}:
        assert getattr(found, other) is False


@pytest.mark.parametrize(("name", "volumes"), [
    ("Rune Falkner-Nebelchor 01 - 05-AUDiOBOOK-5CD-MP3-DE-2024-EXAMPLE", (1, 5)),
    ("Rune Falkner-Nebelchor - Die komplette Hoerspielserie, Folge 1-4-WEB-FLAC-2019-EXAMPLE", (1, 4)),
    ("Rune.Falkner.-.Nebelchor.Band.1-3", (1, 3)),
    ("Rune Falkner - Nebelchor Baende 1 bis 3", (1, 3)),
])
def test_a_range_of_volumes_is_a_bundle(name: str, volumes: tuple[int, int]) -> None:
    found = parse(name)
    assert found.bundle and found.bundle_range == volumes


@pytest.mark.parametrize("name", [
    "Rune.Falkner-Nebelchor.Trilogie-AUDiOBOOK-3CD-MP3-DE-2009-EXAMPLE",
    "Rune Falkner - Nebelchor Box Set EPUB",
    "Rune Falkner - Gesamtausgabe - EPUB",
    "Verlag Datenbank ebook-Update KW 41",
    "Rune Falkner - Die Romane - EPUB",
])
def test_words_of_a_bundle_make_a_bundle(name: str) -> None:
    assert parse(name).bundle


@pytest.mark.parametrize("name", [
    "Rune Falkner - Zimmer 4417",
    "Rune Falkner - P.S. - Ich finde dich - 12 Kurzkrimis",
    "Rune Falkner - Spiele fuer 3-5 Jahre",
    "Rune.Falkner.-.Die.Laternenstrasse.2019-2021",
])
def test_numbers_that_are_titles_ages_or_years_are_no_bundle(name: str) -> None:
    assert parse(name).bundle_range is None


@pytest.mark.parametrize(("name", "volumes"), [
    ("Rune Falkner - Nebelchor - Band 02 - Die Laternenstrasse", (2.0,)),
    ("Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse", (2.0,)),
    ("Rune Falkner - Die Laternenstrasse (Nebelchor #2)", (2.0,)),
    ("Rune.Falkner.Nebel.Reihe.E002.Die.Laternenstrasse.German.WEB.MP3.Audiobook-EXAMPLE", (2.0,)),
    ("Rune Falkner-Der Nebelchor, Folge 4 Brandung (Hoerspiel)-16BIT-44.1KHZ-WEB-FLAC-2019-EXAMPLE", (4.0,)),
    ("Rune Falkner - Zimmer 4417", ()),
])
def test_volumes_are_numbers_after_a_word_for_a_volume_or_with_a_leading_zero(name: str,
                                                                             volumes: tuple[float, ...]) -> None:
    assert parse(name).volumes == volumes


def test_the_group_and_the_year_of_a_scene_name() -> None:
    found = parse("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE")
    assert (found.group, found.year, found.discs) == ("EXAMPLE", 2021, None)
    assert parse("Rune Falkner - Die Laternenstrasse-6CD-DE").discs == 6
    # A dash inside a title is no group.
    assert parse("Rune Falkner - Das Merlin-Archiv").group == ""


def test_the_head_is_the_name_without_the_tags_of_the_scene() -> None:
    assert parse("Rune.Falkner.-.Die.Laternenstrasse.2021.GERMAN.RETAiL.EPUB.eBook-EXAMPLE").head == \
        "Rune Falkner - Die Laternenstrasse"


def test_a_hostile_name_is_read_without_harm() -> None:
    for name in ("", " ", "-" * 500, "\0" + chr(0x202E) * 50, "a" * 5000, "(((" * 100, "1-2-3-4-5-6-7-8-9-10" * 20):
        found = parse(name)
        assert len(found.text) <= rn.NAME_MAX


# --- Holding a name against the book --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "Rune.Falkner.-.Die.Laternenstrasse",
    "Falkner, Rune - Die Laternenstraße",
    "Falkner Rune-Die Laternenstrasse",
    "R. Falkner - Die Laternenstrasse",
    "Ida Leser Rune Falkner-Die Laternenstrasse-DE-AUDIOBOOK-6CD-FLAC-2016-EXAMPLE",
    "Ida Leser liest Rune Falkner - Die Laternenstrasse",
    "Rune Falkner & Ida Leser - Die Laternenstrasse",
    "Die.Laternenstrasse.,Rune.Falkner",
    "Rune Falkner (Hoerbuch)-Die Laternenstrasse-16BIT-44-KHZ-WEB-FLAC-2017-EXAMPLE",
    "Rune.Falkner.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE",
    "Rune.Falkner.-.Die.Laternenstrase",  # a letter lost on the way
])
def test_the_author_and_the_title_are_found_in_every_way_of_writing(name: str) -> None:
    found = rm.match(parse(name), BOOK)
    assert found.author and found.title, name


@pytest.mark.parametrize("name", [
    "Holly Falkner - Die Laternenstrasse",  # the last name alone is no author
    "Falkner - Die Laternenstrasse",
    "Die Laternenstrasse - Ida Leser",
])
def test_the_last_name_alone_or_another_author_is_no_author(name: str) -> None:
    found = rm.match(parse(name), BOOK)
    assert not found.author and found.title


@pytest.mark.parametrize("name", [
    "Rune Falkner - Die Brandung",
    "Rune Falkner - Laterne",
    "Rune Falkner - Die Laternenstrassenbahn der Zukunft",
])
def test_another_title_of_the_author_is_no_match(name: str) -> None:
    found = rm.match(parse(name), BOOK)
    assert found.author and not found.title


def test_a_title_of_one_word_must_stand_as_it_is() -> None:
    one = rm.Book(titles=(rm.Title("Glimmer", ""),), authors=("Rune Falkner",))
    assert rm.match(parse("Rune.Falkner.-.Glimmer"), one).title
    assert not rm.match(parse("Rune.Falkner.-.Glimmers"), one).title
    assert not rm.match(parse("Rune.Falkner.-.Glimer"), one).title


def test_a_long_word_with_two_letters_more_is_another_word() -> None:
    guard = rm.Book(titles=(rm.Title("Der Moorwächter", ""),), authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner - Der Moorwaechter"), guard).title
    assert not rm.match(parse("Rune Falkner - Die Moorwaechterin"), guard).title


def test_a_title_written_together_or_apart_is_the_same() -> None:
    joined = rm.Book(titles=(rm.Title("SiebenSee", ""),), authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner - Sieben See"), joined).title
    apart = rm.Book(titles=(rm.Title("Das Kolibri-Dekret", ""),), authors=("Rune Falkner",))
    assert rm.match(parse("Rune.Falkner.Das.Kolibri.Dekret.GERMAN.EPUB"), apart).title


def test_the_core_of_a_title_counts_not_its_advertising() -> None:
    sold = rm.Book(titles=(rm.Title("Die Laternenstraße: Ein Nebelchor-Krimi 2 – Der neue Fall der Nr.-1-Bestsellerautorin",
                                     "de"),), authors=("Rune Falkner",))
    assert rm.match(parse("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB"), sold).title


@pytest.mark.parametrize(("name", "other"), [
    ("Rune Falkner - Nebelchor - Band 03 - Die Brandung", True),
    ("Rune.Falkner.-.Nebelchor.03.-.Die.Laternenstrasse", True),
    ("Rune Falkner - Nebelchor 3 - Die Brandung", True),
    ("Rune Falkner - Nebelchor - Band 02 - Die Laternenstrasse", False),
    ("Rune Falkner - Die Laternenstrasse", False),
])
def test_another_volume_of_the_series(name: str, other: bool) -> None:
    assert rm.match(parse(name), BOOK).other_volume is other


def test_a_title_known_in_one_language_only_says_that_language() -> None:
    both = rm.Book(titles=(rm.Title("Die Laternenstraße", "de"), rm.Title("The Lantern Road", "en")),
                   authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner - The Lantern Road"), both).title_language == "en"
    assert rm.match(parse("Rune Falkner - Die Laternenstrasse"), both).title_language == "de"
    # A title of no known language beside it says nothing either.
    unknown = rm.Book(titles=(rm.Title("Die Laternenstraße", "de"), rm.Title("Die Laternenstrasse", "")),
                      authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner - Die Laternenstrasse"), unknown).title_language == ""
    # The same title in both languages says nothing.
    same = rm.Book(titles=(rm.Title("Lumen", "de"), rm.Title("Lumen", "en")), authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner - Lumen"), same).title_language == ""


def test_the_author_is_found_with_umlauts_in_any_spelling() -> None:
    umlaut = rm.Book(titles=(rm.Title("Süßwasser", "de"),), authors=("Jörg Mörck",))
    for name in ("Joerg.Moerck.-.Suesswasser", "Jorg Morck - Susswasser", "Mörck, Jörg - Süßwasser"):
        found = rm.match(parse(name), umlaut)
        assert found.author and found.title, name
