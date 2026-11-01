"""The matching from the scan to the answer, against faked services: what a sure match puts into a book, what it never
overrides, what survives a new scan and a new run, translations joined and split, and the walls around the job."""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Edition, ExternalRef, Job, MatchEntry, Work
from app.services import jobs
from app.services.match import apply, review
from app.services.match import run as matching
from app.services.sources import config, throttle

from .bookmaker import cover_image
from .fake_sources import audible_product, fixture, hc_doc, hc_edition, ol_doc
from .library_helpers import book, books_folder, make_library, recording, run_jobs, scan
from .match_helpers import World, edition_of, entries, entry_for, member

ORCHARD = {"title": "The Quiet Orchard", "authors": ("Mara Lindqvist",), "language": "en"}
#: The ISBN of the invented book: an e-book is sure on one catalogue's word only with the ISBN of its own file (or a second
#: source that tells the same book).
ORCHARD_ISBN = "9780000000002"
SURE_ORCHARD = {**ORCHARD, "isbn": ORCHARD_ISBN}
OL_ORCHARD = ol_doc("OL1W", "The Quiet Orchard", ["Mara Lindqvist"], languages=("eng", "ger"), cover=77,
                    subjects=("Fantasy", "Orchards"), year=2019, isbns=(ORCHARD_ISBN,))
#: The same record without the ISBN: found by its title and author only.
OL_PLAIN = ol_doc("OL1W", "The Quiet Orchard", ["Mara Lindqvist"], languages=("eng", "ger"), cover=77,
                  subjects=("Fantasy", "Orchards"), year=2019)


def fresh(world: World, name: str = "shelf", **files: dict[str, Any]) -> tuple[Any, int]:
    """A folder with the books named (relative path -> arguments of ``book``), a library on it, scanned and matched."""
    folder = books_folder(name)
    for relative, arguments in files.items():
        book(folder, relative, **arguments)
    library = make_library(folder, name.title())
    member(library)
    scan(library)
    return folder, library


def description_of(title: str) -> str:
    return edition_of(title).description


# ---- A sure match -----------------------------------------------------------------------------------------------


def test_a_sure_match_fills_what_the_files_leave_empty(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    world.fake.ol_work = {"description": "Three sisters and an orchard."}
    fresh(world, **{"a.epub": SURE_ORCHARD})
    assert entries() == [("The Quiet Orchard", "sure", "", "")]
    edition = edition_of("The Quiet Orchard")
    assert edition.description == "Three sisters and an orchard."
    assert edition.published == "2019" and edition.genre == "fantasy"
    assert edition.cover and edition.color, "the cover came from the source, through its redirect, as a stored picture"
    assert set(edition.overlay) == {"description", "year", "genre", "cover"} and all(
        item["by"] == "source" for item in edition.overlay.values())
    with SessionLocal() as db:
        refs = {(r.source, r.kind, r.key) for r in db.scalars(select(ExternalRef))}
    assert refs == {("openlibrary", "work", "OL1W")}


def test_the_work_shows_what_a_match_gave(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    fresh(world, **{"a.epub": SURE_ORCHARD})
    with SessionLocal() as db:
        work_id = db.scalars(select(Work.id)).one()
    shown = client.get(f"/api/v1/works/{work_id}").json()
    assert shown["description"] == "A description from Open Library." and shown["year"] == 2019
    assert shown["genre"] == "fantasy" and shown["cover_url"] and "?v=" in shown["cover_url"]
    assert client.get(shown["cover_url"]).headers["content-type"] == "image/webp"


def test_what_the_file_says_is_never_replaced_by_a_source(client: TestClient, operator: Account, world: World) -> None:
    """The rank: a person, then the file, then a source. The file has its description, its series and its genre."""
    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], series="Orchard Tales", position=4,
                            description="The description of Hardcover.")]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ORCHARD_ISBN)]}
    fresh(world, **{"a.epub": dict(**SURE_ORCHARD, description="<p>The description in the file.</p>", series="Orchard Saga",
                                    series_pos=1, subjects=("Krimi",))})
    edition = edition_of("The Quiet Orchard")
    assert (edition.description, edition.series_name, edition.series_pos, edition.genre) == (
        "The description in the file.", "Orchard Saga", 1.0, "krimi")
    assert entry_for("The Quiet Orchard").state == "sure"


def test_a_source_fills_the_series_the_file_does_not_know_and_the_place_in_it(client: TestClient, operator: Account,
                                                                              world: World) -> None:
    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], series="Orchard Tales", position=1)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ORCHARD_ISBN)]}
    fresh(world, **{"a.epub": SURE_ORCHARD})
    edition = edition_of("The Quiet Orchard")
    assert (edition.series_name, edition.series_pos) == ("Orchard Tales", 1.0) and edition.series_id


