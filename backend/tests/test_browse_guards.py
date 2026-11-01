"""The rules of the themed shelves that need a test of their own: the brake on the
three ways in, the hosts of the sources, the wish list of the caller, the short books that are never for children, the
long ones, a page that asks again for ever, the answers of Apple with odd fields, the lists of Discover without Audible,
the shelves kept in memory, the favourite read in a few days."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, ForeignBook, HistoryEntry, ShelfEntry
from app.services import brakes
from app.services.browse import cache, shelves, view
from app.services.sources import apple, config

from .browse_helpers import Browse, personal, shelf, wall
from .conftest import new_client
from .fake_sources import fixture
from .reading_helpers import Clock, World, at

# ---- The ways in ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/discover/browse", "/api/v1/discover/browse/personal",
                                  "/api/v1/discover/shelves/krimi"])
def test_every_way_into_the_shelves_is_braked(browse: Browse, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setitem(brakes.LIMITS, "discover", 2)
    brakes.forget()
    with new_client(browse.carol) as carol:
        codes = [carol.get(path, params={"market": "de"}).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[-1] == 429, codes


def test_the_hosts_of_every_source_are_the_ones_of_the_table_and_no_other() -> None:
    """A host more for a source would let its requests go there; the table is checked as a whole."""
    audible = {f"api.audible.{end}" for end in config.AUDIBLE_MARKETS.values()} | {"m.media-amazon.com"}
    assert {source: set(hosts) for source, hosts in config.HOSTS.items()} == {
        "hardcover": {"api.hardcover.app", "assets.hardcover.app"},
        "openlibrary": {"openlibrary.org", "covers.openlibrary.org"},
        "audible": audible,
        "dnb": {"services.dnb.de"},
        "apple": {"rss.marketingtools.apple.com", "itunes.apple.com", "is1-ssl.mzstatic.com", "is2-ssl.mzstatic.com",
                  "is3-ssl.mzstatic.com", "is4-ssl.mzstatic.com", "is5-ssl.mzstatic.com"},
        "wikidata": {"www.wikidata.org", "commons.wikimedia.org", "upload.wikimedia.org"},
        "bnf": {"catalogue.bnf.fr"}, "libris": {"libris.kb.se"}, "kb": {"jsru.kb.nl"}, "nb": {"api.nb.no"},
    }


def test_the_wish_mark_of_a_card_is_the_caller_s_own(browse: Browse) -> None:
    with new_client(browse.carol) as carol, new_client(browse.bob) as bob:
        card = shelf(carol, "krimi", limit=60)["items"][0]
        assert carol.put(f"/api/v1/wishlist/{card['token']}").status_code == 200
        mine = {i["token"]: i["wished"] for i in shelf(carol, "krimi", limit=60)["items"]}
        theirs = {i["token"]: i["wished"] for i in shelf(bob, "krimi", limit=60)["items"]}
        on_wall = {i["token"]: i["wished"] for s in wall(bob)["strips"] for i in s["items"]}
    assert mine[card["token"]] is True and theirs[card["token"]] is False
    assert on_wall.get(card["token"]) in (False, None)


def test_a_section_holds_three_books_of_one_author_and_two_of_one_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apple's chart named one author ten times in a row, and Open Library the volumes of a series one by one."""
    world = Browse(monkeypatch, fetch_shelves=False)
    chart = copy.deepcopy(fixture("apple_genre.json"))
    first = chart["feed"]["entry"][0]
    chart["feed"]["entry"] = [{**copy.deepcopy(first), "id": {"label": "", "attributes": {"im:id": str(1900000000 + n)}},
                               "im:name": {"label": f"Nebelwacht {n}"}} for n in range(6)]
    world.fake.apple_genres = {"9032": chart}
    docs = copy.deepcopy(world.fake.ol_shelf)
    for n, doc in enumerate(docs[:4]):
        doc["subject"] = [*doc["subject"], "series:Die_Deichchronik"]
        doc["author_name"] = [f"Autorin {n}"]
    world.fake.ol_shelf = docs
    world.shelves("de")
    with SessionLocal() as db:
        data = view.read(db, "de")
    popular = view.in_section(data["krimi"], "popular")
    by_brandt = [b for b in popular if b.row.authors == ["Ilse Brandt"]]
    assert len(by_brandt) == shelves.PER_AUTHOR == 3
    with SessionLocal() as db:
        series = {e.foreign_id for e in db.scalars(select(ShelfEntry)) if e.series_key == "s:die deichchronik"}
    assert len(series) >= 3, "the answers hold three volumes and more"
    assert sum(1 for b in popular if any(r.id in series for r in b.rows)) == shelves.PER_SERIES == 2


