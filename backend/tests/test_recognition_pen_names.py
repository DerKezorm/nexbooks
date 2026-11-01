"""Pen names: two names that are one person only on evidence, only for a name the files give as an author, and the way
back. The evidence belongs to a book and goes with it (a match taken back, a book read again); a person can split a name
off for good; and an older version's joins without evidence are undone by themselves. All names, titles, ISBNs and
ASINs are invented."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, AliasClaim, Credit, Edition, Person, PersonAlias, Work
from app.services import aliases
from app.services.match import apply
from app.services.match import run as matching
from app.services.match.score import Wanted, author_state, score
from app.services.sources import cache as source_cache
from app.services.sources import hardcover
from app.services.sources.model import Candidate

from .conftest import make_account, new_client, sign_in
from .fake_sources import audible_product, hc_doc
from .library_helpers import book, books_folder, make_library, recording, run_jobs, scan
from .match_helpers import World, member
from .test_scanner import state

PEN = "Nele Beispielname"
REAL = "Mira Klarstein"


def people() -> dict[str, list[str]]:
    """The authors, each with the other names that lead to them."""
    with SessionLocal() as db:
        authors = select(Credit.person_id).where(Credit.role == "author")
        return {person.name: sorted(alias.name for alias in db.scalars(
            select(PersonAlias).where(PersonAlias.person_id == person.id)))
            for person in db.scalars(select(Person).where(Person.id.in_(authors)))}


def work_authors() -> list[tuple[str, list[str]]]:
    return sorted((work["title"], sorted(work["authors"])) for work in state()["works"])


def hardcover_library(world: World, name: str = "pen-hc", *, alternates: Any = None, author_tag: bool = True) -> int:
    """An e-book under the real name, and a recording whose tags name the pen name; Hardcover lists the book under the
    real name and knows the pen name as another name of it."""
    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7, "Nachtglas", [REAL], users=900, year=2021)]
    world.fake.hc_alternates = {REAL: [PEN, "M. Klarstein-Weiss"]} if alternates is None else alternates
    folder = books_folder(name)
    book(folder, "text/nachtglas.epub", title="Nachtglas", authors=(REAL,), language="de")
    tags: dict[str, Any] = {"album": "Nachtglas", "artist": PEN, "track": 1}
    if author_tag:
        tags["author"] = PEN
    recording(folder, "Hoerbuch/Nachtglas (2021)/Audio/01.mp3", seconds=290.0, **tags)
    library_id = make_library(folder)
    member(library_id)
    scan(library_id)
    return library_id


# ---- Evidence, and what is none --------------------------------------------------------------------------------------


def test_hardcover_naming_the_author_tag_as_another_name_of_the_author_proves_it(
        client: TestClient, operator: Account, world: World) -> None:
    hardcover_library(world)
    assert people() == {REAL: [PEN]}
    (work,) = state()["works"]
    assert sorted(edition["kind"] for edition in work["editions"]) == ["audio", "ebook"]
    assert world.fake.count("api.hardcover.app") > 0


def test_the_artist_tag_is_no_author_field_and_proves_nothing(client: TestClient, operator: Account,
                                                              world: World) -> None:
    hardcover_library(world, "pen-hc-artist", author_tag=False)
    assert sorted(people()) == sorted([REAL, PEN])
    assert len(state()["works"]) == 2


def test_two_authors_of_one_name_at_hardcover_prove_nothing_and_are_never_put_together(
        client: TestClient, operator: Account, world: World) -> None:
    hardcover_library(world, "pen-hc-namesakes", alternates={REAL: [[PEN], ["Someone Else Entirely"]]})
    assert sorted(people()) == sorted([REAL, PEN])
    with SessionLocal() as db:
        assert hardcover.author_alternates(db, REAL) == []


def studio_recording(folder: Any, title: str, asin: str, artist: str) -> None:
    recording(folder, f"Hoerspiele/{title}/01.mp3", seconds=180.0, album=title, artist=artist, asin=asin, track=1)


def test_a_studio_or_a_narrator_in_the_artist_tag_never_becomes_a_name_of_an_author(
        client: TestClient, operator: Account, world: World) -> None:
    """The cases of the review: a studio in the artist tag of two recordings by two authors, matched by their ASIN; a
    narrator in the artist tag. None of them is evidence, and the two authors stay two people."""
    world.only("audible")
    world.fake.audible = {"de": [audible_product("B0STUDIO01", "Das Nebelhaus", ["Kai Ostermann"], 3),
                                 audible_product("B0STUDIO02", "Der Turm", ["Lara Winter"], 3),
                                 audible_product("B0NARR0001", "Die Klippe", ["Kai Ostermann"], 3,
                                                 narrators=("Vollbesetzung",))]}
    folder = books_folder("pen-studio")
    book(folder, "Ostermann/Anderes Buch/x.epub", title="Anderes Buch", authors=("Kai Ostermann",), language="de")
    book(folder, "Winter/Noch Eins/x.epub", title="Noch Eins", authors=("Lara Winter",), language="de")
    studio_recording(folder, "Das Nebelhaus", "B0STUDIO01", "Beispiel Hoerspiele Studios")
    studio_recording(folder, "Der Turm", "B0STUDIO02", "Beispiel Hoerspiele Studios")
    studio_recording(folder, "Die Klippe", "B0NARR0001", "Ben Hollister")
    library_id = make_library(folder)
    member(library_id)
    scan(library_id)
    found = people()
    assert found.get("Kai Ostermann") == [] and found.get("Lara Winter") == []
    with SessionLocal() as db:
        assert db.scalars(select(PersonAlias.id)).all() == []
        assert db.scalars(select(AliasClaim.id)).all() == []


def two_books_with_isbns(world: World, name: str, count: int) -> int:
    """``count`` e-books under the pen name with ISBNs that the National Library knows under the real name."""
    world.only("dnb")
    records = []
    folder = books_folder(name)
    for number in range(1, count + 1):
        isbn = {1: "9783000000010", 2: "9783000000027", 3: "9783000000034"}[number]
        title = f"Nachtglas {['', 'Eins', 'Zwei', 'Drei'][number]}".strip()
        book(folder, f"text/{number}.epub", title=title, authors=(PEN,), language="de", isbn=isbn)
        records.append((f"D{number}", title, "", "Klarstein, Mira", isbn))
    book(folder, "text/real.epub", title="Ein eigenes Buch", authors=(REAL,), language="de")
    world.fake.dnb = dnb_xml(records)
    library_id = make_library(folder, name=name)
    member(library_id)
    scan(library_id)
    return library_id


def dnb_xml(records: list[tuple[str, str, str, str, str]]) -> bytes:
    """An answer of the National Library with ``(number, title, subtitle, author "Last, First", isbn)`` records."""
    body = ""
    for index, (ident, title, sub, author, isbn) in enumerate(records, start=1):
        subtitle = f'<subfield code="b">{sub}</subfield>' if sub else ""
        number = f'<datafield tag="020" ind1=" " ind2=" "><subfield code="a">{isbn}</subfield></datafield>' if isbn else ""
        body += (f'<record><recordData><record xmlns="http://www.loc.gov/MARC21/slim"><leader>00000nam a2200000 c 4500'
                 f'</leader><controlfield tag="001">{ident}</controlfield><controlfield tag="007">tu</controlfield>{number}'
                 f'<datafield tag="041" ind1=" " ind2=" "><subfield code="a">ger</subfield></datafield>'
                 f'<datafield tag="082" ind1="0" ind2="4"><subfield code="a">B</subfield></datafield>'
                 f'<datafield tag="100" ind1="1" ind2=" "><subfield code="a">{author}</subfield><subfield code="4">aut'
                 f'</subfield></datafield><datafield tag="245" ind1="1" ind2="0"><subfield code="a">{title}'
                 f'{" :" if sub else ""}</subfield>{subtitle}</datafield><datafield tag="264" ind1=" " ind2="1">'
                 f'<subfield code="c">2021</subfield></datafield></record></recordData><recordPosition>{index}'
                 f'</recordPosition></record>')
    return ('<?xml version="1.0" encoding="UTF-8"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            f'<version>1.1</version><numberOfRecords>{len(records)}</numberOfRecords><records>{body}</records>'
            '</searchRetrieveResponse>').encode()


def test_one_isbn_of_a_stranger_proves_nothing_but_two_books_do(client: TestClient, operator: Account,
                                                                 world: World) -> None:
    two_books_with_isbns(world, "pen-isbn-one", 1)
    assert sorted(people()) == sorted([REAL, PEN]), "one book with a stranger's ISBN is a mistake of the file"
    two_books_with_isbns(world, "pen-isbn-two", 2)
    assert people() == {REAL: [PEN]} or people() == {PEN: [REAL]}


def test_a_wrong_isbn_never_makes_two_authors_one(client: TestClient, operator: Account, world: World) -> None:
    world.only("dnb")
    world.fake.dnb = dnb_xml([("D9", "Das Haus", "Roman", "Weiss, Maria", "9783000000010")])
    folder = books_folder("pen-wrong-isbn")
    book(folder, "Brandt/Das Haus/x.epub", title="Das Haus", authors=("Anna Brandt",), language="de", isbn="9783000000010")
    book(folder, "Weiss/Das Haus/y.epub", title="Das Haus", authors=("Maria Weiss",), language="de")
    library_id = make_library(folder)
    member(library_id)
    scan(library_id)
    assert sorted(people()) == ["Anna Brandt", "Maria Weiss"]
    assert len(state()["works"]) == 2


def test_the_tags_that_say_one_writes_as_another_are_evidence_of_the_book_and_go_with_it(
        client: TestClient, operator: Account, world: World) -> None:
    world.only()
    folder = books_folder("pen-tags")
    book(folder, "text/nachtglas.epub", title="Nachtglas", authors=(REAL,), language="de")
    part = recording(folder, "Hoerbuch/Nachtglas (2021)/01.mp3", seconds=5.0, album="Nachtglas",
                     artist=f"{REAL} schreibt als {PEN}", track=1)
    book(folder, "text/pen.epub", title="Unter anderem Namen", authors=(PEN,), language="de")
    library_id = make_library(folder)
    scan(library_id)
    assert people() == {REAL: [PEN]}
    # The tag is changed: the evidence goes, and the pen name is a person of its own again.
    recording(folder, "Hoerbuch/Nachtglas (2021)/01.mp3", seconds=5.0, album="Nachtglas", artist=REAL, track=1)
    assert part.exists()
    scan(library_id)
    assert sorted(people()) == sorted([REAL, PEN])
    assert ("Unter anderem Namen", [PEN]) in work_authors()


# ---- The evidence of a match goes with the match ---------------------------------------------------------------------


def test_taking_a_match_back_takes_its_evidence_and_the_books_go_back_to_their_authors(
        client: TestClient, operator: Account, world: World) -> None:
    hardcover_library(world, "pen-clear")
    assert people() == {REAL: [PEN]} and len(state()["works"]) == 1
    with SessionLocal() as db:
        for edition in db.scalars(select(Edition)).all():
            apply.clear_match(db, edition)
        db.commit()
    assert sorted(people()) == sorted([REAL, PEN])
    assert work_authors() == [("Nachtglas", [REAL]), ("Nachtglas", [PEN])]
    with SessionLocal() as db:
        assert db.scalars(select(AliasClaim.id).where(AliasClaim.source == "hardcover")).all() == []


def test_a_match_again_without_the_evidence_loosens_the_names(client: TestClient, operator: Account,
                                                              world: World) -> None:
    hardcover_library(world, "pen-rematch")
    assert people() == {REAL: [PEN]}
    world.fake.hc_alternates = {REAL: ["Someone Else"]}
    source_cache.forget()
    with SessionLocal() as db:
        matching.rematch_all(db)
    run_jobs()
    assert sorted(people()) == sorted([REAL, PEN])


# ---- The way back by hand ---------------------------------------------------------------------------------------------


def pen_person() -> tuple[int, int]:
    with SessionLocal() as db:
        person = db.scalar(select(Person).where(Person.name == REAL))
        alias = db.scalar(select(PersonAlias))
        assert person is not None and alias is not None
        return person.id, alias.id


def test_a_person_who_manages_every_library_splits_a_pen_name_off_for_good(client: TestClient, operator: Account,
                                                                            world: World) -> None:
    hardcover_library(world, "pen-split")
    person_id, alias_id = pen_person()
    shown = client.get(f"/api/v1/people/{person_id}").json()
    assert shown["pen_names"] == [{"id": alias_id, "name": PEN}] and shown["can_split"] is True
    answer = client.post(f"/api/v1/people/{person_id}/split", json={"alias_id": alias_id})
    assert answer.status_code == 200, answer.text
    assert answer.json()["pen_names"] == [] and answer.json()["aliases"] == []
    assert sorted(people()) == sorted([REAL, PEN])
    assert work_authors() == [("Nachtglas", [REAL]), ("Nachtglas", [PEN])]
    # The word of a person stands over every match that comes.
    with SessionLocal() as db:
        matching.rematch_all(db)
    run_jobs()
    assert sorted(people()) == sorted([REAL, PEN])
    with SessionLocal() as db:
        assert db.scalars(select(AliasClaim.source).where(AliasClaim.apart.is_(True))).all() == ["hand"]


def test_a_reader_may_not_split_and_a_stranger_sees_nothing(client: TestClient, operator: Account,
                                                            world: World) -> None:
    library_id = hardcover_library(world, "pen-split-rights")
    person_id, alias_id = pen_person()
    reader = make_account("leser", "member")
    member(library_id, "leser", "read")
    other = new_client()
    sign_in(other, reader)
    shown = other.get(f"/api/v1/people/{person_id}").json()
    assert shown["can_split"] is False and shown["aliases"] == [PEN]
    refused = other.post(f"/api/v1/people/{person_id}/split", json={"alias_id": alias_id})
    assert refused.status_code == 403
    stranger = make_account("fremd", "member")
    third = new_client()
    sign_in(third, stranger)
    assert third.post(f"/api/v1/people/{person_id}/split", json={"alias_id": alias_id}).status_code == 404
    assert client.post(f"/api/v1/people/{person_id}/split", json={"alias_id": alias_id + 99}).status_code == 404
    assert people() == {REAL: [PEN]}


def test_whoever_manages_one_library_only_may_not_split_a_person_of_two(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    library_id = hardcover_library(world, "pen-split-two")
    folder = books_folder("pen-split-two-b")
    book(folder, "x/zweites.epub", title="Ein zweites Buch", authors=(REAL,), language="de")
    other_library = make_library(folder, name="Zweite")
    scan(other_library)
    person_id, alias_id = pen_person()
    manager = make_account("verwalter", "member")
    member(library_id, "verwalter", "manage")
    other = new_client()
    sign_in(other, manager)
    assert other.get(f"/api/v1/people/{person_id}").json()["can_split"] is False
    assert other.post(f"/api/v1/people/{person_id}/split", json={"alias_id": alias_id}).status_code == 403
    member(other_library, "verwalter", "manage")
    assert other.get(f"/api/v1/people/{person_id}").json()["can_split"] is True
    assert other.post(f"/api/v1/people/{person_id}/split", json={"alias_id": alias_id}).status_code == 200


def test_the_split_is_in_the_openapi_description(client: TestClient, operator: Account) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    assert "post" in spec["paths"]["/api/v1/people/{person_id}/split"]


# ---- What an older version joined without evidence is undone ------------------------------------------------------


def test_names_an_older_version_joined_without_evidence_are_two_people_again(client: TestClient, operator: Account,
                                                                            world: World) -> None:
    """An older version made the artist tag of a studio an alias of an author and moved the recording into the
    author's work. Nothing proves it by the present rules: the next settling of the library undoes it."""
    world.only()
    folder = books_folder("pen-legacy")
    book(folder, "text/nebelhaus.epub", title="Das Nebelhaus", authors=("Kai Ostermann",), language="de")
    recording(folder, "Hoerspiele/Das Nebelhaus/01.mp3", seconds=5.0, album="Das Nebelhaus",
              artist="Beispiel Hoerspiele Studios", track=1)
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        author = db.scalar(select(Person).where(Person.name == "Kai Ostermann"))
        studio = db.scalar(select(Person).where(Person.name == "Beispiel Hoerspiele Studios"))
        assert author is not None and studio is not None
        aliases.merge_people(db, author, studio)
        db.add(PersonAlias(person_id=author.id, name="Beispiel Hoerspiele Studios", key=studio.key, source="asin"))
        aliases.regroup(db, {author.id})
        db.commit()
    assert people() == {"Kai Ostermann": ["Beispiel Hoerspiele Studios"]}
    assert len(state()["works"]) == 1
    with SessionLocal() as db:
        assert aliases.recompute(db)
        db.commit()
    assert sorted(people()) == ["Beispiel Hoerspiele Studios", "Kai Ostermann"]
    assert work_authors() == [("Das Nebelhaus", ["Beispiel Hoerspiele Studios"]), ("Das Nebelhaus", ["Kai Ostermann"])]


