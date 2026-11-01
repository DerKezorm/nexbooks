"""A job its program holds paused: for a while it is just paused, then somebody has to hear of it, and when the job goes
on the note goes. Only invented books."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from app import clock
from app.services.procure import tracking
from app.services.procure.clients import Job


def row(state: str = "paused", hours: float = 7.0, code: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=1, state=state, progress=10.0, remaining_seconds=None, problem_code=code, problem_values=None,
                           updated_at=clock.now() - timedelta(hours=hours), missing_count=0, client_state="",
                           client_name="Programm", size=0)


def follow(item: Any, job: Job) -> None:
    tracking._follow(None, item, job, [])  # type: ignore[arg-type]


def test_a_job_paused_for_a_while_is_just_paused() -> None:
    item = row(hours=5.0)
    follow(item, Job(ref="a", client_state="Paused", state="paused"))
    assert (item.state, item.problem_code) == ("paused", "")


def test_a_job_that_stays_paused_waits_for_a_person() -> None:
    item = row()
    follow(item, Job(ref="a", client_state="Paused", state="paused"))
    assert (item.state, item.problem_code, item.problem_values) == (
        "problem", "paused", {"client": "Programm", "why": []})


def test_the_note_stays_while_the_job_stands_still() -> None:
    item = row(state="problem", hours=0.0, code="paused")
    follow(item, Job(ref="a", client_state="Paused", state="paused"))
    assert (item.state, item.problem_code) == ("problem", "paused")


def test_the_note_goes_when_the_job_goes_on() -> None:
    item = row(state="problem", code="paused")
    follow(item, Job(ref="a", client_state="Downloading", state="downloading"))
    assert (item.state, item.problem_code) == ("downloading", "")


def test_a_job_that_was_just_paused_does_not_count_the_time_before() -> None:
    item = row(state="downloading")
    follow(item, Job(ref="a", client_state="Paused", state="paused"))
    assert (item.state, item.problem_code) == ("paused", "")


def test_the_round_keeps_asking_about_such_a_job() -> None:
    assert "paused" in tracking.OF_THE_JOB
