"""Discover over the API: the lists set against what the caller has, the filters, the wish list and the pages of
books.

The rule under test: **what the caller has is worked out from the libraries the caller may see, and from nothing
else.** Two people with two libraries read the same list; the one never learns that the other owns a book, by the
plaque, by the switch that hides it, by the number of books it hid, by the order or by anything else in the answer."""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Edition, LibraryMember, Wish
from app.services.discover import markets, owned, wishlist
from app.services.discover.view import by_token

from .concurrent import at_once
from .conftest import make_account, new_client
from .discover_helpers import entries
from .discover_shelves import Shelves, by_title, read, token_of
from .library_helpers import book, scan
from .match_helpers import member

# ---- What the caller has -----------------------------------------------------------------------------------------


def test_a_list_marks_what_the_caller_has_by_title_and_author_and_by_nothing_else(shelves: Shelves) -> None:
    alice, bob, carol = (shelves.client(a) for a in (shelves.alice, shelves.bob, shelves.carol))
    mine = by_title(read(alice))
    assert mine["Der Leuchtturmwaerter"]["owned_work_id"] == shelves.work_id("Der Leuchtturmwaerter")
    # "I. Marwitz" on the shelf is "Ilse Marwitz" of the list: the last name and an initial that fits.
    assert mine["Die Salzwiese"]["owned_work_id"] == shelves.work_id("Die Salzwiese")
    assert mine["Orbit Null"]["owned_work_id"] is None, "bob has it, alice has not"
    theirs = by_title(read(bob))
    assert theirs["Orbit Null"]["owned_work_id"] == shelves.work_id("Orbit Null")
    assert theirs["Der Leuchtturmwaerter"]["owned_work_id"] is None
    assert all(item["owned_work_id"] is None for item in read(carol)["items"])
    assert all(item["owned_work_id"] is None for item in read(shelves.client(make_account("someone")))["items"])


def test_a_book_in_a_library_the_caller_is_not_in_changes_nothing_in_their_answer(shelves: Shelves) -> None:
    """No oracle: the answer of a person is the same whatever the libraries of the others hold (the plaque, the switch
    that hides, the count of what was hidden, the order, the totals, the whole body)."""
    carol, alice = shelves.client(shelves.carol), shelves.client(shelves.alice)
    asked = [("best", {}), ("best", {"hide_owned": "true"}), ("best", {"format": "audio"}), ("new", {}), ("soon", {})]
    before_carol = [read(carol, name, **params) for name, params in asked]
    before_alice = [read(alice, name, **params) for name, params in asked]
    # Bob's library gets books of the lists, among them the one at the top of the chart; carol and alice have none of
    # it.
    book(shelves.second, "weber.epub", title="Das Lied der Weberinnen", authors=("Iris Quist",), language="de")
    book(shelves.second, "echo.epub", title="Die Salzwiese", authors=("Ilse Marwitz",), language="de")
    scan(shelves.second_id)
    after_carol = [read(carol, name, **params) for name, params in asked]
    after_alice = [read(alice, name, **params) for name, params in asked]
    strip = lambda bodies: [{k: v for k, v in b.items() if k != "fetched_at"} for b in bodies]
    assert strip(after_carol) == strip(before_carol)
    assert strip(after_alice) == strip(before_alice)
    assert before_carol[1]["owned"] == 0 and all(item["owned_work_id"] is None for item in after_carol[0]["items"])


def test_the_switch_that_hides_what_the_caller_has_hides_only_that_and_counts_only_that(shelves: Shelves) -> None:
    alice, bob = shelves.client(shelves.alice), shelves.client(shelves.bob)
    shown = read(alice, hide_owned="true")
    titles = {item["title"] for item in shown["items"]}
    assert "Der Leuchtturmwaerter" not in titles and "Die Salzwiese" not in titles and "Orbit Null" in titles
    assert shown["owned"] == 2 and shown["total"] == len(shown["items"]) + 2
    # The same list for bob hides his own book and counts one.
    other = read(bob, hide_owned="true")
    assert "Orbit Null" not in {item["title"] for item in other["items"]} and other["owned"] == 1
    # Not hidden: everything is there, in the same order.
    assert [i["title"] for i in read(alice)["items"]] == [i["title"] for i in read(bob)["items"]]


