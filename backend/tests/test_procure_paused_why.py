"""A job SABnzbd holds paused says why in its labels; the card under "Braucht dich" carries the words, and the person may
block the release and search on, or cancel. Only invented books."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from app import clock
from app.db import SessionLocal
from app.models import BlocklistEntry, Download
from app.services.procure import tracking
from app.services.procure.clients import Job, Target, open_client

from .follow_helpers import audio_world, downloads, round_of_tracking, rows, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .test_procure_paused_jobs import follow, row


def jobs_of(net: FakeNetwork, slot: dict[str, Any], *, queue_paused: bool = False) -> Job:
    sab = sabnzbd(net)
    sab.put("SABnzbd_nzo_a", where="queue", filename="Some.Release", cat="nexbooks", mb="10.0", mbleft="5.0",
            percentage="50", **slot)
    sab.jobs["SABnzbd_nzo_a"] = ("nexbooks", "Some.Release", b"")
    sab.queue_paused = queue_paused
    with open_client(Target("sabnzbd", f"http://{sab.host}:8080", "", sab.api_key, "nexbooks")) as client:
        return client.jobs(["SABnzbd_nzo_a"])["SABnzbd_nzo_a"]  # type: ignore[no-any-return]


def test_a_paused_job_names_its_labels(net: FakeNetwork) -> None:
    job = jobs_of(net, {"status": "Paused", "labels": ["DUPLICATE", "TOO LARGE"]})
    assert job.state == "paused" and job.why == ("DUPLICATE", "TOO LARGE")


def test_a_paused_job_without_labels_names_nothing(net: FakeNetwork) -> None:
    assert jobs_of(net, {"status": "Paused"}).why == ()
    assert jobs_of(net, {"status": "Paused", "labels": "DUPLICATE"}).why == ()


def test_the_labels_are_read_with_care(net: FakeNetwork) -> None:
    messy = ["  DUPLICATE  ", "DUPLICATE", "", 5, None, "x" * 500, *[f"L{number}" for number in range(20)]]
    job = jobs_of(net, {"status": "Paused", "labels": messy})
    assert job.why[0] == "DUPLICATE" and job.why.count("DUPLICATE") == 1 and len(job.why) == 6 and all(len(label) <= 40 for label in job.why)


def test_a_queue_that_stands_is_a_reason_of_its_own(net: FakeNetwork) -> None:
    job = jobs_of(net, {"status": "Queued", "labels": ["FILTERED"]}, queue_paused=True)
    assert job.state == "paused" and job.why == ("FILTERED", "queue_paused")


@pytest.mark.parametrize("label", ["ENCRYPTED", " encrypted "])
def test_a_job_with_a_password_is_over_and_no_question(net: FakeNetwork, label: str) -> None:
    job = jobs_of(net, {"status": "Paused", "labels": [label]})
    assert (job.state, job.reason) == ("failed", "encrypted")


def test_a_job_with_a_password_is_blocked_and_the_search_goes_on_without_asking(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    wish(world)
    run_jobs()
    [download] = downloads()
    sab.put(download.client_ref, where="queue", status="Paused", labels=["ENCRYPTED"], percentage="10", mb="10.0",
            mbleft="9.0")
    round_of_tracking()
    run_jobs()
    assert [entry.reason for entry in rows(BlocklistEntry)] == ["client_failed"]
    assert world.client.get("/api/v1/procure/problems").json()["problems"] == []
    round_of_tracking()
    assert download.client_ref in sab.removed


def test_a_job_that_goes_on_has_no_reason(net: FakeNetwork) -> None:
    job = jobs_of(net, {"status": "Downloading", "labels": ["DUPLICATE"]})
    assert job.state == "downloading" and job.why == ()


def test_the_note_keeps_the_reasons_of_the_program() -> None:
    item = row()
    follow(item, Job(ref="a", client_state="Paused", state="paused", why=("DUPLICATE",)))
    assert item.problem_values == {"client": "Programm", "why": ["DUPLICATE"]}
    assert "paused" in tracking.OF_THE_JOB


@pytest.mark.parametrize("block", [True, False])
def test_a_person_blocks_or_cancels_a_paused_job_and_it_leaves_the_program(operator: Any, net: FakeNetwork,
                                                                           block: bool) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    wish(world)
    run_jobs()
    [download] = downloads()
    job = download.client_ref
    sab.put(job, where="queue", status="Paused", labels=["DUPLICATE"], percentage="10", mb="10.0", mbleft="9.0")
    round_of_tracking()
    with SessionLocal() as db:
        db.execute(update(Download).where(Download.id == download.id).values(
            updated_at=clock.now() - timedelta(hours=7)))
        db.commit()
    round_of_tracking()
    [shown] = world.client.get("/api/v1/procure/problems").json()["problems"]
    assert shown["code"] == "paused" and shown["values"]["why"] == ["DUPLICATE"]
    answer = world.client.post(f"/api/v1/procure/downloads/{download.id}/cancel", json={"block": block})
    assert answer.status_code == 200 and answer.json()["state"] == "removed", answer.text
    assert job in sab.removed
    assert len(rows(BlocklistEntry)) == (1 if block else 0)
    assert world.client.get("/api/v1/procure/problems").json()["problems"] == []
    [wish_shown] = wishes_of(world)
    # Cancelled only, the wish waits; blocked, it searches on (the release it took is never taken again).
    assert (wish_shown["state"] == "idle") is (not block), wish_shown["state"]
