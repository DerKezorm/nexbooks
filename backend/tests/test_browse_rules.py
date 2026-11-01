"""The rules of the themed shelves, tried with invented books: **a thin shelf is better than a wrong book.**

A content warning, one tag among a hundred, a subject for young people among sixty, a prize that was the author's, a
first year no edition backs, grown-up books among the books for children: all with subjects and books made up for the
purpose."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ShelfEntry
from app.services.browse import shelves, view
from app.services.sources import openlibrary

from .browse_helpers import Browse

#: Subjects that say nothing about any shelf.
FILLER = [f"Filler topic {n}" for n in range(100)]
KIDS = ["Picture book", "Board book"]
YOUNG = ["Young adult fiction", "Teenagers", "High school"]
FANTASY = ["Fantasy", "Magic", "Dragons"]


def judge(shelf: str, subjects: list[str], title: str = "") -> float | None:
    return shelves.judge(shelves.BY_KEY[shelf], subjects, title=title)


def test_the_subjects_of_a_shelf_put_a_book_on_it() -> None:
    assert judge("fantasy", FANTASY) is not None
    assert judge("kinder", KIDS) is not None and judge("jugend", YOUNG) is not None
    assert judge("natur", ["Nature", "Ecology", "Animals", "Birds"]) is not None


def test_a_book_open_library_gives_a_content_warning_stands_on_no_shelf() -> None:
    assert judge("fantasy", [*FANTASY, "content_warning:violence"]) is None
    assert judge("natur", ["Nature", "Ecology", "Animals", "content_warning:violence"]) is None


def test_a_comic_a_manga_and_a_study_guide_stand_on_no_shelf() -> None:
    assert judge("fantasy", [*FANTASY, "Manga"]) is None
    assert judge("fantasy", [*FANTASY, "Graphic novels"]) is None
    assert judge("fantasy", FANTASY, title="Manga Guide") is None
    assert judge("fantasy", [*FANTASY, "Study guides"]) is None


def test_one_tag_among_many_is_no_proof() -> None:
    """A book with a hundred subjects and one of fantasy is not a book of fantasy; three of them are, and so are two of a
    handful."""
    assert judge("fantasy", ["Fantasy", *FILLER]) is None
    assert judge("fantasy", [*FANTASY, *FILLER]) is not None
    assert judge("fantasy", ["Fantasy", "Magic", *FILLER[:8]]) is not None
    assert judge("fantasy", ["Fantasy", "Magic", *FILLER[:28]]) is None
    assert judge("fantasy", ["Fantasy", *FILLER[:2]]) is None, "one word among three subjects is no proof either"


def test_a_word_inside_a_subject_does_not_put_a_book_on_a_shelf() -> None:
    """"Political science" holds the word "science"; it says nothing of nature."""
    assert judge("natur", ["Political science"] * 3) is None


def test_a_book_of_another_kind_is_not_on_a_shelf_its_subjects_speak_against() -> None:
    """A tale of vampires with three subjects of science fiction and four of horror is a horror novel."""
    sf = ["Science fiction", "Aliens", "Time travel"]
    assert judge("scifi", sf) is not None and judge("scifi", [*sf, "Horror", "Horror tales"]) is not None
    assert judge("scifi", [*sf, "Horror", "Horror tales", "Horror fiction", "Vampires"]) is None


def test_one_subject_for_young_people_among_many_makes_no_book_for_young_people() -> None:
    """A school's reading list adds one such subject to a grown-up classic; a book for young people needs two."""
    assert judge("jugend", ["Young adult fiction", *FILLER[:59]]) is None
    assert judge("jugend", ["Young adult fiction", "Teenagers", *FILLER[:20]]) is not None


@pytest.mark.parametrize("word", ["Erotica", "Dark romance", "Spicy", "Murder", "Suicide"])
def test_a_subject_for_grown_ups_keeps_a_book_from_the_shelf_for_children(word: str) -> None:
    assert judge("kinder", [*KIDS, word]) is None


@pytest.mark.parametrize("word", ["Erotica", "Dark romance", "Spicy"])
def test_a_subject_for_grown_ups_keeps_a_book_from_the_shelf_for_young_people(word: str) -> None:
    assert judge("jugend", [*YOUNG, word]) is None


def test_the_lists_of_the_new_york_times_say_for_whom_a_book_is() -> None:
    """A book for children that stood on the list for young people is one for young people; one on a list for
    grown-ups is for neither."""
    young, kids = "nyt:young-adult-hardcover=2019-01-27", "nyt:childrens-middle-grade-hardcover=2019-01-27"
    grown = "nyt:combined-print-and-e-book-fiction=2023-05-21"
    assert judge("kinder", [*KIDS, kids]) is not None
    assert judge("kinder", [*KIDS, young]) is None and judge("kinder", [*KIDS, grown]) is None
    assert judge("jugend", [*YOUNG, grown]) is None
    assert shelves.nyt_lists([young]) == {"young"} and shelves.nyt_lists([grown]) == {"adult"}
    assert shelves.nyt_lists(["nyt:series-books=2020", "nyt:graphic-books-and-manga=2021"]) == set()


