"""The catalogue of an author's works made right before it orders by itself: people of one name,
the kind of a work, series, works that were two, the cost of building, a catalogue that thins out while a source is
away. Every name, title and number is invented; the answers of the sources are in ``tests/fixtures/oeuvre``."""

from __future__ import annotations

import random
import time
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, CatalogPerson, CatalogWork
from app.services import series_names
from app.services.match import score as match_score
from app.services.oeuvre import kinds, people, store, works
from app.services.oeuvre.view import same_title
from app.services.readers.names import compare_key, keys_alike, titles_alike
from app.services.sources import authorq, bnf, config, dnb, libris, xmlsafe
from app.services.sources.model import Candidate, Entry

from .oeuvre_fakes import Sources


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Sources:
    found = Sources(monkeypatch)
    with SessionLocal() as db:
        db.add(Account(name="reader-en", display_name="R", role="member", sign_in="password", password_hash="x",
                       language="en"))
        db.commit()
    return found


def e(source: str, title: str, language: str = "en", **values: Any) -> Entry:
    return Entry(source=source, key=f"{source}:{title}:{language}:{id(values)}", title=title, language=language,
                 **values)


def built(entries: list[Entry], language: str = "en", **values: Any) -> dict[str, works.Work]:
    answered: dict[str, set[str]] = {}
    for entry in entries:
        answered.setdefault(entry.language, set()).add(entry.source)
    found = works.build(entries, answered=answered, author_language=language, author="Ida Falkner", **values)
    return {work.title_in(language): work for work in found}


def catalogue(name: str) -> tuple[CatalogPerson, dict[str, CatalogWork]]:
    with SessionLocal() as db:
        row = store.for_name(db, name)
        assert row is not None
        db.commit()
        assert store.build(db, row)
        out = {}
        for work in db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id)):
            for item in (work.titles or {}).values():
                out[item["title"]] = work
        db.expunge_all()
        return row, out


# ---- Two people of one name -----------------------------------------------------------------------------------------


def test_the_books_of_another_person_of_the_name_are_left_out(sources: Sources) -> None:
    """Open Library and Audible know two Tobias Wrens by two numbers, Hardcover files both under one author: the other
    one's book is no work of the catalogue, though two sources name it."""
    row, found = catalogue("Tobias Wren")
    assert "Ledger of Tides" not in found
    assert found["The Glass Harbour"].kind == "roman" and found["Night Ferry"].kind == "roman"
    assert row.sources["namesakes"] >= 1


def test_a_work_no_number_proves_is_unsure_for_a_name_two_people_share(sources: Sources) -> None:
    """Audible put somebody else's book under the writer's number, and Hardcover mixes the two: nothing that keeps the
    people apart proves it, so it is not sure (it is never fetched by itself)."""
    _row, found = catalogue("Tobias Wren")
    teacher = found["The Lantern Teacher"]
    assert (teacher.kind, teacher.why) == ("unsure", "namesake")


def test_the_page_says_that_the_name_is_shared(sources: Sources) -> None:
    from .conftest import make_account, new_client

    catalogue("Tobias Wren")
    with SessionLocal() as db:
        token = db.scalar(select(CatalogPerson.token).where(CatalogPerson.key == "tobias wren"))
    client = new_client(make_account("nora"))
    page = client.get(f"/api/v1/catalog/authors/{token}").json()
    assert page["common_name"] is True
    halloran = client.post("/api/v1/catalog/authors", json={"name": "Edmund Halloran"}).json()
    sources.build()
    assert client.get(f"/api/v1/catalog/authors/{halloran['token']}").json()["common_name"] is False
    client.close()


def wren(source: str, title: str, person: str, **values: Any) -> Entry:
    return e(source, title, person=person, via="Tobias Wren", **values)


def test_another_number_with_the_same_works_is_the_same_person_filed_twice() -> None:
    entries = [wren("openlibrary", "The Glass Harbour", "OL1A"), wren("openlibrary", "Night Ferry", "OL1A"),
               wren("openlibrary", "The Glass Harbour", "OL3A"), wren("openlibrary", "Ledger of Tides", "OL2A"),
               wren("hardcover", "The Glass Harbour", "")]
    sorted_out = people.sort_out(entries)
    kept = {(entry.title, entry.person) for entry in sorted_out.entries}
    assert ("The Glass Harbour", "OL3A") in kept and ("Ledger of Tides", "OL2A") not in kept
    # Two keys of Open Library without years say nothing of the name: one person may be filed twice.
    assert sorted_out.namesakes == 0
    assert all(entry.who == people.SAME for entry in sorted_out.entries if entry.source == "openlibrary")


