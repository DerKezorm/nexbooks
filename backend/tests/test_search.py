"""The search over the books: found by the start of words, only among what the caller may see, with the same cost for
everybody, kept up to date with every change of a book, and asked in a way that leaves nothing for SQL or the index
syntax to read.

The rule under test is the one of the whole interface: **what a person may not see is not there.** It is not found, not
counted, does not decide the order, and takes no time of its own: every word in the index carries its library, and a
search asks for the words of the caller's libraries only."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import clock
from app.db import SessionLocal
from app.models import Account, Edition, Library, LibraryMember, SearchEntry
from app.services import brakes, search, search_index

from .bookmaker import cover_image
from .conftest import TAB, make_account, new_client
from .library_helpers import book, books_folder, make_library, recording, run_jobs, scan


class Shelves:
    """Two libraries and four people: tester (operator, both), rita (family only), sam (club only), tom (none).

    ``Alpha`` is in both libraries (the e-book in the family's, the recording in the club's) with a word only in each of
    them; ``Delta`` and ``Zeta`` lie in the club only, ``Gamma`` and ``Beta`` in the family's."""

    def __init__(self) -> None:
        self.family = books_folder("family")
        self.club = books_folder("club")
        book(self.family, "alpha.epub", title="Alpha Lighthouse", authors=("Ada Feldkamp",), language="de",
             series="Saga", series_pos=1, cover=cover_image((190, 30, 40)), subjects=("Roman",),
             description="A keeper of the family lamp.", isbn="9783999900100")
        book(self.family, "beta.epub", title="Beta", authors=("Ben Hollister",), language="en", series="Saga",
             series_pos=2, subjects=("Fantasy",), description="Seaweed everywhere.")
        book(self.family, "gamma.epub", title="Über den Wolken", authors=("Eva Müller",), language="de",
             subjects=("Krimi",), description="Die Straße nach Süden.")
        recording(self.club, "alpha/alpha.m4b", 300.0, title="Alpha Lighthouse", album="Alpha Lighthouse",
                  artist="Ada Feldkamp", narrator="Clara Vienne")
        book(self.club, "delta.epub", title="Delta Harbour", authors=("Ada Feldkamp",), language="de",
             subjects=("Roman",), description="Kelp and tides.")
        book(self.club, "zeta.epub", title="Zeta", authors=("Zed Quist",), language="en", subjects=("Roman",),
             description="Kelp and tides again.")
        self.family_id = make_library(self.family, "Family", scan=True)
        self.club_id = make_library(self.club, "Club", scan=True)
        self.rita = make_account("rita")
        self.sam = make_account("sam")
        self.tom = make_account("tom")
        with SessionLocal() as db:
            tester = db.scalar(select(Account).where(Account.name == "tester"))
            assert tester is not None
            db.query(LibraryMember).delete()
            for user, library, right in ((tester.id, self.family_id, "manage"), (tester.id, self.club_id, "manage"),
                                         (self.rita.id, self.family_id, "read"), (self.sam.id, self.club_id, "read")):
                db.add(LibraryMember(library_id=library, user_id=user, right=right))
            db.commit()


@pytest.fixture
def shelves(client: TestClient, operator: Account) -> Shelves:
    return Shelves()


#: Where the rows a test writes straight into the index start. No edition comes near it: a row on the number of an edition
#: takes its place in the table of sizes and leaves the words of the edition in the index with nothing to delete them by.
CROWD = 1_000_000_000


@pytest.fixture(autouse=True)
def no_crowd_left() -> Iterator[None]:
    """No trigger takes those rows away: they have no edition."""
    yield
    with SessionLocal() as db:
        db.execute(text("DELETE FROM book_fts WHERE rowid >= :first"), {"first": CROWD})
        db.commit()


def found(client: TestClient, q: str, **params: object) -> list[str]:
    answer = client.get("/api/v1/search", params={"q": q, **params})
    assert answer.status_code == 200, answer.text
    return [item["title"] for item in answer.json()["items"]]


# ---- What is found --------------------------------------------------------------------------------------------------


def test_every_word_is_the_start_of_a_word_and_all_of_them_must_be_found(client: TestClient, shelves: Shelves) -> None:
    assert found(client, "alp") == ["Alpha Lighthouse"]
    assert found(client, "light alp") == ["Alpha Lighthouse"], "the order of the words does not matter"
    assert found(client, "alpha delta") == [], "every word must be found"
    assert found(client, "house") == [], "the start of a word, not a part of one"
    assert found(client, "ALPHA") == ["Alpha Lighthouse"]