def test_the_place_in_the_series_is_filled_when_the_file_names_only_the_series(client: TestClient, operator: Account,
                                                                               world: World) -> None:
    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], series="Orchard Tales", position=3)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ORCHARD_ISBN)]}
    fresh(world, **{"a.epub": dict(**SURE_ORCHARD, series="orchard tales")})
    edition = edition_of("The Quiet Orchard")
    assert edition.series_name == "orchard tales" and edition.series_pos == 3.0


def test_a_cover_in_the_file_is_kept_and_none_is_fetched(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    fresh(world, **{"a.epub": dict(**ORCHARD, cover=cover_image((10, 120, 40)))})
    assert world.fake.count("covers.openlibrary.org") == 0
    assert "cover" not in (edition_of("The Quiet Orchard").overlay or {})


def test_an_isbn_settles_the_book_with_one_source_and_stops_asking(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    world.connect_hardcover()
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Somebody Else"], isbns=("9783999900018",))]
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"])]
    fresh(world, **{"a.epub": dict(**ORCHARD, isbn="9783999900018")})
    assert entry_for("The Quiet Orchard").state == "sure"
    assert world.fake.count("api.hardcover.app") == 1 or world.fake.count("openlibrary.org", "/search") == 1
    assert world.fake.count("services.dnb.de") == 0 and world.fake.count("api.audible") == 0


def test_an_isbn_that_belongs_to_another_book_is_no_proof_the_book_waits_for_a_person(
        client: TestClient, operator: Account, world: World) -> None:
    """The ISBN in a file is sometimes a stranger's (a leftover of a bundle): the book it leads to is another one."""
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL9W", "Study Guide: Dinner at Eight", ["Somebody Else"], isbns=("9783999900018",))]
    world.fake.ol_work = {"description": "Words of the study guide."}
    fresh(world, **{"a.epub": dict(**ORCHARD, isbn="9783999900018")})
    assert entries() == [("The Quiet Orchard", "maybe", "isbn_conflict", "")]
    assert edition_of("The Quiet Orchard").description == "" and not edition_of("The Quiet Orchard").overlay
    with SessionLocal() as db:
        assert db.scalar(select(ExternalRef.id)) is None, "nothing is joined to the stranger's work"


# ---- A match that is not sure -----------------------------------------------------------------------------------


def test_a_book_that_is_not_sure_waits_for_a_person_with_its_proposals(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",)),
                     ol_doc("OL2W", "The Quiet Orchard Companion Workbook", ["Mara Lindqvist"])]
    fresh(world, **{"a.epub": ORCHARD})
    row = entry_for("The Quiet Orchard")
    assert (row.state, row.reason, row.decision) == ("maybe", "author_weak", "")
    assert [p["id"] for p in row.proposals] == ["openlibrary:OL1W:"], "what cannot be the book is not offered"
    assert row.proposals[0]["why"][:2] == ["title_exact", "author_weak"] and row.proposals[0]["fatal"] == ""
    edition = edition_of("The Quiet Orchard")
    assert not edition.overlay and edition.description == "", "nothing from a source is applied before a person says so"


def test_when_nothing_can_be_the_book_the_nearest_are_kept_with_what_is_wrong_with_them(client: TestClient,
                                                                                         operator: Account,
                                                                                         world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard Companion Workbook", ["Mara Lindqvist"]),
                     ol_doc("OL2W", "The Quiet Orchard", ["Hanna Lindqvist"])]
    fresh(world, **{"a.epub": ORCHARD})
    row = entry_for("The Quiet Orchard")
    assert (row.state, row.reason) == ("open", "nothing_fits")
    assert sorted(p["fatal"] for p in row.proposals) == ["author_differs", "companion"]


def test_a_book_the_sources_do_not_know_is_open(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    fresh(world, **{"a.epub": ORCHARD})
    row = entry_for("The Quiet Orchard")
    assert (row.state, row.reason) == ("open", "nothing_found")


def test_a_book_that_got_no_answer_stays_waiting_and_is_not_called_open(client: TestClient, operator: Account,
                                                                         world: World) -> None:
    world.only("openlibrary")
    world.fake.status["openlibrary.org"] = 503
    fresh(world, **{"a.epub": SURE_ORCHARD})
    assert entries() == [("The Quiet Orchard", "pending", "", "")]
    # The source rests; the clock picks the book up again afterwards.
    world.advance(31)
    del world.fake.status["openlibrary.org"]
    world.fake.ol = [OL_ORCHARD]
    with SessionLocal() as db:
        assert matching.enqueue_pending(db) == 1
    run_jobs()
    assert entries()[0][1] == "sure"


def test_while_the_source_rests_nothing_is_queued_and_nothing_asked(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    world.only("openlibrary")
    world.fake.status["openlibrary.org"] = 503
    fresh(world, **{"a.epub": ORCHARD})
    asked = len(world.fake.log)
    with SessionLocal() as db:
        assert matching.enqueue_pending(db) == 0
    run_jobs()
    assert len(world.fake.log) == asked


def test_one_source_failing_does_not_stop_the_others(client: TestClient, operator: Account, world: World) -> None:
    world.connect_hardcover()
    world.fake.status["api.hardcover.app"] = 500
    world.fake.ol = [OL_ORCHARD]
    world.fake.audible = {"com": [audible_product("B0EXAMPL09", "The Quiet Orchard", ["Mara Lindqvist"], 600,
                                                 language="english")]}
    fresh(world, **{"a.epub": ORCHARD})
    assert entries()[0][1] == "sure"
    assert throttle.state_of("hardcover").last_error == "server"


# ---- The switches -----------------------------------------------------------------------------------------------


def test_with_the_sources_off_a_scan_asks_nobody_and_the_books_wait(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    world.settings(sources_enabled=False)
    fresh(world, **{"a.epub": ORCHARD})
    assert world.fake.log == []
    assert entries() == [("The Quiet Orchard", "pending", "", "")]
    assert edition_of("The Quiet Orchard").title == "The Quiet Orchard", "everything from the files is as it was"
    with SessionLocal() as db:
        assert matching.enqueue_pending(db) == 0
    assert run_jobs() == 0


def test_switching_the_sources_on_picks_up_what_waited(client: TestClient, operator: Account, world: World) -> None:
    world.settings(sources_enabled=False)
    world.fake.ol = [OL_ORCHARD]
    fresh(world, **{"a.epub": SURE_ORCHARD})
    assert entries()[0][1] == "pending"
    response = client.put("/api/v1/sources", json={"enabled": True})
    assert response.status_code == 200
    run_jobs()
    assert entries()[0][1] == "sure"


# ---- What survives ----------------------------------------------------------------------------------------------


def test_a_changed_file_keeps_what_a_source_gave_and_is_not_looked_at_again(client: TestClient, operator: Account,
                                                                            world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    folder, library = fresh(world, **{"a.epub": ORCHARD})
    asked = len(world.fake.log)
    before = edition_of("The Quiet Orchard")
    book(folder, "a.epub", **ORCHARD, text_bytes=9000)
    scan(library)
    after = edition_of("The Quiet Orchard")
    assert after.id == before.id and after.description == before.description and after.cover == before.cover
    assert len(world.fake.log) == asked, "the same book, only a new file: no new question"


def test_another_title_in_a_changed_file_is_a_new_look(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    folder, library = fresh(world, **{"a.epub": ORCHARD})
    asked = len(world.fake.log)
    book(folder, "a.epub", title="The Quiet Orchard Revised", authors=("Mara Lindqvist",), language="en")
    scan(library)
    assert len(world.fake.log) > asked
    assert entries()[0][0] == "The Quiet Orchard Revised"


def test_what_a_person_corrected_beats_the_file_and_the_source_and_survives_everything(client: TestClient, operator: Account,
                                                                                       world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    folder, library = fresh(world, **{"a.epub": dict(**ORCHARD, subjects=("Fantasy",), series="Orchard Saga",
                                                      series_pos=1)})
    edition = edition_of("The Quiet Orchard")
    response = client.patch(f"/api/v1/editions/{edition.id}", json={
        "genre": "krimi", "series": "My Own Series", "series_pos": 7, "title": "The Quiet Orchard Retold",
        "authors": ["M. Lindqvist", "Jonas Okafor"], "language": "DE"})
    assert response.status_code == 200, response.text
    for step in ("rematch", "rescan", "match again"):
        if step == "rematch":
            assert client.post("/api/v1/sources/rematch").status_code == 202
            run_jobs()
        elif step == "rescan":
            book(folder, "a.epub", **ORCHARD, subjects=("Fantasy",), series="Orchard Saga", series_pos=1, text_bytes=9000)
            scan(library)
        else:
            with SessionLocal() as db:
                row = db.scalar(select(MatchEntry))
                assert row is not None
                matching.match_entry(db, row)
        mine = edition_of("The Quiet Orchard Retold")
        assert (mine.genre, mine.series_name, mine.series_pos, mine.language) == ("krimi", "My Own Series", 7.0, "de"), step
    with SessionLocal() as db:
        authors = db.execute(select(apply.catalog.Person.name).join(
            apply.catalog.Credit, apply.catalog.Credit.person_id == apply.catalog.Person.id).where(
            apply.catalog.Credit.edition_id == edition.id).order_by(apply.catalog.Credit.position)).scalars().all()
    assert authors == ["M. Lindqvist", "Jonas Okafor"]


def test_a_correction_can_be_taken_back_and_the_file_speaks_again(client: TestClient, operator: Account, world: World) -> None:
    world.settings(sources_enabled=False)
    fresh(world, **{"a.epub": dict(**ORCHARD, subjects=("Fantasy",))})
    edition = edition_of("The Quiet Orchard")
    assert client.patch(f"/api/v1/editions/{edition.id}", json={"genre": "scifi"}).status_code == 200
    assert edition_of("The Quiet Orchard").genre == "scifi"
    assert client.patch(f"/api/v1/editions/{edition.id}", json={"genre": None}).status_code == 200
    assert edition_of("The Quiet Orchard").genre == "fantasy"
    # An empty text is a correction too: to nothing.
    assert client.patch(f"/api/v1/editions/{edition.id}", json={"genre": ""}).status_code == 200
    assert edition_of("The Quiet Orchard").genre == ""


def test_a_decision_stays_after_a_new_run(client: TestClient, operator: Account, world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",))]
    fresh(world, **{"a.epub": ORCHARD})
    entry = entry_for("The Quiet Orchard")
    assert entry.state == "maybe"
    with SessionLocal() as db:
        row = db.get(MatchEntry, entry.id)
        assert row is not None
        review.keep(db, row, operator.id)
    asked = len(world.fake.log)
    assert client.post("/api/v1/sources/rematch").json() == {"books": 0}, "no entry without a decision is left to match"
    run_jobs()
    assert len(world.fake.log) == asked
    assert entries() == [("The Quiet Orchard", "maybe", "author_weak", "kept")]


def test_a_decision_stays_when_the_book_changes_too(client: TestClient, operator: Account, world: World) -> None:
    """A person said "leave it"; the file is rewritten with another title (a new look for any undecided book)."""
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",))]
    folder, library = fresh(world, **{"a.epub": ORCHARD})
    entry = entry_for("The Quiet Orchard")
    with SessionLocal() as db:
        row = db.get(MatchEntry, entry.id)
        assert row is not None
        review.keep(db, row, operator.id)
    asked = len(world.fake.log)
    book(folder, "a.epub", title="The Quiet Orchard Revised", authors=("Mara Lindqvist",), language="en")
    scan(library)
    assert entries() == [("The Quiet Orchard Revised", "maybe", "author_weak", "kept")]
    assert len(world.fake.log) == asked, "nobody was asked: a person had decided"


def test_matching_everything_again_replaces_what_a_source_gave_and_asks_the_cache(client: TestClient, operator: Account,
                                                                                world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    fresh(world, **{"a.epub": SURE_ORCHARD})
    asked = len(world.fake.log)
    assert client.post("/api/v1/sources/rematch").json() == {"books": 1}
    run_jobs()
    assert len(world.fake.log) == asked, "the answers were kept"
    assert entries()[0][1] == "sure"
    # A day after a month the cache has run out, and the world has changed its mind.
    world.days(40)
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",))]
    with SessionLocal() as db:
        # (The browser's session ran out in the forty days; the operator's request is the same call.)
        assert matching.rematch_all(db) == 1
    run_jobs()
    assert entries()[0][1] == "maybe"
    edition = edition_of("The Quiet Orchard")
    assert not edition.overlay and edition.description == "", "a match that is no longer sure takes back what it gave"
    with SessionLocal() as db:
        assert db.scalar(select(ExternalRef.id)) is None


# ---- A source that cannot be asked again ------------------------------------------------------------------------


def matched_by_two(world: World) -> None:
    """A book matched by Hardcover (series, place, description) and Open Library, both sure."""
    world.connect_hardcover()
    world.only("hardcover", "openlibrary")
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], series="Orchard Saga", position=2,
                            description="From Hardcover.", year=2019)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ORCHARD_ISBN)]}
    world.fake.ol = [OL_PLAIN]
    world.fake.ol_work = {"description": "From Open Library."}
    fresh(world, **{"a.epub": SURE_ORCHARD})
    edition = edition_of("The Quiet Orchard")
    assert (edition.series_name, edition.series_pos, edition.description) == ("Orchard Saga", 2.0, "From Hardcover.")
    assert entries() == [("The Quiet Orchard", "sure", "", "")]


def rematch() -> int:
    """"Match everything again", as the operator's request does it (the browser session of a test would have run out
    when the test lets days pass)."""
    with SessionLocal() as db:
        return matching.rematch_all(db)


def sources_of_refs() -> set[str]:
    with SessionLocal() as db:
        return set(db.scalars(select(ExternalRef.source)))


@pytest.mark.parametrize("how", ["resting", "failing"])
def test_matching_again_while_a_source_of_the_last_match_is_away_changes_nothing_and_waits(
        client: TestClient, operator: Account, world: World, how: str) -> None:
    matched_by_two(world)
    if how == "resting":
        throttle.fail("hardcover", "server")
    else:
        world.days(40)  # the kept answers have run out, so Hardcover is asked and fails
        world.fake.status["api.hardcover.app"] = 500
    assert rematch() == 1
    run_jobs()
    edition = edition_of("The Quiet Orchard")
    assert (edition.series_name, edition.series_pos, edition.description) == ("Orchard Saga", 2.0, "From Hardcover.")
    assert sources_of_refs() == {"hardcover", "openlibrary"}
    assert entries() == [("The Quiet Orchard", "pending", matching.RECHECK, "")], "waiting is not an answer"
    # The source is back: the book is looked at again, and the answer is the same.
    world.fake.status.clear()
    world.advance(4 * 3600)
    with SessionLocal() as db:
        assert matching.enqueue_pending(db) == 1
    run_jobs()
    assert entries() == [("The Quiet Orchard", "sure", "", "")]
    assert edition_of("The Quiet Orchard").series_name == "Orchard Saga"


def test_a_book_that_waits_for_a_source_does_not_hold_up_the_books_behind_it(client: TestClient, operator: Account,
                                                                              world: World) -> None:
    world.connect_hardcover()
    world.only("hardcover", "openlibrary")
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], series="Orchard Saga", position=2,
                            description="From Hardcover.", year=2019)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English")]}
    world.fake.ol = [OL_ORCHARD, ol_doc("OL4W", "Salt Harbor", ["Greta Hellwig"], languages=("eng",), year=2020,
                                         isbns=("9780000000019",))]
    world.fake.ol_work = {"description": "From Open Library."}
    fresh(world, **{"a.epub": ORCHARD, "b.epub": {"title": "Salt Harbor", "authors": ("Greta Hellwig",),
                                                     "language": "en", "isbn": "9780000000019"}})
    assert entries() == [("Salt Harbor", "sure", "", ""), ("The Quiet Orchard", "sure", "", "")]
    world.days(40)  # the kept answers have run out: Open Library is asked for both
    throttle.fail("hardcover", "server")
    asked = world.fake.count("openlibrary.org", "/search")
    assert rematch() == 2
    run_jobs()
    assert world.fake.count("openlibrary.org", "/search") >= asked + 2, "the second book was looked at too"
    assert entries() == [("Salt Harbor", "sure", "", ""), ("The Quiet Orchard", "pending", matching.RECHECK, "")]


def test_a_source_switched_off_keeps_what_it_gave_and_only_what_a_new_answer_brings_is_replaced(
        client: TestClient, operator: Account, world: World) -> None:
    matched_by_two(world)
    world.only("openlibrary")
    # (The one source left proves the book by the ISBN of its file.)
    world.fake.ol = [OL_ORCHARD]
    world.fake.ol_work = {"description": "Newer words from Open Library."}
    world.days(40)
    assert rematch() == 1
    run_jobs()
    edition = edition_of("The Quiet Orchard")
    assert (edition.series_name, edition.series_pos) == ("Orchard Saga", 2.0), "Hardcover gave the series, still there"
    assert edition.description == "Newer words from Open Library.", "a new answer replaces what it answers"
    assert sources_of_refs() == {"hardcover", "openlibrary"}
    assert entries() == [("The Quiet Orchard", "sure", "", "")]


def test_a_source_switched_off_leaves_the_values_when_the_others_are_unsure_and_a_person_looks(
        client: TestClient, operator: Account, world: World) -> None:
    matched_by_two(world)
    world.only("openlibrary")
    world.fake.ol = [ol_doc("OL1W", "The Quiet Orchard", ["Lindqvist"], languages=("eng",), year=2019)]
    world.days(40)
    assert rematch() == 1
    run_jobs()
    assert entries() == [("The Quiet Orchard", "maybe", "author_weak", "")], "it is on the list for a person"
    edition = edition_of("The Quiet Orchard")
    assert (edition.series_name, edition.series_pos) == ("Orchard Saga", 2.0)
    assert sources_of_refs() == {"hardcover", "openlibrary"}


def test_a_source_switched_off_is_not_believed_for_a_book_the_others_no_longer_name_the_same(
        client: TestClient, operator: Account, world: World) -> None:
    world.connect_hardcover()
    world.only("hardcover")
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], series="Orchard Saga", position=2,
                            description="From Hardcover.", year=2019)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ORCHARD_ISBN)]}
    fresh(world, **{"a.epub": SURE_ORCHARD})
    assert edition_of("The Quiet Orchard").series_name == "Orchard Saga"
    # Hardcover goes; the only source left names the book by a key of its own: nothing says it is the same one.
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    world.fake.ol_work = {"description": "From Open Library."}
    assert rematch() == 1
    run_jobs()
    edition = edition_of("The Quiet Orchard")
    assert (edition.series_name, edition.description) == ("", "From Open Library.")
    assert sources_of_refs() == {"openlibrary"}


