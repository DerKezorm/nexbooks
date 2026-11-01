"""The catalogue of an author over the API: built in the background and never while somebody waits, what a
person has worked out from the libraries they may see and from nothing else, the strict rule for titles, moving a work
to another kind, the authors in the search and on the page of a book. Every book and every answer is invented."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import OPERATOR, Account, AliasClaim, CatalogPerson, Edition, Job, Person, PersonAlias
from app.services import aliases
from app.services.libraries import member_library_ids
from app.services.oeuvre import store
from app.services.oeuvre import view as view_service
from app.services.oeuvre.view import same_title

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, join, make_library
from .oeuvre_fakes import Sources


class Shelf:
    """Two libraries: alice reads the first (two books of Edmund Halloran and one the sources do not know), bob the
    second (Halloran's "Ruß" and a book of Vera Brenner); carol has none."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sources = Sources(monkeypatch)
        first, second = books_folder("a1-first"), books_folder("a1-second")
        book(first, "rosen.epub", title="Ruß und Rosen", authors=("Edmund Halloran",), language="de")
        book(first, "ufer.epub", title="Das neunte Ufer", authors=("Edmund Halloran",), language="de",
             isbn="9783000000132")
        book(first, "leuchte.epub", title="Die Leuchte am Kai", authors=("Edmund Halloran",), language="de")
        book(second, "russ.epub", title="Ruß", authors=("Edmund Halloran",), language="de")
        book(second, "licht.epub", title="Kaltes Licht", authors=("Vera Brenner",), language="de")
        self.first = make_library(first, "First", scan=True)
        self.second = make_library(second, "Second", scan=True)
        self.alice = make_account("alice")
        self.bob = make_account("bob")
        self.carol = make_account("carol")
        with SessionLocal() as db:
            for account in db.scalars(select(Account).where(Account.name.in_(("alice", "bob", "carol")))):
                account.language = "de"
            db.commit()
        join(self.first, self.alice.id, "read")
        join(self.second, self.bob.id, "read")
        # (Reading the libraries matched their books with the stand-in; what the catalogue asks is counted from here.)
        self.sources.fake.log.clear()
        self.clients: list[TestClient] = []

    def client(self, account: Account) -> TestClient:
        found = new_client(account)
        self.clients.append(found)
        return found

    def person_id(self, name: str = "Edmund Halloran") -> int:
        with SessionLocal() as db:
            ident = db.scalar(select(Person.id).where(Person.name == name))
            assert ident is not None
            return ident


@pytest.fixture
def shelf(monkeypatch: pytest.MonkeyPatch) -> Iterator[Shelf]:
    found = Shelf(monkeypatch)
    yield found
    for client in found.clients:
        client.close()


def by_title(body: dict[str, Any], language: str = "de") -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for work in body["works"]:
        titles = {e["language"]: e["title"] for e in work["editions"]}
        out[titles.get(language) or titles.get("en") or next(iter(titles.values()))] = work
    return out


def built_page(shelf: Shelf, account: Account) -> dict[str, Any]:
    client = shelf.client(account)
    first = client.get(f"/api/v1/people/{shelf.person_id()}/catalog")
    assert first.status_code == 200, first.text
    shelf.sources.build()
    again = client.get(f"/api/v1/people/{shelf.person_id()}/catalog")
    assert again.status_code == 200
    return again.json()


def test_the_catalogue_is_built_in_the_background_and_not_while_the_person_waits(shelf: Shelf) -> None:
    client = shelf.client(shelf.alice)
    first = client.get(f"/api/v1/people/{shelf.person_id()}/catalog")
    assert first.status_code == 200
    body = first.json()
    # Nothing was asked yet: the answer says it is being built and shows the person's own books.
    assert shelf.sources.fake.log == []
    assert body["state"] == "building" and shelf.sources.waiting() == 1
    assert {w["key"][:4] for w in body["works"]} == {"own-"}
    assert shelf.sources.build() == 1
    ready = client.get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert ready["state"] == "ready" and ready["refreshing"] is False
    assert ready["orig_language"] == "en" and "Richard Vane" in ready["aliases"]
    assert ready["works_total"] >= 14
    # Looked at again the same day: no new build.
    asked = len(shelf.sources.fake.log)
    client.get(f"/api/v1/people/{shelf.person_id()}/catalog")
    assert shelf.sources.waiting() == 0 and len(shelf.sources.fake.log) == asked
    # A day later the page asks for a new build, and shows the old catalogue meanwhile.
    shelf.sources.days(1.1)
    later = client.get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert later["state"] == "ready" and later["refreshing"] is True and shelf.sources.waiting() == 1


def test_what_a_person_has_comes_from_their_own_libraries_only(shelf: Shelf) -> None:
    alice = by_title(built_page(shelf, shelf.alice))
    assert alice["Das neunte Ufer"]["work"] is not None and alice["Das neunte Ufer"]["have_ebook"] is True
    assert alice["Das neunte Ufer"]["have_audio"] is False
    # "Ruß" stands in bob's library: for alice it is a book she does not have.
    assert alice["Ruß"]["work"] is None and alice["Ruß"]["have_ebook"] is False
    bob = by_title(shelf.client(shelf.bob).get(f"/api/v1/people/{shelf.person_id()}/catalog").json())
    assert bob["Ruß"]["work"] is not None
    assert bob["Das neunte Ufer"]["work"] is None and bob["Ruß und Rosen"]["work"] is None


def test_a_short_title_is_never_a_part_of_a_longer_one(shelf: Shelf) -> None:
    alice = by_title(built_page(shelf, shelf.alice))
    assert alice["Ruß und Rosen"]["work"] is not None
    assert alice["Ruß"]["work"] is None
    assert not same_title("Es", "Wir sehen es kommen", "Ida Falkner")
    assert not same_title("Ruß", "Ruß und Rosen", "Edmund Halloran")
    assert same_title("Die Laternenstraße: Ein Krimi", "Die Laternenstraße", "Ida Falkner")


def test_a_book_of_the_person_the_sources_do_not_know_still_stands_on_the_page(shelf: Shelf) -> None:
    alice = by_title(built_page(shelf, shelf.alice))
    own = alice["Die Leuchte am Kai"]
    assert own["sources"] == ["library"] and own["token"] is None and own["work"] is not None
    page = shelf.client(shelf.alice).get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert page["on_shelf"] == 3


def test_the_page_of_an_author_one_sees_no_book_of_is_not_found_by_number(shelf: Shelf) -> None:
    built_page(shelf, shelf.alice)
    carol = shelf.client(shelf.carol)
    assert carol.get(f"/api/v1/people/{shelf.person_id()}/catalog").status_code == 404
    assert carol.get(f"/api/v1/people/{shelf.person_id('Vera Brenner')}/catalog").status_code == 404
    # By the token of the catalogue (from a search or the page of a book) the works are public, nothing is hers.
    with SessionLocal() as db:
        token = db.scalar(select(CatalogPerson.token).where(CatalogPerson.key == "edmund halloran"))
    page = carol.get(f"/api/v1/catalog/authors/{token}").json()
    assert page["person_id"] is None and page["on_shelf"] == 0
    assert all(w["work"] is None for w in page["works"])
    assert carol.get("/api/v1/catalog/authors/not-a-token").status_code == 404


def test_a_book_to_come_is_counted_with_its_day(shelf: Shelf) -> None:
    page = built_page(shelf, shelf.alice)
    keeper = by_title(page)["Der Hüter der verlorenen Zimmer"]
    editions = {e["language"]: e for e in keeper["editions"]}
    assert editions["de"]["upcoming"] and editions["de"]["released"] == "2027-03-03"
    assert editions["en"]["upcoming"] and page["soon"] == 1
    # A day after today beside an earlier year is a new edition of a book that is out, not a book to come.
    salt = {e["language"]: e for e in by_title(page)["Die Salztöchter"]["editions"]}
    assert not salt["de"]["upcoming"]


def test_moving_a_work_holds_for_everybody_and_can_be_taken_back(shelf: Shelf) -> None:
    page = built_page(shelf, shelf.alice)
    comic = by_title(page)["Wrenfield. Die Graphic Novel"]
    assert comic["kind"] == "unsure" and comic["why"] == "comic" and page["can_move"] is False
    url = f"/api/v1/catalog/authors/{page['token']}/works/{comic['key']}/kind"
    alice = shelf.client(shelf.alice)
    assert alice.put(url, json={"kind": "roman"}).status_code == 403
    # Who may fetch books may not move one: a move holds for everybody (only the operator and who manages a library).
    join(shelf.first, shelf.alice.id, "acquire")
    assert alice.put(url, json={"kind": "roman"}).status_code == 403
    assert alice.delete(url).status_code == 403
    assert shelf.client(shelf.alice).get(f"/api/v1/catalog/authors/{page['token']}").json()["can_move"] is False
    join(shelf.first, shelf.alice.id, "manage")
    assert alice.put(url, json={"kind": "unsure"}).status_code == 422
    moved = alice.put(url, json={"kind": "erz"})
    assert moved.status_code == 200 and moved.json()["can_move"] is True
    assert by_title(moved.json())["Wrenfield. Die Graphic Novel"]["kind"] == "erz"
    seen = shelf.client(shelf.carol).get(f"/api/v1/catalog/authors/{page['token']}").json()
    assert by_title(seen)["Wrenfield. Die Graphic Novel"]["moved"] is True
    # A new build keeps the move.
    shelf.sources.days(1.1)
    alice.get(f"/api/v1/people/{shelf.person_id()}/catalog")
    shelf.sources.build()
    rebuilt = alice.get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert by_title(rebuilt)["Wrenfield. Die Graphic Novel"]["kind"] == "erz"
    back = alice.delete(url)
    assert back.status_code == 200 and by_title(back.json())["Wrenfield. Die Graphic Novel"]["kind"] == "unsure"
    # A work the sources placed for sure is not moved, not even by the operator.
    soot = by_title(back.json())["Ruß"]
    assert soot["kind"] == "roman"
    sure = f"/api/v1/catalog/authors/{page['token']}/works/{soot['key']}/kind"
    refused = alice.put(sure, json={"kind": "sach"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_unsure"
    operator = shelf.client(make_account("olga", role=OPERATOR))
    assert operator.put(sure, json={"kind": "sach"}).status_code == 409
    assert operator.put(url, json={"kind": "erz"}).status_code == 200
    assert by_title(shelf.client(shelf.carol).get(f"/api/v1/catalog/authors/{page['token']}").json())["Ruß"][
        "kind"] == "roman"
    assert alice.put(f"/api/v1/catalog/authors/{page['token']}/works/nope/kind", json={"kind": "erz"}).status_code \
        == 404


def test_no_build_without_a_source(shelf: Shelf) -> None:
    shelf.sources.settings(sources_enabled=False)
    page = shelf.client(shelf.alice).get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert page["state"] == "main_off" and shelf.sources.waiting() == 0
    shelf.sources.settings(sources_enabled=True, **{f"source_{name}": False for name in store.SOURCES})
    page = shelf.client(shelf.alice).get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert page["state"] == "no_source" and shelf.sources.waiting() == 0


def test_the_search_names_the_authors_of_the_persons_libraries_only(shelf: Shelf) -> None:
    built_page(shelf, shelf.alice)
    alice = shelf.client(shelf.alice)
    hits = alice.get("/api/v1/search/people", params={"q": "hall"}).json()["items"]
    assert [h["name"] for h in hits] == ["Edmund Halloran"]
    assert hits[0]["person_id"] == shelf.person_id() and hits[0]["on_shelf"] == 3 and hits[0]["works"] >= 14
    assert alice.get("/api/v1/search/people", params={"q": "brenner"}).json()["items"] == []
    assert alice.get("/api/v1/search/people", params={"q": "h"}).json()["items"] == []


def catalog_people() -> int:
    with SessionLocal() as db:
        return len(list(db.scalars(select(CatalogPerson.id))))


def test_the_catalogue_search_names_authors_by_a_pen_name_and_makes_nothing_while_somebody_types(shelf: Shelf) -> None:
    alice = shelf.client(shelf.alice)
    found = alice.post("/api/v1/search/catalog", json={"q": "Richard Vane"})
    assert found.status_code == 200
    people = found.json()["people"]
    assert people[0]["name"] == "Edmund Halloran" and people[0]["alias"] == "Richard Vane"
    assert people[0]["person_id"] == shelf.person_id()
    # Carol sees no book of his: no person, and no catalogue made for the typing.
    carol = shelf.client(shelf.carol)
    hits = carol.post("/api/v1/search/catalog", json={"q": "Richard Vane"}).json()["people"]
    assert hits[0]["person_id"] is None and hits[0]["on_shelf"] == 0 and hits[0]["token"] is None
    assert catalog_people() == 0
    # Opening the hit makes the catalogue, once; its page is the catalogue's, by its token.
    opened = carol.post("/api/v1/catalog/authors", json={"name": hits[0]["name"]})
    assert opened.status_code == 200 and opened.json()["person_id"] is None and opened.json()["token"]
    again = carol.post("/api/v1/catalog/authors", json={"name": hits[0]["name"]}).json()
    assert again["token"] == opened.json()["token"] and catalog_people() == 1
    assert carol.get(f"/api/v1/catalog/authors/{again['token']}").status_code == 200
    assert carol.post("/api/v1/catalog/authors", json={"name": "x"}).status_code == 422
    # A catalogue that is there is named with its token from then on.
    hits = carol.post("/api/v1/search/catalog", json={"q": "Richard Vane"}).json()["people"]
    assert hits[0]["token"] == again["token"]


def test_a_pen_name_leads_to_the_real_name_only_by_books_one_may_see(shelf: Shelf) -> None:
    """The real name behind a pen name is learned from books: who sees none of them learns nothing of it."""
    with SessionLocal() as db:
        person = db.get(Person, shelf.person_id())
        assert person is not None
        db.add(PersonAlias(person_id=person.id, name="Rowan Fell", key="rowan fell"))
        db.commit()
        # No book proves it in a library carol or alice is in.
        assert aliases.person_for(db, "Rowan Fell") is not None
        assert aliases.person_for(db, "Rowan Fell", member_library_ids(shelf.carol.id)) is None
        assert aliases.person_for(db, "Rowan Fell", member_library_ids(shelf.alice.id)) is None
        row = store.for_name(db, "Rowan Fell", member_library_ids(shelf.carol.id))
        assert row is not None and row.person_id is None and row.key == "rowan fell"
        db.commit()
        people, _pens = view_service.people_of(db, row, member_library_ids(shelf.carol.id))
        assert people == []
        assert view_service.people_of(db, row)[0] == [person.id]
        # A book of alice's library proves it: for her the pen name leads to him.
        edition = db.scalar(select(Edition.id).where(Edition.library_id == shelf.first))
        db.add(AliasClaim(name_a="Rowan Fell", key_a="rowan fell", name_b=person.name, key_b=person.key,
                          source="tags", edition_id=edition))
        db.commit()
        found = aliases.person_for(db, "Rowan Fell", member_library_ids(shelf.alice.id))
        assert found is not None and found.id == person.id
        assert aliases.person_for(db, "Rowan Fell", member_library_ids(shelf.carol.id)) is None
        assert view_service.people_of(db, row, member_library_ids(shelf.alice.id))[0] == [person.id]


def test_the_page_of_a_book_of_the_sources_names_its_author(shelf: Shelf) -> None:
    page = built_page(shelf, shelf.alice)
    token = by_title(page)["Ruß"]["token"]
    body = shelf.client(shelf.alice).get(f"/api/v1/discover/books/{token}").json()
    about = body["about"][0]
    assert about["name"] == "Edmund Halloran" and about["person_id"] == shelf.person_id()
    assert about["works"] >= 14 and about["on_shelf"] == 3 and about["soon"] == 1


def test_a_work_on_the_wish_list_says_so(shelf: Shelf) -> None:
    page = built_page(shelf, shelf.alice)
    token = by_title(page)["Ruß"]["token"]
    alice = shelf.client(shelf.alice)
    assert alice.put(f"/api/v1/wishlist/{token}").status_code == 200
    again = alice.get(f"/api/v1/people/{shelf.person_id()}/catalog").json()
    assert by_title(again)["Ruß"]["wished"] is True
    assert by_title(shelf.client(shelf.bob).get(
        f"/api/v1/people/{shelf.person_id()}/catalog").json())["Ruß"]["wished"] is False


def test_the_covers_of_the_works_are_fetched_when_their_pictures_are_asked_for(shelf: Shelf) -> None:
    page = built_page(shelf, shelf.alice)
    named = [w for w in page["works"] if w["token"] and w["cover_url"] and w["cover_url"].startswith(
        f"/api/v1/discover/books/{w['token']}/cover")]
    assert named, "a work the sources have a cover for names the address of the server for it"
    with SessionLocal() as db:
        assert db.scalar(select(Job.id).where(Job.kind == "fcover")) is None, "looking at the page queues nothing"
    answer = shelf.client(shelf.alice).get(named[0]["cover_url"])
    assert answer.status_code in (200, 404)


def test_an_author_nobody_looked_at_is_forgotten(shelf: Shelf) -> None:
    shelf.client(shelf.carol).post("/api/v1/catalog/authors", json={"name": "Edmund Halloran"})
    with SessionLocal() as db:
        # Halloran is an author of the libraries: kept. A namesake a search made is not.
        store.for_name(db, "Ellen Halloran")
        db.commit()
    shelf.sources.days(31)
    with SessionLocal() as db:
        assert store.prune(db) == 1
        keys = set(db.scalars(select(CatalogPerson.key)))
    assert keys == {"edmund halloran"}
