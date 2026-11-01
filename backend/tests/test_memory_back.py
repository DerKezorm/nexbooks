"""Giving memory back after big work (``services/memory.py``): the C allocator is asked once when the queue of jobs
ran empty after work (not while it works, not while it idles), and every book file the scan opens is dropped from the
page cache when it is closed. What stays in memory once all is done is measured in the container; these tests keep the
two calls where they belong."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Job
from app.services import jobs, memory
from app.services.readers import files

from .library_helpers import book, books_folder, make_library, run_jobs


@pytest.fixture(autouse=True)
def own_kinds() -> Iterator[None]:
    before = dict(jobs.HANDLERS)
    yield
    jobs.HANDLERS.clear()
    jobs.HANDLERS.update(before)


def test_the_allocator_is_asked_once_when_the_queue_ran_empty_after_work(monkeypatch: pytest.MonkeyPatch) -> None:
    trims: list[int] = []
    done: list[int] = []
    monkeypatch.setattr(memory, "trim", lambda: trims.append(len(done)) or True)
    monkeypatch.setattr(jobs, "POLL_SECONDS", 0.01)
    jobs.register("memtest", lambda _db, job: done.append(job.ref))
    with SessionLocal() as db:
        for ref in range(3):
            jobs.enqueue(db, "memtest", ref)
        db.commit()

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(jobs.run_forever(stop, lane="main"))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if trims:
                break
        # Idling on: no second trim without new work.
        await asyncio.sleep(0.15)
        stop.set()
        await task

    asyncio.run(run())
    assert done == [0, 1, 2]
    assert trims == [3]
    with SessionLocal() as db:
        assert {row.state for row in db.scalars(select(Job))} == {"done"}
    jobs.stopping.clear()


def test_the_allocator_is_asked_at_most_once_a_minute_and_the_last_work_is_not_left_untrimmed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A source that drips one job after the other: one trim in the minute, and the work that came after it is
    trimmed once the minute is over, with no new work needed."""
    now = [1000.0]
    trims: list[float] = []
    monkeypatch.setattr(clock, "monotonic", lambda: now[0])
    monkeypatch.setattr(memory, "trim", lambda: trims.append(now[0]) or True)
    assert memory.trim_if_due() is True
    for _ in range(5):
        now[0] += 10
        assert memory.trim_if_due() is False
    assert trims == [1000.0]
    now[0] += memory.TRIM_EVERY
    assert memory.trim_if_due() is True and trims == [1000.0, now[0]]


def test_a_burst_of_work_right_after_a_trim_is_trimmed_when_the_minute_is_over(
        monkeypatch: pytest.MonkeyPatch) -> None:
    now = [5000.0]
    trims: list[float] = []
    monkeypatch.setattr(clock, "monotonic", lambda: now[0])
    monkeypatch.setattr(memory, "trim", lambda: trims.append(now[0]) or True)
    monkeypatch.setattr(jobs, "POLL_SECONDS", 0.01)
    memory.trim_if_due()
    jobs.register("memtest", lambda _db, _job: None)
    with SessionLocal() as db:
        jobs.enqueue(db, "memtest", 1)
        db.commit()

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(jobs.run_forever(stop, lane="main"))
        await asyncio.sleep(0.3)
        assert trims == [5000.0], "trimmed again within the minute"
        now[0] += memory.TRIM_EVERY + 1
        for _ in range(300):
            await asyncio.sleep(0.01)
            if len(trims) == 2:
                break
        stop.set()
        await task

    asyncio.run(run())
    assert trims == [5000.0, 5061.0]
    jobs.stopping.clear()


def test_trim_says_whether_it_could_and_never_fails() -> None:
    assert memory.trim() in (True, False)
    memory.forget_pages(-1)


def test_every_file_the_scan_opens_leaves_the_page_cache_when_it_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    forgotten: list[int] = []
    monkeypatch.setattr(memory, "forget_pages", lambda descriptor: forgotten.append(descriptor))
    folder = books_folder("cache")
    book(folder, "a.epub", title="Paper Lanterns")
    path = Path(folder)
    with files.open_below(str(path), "a.epub") as handle:
        handle.read(10)
        opened = handle.fileno()
    assert forgotten == [opened]
    # Also when the reading failed.
    with pytest.raises(ValueError), files.open_below(str(path), "a.epub"):
        raise ValueError
    assert len(forgotten) == 2
    # And a real scan (the checksum of the book goes through the same door).
    forgotten.clear()
    make_library(folder, "Cache", scan=True)
    run_jobs()
    assert forgotten, "the scan read a book without dropping its pages"