def test_an_old_text_in_a_new_record_is_not_new(browse: Browse) -> None:
    """Open Library makes new records of old texts ("Early works to 1800"): the year of the record is not the book's."""
    doc = copy.deepcopy(next(d for d in browse.fake.ol_shelf if d["title"] == "Fog Runner"))
    doc["key"], doc["title"] = "/works/OL1999W", "Old Fog"
    doc["subject"] = [*doc["subject"], "Mystery", "Early works to 1800"]
    doc["editions"]["docs"][0]["title"] = "Alter Nebel"
    browse.fake.ol_shelf = [doc, *browse.fake.ol_shelf]
    browse.advance(25 * 3600)
    browse.shelves("de")
    with SessionLocal() as db:
        new = {b.row.title for b in view.in_section(view.read(db, "de")["krimi"], "new")}
    assert "Nebelläufer" in new and "Alter Nebel" not in new


# ---- The rows made for the person ---------------------------------------------------------------------------------------


def _shelf_book(browse: Browse, title: str, shelf_key: str, *, evidence: float = 3.0) -> ForeignBook:
    """Moves a book of the fixtures to another shelf (it stands there alone)."""
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == title))
        assert row is not None
        db.query(ShelfEntry).filter(ShelfEntry.foreign_id == row.id).delete()
        db.add(ShelfEntry(source="openlibrary", market="de", shelf=shelf_key, section="popular", rank=99,
                          foreign_id=row.id, award="", topics=[], evidence=evidence, series_key=""))
        db.commit()
        db.expunge(row)
    cache.forget()
    return row


def test_the_short_row_holds_no_book_for_children_or_young_people(browse: Browse) -> None:
    def short() -> list[str]:
        body = personal(browse.client(browse.carol))
        return next(([i["title"] for i in row["items"]] for row in body["rows"] if row["key"] == "short"), [])

    assert "Hafenlichter" in short()
    _shelf_book(browse, "Hafenlichter", "kinder")
    assert "Hafenlichter" not in short(), "a short book for children is no evening's reading"
    _shelf_book(browse, "Kurze Nacht", "jugend")
    assert "Kurze Nacht" not in short()


def _pages(title: str, pages: int, new_title: str | None = None) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == title))
        assert row is not None
        data = dict(row.data)
        data["pages"] = pages
        row.data = data
        if new_title:
            row.title = new_title
        db.commit()
    cache.forget()


def test_the_long_row_holds_books_of_more_than_600_pages_and_no_box(browse: Browse) -> None:
    def long() -> list[dict[str, Any]]:
        body = personal(browse.client(browse.carol))
        return next((row["items"] for row in body["rows"] if row["key"] == "long"), [])

    assert long() == [], "no book of the fixtures is that long"
    _pages("Der stille Pier", 640)
    _pages("Hafenlichter", 600)
    _pages("Die neunte Glocke", 1200, new_title="Die neunte Glocke: Band 1-3")
    assert [(i["title"], i["pages"]) for i in long()] == [("Der stille Pier", 640)], "600 is not more than 600"
    assert shelves.is_bundle("Die Glocken-Trilogie in einem Band") and shelves.is_bundle("Box Set Books 1 & 2")
    assert not shelves.is_bundle("Die lange Nacht am Deich") and not shelves.is_bundle("Kapitel-22")


# ---- The page that asks again ---------------------------------------------------------------------------------------


