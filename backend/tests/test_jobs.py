"""The queue of background work: kept in the database, one claimer per job, at most one waiting job of a kind for the
same thing, tried a limited number of times, and picked up again after a stop."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from app.services import jobs


@pytest.fixture(autouse=True)
def own_kinds() -> Iterator[None]:
    """Kinds of the test only; whatever a block registered stays as it was."""
    before = dict(jobs.HANDLERS)
    yield
    jobs.HANDLERS.clear()
    jobs.HANDLERS.update(before)


def states() -> dict[int, tuple[str, int]]:
    with SessionLocal() as db:
        return {row.ref: (row.state, row.attempts) for row in db.scalars(select(Job))}


def queue(kind: str, ref: int = 0) -> None:
    with SessionLocal() as db:
        jobs.enqueue(db, kind, ref)
        db.commit()


def test_a_job_runs_once_and_is_done() -> None:
    seen: list[int] = []
    jobs.register("probe", lambda _db, job: seen.append(job.ref))
    queue("probe", 7)
    assert jobs.run_pending() == 1
    assert seen == [7] and states() == {7: ("done", 1)}
    assert jobs.run_pending() == 0, "nothing is left"


def test_a_job_that_waits_for_the_same_thing_is_queued_once() -> None:
    jobs.register("probe", lambda _db, _job: None)
    for _ in range(5):
        queue("probe", 3)
    with SessionLocal() as db:
        assert jobs.waiting(db) == 1
    queue("probe", 4)
    with SessionLocal() as db:
        assert jobs.waiting(db) == 2
    # Once it ran, the same thing may wait again.
    assert jobs.run_pending() == 2
    queue("probe", 3)
    with SessionLocal() as db:
        assert jobs.waiting(db) == 1


def test_the_oldest_job_goes_first() -> None:
    order: list[int] = []
    jobs.register("probe", lambda _db, job: order.append(job.ref))
    for ref in (5, 1, 9):
        queue("probe", ref)
    jobs.run_pending()
    assert order == [5, 1, 9]


def test_a_job_that_fails_is_tried_again_up_to_the_limit_and_then_stays_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def broken(_db: object, job: Job) -> None:
        calls.append(job.attempts)
        raise jobs.Retry("not now")

    jobs.register("probe", broken)
    queue("probe", 2)
    for _ in range(jobs.ATTEMPTS + 3):
        jobs.run_pending()
    assert calls == list(range(1, jobs.ATTEMPTS + 1)), "tried ATTEMPTS times and no more"
    assert states() == {2: ("failed", jobs.ATTEMPTS)}


def test_a_job_that_gives_up_stays_failed_at_once() -> None:
    def hopeless(_db: object, _job: Job) -> None:
        raise jobs.GiveUp("cannot be done")

    jobs.register("probe", hopeless)
    queue("probe", 1)
    jobs.run_pending()
    assert states() == {1: ("failed", 1)}
    with SessionLocal() as db:
        assert db.scalar(select(Job.error)) == "cannot be done"


def test_a_kind_nobody_knows_fails_and_stops_nothing_else() -> None:
    done: list[int] = []
    jobs.register("probe", lambda _db, job: done.append(job.ref))
    queue("ghost", 1)
    queue("probe", 2)
    jobs.run_pending()
    assert done == [2]
    assert states() == {1: ("failed", 1), 2: ("done", 1)}


def test_a_stop_puts_the_job_back_without_using_a_try() -> None:
    def stopping(_db: object, _job: Job) -> None:
        raise jobs.Interrupted

    jobs.register("probe", stopping)
    queue("probe", 4)
    jobs.run_pending()
    assert states() == {4: ("queued", 0)}


def test_what_a_stopped_server_left_running_waits_again() -> None:
    jobs.register("probe", lambda _db, _job: None)
    with SessionLocal() as db:
        db.add(Job(kind="probe", ref=1, state="running", attempts=1))
        # Another job of the same kind and thing is waiting already: the left one is dropped, not run twice.
        db.add(Job(kind="probe", ref=2, state="running", attempts=1))
        db.add(Job(kind="probe", ref=2, state="queued"))
        db.commit()
        jobs.recover(db)
    assert states()[1] == ("queued", 1)
    with SessionLocal() as db:
        by_state = sorted(row.state for row in db.scalars(select(Job).where(Job.ref == 2)))
    assert by_state == ["done", "queued"]


def test_two_claimers_never_take_the_same_job() -> None:
    taken: list[int] = []
    lock = threading.Lock()

    def slow(_db: object, job: Job) -> None:
        with lock:
            taken.append(job.id)

    jobs.register("probe", slow)
    for ref in range(1, 21):
        queue("probe", ref)
    threads = [threading.Thread(target=jobs.run_pending) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(taken) == sorted(set(taken)) and len(taken) == 20


def test_the_name_of_a_kind_fits_its_column() -> None:
    with pytest.raises(ValueError):
        jobs.register("x" * 17, lambda _db, _job: None)
