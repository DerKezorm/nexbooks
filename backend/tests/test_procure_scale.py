"""The floor under the automatic work: with a household's worth of wishes the reading of the newest entries and
a tick of the planner take seconds, not minutes, and a scan that runs at the same time is not held up by them. The
thresholds are generous on purpose; they catch a loop that has become quadratic, not time it."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Work
from app.services.procure import planner, rss

from .follow_helpers import sabnzbd, wishes_of
from .library_helpers import book, books_folder, join, make_library, run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, make_world

WISHES = 150
ENTRIES = 100
SYNC_SECONDS = 40
TICK_SECONDS = 40


def many_wishes(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> Any:
    # A household's worth of wishes from one person in one day: the brake per day is not what is measured here.
    monkeypatch.setattr(get_settings(), "wishes_per_day", 0)
    sabnzbd(net)
    world = make_world(net)
    world.indexer.items = [Item("Nobody Else-Nothing At All-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                categories=(3130,))]
    folder = books_folder("many")
    for number in range(WISHES):
        book(folder, f"a{number}/book{number}.epub", title=f"Quiet Book Number {number}",
             authors=(f"Writer Number{number}",), language="en")
    library = make_library(folder, "Many")
    scan(library)
    join(library, world.person.id, "acquire")
    with SessionLocal() as db:
        work_ids = [work_id for work_id in db.scalars(select(Work.id).order_by(Work.id))]
    for work_id in work_ids:
        # Two libraries take recordings: the wish names where it goes.
        answer = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": work_id}],
                                                                   "kinds": ["audio"], "language": "de",
                                                                   "library_id": world.library_id})
        assert answer.status_code == 201, answer.text
    run_jobs()
    return world


def test_a_hundred_new_entries_against_many_wishes_take_seconds(operator: Any, net: FakeNetwork,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    world = many_wishes(net, monkeypatch)
    assert len(wishes_of(world)) >= WISHES
    world.indexer.items = [Item(f"Somebody{number} Else{number}-Another Thing {number}-MP3CD-DE-2021-EXAMPLE",
                                size=400_000_000, categories=(3130,)) for number in range(ENTRIES)]
    started = time.monotonic()
    assert rss.sync() == ENTRIES
    elapsed = time.monotonic() - started
    print(f"\nscale: {ENTRIES} new entries against {WISHES + 1} wishes in {elapsed:.2f}s")
    assert elapsed < SYNC_SECONDS


def test_a_tick_with_many_wishes_and_a_scan_at_the_same_time_hold_each_other_up_little(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    world = many_wishes(net, monkeypatch)
    other = books_folder("second")
    for number in range(300):
        book(other, f"b{number // 10}/book{number}.epub", title=f"Another Quiet Book {number}",
             authors=(f"Author Number{number}",), language="en")
    second = make_library(other, "Second")
    started = time.monotonic()
    scan(second)
    alone = time.monotonic() - started
    other2 = books_folder("third")
    for number in range(300):
        book(other2, f"c{number // 10}/book{number}.epub", title=f"Third Quiet Book {number}",
             authors=(f"Writer Third{number}",), language="en")
    third = make_library(other2, "Third")
    stop = threading.Event()
    ticks: list[float] = []

    def keep_ticking() -> None:
        while not stop.is_set():
            begun = time.monotonic()
            planner.reset()
            planner.tick()
            ticks.append(time.monotonic() - begun)
            time.sleep(0.05)

    thread = threading.Thread(target=keep_ticking)
    thread.start()
    started = time.monotonic()
    scan(third)
    together = time.monotonic() - started
    stop.set()
    thread.join()
    print(f"\nscale: scan of 300 files alone {alone:.2f}s, with ticks running {together:.2f}s "
          f"({len(ticks)} ticks, the longest {max(ticks, default=0):.2f}s)")
    assert max(ticks, default=0) < TICK_SECONDS
    assert together < max(alone * 4, alone + 30)
    assert len(wishes_of(world)) >= WISHES
