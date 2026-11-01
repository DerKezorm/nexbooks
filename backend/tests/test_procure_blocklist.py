"""The blocklist: a release blocked for its book is turned down by every later search of that book and no
other; who may ask for books and sees the book sees the list and takes an entry off again."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import BlocklistEntry, ForeignBook

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, join, make_library, run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, Item, allow_languages, by_title, codes, make_world, search

GOOD = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"
SECOND = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.AZW3-EXAMPLE"


def block(world: Any, found: dict[str, Any], title: str, reason: str = "by_hand") -> Any:
    release = by_title(found)[title]["id"]
    return world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/block",
                             json={"reason": reason})


def test_a_blocked_release_is_turned_down_and_the_next_moves_up(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    world.indexer.items = [Item(GOOD), Item(SECOND)]
    found = search(world, language="de")
    assert by_title(found)[GOOD]["rank"] == 1
    answer = block(world, found, GOOD, "other_language")
    assert answer.status_code == 201, answer.text
    entry = answer.json()
    assert (entry["title"], entry["reason"], entry["by"], entry["kind"], entry["protocol"]) == (
        GOOD, "other_language", "rita", "ebook", "usenet")
    assert entry["work_id"] == world.work_id and entry["book_author"] == AUTHOR
    shown = by_title(world.client.get(f"/api/v1/procure/searches/{found['id']}").json())
    assert codes(shown[GOOD]) == ["blocklisted"] and shown[GOOD]["rank"] is None
    assert shown[SECOND]["rank"] == 1
    # The next search of the book turns it down as well.
    again = by_title(search(world, language="fr"))
    assert "blocklisted" in codes(again[GOOD])


def test_blocking_twice_keeps_one_entry_and_a_name_with_dots_or_spaces_is_the_same(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    first = block(world, found, GOOD).json()
    second = block(world, found, GOOD).json()
    assert first["id"] == second["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 1
    world.indexer.items = [Item(GOOD.replace(".", " ").lower())]
    later = search(world, language="en")
    assert codes(later["releases"][0])[0] == "blocklisted"


def test_an_entry_counts_for_its_book_only(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    block(world, found, GOOD)
    with SessionLocal() as db:
        db.add(ForeignBook(token="tok" + "y" * 13, ref="hardcover:9", title="Der Salzpfad", authors=[AUTHOR],
                           language="de", series="Nebelchor", series_pos=3.0))
        db.commit()
    answer = world.client.post("/api/v1/procure/searches", json={"book": "tok" + "y" * 13, "language": "de"})
    run_jobs()
    other = world.client.get(f"/api/v1/procure/searches/{answer.json()['id']}").json()
    assert "blocklisted" not in codes(by_title(other)[GOOD])


def test_the_list_and_taking_an_entry_off_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    # Several languages, each a search or a wish of its own: all of them allowed (settings, "Sprachen").
    allow_languages("de", "en", "fr", "es", "it", "nl", "sv", "pl")
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    entry = block(world, found, GOOD).json()
    listed = world.client.get("/api/v1/procure/blocklist").json()["entries"]
    assert [item["id"] for item in listed] == [entry["id"]]
    assert listed[0]["book_title"] and listed[0]["book_author"] == AUTHOR
    # Another person who may ask for books in the library sees it and may take it off.
    tom = make_account("tom")
    join(world.library_id, tom.id, "manage")
    other = new_client(tom)
    assert [item["id"] for item in other.get("/api/v1/procure/blocklist").json()["entries"]] == [entry["id"]]
    assert other.delete(f"/api/v1/procure/blocklist/{entry['id']}").status_code == 204
    assert world.client.get("/api/v1/procure/blocklist").json()["entries"] == []
    assert world.client.delete(f"/api/v1/procure/blocklist/{entry['id']}").status_code == 404
    later = by_title(search(world, language="fr"))
    assert "blocklisted" not in codes(later[GOOD])


def test_who_may_not_ask_for_books_or_does_not_see_the_book_neither_sees_nor_changes_the_list(
        operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    entry = block(world, found, GOOD).json()
    reader = make_account("lena")
    join(world.library_id, reader.id, "read")
    as_reader = new_client(reader)
    assert as_reader.get("/api/v1/procure/blocklist").status_code == 403
    assert as_reader.delete(f"/api/v1/procure/blocklist/{entry['id']}").status_code == 403
    # An acquirer of another library does not see the book: neither the entry.
    folder = books_folder("other")
    book(folder, "x.epub", title="Ein anderes Buch", authors=("Ida Leser",))
    other = make_library(folder, "Other")
    scan(other)
    oskar = make_account("oskar")
    join(other, oskar.id, "acquire")
    as_oskar = new_client(oskar)
    assert as_oskar.get("/api/v1/procure/blocklist").json()["entries"] == []
    assert as_oskar.delete(f"/api/v1/procure/blocklist/{entry['id']}").status_code == 404
    assert new_client(operator).get("/api/v1/procure/blocklist").status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 1


def test_a_reason_that_does_not_exist_is_refused(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    answer = block(world, found, GOOD, "because")
    assert answer.status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BlocklistEntry)) == 0


def test_a_person_blocks_only_out_of_their_own_search(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    world.indexer.items = [Item(GOOD)]
    found = search(world, language="de")
    tom = make_account("tom")
    join(world.library_id, tom.id, "manage")
    release = found["releases"][0]["id"]
    answer = new_client(tom).post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/block", json={})
    assert answer.status_code == 404
