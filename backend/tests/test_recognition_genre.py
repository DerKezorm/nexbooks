"""The genre of a book: the words that stand for a genre (also half-written, also encoded twice), the majority of several
voices, the title that names its kind, and the guess from the author's other books, which rests on the books of the same
library only. All invented books."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Edition, LibraryMember
from app.services import genres

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, make_library, recording, scan


def see(*library_ids: int, name: str = "tester", right: str = "manage") -> None:
    """The account ``name`` is a member of the libraries (the operator sees books only when they are)."""
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.name == name))
        assert account is not None
        for library_id in library_ids:
            db.add(LibraryMember(library_id=library_id, user_id=account.id, right=right))
        db.commit()

# ---- Words -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("subject", "genre"), [
    ("Psychothriller", "krimi"),
    ("Roman-Thriller", "krimi"),
    ("Kriminalroman", "krimi"),
    ("Regionalkrimi", "krimi"),
    ("Krimis & Thriller", "krimi"),
    ("Krimis &amp; Thriller", "krimi"),
    ("Krimis &amp;amp; Thriller", "krimi"),
    ("Jugendbücher", "kinder"),
    # The accents of a word as the entities of a markup language (the file went through more than one tool).
    ("M&#228;dchenb&#252;cher", "kinder"),
    ("M&amp;#228;dchenb&amp;#252;cher", "kinder"),
    ("Kinder- und Jugendb&uuml;cher", "kinder"),
    ("Mädchenbuch", "kinder"),
    ("Kinderkrimi", "kinder"),
    ("Science Fiction & Fantasy", "scifi"),
    ("Biografien & Erinnerungen", "sachbuch"),
    ("Belletristik", "roman"),
    ("Fiction", "roman"),
    ("Bestseller", ""),
    ("", ""),
])
def test_a_subject_stands_for_a_genre_whatever_the_half_word_or_the_encoding(subject: str, genre: str) -> None:
    assert genres.genre_of_one(subject) == genre


# ---- Votes -----------------------------------------------------------------------------------------------------------


def test_a_telling_genre_beats_novel_when_the_voices_together_say_so() -> None:
    # The file says only "Fiction" (every file does), the library and the shop say crime.
    votes = genres.add_votes(genres.tally(["Fiction"], 3.0), genres.tally(["Belletristik", "Kriminalroman"], 2.0),
                             genres.tally(["Krimis & Thriller"], 2.0))
    assert genres.winner(votes) == "krimi"


def test_novel_is_the_answer_when_nobody_says_more() -> None:
    assert genres.winner(genres.add_votes(genres.tally(["Fiction"], 3.0), genres.tally(["Belletristik"], 2.0))) == "roman"


def test_nothing_is_the_answer_when_nobody_voted() -> None:
    assert genres.winner({}) == "" and genres.winner(genres.tally(["Bestseller", "Neu"], 3.0)) == ""


def test_a_single_thin_voice_does_not_beat_novel_but_beats_nothing() -> None:
    # One of the many subjects of an open catalogue says "thriller": too thin against the novel the rest says.
    thin = genres.tally(["Fiction", "Fiction", "Fiction", "Thriller", "x"], 1.0)
    assert genres.winner(thin) == "roman"
    # Alone it is still better than no genre.
    assert genres.winner({"krimi": 0.3}) == "krimi"


def test_a_voice_that_is_one_subject_among_many_casts_its_weight_in_proportion() -> None:
    votes = genres.tally(["Thriller", "Mystery", "Fiction"], 2.0)
    assert votes["krimi"] == pytest.approx(2.0 * 4 / 5) and votes["roman"] == pytest.approx(2.0 / 5)


def test_a_tie_goes_to_the_genre_whose_words_say_more() -> None:
    assert genres.winner({"roman": 1.0, "fantasy": 1.5, "kinder": 1.5}) == "kinder"


def test_a_thin_telling_voice_does_not_beat_a_novel_the_voices_agree_on() -> None:
    # Two catalogues say "fiction" with all their weight; one thin voice says "non-fiction" and another "young adult".
    assert genres.winner({"roman": 3.0, "sachbuch": 0.67, "kinder": 0.5}) == "roman"
    # A whole voice of a catalogue that says so beats a novel.
    assert genres.winner({"roman": 3.0, "krimi": 1.33}) == "krimi"


def test_a_source_that_knows_a_book_in_three_records_votes_once() -> None:
    from app.models import Edition
    from app.services.match.apply import build_values
    from app.services.match.score import Scored
    from app.services.sources.model import Candidate

    def candidate(source: str, key: str, *subjects: str) -> Scored:
        return Scored(candidate=Candidate(source=source, key=key, title="Das Buch", authors=["Ada Feldkamp"],
                                          subjects=list(subjects)))

    best = candidate("audible", "A1", "Belletristik", "Literatur")
    support = [candidate("openlibrary", f"OL{n}W", "Thrillers", "Fiction") for n in range(3)]
    values, _origins, extras = build_values(Edition(kind="ebook"), best, support)
    # Once: Audible says a novel (weight 2), Open Library two thirds crime (weight 1, once): the novel wins; three times Open
    # Library would have made it crime.
    assert values["genre"] == "roman" and extras["genre"]["votes"]["krimi"] == pytest.approx(0.667, abs=0.001)


# ---- In the files ------------------------------------------------------------------------------------------------------


def works_of(session: TestClient) -> dict[str, dict]:
    answer = session.get("/api/v1/works?per_page=200")
    assert answer.status_code == 200, answer.text
    return {item["title"]: item for item in answer.json()["items"]}


def test_a_title_that_names_its_kind_gives_the_genre_and_loses_the_kind(client: TestClient, operator: Account) -> None:
    folder = books_folder("kinds")
    book(folder, "a.epub", title="Der Beobachter - Kriminalroman", authors=("Ada Feldkamp",), language="de")
    book(folder, "b.epub", title="Ahnenland | Thriller", authors=("Ada Feldkamp",), language="de")
    see(make_library(folder, "Kinds", scan=True))
    items = works_of(client)
    assert set(items) == {"Der Beobachter", "Ahnenland"}
    assert items["Der Beobachter"]["genre"] == "krimi" and items["Ahnenland"]["genre"] == "krimi"
    assert items["Der Beobachter"]["genre_guess"] is False
    detail = client.get(f"/api/v1/works/{items['Der Beobachter']['id']}").json()
    assert detail["editions"][0]["subtitle"] == "Kriminalroman"


def test_the_words_of_the_file_are_voted_not_taken_one_by_one(client: TestClient, operator: Account) -> None:
    folder = books_folder("votes")
    book(folder, "a.epub", title="Alpha", authors=("Ada Feldkamp",), language="de",
         subjects=("Fiction", "Psychothriller", "Krimis &amp;amp; Thriller"))
    book(folder, "b.epub", title="Beta", authors=("Ben Hollister",), language="de", subjects=("Fiction",))
    book(folder, "c.epub", title="Gamma", authors=("Cora Lindqvist",), language="de", subjects=("Bestseller",))
    see(make_library(folder, "Votes", scan=True))
    items = works_of(client)
    assert items["Alpha"]["genre"] == "krimi" and items["Beta"]["genre"] == "roman" and items["Gamma"]["genre"] == ""
    assert not any(item["genre_guess"] for item in items.values())


# ---- The guess from the author's other books ----------------------------------------------------------------------------


def author_library(name: str, *, crime: int, other: int = 0, bare: int = 1, author: str = "Ada Feldkamp",
                   see_it: bool = True, tag: str = "") -> int:
    """A library with ``crime`` books of an author that are crime novels, ``other`` that are novels, and ``bare`` that say
    no genre at all. ``tag`` goes before the titles (so that libraries of several authors have different titles)."""
    folder = books_folder(name)
    for index in range(crime):
        book(folder, f"crime{index}.epub", title=f"{tag}Dunkel {index}", authors=(author,), language="de",
             subjects=("Krimi",))
    for index in range(other):
        book(folder, f"other{index}.epub", title=f"{tag}Hell {index}", authors=(author,), language="de",
             subjects=("Roman",))
    for index in range(bare):
        book(folder, f"bare{index}.epub", title=f"{tag}Leer {index}", authors=(author,), language="de")
    library_id = make_library(folder, name, scan=True)
    if see_it:
        see(library_id)
    return library_id


def test_a_book_with_no_genre_gets_the_genre_most_of_the_authors_other_books_have_marked_as_a_guess(
        client: TestClient, operator: Account) -> None:
    author_library("guess", crime=3)
    items = works_of(client)
    assert items["Leer 0"]["genre"] == "krimi" and items["Leer 0"]["genre_guess"] is True
    assert items["Dunkel 0"]["genre_guess"] is False
    detail = client.get(f"/api/v1/works/{items['Leer 0']['id']}").json()
    assert detail["editions"][0]["genre_guess"] is True


def test_a_guess_needs_two_other_books_and_a_clear_majority(client: TestClient, operator: Account) -> None:
    # One other book is no majority.
    author_library("one", crime=1, author="Ben Hollister", tag="A ")
    # Two against two is none.
    author_library("split", crime=2, other=2, author="Cora Lindqvist", tag="B ")
    # Three of four is.
    author_library("clear", crime=3, other=1, author="Dora Nystrom", tag="C ")
    items = works_of(client)
    assert items["A Leer 0"]["genre"] == "" and not items["A Leer 0"]["genre_guess"]
    assert items["B Leer 0"]["genre"] == "" and not items["B Leer 0"]["genre_guess"]
    assert items["C Leer 0"]["genre"] == "krimi" and items["C Leer 0"]["genre_guess"] is True


def test_a_guess_rests_on_the_books_of_the_same_library_only(client: TestClient, operator: Account) -> None:
    """What the books of a library the person is not in say is not told to them by a genre: the guess is made from the
    library's own books, so a person in one library cannot learn what the other holds."""
    open_id = author_library("open", crime=0, bare=1, see_it=False)
    closed_id = author_library("closed", crime=4, bare=0, see_it=False)
    see(open_id, closed_id)
    rita = make_account("rita")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=open_id, user_id=rita.id, right="read"))
        db.commit()
    assert closed_id != open_id
    with new_client(rita) as session:
        items = works_of(session)
        assert set(items) == {"Leer 0"}
        assert items["Leer 0"]["genre"] == "" and items["Leer 0"]["genre_guess"] is False
    # The operator sees both libraries; the book of the open library still has no guess (not made from the other one).
    assert works_of(client)["Leer 0"]["genre"] == ""
    with SessionLocal() as db:
        assert db.scalar(select(Edition.genre_guess).where(Edition.title == "Leer 0")) is False