def test_years_of_life_and_the_numbers_of_a_library_say_that_two_people_share_a_name() -> None:
    entries = [wren("openlibrary", "The Glass Harbour", "OL1A"), wren("openlibrary", "Ledger of Tides", "OL2A")]
    known = [people.Namesake(number="OL1A", works=12, born="1970"), people.Namesake(number="OL2A", works=3, born="1902")]
    assert people.sort_out(entries, known=known).namesakes >= 1
    library = [wren("dnb", "Das gläserne Haff", "118000001", language="de"),
               wren("dnb", "Das gläserne Haff", "118000001", language="de"),
               wren("dnb", "Gezeitenbuch", "118000002", language="de")]
    found = people.sort_out(library)
    assert found.namesakes == 1 and [entry.title for entry in found.entries] == ["Das gläserne Haff"] * 2


def test_the_markets_of_audible_and_the_pen_names_number_people_apart() -> None:
    entries = [wren("audible", "The Glass Harbour", "us:B0A"), wren("audible", "Der gläserne Hafen", "de:B0B",
                                                                    language="de"),
               e("openlibrary", "Rain Fury", person="OL7A", via="Rowan Fell"),
               wren("openlibrary", "The Glass Harbour", "OL1A")]
    found = people.sort_out(entries)
    assert len(found.entries) == 4 and found.namesakes == 0


def test_what_proves_a_work_for_a_shared_name() -> None:
    same = {"who": people.SAME}
    assert people.proven([e("openlibrary", "A", **same)])
    assert people.proven([e("libris", "A", language="sv", **same)])
    assert not people.proven([e("hardcover", "A"), e("audible", "A", **same)])
    assert people.proven([e("bnf", "A", language="fr"), e("audible", "A", language="fr", **same)])
    assert not people.proven([e("bnf", "A", language="fr"), e("hardcover", "A", language="fr")])


def test_the_sources_name_the_number_of_the_person() -> None:
    from app.services.sources import audible, openlibrary

    data = {"docs": [{"key": "/works/OL1W", "title": "The Glass Harbour", "author_name": ["Tobias Wren", "Ann Roe"],
                      "author_key": ["OL800001A", "OL5A"], "language": ["eng"]}]}
    assert [entry.person for entry in openlibrary.author_entries_of(data, "Tobias Wren", "en")] == ["OL800001A"]
    products = [{"asin": "B0WRENPR01", "authors": [{"asin": "B0WRENAU01", "name": "Tobias Wren"},
                                                   {"asin": "B0OTHER001", "name": "Ann Roe"}]}]
    assert audible.author_numbers(products, "Tobias Wren") == {"B0WRENPR01": "B0WRENAU01"}
    record = ('<record xmlns="http://www.loc.gov/MARC21/slim"><leader>00000nam a2200000 c 4500</leader>'
              '<controlfield tag="001">1</controlfield><datafield tag="100" ind1="1" ind2=" ">'
              '<subfield code="0">(DE-588)118000001</subfield><subfield code="0">https://d-nb.info/gnd/118000001'
              '</subfield><subfield code="a">Wren, Tobias</subfield><subfield code="4">aut</subfield></datafield>'
              '<datafield tag="245" ind1="1" ind2="0"><subfield code="a">Das gläserne Haff</subfield></datafield>'
              '</record>')
    xml = ('<?xml version="1.0"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>1'
           f'</numberOfRecords><records><record><recordData>{record}</recordData></record></records>'
           '</searchRetrieveResponse>').encode()
    found, _page, _total = dnb.parse_page(xml)
    assert dnb.entry_of(found[0], "Tobias Wren").person == "118000001"  # type: ignore[union-attr]
    item = {"identifier": "http://libris.kb.se/bib/1", "title": "Glashamnen", "creator": "Wren, Tobias, 1970-",
            "language": "swe", "type": "book"}
    assert libris.entry_of(item, "Tobias Wren", "sv").person == "y1970"  # type: ignore[union-attr]
    assert authorq.person_by_years("Tobias Wren", "Wren, Tobias, 1902-1988") == "y1902"
    assert authorq.person_by_years("Tobias Wren", "Wren, Tobias") == ""
    assert authorq.person_by_years("Tobias Wren", "Wren, Ann, 1970-") == ""
    assert bnf.SOURCE == "bnf"


