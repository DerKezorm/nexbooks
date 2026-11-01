"""Watching an author over the API: what switching it on wishes (the books announced, never what is out), the choice of
kind, language and kinds of work, a wish list instead of wishes for who may not ask for books, nothing wished twice,
switching it off, and that a watch is its person's own. The beat is tried in ``test_watching_beat``, the languages and
what is no new book in ``test_watching_languages``."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, AuthorWatch, CatalogPerson, Indexer, ProcureWish
from app.services.oeuvre import watch as watching

from .library_helpers import book, books_folder, join
from .watch_helpers import AUTHOR, KEEPER, ORIGINAL, World


def test_switching_on_wishes_the_book_that_is_announced_and_nothing_that_is_out(watchers: World) -> None:
    token = watchers.built()
    made = watchers.watch(watchers.alice, token, kind="both", language="de")
    assert made["state"] == "watching" and made["can_acquire"] is True
    assert made["made"] == {"wished": 2, "listed": 0, "stopped": ""}
    rows = watchers.wishes()
    # One book, announced for March 2027 in German: an e-book and an audio book, waiting for their day.
    assert watchers.titles_of(rows) == [ORIGINAL, ORIGINAL]
    assert {(row.kind, row.language, row.state, row.release_date) for row in rows} == {
        ("ebook", "de", "waiting", "2027-03-03"), ("audio", "de", "waiting", "2027-03-03")}
    assert all(row.user_id == watchers.alice.id for row in rows)
    # The book that stands on the shelf, the early ones and the box are not wished: they are out.
    assert [(item.via, item.language) for item in watchers.items()] == [("wish", "de"), ("wish", "de")]
    row = watchers.watch_row(watchers.alice)
    assert row.primed_at is not None and row.since == "2026-10-12" and row.seen
    # The page of the author says so.
    page = watchers.client(watchers.alice).get(f"/api/v1/catalog/authors/{token}").json()
    assert page["watch"]["state"] == "watching" and page["watch"]["wished"] == 2
    assert [(line["title"], line["state"], line["date"]) for line in page["watch"]["announced"]] == [
        (KEEPER, "wished", "2027-03-03")]
    assert page["watch"]["not_yet"] == []


def test_a_watch_is_the_persons_own_and_is_seen_by_nobody_else(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    bob, carol = watchers.client(watchers.bob), watchers.client(watchers.carol)
    assert bob.get(f"/api/v1/catalog/authors/{token}").json()["watch"] is None
    assert carol.get("/api/v1/watching").json()["items"] == []
    mine = watchers.client(watchers.alice).get("/api/v1/watching").json()
    assert [item["name"] for item in mine["items"]] == [AUTHOR] and mine["limit"] == watching.MAX_WATCHES
    # Carol switching the watch off does not touch alice's.
    assert carol.delete(f"/api/v1/watching/authors/{token}").status_code == 204
    assert len(watchers.wishes()) == 1
    assert watchers.client(watchers.alice).get("/api/v1/watching").json()["items"][0]["next"]["title"] == KEEPER
    # A token nobody has, and a language that is none.
    assert carol.put("/api/v1/watching/authors/nothing-here", json={}).status_code == 404
    assert carol.delete("/api/v1/watching/authors/nothing-here").status_code == 404
    assert carol.put(f"/api/v1/watching/authors/{token}", json={"language": "Deutsch!"}).status_code == 422
    assert carol.put(f"/api/v1/watching/authors/{token}", json={"language": "both"}).json()["detail"]["code"] == (
        "language_not_accepted")
    assert carol.put(f"/api/v1/watching/authors/{token}", json={"kind": "everything"}).status_code == 422
    # Nobody who is not signed in.
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="http://testserver") as nobody:
        assert nobody.get("/api/v1/watching").status_code == 401


def test_a_key_reads_the_list_and_changes_nothing(watchers: World, client: TestClient, operator: Account) -> None:
    token = watchers.built()
    watchers.watch(operator, token, kind="ebook", language="de")
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    secret = str(client.post("/api/v1/api-keys", json={"name": "deck"}).json()["secret"])
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(client.app, base_url="http://testserver", headers={"X-Nexbooks-Client": "program-tab"}) as program:
        listed = program.get("/api/v1/watching", headers=headers)
        assert listed.status_code == 200 and [item["name"] for item in listed.json()["items"]] == [AUTHOR]
        # (A key reaches no route that changes something: refused as no way in, like every other change.)
        assert program.put(f"/api/v1/watching/authors/{token}", headers=headers,
                           json={"kind": "audio", "language": "de"}).status_code in (401, 403)
        assert program.delete(f"/api/v1/watching/authors/{token}", headers=headers).status_code in (401, 403)
    assert watchers.watch_row(operator).kind == "ebook"


def test_the_kind_and_the_language_are_the_persons_choice(watchers: World) -> None:
    token = watchers.built()
    audio = watchers.watch(watchers.alice, token, kind="audio", language="de")
    assert audio["made"]["wished"] == 1
    assert [(row.kind, row.language) for row in watchers.wishes()] == [("audio", "de")]
    # Another language books are fetched in: the book is announced in English for 15 November, as a recording too. The
    # recording is wished already (in German): one wish per book and kind, whatever its language.
    english = watchers.watch(watchers.alice, token, kind="audio", language="en")
    assert english["made"]["wished"] == 0 and len(watchers.wishes()) == 1
    assert english["language"] == "en" and english["target"] == "en"
    ebook = watchers.watch(watchers.alice, token, kind="both", language="en")
    assert ebook["made"]["wished"] == 1
    assert {(row.kind, row.language, row.release_date) for row in watchers.wishes()} == {
        ("audio", "de", "2027-03-03"), ("ebook", "en", "2026-11-15")}


def test_a_work_the_sources_could_not_place_is_never_wished(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Der unklare Fall", language="de", released="2027-05-01", kind="unsure")
    with SessionLocal() as db:
        from app.models import CatalogWork

        work = db.scalar(select(CatalogWork).where(CatalogWork.key == "newbook"))
        assert work is not None
        work.why = "short"
        db.commit()
    # (And one a source calls a novel but some other person of the name may have written: the reason keeps it out.)
    watchers.new_work("Der fremde Name", language="de", released="2027-06-01", kind="roman", key="stranger")
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == "stranger"))
        assert work is not None
        work.why = "namesake"
        db.commit()
    made = watchers.watch(watchers.alice, token, kind="ebook", language="de", more=True)
    assert watchers.titles_of(watchers.wishes()) == [ORIGINAL]
    assert made["made"]["wished"] == 1


@pytest.mark.parametrize(("kind", "more", "wished"), [("sach", False, False), ("sach", True, True), ("kinder", True, True),
                                                    ("box", False, False), ("erz", False, True), ("roman", False, True)])
def test_novels_and_stories_by_default_and_the_rest_by_a_tick(watchers: World, kind: str, more: bool,
                                                              wished: bool) -> None:
    token = watchers.built()
    watchers.new_work("Das andere Buch", language="de", released="2027-06-01", kind=kind)
    watchers.watch(watchers.alice, token, kind="ebook", language="de", more=more)
    assert ("Das andere Buch" in watchers.titles_of(watchers.wishes())) is wished


def test_without_the_right_to_acquire_the_books_go_onto_the_wish_list(watchers: World) -> None:
    token = watchers.built()
    made = watchers.watch(watchers.bob, token, kind="both", language="de")
    # One entry for the book, whatever the kinds; no wish.
    assert made["made"] == {"wished": 0, "listed": 1, "stopped": ""} and made["can_acquire"] is False
    assert watchers.wishes() == [] and watchers.listed(watchers.bob) == [ORIGINAL]
    assert [(item.via, item.kind) for item in watchers.items()] == [("list", "")]
    bob = watchers.client(watchers.bob)
    listing = bob.get("/api/v1/wishlist").json()
    assert listing["count"] == 1 and list(listing["via"].values()) == [AUTHOR]
    assert list(listing["via"]) == [listing["items"][0]["token"]]
    # Again: still one.
    assert watchers.watch(watchers.bob, token, kind="both", language="de")["made"]["listed"] == 0
    assert watchers.listed(watchers.bob) == [ORIGINAL]
    # Carol, who has no library at all: the same, on her own list.
    assert watchers.watch(watchers.carol, token, kind="both", language="de")["made"]["listed"] == 1
    assert watchers.listed(watchers.carol) == [ORIGINAL] and watchers.listed(watchers.bob) == [ORIGINAL]
    # A person who may ask for books has no list entry from it.
    assert watchers.client(watchers.alice).get("/api/v1/wishlist").json()["count"] == 0


def test_a_kind_the_person_may_not_ask_for_goes_onto_the_list(watchers: World) -> None:
    """The library takes e-books only: the e-book is wished, the audio book is not (no place for it)."""
    from app.models import Library

    with SessionLocal() as db:
        library = db.get(Library, watchers.library)
        assert library is not None
        library.kind = "ebook"
        db.commit()
    token = watchers.built()
    made = watchers.watch(watchers.alice, token, kind="both", language="de")
    assert made["made"] == {"wished": 1, "listed": 1, "stopped": ""}
    assert [(row.kind) for row in watchers.wishes()] == ["ebook"]
    assert watchers.listed(watchers.alice) == [ORIGINAL]


def test_a_book_somebody_wished_by_hand_is_not_wished_twice(watchers: World) -> None:
    token = watchers.built()
    alice = watchers.client(watchers.alice)
    page = alice.get(f"/api/v1/catalog/authors/{token}").json()
    keeper = next(w for w in page["works"] if any(e["title"] == KEEPER for e in w["editions"]))
    by_hand = alice.post("/api/v1/procure/wishes", json={"items": [{"book": keeper["token"]}], "kinds": ["ebook"],
                                                       "language": "de"})
    assert by_hand.status_code == 201, by_hand.text
    made = watchers.watch(watchers.alice, token, kind="both", language="de")
    # Only the audio book is new; the e-book wish is the one made by hand and is not labelled with the watch.
    assert made["made"]["wished"] == 1 and len(watchers.wishes()) == 2
    shown = {row["kind"]: row["via"] for row in alice.get("/api/v1/procure/wishes").json()["wishes"]}
    assert shown == {"ebook": "", "audio": AUTHOR}


def test_the_wishes_of_a_watch_say_where_they_came_from_to_the_person_only(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="both", language="de")
    listing = watchers.client(watchers.alice).get("/api/v1/procure/wishes").json()["wishes"]
    assert [row["via"] for row in listing] == [AUTHOR, AUTHOR]
    # The other people who may ask for books in the library see the wishes, not whom alice watches.
    from app.models import LibraryMember

    other = watchers.carol
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=watchers.library, user_id=other.id, right="acquire"))
        db.commit()
    seen = watchers.client(other).get("/api/v1/procure/wishes").json()["wishes"]
    assert [(row["by"] != "", row["via"]) for row in seen] == [(True, ""), (True, "")]


def test_two_switches_at_the_same_moment_make_one_set_of_wishes(watchers: World) -> None:
    token = watchers.built()
    gate = threading.Barrier(3)
    answers: list[int] = []

    def press(client: Any) -> None:
        gate.wait()
        answers.append(client.put(f"/api/v1/watching/authors/{token}", json={"kind": "both", "language": "de"}).status_code)

    clients = [watchers.client(watchers.alice) for _ in range(2)]
    threads = [threading.Thread(target=press, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join()
    assert answers == [200, 200]
    assert len(watchers.wishes()) == 2 and len(watchers.items()) == 2
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AuthorWatch)))) == 1


def test_a_wish_taken_away_is_not_made_again(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    [wish] = watchers.wishes()
    alice = watchers.client(watchers.alice)
    assert alice.delete(f"/api/v1/procure/wishes/{wish.id}").status_code == 204
    again = watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert again["made"]["wished"] == 0 and watchers.wishes() == []
    # The same when the beat looks again after a new build.
    watchers.days(1.1)
    watchers.beat()
    assert watchers.wishes() == []


def test_switching_off_keeps_the_wishes_or_takes_them_away_after_a_question(watchers: World) -> None:
    token = watchers.built()
    alice = watchers.client(watchers.alice)
    watchers.watch(watchers.alice, token, kind="both", language="de")
    assert len(watchers.wishes()) == 2
    # The default keeps them, and the label stays.
    assert alice.delete(f"/api/v1/watching/authors/{token}").status_code == 204
    assert len(watchers.wishes()) == 2 and alice.get(f"/api/v1/catalog/authors/{token}").json()["watch"] is None
    assert [row["via"] for row in alice.get("/api/v1/procure/wishes").json()["wishes"]] == [AUTHOR, AUTHOR]
    # Switched on again, nothing is wished again (it is wished), and then switched off with the wishes taken away.
    assert watchers.watch(watchers.alice, token, kind="both", language="de")["made"]["wished"] == 0
    assert alice.delete(f"/api/v1/watching/authors/{token}", params={"wishes": "remove"}).status_code == 204
    assert watchers.wishes() == [] and watchers.items() == []
    # Twice is no error.
    assert alice.delete(f"/api/v1/watching/authors/{token}").status_code == 204
    assert alice.delete(f"/api/v1/watching/authors/{token}", params={"wishes": "bogus"}).status_code == 422


def test_switching_off_with_the_wishes_taken_away_keeps_a_wish_that_is_on_its_way(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    with SessionLocal() as db:
        wish = db.scalar(select(ProcureWish))
        assert wish is not None
        wish.state = "loading"
        db.commit()
    assert watchers.client(watchers.alice).delete(f"/api/v1/watching/authors/{token}",
                                                  params={"wishes": "remove"}).status_code == 204
    assert [row.state for row in watchers.wishes()] == ["loading"]


def test_switching_off_leaves_a_wish_of_a_library_the_person_may_no_longer_ask_for_books_in(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    join(watchers.library, watchers.alice.id, "read")
    answer = watchers.client(watchers.alice).delete(f"/api/v1/watching/authors/{token}", params={"wishes": "remove"})
    assert answer.status_code == 204
    assert len(watchers.wishes()) == 1 and watchers.items() == []
    with SessionLocal() as db:
        assert db.scalar(select(AuthorWatch.id)) is None


def test_switching_off_with_the_wishes_taken_away_takes_the_list_entries_too(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.bob, token, kind="both", language="de")
    assert watchers.listed(watchers.bob) == [ORIGINAL]
    assert watchers.client(watchers.bob).delete(f"/api/v1/watching/authors/{token}",
                                                params={"wishes": "remove"}).status_code == 204
    assert watchers.listed(watchers.bob) == []


def test_a_person_watches_as_many_authors_as_they_may(watchers: World, monkeypatch: pytest.MonkeyPatch) -> None:
    token = watchers.built()
    monkeypatch.setattr(watching, "MAX_WATCHES", 1)
    watchers.watch(watchers.alice, token, kind="both", language="de")
    with SessionLocal() as db:
        from app.services.oeuvre import store

        other = store.for_name(db, "Marit Lindqvist")
        db.commit()
        assert other is not None
        other_token = other.token
    alice = watchers.client(watchers.alice)
    refused = alice.put(f"/api/v1/watching/authors/{other_token}", json={})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "too_many_watches"
    # Changing the watch she has is no new one.
    assert alice.put(f"/api/v1/watching/authors/{token}", json={"kind": "audio", "language": "de"}).status_code == 200


def test_by_default_a_watch_wishes_in_the_first_language_books_are_fetched_in(watchers: World) -> None:
    token = watchers.built()
    made = watchers.watch(watchers.alice, token)
    assert made["language"] == "" and made["kind"] == "both" and made["more"] is False
    assert made["languages"] == ["de", "en"] and made["target"] == "de" and made["language_off"] is False
    with SessionLocal() as db:
        row = db.scalar(select(CatalogPerson).where(CatalogPerson.token == token))
        assert row is not None
        row.languages = ["en"]
        db.commit()
    assert watchers.watch(watchers.carol, token)["target"] == "de"


def test_the_catalogue_is_asked_for_when_it_is_not_built_and_the_first_look_follows(watchers: World) -> None:
    client = watchers.client(watchers.alice)
    opened = client.post("/api/v1/catalog/authors", json={"name": AUTHOR}).json()
    made = watchers.watch(watchers.alice, opened["token"], kind="both", language="de")
    assert made["state"] == "waiting" and made["made"] == {"wished": 0, "listed": 0, "stopped": ""}
    assert watchers.sources.waiting() == 1 and watchers.wishes() == []
    watchers.sources.build()
    assert watchers.beat()["looked"] == 1
    assert len(watchers.wishes()) == 2
    assert client.get("/api/v1/watching").json()["items"][0]["state"] == "watching"


def test_a_watch_goes_with_its_person_and_its_wishes_stay_labelled_in_the_database(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.bob, token, kind="both", language="de")
    from app.models import Account

    with SessionLocal() as db:
        bob = db.get(Account, watchers.bob.id)
        assert bob is not None
        db.delete(bob)
        db.commit()
    assert watchers.items() == []
    with SessionLocal() as db:
        assert db.scalar(select(AuthorWatch.id)) is None


def test_the_indexer_is_needed_to_wish_and_the_look_is_made_again_when_one_is_there(watchers: World) -> None:
    token = watchers.built()
    with SessionLocal() as db:
        db.get(Indexer, watchers.indexer).enabled = False  # type: ignore[union-attr]
        db.commit()
    made = watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert made["made"] == {"wished": 0, "listed": 0, "stopped": "no_indexers"}
    # Nothing is kept as done: the look is made again.
    assert watchers.wishes() == [] and watchers.items() == []
    row = watchers.watch_row(watchers.alice)
    assert row.retry_at is not None and row.primed_at is None
    with SessionLocal() as db:
        db.get(Indexer, watchers.indexer).enabled = True  # type: ignore[union-attr]
        db.commit()
    assert watchers.beat()["looked"] == 0
    watchers.days(1 / 24 + 0.01)
    assert watchers.beat()["looked"] == 1
    assert len(watchers.wishes()) == 1 and watchers.watch_row(watchers.alice).primed_at is not None


def test_a_book_the_person_has_is_not_wished(watchers: World) -> None:
    """The keeper stands on the shelf in German as an e-book: the e-book is not wished, the audio book is."""
    folder = books_folder("a2-first")
    book(folder, "hueter.epub", title=KEEPER, authors=(AUTHOR,), language="de")
    from .library_helpers import scan

    scan(watchers.library)
    token = watchers.built()
    made = watchers.watch(watchers.alice, token, kind="both", language="de")
    assert [(row.kind) for row in watchers.wishes()] == ["audio"] and made["made"]["wished"] == 1


def test_a_wish_is_made_by_the_person_with_their_own_right_in_the_library_that_takes_the_kind(watchers: World) -> None:
    token = watchers.built()
    join(watchers.library, watchers.alice.id, "acquire")
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    [wish] = watchers.wishes()
    assert wish.library_id == watchers.library and wish.user_id == watchers.alice.id and wish.foreign_id is not None
