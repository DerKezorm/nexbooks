"""Twins that the spelling kept apart: ``Straße`` and ``Strasse``, ``Süden`` and ``Sueden`` are one title and one name
(pen names: ``test_recognition_pen_names.py``). All names, titles and numbers are invented."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import (
    Credit,
    Edition,
    Library,
    Person,
    Series,
    Work,
)
from app.services import catalog
from app.services.readers.names import authors_compatible, compare_key, person_key, title_keys
from app.services.readers.text import key_fold

from .library_helpers import book, books_folder, make_library, recording, scan
from .test_scanner import state

# ---- Spelling: ß and ss, ä and ae ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("one,other", [
    ("Straße nach Süden", "Strasse nach Sueden"), ("Größe der Nacht", "Groesse der Nacht"),
    ("Über den Wolken", "Ueber den Wolken"), ("Äpfel und Öl", "Aepfel und Oel"), ("Müller", "Mueller"),
    ("Der Nebelhüter", "Der Nebelhueter")])
def test_the_spelling_of_the_umlauts_with_two_letters_is_the_same_title_and_the_same_name(one: str, other: str) -> None:
    assert key_fold(one) == key_fold(other)
    assert title_keys(one) == title_keys(other)
    assert compare_key(one) == compare_key(other)
    assert person_key(f"Mara {one}") == person_key(f"Mara {other}")
    assert authors_compatible(f"Mara {one}", f"Mara {other}")


def test_different_words_stay_different() -> None:
    assert key_fold("Neue Welt") != key_fold("Nebel Welt")
    assert title_keys("Strasse nach Norden") != title_keys("Strasse nach Sueden")
    assert not authors_compatible("Mara Müller", "Mara Maller")


def test_the_ebook_and_the_recording_of_a_title_written_two_ways_are_the_twins_of_one_work() -> None:
    folder = books_folder("spelling")
    book(folder, "text/strasse.epub", title="Straße nach Süden", authors=("Mara Müller",), language="de")
    recording(folder, "Mara Mueller/Strasse nach Sueden (2021)/Audio/01.mp3", seconds=5.0, album="Strasse nach Sueden",
              artist="Mara Mueller", track=1)
    scan(make_library(folder))
    (work,) = state()["works"]
    assert sorted(edition["kind"] for edition in work["editions"]) == ["audio", "ebook"]
    with SessionLocal() as db:
        assert len(db.scalars(select(Person.id)).all()) == 1, "one author, not two"


def test_works_and_people_kept_apart_by_an_older_version_come_together_by_themselves() -> None:
    folder = books_folder("spelling-old")
    book(folder, "text/strasse.epub", title="Straße nach Süden", authors=("Mara Müller",), language="de")
    recording(folder, "Mara Mueller/Strasse nach Sueden (2021)/Audio/01.mp3", seconds=5.0, album="Strasse nach Sueden",
              artist="Mara Mueller", track=1)
    library_id = make_library(folder)
    scan(library_id)
    # The state an older version left: the audio edition under a work of its own, the people and the series told by the
    # keys of the time (one key for ``Müller`` and one for ``Mueller``), and the files read by the older rules.
    with SessionLocal() as db:
        audio = db.scalar(select(Edition).where(Edition.kind == "audio"))
        assert audio is not None
        old_work = Work(title="Strasse nach Sueden", key="strasse nach sueden", main_key="strasse nach sueden",
                        added_at=audio.created_at, updated_at=audio.created_at)
        db.add(old_work)
        db.flush()
        shared = db.scalar(select(Person))
        assert shared is not None
        # The keys of the time: ``Müller`` without the dots, ``Mueller`` as it is written.
        db.execute(update(Person).where(Person.id == shared.id).values(key="mara muller"))
        twin = Person(name="Mara Mueller", sort_name="Mueller, Mara", key="mara mueller")
        db.add(twin)
        db.flush()
        db.execute(update(Credit).where(Credit.edition_id == audio.id, Credit.role == "author").values(person_id=twin.id))
        db.add(Credit(person_id=twin.id, work_id=old_work.id, role="author", position=0))
        db.execute(update(Edition).where(Edition.id == audio.id).values(work_id=old_work.id))
        db.add(Series(name="Quist-Saga", key="quist saga"))
        db.add(Series(name="Quist Saga", key="quist-saga"))
        db.execute(update(Library).where(Library.id == library_id).values(rev=1))
        from app.models import BookFile

        db.execute(update(BookFile).values(rev=1))
        db.commit()
        assert len(db.scalars(select(Work.id)).all()) == 2 and len(db.scalars(select(Person.id)).all()) == 2
    scan(library_id)
    (work,) = state()["works"]
    assert len(work["editions"]) == 2
    with SessionLocal() as db:
        assert len(db.scalars(select(Person.id)).all()) == 1
        keys = {person.key for person in db.scalars(select(Person))}
        assert keys == {person_key("Mara Müller")}
        assert len(db.scalars(select(Series.id)).all()) == 1


def test_keys_that_are_put_right_never_lose_a_credit() -> None:
    folder = books_folder("rekey")
    book(folder, "a.epub", title="Das Gut", authors=("Jörg Müller",), language="de")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        person = db.scalar(select(Person))
        assert person is not None
        db.execute(update(Person).where(Person.id == person.id).values(key="old key"))
        db.commit()
        credits = db.scalar(select(Credit.id).where(Credit.person_id == person.id))
        assert credits is not None
        assert catalog.rekey_people(db) == 1
        db.commit()
        assert db.scalar(select(Person.key)) == person_key("Jörg Müller")
        assert db.scalar(select(Credit.id).where(Credit.person_id == person.id)) == credits