def test_it_looks_in_the_title_the_people_the_series_the_description_and_the_isbn(client: TestClient,
                                                                                   shelves: Shelves) -> None:
    assert sorted(found(client, "feldk")) == ["Alpha Lighthouse", "Delta Harbour"]
    assert sorted(found(client, "saga")) == ["Alpha Lighthouse", "Beta"]
    assert found(client, "seaweed") == ["Beta"]
    assert found(client, "9783999900100") == ["Alpha Lighthouse"]
    # The narrator of a recording is a person of the book, too.
    assert found(client, "vienne") == ["Alpha Lighthouse"]


def test_the_title_weighs_more_than_the_description(client: TestClient, shelves: Shelves) -> None:
    # "kelp" is in the description of Delta and Zeta alike; "zeta" in the title of one and a description of none.
    assert sorted(found(client, "kelp")) == ["Delta Harbour", "Zeta"]
    with SessionLocal() as db:
        zeta = db.scalar(select(Edition).where(Edition.title == "Zeta"))
        assert zeta is not None
        zeta.description = "Harbour lights and more Delta."
        db.commit()
        search_index.update(db, [zeta.id])
        db.commit()
    # "delta": the title of one book, a word in the description of the other.
    assert found(client, "delta") == ["Delta Harbour", "Zeta"]


def test_accents_and_the_sharp_s_do_not_count(client: TestClient, shelves: Shelves) -> None:
    assert found(client, "uber wolken") == ["Über den Wolken"]
    assert found(client, "ÜBER") == ["Über den Wolken"]
    assert found(client, "muller") == ["Über den Wolken"]
    assert found(client, "strasse") == ["Über den Wolken"]
    assert found(client, "Straße süden") == ["Über den Wolken"]


def test_nothing_a_person_types_is_syntax(client: TestClient, shelves: Shelves) -> None:
    for query in ['"', "*", "alpha*", 'alpha" OR "x', "NEAR(a b)", "alpha OR beta", "title:alpha", "-alpha", "(", ")",
                  "'; DROP TABLE editions; --", "a" * 200, "\x00alpha", "{scope} : x", "^alpha", "alpha AND"]:
        answer = client.get("/api/v1/search", params={"q": query})
        assert answer.status_code == 200, (query[:30], answer.text[:200])
    assert found(client, "alpha OR beta") == [], "OR is a word like any other here, and 'or' is nobody's title"
    assert found(client, "") == []
    assert found(client, "   ") == []
    with SessionLocal() as db:
        assert db.scalar(select(text("count(*)")).select_from(Edition)) == 6


def test_a_page_is_cut_to_the_limit_and_the_limit_is_checked(client: TestClient, shelves: Shelves) -> None:
    assert len(found(client, "a", limit=2)) == 2
    assert client.get("/api/v1/search", params={"q": "a", "limit": 0}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "a", "limit": 500}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "x" * 400}).status_code == 422


def test_the_answer_is_the_work_as_the_caller_sees_it(client: TestClient, shelves: Shelves) -> None:
    answer = client.get("/api/v1/search", params={"q": "alpha"}).json()
    [item] = answer["items"]
    assert {"id", "title", "authors", "color", "cover_url", "editions", "audio_only"} <= set(item)
    assert sorted(e["kind"] for e in item["editions"]) == ["audio", "ebook"]


# ---- Who finds what -------------------------------------------------------------------------------------------------


def test_each_person_finds_only_the_books_of_their_libraries(client: TestClient, shelves: Shelves) -> None:
    with new_client(shelves.rita) as rita, new_client(shelves.sam) as sam, new_client(shelves.tom) as tom:
        assert found(rita, "alpha") == ["Alpha Lighthouse"]
        assert found(rita, "delta") == [] and found(rita, "zeta") == [] and found(rita, "kelp") == []
        assert found(sam, "delta") == ["Delta Harbour"]
        assert found(sam, "beta") == [] and found(sam, "seaweed") == [] and found(sam, "uber") == []
        assert found(tom, "alpha") == [] and found(tom, "a") == []
        assert sorted(found(client, "kelp")) == ["Delta Harbour", "Zeta"]


