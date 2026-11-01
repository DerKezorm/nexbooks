"""The names in a field of authors, read whole: a pen name in a bracket (``Feldkamp, Mara (als Mara Lind)``), a reader
in front of the author (``Ben Hollister liest Mara Feldkamp``), the title of the book beside its author (``Der
Leuchtturm (ungekürzt) - Mara Feldkamp``), the mark of a part (``[Autorin]``, ``- Übersetzer``). A real library made
people of the pieces (``Feldkamp``, ``Mara (als Mara Lind)``) and two works of one book; the next reading puts that
right. All names and titles are invented."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, AliasClaim, BookFile, Credit, Edition, Person, PersonAlias
from app.services import aliases, catalog, scanner
from app.services.readers import names
from app.services.readers.names import clean_authors, display_name, read_people, split_authors, split_people

from .library_helpers import book, books_folder, make_library, scan
from .test_recognition_folders import parts
from .test_scanner import state

# ---- One field, read whole ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "people", "pens"), [
    # A pen name in a bracket beside the name, the way a package's creator (or its file-as) writes it.
    ("Feldkamp, Mara (als Mara Lind)", [("Mara Feldkamp", ""), ("Mara Lind", "")], [("Mara Feldkamp", "Mara Lind")]),
    ("Mara Feldkamp (als Mara Lind)", [("Mara Feldkamp", ""), ("Mara Lind", "")], [("Mara Feldkamp", "Mara Lind")]),
    ("Mara Feldkamp (writing as Mara Lind)", [("Mara Feldkamp", ""), ("Mara Lind", "")],
     [("Mara Feldkamp", "Mara Lind")]),
    ("Feldkamp, Mara [alias Mara Lind]", [("Mara Feldkamp", ""), ("Mara Lind", "")], [("Mara Feldkamp", "Mara Lind")]),
    ("Mara Feldkamp (pseud. Mara Lind)", [("Mara Feldkamp", ""), ("Mara Lind", "")], [("Mara Feldkamp", "Mara Lind")]),
    # The other way round: the pen name, and the real name in the bracket.
    ("Lind, Mara (d. i. Mara Feldkamp)", [("Mara Lind", ""), ("Mara Feldkamp", "")], [("Mara Feldkamp", "Mara Lind")]),
    ("Mara Lind (Pseudonym von Mara Feldkamp)", [("Mara Lind", ""), ("Mara Feldkamp", "")],
     [("Mara Feldkamp", "Mara Lind")]),
    # Written out in the field.
    ("Mara Feldkamp schreibt als Mara Lind", [("Mara Feldkamp", ""), ("Mara Lind", "")],
     [("Mara Feldkamp", "Mara Lind")]),
    ("Feldkamp, Mara writing as Mara Lind", [("Mara Feldkamp", ""), ("Mara Lind", "")],
     [("Mara Feldkamp", "Mara Lind")]),
    ("Mara Feldkamp alias Mara Lind", [("Mara Feldkamp", ""), ("Mara Lind", "")], [("Mara Feldkamp", "Mara Lind")]),
    # A mark without the other name is only taken off.
    ("Mara Lind (Pseud.)", [("Mara Lind", "")], []),
    ("Lind, Mara, pseud.", [("Mara Lind", "")], []),
    # A bracket that says too little: the name without it, and no pen name.
    ("Feldkamp, Mara (als Lind)", [("Mara Feldkamp", "")], []),
    ("Mara Feldkamp (als Mara & Ben Lind)", [("Mara Feldkamp", "")], []),
    # The reader and the author in one field.
    ("Ben Hollister liest Mara Feldkamp", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Ben Hollister Liest Mara Feldkamp", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Ben Hollister reads Mara Feldkamp", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Mara Feldkamp, gelesen von Ben Hollister", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Mara Feldkamp - gelesen von Ben Hollister", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Mara Feldkamp (gelesen von Ben Hollister)", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Mara Feldkamp read by Ben Hollister", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Mara Feldkamp, narrated by Ben Hollister", [("Mara Feldkamp", ""), ("Ben Hollister", "narrator")], []),
    ("Ben Hollister und Anna Brandt lesen Mara Feldkamp",
     [("Mara Feldkamp", ""), ("Ben Hollister", "narrator"), ("Anna Brandt", "narrator")], []),
    # Both at once: the reader of a book by a pen name.
    ("Ben Hollister liest Feldkamp, Mara (als Mara Lind)",
     [("Mara Feldkamp", ""), ("Mara Lind", ""), ("Ben Hollister", "narrator")], [("Mara Feldkamp", "Mara Lind")]),
    # The mark of the part a person played.
    ("Mara Feldkamp [Autorin]", [("Mara Feldkamp", "")], []),
    ("Mara Feldkamp (Author)", [("Mara Feldkamp", "")], []),
    ("Dietmar Weber - Übersetzer", [("Dietmar Weber", "translator")], []),
    ("Dietmar Weber – Sprecher", [("Dietmar Weber", "narrator")], []),
    ("Feldkamp, Mara (Hrsg.)", [("Mara Feldkamp", "editor")], []),
    # What a list puts for the rest of its people is nobody.
    ("Mara Feldkamp; div.", [("Mara Feldkamp", "")], []),
    ("Mara Feldkamp; u. a.", [("Mara Feldkamp", "")], []),
])
def test_a_field_of_authors_is_read_with_what_it_says_beside_the_names(
        field: str, people: list[tuple[str, str]], pens: list[tuple[str, str]]) -> None:
    read = read_people(field)
    assert read.people == people
    assert read.pens == pens


@pytest.mark.parametrize(("field", "people"), [
    # A comma that is no turning round: a list of full names, a firm, a word after the name.
    ("Mara Feldkamp, Ben Hollister", [("Mara Feldkamp", ""), ("Ben Hollister", "")]),
    ("Orchard Press, Inc.", [("Orchard Press, Inc.", "")]),
    ("Hollister, Ben, Jr.", [("Ben Hollister Jr.", "")]),
    ("Ben Hollister, Jr.", [("Ben Hollister Jr.", "")]),
    ("Hollister Jr., Ben", [("Ben Hollister Jr.", "")]),
    ("Feldkamp, Mara, Hollister, Ben", [("Mara Feldkamp", ""), ("Ben Hollister", "")]),
    # A name with a hyphen and a name with a particle, in both orders.
    ("Jean-Luc Marchetti", [("Jean-Luc Marchetti", "")]),
    ("Marchetti, Jean-Luc", [("Jean-Luc Marchetti", "")]),
    ("Kurt von Aldenau", [("Kurt von Aldenau", "")]),
    ("von Aldenau, Kurt", [("Kurt von Aldenau", "")]),
    ("Aldenau, Kurt von", [("Kurt von Aldenau", "")]),
    # A word like "liest" in a title or a sentence is no reader: both sides must be names.
    ("Mara liest Bücher", [("Mara liest Bücher", "")]),
    ("Das Kind liest Mara Feldkamp", [("Das Kind liest Mara Feldkamp", "")]),
    ("Ben Hollister liest", [("Ben Hollister liest", "")]),
    # A name that looks like a mark is a name.
    ("Ed Brandt", [("Ed Brandt", "")]),
    ("Anne Illner", [("Anne Illner", "")]),
    ("Mara Alias", [("Mara Alias", "")]),
])
def test_what_only_looks_like_a_mark_stays_the_way_it_came(field: str, people: list[tuple[str, str]]) -> None:
    read = read_people(field)
    assert read.people == people
    assert read.pens == []


def test_the_title_beside_its_author_names_the_author_only_when_it_is_the_title_of_the_book() -> None:
    field = "Der Leuchtturm (ungekürzt) - Mara Feldkamp"
    assert split_authors(field, titles=["Der Leuchtturm"]) == ["Mara Feldkamp"]
    assert split_authors("Mara Feldkamp - Der Leuchtturm", titles=["Der Leuchtturm"]) == ["Mara Feldkamp"]
    # Umlauts written out are the same title.
    assert split_authors("Wer Wind saet (ungekürzt) - Mara Feldkamp", titles=["Wer Wind sät"]) == ["Mara Feldkamp"]
    # Another book's title proves nothing: the field stays the way it came.
    assert split_authors(field, titles=["Das Moor"]) == [field]
    assert split_authors(field) == [field]
    # Both sides names, and the title is one of them: the other is the author.
    assert split_authors("Anna Brandt - Mara Feldkamp", titles=["Anna Brandt"]) == ["Mara Feldkamp"]
    # A side that is no name is never the author.
    assert split_authors("Der Leuchtturm - Das Moor", titles=["Der Leuchtturm"]) == ["Der Leuchtturm - Das Moor"]


def test_a_source_lists_one_person_per_entry_and_its_translator_among_the_authors_is_none() -> None:
    assert clean_authors(["Mara Feldkamp", "Dietmar Weber - Übersetzer"]) == ["Mara Feldkamp"]
    assert clean_authors(["Dietmar Weber - Übersetzer"], any_role=True) == ["Dietmar Weber"]
    # One entry is one name: an ampersand in it is a firm's or a pair's name, not two people.
    assert clean_authors(["Simon & Feldkamp"]) == ["Simon & Feldkamp"]
    assert clean_authors(["Feldkamp, Mara (als Mara Lind)"]) == ["Mara Feldkamp", "Mara Lind"]
    assert clean_authors(["Ben Hollister liest Mara Feldkamp"]) == ["Mara Feldkamp"]


def test_the_pen_name_of_a_field_is_the_pair_the_tags_give_as_evidence() -> None:
    assert names.pen_pairs("Feldkamp, Mara (als Mara Lind)") == [("Mara Feldkamp", "Mara Lind")]
    assert names.pen_name_of("Mara Feldkamp schreibt als Mara Lind") == ("Mara Feldkamp", "Mara Lind")
    assert names.author_names("Feldkamp, Mara (als Mara Lind)") == ["Mara Feldkamp", "Mara Lind"]
    assert names.author_names("Ben Hollister liest Mara Feldkamp") == ["Mara Feldkamp"]
    assert names.author_names("Feldkamp, Mara") == ["Feldkamp, Mara"]


def test_a_word_after_the_last_name_belongs_to_it() -> None:
    assert names.sort_name("Ben Hollister Jr.") == "Hollister Jr., Ben"
    assert names.person_key("Hollister Jr., Ben") == names.person_key("Ben Hollister Jr.")
    assert split_people("Hollister, Ben, Jr.; Ben Hollister Jr.") == [("Ben Hollister Jr.", "")]


# ---- Through the scan ----------------------------------------------------------------------------------------------


def test_an_ebook_by_a_pen_name_in_a_bracket_and_its_recording_are_one_work_by_one_person() -> None:
    folder = books_folder("pen-bracket")
    book(folder, "Mara Feldkamp/Der Leuchtturm.epub", title="Der Leuchtturm", authors=("Feldkamp, Mara (als Mara Lind)",),
         language="de")
    parts(folder, "Hoerbuch/Der Leuchtturm (2019)/Audio/{number:02d}.mp3", 2, album="Der Leuchtturm",
          artist="Mara Feldkamp", narrator="Ben Hollister")
    scan(make_library(folder, "Pen bracket"))
    (work,) = state()["works"]
    assert work["authors"] == ["Mara Feldkamp"]
    assert sorted(edition["kind"] for edition in work["editions"]) == ["audio", "ebook"]
    with SessionLocal() as db:
        # The package says it: Mara Lind is another name of Mara Feldkamp, and nothing of the bracket is a person.
        assert db.scalars(select(AliasClaim.source)).all() == ["tags"]
        assert db.scalars(select(PersonAlias.name)).all() == ["Mara Lind"]
        assert sorted(db.scalars(select(Person.name))) == ["Ben Hollister", "Mara Feldkamp"]


def test_a_recording_whose_artist_holds_its_title_and_author_is_by_the_author() -> None:
    folder = books_folder("title-author")
    book(folder, "Mara Feldkamp/Der Leuchtturm.epub", title="Der Leuchtturm", authors=("Mara Feldkamp",), language="de")
    parts(folder, "Hoerbuch/Der Leuchtturm (2019)/Audio/{number:02d}.mp3", 2, album="Der Leuchtturm",
          artist="Der Leuchtturm (ungekürzt) - Mara Feldkamp", albumartist="Ben Hollister liest Mara Feldkamp")
    scan(make_library(folder, "Title author"))
    (work,) = state()["works"]
    assert work["authors"] == ["Mara Feldkamp"]
    audio = next(edition for edition in work["editions"] if edition["kind"] == "audio")
    assert audio["narrators"] == ["Ben Hollister"], "the reader of the album artist, never the author"
    with SessionLocal() as db:
        assert sorted(db.scalars(select(Person.name))) == ["Ben Hollister", "Mara Feldkamp"]


def test_an_author_who_reads_her_own_book_is_its_narrator() -> None:
    folder = books_folder("own-reader")
    parts(folder, "Mara Feldkamp/Der Leuchtturm (2019)/Audio/{number:02d}.mp3", 2, album="Der Leuchtturm",
          artist="Mara Feldkamp", narrator="Mara Feldkamp")
    scan(make_library(folder, "Own reader"))
    (work,) = state()["works"]
    assert work["editions"][0]["narrators"] == ["Mara Feldkamp"]


# ---- What an older rule left: put right by the next reading -----------------------------------------------------


def _two_works_by_the_old_rule(name: str) -> int:
    """The state an older rule left: the e-book's field read as one name (``display_name`` of the whole field), so the
    e-book and its recording stand in two works by two people."""
    folder = books_folder(name)
    book(folder, "Mara Feldkamp/Der Leuchtturm.epub", title="Der Leuchtturm", authors=("Feldkamp, Mara (als Mara Lind)",),
         language="de")
    parts(folder, "Hoerbuch/Der Leuchtturm (2019)/Audio/{number:02d}.mp3", 2, album="Der Leuchtturm",
          artist="Mara Feldkamp")
    library_id = make_library(folder, "Old rule")
    scan(library_id)
    with SessionLocal() as db:
        row = db.scalar(select(BookFile).where(BookFile.format == "epub"))
        assert row is not None
        row.probe = {**row.probe, "authors": [display_name("Feldkamp, Mara (als Mara Lind)")], "pen_names": []}
        row.dirty = True
        db.commit()
    # Put together from what was read (the file is not read again: it did not change).
    scan(library_id)
    with SessionLocal() as db:
        # What the older rule left, and a person a rule before it made of a reader and an author.
        db.add(Person(name="Ben Hollister liest Mara Feldkamp", sort_name="Feldkamp, Ben Hollister liest Mara",
                      key="ben hollister liest mara feldkamp"))
        db.commit()
    assert sorted(work["authors"][0] for work in state()["works"]) == ["Mara (als Mara Lind) Feldkamp", "Mara Feldkamp"]
    return library_id


def test_the_next_reading_joins_the_two_works_and_the_people_of_the_old_rule_go(
        monkeypatch: pytest.MonkeyPatch) -> None:
    library_id = _two_works_by_the_old_rule("old-rule")
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    (work,) = state()["works"]
    assert work["authors"] == ["Mara Feldkamp"]
    assert sorted(edition["kind"] for edition in work["editions"]) == ["audio", "ebook"]
    with SessionLocal() as db:
        assert db.scalars(select(Person.name)).all() == ["Mara Feldkamp"]
        assert db.scalars(select(PersonAlias.name)).all() == ["Mara Lind"]


def test_a_work_a_person_split_by_hand_stays_split_when_the_names_are_read_anew(
        monkeypatch: pytest.MonkeyPatch) -> None:
    library_id = _two_works_by_the_old_rule("old-rule-hand")
    with SessionLocal() as db:
        for edition in db.scalars(select(Edition)):
            edition.grouped_by = "hand"
        db.commit()
    monkeypatch.setattr(scanner, "READ_REV", scanner.READ_REV + 1)
    scan(library_id)
    works = state()["works"]
    assert len(works) == 2, "what a person decided is not undone"
    assert [work["authors"] for work in works] == [["Mara Feldkamp"], ["Mara Feldkamp"]]


def test_two_names_a_person_said_are_two_people_stay_two_also_when_a_bracket_says_otherwise(
        client: TestClient, operator: Account) -> None:
    folder = books_folder("pen-apart")
    book(folder, "Mara Feldkamp/Der Leuchtturm.epub", title="Der Leuchtturm", authors=("Feldkamp, Mara (als Mara Lind)",),
         language="de")
    with SessionLocal() as db:
        aliases.keep_apart(db, "Mara Feldkamp", "Mara Lind", None)
        db.commit()
    scan(make_library(folder, "Apart"))
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PersonAlias.id))) == 0
        assert sorted(db.scalars(select(Person.name))) == ["Mara Feldkamp", "Mara Lind"]


def test_people_without_a_book_go_and_people_with_one_stay(client: TestClient, operator: Account) -> None:
    folder = books_folder("unused")
    book(folder, "a.epub", title="Alpha", authors=("Mara Feldkamp",), language="de")
    scan(make_library(folder, "Unused"))
    with SessionLocal() as db:
        db.add(Person(name="Feldkamp", sort_name="Feldkamp", key="feldkamp"))
        db.commit()
        assert catalog.drop_unused_people(db) == 1
        db.commit()
        assert db.scalars(select(Person.name)).all() == ["Mara Feldkamp"]
        assert db.scalar(select(func.count(Credit.id))) >= 1
        assert catalog.drop_unused_people(db) == 0
