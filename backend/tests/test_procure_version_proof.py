"""Which version of a recording came: only a source that says which version it lists proves one.

A library catalogue names a running time and no version. A shortened reading it lists must not count as the unabridged
one, and a length that fits an unabridged and a shortened recording proves neither. Only invented books."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import EDITION_AUDIO
from app.services.discover import foreign
from app.services.procure import checking, formats, importing
from app.services.sources.model import Candidate


def recording(minutes: int, *, album: str = "Die Nebelbahn") -> checking.Unit:
    item = SimpleNamespace(path="01.mp3", format="mp3", size=minutes * 60 * 16000, origin="source", unit="",
                           probe={"format": "mp3", "duration": minutes * 60.0, "album": album, "title": album,
                                  "authors": ["Mara Lind"], "language": "de"})
    return checking.units_of([item])[0]


def wanted(runtimes: dict[str, list[int]], **config: Any) -> checking.Wanted:
    return checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de",
                           subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                    "authors": ["Mara Lind"], "series": "", "position": None},
                           config={**formats.default_config(), **config}, runtimes=runtimes)


def minutes(*values: int) -> list[int]:
    return [value * 60 for value in values]


def candidate(source: str, key: str, title: str, runtime: int, *, variant: str = "", language: str = "de",
              author: str = "Mara Lind") -> Candidate:
    return Candidate(source=source, key=key, title=title, authors=[author], languages=[language],
                     runtime_min=runtime, variant=variant)


def test_a_time_of_a_source_that_says_no_version_proves_nothing() -> None:
    assert checking.runtime_variant(129 * 60, {checking.UNSAID: minutes(129)})[:2] == ("", False)


def test_a_shortened_reading_a_library_lists_is_a_question_and_no_unabridged_book() -> None:
    verdict = checking.judge_units([recording(129)], wanted({checking.UNSAID: minutes(129)}))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "unproven")


def test_a_time_a_shop_says_is_abridged_is_that_whatever_a_library_lists() -> None:
    times = {"abridged": minutes(129), checking.UNSAID: minutes(129)}
    assert checking.runtime_variant(129 * 60, times)[:2] == ("abridged", True)
    verdict = checking.judge_units([recording(129)], wanted(times))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "abridged")


def test_a_length_of_two_versions_proves_neither() -> None:
    times = {"unabridged": minutes(400), "abridged": minutes(395)}
    assert checking.runtime_variant(398 * 60, times)[:2] == ("", False)
    verdict = checking.judge_units([recording(398)], wanted(times))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "unproven")


@pytest.mark.parametrize(("loaded", "full"), [(369, 421), (618, 704), (639, 710), (750, 800)])
def test_a_reading_a_little_too_short_is_a_question(loaded: int, full: int) -> None:
    variant, proven, values = checking.runtime_variant(loaded * 60, {"unabridged": minutes(full)})
    assert (variant, proven) == (checking.SHORTER, False) and values["unabridged"] == full * 60
    verdict = checking.judge_units([recording(loaded)], wanted({"unabridged": minutes(full)}))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "shorter")
    assert verdict.values["loaded"] == loaded * 60


def test_a_length_within_the_proof_stays_the_unabridged_reading() -> None:
    assert checking.runtime_variant(761 * 60, {"unabridged": minutes(800)})[:2] == ("unabridged", True)


def test_a_word_in_the_names_settles_a_reading_a_little_too_short() -> None:
    verdict = checking.judge_units([recording(618, album="Die Nebelbahn (ungekürzt)")],
                                   wanted({"unabridged": minutes(704)}))
    assert verdict.outcome == "take" and verdict.variant == "unabridged"


def test_with_shortened_readings_allowed_nothing_is_asked() -> None:
    verdict = checking.judge_units([recording(618)], wanted({"unabridged": minutes(704)}, abridged=True))
    assert verdict.outcome == "take" and verdict.variant == "unabridged" and not verdict.variant_proven


def test_an_upload_is_never_asked_about_it() -> None:
    found = wanted({"unabridged": minutes(704)})
    found.upload = True
    verdict = checking.judge_units([recording(618)], found)
    assert verdict.outcome == "take" and verdict.variant == ""


def play(minutes_long: int, **tags: Any) -> checking.Unit:
    item = SimpleNamespace(path="01.mp3", format="mp3", size=minutes_long * 60 * 16000, origin="source", unit="",
                           probe={"format": "mp3", "duration": minutes_long * 60.0, "album": "Die Nebelbahn",
                                  "title": "Die Nebelbahn", "authors": ["Mara Lind"], "language": "de", **tags})
    return checking.units_of([item])[0]


def test_a_play_is_turned_down_where_plays_are_not_taken() -> None:
    verdict = checking.judge_units([play(80, genre="Hörspiel")], wanted({}))
    assert (verdict.outcome, verdict.code) == ("reject", "dramatized")


def test_a_play_for_a_book_with_a_known_reading_is_a_question_even_where_plays_are_allowed() -> None:
    verdict = checking.judge_units([play(322, genre="Hörspiel")], wanted({"unabridged": minutes(811)}, dramatized=True))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "dramatized")
    assert verdict.values["unabridged"] == 811 * 60


def test_a_play_of_a_book_nobody_lists_a_reading_of_is_taken_where_plays_are_allowed() -> None:
    verdict = checking.judge_units([play(322, genre="Hörspiel")], wanted({}, dramatized=True))
    assert verdict.outcome == "take" and verdict.variant == "dramatized" and verdict.variant_proven


def test_a_person_who_keeps_it_keeps_a_play() -> None:
    found = wanted({"unabridged": minutes(811)}, dramatized=True)
    found.decision = {"variant": "dramatized"}
    verdict = checking.judge_units([play(322, genre="Hörspiel")], found)
    assert verdict.outcome == "take" and verdict.variant == "dramatized"


def test_a_cast_in_the_tags_is_a_question_and_no_unabridged_book() -> None:
    cast = ["Ada Eins", "Ben Zwei", "Cleo Drei", "Dov Vier", "Eva Fuenf"]
    verdict = checking.judge_units([play(106, narrators=cast)], wanted({"unabridged": minutes(367)}))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "dramatized")


def test_a_cast_in_the_tags_goes_in_where_plays_are_allowed_and_nobody_lists_a_reading() -> None:
    cast = ["Ada Eins", "Ben Zwei", "Cleo Drei", "Dov Vier", "Eva Fuenf"]
    verdict = checking.judge_units([play(106, narrators=cast)], wanted({}, dramatized=True))
    assert verdict.outcome == "take"


def test_the_length_of_a_play_a_shop_lists_is_a_question() -> None:
    times = {"dramatized": minutes(322), "unabridged": minutes(811)}
    assert checking.runtime_variant(322 * 60, times)[:2] == ("dramatized", True)
    verdict = checking.judge_units([recording(322)], wanted(times))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "dramatized")


def test_the_answer_keeps_a_play_as_a_play(operator: Any, net: Any, tmp_path: Any,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from .filing_helpers import audio_wish, download_roots, editions, finish, the_download
    from .filing_helpers import recording as parts
    from .library_helpers import run_jobs

    roots = download_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(importing, "_runtimes", lambda *_args: {"unabridged": [48660], "abridged": [],
                                                                "dramatized": [], checking.UNSAID: []})
    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "dramatized": True})
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    parts(folder, parts=2, seconds=4.0, album="Die Laternenstraße (Hörspiel)")
    finish(sab, job, folder)
    row = the_download()
    assert (row.problem_code, row.problem_values["reason"]) == ("runtime", "dramatized")
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "keep"})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert audio.variant == "dramatized"


@pytest.mark.parametrize("allowed", [False, True])
def test_a_genre_against_a_running_time_that_proves_the_reading_is_a_question(allowed: bool) -> None:
    verdict = checking.judge_units([play(600, genre="Hörspiel")], wanted({"unabridged": minutes(600)}, dramatized=allowed))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "genre")


def test_a_genre_with_a_length_that_fits_no_reading_stays_a_play() -> None:
    verdict = checking.judge_units([play(322, genre="Hörspiel")], wanted({"unabridged": minutes(811)}))
    assert (verdict.outcome, verdict.code) == ("reject", "dramatized")


def test_a_genre_with_a_length_near_a_reading_but_no_proof_stays_a_play() -> None:
    verdict = checking.judge_units([play(690, genre="Hörspiel")], wanted({"unabridged": minutes(600)}))
    assert (verdict.outcome, verdict.code) == ("reject", "dramatized")


def test_a_genre_with_no_time_to_hold_it_against_stays_a_play() -> None:
    verdict = checking.judge_units([play(600, genre="Hörspiel")], wanted({checking.UNSAID: minutes(600)}))
    assert (verdict.outcome, verdict.code) == ("reject", "dramatized")


def test_a_word_in_the_names_is_more_than_a_genre() -> None:
    verdict = checking.judge_units([play(600, genre="Hörspiel", album="Die Nebelbahn (Hörspiel)")],
                                   wanted({"unabridged": minutes(600)}))
    assert (verdict.outcome, verdict.code) == ("reject", "dramatized")


def test_the_answer_makes_it_the_reading() -> None:
    found = wanted({"unabridged": minutes(600)})
    found.decision = {"variant": "unabridged"}
    verdict = checking.judge_units([play(600, genre="Hörspiel")], found)
    assert verdict.outcome == "take" and verdict.variant == "unabridged"


def test_the_reason_of_the_question_leads_to_the_reading(operator: Any, net: Any, tmp_path: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from .filing_helpers import audio_wish, download_roots, editions, finish, the_download
    from .filing_helpers import recording as parts
    from .library_helpers import run_jobs

    roots = download_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(importing, "_runtimes", lambda *_args: {"unabridged": [8], "abridged": [], "dramatized": [],
                                                                checking.UNSAID: []})
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    parts(folder, parts=2, seconds=4.0, genre="Hörspiel")
    finish(sab, job, folder)
    row = the_download()
    assert (row.problem_code, row.problem_values["reason"]) == ("runtime", "genre")
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "keep"})
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert audio.variant == "unabridged"


def test_a_time_a_shop_says_is_unabridged_proves_that() -> None:
    assert checking.runtime_variant(401 * 60, {"unabridged": minutes(400)})[:2] == ("unabridged", True)
    verdict = checking.judge_units([recording(401)], wanted({"unabridged": minutes(400)}))
    assert verdict.outcome == "take" and verdict.variant == "unabridged" and verdict.variant_proven


def test_a_bundle_of_the_catalogue_is_not_the_measure_of_one_book() -> None:
    times = {"unabridged": minutes(1056, 1586)}
    variant, proven, values = checking.runtime_variant(843 * 60, times)
    assert values["unabridged"] == 1056 * 60 and (variant, proven) == ("abridged", False)
    assert checking.runtime_variant(1050 * 60, times)[:2] == ("unabridged", True)


def test_the_form_of_a_source_without_a_version_says_none() -> None:
    form = foreign.form_of(candidate("dnb", "1", "Die Nebelbahn", 129), "audio")
    assert form["variant"] == "" and foreign.version_of(form) == ""
    assert foreign.version_of({"variant": "unabridged", "source": "dnb"}) == ""
    assert foreign.version_of({"variant": "abridged", "source": "audible"}) == "abridged"


def download(**values: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"foreign_id": None, "work_id": None, "language": "de", **values})


SUBJECT = {"title": "Die Nebelbahn", "authors": ["Mara Lind"], "duration": None}


def test_the_rows_of_a_book_speak_each_for_itself(operator: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importing, "_audible_runtimes", lambda _db, _subject, _language, found: found)
    with SessionLocal() as db:
        book = foreign.upsert(db, candidate("dnb", "1", "Die Nebelbahn", 618), "audio")
        foreign.upsert(db, candidate("audible", "A1", "Die Nebelbahn", 704, variant="unabridged"), "audio")
        foreign.upsert(db, candidate("audible", "A2", "Die Nebelbahn", 300, variant="abridged", language="en"), "audio")
        found = importing._runtimes(db, download(foreign_id=book.id), {**SUBJECT, "duration": 618 * 60})
    assert found == {"unabridged": minutes(704), "abridged": [], "dramatized": [], checking.UNSAID: minutes(618)}


def test_a_row_of_an_earlier_version_says_no_version_of_a_library(operator: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importing, "_audible_runtimes", lambda _db, _subject, _language, found: found)
    with SessionLocal() as db:
        book = foreign.upsert(db, candidate("dnb", "1", "Die Nebelbahn", 129), "audio")
        book.data = {**book.data, "forms": [{**book.data["forms"][0], "variant": "unabridged"}]}
        found = importing._runtimes(db, download(foreign_id=book.id), SUBJECT)
    assert found["unabridged"] == [] and found[checking.UNSAID] == minutes(129)


def test_a_wish_that_joined_a_work_still_asks_the_rows_of_the_sources(operator: Any,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importing, "_audible_runtimes", lambda _db, _subject, _language, found: found)
    with SessionLocal() as db:
        foreign.upsert(db, candidate("audible", "A1", "Die Nebelbahn", 704, variant="unabridged"), "audio")
        found = importing._runtimes(db, download(work_id=7), SUBJECT)
    assert found["unabridged"] == minutes(704)


def test_audible_is_asked_beside_a_library_catalogue(operator: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.sources import audible

    monkeypatch.setattr(audible, "search", lambda *_args, **_kwargs: [
        candidate("audible", "A1", "Die Nebelbahn", 1076, variant="unabridged"),
        candidate("audible", "A2", "Die Nebelbahn", 900, variant="unabridged", language="en"),
        candidate("audible", "A3", "Die Nebelbahn", 800)])
    with SessionLocal() as db:
        book = foreign.upsert(db, candidate("dnb", "1", "Die Nebelbahn", 609), "audio")
        found = importing._runtimes(db, download(foreign_id=book.id), SUBJECT)
    assert found["unabridged"] == minutes(1076)
    assert sorted(found[checking.UNSAID]) == minutes(609, 800)
