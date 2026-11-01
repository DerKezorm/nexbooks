"""An open question under "Braucht dich" is judged again by the rules of today when they are newer than the ones that asked
it: from the files it has, by the same check as a fresh download. What is gone, decided or uploaded stays as it is, and two
runs at the same moment file nothing twice. Only invented books."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import BlocklistEntry, Download, PlaceFile
from app.services.procure import checking, importing, planner, recheck

from .concurrent import at_once
from .filing_helpers import audio_wish, download_roots, editions, finish, recording
from .follow_helpers import rows
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import TITLE

#: The recording of six discs of two parts, four seconds each: 48 seconds in all.
TOTAL = 48


def runtimes_are(monkeypatch: pytest.MonkeyPatch, seconds: int | None) -> None:
    """The running time the catalogue knows of the book (nothing for ``None``)."""
    monkeypatch.setattr(importing, "_runtimes", lambda *_args: {
        "unabridged": [seconds] if seconds else [], "abridged": [], "dramatized": [], checking.UNSAID: []})


def six_discs(net: FakeNetwork, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalogue: int | None) -> tuple[Any, Path]:
    """A release of six folders ``CD1`` to ``CD6``, each with two parts; the check asks about its running time because
    the catalogue names ``catalogue`` seconds."""
    roots = download_roots(tmp_path, monkeypatch)
    runtimes_are(monkeypatch, catalogue)
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "release"
    for number in range(1, 7):
        recording(folder / f"CD{number}", parts=2, seconds=4.0, album=TITLE)
    finish(sab, job, folder)
    return world, folder


def the_row() -> Download:
    [row] = rows(Download)
    return row


def older(generation: int = 0) -> None:
    """The rules have moved on: the download was judged by an older generation."""
    with SessionLocal() as db:
        db.execute(update(Download).values(check_rev=generation))
        db.commit()


def judge_again() -> int:
    with SessionLocal() as db:
        return recheck.run(db)


def test_a_question_asked_by_the_rules_of_today_is_not_asked_again(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    row = the_row()
    assert (row.state, row.problem_code, row.problem_values["reason"]) == ("problem", "runtime", "abridged")
    assert row.check_rev == checking.CHECK_REV
    assert judge_again() == 0
    assert the_row().problem_code == "runtime"


def test_the_card_of_an_old_judgement_is_judged_again_and_the_book_is_filed(operator: Any, net: FakeNetwork,
                                                                           tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """The six discs are one recording of the length the catalogue names. The card of the old rules said 48 seconds against
    100; today's numbers fit, and the book is filed as it would have been the day it came."""
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    assert the_row().problem_code == "runtime"
    runtimes_are(monkeypatch, TOTAL)
    older()
    assert judge_again() == 1
    assert (the_row().state, the_row().problem_code) == ("completed", "")
    run_jobs()
    row = the_row()
    assert (row.state, row.problem_code) == ("imported", "")
    assert row.check_rev == checking.CHECK_REV
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert audio.variant == "unabridged"
    assert judge_again() == 0


def test_a_question_that_is_still_a_question_is_asked_again_with_the_numbers_of_today(operator: Any, net: FakeNetwork,
                                                                                      tmp_path: Path,
                                                                                      monkeypatch: pytest.MonkeyPatch
                                                                                      ) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    assert the_row().problem_values["unabridged"] == 100
    runtimes_are(monkeypatch, 150)
    older()
    assert judge_again() == 1
    run_jobs()
    row = the_row()
    assert (row.state, row.problem_code) == ("problem", "runtime")
    assert row.problem_values["unabridged"] == 150 and row.problem_values["loaded"] == TOTAL
    assert row.check_rev == checking.CHECK_REV
    assert [edition for edition in editions() if edition.kind == "audio"] == []