# ---- Translations -----------------------------------------------------------------------------------------------


def two_languages(world: World) -> None:
    world.connect_hardcover()
    world.only("hardcover")
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"], users=500)]
    world.fake.hc_editions = {"7001": [hc_edition("The Quiet Orchard", "English", isbn=ORCHARD_ISBN),
                                       hc_edition("Der stille Obstgarten", "German", isbn="9780000000026")]}


GERMAN = {"title": "Der stille Obstgarten", "authors": ("Mara Lindqvist",), "language": "de", "isbn": "9780000000026"}


def works_of_editions() -> dict[str, int]:
    with SessionLocal() as db:
        return {e.title: e.work_id for e in db.scalars(select(Edition))}


def test_a_translation_is_joined_to_its_work_by_the_work_the_source_knows(client: TestClient, operator: Account,
                                                                          world: World) -> None:
    two_languages(world)
    fresh(world, **{"en.epub": SURE_ORCHARD, "de.epub": GERMAN})
    assert [row[1] for row in entries()] == ["sure", "sure"]
    works = works_of_editions()
    assert works["The Quiet Orchard"] == works["Der stille Obstgarten"], "one book in two languages"
    with SessionLocal() as db:
        assert db.query(Work).count() == 1
        assert {e.grouped_by for e in db.scalars(select(Edition))} == {"auto", ""}
    shown = client.get("/api/v1/works").json()["items"]
    assert len(shown) == 1 and sorted(e["language"] for e in shown[0]["editions"]) == ["de", "en"]


