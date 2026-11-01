"""Two names that are one person (a pen name, on evidence of a book, from the work on the audiobooks) meet the photos and
the short texts of the authors: what was learned about the name that goes is kept for the person only when it
was sure, a photo in the folder of the pen name counts after the one in the folder of the person's own name, the authors
are found by either name (by a name whose evidence the caller may see), and after a split each of the two has their own
photo or none, never the other's. The names, the books and the photos are invented."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, AliasClaim, Edition, Person, PersonAlias, PersonInfo, PersonPhoto
from app.services import aliases, photos, portraits

from .bookmaker import cover_image
from .conftest import new_client
from .library_helpers import age, run_jobs
from .reading_helpers import World

WHEN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


def person_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalars(select(Person.id).where(Person.name == name)).one()


def a_photo(color: tuple[int, int, int]) -> str:
    return photos.save(cover_image(color, (80, 80), "PNG"))


def known(name: str, *, state: str, photo: str = "", qid: str = "") -> None:
    """What a look-up found for ``name`` (for that name: ``name_key``)."""
    with SessionLocal() as db:
        person = db.get(Person, person_id(name))
        assert person is not None
        db.add(PersonInfo(person_id=person.id, wikidata_id=qid, wikidata_state=state, photo=photo,
                          photo_source="wikidata" if photo else "", photo_author="A. Photographer" if photo else "",
                          photo_license="CC BY 4.0" if photo else "", checked_at=WHEN, name_key=person.key))
        db.commit()


def prove(pen: str, real: str, title: str) -> None:
    """The tags of the e-book ``title`` say that ``pen`` is another name of ``real`` (evidence of that book)."""
    with SessionLocal() as db:
        edition = db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == "ebook")).one()
        db.add(AliasClaim(name_a=pen, key_a=aliases.key_of(pen), name_b=real, key_b=aliases.key_of(real),
                          source=aliases.TAGS, edition_id=edition, apart=False, created_at=clock.now()))
        db.flush()
        aliases.recompute(db)
        db.commit()


def one_person() -> tuple[int, int]:
    """``Cy Dorn`` is proved to be another name of ``Ada Feldkamp`` (who has more e-books, so she stays)."""
    ada, cy = person_id("Ada Feldkamp"), person_id("Cy Dorn")
    prove("Cy Dorn", "Ada Feldkamp", "Gamma")
    with SessionLocal() as db:
        assert db.get(Person, cy) is None and db.get(Person, ada) is not None
    return ada, cy


def split(client: TestClient, ada: int, name: str = "Cy Dorn") -> dict[str, object]:
    """Splits ``name`` off ``ada`` by hand, as the operator does on the author's page."""
    page = client.get(f"/api/v1/people/{ada}").json()
    alias = next(item for item in page["pen_names"] if item["name"] == name)
    answer = client.post(f"/api/v1/people/{ada}/split", json={"alias_id": alias["id"]})
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def info(person: int) -> PersonInfo | None:
    with SessionLocal() as db:
        row = db.get(PersonInfo, person)
        if row is not None:
            db.expunge(row)
        return row


def test_the_sure_photo_of_the_name_that_goes_is_kept_when_the_person_that_stays_has_none(
        world: World, client: TestClient) -> None:
    photo = a_photo((20, 60, 120))
    known("Cy Dorn", state="sure", photo=photo, qid="Q900001")
    known("Ada Feldkamp", state="none")
    ada, _cy = one_person()
    kept = info(ada)
    assert kept is not None and (kept.wikidata_id, kept.photo, kept.photo_license) == ("Q900001", photo, "CC BY 4.0")
    page = client.get(f"/api/v1/people/{ada}").json()
    assert page["aliases"] == ["Cy Dorn"]
    assert page["photo_url"] and page["credit"]["license"] == "CC BY 4.0"
    assert client.get(page["photo_url"]).status_code == 200


def test_the_sure_photo_of_the_person_that_stays_wins_and_nothing_is_mixed(world: World) -> None:
    own, other = a_photo((120, 20, 20)), a_photo((20, 120, 20))
    known("Ada Feldkamp", state="sure", photo=own, qid="Q900002")
    known("Cy Dorn", state="sure", photo=other, qid="Q900003")
    ada, cy = one_person()
    kept = info(ada)
    assert kept is not None and (kept.wikidata_id, kept.photo) == ("Q900002", own)
    assert info(cy) is None


def test_a_person_nothing_was_sure_of_is_looked_up_again_with_the_books_of_both_names(world: World) -> None:
    known("Ada Feldkamp", state="none")
    known("Cy Dorn", state="none")
    ada, cy = one_person()
    kept = info(ada)
    assert kept is not None and kept.checked_at is None and kept.photo == ""
    assert info(cy) is None


def put_photo(world: World, relative: str, color: tuple[int, int, int]) -> Path:
    path = world.family / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cover_image(color, (300, 400), "JPEG"))
    age(path)
    return path


def run_portraits(library_id: int) -> None:
    from app.models import Library

    with SessionLocal() as db:
        portraits.run(db, db.get(Library, library_id))  # type: ignore[arg-type]


