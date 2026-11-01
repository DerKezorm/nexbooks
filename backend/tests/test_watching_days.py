"""Watching an author over days: a book out on the day the watch was switched on is never wished, a wish follows the
day its book is announced for when the announcement moves, a second person of the library sees that a book is wished
already, and with the main switch of the automatic work off switching a watch on wishes nothing and says so. The
clock jumps; every book is invented."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import CatalogPerson, CatalogWork, ProcureWish
from app.services.procure import automation

from .library_helpers import join
from .watch_helpers import KEEPER, World


def rebuilt() -> None:
    """The catalogue counts as built again (a later look follows)."""
    with SessionLocal() as db:
        row = db.scalar(select(CatalogPerson).where(CatalogPerson.key == "edmund halloran"))
        assert row is not None
        row.built_at = max(row.built_at or clock.now(), clock.now()) + timedelta(seconds=1)
        db.commit()


def announce_keeper(day: str) -> None:
    """The German edition of the book to come is announced for another day now."""
    with SessionLocal() as db:
        work = next(w for w in db.scalars(select(CatalogWork)) if KEEPER in str(w.titles))
        titles = dict(work.titles)
        titles["de"] = {**titles["de"], "released": day, "year": int(day[:4])}
        work.titles = titles
        db.commit()
    rebuilt()


def set_wish(**values: Any) -> None:
    with SessionLocal() as db:
        for row in db.scalars(select(ProcureWish)):
            for name, value in values.items():
                setattr(row, name, value)
        db.commit()


def keeper_wish(world: World) -> ProcureWish:
    rows = [row for row in world.wishes() if row.kind == "ebook"]
    assert len(rows) == 1
    return rows[0]


# ---- A book out on the first day -----------------------------------------------------------------------------------


def test_a_book_out_on_the_day_the_watch_was_switched_on_is_never_wished_when_the_catalogue_learns_it_later(
        watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    before = {row.id for row in watchers.wishes()}
    # The catalogue learns of a book that came out on the very day of the first look (12 October 2026).
    watchers.new_work("Heute erschienen", language="de", released="2026-10-12", key="k_today")
    watchers.beat()
    assert {row.id for row in watchers.wishes()} == before
    # A book that came out after that day, learned later, is a new book (control).
    watchers.days(2)
    watchers.new_work("Danach erschienen", language="de", released="2026-10-13", key="k_after")
    watchers.beat()
    assert "Danach erschienen" in watchers.titles_of([row for row in watchers.wishes() if row.id not in before])


# ---- The day follows the announcement ------------------------------------------------------------------------------


def test_a_wish_follows_its_book_when_the_announcement_is_put_back(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    assert (keeper_wish(watchers).state, keeper_wish(watchers).release_date) == ("waiting", "2027-03-03")
    announce_keeper("2027-09-01")
    watchers.beat()
    assert (keeper_wish(watchers).state, keeper_wish(watchers).release_date) == ("waiting", "2027-09-01")


def test_a_wish_that_found_nothing_after_a_day_the_book_did_not_come_waits_for_the_new_day(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    set_wish(state="not_found", release_date="")
    announce_keeper("2028-01-10")
    watchers.beat()
    assert (keeper_wish(watchers).state, keeper_wish(watchers).release_date) == ("waiting", "2028-01-10")


def test_a_wish_brought_forward_to_a_day_that_is_here_is_due_and_a_wish_being_searched_is_left_alone(
        watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    announce_keeper("2026-10-12")
    watchers.beat()
    assert (keeper_wish(watchers).state, keeper_wish(watchers).release_date) == ("waiting", "2026-10-12")
    set_wish(state="searching")
    announce_keeper("2027-05-05")
    watchers.beat()
    assert (keeper_wish(watchers).state, keeper_wish(watchers).release_date) == ("searching", "2026-10-12")


# ---- A second person of the library -------------------------------------------------------------------------------


def test_the_second_watcher_of_a_library_sees_the_book_wished_already(watchers: World) -> None:
    token = watchers.built()
    join(watchers.library, watchers.carol.id, "acquire")
    watchers.watch(watchers.alice, token, kind="ebook")
    page = watchers.watch(watchers.carol, token, kind="ebook")
    line = next(item for item in page["announced"] if item["title"] == KEEPER)
    assert (line["state"], line["kinds"]) == ("covered", ["ebook"])
    # (alice's own page says wished, as before.)
    mine = watchers.client(watchers.alice).get(f"/api/v1/catalog/authors/{token}").json()["watch"]
    assert next(item for item in mine["announced"] if item["title"] == KEEPER)["state"] == "wished"


# ---- The main switch ------------------------------------------------------------------------------------------------


def test_with_the_main_switch_off_switching_a_watch_on_wishes_nothing_and_says_so(watchers: World) -> None:
    token = watchers.built()
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": False})
    answer = watchers.watch(watchers.alice, token, kind="ebook")
    assert answer["made"] == {"wished": 0, "listed": 0, "stopped": "off"}
    assert answer["paused"] is True
    assert watchers.wishes() == []
    assert watchers.beat() == {"looked": 0, "asked": 0}
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": True})
    watchers.beat()
    assert [(row.kind, row.release_date) for row in watchers.wishes()] == [("ebook", "2027-03-03")]
    assert watchers.client(watchers.alice).get(f"/api/v1/catalog/authors/{token}").json()["watch"]["paused"] is False


# ---- The strip of the watched authors --------------------------------------------------------------------------------


def test_the_strip_names_a_book_in_doubt_only_for_its_recording_and_leaves_out_one_in_doubt_for_every_kind(
        watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="both")

    def marks(*found: str, **more: Any) -> str | None:
        with SessionLocal() as db:
            work = next(w for w in db.scalars(select(CatalogWork)) if KEEPER in str(w.titles))
            titles = dict(work.titles)
            titles["de"] = {**titles["de"], "marks": list(found), **more}
            work.titles = titles
            db.commit()
        item = watchers.client(watchers.alice).get("/api/v1/watching").json()["items"][0]
        return item["next"]["title"] if item["next"] else None

    assert marks("audio:play") == KEEPER
    assert marks("audio:play", "text:part:1:2") is None
    # (Nor is a later edition of a book out in German: a record of a German edition of 2019.)
    assert marks(year=2019) is None


def test_a_wish_for_another_library_is_no_wish_for_the_second_watcher(watchers: World) -> None:
    """(carol's books would go into a library of her own: alice's wish in the first one does not fetch hers.)"""
    from app.services.oeuvre import watch as watching

    from .library_helpers import book, books_folder, make_library

    token = watchers.built()
    folder = books_folder("a2-carol")
    book(folder, "other.epub", title="Ein anderes Buch", authors=("Zed Other",), language="de", isbn="9783000000999")
    own = make_library(folder, "Carols", scan=True)
    join(own, watchers.carol.id, "acquire")
    watchers.watch(watchers.alice, token, kind="ebook")
    wish = keeper_wish(watchers)
    with SessionLocal() as db:
        assert watching._covered(db, watchers.carol.id, [wish.foreign_id]) == set()
        join(watchers.library, watchers.carol.id, "acquire")
    with SessionLocal() as db:
        # (With the first library hers too, it is the first by name: "Carols" before "First".)
        target = watching.search.targets(db, watchers.carol.id)["ebook"]
        found = watching._covered(db, watchers.carol.id, [wish.foreign_id])
    assert found == (set() if target.id == own else {(wish.foreign_id, "de", "ebook")})
