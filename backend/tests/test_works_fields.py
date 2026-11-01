"""What a book *says about itself* is only what the editions the person may see say.

A book can lie in two libraries (its e-book in one, its recording in the other). The member of only one of them must not
read, off the work, what the other library's copy says: its description, series and volume, genre, pages, year, original
title, authors, cover, colour, or when it came. Not in a list, not in the detail, not in the numbers per genre, not in
the order of the shelf, not in a series or on the page of a person."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, Edition, LibraryMember
from app.services import genres
from app.services.catalog import MINUTES_PER_PAGE
from app.services.readers.names import title_keys

from .bookmaker import cover_image
from .conftest import make_account, new_client
from .library_helpers import book, books_folder, make_library, recording, scan

SECRETS = ("Private description of the family copy", "Secret Saga", "The Hidden Tale", "Ada Feldkamp")


@contextmanager
def at(moment):  # type: ignore[no-untyped-def]
    """The server's clock stands at ``moment`` (the scan stamps what it makes with it)."""
    original = clock.now
    clock.now = lambda: moment  # type: ignore[assignment]
    try:
        yield
    finally:
        clock.now = original


class Shelves:
    """The family library holds the e-book of "Private Tale" and says everything about it; the club library holds a book
    of its own and, later, the recording of "Private Tale", which says almost nothing."""

    def __init__(self) -> None:
        base = clock.now() + timedelta(hours=1)
        self.family, self.club = books_folder("family"), books_folder("club")
        book(self.family, "private.epub", title="Private Tale", authors=("Ada Feldkamp",), language="en",
             series="Secret Saga", series_pos=3, subjects=("Fantasy",), date="2011-03-01T00:00:00+00:00",
             description="<p>Private description of the family copy</p>", original_title="The Hidden Tale",
             cover=cover_image((190, 30, 40)), cover_style="property", text_bytes=2200 * 409)
        self.family_id = make_library(self.family, "Family")
        self.club_id = make_library(self.club, "Club")
        self.rita, self.sam = make_account("rita"), make_account("sam")
        with SessionLocal() as db:
            tester = db.scalar(select(Account).where(Account.name == "tester"))
            assert tester is not None
            db.query(LibraryMember).delete()
            for user, library, right in ((tester.id, self.family_id, "manage"), (tester.id, self.club_id, "manage"),
                                         (self.rita.id, self.family_id, "read"), (self.sam.id, self.club_id, "read")):
                db.add(LibraryMember(library_id=library, user_id=user, right=right))
            db.commit()
        # First the family's book, then the club's own, last the recording: the work of "Private Tale" is older than
        # the recording, and the club's own book lies between.
        with at(base):
            scan(self.family_id)
        book(self.club, "club-book.epub", title="Club Book", authors=("Zed Alpha",), language="en")
        with at(base + timedelta(hours=1)):
            scan(self.club_id)
        recording(self.club, "private/private.m4b", 600.0, title="Private Tale", album="Private Tale",
                  artist="A. Feldkamp", narrator="Ben Hollister")
        with at(base + timedelta(hours=2)):
            scan(self.club_id)
        self.recording_added = base + timedelta(hours=2)


@pytest.fixture
def shelves(client: TestClient, operator: Account) -> Shelves:
    return Shelves()


def item(session: TestClient, title: str = "Private Tale", **params: str) -> dict:  # type: ignore[type-arg]
    answer = session.get("/api/v1/works", params={"per_page": 50, **params})
    assert answer.status_code == 200, answer.text
    return next(i for i in answer.json()["items"] if i["title"] == title)


def test_the_operator_sees_one_work_with_both_editions_and_all_it_says(client: TestClient, shelves: Shelves) -> None:
    full = item(client)
    assert sorted(e["kind"] for e in full["editions"]) == ["audio", "ebook"]
    assert full["series"]["name"] == "Secret Saga" and full["series"]["pos"] == 3.0 and full["genre"] == "fantasy"
    assert full["pages"] == 409 and full["year"] == 2011
    assert [a["name"] for a in full["authors"]] == ["Ada Feldkamp"]
    detail = client.get(f"/api/v1/works/{full['id']}").json()
    assert detail["description"] == "Private description of the family copy" and detail["original_title"] == (
        "The Hidden Tale")


def test_the_member_of_the_club_reads_nothing_off_the_work_that_only_the_family_copy_says(
        shelves: Shelves, client: TestClient) -> None:
    full = item(client)
    with new_client(shelves.sam) as sam:
        mine = item(sam)
        assert mine["id"] == full["id"]
        assert [e["kind"] for e in mine["editions"]] == ["audio"]
        assert mine["genre"] == "" and mine["series"] is None and mine["year"] is None
        # No page count of the book: the recording's length (600 s is ten minutes) gives the width of the spine.
        assert mine["pages"] == round(10 / MINUTES_PER_PAGE)
        assert [a["name"] for a in mine["authors"]] == ["A. Feldkamp"]
        assert mine["cover_url"] is None
        assert mine["color"] == genres.palette_color("", title_keys("Private Tale")[0])
        detail = sam.get(f"/api/v1/works/{mine['id']}")
        assert detail.status_code == 200
        assert detail.json()["description"] == "" and detail.json()["original_title"] == ""
        assert not any(secret in detail.text for secret in SECRETS), detail.text
        listing = sam.get("/api/v1/works", params={"per_page": 50})
        assert not any(secret in listing.text for secret in SECRETS)
        # The cover is the family's: for the club there is none.
        assert sam.get(f"/api/v1/works/{mine['id']}/cover").status_code == 404


