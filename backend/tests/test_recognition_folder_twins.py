"""An e-book and a recording that lie in folders of the same name in two libraries (``Autor/Titel (2019)`` in the
e-books and in the recordings) are one book, whatever their files call them; unless they say two volumes, two
authors, or the folder holds more than one book of a kind. All names and titles are invented."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Edition, Work
from app.services import catalog

from .library_helpers import book, books_folder, make_library, recording, scan

FOLDER = "Ida Brandt/Der Spiegelsaal (2019)"


def shelves(name: str) -> tuple[Any, Any]:
    return books_folder(f"{name}-e"), books_folder(f"{name}-a")


def read(*folders: Any) -> None:
    for number, folder in enumerate(folders):
        scan(make_library(folder, f"Books {number}"))


def works() -> list[set[str]]:
    with SessionLocal() as db:
        found: dict[int, set[str]] = {}
        for work_id, kind in db.execute(select(Edition.work_id, Edition.kind)):
            found.setdefault(work_id, set()).add(kind)
        return sorted(found.values(), key=sorted)


@pytest.mark.parametrize(("ebook", "album"), [
    # The package names the author in front and a number behind.
    ("Brandt, Ida - Spiegelsaal 03", "Der Spiegelsaal"),
    # The tags of the recording carry a subtitle the e-book does not.
    ("Der Spiegelsaal", "Spiegelsaal: Die Rückkehr"),
])
def test_an_e_book_and_a_recording_in_folders_of_one_name_are_one_book_whatever_the_tags_add(ebook: str,
                                                                                             album: str) -> None:
    books, audio = shelves("folder-twins")
    book(books, f"{FOLDER}/spiegelsaal.epub", title=ebook, authors=("Ida Brandt",), language="de")
    recording(audio, f"{FOLDER}/01.mp3", seconds=5.0, album=album, title=album, artist="Ida Brandt", track=1)
    read(books, audio)
    assert works() == [{"audio", "ebook"}]


@pytest.mark.parametrize(("folder", "ebook", "album"), [
    # The tags of the recording name another title, and a reader where the author belongs.
    (FOLDER, "Der Spiegelsaal", "In ewiger Nacht"),
    # The folder's name holds no word of the titles.
    ("Ida Brandt/Neu", "Brandt, Ida - Spiegelsaal 03", "Spiegelsaal 03"),
    # The package of the e-book names another title: the folder alone is no proof against it.
    (FOLDER, "In ewiger Nacht", "Der Spiegelsaal"),
])
def test_folders_of_one_name_without_a_word_of_each_title_are_no_proof(folder: str, ebook: str, album: str) -> None:
    books, audio = shelves("folder-twins-noword")
    book(books, f"{folder}/spiegelsaal.epub", title=ebook, authors=("Ida Brandt",), language="de")
    reader = "Mara Leser" if album == "In ewiger Nacht" else "Ida Brandt"
    recording(audio, f"{folder}/01.mp3", seconds=5.0, album=album, title=album, artist=reader, track=1)
    read(books, audio)
    assert works() == [{"audio"}, {"ebook"}]


def test_two_authors_two_volumes_or_two_books_in_one_folder_stay_apart() -> None:
    books, audio = shelves("folder-twins-apart")
    book(books, f"{FOLDER}/spiegelsaal.epub", title="Der Spiegelsaal", authors=("Ida Brandt",), language="de")
    recording(audio, f"{FOLDER}/01.mp3", seconds=5.0, album="Ein Kalt-Krimi", title="Ein Kalt-Krimi",
              artist="Rune Falkner", track=1)
    other = "Ida Brandt/Spiegelsaal"
    book(books, f"{other}/eins.epub", title="Spiegelsaal", authors=("Ida Brandt",), language="de", series="Saal",
         series_pos=1.0)
    recording(audio, f"{other}/01.mp3", seconds=5.0, album="Ein Saal-Krimi 2", title="Ein Saal-Krimi 2",
              artist="Ida Brandt", track=1, series="Saal", series_pos="2")
    crowded = "Ida Brandt/Spiegelsaal Sammlung"
    book(books, f"{crowded}/a.epub", title="Spiegelsaal Eins", authors=("Ida Brandt",), language="de")
    book(books, f"{crowded}/b.epub", title="Spiegelsaal Zwei", authors=("Ida Brandt",), language="de")
    recording(audio, f"{crowded}/01.mp3", seconds=5.0, album="Ein Kalt-Krimi", title="Ein Kalt-Krimi",
              artist="Ida Brandt", track=1)
    read(books, audio)
    assert {"audio", "ebook"} not in works()


def test_a_work_a_person_placed_is_never_joined_by_its_folder() -> None:
    books, audio = shelves("folder-twins-hand")
    book(books, f"{FOLDER}/spiegelsaal.epub", title="Brandt, Ida - Spiegelsaal 03", authors=("Ida Brandt",),
         language="de")
    recording(audio, f"{FOLDER}/01.mp3", seconds=5.0, album="Der Spiegelsaal", title="Der Spiegelsaal",
              artist="Ida Brandt", track=1)
    first, second = make_library(books, "Books"), make_library(audio, "Recordings")
    scan(first)
    with SessionLocal() as db:
        db.execute(update(Edition).values(grouped_by="hand"))
        db.commit()
    scan(second)
    with SessionLocal() as db:
        catalog.join_twin_works(db)
        db.commit()
        assert len(db.scalars(select(Work.id)).all()) == 2