# ---- The kind of a work ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("groups", "says"), [
    (["B"], "fiction"), (["K"], "kids"), (["150"], "nonfiction"), (["323.49"], "nonfiction"),
    # A school edition of an English novel is filed under language; numbers of other schemes say nothing.
    (["420"], ""), (["08A", "810", "820"], ""), (["59"], ""), (["23", "S"], ""), (["2303"], ""), (["741.5"], ""),
    (["12.1A"], ""), (["830"], ""),
])
def test_the_subject_groups_of_the_german_library(groups: list[str], says: str) -> None:
    assert kinds.subject_group_says(groups) == says


def test_a_novel_read_in_schools_stays_a_novel() -> None:
    """Twenty records of school editions with a language group outnumbered the fiction ones and made the novel a book of
    facts."""
    entries = [e("dnb", "Die Spottdrossel", "de", signals={"group": ["810", "B"]}, isbns=[f"97830000{n:05d}"])
               for n in range(6)]
    entries += [e("dnb", "Die Spottdrossel", "de", signals={"group": ["08A", "810"]}) for _ in range(9)]
    entries += [e("dnb", "Die Spottdrossel", "de", signals={"group": ["420"]}, isbns=[f"97830001{n:05d}"])
                for n in range(8)]
    entries += [e("hardcover", "Die Spottdrossel", "de", signals={"lit": 1})]
    assert kinds.kind_of(entries) == ("roman", "")


def test_the_records_of_one_isbn_count_once() -> None:
    reprints = [e("dnb", "Das Haff", "de", signals={"group": ["150"]}, isbns=["9783000000019"]) for _ in range(5)]
    fiction = [e("dnb", "Das Haff", "de", signals={"group": ["B"]}, isbns=[f"978300000{n:04d}"]) for n in (2, 3)]
    assert kinds.signals_of(reprints + fiction).nonfic == 1
    assert kinds.kind_of(reprints + fiction + [e("hardcover", "Das Haff", "de", signals={"lit": 1})])[0] == "roman"


def test_one_record_of_a_set_does_not_make_a_novel_a_box() -> None:
    entries = [e("openlibrary", "Sternenfahrt"), e("audible", "Sternenfahrt", form="audio", runtime_min=600),
               e("hardcover", "Sternenfahrt", compilation=True), e("hardcover", "Sternenfahrt"),
               e("dnb", "Sternenfahrt", "de", signals={"group": ["B"]})]
    assert kinds.kind_of(entries)[0] == "roman"
    assert kinds.kind_of([e("hardcover", "Sternenfahrt", compilation=True), e("openlibrary", "Sternenfahrt")])[0] \
        == "box"
    # A set says so in a title too: two signs.
    assert kinds.kind_of([e("hardcover", "Sternenfahrt", compilation=True), e("openlibrary", "Sternenfahrt"),
                          e("openlibrary", "Sternenfahrt: The Collection"), e("dnb", "Sternenfahrt")])[0] == "box"


def test_a_short_recording_is_no_novel_whatever_the_library_files_it_under() -> None:
    entries = [e("dnb", "Die blaue Gentiane", "de", signals={"group": ["B"]}, isbns=["9783000000101"]),
               e("dnb", "Die blaue Gentiane", "de", signals={"group": ["B"]}, isbns=["9783000000118"]),
               e("hardcover", "Die blaue Gentiane", "de", pages=28),
               e("audible", "Die blaue Gentiane", "de", form="audio", runtime_min=41)]
    assert kinds.kind_of(entries) == ("unsure", "short")


def test_a_title_that_names_two_works_only_when_both_say_something_by_themselves() -> None:
    assert kinds.kind_of([e("openlibrary", "Inspektor Falk ermittelt in Paris")],
                         ["inspektor falk", "falk", "paris"], "inspektor falk ermittelt in paris")[0] != "box"
    assert kinds.kind_of([e("openlibrary", "Das Nebelhaus und Der stille Kai")],
                         ["nebelhaus", "stille kai", "das nebelhaus und der stille kai"],
                         "nebelhaus und der stille kai")[0] == "box"


