"""The main switch of the automatic work, switched off: no way goes out by
itself. A wish that would be searched rests (``idle``, ``main_off``): the one that waits for a free place of its person,
the one that comes back, the one a replacement or a cancel would search for, a better format. The one search that goes
on is the one a person starts with "Jetzt suchen", and it loads nothing."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ForeignBook, ProcureWish
from app.services.procure import wishes

from .auto_helpers import Moving, configure, searching_requests, tick
from .follow_helpers import AUDIO, SECOND_AUDIO, audio_world, downloads, round_of_tracking, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"


def many_wishes(world: Any, number: int) -> list[str]:
    tokens = []
    with SessionLocal() as db:
        for count in range(number):
            token = f"off{count:013d}"
            tokens.append(token)
            db.add(ForeignBook(token=token, ref=f"hardcover:{700 + count}", title=f"Zwielicht{chr(97 + count)}x",
                               authors=["Rune Falkner"], language="de"))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={
        "items": [{"book": token} for token in tokens], "kinds": ["audio"], "language": "de"})
    assert reply.status_code == 201, reply.text
    return tokens


def books_searched(world: Any) -> int:
    found = set()
    for request in searching_requests(world.indexer):
        text = request.url.params.get("q") or request.url.params.get("title") or ""
        for letter in "abcdefghij":
            if f"Zwielicht{letter}x" in text:
                found.add(letter)
    return len(found)


def test_wishes_that_wait_for_a_free_place_rest_when_the_switch_goes_off(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    many_wishes(world, 6)
    with SessionLocal() as db:
        waiting = db.query(ProcureWish).filter(ProcureWish.search_id.is_(None)).count()
    assert waiting == 3, "three searches at a time: three wishes wait for a place"
    configure(enabled=False)
    run_jobs()
    tick()
    run_jobs()
    tick()
    run_jobs()
    assert books_searched(world) == 3, "only the searches that began before the switch went off"
    states = sorted((row["state"], (row["detail"] or {}).get("reason", "")) for row in wishes_of(world))
    assert states.count(("idle", "main_off")) == 3, states
    assert downloads() == []


def test_begin_does_not_search_for_a_wish_that_was_touched_before(operator: Any, net: FakeNetwork) -> None:
    """The old test of the first search only: an older wish (updated since it was made) was searched all the same."""
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    asked = len(searching_requests(world.indexer))
    configure(enabled=False)
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        row.state, row.search_id, row.detail = "searching", None, {"total": 0, "reasons": []}
        db.commit()
        wishes.begin(db, row)
        db.refresh(row)
        assert row.state == "idle" and row.detail["reason"] == "main_off" and row.search_id is None
    run_jobs()
    assert len(searching_requests(world.indexer)) == asked


def test_kick_and_the_end_of_a_search_start_nothing_with_the_switch_off(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    many_wishes(world, 5)
    configure(enabled=False)
    asked = len(searching_requests(world.indexer))
    with SessionLocal() as db:
        wishes.kick(db)
    assert len(searching_requests(world.indexer)) == asked
    run_jobs()
    # The searches that were queued before the switch went off ran (three at most); nobody started another.
    assert books_searched(world) <= 3


def test_a_cancelled_download_with_search_on_does_not_search_with_the_switch_off(operator: Any,
                                                                                net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    [download] = downloads()
    assert download.state in ("queued", "downloading")
    asked = len(searching_requests(world.indexer))
    configure(enabled=False)
    with SessionLocal() as db:
        from app.models import Download

        wishes.after_cancel(db, db.get(Download, download.id), search_on=True)
        run_jobs()
    assert len(searching_requests(world.indexer)) == asked
    assert wishes_of(world)[0]["state"] == "idle" and wishes_of(world)[0]["detail"]["reason"] == "main_off"
    assert sab is not None


def test_nothing_is_read_loaded_or_replaced_with_the_switch_off(operator: Any, net: FakeNetwork,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "not_found"
    configure(enabled=False)
    moving = Moving(monkeypatch)
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,))]
    asked = len(world.indexer.requests)
    for _ in range(30):
        moving.pass_by(hours=1)
        tick()
        run_jobs()
        round_of_tracking()
    assert len(world.indexer.requests) == asked, "no reading, no search, no file"
    assert sab.jobs == {} and downloads() == []
    configure(enabled=True)
    tick()
    run_jobs()
    assert len(sab.jobs) == 1


def test_a_failure_with_the_switch_off_searches_for_no_replacement(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item(AUDIO, size=400_000_000, categories=(3130,)),
                        Item(SECOND_AUDIO, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    [first] = downloads()
    configure(enabled=False)
    asked = len(world.indexer.requests)
    sab.fail(first.client_ref)
    round_of_tracking()
    run_jobs()
    assert len(downloads()) == 1 and len(world.indexer.requests) == asked
    assert wishes_of(world)[0]["state"] == "idle"


def test_a_search_for_a_better_format_does_not_come_back_with_the_switch_off(operator: Any, net: FakeNetwork,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_procure_upgrade_auto import OTHER as UPGRADE_OTHER
    from .test_procure_upgrade_auto import pdf_library

    world, sab = pdf_library(net, UPGRADE_OTHER)
    assert wishes_of(world)[0]["state"] == "upgrade"
    configure(enabled=False)
    moving = Moving(monkeypatch)
    moving.pass_by(hours=30)
    asked = len(world.indexer.requests)
    tick()
    run_jobs()
    tick()
    assert len(world.indexer.requests) == asked and sab.jobs == {}
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        assert row is not None and row.state == "upgrade", "it keeps what it looks for"
    configure(enabled=True)
    tick()
    run_jobs()
    assert len(world.indexer.requests) > asked, "and goes on when the switch is on again"