def test_leaving_a_library_takes_its_books_off_the_shelf_of_the_list(shelves: Shelves) -> None:
    alice = shelves.client(shelves.alice)
    assert by_title(read(alice))["Der Leuchtturmwaerter"]["owned_work_id"] is not None
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=shelves.alice.id).delete()
        db.commit()
    assert all(item["owned_work_id"] is None for item in read(alice)["items"])


def test_the_hiding_is_on_from_the_start_and_is_kept_with_the_profile(shelves: Shelves) -> None:
    alice = shelves.client(shelves.alice)
    assert alice.get("/api/v1/auth/me").json()["profile"]["discover_hide_owned"] is True
    assert "Der Leuchtturmwaerter" not in {i["title"] for i in
                                           alice.get("/api/v1/discover/lists/best", params={"market": "de"}).json()["items"]}
    assert alice.put("/api/v1/me/preferences", json={"discover_hide_owned": False}).status_code == 200
    assert "Der Leuchtturmwaerter" in {i["title"] for i in
                                       alice.get("/api/v1/discover/lists/best", params={"market": "de"}).json()["items"]}
    assert alice.put("/api/v1/me/preferences", json={"discover_hide_owned": "yes"}).status_code == 422


def test_the_rule_for_a_book_on_the_shelf_needs_the_whole_author_and_the_place_in_a_series() -> None:
    shelf = owned.Shelf(
        by_title={"nach dem sturm": [(1, ["Hans Feldkamp"], None)], "der erste band": [(2, ["Ada Quist"], 1.0)]},
        by_isbn={"9783999900018": 3}, by_asin={"B0EXAMPL01": 4}, by_ref={("hardcover", "8101"): 5})
    mara = ["Mara Feldkamp"]
    assert owned.find(shelf, title="Nach dem Sturm", authors=mara) is None, "the last name alone is no author"
    assert owned.find(shelf, title="Nach dem Sturm", authors=["Hans Feldkamp"]) == 1
    assert owned.find(shelf, title="Nach dem Sturm", authors=["H. Feldkamp"]) == 1
    assert owned.find(shelf, title="Nach dem Sturm", authors=["Feldkamp"]) is None
    assert owned.find(shelf, title="Nach dem Sturm", authors=[]) is None, "no author, no title match"
    assert owned.find(shelf, title="Der erste Band", authors=["Ada Quist"], position=2.0) is None
    assert owned.find(shelf, title="Der erste Band", authors=["Ada Quist"], position=1.0) == 2
    assert owned.find(shelf, title="Anything", authors=mara, isbns=["9783999900018"]) == 3
    assert owned.find(shelf, title="Anything", authors=mara, asin="b0exampl01") == 4
    assert owned.find(shelf, title="Anything", authors=mara, refs=[("hardcover", "8101")]) == 5
    assert owned.find(shelf, title="Anything", authors=mara, refs=[("hardcover", "9999")]) is None


def test_the_names_a_source_gave_to_an_edition_the_caller_sees_find_the_book_but_those_of_others_do_not(
        shelves: Shelves) -> None:
    from app.models import ExternalRef

    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.title == "Orbit Null"))
        assert edition is not None
        db.add(ExternalRef(edition_id=edition.id, source="audible", kind="edition", key="B0CHART002", market="de"))
        db.commit()
    # "Nebel ueber dem Deich" (Audible's ASIN B0CHART002) is bob's through the name Audible gave his edition, whatever
    # it is called: the title and the author say nothing of it.
    bob, alice = shelves.client(shelves.bob), shelves.client(shelves.alice)
    assert by_title(read(bob))["Nebel ueber dem Deich"]["owned_work_id"] == shelves.work_id("Orbit Null")
    assert by_title(read(alice))["Nebel ueber dem Deich"]["owned_work_id"] is None


# ---- Filters, merging, ranks ---------------------------------------------------------------------------------------


