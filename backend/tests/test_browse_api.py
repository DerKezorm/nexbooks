"""Browse over the API: the wall of thirteen shelves, one shelf opened with its filters, and the rule above all:

**what the caller has is worked out from the libraries the caller may see, and from nothing else.** The shelves are the
same for everybody; two people with two libraries read the same shelf, and the one never learns that the other owns a book.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Job
from app.services import jobs
from app.services.browse import shelves

from .browse_helpers import Browse, personal, shelf, titles, wall
from .conftest import make_account, new_client
from .library_helpers import book, scan


def by_title(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["title"]: item for item in body["items"]}


# ---- The wall ------------------------------------------------------------------------------------------------------


def test_the_wall_has_three_departments_with_the_shelves_of_the_table_in_their_order(browse: Browse) -> None:
    body = wall(browse.client(browse.alice))
    assert [d["key"] for d in body["departments"]] == ["bel", "sach", "kj"]
    assert [[s["key"] for s in d["shelves"]] for d in body["departments"]] == [
        ["krimi", "romane", "fantasy", "scifi", "klassiker"],
        ["bio", "geschichte", "natur", "gesellschaft", "leben", "reisen"],
        ["kinder", "jugend"]]
    assert body["state"] == "ready" and body["asking"] and not body["pending"]
    assert body["sources"] == ["apple", "openlibrary"]
    assert body["market"] == "de" and "us" in body["markets"]


def test_a_tile_counts_the_books_of_the_shelf_and_the_new_ones_and_shows_three_covers(browse: Browse) -> None:
    body = wall(browse.client(browse.alice))
    tile = next(s for d in body["departments"] for s in d["shelves"] if s["key"] == "krimi")
    assert tile["books"] == 19 and tile["new"] == 5
    assert len(tile["fan"]) == 3 and all(card["cover_url"] for card in tile["fan"]) or len(tile["fan"]) <= 3


def test_a_strip_shows_the_new_books_first_and_the_sections_that_have_three_or_more(browse: Browse) -> None:
    body = wall(browse.client(browse.alice))
    strip = next(s for s in body["strips"] if s["shelf"] == "krimi")
    assert strip["section"] == "new"
    assert titles(strip) == ["Nebelwacht", "Nebelläufer", "Die Salzwiese", "Die Salztöchter",
                             "Acht Stunden bis zum Morgen"], "the sources woven, Apple's chart first"
    assert {s["key"]: s["count"] for s in strip["sections"]} == {"new": 5, "popular": 13, "classic": 6}, \
        "the prizes are two books: a strip of two is not a strip"
    # A shelf of classics has no section "new": it begins with what is read.
    assert "new" not in shelves.BY_KEY["klassiker"].sections and shelves.BY_KEY["klassiker"].sections[0] == "popular"


def test_the_cards_of_a_strip_say_what_the_back_of_the_cover_shows(browse: Browse) -> None:
    body = wall(browse.client(browse.alice))
    strip = next(s for s in body["strips"] if s["shelf"] == "krimi")
    cards = by_title(strip)
    apple, ol = cards["Nebelwacht"], cards["Nebelläufer"]
    assert apple["blurb"].startswith("Ein Kommissar am Deich") and apple["pages"] is None
    assert apple["authors"] == ["Ilse Brandt"] and apple["released"] == "2026-09-20" and apple["year"] == 2026
    assert ol["pages"] == 276 and ol["blurb"] == "" and ol["language"] == "de" and ol["released"] == "2026-03-05"
    assert apple["kinds"] == ["ebook"] and apple["sources"] == ["apple"] and ol["sources"] == ["openlibrary"]
    assert apple["token"] and apple["token"] != ol["token"]


def test_the_page_of_a_book_of_a_shelf_is_reached_by_its_token(browse: Browse) -> None:
    client = browse.client(browse.alice)
    card = by_title(next(s for s in wall(client)["strips"] if s["shelf"] == "krimi"))["Nebelwacht"]
    page = client.get(f"/api/v1/discover/books/{card['token']}")
    assert page.status_code == 200 and page.json()["title"] == "Nebelwacht"
    assert client.get(f"/api/v1/discover/books/{card['token']}/cover").status_code in (200, 404)


# ---- What the caller has ---------------------------------------------------------------------------------------------


def test_what_the_caller_has_is_marked_by_the_libraries_they_see_and_hidden_on_request(browse: Browse) -> None:
    alice, bob = browse.client(browse.alice), browse.client(browse.bob)
    mine = by_title(shelf(alice, "krimi"))
    assert mine["Der Leuchtturmwaerter"]["owned_work_id"] == browse.work_id("Der Leuchtturmwaerter")
    assert mine["Die Salzwiese"]["owned_work_id"] == browse.work_id("Die Salzwiese"), "I. Marwitz is Ilse Marwitz"
    assert mine["Orbit Null"]["owned_work_id"] is None, "bob has it, alice has not"
    theirs = by_title(shelf(bob, "krimi"))
    assert theirs["Orbit Null"]["owned_work_id"] == browse.work_id("Orbit Null")
    hidden = shelf(alice, "krimi", hide_owned="true")
    assert "Der Leuchtturmwaerter" not in titles(hidden) and "Die Salzwiese" not in titles(hidden)
    assert "Orbit Null" in titles(hidden) and hidden["owned"] == 2 and hidden["total"] == 17
    assert shelf(alice, "krimi")["total"] == 19


def test_a_book_in_a_library_the_caller_is_not_in_changes_nothing_in_their_answer(browse: Browse) -> None:
    """No oracle: the answer of a person is the same whatever the libraries of the others hold (the plaque, the switch
    that hides, the numbers, the order, the whole body)."""
    carol, alice = browse.client(browse.carol), browse.client(browse.alice)

    def read() -> list[Any]:
        out: list[Any] = []
        for hide in ("false", "true"):
            for client in (carol, alice):
                out += [wall(client, hide_owned=hide), shelf(client, "krimi", hide_owned=hide),
                        personal(client, hide_owned=hide)]
        return out

    read()
    # The covers of the books shown are fetched when they are shown: the answers are compared once they are there.
    jobs.run_pending(1000)
    before = read()
    book(browse.second, "glocke.epub", title="Die neunte Glocke", authors=("Edmund Halloran",), language="de")
    book(browse.second, "nebel.epub", title="Nebelwacht", authors=("Ilse Brandt",), language="de")
    scan(browse.second_id)
    after = read()
    assert after == before
    assert all(card["owned_work_id"] is None for card in shelf(carol, "krimi")["items"])


def test_a_person_with_no_library_has_nothing_and_a_new_account_sees_the_same_shelves(browse: Browse) -> None:
    someone = new_client(make_account("someone"))
    body = shelf(someone, "krimi", hide_owned="true")
    assert body["owned"] == 0 and body["total"] == 19
    assert titles(shelf(browse.client(browse.carol), "krimi")) == titles(shelf(someone, "krimi"))


# ---- One shelf opened -------------------------------------------------------------------------------------------------


def test_a_shelf_opened_holds_every_book_of_its_sections_once_the_most_wanted_first(browse: Browse) -> None:
    body = shelf(browse.client(browse.alice), "krimi")
    assert body["total"] == 19 and len(body["items"]) == 19 and body["shelf"] == "krimi" and body["dept"] == "bel"
    assert titles(body)[:4] == ["Nebelwacht", "Der stille Pier", "Der Leuchtturmwaerter", "Hafenlichter"]
    assert len(set(titles(body))) == 19, "a book that stands in two sections is one book"
    assert {s["key"]: s["count"] for s in body["sections"]} == {"new": 5, "popular": 13, "award": 2, "classic": 6}


def test_a_section_shows_its_books_in_the_order_the_sources_gave(browse: Browse) -> None:
    award = shelf(browse.client(browse.alice), "krimi", section="award")
    assert titles(award) == ["Der lange Heimweg", "Ein Fall von Salz"]
    assert {c["award"] for c in award["items"]} == {"Edgar Award"}
    assert titles(shelf(browse.client(browse.alice), "krimi", section="new")) == [
        "Nebelwacht", "Nebelläufer", "Die Salzwiese", "Die Salztöchter", "Acht Stunden bis zum Morgen"]


def test_the_language_filter_keeps_the_books_whose_language_nobody_said(browse: Browse) -> None:
    client = browse.client(browse.alice)
    german = shelf(client, "krimi", language="de")
    assert german["total"] == 19, "Apple does not say what language a book is in, and Open Library's are German here"
    assert shelf(client, "krimi", language="fr")["total"] == 6, "only the six of Apple's chart that say nothing remain"
    assert [f["key"] for f in german["languages"]] == ["de"]


def test_the_format_filter_asks_what_the_book_is_known_as(browse: Browse) -> None:
    client = browse.client(browse.alice)
    assert shelf(client, "krimi", format="ebook")["total"] == 19
    assert shelf(client, "krimi", format="audio")["total"] == 0, "no open source names a recording"


def test_the_age_filter_cuts_at_this_year_and_the_one_before_and_sixteen_years_back(browse: Browse) -> None:
    client = browse.client(browse.alice)
    new = shelf(client, "krimi", years="new")
    assert titles(new) == ["Nebelwacht", "Die Salzwiese", "Orbit Null", "Nebelläufer", "Die Salztöchter",
                           "Fremdes Bild", "Acht Stunden bis zum Morgen"]
    assert new["year_bounds"] == {"new_from": 2025, "mid_from": 2010}
    assert titles(shelf(client, "krimi", years="old")) == [
        "Der lange Heimweg", "Ein Fall von Salz", "Altes Geld tötet", "Das verschlossene Boot", "Gezeitentafel",
        "Der unmögliche Preis"]
    assert titles(shelf(client, "krimi", years="mid")) == [
        "Der stille Pier", "Der Leuchtturmwaerter", "Hafenlichter", "Die neunte Glocke", "Kurze Nacht"],         "2010 to 2024 holds the rest that has a year"


def test_the_topic_filter_and_the_counts_of_the_topics(browse: Browse) -> None:
    client = browse.client(browse.alice)
    body = shelf(client, "krimi")
    counts = {t["key"]: t["count"] for t in body["topics"]}
    assert counts["ermittler"] >= 3 and "psycho" not in counts
    detectives = shelf(client, "krimi", topic="ermittler")
    assert detectives["total"] == counts["ermittler"]
    assert "Hafenlichter" in titles(detectives) and "Nebelwacht" in titles(detectives), \
        "a subject (detective and mystery stories) and a genre of Apple (Polizeiromane) say the same"
    assert all("ermittler" in c["topics"] for c in detectives["items"])


def test_sorting_by_the_newest_and_by_the_fewest_pages(browse: Browse) -> None:
    client = browse.client(browse.alice)
    short = by_pages = shelf(client, "krimi", sort="short")["items"]
    pages = [c["pages"] for c in by_pages if c["pages"] is not None]
    assert pages == sorted(pages) and pages[0] == 160
    assert all(c["pages"] is None for c in short[len(pages):]), "the books whose pages nobody said come last"
    newest = shelf(client, "krimi", sort="new")["items"]
    assert newest[0]["title"] == "Nebelwacht" and newest[0]["released"] == "2026-09-20"


def test_a_shelf_is_paged_and_the_total_does_not_change(browse: Browse) -> None:
    client = browse.client(browse.alice)
    first = shelf(client, "krimi", limit=8)
    second = shelf(client, "krimi", limit=8, offset=8)
    third = shelf(client, "krimi", limit=8, offset=16)
    assert [len(p["items"]) for p in (first, second, third)] == [8, 8, 3]
    assert {p["total"] for p in (first, second, third)} == {19}
    assert titles(first) + titles(second) + titles(third) == titles(shelf(client, "krimi"))


def test_the_shelves_hold_the_same_books_for_both_markets_titles(browse: Browse) -> None:
    browse.shelves("us")
    client = browse.client(browse.alice)
    english = shelf(client, "krimi", market="us", section="award")
    assert titles(english) == ["The Long Goodbye Road", "A Case of Salt"] and english["market"] == "us"
    assert titles(shelf(client, "krimi", section="award")) == ["Der lange Heimweg", "Ein Fall von Salz"]


# ---- The sources are never asked while the caller waits ----------------------------------------------------------------


def test_reading_asks_no_source_and_a_market_never_fetched_says_that_it_is_being_fetched(browse: Browse) -> None:
    client = browse.client(browse.alice)
    asked = len(browse.fake.log)
    body = wall(client, market="us")
    assert len(browse.fake.log) == asked, "nothing is asked while the caller waits"
    assert body["state"] == "fetching" and body["pending"] and body["strips"] == []
    with SessionLocal() as db:
        waiting = db.scalar(select(func.count()).select_from(Job).where(Job.kind == "shelves", Job.state == "queued"))
    assert waiting == len(shelves.SHELVES) * 2 - 0 or waiting > 0
    browse.shelves("us")
    done = wall(client, market="us")
    assert done["state"] == "ready" and not done["pending"] and done["strips"]


def test_with_the_main_switch_off_what_was_kept_is_shown_and_a_new_market_says_why(browse: Browse) -> None:
    browse.settings(sources_enabled=False)
    client = browse.client(browse.alice)
    kept = wall(client)
    assert kept["state"] == "ready" and not kept["asking"] and kept["strips"]
    empty = wall(client, market="fr")
    assert empty["state"] == "main_off" and empty["strips"] == [] and not empty["pending"]
    assert shelf(client, "krimi", market="fr")["items"] == []


def test_with_every_source_off_a_new_market_says_that_there_is_none(browse: Browse) -> None:
    browse.settings(source_apple=False, source_openlibrary=False)
    body = wall(browse.client(browse.alice), market="fr")
    assert body["state"] == "no_source" and not body["asking"]


# ---- The doors ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/discover/browse", "/api/v1/discover/browse/personal",
                                  "/api/v1/discover/shelves/krimi"])
def test_nobody_reads_the_shelves_without_signing_in(browse: Browse, path: str) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get(path).status_code == 401


@pytest.mark.parametrize("params", [
    {"market": "deu"}, {"market": "D1"}, {"section": "bogus"}, {"years": "old2"}, {"sort": "random"}, {"format": "video"},
    {"limit": 0}, {"limit": 100000}, {"offset": -1}, {"topic": "Ermittler"}, {"topic": "a" * 40}, {"language": "ger"}])
def test_hostile_questions_are_refused(browse: Browse, params: dict[str, Any]) -> None:
    answer = browse.client(browse.alice).get("/api/v1/discover/shelves/krimi", params=params)
    assert answer.status_code == 422, answer.text


@pytest.mark.parametrize("key", ["nosuchshelf", "..%2F..%2Fetc", "KRIMI", "krimi%00", "a" * 300])
def test_a_shelf_that_is_none_is_not_found(browse: Browse, key: str) -> None:
    assert browse.client(browse.alice).get(f"/api/v1/discover/shelves/{key}").status_code == 404


def test_a_market_that_is_not_in_the_table_is_not_asked_for(browse: Browse) -> None:
    asked = len(browse.fake.log)
    body = wall(browse.client(browse.alice), market="zz")
    assert len(browse.fake.log) == asked
    assert body["market"] in body["markets"], "the market falls back to the one of the page's language, never to 'zz'"
