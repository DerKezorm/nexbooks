"""The history of the reading stack goes where its edition goes (the work on the audiobooks puts editions
together and works together): the parts of a recording that an older version kept as editions of their own become one,
and what was finished in any of them is finished in the one; a book that is put to another work keeps its entries when
the old work goes. Nothing is lost, nothing is entered twice. All names are invented."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, BookFile, Edition, HistoryEntry, Progress, Work

from .conftest import make_account
from .library_helpers import books_folder, make_library, scan
from .reading_helpers import World
from .test_recognition_folders import forget_editions, old_style, parts

WHEN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_what_was_finished_in_a_piece_of_a_recording_is_finished_in_the_whole() -> None:
    folder = books_folder("history-pieces")
    for disc in (1, 2):
        parts(folder, f"Rune Falkner/Nachtglas (2019)/CD {disc}/{{number:02d}}.mp3", 2, seconds=60.0,
              album="Nachtglas", artist="Rune Falkner")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        paths = {disc: sorted(db.scalars(select(BookFile.path).where(BookFile.path.like(f"%CD {disc}/%"))))
                 for disc in (1, 2)}
    forget_editions()
    # An older version made one edition of every disc. Pia finished the first twice (on two days) and Quentin twice; in
    # the second, Pia listened to the end (the pass is entered) and entered it on the first of her days, and Quentin
    # finished it on a day of its own.
    ids = old_style(library_id, [paths[1], paths[2]])
    first, second = ids[paths[1][0]], ids[paths[2][0]]
    pia, quentin = make_account("pia", "member"), make_account("quentin", "member")
    with SessionLocal() as db:
        for who, edition, day in ((pia, first, "2026-09-01"), (pia, first, "2026-09-20"), (quentin, first, "2026-09-02"),
                                  (quentin, first, "2026-09-05"), (pia, second, "2026-09-01"),
                                  (quentin, second, "2026-09-03")):
            db.add(HistoryEntry(user_id=who.id, edition_id=edition, kind="listened", finished_on=day, source="hand",
                                created_at=WHEN))
        db.add(Progress(user_id=pia.id, edition_id=second, locator="time:59.000", fraction=0.99, unit="time",
                        moved_at=clock.now(), received_at=clock.now(), pass_done=True))
        db.commit()
    scan(library_id)
    with SessionLocal() as db:
        (edition,) = db.scalars(select(Edition)).all()
        # The edition people did most in is the one that stays (four finished passes against two and a place).
        assert edition.id == first
        entries = sorted((h.user_id, h.finished_on, h.edition_id, h.work_id) for h in db.scalars(select(HistoryEntry)))
        # Every pass is there once: Pia's day in both pieces is one entry, Quentin's day of the second piece came along.
        assert entries == sorted((who.id, day, first, edition.work_id) for who, day in (
            (pia, "2026-09-01"), (pia, "2026-09-20"), (quentin, "2026-09-02"), (quentin, "2026-09-03"),
            (quentin, "2026-09-05")))
        (place,) = db.scalars(select(Progress)).all()
        # The place moved by the first disc (two minutes), and the pass it ended stays entered: the next report does not enter it again.
        assert place.edition_id == first and place.pass_done is True
        assert 178.0 <= float(place.locator.removeprefix("time:")) <= 180.0


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


def test_a_book_put_to_another_work_keeps_its_history_when_the_old_work_goes(world: World, client: TestClient) -> None:
    gamma = world.edition("Gamma")
    assert client.post(f"/api/v1/editions/{gamma}/finished", json={"finished_on": "2026-08-01"}).status_code == 201
    old = world.work_of(gamma)
    with SessionLocal() as db:
        other = Work(title="Gamma", added_at=WHEN, updated_at=WHEN)
        db.add(other)
        db.flush()
        new = other.id
        edition = db.get(Edition, gamma)
        assert edition is not None
        edition.work_id = new
        db.flush()
        db.delete(db.get(Work, old))
        db.commit()
    entries = client.get("/api/v1/history").json()
    assert [(e["edition_id"], e["work_id"]) for e in entries] == [(gamma, new)]
    assert client.get("/api/v1/history", params={"work": new}).json()[0]["edition_id"] == gamma
    assert client.get("/api/v1/history", params={"work": old}).json() == []
    stack = client.get("/api/v1/stack").json()
    assert [entry["work"]["id"] for year in stack["years"] for entry in year["entries"]] == [new]
