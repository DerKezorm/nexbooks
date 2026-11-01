"""What the folders and tags of a real library taught about recognising a book: the series in front of a title, the case
of a series that is no title, an author tag that names a series, parts that carry the number of the part in the album, a
language nobody said, a description that belongs to another book, and a reading that nothing proves. Scan to answer, with
invented books, authors and sources throughout."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Account, AliasClaim, Edition, Person, PersonAlias
from app.services.match import apply
from app.services.match.score import Wanted, decide
from app.services.sources.model import Candidate

from .fake_sources import audible_product, dnb_sru, hc_doc, hc_edition, ol_doc
from .library_helpers import book, books_folder, make_library, recording, scan
from .match_helpers import World, edition_of, entry_for, member
from .test_recognition_folders import parts
from .test_scanner import state


def only_edition(kind: str) -> Edition:
    with SessionLocal() as db:
        row = db.scalar(select(Edition).where(Edition.kind == kind).order_by(Edition.id))
        assert row is not None
        db.expunge(row)
        return row


# ---- A series in front of the title ------------------------------------------------------------------------------


def test_an_ebook_whose_title_carries_its_series_and_number_is_the_book_after_the_dash(
        client: TestClient, operator: Account, world: World) -> None:
    """``Reihe Eins & Zwei 03 - Titel`` in the package file, ``Titel (2009)`` as the folder, ``Titel - Autorin`` as the
    file: the title is ``Titel``, the series the part before, the volume 3. With the book known to Audible as volume 3 of
    that series, in the reading and the abridged one, and to the National Library, it is sure."""
    world.only("audible", "dnb")
    world.fake.dnb = dnb_sru([("D1", "Titel", "Lindqvist, Mara")])
    world.fake.audible = {"de": [
        audible_product("B0TITEL001", "Titel", ["Mara Lindqvist"], 600, series="Reihe Eins & Zwei", sequence="3"),
        audible_product("B0TITEL002", "Titel", ["Mara Lindqvist"], 300, kind="abridged",
                        series="Reihe Eins & Zwei (gekürzt)", sequence="3"),
        audible_product("B0ANDERS01", "Anderer Titel", ["Mara Lindqvist"], 500, series="Reihe Eins & Zwei",
                        sequence="4")]}
    folder = books_folder("prefix")
    book(folder, "Mara Lindqvist/Titel (2009)/Titel - Mara Lindqvist.epub", title="Reihe Eins & Zwei 03 - Titel",
         authors=("Mara Lindqvist",), language="de")
    scan(make_library(folder, "Prefix"))
    edition = only_edition("ebook")
    assert (edition.title, edition.series_name, edition.series_pos) == ("Titel", "Reihe Eins & Zwei", 3.0)
    row = entry_for("Titel")
    assert (row.state, row.reason) == ("sure", ""), (row.state, row.reason, [p["why"] for p in row.proposals or []])


@pytest.mark.parametrize("title,expected,series,pos", [
    ("Walter Hartung 04 - Rachefrühling", "Rachefrühling", "Walter Hartung", 4.0),
    ("Carl Moor 01 (Erbarmen)", "Erbarmen", "Carl Moor", 1.0),
])
def test_the_series_in_front_of_the_title_goes_when_the_folder_and_the_file_bear_the_title_out(
        title: str, expected: str, series: str, pos: float) -> None:
    folder = books_folder("prefix-more")
    book(folder, f"Mara Lindqvist/{expected} (2020)/{expected} - Mara Lindqvist.epub", title=title,
         authors=("Mara Lindqvist",), language="de")
    scan(make_library(folder, "Prefix"))
    edition = only_edition("ebook")
    assert (edition.title, edition.series_name, edition.series_pos) == (expected, series, pos)


def test_a_title_with_a_number_stays_a_title_when_nothing_bears_the_rest_out() -> None:
    folder = books_folder("number-title")
    book(folder, "Mara Lindqvist/Lantern (2020)/lantern.epub", title="Lantern 451: A Story",
         authors=("Mara Lindqvist",), language="en")
    scan(make_library(folder, "Number"))
    edition = only_edition("ebook")
    assert edition.title.startswith("Lantern 451") and edition.series_name == ""


# ---- The case of a series is no title ----------------------------------------------------------------------------


def test_an_album_that_only_says_which_case_of_a_series_it_is_leaves_the_title_to_the_folder() -> None:
    folder = books_folder("ordinal")
    parts(folder, "Jean Bannalec/Bretonischer Ruhm - Kommissar Dupins zwölfter Fall (2025)/Audio/{number:03d}.mp3", 2,
          album="Kommissar Dupins zwölfter Fall", artist="Jean Bannalec")
    scan(make_library(folder, "Ordinal"))
    edition = only_edition("audio")
    assert (edition.title, edition.series_name, edition.series_pos) == ("Bretonischer Ruhm", "Kommissar Dupin", 12.0)


# ---- Authors that are none ---------------------------------------------------------------------------------------


def test_an_author_tag_that_is_the_name_of_the_series_or_the_book_gives_way_to_the_person_at_the_top() -> None:
    folder = books_folder("author-tag")
    parts(folder, "Volker Falkenau/Kluftfels (2018)/{number:02d}. Kommissar Kluftfels - Kluftfels.mp3", 2,
          album="Kluftfels", artist="Kommissar Kluftfels")
    parts(folder, "Volker Falkenau/Nebelnacht (2010)/Audio/{number:02d}.mp3", 2, album="Volker Falkenau",
          artist="Nebelnacht")
    scan(make_library(folder, "Authors"))
    found = {work["title"]: work for work in state()["works"]}
    assert set(found) == {"Kluftfels", "Nebelnacht"}
    assert found["Kluftfels"]["authors"] == ["Volker Falkenau"] and found["Nebelnacht"]["authors"] == ["Volker Falkenau"]


def test_a_tag_that_names_another_person_than_the_folder_is_left_alone() -> None:
    folder = books_folder("author-other")
    parts(folder, "Volker Falkenau/Sammelband (2018)/Audio/{number:02d}.mp3", 2, album="Sammelband", artist="Anna Brandt")
    scan(make_library(folder, "Authors"))
    assert state()["works"][0]["authors"] == ["Anna Brandt"]


def test_the_tag_says_one_writes_as_another_and_that_is_the_proof_of_the_pen_name() -> None:
    folder = books_folder("pen")
    parts(folder, "Mara Falk/Sommerglas (2014)/Audio/{number:02d}.mp3", 2, album="Sommerglas",
          artist="Mara Falk schreibt als Mara Tiefenbach", narrator="Ben Hollister")
    parts(folder, "Mara Tiefenbach/Winterglas (2015)/Audio/{number:02d}.mp3", 2, album="Winterglas",
          artist="Mara Tiefenbach")
    scan(make_library(folder, "Pen"))
    with SessionLocal() as db:
        # The tag is the proof: both names are one person, the other name is shown beside it; the evidence stays with the
        # recording (it goes when the tag goes).
        assert db.scalars(select(AliasClaim.source)).all() == ["tags"]
        assert len(db.scalars(select(Person.name).where(Person.name.like("Mara%"))).all()) == 1
        assert db.scalar(select(func.count(PersonAlias.id))) == 1


def test_a_reader_named_in_the_artist_tag_is_the_narrator_and_no_author() -> None:
    folder = books_folder("reads")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas",
          artist="Ben Hollister liest Rune Falkner")
    scan(make_library(folder, "Reads"))
    work = state()["works"][0]
    assert work["authors"] == ["Rune Falkner"]
    assert only_edition("audio") is not None


# ---- The parts of a recording ------------------------------------------------------------------------------------


def test_files_named_with_the_number_of_the_part_are_one_recording_whatever_their_albums_say() -> None:
    """A tool wrote the number of the part into the album too, so the albums differ and the parts looked like six books."""
    folder = books_folder("parts-albums")
    for number in range(1, 4):
        recording(folder, f"Charlotte Lund/Sturmglas (1991)/1.-.Sturmglas/Audio/Charlotte Lund-Sturmglas-{number}v3.mp3",
                  seconds=30.0, track=number, album=f"Sturmglas {number}v3", artist="Charlotte Lund")
    scan(make_library(folder, "Parts"))
    work = state()["works"]
    assert len(work) == 1 and len(work[0]["editions"]) == 1 and len(work[0]["editions"][0]["files"]) == 3
    assert work[0]["title"] == "Sturmglas"


def test_the_name_of_a_box_is_no_series_and_every_numbered_folder_in_it_is_a_book() -> None:
    folder = books_folder("box")
    for number, title in ((1, "Erstes Echo"), (2, "Zweites Echo")):
        parts(folder, f"Mara Falk/Echo 5 - Echoland (2024)/0{number} - {title}/{{number:02d}}.mp3", 2,
              artist="Mara Falk", album=f"Echo {number}")
    scan(make_library(folder, "Box"))
    found = {work["title"]: work for work in state()["works"]}
    assert set(found) == {"Erstes Echo", "Zweites Echo"}
    with SessionLocal() as db:
        assert {e.series_name for e in db.scalars(select(Edition))} == {"Echo"}
        assert sorted(e.series_pos for e in db.scalars(select(Edition))) == [1.0, 2.0]


# ---- A language nobody said --------------------------------------------------------------------------------------


def test_a_recording_with_no_language_takes_the_one_of_the_ebook_of_its_work_and_says_so(
        client: TestClient, operator: Account) -> None:
    folder = books_folder("lang-twin")
    book(folder, "Rune Falkner/Nachtglas (2019)/nachtglas.epub", title="Nachtglas", authors=("Rune Falkner",),
         language="de")
    recording(folder, "Rune Falkner/Nachtglas (2019)/Audio/01.mp3", seconds=30.0, album="Nachtglas",
              artist="Rune Falkner")
    library = make_library(folder, "Twin")
    member(library)
    scan(library)
    audio = only_edition("audio")
    assert audio.language == "de"
    work_id = audio.work_id
    shown = client.get(f"/api/v1/works/{work_id}").json()
    by_kind = {e["kind"]: e for e in shown["editions"]}
    assert by_kind["audio"]["language"] == "de" and by_kind["audio"]["language_guess"] is True
    assert by_kind["ebook"]["language_guess"] is False


def test_a_recording_with_no_language_takes_the_language_most_of_the_library_has() -> None:
    folder = books_folder("lang-majority")
    for number in range(1, 4):
        book(folder, f"Autor Eins/Buch {number} ({2010 + number})/buch{number}.epub", title=f"Buch Nummer{number}",
             authors=("Autor Eins",), language="fr")
    recording(folder, "Autor Zwei/Anderes (2015)/Audio/01.mp3", seconds=30.0, album="Anderes", artist="Autor Zwei")
    scan(make_library(folder, "Majority"))
    audio = only_edition("audio")
    assert audio.language == "fr"


def test_no_language_is_derived_without_a_clear_majority() -> None:
    folder = books_folder("lang-none")
    for number, language in enumerate(("fr", "de", "en"), start=1):
        book(folder, f"Autor Eins/Buch {number} ({2010 + number})/buch{number}.epub", title=f"Buch Nummer{number}",
             authors=("Autor Eins",), language=language)
    recording(folder, "Autor Zwei/Anderes (2015)/Audio/01.mp3", seconds=30.0, album="Anderes", artist="Autor Zwei")
    scan(make_library(folder, "None"))
    assert only_edition("audio").language == ""


def test_a_language_a_file_gives_is_never_replaced_by_a_derived_one() -> None:
    folder = books_folder("lang-file")
    book(folder, "Rune Falkner/Nachtglas (2019)/nachtglas.epub", title="Nachtglas", authors=("Rune Falkner",),
         language="de")
    recording(folder, "Rune Falkner/Nachtglas (2019)/Audio/01.mp3", seconds=30.0, album="Nachtglas",
              artist="Rune Falkner")
    scan(make_library(folder, "File"))
    assert only_edition("audio").language == "de"


# ---- A description of another book -------------------------------------------------------------------------------

GERMAN_BLURB = ("Ein kleines Dorf am Meer, in dem nichts so ist, wie es scheint. Als die junge Ärztin dort ankommt, "
                "merkt sie schnell, dass die Menschen etwas verbergen und dass sie nicht willkommen ist.")
ENGLISH_BLURB = ("A small village by the sea where nothing is as it seems. When the young doctor arrives there, she "
                 "soon realises that the people are hiding something and that she is not welcome.")


def scored_of(description: str, language: str = "de") -> Any:
    wanted = Wanted(kind="ebook", title="Nachtglas", authors=["Rune Falkner"], language=language)
    candidate = Candidate(source="hardcover", key="1", title="Nachtglas", authors=["Rune Falkner"],
                          languages=[language], description=description)
    decision = decide(wanted, [candidate])
    assert decision.best is not None
    return decision.best


def test_a_description_in_another_language_than_the_book_is_not_taken() -> None:
    edition = Edition(kind="ebook", language="de")
    values, origins, _ = apply.build_values(edition, scored_of(ENGLISH_BLURB), [])
    assert "description" not in values
    values, origins, _ = apply.build_values(edition, scored_of(GERMAN_BLURB), [])
    assert values["description"] == GERMAN_BLURB and origins["description"] == "hardcover"


def test_a_short_description_or_a_book_of_no_language_is_taken_as_it_is() -> None:
    edition = Edition(kind="ebook", language="de")
    values, _, _ = apply.build_values(edition, scored_of("Ein Roman."), [])
    assert values["description"] == "Ein Roman."
    values, _, _ = apply.build_values(Edition(kind="ebook", language=""), scored_of(ENGLISH_BLURB, language=""), [])
    assert values["description"] == ENGLISH_BLURB


def test_the_next_source_with_a_fitting_description_is_taken_instead() -> None:
    wanted = Wanted(kind="ebook", title="Nachtglas", authors=["Rune Falkner"], language="de")
    english = Candidate(source="hardcover", key="1", title="Nachtglas", authors=["Rune Falkner"], languages=["de"],
                        description=ENGLISH_BLURB)
    german = Candidate(source="dnb", key="2", title="Nachtglas", authors=["Rune Falkner"], languages=["de"],
                       description=GERMAN_BLURB)
    decision = decide(wanted, [english, german])
    assert decision.best is not None and decision.state == "sure"
    values, origins, _ = apply.build_values(Edition(kind="ebook", language="de"), decision.best, decision.support)
    assert values["description"] == GERMAN_BLURB and origins["description"] == "dnb"


# ---- A reading nothing proves ------------------------------------------------------------------------------------


def test_a_recording_two_sources_agree_on_is_sure_for_the_book_and_keeps_its_reading_unproven(
        client: TestClient, operator: Account, world: World) -> None:
    """Neither Hardcover nor Open Library gives a running time, so nothing proves *which* reading the files are; two
    sources that tell the same book make the book sure, and the edition says the reading is not proven."""
    world.only("hardcover", "openlibrary")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(8001, "Nachtglas", ["Rune Falkner"], year=2019)]
    world.fake.hc_editions = {"8001": [hc_edition("Nachtglas", "German")]}
    world.fake.ol = [ol_doc("OL9W", "Nachtglas", ["Rune Falkner"], languages=("ger",), year=2019)]
    folder = books_folder("unproven")
    recording(folder, "Rune Falkner/Nachtglas (2019)/Audio/01.mp3", seconds=120.0, album="Nachtglas",
              artist="Rune Falkner", language="de")
    library = make_library(folder, "Unproven")
    member(library)
    scan(library)
    row = entry_for("Nachtglas")
    assert (row.state, row.reason) == ("sure", ""), (row.state, row.reason)
    audio = only_edition("audio")
    assert audio.variant_uncertain is True and not audio.asin


def test_a_recording_one_source_alone_tells_waits_for_a_person(client: TestClient, operator: Account,
                                                              world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL9W", "Nachtglas", ["Rune Falkner"], languages=("ger",), year=2019)]
    folder = books_folder("one-source")
    recording(folder, "Rune Falkner/Nachtglas (2019)/Audio/01.mp3", seconds=120.0, album="Nachtglas",
              artist="Rune Falkner")
    library = make_library(folder, "One")
    member(library)
    scan(library)
    row = entry_for("Nachtglas")
    assert (row.state, row.reason) == ("maybe", "runtime_unproven"), (row.state, row.reason)


def test_the_reading_of_a_recording_is_set_by_hand_by_a_member_with_manage_only(
        client: TestClient, operator: Account, world: World) -> None:
    folder = books_folder("variant-hand")
    recording(folder, "Rune Falkner/Nachtglas (2019)/Audio/01.mp3", seconds=120.0, album="Nachtglas",
              artist="Rune Falkner")
    library = make_library(folder, "Hand")
    member(library)
    scan(library)
    audio = only_edition("audio")
    assert audio.variant_uncertain is True
    reply = client.patch(f"/api/v1/editions/{audio.id}", json={"variant": "abridged"})
    assert reply.status_code == 200, reply.text
    after = edition_of(audio.title)
    assert (after.variant, after.variant_uncertain) == ("abridged", False)
    shown = client.get(f"/api/v1/works/{audio.work_id}").json()["editions"][0]
    assert shown["variant"] == "abridged" and shown["variant_uncertain"] is False and "variant" in shown["corrected"]
    # Taken back, the reading is unknown again.
    assert client.patch(f"/api/v1/editions/{audio.id}", json={"variant": None}).status_code == 200
    assert edition_of(audio.title).variant_uncertain is True
    assert client.patch(f"/api/v1/editions/{audio.id}", json={"variant": "loud"}).status_code == 422


def test_a_reading_cannot_be_set_for_an_ebook(client: TestClient, operator: Account, world: World) -> None:
    folder = books_folder("variant-ebook")
    book(folder, "Rune Falkner/Nachtglas (2019)/nachtglas.epub", title="Nachtglas", authors=("Rune Falkner",),
         language="de")
    library = make_library(folder, "Hand")
    member(library)
    scan(library)
    ebook = only_edition("ebook")
    assert client.patch(f"/api/v1/editions/{ebook.id}", json={"variant": "abridged"}).status_code == 422


# ---- The same recording twice ------------------------------------------------------------------------------------


def test_recordings_of_one_work_with_the_same_running_time_are_named_as_a_probable_duplicate() -> None:
    from types import SimpleNamespace

    from app.routers.works import duplicate_groups

    def rec(ident: int, minutes: float, kind: str = "audio") -> Any:
        return SimpleNamespace(id=ident, kind=kind, duration=int(minutes * 60))

    assert duplicate_groups([rec(1, 470), rec(2, 461), rec(3, 300), rec(4, 120, "ebook")]) == [[1, 2]]
    assert duplicate_groups([rec(1, 470), rec(2, 490)]) == [], "more than three percent apart"
    assert duplicate_groups([rec(1, 470)]) == []
    assert duplicate_groups([rec(5, 100), rec(6, 101), rec(7, 102), rec(8, 400)]) == [[5, 6, 7]]


def test_the_page_of_a_book_names_the_duplicates_and_changes_nothing(client: TestClient, operator: Account) -> None:
    folder = books_folder("dups")
    recording(folder, "Rune Falkner/Nachtglas (2019)/Audio/01.mp3", seconds=120.0, album="Nachtglas",
              artist="Rune Falkner")
    library = make_library(folder, "Dups")
    member(library)
    scan(library)
    work_id = only_edition("audio").work_id
    assert client.get(f"/api/v1/works/{work_id}").json()["duplicates"] == []


# ---- The title of the folder, when the tags' title is not known -----------------------------------------------------


def test_a_book_the_sources_do_not_know_by_its_title_is_asked_for_by_the_title_of_its_folder(
        client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    asked: list[str] = []

    def answer(params: dict[str, list[str]]) -> list[dict[str, Any]]:
        title = params.get("title", [""])[0]
        asked.append(title)
        if "Ruhm" in title:
            return [ol_doc("OL5W", "Bretonischer Ruhm", ["Jean Bannalec"], languages=("ger",), year=2025)]
        return []

    world.fake.ol_for = answer
    folder = books_folder("alt-title")
    book(folder, "Jean Bannalec/Bretonischer Ruhm (2025)/buch.epub", title="Ein ganz anderer Name",
         authors=("Jean Bannalec",), language="de")
    library = make_library(folder, "Alt")
    member(library)
    scan(library)
    assert any("Ruhm" in title for title in asked) and any("anderer" in title for title in asked)
    assert asked.index(next(t for t in asked if "anderer" in t)) < asked.index(next(t for t in asked if "Ruhm" in t))
    row = entry_for("Ein ganz anderer Name")
    # Found by the title of its folder; one catalogue alone and no ISBN in the file: a proposal for a person.
    assert (row.state, row.reason) == ("maybe", "one_source"), (row.state, row.reason)
    assert row.proposals and row.proposals[0]["c"]["title"] == "Bretonischer Ruhm"


def test_a_book_the_first_question_settles_is_not_asked_a_second_time(client: TestClient, operator: Account,
                                                                      world: World) -> None:
    world.only("openlibrary")
    asked: list[str] = []

    def answer(params: dict[str, list[str]]) -> list[dict[str, Any]]:
        asked.append(params.get("title", [""])[0])
        return [ol_doc("OL5W", "Ein ganz anderer Name", ["Jean Bannalec"], languages=("ger",), year=2025,
                       isbns=("9780000000026",))]

    world.fake.ol_for = answer
    folder = books_folder("alt-title-settled")
    book(folder, "Jean Bannalec/Bretonischer Ruhm (2025)/buch.epub", title="Ein ganz anderer Name",
         authors=("Jean Bannalec",), language="de", isbn="9780000000026")
    library = make_library(folder, "Alt")
    member(library)
    scan(library)
    assert entry_for("Ein ganz anderer Name").state == "sure"
    assert not any("Ruhm" in title for title in asked)


# ---- A cast in the field of the authors ----------------------------------------------------------------------------


def test_a_recording_whose_author_field_names_a_cast_is_taken_for_a_play_and_stays_unproven() -> None:
    folder = books_folder("cast")
    cast = "Rune Falkner; " + "; ".join(f"Sprecher {name}" for name in ("Eins", "Zwei", "Drei", "Vier", "Fuenf"))
    parts(folder, "Rune Falkner/Echoland (2024)/Audio/{number:02d}.mp3", 2, album="Echoland", artist=cast)
    parts(folder, "Rune Falkner/Stille (2020)/Audio/{number:02d}.mp3", 2, album="Stille", artist="Rune Falkner")
    scan(make_library(folder, "Cast"))
    with SessionLocal() as db:
        by_title = {e.title: e for e in db.scalars(select(Edition))}
    assert (by_title["Echoland"].variant, by_title["Echoland"].variant_uncertain) == ("dramatized", True)
    assert (by_title["Stille"].variant, by_title["Stille"].variant_uncertain) == ("unabridged", True)


def test_a_shelf_with_a_name_like_a_person_is_no_author_and_does_not_replace_the_tags() -> None:
    folder = books_folder("shelf-name")
    parts(folder, "Neue Hörbücher/Nachtglas (2019)/{number:02d}. Mara Lindqvist - Nachtglas.mp3", 2, album="Nachtglas",
          artist="Mara Lindqvist")
    scan(make_library(folder, "Shelf"))
    assert state()["works"][0]["authors"] == ["Mara Lindqvist"]


def test_the_number_of_the_part_in_the_file_name_joins_parts_whose_albums_are_all_different() -> None:
    folder = books_folder("parts-other-albums")
    for number, album in enumerate(("Sturmglas: der Anfang", "Sturmglas: die Mitte", "Sturmglas: das Ende"), start=1):
        recording(folder, f"Charlotte Lund/Sturmglas (1991)/Charlotte Lund-Sturmglas-{number}v3.mp3", seconds=30.0,
                  track=number, album=album, artist="Charlotte Lund")
    scan(make_library(folder, "Parts"))
    work = state()["works"]
    assert len(work) == 1 and len(work[0]["editions"]) == 1 and len(work[0]["editions"][0]["files"]) == 3


def test_files_without_the_number_of_a_part_and_with_other_albums_stay_other_books() -> None:
    folder = books_folder("parts-two-books")
    recording(folder, "Charlotte Lund/Sammlung (2000)/erste.mp3", seconds=30.0, album="Erste Geschichte",
              artist="Charlotte Lund")
    recording(folder, "Charlotte Lund/Sammlung (2000)/zweite.mp3", seconds=30.0, album="Zweite Geschichte",
              artist="Charlotte Lund")
    scan(make_library(folder, "Parts"))
    with SessionLocal() as db:
        assert len(db.scalars(select(Edition.id)).all()) == 2, "two albums, no number of a part: two recordings"


def test_a_tag_that_is_the_person_of_the_top_folder_written_shorter_is_left_as_the_tag_has_it() -> None:
    folder = books_folder("author-short")
    parts(folder, "Rune Falkner/Nachtglas (2019)/{number:02d}. R. Falkner - Nachtglas.mp3", 2, album="Nachtglas",
          artist="R. Falkner")
    scan(make_library(folder, "Short"))
    assert state()["works"][0]["authors"] == ["R. Falkner"]


def test_an_album_that_names_the_case_of_a_series_gives_way_to_a_folder_with_no_year() -> None:
    folder = books_folder("ordinal-plain")
    parts(folder, "Jean Bannalec/Bretonischer Ruhm/Audio/{number:02d}.mp3", 2, album="Kommissar Dupins zwölfter Fall",
          artist="Jean Bannalec")
    scan(make_library(folder, "Plain"))
    edition = only_edition("audio")
    assert edition.title == "Bretonischer Ruhm" and (edition.series_name, edition.series_pos) == ("Kommissar Dupin", 12.0)


def test_no_language_is_derived_from_fewer_than_three_books_or_from_less_than_half_of_them() -> None:
    folder = books_folder("lang-few")
    for number in (1, 2):
        book(folder, f"Autor Eins/Buch {number} ({2010 + number})/buch{number}.epub", title=f"Buch Nummer{number}",
             authors=("Autor Eins",), language="fr")
    recording(folder, "Autor Zwei/Anderes (2015)/Audio/01.mp3", seconds=30.0, album="Anderes", artist="Autor Zwei")
    scan(make_library(folder, "Few"))
    assert only_edition("audio").language == "", "two books are no majority"
    other = books_folder("lang-split")
    for number, language in enumerate(("fr", "fr", "fr", "de", "de", "de", "en"), start=1):
        book(other, f"Autor Eins/Buch {number} ({2010 + number})/buch{number}.epub", title=f"Buch Nummer{number}",
             authors=("Autor Eins",), language=language)
    recording(other, "Autor Zwei/Anderes (2015)/Audio/01.mp3", seconds=30.0, album="Anderes", artist="Autor Zwei")
    split = make_library(other, "Split")
    scan(split)
    with SessionLocal() as db:
        assert db.scalar(select(Edition.language).where(Edition.kind == "audio", Edition.library_id == split)) == ""


def test_the_narrators_of_a_product_are_taken_only_when_the_running_time_proves_the_recording() -> None:
    wanted = Wanted(kind="audio", title="Nachtglas", authors=["Rune Falkner"], duration=36000)
    product = Candidate(source="audible", key="B0A", asin="B0A", title="Nachtglas", authors=["Rune Falkner"],
                        narrators=["Ben Hollister"], runtime_min=600, variant="unabridged", languages=["de"])
    proven = decide(wanted, [product])
    assert proven.best is not None and proven.best.proof
    values, _, _ = apply.build_values(Edition(kind="audio", language="de"), proven.best, [])
    assert values["narrators"] == ["Ben Hollister"] and values["variant"] == "unabridged"
    other = Candidate(source="audible", key="B0B", asin="B0B", title="Nachtglas", authors=["Rune Falkner"],
                      narrators=["Sina Voss"], runtime_min=700, variant="abridged", languages=["de"])
    plausible = decide(wanted, [other])
    assert plausible.best is not None and plausible.best.plausible and not plausible.best.proof
    values, _, _ = apply.build_values(Edition(kind="audio", language="de"), plausible.best, [])
    assert "narrators" not in values and "variant" not in values and "asin" not in values


# ---- The language of a text, and the worked-out values of the overlay -----------------------------------------------


def test_the_language_of_a_text_is_told_only_when_the_text_is_long_enough_and_one_language_leads() -> None:
    from app.services.readers.text import guess_language, text_fits_language

    assert guess_language("Der Mann ist nicht da und die Frau auch nicht") == "", "too short"
    assert guess_language(GERMAN_BLURB) == "de" and guess_language(ENGLISH_BLURB) == "en"
    mixed = "the and of is that der die das und ist nicht the and of is that der die das und ist nicht zu mit"
    assert guess_language(mixed) == "", "two languages lead alike"
    assert text_fits_language(ENGLISH_BLURB, "") and text_fits_language("", "de")
    assert text_fits_language(GERMAN_BLURB, "de") and not text_fits_language(GERMAN_BLURB, "en")
    assert text_fits_language(GERMAN_BLURB, "DE-at"), "the first two letters of the language of the book"


def test_a_value_worked_out_survives_a_match_and_never_replaces_what_a_person_or_a_file_said() -> None:
    from app.services.match import overlay

    guessed = overlay.with_guess(None, "language", "de")
    assert overlay.is_guess(guessed, "language")
    # A match replaces what sources said, not what was worked out.
    after = overlay.with_source(guessed, {"description": "Text"}, origins={"description": "dnb"})
    assert overlay.is_guess(after, "language") and after["description"]["v"] == "Text"
    # A person's word is never overwritten; a source's word is not either.
    by_hand = overlay.with_hand(None, {"language": "en"})
    assert overlay.with_guess(by_hand, "language", "de")["language"]["v"] == "en"
    assert not overlay.is_guess(overlay.with_guess(by_hand, "language", "de"), "language")
    by_source = overlay.with_source(None, {"language": "fr"}, origins={"language": "dnb"})
    assert overlay.with_guess(by_source, "language", "de")["language"]["v"] == "fr"
    # Nothing to work out takes a guess back; a person's correction is taken back with its own call.
    assert overlay.with_guess(guessed, "language", "") == {}


def test_a_series_after_the_title_in_the_tags_or_the_folder_is_the_series_and_the_volume() -> None:
    folder = books_folder("suffix")
    book(folder, "Mara Lindqvist/Tiefe Wunden (2012)/tiefe.epub", title="Tiefe Wunden (Anselm & Quist 3)",
         authors=("Mara Lindqvist",), language="de")
    parts(folder, "Mara Lindqvist/Schändung - Carl Moor, Sonderdezernat Q - Band 2 (2025)/Audio/{number:02d}.mp3", 2,
          album="Carl Moor, Sonderdezernat Q", artist="Mara Lindqvist")
    scan(make_library(folder, "Suffix"))
    with SessionLocal() as db:
        found = {e.title: (e.series_name, e.series_pos) for e in db.scalars(select(Edition))}
    assert found == {"Tiefe Wunden": ("Anselm & Quist", 3.0), "Schändung": ("Carl Moor, Sonderdezernat Q", 2.0)}


def test_a_language_a_source_names_replaces_one_that_was_only_worked_out() -> None:
    from app.services.match import overlay

    wanted = Wanted(kind="audio", title="Nachtglas", authors=["Rune Falkner"], duration=36000)
    product = Candidate(source="audible", key="B0A", asin="B0A", title="Nachtglas", authors=["Rune Falkner"],
                        narrators=["Ben Hollister"], runtime_min=600, variant="unabridged", languages=["en"])
    best = decide(wanted, [product]).best
    assert best is not None
    guessed = Edition(kind="audio", language="de", overlay=overlay.with_guess(None, "language", "de"))
    values, origins, _ = apply.build_values(guessed, best, [])
    assert values["language"] == "en" and origins["language"] == "audible"
    told = Edition(kind="audio", language="de", overlay=overlay.with_source(None, {"language": "de"}, origins={"language": "dnb"}))
    values, _, _ = apply.build_values(told, best, [])
    assert "language" not in values, "a language a file or a source told stays"
