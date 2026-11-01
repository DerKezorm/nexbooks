"""Names the judgement read wrongly: a word of a language inside the title,
a boast that looks like a volume, figures of a recording and dates that look like a range of volumes, a title that goes
on, a sample in any place, a few shapes of bundles and discs, and the version of a book before its format.

The names are invented; their shapes are the ones the measurement of real names showed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.procure import formats, judge
from app.services.procure import release_match as rm
from app.services.procure.release_name import parse

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
MB = 1_000_000


def book(title: str, author: str = "Rune Falkner", language: str = "en", series: str = "",
         position: float | None = None) -> rm.Book:
    return rm.Book(titles=(rm.Title(title, language),), authors=(author,), series=series, position=position)


def verdict(name: str, wanted_book: rm.Book, *, language: str = "en", size: int = 2 * MB, **have: Any) -> judge.Verdict:
    parsed = parse(name)
    wanted = judge.Wanted(book=wanted_book, language=language, **have)
    offer = judge.Offer(title=name, size=size, published_at=NOW - timedelta(days=3), protocol="usenet")
    return judge.judge(parsed, offer, rm.match(parsed, wanted_book), wanted, formats.default_config(), NOW)


def codes(found: judge.Verdict) -> list[str]:
    return [item["code"] for item in found.reasons]


# --- A word of a language inside the title is no mark ----------------------------------------------------------------


@pytest.mark.parametrize(("title", "name"), [
    ("The Dutch Garden", "Rune.Falkner.-.The.Dutch.Garden.EPUB-EXAMPLE"),
    ("The Danish Ferry", "Rune Falkner - The Danish Ferry (2015) EPUB"),
    ("The French Cartographer", "Rune.Falkner.-.The.French.Cartographer.RETAiL.EPUB-EXAMPLE"),
    ("NO", "Rune Falkner - NO - EPUB"),
    ("Swedish Harbour", "Rune.Falkner.Swedish.Harbour.EPUB"),
])
def test_a_word_of_a_language_that_is_part_of_the_title_is_no_mark(title: str, name: str) -> None:
    found = verdict(name, book(title))
    assert found.passes, codes(found)
    assert found.language == "en"


@pytest.mark.parametrize(("title", "name", "language"), [
    # A mark beside the title is still a mark.
    ("The Dutch Garden", "Rune.Falkner.-.The.Dutch.Garden.DUTCH.EPUB-EXAMPLE", "nl"),
    ("The Dutch Garden", "Rune Falkner - The Dutch Garden (NL) EPUB", "nl"),
    ("NO", "Rune Falkner - NO - GERMAN EPUB", "de"),
    ("The Danish Ferry", "Rune Falkner - The Danish Ferry FRENCH EPUB", "fr"),
])
def test_a_mark_beside_the_title_stays_a_mark(title: str, name: str, language: str) -> None:
    found = verdict(name, book(title))
    assert (found.language, found.language_how) == (language, "mark")
    assert codes(found) == ["other_language"]


def test_the_language_of_a_name_without_the_book_is_unchanged() -> None:
    # parse() alone cannot know the title: it reads the mark as it stands, the judgement takes it back.
    assert parse("Rune.Falkner.-.The.Dutch.Garden.EPUB").language == "nl"


# --- A boast, a figure of a recording, a date: no volume, no range ---------------------------------------------------


@pytest.mark.parametrize("name", [
    "Rune Falkner - The Harbor Light (#1 New York Times bestselling author) EPUB",
    "Rune.Falkner.-.The.Harbor.Light.No.1.Bestseller.EPUB",
    "Rune Falkner - The Harbor Light (Nr. 1 Spiegel Bestseller) EPUB",
    "Rune Falkner Nr.1-Bestsellerautor The Harbor Light EPUB",
    "Rune.Falkner.-.#1.NYT.Best.Selling.Author.-.The.Harbor.Light.EPUB",
])
def test_a_boast_of_a_number_one_is_no_volume(name: str) -> None:
    found = parse(name)
    assert found.volumes == () and found.ad_numbers
    reading = verdict(name, book("The Harbor Light", series="Harbor Books", position=3.0))
    assert "other_volume" not in codes(reading), codes(reading)


def test_a_real_first_volume_is_still_a_volume() -> None:
    assert parse("Rune Falkner - Harbor Books #1 - The Harbor Light").volumes == (1.0,)
    assert parse("Rune Falkner - Harbor Books No. 1 - The Harbor Light").volumes == (1.0,)
    wanted = book("The Harbor Light", series="Harbor Books", position=3.0)
    assert "other_volume" in codes(verdict("Rune Falkner - Harbor Books #1 - The Harbor Light", wanted))


def test_the_boast_after_the_name_of_the_series_is_no_volume_either() -> None:
    wanted = book("The Harbor Light", series="Harbor Books", position=3.0)
    found = verdict("Rune.Falkner.-.Harbor.Books.#1.New.York.Times.Bestselling.-.The.Harbor.Light.EPUB", wanted)
    assert "other_volume" not in codes(found)


@pytest.mark.parametrize("name", [
    "Rune Falkner-Nebelchor 3-16BIT-44-KHZ-WEB-FLAC-2021-EXAMPLE",
    "Rune.Falkner.-.Nebelchor.5.16BIT.44.1KHZ.FLAC.German",
    "Rune Falkner - Nebelchor 2 320kbps MP3 Hoerbuch",
    "Rune Falkner - Nebelchor 1-128K MP3 Hoerbuch",
    "Rune Falkner - Nebelchor 3-48K Der Chor der Woelfe",
    "Rune Falkner - Nebelchor 4 - 44-KHZ - 16BIT",
])
def test_figures_of_a_recording_are_no_range_of_volumes(name: str) -> None:
    found = parse(name)
    assert found.bundle_range is None and not found.bundle, name


@pytest.mark.parametrize("name", [
    "Rune Falkner - Nebelchor (2021-03-14) EPUB",
    "Rune Falkner - Nebelchor 14-03-2021 EPUB",
    "Rune Falkner - Nebelchor ISBN 978-3-99-123456-4 EPUB",
    "Rune Falkner - Nebelchor 3-16-148410-0 EPUB",
    "Rune Falkner - Nebelchor 2021-03 EPUB",
    # Neither number of a chain of dashes is the end of a range: not the first (a dash and a digit follow) and not the
    # second (a digit and a dash come before).
    "Rune Falkner - Nebelchor 12-34-57 EPUB",
])
def test_a_date_or_an_isbn_in_a_name_is_no_range_of_volumes(name: str) -> None:
    found = parse(name)
    assert found.bundle_range is None and not found.bundle, name


@pytest.mark.parametrize("name", [
    "Rune Falkner - Nebelchor 03-14-2021 EPUB",
    "Rune Falkner - Nebelchor 03-14-2021 Der Chor der Woelfe",
    "Rune Falkner - Nebelchor 3-16BIT-44-KHZ Der Chor der Woelfe",
])
def test_the_number_of_a_date_or_a_figure_of_a_recording_is_no_volume(name: str) -> None:
    assert parse(name).volumes == (), name


@pytest.mark.parametrize(("name", "volumes"), [
    ("Rune Falkner - Nebelchor 1 - 3 - Hoerbuch", (1, 3)),
    ("Rune Falkner - Nebelchor 2 bis 4 EPUB", (2, 4)),
    ("Rune Falkner - Nebelchor 01-05 EPUB", (1, 5)),
    ("Rune Falkner - Nebelchor Band 1-3 EPUB", (1, 3)),
])
def test_a_range_of_volumes_is_still_a_bundle(name: str, volumes: tuple[int, int]) -> None:
    assert parse(name).bundle_range == volumes


@pytest.mark.parametrize("name", [
    "Rune Falkner - Nebelchor 3 - 12 Monate EPUB",
    "Rune Falkner - Sechs 2 - 4 Stunden EPUB",
    "Rune Falkner - Nebelchor 1 - 99 EPUB",
    "Rune Falkner - Nebelchor 1 - 40 EPUB",
])
def test_a_small_range_with_a_unit_or_a_large_one_without_words_is_no_bundle(name: str) -> None:
    assert parse(name).bundle_range is None


# --- A title that goes on ----------------------------------------------------------------------------------------------


RING = book("Der Ring", language="de")


@pytest.mark.parametrize("name", [
    "Rune.Falkner.-.Der.Ring.der.Fuenf.GERMAN.EPUB-EXAMPLE",
    "Rune Falkner - Der Ring der Fünf EPUB",
    "Rune Falkner - Der Ring und die Rose EPUB",
    "Rune Falkner - Der Ring Die Wanderung zum Meer EPUB",
])
def test_a_title_that_goes_on_into_more_words_is_another_book(name: str) -> None:
    found = rm.match(parse(name), RING)
    assert found.author and not found.title and found.longer, name
    assert "title_mismatch" in codes(verdict(name, RING, language="de"))


@pytest.mark.parametrize("name", [
    "Rune Falkner - Der Ring EPUB",
    "Rune.Falkner.-.Der.Ring.GERMAN.EPUB-EXAMPLE",
    "Rune Falkner - Der Ring - Thriller EPUB",
    "Rune Falkner - Der Ring (Ein Nebelchor-Krimi) EPUB",
    "Rune Falkner - Der Ring Roman GERMAN EPUB",
    "Rune.Falkner.-.Der.Ring.Ungekuerzt.Hoerbuch.MP3",
    "Rune Falkner - Der Ring, der Fünf EPUB",
    "Der Ring - Rune Falkner",
    # One word more, or two that do not begin with a joining word, are not enough to call it another book.
    "Rune.Falkner.-.Der.Ring.Sonderedition.GERMAN.EPUB",
    "Rune Falkner - Der Ring Neue Fassung EPUB",
])
def test_a_title_followed_by_a_subtitle_a_kind_a_tag_or_a_dash_is_the_book(name: str) -> None:
    found = rm.match(parse(name), RING)
    assert found.title and not found.longer, name


@pytest.mark.parametrize("name", [
    # The words are the name of a series with its volume after the title (a volume of a series whose name begins
    # like the title, with a title of its own after it, is another book: ``test_procure_title_whole``).
    "Rune Falkner - Der Ring der Wellen 01",
    # A word of the author after the title is the author, not more of the title.
    "Der Ring von Rune Falkner",
    "Rune.Falkner.-.Der.Ring.von.Rune.Falkner.Folge.2",
])
def test_words_after_the_title_that_are_a_series_or_the_author_are_no_more_of_a_title(name: str) -> None:
    found = rm.match(parse(name), RING)
    assert found.title and not found.longer, name


def test_a_subtitle_of_the_catalogue_is_known_in_both_spellings_of_an_umlaut() -> None:
    catalogue = rm.Book(titles=(rm.Title("Lumen 4: Der Chor der Wölfe", "de"),), authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner-Lumen 4 Der Chor der Woelfe-Audiobook-WEB-MP3-DE-2022"), catalogue).title
    other = rm.Book(titles=(rm.Title("Lumen", "de"),), authors=("Rune Falkner",))
    assert not rm.match(parse("Rune Falkner - Lumen der Weg ins Nichts EPUB"), other).title


def test_the_words_after_the_title_that_are_the_subtitle_of_the_catalogue_are_no_more_of_a_title() -> None:
    with_subtitle = rm.Book(titles=(rm.Title("Der Ring", "de"), rm.Title("Der Ring: Der Ring der Fünf", "de")),
                            authors=("Rune Falkner",))
    assert rm.match(parse("Rune Falkner - Der Ring der Fünf EPUB"), with_subtitle).title


# --- A sample in any place ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "Rune Falkner - Letzte Hilfe - Vorab Der Ring EPUB",
    "Vorab - Rune Falkner - Der Ring",
    "Rune.Falkner.-.Der.Ring.Vorabdruck.EPUB",
    "Rune.Falkner.-.Der.Ring.GERMAN.EPUB-LESEPROBE",
    "Rune Falkner - Der Ring (Excerpt)",
    "Rune Falkner - Sample - Der Ring",
    "Rune Falkner - Der Ring Leseprobe",
])
def test_a_sample_is_a_sample_wherever_the_word_stands(name: str) -> None:
    found = parse(name)
    assert found.sample, name
    assert "sample" in codes(verdict(name, RING, language="de"))


def test_a_word_that_only_begins_like_a_sample_word_is_none() -> None:
    assert not parse("Rune Falkner - Der Vorabend EPUB").sample
    assert not parse("Rune Falkner - Das Fragmentarium EPUB").sample


# --- Discs, ranges, spam ---------------------------------------------------------------------------------------------


def test_four_cds_with_a_space_say_an_audiobook() -> None:
    found = parse("Rune Falkner - Der Ring - 4 CDs")
    assert found.kind == "audio" and found.discs == 4
    assert parse("Rune Falkner - Der Ring - 4 CD").kind == "audio"


# --- The version of the book before its format --------------------------------------------------------------------------


AUDIO = book("Der Ring", language="de")
GOOD = {"unabridged": "Rune Falkner-Der Ring-Ungekuerzt-16BIT-44.1KHZ-WEB-FLAC-DE-2021-EXAMPLE",
        "unabridged_mp3": "Rune Falkner-Der Ring-Ungekuerzt-MP3-128kbps-DE-2021-EXAMPLE",
        "abridged": "Rune Falkner-Der Ring-Gekuerzt-16BIT-44.1KHZ-WEB-FLAC-DE-2021-EXAMPLE",
        "abridged_mp3": "Rune Falkner-Der Ring-Gekuerzt-MP3-128kbps-DE-2021-EXAMPLE",
        "dramatized": "Rune Falkner-Der Ring-Hoerspiel-16BIT-44.1KHZ-WEB-FLAC-DE-2021-EXAMPLE",
        "dramatized_mp3": "Rune Falkner-Der Ring-Hoerspiel-MP3-128kbps-DE-2021-EXAMPLE"}


def permissive() -> dict[str, Any]:
    config = formats.default_config()
    config["abridged"] = True
    config["dramatized"] = True
    config["variant"] = "prefer"
    return config


def not_better(release: str, held: dict[str, str]) -> str | None:
    """The format named by ``not_better`` when the release is turned down for it, else None."""
    parsed = parse(release)
    wanted = judge.Wanted(book=AUDIO, language="de", have_by={"audio": held})
    offer = judge.Offer(title=release, size=400 * MB, published_at=NOW - timedelta(days=3), protocol="usenet")
    found = judge.judge(parsed, offer, rm.match(parsed, AUDIO), wanted, permissive(), NOW)
    assert found.kind == "audio"
    for item in found.reasons:
        if item["code"] == "not_better":
            return str(item["arg"])
    assert found.passes, codes(found)
    return None


# Matrix: (what is there, the release) -> turned down as "not better" or let through. FLAC is the goal of the
# ranking; "mp3_128" is below it.
@pytest.mark.parametrize(("held", "release", "expected"), [
    # An unabridged reading takes the place of an abridged one or a dramatisation at any format, even at the goal.
    ({"abridged": "flac"}, "unabridged_mp3", None),
    ({"dramatized": "flac"}, "unabridged_mp3", None),
    ({"abridged": "flac", "dramatized": "flac"}, "unabridged", None),
    ({"abridged": "mp3_128"}, "unabridged_mp3", None),
    # A fuller version is never replaced by a lesser one, whatever the format.
    ({"unabridged": "mp3_128"}, "abridged", "mp3_128"),
    ({"unabridged": "mp3_128"}, "dramatized", "mp3_128"),
    ({"dramatized": "mp3_128"}, "abridged", "mp3_128"),
    ({"unabridged": "flac"}, "abridged_mp3", "flac"),
    # The same version is weighed by format against the goal.
    ({"unabridged": "mp3_128"}, "unabridged", None),
    ({"unabridged": "flac"}, "unabridged", "flac"),
    ({"unabridged": "flac"}, "unabridged_mp3", "flac"),
    ({"unabridged": "mp3_128"}, "unabridged_mp3", "mp3_128"),
    ({"abridged": "mp3_128"}, "abridged", None),
    ({"abridged": "flac"}, "abridged", "flac"),
    ({"abridged": "mp3_128"}, "abridged_mp3", "mp3_128"),
    ({"dramatized": "mp3_128"}, "dramatized", None),
    # A dramatisation is fuller than an abridged reading and takes its place.
    ({"abridged": "flac"}, "dramatized_mp3", None),
    # Nothing there: every release is a candidate.
    ({}, "abridged", None),
    ({}, "unabridged_mp3", None),
])
def test_the_version_of_a_book_comes_before_its_format(held: dict[str, str], release: str, expected: str | None,
                                                       ) -> None:
    assert not_better(GOOD[release], held) == expected


def test_nothing_better_is_looked_for_from_the_goal_on() -> None:
    config = permissive()
    config["target"] = {**config["target"], "audio": "mp3_128"}
    parsed = parse(GOOD["unabridged"])
    wanted = judge.Wanted(book=AUDIO, language="de", have_by={"audio": {"unabridged": "mp3_128"}})
    offer = judge.Offer(title=GOOD["unabridged"], size=400 * MB, published_at=NOW - timedelta(days=3),
                        protocol="usenet")
    found = judge.judge(parsed, offer, rm.match(parsed, AUDIO), wanted, config, NOW)
    # FLAC is better than the MP3 there is, but the MP3 is the goal: the book is done.
    assert found.reasons == [{"code": "not_better", "arg": "mp3_128"}]
    config["target"]["audio"] = "flac"
    assert judge.judge(parsed, offer, rm.match(parsed, AUDIO), wanted, config, NOW).passes


def test_an_old_single_format_for_a_kind_still_counts_as_the_fullest_version() -> None:
    wanted = judge.Wanted(book=AUDIO, language="de", have={"audio": "flac"})
    assert wanted.held("audio") == {"unabridged": "flac"}
    assert judge.Wanted(book=AUDIO, language="de", have={"ebook": "epub"}).held("ebook") == {"": "epub"}


# --- Dramatisation is no unabridged reading; the words of the reasons per setting ----------------------------------


def ranked(names: list[str], cfg: dict[str, Any]) -> list[judge.Verdict]:
    found = []
    for name in names:
        parsed = parse(name)
        wanted = judge.Wanted(book=AUDIO, language="de")
        offer = judge.Offer(title=name, size=400 * MB, published_at=NOW - timedelta(days=3), protocol="usenet")
        found.append(judge.judge(parsed, offer, rm.match(parsed, AUDIO), wanted, cfg, NOW))
    judge.rank(found)
    return found


def test_a_dramatisation_does_not_keep_an_abridged_reading_out() -> None:
    dramatized, abridged = ranked([GOOD["dramatized"], GOOD["abridged"]], permissive())
    assert dramatized.passes and abridged.passes
    assert (dramatized.rank, abridged.rank) == (1, 2)


def test_an_unabridged_reading_keeps_an_abridged_one_out_and_says_why() -> None:
    unabridged, abridged = ranked([GOOD["unabridged_mp3"], GOOD["abridged"]], permissive())
    assert unabridged.rank == 1 and abridged.rank is None
    assert abridged.reasons == [{"code": "abridged", "arg": "alternative"}]


def test_an_abridged_reading_the_settings_do_not_take_says_so() -> None:
    config = formats.default_config()
    assert config["abridged"] is False
    (abridged,) = ranked([GOOD["abridged"]], config)
    assert abridged.reasons == [{"code": "abridged", "arg": "settings"}]
    only = permissive()
    only["variant"] = "unabridged"
    (still,) = ranked([GOOD["abridged"]], only)
    assert still.reasons == [{"code": "abridged", "arg": "settings"}]


# --- Names of files that are no books; the size an audiobook may have ----------------------------------------------------


@pytest.mark.parametrize("name", ["Rune Falkner - Nebelchor.nfo", "Rune.Falkner.-.Nebelchor.NFO", "x.jpg", "Rune Falkner.txt"])
def test_the_name_of_a_file_that_is_no_book_is_turned_down(name: str) -> None:
    assert parse(name).not_a_book
    # Whatever the category or the size says: 473 MB with no hint of a kind was an audiobook, and passed.
    found = verdict(name, book("Nebelchor"), size=473 * MB)
    assert "not_a_book" in codes(found), codes(found)
    assert found.reasons[0]["code"] == "not_a_book" or "blocklisted" in codes(found)


@pytest.mark.parametrize("name", [
    "Rune.Falkner.-.Nebelchor.READNFO.EPUB-EXAMPLE",
    "Rune.Falkner.-.Nebelchor.NFO.EPUB-EXAMPLE",
    "Rune Falkner - Nebelchor Dr. No. EPUB",
    "Rune Falkner - Nebelchor.EPUB",
])
def test_a_tag_or_a_word_that_only_looks_like_a_file_name_is_no_reason(name: str) -> None:
    assert not parse(name).not_a_book


def test_a_long_flac_audiobook_fits_the_size_the_operator_starts_with() -> None:
    # Fourteen hours as FLAC is some 5.6 GB; the first rounds turned it down as too large.
    name = "Rune Falkner-Der Ring-Ungekuerzt-16BIT-44.1KHZ-WEB-FLAC-DE-2021-EXAMPLE"
    found = verdict(name, book("Der Ring", language="de"), language="de", size=5_600 * MB)
    assert found.passes and found.format == "flac", codes(found)
    # Forty hours of it still fit; a hundred gigabytes do not.
    assert verdict(name, book("Der Ring", language="de"), language="de", size=16_000 * MB).passes
    assert "too_large" in codes(verdict(name, book("Der Ring", language="de"), language="de", size=100_000 * MB))