# ---- Series -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "position", "library"), [
    ("Bastei-Lübbe-Taschenbuch", 1750.0, True), ("Reihe Rororo", 4281.0, True), ("btb", 7427.0, True),
    ("Reader's Digest Hörbuch : Die grossen Bestseller unserer Zeit", None, True),
    ("Svenska Ljud Classica", 9293.0, False), ("Klassiker im Insel Verlag", None, True),
    ("Club-Taschenbuch", None, True), ("Aschendorff-Paperbacks", None, True), ("SmartEdition", 3.0, True),
    ("Die Brigitte-Edition", 14.0, True), ("Ein Buch für die Stadt", 2005.0, True), ("Universal-Bibliothek", 3.0, True),
])
def test_the_lines_of_a_publisher_are_no_series(name: str, position: float | None, library: bool) -> None:
    assert series_names.is_line(name, position, library=library)
    assert match_score.junk_series(Candidate(source="dnb", key="1", title="X", series=name, series_pos=position))


@pytest.mark.parametrize(("name", "authors"), [
    ("O. T. Brandmeiers Bibliothek des Grauens", ()), ("Brandmeier's Library of Night", ()),
    ("Germanski bestseler", ()), ("Meilleures ventes", ()), ("Žepna knjiga", ()), ("Džepna knjiga", ()),
    ("Neobooks", ()), ("Storytel Original", ()), ("Lena Vorlage - Die Hörspieltrilogie", ("Lena Vorlage",)),
    ("Lena Vorlage - Die Trilogie", ("Lena Vorlage",)), ("Gesprochen von Ida Brenner", ()),
    ("Ida Brenner liest", ()), ("Read by Ida Brenner", ()),
])
def test_the_lines_of_shops_and_platforms_are_no_series(name: str, authors: tuple[str, ...]) -> None:
    assert series_names.is_line(name, 2.0, authors=list(authors))


@pytest.mark.parametrize("name", ["Anselm Quist", "Kommissar Lund", "The Tuesday Lantern Club",
                                  "Die Bibliothek der stillen Flüsse", "Ein Fall für Kommissar Lund",
                                  "The Midnight Library Cases", "Lulu und die Wolkenpferde"])
def test_a_series_of_the_author_is_one(name: str) -> None:
    assert not series_names.is_line(name, 2.0, library=True)


def test_two_spellings_and_two_names_of_one_series_are_one() -> None:
    assert series_names.key("Anselm Quist Series") == series_names.key("Anselm Quist (gekürzt)") == "anselm quist"
    entries = []
    for number, (en, de) in enumerate((("The Quiet Tide", "Die stille Flut"), ("Grey Water", "Graues Wasser"),
                                       ("Salt Wind", "Salzwind")), start=1):
        entries += [e("hardcover", en, series="Inspector Vane", series_pos=float(number), work_key=f"h{number}"),
                    e("hardcover", de, "de", series="Inspector Vane", series_pos=float(number), work_key=f"h{number}"),
                    e("openlibrary", en, series="", work_key=f"o{number}"),
                    e("audible", de, "de", series="Kommissar Vane", series_pos=float(number), form="audio"),
                    e("audible", en, series="Inspector Vane Series", series_pos=float(number), form="audio")]
    found = built(entries)
    assert {work.series for work in found.values()} == {"Inspector Vane"}


def test_two_series_that_share_one_place_once_stay_two() -> None:
    """The first book of a cycle within a cycle stands first in both: that alone makes no two names one series."""
    entries = [e("hardcover", "The Colour of Rain", series="Discland", series_pos=1.0, work_key="h1"),
               e("audible", "The Colour of Rain", series="Wizard Tales", series_pos=1.0, form="audio"),
               e("openlibrary", "The Colour of Rain"),
               e("hardcover", "Second Light", series="Discland", series_pos=2.0, work_key="h2"),
               e("openlibrary", "Second Light"),
               e("hardcover", "Sorcery", series="Wizard Tales", series_pos=3.0, work_key="h3"),
               e("openlibrary", "Sorcery")]
    found = built(entries)
    assert found["Second Light"].series == "Discland" and found["Sorcery"].series == "Wizard Tales"


