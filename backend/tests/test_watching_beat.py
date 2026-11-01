"""The beat of watching an author: what a look after a new build wishes (a book announced since, a book out since
the last look, never an old one and never one that was out when the watch was switched on), when the catalogue of a watched
author is asked for again (a day old, the lane free, the sources with room, people in turn, nothing for an author nobody
watches, nothing with the main switch off), and the brakes on the wishes of a day. The clock jumps; every book is invented."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, AuthorWatch, CatalogPerson, Job, SourceState
from app.services.oeuvre import store
from app.services.oeuvre import watch as watching
from app.services.procure import automation, planner, wishes

from .conftest import make_account
from .watch_helpers import ORIGINAL, World


def looked(world: World) -> int:
    return world.beat()["looked"]


def test_a_book_announced_after_the_switch_on_is_wished_by_the_beat(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01")
    assert looked(watchers) == 1
    assert [(row.kind, row.state, row.release_date) for row in watchers.wishes()] == [
        ("ebook", "waiting", "2027-03-03"), ("ebook", "waiting", "2027-06-01")]
    # Once per build: the next beat finds nothing to look at.
    assert looked(watchers) == 0


def test_a_book_out_since_the_last_look_is_wished_and_an_old_one_never(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert watchers.watch_row(watchers.alice).since == "2026-10-12"
    watchers.days(2)
    # A new build brings three books: one out yesterday, one out long ago, one out the day before the switch-on.
    watchers.new_work("Gestern erschienen", language="de", released="2026-10-13", key="fresh")
    watchers.new_work("Aus alter Zeit", language="de", released="2015-05-05", key="old")
    watchers.new_work("Kurz davor", language="de", released="2026-10-11", key="before")
    assert looked(watchers) == 1
    assert watchers.titles_of(watchers.wishes()) == sorted([ORIGINAL, "Gestern erschienen"])
    fresh = next(row for row in watchers.wishes() if row.release_date == "")
    # A book that is out is searched at once, as a background search.
    assert fresh.state in ("searching", "not_found") and fresh.kind == "ebook"
    assert watchers.watch_row(watchers.alice).since == "2026-10-14"


def test_a_book_out_the_day_the_watch_was_switched_on_is_never_wished_later(watchers: World) -> None:
    watchers.built()
    watchers.new_work("Heute erschienen", language="de", released="2026-10-12", key="today")
    with SessionLocal() as db:
        token = db.scalar(select(CatalogPerson.token))
    assert token is not None
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert watchers.titles_of(watchers.wishes()) == [ORIGINAL]
    # A later build brings two more books; the first is known to the last look, so it stays unwished, and so does one
    # that came out on the day the watch was switched on (it was out then); one out the day after is new.
    watchers.days(1)
    watchers.new_work("Am selben Tag erschienen", language="de", released="2026-10-12", key="same")
    watchers.new_work("Morgen erschienen", language="de", released="2026-10-13", key="later")
    assert looked(watchers) == 1
    assert watchers.titles_of(watchers.wishes()) == sorted([ORIGINAL, "Morgen erschienen"])


def _rekey(old: str, new: str) -> None:
    """The same work under another key (a new build of the catalogue that put its sources together another way)."""
    from app.models import CatalogWork

    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == old))
        assert work is not None
        work.key = new
        db.commit()


def test_a_work_that_comes_back_under_another_key_is_still_the_same_book(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Heute erschienen", language="de", released="2026-10-12", key="first")
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    [wish] = watchers.wishes()
    # Taken away by the person; the build gives the work another key.
    assert watchers.client(watchers.alice).delete(f"/api/v1/procure/wishes/{wish.id}").status_code == 204
    _rekey("first", "second")
    watchers.new_work("Anderes Buch", language="de", released="2027-09-01", key="other")
    assert looked(watchers) == 1
    # A wish taken away stays away, and what was out when the watch was switched on is still not wished.
    assert watchers.titles_of(watchers.wishes()) == ["Anderes Buch"]


def test_a_wish_taken_away_stays_away_under_a_new_key_of_the_work(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    [wish] = watchers.wishes()
    assert watchers.client(watchers.alice).delete(f"/api/v1/procure/wishes/{wish.id}").status_code == 204
    with SessionLocal() as db:
        from app.models import CatalogWork

        for work in db.scalars(select(CatalogWork)):
            if "Zimmer" in str(work.titles):
                work.key = "rekeyed"
        db.commit()
    watchers.new_work("Anderes Buch", language="de", released="2027-09-01", key="other")
    assert looked(watchers) == 1
    assert watchers.titles_of(watchers.wishes()) == ["Anderes Buch"]


def test_the_other_language_is_not_wished_when_the_first_is_out(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    assert [(row.kind, row.language) for row in watchers.wishes()] == [("ebook", "de")]
    # The German edition comes out (it is no longer announced), the English one is still to come.
    with SessionLocal() as db:
        from app.models import CatalogWork

        for work in db.scalars(select(CatalogWork)):
            if "Zimmer" in str(work.titles):
                titles = dict(work.titles)
                titles["de"] = {**titles["de"], "released": "2026-10-01", "year": 2026}
                work.titles = titles
        db.commit()
    watchers.new_work("Anderes Buch", language="de", released="2027-09-01", key="other")
    assert looked(watchers) == 1
    assert sorted((row.language, row.kind) for row in watchers.wishes()) == [("de", "ebook"), ("de", "ebook")]
    assert watchers.titles_of(watchers.wishes()) == sorted([ORIGINAL, "Anderes Buch"])


def test_changing_the_watch_wishes_what_is_announced_and_leaves_what_is_out_to_the_beat(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    watchers.days(1)
    watchers.new_work("Heute erschienen", language="de", released="2026-10-13", key="out")
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01", key="soon")
    # A change of the watch looks at what is announced only; the book that came out waits for the beat.
    again = watchers.watch(watchers.alice, token, kind="ebook", language="de", more=True)
    assert again["made"]["wished"] == 1
    assert watchers.titles_of(watchers.wishes()) == sorted([ORIGINAL, "Das späte Licht"])
    assert looked(watchers) == 1
    assert watchers.titles_of(watchers.wishes()) == sorted([ORIGINAL, "Das späte Licht", "Heute erschienen"])


def test_a_book_the_person_has_by_the_time_of_the_look_is_not_wished(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    from .library_helpers import book, books_folder, scan
    from .watch_helpers import AUTHOR

    watchers.new_work("Schon da", language="de", released="2026-10-12", key="have")
    book(books_folder("a2-first"), "schon.epub", title="Schon da", authors=(AUTHOR,), language="de")
    scan(watchers.library)
    with SessionLocal() as db:
        person = db.scalar(select(CatalogPerson))
        assert person is not None
        person.built_at = max(person.built_at, clock.now()) + timedelta(seconds=2)
        db.commit()
    assert looked(watchers) == 1
    assert "Schon da" not in watchers.titles_of(watchers.wishes())


def test_the_catalogue_of_a_watched_author_is_asked_for_when_it_is_a_day_old_and_not_before(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    asked = len(watchers.sources.fake.log)
    assert watchers.beat() == {"looked": 0, "asked": 0} and watchers.sources.waiting() == 0
    watchers.days(0.9)
    assert watchers.beat()["asked"] == 0
    watchers.days(0.2)
    assert watchers.beat()["asked"] == 1 and watchers.sources.waiting() == 1
    # Asked for, not asked of the sources: the build is the lane's.
    assert len(watchers.sources.fake.log) == asked
    # The lane has it: nothing is asked for twice.
    assert watchers.beat()["asked"] == 0
    watchers.sources.build()
    assert len(watchers.sources.fake.log) > asked
    assert watchers.beat()["looked"] == 1


def test_nothing_is_asked_for_an_author_nobody_watches(watchers: World) -> None:
    watchers.built()
    asked = len(watchers.sources.fake.log)
    watchers.days(3)
    assert watchers.beat() == {"looked": 0, "asked": 0}
    assert watchers.sources.waiting() == 0 and len(watchers.sources.fake.log) == asked


def test_with_the_main_switch_off_the_beat_does_nothing(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": False})
    watchers.days(3)
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01")
    assert watchers.beat() == {"looked": 0, "asked": 0}
    assert watchers.sources.waiting() == 0 and len(watchers.wishes()) == 1
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": True})
    assert watchers.beat()["looked"] == 1


def test_the_lane_of_the_catalogues_must_be_free(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    watchers.days(1.5)
    # Somebody looks at the page of another author: its build is in the lane.
    with SessionLocal() as db:
        other = store.for_name(db, "Marit Lindqvist")
        assert other is not None
        store.request(db, other)
    assert watchers.sources.waiting() == 1
    assert watchers.beat()["asked"] == 0
    watchers.sources.build()
    assert watchers.beat()["asked"] == 1


def test_the_sources_must_have_room(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    watchers.days(1.5)
    with SessionLocal() as db:
        row = db.get(SourceState, "dnb")
        assert row is not None
        row.day, row.day_count = clock.now().strftime("%Y-%m-%d"), 2000
        db.commit()
    assert watchers.beat()["asked"] == 0
    with SessionLocal() as db:
        row = db.get(SourceState, "dnb")
        assert row is not None
        row.day_count = 100
        db.commit()
    assert watchers.beat()["asked"] == 1


def test_a_build_that_did_not_come_is_not_asked_for_every_few_seconds(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    watchers.days(1.5)
    assert watchers.beat()["asked"] == 1
    with SessionLocal() as db:
        db.query(Job).delete()
        db.commit()
    assert watchers.beat()["asked"] == 0
    watchers.days(1 / 24 + 0.01)
    assert watchers.beat()["asked"] == 1


def _author(name: str) -> int:
    with SessionLocal() as db:
        row = store.for_name(db, name)
        assert row is not None
        db.commit()
        return row.id


def _queued_authors() -> list[int]:
    with SessionLocal() as db:
        return [int(job.ref) for job in db.scalars(select(Job).where(Job.kind == "catalog", Job.state == "queued"))]


def test_people_take_turns(watchers: World) -> None:
    """Alice watches three authors, bob one: after alice's first, bob's comes, then alice's second (bob's catalogue is due
    again an hour later and it is his turn again: nobody waits behind a person with many authors)."""
    first, second, third, fourth = (_author(name) for name in ("Ada Brandt", "Bo Carlsen", "Cy Dahl", "Di Ekberg"))
    with SessionLocal() as db:
        moment = clock.now()
        for ident in (first, second, third):
            db.add(AuthorWatch(user_id=watchers.alice.id, catalog_person_id=ident, kind="both", language="de",
                               more=False, created_at=moment, updated_at=moment))
        db.add(AuthorWatch(user_id=watchers.bob.id, catalog_person_id=fourth, kind="both", language="de", more=False,
                           created_at=moment, updated_at=moment))
        db.commit()
    order: list[int] = []
    for _ in range(3):
        assert watchers.beat()["asked"] == 1
        [ident] = _queued_authors()
        order.append(ident)
        with SessionLocal() as db:
            db.query(Job).delete()
            db.commit()
        watchers.days(1 / 24 + 0.01)
    assert order == [first, fourth, second]


def test_a_blocked_person_is_not_looked_at_nor_asked_for(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    with SessionLocal() as db:
        alice = db.get(Account, watchers.alice.id)
        assert alice is not None
        alice.blocked_at = clock.now()
        db.commit()
    watchers.days(2)
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01")
    assert watchers.beat() == {"looked": 0, "asked": 0}


def test_a_look_that_breaks_is_logged_and_made_again_later(watchers: World, monkeypatch: pytest.MonkeyPatch,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01")

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the look broke")

    real = watching.run
    monkeypatch.setattr(watching, "run", broken)
    with caplog.at_level(logging.ERROR):
        assert looked(watchers) == 1
    assert "Looking at the catalogue of a watched author failed" in caplog.text
    assert watchers.watch_row(watchers.alice).retry_at is not None
    # It waits an hour, then looks again (and this time it goes through).
    monkeypatch.setattr(watching, "run", real)
    assert looked(watchers) == 0
    watchers.days(1 / 24 + 0.01)
    assert looked(watchers) == 1
    assert "Das späte Licht" in watchers.titles_of(watchers.wishes())


def test_the_day_of_wishes_is_shared_with_the_person_and_the_rest_follows_the_next_day(
        watchers: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "wishes_per_day", 4)
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert len(watchers.wishes()) == 1
    for number in range(3):
        watchers.new_work(f"Buch {number}", language="de", released=f"2027-0{number + 4}-01", key=f"b{number}")
    # Half of four wishes a day is two; one was made already.
    assert looked(watchers) == 1
    assert len(watchers.wishes()) == 2
    row = watchers.watch_row(watchers.alice)
    assert row.retry_at is not None and row.retry_at > clock.now()
    # The rest of her day is hers by hand.
    alice = watchers.client(watchers.alice)
    page = alice.get(f"/api/v1/catalog/authors/{token}").json()
    keeper = next(w for w in page["works"] if w["key"] == "b2")
    made = alice.post("/api/v1/procure/wishes", json={"items": [{"book": keeper["token"]}], "kinds": ["ebook"],
                                                       "language": "de"})
    assert made.status_code == 201
    # Nothing more today, not even when the beat comes again; the next day it goes on.
    assert looked(watchers) == 0
    watchers.days(1.1)
    looked(watchers)
    assert {"Buch 0", "Buch 1"} <= set(watchers.titles_of(watchers.wishes()))


def test_the_operator_has_no_share_to_keep(watchers: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "wishes_per_day", 2)
    operator = make_account("boss", "operator")
    from .library_helpers import join

    join(watchers.library, operator.id, "acquire")
    token = watchers.built()
    for number in range(3):
        watchers.new_work(f"Buch {number}", language="de", released=f"2027-0{number + 4}-01", key=f"b{number}")
    made = watchers.watch(operator, token, kind="ebook", language="de")
    assert made["made"]["wished"] == 4 and made["made"]["stopped"] == ""


def test_many_books_at_once_are_wished_a_few_at_a_time(watchers: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watching, "MAX_PER_LOOK", 2)
    token = watchers.built()
    for number in range(3):
        watchers.new_work(f"Buch {number}", language="de", released=f"2027-0{number + 4}-01", key=f"b{number}")
    made = watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert made["made"] == {"wished": 2, "listed": 0, "stopped": "many"}
    assert watchers.watch_row(watchers.alice).primed_at is None
    assert looked(watchers) == 0
    watchers.sources.advance(61)
    assert looked(watchers) == 1
    assert len(watchers.wishes()) == 2 + 2


def test_a_look_for_a_book_that_is_out_is_a_search_of_the_background(watchers: World,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    seen: list[Any] = []
    real = wishes.begin

    def spy(db: Any, row: Any, background: Any = None) -> None:
        seen.append(background)
        real(db, row, background)

    monkeypatch.setattr(wishes, "begin", spy)
    watchers.days(1)
    watchers.new_work("Heute erschienen", language="de", released="2026-10-13", key="out")
    assert looked(watchers) == 1
    assert seen == [True]


def test_the_planner_runs_the_beat_and_a_broken_beat_does_not_stop_it(watchers: World,
                                                                     monkeypatch: pytest.MonkeyPatch,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    calls: list[int] = []

    def beat(_db: Any) -> dict[str, int]:
        calls.append(1)
        raise RuntimeError("no")

    monkeypatch.setattr(planner.watching, "tick", beat)
    planner.reset()
    with caplog.at_level(logging.ERROR):
        assert planner.tick() is not None
    assert calls == [1] and "Watching the authors failed" in caplog.text


def test_an_author_somebody_watches_is_not_forgotten(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.bob, token, kind="both", language="de")
    other = _author("Unwatched Person")
    # An author somebody watches, with nothing wished for them yet, is kept too (the watch alone is reason enough).
    quiet = _author("Quiet Name")
    with SessionLocal() as db:
        moment = clock.now()
        db.add(AuthorWatch(user_id=watchers.carol.id, catalog_person_id=quiet, kind="both", language="de", more=False,
                           created_at=moment, updated_at=moment))
        db.commit()
    with SessionLocal() as db:
        for row in db.scalars(select(CatalogPerson)):
            row.person_id = None
            row.wanted_at = clock.now() - timedelta(days=200)
            row.created_at = clock.now() - timedelta(days=200)
        db.commit()
    with SessionLocal() as db:
        assert store.prune(db) == 1
        assert db.get(CatalogPerson, other) is None
        assert db.get(CatalogPerson, quiet) is not None
        assert db.scalar(select(CatalogPerson).where(CatalogPerson.token == token)) is not None
    # After the watch is switched off, the entry it put on the wish list still names the author: it stays.
    assert watchers.client(watchers.bob).delete(f"/api/v1/watching/authors/{token}").status_code == 204
    with SessionLocal() as db:
        assert store.prune(db) == 0
        assert db.scalar(select(CatalogPerson).where(CatalogPerson.token == token)) is not None
    # Taken away with it, nothing names the author: it goes at its time.
    watchers.watch(watchers.bob, token, kind="both", language="de")
    assert watchers.client(watchers.bob).delete(f"/api/v1/watching/authors/{token}",
                                                params={"wishes": "remove"}).status_code == 204
    with SessionLocal() as db:
        for row in db.scalars(select(CatalogPerson)):
            row.wanted_at = clock.now() - timedelta(days=200)
        db.commit()
    with SessionLocal() as db:
        assert store.prune(db) == 1