def test_a_chain_of_names_through_a_third_proves_nothing(client: TestClient, operator: Account) -> None:
    folder = books_folder("pen-chain")
    for number, name in enumerate(("Kai Ostermann", "Lara Winter", "Ben Hollister"), start=1):
        book(folder, f"x/{number}.epub", title=f"Buch {name}", authors=(name,), language="de")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        editions = db.scalars(select(Edition.id).order_by(Edition.id)).all()
        # Studio -> Ostermann and Studio -> Winter (the studio is the file's side both times): a chain, no person.
        for edition_id, other in zip(editions[:2], ("Kai Ostermann", "Lara Winter"), strict=True):
            db.add(AliasClaim(name_a="Ben Hollister", key_a=aliases.key_of("Ben Hollister"), name_b=other,
                              key_b=aliases.key_of(other), source="tags", edition_id=edition_id))
        db.commit()
        aliases.recompute(db)
        db.commit()
    assert sorted(people()) == ["Ben Hollister", "Kai Ostermann", "Lara Winter"]
    with SessionLocal() as db:
        assert db.scalars(select(PersonAlias.id)).all() == []


def test_one_author_with_two_pen_names_is_one_person(client: TestClient, operator: Account) -> None:
    folder = books_folder("pen-star")
    for number, name in enumerate((REAL, PEN, "Mia Klar"), start=1):
        book(folder, f"x/{number}.epub", title=f"Buch {number}", authors=(name,), language="de")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        editions = db.scalars(select(Edition.id).order_by(Edition.id)).all()
        for edition_id, pen in zip(editions[1:], (PEN, "Mia Klar"), strict=True):
            db.add(AliasClaim(name_a=pen, key_a=aliases.key_of(pen), name_b=REAL, key_b=aliases.key_of(REAL),
                              source="tags", edition_id=edition_id))
        db.commit()
        aliases.recompute(db)
        db.commit()
    assert people() == {REAL: ["Mia Klar", PEN]}


