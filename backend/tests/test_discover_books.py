"""The page of a book of the sources, the gaps in a series, more of the authors on the shelves, covers and the
catalogue search that leads to a page. Every answer is an invented one; no test asks a real service.

The rule under test is the same as everywhere in Discover: **what the caller reads and has is worked out from the
libraries they may see.** A series that lies only in somebody else's library is not theirs, so it has no gap in their
page, no job that is queued for it because of them, and no trace in what they read."""

from __future__ import annotations

import json
import zlib

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Edition, ExternalRef, ForeignBook, Job, Series
from app.services import jobs
from app.services.discover import foryou, pics
from app.services.sources import cache, hardcover
from app.services.sources.model import Candidate

from .conftest import make_account, new_client
from .discover_helpers import TODAY
from .discover_shelves import Shelves, by_title, read, token_of
from .fake_sources import audible_product


def hardcover_row(title: str, place: float, readers: int) -> Candidate:
    """A row of the books of a series at Hardcover as the client makes it: a book, its place, its readers."""
    return Candidate(source="hardcover", key=str(zlib.crc32(f"{title}|{place}".encode())), title=title,
                     authors=["Iris Quist"], series="Die Chroniken von Arvel", series_pos=place, weight=readers)


def run_all() -> int:
    ran = 0
    while True:
        step = jobs.run_pending(1000)
        ran += step
        if not step:
            return ran


def jobs_of(kind: str) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count(Job.id)).where(Job.kind == kind)) or 0)


# ---- The page of a book ------------------------------------------------------------------------------------------


