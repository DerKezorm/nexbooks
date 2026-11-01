"""The reading stack over the API: a book is entered as finished by the rule at the end of the book and by hand,
once for each pass; the stack comes year by year with what each year comes to. The rule under test, everywhere: **the
history is the person's own**, and an edition of a library the person is not a member of is one that does not exist."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, HistoryEntry, Library, LibraryMember, Progress
from app.services import apikeys
from app.services import history as service

from .conftest import new_client
from .reading_helpers import Clock, World, at, heard


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


@pytest.fixture
def tick(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


def put(client: TestClient, edition: int, body: dict[str, object]):  # type: ignore[no-untyped-def]
    return client.put(f"/api/v1/editions/{edition}/progress", json=body)


def finish(client: TestClient, edition: int, day: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/editions/{edition}/finished", json={} if day is None else {"finished_on": day})


def entries(client: TestClient, work: int | None = None) -> list[dict[str, object]]:
    return client.get("/api/v1/history", params={} if work is None else {"work": work}).json()


# ---- The rule at the end of the book -----------------------------------------------------------------------------------


def test_the_end_of_an_ebook_is_entered_once_at_98_percent(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.97))
        assert entries(rita) == []
        put(rita, edition, at(0.98))
        found = entries(rita)
        assert len(found) == 1
        assert found[0]["kind"] == "read" and found[0]["source"] == "auto" and found[0]["edition_id"] == edition
        assert found[0]["finished_on"] == "2026-10-13"
        # The reader goes on reporting the last pages: still the one entry.
        tick.advance(seconds=30)
        put(rita, edition, at(0.99))
        put(rita, edition, at(1.0))
        assert len(entries(rita)) == 1


def test_wandering_about_the_end_is_not_a_new_pass(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.99))
        tick.advance(days=2)
        # A look back at a page near the end, and forward again: the same pass.
        put(rita, edition, at(0.85))
        tick.advance(seconds=60)
        put(rita, edition, at(0.99))
        assert len(entries(rita)) == 1


def test_reading_it_again_is_a_second_entry_with_its_own_year(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.99))
    # Over a year later (a session of that age has ended: the person signs in again).
    tick.advance(days=400)
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.05))
        put(rita, edition, at(0.5))
        tick.advance(days=3)
        put(rita, edition, at(0.99))
        found = entries(rita)
        assert [e["finished_on"] for e in found] == ["2027-11-20", "2026-10-13"]
        stack = rita.get("/api/v1/stack").json()
        assert [y["year"] for y in stack["years"]] == [2026, 2027]


def test_a_recording_is_finished_when_less_than_45_seconds_are_left(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha", "audio")
    with new_client(world.rita) as rita:
        put(rita, edition, heard(500))
        put(rita, edition, heard(554))
        assert entries(rita) == []
        put(rita, edition, heard(556))
        found = entries(rita)
        assert len(found) == 1 and found[0]["kind"] == "listened" and found[0]["edition_id"] == edition


def test_the_e_book_and_the_recording_of_a_book_are_two_entries(world: World, tick: Clock) -> None:
    ebook, audio = world.edition("Alpha"), world.edition("Alpha", "audio")
    with new_client(world.rita) as rita:
        put(rita, ebook, at(0.99))
        put(rita, audio, heard(600))
        found = entries(rita, world.work_of(ebook))
        assert sorted(e["kind"] for e in found) == ["listened", "read"]


def test_a_report_that_lost_enters_nothing(world: World, tick: Clock) -> None:
    """A device that was offline reports the end after the other device already stands further back: the older report is
    dropped and so is its entry."""
    edition = world.edition("Alpha")
    with new_client(world.rita) as a, new_client(world.rita) as b:
        put(a, edition, at(0.3, device_key="dev-a"))
        tick.advance(seconds=10)
        put(b, edition, at(0.4, device_key="dev-b"))
        late = put(a, edition, at(0.99, device_key="dev-a", age=500))
        assert late.json()["applied"] is False
        assert entries(a) == []


def test_two_devices_reporting_the_end_at_once_make_one_entry(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    start = threading.Barrier(8)

    def report(index: int) -> int:
        with new_client(world.rita) as client:
            start.wait()
            return put(client, edition, at(0.99, device_key=f"dev-{index}")).status_code

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(report, range(8))) == {200}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 1


# ---- By hand -----------------------------------------------------------------------------------------------------------


def test_finished_by_hand_is_entered_for_today_and_a_second_press_is_the_same_entry(world: World, tick: Clock) -> None:
    edition = world.edition("Beta")
    with new_client(world.rita) as rita:
        first = finish(rita, edition)
        assert first.status_code == 201 and first.json()["source"] == "hand" and first.json()["finished_on"] == "2026-10-13"
        again = finish(rita, edition)
        assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
        assert len(entries(rita)) == 1


def test_a_double_click_from_a_crowd_of_requests_is_one_entry(world: World, tick: Clock) -> None:
    edition = world.edition("Beta")
    start = threading.Barrier(10)

    def press(_: int) -> int:
        with new_client(world.rita) as client:
            start.wait()
            return finish(client, edition).status_code

    with ThreadPoolExecutor(10) as pool:
        codes = list(pool.map(press, range(10)))
    assert codes.count(201) == 1 and codes.count(200) == 9
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 1


def test_a_book_finished_long_ago_may_be_entered_with_its_day(world: World, tick: Clock) -> None:
    edition = world.edition("Beta")
    with new_client(world.rita) as rita:
        made = finish(rita, edition, "2022-07-09")
        assert made.status_code == 201 and made.json()["finished_on"] == "2022-07-09"
        # Another day is another pass.
        assert finish(rita, edition, "2024-01-02").status_code == 201
        assert [e["finished_on"] for e in entries(rita)] == ["2024-01-02", "2022-07-09"]


@pytest.mark.parametrize("day", ["2026-10-15", "2999-01-01", "1850-01-01", "2026-13-01", "2026-02-30", "yesterday",
                                 "2026-1-1", "20261013"])
def test_a_day_that_is_none_is_refused(world: World, tick: Clock, day: str) -> None:
    edition = world.edition("Beta")
    with new_client(world.rita) as rita:
        assert finish(rita, edition, day).status_code == 422
        assert entries(rita) == []


def test_the_day_is_the_day_of_the_person_s_time_zone(world: World, tick: Clock) -> None:
    """Twenty past eleven at night in Greenwich is the next morning in Auckland."""
    tick.now = datetime(2026, 10, 13, 23, 20, tzinfo=UTC)
    edition = world.edition("Beta")
    with new_client(world.rita) as rita:
        assert rita.put("/api/v1/me/preferences", json={"timezone": "Pacific/Auckland"}).status_code == 200
        assert finish(rita, edition).json()["finished_on"] == "2026-10-14"
        assert rita.get("/api/v1/stack").json()["today"] == "2026-10-14"
        # The day a device names may be a day ahead of the server's UTC date, no more.
        assert finish(rita, edition, "2026-10-14").status_code == 200
        assert finish(rita, edition, "2026-10-16").status_code == 422


def test_finished_by_hand_stops_the_rule_from_entering_the_same_pass_again(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.5))
        finish(rita, edition)
        tick.advance(days=1)
        put(rita, edition, at(0.99))
        assert len(entries(rita)) == 1


def test_a_book_never_begun_can_be_finished_by_hand(world: World, tick: Clock) -> None:
    edition = world.edition("Beta")
    with new_client(world.rita) as rita:
        assert finish(rita, edition).status_code == 201
        assert rita.get(f"/api/v1/editions/{edition}/progress").json() is None


def test_not_finished_takes_the_entry_away_and_the_rule_does_not_bring_it_back(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.99))
        entry = entries(rita)[0]["id"]
        assert rita.delete(f"/api/v1/history/{entry}").status_code == 204
        assert entries(rita) == []
        tick.advance(days=1)
        # The person is still at the end of the same pass: the rule leaves it be.
        put(rita, edition, at(1.0))
        assert entries(rita) == []
        # Finishing it by hand is the person's word.
        assert finish(rita, edition).status_code == 201
        assert rita.delete("/api/v1/history/999999").status_code == 404


# ---- Whose it is -------------------------------------------------------------------------------------------------------


def test_the_history_is_the_person_s_own_and_the_operator_sees_none_of_it(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        finish(rita, edition)
        entry = entries(rita)[0]["id"]
    with new_client(world.tester()) as operator:
        assert operator.get(f"/api/v1/editions/{edition}").status_code == 200
        assert entries(operator) == []
        assert operator.get("/api/v1/stack").json()["years"] == []
        # Somebody else's entry is one that does not exist, for taking away too.
        assert operator.delete(f"/api/v1/history/{entry}").status_code == 404
        assert finish(operator, edition).status_code == 201
    with new_client(world.rita) as rita:
        assert len(entries(rita)) == 1 and rita.get("/api/v1/stack").json()["years"][0]["books"] == 1


def test_an_edition_one_cannot_see_cannot_be_finished_and_says_nothing(world: World, tick: Clock) -> None:
    club = world.edition("Delta")
    with new_client(world.rita) as rita, new_client(world.tom) as tom:
        for client in (rita, tom):
            assert finish(client, club).status_code == 404
            assert put(client, club, at(0.99)).status_code == 404
        assert finish(rita, club).json() == finish(tom, club).json()
        assert finish(rita, 999999).json() == finish(rita, club).json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 0


def test_a_book_of_a_library_one_left_is_hidden_and_comes_back_with_the_membership(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        finish(rita, edition)
        with SessionLocal() as db:
            db.query(LibraryMember).filter_by(user_id=world.rita.id).delete()
            db.commit()
        assert entries(rita) == []
        assert rita.get("/api/v1/stack").json()["years"] == []
        with SessionLocal() as db:
            db.add(LibraryMember(library_id=world.family_id, user_id=world.rita.id, right="read"))
            db.commit()
        assert len(entries(rita)) == 1
        assert rita.get("/api/v1/stack").json()["years"][0]["books"] == 1


def test_a_key_reads_the_history_and_cannot_change_it(world: World, client: TestClient) -> None:
    edition = world.edition("Alpha")
    assert finish(client, edition).status_code == 201
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    apikeys.forget()
    head = {"Authorization": f"Bearer {key}"}
    with new_client() as program:
        assert len(program.get("/api/v1/history", headers=head).json()) == 1
        assert len(program.get("/api/v1/stack", headers=head).json()["years"]) == 1
        for answer in (program.post(f"/api/v1/editions/{edition}/finished", json={}, headers=head),
                       program.delete("/api/v1/history/1", headers=head)):
            assert answer.status_code in (401, 403), answer.text


def test_a_change_names_its_tab(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        rita.headers.pop("X-Nexbooks-Client")
        assert finish(rita, edition).status_code == 400
        assert rita.delete("/api/v1/history/1").status_code == 400


def test_a_device_token_finishes_a_book_like_a_browser_does(world: World, client: TestClient) -> None:
    edition = world.edition("Alpha")
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    with new_client() as phone:
        made = phone.post(f"/api/v1/editions/{edition}/finished", json={}, headers={"Authorization": f"Bearer {token}"})
        assert made.status_code == 201


# ---- The stack and the year ----------------------------------------------------------------------------------------------


def test_the_stack_comes_year_by_year_with_what_each_year_comes_to(world: World, tick: Clock) -> None:
    alpha, audio, beta, gamma = (world.edition("Alpha"), world.edition("Alpha", "audio"), world.edition("Beta"),
                                 world.edition("Gamma"))
    with new_client(world.rita) as rita:
        finish(rita, gamma, "2024-03-01")
        finish(rita, alpha, "2025-02-10")
        finish(rita, audio, "2025-05-01")
        finish(rita, beta, "2025-11-30")
        stack = rita.get("/api/v1/stack").json()
        assert [y["year"] for y in stack["years"]] == [2024, 2025]
        old, last = stack["years"]
        assert (old["books"], old["read"], old["listened"]) == (1, 1, 0)
        assert (last["books"], last["read"], last["listened"]) == (3, 2, 1)
        # In the order they were finished: the first lies at the bottom of the tower.
        assert [e["work"]["title"] for e in last["entries"]] == ["Alpha", "Alpha", "Beta"]
        assert [e["kind"] for e in last["entries"]] == ["read", "listened", "read"]
        # Pages are those of the books that were read, the time that of the recording.
        pages = {e["work"]["title"]: e["pages"] for e in last["entries"] if e["kind"] == "read"}
        assert last["pages"] == sum(pages.values()) and last["pages"] > 0
        assert last["seconds"] == 600 and [e["seconds"] for e in last["entries"]] == [0, 600, 0]
        # The thickest is the book with the most pages; the genre is the one most of the year's books are (none said).
        assert last["thickest"]["work"]["title"] == "Beta" and last["thickest"]["pages"] == pages["Beta"]
        assert last["genre"] == "" or isinstance(last["genre"], str)
        # The entries carry the book as the shelf shows it.
        assert last["entries"][0]["work"]["color"].startswith("#") and last["entries"][0]["work"]["authors"][0]["name"]


def test_a_year_without_entries_does_not_exist_and_an_empty_stack_is_empty(world: World) -> None:
    with new_client(world.rita) as rita:
        assert rita.get("/api/v1/stack").json()["years"] == []


def test_the_genre_of_a_year_is_the_one_most_books_have(world: World, tick: Clock) -> None:
    from app.models import Edition

    with SessionLocal() as db:
        for title, genre in (("Alpha", "krimi"), ("Beta", "krimi"), ("Gamma", "fantasy")):
            for edition in db.scalars(select(Edition).where(Edition.title == title)):
                edition.genre = genre
        db.commit()
    with new_client(world.rita) as rita:
        for title in ("Alpha", "Beta", "Gamma"):
            finish(rita, world.edition(title), "2025-06-01")
        year = rita.get("/api/v1/stack").json()["years"][0]
        assert year["genre"] == "krimi"


def test_the_list_of_entries_is_paged_and_filtered_by_book(world: World, tick: Clock) -> None:
    alpha, beta = world.edition("Alpha"), world.edition("Beta")
    with new_client(world.rita) as rita:
        for day in ("2023-01-01", "2024-01-01", "2025-01-01"):
            finish(rita, alpha, day)
        finish(rita, beta, "2025-06-01")
        everything = rita.get("/api/v1/history")
        assert len(everything.json()) == 4 and everything.headers["x-total-count"] == "4"
        assert everything.json()[0]["finished_on"] == "2025-06-01"
        first = rita.get("/api/v1/history", params={"limit": 2})
        assert len(first.json()) == 2 and first.headers["x-total-count"] == "4"
        assert len(rita.get("/api/v1/history", params={"limit": 2, "offset": 3}).json()) == 1
        mine = rita.get("/api/v1/history", params={"work": world.work_of(alpha)})
        assert len(mine.json()) == 3 and mine.headers["x-total-count"] == "3"
        assert rita.get("/api/v1/history", params={"work": 999999}).json() == []
        assert rita.get("/api/v1/history", params={"limit": 0}).status_code == 422
        assert rita.get("/api/v1/history", params={"limit": service.LIST_LIMIT + 1}).status_code == 422


def test_there_is_a_limit_to_the_stack_and_it_says_so(world: World, tick: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "MAX_ENTRIES", 2)
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        assert finish(rita, edition, "2024-01-01").status_code == 201
        assert finish(rita, edition, "2025-01-01").status_code == 201
        refused = finish(rita, edition, "2026-01-01")
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "history_limit"
        # The rule at the end of the book is told no too, but the place is kept.
        assert put(rita, edition, at(0.99)).status_code == 200
        assert len(entries(rita)) == 2


# ---- What goes with what -------------------------------------------------------------------------------------------------


def test_removing_a_library_takes_the_history_of_its_books_with_it(world: World, client: TestClient, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        finish(rita, edition)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 1
    assert client.delete(f"/api/v1/libraries/{world.family_id}").status_code == 204
    with SessionLocal() as db:
        assert db.get(Library, world.family_id) is None
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 0


def test_deleting_an_account_takes_its_history(world: World, tick: Clock) -> None:
    with new_client(world.rita) as rita:
        finish(rita, world.edition("Alpha"))
    with SessionLocal() as db:
        db.delete(db.get(Account, world.rita.id))
        db.commit()
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 0


def test_a_file_that_is_gone_hides_the_entry_but_does_not_lose_it(world: World, tick: Clock) -> None:
    from app.models import Edition

    edition = world.edition("Beta")
    with new_client(world.rita) as rita:
        finish(rita, edition)
        with SessionLocal() as db:
            db.get(Edition, edition).missing_at = datetime(2026, 10, 1, tzinfo=UTC)  # type: ignore[union-attr]
            db.commit()
        assert entries(rita) == [] and rita.get("/api/v1/stack").json()["years"] == []
        with SessionLocal() as db:
            db.get(Edition, edition).missing_at = None  # type: ignore[union-attr]
            db.commit()
        assert len(entries(rita)) == 1


def test_a_pass_is_let_go_below_half_the_book(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.99))
        with SessionLocal() as db:
            assert db.get(Progress, (world.rita.id, edition)).pass_done is True  # type: ignore[union-attr]
        put(rita, edition, at(0.6))
        with SessionLocal() as db:
            assert db.get(Progress, (world.rita.id, edition)).pass_done is True  # type: ignore[union-attr]
        put(rita, edition, at(0.4))
        with SessionLocal() as db:
            assert db.get(Progress, (world.rita.id, edition)).pass_done is False  # type: ignore[union-attr]


def test_a_book_finished_on_a_day_gone_by_does_not_take_the_entry_of_the_pass_in_progress(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition, at(0.6))
        assert finish(rita, edition, "2020-05-05").status_code == 201
        with SessionLocal() as db:
            assert db.get(Progress, (world.rita.id, edition)).pass_done is False  # type: ignore[union-attr]
        put(rita, edition, at(0.99))
        sources = sorted(str(e["source"]) for e in entries(rita))
        assert sources == ["auto", "hand"]
        # A book finished today does claim the pass: the end of the same pass is not a second entry.
        other = world.edition("Beta")
        put(rita, other, at(0.6))
        assert finish(rita, other).status_code == 201
        put(rita, other, at(0.99))
        assert sorted(str(e["source"]) for e in entries(rita, world.work_of(other))) == ["hand"]