def test_apple_s_e_book_and_audible_s_recording_of_one_book_are_one_card(shelves: Shelves) -> None:
    body = read(shelves.client(shelves.carol))
    card = by_title(body)["Der Leuchtturmwaerter"]
    assert card["kinds"] == ["audio", "ebook"] and card["sources"] == ["apple", "audible"]
    assert [t for t in by_title(body)].count("Der Leuchtturmwaerter") == 1
    assert {f["kind"] for f in card["forms"]} == {"ebook", "audio"}


def test_the_rank_counts_the_merged_list_and_the_filters_keep_it(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    everything = read(carol)
    assert [i["rank"] for i in everything["items"]] == list(range(1, len(everything["items"]) + 1))
    by_rank = {i["title"]: i["rank"] for i in everything["items"]}
    # A filter on the language or the genre does not renumber ...
    crime = read(carol, genre="krimi")
    assert crime["items"] and all(by_rank[i["title"]] == i["rank"] for i in crime["items"])
    # ... but the format does: the list of the e-books is Apple's chart as it is, the list of the recordings
    # Audible's.
    ebooks = read(carol, format="ebook")
    assert [(i["rank"], i["title"]) for i in ebooks["items"]] == [(1, "Der Leuchtturmwaerter"), (2, "Die Salzwiese"),
                                                                  (3, "Orbit Null")]
    recordings = read(carol, format="audio")
    assert [i["title"] for i in recordings["items"]] == ["Der Leuchtturmwaerter", "Nebel ueber dem Deich",
                                                         "The Salt Orchard"]
    assert [i["rank"] for i in recordings["items"]] == [1, 2, 3]


def test_the_source_named_follows_the_filter(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    assert read(carol)["sources"] == ["apple", "audible"]
    assert read(carol, format="ebook")["sources"] == ["apple"]
    assert read(carol, format="audio")["sources"] == ["audible"]
    # Nothing found: the sources that would have been asked for the format.
    assert read(carol, format="audio", genre="lyrik")["sources"] == ["audible"]


def test_the_language_filter_offers_the_languages_the_list_holds_and_keeps_those_and_the_unknown(
        shelves: Shelves) -> None:
    """Apple's chart does not say what language a book is in, and Audible's products do: a book both lists name
    (a chart book that is in both) has the language Audible gave; two that only Apple names have none. The filter does
    not leave those out, since it may well be the book asked for."""
    carol = shelves.client(shelves.carol)
    body = read(carol)
    assert {entry["code"]: entry["count"] for entry in body["languages"]} == {"de": 2, "en": 1}
    assert {i["title"]: i["language"] for i in body["items"]}["Orbit Null"] in ("", None)
    assert {i["title"]: i["language"] for i in body["items"]}["Der Leuchtturmwaerter"] == "de"
    english = read(carol, language="en")
    assert {i["title"] for i in english["items"]} == {"The Salt Orchard", "Die Salzwiese", "Orbit Null"}
    german = read(carol, language="de")
    assert {i["title"] for i in german["items"]} == {"Der Leuchtturmwaerter", "Nebel ueber dem Deich", "Die Salzwiese",
                                                     "Orbit Null"}
    assert {i["title"] for i in read(carol, language="fr")["items"]} == {"Die Salzwiese", "Orbit Null"}


def test_the_isbn_and_the_asin_of_an_edition_in_another_library_are_no_oracle(shelves: Shelves) -> None:
    """The ISBN or the ASIN of a book on bob's shelf, the same as the one of a chart book: alice and carol (who are not
    in bob's library) are not told they have it, by the plaque, the switch that hides it or the shelf itself."""
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.title == "Orbit Null"))
        assert edition is not None
        edition.asin, edition.isbn = "B0CHART002", "9783999900018"
        db.commit()
    with SessionLocal() as db:
        theirs = {name: owned.load(db, account.id) for name, account in
                  (("alice", shelves.alice), ("bob", shelves.bob), ("carol", shelves.carol))}
    assert "B0CHART002" in theirs["bob"].by_asin and "9783999900018" in theirs["bob"].by_isbn
    for name in ("alice", "carol"):
        assert "B0CHART002" not in theirs[name].by_asin and "9783999900018" not in theirs[name].by_isbn
    alice, bob, carol = (shelves.client(a) for a in (shelves.alice, shelves.bob, shelves.carol))
    assert by_title(read(bob))["Nebel ueber dem Deich"]["owned_work_id"] is not None, "bob has it, by the ASIN"
    for client in (alice, carol):
        assert by_title(read(client))["Nebel ueber dem Deich"]["owned_work_id"] is None
        shown = [i["title"] for i in read(client, hide_owned="true")["items"]]
        assert "Nebel ueber dem Deich" in shown, "it is not hidden: it is not theirs"