def test_the_page_of_a_book_says_what_the_source_says_and_looks_for_the_other_form_in_the_background(
        shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    token = token_of(carol, "Orbit Null")
    page = carol.get(f"/api/v1/discover/books/{token}").json()
    assert page["title"] == "Orbit Null" and page["authors"] == ["Theo Valcourt"] and page["token"] == token
    assert [(f["kind"], f["language"], f["source"]) for f in page["forms_known"]] == [("ebook", "", "apple")]
    assert page["owned_work_id"] is None and page["upcoming"] is False and page["pending"] is True
    # The other form is looked for by the job, not by the request: Audible knows a recording of it.
    shelves.fake.audible = {"de": [audible_product("B0ORBIT001", "Orbit Null", ["Theo Valcourt"], 480,
                                                   narrators=("Clara Vienne",), summary="<p>A made-up story.</p>")]}
    asked = len(shelves.fake.log)
    again = carol.get(f"/api/v1/discover/books/{token}").json()
    assert len(shelves.fake.log) == asked, "opening the page asks no source"
    assert again["pending"] is True
    run_all()
    page = carol.get(f"/api/v1/discover/books/{token}").json()
    assert page["pending"] is False
    audio = [f for f in page["forms_known"] if f["kind"] == "audio"]
    assert audio and audio[0]["minutes"] == 480 and audio[0]["narrators"] == ["Clara Vienne"]
    assert audio[0]["variant"] == "unabridged" and page["description"] == "A made-up story."
    assert set(page["kinds"]) == {"ebook", "audio"}
    # It is done once: opening the page again queues nothing.
    before = jobs_of("fenrich")
    carol.get(f"/api/v1/discover/books/{token}")
    assert jobs_of("fenrich") == before


def test_the_page_of_a_book_names_its_titles_with_their_languages(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    token = token_of(carol, "Orbit Null")
    with SessionLocal() as db:
        row = db.scalars(select(ForeignBook).where(ForeignBook.token == token)).one()
        row.language = "sv"
        row.data = {**(row.data or {}), "titles": [{"text": "Bana Noll", "language": "sv"},
                                                   {"text": "  Die Null  Bahn ", "language": "de"}, "not one", {"text": ""}]}
        db.commit()
    page = carol.get(f"/api/v1/discover/books/{token}").json()
    assert page["title"] == "Orbit Null"
    assert page["titles"] == [{"text": "Orbit Null", "language": "sv"}, {"text": "Bana Noll", "language": "sv"},
                              {"text": "Die Null Bahn", "language": "de"}]


def test_with_the_main_switch_off_the_page_of_a_book_queues_nothing_and_does_not_wait_for_anything(
        shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    token = token_of(carol, "Orbit Null")
    shelves.settings(sources_enabled=False)
    asked = len(shelves.fake.log)
    page = carol.get(f"/api/v1/discover/books/{token}").json()
    assert page["pending"] is False
    assert jobs_of("fenrich") == 0 and len(shelves.fake.log) == asked


def test_the_page_of_a_book_the_caller_has_leads_to_the_book_on_the_shelf(shelves: Shelves) -> None:
    alice, bob = shelves.client(shelves.alice), shelves.client(shelves.bob)
    token = token_of(alice, "Der Leuchtturmwaerter")
    assert alice.get(f"/api/v1/discover/books/{token}").json()["owned_work_id"] == shelves.work_id("Der Leuchtturmwaerter")
    assert bob.get(f"/api/v1/discover/books/{token}").json()["owned_work_id"] is None


def test_what_the_page_of_a_book_shows_of_a_series_and_of_the_author_is_the_callers_own(shelves: Shelves) -> None:
    shelves.connect_hardcover()
    shelves.only("apple", "audible", "hardcover")
    shelves.fetch("de", "trending")
    alice, carol = shelves.client(shelves.alice), shelves.client(shelves.carol)
    token = token_of(alice, "Glas und Asche", "trending")
    mine = alice.get(f"/api/v1/discover/books/{token}").json()
    assert mine["series"] == "Die Chroniken von Arvel" and mine["series_pos"] == 2.0
    own = [v for v in mine["volumes"] if v["state"] == "own"]
    assert [(v["pos"], v["work"]["title"]) for v in own] == [(1.0, "Die Krone der Weberinnen")]
    assert [m["title"] for m in mine["more"]] == ["Die Krone der Weberinnen"]
    # Carol has none of it: the same volumes are there, but none is hers and nothing of alice shows.
    theirs = carol.get(f"/api/v1/discover/books/{token}").json()
    assert all(v["work"] is None for v in theirs["volumes"]) and theirs["more"] == []
    assert not any(v["state"] == "own" for v in theirs["volumes"])
    assert "Die Krone der Weberinnen" in json.dumps(theirs["volumes"]), "the volume is public: a gap for carol"


# ---- Covers ------------------------------------------------------------------------------------------------------


def test_a_cover_is_served_to_the_signed_in_with_a_cache_that_cannot_go_stale(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    card = by_title(read(carol))["Der Leuchtturmwaerter"]
    assert card["cover_url"] and card["cover_url"].startswith("/api/v1/discover/books/" + card["token"] + "/cover?v=")
    answer = carol.get(card["cover_url"])
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/webp"
    assert "immutable" in answer.headers["cache-control"] and answer.headers["x-content-type-options"] == "nosniff"
    assert answer.content[:4] == b"RIFF"
    etag = answer.headers["etag"]
    assert carol.get(card["cover_url"], headers={"If-None-Match": etag}).status_code == 304
    # Without the number of the cover the browser must ask each time.
    bare = carol.get(f"/api/v1/discover/books/{card['token']}/cover")
    assert bare.status_code == 200 and "no-cache" in bare.headers["cache-control"]
    assert new_client().get(card["cover_url"]).status_code == 401


def test_a_book_without_a_cover_has_no_address_and_the_address_answers_404(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    card = by_title(read(carol))["Orbit Null"]
    assert card["cover_url"] is None, "its address was at a host that is not Apple's"
    assert carol.get(f"/api/v1/discover/books/{card['token']}/cover").status_code == 404
    assert carol.get("/api/v1/discover/books/nope/cover").status_code in (404, 422)


def test_the_cover_of_a_foreign_book_is_not_a_cover_of_the_shelf(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    card = by_title(read(carol))["Der Leuchtturmwaerter"]
    with SessionLocal() as db:
        name = db.scalar(select(ForeignBook.cover).where(ForeignBook.token == card["token"]))
    assert name and pics.path_of(name) is not None
    # The route of the covers of the shelf does not serve it: the pictures lie in different folders.
    from app.services import covers

    assert covers.path_of(name) is None


# ---- The catalogue search leads to a page --------------------------------------------------------------------------


def test_a_hit_of_the_catalogue_has_a_page_and_leads_to_the_shelf_when_the_caller_has_the_book(shelves: Shelves) -> None:
    shelves.fake.audible = {"de": [audible_product("B0ORBIT001", "Orbit Null", ["Theo Valcourt"], 480, language="german")]}
    shelves.fake.audible_chart = {}
    alice, bob = shelves.client(shelves.alice), shelves.client(shelves.bob)
    hits = {}
    for name, client in (("alice", alice), ("bob", bob)):
        answer = client.post("/api/v1/search/catalog", json={"q": "orbit null"})
        assert answer.status_code == 200, answer.text
        hits[name] = answer.json()["items"]
    assert [h["title"] for h in hits["alice"]] == ["Orbit Null"] and hits["alice"][0]["owned_work_id"] is None
    assert hits["bob"][0]["owned_work_id"] == shelves.work_id("Orbit Null"), "bob has it on his shelf"
    # (The page of a hit is made when the person opens it.)
    assert hits["alice"][0]["token"] is None
    ticket = alice.post("/api/v1/search/catalog", json={"q": "orbit null"}).json()["ticket"]
    key = hits["alice"][0]["key"]
    token = alice.post(f"/api/v1/search/catalog/{ticket}/books", json={"key": key}).json()["token"]
    page = alice.get(f"/api/v1/discover/books/{token}")
    assert page.status_code == 200 and page.json()["title"] == "Orbit Null"
    assert alice.put(f"/api/v1/wishlist/{token}").json()["count"] == 1


# ---- For you ---------------------------------------------------------------------------------------------------


def connect(shelves: Shelves, *, hardcover: bool = True) -> None:
    """Hardcover on (or Audible only), and alice's volume of the series named at Hardcover."""
    if hardcover:
        shelves.connect_hardcover()
        shelves.only("apple", "audible", "hardcover")
        with SessionLocal() as db:
            edition = db.scalar(select(Edition).where(Edition.title == "Die Krone der Weberinnen"))
            assert edition is not None
            db.add(ExternalRef(edition_id=edition.id, source="hardcover", kind="work", key="8101"))
            db.commit()
    else:
        shelves.only("apple", "audible")


def test_the_gaps_in_a_series_are_looked_up_in_the_background_and_then_set_against_the_shelf(shelves: Shelves) -> None:
    connect(shelves)
    alice = shelves.client(shelves.alice)
    first = alice.get("/api/v1/discover/for-you").json()
    assert first["pending"] is True and first["gaps"] == [] and first["state"] == "ready"
    assert jobs_of("series") == 1 and jobs_of("authors") >= 1
    asked = len(shelves.fake.log)
    assert len(alice.get("/api/v1/discover/for-you").json()["authors"]) >= 0
    assert len(shelves.fake.log) == asked, "no source is asked while the caller waits"
    run_all()
    body = alice.get("/api/v1/discover/for-you").json()
    assert body["pending"] is False
    [gap] = body["gaps"]
    assert gap["name"] == "Die Chroniken von Arvel" and gap["author"] == "Iris Quist"
    assert (gap["have"], gap["out"], gap["missing"], gap["soon"]) == (1, 3, 2, 1)
    assert [(v["pos"], v["state"]) for v in gap["volumes"]] == [(1.0, "own"), (2.0, "missing"), (3.0, "soon"),
                                                                (4.0, "missing")]
    assert gap["volumes"][0]["work"]["title"] == "Die Krone der Weberinnen" and gap["volumes"][0]["book"] is None
    assert gap["volumes"][2]["book"]["title"] == "Der Thron aus Bernstein" and gap["volumes"][2]["book"]["upcoming"]
    assert gap["volumes"][2]["book"]["released"] == "2027-02-01" and TODAY < "2027-02-01"
    # The volume that comes out is a page of its own.
    token = gap["volumes"][2]["book"]["token"]
    assert alice.get(f"/api/v1/discover/books/{token}").json()["title"] == "Der Thron aus Bernstein"


def test_a_series_as_hardcover_lists_it_is_five_volumes_and_the_two_the_reader_lacks(shelves: Shelves) -> None:
    """The shape of a real answer (invented here): seven places and 61 rows, because every edition and every translation
    stands under a place of its own, and a box and the copies of a prelude under 0 and 0.5. The readers say which row is
    the book. A reader who has the first three volumes lacks two, and the card says so."""
    shelves.only("hardcover")
    rows = []
    shape = {0: [19, 1, 0, 0, 0, 0, 0, 0, 0], 0.5: [1, 1, 0, 0, 0, 0, 0, 0], 1: [12374, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
             2: [8674, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0], 3: [8262, 1, 0, 0, 0, 0, 0, 0, 0], 4: [3975], 5: [3602]}
    for place, readers in shape.items():
        for n, count in enumerate(readers):
            title = f"Band {place} Ausgabe {n}" if n else f"Band {place}"
            rows.append(hardcover_row(title, place, count))
    assert len(rows) == 9 + 8 + 15 + 11 + 9 + 1 + 1
    rows.sort(key=lambda row: row.weight)  # the book is the last of its place in the answer, not the first
    volumes = hardcover.volumes_of(rows)
    assert [(v.series_pos, v.title) for v in volumes] == [(float(p), f"Band {p}") for p in (1, 2, 3, 4, 5)]
    with SessionLocal() as db:
        series = Series(name="Die Sternenwache", key="die sternenwache")
        db.add(series)
        db.flush()
        foryou._keep(db, foryou.series_key(series.id), volumes, "ebook", {"source": "hardcover"})
        mine = foryou.MySeries(series_id=series.id, name=series.name, author="Iris Quist",
                               places={1.0: 101, 2.0: 102, 3.0: 103}, works=3)
        card = foryou._series_card(db, mine, foryou._kept(foryou.series_key(series.id)) or {}, TODAY)
    assert (card.have, card.out, card.missing, card.soon) == (3, 5, 2, 0)
    assert [(v.pos, v.state) for v in card.volumes] == [(1.0, "own"), (2.0, "own"), (3.0, "own"), (4.0, "missing"),
                                                        (5.0, "missing")]


def test_more_of_the_authors_on_the_shelf_leaves_out_what_the_caller_has_and_other_people_with_the_name(
        shelves: Shelves) -> None:
    connect(shelves)
    alice = shelves.client(shelves.alice)
    alice.get("/api/v1/discover/for-you")
    run_all()
    authors = {a["name"]: a for a in alice.get("/api/v1/discover/for-you").json()["authors"]}
    mara = authors["Mara Feldkamp"]
    assert mara["on_shelf"] == 1
    # "Der Leuchtturmwaerter" is on the shelf; "Nach dem Sturm" there is by Hans Feldkamp, not by Mara.
    assert [b["title"] for b in mara["books"]] == ["Briefe aus Triest", "Nach dem Sturm"]


def test_what_somebody_else_reads_is_not_in_the_answer_and_makes_no_job_for_the_caller(shelves: Shelves) -> None:
    connect(shelves)
    shelves.client(shelves.alice).get("/api/v1/discover/for-you")
    run_all()
    series_jobs = jobs_of("series")
    for who in (shelves.bob, shelves.carol, make_account("nobody")):
        body = shelves.client(who).get("/api/v1/discover/for-you").json()
        text = json.dumps(body)
        assert body["gaps"] == [] and "Arvel" not in text and "Glas und Asche" not in text
        assert "Briefe aus Triest" not in text and "Iris Quist" not in text
    assert jobs_of("series") == series_jobs, "bob's and carol's pages queued no job for alice's series"
    operator = new_client(make_account("operator-two", "operator"))
    assert operator.get("/api/v1/discover/for-you").json()["gaps"] == []


def test_a_person_without_a_library_has_nothing_to_look_up(shelves: Shelves) -> None:
    connect(shelves)
    before = jobs_of("series") + jobs_of("authors")
    body = shelves.client(shelves.carol).get("/api/v1/discover/for-you").json()
    assert body == {"state": "ready", "sources": ["hardcover", "audible"], "pending": False, "gaps": [], "authors": []}
    assert jobs_of("series") + jobs_of("authors") == before


def test_the_series_page_of_a_book_shows_the_gaps_only_to_somebody_who_reads_the_series(shelves: Shelves) -> None:
    connect(shelves)
    alice, bob = shelves.client(shelves.alice), shelves.client(shelves.bob)
    with SessionLocal() as db:
        series_id = db.scalar(select(Series.id).where(Series.name == "Die Chroniken von Arvel"))
    assert bob.get(f"/api/v1/discover/series/{series_id}").status_code == 404
    first = alice.get(f"/api/v1/discover/series/{series_id}").json()
    assert first["pending"] is True and [v["state"] for v in first["volumes"]] == ["own"]
    run_all()
    shown = alice.get(f"/api/v1/discover/series/{series_id}").json()
    assert [v["state"] for v in shown["volumes"]] == ["own", "missing", "soon", "missing"]
    assert bob.get(f"/api/v1/discover/series/{series_id}").status_code == 404
    assert alice.get("/api/v1/discover/series/999999").status_code == 404


def test_without_hardcover_audible_names_the_volumes(shelves: Shelves) -> None:
    connect(shelves, hardcover=False)
    shelves.fake.audible = {"de": [
        audible_product("B0ARVEL002", "Glas und Asche", ["Iris Quist"], 600, series="Die Chroniken von Arvel",
                        sequence="2"),
        audible_product("B0ARVEL001", "Die Krone der Weberinnen", ["Iris Quist"], 700, series="Die Chroniken von Arvel",
                        sequence="1"),
        audible_product("B0OTHER001", "Another Series", ["Iris Quist"], 100, series="Another Series", sequence="1")]}
    alice = shelves.client(shelves.alice)
    alice.get("/api/v1/discover/for-you")
    run_all()
    [gap] = alice.get("/api/v1/discover/for-you").json()["gaps"]
    assert [(v["pos"], v["state"]) for v in gap["volumes"]] == [(1.0, "own"), (2.0, "missing")]
    assert shelves.fake.count("api.hardcover.app") == 0


def test_a_source_that_does_not_answer_leaves_no_gap_and_what_it_left_is_asked_for_again_the_next_day(
        shelves: Shelves) -> None:
    connect(shelves)
    shelves.fake.status["api.hardcover.app"] = 500
    alice = shelves.client(shelves.alice)
    alice.get("/api/v1/discover/for-you")
    run_all()
    assert alice.get("/api/v1/discover/for-you").json()["gaps"] == []
    with SessionLocal() as db:
        series_id = db.scalar(select(Series.id).where(Series.name == "Die Chroniken von Arvel"))
    # Hardcover rests after the failure; Audible knew nothing of the series: an empty answer, kept for a day only.
    kept = cache.get("discover", foryou.series_key(series_id))
    assert kept["volumes"] == [] and kept["source"] == "audible"
    shelves.days(2)
    assert cache.get("discover", foryou.series_key(series_id)) is cache.MISSING


def test_with_the_switch_off_the_page_says_what_is_missing_and_queues_nothing(shelves: Shelves) -> None:
    connect(shelves)
    shelves.settings(sources_enabled=False)
    alice = shelves.client(shelves.alice)
    body = alice.get("/api/v1/discover/for-you").json()
    assert body["state"] == "main_off" and body["gaps"] == [] and body["authors"] == [] and body["pending"] is False
    assert jobs_of("series") == 0 and jobs_of("authors") == 0
    shelves.settings(sources_enabled=True, source_audible=False, source_hardcover=False)
    assert alice.get("/api/v1/discover/for-you").json()["state"] == "no_source"


def test_a_series_job_for_a_series_that_is_gone_gives_up(shelves: Shelves) -> None:
    with SessionLocal() as db:
        jobs.enqueue(db, "series", ref=424242)
        jobs.enqueue(db, "authors", ref=424242)
        db.commit()
    run_all()
    with SessionLocal() as db:
        states = [j.state for j in db.scalars(select(Job).where(Job.kind.in_(("series", "authors"))))]
    assert states == ["failed", "failed"]


def test_a_book_no_source_answered_for_on_the_last_try_is_marked_so_that_nobody_queues_it_for_ever(
        shelves: Shelves) -> None:
    from types import SimpleNamespace

    from app.services.discover import enrich

    carol = shelves.client(shelves.carol)
    token_of(carol, "Orbit Null")
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Orbit Null"))
        assert row is not None
        shelves.fake.status["api.audible.de"] = 500
        try:
            enrich.enrich_job(db, SimpleNamespace(ref=row.id, attempts=1))
        except jobs.Retry:
            pass
        else:
            raise AssertionError("an early try with no answer is tried again")
        assert db.get(ForeignBook, row.id).enriched_at is None  # type: ignore[union-attr]
        shelves.days(1)
        shelves.fake.status["api.audible.de"] = 500
        enrich.enrich_job(db, SimpleNamespace(ref=row.id, attempts=jobs.ATTEMPTS))
        assert db.get(ForeignBook, row.id).enriched_at is not None  # type: ignore[union-attr]
