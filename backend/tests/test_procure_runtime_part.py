"""A recording of under a tenth of the unabridged running time is a piece of the book, not an abridged reading: it is
turned down, the release is blocked and the search goes on. Real abridged readings (from about 14 percent) are still
asked about. Only invented books."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models import BlocklistEntry, Download
from app.services.procure import checking, importing

from .filing_helpers import audio_wish, download_roots, finish, recording
from .follow_helpers import rows
from .procure_fakes import FakeNetwork
from .test_procure_version_proof import minutes, wanted
from .test_procure_version_proof import recording as unit_of

FULL = 3037  # minutes: a reading of 50 hours and 37 minutes


def seconds_of(share: float) -> float:
    return FULL * 60 * share


def test_a_recording_between_two_times_of_the_catalogue_is_held_against_the_longer_one() -> None:
    """A far too short time of the catalogue (a sample, one part of a set) must not make a short reading a long one."""
    times = {"unabridged": minutes(45, 839, 3743), "abridged": minutes(228)}
    found, proven, values = checking.runtime_variant(184 * 60, times)
    assert (found, proven, values["unabridged"]) == (checking.ABRIDGED, False, 839 * 60)


def test_a_recording_longer_than_every_time_of_the_catalogue_stays_measured_by_the_longest() -> None:
    found, proven, values = checking.runtime_variant(412 * 60, {"unabridged": minutes(120, 354)})
    assert (found, proven, values["unabridged"]) == (checking.UNABRIDGED, False, 354 * 60)


@pytest.mark.parametrize(("share", "variant"), [(0.099, checking.PART), (0.10, checking.ABRIDGED),
                                                (0.14, checking.ABRIDGED), (0.0171, checking.PART)])
def test_the_edge_of_a_piece_of_the_book_lies_at_a_tenth(share: float, variant: str) -> None:
    found, proven, values = checking.runtime_variant(seconds_of(share), {"unabridged": minutes(FULL)})
    assert (found, proven) == (variant, False) and values["unabridged"] == FULL * 60


@pytest.mark.parametrize("share", [0.0171, 0.099])
def test_a_piece_of_the_book_is_turned_down_and_not_asked_about(share: float) -> None:
    minutes_long = round(FULL * share)
    verdict = checking.judge_units([unit_of(minutes_long)], wanted({"unabridged": minutes(FULL)}))
    assert (verdict.outcome, verdict.code) == ("reject", "runtime_part")
    assert verdict.values["loaded"] == minutes_long * 60 and verdict.values["unabridged"] == FULL * 60


@pytest.mark.parametrize("abridged_allowed", [False, True])
def test_a_piece_is_turned_down_whether_abridged_readings_are_taken_or_not(abridged_allowed: bool) -> None:
    verdict = checking.judge_units([unit_of(52)], wanted({"unabridged": minutes(FULL)}, abridged=abridged_allowed))
    assert (verdict.outcome, verdict.code) == ("reject", "runtime_part")


def test_a_real_abridged_reading_is_still_a_question() -> None:
    verdict = checking.judge_units([unit_of(round(FULL * 0.14))], wanted({"unabridged": minutes(FULL)}))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "abridged")


def test_a_time_a_shop_lists_as_abridged_is_that_even_when_short() -> None:
    times = {"unabridged": minutes(FULL), "abridged": minutes(52)}
    verdict = checking.judge_units([unit_of(52)], wanted(times))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "abridged")


def test_without_a_running_time_of_the_catalogue_nothing_is_a_piece() -> None:
    verdict = checking.judge_units([unit_of(52)], wanted({}))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "unproven")


def test_a_person_who_filed_a_recording_by_hand_is_asked_as_before() -> None:
    found = wanted({"unabridged": minutes(FULL)})
    found.upload = True
    verdict = checking.judge_units([unit_of(52)], found)
    assert verdict.outcome == "take" and verdict.variant == "abridged"


def test_the_answer_of_a_person_stands() -> None:
    found = wanted({"unabridged": minutes(FULL)})
    found.decision = {"variant": "abridged"}
    verdict = checking.judge_units([unit_of(52)], found)
    assert verdict.outcome == "take" and verdict.variant == "abridged"


def test_the_download_is_turned_down_blocked_and_the_search_goes_on(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    roots = download_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(importing, "_runtimes", lambda *_args: {"unabridged": [200], "abridged": [], "dramatized": [],
                                                                checking.UNSAID: []})
    _world, sab, job = audio_wish(net)
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    recording(folder, parts=2, seconds=4.0)
    finish(sab, job, folder)
    [row] = rows(Download)
    assert row.state == "rejected" and row.failed_reason == "runtime_part"
    [block] = rows(BlocklistEntry)
    assert block.reason == "partial"