def test_joined_works_are_split_and_joined_by_hand_and_the_matching_leaves_them(client: TestClient, operator: Account,
                                                                                world: World) -> None:
    two_languages(world)
    fresh(world, **{"en.epub": SURE_ORCHARD, "de.epub": GERMAN})
    german = edition_of("Der stille Obstgarten")
    answer = client.post(f"/api/v1/editions/{german.id}/split")
    assert answer.status_code == 200
    works = works_of_editions()
    assert works["The Quiet Orchard"] != works["Der stille Obstgarten"]
    assert len(client.get("/api/v1/works").json()["items"]) == 2
    # Matching everything again does not put them back together: a person split them.
    client.post("/api/v1/sources/rematch")
    run_jobs()
    assert len(client.get("/api/v1/works").json()["items"]) == 2
    # And a person can join them again, to the work they see.
    target = works["The Quiet Orchard"]
    assert client.post(f"/api/v1/editions/{german.id}/join", json={"work_id": target}).status_code == 200
    assert len(client.get("/api/v1/works").json()["items"]) == 1
    assert edition_of("Der stille Obstgarten").grouped_by == "hand"


def test_the_work_a_translation_left_is_gone_when_it_was_the_last_edition(client: TestClient, operator: Account,
                                                                          world: World) -> None:
    two_languages(world)
    fresh(world, **{"en.epub": SURE_ORCHARD, "de.epub": GERMAN})
    with SessionLocal() as db:
        assert db.query(Work).count() == 1
        assert db.query(Edition).count() == 2


