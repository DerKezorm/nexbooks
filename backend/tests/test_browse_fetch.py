"""The themed shelves of Browse, fetched: what the open sources are asked, how their answers become the sections
of a shelf (new, popular, award, classic), and the rules of the fetching (a day, an hour after a failure, the main switch,
the 24 hours of covers and descriptions).

Every book is invented; no test reaches a real service. The rule under test above all: **the shelves are full with the open
sources alone.** Audible is off and Hardcover is not connected in every test here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import clock
from app.db import SessionLocal
from app.models import ForeignBook, Job, ListEntry, ShelfEntry, ShelfFetch
from app.services import jobs
from app.services.browse import fetch, shelves
from app.services.discover import foreign

from .browse_helpers import (
    APPLE_NEW,
    APPLE_POPULAR,
    OL_AWARD_DE,
    OL_POPULAR_DE,
    Browse,
    stored,
    wall,
)
from .conftest import new_client
from .fake_sources import host_of

# ---- The open sources alone fill the shelves -----------------------------------------------------------------------


def test_the_open_sources_alone_fill_every_section_of_a_shelf(browse: Browse) -> None:
    assert stored("krimi", "popular", source="apple") == APPLE_POPULAR
    assert stored("krimi", "new", source="apple") == APPLE_NEW
    assert stored("krimi", "soon", source="apple") == ["Kommende Dinge"]
    assert stored("krimi", "popular", source="openlibrary") == OL_POPULAR_DE
    assert stored("krimi", "new", source="openlibrary") == ["Nebelläufer", "Die Salztöchter", "Acht Stunden bis zum Morgen"]
    assert stored("krimi", "award", source="openlibrary") == OL_AWARD_DE
    assert stored("krimi", "classic", source="openlibrary") == [
        "Der lange Heimweg", "Ein Fall von Salz", "Altes Geld tötet", "Das verschlossene Boot", "Gezeitentafel",
        "Der unmögliche Preis"]
    hosts = set(browse.fake.hosts())
    assert not [h for h in hosts if h.startswith("api.audible.") or h == "api.hardcover.app"], hosts
    assert {"itunes.apple.com", "openlibrary.org"} <= hosts


def test_every_shelf_of_the_table_is_asked_and_kept_for_the_market(browse: Browse) -> None:
    with SessionLocal() as db:
        rows = {(r.source, r.shelf): r for r in db.scalars(select(ShelfFetch).where(ShelfFetch.market == "de"))}
    assert set(rows) == set(fetch.units("de"))
    assert {shelf for _source, shelf in rows} == {shelf.key for shelf in shelves.SHELVES}
    # The fixtures hold crime novels only: Open Library has nothing for the other topics, and says so (empty, not failed).
    assert all(row.state in ("ok", "empty") for row in rows.values())
    # The chart of the fixtures answers every genre, and every book in it is filed under crime: only the shelf of crime
    # keeps them (a chart of one genre holds books of others, and they do not count for it).
    assert rows[("apple", "krimi")].state == "ok" and rows[("apple", "krimi")].count > 0
    assert all(row.state == "empty" for (source, shelf), row in rows.items() if source == "apple" and shelf != "krimi")
    assert rows[("openlibrary", "krimi")].state == "ok" and rows[("openlibrary", "fantasy")].state == "empty"


def test_the_questions_to_open_library_are_the_fixed_words_of_the_table(browse: Browse) -> None:
    """Each shelf asks four questions of Open Library (the books wanted of the last years, of the last two years, the ones
    with a prize, the old ones still read): the subjects are the table's, as whole subjects (their keys), and a
    language is asked for in its own three letters. A shelf of non-fiction asks no question for old books."""
    questions = [r.url.params["q"] for r in browse.fake.log if r.url.path == "/search.json"]
    assert questions
    for question in questions:
        assert question.endswith(" language:ger")
        assert question.startswith("(subject_key:")
        assert ' subject:"' not in question.split(") (", 1)[0], "a subject is asked as a whole, never as a phrase"
    krimi = [q for q in questions if q.startswith('(subject_key:"thriller" OR subject_key:"mystery"')]
    assert len(krimi) == 4
    assert sum('"Edgar Award"' in q for q in krimi) == 1
    assert sum("first_publish_year:[2017 TO 2026]" in q for q in krimi) == 1
    assert sum("first_publish_year:[2025 TO 2026]" in q for q in krimi) == 1
    assert sum("first_publish_year:[1800 TO 1975]" in q for q in krimi) == 1
    assert all(r.url.params["lang"] == "ger" and r.url.params["sort"] == "readinglog"
               for r in browse.fake.log if r.url.path == "/search.json")


def test_apple_is_asked_for_each_genre_of_the_shelf_in_its_country(browse: Browse) -> None:
    paths = [r.url.path for r in browse.fake.log if host_of(r) == "itunes.apple.com"]
    assert len(paths) == sum(len(s.apple) + len(s.apple_classic) for s in shelves.SHELVES)
    assert all(path.startswith("/de/rss/toppaidebooks/genre=") and path.endswith("/limit=100/json") for path in paths)
    assert sum("genre=9032/" in path for path in paths) == 1


# ---- What a section keeps and what it leaves out --------------------------------------------------------------------


def test_a_prize_the_book_was_too_old_to_win_is_not_named(browse: Browse) -> None:
    """"Der unmögliche Preis" (written in 1890) carries the subject Edgar Award; the prize was first given in 1946."""
    assert "Der unmögliche Preis" not in stored("krimi", "award")
    assert "Der unmögliche Preis" in stored("krimi", "classic"), "it is still an old book that is read"
    with SessionLocal() as db:
        awards = {e.award for e in db.scalars(select(ShelfEntry).where(ShelfEntry.section == "award"))}
    assert awards == {"Edgar Award"}


def test_a_book_of_open_library_without_a_picture_is_left_out(browse: Browse) -> None:
    for section in ("popular", "new", "award", "classic"):
        assert "Ohne Bild" not in stored("krimi", section)


def test_an_unknown_day_of_apple_is_no_day_and_a_foreign_picture_is_none(browse: Browse) -> None:
    with SessionLocal() as db:
        unknown = db.scalar(select(ForeignBook).where(ForeignBook.title == "Das Raetsel von Kalmar"))
        foreign_picture = db.scalar(select(ForeignBook).where(ForeignBook.title == "Fremdes Bild"))
    assert unknown is not None and unknown.released == ""
    assert foreign_picture is not None and foreign_picture.cover_url == ""


def test_the_title_and_the_picture_are_those_of_the_edition_in_the_language_of_the_market(browse: Browse) -> None:
    with SessionLocal() as db:
        german = db.scalar(select(ForeignBook).where(ForeignBook.ref == "openlibrary:OL1001W~de"))
    assert german is not None and german.title == "Der stille Pier" and german.language == "de"
    assert german.cover_url.endswith("/b/id/75001-L.jpg"), "the cover of the German edition, not the work's"
    assert german.data["pages"] == 312 and german.data["year"] == 2022
    browse.shelves("us")
    with SessionLocal() as db:
        english = db.scalar(select(ForeignBook).where(ForeignBook.ref == "openlibrary:OL1001W"))
        both = db.scalar(select(func.count()).select_from(ForeignBook).where(ForeignBook.ref.like("openlibrary:OL1001W%")))
    assert english is not None and english.title == "The Quiet Pier" and english.language == "en"
    assert both == 2, "two markets are two rows, so that the one never overwrites the title of the other"


def test_the_books_of_apple_say_no_language_and_carry_their_description(browse: Browse) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Nebelwacht"))
    assert row is not None and row.language == ""
    assert row.data["description"].startswith("Ein Kommissar am Deich")
    assert row.authors == ["Ilse Brandt"] and row.released == "2026-09-20"


# ---- When the sources are asked --------------------------------------------------------------------------------------


def test_a_part_of_a_shelf_is_fetched_again_after_a_day_and_not_before(browse: Browse) -> None:
    asked = len(browse.fake.log)
    assert browse.shelves("de") == 0, "everything was fetched a moment ago"
    assert len(browse.fake.log) == asked
    browse.advance(23 * 3600)
    assert browse.shelves("de") == 0
    browse.advance(2 * 3600)
    assert browse.shelves("de") == len(fetch.units("de"))


def test_a_failed_part_is_asked_again_after_an_hour_and_keeps_its_books_meanwhile(browse: Browse) -> None:
    before = stored("krimi", "popular")
    browse.advance(25 * 3600)
    browse.fake.status["openlibrary.org"] = 503
    with SessionLocal() as db:
        assert fetch.fetch_unit(db, "openlibrary", "de", "krimi") == "failed"
        row = db.get(ShelfFetch, ("openlibrary", "de", "krimi"))
        assert row is not None and row.state == "failed" and row.error == "server"
        assert not fetch.is_due(row, clock.now())
    assert stored("krimi", "popular") == before, "a unit that fails keeps what it had"
    del browse.fake.status["openlibrary.org"]
    browse.advance(30 * 60)
    with SessionLocal() as db:
        assert not fetch.is_due(db.get(ShelfFetch, ("openlibrary", "de", "krimi")), clock.now()), "not before an hour"
    browse.advance(40 * 60)
    with SessionLocal() as db:
        assert fetch.is_due(db.get(ShelfFetch, ("openlibrary", "de", "krimi")), clock.now())
        assert fetch.fetch_unit(db, "openlibrary", "de", "krimi") == "ok"
        assert db.get(ShelfFetch, ("openlibrary", "de", "krimi")).state == "ok"  # type: ignore[union-attr]


def test_a_source_that_rests_after_an_error_is_not_asked_by_the_units_that_follow(browse: Browse) -> None:
    """After a failure the source is left alone for a while: the units that come next are not asked and not marked as failed
    (nothing was sent), so that they are asked again at the next look."""
    browse.advance(25 * 3600)
    browse.fake.status["openlibrary.org"] = 503
    before = browse.fake.count("openlibrary.org") - browse.fake.count("covers.openlibrary.org")
    browse.shelves("de")
    # The searches of the shelves; the covers come from another host of the source, later, once it has rested.
    asked_ol = browse.fake.count("openlibrary.org") - browse.fake.count("covers.openlibrary.org") - before
    with SessionLocal() as db:
        failed = db.scalars(select(ShelfFetch).where(ShelfFetch.source == "openlibrary", ShelfFetch.state == "failed")).all()
    assert len(failed) == 1 and asked_ol <= 3, "one try and the rest left alone"


def test_a_part_of_a_shelf_is_replaced_as_a_whole(browse: Browse) -> None:
    browse.fake.ol_shelf = [d for d in browse.fake.ol_shelf if d["title"] in ("The Ninth Bell", "Tide Table")]
    browse.advance(25 * 3600)
    browse.shelves("de")
    assert stored("krimi", "popular", source="openlibrary") == ["Die neunte Glocke"]
    assert stored("krimi", "award", source="openlibrary") == []
    assert stored("krimi", "classic", source="openlibrary") == ["Gezeitentafel"]


def test_with_the_main_switch_off_nothing_is_queued_and_nothing_is_asked(browse: Browse) -> None:
    browse.settings(sources_enabled=False)
    browse.advance(25 * 3600)
    asked = len(browse.fake.log)
    with SessionLocal() as db:
        assert fetch.want(db, "de") == 0
        assert fetch.enqueue_due(db) == 0
        waiting = db.scalar(select(func.count()).select_from(Job).where(Job.kind == "shelves", Job.state == "queued"))
    assert waiting == 0 and len(browse.fake.log) == asked
    assert stored("krimi", "popular"), "what was kept is still there"


def test_a_job_that_was_queued_before_the_switch_went_off_asks_nothing(browse: Browse) -> None:
    browse.advance(25 * 3600)
    with SessionLocal() as db:
        assert fetch.want(db, "de") > 0
    browse.settings(sources_enabled=False)
    asked = len(browse.fake.log)
    jobs.run_pending(1000)
    assert len(browse.fake.log) == asked


def test_a_source_that_is_off_is_not_asked_and_the_other_still_fills_the_shelf(browse: Browse) -> None:
    browse.settings(source_apple=False)
    browse.advance(25 * 3600)
    asked_apple = browse.fake.count("itunes.apple.com")
    browse.shelves("de")
    assert browse.fake.count("itunes.apple.com") == asked_apple
    assert stored("krimi", "award"), "Open Library alone gives the shelf its prizes"


def test_one_job_waits_for_a_unit_however_often_it_is_looked_at(browse: Browse) -> None:
    browse.advance(25 * 3600)
    with SessionLocal() as db:
        first = fetch.want(db, "de")
        again = fetch.want(db, "de")
        waiting = db.scalar(select(func.count()).select_from(Job).where(Job.kind == "shelves", Job.state == "queued"))
    assert first == len(fetch.units("de")) and again == 0 and waiting == first


def test_the_loop_keeps_the_markets_somebody_looked_at_in_the_last_fortnight(browse: Browse) -> None:
    browse.advance(25 * 3600)
    with SessionLocal() as db:
        queued = fetch.enqueue_due(db)
    assert queued == fetch.PER_LOOK, "a few at a time"
    browse.days(15)
    with SessionLocal() as db:
        db.query(Job).delete()
        db.commit()
        assert fetch.enqueue_due(db) == 0, "a market nobody has looked at for fifteen days is let go"


def test_a_market_or_a_unit_that_is_none_is_not_fetched(browse: Browse) -> None:
    with SessionLocal() as db:
        assert fetch.want(db, "zz") == 0
        with pytest.raises(jobs.GiveUp):
            fetch.fetch_unit(db, "openlibrary", "de", "nosuchshelf")
        with pytest.raises(jobs.GiveUp):
            fetch.fetch_unit(db, "dnb", "de", "krimi")
        with pytest.raises(jobs.GiveUp):
            fetch.fetch_unit(db, "apple", "zz", "krimi")


# ---- The 24 hours of covers and words ---------------------------------------------------------------------------------


def test_the_shelves_queue_the_covers_of_their_first_books_only_and_a_look_queues_none(browse: Browse) -> None:
    """Fetching the shelves queues the covers of the first books of each section, not of all (a market gave 1,513 jobs for
    covers a day, in front of the scan), in the lane of Discover; a look at the wall queues nothing: the covers of the
    rest are fetched when their pictures are asked for."""
    with SessionLocal() as db:
        db.query(Job).filter(Job.kind == "fcover").delete()
        db.commit()
    browse.advance(25 * 3600)
    browse.shelves("de")
    with SessionLocal() as db:
        wanted = set(db.scalars(select(Job.ref).where(Job.kind == "fcover")))
        books = db.scalar(select(func.count(ForeignBook.id)).where(ForeignBook.cover_url != ""))
        front = set(db.scalars(select(ShelfEntry.foreign_id).where(ShelfEntry.rank <= foreign.FRONT_PER_SECTION)))
        front |= set(db.scalars(select(ListEntry.foreign_id).where(ListEntry.rank <= foreign.FRONT_PER_LIST)))
    assert jobs.LANES["discover"] == ("shelves", "fcover"), "neither waits in front of a scan"
    assert wanted and wanted <= front, "only the first books of a section or a list"
    print(f"MEASURE shelves: books_with_address={books} cover_jobs_queued={len(wanted)}")
    with SessionLocal() as db:
        db.query(Job).delete()
        db.commit()
    with new_client(browse.carol) as carol:
        wall(carol)
    with SessionLocal() as db:
        assert not db.scalar(select(func.count()).select_from(Job).where(Job.kind == "fcover")), "a look queues none"


def test_the_shelves_use_half_of_a_source_s_day_and_leave_the_rest(browse: Browse) -> None:
    """With many markets the shelves would use up Apple's day (2,000), which the best sellers of Discover need too: past
    half of it they queue nothing more, neither a part of a shelf nor a cover."""
    from app.models import SourceState
    from app.services.sources import config

    browse.advance(25 * 3600)
    with SessionLocal() as db:
        today = clock.now().strftime("%Y-%m-%d")
        for source in ("apple", "openlibrary"):
            state = db.get(SourceState, source)
            assert state is not None
            state.day, state.day_count = today, int(config.LIMITS[source].per_day * fetch.BUDGET_SHARE)
        db.commit()
        assert fetch.want(db, "us") == 0 and fetch.enqueue_due(db) == 0
        db.query(Job).delete()
        db.commit()
    with new_client(browse.carol) as carol:
        wall(carol)
    with SessionLocal() as db:
        assert not db.scalar(select(func.count()).select_from(Job).where(Job.kind.in_(("fcover", "shelves"))))
        state = db.get(SourceState, "apple")
        assert state is not None
        state.day_count -= 1
        db.commit()
        assert fetch.want(db, "us") > 0, "below half of the day it goes on"


def test_covers_and_descriptions_of_the_shelves_are_gone_after_a_day_and_the_facts_stay(browse: Browse) -> None:
    with new_client(browse.carol) as carol:
        wall(carol)
    jobs.run_pending(1000)
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Nebelwacht"))
        assert row is not None and row.cover and row.data["description"], "the cover is fetched when it is shown"
    browse.advance(25 * 3600)
    with SessionLocal() as db:
        foreign.expire_pictures(db)
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Nebelwacht"))
        assert row is not None and row.cover == "" and row.color == ""
        assert "description" not in row.data and "text_at" not in row.data
        assert row.title == "Nebelwacht" and row.authors == ["Ilse Brandt"] and row.released == "2026-09-20"
        kept = db.scalar(select(func.count()).select_from(ShelfEntry).where(ShelfEntry.shelf == "krimi"))
    assert kept, "the place on the shelf is a fact and stays"


def test_the_books_of_a_shelf_are_not_forgotten_while_they_stand_on_it(browse: Browse) -> None:
    browse.days(40)
    with SessionLocal() as db:
        foreign.prune(db)
        row = db.scalar(select(ForeignBook).where(ForeignBook.title == "Nebelwacht"))
    assert row is not None, "a book that stands on a shelf is named; one nobody names for a month goes"


def test_a_book_nothing_names_any_more_is_forgotten_after_a_month(browse: Browse) -> None:
    with SessionLocal() as db:
        db.query(ShelfEntry).delete()
        db.commit()
    browse.days(40)
    with SessionLocal() as db:
        assert foreign.prune(db) > 0
        assert db.scalar(select(ForeignBook).where(ForeignBook.title == "Nebelwacht")) is None
