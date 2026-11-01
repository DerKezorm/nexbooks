"""The catalogue of an author's works built from the answers of the sources: works put together across
languages and sources, the rule of two sources, series, pen names, books to come, the kind of each work, and which
source is asked for which language. Every answer is recorded and invented (``tests/fixtures/oeuvre``)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, CatalogPerson, CatalogWork, ForeignBook
from app.services.oeuvre import store

from .oeuvre_fakes import Sources


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Sources:
    """The stand-in sources, and a person on the server who reads German: the catalogue is built for German and for
    the author's own language."""
    found = Sources(monkeypatch)
    set_languages("de")
    return found


def built(name: str) -> tuple[CatalogPerson, dict[str, CatalogWork]]:
    """Builds the catalogue of ``name`` now; its works by their English title (else German, else any)."""
    with SessionLocal() as db:
        row = store.for_name(db, name)
        assert row is not None
        db.commit()
        assert store.build(db, row)
        works = db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id)).all()
        by: dict[str, CatalogWork] = {}
        for work in works:
            titles = work.titles or {}
            title = (titles.get("en") or titles.get("de") or next(iter(titles.values())))["title"]
            by[title] = work
        db.expunge_all()
        return row, by


def languages_of(work: CatalogWork) -> dict[str, Any]:
    return {code: (item["title"], item["year"], item["released"], item["ebook"], item["audio"])
            for code, item in (work.titles or {}).items()}


def set_languages(*codes: str) -> None:
    with SessionLocal() as db:
        for index, code in enumerate(codes):
            db.add(Account(name=f"reader{index}", display_name="R", role="member", sign_in="password",
                           password_hash="x", language=code))
        db.commit()


def test_one_work_holds_its_editions_in_every_language_and_both_forms(sources: Sources) -> None:
    row, works = built("Edmund Halloran")
    shore = works["The Ninth Shore"]
    titles = languages_of(shore)
    assert titles["en"][0] == "The Ninth Shore" and titles["de"][0] == "Das neunte Ufer"
    # A book and a recording in both languages; the German year is the earliest German edition's.
    assert titles["de"][3] and titles["de"][4] and titles["en"][4]
    assert shore.orig_language == "en" and shore.kind == "roman"
    assert set(shore.sources or []) >= {"hardcover", "openlibrary", "dnb", "audible"}
    # A later paperback with "Roman" in the title is the same work, with the year of the first edition.
    salt = languages_of(works["The Salt Daughters"])
    assert salt["de"][0] == "Die Salztöchter" and salt["de"][1] == 1976
    assert row.orig_language == "en"


def test_a_work_only_one_source_knows_is_left_out_when_others_answered(sources: Sources) -> None:
    _row, works = built("Edmund Halloran")
    assert "Halloran Calendar 2025" not in works
    # Nor is a book of a namesake (another first name) anywhere in the catalogue.
    assert "Harbour Lights" not in works and "Leuchtfeuer" not in {
        (w.titles or {}).get("de", {}).get("title") for w in works.values()}


def test_short_titles_stay_two_works(sources: Sources) -> None:
    _row, works = built("Edmund Halloran")
    assert languages_of(works["Soot"])["de"][0] == "Ruß"
    assert languages_of(works["Soot and Roses"])["de"][0] == "Ruß und Rosen"
    assert works["Soot"].key != works["Soot and Roses"].key


def test_a_series_title_gives_the_series_and_the_title(sources: Sources) -> None:
    _row, works = built("Edmund Halloran")
    road = works["The Ash Road"]
    assert road.series and road.series_pos == 1.0
    assert languages_of(road)["de"][0] == "Die Aschenstraße"
    assert {works[t].series_pos for t in ("The Ash Road", "Three Doors", "The Salt Plains")} == {1.0, 2.0, 3.0}


def test_the_kinds_of_works(sources: Sources) -> None:
    _row, works = built("Edmund Halloran")
    kinds = {title: (work.kind, work.why) for title, work in works.items()}
    assert kinds["Soot and Roses"] == ("erz", "")
    assert kinds["Pim and the Lighthouse"] == ("kinder", "")
    assert kinds["On Being Afraid"] == ("sach", "")
    assert kinds["The Wrenfield Chronicles: Complete Edition"][0] == "box"
    assert kinds["Soot"] == ("roman", "")


def test_books_under_a_pen_name_come_with_it(sources: Sources) -> None:
    row, works = built("Edmund Halloran")
    assert "Richard Vane" in (row.aliases or [])
    rain = works["Rain Fury"]
    assert languages_of(rain)["de"][0] == "Regenwut"
    # Hardcover lists the book under the author: the pen name stands for the German edition and Open Library.
    assert rain.kind == "roman"


def test_a_book_to_come_has_its_day_in_each_language(sources: Sources) -> None:
    _row, works = built("Edmund Halloran")
    keeper = languages_of(works["The Keeper of Lost Rooms"])
    assert keeper["en"][2] == "2026-11-15"
    assert keeper["de"][2] == "2027-03-03"


def test_every_work_has_a_book_of_the_sources_with_all_its_titles(sources: Sources) -> None:
    _row, works = built("Edmund Halloran")
    with SessionLocal() as db:
        book = db.get(ForeignBook, works["The Ninth Shore"].foreign_id)
        assert book is not None
        titles = {(item["text"], item["language"]) for item in book.data["titles"]}
        assert ("Das neunte Ufer", "de") in titles and ("The Ninth Shore", "en") in titles
        kinds = {(form["kind"], form["language"]) for form in book.data["forms"]}
        assert ("audio", "de") in kinds and ("ebook", "en") in kinds