def test_a_guess_is_taken_back_when_the_book_gets_a_genre_of_its_own(client: TestClient, operator: Account) -> None:
    library_id = author_library("own", crime=3)
    assert works_of(client)["Leer 0"]["genre_guess"] is True
    folder = books_folder("own-again")
    assert folder  # (a second folder only to make sure that no other library gets a say)
    from app.models import Library

    with SessionLocal() as db:
        library = db.get(Library, library_id)
        path = library.path
    from pathlib import Path

    book(Path(path), "bare0.epub", title="Leer 0", authors=("Ada Feldkamp",), language="de", subjects=("Fantasy",))
    scan(library_id)
    item = works_of(client)["Leer 0"]
    assert item["genre"] == "fantasy" and item["genre_guess"] is False


def test_the_genre_of_the_recording_of_a_book_is_its_genre_and_no_guess_is_made_for_the_book(client: TestClient, operator: Account) -> None:
    folder = books_folder("twins")
    for index in range(3):
        book(folder, f"crime{index}.epub", title=f"Dunkel {index}", authors=("Ada Feldkamp",), language="de",
             subjects=("Krimi",))
    book(folder, "bare.epub", title="Leer 0", authors=("Ada Feldkamp",), language="de")
    recording(folder, "leer/leer.m4b", 120.0, title="Leer 0", album="Leer 0", artist="Ada Feldkamp", genre="Fantasy")
    see(make_library(folder, "Twins", scan=True))
    item = works_of(client)["Leer 0"]
    # The recording says fantasy: that is what the book shows, and it is not a guess.
    assert item["genre"] == "fantasy" and item["genre_guess"] is False
    with SessionLocal() as db:
        ebook = db.scalar(select(Edition).where(Edition.title == "Leer 0", Edition.kind == "ebook"))
        assert ebook is not None and ebook.genre == "" and ebook.genre_guess is False