# ---- Recordings -------------------------------------------------------------------------------------------------


def test_a_recording_is_proven_by_its_running_time(client: TestClient, operator: Account, world: World) -> None:
    world.only("audible")
    world.fake.audible = {"de": [
        audible_product("B0EXAMPL01", "Die Uhrmacherin", ["Ada Feldkamp"], 10, series="Uhrwerk", sequence="2",
                        narrators=("Ben Hollister", "Sina Voss")),
        audible_product("B0EXAMPL02", "Die Uhrmacherin", ["Ada Feldkamp"], 5, kind="abridged"),
        audible_product("B0EXAMPL03", "Die Uhrmacherin (Hörspiel)", ["Ada Feldkamp"], 6, kind="abridged")]}
    folder = books_folder("audio")
    recording(folder, "uhr/uhr.m4b", 600.0, title="Die Uhrmacherin", album="Die Uhrmacherin", artist="Ada Feldkamp")
    library = make_library(folder, "Audio")
    scan(library)
    row = entry_for("Die Uhrmacherin")
    assert row.state == "sure", (row.state, row.reason, [p["why"] for p in row.proposals or []])
    edition = edition_of("Die Uhrmacherin")
    assert (edition.variant, edition.variant_uncertain, edition.asin) == ("unabridged", False, "B0EXAMPL01")
    assert (edition.series_name, edition.series_pos) == ("Uhrwerk", 2.0)
    with SessionLocal() as db:
        from app.models import NARRATOR, Credit, Person

        narrators = db.scalars(select(Person.name).join(Credit, Credit.person_id == Person.id).where(
            Credit.edition_id == edition.id, Credit.role == NARRATOR)).all()
    assert sorted(narrators) == ["Ben Hollister", "Sina Voss"]


