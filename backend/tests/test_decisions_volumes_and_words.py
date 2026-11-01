"""Volumes in words and parts of a book, the words of versions that are not the reading wished, subtitles and close
forms of an author, publishers and editions beside a title, and the article of a title: before the download (the name
of a release) and after it (the files). Rule of all of them: only a strong sign decides without a person; a name that
gives another volume or a part is never the book wished. All names and titles are invented."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import book_words, catalog, series_names
from app.services.procure import checking, formats, judge
from app.services.procure import release_match as rm
from app.services.procure.release_name import parse

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
MB = 1_000_000
#: A volume of a series: the second of "Nebelchor".
SERIES = rm.Book(titles=(rm.Title("Die Laternenstraße", "de"),), authors=("Rune Falkner",), series="Nebelchor",
                 position=2.0)
#: The first volume of a series whose title names the volume in words.
STARS = rm.Book(titles=(rm.Title("Mondglas - Das erste Buch der Sterne", "de"),), authors=("Ida Leser",),
                series="Mondglas", position=1.0)
#: A work of no series whose other title names its part in words.
SAGA = rm.Book(titles=(rm.Title("Kaltwasser", "de"), rm.Title("Kaltwasser. Der Saga erster Teil", "de")),
               authors=("Rune Falkner",))
#: A book of no series, and no volume named.
SINGLE = rm.Book(titles=(rm.Title("Der Leuchtturm", "de"),), authors=("Mara Feldkamp",))


def judged(name: str, book: rm.Book, size: int = 2 * MB, **over: Any) -> judge.Verdict:
    parsed = parse(name)
    offer = judge.Offer(title=name, size=size, published_at=None, protocol="usenet", **over)
    return judge.judge(parsed, offer, rm.match(parsed, book), judge.Wanted(book=book, language="de"),
                       formats.default_config(), NOW)


def outcome(verdict: judge.Verdict) -> str:
    codes = [item["code"] for item in verdict.reasons]
    if verdict.passes:
        return "pass"
    return ("ask:" if verdict.doubtful else "reject:") + ",".join(codes)


# --- Volumes in words -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "volume"), [
    ("Faltwerk - Der Saga zweiter Teil", 2), ("Mondglas - Das dritte Buch der Sterne", 3),
    ("Kaltwasser, zweiter Band", 2), ("Kaltwasser - Band zwei", 2), ("Kaltwasser - Teil II", 2),
    ("Kaltwasser.Teil.IV.GERMAN", 4), ("Kaltwasser - Book Two", 2), ("The Second Book of Tides", 2),
    ("Kaltwasser - Band 3", 3), ("Die zwei Brücken", None), ("Der erste Tag", None), ("Teil 1 von 2", None),
    ("Kaltwasser - Part 2", None), ("Kapitel I", None), ("teil i", None),
])
def test_a_volume_in_words_or_roman_numerals_is_read_as_a_number(text: str, volume: int | None) -> None:
    assert book_words.title_volume(text) == volume


@pytest.mark.parametrize(("name", "expected"), [
    ("Ida Leser - Mondglas - Das zweite Buch der Sterne - EPUB", "reject:other_volume"),
    ("Ida.Leser.Mondglas.-.Das.dritte.Buch.der.Sterne", "reject:other_volume"),
    ("Ida Leser - Mondglas - Das erste Buch der Sterne - GERMAN EPUB", "pass"),
])
def test_another_volume_in_words_is_another_book_of_the_series(name: str, expected: str) -> None:
    assert outcome(judged(name, STARS)) == expected


@pytest.mark.parametrize(("name", "expected"), [
    ("Rune Falkner - Kaltwasser - Der Saga zweiter Teil - EPUB", "reject:other_volume"),
    ("Rune Falkner - Kaltwasser - Teil II - EPUB", "reject:other_volume"),
    ("Rune Falkner - Kaltwasser - Der Saga erster Teil - EPUB", "pass"),
])
def test_a_title_that_names_its_volume_holds_off_the_others(name: str, expected: str) -> None:
    assert outcome(judged(name, SAGA)).startswith(expected)


@pytest.mark.parametrize("name", [
    "Mara Feldkamp - Der Leuchtturm, zweiter Band - EPUB",
    "Mara Feldkamp - Der Leuchtturm - Band zwei - EPUB",
    "Mara Feldkamp - Der Leuchtturm - Book Two - EPUB",
])
def test_a_volume_in_words_of_a_book_nobody_knows_a_series_of_is_a_question(name: str) -> None:
    found = judged(name, SINGLE)
    assert found.doubtful and "maybe_other_volume" in [item["code"] for item in found.reasons]


def test_the_first_part_in_words_is_the_work_as_it_is_known() -> None:
    book = rm.Book(titles=(rm.Title("Kaltwasser", "de"),), authors=("Rune Falkner",))
    assert judged("Rune Falkner - Kaltwasser - Der Saga erster Teil - EPUB", book).passes
    # In digits it may be a part of two: a question, as before.
    assert outcome(judged("Rune Falkner - Kaltwasser - Teil 1 - EPUB", book)).startswith("ask:maybe_partial")


# --- Parts beside a volume of a series ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "size", "expected"), [
    ("Rune Falkner - Die Laternenstrasse - Teil 1 von 2 - EPUB", 2 * MB, "reject:partial"),
    ("Rune Falkner - Die Laternenstrasse - CD 1-4 - MP3", 300 * MB, "reject:partial"),
    ("Rune Falkner - Die Laternenstrasse - Disc 3 - MP3", 300 * MB, "reject:partial"),
    ("Rune Falkner - Die Laternenstrasse - CD 2 - MP3", 300 * MB, "reject:partial"),
    ("Rune Falkner - Die Laternenstrasse - Part 1 - MP3", 300 * MB, "reject:partial"),
    ("Rune Falkner - Die Laternenstrasse - Part 2 - MP3", 300 * MB, "ask:maybe_partial"),
    ("Rune Falkner - Nebelchor 2 - Die Laternenstrasse - Teil 3 - Hoerbuch", 300 * MB, "reject:partial"),
    ("Rune Falkner - Die Laternenstrasse - Teil 2 - EPUB", 2 * MB, "pass"),
    ("Rune Falkner - Die Laternenstrasse - Band 2 von 7 - EPUB", 2 * MB, "pass"),
])
def test_a_part_beside_a_volume_of_a_series_is_never_the_volume(name: str, size: int, expected: str) -> None:
    assert outcome(judged(name, SERIES, size)).startswith(expected)


def test_the_number_of_parts_has_a_limit_and_a_part_of_too_many_is_no_part_of() -> None:
    assert book_words.part_of("Teil 2 von 99") == (2, 99)
    assert book_words.part_of("Teil 2 von 120") == (2, None)
    assert book_words.part_of("Teil 5 von 3") == (5, None)
    assert book_words.sure_part("Teil 2 von 120") is None
    assert book_words.sure_part("Teil 2 von 3") == (2, 3)


# --- Words of a version that is not the reading ------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "size", "code"), [
    ("Mara Feldkamp - Der Leuchtturm - Radiofassung - MP3", 300 * MB, "dramatized"),
    ("Mara Feldkamp - Der Leuchtturm - Rundfunkfassung - MP3", 300 * MB, "dramatized"),
    ("Mara Feldkamp - Der Leuchtturm - Hoerfassung - MP3", 300 * MB, "abridged"),
    ("Mara Feldkamp - Der Leuchtturm - Kurzfassung - MP3", 300 * MB, "abridged"),
    ("Mara Feldkamp - Der Leuchtturm - Condensed - Audiobook", 300 * MB, "abridged"),
    ("Mara Feldkamp - Der Leuchtturm - Auszug Kapitel 1 - EPUB", 2 * MB, "sample"),
    ("Mara Feldkamp - Der Leuchtturm - Auszuege - Hoerbuch", 300 * MB, "sample"),
    ("Mara Feldkamp - Der Leuchtturm - Kinderbuch Version - EPUB", 2 * MB, "adaptation"),
])
def test_the_words_of_another_version_are_reasons(name: str, size: int, code: str) -> None:
    found = judged(name, SINGLE, size)
    assert not found.passes and code in [item["code"] for item in found.reasons]


def test_a_word_of_a_sample_the_title_wished_holds_is_a_word_of_the_title() -> None:
    book = rm.Book(titles=(rm.Title("Der Auszug", "de"),), authors=("Mara Feldkamp",))
    assert judged("Mara Feldkamp - Der Auszug - GERMAN EPUB", book).passes


# --- Subtitles, close forms of an author --------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "expected"), [
    ("Mara Feldkamp - Der Leuchtturm. Eine Geschichte vom Meer - EPUB", "ask:maybe_other_book"),
    ("Mara Feldkamp - Der Leuchtturm oder Die Nacht am Meer - EPUB", "ask:maybe_other_book"),
    ("Feldkamp, Mara - Der Leuchtturm oder Die Nacht am Meer - Epub", "ask:maybe_other_book"),
    # Without a space after the full stop nothing tells a subtitle from more of the title.
    ("Mara.Feldkamp.Der.Leuchtturm.Eine.Geschichte.vom.Meer.EPUB", "reject:title_mismatch"),
    # A volume after the full stop is no subtitle.
    ("Mara Feldkamp - Der Leuchtturm. Zweiter Teil - EPUB", "reject:"),
])
def test_a_subtitle_after_a_full_stop_or_oder_is_a_question(name: str, expected: str) -> None:
    assert outcome(judged(name, SINGLE)).startswith(expected)


def test_a_subtitle_that_is_another_book_of_the_author_stays_turned_down() -> None:
    book = rm.Book(titles=(rm.Title("Der Leuchtturm", "de"),), authors=("Mara Feldkamp",),
                   others=("Die Nacht am Meer",))
    assert outcome(judged("Mara Feldkamp - Der Leuchtturm oder Die Nacht am Meer - EPUB", book)).startswith(
        "reject:")


@pytest.mark.parametrize(("name", "expected"), [
    ("AB Feldkamp - Der Leuchtturm - EPUB", "ask:maybe_other_book"),
    ("Feldkamp, Anna Berit - Der Leuchtturm - EPUB", "ask:maybe_other_book"),
    ("Feldkamp, Hans Otto - Der Leuchtturm - EPUB", "reject:author_mismatch"),
    ("A. B. Feldkamp - Der Leuchtturm - EPUB", "pass"),
])
def test_initials_written_together_or_spelled_out_are_a_question(name: str, expected: str) -> None:
    book = rm.Book(titles=(rm.Title("Der Leuchtturm", "de"),), authors=("A. B. Feldkamp",))
    assert outcome(judged(name, book)).startswith(expected)


# --- What may stand beside a title ---------------------------------------------------------------------------------------


def test_a_publisher_a_shelf_and_an_edition_beside_the_title_ask_nothing() -> None:
    house = min(series_names.PUBLISHERS).title()
    for name in (f"Mara Feldkamp - Der Leuchtturm ({house}) - EPUB",
                 f"Mara Feldkamp - Der Leuchtturm ({house} Taschenbuch) - EPUB",
                 f"Mara Feldkamp - Der Leuchtturm (2006, {house}) - EPUB",
                 f"Mara Feldkamp - Der Leuchtturm - {house} Verlag - EPUB",
                 "kinder - Feldkamp, Mara - Der Leuchtturm - ebook german epub",
                 "Mara Feldkamp - Der Leuchtturm - Limitierte Sonderausgabe - EPUB",
                 "Mara Feldkamp - Der Leuchtturm - Die Kuestensaga - EPUB"):
        assert judged(name, SINGLE).passes, name


def test_words_beside_a_title_that_are_no_publisher_still_ask() -> None:
    found = judged("Mara Feldkamp - Der Leuchtturm (Mit Arbeitsblaettern zum Text) - EPUB", SINGLE)
    assert found.doubtful


def test_the_article_of_another_language_before_a_title_of_both_is_a_question() -> None:
    book = rm.Book(titles=(rm.Title("Der Nachtfalke", "de"), rm.Title("The Nachtfalke", "en")),
                   authors=("Mara Feldkamp",))
    found = judged("Mara Feldkamp - The Nachtfalke - EPUB", book)
    assert found.doubtful and [item["code"] for item in found.reasons] == ["language_unsure"]
    assert judged("Mara Feldkamp - Der Nachtfalke - EPUB", book).passes
    # The title wished in the language wished begins with that article itself: no sign.
    own = rm.Book(titles=(rm.Title("The Nachtfalke", ""),), authors=("Mara Feldkamp",))
    assert judged("Mara Feldkamp - The Nachtfalke - EPUB", own).passes


# --- After the download ------------------------------------------------------------------------------------------------


def subject(title: str, *, position: float | None = None, series: str = "", titles: list[str] | None = None,
            language: str = "de") -> dict[str, Any]:
    return {"title": title, "titles": [{"text": text, "language": language} for text in (titles or [title])],
            "authors": ["Mara Feldkamp"], "series": series, "position": position, "others": [], "language": language}


def files(wish: dict[str, Any], title: str, *, kind: str = "ebook", probe: dict[str, Any] | None = None,
          seconds: float = 0.0, language: str = "de", **wanted: Any) -> checking.Verdict:
    fmt = "epub" if kind == "ebook" else "mp3"
    meta = catalog.Meta(kind=kind, title=title, authors=["Mara Feldkamp"], language=language, duration=seconds,
                        variant="unabridged" if kind == "audio" else "")
    meta.uncertain = False
    item = SimpleNamespace(path=f"Buch/buch.{fmt}", format=fmt,
                           probe=probe if probe is not None else {"language": language, "text_language": language,
                                                                  "duration": seconds},
                           size=max(1, int(seconds * 16000)), origin="source", unit="u")
    unit = checking.Unit(key="u", kind=kind, files=[item], meta=meta)
    values: dict[str, Any] = {"kind": kind, "language": "de", "accepted": ["de"], "subject": wish,
                              "config": formats.default_config(), "named_author": True}
    values.update(wanted)
    return checking.judge_units([unit], checking.Wanted(**values))


def said(verdict: checking.Verdict) -> str:
    return verdict.outcome + (f":{verdict.code}" if verdict.code else "")


@pytest.mark.parametrize(("wish", "title", "expected"), [
    (subject("Mondglas - Das erste Buch der Sterne", position=1.0, series="Mondglas"),
     "Mondglas - Das zweite Buch der Sterne", "reject:other_volume"),
    (subject("Kaltwasser", titles=["Kaltwasser", "Kaltwasser. Der Saga erster Teil"]),
     "Kaltwasser. Der Saga zweiter Teil", "reject:other_volume"),
    (subject("Der Leuchtturm"), "Der Leuchtturm, zweiter Band", "ask:partial"),
    (subject("Der Leuchtturm", position=2.0, series="Kuestenreihe"), "Der Leuchtturm - Teil 1 von 2", "ask:partial"),
    (subject("Der Leuchtturm", position=2.0, series="Kuestenreihe"), "Der Leuchtturm - Teil 2", "take"),
    (subject("Der Leuchtturm"), "Der Leuchtturm (Auszug)", "reject:sample"),
    (subject("Der Leuchtturm"), "Der Leuchtturm - Kinderbuch Version", "reject:adaptation"),
])
def test_the_files_name_another_volume_a_part_or_another_version(wish: dict[str, Any], title: str,
                                                                expected: str) -> None:
    assert said(files(wish, title)) == expected


def test_nothing_that_says_the_language_is_a_question_even_with_one_language_allowed() -> None:
    wish = subject("Der Leuchtturm")
    quiet = {"language": "", "duration": 5 * 3600}
    assert said(files(wish, "Der Leuchtturm", kind="audio", probe=quiet, seconds=5 * 3600, language="")) == \
        "ask:language_unknown"
    # The category of the indexer, or a mark in the name of the folder, is a witness.
    assert said(files(wish, "Der Leuchtturm", kind="audio", probe=quiet, seconds=5 * 3600, language="",
                      category_language="de")) == "take"
    # A title known in one language says nothing of the language a recording is read in.
    assert said(files(wish, "Der Leuchtturm", kind="audio", probe=quiet, seconds=5 * 3600, language="",
                      title_language="de")) == "ask:language_unknown"
    assert said(files(wish, "Der Leuchtturm", probe={"language": "", "text_language": ""}, language="")) == \
        "ask:language_unknown"


def test_a_recording_well_below_the_unabridged_running_time_is_abridged() -> None:
    full = {"unabridged": [10_000]}
    assert checking.runtime_variant(7_900, full)[0] == "abridged"
    assert checking.runtime_variant(8_100, full)[0] == checking.SHORTER
    assert checking.RUNTIME_ABRIDGED == 0.80


# --- An e-book that betters one the library has --------------------------------------------------------------------------


def have(**values: Any) -> checking.Have:
    return checking.Have(**{"fmt": "pdf", **values})


@pytest.mark.parametrize(("old", "new", "expected"), [
    # The old one says nothing of its edition, the new one fits it: the same.
    (have(title="Der Leuchtturm"), have(fmt="epub", title="Der Leuchtturm", isbn="9780000000002", pages=300), "same"),
    # The same ISBN, the same translator, the same publisher with pages close: the same.
    (have(fmt="mobi", isbn="9780000000002"), have(fmt="epub", isbn="978-0-00-000000-2"), "same"),
    (have(fmt="mobi", translators=["Ida Leser"]), have(fmt="epub", translators=["Ida Leser"]), "same"),
    (have(fmt="mobi", publisher="Beispielhaus", pages=300), have(fmt="epub", publisher="Beispielhaus", pages=310),
     "same"),
    # Two of a kind that differ: another edition.
    (have(fmt="mobi", isbn="9780000000002"), have(fmt="epub", isbn="9780000000019"), "apart"),
    (have(fmt="mobi", translators=["Ida Leser"]), have(fmt="epub", translators=["Rune Falkner"]), "apart"),
    (have(fmt="mobi", pages=1500), have(fmt="epub", pages=800), "apart"),
    # The old one says something, nothing proves the same: a person decides.
    (have(fmt="mobi", publisher="Beispielhaus"), have(fmt="epub"), "unsure"),
    (have(fmt="mobi", pages=300), have(fmt="epub", pages=320), "unsure"),
    # The old one says nothing, and the new one does not fit it.
    (have(title="Der Leuchtturm"), have(fmt="epub", title="Die Nacht am Meer"), "unsure"),
    (have(title="Der Leuchtturm", year="1990"), have(fmt="epub", title="Der Leuchtturm", year="2021"), "unsure"),
])
def test_an_e_book_replaces_another_only_when_it_is_the_same_edition(old: checking.Have, new: checking.Have,
                                                                    expected: str) -> None:
    assert checking.text_against(old, new) == expected


def test_an_unsure_edition_is_a_question_and_the_answer_decides() -> None:
    wish = subject("Der Leuchtturm")
    old = have(fmt="mobi", publisher="Beispielhaus", language="de")
    asked = files(wish, "Der Leuchtturm", haves=[old])
    assert said(asked) == "ask:same_edition" and asked.values["have"] == "mobi" and asked.values["found"] == "epub"
    replaced = files(wish, "Der Leuchtturm", haves=[old], decision={"replace": True})
    assert said(replaced) == "take" and replaced.replaces
    beside = files(wish, "Der Leuchtturm", haves=[old], decision={"replace": False})
    assert said(beside) == "take" and not beside.replaces
    # An upgrade wish that brings nothing better is turned down, as before.
    assert said(files(wish, "Der Leuchtturm", haves=[have(fmt="epub", publisher="Beispielhaus", language="de")],
                      upgrade=True)) == "reject:not_better"