def test_a_series_needs_a_place_and_a_library_line_gives_way() -> None:
    found = built([e("dnb", "Die Laterne", "de", series="Meine Edition", series_pos=None),
                   e("dnb", "Die Laterne", "de", series="Bastei-Lübbe-Taschenbuch", series_pos=1750.0),
                   e("audible", "Die Laterne", "de", series="Kai Lorn", series_pos=2.0, form="audio")], "de")
    assert (found["Die Laterne"].series, found["Die Laterne"].series_pos) == ("Kai Lorn", 2.0)
    alone = built([e("dnb", "Das Ufer", "de", series="Meine Edition"), e("openlibrary", "Das Ufer", "de")], "de")
    assert alone["Das Ufer"].series == ""


def test_two_volumes_of_a_colon_series_are_two_works() -> None:
    found = built([e("openlibrary", "Moorland: Das Haus"), e("hardcover", "Moorland: Das Haus"),
                   e("openlibrary", "Moorland: Der Wald"), e("hardcover", "Moorland: Der Wald")])
    assert set(found) == {"Das Haus", "Der Wald"} and {w.series for w in found.values()} == {"Moorland"}
    # A subtitle of an edition is no volume, and a book of the series' name alone keeps its subtitles.
    edition = built([e("openlibrary", "Glockenschlag: Illustrierte Ausgabe"), e("hardcover", "Glockenschlag: Illustrierte Ausgabe"),
                     e("openlibrary", "Glockenschlag: Der Roman"), e("hardcover", "Glockenschlag: Der Roman")])
    assert len(edition) == 1
    alone = built([e("openlibrary", "Moorland"), e("hardcover", "Moorland"), e("openlibrary", "Moorland: Das Haus"),
                   e("hardcover", "Moorland: Das Haus"), e("openlibrary", "Moorland: Der Wald"),
                   e("hardcover", "Moorland: Der Wald")])
    assert len(alone) == 1


# ---- Works that were two ----------------------------------------------------------------------------------------------


def test_and_and_the_ampersand_are_one_title() -> None:
    found = built([e("openlibrary", "Salt & Ashes"), e("hardcover", "Salt & Ashes"), e("audible", "Salt and Ashes",
                                                                                       form="audio"),
                   e("openlibrary", "Salt and Ashes")])
    assert len(found) == 1
    assert works.title_key("Salz und Asche") == works.title_key("Salz & Asche") == "salz asche"


def test_two_isbns_from_two_sources_join_two_titles_of_one_work() -> None:
    """The German library files the old translation under its title, Hardcover the German editions under the work."""
    joined = built([e("hardcover", "The Ninth Bell", work_key="h1"),
                    e("hardcover", "Die neunte Glocke", "de", work_key="h1",
                      isbns=["9783000000201", "9783000000218", "9783000000225"]),
                    e("dnb", "Die neunte Glocke", "de", isbns=["9783000000225"]),
                    e("openlibrary", "The Ninth Bell"),
                    e("dnb", "Glockenschlag", "de", isbns=["9783000000201"]),
                    e("dnb", "Glockenschlag", "de", isbns=["9783000000218"]),
                    e("audible", "Glockenschlag", "de", form="audio")])
    assert len(joined) == 1


@pytest.mark.parametrize("second", [
    # One number in common proves nothing (a record that lists the number of another book).
    [e("dnb", "Glockenschlag", "de", isbns=["9783000000201"]), e("audible", "Glockenschlag", "de", form="audio")],
    # Two numbers of one source do not either.
    [e("dnb", "Glockenschlag", "de", isbns=["9783000000201", "9783000000218"]),
     e("audible", "Glockenschlag", "de", form="audio")],
    # Two places of one series are two books.
    [e("dnb", "Glockenschlag", "de", isbns=["9783000000201"]), e("dnb", "Glockenschlag", "de", isbns=["9783000000218"]),
     e("audible", "Glockenschlag", "de", form="audio", series="Glocken", series_pos=2.0)],
])
def test_what_does_not_join_two_works(second: list[Entry]) -> None:
    first = [e("hardcover", "Die neunte Glocke", "de", work_key="h1", isbns=["9783000000201", "9783000000218"],
               series="Glocken", series_pos=1.0),
             e("dnb", "Die neunte Glocke", "de", isbns=["9783000000225"]), e("openlibrary", "Die neunte Glocke", "de")]
    if len(second) == 2 and len(second[0].isbns) == 2:
        # (The two numbers of the first work come from the German library as well.)
        first[0] = e("dnb", "Die neunte Glocke", "de", isbns=["9783000000201", "9783000000218"])
    assert len(built(first + second, "de")) == 2