def test_a_recording_whose_time_fits_the_abridged_version_is_marked_abridged(client: TestClient, operator: Account,
                                                                             world: World) -> None:
    world.only("audible")
    world.fake.audible = {"de": [
        audible_product("B0EXAMPL01", "Die Uhrmacherin", ["Ada Feldkamp"], 20),
        audible_product("B0EXAMPL02", "Die Uhrmacherin", ["Ada Feldkamp"], 10, kind="abridged")]}
    folder = books_folder("audio")
    recording(folder, "uhr/uhr.m4b", 600.0, title="Die Uhrmacherin", album="Die Uhrmacherin", artist="Ada Feldkamp")
    scan(make_library(folder, "Audio"))
    edition = edition_of("Die Uhrmacherin")
    assert (edition.variant, edition.variant_uncertain) == ("abridged", False)


def test_a_recording_no_product_matches_in_time_keeps_the_guess_and_waits_for_a_person(client: TestClient,
                                                                                       operator: Account,
                                                                                       world: World) -> None:
    world.only("audible")
    world.fake.audible = {"de": [audible_product("B0EXAMPL01", "Die Uhrmacherin", ["Ada Feldkamp"], 99)]}
    folder = books_folder("audio")
    recording(folder, "uhr/uhr.m4b", 600.0, title="Die Uhrmacherin", album="Die Uhrmacherin", artist="Ada Feldkamp")
    scan(make_library(folder, "Audio"))
    row = entry_for("Die Uhrmacherin")
    assert (row.state, row.reason) == ("maybe", "runtime_unproven")
    assert edition_of("Die Uhrmacherin").variant_uncertain is True


