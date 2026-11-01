"""Searches: a search that lost its job, a progress that fills in line by line, the same search
shown again, languages the indexers do not know, accents in a query, ``t=book`` answers that do not hold the book, one
release at a time for a book and a kind, blocks that outlive a work, and the places that were left unguarded.
Invented names, example.com, keys made at run time."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app import clock
from app.db import SessionLocal
from app.models import (
    Account,
    BlocklistEntry,
    Download,
    Edition,
    Indexer,
    Job,
    Library,
    LibraryMember,
    ReleaseSearch,
    Work,
)
from app.services import jobs
from app.services.procure import blocklist, loading, torrent
from app.services.procure import search as search_service

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, join, make_library, run_jobs, scan
from .procure_fakes import FakeNetwork, FakeSabnzbd
from .procure_search_helpers import (
    TORRENT_HOST,
    FeedIndexer,
    Item,
    add_indexer,
    add_program,
    allow_languages,
    by_title,
    codes,
    make_world,
    search,
)

GOOD = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"
SECOND = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.AZW3-EXAMPLE"
AUDIO = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE"


def start(world: Any, **payload: Any) -> Any:
    return world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, **payload})


def sabnzbd(net: FakeNetwork) -> FakeSabnzbd:
    key = secrets.token_hex(16)
    fake = net.add(FakeSabnzbd("sab.example.com", key))
    add_program("sabnzbd", "sab.example.com", secret=key)
    return fake  # type: ignore[no-any-return]


# --- A search without a job is over --------------------------------------------------------------------------------


def test_a_search_whose_job_was_given_up_is_failed_and_holds_no_place(operator: Any, net: FakeNetwork,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(search_service, "_keep", broken)
    allow_languages("de", "en", "fr", "es")
    for language in ("de", "en", "fr"):
        answer = start(world, language=language)
        assert answer.status_code == 202, answer.text
        run_jobs()
        shown = world.client.get(f"/api/v1/procure/searches/{answer.json()['id']}").json()
        assert shown["state"] == "failed" and shown["error"] == "search_failed"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ReleaseSearch).where(
            ReleaseSearch.state.in_(("queued", "running")))) == 0
    # The fourth is not turned away with "too many searches": none of the failed ones holds a place.
    monkeypatch.undo()
    assert start(world, language="es").status_code == 202


def test_a_search_a_stopped_server_left_without_a_job_is_failed_at_the_start(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    answer = start(world, language="de")
    search_id = answer.json()["id"]
    with SessionLocal() as db:
        # What a crash can leave: the search runs, its job is gone.
        db.execute(update(ReleaseSearch).values(state="running"))
        db.execute(delete(Job))
        db.commit()
    stop = asyncio.Event()
    stop.set()
    asyncio.run(jobs.run_forever(stop, lane="procure"))
    jobs.stopping.clear()
    shown = world.client.get(f"/api/v1/procure/searches/{search_id}").json()
    assert shown["state"] == "failed"


def test_a_search_without_a_job_does_not_hold_one_of_the_three_places(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    allow_languages("de", "en", "fr", "es")
    for language in ("de", "en", "fr"):
        assert start(world, language=language).status_code == 202
    with SessionLocal() as db:
        # Their jobs are gone (a crash, a restore): nothing would ever finish them.
        db.execute(delete(Job))
        db.execute(update(ReleaseSearch).values(state="running"))
        db.commit()
    again = start(world, language="es")
    assert again.status_code == 202, again.text
    with SessionLocal() as db:
        states = sorted(db.scalars(select(ReleaseSearch.state)))
    assert states == ["failed", "failed", "failed", "queued"]


def test_a_search_that_still_has_its_job_is_left_alone_at_the_start(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    search_id = start(world, language="de").json()["id"]
    stop = asyncio.Event()
    stop.set()
    asyncio.run(jobs.run_forever(stop, lane="procure"))
    jobs.stopping.clear()
    assert world.client.get(f"/api/v1/procure/searches/{search_id}").json()["state"] == "queued"
    run_jobs()
    assert world.client.get(f"/api/v1/procure/searches/{search_id}").json()["state"] == "done"


# --- The progress fills in line by line ------------------------------------------------------------------------------


class Gated(FeedIndexer):
    """An indexer that answers a search only when the test lets it."""

    def __init__(self, host: str, key: str) -> None:
        super().__init__(host, key)
        self.gate = threading.Event()
        self.asked = threading.Event()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("t") in ("search", "book"):
            self.asked.set()
            self.gate.wait(20)
        return super().handle(request)


def test_a_line_of_the_progress_is_done_when_its_indexer_is_not_when_all_are(operator: Any,
                                                                            net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    slow = net.add(Gated("slow.example.com", "s" * 32))
    add_indexer(slow, name="Slow", priority=2)
    slow.items = [Item(SECOND)]
    search_id = start(world, language="de").json()["id"]
    worker = threading.Thread(target=run_jobs)
    worker.start()
    try:
        assert slow.asked.wait(20)
        deadline = time.monotonic() + 20
        lines: dict[str, Any] = {}
        while time.monotonic() < deadline:
            shown = world.client.get(f"/api/v1/procure/searches/{search_id}").json()
            lines = {entry["name"]: entry for entry in shown["indexers"]}
            if lines.get("Shelf", {}).get("state") == "done":
                break
            time.sleep(0.05)
        assert shown["state"] == "running"
        assert lines["Shelf"]["state"] == "done" and lines["Shelf"]["found"] == 1
        assert lines["Slow"]["state"] == "searching"
    finally:
        slow.gate.set()
        worker.join(30)
    shown = world.client.get(f"/api/v1/procure/searches/{search_id}").json()
    assert shown["state"] == "done" and {entry["state"] for entry in shown["indexers"]} == {"done"}


# --- The same search shown again ---------------------------------------------------------------------------------------


def test_a_book_looked_for_again_within_fifteen_minutes_shows_the_search_that_finished(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    asked = len(world.indexer.requests)
    again = start(world, language="de")
    assert again.status_code == 202 and again.json()["id"] == first["id"] and again.json()["state"] == "done"
    assert len(again.json()["releases"]) == 1
    assert len(world.indexer.requests) == asked


def test_search_again_asks_the_indexers_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    asked = len(world.indexer.requests)
    fresh = start(world, language="de", new=True)
    assert fresh.json()["id"] != first["id"] and fresh.json()["state"] == "queued"
    run_jobs()
    assert len(world.indexer.requests) > asked


def test_an_old_search_another_language_or_another_book_is_not_shown_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    world.indexer.items = [Item(GOOD)]
    first = search(world, language="de")
    assert start(world, language="fr").json()["id"] != first["id"]
    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).where(ReleaseSearch.id == first["id"]).values(
            finished_at=clock.now() - timedelta(minutes=16)))
        db.commit()
    assert start(world, language="de").json()["id"] != first["id"]


def test_a_failed_search_is_never_shown_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    first = search(world, language="de")
    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).where(ReleaseSearch.id == first["id"]).values(state="failed"))
        db.commit()
    assert start(world, language="de").json()["id"] != first["id"]


def test_a_search_is_not_shown_to_another_person_or_for_other_kinds(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    first = search(world, language="de")
    tom = make_account("tom")
    join(world.library_id, tom.id, "acquire")
    other = new_client(tom).post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    assert other.json()["id"] != first["id"]
    # The kinds the person may ask for changed (their library takes only e-books now): a new search.
    with SessionLocal() as db:
        db.execute(update(Library).values(kind="ebook"))
        db.commit()
    assert start(world, language="de").json()["id"] != first["id"]


# --- What is there is told by version to the judgement ------------------------------------------------------------------


def test_the_search_tells_the_judgement_what_is_there_by_version(operator: Any, net: FakeNetwork,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """An abridged recording at the goal is what the book has: an unabridged release still takes its place."""
    world = make_world(net)
    real = search_service.subject_of_work

    def with_an_abridged_flac(db: Any, uid: int, work_id: int, languages: Any) -> dict[str, Any]:
        subject = real(db, uid, work_id, languages)
        subject["have"] = {**subject["have"], "audio": "flac"}
        subject["have_by"] = {**subject["have_by"], "audio": {"abridged": "flac"}}
        return subject  # type: ignore[no-any-return]

    monkeypatch.setattr(search_service, "subject_of_work", with_an_abridged_flac)
    unabridged = "Rune Falkner-Die Laternenstrasse-Ungekuerzt-MP3-128kbps-DE-2021-EXAMPLE"
    world.indexer.items = [Item(unabridged, size=400_000_000, categories=(3130,))]
    rows = by_title(search(world, language="de"))
    assert rows[unabridged]["rank"] == 1 and rows[unabridged]["reasons"] == []


# --- A language the indexers do not know -----------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["ja", "xx", "nb"])
def test_a_language_the_indexers_do_not_know_takes_every_accepted_language(operator: Any, net: FakeNetwork,
                                                                           language: str) -> None:
    world = make_world(net, language="fr")
    world.indexer.items = [
        Item(GOOD), Item("Rune.Falkner.-.The.Lantern.Road.ENGLISH.EPUB-EXAMPLE"),
        Item("Rune.Falkner.-.Die.Laternenstrasse.FRENCH.EPUB-EXAMPLE")]
    found = search(world, language=language)
    assert found["language"] == "" and found["languages"] == ["de", "en"]
    rows = by_title(found)
    assert rows[GOOD]["rank"] is not None
    assert "other_language" in codes(rows["Rune.Falkner.-.Die.Laternenstrasse.FRENCH.EPUB-EXAMPLE"])
    # English is accepted as well as German: a release marked English is not "another language" (this one is another
    # book, but not for its language).
    assert "other_language" not in codes(rows["Rune.Falkner.-.The.Lantern.Road.ENGLISH.EPUB-EXAMPLE"])


def test_the_language_of_the_person_that_the_indexers_do_not_know_is_the_same(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    with SessionLocal() as db:
        db.execute(update(Account).where(Account.id == world.person.id).values(language="ja"))
        db.commit()
    answer = start(world)
    assert answer.status_code == 202 and answer.json()["language"] == "" and answer.json()["languages"] == ["de", "en"]
    status = world.client.get(f"/api/v1/procure/works/{world.work_id}")
    assert status.status_code == 200 and status.json()["allowed"] is True


def test_every_accepted_language_counts_for_the_categories_and_for_what_is_there(operator: Any,
                                                                                 net: FakeNetwork) -> None:
    world = make_world(net)
    with SessionLocal() as db:
        db.execute(update(Indexer).values(
            categories=[7020, 7120, 7220, 7320, 3030, 3130, 3230, 3330],
            category_langs={"7120": "de", "7220": "en", "7320": "fr", "3130": "de", "3230": "en", "3330": "fr"}))
        db.commit()
    search(world, language="ja")
    asked = {request.url.params.get("cat") for request in world.indexer.requests
             if request.url.params.get("t") == "search"}
    # German and English are accepted: the categories of both and the mixed ones, not the French.
    assert asked == {"7020,7120,7220,3030,3130,3230"}


def test_the_book_in_the_library_in_an_accepted_language_is_what_is_there(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net, language="en")
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="ja")
    ebook = next(card for card in found["cards"] if card["kind"] == "ebook")
    assert ebook["have"] == "epub"


# --- Accents, and the original title stays -----------------------------------------------------------------------------


def test_a_query_is_also_written_without_accents() -> None:
    subject = {"authors": ["Elodie Marchand"], "titles": [{"text": "La Lumière du port", "language": "fr"}]}
    assert search_service.queries(subject, ["fr"]) == ["Elodie Marchand La Lumière du port",
                                                       "Elodie Marchand La Lumiere du port"]
    subject = {"authors": ["Åsa Lindqvist"], "titles": [{"text": "Ça", "language": "fr"}]}
    assert "Asa Lindqvist Ca" in search_service.queries(subject, ["fr"])
    subject = {"authors": ["Rune Falkner"], "titles": [{"text": "Højt og lavt", "language": "da"}]}
    assert "Rune Falkner Hojt og lavt" in search_service.queries(subject, ["da"])


def test_the_limit_of_queries_keeps_every_title_in_its_first_spelling() -> None:
    subject = {"authors": ["Rune Falkner"], "titles": [
        {"text": "Die Mörderstraße", "language": "de"}, {"text": "Der Männerweg", "language": "de"},
        {"text": "La Rue de la Mort", "language": "fr"}]}
    found = search_service.queries(subject, ["de", "fr"])
    assert len(found) == search_service.MAX_QUERIES
    assert found[:3] == ["Rune Falkner Die Mörderstraße", "Rune Falkner Der Männerweg",
                         "Rune Falkner La Rue de la Mort"]


# --- T=book that gives the author but not the book ---------------------------------------------------------------------


def test_a_t_book_answer_with_the_author_but_another_book_leaves_the_free_text_to_ask(operator: Any,
                                                                                      net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.book_items = [Item("Rune.Falkner.-.Nebelchor.03.-.Die.Brandung.GERMAN.EPUB-EXAMPLE")]
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    free = {request.url.params.get("cat") for request in world.indexer.requests
            if request.url.params.get("t") == "search"}
    assert any("7120" in (cats or "") for cats in free), free
    assert by_title(found)[GOOD]["rank"] == 1


def test_a_t_book_answer_with_the_book_spares_the_free_text_of_the_e_books(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.book_items = [Item(GOOD)]
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,))]
    search(world, language="de")
    free = {request.url.params.get("cat") for request in world.indexer.requests
            if request.url.params.get("t") == "search"}
    assert free == {"3030,3130"}


# --- One release at a time for a book and a kind ------------------------------------------------------------------------


def searched_two(net: FakeNetwork) -> tuple[Any, dict[str, Any]]:
    world = make_world(net)
    world.indexer.items = [Item(GOOD), Item(SECOND), Item(AUDIO, size=400_000_000, categories=(3130,))]
    return world, search(world, language="de")


def release_of(found: dict[str, Any], title: str) -> int:
    return next(item["id"] for item in found["releases"] if item["title"] == title)


def load(world: Any, found: dict[str, Any], title: str, **body: Any) -> httpx.Response:
    return world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release_of(found, title)}/load",
                             json=body)


def test_a_book_loads_one_release_of_a_kind_at_a_time(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched_two(net)
    assert load(world, found, GOOD).status_code == 201
    second = load(world, found, SECOND)
    assert second.status_code == 409 and second.json()["detail"]["code"] == "already_loading"
    assert len(sab.jobs) == 1
    # Another kind of the same book is another load.
    assert load(world, found, AUDIO).status_code == 201
    assert len(sab.jobs) == 2
    # Another person's search for the same book is the same book.
    tom = make_account("tom")
    join(world.library_id, tom.id, "acquire")
    as_tom = new_client(tom)
    other = as_tom.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"}).json()
    run_jobs()
    shown = as_tom.get(f"/api/v1/procure/searches/{other['id']}").json()
    release = next(item["id"] for item in shown["releases"] if item["title"] == SECOND)
    refused = as_tom.post(f"/api/v1/procure/searches/{other['id']}/releases/{release}/load", json={})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "already_loading"


def test_a_clash_the_database_finds_is_the_same_answer_as_one_the_check_finds(operator: Any, net: FakeNetwork,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Two requests that both passed the check before either wrote (a second process): the unique index refuses the
    second, and the person is told the same as before, not given an error."""
    sab = sabnzbd(net)
    world, found = searched_two(net)
    assert load(world, found, GOOD).status_code == 201
    monkeypatch.setattr(loading, "ACTIVE", ())
    second = load(world, found, SECOND)
    assert second.status_code == 409 and second.json()["detail"]["code"] == "already_loading"
    assert len(sab.jobs) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Download)) == 1


