"""Wishes: a wish is searched at once and the best release that fits is loaded; what a search that finds
nothing leaves; one open wish per book, kind and language whoever asks and however fast; who sees and changes a wish."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, Library, ProcureWish
from app.services.procure import automation, wishes

from .conftest import make_account, new_client
from .follow_helpers import (
    AUDIO,
    BAD_AUDIO,
    audio_world,
    downloads,
    sabnzbd,
    wish,
    wish_rows,
    wishes_of,
)
from .library_helpers import books_folder, join, make_library, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item


def switch_off() -> None:
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": False})


def test_a_wish_is_searched_at_once_and_the_best_release_is_loaded(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    made = wish(world)
    [result] = made["results"]
    assert result["status"] == "created" and result["kind"] == "audio" and result["wish"]["state"] == "searching"
    assert result["wish"]["book"]["title"] and result["wish"]["library"]["name"] == "Books"
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "loading" and shown["mine"] is True
    [download] = downloads()
    assert (download.state, download.kind, download.wish_id, download.title) == ("queued", "audio", shown["id"], AUDIO)
    assert len(sab.jobs) == 1


def test_a_search_that_finds_nothing_leaves_a_wish_with_the_time_of_the_next_try(operator: Any,
                                                                                  net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item("Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                  categories=(3130,)))
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found"
    assert shown["detail"]["total"] == 1 and shown["detail"]["reasons"]
    assert shown["next_try_at"] is not None and downloads() == []


def test_releases_that_do_not_pass_say_why(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net, Item(BAD_AUDIO, size=400_000_000, categories=(3130,)))
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found"
    assert [item["code"] for item in shown["detail"]["reasons"]] == ["dramatized"]


def test_with_the_main_switch_off_a_wish_waits_until_a_person_searches(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    switch_off()
    [result] = wish(world)["results"]
    assert result["wish"]["state"] == "idle" and result["wish"]["detail"]["reason"] == "main_off"
    run_jobs()
    assert wishes_of(world)[0]["state"] == "idle" and downloads() == []
    answer = world.client.post(f"/api/v1/procure/wishes/{result['wish']['id']}/search")
    assert answer.status_code == 200 and answer.json()["state"] == "searching"
    run_jobs()
    [shown] = wishes_of(world)
    # Found, but nothing is loaded by itself.
    assert shown["state"] == "idle" and shown["detail"]["passing"] == 1
    assert downloads() == [] and sab.jobs == {}


def test_an_indexer_is_needed_to_wish(operator: Any, net: FakeNetwork) -> None:
    world = audio_world(net)
    from app.models import Indexer

    with SessionLocal() as db:
        db.execute(update(Indexer).values(enabled=False))
        db.commit()
    answer = wish(world, expect=409)
    assert answer["detail"]["code"] == "no_indexers" and wish_rows() == []


def test_a_book_has_one_open_wish_per_kind_and_language_whoever_asks(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    first = wish(world)["results"][0]
    other = make_account("tom")
    from app.models import LibraryMember

    with SessionLocal() as db:
        library_id = db.scalar(select(Library.id))
        db.add(LibraryMember(library_id=library_id, user_id=other.id, right="acquire"))
        db.commit()
    with new_client(other) as tom:
        again = tom.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}], "kinds": ["audio"],
                                                         "language": "de"})
        # Nothing was made: 200, with the wish that is there.
        assert again.status_code == 200
        [result] = again.json()["results"]
        assert result["status"] == "exists" and result["wish"]["id"] == first["wish"]["id"]
        assert result["wish"]["mine"] is False
    assert len(wish_rows()) == 1


def test_two_people_asking_at_the_same_moment_make_one_wish(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    other = make_account("tom")
    from app.models import LibraryMember

    with SessionLocal() as db:
        db.add(LibraryMember(library_id=db.scalar(select(Library.id)), user_id=other.id, right="acquire"))
        db.commit()
    answers: list[list[str]] = []
    gate = threading.Barrier(3)

    def ask(client: Any) -> None:
        gate.wait()
        reply = client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                            "kinds": ["audio"], "language": "de"})
        answers.append([item["status"] for item in reply.json()["results"]] if reply.status_code in (200, 201)
                       else [str(reply.status_code)])

    with new_client(other) as tom, new_client(world.person) as rita:
        threads = [threading.Thread(target=ask, args=(client,)) for client in (tom, rita, world.client)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert sorted(item[0] for item in answers) == ["created", "exists", "exists"], answers
    assert len(wish_rows()) == 1


def test_both_kinds_make_two_wishes_and_what_the_book_has_makes_none(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    made = wish(world, kinds=("ebook", "audio"), language="de")["results"]
    assert [(item["kind"], item["status"]) for item in made] == [("ebook", "created"), ("audio", "created")]
    # The library holds the e-book in English, at the goal: nothing is wished there.
    again = wish(world, kinds=("ebook",), language="en", expect=200)["results"]
    assert again[0]["status"] == "have" and again[0]["wish"] is None


def test_a_wish_names_the_library_it_goes_into_and_only_one_the_person_may_use(operator: Any,
                                                                               net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    folder = books_folder("second")
    second = make_library(folder, "Second")
    join(second, world.person.id, "read")
    refused = wish(world, library_id=second, expect=403)
    assert refused["detail"]["code"] == "library_not_allowed"
    join(second, world.person.id, "acquire")
    [result] = wish(world, library_id=second)["results"]
    assert result["wish"]["library"] == {"id": second, "name": "Second"}
    run_jobs()
    assert downloads()[0].library_id == second


def test_a_wish_for_a_book_that_is_not_out_waits_for_its_day(operator: Any, net: FakeNetwork,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    soon = (clock.now() + timedelta(days=30)).date().isoformat()
    token = "tok" + "w" * 13
    with SessionLocal() as db:
        db.add(ForeignBook(token=token, ref="hardcover:77", title="Die Laternenstraße", authors=["Rune Falkner"],
                           language="de", series="Nebelchor", series_pos=2.0, released=soon))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                              "language": "de"})
    [result] = reply.json()["results"]
    assert result["wish"]["state"] == "waiting" and result["wish"]["release_date"] == soon
    run_jobs()
    assert wishes_of(world)[0]["state"] == "waiting" and sab.jobs == {}
    # "Jetzt suchen" does not search for a book that is not out.
    refused = world.client.post(f"/api/v1/procure/wishes/{result['wish']['id']}/search")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "wish_waiting"
    # On its day the wish searches by itself.
    later = clock.now() + timedelta(days=31)
    with monkeypatch.context() as moved:
        moved.setattr(clock, "now", lambda: later)
        with SessionLocal() as db:
            assert wishes.run_due(db) == 1
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


def test_a_wish_that_found_nothing_is_searched_again_when_its_time_comes(operator: Any, net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net, Item("Somebody Else-Another Book-MP3CD-DE-2021-EXAMPLE", size=400_000_000,
                                  categories=(3130,)))
    wish(world)
    run_jobs()
    assert wishes_of(world)[0]["state"] == "not_found"
    with SessionLocal() as db:
        assert wishes.run_due(db) == 0
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,))]
    later = clock.now() + timedelta(hours=7)
    with monkeypatch.context() as moved:
        moved.setattr(clock, "now", lambda: later)
        with SessionLocal() as db:
            assert wishes.run_due(db) == 1
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading" and len(sab.jobs) == 1


def test_many_books_at_once_wait_for_a_free_place_to_search(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    tokens = []
    with SessionLocal() as db:
        for number in range(5):
            token = f"tok{number:013d}"
            tokens.append(token)
            db.add(ForeignBook(token=token, ref=f"hardcover:{number}", title=f"Book {number}",
                               authors=["Rune Falkner"], language="de"))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token} for token in tokens],
                                                              "kinds": ["audio"], "language": "de"})
    assert reply.status_code == 201 and len(reply.json()["results"]) == 5
    waiting = [item for item in wishes_of(world) if item["waiting_for_a_place"]]
    assert len(waiting) == 2
    run_jobs()
    assert all(not item["waiting_for_a_place"] for item in wishes_of(world))
    assert {item["state"] for item in wishes_of(world)} <= {"not_found", "loading"}


@pytest.mark.parametrize(("label", "reason"), [("drama", "not_a_book"), ("box", "not_a_book"),
                                              ("part", "not_a_book"), ("work", "volume_unknown")])
def test_a_hit_of_the_catalogue_search_that_is_no_book_of_its_own_is_never_wished(operator: Any, net: FakeNetwork,
                                                                                  label: str, reason: str) -> None:
    """A play made of a book, a box, a part, or a book named like its series whose volume nobody knows: a wish would
    fetch the book, another volume, or nothing (the search shows them without the buttons)."""
    from app.services.discover import foreign
    from app.services.sources.model import Candidate

    world = audio_world(net)
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(source="audible", key="B0SEARCH01", title="Das Salztor",
                                           authors=["Ada Sorrel"], languages=["de"]), "audio")
        row.data = {**(row.data or {}), "search_label": label, "search_why_not": reason}
        db.commit()
        token = row.token
    answer = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                               "language": "de"})
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == reason, answer.text
    assert wish_rows() == []
    # The same page of a book that a search showed as a book: wished.
    with SessionLocal() as db:
        found = db.scalar(select(ForeignBook).where(ForeignBook.token == token))
        assert found is not None
        found.data = {key: value for key, value in found.data.items() if not key.startswith("search_")}
        db.commit()
    made = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": token}], "kinds": ["audio"],
                                                             "language": "de"})
    assert made.status_code == 201, made.text


def test_a_book_that_does_not_exist_or_a_work_the_person_does_not_see_is_not_found(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    world = audio_world(net)
    unknown = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": "nothing" * 2}],
                                                                "kinds": ["audio"]})
    # Every book failed: the failure of the first, not a 201 that made nothing.
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "not_found"
    both = world.client.post("/api/v1/procure/wishes", json={"items": [{}], "kinds": ["audio"]})
    assert both.status_code == 422 and both.json()["detail"]["code"] == "invalid_input"


def test_taking_a_wish_away_asks_nothing_of_the_download_it_may_have(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert world.client.delete(f"/api/v1/procure/wishes/{shown['id']}").status_code == 204
    assert wishes_of(world) == [] and wish_rows() == []
    [download] = downloads()
    assert download.wish_id is None and download.state == "queued" and len(sab.jobs) == 1


def test_who_sees_a_wish_is_who_may_ask_for_books_in_its_library(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    wish(world)
    reader = make_account("reader")
    outsider = make_account("outsider")
    colleague = make_account("colleague")
    other_library = make_library(books_folder("elsewhere"), "Elsewhere")
    join(world.library_id, reader.id, "read")
    join(world.library_id, colleague.id, "acquire")
    join(other_library, outsider.id, "acquire")
    with new_client(reader) as rita, new_client(outsider) as out, new_client(colleague) as col:
        assert rita.get("/api/v1/procure/wishes").status_code == 403
        assert rita.get("/api/v1/procure/counts").json()["allowed"] is False
        assert rita.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                         "kinds": ["audio"]}).status_code == 403
        assert out.get("/api/v1/procure/wishes").json()["wishes"] == []
        shown = col.get("/api/v1/procure/wishes").json()["wishes"]
        assert len(shown) == 1 and shown[0]["mine"] is False and shown[0]["by"] == "rita"
        wish_id = shown[0]["id"]
        # A stranger can neither search for it nor take it away.
        assert out.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 404
        assert out.delete(f"/api/v1/procure/wishes/{wish_id}").status_code == 404
        assert col.get("/api/v1/procure/counts").json()["seeking"] + col.get(
            "/api/v1/procure/counts").json()["running"] == 1
        assert col.delete(f"/api/v1/procure/wishes/{wish_id}").status_code == 204
    assert wish_rows() == []


def test_the_right_is_per_library_a_reader_of_the_book_who_may_ask_elsewhere_sees_nothing(operator: Any,
                                                                                         net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    wish(world)
    mixed = make_account("mixed")
    other_library = make_library(books_folder("elsewhere"), "Elsewhere")
    join(world.library_id, mixed.id, "read")
    join(other_library, mixed.id, "acquire")
    with new_client(mixed) as mia:
        assert mia.get("/api/v1/procure/counts").json()["allowed"] is True
        assert mia.get("/api/v1/procure/wishes").json()["wishes"] == []
        assert mia.get("/api/v1/procure/counts").json()["seeking"] == 0
    with SessionLocal() as db:
        wish_id = db.scalar(select(ProcureWish.id))
    with new_client(mixed) as mia:
        # She sees the book (she may read in its library), and still may not touch a wish of it there.
        assert mia.get(f"/api/v1/works/{world.work_id}").status_code == 200
        assert mia.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 404
        assert mia.post(f"/api/v1/procure/wishes/{wish_id}/language", json={"language": None}).status_code == 404
        assert mia.delete(f"/api/v1/procure/wishes/{wish_id}").status_code == 404
    assert len(wish_rows()) == 1


def test_the_operator_has_no_right_of_his_own_here(operator: Any, client: Any, net: FakeNetwork) -> None:
    audio_world(net)
    assert client.get("/api/v1/procure/wishes").status_code == 403
    assert client.get("/api/v1/procure/counts").json() == {
        "allowed": False, "seeking": 0, "running": 0, "needs": 0, "blocklist": 0, "automatic": False, "indexers": 0,
        "clients_down": []}


def test_the_options_of_a_book_say_what_may_be_wished_and_what_is_wished(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    first = world.client.get("/api/v1/procure/book-options", params={"work_id": world.work_id}).json()
    kinds = {item["kind"]: item for item in first["kinds"]}
    assert kinds["ebook"]["state"] == "have" and kinds["ebook"]["have"] == "epub"
    assert kinds["audio"]["state"] == "free" and kinds["audio"]["libraries"][0]["name"] == "Books"
    assert first["automatic"] is True and first["indexers"] == 1 and first["allowed"] is True
    wish(world)
    again = world.client.get("/api/v1/procure/book-options", params={"work_id": world.work_id}).json()
    assert {item["kind"]: item["state"] for item in again["kinds"]}["audio"] == "wished"
    run_jobs()
    third = world.client.get("/api/v1/procure/book-options", params={"work_id": world.work_id}).json()
    assert {item["kind"]: item["state"] for item in third["kinds"]}["audio"] == "loading"
    assert world.client.get("/api/v1/procure/book-options").status_code == 422


def test_a_release_chosen_by_hand_makes_a_wish_that_is_on_its_way(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world = audio_world(net)
    switch_off()
    answer = world.client.post("/api/v1/procure/searches", json={"work_id": world.work_id, "language": "de"})
    run_jobs()
    found = world.client.get(f"/api/v1/procure/searches/{answer.json()['id']}").json()
    release = next(item["id"] for item in found["releases"] if item["title"] == AUDIO)
    assert world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/load",
                             json={}).status_code == 201
    [shown] = wishes_of(world)
    assert shown["state"] == "loading" and shown["kind"] == "audio"
    assert downloads()[0].wish_id == shown["id"]


def test_a_book_that_is_on_its_way_already_is_adopted_by_the_wish_not_a_failure(operator: Any, net: FakeNetwork,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """The load of the best release finds the book loading already (somebody else was quicker): the wish takes that
    download over and stays ``loading``."""
    from app.errors import error
    from app.services.procure import loading

    sabnzbd(net)
    world = audio_world(net)
    real = loading.load

    def quicker(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise error("already_loading", "A release of this book is on its way.", 409)

    monkeypatch.setattr(loading, "load", quicker)
    wish(world)
    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "loading" and shown["detail"] is None
    assert [item.wish_id for item in downloads()] == [shown["id"]]


def test_a_search_that_is_done_settles_its_wish_once_however_many_ask(operator: Any, net: FakeNetwork) -> None:
    """The job that ran the search and a request for the list both settle a wish whose search is done: one load, and the
    wish stays ``loading`` (a second round used to find the release ``already_loading`` and turn the wish back)."""
    sab = sabnzbd(net)
    world = audio_world(net)
    wish(world)
    from app.models import ReleaseSearch

    # Run the search job only; the wish is not settled by it when nobody asks: do it by hand, from two threads.
    from app.services import jobs

    original = wishes.after_search
    wishes.after_search = lambda db, found: None  # type: ignore[assignment]
    try:
        jobs.run_pending(1000)
    finally:
        wishes.after_search = original  # type: ignore[assignment]
    with SessionLocal() as db:
        found = db.scalars(select(ReleaseSearch)).one()
        assert found.state == "done"
    gate = threading.Barrier(2)

    def settle() -> None:
        with SessionLocal() as db:
            current = db.scalars(select(ReleaseSearch)).one()
            gate.wait()
            wishes.after_search(db, current)

    threads = [threading.Thread(target=settle) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    [shown] = wishes_of(world)
    assert shown["state"] == "loading" and shown["detail"] is None
    assert len(downloads()) == 1 and len(sab.jobs) == 1