def test_the_market_asked_is_the_one_of_the_language_of_the_recording(client: TestClient, operator: Account,
                                                                       world: World) -> None:
    world.only("audible")
    world.settings(source_markets=["de", "us", "fr"])
    folder = books_folder("audio")
    recording(folder, "a/a.m4b", 600.0, title="Die Uhrmacherin", album="Die Uhrmacherin", artist="Ada Feldkamp")
    scan(make_library(folder, "Audio"))
    # The recording says no language: every market the operator chose is asked, in their order.
    # (A market that finds nothing by the free text is asked once more by the fields title and author.)
    assert list(dict.fromkeys(world.fake.hosts())) == ["api.audible.de", "api.audible.com", "api.audible.fr"]
    with SessionLocal() as db:
        assert config.markets_for(db, "de") == ["de"] and config.markets_for(db, "en") == ["us"]
        assert config.markets_for(db, "sv") == ["de", "us", "fr"]
        assert config.markets_for(db, "fr") == ["fr"]
        world.settings(source_markets=["de", "us"])
    with SessionLocal() as db:
        assert config.markets_for(db, "fr") == ["de", "us"], "a market the operator did not choose is not asked"


# ---- The job ----------------------------------------------------------------------------------------------------


def test_the_server_stopping_ends_the_run_and_the_next_one_goes_on(client: TestClient, operator: Account,
                                                                    world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    folder = books_folder("many")
    for number in range(4):
        book(folder, f"b{number}.epub", title=f"The Quiet Orchard {number}", authors=("Mara Lindqvist",), language="en")
    library = make_library(folder, "Many")
    world.settings(sources_enabled=False)
    scan(library)
    assert [row[1] for row in entries()] == ["pending"] * 4
    world.settings(sources_enabled=True)
    stopped = threading.Event()

    def stop_after_two(request: httpx.Request) -> None:
        if len([r for r in world.fake.log if "search" in r.url.path]) == 2 and not stopped.is_set():
            stopped.set()
            jobs.stopping.set()

    world.fake.on_request = stop_after_two
    with SessionLocal() as db:
        matching.enqueue_pending(db)
    jobs.run_pending(10)
    jobs.stopping.clear()
    waiting = [row[1] for row in entries()].count("pending")
    assert 0 < waiting < 4, "the run stopped half way"
    with SessionLocal() as db:
        assert db.scalar(select(Job.state).where(Job.kind == "match")) == "queued", "and waits to go on"
    world.fake.on_request = None
    run_jobs()
    assert [row[1] for row in entries()].count("pending") == 0


def test_a_book_that_makes_the_matching_fail_does_not_stop_the_others(client: TestClient, operator: Account, world: World,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    folder = books_folder("two")
    book(folder, "a.epub", **ORCHARD)
    book(folder, "b.epub", title="The Quiet Orchard Two", authors=("Mara Lindqvist",), language="en")
    library = make_library(folder, "Two")
    world.settings(sources_enabled=False)
    scan(library)
    world.settings(sources_enabled=True)
    original = matching.settle

    def breaking(db: Any, entry: Any, edition: Any, decision: Any, **kw: Any) -> None:
        if edition.title == "The Quiet Orchard":
            raise RuntimeError("a fault in nexbooks")
        original(db, entry, edition, decision, **kw)

    monkeypatch.setattr(matching, "settle", breaking)
    with SessionLocal() as db:
        matching.enqueue_pending(db)
    run_jobs()
    states = {title: (state, reason) for title, state, reason, _ in entries()}
    assert states["The Quiet Orchard"] == ("open", "failed") and states["The Quiet Orchard Two"][0] != "pending"


def test_hardcover_searches_of_a_library_go_in_bundles(client: TestClient, operator: Account, world: World) -> None:
    world.only("hardcover")
    world.connect_hardcover()
    world.fake.hc = [hc_doc(7001, "Something", ["Nobody"], users=1)]
    folder = books_folder("bundle")
    for number in range(11):
        book(folder, f"b{number}.epub", title=f"Title Number {number}", authors=("Mara Lindqvist",), language="en")
    library = make_library(folder, "Bundle")
    world.settings(sources_enabled=False)
    scan(library)
    world.settings(sources_enabled=True)
    with SessionLocal() as db:
        matching.enqueue_pending(db)
    run_jobs()
    searches = [r for r in world.fake.log if b"search(" in r.content]
    assert len(searches) == 3, "eleven books are three requests (five, five, one), not eleven"


def test_the_scan_of_a_library_that_came_before_the_sources_catches_up(client: TestClient, operator: Account,
                                                                        world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [OL_ORCHARD]
    world.settings(sources_enabled=False)
    fresh(world, **{"a.epub": SURE_ORCHARD})
    with SessionLocal() as db:
        db.query(MatchEntry).delete()
        db.commit()
    world.settings(sources_enabled=True)
    with SessionLocal() as db:
        assert matching.enqueue_pending(db) == 1
    run_jobs()
    assert entries()[0][1] == "sure"


def test_nothing_of_the_files_goes_to_the_sources_but_the_words_of_the_book(client: TestClient, operator: Account,
                                                                            world: World) -> None:
    world.connect_hardcover()
    world.fake.ol = [OL_ORCHARD]
    world.fake.hc = [hc_doc(7001, "The Quiet Orchard", ["Mara Lindqvist"])]
    folder = books_folder("private-family-folder")
    book(folder, "secret-file-name.epub", **ORCHARD, isbn="9783999900018", description="<p>Private words of the file.</p>")
    scan(make_library(folder, "Family Library Name"))
    assert world.fake.log
    for request in world.fake.log:
        text = f"{request.url} {request.content.decode('utf-8', 'replace')}"
        for secret in ("private-family-folder", "secret-file-name", "Family Library Name", "Private words", ".epub", "tester",
                       "ops"):
            assert secret not in text, secret


def test_the_sample_fixtures_make_a_whole_match(client: TestClient, operator: Account, world: World) -> None:
    """The recorded shapes of the real services, end to end: Open Library's search, work page and cover."""
    world.only("openlibrary")
    world.fake.ol = fixture("openlibrary_search.json")["docs"]
    world.fake.ol_work = fixture("openlibrary_work.json")
    fresh(world, **{"a.epub": {**ORCHARD, "isbn": "9783999900018"}})
    assert entry_for("The Quiet Orchard").state == "sure"
    assert description_of("The Quiet Orchard").startswith("Three sisters inherit an orchard")
