"""The judgement of a release: the reasons as codes, the format by the operator's ranking, the language
sure or presumed, and the order of those that pass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.procure import formats, judge
from app.services.procure import release_match as rm
from app.services.procure.release_name import parse

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
BOOK = rm.Book(titles=(rm.Title("Die Laternenstraße", "de"), rm.Title("The Lantern Road", "en")),
               authors=("Rune Falkner",), series="Nebelchor", position=2.0)
MB = 1_000_000


def config(**over: Any) -> dict[str, Any]:
    found = formats.default_config()
    found.update(over)
    return found


def offer(name: str, **over: Any) -> judge.Offer:
    values: dict[str, Any] = {"title": name, "size": 2 * MB, "published_at": NOW - timedelta(days=3),
                              "protocol": "usenet"}
    values.update(over)
    return judge.Offer(**values)


def verdict(name: str, *, wanted: judge.Wanted | None = None, cfg: dict[str, Any] | None = None,
            **over: Any) -> judge.Verdict:
    parsed = parse(name)
    wanted = wanted or judge.Wanted(book=BOOK, language="de")
    return judge.judge(parsed, offer(name, **over), rm.match(parsed, wanted.book), wanted, cfg or config(), NOW)


def codes(found: judge.Verdict) -> list[str]:
    return [item["code"] for item in found.reasons]


def test_a_release_of_the_book_in_the_language_and_a_ticked_format_passes() -> None:
    found = verdict("Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE")
    assert found.passes and found.kind == "ebook" and found.format == "epub" and found.tier == 0
    assert (found.language, found.language_how, found.language_sure) == ("de", "mark", True)


@pytest.mark.parametrize(("name", "over", "code"), [
    ("Rune Falkner - Die Laternenstrasse - Leseprobe GERMAN EPUB", {}, "sample"),
    ("Ida Klang, Rune Falkner-Die Laternenstrasse (Original Soundtrack)-WEB-FLAC-2025", {"size": 300 * MB},
     "soundtrack"),
    ("Rune Falkner - Die Laternenstrasse - Lektuerehilfe GERMAN EPUB", {}, "companion"),
    ("Rune Falkner-Nebelchor 01 - 05-AUDiOBOOK-5CD-MP3-DE-2024-EXAMPLE", {"size": 2000 * MB}, "bundle"),
    ("Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.GERMAN.EPUB-EXAMPLE", {}, "other_volume"),
    ("Holly.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", {}, "author_mismatch"),
    ("Rune.Falkner.-.Die.Brandung.GERMAN.EPUB-EXAMPLE", {}, "title_mismatch"),
    ("Rune.Falkner.-.Die.Laternenstrasse.FRENCH.EPUB-EXAMPLE", {}, "other_language"),
    ("Rune Falkner-Die Laternenstrasse (Gekuerzt)-WEB-MP3-DE-2017-EXAMPLE", {"size": 300 * MB}, "abridged"),
    ("Rune.Falkner.-.Die.Laternenstrasse.-.Das.Hoerspiel.DE.MP3-EXAMPLE", {"size": 300 * MB}, "dramatized"),
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.PDF-EXAMPLE", {}, "format_not_wanted"),
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", {"size": 10_000}, "too_small"),
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", {"size": 900 * MB}, "too_large"),
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", {"protocol": "torrent", "seeders": 0},
     "not_enough_seeders"),
    ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", {"blocklisted": True}, "blocklisted"),
])
def test_each_reason_has_its_code(name: str, over: dict[str, Any], code: str) -> None:
    found = verdict(name, **over)
    assert code in codes(found), codes(found)
    assert not found.passes


def test_a_post_older_than_the_retention_is_turned_down() -> None:
    wanted = judge.Wanted(book=BOOK, language="de", retention_days=1000)
    old = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", wanted=wanted,
                  published_at=NOW - timedelta(days=1500))
    assert codes(old) == ["older_than_retention"]
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", wanted=wanted).passes
    # A torrent has no retention.
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", wanted=wanted, protocol="torrent",
                   seeders=5, published_at=NOW - timedelta(days=1500)).passes


def test_the_reasons_come_strongest_first() -> None:
    found = verdict("Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.FRENCH.PDF-EXAMPLE", blocklisted=True)
    assert codes(found) == ["blocklisted", "other_volume", "other_language", "format_not_wanted"]


UNSAID = rm.Book(titles=(rm.Title("Die Laternenstraße", ""),), authors=("Rune Falkner",), series="Nebelchor",
                position=2.0)


def test_a_presumed_language_is_never_a_reason() -> None:
    found = verdict("Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.EPUB-EXAMPLE",
                    wanted=judge.Wanted(book=UNSAID, language="de"))
    assert found.passes and (found.language, found.language_how, found.language_sure) == ("de", "presumed", False)


def test_a_title_known_only_in_another_language_is_a_reason_and_the_language_of_a_category_only_a_hint() -> None:
    by_title = verdict("Rune.Falkner.-.The.Lantern.Road.EPUB-EXAMPLE")
    assert codes(by_title) == ["other_language"] and by_title.language_how == "title"
    unsaid = judge.Wanted(book=UNSAID, language="de")
    # Indexers fill their categories with books of every language: a name that says nothing is not turned down for it.
    by_category = verdict("Rune.Falkner.-.Die.Laternenstrasse.EPUB-EXAMPLE", wanted=unsaid, category_language="fr")
    assert by_category.passes and (by_category.language, by_category.language_how) == ("fr", "category")
    assert not by_category.language_sure
    # A mark in the name outweighs the category, and so does a title of the language looked for.
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", wanted=unsaid,
                   category_language="fr").passes
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse.EPUB-EXAMPLE", category_language="fr").passes


def test_the_kind_comes_from_the_name_then_the_category_then_the_size() -> None:
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse", category_kind="audio", size=400 * MB).kind == "audio"
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse", size=400 * MB).kind == "audio"
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse", size=4 * MB).kind == "ebook"
    # A name that says Hörbuch is an audiobook even in an e-book category (the Luisterboek of the measurements).
    assert verdict("Rune Falkner - Die Laternenstrasse (Luisterboek)", category_kind="ebook",
                   size=400 * MB).kind == "audio"


@pytest.mark.parametrize(("name", "size", "expected", "how"), [
    ("Rune Falkner-Die Laternenstrasse-WEB-FLAC-2021-EXAMPLE", 2000, "flac", "name"),
    ("Rune.Falkner.Die.Laternenstrasse.M4B.AAC.128kbps-EXAMPLE", 600, "m4b_aac", "name"),
    ("Rune.Falkner.Die.Laternenstrasse.M4B.AAC.64kbps-EXAMPLE", 300, "mp3_128", "name"),
    ("Rune.Falkner.Die.Laternenstrasse.MP3.320kbps-EXAMPLE", 1200, "mp3_320", "name"),
    ("Rune.Falkner.Die.Laternenstrasse.MP3.192kbps-EXAMPLE", 700, "mp3_192", "name"),
    ("Rune.Falkner.Die.Laternenstrasse.MP3.64kbps-EXAMPLE", 200, "mp3_64", "name"),
    # 10 hours: 450 MB is 100 kbit/s, 1 GB some 222.
    ("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", 450, "mp3_128", "estimate"),
    # 288 MB is 64 kbit/s.
    ("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", 288, "mp3_64", "estimate"),
    ("Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE", 1000, "mp3_192", "estimate"),
    ("Rune Falkner - Die Laternenstrasse - Hoerbuch", 3000, "flac", "estimate"),
])
def test_the_format_of_an_audiobook_by_its_codec_and_its_rate(name: str, size: int, expected: str,
                                                               how: str) -> None:
    wanted = judge.Wanted(book=BOOK, language="de", duration=10 * 3600)
    found = verdict(name, wanted=wanted, size=size * MB, category_kind="audio")
    assert (found.format, found.format_how) == (expected, how)


def test_an_unknown_format_stands_at_the_last_ticked_place_and_is_no_reason() -> None:
    found = verdict("Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN")
    assert found.passes and found.format is None and found.format_how == "unknown"
    assert found.tier == 2  # MOBI: PDF is not ticked


def test_an_upgrade_must_be_better_than_what_there_is_and_stops_at_the_goal() -> None:
    have_mobi = judge.Wanted(book=BOOK, language="de", have={"ebook": "mobi"})
    assert verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB", wanted=have_mobi).passes
    assert codes(verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.MOBI", wanted=have_mobi)) == ["not_better"]
    assert codes(verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN", wanted=have_mobi)) == ["not_better"]
    # At the goal already: nothing is looked for.
    have_epub = judge.Wanted(book=BOOK, language="de", have={"ebook": "epub"})
    assert codes(verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB", wanted=have_epub)) == ["not_better"]
    goal_azw3 = config(target={"ebook": "azw3", "audio": "flac"})
    have_azw3 = judge.Wanted(book=BOOK, language="de", have={"ebook": "azw3"})
    assert codes(verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB", wanted=have_azw3,
                         cfg=goal_azw3)) == ["not_better"]


def test_the_order_of_those_that_pass() -> None:
    names = {
        "a": ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.MOBI-EXAMPLE", {}),
        "b": ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE", {"priority": 10}),
        "c": ("Rune.Falkner.-.Die.Laternenstrasse.EPUB-EXAMPLE", {"priority": 1}),
        "d": ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-OTHER", {"priority": 5}),
        "e": ("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.AZW3-EXAMPLE", {"priority": 1}),
    }
    found = {key: verdict(name, **over) for key, (name, over) in names.items()}
    judge.rank(list(found.values()))
    order = sorted(found, key=lambda key: found[key].rank or 99)
    # The format first, then a sure language, then the indexer's priority.
    assert order == ["d", "b", "c", "e", "a"]
    assert found["d"].score >= found["b"].score >= found["c"].score > found["a"].score


def test_seeders_and_age_come_after_the_priority() -> None:
    many = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-A", protocol="torrent", seeders=40)
    few = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-B", protocol="torrent", seeders=2)
    fresh = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-C", published_at=NOW - timedelta(days=1))
    old = verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-D", published_at=NOW - timedelta(days=2000))
    judge.rank([few, many, old, fresh])
    assert (many.rank or 0) < (few.rank or 0) and (fresh.rank or 0) < (old.rank or 0)


def test_an_abridged_reading_passes_only_when_allowed_and_no_unabridged_one_does() -> None:
    allow = config(variant="prefer", abridged=True)
    wanted = judge.Wanted(book=BOOK, language="de")
    short = verdict("Rune Falkner-Die Laternenstrasse (Gekuerzt)-WEB-MP3-DE-2017-EXAMPLE", wanted=wanted, cfg=allow,
                    size=300 * MB)
    assert short.passes
    judge.rank([short])
    assert short.rank == 1
    short = verdict("Rune Falkner-Die Laternenstrasse (Gekuerzt)-WEB-MP3-DE-2017-EXAMPLE", wanted=wanted, cfg=allow,
                    size=300 * MB)
    whole = verdict("Rune Falkner-Die Laternenstrasse-WEB-MP3-DE-2017-EXAMPLE", wanted=wanted, cfg=allow,
                    size=600 * MB)
    judge.rank([short, whole])
    assert whole.rank == 1 and short.rank is None and codes(short) == ["abridged"]
    # "Only unabridged" turns it down whatever the switch says.
    strict = config(variant="unabridged", abridged=True)
    assert codes(verdict("Rune Falkner-Die Laternenstrasse (Gekuerzt)-WEB-MP3-DE-2017-EXAMPLE", cfg=strict,
                         size=300 * MB)) == ["abridged"]


def test_a_dramatisation_passes_when_allowed_and_comes_after_a_reading() -> None:
    allow = config(dramatized=True)
    play = verdict("Rune.Falkner.-.Die.Laternenstrasse.-.Das.Hoerspiel.DE.MP3-EXAMPLE", cfg=allow, size=300 * MB)
    reading = verdict("Rune.Falkner.-.Die.Laternenstrasse.DE.MP3.128kbps-EXAMPLE", cfg=allow, size=300 * MB)
    judge.rank([play, reading])
    assert play.passes and reading.rank == 1 and play.rank == 2


def test_a_bundle_word_that_is_part_of_the_title_is_no_bundle() -> None:
    box = rm.Book(titles=(rm.Title("Die Trilogie der Laternen", "de"),), authors=("Rune Falkner",))
    found = verdict("Rune.Falkner.-.Die.Trilogie.der.Laternen.GERMAN.EPUB", wanted=judge.Wanted(book=box,
                                                                                              language="de"))
    assert found.passes
    # A book that is a bundle itself takes a bundle.
    whole = rm.Book(titles=(rm.Title("Nebelchor 1-3", "de"),), authors=("Rune Falkner",), bundle=True)
    assert verdict("Rune.Falkner.-.Nebelchor.1-3.GERMAN.EPUB", wanted=judge.Wanted(book=whole, language="de")).passes


def test_the_card_counts_and_names_the_most_frequent_reasons() -> None:
    found = [verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.EPUB-A"),
             verdict("Rune.Falkner.-.Die.Laternenstrasse.FRENCH.EPUB-B"),
             verdict("Rune.Falkner.-.Die.Laternenstrasse.FRENCH.EPUB-C"),
             verdict("Rune.Falkner.-.Die.Laternenstrasse.GERMAN.PDF-D")]
    judge.rank(found)
    card = judge.summary(found, "ebook")
    assert card == {"total": 4, "passing": 1, "reasons": [{"code": "other_language", "count": 2},
                                                          {"code": "format_not_wanted", "count": 1}]}


def test_a_goal_low_in_the_ranking_still_fetches_the_best_and_stops_upgrades_at_the_goal() -> None:
    """FLAC on top and MP3 128 as the goal. A book that has nothing is fetched in the best format
    there is (FLAC before MP3 128); a book that has MP3 192 is at the goal or above it, and no release replaces it."""
    goal_128 = config(target={"ebook": "epub", "audio": "mp3_128"})
    wanted = judge.Wanted(book=BOOK, language="de", duration=10 * 3600)
    flac = verdict("Rune Falkner-Die Laternenstrasse-WEB-FLAC-DE-2021-EXAMPLE", wanted=wanted, cfg=goal_128,
                   size=2000 * MB, category_kind="audio")
    low = verdict("Rune.Falkner.Die.Laternenstrasse.MP3.128kbps.DE-EXAMPLE", wanted=wanted, cfg=goal_128,
                  size=450 * MB, category_kind="audio")
    assert flac.passes and low.passes and (flac.format, low.format) == ("flac", "mp3_128")
    judge.rank([low, flac])
    assert (flac.rank, low.rank) == (1, 2)
    has_192 = judge.Wanted(book=BOOK, language="de", duration=10 * 3600, have={"audio": "mp3_192"})
    better = verdict("Rune Falkner-Die Laternenstrasse-WEB-FLAC-DE-2021-EXAMPLE", wanted=has_192, cfg=goal_128,
                     size=2000 * MB, category_kind="audio")
    assert codes(better) == ["not_better"]
    # Below the goal (MP3 64 allowed for once) a better release is still looked for.
    allow_64 = config(target={"ebook": "epub", "audio": "mp3_128"},
                      audio=[{**item, "on": True} for item in formats.default_config()["audio"]])
    has_64 = judge.Wanted(book=BOOK, language="de", duration=10 * 3600, have={"audio": "mp3_64"})
    assert verdict("Rune Falkner-Die Laternenstrasse-WEB-FLAC-DE-2021-EXAMPLE", wanted=has_64, cfg=allow_64,
                   size=2000 * MB, category_kind="audio").passes
