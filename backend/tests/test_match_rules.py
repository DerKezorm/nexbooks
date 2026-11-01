"""The rules that settle a recording without its running time, and the traps of the real folders and catalogues the first
runs on a real library found: two sources that tell the same book are enough for the book, a series record is no other
book, a publisher's imprint is no series, the number a file begins with is its part and no volume. Pure rule, invented
names throughout."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.match import score as rule
from app.services.match.run import path_hints, wanted_from
from app.services.match.score import Wanted, author_state, decide, score
from app.services.readers import names
from app.services.sources.model import Candidate


def want(**kw: Any) -> Wanted:
    base: dict[str, Any] = {"kind": "audio", "title": "Nachtglas", "authors": ["Rune Falkner"], "language": "",
                            "duration": 36000}
    return Wanted(**{**base, **kw})


def cand(source: str, **kw: Any) -> Candidate:
    base: dict[str, Any] = {"source": source, "key": kw.pop("key", f"{source}-1"), "title": "Nachtglas",
                            "authors": ["Rune Falkner"], "languages": []}
    return Candidate(**{**base, **kw})


# ---- Sure without a running time ---------------------------------------------------------------------------------


def test_two_sources_that_tell_the_same_book_make_it_sure_and_say_the_recording_is_not_proven() -> None:
    decision = decide(want(), [cand("dnb"), cand("hardcover")])
    assert decision.state == "sure" and decision.variant_unproven
    assert decision.best is not None and not decision.best.proof


def test_one_source_alone_without_a_running_time_waits_for_a_person() -> None:
    decision = decide(want(), [cand("dnb")])
    assert (decision.state, decision.reason) == ("maybe", "runtime_unproven") and not decision.variant_unproven


def test_a_running_time_that_proves_the_recording_needs_no_second_source_and_is_not_marked_unproven() -> None:
    decision = decide(want(), [cand("audible", runtime_min=600, asin="B0NIGHT001")])
    assert decision.state == "sure" and not decision.variant_unproven and decision.best is not None
    assert decision.best.proof


def test_a_running_time_up_to_a_fifth_short_is_plausible_and_the_book_is_sure_without_a_proof() -> None:
    decision = decide(want(), [cand("audible", runtime_min=700)])
    assert decision.state == "sure" and decision.variant_unproven
    assert decision.best is not None and decision.best.plausible and not decision.best.proof


def test_a_running_time_that_is_another_recording_leaves_the_book_to_a_person_unless_a_second_source_agrees() -> None:
    other = cand("audible", runtime_min=1200)
    assert decide(want(), [other]).state == "maybe"
    together = decide(want(), [other, cand("dnb")])
    assert together.state == "sure" and together.variant_unproven, "the book is told by two sources; the recording not"


def test_the_running_time_never_makes_a_book_sure_whose_title_is_another() -> None:
    wrong = cand("audible", title="Sternenstaub", runtime_min=600)
    decision = decide(want(), [wrong])
    assert decision.state == "open" and decision.reason == "nothing_fits"


def test_a_record_a_source_lists_twice_is_one_voice() -> None:
    twin = [cand("audible", key="B0A", runtime_min=600), cand("audible", key="B0B", runtime_min=600)]
    assert len(rule.distinct(twin)) == 1
    decision = decide(want(), twin)
    assert decision.state == "sure" and "ambiguous" not in decision.reason
    assert len(decision.ranked) == 1, "the list a person looks at shows the record once"
    one = decide(want(), [cand("dnb", key="D1"), cand("dnb", key="D2")])
    assert one.state == "maybe", "two records of one source are no second source"


# ---- Series records ----------------------------------------------------------------------------------------------


def test_the_record_of_a_series_is_no_other_book_and_proves_the_recording_by_its_time() -> None:
    wanted = want(title="Die Frequenz des Todes", authors=["Rune Falkner", "Anselm Quist"], series="Auris",
                  series_pos=2.0, duration=414 * 60)
    record = cand("audible", title="Auris 2", authors=["Rune Falkner", "Anselm Quist"], series="Auris", series_pos=2.0,
                  runtime_min=414)
    real = cand("hardcover", title="Die Frequenz des Todes", authors=["Anselm Quist", "Rune Falkner"], series="Auris",
                series_pos=2.0)
    assert score(wanted, record).record and score(wanted, record).fatal == ""
    decision = decide(wanted, [record, real])
    assert decision.state == "sure" and decision.reason == ""


def test_the_record_of_the_series_alone_with_the_time_and_the_volume_is_sure_and_without_them_is_not() -> None:
    wanted = want(title="Wilde Lupinen", series="Sturmzeit", series_pos=2.0, duration=461 * 60)
    record = cand("dnb", title="Sturmzeit", series="Die Sturmzeittrilogie", series_pos=2.0)
    assert score(wanted, record).record
    assert (decide(wanted, [record]).state, decide(wanted, [record]).reason) == ("maybe", "series_only")


def test_a_record_of_another_volume_is_still_another_volume() -> None:
    wanted = want(title="Wilde Lupinen", series="Sturmzeit", series_pos=2.0)
    assert score(wanted, cand("dnb", title="Sturmzeit", series="Sturmzeit", series_pos=4.0)).fatal == "volume_differs"


@pytest.mark.parametrize("series,pos", [("Fischer", 1983.0), ("Ullstein", 2829.0), ("Blanvalet", 601.0),
                                        ("Mörderische Schwestern", 3672.0), ("Rune Falkner", 2.0), ("Heyne", 7.0),
                                        ("Fischer Taschenbuch", 12.0), ("dtv", 3.0)])
def test_the_imprint_of_a_publisher_and_the_name_of_the_author_are_no_series(series: str, pos: float) -> None:
    junk = cand("dnb", series=series, series_pos=pos)
    assert rule.junk_series(junk)
    scored = score(want(), junk)
    assert "no_volume" not in scored.missing and scored.fatal == ""


def test_the_series_of_a_source_is_compatible_when_one_name_holds_the_other() -> None:
    assert rule._series_compatible("Auris", "Auris Hörspielreihe") and rule._series_compatible("Die Auris-Reihe", "Auris-Reihe")
    assert rule._series_compatible("", "Auris") and not rule._series_compatible("Auris", "Elena")


def test_the_record_of_a_series_is_known_by_the_series_of_the_candidate_and_by_the_series_of_the_book() -> None:
    by_candidate = cand("audible", title="Auris 2", series="Auris", series_pos=2.0)
    assert rule.series_record(want(series=""), by_candidate)
    by_book = cand("dnb", title="Elena", series="", series_pos=3.0)
    assert rule.series_record(want(series="Elena"), by_book)
    assert not rule.series_record(want(series="Auris"), cand("dnb", title="Ein ganz anderes Buch", series="Auris"))


def test_the_record_of_a_series_without_a_volume_is_never_sure_for_a_book_with_its_own_title() -> None:
    wanted = want(title="Die Nebelwacht", series="Moorland", series_pos=2.0)
    record = cand("audible", title="Moorland", series="Moorland", series_pos=2.0, runtime_min=600)
    scored = score(wanted, record)
    assert scored.record and scored.capped and "series_only" in scored.against
    assert decide(wanted, [record]).state != "sure"
    # With its volume in the title, or for a book whose title begins with the series, it says what it said before.
    numbered = score(wanted, cand("audible", title="Moorland 2", series="Moorland", series_pos=2.0, runtime_min=600))
    assert numbered.record and "series_only" not in numbered.against
    named = score(want(title="Moorland Folge 2 Die Nebelwacht", series="", series_pos=2.0), cand(
        "audible", title="Moorland", series="", series_pos=2.0, runtime_min=600))
    assert named.record and "series_only" not in named.against


def test_a_real_series_is_kept_and_a_series_the_book_has_itself_is_never_junk() -> None:
    assert not rule.junk_series(cand("hardcover", series="Auris", series_pos=3.0))
    wanted = want(series="Perry Rhodan", series_pos=3200.0)
    assert not rule.junk_series(cand("dnb", series="Perry Rhodan", series_pos=3200.0), wanted)


def test_the_series_of_a_source_is_compared_without_the_form_of_the_recording() -> None:
    assert rule.series_key("Bodenstein & Kirchhoff (gekürzt)") == rule.series_key("Bodenstein & Kirchhoff")
    wanted = want(series="Bodenstein & Kirchhoff", series_pos=10.0)
    scored = score(wanted, cand("audible", series="Bodenstein & Kirchhoff (gekürzt)", series_pos=10.0))
    assert "volume_ok" in scored.why


# ---- Years -------------------------------------------------------------------------------------------------------


def test_a_book_older_than_its_recording_is_not_against_it_and_a_recording_older_than_its_book_is() -> None:
    wanted = want(year=2011)
    assert "year_off" not in score(wanted, cand("dnb", year=1998)).against
    assert "year_off" in score(wanted, cand("dnb", year=2015)).against
    ebook = want(kind="ebook", year=2011, duration=0)
    assert "year_off" in score(ebook, cand("dnb", year=1998)).against


# ---- Authors -----------------------------------------------------------------------------------------------------


def test_the_candidate_may_name_a_pen_name_too() -> None:
    assert author_state(["Mara Tiefenbach"], ["Mara Falk schreibt als Mara Tiefenbach"]) == "match"
    assert author_state(["Ben Hollister"], ["Mara Falk schreibt als Mara Tiefenbach"]) == "none"


def test_a_tag_that_says_one_writes_as_another_is_both_names() -> None:
    assert names.author_names("Mara Falk schreibt als Mara Tiefenbach") == ["Mara Falk", "Mara Tiefenbach"]
    assert author_state(["Mara Falk schreibt als Mara Tiefenbach", "Ben Hollister"], ["Mara Tiefenbach"]) == "match"
    assert author_state(["Mara Falk schreibt als Mara Tiefenbach"], ["Mara Falk"]) == "match"


def test_the_reader_in_a_field_of_authors_is_no_author() -> None:
    assert names.author_names("Ben Hollister liest Rune Falkner") == ["Rune Falkner"]
    assert names.split_people("Ben Hollister liest Rune Falkner") == [("Rune Falkner", ""), ("Ben Hollister", "narrator")]
    assert author_state(["Ben Hollister liest Rune Falkner"], ["Rune Falkner"]) == "match"


def test_the_authors_of_a_source_may_be_a_part_of_the_authors_of_the_file_and_the_other_way_round() -> None:
    assert author_state(["Rune Falkner", "Ben Hollister"], ["Rune Falkner"]) == "match"
    assert author_state(["Rune Falkner"], ["Anselm Quist", "Rune Falkner"]) == "match"
    assert author_state(["Ben Hollister"], ["Rune Falkner"]) == "none"


def test_the_author_at_the_top_of_the_library_is_one_more_name_of_the_author_beside_the_tags() -> None:
    from types import SimpleNamespace

    edition = SimpleNamespace(kind="audio", title="Rauhnacht", subtitle="", language="", isbn="", asin="",
                              series_name="", series_pos=None, duration=3600, original_title="")
    wanted = wanted_from(edition, ["Kommissar Kluftinger"], "Volker Klüpfel/Rauhnacht (2010)/Audio/01.mp3")
    assert wanted.authors == ["Kommissar Kluftinger", "Volker Klüpfel"]
    assert author_state(wanted.authors, ["Volker Klüpfel", "Michael Kobr"]) == "match"
    # Not added when a tag names the same person.
    again = wanted_from(edition, ["Volker Klüpfel"], "Volker Klüpfel/Rauhnacht (2010)/Audio/01.mp3")
    assert again.authors == ["Volker Klüpfel"]


# ---- Names of files and folders ----------------------------------------------------------------------------------


def test_the_number_a_file_of_a_recording_begins_or_ends_with_is_its_part_and_no_volume() -> None:
    for path in ("Nele Hartung/In ewiger Freundschaft (2021)/001 - In ewiger Freundschaft.mp3",
                 "Nele Hartung/Passagier (2026)/Passagier - 01.mp3", "Nele Hartung/Passagier (2026)/Audio/10. Track.mp3"):
        assert path_hints("audio", path, None).volume is None, path
    # A marked volume counts, and the name of the folder.
    assert path_hints("audio", "Nele Hartung/Passagier (2026)/Passagier Band 3.mp3", None).volume == 3.0
    assert path_hints("audio", "Nele Hartung/Passagier - Reihe 7 (2026)/01.mp3", None).volume == 7.0
    # For an e-book the number at the front of the name of the file is the volume, as before.
    assert path_hints("ebook", "Nele Hartung/02 - Passagier.epub", None).volume == 2.0


def test_a_leading_track_number_does_not_turn_the_right_book_into_another_volume() -> None:
    """The case that left a book open that had everything: title, author, the time to the minute, and a file called
    ``001 - Titel.mp3`` that read as volume 1 against the volume 10 the source names."""
    from types import SimpleNamespace

    edition = SimpleNamespace(kind="audio", title="In ewiger Freundschaft", subtitle="", language="", isbn="", asin="",
                              series_name="", series_pos=None, duration=768 * 60, original_title="")
    wanted = wanted_from(edition, ["Nele Hartung"], "Nele Hartung/In ewiger Freundschaft (2021)/001 - In ewiger Freundschaft.mp3")
    product = cand("audible", title="In ewiger Freundschaft", authors=["Nele Hartung"], series="Bodenstein & Kirchhoff",
                   series_pos=10.0, runtime_min=768)
    decision = decide(wanted, [product])
    assert decision.state == "sure" and decision.best is not None and decision.best.proof


def test_the_title_of_the_folder_counts_as_the_same_title_when_the_candidate_has_it_whole() -> None:
    wanted = want(title="Ein ganz anderer Name", alt_titles=["Bretonischer Ruhm"])
    scored = score(wanted, cand("dnb", title="Bretonischer Ruhm"))
    assert "title_exact" in scored.why and scored.fatal == ""
    assert score(want(title="Ein ganz anderer Name"), cand("dnb", title="Bretonischer Ruhm")).fatal == "title_differs"


def test_the_title_of_the_folder_is_a_second_title_the_book_may_be_known_by() -> None:
    from types import SimpleNamespace

    edition = SimpleNamespace(kind="audio", title="Kommissar Dupins zwölfter Fall", subtitle="", language="", isbn="",
                              asin="", series_name="", series_pos=None, duration=549 * 60, original_title="")
    path = "Jean Bannalec/Bretonischer Ruhm - Kommissar Dupins zwölfter Fall (2025)/Audio/001.mp3"
    wanted = wanted_from(edition, ["Jean Bannalec"], path)
    assert wanted.alt_titles == ["Bretonischer Ruhm"] and wanted.name_volume == 12.0
    right = cand("dnb", title="Bretonischer Ruhm", authors=["Jean Bannalec"], series="Kommissar Dupin", series_pos=12.0)
    wrong = cand("audible", title="Bretonischer Glanz", authors=["Jean Bannalec"], series="Kommissar Dupin",
                 series_pos=15.0, runtime_min=549)
    decision = decide(wanted, [right, wrong])
    assert decision.best is not None and decision.best.candidate.title == "Bretonischer Ruhm"
    # The running time of another book is never the proof of this one.
    assert decide(wanted, [wrong]).state != "sure"


# ---- A series in the title ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("title,series,pos,rest", [
    ("Walter Hartung 04 - Rachefrühling", "Walter Hartung", 4.0, "Rachefrühling"),
    ("Carl Moor 01 (Erbarmen)", "Carl Moor", 1.0, "Erbarmen"),
    ("Anselm & Quist 03 - Tiefe Wunden", "Anselm & Quist", 3.0, "Tiefe Wunden"),
    ("Reihe Eins Band 3: Der Titel", "Reihe Eins", 3.0, "Der Titel"),
    ("Reihe Eins #3 - Der Titel", "Reihe Eins", 3.0, "Der Titel"),
])
def test_a_series_with_its_number_in_front_of_the_title_is_cut_off_the_title(title: str, series: str, pos: float,
                                                                           rest: str) -> None:
    found = names.split_prefix_series(title, "")
    assert found is not None and (found.series, found.pos, found.title) == (series, pos, rest)


def test_a_plain_number_is_a_volume_only_when_the_names_of_the_folder_and_file_bear_the_title_out() -> None:
    assert names.split_prefix_series("Reihe Eins 3 - Der Titel", "") is None
    goes_on = names.split_prefix_series("Anselm Quist 01 - und der Stein am Fluss", "")
    assert goes_on is not None
    assert (goes_on.title, goes_on.series, goes_on.pos) == ("Anselm Quist und der Stein am Fluss", "Anselm Quist", 1)
    assert names.split_series_title("Anselm Quist Band 2 - and the Lost Key").title == "Anselm Quist and the Lost Key"
    assert names.split_series_title("Anselm Quist Band 2 - Ahnenland").title == "Ahnenland"
    found = names.split_prefix_series("Reihe Eins 3 - Der Titel", "Der Titel (2009) Der Titel - Autorin")
    assert found is not None and found.title == "Der Titel" and found.pos == 3.0
    assert names.split_prefix_series("Lantern 451: A Novel", "") is None
    assert names.split_prefix_series("Apollo 13 - The Film", "something else") is None


def test_the_case_of_a_series_is_a_series_and_a_volume_and_no_title() -> None:
    assert names.ordinal_series("Kommissar Dupins zwölfter Fall") == ("Kommissar Dupin", 12.0)
    assert names.ordinal_series("Inspector Marlowe's third case") == ("Inspector Marlowe", 3.0)
    assert names.ordinal_series("Der zwölfte Mann") is None
    folder = names.folder_book("Bretonischer Ruhm - Kommissar Dupins zwölfter Fall (2025)", depth=2)
    assert (folder.title, folder.series, folder.pos, folder.year) == ("Bretonischer Ruhm", "Kommissar Dupin", 12.0, 2025)


def test_the_name_of_a_box_is_no_series_but_the_series_it_names_is() -> None:
    sub = names.folder_book("03 - Todesrauschen", depth=3, parent="Auris 5 - Tödlicher Schall (2024)")
    assert (sub.title, sub.series, sub.pos) == ("Todesrauschen", "Auris", 3.0)
    plain = names.folder_book("1.-.Sturmzeit", depth=3, parent="Sturmzeit (1991)")
    assert (plain.title, plain.series, plain.pos) == ("Sturmzeit", "Sturmzeit", 1.0)


def test_the_hint_of_the_kind_of_recording_is_cut_off_the_title() -> None:
    wanted = want(title="Passagier 23: Das ungekuerzte Hoerspiel")
    scored = score(wanted, cand("audible", title="Passagier 23", runtime_min=600))
    assert scored.fatal == "" and scored.score >= rule.SURE


# ---- Translations ------------------------------------------------------------------------------------------------


def test_a_book_of_the_same_author_and_series_under_another_title_is_a_possible_translation_never_sure() -> None:
    wanted = want(title="Das Paket", language="de", series="Reihe Eins", series_pos=2.0, year=2016)
    english = cand("hardcover", title="The Package", languages=["en"], series="Reihe Eins", series_pos=2.0, year=2016)
    decision = decide(wanted, [english])
    assert (decision.state, decision.reason) == ("maybe", "possible_translation")
    assert decision.best is not None and decision.best.candidate.title == "The Package"
    # Another author, or no sign of the same book, is nothing.
    other = cand("hardcover", title="The Package", authors=["Somebody Else"], languages=["en"], series="Reihe Eins",
                 series_pos=2.0)
    assert decide(wanted, [other]).state == "open"
    assert decide(wanted, [cand("hardcover", title="The Package", languages=["en"], year=1980)]).state == "open"


# ---- A series after the title, in the folder with three parts ---------------------------------------------------------


@pytest.mark.parametrize("title,series,pos,rest", [
    ("Erbarmen (Carl Moor 1)", "Carl Moor", 1.0, "Erbarmen"),
    ("Tiefe Wunden (Anselm & Quist 7)", "Anselm & Quist", 7.0, "Tiefe Wunden"),
    ("Die Unverbesserlichen - Der große Coup - Die Unverbesserlichen 1", "Die Unverbesserlichen", 1.0,
     "Die Unverbesserlichen - Der große Coup"),
    ("Das Haus - Nachtreihe Band 2", "Nachtreihe", 2.0, "Das Haus"),
])
def test_a_series_with_its_number_after_the_title_is_cut_off_the_title(title: str, series: str, pos: float,
                                                                      rest: str) -> None:
    found = names.split_suffix_series(title)
    assert found is not None and (found.series, found.pos, found.title) == (series, pos, rest)


@pytest.mark.parametrize("title", ["Titel (Teil 2)", "Das Boot (Director's Cut)", "Apollo 13", "Das Haus - Anderes Haus 2",
                                   "Windows (Version)", "Nachtglas"])
def test_a_title_with_no_series_at_its_end_is_left_alone(title: str) -> None:
    assert names.split_suffix_series(title) is None


def test_a_folder_of_a_title_a_series_and_a_volume_says_all_three() -> None:
    folder = names.folder_book("Schändung - Carl Moor, Sonderdezernat Q - Band 2 (2025)", depth=2)
    assert (folder.title, folder.series, folder.pos, folder.year, folder.author) == (
        "Schändung", "Carl Moor, Sonderdezernat Q", 2.0, 2025, "")
    named = names.folder_book("Die Unverbesserlichen - Der große Coup - Die Unverbesserlichen 1 (2022)", depth=2)
    assert (named.title, named.series, named.pos) == ("Die Unverbesserlichen - Der große Coup", "Die Unverbesserlichen", 1.0)
    plain = names.folder_book("Morgen, Klufti, wird's was geben - Eine Weihnachtsgeschichte (2021)", depth=2)
    assert plain.series == "" and plain.title == "Morgen, Klufti, wird's was geben - Eine Weihnachtsgeschichte"


def test_a_subtitle_only_one_side_has_costs_three_points_against_the_same_title() -> None:
    assert rule.P_TITLE_MAIN == rule.P_TITLE_EXACT - 3
    scored = score(want(title="Morgen, Klufti: Eine Weihnachtsgeschichte"), cand("dnb", title="Morgen, Klufti"))
    assert "title_main" in scored.why and scored.fatal == ""


def test_a_title_that_only_shares_most_of_its_words_is_never_sure_not_even_with_the_running_time_and_the_volume() -> None:
    wanted = want(title="Die dunkle Stadt am grauen Meer hinter Glas", series="Dupin", series_pos=11.0, duration=556 * 60)
    other = cand("audible", title="Die dunkle Stadt am grauen Strand hinter Glas", series="Dupin", series_pos=11.0,
                 runtime_min=556)
    scored = score(wanted, other)
    assert "title_close" in scored.why and scored.proof and scored.capped
    assert decide(wanted, [other]).state == "maybe"


def test_the_sentence_of_the_list_names_a_second_source_and_a_running_time_when_a_recording_lacks_them() -> None:
    from app.services.match.gap import gap_of

    proposal = {"score": 88, "missing": ["no_runtime", "one_source"], "why": ["title_exact", "author_match"],
                "fatal": "", "c": {"source": "dnb", "title": "Nachtglas", "authors": ["Rune Falkner"]}}
    gap = gap_of("runtime_unproven", [proposal])
    assert gap is not None and gap["points"] == 88 and gap["missing"] == ["second_source", "runtime"] and gap["join"] == "or"
    # A source that gives a running time (it only does not fit) cannot be asked for one: only a second source helps.
    with_time = {**proposal, "missing": ["one_source"], "c": {**proposal["c"], "runtime_min": 700}}
    again = gap_of("runtime_unproven", [with_time])
    assert again is not None and again["missing"] == ["second_source"]
    # Another reason keeps the words of the points.
    other = gap_of("low_score", [{**proposal, "score": 60, "missing": ["no_year"], "c": {**proposal["c"], "year": 2019}}])
    assert other is not None and "second_source" not in other["missing"]