def test_two_different_releases_at_once_hand_over_one(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched_two(net)
    answers: list[int] = []
    gate = threading.Barrier(2)

    def click(title: str) -> None:
        gate.wait()
        answers.append(load(world, found, title).status_code)

    threads = [threading.Thread(target=click, args=(title,)) for title in (GOOD, SECOND)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [201, 409]
    assert len(sab.jobs) == 1


def test_a_release_that_failed_to_go_over_leaves_the_book_free_for_another(operator: Any, net: FakeNetwork) -> None:
    world, found = searched_two(net)
    # No program at all: nothing is written down, the next try is not in the way.
    assert load(world, found, GOOD).status_code == 409
    sab = sabnzbd(net)
    assert load(world, found, SECOND).status_code == 201
    assert len(sab.jobs) == 1


def test_the_database_itself_allows_one_active_download_of_a_book_and_a_kind(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)

    def row(state: str, kind: str = "ebook") -> Download:
        moment = clock.now()
        return Download(work_id=world.work_id, kind=kind, title=f"{state}-{kind}", protocol="usenet", state=state,
                        created_at=moment, updated_at=moment)

    with SessionLocal() as db:
        db.add(row("queued"))
        db.commit()
        db.add(row("unsure"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(row("queued", "audio"))
        db.commit()
        # A job that is over (a state that comes later in a download) does not stand in the way: the index looks at the ones
        # on their way only.
        db.add(row("done"))
        db.add(row("done"))
        db.commit()


# --- The blocklist outlives a work ------------------------------------------------------------------------------------


def block(world: Any, found: dict[str, Any], title: str) -> dict[str, Any]:
    release = by_title(found)[title]["id"]
    answer = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/block",
                               json={"reason": "by_hand"})
    assert answer.status_code == 201, answer.text
    return answer.json()  # type: ignore[no-any-return]


def forget_the_work(world: Any) -> None:
    """What the scanner does when a work is rebuilt: the work goes (and its editions and files), the next scan makes
    a new one."""
    with SessionLocal() as db:
        db.execute(delete(Edition).where(Edition.work_id == world.work_id))
        db.execute(delete(Work).where(Work.id == world.work_id))
        db.commit()
    scan(world.library_id)
    with SessionLocal() as db:
        new_id = db.scalar(select(Work.id))
    assert new_id is not None
    world.work_id = new_id


def test_a_blocked_release_is_still_blocked_when_the_work_is_built_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD), Item(SECOND)]
    found = search(world, language="de")
    entry = block(world, found, GOOD)
    forget_the_work(world)
    with SessionLocal() as db:
        row = db.scalars(select(BlocklistEntry)).one()
        assert row.id == entry["id"] and row.work_id is None and row.foreign_id is None and row.book_key
    again = by_title(search(world, language="de", new=True))
    assert codes(again[GOOD]) == ["blocklisted"] and again[SECOND]["rank"] == 1
    # The entry is the book's again: listed, and taken off the list the usual way.
    listed = world.client.get("/api/v1/procure/blocklist").json()["entries"]
    assert [item["id"] for item in listed] == [entry["id"]] and listed[0]["work_id"] == world.work_id
    assert world.client.delete(f"/api/v1/procure/blocklist/{entry['id']}").status_code == 204


def test_an_entry_that_waits_does_not_turn_down_the_release_of_another_book(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    block(world, found, GOOD)
    with SessionLocal() as db:
        db.execute(delete(Work).where(Work.id == world.work_id))
        db.commit()
    folder = books_folder("another")
    book(folder, "x.epub", title="Ein anderes Buch", authors=("Rune Falkner",))
    other = make_library(folder, "Other")
    scan(other)
    join(other, world.person.id, "acquire")
    with SessionLocal() as db:
        other_id = db.scalar(select(Work.id))
    world.work_id = other_id
    shown = by_title(search(world, language="de"))
    assert shown[GOOD]["rank"] is None and "blocklisted" not in codes(shown[GOOD])
    with SessionLocal() as db:
        assert db.scalars(select(BlocklistEntry)).one().work_id is None


def test_the_blocklist_holds_one_entry_per_release_and_book_whatever_races(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)

    def entry(title_key: str, **over: Any) -> BlocklistEntry:
        values: dict[str, Any] = {"kind": "ebook", "title": "T", "title_key": title_key, "protocol": "usenet",
                                  "reason": "by_hand", "created_at": clock.now(), "work_id": world.work_id}
        values.update(over)
        return BlocklistEntry(**values)

    waiting = {"work_id": None, "foreign_id": None, "book_key": "k"}
    with SessionLocal() as db:
        db.add(entry("a"))
        db.add(entry("a", **waiting))
        db.commit()
        for twin in (entry("a"), entry("a", **waiting)):
            db.add(twin)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        # Another protocol, another name or another book is another entry.
        db.add(entry("a", protocol="torrent"))
        db.add(entry("b"))
        db.add(entry("a", **{**waiting, "book_key": "other"}))
        db.commit()


def test_blocking_two_ways_at_once_keeps_one_entry(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    results: list[int] = []
    gate = threading.Barrier(3)

    def click() -> None:
        gate.wait()
        results.append(block(world, found, GOOD)["id"])

    threads = [threading.Thread(target=click) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 1


def test_a_block_that_another_request_wrote_a_moment_before_is_that_block(operator: Any, net: FakeNetwork) -> None:
    """The check found nothing, the write met the entry the other request had just written: the unique index says so, and
    the answer is the entry that is there."""
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    entry = block(world, found, GOOD)
    release = SimpleNamespace(title=GOOD, protocol="usenet", kind="ebook", info_hash="", indexer_name="Shelf")
    with SessionLocal() as db:
        real = db.scalar
        asked: list[int] = []

        def blind_once(*args: Any, **kwargs: Any) -> Any:
            asked.append(1)
            return None if len(asked) == 1 else real(*args, **kwargs)

        db.scalar = blind_once  # type: ignore[method-assign]
        again = blocklist.block(db, None, release, work_id=world.work_id, foreign_id=None,  # type: ignore[arg-type]
                                reason="by_hand", book="k")
        assert again.id == entry["id"] and len(asked) == 2
        db.commit()
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 1


def test_the_key_of_a_book_is_the_author_and_the_core_of_the_title() -> None:
    one = blocklist.book_key({"authors": ["Rune Falkner"], "title": "Die Laternenstraße: Ein Krimi"})
    two = blocklist.book_key({"authors": ["Falkner, Rune"], "title": "Die Laternenstrasse"})
    assert one and one == two
    assert blocklist.book_key({"authors": [], "title": "Die Laternenstraße"}) == ""
    assert blocklist.book_key({"authors": ["Rune Falkner"], "title": ""}) == ""
    assert blocklist.book_key(None) == ""


# --- A torrent blocked by its info hash is blocked under any name ----------------------------------------------------------


def test_a_torrent_blocked_by_its_info_hash_is_blocked_under_another_name(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    digest = hashlib.sha1(b"a book", usedforsecurity=False).hexdigest()
    tracker.items = [Item(GOOD, seeders=5, info_hash=digest)]
    found = search(world, language="de")
    release = next(item for item in found["releases"] if item["protocol"] == "torrent")
    answer = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release['id']}/block",
                               json={"reason": "damaged"})
    assert answer.status_code == 201
    renamed = "Rune Falkner - Die Laternenstrasse (reupload) GERMAN EPUB-OTHER"
    tracker.items = [Item(renamed, seeders=9, info_hash=digest)]
    later = by_title(search(world, language="de", new=True))
    assert codes(later[renamed])[0] == "blocklisted"
    # A torrent of the same name and another hash is the same release for the name (the name decides too).
    tracker.items = [Item(GOOD, seeders=5, info_hash=hashlib.sha1(b"other", usedforsecurity=False).hexdigest())]
    again = by_title(search(world, language="de", new=True))
    assert codes(again[GOOD])[0] == "blocklisted"


# --- Places that were left unguarded ---------------------------------------------------------------------------------


def two_libraries(net: FakeNetwork) -> tuple[Any, dict[str, Any], int]:
    """The book lives in one library and the person may ask for books in a second one too."""
    world = make_world(net)
    world.indexer.items = [Item(GOOD), Item(AUDIO, size=400_000_000, categories=(3130,))]
    found = search(world, language="de")
    folder = books_folder("second")
    book(folder, "y.epub", title="Ein zweites Buch", authors=("Ida Leser",))
    second = make_library(folder, "Second", kind="ebook")
    scan(second)
    join(second, world.person.id, "acquire")
    return world, found, second


def leave(world: Any, library_id: int) -> None:
    with SessionLocal() as db:
        db.execute(delete(LibraryMember).where(LibraryMember.library_id == library_id,
                                               LibraryMember.user_id == world.person.id))
        db.commit()


def test_a_person_who_no_longer_sees_the_book_loads_nothing_though_they_may_ask_elsewhere(operator: Any,
                                                                                         net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found, _second = two_libraries(net)
    leave(world, world.library_id)
    answer = load(world, found, GOOD)
    assert answer.status_code == 404, answer.text
    assert sab.jobs == {} and world.indexer.fetched == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Download)) == 0


def test_a_person_who_no_longer_sees_the_book_blocks_nothing_either(operator: Any, net: FakeNetwork) -> None:
    world, found, _second = two_libraries(net)
    leave(world, world.library_id)
    release = by_title(found)[GOOD]["id"]
    answer = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/block", json={})
    assert answer.status_code == 404, answer.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 0


def test_a_kind_without_a_place_for_the_person_is_not_loaded_and_not_blocked(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found, second = two_libraries(net)
    # The person keeps the first library (both kinds) but its right goes down to reading: only the second library, which
    # takes e-books only, is left to ask in. An audiobook of the search has no place.
    join(world.library_id, world.person.id, "read")
    refused = load(world, found, AUDIO)
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "acquire_not_allowed"
    assert sab.jobs == {} and world.indexer.fetched == []
    release = by_title(found)[AUDIO]["id"]
    blocked = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/block", json={})
    assert blocked.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 0
    assert second


# --- A number of thousands of digits in a torrent ------------------------------------------------------------------------


@pytest.mark.parametrize("data", [
    b"d4:infod6:lengthi" + b"9" * 5000 + b"e4:name1:x12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee",
    b"d4:infod6:lengthi1e4:name1:x12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee3:fooi" + b"1" * 5000 + b"ee",
    b"d4:infod6:lengthi1e4:name" + b"1" * 5000 + b":x6:pieces20:" + b"x" * 20 + b"ee",
])
def test_a_number_of_thousands_of_digits_is_an_invalid_torrent_not_an_error(data: bytes) -> None:
    with pytest.raises(torrent.TorrentInvalid):
        torrent.read_torrent(data)


def test_a_torrent_with_such_a_number_is_refused_by_the_load_with_a_code(operator: Any, net: FakeNetwork) -> None:
    from .procure_fakes import FakeQbittorrent

    world = make_world(net)
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    qbit_key = secrets.token_hex(8)
    net.add(FakeQbittorrent("qbit.example.com", "nexbooks", qbit_key))
    add_program("qbittorrent", "qbit.example.com", username="nexbooks", secret=qbit_key)
    tracker.items = [Item(GOOD, seeders=5)]
    guid = hashlib.sha256(GOOD.encode()).hexdigest()[:16]
    tracker.files[guid] = httpx.Response(200, content=b"d4:infod6:lengthi" + b"9" * 5000 + b"eee")
    found = search(world, language="de")
    release = next(item["id"] for item in found["releases"] if item["protocol"] == "torrent")
    answer = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/load", json={})
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "release_file_invalid"
