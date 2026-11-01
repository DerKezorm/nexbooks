"""The newest entries of the indexers: a release that appears is held against the wishes that wait with the
rules of a search; the best that fits is loaded, once; every indexer is read in its time and within its limit; an entry
is judged once."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import BlocklistEntry, Download, Indexer, ProcureWish, ReleaseSearch
from app.services.procure import automation, blocklist, rss

from .auto_helpers import Moving, configure, indexer_row, reading_requests, runs, seen, tick
from .follow_helpers import AUDIO, BAD_AUDIO, SECOND_AUDIO, audio_world, downloads, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, World

OTHER = "Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE"


def waiting_world(net: FakeNetwork) -> tuple[World, Any]:
    """A wish that found nothing, an indexer whose feed the test changes."""
    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "not_found"
    return world, sab


def good(title: str = AUDIO, **extra: Any) -> Item:
    return Item(title, size=400_000_000, categories=(3130,), **extra)


def test_a_release_among_the_newest_entries_is_loaded_for_the_wish_that_waits(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    before = len(reading_requests(world.indexer))
    world.indexer.items = [good(), Item(OTHER, size=1, categories=(3130,))]
    assert rss.sync() == 2
    [shown] = wishes_of(world)
    assert shown["state"] == "loading"
    [download] = downloads()
    assert download.title == AUDIO and download.state == "queued" and download.wish_id == shown["id"]
    assert len(sab.jobs) == 1
    assert len(reading_requests(world.indexer)) == before + 1, "one request read the newest entries"
    with SessionLocal() as db:
        made = db.scalars(select(ReleaseSearch).where(ReleaseSearch.origin == "rss")).all()
        assert len(made) == 1 and made[0].user_id == world.person.id and made[0].state == "done"


def test_nothing_is_loaded_twice_when_the_same_entries_come_again(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    world.indexer.items = [good()]
    rss.sync()
    assert len(sab.jobs) == 1
    moving_on(net)
    assert rss.sync() == 0, "the entry was seen"
    assert len(downloads()) == 1 and len(sab.jobs) == 1


def moving_on(net: FakeNetwork) -> None:
    """The reading of every indexer is due again."""
    with SessionLocal() as db:
        db.execute(update(Indexer).values(rss_checked_at=None))
        db.commit()


def test_an_entry_that_does_not_pass_is_judged_once_and_not_loaded(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    world.indexer.items = [Item(BAD_AUDIO, size=400_000_000, categories=(3130,))]
    assert rss.sync() == 1
    assert [item["state"] for item in wishes_of(world)] == ["not_found"] and sab.jobs == {}
    assert len(seen()) == 1
    with SessionLocal() as db:
        assert db.scalars(select(ReleaseSearch).where(ReleaseSearch.origin == "rss")).all() == []
    moving_on(net)
    assert rss.sync() == 0, "the same entry is not looked at again"
    world.indexer.items = [Item(BAD_AUDIO, size=400_000_000, categories=(3130,)), good()]
    moving_on(net)
    assert rss.sync() == 1
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


@pytest.mark.parametrize("title", [
    "Ida Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE",
    "Rune Falkner-Ein ganz anderes Buch-MP3CD-DE-2021-EXAMPLE",
    "Rune Falkner-Die Laternenstrasse-MP3CD-ENGLISH-2021-EXAMPLE",
    "Rune Falkner-Nebelchor 07-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE",
    BAD_AUDIO,
])
def test_the_rules_of_a_search_hold_for_the_newest_entries_too(operator: Any, net: FakeNetwork, title: str) -> None:
    world, sab = waiting_world(net)
    world.indexer.items = [Item(title, size=400_000_000, categories=(3130,))]
    rss.sync()
    assert [item["state"] for item in wishes_of(world)] == ["not_found"] and sab.jobs == {} and downloads() == []


def test_a_release_on_the_blocklist_of_the_book_is_not_taken(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    with SessionLocal() as db:
        db.add(BlocklistEntry(work_id=world.work_id, kind="audio", title=AUDIO, title_key=blocklist.key_of(AUDIO),
                              protocol="usenet", reason="by_hand"))
        db.commit()
    world.indexer.items = [good()]
    rss.sync()
    assert wishes_of(world)[0]["state"] == "not_found" and sab.jobs == {}
    world.indexer.items = [good(), good(SECOND_AUDIO)]
    moving_on(net)
    rss.sync()
    assert [item.title for item in downloads()] == [SECOND_AUDIO]


def test_the_best_of_all_the_indexers_is_loaded(operator: Any, net: FakeNetwork) -> None:
    from .procure_search_helpers import FeedIndexer, add_indexer

    world, sab = waiting_world(net)
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    add_indexer(second, name="Second", priority=1)
    world.indexer.items = [good(SECOND_AUDIO)]
    second.items = [good()]
    with SessionLocal() as db:
        db.execute(update(Indexer).where(Indexer.name == "Shelf").values(priority=10))
        db.commit()
    rss.sync()
    assert len(downloads()) == 1
    [loaded_] = downloads()
    # Both fit; the one from the indexer with the lower number (the higher priority) comes first.
    assert loaded_.indexer_name == "Second" and loaded_.title == AUDIO and len(sab.jobs) == 1


def test_only_a_wish_that_waits_takes_a_release(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    # A person cancelled the work on it: the wish rests and nothing is loaded for it by itself.
    with SessionLocal() as db:
        db.execute(update(ProcureWish).values(state="idle", detail={"reason": "cancelled"}))
        db.commit()
    world.indexer.items = [good()]
    rss.sync()
    assert downloads() == [] and sab.jobs == {}
    assert wishes_of(world)[0]["state"] == "idle"


def test_a_wish_that_is_searched_at_this_moment_is_left_alone(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, good())
    wish(world)
    # The wish is searching (the job did not run yet): the newest entries do not take it.
    world.indexer.items = [good()]
    rss.sync()
    assert downloads() == []
    run_jobs()
    assert len(downloads()) == 1 and len(sab.jobs) == 1


def test_the_search_and_the_newest_entries_find_one_release_and_load_it_once(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    world.indexer.items = [good()]
    # Both ways at once: the reading and a person who searches again.
    import threading

    gate = threading.Barrier(2)
    answers: list[Any] = []

    def search_again() -> None:
        gate.wait()
        wish_id = wishes_of(world)[0]["id"]
        answers.append(world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code)

    thread = threading.Thread(target=search_again)
    thread.start()
    gate.wait()
    rss.sync()
    thread.join()
    run_jobs()
    assert len(downloads()) == 1 and len(sab.jobs) == 1
    assert wishes_of(world)[0]["state"] == "loading"


def test_an_indexer_is_read_in_the_time_the_operator_chose(operator: Any, net: FakeNetwork,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    world, _sab = waiting_world(net)
    moving = Moving(monkeypatch)
    configure(rss_minutes=30)
    before = len(reading_requests(world.indexer))
    assert rss.sync() == 1
    assert len(reading_requests(world.indexer)) == before + 1
    moving.pass_by(minutes=29)
    rss.sync()
    assert len(reading_requests(world.indexer)) == before + 1, "not before the time"
    moving.pass_by(minutes=2)
    rss.sync()
    assert len(reading_requests(world.indexer)) == before + 2
    configure(rss_minutes=5)
    moving.pass_by(minutes=5)
    rss.sync()
    assert len(reading_requests(world.indexer)) == before + 3


def test_with_the_main_switch_off_nothing_is_read(operator: Any, net: FakeNetwork) -> None:
    world, sab = waiting_world(net)
    world.indexer.items = [good()]
    configure(enabled=False)
    before = len(world.indexer.requests)
    assert rss.sync() == 0 and tick() is not None
    assert len(world.indexer.requests) == before and downloads() == [] and sab.jobs == {}
    assert indexer_row().rss_checked_at is None


def test_an_indexer_that_rests_is_left_out_and_the_others_are_read(operator: Any, net: FakeNetwork,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from .procure_search_helpers import FeedIndexer, add_indexer

    world, sab = waiting_world(net)
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    second_id = add_indexer(second, name="Second", priority=5)
    moving = Moving(monkeypatch)
    second.items = [good()]
    from datetime import timedelta as delta

    with SessionLocal() as db:
        row = db.get(Indexer, world.indexer_id)
        row.paused_until, row.pause_cause = moving_now() + delta(hours=1), "request_limit"
        db.commit()
    before = len(world.indexer.requests)
    rss.sync()
    assert len(world.indexer.requests) == before, "the resting indexer was not asked"
    assert len(downloads()) == 1 and downloads()[0].indexer_id == second_id and len(sab.jobs) == 1
    assert [line.rss_error for line in [indexer_row(world.indexer_id)]] == ["indexer_limit_reached"]
    moving.pass_by(hours=2)


def moving_now() -> Any:
    from app import clock

    return clock.now()


def test_a_request_limit_answer_pauses_the_indexer_and_is_kept(operator: Any, net: FakeNetwork) -> None:
    import httpx

    world, _sab = waiting_world(net)
    original = world.indexer.handle

    def limited(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("t") == "search" and not request.url.params.get("q"):
            return httpx.Response(429, headers={"retry-after": "600"}, content=b"")
        return original(request)

    world.indexer.handle = limited  # type: ignore[method-assign]
    moving_on(net)
    rss.sync()
    row = indexer_row(world.indexer_id)
    assert row.rss_error == "indexer_limit_reached" and row.paused_until is not None
    assert row.pause_cause == "request_limit"
    [line] = [run for run in runs() if run.kind == "rss"]
    assert line.counts["indexers"][str(world.indexer_id)]["error"] == "indexer_limit_reached"


def test_the_reading_may_use_half_of_the_limit_of_the_day_and_not_more(operator: Any, net: FakeNetwork,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    world, _sab = waiting_world(net)
    moving = Moving(monkeypatch)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(daily_limit=20))
        db.commit()
    # Half of 20 is 10 requests for the reading: one every 144 minutes; every request counts.
    row = indexer_row()
    assert rss.interval_of(row, 5) == timedelta(minutes=144)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(used_count=10, used_day=__import__("app.services.procure.budget",
                                                                               fromlist=["day"]).day()))
        db.commit()
    before = len(reading_requests(world.indexer))
    rss.sync()
    assert len(reading_requests(world.indexer)) == before and indexer_row().rss_error == "rss_share_used"
    # Tomorrow the count starts again.
    moving.pass_by(days=1)
    rss.sync()
    assert len(reading_requests(world.indexer)) == before + 1 and indexer_row().rss_error == ""


def test_a_second_page_is_read_only_while_a_page_holds_nothing_but_new_entries(operator: Any, net: FakeNetwork) -> None:
    world, _sab = waiting_world(net)
    world.indexer.items = [Item(f"Nobody {number}-Nothing {number}-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                categories=(3130,)) for number in range(250)]
    before = len(reading_requests(world.indexer))
    assert rss.sync() == 250
    assert len(reading_requests(world.indexer)) == before + 3, "three pages are the most"
    moving_on(net)
    before = len(reading_requests(world.indexer))
    assert rss.sync() == 0
    assert len(reading_requests(world.indexer)) == before + 1, "the first page held only what was seen"


def test_what_was_seen_is_forgotten_after_two_weeks(operator: Any, net: FakeNetwork,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    world, _sab = waiting_world(net)
    moving = Moving(monkeypatch)
    world.indexer.items = [Item(BAD_AUDIO, size=400_000_000, categories=(3130,))]
    rss.sync()
    assert len(seen()) == 1
    moving.pass_by(days=15)
    with SessionLocal() as db:
        assert rss.prune(db) == 1
    assert seen() == []


def test_a_wish_for_a_book_that_is_not_out_yet_takes_the_release_that_appears(operator: Any, net: FakeNetwork) -> None:
    from datetime import timedelta as delta

    from app import clock
    from app.models import ForeignBook

    sab = sabnzbd(net)
    world = audio_world(net, Item(OTHER, size=400_000_000, categories=(3130,)))
    soon = (clock.now() + delta(days=30)).date().isoformat()
    token = "tok" + "w" * 13
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:77", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de", series="Nebelchor", series_pos=2.0, released=soon))
        db.commit()
    world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"], "language": "de"})
    assert wishes_of(world)[0]["state"] == "waiting"
    world.indexer.items = [good()]
    rss.sync()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


def test_the_log_counts_the_requests_and_names_neither_a_key_nor_a_title(operator: Any, net: FakeNetwork) -> None:
    world, _sab = waiting_world(net)
    world.indexer.items = [good()]
    rss.sync()
    [line] = [run for run in runs() if run.kind == "rss"]
    counts = line.counts
    assert counts["indexers"][str(world.indexer_id)] == {"requests": 1, "entries": 1, "error": ""}
    assert counts["wishes"] == 1 and counts["matched"] == 1 and counts["loaded"] == 1
    text = str(line.counts) + line.error
    assert world.indexer.api_key not in text and "Laternenstrasse" not in text and "Falkner" not in text


def test_a_wish_of_a_person_who_lost_the_right_does_not_load(operator: Any, net: FakeNetwork) -> None:
    from app.models import LibraryMember

    world, sab = waiting_world(net)
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=world.person.id).delete()
        db.commit()
    world.indexer.items = [good()]
    rss.sync()
    assert downloads() == [] and sab.jobs == {}


def test_rss_needs_the_wish_to_have_a_place_in_a_library_that_still_takes_the_book(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    world, _sab = waiting_world(net)
    with SessionLocal() as db:
        db.execute(update(ProcureWish).values(library_id=None))
        db.commit()
    world.indexer.items = [good()]
    rss.sync()
    assert downloads() == []


def test_the_switch_on_again_wakes_the_wishes_that_rested(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, good())
    configure(enabled=False)
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "idle" and sab.jobs == {}
    configure(enabled=True)
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


def test_the_settings_keep_the_reading_time_valid() -> None:
    with SessionLocal() as db:
        for value in (5, 15, 30, 60):
            assert automation.save(db, {**automation.config(db), "rss_minutes": value})["rss_minutes"] == value
        with pytest.raises(Exception):  # noqa: B017 - the error carries the field
            automation.save(db, {**automation.config(db), "rss_minutes": 7})
    assert Download is not None


def test_a_torrent_among_the_newest_entries_is_loaded_into_the_torrent_program(operator: Any, net: FakeNetwork) -> None:
    from .follow_helpers import qbittorrent, torrent_world

    qbit = qbittorrent(net)
    world, tracker, info_hash = torrent_world(net)
    items = list(tracker.items)
    tracker.items = [Item(OTHER, size=400_000_000, categories=(3130,), seeders=20)]
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "not_found" and qbit.added == []
    tracker.items = items
    assert rss.sync() >= 1
    [row] = downloads()
    assert row.protocol == "torrent" and row.client_ref == info_hash and len(qbit.added) == 1
    assert wishes_of(world)[0]["state"] == "loading"


def test_an_indexer_that_keeps_failing_the_same_way_names_the_failure_once_in_the_log(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    world, _sab = waiting_world(net)
    moving = Moving(monkeypatch)
    configure(rss_minutes=5)
    original = world.indexer.handle

    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("t") == "search" and not request.url.params.get("q"):
            return httpx.Response(401, content=b"")
        return original(request)

    world.indexer.handle = broken  # type: ignore[method-assign]
    asked = 0
    for number in range(4):
        rss.sync()
        if number == 0:
            asked = len(world.indexer.requests)
        moving.pass_by(minutes=6)
    assert indexer_row(world.indexer_id).rss_error == "indexer_key_rejected"
    errors = [run.counts["indexers"][str(world.indexer_id)]["error"] for run in runs() if run.kind == "rss"]
    assert errors == ["indexer_key_rejected"], "the failure is named when it is new, and the key is not tried again"
    assert len(world.indexer.requests) == asked, "an indexer that refused the key is left alone, not asked every reading"


def test_an_indexer_that_is_down_is_noted_and_the_others_are_read(operator: Any, net: FakeNetwork,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from .procure_search_helpers import FeedIndexer, add_indexer

    world, sab = waiting_world(net)
    second = net.add(FeedIndexer("second.example.com", "k" * 32))
    second.items = [good()]
    add_indexer(second, name="Second", priority=30)
    net.down.add(world.indexer.host)
    assert rss.sync() == 1
    assert indexer_row(world.indexer_id).rss_error == "indexer_unreachable"
    assert len(downloads()) == 1 and downloads()[0].indexer_name == "Second" and len(sab.jobs) == 1
    [line] = [run for run in runs() if run.kind == "rss"]
    assert line.counts["indexers"][str(world.indexer_id)]["error"] == "indexer_unreachable"
    net.down.discard(world.indexer.host)
    asked = len(world.indexer.requests)
    held = rss.read(world.indexer_id, clock.now())
    assert held.error == "indexer_unreachable" and len(world.indexer.requests) == asked, "it rests a little while"
    Moving(monkeypatch).pass_by(minutes=3)
    again = rss.read(world.indexer_id, clock.now())
    assert again.error == "" and len(again.fresh) == 1, "the one that was down is read again"
    assert indexer_row(world.indexer_id).rss_error == ""


def test_a_wish_whose_person_may_only_read_now_takes_nothing_and_is_left_as_it_was(operator: Any,
                                                                                  net: FakeNetwork) -> None:
    from .library_helpers import join

    world, sab = waiting_world(net)
    join(world.library_id, world.person.id, "read")
    with SessionLocal() as db:
        detail = db.scalar(select(ProcureWish.detail))
    world.indexer.items = [good()]
    rss.sync()
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        assert row.state == "not_found" and row.detail == detail and "load_error" not in (row.detail or {})
    assert downloads() == [] and sab.jobs == {}


def test_a_wish_another_was_quicker_to_take_leaves_no_search_behind_and_loads_nothing(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import wishes

    world, sab = waiting_world(net)
    world.indexer.items = [good()]
    monkeypatch.setattr(wishes, "claim", lambda *args, **kwargs: False)
    rss.sync()
    assert downloads() == [] and sab.jobs == {}
    with SessionLocal() as db:
        assert db.scalars(select(ReleaseSearch).where(ReleaseSearch.origin == "rss")).all() == []


def test_entries_that_name_no_author_of_a_wish_are_not_even_judged(operator: Any, net: FakeNetwork,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import search

    world, _sab = waiting_world(net)
    calls: list[int] = []
    original = search.judge_items

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(search, "judge_items", counting)
    world.indexer.items = [Item(f"Somebody{number} Else{number}-Another Thing-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                categories=(3130,)) for number in range(30)]
    assert rss.sync() == 30
    assert calls == [], "no entry names the author: the judgement is not asked for any of them"
    world.indexer.items = [good()]
    moving_on(net)
    rss.sync()
    assert calls == [1]