def folder_photos() -> dict[int, str]:
    with SessionLocal() as db:
        return {row.person_id: row.path for row in db.scalars(select(PersonPhoto))}


def test_the_photo_in_the_folder_of_the_pen_name_goes_with_the_person_and_the_own_folder_wins(world: World) -> None:
    put_photo(world, "Cy Dorn/author.jpg", (90, 30, 30))
    run_portraits(world.family_id)
    assert folder_photos() == {person_id("Cy Dorn"): "Cy Dorn/author.jpg"}
    ada, _cy = one_person()
    assert folder_photos() == {ada: "Cy Dorn/author.jpg"}
    # The next look at the folders still finds it under the other name.
    run_portraits(world.family_id)
    assert folder_photos() == {ada: "Cy Dorn/author.jpg"}
    # A photo in the folder of her own name wins, also over a folder of another name that comes first in the alphabet.
    prove("Aaron Quill", "Ada Feldkamp", "Beta")
    put_photo(world, "Aaron Quill/author.jpg", (30, 30, 90))
    put_photo(world, "Ada Feldkamp/author.jpg", (30, 90, 30))
    run_portraits(world.family_id)
    assert folder_photos() == {ada: "Ada Feldkamp/author.jpg"}


def test_an_author_is_found_by_the_other_name_too(world: World, client: TestClient) -> None:
    ada, _cy = one_person()
    found = client.get("/api/v1/authors", params={"q": "cy dorn"}).json()
    assert [item["id"] for item in found["items"]] == [ada]
    assert client.get("/api/v1/authors", params={"q": "nobody here"}).json()["items"] == []


# ---- After a split, each their own (or nothing) ---------------------------------------------------------------------


def photo_of_page(client: TestClient, ident: int) -> str | None:
    return client.get(f"/api/v1/people/{ident}").json()["photo_url"]  # type: ignore[no-any-return]


def test_after_a_split_the_person_that_stays_keeps_no_photo_found_for_the_other_name(
        world: World, client: TestClient) -> None:
    photo = a_photo((20, 60, 120))
    known("Cy Dorn", state="sure", photo=photo, qid="Q900001")
    known("Ada Feldkamp", state="none")
    ada, _cy = one_person()
    assert photo_of_page(client, ada)
    after = split(client, ada)
    assert after["pen_names"] == [] and after["photo_url"] is None and after["credit"] is None
    assert info(ada) is None
    cy = person_id("Cy Dorn")
    assert cy != ada and info(cy) is None and photo_of_page(client, cy) is None


def test_after_a_split_the_own_photo_of_the_person_that_stays_stays(world: World, client: TestClient) -> None:
    own, other = a_photo((120, 20, 20)), a_photo((20, 120, 20))
    known("Ada Feldkamp", state="sure", photo=own, qid="Q900002")
    known("Cy Dorn", state="sure", photo=other, qid="Q900003")
    ada, _cy = one_person()
    split(client, ada)
    kept = info(ada)
    assert kept is not None and (kept.wikidata_id, kept.photo) == ("Q900002", own)
    cy = person_id("Cy Dorn")
    assert info(cy) is None and photo_of_page(client, cy) is None


def test_after_a_split_the_photo_in_the_folder_of_the_other_name_finds_its_own_person(
        world: World, client: TestClient) -> None:
    put_photo(world, "Cy Dorn/author.jpg", (90, 30, 30))
    run_portraits(world.family_id)
    ada, _cy = one_person()
    assert folder_photos() == {ada: "Cy Dorn/author.jpg"}
    split(client, ada)
    # At once nothing of the other name is left with her; the folders are read again by a job of their own.
    assert folder_photos() == {}
    assert photo_of_page(client, ada) is None
    run_jobs()
    cy = person_id("Cy Dorn")
    assert folder_photos() == {cy: "Cy Dorn/author.jpg"}
    assert photo_of_page(client, ada) is None and photo_of_page(client, cy)


def test_an_author_is_never_found_by_a_name_out_of_a_library_the_caller_cannot_see(
        world: World, client: TestClient) -> None:
    """The evidence that ``Zed Quarry`` is another name of ``Ada Feldkamp`` lies in a book of the club, which Rita is
    not a member of: she finds Ada by her own name and never by that one."""
    prove("Zed Quarry", "Ada Feldkamp", "Delta")
    ada = person_id("Ada Feldkamp")
    with SessionLocal() as db:
        assert db.scalars(select(PersonAlias.person_id).where(PersonAlias.name == "Zed Quarry")).all() == [ada]
    assert [item["id"] for item in client.get("/api/v1/authors", params={"q": "zed quarry"}).json()["items"]] == [ada]
    with new_client(world.rita) as rita:
        assert rita.get("/api/v1/authors", params={"q": "zed quarry"}).json()["items"] == []
        assert [item["id"] for item in rita.get("/api/v1/authors", params={"q": "feldkamp"}).json()["items"]] == [ada]
        page = rita.get(f"/api/v1/people/{ada}").json()
        assert page["aliases"] == [] and page["pen_names"] == []