def test_a_shop_that_writes_the_series_into_the_title_makes_no_second_work() -> None:
    found = built([e("dnb", "Das grobe Netz", "de", series="Kommissar Lund", series_pos=2.0),
                   e("audible", "Das grobe Netz", "de", series="Kommissar Lund", series_pos=2.0, form="audio"),
                   e("dnb", "Das grobe Netz. Ein Fall für Kommissar Lund", "de", series="Kommissar Lund",
                     series_pos=2.0),
                   e("audible", "Das grobe Netz. Ein Fall für Kommissar Lund", "de", series="Kommissar Lund",
                     series_pos=2.0, form="audio"),
                   # Another place, or a title that is no start of the other: two works.
                   e("dnb", "Das grobe Netz und mehr", "de", series="Kommissar Lund", series_pos=3.0),
                   e("audible", "Das grobe Netz und mehr", "de", series="Kommissar Lund", series_pos=3.0, form="audio"),
                   e("dnb", "Grauer Hafen", "de", series="Kommissar Lund", series_pos=2.0),
                   e("audible", "Grauer Hafen", "de", series="Kommissar Lund", series_pos=2.0, form="audio"),
                   # One word is too little to say the one title is the other's start.
                   e("dnb", "Nebel", "de", series="Kommissar Lund", series_pos=4.0),
                   e("audible", "Nebel", "de", series="Kommissar Lund", series_pos=4.0, form="audio"),
                   e("dnb", "Nebel über Kai", "de", series="Kommissar Lund", series_pos=4.0),
                   e("audible", "Nebel über Kai", "de", series="Kommissar Lund", series_pos=4.0, form="audio")], "de")
    assert len(found) == 5 and "Das grobe Netz" in found


def test_the_title_of_open_librarys_work_names_the_original() -> None:
    found = built([e("libris", "Kvinnan vid kanalen", "sv"), e("openlibrary", "Kvinnan vid kanalen", "sv", work_key="o1"),
                   e("openlibrary", "Die Frau am Kanal", "de", work_key="o2",
                     signals={"work_title": "Kvinnan vid kanalen"}),
                   e("dnb", "Die Frau am Kanal", "de"), e("audible", "Die Frau am Kanal", "de", form="audio")], "sv")
    assert len(found) == 1 and set(found["Kvinnan vid kanalen"].titles) == {"sv", "de"}
    # A record that names the original title but not its language names the author's.
    named = built([e("libris", "Isvägen", "sv"), e("openlibrary", "Isvägen", "sv"),
                   e("dnb", "Die Eisstraße", "de", original_title="Isvägen"), e("audible", "Die Eisstraße", "de")],
                  "sv")
    assert len(named) == 1


def test_a_series_title_of_open_librarys_work_joins_no_volume_to_the_box() -> None:
    found = built([e("openlibrary", "Wrenfield: The Ash Road", work_key="o1"),
                   e("openlibrary", "Die Aschenstraße", "de", work_key="o1",
                     signals={"work_title": "Wrenfield: The Ash Road"}),
                   e("openlibrary", "Wrenfield: Three Doors", work_key="o2"),
                   e("openlibrary", "Wrenfield: The Salt Plains", work_key="o3"),
                   e("openlibrary", "Wrenfield", work_key="o4", compilation=True),
                   e("hardcover", "Wrenfield", compilation=True), e("hardcover", "The Ash Road"),
                   e("hardcover", "Three Doors"), e("hardcover", "The Salt Plains"), e("dnb", "Die Aschenstraße", "de")])
    assert {"The Ash Road", "Three Doors", "The Salt Plains"} <= set(found)


# ---- The cost of a catalogue -------------------------------------------------------------------------------------


def _random_titles(count: int) -> list[str]:
    words = ["harbour", "glass", "night", "ferry", "salt", "tide", "lantern", "grey", "water", "wind", "moor", "house",
             "door", "road", "bell", "shore", "ninth", "quiet", "cold", "light"]
    pick = random.Random(7)
    titles = []
    for _ in range(count):
        size = pick.randint(1, 4)
        title = " ".join(pick.choice(words) for _ in range(size))
        if pick.random() < 0.3:
            title = title[:-1] if len(title) > 4 else title + "s"
        if pick.random() < 0.1:
            title += f" {pick.randint(1, 5)}"
        titles.append(title)
    return titles