def test_the_member_of_the_family_reads_what_the_family_copy_says(shelves: Shelves, client: TestClient) -> None:
    with new_client(shelves.rita) as rita:
        mine = item(rita)
        assert mine["series"]["name"] == "Secret Saga" and mine["genre"] == "fantasy" and mine["pages"] == 409
        assert [e["kind"] for e in mine["editions"]] == ["ebook"]
        detail = rita.get(f"/api/v1/works/{mine['id']}").json()
        assert detail["description"] == "Private description of the family copy"
        assert not any(word in rita.get("/api/v1/works", params={"per_page": 50}).text for word in ("Ben Hollister",
                                                                                                    "A. Feldkamp"))


def test_the_genre_filter_and_the_numbers_per_genre_ask_for_what_the_person_sees(
        shelves: Shelves, client: TestClient) -> None:
    def names(session: TestClient, **params: str) -> list[str]:
        return sorted(i["title"] for i in session.get("/api/v1/works", params=params).json()["items"])

    assert names(client, genre="fantasy") == ["Private Tale"]
    with new_client(shelves.sam) as sam, new_client(shelves.rita) as rita:
        # The club's copy has no genre, so for the club the book is one without: the filter cannot tell the club that the
        # family shelves it as fantasy.
        assert names(sam, genre="fantasy") == []
        assert names(sam, genre="none") == ["Club Book", "Private Tale"]
        assert sam.get("/api/v1/works/stats").json()["genres"] == {"none": 2}
        assert sam.get("/api/v1/works", params={"genre": "none"}).json()["total"] == 2
        assert names(rita, genre="fantasy") == ["Private Tale"] and names(rita, genre="none") == []
        assert rita.get("/api/v1/works/stats").json()["genres"] == {"fantasy": 1}


def test_the_order_of_the_shelf_and_of_the_new_ones_is_the_order_of_what_the_person_sees(
        shelves: Shelves, client: TestClient) -> None:
    def order(session: TestClient, sort: str) -> list[str]:
        return [i["title"] for i in session.get("/api/v1/works", params={"sort": sort}).json()["items"]]

    # Everybody: fantasy first, the book without a genre last.
    assert order(client, "shelf") == ["Private Tale", "Club Book"]
    with new_client(shelves.sam) as sam:
        # For the club neither book has a genre: by author, "Alpha, Zed" before "Feldkamp, A.". The family's genre must
        # not put "Private Tale" first.
        assert order(sam, "shelf") == ["Club Book", "Private Tale"]
        # "Added": the recording came after the club's own book, though the work is older than both.
        assert order(sam, "added") == ["Private Tale", "Club Book"]
        assert item(sam)["added_at"].startswith(shelves.recording_added.strftime("%Y-%m-%dT%H:%M"))
    # For the operator the work is as old as the family's e-book: the club's book is the newer one.
    assert order(client, "added") == ["Club Book", "Private Tale"]


def test_a_series_is_known_by_the_editions_the_person_sees(shelves: Shelves, client: TestClient) -> None:
    series_id = item(client)["series"]["id"]
    assert [w["title"] for w in client.get(f"/api/v1/series/{series_id}").json()["works"]] == ["Private Tale"]
    with new_client(shelves.rita) as rita, new_client(shelves.sam) as sam:
        assert [w["title"] for w in rita.get(f"/api/v1/series/{series_id}").json()["works"]] == ["Private Tale"]
        hidden, absent = sam.get(f"/api/v1/series/{series_id}"), sam.get("/api/v1/series/987654")
        assert (hidden.status_code, hidden.json()) == (404, absent.json())


def test_an_author_is_known_by_the_editions_the_person_sees(shelves: Shelves, client: TestClient) -> None:
    ada = item(client)["authors"][0]["id"]
    with new_client(shelves.rita) as rita, new_client(shelves.sam) as sam:
        assert [w["title"] for w in rita.get(f"/api/v1/people/{ada}").json()["wrote"]] == ["Private Tale"]
        # The club's recording names another person as its author: the family's author is not one the club knows.
        hidden, absent = sam.get(f"/api/v1/people/{ada}"), sam.get("/api/v1/people/987654")
        assert (hidden.status_code, hidden.json()) == (404, absent.json())
        theirs = item(sam)["authors"][0]["id"]
        assert [w["title"] for w in sam.get(f"/api/v1/people/{theirs}").json()["wrote"]] == ["Private Tale"]


def test_the_work_is_one_work_and_the_server_keeps_the_summary_of_both(shelves: Shelves, client: TestClient) -> None:
    """The columns of the work are the server's summary of every edition (the lead is the family's e-book): what a person
    is shown is worked out apart from them."""
    from app.models import Work

    with SessionLocal() as db:
        work = db.scalars(select(Work).where(Work.title == "Private Tale")).one()
        assert work.series_pos == 3.0 and work.genre == "fantasy" and work.description.startswith("Private")
        editions = db.scalars(select(Edition).where(Edition.work_id == work.id)).all()
        assert sorted(e.kind for e in editions) == ["audio", "ebook"]
