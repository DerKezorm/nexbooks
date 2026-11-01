"""The list "for later" of the apps under ``/api/app/1/later``: the books on a person's own shelves they mean to read or
hear. It is the person's own, shows only what the person sees and keeps the rest, follows a book into another work, holds
500 books, and a book that is finished leaves it by itself, whichever way and on whichever device."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.db import SessionLocal
from app.main import app
from app.models import Edition, LaterEntry, LibraryMember, Work
from app.services import later as later_service

from .app_helpers import Shelf, app_of, device_token, make_shelf
from .conftest import new_client
from .reading_helpers import Clock

ORCHARD, KITE, SECRET = "The Quiet Orchard", "Kite Season", "A Secret Shelf"


@pytest.fixture
def shelf() -> Shelf:
    return make_shelf()


def listed(client: TestClient) -> list[int]:
    answer = client.get("/api/app/1/later")
    assert answer.status_code == 200, answer.text
    return [item["work_id"] for item in answer.json()["items"]]


def test_a_book_goes_on_the_list_and_the_newest_comes_first(shelf: Shelf, monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    first, second = shelf.works[ORCHARD], shelf.works[KITE]
    assert listed(shelf.ada_app) == []
    assert shelf.ada_app.put(f"/api/app/1/later/{first}").status_code == 204
    tick.advance(seconds=60)
    assert shelf.ada_app.put(f"/api/app/1/later/{second}").status_code == 204
    items = shelf.ada_app.get("/api/app/1/later").json()["items"]
    assert [item["work_id"] for item in items] == [second, first]
    assert set(items[0]) == {"work_id", "added_at"} and items[0]["added_at"].startswith("2026-10-13T12:01:00")


def test_asking_twice_does_no_harm_and_keeps_the_moment_it_was_put_there(
        shelf: Shelf, monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    work = shelf.works[ORCHARD]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    before = shelf.ada_app.get("/api/app/1/later").json()
    tick.advance(seconds=3600)
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    assert shelf.ada_app.get("/api/app/1/later").json() == before
    assert shelf.ada_app.delete(f"/api/app/1/later/{work}").status_code == 204
    assert shelf.ada_app.delete(f"/api/app/1/later/{work}").status_code == 204
    assert shelf.ada_app.delete("/api/app/1/later/999999").status_code == 204
    assert listed(shelf.ada_app) == []


def test_a_work_the_person_does_not_see_is_like_one_that_does_not_exist(shelf: Shelf) -> None:
    for work in (shelf.works[SECRET], 999999):
        refused = shelf.ada_app.put(f"/api/app/1/later/{work}")
        assert refused.status_code == 404 and refused.json()["detail"]["code"] == "not_found"
    assert listed(shelf.ada_app) == []


def test_the_list_is_the_persons_own(shelf: Shelf) -> None:
    work = shelf.works[ORCHARD]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    assert listed(shelf.ben_app) == []
    assert shelf.ben_app.delete(f"/api/app/1/later/{work}").status_code == 204
    assert listed(shelf.ada_app) == [work]


def test_an_entry_the_person_does_not_see_stays_and_comes_back(shelf: Shelf) -> None:
    work = shelf.works[ORCHARD]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    with SessionLocal() as db:
        row = db.scalars(select(LibraryMember).where(LibraryMember.user_id == shelf.ada.id)).one()
        library, right = row.library_id, row.right
        db.delete(row)
        db.commit()
    assert listed(shelf.ada_app) == []
    with SessionLocal() as db:
        assert db.get(LaterEntry, (shelf.ada.id, work)) is not None
        db.add(LibraryMember(library_id=library, user_id=shelf.ada.id, right=right))
        db.commit()
    assert listed(shelf.ada_app) == [work]


def test_only_a_device_comes_in(shelf: Shelf) -> None:
    work = shelf.works[ORCHARD]
    with TestClient(app, base_url="http://testserver") as nobody:
        assert nobody.get("/api/app/1/later").status_code == 401
    with new_client(shelf.ada) as browser:
        assert browser.get("/api/app/1/later").status_code == 401
        assert browser.put(f"/api/app/1/later/{work}", headers={"X-Nexbooks-Client": "tab-test0000"}).status_code == 401
    assert listed(shelf.ada_app) == []


def test_a_full_list_says_so_and_a_book_on_it_can_still_be_asked_for(
        shelf: Shelf, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(later_service, "MAX_ENTRIES", 2)
    first, second, third = shelf.works[ORCHARD], shelf.works[KITE], shelf.works[SECRET]
    assert shelf.ben_app.put(f"/api/app/1/later/{first}").status_code == 204
    assert shelf.ben_app.put(f"/api/app/1/later/{second}").status_code == 204
    full = shelf.ben_app.put(f"/api/app/1/later/{third}")
    assert full.status_code == 409 and full.json()["detail"]["code"] == "later_full"
    assert shelf.ben_app.put(f"/api/app/1/later/{first}").status_code == 204, "a book that is on it asks for no room"
    assert sorted(listed(shelf.ben_app)) == sorted([first, second])
    assert shelf.ben_app.delete(f"/api/app/1/later/{first}").status_code == 204
    assert shelf.ben_app.put(f"/api/app/1/later/{third}").status_code == 204


def test_the_limit_is_500() -> None:
    assert later_service.MAX_ENTRIES == 500


def test_a_book_finished_by_hand_leaves_the_list_and_can_go_back_on_it(shelf: Shelf) -> None:
    work, edition, other = shelf.works[ORCHARD], shelf.editions[f"{ORCHARD}/ebook"], shelf.works[KITE]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    assert shelf.ada_app.put(f"/api/app/1/later/{other}").status_code == 204
    assert shelf.ada_app.post(f"/api/app/1/editions/{edition}/finished", json={}).status_code == 201
    assert listed(shelf.ada_app) == [other]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    assert work in listed(shelf.ada_app)


def test_a_book_finished_on_a_day_gone_by_leaves_the_list_too(shelf: Shelf) -> None:
    work, edition = shelf.works[ORCHARD], shelf.editions[f"{ORCHARD}/ebook"]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    entered = shelf.ada_app.post(f"/api/app/1/editions/{edition}/finished", json={"finished_on": "2020-05-01"})
    assert entered.status_code == 201
    assert listed(shelf.ada_app) == []


def test_a_recording_that_reaches_its_end_on_another_device_leaves_the_list(shelf: Shelf) -> None:
    work, edition = shelf.works[ORCHARD], shelf.editions[f"{ORCHARD}/audio"]
    with SessionLocal() as db:
        found = db.get(Edition, edition)
        assert found is not None
        length = found.duration
    assert length > 0
    second = app_of(device_token(shelf.ada, "Ada's iPad"))
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    near = second.put(f"/api/app/1/editions/{edition}/progress", json={"locator": "time:10", "percent": 1})
    assert near.status_code == 200
    assert listed(shelf.ada_app) == [work], "a place in the book takes nothing off"
    end = second.put(f"/api/app/1/editions/{edition}/progress",
                     json={"locator": f"time:{length}", "percent": 100, "age": 0})
    assert end.status_code == 200 and end.json()["kept"] is True
    assert [entry["edition_id"] for entry in shelf.ada_app.get("/api/app/1/history").json()] == [edition]
    assert listed(shelf.ada_app) == []


def test_a_place_at_the_end_that_is_reported_again_takes_no_book_off_that_went_back_on_the_list(shelf: Shelf) -> None:
    work, edition = shelf.works[ORCHARD], shelf.editions[f"{ORCHARD}/ebook"]
    place = {"locator": "epubcfi(/6/2)", "percent": 99, "age": 0}
    assert shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress", json=place).status_code == 200
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    assert shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress", json={**place, "percent": 100}).status_code == 200
    assert listed(shelf.ada_app) == [work]


def test_what_another_person_finishes_leaves_the_list_alone(shelf: Shelf) -> None:
    work, edition = shelf.works[ORCHARD], shelf.editions[f"{ORCHARD}/ebook"]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    assert shelf.ben_app.post(f"/api/app/1/editions/{edition}/finished", json={}).status_code == 201
    assert listed(shelf.ada_app) == [work]


def join_works(old: int, new: int) -> None:
    with SessionLocal() as db:
        db.execute(text("UPDATE editions SET work_id = :new WHERE work_id = :old"), {"new": new, "old": old})
        db.execute(delete(Work).where(Work.id == old))
        db.commit()


def test_an_entry_goes_with_the_book_when_two_works_are_joined(shelf: Shelf) -> None:
    old, new = shelf.works[ORCHARD], shelf.works[KITE]
    assert shelf.ada_app.put(f"/api/app/1/later/{old}").status_code == 204
    join_works(old, new)
    assert listed(shelf.ada_app) == [new]


def test_two_entries_of_joined_works_become_one(shelf: Shelf) -> None:
    old, new = shelf.works[ORCHARD], shelf.works[KITE]
    assert shelf.ada_app.put(f"/api/app/1/later/{old}").status_code == 204
    assert shelf.ada_app.put(f"/api/app/1/later/{new}").status_code == 204
    join_works(old, new)
    assert listed(shelf.ada_app) == [new]


def test_an_edition_that_leaves_a_work_with_others_takes_no_entry_along(shelf: Shelf) -> None:
    work, target, edition = shelf.works[ORCHARD], shelf.works[KITE], shelf.editions[f"{ORCHARD}/audio"]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    with SessionLocal() as db:
        db.execute(text("UPDATE editions SET work_id = :new WHERE id = :id"), {"new": target, "id": edition})
        db.commit()
    assert listed(shelf.ada_app) == [work]


def test_a_work_that_goes_for_good_takes_its_entry_along(shelf: Shelf) -> None:
    work = shelf.works[ORCHARD]
    assert shelf.ada_app.put(f"/api/app/1/later/{work}").status_code == 204
    with SessionLocal() as db:
        db.execute(delete(Work).where(Work.id == work))
        db.commit()
        assert db.get(LaterEntry, (shelf.ada.id, work)) is None


def test_an_account_that_goes_takes_its_list_along(shelf: Shelf) -> None:
    work = shelf.works[ORCHARD]
    assert shelf.ben_app.put(f"/api/app/1/later/{work}").status_code == 204
    with SessionLocal() as db:
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": shelf.ben.id})
        db.commit()
        assert db.scalars(select(LaterEntry)).all() == []