def test_the_near_keys_are_exactly_the_alike_ones() -> None:
    keys = sorted({compare_key(title) for title in _random_titles(400)})
    every = {(a, b) for i, a in enumerate(keys) for b in keys[i + 1:] if titles_alike(a, b)}
    near = works._Near(keys).pairs(works._Pause())
    assert {tuple(sorted(pair)) for pair in near} == {tuple(sorted(pair)) for pair in every}
    for a, b in list(every)[:50]:
        assert keys_alike(a, b) == titles_alike(a, b)


def test_a_large_catalogue_compares_few_pairs_and_gives_the_processor_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """1500 records: every title made a key once, and only keys of the same numbers and a fitting length compared (the
    first version compared every pair, with both keys made anew each time: 20 seconds for a prolific author)."""
    calls: list[float] = []
    compared = [0]
    real_sleep, real_alike = time.sleep, works.keys_alike
    monkeypatch.setattr(works.time, "sleep", lambda seconds: (calls.append(seconds), real_sleep(seconds))[1])

    def counted(a: str, b: str, threshold: float = 0.9) -> bool:
        compared[0] += 1
        return real_alike(a, b, threshold)

    monkeypatch.setattr(works, "keys_alike", counted)
    titles = _random_titles(1500)
    entries = [e(("openlibrary", "hardcover", "audible")[n % 3], title) for n, title in enumerate(titles)]
    keys = len({compare_key(title) for title in titles})
    works.title_key.cache_clear()
    works.group(entries, "en")
    assert compared[0] < keys * (keys - 1) / 2 * 0.4, (compared[0], keys)
    assert works.title_key.cache_info().misses <= len(set(titles))
    assert calls and all(seconds == 0 for seconds in calls)


# ---- A catalogue that thins out while a source is away ------------------------------------------------------------


def test_a_thinner_catalogue_while_a_source_is_away_keeps_the_one_before_and_is_tried_again_in_an_hour(
        sources: Sources) -> None:
    row, before = catalogue("Edmund Halloran")
    assert row.sources.get("retry_after") is None
    sources.days(1.1)
    # Only Audible answers: a handful of recordings for a catalogue of fourteen works.
    for host in ("api.hardcover.app", "services.dnb.de", "openlibrary.org"):
        sources.fake.status[host] = 503
    with SessionLocal() as db:
        again = db.get(CatalogPerson, row.id)
        assert again is not None and store.due(again)
        assert store.build(db, again)
        kept = db.scalars(select(CatalogWork).where(CatalogWork.catalog_person_id == row.id)).all()
        assert len(kept) == len({w.id for w in before.values()})
        assert not store.due(again)
    # An hour on, one source is still away: the catalogue that came out is full enough to stand, and is tried again
    # in an hour; then with every source back it stands for a day.
    sources.advance(3700)
    sources.fake.status = {"api.audible.com": 503}
    with SessionLocal() as db:
        again = db.get(CatalogPerson, row.id)
        assert again is not None and store.due(again)
        assert store.build(db, again) and again.sources.get("retry_after")
        assert "audible" in again.sources["away"] and not store.due(again)
    sources.advance(3700)
    sources.fake.status.clear()
    with SessionLocal() as db:
        again = db.get(CatalogPerson, row.id)
        assert again is not None and store.due(again)
        assert store.build(db, again) and again.sources.get("retry_after") is None
        assert not store.due(again)


# ---- Smaller things -------------------------------------------------------------------------------------------------


def test_the_hosts_of_every_source_are_these_and_no_other() -> None:
    assert config.HOSTS == {
        "hardcover": ("api.hardcover.app", "assets.hardcover.app"),
        "openlibrary": ("openlibrary.org", "covers.openlibrary.org"),
        "audible": ("api.audible.de", "api.audible.com", "api.audible.co.uk", "api.audible.fr", "api.audible.ca",
                    "api.audible.com.au", "api.audible.it", "api.audible.es", "api.audible.co.jp", "api.audible.in",
                    "api.audible.com.br", "m.media-amazon.com"),
        "dnb": ("services.dnb.de",),
        "apple": ("rss.marketingtools.apple.com", "itunes.apple.com", "is1-ssl.mzstatic.com", "is2-ssl.mzstatic.com",
                  "is3-ssl.mzstatic.com", "is4-ssl.mzstatic.com", "is5-ssl.mzstatic.com"),
        "wikidata": ("www.wikidata.org", "commons.wikimedia.org", "upload.wikimedia.org"),
        "bnf": ("catalogue.bnf.fr",),
        "libris": ("libris.kb.se",),
        "kb": ("jsru.kb.nl",),
        "nb": ("api.nb.no",),
    }
    assert config.REDIRECT_HOSTS == {"openlibrary": (("archive.org",), (".archive.org",))}