def test_a_source_that_is_off_does_not_keep_the_wall_asking_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open Library off from the start: its parts of the shelves are never fetched, and no job waits for them. The page
    asked again 45 times, every four seconds, and said "not all shelves are filled" for ever."""
    world = Browse(monkeypatch, fetch_shelves=False)
    world.only("apple")
    world.shelves("de")
    with new_client(world.carol) as carol:
        body = wall(carol)
    assert body["state"] == "ready" and body["pending"] is False


def test_the_wall_asks_again_while_a_job_waits_for_a_part_never_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    world = Browse(monkeypatch, fetch_shelves=False)
    with new_client(world.carol) as carol:
        first = wall(carol)
    assert first["state"] == "fetching" and first["pending"] is True
    world.shelves("de")
    with new_client(world.carol) as carol:
        assert wall(carol)["pending"] is False


# ---- Apple's answers ----------------------------------------------------------------------------------------------------


def test_the_words_of_apple_come_with_their_entities_resolved() -> None:
    chart = copy.deepcopy(fixture("apple_genre.json"))
    entry = chart["feed"]["entry"][0]
    entry["im:name"]["label"] = "Bonny &amp; Clyde"
    entry["summary"]["label"] = "Sie sagt, he&#39;s gone &amp;amp; nie wieder &lt;b&gt;da&lt;/b&gt;."
    found = apple.parse_genre(chart)[0]
    assert found.title == "Bonny & Clyde"
    assert found.description == "Sie sagt, he's gone & nie wieder da."


@pytest.mark.parametrize("image", [3, "x", None, [3, None, {"label": 4}], {"label": "https://is1-ssl.mzstatic.com/a.jpg"}])
def test_a_picture_field_that_is_no_list_of_pictures_costs_the_picture_and_nothing_else(image: object) -> None:
    chart = copy.deepcopy(fixture("apple_genre.json"))
    chart["feed"]["entry"][0]["im:image"] = image
    found = apple.parse_genre(chart)
    assert found[0].title == "Nebelwacht" and found[0].cover_url == ""


def test_the_genre_apple_files_a_book_under_is_read() -> None:
    found = apple.parse_genre(fixture("apple_genre.json"))
    assert {c.category for c in found} == {"9032"}


# ---- Discover without Audible -------------------------------------------------------------------------------------------


def _list(client: Any, name: str) -> dict[str, Any]:
    answer = client.get(f"/api/v1/discover/lists/{name}", params={"market": "de", "hide_owned": "false"})
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def test_without_audible_the_new_the_coming_and_the_trending_lists_are_made_from_the_shelves(browse: Browse) -> None:
    with new_client(browse.carol) as carol:
        new, soon, trending = (_list(carol, name) for name in ("new", "soon", "trending"))
    assert new["state"] == "ready" and set(new["sources"]) <= {"apple", "openlibrary"} and new["items"]
    days = [i["released"] for i in new["items"] if i["released"]]
    assert days == sorted(days, reverse=True), "the newest first"
    assert [i["title"] for i in soon["items"]] == ["Kommende Dinge"], "only with a day after today that a source gave"
    assert all(i["upcoming"] for i in soon["items"])
    assert trending["items"] and [i["rank"] for i in trending["items"]] == list(range(1, len(trending["items"]) + 1))
    with SessionLocal() as db:
        data = view.read(db, "de")
    firsts = {view.in_section(data[s.key], "popular")[0].row.title for s in shelves.SHELVES
              if view.in_section(data[s.key], "popular")}
    assert trending["items"][0]["title"] in firsts, "the first of every shelf, then the second"


def test_a_coming_book_whose_day_has_passed_is_no_coming_book_any_more(browse: Browse) -> None:
    """"Kommende Dinge" comes out on 1 December; a week after, the shelves not fetched again, it is not to come."""
    with new_client(browse.carol) as carol:
        assert [i["title"] for i in _list(carol, "soon")["items"]] == ["Kommende Dinge"]
    browse.days(56)
    with new_client(browse.carol) as carol:
        assert _list(carol, "soon")["items"] == []


def test_with_audible_the_new_and_the_coming_lists_are_audible_s_as_before(shelves_world: Any) -> None:
    from app.services.discover import view as lists_view

    shelves_world.new_among_the_best()
    with SessionLocal() as db:
        assert not lists_view.uses_shelves(db, "new") and not lists_view.uses_shelves(db, "soon")
        assert lists_view.uses_shelves(db, "trending"), "Hardcover is not connected"
    with new_client(shelves_world.carol) as carol:
        new, soon = _list(carol, "new"), _list(carol, "soon")
    assert new["items"] and new["sources"] == ["audible"]
    assert soon["sources"] in ([], ["audible"]) and all("openlibrary" not in i["sources"] for i in soon["items"])


@pytest.fixture
def shelves_world(monkeypatch: pytest.MonkeyPatch) -> Browse:
    world = Browse(monkeypatch)
    world.only("apple", "openlibrary", "audible")
    world.settings(source_audible=True)
    world.fetch("de")
    return world


# ---- The shelves kept in memory ---------------------------------------------------------------------------------------


def test_the_shelves_of_a_market_are_read_once_until_they_change(browse: Browse) -> None:
    with SessionLocal() as db:
        first = view.read(db, "de")
        db.query(ShelfEntry).filter(ShelfEntry.shelf == "krimi").delete()
        db.commit()
        kept = view.read(db, "de")
    assert kept["krimi"].books, "read from memory: nothing asked the database again"
    browse.advance(25 * 3600)
    browse.shelves("de")
    with SessionLocal() as db:
        again = view.read(db, "de")
    assert again["krimi"].books and first["krimi"].order == again["krimi"].order, "fetched again: read anew"
    cache.forget("de")
    with SessionLocal() as db:
        db.query(ShelfEntry).filter(ShelfEntry.shelf == "krimi").delete()
        db.commit()
        assert view.read(db, "de")["krimi"].books == {}


def test_each_reading_of_the_shelves_kept_in_memory_is_a_copy_of_its_own(browse: Browse) -> None:
    with SessionLocal() as db:
        first = view.read(db, "de")
        view.lay_over(db, browse.alice.id, first)
        assert any(b.owned_work_id is not None for b in first["krimi"].books.values()), "alice has a book there"
        second = view.read(db, "de")
    assert all(b.owned_work_id is None for b in second["krimi"].books.values())


def test_the_shelves_kept_in_memory_change_when_they_are_fetched_again(browse: Browse) -> None:
    with SessionLocal() as db:
        assert "Hafenlichter" in {b.row.title for b in view.read(db, "de")["krimi"].books.values()}
    browse.fake.ol_shelf = [d for d in browse.fake.ol_shelf if d["title"] != "Harbour Lights"]
    browse.advance(25 * 3600)
    browse.shelves("de")
    with SessionLocal() as db:
        assert "Hafenlichter" not in {b.row.title for b in view.read(db, "de")["krimi"].books.values()}


def test_what_one_person_has_does_not_stick_to_the_shelves_kept_for_everybody(browse: Browse) -> None:
    with new_client(browse.alice) as alice, new_client(browse.carol) as carol:
        mine = [i for i in shelf(alice, "krimi", limit=60)["items"] if i["owned_work_id"] is not None]
        theirs = shelf(carol, "krimi", limit=60)["items"]
    assert mine, "alice has a book of the shelf"
    assert all(i["owned_work_id"] is None and i["wished"] is False for i in theirs)


# ---- A favourite ------------------------------------------------------------------------------------------------------------


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


@pytest.fixture
def tick(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


def _days(edition: int) -> list[int | None]:
    with SessionLocal() as db:
        return [e.days for e in db.scalars(select(HistoryEntry).where(HistoryEntry.edition_id == edition))]


def test_a_book_first_reported_near_its_end_is_no_favourite(world: World, tick: Clock) -> None:
    """Read elsewhere and reported here first at 92 %, finished the next day: nobody knows how long it took."""
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=at(0.92)).status_code == 200
        tick.advance(days=1)
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=at(0.99)).status_code == 200
    assert _days(edition) == [None]


def test_a_pass_whose_start_was_not_seen_begins_when_the_person_is_back_near_the_start(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=at(0.3)).status_code == 200
        tick.advance(days=1)
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=at(0.1)).status_code == 200
        tick.advance(days=2)
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=at(0.99)).status_code == 200
    assert _days(edition) == [2]
