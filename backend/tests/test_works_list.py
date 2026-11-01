"""The list view of the library over the API: ``GET /works?extras=list`` adds, to each book, the place and the end
the caller holds in it and, for a member who manages a library the book lies in, the matching and the library. The rule
under test: **the extras come from what the caller may see** - the reading is theirs alone, and the matching and the
libraries are shown to a member with ``manage`` for the libraries they manage and to nobody else."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Edition, LibraryMember, MatchEntry

from .conftest import new_client
from .library_helpers import book, make_library, run_jobs
from .reading_helpers import World, at, heard


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


def settle(edition: Edition, state: str, decision: str = "") -> None:
    """What the matching made of an edition (the scan made the entry; the state is put in its place)."""
    with SessionLocal() as db:
        entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == edition.id))
        if entry is None:
            entry = MatchEntry(library_id=edition.library_id, edition_id=edition.id)
            db.add(entry)
        entry.state, entry.decision = state, decision
        db.commit()


def rows(client: TestClient, **params: object) -> dict[str, dict[str, object]]:
    answer = client.get("/api/v1/works", params={"extras": "list", "per_page": 200, **params})
    assert answer.status_code == 200, answer.text
    return {item["title"]: item for item in answer.json()["items"]}


def test_a_list_without_extras_is_the_list_it_was(world: World) -> None:
    with new_client(world.rita) as rita:
        items = rita.get("/api/v1/works").json()["items"]
        assert items and all(item["mine"] is None for item in items)


def test_the_extras_say_where_the_caller_is_in_the_e_book_and_in_the_recording(world: World) -> None:
    ebook, audio = world.edition("Alpha"), world.edition("Alpha", "audio")
    with new_client(world.rita) as rita:
        before = rows(rita)["Alpha"]["mine"]
        assert before["ebook"] is None and before["audio"] is None and before["moved_at"] is None
        rita.put(f"/api/v1/editions/{ebook}/progress", json=at(0.4))
        rita.put(f"/api/v1/editions/{audio}/progress", json=heard(150))
        mine = rows(rita)["Alpha"]["mine"]
        assert mine["ebook"] == pytest.approx(0.4) and mine["audio"] == pytest.approx(0.25)
        assert not mine["ebook_done"] and not mine["audio_done"] and mine["moved_at"]
        assert rows(rita)["Beta"]["mine"]["ebook"] is None


def test_the_end_of_a_book_shows_for_each_kind_alone(world: World) -> None:
    audio = world.edition("Alpha", "audio")
    with new_client(world.rita) as rita:
        rita.post(f"/api/v1/editions/{audio}/finished", json={})
        mine = rows(rita)["Alpha"]["mine"]
        assert mine["audio_done"] is True and mine["ebook_done"] is False


def test_the_reading_is_the_caller_s_own(world: World) -> None:
    ebook = world.edition("Alpha")
    with new_client(world.rita) as rita:
        rita.put(f"/api/v1/editions/{ebook}/progress", json=at(0.9))
        rita.post(f"/api/v1/editions/{ebook}/finished", json={})
    with new_client(world.tester()) as operator:
        mine = rows(operator)["Alpha"]["mine"]
        assert mine["ebook"] is None and mine["ebook_done"] is False and mine["moved_at"] is None


def test_a_member_who_only_reads_gets_neither_the_matching_nor_the_libraries(world: World) -> None:
    with SessionLocal() as db:
        every = list(db.scalars(select(Edition)))
    for edition in every:
        settle(edition, "open")
    with new_client(world.rita) as rita:
        for item in rows(rita).values():
            assert item["mine"]["match"] is None and item["mine"]["libraries"] == []


def test_a_member_who_manages_sees_the_matching_and_the_library_and_the_worst_of_a_works_editions(world: World) -> None:
    with SessionLocal() as db:
        family = list(db.scalars(select(Edition).where(Edition.library_id == world.family_id)))
    for edition in family:
        settle(edition, "open" if edition.kind == "audio" else "sure")
    with SessionLocal() as db:
        delta = db.scalars(select(Edition).where(Edition.title == "Delta")).one()
        db.query(MatchEntry).filter_by(edition_id=delta.id).delete()
        db.commit()
    with new_client(world.tester()) as operator:
        found = rows(operator)
        assert found["Alpha"]["mine"]["match"] == "open"
        assert found["Alpha"]["mine"]["libraries"] == ["Family"]
        assert found["Beta"]["mine"]["match"] == "sure"
        # The club's book has no entry yet: it waits.
        assert found["Delta"]["mine"]["match"] == "pending" and found["Delta"]["mine"]["libraries"] == ["Club"]


def test_a_decision_of_a_person_shows_as_decided(world: World) -> None:
    with SessionLocal() as db:
        edition = db.scalars(select(Edition).where(Edition.title == "Delta")).one()
    settle(edition, "maybe", "accepted")
    with new_client(world.tester()) as operator:
        assert rows(operator)["Delta"]["mine"]["match"] == "decided"


def test_the_libraries_a_manager_does_not_manage_add_nothing(world: World) -> None:
    """A book in two libraries: a member who manages only one of them is told about that one only."""
    book(world.club, "Ada Feldkamp/Beta/beta.epub", title="Beta", authors=("Ada Feldkamp",), language="en",
         series="Harbour Tales", series_pos=2, text_bytes=70_000, date="2014-03-01")
    run_jobs()
    from app.services import libraries

    with SessionLocal() as db:
        libraries.start_scan(db, world.club_id)
    run_jobs()
    with SessionLocal() as db:
        # sam manages the club, reads the family.
        db.query(LibraryMember).filter_by(user_id=world.sam.id).delete()
        db.add(LibraryMember(library_id=world.club_id, user_id=world.sam.id, right="manage"))
        db.add(LibraryMember(library_id=world.family_id, user_id=world.sam.id, right="read"))
        db.commit()
    with new_client(world.sam) as sam:
        beta = rows(sam)["Beta"]
        assert beta["mine"]["libraries"] == ["Club"]
        assert len(beta["editions"]) == 2
    with new_client(world.rita) as rita:
        assert rows(rita)["Beta"]["mine"]["libraries"] == []


def test_the_extras_do_not_widen_what_a_book_shows(world: World) -> None:
    """The list with extras shows the same books, in the same order and with the same fields, as the list without."""
    with new_client(world.rita) as rita:
        plain = rita.get("/api/v1/works", params={"per_page": 200}).json()["items"]
        more = rita.get("/api/v1/works", params={"per_page": 200, "extras": "list"}).json()["items"]
        assert [i["id"] for i in plain] == [i["id"] for i in more]
        for left, right in zip(plain, more, strict=True):
            assert {k: v for k, v in right.items() if k != "mine"} == {k: v for k, v in left.items() if k != "mine"}


def test_the_extras_are_checked(world: World) -> None:
    with new_client(world.rita) as rita:
        assert rita.get("/api/v1/works", params={"extras": "everything"}).status_code == 422


def test_a_library_made_later_is_listed_too(world: World, tmp_path: object) -> None:
    from .library_helpers import books_folder

    folder = books_folder("later")
    book(folder, "Later/later.epub", title="Later", authors=("Ola Brandt",), language="en")
    library = make_library(folder, "Later", scan=True)
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library, user_id=world.rita.id, right="manage"))
        db.commit()
    with new_client(world.rita) as rita:
        assert rows(rita)["Later"]["mine"]["libraries"] == ["Later"]