def test_one_title_holds_the_other_only_both_ways_round() -> None:
    """``Das Ufer 2`` is the title ``Das Ufer`` with a volume as a release name reads it, but not the other way round:
    the rule needs both."""
    assert not same_title("Das Ufer 2", "Das Ufer", "Ida Falkner")
    assert not same_title("Das Ufer", "Das Ufer 2", "Ida Falkner")
    assert same_title("Der Nebelchor (Ein Fall für Anselm Quist 1)", "Der Nebelchor", "Ida Falkner")


def test_xml_that_nests_too_deep_is_refused_before_it_is_parsed() -> None:
    deep = b'<?xml version="1.0"?><searchRetrieveResponse>' + b"<a>" * 200 + b"</a>" * 200 + \
        b"</searchRetrieveResponse>"
    assert xmlsafe.too_deep(deep) and not xmlsafe.too_deep(b"<a><b/><c>x</c></a>")
    for parser in (lambda xml: bnf.parse(xml, "Odile Marchetti"), dnb.parse_page):
        with pytest.raises(ValueError):
            parser(deep)
    big = b"<r>" + b"<a>" * 20000 + b"</a>" * 20000 + b"</r>"
    started = time.perf_counter()
    assert xmlsafe.too_deep(big)
    assert time.perf_counter() - started < 0.5


def test_what_looks_like_tags_inside_cdata_is_text_and_a_cdata_that_never_ends_is_refused() -> None:
    """The German library writes abstracts with ``<br>`` in a CDATA section: such an answer is no deep one."""
    abstract = b"<![CDATA[Abstract: one.<br>Two.<br><br>" + b"<br>" * 100 + b"]]>"
    answer = b"<r><record><datafield><subfield>" + abstract + b"</subfield></datafield></record></r>"
    assert not xmlsafe.too_deep(answer)
    assert xmlsafe.too_deep(b"<r><a>" + b"<![CDATA[no end" + b"<a>" * 10)
    assert xmlsafe.too_deep(b"<r>" + abstract + b"<a>" * 200 + b"</a>" * 200 + b"</r>")


def test_many_records_are_put_together_in_a_helper_process_with_the_same_works() -> None:
    from app.services.oeuvre import compute

    def records() -> list[Entry]:
        return [e(("openlibrary", "hardcover", "audible")[n % 3], title,
                  isbns=[f"97800000{n:05d}"] if n % 5 == 0 else []) for n, title in enumerate(_random_titles(330))]

    answered = {"en": {"openlibrary", "hardcover", "audible"}}
    values: dict[str, Any] = {"answered": answered, "author_language": "en", "pen_names": set(), "author": "Ida Falkner",
                              "namesakes": 0}
    here = works.build(records(), **values)
    assert len(records()) >= compute.PROCESS_FROM
    there = compute.build(records(), **values)
    assert [(w.key, w.kind, w.title_in("en"), len(w.entries)) for w in there] == \
        [(w.key, w.kind, w.title_in("en"), len(w.entries)) for w in here]
    with pytest.raises(compute.Failed):
        compute.build(records(), **values, timeout=0.0)
    with pytest.raises(compute.Failed):
        compute.build(records(), **values, stop=lambda: True)


def test_hardcover_says_how_many_other_authors_of_the_name_have_books(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.sources import hardcover

    answer = {"search": {"results": {"hits": [
        {"document": {"id": "71001", "name": "Tobias Wren", "books_count": 15, "alternate_names": ["T. Wren"]}},
        {"document": {"id": "71002", "name": "Tobias Wren", "books_count": 2, "alternate_names": []}},
        {"document": {"id": "71003", "name": "Tobias Wren", "books_count": 0, "alternate_names": []}},
        {"document": {"id": "71004", "name": "Ann Wren", "books_count": 9, "alternate_names": []}}]}}}
    monkeypatch.setattr(hardcover, "_post", lambda db, query, variables, interactive: answer)
    assert hardcover._author_id(None, "Tobias Wren") == (71001, ["T. Wren"], 1)  # type: ignore[arg-type]
