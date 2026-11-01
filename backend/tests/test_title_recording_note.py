"""A title without the note a tool or a shop puts at its end (the reader, the form): the work is called by its title, and
the reader the note names is the narrator of a recording that has none. Only invented books."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import EDITION_AUDIO, EDITION_EBOOK, Credit, Person
from app.services import catalog
from app.services.readers.names import strip_recording_note

from .library_helpers import books_folder, make_library, recording, scan

TITLE = "Die Laternenstrasse"


@pytest.mark.parametrize("note", [
    "(gelesen von Ida Leser)", "[Gelesen von Ida Leser]", "- gelesen von Ida Leser", ", gelesen von Ida Leser",
    ". Gelesen von Ida Leser", "(read by Ida Leser)", "- narrated by Ida Leser", "(Sprecher: Ida Leser)",
    "(Sprecherin: Ida Leser)", "(vorgelesen von Ida Leser)", "(gesprochen von Ida Leser)", "(lu par Ida Leser)",
    "(gelezen door Ida Leser)", "(ungekürzte Lesung von Ida Leser)", "(Lesung mit Ida Leser)",
    "(gelesen von Ida Leser, ungekürzt)", "- ungekürzt - gelesen von Ida Leser", "(ungekürzt gelesen von Ida Leser)",
])
def test_a_note_with_a_reader_goes_and_the_reader_is_kept(note: str) -> None:
    assert strip_recording_note(f"{TITLE} {note}".replace(" ,", ",").replace(" .", ".")) == (TITLE, ["Ida Leser"])


@pytest.mark.parametrize("note", [
    "(ungekürzt)", "(ungekürzte Lesung)", "(Hörbuch)", "[Hoerbuch]", "(Audiobook)", "(unabridged)", "- Hörbuch",
    "(Hörbuch-Download)", "- ungekürzte Lesung", "(MP3-CD)", "(ungekürzt, Hörbuch)",
])
def test_a_note_of_the_form_goes(note: str) -> None:
    assert strip_recording_note(f"{TITLE} {note}") == (TITLE, [])


def test_two_readers_are_two_names() -> None:
    assert strip_recording_note(f"{TITLE} (gelesen von Ida Leser und Ben Sprecher)") == (TITLE, ["Ida Leser", "Ben Sprecher"])
    assert strip_recording_note(f"{TITLE} (read by Ida Leser & Ben Sprecher)") == (TITLE, ["Ida Leser", "Ben Sprecher"])


def test_a_note_after_a_note_goes_too() -> None:
    assert strip_recording_note(f"{TITLE} (Hörbuch) - gelesen von Ida Leser") == (TITLE, ["Ida Leser"])


@pytest.mark.parametrize("title", [
    "Der Leser", "Der Vorleser", "Das Hörbuch", "Hörbuch", "Lesung", "Gelesen von Ida Leser", "(Hörbuch)", "Die Lesung",
    "Faust - Eine Lesung", "Der Mann, der gelesen wurde", "Titel (Teil 2)", "Titel (gekürzt)", "Titel (Hörspiel)",
    "Titel (gelesen von Ida Leser und 2 weiteren)", "Titel (gelesen von Ida Leser, Teil 2)", "Titel - gelesen von",
    "Titel (Sprecher)", "Das gelesen von Buch",
])
def test_a_title_that_is_the_word_or_a_note_that_says_more_stays(title: str) -> None:
    assert strip_recording_note(title) == (title, [])


def audio(album: str, narrators: tuple[str, ...] = ()) -> SimpleNamespace:
    probe: dict[str, Any] = {"format": "mp3", "duration": 600.0, "album": album, "title": album,
                             "authors": ["Rune Falkner"], "narrators": list(narrators), "language": "de"}
    return SimpleNamespace(path="Rune Falkner/Die Laternenstrasse/01.mp3", format="mp3", size=600 * 12000, origin="source",
                           unit="", probe=probe, duration_ms=600000)


def ebook(title: str) -> SimpleNamespace:
    return SimpleNamespace(path="Rune Falkner/Die Laternenstrasse/book.epub", format="epub", duration_ms=0,
                           probe={"title": title, "authors": ["Rune Falkner"], "format": "epub"})


@pytest.mark.parametrize(("written", "title"), [
    ("Die Laternenstrasse (Diogenes Taschenbuch, 22953)", "Die Laternenstrasse"),
    ("Die Laternenstrasse: Fischer Klassik PLUS", "Die Laternenstrasse"),
    ("Die Laternenstrasse: Deutsche Ausgabe", "Die Laternenstrasse"),
    ("Die Laternenstrasse (Kurt Beispiel: Übers.)", "Die Laternenstrasse"),
    ("Die Laternenstrasse (ungekuerzt Hoer gut Klassiker)", "Die Laternenstrasse"),
    ("Die Laternenstrasse: Ein Klassiker neu erzählt", "Die Laternenstrasse: Ein Klassiker neu erzählt"),
    ("Die Laternenstrasse (Eine Reise)", "Die Laternenstrasse (Eine Reise)"),
    ("Die Laternenstrasse (Hörspiel)", "Die Laternenstrasse (Hörspiel)"),
])
def test_a_publishers_line_a_translator_and_the_form_are_no_part_of_a_title(written: str, title: str) -> None:
    assert catalog.build_meta(EDITION_EBOOK, [ebook(written)], [], top="Rune Falkner").title == title


def test_the_label_of_a_disc_is_no_author_where_the_folder_names_one() -> None:
    row = audio("Die Laternenstrasse")
    row.probe["authors"] = ["VORL 1"]
    assert catalog.build_meta(EDITION_AUDIO, [row], [], top="Rune Falkner").authors == ["Rune Falkner"]


def test_the_number_of_a_disc_behind_the_name_is_no_part_of_the_author() -> None:
    row = audio("Die Laternenstrasse")
    row.probe["authors"] = ["Rune Falkner 2"]
    assert catalog.build_meta(EDITION_AUDIO, [row], [], top="Rune Falkner").authors == ["Rune Falkner"]
    assert catalog.build_meta(EDITION_AUDIO, [row], [], top="").authors == ["Rune Falkner"]


def test_a_scan_takes_the_folder_for_the_author_over_the_label_of_a_disc(client: Any, operator: Any) -> None:
    folder = books_folder("disc-marks")
    recording(folder, "Rune Falkner/Nebelchor/01 - Die Laternenstrasse.mp3", seconds=4.0, album="Die Laternenstrasse",
              artist="VORL 1", title="01")
    library = make_library(folder, "Hoeren")
    scan(library)
    with SessionLocal() as db:
        names = list(db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
            Credit.role == "author")))
    assert set(names) == {"Rune Falkner"}


def test_a_recording_is_called_by_its_title_and_its_reader_is_the_narrator() -> None:
    meta = catalog.build_meta(EDITION_AUDIO, [audio(f"{TITLE} (gelesen von Ida Leser)")], [], top="Rune Falkner")  # type: ignore[arg-type]
    assert meta.title == TITLE
    assert meta.narrators == ["Ida Leser"]


def test_a_narrator_the_tags_name_is_kept_and_the_note_only_goes() -> None:
    meta = catalog.build_meta(EDITION_AUDIO, [audio(f"{TITLE} (gelesen von Ida Leser)", ("Ben Sprecher",))], [],  # type: ignore[arg-type]
                              top="Rune Falkner")
    assert meta.title == TITLE
    assert meta.narrators == ["Ben Sprecher"]


def test_a_book_has_no_narrator_and_loses_the_note_too() -> None:
    meta = catalog.build_meta(EDITION_EBOOK, [ebook(f"{TITLE} (gelesen von Ida Leser)")], [], top="Rune Falkner")  # type: ignore[arg-type]
    assert meta.title == TITLE
    assert meta.narrators == []


def test_the_form_of_a_book_is_no_part_of_its_title_but_the_variant_is_still_read() -> None:
    meta = catalog.build_meta(EDITION_AUDIO, [audio(f"{TITLE} (ungekürzt)")], [], top="Rune Falkner")  # type: ignore[arg-type]
    assert meta.title == TITLE
    assert meta.variant == "unabridged"


def test_a_title_that_names_a_kind_after_the_note_keeps_working() -> None:
    meta = catalog.build_meta(EDITION_EBOOK, [ebook(f"{TITLE} - Roman (Hörbuch)")], [], top="Rune Falkner")  # type: ignore[arg-type]
    assert meta.title == TITLE


def test_the_books_that_are_in_get_the_new_rule() -> None:
    from app.services import scanner

    # Raised with the rule: an installation that put its books together by the generation before is put together again.
    assert scanner.ASSEMBLE_REV >= 8


@pytest.mark.parametrize("title", ["- gelesen von Ida Leser", ", Hörbuch", "(gelesen von Ida Leser)", "[ungekürzt]"])
def test_a_title_that_is_only_a_note_keeps_its_note_and_names_no_reader(title: str) -> None:
    assert strip_recording_note(title) == (title, [])