def test_a_work_in_two_libraries_is_found_by_what_the_caller_may_see_of_it(client: TestClient, shelves: Shelves) -> None:
    with new_client(shelves.rita) as rita, new_client(shelves.sam) as sam:
        # The narrator is on the club's recording, the lamp in the family's e-book.
        assert found(sam, "vienne") == ["Alpha Lighthouse"] and found(rita, "vienne") == []
        assert found(rita, "lamp") == ["Alpha Lighthouse"] and found(sam, "lamp") == []
        assert found(rita, "9783999900100") == ["Alpha Lighthouse"] and found(sam, "9783999900100") == []
        # What the caller sees of the work is what they see of it everywhere: Sam sees the recording only.
        [item] = sam.get("/api/v1/search", params={"q": "alpha"}).json()["items"]
        assert [e["kind"] for e in item["editions"]] == ["audio"]


def test_a_book_without_a_file_on_the_disk_is_not_found(client: TestClient, shelves: Shelves) -> None:
    with SessionLocal() as db:
        zeta = db.scalar(select(Edition).where(Edition.title == "Zeta"))
        assert zeta is not None
        zeta.missing_at = clock.now()
        db.commit()
    assert found(client, "zeta") == []
    assert found(client, "delta") == ["Delta Harbour"]


def test_what_the_caller_may_not_see_does_not_take_a_place_in_the_answer(client: TestClient, shelves: Shelves) -> None:
    """The cut-off of the answer is made by the statement that carries the rights, so a book that is gone (or in no library
    of the caller) can never push a visible one out of a short list."""
    with SessionLocal() as db:
        delta = db.scalar(select(Edition).where(Edition.title == "Delta Harbour"))
        zeta = db.scalar(select(Edition).where(Edition.title == "Zeta"))
        assert delta is not None and zeta is not None
        # The better hit of "kelp" by far, and gone from the disk.
        delta.description = "Kelp kelp kelp kelp kelp kelp kelp kelp."
        delta.missing_at = clock.now()
        db.commit()
        search_index.update(db, [delta.id])
        db.commit()
    assert found(client, "kelp", limit=1) == ["Zeta"]


def test_a_removed_library_leaves_nothing_in_the_index(client: TestClient, shelves: Shelves) -> None:
    with SessionLocal() as db:
        before = db.execute(text("SELECT count(*) FROM book_fts_docsize")).scalar()
        assert before == 6
        db.delete(db.get(Library, shelves.club_id))
        db.commit()
        assert db.execute(text("SELECT count(*) FROM book_fts_docsize")).scalar() == 3
        assert db.scalar(select(text("count(*)")).select_from(SearchEntry)) == 3
    assert found(client, "kelp") == []


# ---- The same cost for everybody ------------------------------------------------------------------------------------


