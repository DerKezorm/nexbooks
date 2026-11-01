"""Fetching the lists: what is asked of the sources and only when it may be, what is kept, what a failure or a stop
leaves, and when a list is due again. Every answer is an invented one (``tests/fixtures/sources``); no test asks a
real service."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ForeignBook, Job, ListEntry, ListFetch, Wish
from app.services import covers, jobs
from app.services.discover import foreign, lists, pics
from app.services.readers.limits import COVER_MAX_BYTES
from app.services.sources import apple, audible, hardcover
from app.services.sources.model import Candidate

from .conftest import make_account
from .discover_helpers import NOW, TODAY, Disc, entries, entries_released, waiting_jobs
from .fake_sources import audible_product, fixture, host_of

APPLE = "rss.marketingtools.apple.com"


@pytest.fixture
def disc(monkeypatch: pytest.MonkeyPatch) -> Disc:
    return Disc(monkeypatch)


# ---- What the clients make of the answers ------------------------------------------------------------------------


def test_the_chart_of_apple_gives_the_books_in_order_and_leaves_out_what_is_none() -> None:
    found = apple.parse(fixture("apple_chart.json"), "de")
    assert [c.title for c in found] == ["Der Leuchtturmwaerter", "Die Salzwiese", "Orbit Null"]
    first = found[0]
    assert first.key == "1700000001" and first.authors == ["Mara Feldkamp"] and first.released == "2024-03-14"
    assert first.languages == ["de"] and first.subjects == ["Romane & Erzählungen"]
    assert first.cover_url.startswith("https://is1-ssl.mzstatic.com/")
    # A picture at a host that is not Apple's is not kept as an address.
    assert found[2].cover_url == ""
    for data in (None, [], {}, {"feed": None}, {"feed": {"results": "x"}}, {"feed": {"results": [1, None, {}]}}):
        assert apple.parse(data) == []


def test_the_address_of_apple_is_built_from_two_letters_only() -> None:
    assert apple.url("de") == "https://rss.marketingtools.apple.com/api/v2/de/books/top-paid/50/books.json"
    for bad in ("", "d", "de/", "../x", "DE", "d3", "deu"):
        with pytest.raises(ValueError):
            apple.url(bad)
    assert apple.bigger("https://is1-ssl.mzstatic.com/image/thumb/x/100x100bb.jpg").endswith("/320x480bb.jpg")
    # The older feed names the height only.
    assert apple.bigger("https://is1-ssl.mzstatic.com/image/thumb/x/0x170bb.png").endswith("/320x480bb.png")


def test_the_lists_of_audible_carry_the_day_and_the_categories() -> None:
    found = audible.parse_products(fixture("audible_best.json"), "de")
    assert [c.key for c in found] == ["B0CHART001", "B0CHART002", "B0CHART003"], "a bad number is left out"
    assert found[0].released == "2024-04-02" and found[0].subjects == ["Belletristik", "Romane"]
    assert found[1].series == "Die Deich-Krimis" and found[1].series_pos == 1.0


def test_the_trends_of_hardcover_come_in_the_order_of_the_ids_and_bad_records_are_dropped(disc: Disc) -> None:
    disc.connect_hardcover()
    with SessionLocal() as db:
        found = hardcover.trending(db, NOW.date())
    assert [c.title for c in found] == ["Die Krone der Weberinnen", "Glas und Asche", "Orbit Sieben"]
    assert found[0].authors == ["Iris Quist"] and found[0].subjects == ["Fantasy"] and found[0].released == "2026-02-10"
    assert found[0].series == "Die Chroniken von Arvel" and found[0].series_pos == 1.0
    assert found[0].cover_url == "", "a picture at a host that is not Hardcover's is not kept"
    assert found[1].cover_url.startswith("https://assets.hardcover.app/")
    asked = [r for r in disc.fake.log if host_of(r) == "api.hardcover.app"]
    assert len(asked) == 2 and b"mutation" not in b"".join(r.content for r in asked)


# ---- The volumes of a series at Hardcover ------------------------------------------------------------------------


def _row(title: str, place: float, readers: int) -> Candidate:
    return Candidate(source="hardcover", key=str(abs(hash((title, place)))), title=title, authors=["Iris Quist"],
                     series="Die Chroniken von Arvel", series_pos=place, weight=readers)


def test_a_series_at_hardcover_is_one_book_for_each_place_without_translations_boxes_and_side_places(
        disc: Disc) -> None:
    disc.connect_hardcover()
    with SessionLocal() as db:
        found = hardcover.series_books(db, 77)
    assert [(c.series_pos, c.title) for c in found] == [
        (1.0, "Die Krone der Weberinnen"), (2.0, "Glas und Asche"), (3.0, "Der Thron aus Bernstein"),
        (4.0, "Silberschwinge")], "five volumes in the answer's 17 rows are four books"
    # The question leaves out the copies and the boxes itself (the answer gives 61 rows for a series of seven books
    # when it does not), and says which: the books that name another as their original, and the compilations.
    [asked] = [r for r in disc.fake.log if host_of(r) == "api.hardcover.app"]
    query = json.loads(asked.content)["query"].replace(" ", "")
    assert "canonical_id:{_is_null:true}" in query and "compilation:{_eq:false}" in query


def test_among_the_rows_of_one_place_the_one_with_most_readers_is_the_volume() -> None:
    found = hardcover.volumes_of([_row("Copy", 1, 3), _row("Die Krone", 1, 1500), _row("Another copy", 1, 0),
                                  _row("Glas", 2, 900), _row("Glas, another edition", 2, 4)])
    assert [c.title for c in found] == ["Die Krone", "Glas"]


def test_a_place_that_is_not_a_whole_number_from_one_needs_readers_of_its_own() -> None:
    found = hardcover.volumes_of([
        _row("Prelude copy", 0, 4), _row("Novella", 1.5, 300), _row("Novella copy", 2.5, 9), _row("Book 1", 1, 1500),
        _row("Book 2", 2, 900), _row("Book 5, not out yet", 5, 0)])
    # 0 and 2.5 have fewer readers than 5 or than 2 percent of the readers of the best known volume (30); the
    # novella has more; a whole place keeps its book whatever the readers (a volume that is not out has none).
    assert [(c.series_pos, c.title) for c in found] == [
        (1.0, "Book 1"), (1.5, "Novella"), (2.0, "Book 2"), (5.0, "Book 5, not out yet")]
    assert hardcover.volumes_of([_row("Only a copy", 0.5, 2)]) == []
    assert hardcover.volumes_of([]) == []


def test_a_title_that_names_a_pack_of_books_is_no_volume() -> None:
    packs = ["Arvel Box Set", "Arvel Boxed Set", "The Complete Omnibus", "Arvel Bundle", "Arvel Collection",
             "The Arvel 4-Book Set", "Arvel Books 1-3", "Arvel Bücher 1 bis 3", "Arvel Gesamtausgabe",
             "Arvel Sammelband", "Arvel Coffret"]
    found = hardcover.volumes_of([_row(title, 1 + n, 5000) for n, title in enumerate(packs)] +
                                 [_row("Die Krone der Weberinnen", 20, 10), _row("Das Buch 3 der Zeit", 21, 10),
                                  _row("Boxing Day", 22, 10)])
    assert [c.title for c in found] == ["Die Krone der Weberinnen", "Das Buch 3 der Zeit", "Boxing Day"]


# ---- Fetching a unit ---------------------------------------------------------------------------------------------


def test_a_chart_is_kept_as_a_list_with_ranks_books_and_covers_to_fetch(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        assert lists.fetch_unit(db, "apple", "de", "best") == "ok"
    assert entries("best") == [(1, "apple", "Der Leuchtturmwaerter"), (2, "apple", "Die Salzwiese"),
                               (3, "apple", "Orbit Null")]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(ForeignBook.id))) == 3
        log = db.get(ListFetch, ("apple", "de", "best"))
        assert log is not None and log.state == "ok" and log.count == 3 and log.fetched_at == NOW
        # A job for each cover that has an address at the source's host (two of the three).
        assert sorted(j.ref for j in db.scalars(select(Job).where(Job.kind == "fcover"))) == sorted(
            r.id for r in db.scalars(select(ForeignBook).where(ForeignBook.cover_url != "")))
    assert disc.fake.count(APPLE) == 1


def chart_page(prefix: str, count: int, days: list[int], *, newest_first: bool = False) -> list[dict]:
    """``count`` invented products in the format Audible answers in (one page of a chart): the n-th comes out
    ``days[n % len(days)]`` days from the day of the tests (a negative number: that long ago). With ``newest_first``
    in the order of the day, the furthest first (the sort ``-ReleaseDate``)."""
    page = [audible_product(f"B0{prefix.upper()}{n:07d}", f"Buch {prefix} {n}", [f"Autor {n % 7}"], 400,
                            released=(NOW + timedelta(days=days[n % len(days)])).date().isoformat())
            for n in range(count)]
    return sorted(page, key=lambda p: p["release_date"], reverse=True) if newest_first else page


def test_the_newest_first_of_audible_is_the_list_of_what_comes_out(disc: Disc) -> None:
    disc.only("audible")
    with SessionLocal() as db:
        lists.fetch_unit(db, "audible", "de", "release")
    # Soon: what is after today, the nearest first; what is out, and the one with no day, is not on it.
    assert [t for _r, _s, t in entries("soon")] == ["Schwarzer Sand", "Das Echo am Ende der Strasse"]
    assert entries("soon")[1][0] == 2
    assert entries("new") == [], "the new ones are not taken from the newest first: that holds hundreds of one day"


def test_the_best_sellers_have_no_pre_orders_and_the_new_ones_are_the_best_sellers_of_the_last_weeks(
        disc: Disc) -> None:
    disc.only("audible")
    # Two whole pages of 50. Some products come out in the future (pre-orders), some came out in the last days or
    # weeks, the rest a year ago.
    days = [-3, 40, -365, 12, -45, -62, -400, 60, -50, 5]
    disc.fake.audible_chart["BestSellers"] = chart_page("a", 50, days)
    disc.fake.audible_pages[("BestSellers", 2)] = chart_page("b", 50, days)
    with SessionLocal() as db:
        lists.fetch_unit(db, "audible", "de", "best")
    assert disc.fake.count("api.audible.de") == lists.BEST_PAGES == 2, "one unit gives best and new"
    best = entries("best")
    assert len(best) == lists.LIST_SIZE and [r for r, _s, _t in best] == list(range(1, lists.LIST_SIZE + 1))
    assert max(d for d, in entries_released("best")) <= TODAY, "no pre-order is among the best sellers"
    # New: out since 18 August (eight weeks before the day of the tests), the newest first, never a day after today.
    begin = (NOW - timedelta(weeks=8)).date().isoformat()
    new = [d for d, in entries_released("new")]
    assert len(new) == 30 and new == sorted(new, reverse=True)
    assert all(begin <= day <= TODAY for day in new) and len(set(new)) == 3, "3, 45 and 50 days ago, not 62"


def test_the_unit_of_the_best_sellers_does_not_write_the_list_of_what_comes_out(disc: Disc) -> None:
    disc.only("audible")
    disc.fake.audible_chart["BestSellers"] = chart_page("a", 50, [-2, 20])
    with SessionLocal() as db:
        lists.fetch_unit(db, "audible", "de", "best")
    assert entries("soon") == []
    assert len(entries("best")) == 25


def test_the_pages_of_what_comes_out_are_read_until_they_reach_today(disc: Disc) -> None:
    disc.only("audible")
    # The first page ends in the past: that is the whole story, one request.
    disc.fake.audible_chart["-ReleaseDate"] = chart_page("c", 50, [30, 20, 10, 5, 1, -1], newest_first=True)
    with SessionLocal() as db:
        lists.fetch_unit(db, "audible", "de", "release")
    assert disc.fake.count("api.audible.de") == 1
    assert len(entries("soon")) == 42
    # The first page ends in the future: the second one is read as well.
    disc.fake.log.clear()
    disc.fake.audible_chart["-ReleaseDate"] = chart_page("d", 50, [30, 20, 10, 5], newest_first=True)
    disc.fake.audible_pages[("-ReleaseDate", 2)] = chart_page("e", 50, [3, 2, 1, 0, -1], newest_first=True)
    with SessionLocal() as db:
        lists.fetch_unit(db, "audible", "de", "release")
    assert disc.fake.count("api.audible.de") == 2
    days = [d for d, in entries_released("soon")]
    assert len(days) == lists.LIST_SIZE and days == sorted(days) and days[0] > TODAY, "the nearest first, none out"


def test_a_chart_gives_the_whole_page_of_fifty_products(disc: Disc) -> None:
    disc.only("audible")
    disc.fake.audible_chart["BestSellers"] = chart_page("a", 50, [-1])
    with SessionLocal() as db:
        assert len(audible.chart(db, "de", "best")) == 50


def test_a_page_of_a_chart_gives_all_fifty_products_and_a_search_twenty_five() -> None:
    page = {"products": [audible_product(f"B0LONG{n:04d}", f"Titel {n}", ["A Person"], 300) for n in range(50)]}
    assert len(audible.parse_products(page, "de", audible.PAGE)) == 50
    assert len(audible.parse_products(page, "de")) == audible.MAX_RESULTS == 25


def test_a_list_is_replaced_whole_and_a_failure_keeps_the_old_one(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        lists.fetch_unit(db, "apple", "de", "best")
    disc.fake.apple = {"feed": {"results": fixture("apple_chart.json")["feed"]["results"][:1]}}
    disc.days(1)
    with SessionLocal() as db:
        lists.fetch_unit(db, "apple", "de", "best")
    assert entries("best") == [(1, "apple", "Der Leuchtturmwaerter")]
    disc.fake.status[APPLE] = 500
    disc.days(1)
    with SessionLocal() as db:
        assert lists.fetch_unit(db, "apple", "de", "best") == "failed"
        log = db.get(ListFetch, ("apple", "de", "best"))
        assert log is not None and log.state == "failed" and log.error == "server"
    assert entries("best") == [(1, "apple", "Der Leuchtturmwaerter")], "what was kept stays"


def test_a_stop_in_the_middle_leaves_nothing_and_the_job_runs_again(disc: Disc) -> None:
    disc.only("apple")
    disc.fake.on_request = lambda _r: jobs.stopping.set()
    with SessionLocal() as db:
        lists.want(db, "de", ("best",))
    jobs.run_pending(10)
    assert entries("best") == [], "a unit that was stopped writes nothing"
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.kind == "lists"))
        assert job is not None and job.state == "queued" and job.attempts == 0, "no try is used up by a stop"
    jobs.stopping.clear()
    disc.fake.on_request = None
    jobs.run_pending(10)
    assert len(entries("best")) == 3


def test_a_job_that_was_running_when_the_server_stopped_is_taken_up_again(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        lists.want(db, "de", ("best",))
        job = db.scalar(select(Job).where(Job.kind == "lists"))
        assert job is not None
        job.state = "running"
        db.commit()
        jobs.recover(db)
    assert [j.state for j in waiting_jobs("lists")] == ["queued"]
    jobs.run_pending(10)
    assert len(entries("best")) == 3


# ---- When a list is fetched, and when nothing is asked -----------------------------------------------------------


def test_a_list_is_fetched_once_in_twelve_hours_and_only_when_somebody_looked(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        assert lists.enqueue_due(db) == 0, "nobody wants anything yet"
    assert disc.fetch("de", "best") == 1 + 2, "the chart, then the two covers"
    asked = disc.fake.count(APPLE)
    # Somebody looks again an hour later: nothing is due.
    disc.advance(3600)
    with SessionLocal() as db:
        lists.want(db, "de", ("best",))
        assert lists.enqueue_due(db) == 0
    assert disc.fake.count(APPLE) == asked
    # Thirteen hours after the fetch it is due again, and the loop queues it by itself.
    disc.advance(12 * 3600)
    with SessionLocal() as db:
        assert lists.enqueue_due(db) == 1
    jobs.run_pending(10)
    assert disc.fake.count(APPLE) == asked + 1


def test_a_list_nobody_looked_at_for_a_fortnight_is_left_alone(disc: Disc) -> None:
    disc.only("apple")
    disc.fetch("de", "best")
    disc.days(15)
    with SessionLocal() as db:
        assert lists.enqueue_due(db) == 0


def test_a_failed_list_is_tried_again_after_an_hour_not_before(disc: Disc) -> None:
    disc.only("apple")
    disc.fake.status[APPLE] = 500
    disc.fetch("de", "best")
    asked = disc.fake.count(APPLE)
    disc.advance(1800)
    with SessionLocal() as db:
        assert lists.enqueue_due(db) == 0
    disc.advance(3600)
    with SessionLocal() as db:
        assert lists.enqueue_due(db) == 1
    jobs.run_pending(10)
    assert disc.fake.count(APPLE) == asked + 1


def test_the_loop_queues_two_at_a_time_and_not_all_at_once(disc: Disc) -> None:
    disc.connect_hardcover()
    with SessionLocal() as db:
        lists.want(db, "de", ("best", "new", "trending"))
        db.query(Job).delete()
        db.commit()
        assert lists.enqueue_due(db) == lists.PER_LOOK == 2


def test_with_the_main_switch_off_nothing_is_queued_and_nothing_is_asked(disc: Disc) -> None:
    disc.settings(sources_enabled=False)
    disc.fetch("de")
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Job.id))) == 0
        assert lists.enqueue_due(db) == 0
    assert disc.fake.log == []


def test_a_job_queued_before_the_switch_went_off_asks_nothing(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        lists.want(db, "de", ("best",))
    disc.settings(sources_enabled=False)
    jobs.run_pending(10)
    assert disc.fake.log == [] and entries("best") == []
    with SessionLocal() as db:
        log = db.get(ListFetch, ("apple", "de", "best"))
        assert log is not None and log.fetched_at is None, "nothing was tried, so nothing is noted"


def test_a_source_that_is_off_is_not_asked_for_its_list_but_the_others_are(disc: Disc) -> None:
    disc.only("audible")
    disc.fetch("de", "best")
    assert disc.fake.count(APPLE) == 0
    assert {s for _r, s, _t in entries("best")} == {"audible"}


def test_the_trends_of_hardcover_are_asked_only_with_a_token(disc: Disc) -> None:
    disc.only("hardcover")
    disc.fetch("de", "trending")
    assert disc.fake.count("api.hardcover.app") == 0
    disc.connect_hardcover()
    disc.fetch("de", "trending")
    assert [t for _r, _s, t in entries("trending")][:2] == ["Die Krone der Weberinnen", "Glas und Asche"]
    with SessionLocal() as db:
        assert {e.market for e in db.scalars(select(ListEntry))} == {""}, "the trends have no market"


def test_a_market_that_is_none_is_not_asked_for(disc: Disc) -> None:
    with SessionLocal() as db:
        lists.want(db, "xx", ("best",))
        assert db.scalar(select(func.count(Job.id))) == 0
        for market in ("", "..", "de/../x"):
            with pytest.raises(jobs.GiveUp):
                lists.fetch_unit(db, "apple", market, "best")
    assert disc.fake.log == []


# ---- Covers ------------------------------------------------------------------------------------------------------


def test_a_cover_comes_from_the_host_of_the_source_and_is_kept_apart_from_the_covers_of_the_shelf(disc: Disc) -> None:
    disc.only("apple")
    disc.fetch("de", "best")
    with SessionLocal() as db:
        book = db.scalar(select(ForeignBook).where(ForeignBook.title == "Der Leuchtturmwaerter"))
        assert book is not None and book.cover and book.color.startswith("#")
        name = book.cover
    path = pics.path_of(name)
    assert path is not None and path.parent == pics.folder()
    # The sweep that follows a scan of a library takes away the covers nothing on a shelf uses; these are not those.
    covers.sweep([])
    assert pics.path_of(name) is not None
    # Only Apple's hosts were asked for pictures, the first time with the bigger size.
    assert any("320x480" in url for url in disc.fake.urls("mzstatic.com"))
    assert not any("evil" in host for host in disc.fake.hosts())


def test_a_cover_address_at_a_foreign_host_is_never_asked_for(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        candidate = Candidate(source="apple", key="1700000777", title="A Book", authors=["A Person"],
                              cover_url="https://evil.example.com/cover.jpg")
        row = foreign.upsert(db, candidate, "ebook")
        db.commit()
        assert foreign.fetch_cover(db, row) is False
    assert disc.fake.log == []


def test_a_cover_that_is_too_large_is_dropped_and_not_tried_again(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(source="apple", key="1700000888", title="Big", authors=["A Person"],
                                           cover_url="https://is1-ssl.mzstatic.com/x/100x100bb.jpg"), "ebook")
        foreign.enqueue_covers(db, [row])
        db.commit()
    disc.fake.bodies["is1-ssl.mzstatic.com"] = b"x" * (COVER_MAX_BYTES + 10)
    jobs.run_pending(10)
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.kind == "fcover"))
        assert job is not None and job.state == "failed" and job.attempts == 1 and job.error == "too_large"
        assert db.scalar(select(ForeignBook.cover).where(ForeignBook.title == "Big")) == ""


def test_a_cover_that_is_no_picture_is_dropped(disc: Disc) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(source="apple", key="1700000999", title="Plain", authors=["A Person"],
                                           cover_url="https://is1-ssl.mzstatic.com/x/100x100bb.jpg"), "ebook")
        db.commit()
        disc.fake.bodies["is1-ssl.mzstatic.com"] = b"<html>not a picture</html>" * 20
        assert foreign.fetch_cover(db, row) is False
        assert row.cover == ""


# ---- Forgetting --------------------------------------------------------------------------------------------------


def test_books_nothing_names_are_forgotten_with_their_covers_but_listed_and_wished_ones_stay(disc: Disc) -> None:
    disc.only("apple")
    disc.fetch("de", "best")
    person = make_account("reader")
    with SessionLocal() as db:
        old = foreign.upsert(db, Candidate(source="apple", key="1700000111", title="Old", authors=["A Person"]), "ebook")
        wished = foreign.upsert(db, Candidate(source="apple", key="1700000222", title="Wished", authors=["A Person"]),
                                "ebook")
        old.seen_at = wished.seen_at = NOW - timedelta(days=60)
        old.cover = pics.save(b"RIFF....WEBPold-cover-bytes")
        db.add(Wish(user_id=person.id, foreign_id=wished.id, title="Wished", author="A Person", added_at=NOW))
        for listed in db.scalars(select(ForeignBook).where(ForeignBook.title == "Orbit Null")):
            listed.seen_at = NOW - timedelta(days=60)
        db.commit()
        assert foreign.prune(db) == 1
        titles = {t for (t,) in db.execute(select(ForeignBook.title))}
        gone_cover = old.cover
    assert titles == {"Der Leuchtturmwaerter", "Die Salzwiese", "Orbit Null", "Wished"}
    assert gone_cover not in {p.name for p in pics.folder().iterdir()}, "its cover went with it"


def test_a_form_two_sources_know_is_one_form_with_what_either_said(disc: Disc) -> None:
    with SessionLocal() as db:
        first = foreign.upsert(db, Candidate(source="apple", key="1700000321", title="Same Book", authors=["A Person"],
                                             languages=["de"]), "ebook")
        second = foreign.upsert(db, Candidate(source="hardcover", key="9321", title="Same Book", authors=["A Person"],
                                              languages=["de"], pages=287), "ebook")
        db.commit()
        forms = foreign.forms_of([first, second])
    assert len(forms) == 1 and forms[0]["source"] == "apple" and forms[0]["pages"] == 287