# ---- Who sees which name ----------------------------------------------------------------------------------------------


def test_a_pen_name_out_of_a_book_of_another_library_is_not_shown(client: TestClient, operator: Account,
                                                                  world: World) -> None:
    """Two libraries: the e-book under the real name in A, the recording that proves the pen name in B. A reader of A
    alone sees the author, never the name out of the tags of B; the manager of both sees it."""
    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7, "Nachtglas", [REAL], users=900, year=2021)]
    world.fake.hc_alternates = {REAL: [PEN]}
    first = books_folder("pen-visible-a")
    book(first, "text/nachtglas.epub", title="Nachtglas", authors=(REAL,), language="de")
    library_a = make_library(first, name="A")
    second = books_folder("pen-visible-b")
    recording(second, "Hoerbuch/Nachtglas (2021)/Audio/01.mp3", seconds=290.0, album="Nachtglas", author=PEN,
              artist=PEN, track=1)
    library_b = make_library(second, name="B")
    member(library_a)
    member(library_b)
    scan(library_a)
    scan(library_b)
    assert people() == {REAL: [PEN]}
    reader = make_account("nur-a", "member")
    member(library_a, "nur-a", "read")
    other = new_client()
    sign_in(other, reader)
    (work,) = other.get("/api/v1/works").json()["items"]
    (author,) = work["authors"]
    assert author["name"] == REAL and author["aliases"] == []
    shown = other.get(f"/api/v1/people/{author['id']}").json()
    assert shown["aliases"] == [] and shown["pen_names"] == []
    assert client.get(f"/api/v1/people/{author['id']}").json()["aliases"] == [PEN]