def test_a_piece_of_a_book_is_turned_down_blocked_and_the_search_goes_on(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Under a tenth of the unabridged time: the rule of today turns it down, as it does a fresh download."""
    world, _folder = six_discs(net, tmp_path, monkeypatch, catalogue=100)
    assert the_row().problem_code == "runtime"
    runtimes_are(monkeypatch, 3037)
    older()
    assert judge_again() == 1
    run_jobs()
    row = the_row()
    assert (row.state, row.failed_reason) == ("rejected", "runtime_part")
    [block] = rows(BlocklistEntry)
    assert block.reason == "partial"
    assert [edition for edition in editions() if edition.kind == "audio"] == []
    # (The wish searches on: nothing else was offered, so it waits for the next try.)
    [shown] = world.client.get("/api/v1/procure/wishes").json()["wishes"]
    assert shown["state"] != "loading"


def test_files_that_are_gone_leave_the_card_as_it_is(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    _world, folder = six_discs(net, tmp_path, monkeypatch, catalogue=100)
    before = the_row()
    shutil.rmtree(folder)
    runtimes_are(monkeypatch, TOTAL)
    older()
    assert judge_again() == 0
    row = the_row()
    assert (row.state, row.problem_code, row.problem_values) == (before.state, before.problem_code, before.problem_values)
    # Not looked at again until the rules change once more.
    assert row.check_rev == checking.CHECK_REV and judge_again() == 0


def test_one_file_missing_is_enough_to_leave_the_card_as_it_is(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    _world, folder = six_discs(net, tmp_path, monkeypatch, catalogue=100)
    next(folder.rglob("*.mp3")).unlink()
    older()
    assert judge_again() == 0
    assert the_row().problem_code == "runtime"


def test_what_a_person_decided_is_not_judged_again(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    # The person keeps it as abridged: the download is filed and is no question any more.
    answer = operator_keeps()
    assert answer in (200, 201)
    run_jobs()
    assert the_row().state == "imported"
    older()
    assert judge_again() == 0
    assert the_row().state == "imported"


def operator_keeps() -> int:
    with SessionLocal() as db:
        row = db.scalar(select(Download))
        assert row is not None
        importing.go_on(db, row, {"variant": "abridged"})
        db.commit()
    return 200


def test_a_recording_whose_version_a_person_set_keeps_its_rules(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    with SessionLocal() as db:
        db.execute(update(Download).values(decision={"variant": "abridged"}))
        db.commit()
    older()
    assert judge_again() == 0
    assert the_row().problem_code == "runtime"


def test_an_upload_keeps_its_rules() -> None:
    from app.services.procure import recheck as module

    with SessionLocal() as db:
        db.add(Download(kind="audio", title="Upload", protocol="upload", state="problem", problem_code="runtime",
                        check_rev=0))
        db.commit()
        assert module.candidates(db) == []


def test_only_open_questions_are_candidates() -> None:
    with SessionLocal() as db:
        for code, state in (("runtime", "problem"), ("path_not_found", "problem"), ("", "completed"),
                            ("runtime", "downloading")):
            db.add(Download(kind="audio", title=f"{code}{state}", protocol="usenet", state=state, problem_code=code,
                            check_rev=0))
        db.commit()
        found = recheck.candidates(db)
        assert [db.get(Download, item).title for item in found] == ["runtimeproblem"]  # type: ignore[union-attr]


def test_a_few_questions_at_a_time(operator: Any) -> None:
    with SessionLocal() as db:
        for number in range(recheck.PORTION + 4):
            db.add(Download(kind="audio", title=f"Frage {number}", protocol="usenet", state="problem",
                            problem_code="bundle", check_rev=0))
        db.commit()
        assert len(recheck.candidates(db)) == recheck.PORTION


def test_two_runs_at_the_same_moment_file_the_book_once(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    runtimes_are(monkeypatch, TOTAL)
    older()
    handed = at_once([judge_again, judge_again, judge_again])
    assert sum(handed) == 1
    run_jobs()
    assert the_row().state == "imported"
    assert len([edition for edition in editions() if edition.kind == "audio"]) == 1


def test_a_person_who_answers_at_the_same_moment_and_the_run_do_not_both_have_it(operator: Any, net: FakeNetwork,
                                                                                 tmp_path: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch
                                                                                 ) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    runtimes_are(monkeypatch, TOTAL)
    older()
    ids = at_once([judge_again, operator_keeps])
    assert ids[1] == 200
    run_jobs()
    assert the_row().state == "imported"
    assert len([edition for edition in editions() if edition.kind == "audio"]) == 1


def test_the_planner_judges_them_again_and_not_with_the_main_switch_off(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import automation

    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    runtimes_are(monkeypatch, TOTAL)
    older()
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": False})
    planner.reset()
    planner.tick()
    assert the_row().state == "problem"
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": True})
    planner.reset()
    planner.tick()
    assert the_row().state == "completed" or the_row().state == "imported"


def test_a_line_of_the_log_for_each_case(operator: Any, net: FakeNetwork, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         caplog: pytest.LogCaptureFixture) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    older()
    with caplog.at_level("INFO", logger="nexbooks.procure"):
        judge_again()
    lines = [record.getMessage() for record in caplog.records if "judged again" in record.getMessage()]
    assert len(lines) == 1 and f"version {checking.CHECK_REV} (was 0)" in lines[0] and "code=runtime" in lines[0]


def test_the_rules_start_at_a_generation_that_judges_every_question_there_is() -> None:
    assert checking.CHECK_REV >= 1
    assert SimpleNamespace(check_rev=0).check_rev < checking.CHECK_REV


def test_a_download_is_claimed_once_and_not_after_a_person_answered(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The claim is one conditional update: whoever comes second finds the download taken (the runs may both have seen it
    as a candidate before either took it)."""
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    older()
    with SessionLocal() as first, SessionLocal() as second:
        seen_first, seen_second = first.scalar(select(Download)), second.scalar(select(Download))
        assert seen_first is not None and seen_second is not None
        assert recheck._claim(first, seen_first) is True
        assert recheck._claim(second, seen_second) is False
    older()
    assert operator_keeps() == 200
    with SessionLocal() as db:
        row = db.scalar(select(Download))
        assert row is not None
        assert recheck._claim(db, row) is False


def test_a_question_without_a_file_on_record_keeps_its_card(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    six_discs(net, tmp_path, monkeypatch, catalogue=100)
    with SessionLocal() as db:
        db.execute(delete(PlaceFile))
        db.commit()
    older()
    assert judge_again() == 0
    row = the_row()
    assert (row.state, row.problem_code, row.check_rev) == ("problem", "runtime", checking.CHECK_REV)
