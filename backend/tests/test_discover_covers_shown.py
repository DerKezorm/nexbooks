"""The covers of the books of the sources are there when they are shown: a card names an address of its own whenever
the source has a cover, the server fetches it when the picture is asked for (once for two who ask together, within the
turn and the day of the source), and the books people see first are fetched ahead in the lane of Discover. A book the
person has in a library shows the cover of that library and asks no source for anything."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, ForeignBook, Job, ListEntry, ListFetch, SourceState
from app.services import jobs
from app.services.discover import foreign, pics
from app.services.discover import view as discover_view
from app.services.sources import config
from app.services.sources.model import Candidate

from .conftest import make_account, new_client
from .discover_helpers import NOW, Disc
from .discover_shelves import Shelves, by_title, read

APPLE_COVER_HOST = "is1-ssl.mzstatic.com"


@pytest.fixture
def disc(monkeypatch: pytest.MonkeyPatch) -> Disc:
    disc = Disc(monkeypatch)
    disc.only("apple")
    return disc


@pytest.fixture
def shelves(monkeypatch: pytest.MonkeyPatch) -> Shelves:
    return Shelves(monkeypatch)


def book_with_address(title: str = "Far Book", key: str = "1700000501") -> str:
    """A book of Apple's with an address for its cover and no cover; returns its token."""
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(source="apple", key=key, title=title, authors=["A Person"], languages=["de"],
                                           cover_url=f"https://{APPLE_COVER_HOST}/x/{key}/100x100bb.jpg"), "ebook")
        db.commit()
        return row.token


def row_of(token: str) -> ForeignBook:
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.token == token))
        assert row is not None
        db.expunge(row)
        return row


def card_of(client: Any, token: str) -> dict[str, Any]:
    answer = client.get(f"/api/v1/discover/books/{token}")
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def cover_requests(disc: Disc) -> int:
    return disc.fake.count(APPLE_COVER_HOST)


# ---- At the look -----------------------------------------------------------------------------------------------


def test_a_card_names_the_address_of_its_own_and_the_server_fetches_the_cover_when_it_is_asked_for(disc: Disc) -> None:
    token = book_with_address()
    reader = new_client(make_account("reader"))
    card = card_of(reader, token)
    assert card["cover_url"] == f"/api/v1/discover/books/{token}/cover"
    assert "mzstatic" not in str(card), "the address of the source never reaches a browser"
    assert cover_requests(disc) == 0, "showing the card fetched nothing"
    answer = reader.get(card["cover_url"])
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/webp"
    assert answer.content[:4] == b"RIFF" and cover_requests(disc) >= 1
    asked = cover_requests(disc)
    again = reader.get(card["cover_url"])
    assert again.status_code == 200 and cover_requests(disc) == asked, "the stored cover is sent, not fetched again"
    assert pics.path_of(row_of(token).cover) is not None
    # The card now carries the number of the cover.
    assert card_of(reader, token)["cover_url"].startswith(f"/api/v1/discover/books/{token}/cover?v=")


@pytest.mark.parametrize("status", [400, 404, 410])
def test_pictures_that_cannot_be_had_are_held_against_no_source(disc: Disc, status: int) -> None:
    disc.fake.status[APPLE_COVER_HOST] = status
    reader = new_client(make_account("reader"))
    for number in range(8):
        token = book_with_address(f"Book {number}", f"17000006{number:02d}")
        assert reader.get(f"/api/v1/discover/books/{token}/cover").status_code == 404
    with SessionLocal() as db:
        state = db.get(SourceState, "apple")
        assert state is not None and state.fail_count == 0 and state.blocked_until is None
        assert config.availability(db, "apple").ready


def test_a_fault_of_a_picture_host_still_counts(disc: Disc) -> None:
    disc.fake.status[APPLE_COVER_HOST] = 500
    reader = new_client(make_account("reader"))
    assert reader.get(f"/api/v1/discover/books/{book_with_address()}/cover").status_code == 404
    with SessionLocal() as db:
        state = db.get(SourceState, "apple")
        assert state is not None and state.fail_count >= 1


