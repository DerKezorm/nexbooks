"""How long a pass through a book took: the day of the first report of a pass is kept, and the rule at the end of
the book enters the days the pass took. A book the person finished within a few days is one they liked: the shelf "like
your favourite books" is made of those.

The history is the person's own, and so are these days: nothing here is read for anybody else."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, HistoryEntry, Progress

from .conftest import new_client
from .reading_helpers import Clock, World, at


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


@pytest.fixture
def tick(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


def put(client, edition: int, body: dict[str, object]):  # type: ignore[no-untyped-def]
    answer = client.put(f"/api/v1/editions/{edition}/progress", json=body)
    assert answer.status_code == 200, answer.text
    return answer


def row(edition: int) -> Progress:
    with SessionLocal() as db:
        found = db.scalar(select(Progress).where(Progress.edition_id == edition))
        assert found is not None
        db.expunge(found)
        return found


def days(edition: int) -> list[int | None]:
    with SessionLocal() as db:
        return [e.days for e in db.scalars(select(HistoryEntry).where(HistoryEntry.edition_id == edition)
                                           .order_by(HistoryEntry.id))]


def test_the_first_report_of_a_pass_is_when_it_began_and_a_later_one_does_not_change_it(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        began = row(edition).pass_started_at
        assert began is not None and began == tick.now
        tick.advance(days=2)
        put(rita, edition, at(0.4))
        assert row(edition).pass_started_at == began


def test_a_book_read_to_the_end_within_three_days_took_three_days(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        tick.advance(days=3)
        put(rita, edition, at(0.99))
    assert days(edition) == [3]


def test_the_same_day_is_nought_days(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        tick.advance(seconds=3600)
        put(rita, edition, at(0.99))
    assert days(edition) == [0]


def test_a_book_finished_by_hand_has_no_days(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        assert rita.post(f"/api/v1/editions/{edition}/finished", json={}).status_code in (200, 201)
    assert days(edition) == [None]


def test_reading_it_again_begins_a_new_pass_from_the_moment_the_person_went_back(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        tick.advance(days=20)
        put(rita, edition, at(0.99))
    tick.advance(days=100)
    # (A session that old has ended: the person signs in again.)
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.1))
        assert row(edition).pass_started_at == tick.now, "the pass began when the person went back before half the book"
        tick.advance(days=2)
        put(rita, edition, at(0.99))
    assert days(edition) == [20, 2]


def test_a_place_from_before_the_days_were_kept_gives_no_days(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        with SessionLocal() as db:
            db.query(Progress).update({Progress.pass_started_at: None})
            db.commit()
        tick.advance(days=1)
        put(rita, edition, at(0.99))
    assert days(edition) == [None]


def test_the_days_of_a_recording_are_counted_the_same_way(world: World, tick: Clock) -> None:
    from .reading_helpers import heard

    edition = world.edition("Alpha", "audio")
    with new_client(world.rita) as rita:
        put(rita, edition, heard(30))
        tick.advance(days=4)
        put(rita, edition, heard(599))
    assert days(edition) == [4]


def test_the_days_are_not_in_what_the_history_tells_the_app_or_the_page(world: World, tick: Clock) -> None:
    """They are for the shelves made on the server; they are no part of the answer of the history (nobody reads them off)."""
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        tick.advance(days=1)
        put(rita, edition, at(0.99))
        body = rita.get("/api/v1/history").json()
    assert body and "days" not in body[0] and "pass_started_at" not in body[0]
