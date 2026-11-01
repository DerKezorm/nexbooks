"""What reading the libraries decides without a person, with real files: a folder of a series is no proof of one book,
a folder and the album artist outweigh the artist, the text of an e-book outweighs its package only where the short
words tell the languages apart, and two works that were there already and become one are told, with the way back.
All names, titles and texts are invented."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import AUTHOR, NARRATOR, Credit, Edition, Person, ProcureHistory, ProcureNotice, Work
from app.services import catalog, libraries

from .conftest import new_client
from .library_helpers import book, books_folder, make_library, operator_account, recording, run_jobs, scan

GERMAN = ("Der Wind kam über die Felder, und sie ging mit dem Hund nach Hause, weil es schon spät war. Er sagte ihr "
          "nicht, dass er die Briefe noch hatte, und sie fragte auch nicht danach. ")
PORTUGUESE = ("Na manhã seguinte o capitão acordou cedo e foi até o convés. O mar estava calmo e o céu prometia um dia "
              "de bom tempo. Ele chamou o imediato e disse que queria partir antes do meio-dia, porque a maré iria "
              "mudar. O homem respondeu que a tripulação ainda dormia, mas que daria ordem para acordar todos. ")


def works() -> list[set[str]]:
    with SessionLocal() as db:
        found: dict[int, set[str]] = {}
        for work_id, kind in db.execute(select(Edition.work_id, Edition.kind)):
            found.setdefault(work_id, set()).add(kind)
        return sorted(found.values(), key=sorted)


def read_all_again(*libraries_: int) -> None:
    """"Alles neu einlesen" for each library."""
    for library in libraries_:
        with SessionLocal() as db:
            libraries.start_scan(db, library, everything=True)
        run_jobs()


SERIES_FOLDERS: list[tuple[str, str, str, dict[str, Any]]] = [
    # The folder is the name of the series, a volume of it in each library.
    ("Ida Brandt/Spiegelwelt", "Spiegelwelt und die Kammer der Uhren", "Spiegelwelt und der Orden der Glocken",
     {"series": "Spiegelwelt", "series_pos": 2.0}),
    # The folder is the name of the series of the e-book, the recording says no volume: nothing proves the same one.
    ("Ida Brandt/Spiegelwelt", "Brandt, Ida - Spiegelwelt 02", "Spiegelwelt", {"series": "Spiegelwelt", "series_pos": 2.0}),
    ("Ida Brandt/Der Herr der Sterne", "Der Herr der Sterne - Die Gefährtin", "Der Herr der Sterne - Die zwei Brücken",
     {}),
    ("Ida Brandt/Kranich", "Die Kranich-Chroniken", "Die Kranich-Offenbarung", {}),
    ("Ida Brandt/Die Spiele von Arkos", "Die Spiele von Arkos: Gefährliche Liebe", "Die Spiele von Arkos - Flammender Zorn",
     {}),
]


@pytest.mark.parametrize(("folder", "ebook", "album", "more"), SERIES_FOLDERS)
def test_a_folder_of_a_series_with_a_volume_in_each_library_holds_two_books(folder: str, ebook: str, album: str,
                                                                           more: dict[str, Any]) -> None:
    books, audio = books_folder("series-folder-e"), books_folder("series-folder-a")
    book(books, f"{folder}/band.epub", title=ebook, authors=("Ida Brandt",), language="de", prose=GERMAN, **more)
    for track in (1, 2):
        recording(audio, f"{folder}/{track:02d}.mp3", seconds=5.0, album=album, title=f"Kapitel {track}",
                  artist="Ida Brandt", track=track)
    first, second = make_library(books, "E-Books", "ebook"), make_library(audio, "Recordings", "audio")
    scan(first)
    scan(second)
    assert works() == [{"audio"}, {"ebook"}]
    read_all_again(first, second)
    assert works() == [{"audio"}, {"ebook"}]


def test_a_book_of_the_work_in_words_keeps_the_folders_apart() -> None:
    books, audio = books_folder("folder-not-work-e"), books_folder("folder-not-work-a")
    folder = "Ida Brandt/Der Spiegelsaal (2019)"
    book(books, f"{folder}/saal.epub", title="Der Spiegelsaal - Die Graphic Novel", authors=("Ida Brandt",),
         language="de", prose=GERMAN)
    recording(audio, f"{folder}/01.mp3", seconds=5.0, album="Der Spiegelsaal", title="Der Spiegelsaal",
              artist="Ida Brandt", track=1)
    scan(make_library(books, "E-Books", "ebook"))
    scan(make_library(audio, "Recordings", "audio"))
    assert works() == [{"audio"}, {"ebook"}]


def people_of(kind: str) -> dict[str, list[str]]:
    with SessionLocal() as db:
        edition = db.scalars(select(Edition).where(Edition.kind == kind)).one()
        found: dict[str, list[str]] = {AUTHOR: [], NARRATOR: []}
        for role, name in db.execute(select(Credit.role, Person.name).join(Person, Person.id == Credit.person_id)
                                     .where(Credit.edition_id == edition.id).order_by(Credit.position)):
            found.setdefault(role, []).append(name)
        return found


def test_the_folder_and_the_album_artist_outweigh_the_artist() -> None:
    audio = books_folder("roles-by-folder")
    for track in (1, 2):
        recording(audio, f"Ida Brandt/Der Spiegelsaal/{track:02d}.mp3", seconds=5.0, album="Der Spiegelsaal",
                  title=f"Kapitel {track}", artist="Mara Leser", albumartist="Ida Brandt", track=track)
    scan(make_library(audio, "Recordings", "audio"))
    found = people_of("audio")
    assert found[AUTHOR] == ["Ida Brandt"] and found[NARRATOR] == ["Mara Leser"]


def test_a_narrator_in_front_of_the_author_in_the_artist_is_the_narrator_when_the_folder_names_the_author() -> None:
    audio = books_folder("roles-in-the-artist")
    for track in (1, 2):
        recording(audio, f"Ada Feldkamp/Die Nebelwacht/{100 + track}-ida_brenner_ada_feldkamp-nebelwacht_kapitel_"
                  f"{track:03d}.mp3", seconds=5.0, album="Die Nebelwacht", title=f"Kapitel {track}",
                  artist="Ida Brenner, Ada Feldkamp", track=track)
    scan(make_library(audio, "Recordings", "audio"))
    found = people_of("audio")
    assert found[AUTHOR] == ["Ada Feldkamp"] and found[NARRATOR] == ["Ida Brenner"]


def test_two_names_in_the_artist_stay_two_authors_when_nothing_else_names_one() -> None:
    audio = books_folder("roles-two-authors")
    for track in (1, 2):
        recording(audio, f"Hoerbuecher/Die Nebelwacht/{track:02d}.mp3", seconds=5.0, album="Die Nebelwacht",
                  title=f"Kapitel {track}", artist="Ida Brenner, Ada Feldkamp", track=track)
    scan(make_library(audio, "Recordings", "audio"))
    assert people_of("audio")[AUTHOR] == ["Ida Brenner", "Ada Feldkamp"]


def variant_of(name: str, seconds: float, **tags: Any) -> tuple[str, bool]:
    audio = books_folder(name)
    recording(audio, f"Moorland/{name}.m4b", seconds=seconds, artist="Ada Feldkamp", **tags)
    scan(make_library(audio, "Recordings", "audio"))
    with SessionLocal() as db:
        edition = db.scalars(select(Edition)).one()
        return edition.variant, edition.variant_uncertain


def test_an_episode_of_a_series_with_the_length_of_one_is_a_play() -> None:
    assert variant_of("episode", 4200.0, album="Moorland - Folge 12: Der Nebel") == ("dramatized", False)


def test_various_readers_make_a_play() -> None:
    assert variant_of("various", 30000.0, album="Die Nebelwacht", narrator="div.") == ("dramatized", False)


def test_a_long_recording_named_with_a_number_is_no_play_by_that() -> None:
    assert variant_of("long", 30000.0, album="Moorland - Folge 12: Der Nebel")[0] == "unabridged"


def test_without_the_folder_the_artist_stays_the_author() -> None:
    audio = books_folder("roles-no-folder")
    for track in (1, 2):
        recording(audio, f"Hoerbuecher/Der Spiegelsaal/{track:02d}.mp3", seconds=5.0, album="Der Spiegelsaal",
                  title=f"Kapitel {track}", artist="Ida Brandt", albumartist="Mara Leser", track=track)
    scan(make_library(audio, "Recordings", "audio"))
    assert people_of("audio")[AUTHOR] == ["Ida Brandt"]


def language_of_the_book() -> str:
    with SessionLocal() as db:
        return db.scalars(select(Edition.language)).one()


@pytest.mark.parametrize(("package", "prose", "expected"), [
    # Tools write ``en`` into German books: the text says German beyond doubt.
    ("en", GERMAN * 3, "de"),
    # Portuguese is read as Spanish by the short words: the package stays.
    ("pt", PORTUGUESE * 3, "pt"),
    # A text the short words read as Spanish may be Portuguese, Catalan or Galician: it never outweighs a package.
    ("en", PORTUGUESE * 3, "en"),
    # Neighbours the short words may mix up: the package stays.
    ("nl", GERMAN * 3, "nl"),
])
def test_the_text_outweighs_the_package_only_where_the_words_tell_the_languages_apart(package: str, prose: str,
                                                                                       expected: str) -> None:
    books = books_folder(f"text-language-{package}")
    book(books, "Ida Brandt/Der Spiegelsaal/saal.epub", title="Der Spiegelsaal", authors=("Ida Brandt",),
         language=package, prose=prose, text_bytes=20000)
    scan(make_library(books, "E-Books", "ebook"))
    assert language_of_the_book() == expected


def test_two_works_that_were_there_and_become_one_are_told_with_the_way_back(monkeypatch: pytest.MonkeyPatch) -> None:
    books, audio = books_folder("told-join-e"), books_folder("told-join-a")
    folder = "Ida Brandt/Der Spiegelsaal (2019)"
    book(books, f"{folder}/saal.epub", title="Brandt, Ida - Spiegelsaal 03", authors=("Ida Brandt",), language="de",
         prose=GERMAN)
    recording(audio, f"{folder}/01.mp3", seconds=5.0, album="Der Spiegelsaal", title="Der Spiegelsaal",
              artist="Ida Brandt", track=1)
    first, second = make_library(books, "E-Books", "ebook"), make_library(audio, "Recordings", "audio")
    # The rules of an older version kept them apart.
    monkeypatch.setattr(catalog, "_folder_twins", lambda db, rows: [])
    scan(first)
    scan(second)
    assert works() == [{"audio"}, {"ebook"}]
    monkeypatch.undo()
    with SessionLocal() as db:
        # A join of a work made in the run itself is no news (a recording that came to its e-book).
        assert catalog.join_twin_works(db, since=clock.now() - timedelta(days=1))["works_joined"] == 1
        db.flush()
        assert db.scalars(select(ProcureHistory)).all() == []
        assert db.scalars(select(ProcureNotice)).all() == []
        db.rollback()
        counts = catalog.join_twin_works(db, since=clock.now() + timedelta(seconds=1))
        db.commit()
        assert counts["works_joined"] == 1
        [line] = db.scalars(select(ProcureHistory)).all()
        [note] = db.scalars(select(ProcureNotice)).all()
        moved = db.get(Edition, line.note["edition_id"])
        assert line.result == "joined" and moved is not None and moved.work_id == line.work_id
        assert note.code == "works_joined" and note.edition_id == moved.id
        assert note.user_id == operator_account().id
        work_count = len(db.scalars(select(Work.id)).all())
    assert works() == [{"audio", "ebook"}] and work_count == 1
    with new_client(operator_account()) as ops:
        [entry] = ops.get("/api/v1/procure/history").json()["entries"]
        assert entry["result"] == "joined" and entry["can_split"] is True
        assert [item["code"] for item in ops.get("/api/v1/procure/problems").json()["notes"]] == ["works_joined"]
        assert ops.post(f"/api/v1/editions/{moved.id}/split").status_code == 200
        [entry] = ops.get("/api/v1/procure/history").json()["entries"]
        assert entry["can_split"] is False
    assert works() == [{"audio"}, {"ebook"}]


@pytest.mark.parametrize(("language", "expected"), [("de", "Teil 1"), ("", "Part 1")])
def test_a_chapter_without_a_name_is_called_in_the_language_of_the_person_when_the_book_has_none(
        language: str, expected: str) -> None:
    audio = books_folder(f"part-language-{language or 'none'}")
    for track in (1, 2):
        recording(audio, f"Ida Brandt/Der Spiegelsaal/{track:02d}.mp3", seconds=5.0, album="Der Spiegelsaal",
                  title="Der Spiegelsaal", artist="Ida Brandt", track=track)
    scan(make_library(audio, "Recordings", "audio"))
    with SessionLocal() as db:
        edition = db.scalars(select(Edition)).one()
        assert edition.language == ""
        work_id = edition.work_id
    with new_client(operator_account()) as ops:
        assert ops.put("/api/v1/me/language", json={"language": language}).status_code == 200
        [shown] = ops.get(f"/api/v1/works/{work_id}").json()["editions"]
        assert [chapter["title"] for chapter in shown["chapters"]] == [expected, expected.replace("1", "2")]