def test_a_member_sees_the_guess_of_a_library_they_are_in(client: TestClient, operator: Account) -> None:
    library_id = author_library("seen", crime=3, see_it=False)
    rita = make_account("rita")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library_id, user_id=rita.id, right="read"))
        db.commit()
    with new_client(rita) as session:
        assert works_of(session)["Leer 0"]["genre_guess"] is True


def test_the_operator_without_the_library_sees_nothing_of_its_guesses(client: TestClient, operator: Account) -> None:
    author_library("hidden", crime=3, see_it=False)
    assert works_of(client) == {}


def test_a_genre_a_voice_said_in_another_library_of_the_person_beats_the_guess_of_this_one(client: TestClient,
                                                                                         operator: Account) -> None:
    """The e-book has a guess (the books of its library); its recording lies in another library the person is in and says
    fantasy. The book shows fantasy, not the guess, whatever the order of the editions."""
    first = author_library("guessing", crime=3, see_it=False)
    folder = books_folder("recordings")
    recording(folder, "leer/leer.m4b", 120.0, title="Leer 0", album="Leer 0", artist="Ada Feldkamp", genre="Fantasy")
    second = make_library(folder, "Recordings", scan=True)
    see(first, second)
    item = works_of(client)["Leer 0"]
    assert item["genre"] == "fantasy" and item["genre_guess"] is False
    with SessionLocal() as db:
        ebook = db.scalar(select(Edition).where(Edition.title == "Leer 0", Edition.kind == "ebook"))
        assert ebook is not None and ebook.genre == "krimi" and ebook.genre_guess is True
    # A person who sees only the library with the e-book sees the guess.
    rita = make_account("rita")
    see(first, name="rita", right="read")
    with new_client(rita) as session:
        assert works_of(session)["Leer 0"]["genre"] == "krimi" and works_of(session)["Leer 0"]["genre_guess"] is True
    # And the filter of the shelf by genre follows what the person sees.
    assert [i["title"] for i in client.get("/api/v1/works?genre=fantasy").json()["items"]] == ["Leer 0"]
    assert "Leer 0" not in [i["title"] for i in client.get("/api/v1/works?genre=krimi").json()["items"]]