def test_what_others_have_costs_a_search_nothing(client: TestClient, shelves: Shelves,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The same search for a person who has no book with the word, while another library holds hundreds with it, and for
    one who has: the work SQLite does is the same however many books the others have, step for step."""
    # The search puts its own time limit on the connection (``search._bounded``); here the counter is the only handler.
    monkeypatch.setattr(search, "_bounded", contextlib.nullcontext)
    with SessionLocal() as db:
        # A second library with a crowd of books that all carry the word "kelp" (rows straight into the index).
        for number in range(400):
            db.execute(text("INSERT INTO book_fts (rowid, title, original, people, series, description, ids) "
                            "VALUES (:id, :t, '', '', '', '', '')"),
                       {"id": CROWD + number, "t": f"l{shelves.club_id}lxkelp l{shelves.club_id}lxkelp{number}"})
        db.commit()

    def run(person: Account) -> int:
        steps = 0

        def tick() -> int:
            nonlocal steps
            steps += 1
            return 0

        with SessionLocal() as db:
            raw = db.connection().connection.driver_connection
            raw.set_progress_handler(tick, 1)
            try:
                search.work_ids(db, person.id, "kelp")
            finally:
                raw.set_progress_handler(None, 0)
        return steps

    run(shelves.rita)  # The first run reads the schema; the numbers below are of runs that have nothing to learn.
    rita_steps = run(shelves.rita)
    tom_steps = run(shelves.tom)
    sam_steps = run(shelves.sam)
    # Rita and Tom have no book with the word; Sam has hundreds. What Rita does does not grow with what Sam has.
    assert rita_steps == run(shelves.rita)
    assert tom_steps < rita_steps, "no library, no query"
    assert sam_steps > rita_steps * 3, (rita_steps, sam_steps)
    # Grow Sam's crowd: Rita's work is the same number of steps.
    with SessionLocal() as db:
        for number in range(400, 1200):
            db.execute(text("INSERT INTO book_fts (rowid, title, original, people, series, description, ids) "
                            "VALUES (:id, :t, '', '', '', '', '')"),
                       {"id": CROWD + number, "t": f"l{shelves.club_id}lxkelp l{shelves.club_id}lxkelp{number}"})
        db.commit()
    # A bigger index is a little more to walk for everybody (a few levels of its tree), whatever the books are; what
    # Sam's books cost Sam grows with them, and Rita's search grows by none of it.
    after = run(shelves.rita)
    assert after <= rita_steps * 1.15, (rita_steps, after)
    assert run(shelves.sam) > sam_steps * 2


def test_the_time_does_not_tell_either(client: TestClient, shelves: Shelves) -> None:
    """The clock says the same as the steps: a search for a word that thousands of other people's books carry takes as
    long as one for a word nobody has."""
    with SessionLocal() as db:
        for number in range(3000):
            db.execute(text("INSERT INTO book_fts (rowid, title, original, people, series, description, ids) "
                            "VALUES (:id, :t, '', '', '', '', '')"),
                       {"id": CROWD + number, "t": f"l{shelves.club_id}lxfoam l{shelves.club_id}lxfoam{number}"})
        db.commit()
    with new_client(shelves.rita) as rita:
        def best(q: str) -> float:
            times = []
            for _ in range(15):
                started = time.perf_counter()
                assert rita.get("/api/v1/search", params={"q": q}).status_code == 200
                times.append(time.perf_counter() - started)
            return min(times)

        brakes.forget()
        with_foreign = best("foam")
        brakes.forget()
        nothing = best("zzzzz")
        # Generous: a factor of three would show a crowd of 3,000 entries read; noise stays well below it.
        assert with_foreign < nothing * 3 + 0.01, (with_foreign, nothing)


# ---- The index follows the books ------------------------------------------------------------------------------------


def test_a_correction_by_hand_is_found_at_once_and_the_old_words_are_gone(client: TestClient, shelves: Shelves) -> None:
    with SessionLocal() as db:
        beta = db.scalar(select(Edition).where(Edition.title == "Beta"))
        assert beta is not None
        beta_id = beta.id
    answer = client.patch(f"/api/v1/editions/{beta_id}", json={"title": "Omega Reef", "authors": ["Quentin Rowe"]})
    assert answer.status_code == 200, answer.text
    assert found(client, "omega") == ["Omega Reef"] and found(client, "rowe") == ["Omega Reef"]
    assert found(client, "beta") == [] and found(client, "hollister") == []
    assert found(client, "seaweed") == ["Omega Reef"], "the rest of the entry is still there"


def test_a_changed_file_is_found_by_its_new_words(client: TestClient, shelves: Shelves) -> None:
    book(shelves.family, "gamma.epub", title="Wolken Atlas", authors=("Eva Müller",), language="de",
         subjects=("Krimi",), description="Neu geschrieben.")
    scan(shelves.family_id)
    assert found(client, "atlas") == ["Wolken Atlas"] and found(client, "neu") == ["Wolken Atlas"]
    assert found(client, "uber") == [] and found(client, "straße") == []


def test_a_book_the_scan_adds_is_found_and_one_it_loses_is_not(client: TestClient, shelves: Shelves) -> None:
    book(shelves.family, "eta.epub", title="Eta Meridian", authors=("Iris Stein",), language="de")
    scan(shelves.family_id)
    assert found(client, "meridian") == ["Eta Meridian"]
    (shelves.family / "eta.epub").unlink()
    scan(shelves.family_id)
    assert found(client, "meridian") == []


def test_the_job_fills_an_empty_index_and_changes_nothing_that_is_right(client: TestClient, shelves: Shelves) -> None:
    with SessionLocal() as db:
        db.execute(text("DELETE FROM book_fts"))
        db.execute(text("DELETE FROM search_entries"))
        # An entry for an edition that is not there.
        db.execute(text("INSERT INTO book_fts (rowid, title) VALUES (999, 'l1lxghost')"))
        db.commit()
    assert found(client, "alpha") == []
    search_index.reindex_all()
    assert found(client, "alpha") == ["Alpha Lighthouse"]
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM book_fts_docsize")).scalar() == 6
        ids = {row[0] for row in db.execute(text("SELECT id FROM book_fts_docsize"))}
        assert 999 not in ids
        written = search_index.update(db, [e.id for e in db.scalars(select(Edition))])
        assert written == 0, "an entry whose words did not change is not written again"


def test_the_job_is_queued_by_the_step_and_run_by_the_loop(client: TestClient, shelves: Shelves) -> None:
    from app.services import jobs

    assert "index" in jobs.HANDLERS
    with SessionLocal() as db:
        jobs.enqueue(db, "index")
        db.commit()
    assert run_jobs() >= 1


def test_a_search_that_runs_too_long_is_stopped_by_the_database_and_answers_503(client: TestClient, shelves: Shelves,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    assert search.MAX_SECONDS == 5.0, "five seconds are what a search may take"
    ok = client.get("/api/v1/search", params={"q": "alpha"})
    assert ok.status_code == 200 and ok.json()["items"]
    # No time at all, and the database asked at every step: the search is cut off whatever it is.
    monkeypatch.setattr(search, "MAX_SECONDS", 0.0)
    monkeypatch.setattr(search, "PROGRESS_STEPS", 1)
    stopped = client.get("/api/v1/search", params={"q": "alpha"})
    assert stopped.status_code == 503
    assert stopped.json()["detail"]["code"] == "search_too_slow"
    # The limit belongs to that one search: the next one, with the time back, finds again.
    monkeypatch.setattr(search, "MAX_SECONDS", 5.0)
    monkeypatch.setattr(search, "PROGRESS_STEPS", 10_000)
    again = client.get("/api/v1/search", params={"q": "alpha"})
    assert again.status_code == 200 and again.json()["items"]


# ---- Doors ----------------------------------------------------------------------------------------------------------


def test_a_search_needs_a_sign_in_and_is_slowed_down(client: TestClient, shelves: Shelves,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(client.app, base_url="http://testserver") as anonymous:
        assert anonymous.get("/api/v1/search", params={"q": "alpha"}).status_code == 401
        assert anonymous.post("/api/v1/search/catalog", json={"q": "alpha"}, headers=TAB).status_code == 401
    brakes.forget()
    # The minute of the brake is the clock's, held still: how long the machine needs for the requests decides nothing.
    now = {"seconds": 5000.0}
    monkeypatch.setattr(clock, "monotonic", lambda: now["seconds"])
    statuses = [client.get("/api/v1/search", params={"q": "alpha"}).status_code for _ in range(brakes.LIMITS["search"] + 3)]
    assert statuses[: brakes.LIMITS["search"]] == [200] * brakes.LIMITS["search"]
    assert statuses[-3:] == [429] * 3
    # A minute later the oldest have gone out of the window and a search passes again.
    now["seconds"] += 61
    assert client.get("/api/v1/search", params={"q": "alpha"}).status_code == 200


def test_a_program_with_a_key_searches_as_its_account_and_never_from_a_web_page(client: TestClient,
                                                                                  shelves: Shelves) -> None:
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    with new_client(shelves.rita) as rita:
        secret = rita.post("/api/v1/api-keys", json={"name": "nexdeck"}).json()["secret"]
    assert secret.startswith("nbk_")
    with TestClient(client.app, base_url="http://testserver") as program:
        auth = {"Authorization": f"Bearer {secret}"}
        mine = program.get("/api/v1/search", params={"q": "s"}, headers=auth)
        assert mine.status_code == 200
        titles = sorted(item["title"] for item in mine.json()["items"])
        assert titles == ["Alpha Lighthouse", "Beta", "Über den Wolken"], "only Rita's libraries"
        assert program.get("/api/v1/search", params={"q": "delta"}, headers=auth).json()["items"] == []
        refused = program.get("/api/v1/search", params={"q": "a"}, headers={**auth, "Origin": "https://example.com"})
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "origin_refused"
        # A key reads; the catalogue of the sources costs the sources a request and is for a browser.
        assert program.post("/api/v1/search/catalog", json={"q": "alpha"}, headers=auth).status_code == 403
        assert program.get("/api/v1/search", params={"q": "a"},
                           headers={"Authorization": "Bearer nbk_" + "x" * 43}).status_code == 401
        # The same wall around the first routes that read a book: the work, the cover, a file.
        work_id = mine.json()["items"][0]["id"]
        assert program.get(f"/api/v1/works/{work_id}", headers=auth).status_code == 200
        assert program.get(f"/api/v1/works/{work_id}", headers={**auth, "Origin": "https://example.com"}).status_code == 403
