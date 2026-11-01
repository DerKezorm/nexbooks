"""Two people that are one author by the spelling of the name alone (swapped words, the same name in two spellings, a
shorter name that fits exactly one longer name), and two authors a person joined by hand: nothing of the files is needed
for them, and a split holds for good, also after the names are worked out anew and the files are scanned again. The
names, the titles and the books are invented."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, AliasClaim, Credit, Edition, Person, PersonAlias
from app.services import aliases, catalog, spelling
from app.services.readers import names as names_module
from app.services.readers.names import authors_compatible, name_relation
from app.services.spelling import Name, spelling_pairs

from .conftest import make_account, new_client, sign_in
from .library_helpers import BOOKS_DIR, book, books_folder, make_library, recording, scan
from .match_helpers import World, member
from .test_scanner import state


@pytest.fixture(autouse=True)
def sources_off(world: World) -> None:
    world.only()


def shelf(name: str, books: list[tuple[str, str]], *, more: list[tuple[str, tuple[str, ...]]] | None = None) -> int:
    """A library with an e-book for each ``(title, author)``, scanned. ``more`` are books with several authors."""
    folder = books_folder(name)
    for number, (title, author) in enumerate(books):
        book(folder, f"b{number}/{title}.epub", title=title, authors=(author,), language="de")
    for number, (title, authors) in enumerate(more or []):
        book(folder, f"m{number}/{title}.epub", title=title, authors=authors, language="de")
    library_id = make_library(folder, name=name)
    member(library_id)
    scan(library_id)
    return library_id


def authors() -> dict[str, list[str]]:
    """The authors with the other names that lead to them."""
    with SessionLocal() as db:
        credited = select(Credit.person_id).where(Credit.role == "author")
        return {person.name: sorted(alias.name for alias in db.scalars(
            select(PersonAlias).where(PersonAlias.person_id == person.id)))
            for person in db.scalars(select(Person).where(Person.id.in_(credited)))}


def titles_of(name: str) -> list[str]:
    with SessionLocal() as db:
        return sorted(db.scalars(select(Edition.title).join(Credit, Credit.edition_id == Edition.id).join(
            Person, Person.id == Credit.person_id).where(Person.name == name, Credit.role == "author")))


def person_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalars(select(Person.id).where(Person.name == name)).one()


def settle_again(library_id: int) -> None:
    """The step after a scan, run once more (what a later scan does with names that did not change)."""
    with SessionLocal() as db:
        catalog.settle_library(db, library_id)


def split(client: TestClient, kept: str, gone: str) -> dict[str, Any]:
    page = client.get(f"/api/v1/people/{person_id(kept)}").json()
    alias = next(item for item in page["pen_names"] if item["name"] == gone)
    answer = client.post(f"/api/v1/people/{page['id']}/split", json={"alias_id": alias["id"]})
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


# ---- The names themselves ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("one", "other", "relation"), [
    ("Rune Falkner", "Falkner Rune", "swap"),
    ("Ada K. Feldkamp", "A. K. Feldkamp", "b_shorter"),
    ("A.K. Feldkamp", "Ada K. Feldkamp", "a_shorter"),
    ("R. Falkner", "Rune Falkner", "a_shorter"),
    ("Rune Falkner", "Rune K. Falkner", "a_shorter"),
    ("Alexander Brandt", "Alexandre Brandt", "peer"),
    ("Lew Falkner", "Leo Falkner", "peer"),
    # No relation: another first name, a group of siblings, one word, a number behind the name, an initial turned round.
    ("Rune Falkner", "Ralf Falkner", ""),
    ("Julia Roth", "Julian Roth", ""),
    ("Brüder Feldkamp", "Jakob Feldkamp", ""),
    ("Ostermann", "Ostermann", ""),
    ("Mara Feldkamp", "Mara Feldkamp 2", ""),
    ("R. 2 Falkner", "Rune 2 Falkner", ""),
    ("S. Falkner", "Falkner S.", ""),
    ("Rune Falkner", "Mira Klarstein", ""),
])
def test_how_two_names_stand_to_each_other(one: str, other: str, relation: str) -> None:
    assert name_relation(one, other) == relation
    turned = {"a_shorter": "b_shorter", "b_shorter": "a_shorter"}.get(relation, relation)
    assert name_relation(other, one) == turned
    if relation in ("peer", "a_shorter", "b_shorter"):
        assert authors_compatible(one, other), "a relation by the first name is always a name that can be one author"


def test_the_relation_never_says_more_than_the_names_that_can_be_one_author_say() -> None:
    names = ["Rune Falkner", "Falkner Rune", "R. Falkner", "R.K. Falkner", "Rune K. Falkner", "Ralf Falkner",
             "Alexander Brandt", "Alexandre Brandt", "A. Brandt", "Brüder Feldkamp", "Jakob Feldkamp", "Lew Falkner",
             "Leo Falkner", "Ostermann", "Mira Klarstein", "M. Klarstein", "Falkner, Rune"]
    for one in names:
        for other in names:
            if name_relation(one, other) in ("peer", "a_shorter", "b_shorter"):
                assert authors_compatible(one, other), (one, other)
            if name_relation(one, other) == "swap":
                assert not authors_compatible(one, other), (one, other)


def names_of(*spellings: str, groups: list[int] | None = None) -> list[Name]:
    return [Name(key=names_module.person_key(text), name=text, group=(groups or list(range(1, 99)))[index])
            for index, text in enumerate(spellings)]


def keys_of(*pairs: tuple[str, str]) -> set[frozenset[str]]:
    return {frozenset((names_module.person_key(one), names_module.person_key(other))) for one, other in pairs}


def test_a_shorter_name_fits_one_longer_name_only() -> None:
    assert spelling_pairs(names_of("R. Falkner", "Rune Falkner")) == keys_of(("R. Falkner", "Rune Falkner"))
    assert spelling_pairs(names_of("R. Falkner", "Rune Falkner", "Ralf Falkner")) == set()
    # Two longer names that are one person count once: the shorter fits that person.
    found = spelling_pairs(names_of("R. Falkner", "Rune Falkner", "Rune K. Falkner"))
    assert found == keys_of(("R. Falkner", "Rune Falkner"), ("R. Falkner", "Rune K. Falkner"),
                            ("Rune Falkner", "Rune K. Falkner"))
    # The same person under two names that lead to the same person already.
    assert spelling_pairs(names_of("R. Falkner", "Rune Falkner", "Rune K. Falkner", groups=[1, 2, 2])) == keys_of(
        ("R. Falkner", "Rune Falkner"), ("R. Falkner", "Rune K. Falkner"), ("Rune Falkner", "Rune K. Falkner"))


def test_what_a_person_said_is_two_people_and_what_stands_on_one_book_is_never_joined() -> None:
    found = names_of("R. Falkner", "Rune Falkner")
    assert spelling_pairs(found, apart=keys_of(("R. Falkner", "Rune Falkner"))) == set()
    assert spelling_pairs(found, together={frozenset((1, 2))}) == set()
    swapped = names_of("Rune Falkner", "Falkner Rune")
    assert spelling_pairs(swapped, apart=keys_of(("Rune Falkner", "Falkner Rune"))) == set()
    assert spelling_pairs(swapped, together={frozenset((1, 2))}) == set()
    assert spelling_pairs(names_of("Brüder Feldkamp", "Jakob Feldkamp", "Wilhelm Feldkamp")) == set()


# ---- By the spelling, from the files -----------------------------------------------------------------------------------


def test_names_that_do_not_stand_in_the_same_libraries_stay_apart() -> None:
    """One person has one name: a member of the one library would read the name off the other."""
    short, long = names_of("A. Feldkamp", "Ada Feldkamp")
    same = {short.group: frozenset({1}), long.group: frozenset({1})}
    assert spelling_pairs([short, long], shelves=same) == {frozenset((short.key, long.key))}
    assert spelling_pairs([short, long], shelves={short.group: frozenset({2}), long.group: frozenset({1})}) == set()
    # Sharing one is not enough: the longer name would reach the second library of the shorter one.
    assert spelling_pairs([short, long], shelves={short.group: frozenset({1, 2}), long.group: frozenset({1})}) == set()


def test_the_same_two_words_the_other_way_round_are_one_author_and_the_spelling_of_most_books_stays(
        client: TestClient, operator: Account) -> None:
    library_id = shelf("sp-swap", [("Nachtglas", "Falkner Rune"), ("Der Turm", "Rune Falkner"),
                                   ("Das Haus", "Rune Falkner")])
    assert authors() == {"Rune Falkner": ["Falkner Rune"]}
    assert titles_of("Rune Falkner") == ["Das Haus", "Der Turm", "Nachtglas"]
    assert len(state()["works"]) == 3
    with SessionLocal() as db:
        assert db.scalars(select(PersonAlias.source)).all() == ["spelling"]
        assert db.scalars(select(AliasClaim.edition_id)).all() == [None]
    # The scan again, and the names worked out again, change nothing.
    settle_again(library_id)
    scan(library_id)
    assert authors() == {"Rune Falkner": ["Falkner Rune"]}
    with SessionLocal() as db:
        assert spelling.note_spelling(db) is False and aliases.recompute(db) is False


def test_when_most_books_have_the_words_the_other_way_round_that_spelling_stays(
        client: TestClient, operator: Account) -> None:
    shelf("sp-swap-most", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Falkner Rune"), ("Das Haus", "Falkner Rune")])
    assert authors() == {"Falkner Rune": ["Rune Falkner"]}


def test_the_spelling_of_most_books_stays_even_when_the_other_has_more_e_books(client: TestClient,
                                                                              operator: Account) -> None:
    """The e-books alone would keep ``Falkner Rune`` (two against one); all the books count, and ``Rune Falkner`` has
    three."""
    folder = books_folder("sp-swap-all")
    book(folder, "a/Nachtglas.epub", title="Nachtglas", authors=("Falkner Rune",), language="de")
    book(folder, "a/Der Turm.epub", title="Der Turm", authors=("Falkner Rune",), language="de")
    book(folder, "b/Das Haus.epub", title="Das Haus", authors=("Rune Falkner",), language="de")
    for number, title in enumerate(("Die Klippe", "Das Moor")):
        recording(folder, f"Hoerbuch/{title}/01.mp3", seconds=5.0, album=title, artist="Rune Falkner",
                  author="Rune Falkner", track=1 + number)
    library_id = make_library(folder, name="sp-swap-all")
    member(library_id)
    scan(library_id)
    assert authors() == {"Rune Falkner": ["Falkner Rune"]}
    assert len(titles_of("Rune Falkner")) == 5


def test_two_authors_of_one_book_are_never_put_together_by_their_names(client: TestClient, operator: Account) -> None:
    shelf("sp-together", [("Nachtglas", "Rune Falkner")], more=[("Der Turm", ("Rune Falkner", "Falkner Rune"))])
    assert sorted(authors()) == ["Falkner Rune", "Rune Falkner"]


def test_a_shorter_name_is_put_together_with_the_one_longer_name_it_fits(client: TestClient,
                                                                       operator: Account) -> None:
    shelf("sp-short", [("Nachtglas", "Rune Falkner"), ("Der Turm", "R. Falkner"), ("Das Haus", "Mira Klarstein")])
    assert authors() == {"Rune Falkner": ["R. Falkner"], "Mira Klarstein": []}
    assert titles_of("Rune Falkner") == ["Der Turm", "Nachtglas"]


def test_a_shorter_name_that_fits_two_people_is_put_together_with_neither(client: TestClient,
                                                                         operator: Account) -> None:
    shelf("sp-short-two", [("Nachtglas", "Rune Falkner"), ("Der Turm", "R. Falkner"), ("Das Haus", "Ralf Falkner")])
    assert sorted(authors()) == ["R. Falkner", "Ralf Falkner", "Rune Falkner"]
    assert [titles_of(name) for name in sorted(authors())] == [["Der Turm"], ["Das Haus"], ["Nachtglas"]]


def test_a_second_longer_name_that_comes_in_loosens_the_shorter_one_again(client: TestClient,
                                                                         operator: Account) -> None:
    library_id = shelf("sp-later", [("Nachtglas", "Rune Falkner"), ("Der Turm", "R. Falkner")])
    assert authors() == {"Rune Falkner": ["R. Falkner"]}
    book(Path(BOOKS_DIR) / "sp-later", "x/Das Haus.epub", title="Das Haus", authors=("Ralf Falkner",), language="de")
    scan(library_id)
    scan(library_id)
    assert sorted(authors()) == ["R. Falkner", "Ralf Falkner", "Rune Falkner"]
    # Nobody lost a book on the way.
    assert titles_of("R. Falkner") == ["Der Turm"] and titles_of("Rune Falkner") == ["Nachtglas"]


def test_a_middle_initial_and_initials_are_one_author_and_the_works_of_one_title_are_one_work(
        client: TestClient, operator: Account) -> None:
    folder = books_folder("sp-initials")
    book(folder, "a/Nachtglas.epub", title="Nachtglas", authors=("Ada K. Feldkamp",), language="de")
    book(folder, "a/Das Haus.epub", title="Das Haus", authors=("Ada K. Feldkamp",), language="de")
    book(folder, "b/Der Turm.epub", title="Der Turm", authors=("A.K. Feldkamp",), language="de")
    recording(folder, "Hoerbuch/Nachtglas (2021)/01.mp3", seconds=5.0, album="Nachtglas", artist="A.K. Feldkamp",
              author="A.K. Feldkamp", track=1)
    library_id = make_library(folder, name="sp-initials")
    member(library_id)
    scan(library_id)
    assert authors() == {"Ada K. Feldkamp": ["A.K. Feldkamp"]}
    works = state()["works"]
    assert sorted(work["title"] for work in works) == ["Das Haus", "Der Turm", "Nachtglas"]
    nachtglas = next(work for work in works if work["title"] == "Nachtglas")
    assert sorted(edition["kind"] for edition in nachtglas["editions"]) == ["audio", "ebook"], (
        "the e-book and the recording of one title by one author are one book")


def test_one_first_name_in_two_languages_is_one_author(client: TestClient, operator: Account) -> None:
    shelf("sp-peers", [("Nachtglas", "Alexander Brandt"), ("Der Turm", "Alexandre Brandt"),
                       ("Das Haus", "Alexandre Brandt")])
    assert len(authors()) == 1
    assert titles_of(next(iter(authors()))) == ["Das Haus", "Der Turm", "Nachtglas"]


def test_a_split_off_name_stays_split_after_the_names_are_worked_out_again_and_the_files_are_scanned_again(
        client: TestClient, operator: Account) -> None:
    """Two real people whose names are the same words turned round: nothing tells them apart, a person can."""
    library_id = shelf("sp-split", [("Nachtglas", "Jonas Ruben"), ("Der Turm", "Ruben Jonas"),
                                    ("Das Haus", "Ruben Jonas")])
    assert authors() == {"Ruben Jonas": ["Jonas Ruben"]}
    answer = split(client, "Ruben Jonas", "Jonas Ruben")
    assert answer["pen_names"] == []
    assert sorted(authors()) == ["Jonas Ruben", "Ruben Jonas"]
    assert titles_of("Jonas Ruben") == ["Nachtglas"] and titles_of("Ruben Jonas") == ["Das Haus", "Der Turm"]
    for _round in range(2):
        with SessionLocal() as db:
            assert spelling.note_spelling(db) is False
            assert aliases.recompute(db) is False
            db.commit()
        settle_again(library_id)
        scan(library_id)
        assert sorted(authors()) == ["Jonas Ruben", "Ruben Jonas"]
    assert len(state()["works"]) == 3


def test_a_shorter_name_split_off_stays_split_and_the_other_longer_name_does_not_pull_it_back(
        client: TestClient, operator: Account) -> None:
    library_id = shelf("sp-split-short", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Rune K. Falkner"),
                                          ("Das Haus", "R. Falkner")])
    assert len(authors()) == 1
    first = next(iter(authors()))
    gone = next(name for name in ("R. Falkner", "Rune Falkner", "Rune K. Falkner") if name != first)
    split(client, first, gone)
    scan(library_id)
    settle_again(library_id)
    # The name that was split off is not joined again to any name of the group it left.
    assert gone in authors()
    assert all(not (gone in names and name != gone) for name, names in authors().items())


# ---- By hand ----------------------------------------------------------------------------------------------------------


def merge(client: TestClient, this: str, other: str, keep: str) -> Any:
    return client.post(f"/api/v1/people/{person_id(this)}/merge", json={"other_id": person_id(other), "keep": keep})


def test_two_authors_joined_by_hand_are_one_and_the_name_that_was_chosen_stays(client: TestClient,
                                                                            operator: Account) -> None:
    shelf("hand-join", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Rune Falkner"), ("Das Haus", "Mira Klarstein"),
                        ("Nachtglas", "Mira Klarstein")])
    page = client.get(f"/api/v1/people/{person_id('Rune Falkner')}").json()
    assert page["can_merge"] is True
    answer = merge(client, "Rune Falkner", "Mira Klarstein", "other")
    assert answer.status_code == 200, answer.text
    assert answer.json()["name"] == "Mira Klarstein" and answer.json()["aliases"] == ["Rune Falkner"]
    assert answer.json()["pen_names"][0]["name"] == "Rune Falkner" and answer.json()["can_split"] is True
    assert authors() == {"Mira Klarstein": ["Rune Falkner"]}
    assert titles_of("Mira Klarstein") == ["Das Haus", "Der Turm", "Nachtglas", "Nachtglas"]
    nachtglas = [work for work in state()["works"] if work["title"] == "Nachtglas"]
    assert len(nachtglas) == 1 and len(nachtglas[0]["editions"]) == 2
    with SessionLocal() as db:
        assert db.scalars(select(PersonAlias.source)).all() == ["hand"]
        (claim,) = db.scalars(select(AliasClaim)).all()
        assert (claim.source, claim.apart, claim.edition_id) == ("hand", False, None)
        assert aliases.recompute(db) is False
    # The scan again keeps it.
    scan_all()
    assert authors() == {"Mira Klarstein": ["Rune Falkner"]}


def scan_all() -> None:
    with SessionLocal() as db:
        ids = list(db.scalars(select(Edition.library_id).distinct()))
    for ident in ids:
        scan(ident)


def test_keeping_this_name_keeps_this_name(client: TestClient, operator: Account) -> None:
    shelf("hand-this", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Mira Klarstein"), ("Das Haus", "Mira Klarstein")])
    answer = merge(client, "Rune Falkner", "Mira Klarstein", "this")
    assert answer.status_code == 200 and answer.json()["name"] == "Rune Falkner"
    assert authors() == {"Rune Falkner": ["Mira Klarstein"]}


def test_a_split_takes_a_join_by_hand_back_and_it_holds(client: TestClient, operator: Account) -> None:
    library_id = shelf("hand-split", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Mira Klarstein")])
    assert merge(client, "Rune Falkner", "Mira Klarstein", "this").status_code == 200
    split(client, "Rune Falkner", "Mira Klarstein")
    assert sorted(authors()) == ["Mira Klarstein", "Rune Falkner"]
    with SessionLocal() as db:
        assert db.scalars(select(AliasClaim).where(AliasClaim.apart.is_(False))).all() == []
        assert db.scalars(select(func.count(AliasClaim.id)).where(AliasClaim.apart.is_(True))).one() == 1
    scan(library_id)
    assert sorted(authors()) == ["Mira Klarstein", "Rune Falkner"]
    # A person who joins them again overrules the split.
    assert merge(client, "Rune Falkner", "Mira Klarstein", "this").status_code == 200
    assert authors() == {"Rune Falkner": ["Mira Klarstein"]}


def test_an_author_with_a_pen_name_joined_with_another_keeps_every_name(client: TestClient, operator: Account) -> None:
    """The tags give a pen name to one author; a person joins that author with a third: all three names are one."""
    folder = books_folder("hand-chain")
    book(folder, "a/Nachtglas.epub", title="Nachtglas", authors=("Rune Falkner",), language="de")
    book(folder, "b/Der Turm.epub", title="Der Turm", authors=("Mira Klarstein",), language="de")
    book(folder, "c/Das Haus.epub", title="Das Haus", authors=("Ada Feldkamp",), language="de")
    library_id = make_library(folder, name="hand-chain")
    member(library_id)
    scan(library_id)
    with SessionLocal() as db:
        edition = db.scalars(select(Edition.id).where(Edition.title == "Nachtglas")).one()
        db.add(AliasClaim(name_a="Mira Klarstein", key_a=aliases.key_of("Mira Klarstein"), name_b="Rune Falkner",
                          key_b=aliases.key_of("Rune Falkner"), source=aliases.TAGS, edition_id=edition, apart=False))
        db.flush()
        aliases.recompute(db)
        db.commit()
    assert authors() == {"Rune Falkner": ["Mira Klarstein"], "Ada Feldkamp": []}
    assert merge(client, "Rune Falkner", "Ada Feldkamp", "other").status_code == 200
    assert authors() == {"Ada Feldkamp": ["Mira Klarstein", "Rune Falkner"]}
    assert len(state()["works"]) == 3


def test_joining_by_hand_is_for_a_browser_that_may_manage_every_library_of_both(client: TestClient,
                                                                                operator: Account) -> None:
    first = shelf("hand-rights-a", [("Nachtglas", "Rune Falkner")])
    second = shelf("hand-rights-b", [("Der Turm", "Mira Klarstein")])
    reader = make_account("leser", "member")
    member(first, "leser", "read")
    member(second, "leser", "read")
    other = new_client()
    sign_in(other, reader)
    body = {"other_id": person_id("Mira Klarstein"), "keep": "this"}
    assert other.get(f"/api/v1/people/{person_id('Rune Falkner')}").json()["can_merge"] is False
    assert other.post(f"/api/v1/people/{person_id('Rune Falkner')}/merge", json=body).status_code == 403
    manager = make_account("verwalter", "member")
    member(first, "verwalter", "manage")
    member(second, "verwalter", "read")
    boss = new_client()
    sign_in(boss, manager)
    assert boss.post(f"/api/v1/people/{person_id('Rune Falkner')}/merge", json=body).status_code == 403
    member(second, "verwalter", "manage")
    stranger = make_account("fremd", "member")
    third = new_client()
    sign_in(third, stranger)
    assert third.post(f"/api/v1/people/{person_id('Rune Falkner')}/merge", json=body).status_code == 404
    assert sorted(authors()) == ["Mira Klarstein", "Rune Falkner"]
    assert boss.post(f"/api/v1/people/{person_id('Rune Falkner')}/merge", json=body).status_code == 200
    assert authors() == {"Rune Falkner": ["Mira Klarstein"]}


def test_an_author_is_not_joined_with_themselves_or_with_somebody_who_is_not_there(client: TestClient,
                                                                                  operator: Account) -> None:
    shelf("hand-bad", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Mira Klarstein")])
    me = person_id("Rune Falkner")
    assert client.post(f"/api/v1/people/{me}/merge", json={"other_id": me, "keep": "this"}).status_code == 422
    assert client.post(f"/api/v1/people/{me}/merge", json={"other_id": me + 999, "keep": "this"}).status_code == 404
    assert client.post(f"/api/v1/people/{me}/merge", json={"other_id": 0, "keep": "this"}).status_code == 422
    assert client.post(f"/api/v1/people/{me}/merge",
                       json={"other_id": person_id("Mira Klarstein"), "keep": "both"}).status_code == 422
    assert sorted(authors()) == ["Mira Klarstein", "Rune Falkner"]


def test_a_name_joined_by_spelling_is_shown_to_whoever_sees_a_book_of_the_author(client: TestClient,
                                                                               operator: Account) -> None:
    library_id = shelf("sp-shown", [("Nachtglas", "Rune Falkner"), ("Der Turm", "Rune Falkner"),
                                    ("Das Haus", "Falkner Rune")])
    reader = make_account("leser", "member")
    member(library_id, "leser", "read")
    other = new_client()
    sign_in(other, reader)
    page = other.get(f"/api/v1/people/{person_id('Rune Falkner')}").json()
    assert page["aliases"] == ["Falkner Rune"] and page["can_split"] is False


def test_the_joins_are_in_the_openapi_description(client: TestClient, operator: Account) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    assert "post" in spec["paths"]["/api/v1/people/{person_id}/merge"]
    assert "can_merge" in spec["components"]["schemas"]["PersonOut"]["properties"]


def _crowd(size: int, last: str = "Schmidtke") -> list[Name]:
    return [Name(key=f"k{number}", name=f"Xa{number:05d}b {last}", group=number) for number in range(size)]


def test_a_last_name_that_very_many_names_share_is_left_alone_and_said_once(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """The names of a library are compared two by two within a last name; a crowd of invented first names under one last
    name would cost the square of them on every scan. Up to ``MAX_BUCKET`` names are compared, more are not, and the
    log says so once; the other last names are compared as before."""
    looked = {"at": 0}
    real = spelling.name_relation

    def counting(one: str, other: str) -> str:
        looked["at"] += 1
        return real(one, other)

    monkeypatch.setattr(spelling, "name_relation", counting)
    limit = spelling.MAX_BUCKET
    spelling_pairs(_crowd(limit))
    assert looked["at"] == limit * (limit - 1) // 2, "a crowd of exactly the limit is still compared"
    looked["at"] = 0
    with caplog.at_level("WARNING", logger="nexbooks.spelling"):
        found = spelling_pairs(_crowd(limit + 1) + [Name("a", "Rune Falkner", 900), Name("b", "Falkner Rune", 901)])
    assert looked["at"] == 1 and found == {frozenset(("a", "b"))}, "a small bucket beside the crowd is compared"
    assert len([record for record in caplog.records if "left alone" in record.getMessage()]) == 1
    assert "Schmidtke" not in caplog.text and "Xa0" not in caplog.text, "no name is written to the log"