def test_a_prize_is_named_only_from_its_tag() -> None:
    """The author of one book won the prize, and another book is about prizes: neither won it."""
    assert shelves.award_of(shelves.BY_KEY["scifi"], ["award:hugo_award=novel"], 1990) == "Hugo Award"
    assert shelves.award_of(shelves.BY_KEY["scifi"], ["Hugo Award winner", "Hugo Award"], 1990) == ""
    assert shelves.award_of(shelves.BY_KEY["scifi"], ["award:hugo_award=novel"], 1930) == "", "written before the prize"


def test_a_first_year_no_edition_backs_is_no_proof_of_age() -> None:
    """A work first written in 1955 with a record of an edition of 1777 has no known first year."""
    assert openlibrary.backed_year(1955, [1777, 1958, 2001]) is None
    assert openlibrary.backed_year(1955, [1956, 1958]) == 1955
    assert openlibrary.backed_year(1955, [1955]) == 1955
    assert openlibrary.backed_year(1900, [1950]) is None, "the only edition known came out fifty years after it"


def test_an_old_text_is_told_by_its_subjects() -> None:
    assert shelves.is_old_text(["Fiction", "Early works to 1800"]) and not shelves.is_old_text(["Fiction", "Mystery"])


# ---- On the shelves ----------------------------------------------------------------------------------------------------


def _doc(number: int, title: str, authors: list[str], subjects: list[str], year: int = 2023) -> dict[str, Any]:
    edition = {"key": f"/books/OL{number}M", "title": title, "language": ["ger"], "cover_i": 90000 + number,
               "publish_date": [str(year)]}
    return {"key": f"/works/OL{number}W", "title": title, "author_name": authors, "first_publish_year": year,
            "language": ["ger"], "cover_i": 80000 + number, "subject": subjects, "number_of_pages_median": 400,
            "edition_count": 9, "editions": {"numFound": 1, "start": 0, "docs": [edition]}}


def _chart(genre: int, books: list[tuple[int, str, str]]) -> dict[str, Any]:
    """An old feed of Apple for one genre, in the order of the list: ``(number, title, author)``."""
    return {"feed": {"entry": [{
        "im:name": {"label": title}, "im:artist": {"label": author},
        "id": {"label": "", "attributes": {"im:id": str(number)}},
        "category": {"attributes": {"im:id": str(genre), "term": "Invented", "label": "Invented"}},
        "im:releaseDate": {"label": "2026-10-01T00:00:00-07:00", "attributes": {"label": "01.10.2026"}},
        "im:image": [{"label": f"https://is1-ssl.mzstatic.com/image/thumb/Publication/{number}/0x170bb.png",
                      "attributes": {"height": "170"}}],
        "summary": {"label": ""}} for number, title, author in books]}}


def _on(shelf: str, section: str) -> set[str]:
    with SessionLocal() as db:
        return {b.row.title for b in view.in_section(view.read(db, "de")[shelf], section)}


def test_no_book_of_an_author_for_grown_ups_stands_among_the_books_for_young_people(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apple files the grown-up fantasy of one author among the books for young people; the author's novel on the shelf of
    fantasy says what they are. Another author's book of the same chart stays."""
    world = Browse(monkeypatch, fetch_shelves=False)
    world.fake.ol_shelf = [_doc(5001, "Die Drachenwacht", ["Wren Hallowell"], [*FANTASY, "Epic fantasy"])]
    world.fake.apple_genre = {"feed": {"entry": []}}
    world.fake.apple_genres = {"11183": _chart(11183, [(1900000001, "Die Glasbruecke", "Wren Hallowell"),
                                                      (1900000002, "Der Sternenhof", "Pia Lindqvist")])}
    world.shelves("de")
    young = _on("jugend", "popular")
    assert "Der Sternenhof" in young, "the chart of Apple gives the shelf its books"
    assert "Die Glasbruecke" not in young, "its author writes for grown-ups"
    assert "Die Drachenwacht" in _on("fantasy", "popular") | _on("fantasy", "new")


def test_the_classics_for_children_come_from_apple_s_chart_of_them_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    world = Browse(monkeypatch, fetch_shelves=False)
    world.fake.ol_shelf = [_doc(5002, "Das Teehaus am Moor", ["Odile Marsh"], [*KIDS, "Juvenile fiction"], 1960)]
    world.fake.apple_genre = {"feed": {"entry": []}}
    world.fake.apple_genres = {"11115": _chart(11115, [(1900000003, "Der kleine Lampenputzer", "Hanno Wiek")])}
    world.shelves("de")
    with SessionLocal() as db:
        sources = set(db.scalars(select(ShelfEntry.source).where(ShelfEntry.shelf.in_(("kinder", "jugend")),
                                                                  ShelfEntry.section == "classic")))
    assert sources == {"apple"}
    assert "Das Teehaus am Moor" not in _on("kinder", "classic")
    assert "Der kleine Lampenputzer" in _on("kinder", "classic")