# ---- Pure rules -------------------------------------------------------------------------------------------------------


def test_the_other_names_of_a_candidate_survive_the_cache() -> None:
    candidate = Candidate(source="hardcover", key="1", title="Nachtglas", authors=[REAL], author_aliases=[PEN])
    assert Candidate.from_json(candidate.to_json()).author_aliases == [PEN]
    assert Candidate.from_json({**candidate.to_json(), "author_aliases": "oops"}).author_aliases == []


def test_a_candidate_that_lists_the_file_author_as_another_name_of_its_author_counts_as_the_same_author() -> None:
    wanted = Wanted(kind="ebook", title="Nachtglas", authors=[PEN], language="de")
    plain = Candidate(source="hardcover", key="1", title="Nachtglas", authors=[REAL])
    named = Candidate(source="hardcover", key="1", title="Nachtglas", authors=[REAL], author_aliases=[PEN])
    assert author_state(wanted.authors, plain.authors) == "none"
    assert score(wanted, plain).fatal == "author_differs"
    assert score(wanted, named).fatal == "" and score(wanted, named).score > score(wanted, plain).score


def test_evidence_lives_in_json_only_as_data(client: TestClient, operator: Account) -> None:
    """The evidence of the tags is kept as names, never run: an odd name is a name."""
    folder = books_folder("pen-odd")
    recording(folder, "Hoerbuch/Nachtglas (2021)/01.mp3", seconds=5.0, album="Nachtglas",
              artist='Mira "x" Klarstein schreibt als <b>Nele</b>', track=1)
    scan(make_library(folder))
    with SessionLocal() as db:
        rows = db.scalars(select(AliasClaim)).all()
    assert all(json.dumps(row.name_a) for row in rows)


def test_a_work_a_person_placed_by_hand_is_not_moved_by_a_pen_name(client: TestClient, operator: Account) -> None:
    folder = books_folder("pen-hand")
    book(folder, "text/nachtglas.epub", title="Nachtglas", authors=(REAL,), language="de")
    recording(folder, "Hoerbuch/Nachtglas (2021)/Audio/01.mp3", seconds=5.0, album="Nachtglas", artist=PEN, track=1)
    library_id = make_library(folder)
    scan(library_id)
    assert len(state()["works"]) == 2
    with SessionLocal() as db:
        audio = db.scalar(select(Edition).where(Edition.kind == "audio"))
        assert audio is not None
        audio.grouped_by = "hand"
        db.add(AliasClaim(name_a=PEN, key_a=aliases.key_of(PEN), name_b=REAL, key_b=aliases.key_of(REAL), source="tags",
                          edition_id=audio.id))
        db.commit()
        aliases.recompute(db)
        db.commit()
        assert len(db.scalars(select(Work.id)).all()) == 2, "placed by hand, left where it is"
