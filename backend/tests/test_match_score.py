"""How a book is told from the others: the points, the fatal differences, the running time of a recording, and the three
answers. Pure rule, no database and no network; every case is a lesson of the measuring run or a trap of the sources."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.match import score as rule
from app.services.match.score import MAYBE, SURE, Wanted, author_state, decide, score, volume_in
from app.services.sources.model import Candidate


def want(**kw: Any) -> Wanted:
    base: dict[str, Any] = {"kind": "ebook", "title": "The Quiet Orchard", "authors": ["Mara Lindqvist"], "language": "en"}
    return Wanted(**{**base, **kw})


def cand(source: str = "openlibrary", **kw: Any) -> Candidate:
    base: dict[str, Any] = {"source": source, "key": kw.pop("key", "K1"), "title": "The Quiet Orchard",
                            "authors": ["Mara Lindqvist"], "languages": ["en"]}
    return Candidate(**{**base, **kw})


# ---- The ISBN beats everything ----------------------------------------------------------------------------------


def test_the_same_isbn_is_the_same_book_whatever_one_thing_else_says() -> None:
    wanted = want(isbn="9783999900018", title="Der stille Obstgarten", language="de")
    other = cand(title="Completely Different", languages=["fr"], isbns=["9783999900018"])
    scored = score(wanted, other)
    assert scored.certain and scored.fatal == "" and scored.score == 100 and scored.why[0] == "isbn_match"
    assert decide(wanted, [other]).state == "sure"
    # An author who is written another way, or is another one (a translator, an editor), is still one thing.
    by_another = cand(authors=["Somebody Else"], isbns=["9783999900018"], languages=["de"], title="Der stille Obstgarten")
    assert score(wanted, by_another).certain and decide(wanted, [by_another]).state == "sure"


def conflicting(**kw: Any) -> Candidate:
    return cand(isbns=["9783999900018"], **kw)


@pytest.mark.parametrize(("kw", "codes"), [
    # A companion has the ISBN of the book in the file: the file made a mistake, on its own reason enough.
    ({"title": "Das offizielle Begleitbuch zu Der stille Obstgarten", "languages": ["de"]}, ["companion"]),
    ({"title": "Der stille Obstgarten Box Set", "languages": ["de"]}, ["collection"]),
    # Another title and another author.
    ({"title": "Completely Different", "authors": ["Somebody Else"], "languages": ["de"]},
     ["title_differs", "author_differs"]),
    # Another title and, for a source whose records are one edition in one language, another language.
    ({"title": "Completely Different", "authors": ["Mara Lindqvist"], "languages": ["en"], "source": "audible"},
     ["title_differs", "language_differs"]),
    # Another author and another language.
    ({"title": "Der stille Obstgarten", "authors": ["Somebody Else"], "languages": ["en"], "source": "dnb"},
     ["author_differs", "language_differs"]),
])
def test_an_isbn_that_leads_to_another_book_is_at_most_maybe_and_says_why(kw: dict[str, Any], codes: list[str]) -> None:
    wanted = want(isbn="9783999900018", title="Der stille Obstgarten", authors=["Mara Lindqvist"], language="de")
    scored = score(wanted, conflicting(**kw))
    assert not scored.certain and scored.fatal == "" and scored.capped
    assert scored.why[0] == "isbn_match" and "isbn_conflict" in scored.why and set(codes) <= set(scored.why)
    assert MAYBE <= scored.score < SURE
    decision = decide(wanted, [conflicting(**kw)])
    assert (decision.state, decision.reason) == ("maybe", "isbn_conflict")


def test_the_companion_of_another_author_with_the_isbn_of_the_file_waits_for_a_person() -> None:
    wanted = want(isbn="9783999900018", title="Der stille Obstgarten", authors=["Mara Lindqvist"], language="de")
    companion = conflicting(title="Der stille Obstgarten. Das Begleitbuch", authors=["Jemand Anderes"], languages=["de"])
    decision = decide(wanted, [companion])
    assert decision.state == "maybe" and decision.reason == "isbn_conflict"
    assert [s.candidate.title for s in decision.ranked] == ["Der stille Obstgarten. Das Begleitbuch"]


def test_a_good_candidate_beats_one_that_only_shares_a_wrong_isbn() -> None:
    wanted = want(isbn="9783999900018", title="Der stille Obstgarten", authors=["Mara Lindqvist"], language="de")
    stranger = conflicting(key="S1", title="Completely Different", authors=["Somebody Else"], languages=["de"])
    right = cand("hardcover", key="H1", title="Der stille Obstgarten", authors=["Mara Lindqvist"], languages=["de"])
    # (A second source tells the same book: an e-book is not sure on one catalogue's word without the ISBN of its own.)
    also = cand("openlibrary", key="O1", title="Der stille Obstgarten", authors=["Mara Lindqvist"], languages=["de"])
    decision = decide(wanted, [stranger, right, also])
    assert decision.state == "sure" and decision.best is not None and decision.best.candidate.key == "H1"


def test_an_asin_that_leads_to_another_book_is_no_more_certain_than_an_isbn() -> None:
    wanted = want(kind="audio", title="Die Uhrmacherin", authors=["Ada Feldkamp"], language="de", asin="B0EXAMPL01")
    stranger = cand("audible", title="Completely Different", authors=["Somebody Else"], asin="B0EXAMPL01",
                    languages=["de"])
    decision = decide(wanted, [stranger])
    assert (decision.state, decision.reason) == ("maybe", "isbn_conflict")


def test_a_different_isbn_decides_nothing_by_itself() -> None:
    scored = score(want(isbn="9783999900018"), cand(isbns=["9783999900025"]))
    assert not scored.certain and scored.score == 80


def test_the_same_asin_settles_a_recording() -> None:
    wanted = want(kind="audio", title="Die Uhrmacherin", authors=["Ada Feldkamp"], language="de", asin="B0EXAMPL01")
    scored = score(wanted, cand("audible", title="Another Title", authors=["Ada Feldkamp"], asin="b0exampl01",
                                languages=["de"]))
    titled_otherwise = cand("audible", asin="B0EXAMPL01", title="Whatever", authors=["Ada Feldkamp"], languages=["de"])
    assert scored.certain and decide(wanted, [titled_otherwise]).state == "sure"


# ---- Authors ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("wanted", "found", "state"), [
    (["Mara Lindqvist"], ["Mara Lindqvist"], "match"),
    (["Mara Lindqvist"], ["Lindqvist, Mara"], "match"),
    (["Mara Lindqvist"], ["M. Lindqvist"], "match"),
    (["Lindqvist, Mara"], ["mara lindqvist"], "match"),
    (["Mara Lindqvist"], ["Hanna Lindqvist"], "none"),
    (["Silas Marlowe"], ["Hedda Thorne"], "none"),
    (["Silas Marlowe"], ["Marlowe"], "weak"),
    (["Mara Lindqvist"], ["Hanna Lindqvist", "Mara Lindqvist"], "match"),
    (["Vesper"], ["Vesper"], "match"),
    ([], ["Mara Lindqvist"], "unknown"),
    (["Mara Lindqvist"], [], "unknown"),
    (["Ulla K. Le Moor"], ["Le Moor, Ulla K."], "match"),
])
def test_an_author_fits_by_last_name_and_first_name_or_initial_never_by_the_last_name_alone(
        wanted: list[str], found: list[str], state: str) -> None:
    assert author_state(wanted, found) == state


def test_another_author_with_the_same_last_name_is_not_the_book() -> None:
    scored = score(want(), cand(authors=["Hanna Lindqvist"]))
    assert scored.fatal == "author_differs"
    assert decide(want(), [cand(authors=["Hanna Lindqvist"])]).state == "open"


def test_a_last_name_alone_is_a_proposal_for_a_person_never_sure() -> None:
    decision = decide(want(), [cand(authors=["Lindqvist"], languages=["en"])])
    assert decision.state == "maybe" and decision.reason == "author_weak"
    assert decision.best is not None and decision.best.capped


def test_a_book_without_a_known_author_is_never_sure() -> None:
    decision = decide(want(authors=[]), [cand()])
    assert decision.state == "maybe" and decision.reason == "author_unknown"


# ---- Titles -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("wanted", "found", "fatal"), [
    ("The Quiet Orchard", "The Quiet Orchard", ""),
    ("The Quiet Orchard (Unabridged)", "The Quiet Orchard", ""),
    ("The Quiet Orchard", "The Quiet Orchard: A Novel", ""),
    ("The Quiet Orchard", "Quiet Orchard, The", ""),
    ("The Quiet Orchard: A Novel", "The Quiet Orchard", ""),
    ("Der stille Obstgarten - Ein Roman", "Der stille Obstgarten", ""),
    ("Nebelchor", "Anselm Quist Bd. 1: Nebelchor", ""),
    ("Moorland", "Moorland: Return", ""),
    ("Moorland", "Moorland Return", "title_differs"),
    ("Salt Harbor", "The Long Tide", "title_differs"),
])
def test_titles_are_the_same_with_their_noise_and_series_prefix_taken_off(wanted: str, found: str, fatal: str) -> None:
    assert score(want(title=wanted), cand(title=found)).fatal == fatal


def test_a_subtitle_only_one_side_has_is_a_question_not_a_sure_thing() -> None:
    """"Moorland" and "Moorland: Return" are two books, "The Quiet Orchard" and "The Quiet Orchard: Part One" may be one."""
    scored = score(want(title="Moorland", authors=["Fred Hartwell"]), cand(title="Moorland: Return", authors=["Fred Hartwell"]))
    assert scored.fatal == "" and "title_main" in scored.why and scored.score < rule.SURE
    assert decide(want(title="Moorland", authors=["Fred Hartwell"]), [cand(title="Moorland: Return", authors=["Fred Hartwell"])]).state == "maybe"
    whole = score(want(title="Moorland", authors=["Fred Hartwell"]), cand(title="Moorland", authors=["Fred Hartwell"]))
    assert whole.score == 80 and "title_exact" in whole.why


def test_a_title_inside_a_longer_one_earns_fewer_points_than_the_same_title() -> None:
    exact = score(want(title="Salt Harbor Chronicles"), cand(title="Salt Harbor Chronicles"))
    part = score(want(title="Salt Harbor Chronicles"), cand(title="Salt Harbor Chronicles Part Two Of Three"))
    assert part.fatal == "" and part.score < exact.score and "title_part" in part.why


def test_a_translated_title_in_the_language_of_the_file_is_the_title() -> None:
    wanted = want(title="Der stille Obstgarten", language="de")
    work = cand("hardcover", languages=["en", "de"], titles={"de": ["Der stille Obstgarten"], "en": ["The Quiet Orchard"]})
    assert score(wanted, work).score == 80 and score(wanted, work).fatal == ""
    # Without the German title of the book the English one is another title.
    assert score(wanted, cand("hardcover", languages=["en", "de"])).fatal == "title_differs"


def test_the_original_title_the_file_names_finds_the_original() -> None:
    wanted = want(title="Der stille Obstgarten", language="de", original_title="The Quiet Orchard")
    assert score(wanted, cand(languages=["en", "de"])).fatal == ""


def test_companions_and_collections_are_not_the_book() -> None:
    assert score(want(), cand(title="Summary and Study Guide: The Quiet Orchard")).fatal == "companion"
    assert score(want(), cand(title="The Quiet Orchard: Companion Workbook")).fatal == "companion"
    assert score(want(), cand(title="The Quiet Orchard Box Set")).fatal == "collection"
    assert score(want(title="Zusammenfassung: Der stille Obstgarten"), cand(
        title="Zusammenfassung: Der stille Obstgarten")).fatal == ""


# ---- Volumes ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "volume"), [
    ("Salt Harbor Band 2", 2.0), ("Salt Harbor Bd. 03", 3.0), ("Salt Harbor, Book 4", 4.0), ("Club 5", 5.0),
    ("Die Chroniken 04", 4.0), ("Salt Harbor #7", 7.0), ("01 - Nebelchor", 1.0), ("Lantern 451", None),
    ("Salt Harbor", None), ("2131", None), ("Gambit 17 Revisited", None),
])
def test_the_volume_a_title_names(title: str, volume: float | None) -> None:
    assert volume_in(title) == volume


def test_band_two_is_not_band_three() -> None:
    wanted = want(title="Salt Harbor", authors=["Theo Ravensberg"], series="Harbor Chronicles", series_pos=2)
    right = cand(title="Salt Harbor", authors=["Theo Ravensberg"], series="Harbor Chronicles", series_pos=2.0)
    wrong = cand(title="Salt Harbor", authors=["Theo Ravensberg"], series="Harbor Chronicles", series_pos=3.0)
    assert score(wanted, right).fatal == "" and "volume_ok" in score(wanted, right).why
    assert score(wanted, wrong).fatal == "volume_differs"


def test_a_bare_number_in_the_title_is_the_volume_too() -> None:
    wanted = want(title="Der Club 5", authors=["Jonas Okafor"], language="de")
    assert score(wanted, cand(title="Der Club 4", authors=["Jonas Okafor"], languages=["de"])).fatal != ""
    assert score(wanted, cand(title="Der Club 5", authors=["Jonas Okafor"], languages=["de"])).fatal == ""


def test_the_place_in_another_series_says_nothing() -> None:
    wanted = want(series="Orchard Tales", series_pos=1)
    other = cand(series="Some Other Saga", series_pos=3.0)
    assert score(wanted, other).fatal == ""


# ---- Language ---------------------------------------------------------------------------------------------------


def test_a_recording_in_another_language_is_not_the_book() -> None:
    wanted = want(kind="audio", title="Die Uhrmacherin", authors=["Ada Feldkamp"], language="de")
    assert score(wanted, cand("audible", title="Die Uhrmacherin", authors=["Ada Feldkamp"],
                              languages=["en"])).fatal == "language_differs"


def test_a_work_that_the_source_does_not_list_in_this_language_needs_a_person() -> None:
    decision = decide(want(language="sv"), [cand(languages=["en", "de"])])
    assert decision.state == "maybe" and decision.reason == "language_missing"


def test_a_file_without_a_language_cannot_be_sure() -> None:
    decision = decide(want(language=""), [cand()])
    assert decision.state == "maybe" and decision.best is not None and decision.best.score == 70


def test_the_year_is_a_weak_sign() -> None:
    assert score(want(year=2019), cand(year=2020)).score == 85 and score(want(year=2019), cand(year=2010)).score == 80


# ---- Recordings -------------------------------------------------------------------------------------------------

AUDIO = {"title": "Die Uhrmacherin", "authors": ["Ada Feldkamp"], "languages": ["de"]}


def product(asin: str, minutes: int, variant: str, title: str = "Die Uhrmacherin") -> Candidate:
    return cand("audible", key=asin, asin=asin, runtime_min=minutes, variant=variant, **{**AUDIO, "title": title})


def recording(seconds: int) -> Wanted:
    return want(kind="audio", language="de", duration=seconds, **{k: v for k, v in AUDIO.items() if k != "languages"})


PRODUCTS = [product("B0EXAMPL01", 600, "unabridged"), product("B0EXAMPL02", 283, "abridged"),
            product("B0EXAMPL03", 359, "dramatized", "Die Uhrmacherin (Hörspiel)")]


@pytest.mark.parametrize(("seconds", "asin", "variant"), [
    (600 * 60, "B0EXAMPL01", "unabridged"), (283 * 60 + 20, "B0EXAMPL02", "abridged"),
    (359 * 60 - 40, "B0EXAMPL03", "dramatized"), (598 * 60, "B0EXAMPL01", "unabridged"),
])
def test_the_running_time_says_which_recording_it_is(seconds: int, asin: str, variant: str) -> None:
    decision = decide(recording(seconds), PRODUCTS)
    assert decision.state == "sure" and decision.best is not None
    assert decision.best.candidate.asin == asin and decision.best.candidate.variant == variant and decision.best.proof


@pytest.mark.parametrize(("minutes", "proves"), [(600, True), (631, True), (632, False), (572, True), (571, False)])
def test_five_percent_is_the_limit_of_the_proof(minutes: int, proves: bool) -> None:
    scored = score(recording(36000), product("B0EXAMPL09", minutes, "unabridged"))
    assert scored.proof is proves


@pytest.mark.parametrize(("minutes", "plausible", "differs"), [(631, False, False), (632, True, False), (750, True, False),
                                                                (751, False, True), (900, False, True)])
def test_up_to_a_fifth_shorter_than_the_product_is_plausible_and_beyond_it_another_recording(
        minutes: int, plausible: bool, differs: bool) -> None:
    scored = score(recording(36000), product("B0EXAMPL09", minutes, "unabridged"))
    assert scored.plausible is plausible and ("runtime_differs" in scored.against) is differs


def test_a_recording_no_running_time_proves_is_never_sure() -> None:
    for seconds in (0, 900 * 60, 450 * 60):
        decision = decide(recording(seconds), PRODUCTS)
        assert decision.state == "maybe" and decision.reason == "runtime_unproven", seconds


def test_the_title_and_the_author_alone_do_not_prove_a_recording_even_with_a_book_source_agreeing() -> None:
    from_book_source = cand("hardcover", **AUDIO)
    decision = decide(recording(900 * 60), [from_book_source])
    assert decision.state == "maybe" and decision.reason == "runtime_unproven"


# ---- The answers ------------------------------------------------------------------------------------------------


def test_the_three_answers() -> None:
    assert decide(want(), [cand(), cand("hardcover", key="H1")]).state == "sure"
    assert decide(want(), [cand()]).state == "maybe", "one catalogue alone, no ISBN: an e-book is a proposal"
    assert decide(want(language=""), [cand()]).state == "maybe"
    assert decide(want(), []).state == "open" and decide(want(), []).reason == "nothing_found"
    nothing_fits = decide(want(), [cand(title="Something Else Entirely", authors=["Other Person"])])
    assert nothing_fits.state == "open" and nothing_fits.reason == "nothing_fits" and nothing_fits.best is None
    assert len(nothing_fits.ranked) == 1, "what was found is kept to be looked at"


def test_a_candidate_that_cannot_be_the_book_never_wins_over_one_that_can() -> None:
    right = cand(key="right")
    wrong = cand(key="wrong", authors=["Hanna Lindqvist"], isbns=[], weight=1_000_000)
    decision = decide(want(), [wrong, right])
    assert decision.best is not None and decision.best.candidate.key == "right"


def test_other_sources_that_agree_add_to_the_best_one() -> None:
    decision = decide(want(), [cand("hardcover", key="h", weight=500), cand("openlibrary", key="o"),
                               cand("dnb", key="d", languages=["de"], title="Anderes Buch", authors=["Jemand Anderes"])])
    assert decision.state == "sure"
    assert {s.candidate.source for s in decision.support} == {"openlibrary"}


def test_two_sources_naming_different_books_make_it_a_question() -> None:
    wanted = want(authors=["Mara Lindqvist", "Jonas Okafor"])
    decision = decide(wanted, [cand("hardcover", key="a", authors=["Mara Lindqvist"]),
                               cand("openlibrary", key="b", authors=["Jonas Okafor"])])
    assert decision.state == "maybe" and decision.reason == "sources_disagree"


def test_two_records_of_one_source_that_are_equally_good_and_differ_make_it_a_question() -> None:
    wanted = want(authors=["Mara Lindqvist", "Jonas Okafor"])
    decision = decide(wanted, [cand(key="a", authors=["Mara Lindqvist"]), cand(key="b", authors=["Jonas Okafor"])])
    assert decision.state == "maybe" and decision.reason == "ambiguous"


def test_the_same_book_twice_in_one_source_is_no_ambiguity() -> None:
    decision = decide(want(), [cand(key="a", weight=3), cand(key="b", weight=9), cand("hardcover", key="h")])
    assert decision.state == "sure" and decision.best is not None and decision.best.candidate.key == "b"


def test_the_thresholds_are_the_ones_the_lessons_set() -> None:
    assert (rule.SURE, rule.MAYBE, rule.RUNTIME_PROOF) == (80, 45, (0.95, 1.05))
    assert rule.P_TITLE_EXACT + rule.P_AUTHOR + rule.P_LANGUAGE == rule.SURE