def test_two_who_ask_together_make_one_fetch(disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    token = book_with_address()
    original = foreign.fetch_cover
    calls: list[int] = []

    def slow(db: Any, row: Any, **kwargs: Any) -> bool:
        calls.append(row.id)
        time.sleep(0.3)
        return original(db, row, **kwargs)

    monkeypatch.setattr(foreign, "fetch_cover", slow)
    codes: list[int] = []
    clients = [new_client(make_account(name)) for name in ("one", "two", "three")]

    def ask(client: Any) -> None:
        codes.append(client.get(f"/api/v1/discover/books/{token}/cover").status_code)

    threads = [threading.Thread(target=ask, args=(c,)) for c in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert codes == [200, 200, 200] and len(calls) == 1, "the cover was fetched once"


def test_a_source_that_has_no_picture_gives_404_once_and_is_not_asked_again(disc: Disc) -> None:
    token = book_with_address()
    disc.fake.bodies[APPLE_COVER_HOST] = b"<html>not a picture</html>" * 20
    reader = new_client(make_account("reader"))
    assert reader.get(f"/api/v1/discover/books/{token}/cover").status_code == 404
    asked = cover_requests(disc)
    assert asked >= 1
    assert reader.get(f"/api/v1/discover/books/{token}/cover").status_code == 404
    assert cover_requests(disc) == asked, "a miss is remembered for a while"
    assert row_of(token).cover == ""


def test_a_source_that_fails_gives_404_without_an_error_text_of_the_source(disc: Disc) -> None:
    token = book_with_address()
    disc.fake.status[APPLE_COVER_HOST] = 500
    reader = new_client(make_account("reader"))
    answer = reader.get(f"/api/v1/discover/books/{token}/cover")
    assert answer.status_code in (404, 429) and "mzstatic" not in answer.text


def test_a_source_that_is_used_up_for_the_day_gives_429_and_nothing_is_sent(disc: Disc) -> None:
    token = book_with_address()
    with SessionLocal() as db:
        limit = config.LIMITS["apple"].per_day
        db.merge(SourceState(source="apple", tokens=3.0, refilled_at=clock.now(), day=clock.now().strftime("%Y-%m-%d"),
                             day_count=int(limit * foreign.ON_DEMAND_SHARE)))
        db.commit()
    reader = new_client(make_account("reader"))
    answer = reader.get(f"/api/v1/discover/books/{token}/cover")
    assert answer.status_code == 429 and answer.headers["retry-after"]
    assert cover_requests(disc) == 0
    assert row_of(token).data.get("cover_miss") is None, "a busy source is no miss"


def test_a_book_the_source_has_no_cover_for_has_no_address(disc: Disc) -> None:
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(source="apple", key="1700000502", title="Bare", authors=["A Person"]),
                             "ebook")
        db.commit()
        token = row.token
    reader = new_client(make_account("reader"))
    assert card_of(reader, token)["cover_url"] is None
    assert reader.get(f"/api/v1/discover/books/{token}/cover").status_code == 404
    assert new_client().get(f"/api/v1/discover/books/{token}/cover").status_code == 401


# ---- A book of the library --------------------------------------------------------------------------------------


def test_a_book_the_person_has_shows_the_cover_of_the_library_and_asks_no_source(shelves: Shelves) -> None:
    bob, carol = shelves.client(shelves.bob), shelves.client(shelves.carol)
    work = shelves.work_id("Orbit Null")
    with SessionLocal() as db:
        db.query(Job).filter(Job.kind == "fcover").delete()
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Orbit Null"))
        assert row is not None
        row.cover_url = "https://is1-ssl.mzstatic.com/x/orbit/100x100bb.jpg"
        db.commit()
    shelves.fake.log.clear()
    mine = by_title(read(bob))["Orbit Null"]
    assert mine["owned_work_id"] == work and mine["cover_url"].startswith(f"/api/v1/works/{work}/cover?v=")
    assert bob.get(mine["cover_url"]).status_code == 200
    assert shelves.fake.count(APPLE_COVER_HOST) == 0, "nothing was fetched from a source for it"
    with SessionLocal() as db:
        assert not db.scalar(select(Job.id).where(Job.kind == "fcover")), "and no job for it"
    # Carol has no access to the work: for her it is a book of the source, with the address of the server for it.
    theirs = by_title(read(carol))["Orbit Null"]
    assert theirs["owned_work_id"] is None
    assert theirs["cover_url"] == f"/api/v1/discover/books/{theirs['token']}/cover"


def test_the_cover_of_a_book_the_person_has_is_there_after_the_day_of_the_sources_is_over(
        shelves: Shelves) -> None:
    bob = shelves.client(shelves.bob)
    before = by_title(read(bob))["Orbit Null"]["cover_url"]
    shelves.days(2)
    with SessionLocal() as db:
        foreign.expire_pictures(db)
    after = by_title(read(bob))["Orbit Null"]["cover_url"]
    assert after == before and bob.get(after).status_code == 200


def test_a_book_everybody_has_with_a_cover_of_the_library_is_not_fetched_ahead(shelves: Shelves) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Orbit Null"))
        assert row is not None
        # Alice and carol do not have it: it is needed for them.
        assert row.id not in discover_view.owned_with_cover_by_all(db, [row])
        others = list(db.scalars(select(Account.id).where(Account.name != "bob")))
        assert others
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Orbit Null"))
        assert row is not None
        bob = db.scalar(select(Account.id).where(Account.name == "bob"))
        original = discover_view.select

        def only_bob(*args: Any, **kwargs: Any) -> Any:
            return original(bob) if args and args[0] is Account.id else original(*args, **kwargs)

        discover_view.select = only_bob  # type: ignore[assignment,misc]
        try:
            assert row.id in discover_view.owned_with_cover_by_all(db, [row])
        finally:
            discover_view.select = original  # type: ignore[assignment,misc]


# ---- Fetched ahead ----------------------------------------------------------------------------------------------


def front_list(count: int, market: str = "de") -> list[int]:
    """A list of Apple's with ``count`` books (the cover address each), looked at by a person; returns the ids."""
    ids: list[int] = []
    with SessionLocal() as db:
        for rank in range(1, count + 1):
            row = foreign.upsert(db, Candidate(
                source="apple", key=f"17000{rank:05d}", title=f"List Book {rank}", authors=["A Person"],
                cover_url=f"https://{APPLE_COVER_HOST}/x/{rank}/100x100bb.jpg"), "ebook", market=market)
            db.flush()
            db.add(ListEntry(source="apple", market=market, list="best", rank=rank, ref=row.ref, foreign_id=row.id,
                             title=row.title, author="A Person", cover_url=row.cover_url, kind="ebook",
                             fetched_at=clock.now()))
            ids.append(row.id)
        db.add(ListFetch(source="apple", market=market, unit="best", state="ok", count=count, fetched_at=clock.now(),
                         wanted_at=clock.now()))
        db.commit()
    return ids


def covered(ids: list[int]) -> list[bool]:
    with SessionLocal() as db:
        found = {r.id: bool(r.cover) for r in db.scalars(select(ForeignBook).where(ForeignBook.id.in_(ids)))}
    return [found[i] for i in ids]


def run_all() -> int:
    ran = 0
    while step := jobs.run_pending(1000):
        ran += step
    return ran


def test_the_first_books_of_a_list_are_fetched_ahead_in_the_lane_of_discover_and_the_rest_wait_for_a_look(
        disc: Disc) -> None:
    ids = front_list(45)
    with SessionLocal() as db:
        queued = foreign.plan_front_covers(db)
        kinds = {job.kind for job in db.scalars(select(Job))}
    assert queued == foreign.FRONT_PER_LIST and kinds == {"fcover"}
    assert "fcover" in jobs.LANES["discover"], "in the lane of Discover, not beside the scan and the index"
    started, asked_before = disc.slept, cover_requests(disc)
    ran = run_all()
    seconds = disc.slept - started
    asked = cover_requests(disc) - asked_before
    shown = covered(ids)
    assert shown[:20] == [True] * 20 and not any(shown[20:]), "the front ones are there, the rest wait for a look"
    # The measure: jobs run, requests to the source, and the time (on the clock of the tests) until the front is there.
    print(f"\nMEASURE front-of-list: jobs={ran} requests={asked} seconds_to_front={seconds:.0f}")
    assert ran == 20 and asked <= 2 * 20 and seconds <= 60
    with SessionLocal() as db:
        assert foreign.plan_front_covers(db) == 0, "nothing more to fetch while they are young"


def test_a_book_the_person_is_shown_is_not_fetched_a_second_time_by_the_job(disc: Disc) -> None:
    ids = front_list(3)
    reader = new_client(make_account("reader"))
    token = row_of_id(ids[0]).token
    assert reader.get(f"/api/v1/discover/books/{token}/cover").status_code == 200
    asked = cover_requests(disc)
    with SessionLocal() as db:
        foreign.plan_front_covers(db)
    run_all()
    assert cover_requests(disc) - asked == 2, "only the two that were not shown"


def row_of_id(ident: int) -> ForeignBook:
    with SessionLocal() as db:
        row = db.get(ForeignBook, ident)
        assert row is not None
        db.expunge(row)
        return row


def test_the_fetching_ahead_stops_at_half_of_the_day_of_a_source(disc: Disc) -> None:
    ids = front_list(10)
    with SessionLocal() as db:
        limit = config.LIMITS["apple"].per_day
        db.merge(SourceState(source="apple", tokens=3.0, refilled_at=clock.now(), day=clock.now().strftime("%Y-%m-%d"),
                             day_count=int(limit * foreign.BACKGROUND_SHARE) - 4))
        db.commit()
        assert foreign.plan_front_covers(db) == 4, "four more fit under the half"
    run_all()
    assert covered(ids).count(True) == 4
    with SessionLocal() as db:
        assert foreign.plan_front_covers(db) == 0


def test_a_cover_about_to_run_out_is_fetched_anew_so_that_there_is_no_hole(
        disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = front_list(6)
    with SessionLocal() as db:
        foreign.plan_front_covers(db)
    run_all()
    assert all(covered(ids))
    first = {r.id: r.cover for r in [row_of_id(i) for i in ids]}
    disc.advance(foreign.RENEW_HOURS * 3600 + 60)
    with SessionLocal() as db:
        assert foreign.plan_front_covers(db) == 6, "they are renewed before the day is over"
    run_all()
    disc.advance(5 * 3600)
    with SessionLocal() as db:
        foreign.expire_pictures(db)
    assert all(covered(ids)), "nothing was lost when the first day was over"
    assert first and all(row_of_id(i).cover for i in ids)


def test_a_book_without_a_picture_is_not_fetched_ahead_over_and_over(disc: Disc) -> None:
    ids = front_list(3)
    disc.fake.bodies[APPLE_COVER_HOST] = b"<html>no</html>" * 20
    with SessionLocal() as db:
        foreign.plan_front_covers(db)
    run_all()
    asked = cover_requests(disc)
    with SessionLocal() as db:
        assert foreign.plan_front_covers(db) == 0
    run_all()
    assert cover_requests(disc) == asked and not any(covered(ids))
    assert timedelta(0) < timedelta(hours=foreign.MISS_HOURS) and NOW


def test_the_books_everybody_has_are_left_out_when_the_first_books_are_queued(
        shelves: Shelves, monkeypatch: pytest.MonkeyPatch) -> None:
    with SessionLocal() as db:
        db.query(Job).delete()
        for row in db.scalars(select(ForeignBook)):
            row.cover = ""
            row.cover_url = f"https://{APPLE_COVER_HOST}/x/{row.id}/100x100bb.jpg"
            row.ref = f"apple:{row.id}"
        db.commit()
        orbit = db.scalar(select(ForeignBook).where(ForeignBook.title == "Orbit Null"))
        assert orbit is not None
        orbit_id = orbit.id
        others = {r.id for r in db.scalars(select(ForeignBook)) if r.id != orbit_id}
        assert others
    monkeypatch.setattr(discover_view, "owned_with_cover_by_all", lambda db, rows: {orbit_id})
    with SessionLocal() as db:
        foreign.plan_front_covers(db)
        queued = set(db.scalars(select(Job.ref).where(Job.kind == "fcover")))
    assert orbit_id not in queued and queued & others
