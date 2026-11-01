"""Ways that can be asked at the same moment (a double click, two devices, two starts of the program) and must be
answered all the same: no request ends in a server error, no thread of the server dies, and what is kept is kept once.

Every test here fires its requests behind a gate so that they really meet, and it fails when a thread died with an
exception (an exception that only ends a thread is never seen by a test that counts answers)."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.models import Bookmark, Progress, Setting
from app.services import settings_service

from .concurrent import at_once
from .conftest import new_client
from .reading_helpers import World, at, report

HOW_MANY = 8
#: A race is met by chance: a way that is easy to lose is asked this many times.
ROUNDS = 6


def codes(answers: list[Any]) -> list[int]:
    return [answer.status_code for answer in answers]


def browsers(account: Any, count: int = HOW_MANY) -> list[TestClient]:
    """Browsers of one person, each one started now (the start of the program is not what is under test here)."""
    return [new_client(account) for _ in range(count)]


def test_the_program_started_several_times_at_the_same_moment_on_an_empty_folder_starts_every_time() -> None:
    """Two processes (or a restart on top of a start) find the same settings missing and write them at the same moment:
    the second write meets the first."""
    for _ in range(ROUNDS):
        with SessionLocal() as db:
            db.execute(delete(Setting))
            db.commit()
        with ExitStack() as stack:
            clients = [new_client() for _ in range(HOW_MANY)]
            at_once([lambda client=client: stack.enter_context(client) for client in clients])
        with SessionLocal() as db:
            assert db.get(Setting, "indexer_langs_rev") is not None


def test_a_setting_saved_for_the_first_time_by_several_at_the_same_moment_is_saved(operator: Any) -> None:
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(operator)) for _ in range(HOW_MANY)]
        for _ in range(ROUNDS):
            with SessionLocal() as db:
                db.execute(delete(Setting).where(Setting.key == "organize_enabled"))
                db.commit()
            answers = at_once([lambda client=client, flag=bool(index % 2): client.put(
                "/api/v1/organize/settings", json={"enabled": flag}) for index, client in enumerate(clients)])
            assert codes(answers) == [200] * HOW_MANY
            with SessionLocal() as db:
                assert db.scalar(
                    select(func.count()).select_from(Setting).where(Setting.key == "organize_enabled")) == 1


def test_the_same_setting_saved_twice_inside_one_transaction_keeps_the_last_value() -> None:
    """The upsert and the object of the session agree: a key written before it was read is still there to change."""
    with SessionLocal() as db:
        db.execute(delete(Setting).where(Setting.key == "organize_enabled"))
        db.commit()
        settings_service.save(db, {"organize_enabled": True}, commit=False)
        settings_service.save(db, {"organize_enabled": False}, commit=False)
        assert settings_service.get(db, "organize_enabled") is False
        db.commit()
    with SessionLocal() as db:
        assert settings_service.get(db, "organize_enabled") is False


def test_a_place_in_a_book_reported_by_several_devices_at_the_same_moment_is_kept_once(operator: Any) -> None:
    world = World()
    edition = world.edition("Beta")
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(world.rita)) for _ in range(HOW_MANY)]
        answers = at_once([lambda client=client, index=index: client.put(
            f"/api/v1/editions/{edition}/progress", json=at(0.1 + index / 100, device_key=f"dev-{index}"))
            for index, client in enumerate(clients)])
    assert codes(answers) == [200] * HOW_MANY
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress)) == 1


def test_one_bookmark_set_several_times_at_the_same_moment_is_one_bookmark(operator: Any) -> None:
    world = World()
    edition = world.edition("Beta")
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(world.rita)) for _ in range(HOW_MANY)]
        answers = at_once([lambda client=client: client.post(
            f"/api/v1/editions/{edition}/bookmarks", json={"locator": report()["locator"], "fraction": 0.25})
            for client in clients])
    assert all(code < 500 for code in codes(answers)), codes(answers)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Bookmark)) == 1


def test_a_book_finished_by_several_devices_at_the_same_moment_is_entered_once(operator: Any) -> None:
    world = World()
    edition = world.edition("Beta")
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(world.rita)) for _ in range(HOW_MANY)]
        answers = at_once([lambda client=client: client.post(f"/api/v1/editions/{edition}/finished", json={})
                           for client in clients])
    assert sorted(codes(answers)) == [200] * (HOW_MANY - 1) + [201]


def test_several_codes_for_the_phone_asked_at_the_same_moment_are_answered_without_a_server_error(
        operator: Any) -> None:
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(operator)) for _ in range(HOW_MANY)]
        answers = at_once([lambda client=client: client.post(
            "/api/v1/devices/pairing", json={"address": "https://books.example.com"}) for client in clients])
    assert all(code in (201, 429) for code in codes(answers)), codes(answers)


def test_a_book_put_on_the_wish_list_and_taken_off_at_the_same_moment_never_says_the_list_is_full(
        shelves: Any) -> None:
    from .discover_shelves import token_of

    alice = shelves.client(shelves.alice)
    token = token_of(alice, "Orbit Null")
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(shelves.alice)) for _ in range(HOW_MANY)]
        for _ in range(ROUNDS):
            answers = at_once([lambda client=client, index=index: (
                client.put if index % 2 == 0 else client.delete)(f"/api/v1/wishlist/{token}")
                for index, client in enumerate(clients)])
            assert codes(answers) == [200] * HOW_MANY