def test_taking_a_book_off_a_list_takes_off_the_callers_wish_and_nobody_elses(shelves: Shelves) -> None:
    alice, bob = shelves.client(shelves.alice), shelves.client(shelves.bob)
    token = token_of(alice, "Der Leuchtturmwaerter")
    assert alice.put(f"/api/v1/wishlist/{token}").json()["count"] == 1
    assert bob.put(f"/api/v1/wishlist/{token}").json()["count"] == 1
    with SessionLocal() as db:
        row = by_token(db, token)
        assert row is not None
        assert wishlist.remove(db, shelves.bob.id, row) == 1, "one wish went: bob's own"
        assert {w.user_id for w in db.scalars(select(Wish))} == {shelves.alice.id}
        assert wishlist.remove(db, shelves.bob.id, row) == 0, "a second try changes nothing"
    assert alice.get("/api/v1/wishlist").json()["count"] == 1
    assert bob.delete(f"/api/v1/wishlist/{token}").json()["count"] == 0
    assert alice.get("/api/v1/wishlist").json()["count"] == 1, "bob's DELETE left alice's wish"


def test_new_and_soon_carry_their_day_and_come_in_the_order_of_the_day(shelves: Shelves) -> None:
    shelves.new_among_the_best()
    carol = shelves.client(shelves.carol)
    new = read(carol, "new")["items"]
    assert [(i["title"], i["released"], i["upcoming"], i["rank"]) for i in new] == [
        ("Kaltes Licht", "2026-10-09", False, None), ("Das Lied der Weberinnen", "2026-09-01", False, None)]
    soon = read(carol, "soon")["items"]
    assert [(i["title"], i["released"], i["upcoming"]) for i in soon] == [
        ("Schwarzer Sand", "2026-10-30", True), ("Das Echo am Ende der Strasse", "2026-12-04", True)]
    best = [i["title"] for i in read(carol, "best", format="audio")["items"]]
    assert "Schwarzer Sand" not in best, "a pre-order is not among the best sellers"
    assert "Kaltes Licht" in best and "Ein Winter auf dem Lande" in best


def test_a_filter_that_is_none_is_refused_and_the_market_falls_back(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    for params in ({"format": "film"}, {"genre": "horror"}, {"language": "deu"}, {"market": "deu"}):
        assert carol.get("/api/v1/discover/lists/best", params=params).status_code == 422
    assert carol.get("/api/v1/discover/lists/nonsense").status_code == 422
    # A market the table does not have is the market of the language of the page.
    body = carol.get("/api/v1/discover/lists/best", params={"market": "zz", "hide_owned": "false"},
                     headers={"X-Nexbooks-Language": "de"}).json()
    assert body["market"] == "de"


def test_the_market_follows_the_profile_then_the_language(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    one = lambda **h: carol.get("/api/v1/discover/lists/best", headers=h).json()["market"]
    assert one(**{"X-Nexbooks-Language": "de"}) == "de" and one(**{"X-Nexbooks-Language": "en"}) == "us"
    assert one(**{"X-Nexbooks-Language": "ja"}) == "us", "a language nexbooks does not show is no hint"
    assert [markets.default_market(code) for code in ("de", "DE-at", "fr", "it", "es", "en", "ja", "")] == [
        "de", "de", "fr", "it", "es", "us", "us", "us"]
    assert carol.put("/api/v1/me/preferences", json={"market": "uk"}).status_code == 200
    assert one(**{"X-Nexbooks-Language": "de"}) == "uk"


# ---- Honest states -------------------------------------------------------------------------------------------------


def test_a_list_says_what_is_missing_when_nothing_can_be_asked(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    shelves.settings(sources_enabled=False)
    # What was kept is still shown, with the switch said to be off ...
    body = read(carol, "best")
    assert body["state"] == "ready" and body["asking"] is False and body["items"]
    assert {a["state"] for a in body["availability"]} == {"main_off"}
    # ... and a list never fetched says what is missing.
    empty = read(carol, "best", market="fr")
    assert empty["items"] == [] and empty["state"] == "main_off"
    shelves.settings(sources_enabled=True, source_apple=False, source_audible=False)
    assert read(carol, "best", market="it")["state"] == "no_source"
    assert read(carol, "trending")["state"] == "no_source", "Hardcover has no token"


def test_a_list_that_is_being_fetched_says_so_and_one_that_came_back_empty_says_that(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    body = read(carol, "best", market="us")
    assert body["state"] == "fetching" and body["items"] == []
    shelves.fake.apple = {"feed": {"results": []}}
    shelves.fake.audible_chart = {"BestSellers": []}
    from app.services import jobs

    while jobs.run_pending(100):
        pass
    assert read(carol, "best", market="us")["state"] == "empty"


def test_looking_at_a_list_is_what_makes_the_server_fetch_it_and_the_answer_does_not_wait_for_it(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    asked = len(shelves.fake.log)
    body = read(carol, "best", market="uk")
    assert len(shelves.fake.log) == asked, "no source is asked while the caller waits"
    assert body["state"] == "fetching"
    from app.services import jobs

    while jobs.run_pending(100):
        pass
    assert len(shelves.fake.log) > asked
    assert read(carol, "best", market="uk")["items"]


def test_the_lists_need_a_sign_in_and_the_wish_list_needs_one_too(shelves: Shelves) -> None:
    anonymous = new_client()
    for path in ("/api/v1/discover/lists/best", "/api/v1/discover/for-you", "/api/v1/wishlist",
                 "/api/v1/discover/books/abc", "/api/v1/discover/series/1"):
        assert anonymous.get(path).status_code == 401
    assert anonymous.put("/api/v1/wishlist/abc").status_code == 401


# ---- The wish list -------------------------------------------------------------------------------------------------


def test_a_book_goes_on_the_wish_list_and_off_it_and_the_list_is_the_person_s_own(shelves: Shelves) -> None:
    alice, bob, boss = shelves.client(shelves.alice), shelves.client(shelves.bob), shelves.client(make_account("boss"))
    token = token_of(alice, "Orbit Null")
    answer = alice.put(f"/api/v1/wishlist/{token}")
    assert answer.status_code == 200 and answer.json()["count"] == 1
    assert [i["title"] for i in answer.json()["items"]] == ["Orbit Null"] and answer.json()["items"][0]["wished"] is True
    assert by_title(read(alice))["Orbit Null"]["wished"] is True
    # Nobody else sees it: not in their list, not on their cards, not the operator.
    for other in (bob, boss, shelves.client(shelves.carol)):
        assert other.get("/api/v1/wishlist").json() == {"count": 0, "items": [], "via": {}, "via_why": {}}
        assert by_title(read(other))["Orbit Null"]["wished"] is False
    operator = new_client(make_account("operator-two", "operator"))
    assert operator.get("/api/v1/wishlist").json()["count"] == 0
    assert alice.delete(f"/api/v1/wishlist/{token}").json() == {"count": 0, "items": [], "via": {}, "via_why": {}}
    assert by_title(read(alice))["Orbit Null"]["wished"] is False


def test_putting_on_and_taking_off_twice_changes_nothing_the_second_time(shelves: Shelves) -> None:
    alice = shelves.client(shelves.alice)
    token = token_of(alice, "Orbit Null")
    assert alice.put(f"/api/v1/wishlist/{token}").json()["count"] == 1
    assert alice.put(f"/api/v1/wishlist/{token}").json()["count"] == 1
    assert alice.delete(f"/api/v1/wishlist/{token}").json()["count"] == 0
    assert alice.delete(f"/api/v1/wishlist/{token}").status_code == 200
    with SessionLocal() as db:
        assert db.query(Wish).count() == 0


def test_two_requests_at_the_same_moment_make_one_row(shelves: Shelves) -> None:
    alice = shelves.client(shelves.alice)
    token = token_of(alice, "Orbit Null")
    with ExitStack() as stack:
        clients = [stack.enter_context(new_client(shelves.alice)) for _ in range(8)]
        answers = at_once([lambda own=own: own.put(f"/api/v1/wishlist/{token}").status_code for own in clients])
    assert answers == [200] * 8
    with SessionLocal() as db:
        assert db.query(Wish).count() == 1


def test_the_wish_list_holds_only_so_many_and_says_so(shelves: Shelves, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wishlist, "MAX_WISHES", 2)
    alice = shelves.client(shelves.alice)
    tokens = [token_of(alice, t) for t in ("Orbit Null", "Nebel ueber dem Deich", "The Salt Orchard")]
    assert alice.put(f"/api/v1/wishlist/{tokens[0]}").status_code == 200
    assert alice.put(f"/api/v1/wishlist/{tokens[1]}").status_code == 200
    full = alice.put(f"/api/v1/wishlist/{tokens[2]}")
    assert full.status_code == 409 and full.json()["detail"]["code"] == "wishlist_full"
    # The one on the list already can be put on it again when the list is full.
    assert alice.put(f"/api/v1/wishlist/{tokens[0]}").status_code == 200


def test_a_book_of_two_sources_is_one_wish_and_comes_off_with_one_press(shelves: Shelves) -> None:
    carol = shelves.client(shelves.carol)
    token = token_of(carol, "Der Leuchtturmwaerter")
    audible_row = None
    with SessionLocal() as db:
        from app.models import ForeignBook

        audible_row = db.scalar(select(ForeignBook).where(ForeignBook.ref == "audible:B0CHART001"))
        assert audible_row is not None
        other = audible_row.token
    carol.put(f"/api/v1/wishlist/{token}")
    carol.put(f"/api/v1/wishlist/{other}")
    assert carol.get("/api/v1/wishlist").json()["count"] == 1, "Apple's and Audible's entry are one book"
    assert carol.delete(f"/api/v1/wishlist/{other}").json()["count"] == 0


def test_a_token_that_is_none_answers_like_a_book_that_is_not_there(shelves: Shelves) -> None:
    alice = shelves.client(shelves.alice)
    for token in ("1", "2", "nope", "x" * 30, "a b", "../x"):
        assert alice.get(f"/api/v1/discover/books/{token}").status_code in (404, 422)
        assert alice.put(f"/api/v1/wishlist/{token}").status_code in (404, 422)
    token = token_of(alice, "Orbit Null")
    assert len(token) == 18 and not token.isdigit()


def test_a_book_on_the_wish_list_that_is_on_the_shelf_now_says_so(shelves: Shelves) -> None:
    bob = shelves.client(shelves.bob)
    token = token_of(bob, "Der Leuchtturmwaerter")
    bob.put(f"/api/v1/wishlist/{token}")
    assert bob.get("/api/v1/wishlist").json()["items"][0]["owned_work_id"] is None
    member(shelves.first_id, "bob", "read")
    assert bob.get("/api/v1/wishlist").json()["items"][0]["owned_work_id"] == shelves.work_id("Der Leuchtturmwaerter")


def test_entries_of_the_fixture_are_what_the_tests_above_count_on(shelves: Shelves) -> None:
    assert [t for _r, s, t in entries("best") if s == "apple"] == ["Der Leuchtturmwaerter", "Die Salzwiese", "Orbit Null"]
    assert [t for _r, s, t in entries("best") if s == "audible"] == [
        "Der Leuchtturmwaerter", "Nebel ueber dem Deich", "The Salt Orchard"]


def test_the_token_of_a_book_and_of_a_wish_does_not_reach_the_log() -> None:
    """The tokens say what somebody looks at: the address of a page, of a cover and of a wish is masked in every line."""
    import secrets

    from app.services import logs

    token = secrets.token_urlsafe(14)[:18]
    for path in (f"/api/v1/discover/books/{token}", f"/api/v1/discover/books/{token}/cover?v=abc", f"/api/v1/wishlist/{token}"):
        assert token not in logs.redact(f"GET {path} -> 200 in 3ms"), path
