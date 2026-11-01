"""Watching an author: the one language it wishes in (the first of the languages books are fetched in, or one of them
the person chose), waiting for that language when a book is announced in another one only, what is no new book of its
own (an adaptation, a study guide, a part, a box, a new edition of an older work: onto the wish list, never a wish), a
year without a day, one wish per book and kind whatever its language, and the wishes of two people of one library. The
clock jumps; every book is invented."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.errors import error
from app.models import CatalogPerson, CatalogWork, LibraryMember, WatchItem
from app.services.oeuvre import store
from app.services.oeuvre import watch as watching
from app.services.procure import formats, wishes

from .conftest import make_account
from .watch_helpers import AUTHOR, KEEPER, ORIGINAL, World


def fetch_in(*languages: str, original: bool = True) -> None:
    """The languages books are fetched in (settings, "Sprachen"), in this order."""
    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "languages": list(languages), "original": original})


def edit_keeper(change: Any) -> None:
    with SessionLocal() as db:
        work = next(w for w in db.scalars(select(CatalogWork)) if ORIGINAL in str(w.titles))
        titles = dict(work.titles)
        change(titles)
        work.titles = titles
        db.commit()


def work_of(key: str) -> CatalogWork:
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == key))
        assert work is not None
        db.expunge(work)
        return work


def page(world: World, account: Any, token: str) -> dict[str, Any]:
    return world.client(account).get(f"/api/v1/catalog/authors/{token}").json()["watch"]  # type: ignore[no-any-return]


# ---- One language ------------------------------------------------------------------------------------------------


def test_a_watch_wishes_in_the_first_language_books_are_fetched_in_and_never_in_another(watchers: World) -> None:
    token = watchers.built()
    made = watchers.watch(watchers.alice, token, kind="both")
    # German comes first: the book announced for March in German is wished, the English edition of November is not.
    assert {(row.kind, row.language, row.release_date) for row in watchers.wishes()} == {
        ("ebook", "de", "2027-03-03"), ("audio", "de", "2027-03-03")}
    assert made["target"] == "de" and made["languages"] == ["de", "en"]
    watchers.watch(watchers.alice, token, kind="ebook")
    assert len(watchers.wishes()) == 2


def test_the_order_of_the_operator_decides_and_the_account_language_does_not(watchers: World) -> None:
    token = watchers.built()
    fetch_in("en", "de")
    made = watchers.watch(watchers.carol, token, kind="ebook")
    assert made["target"] == "en" and made["languages"] == ["en", "de"]
    # "Originalsprache" first: the language the work first came out in (English here).
    fetch_in("original", "de")
    assert watchers.watch(watchers.alice, token, kind="ebook")["target"] == "en"
    assert [(row.language, row.release_date) for row in watchers.wishes()] == [("en", "2026-11-15")]


def test_a_language_books_are_not_fetched_in_is_refused_and_never_turned_into_any_language(watchers: World) -> None:
    token = watchers.built()
    alice = watchers.client(watchers.alice)
    for language in ("da", "fr", "both"):
        refused = alice.put(f"/api/v1/watching/authors/{token}", json={"kind": "ebook", "language": language})
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "language_not_accepted"
    watchers.watch(watchers.alice, token, kind="ebook")
    watchers.new_work("Det sidste lys", language="da", released="2027-06-01", key="dk")
    watchers.beat()
    assert watchers.wishes() and all(row.language == "de" for row in watchers.wishes())


def test_a_language_no_longer_fetched_in_leaves_the_watch_and_it_wishes_in_the_first(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook", language="en")
    assert [row.language for row in watchers.wishes()] == ["en"]
    fetch_in("de", original=False)
    shown = page(watchers, watchers.alice, token)
    assert shown["language"] == "en" and shown["language_off"] is True and shown["target"] == "de"
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01", key="late")
    watchers.beat()
    assert sorted(row.language for row in watchers.wishes()) == ["de", "en"]


def test_the_catalogue_is_built_for_the_languages_books_are_fetched_in_before_those_of_the_people(
        watchers: World) -> None:
    fetch_in("fr", "it", "es", "nl")
    with SessionLocal() as db:
        row = db.scalar(select(CatalogPerson))
        if row is None:
            row = CatalogPerson(token="t-lang", name="Ida Probe", key="ida probe", orig_language="sv")
            db.add(row)
            db.flush()
        found = store.languages_for(db, row)
        db.rollback()
    # Everybody here reads German, but books are fetched in four other languages: those come first and are not cut.
    assert found[:4] == ["fr", "it", "es", "nl"]


# ---- Waiting for the first language --------------------------------------------------------------------------------


def test_a_book_announced_in_another_language_only_waits_on_the_wish_list_until_it_is_announced_in_german(
        watchers: World) -> None:
    token = watchers.built()
    edit_keeper(lambda titles: titles.pop("de"))
    made = watchers.watch(watchers.alice, token, kind="both")
    assert made["made"] == {"wished": 0, "listed": 1, "stopped": ""} and watchers.wishes() == []
    assert watchers.listed(watchers.alice) == [ORIGINAL]
    assert made["not_yet"] == [{"key": made["not_yet"][0]["key"], "title": "The Keeper of Lost Rooms",
                                "language": "de", "announced_in": "en", "listed": True}]
    listing = watchers.client(watchers.alice).get("/api/v1/wishlist").json()
    assert list(listing["via"].values()) == [AUTHOR] and list(listing["via_why"].values()) == ["language:en"]
    # A later build knows the German edition: now it is wished, in German.
    edit_keeper(lambda titles: titles.update(de={"title": KEEPER, "released": "2027-05-05", "year": 2027,
                                                 "ebook": True, "audio": False, "sources": ["dnb"]}))
    with SessionLocal() as db:
        row = db.scalar(select(CatalogPerson).where(CatalogPerson.token == token))
        assert row is not None and row.built_at is not None
        from datetime import timedelta

        row.built_at = row.built_at + timedelta(seconds=5)
        db.commit()
    assert watchers.beat()["looked"] == 1
    assert [(row.kind, row.language, row.release_date) for row in watchers.wishes()] == [("ebook", "de", "2027-05-05")]


def test_without_the_right_the_book_announced_in_another_language_is_listed_as_well(watchers: World) -> None:
    token = watchers.built()
    edit_keeper(lambda titles: titles.pop("de"))
    made = watchers.watch(watchers.bob, token, kind="both")
    assert made["made"]["listed"] == 1 and watchers.listed(watchers.bob) == [ORIGINAL]
    assert [(item.via, item.language, item.why) for item in watchers.items()] == [("list", "en", "language:en")]


# ---- What is no new book -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "why"), [
    ("Koenigs Erlaeuterungen: Der Nebelchor", "companion"),
    ("Der Nebelchor. Lektürehilfe", "companion"),
    ("Der Nebelchor. Schulausgabe mit Materialien", "edition"),
    ("Der Nebelchor, nacherzählt für Kinder", "adaptation"),
    ("Der Nebelchor: Das Buch zum Film", "edition"),
    ("Der Nebelchor. Die Graphic Novel", "adaptation"),
    ("Der Nebelchor. Teil 1 von 2", "part"),
    ("Das Moor-Paket", "box"),
    ("Der Nebelchor. Gesamtausgabe", "box"),
    ("Der Nebelchor. Das Hörspiel", "play"),
])
def test_what_is_no_new_book_of_its_own_goes_onto_the_wish_list_and_is_never_wished(watchers: World, title: str,
                                                                                   why: str) -> None:
    token = watchers.built()
    watchers.new_work(title, language="de", released="2027-05-05", key="doubt", audio=True)
    made = watchers.watch(watchers.alice, token, kind="both")
    assert title not in watchers.titles_of(watchers.wishes())
    assert title in watchers.listed(watchers.alice) and made["made"]["listed"] == 1
    line = next(line for line in made["announced"] if line["title"] == title)
    assert (line["state"], line["why"]) == ("listed", why)
    # The strip does not name it as the next book.
    strip = watchers.client(watchers.alice).get("/api/v1/watching").json()["items"][0]["next"]
    assert strip["title"] == KEEPER


def test_the_volume_of_a_series_named_with_teil_is_a_book_of_its_own(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Die Wrenfield-Chroniken Teil 4", language="de", released="2027-05-05", key="vol4")
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == "vol4"))
        assert work is not None
        work.series, work.series_pos = "The Wrenfield Chronicles", 4.0
        db.commit()
    watchers.watch(watchers.alice, token, kind="ebook")
    assert "Die Wrenfield-Chroniken Teil 4" in watchers.titles_of(watchers.wishes())


def test_new_is_new_in_the_language_and_a_later_edition_in_it_is_not(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Das Moorhaus", language="de", released="2027-03-01", key="late")
    watchers.new_work("Das Fennlicht", language="de", released="2027-03-01", key="again")
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == "late"))
        assert work is not None
        work.year = 2005  # first out in 2005 in another language; the German edition of 2027 is the first in German
        again = db.scalar(select(CatalogWork).where(CatalogWork.key == "again"))
        assert again is not None
        # A record of a German edition of 2019 without a day: the edition of 2027 is a later one in German.
        again.titles = {"de": {**again.titles["de"], "year": 2019}}
        db.commit()
    made = watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert "Das Moorhaus" in watchers.titles_of(watchers.wishes())
    assert watching.doubt_of(work_of("late"), work_of("late").titles["de"]) == ""
    assert "Das Fennlicht" not in watchers.titles_of(watchers.wishes())
    assert "Das Fennlicht" not in [line["title"] for line in made["announced"]]
    # A translation a year after the original is new: the keeper (English 2026, German 2027) is wished.
    assert ORIGINAL in watchers.titles_of(watchers.wishes())


def test_a_study_guide_is_not_wished_by_itself(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Koenigs Erlaeuterungen: Der Nebelchor", language="de", released="2027-05-05", key="guide")
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert "Koenigs Erlaeuterungen: Der Nebelchor" not in watchers.titles_of(watchers.wishes())


# ---- A year without a day ------------------------------------------------------------------------------------------


def year_alone(key: str) -> None:
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == key))
        assert work is not None
        titles = dict(work.titles)
        titles["de"] = {**titles["de"], "released": ""}
        work.titles = titles
        db.commit()


def test_a_year_alone_is_no_first_of_january_and_the_wish_comes_with_the_day(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Das ferne Licht", language="de", released="2027", key="yearonly")
    year_alone("yearonly")
    made = watchers.watch(watchers.alice, token, kind="ebook", language="de")
    assert "2027-01-01" not in [row.release_date for row in watchers.wishes()]
    assert "Das ferne Licht" not in watchers.titles_of(watchers.wishes())
    line = next(line for line in made["announced"] if line["title"] == "Das ferne Licht")
    assert (line["state"], line["date"]) == ("year", "2027")
    assert "~yearonly" in (watchers.watch_row(watchers.alice).seen or [])
    # A later build knows the day: wished then, waiting for it.
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == "yearonly"))
        assert work is not None
        work.titles = {**work.titles, "de": {**work.titles["de"], "released": "2027-04-04"}}
        row = db.scalar(select(CatalogPerson).where(CatalogPerson.token == token))
        assert row is not None and row.built_at is not None
        from datetime import timedelta

        row.built_at = row.built_at + timedelta(seconds=5)
        db.commit()
    watchers.beat()
    assert ("Das ferne Licht", "2027-04-04") in [(watchers.titles_of([row])[0], row.release_date)
                                                 for row in watchers.wishes()]


def test_a_book_announced_for_a_year_is_wished_when_its_day_is_known_after_it_passed(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Das ferne Licht", language="de", released="2027", key="yearonly")
    year_alone("yearonly")
    watchers.watch(watchers.alice, token, kind="ebook", language="de")
    # Months later the sources know the day, and it is gone by: the book is out, and wished all the same.
    watchers.days(200)
    with SessionLocal() as db:
        work = db.scalar(select(CatalogWork).where(CatalogWork.key == "yearonly"))
        assert work is not None
        work.titles = {**work.titles, "de": {**work.titles["de"], "released": "2027-02-02"}}
        row = db.scalar(select(CatalogPerson).where(CatalogPerson.token == token))
        assert row is not None and row.built_at is not None
        from datetime import timedelta

        row.built_at = row.built_at + timedelta(days=201)
        db.commit()
    watchers.beat()
    assert "Das ferne Licht" in watchers.titles_of(watchers.wishes())


# ---- Once per book -------------------------------------------------------------------------------------------------


def test_a_book_wished_by_hand_in_another_language_is_not_wished_again(watchers: World) -> None:
    token = watchers.built()
    alice = watchers.client(watchers.alice)
    shown = alice.get(f"/api/v1/catalog/authors/{token}").json()
    keeper = next(w for w in shown["works"] if any(e["title"] == KEEPER for e in w["editions"]))
    by_hand = alice.post("/api/v1/procure/wishes", json={"items": [{"book": keeper["token"]}], "kinds": ["ebook"],
                                                       "language": "en"})
    assert by_hand.status_code == 201, by_hand.text
    made = watchers.watch(watchers.alice, token, kind="ebook")
    assert made["made"]["wished"] == 0 and [row.language for row in watchers.wishes()] == ["en"]
    assert [(line["title"], line["state"]) for line in made["announced"]] == [(KEEPER, "wished")]
    # The watch made none of it: the wish by hand is not counted as its own.
    assert made["wished"] == 0 and [(item.via, item.why) for item in watchers.items()] == [("held", "wished")]


def test_a_wish_of_another_person_in_the_same_library_is_no_reason_to_give_up(watchers: World) -> None:
    token = watchers.built()
    dora = make_account("dora")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=watchers.library, user_id=dora.id, right="acquire"))
        db.commit()
    watchers.watch(watchers.alice, token, kind="ebook")
    assert watchers.watch(dora, token, kind="ebook")["made"]["wished"] == 0
    assert [row.user_id for row in watchers.wishes()] == [watchers.alice.id]
    # Dora holds no claim of her own: when alice takes her wishes away, dora's next look wishes the book.
    assert not [item for item in watchers.items() if item.user_id == dora.id]
    alice = watchers.client(watchers.alice)
    assert alice.delete(f"/api/v1/watching/authors/{token}", params={"wishes": "remove"}).status_code == 204
    assert watchers.wishes() == []
    assert watchers.watch(dora, token, kind="ebook")["made"]["wished"] == 1
    assert [row.user_id for row in watchers.wishes()] == [dora.id]


# ---- Held, not tried again -----------------------------------------------------------------------------------------


def test_a_wish_refused_for_good_is_held_with_its_code_and_not_tried_at_every_look(
        watchers: World, monkeypatch: pytest.MonkeyPatch) -> None:
    token = watchers.built()
    calls: list[str] = []

    def refuse(*_args: Any, **kwargs: Any) -> Any:
        calls.append(str(kwargs.get("language")))
        raise error("not_a_book", "This is no book of its own. Ask for the book itself.", 422)

    monkeypatch.setattr(wishes, "create", refuse)
    made = watchers.watch(watchers.alice, token, kind="ebook")
    assert calls == ["de"] and made["made"]["wished"] == 0
    assert [(item.via, item.why) for item in watchers.items()] == [("held", "not_a_book")]
    line = next(line for line in made["announced"] if line["title"] == KEEPER)
    assert (line["state"], line["why"]) == ("held", "not_a_book")
    watchers.watch(watchers.alice, token, kind="ebook", more=True)
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01", key="late")
    watchers.beat()
    assert calls == ["de", "de"]  # the new book only


def test_a_book_whose_library_must_be_chosen_goes_onto_the_wish_list(watchers: World,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Two libraries take the kind and the operator chose none: a wish would ask the person which one, so the watch
    makes none and lists the book with the reason."""
    token = watchers.built()

    def ask(*_args: Any, **_kwargs: Any) -> Any:
        raise error("library_required", "Several libraries take this kind of book: name the one it goes into.", 422)

    monkeypatch.setattr(wishes, "create", ask)
    made = watchers.watch(watchers.alice, token, kind="ebook")
    assert made["made"] == {"wished": 0, "listed": 1, "stopped": ""}
    assert [(item.via, item.why) for item in watchers.items()] == [("list", "library")]
    assert watchers.listed(watchers.alice) == [ORIGINAL]


def test_a_claim_a_look_left_behind_is_taken_back_after_a_while(watchers: World) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    with SessionLocal() as db:
        cid = db.scalar(select(CatalogPerson.id).where(CatalogPerson.token == token))
        assert cid is not None
        from app import clock

        late = WatchItem(user_id=watchers.alice.id, catalog_person_id=cid, work_key="late", language="de", kind="ebook",
                         via="", created_at=clock.now())
        db.add(late)
        db.commit()
    watchers.new_work("Das späte Licht", language="de", released="2027-06-01", key="late")
    # A claim of a moment ago holds: another look is on it.
    watchers.beat()
    assert "Das späte Licht" not in watchers.titles_of(watchers.wishes())
    # Ten minutes later it is a claim a look left behind: taken back, and the book is wished.
    watchers.days(11 / (24 * 60))
    watchers.watch(watchers.alice, token, kind="ebook")
    assert "Das späte Licht" in watchers.titles_of(watchers.wishes())


# ---- What the page says --------------------------------------------------------------------------------------------


def test_a_work_the_watch_will_never_wish_is_not_announced_as_open(watchers: World) -> None:
    token = watchers.built()
    watchers.new_work("Nur als Buch", language="de", released="2027-05-05", key="onlybook", ebook=True, audio=False)
    watchers.watch(watchers.alice, token, kind="audio", language="de")
    shown = page(watchers, watchers.alice, token)
    assert [a["title"] for a in shown["announced"] if a["state"] == "open"] == []
    assert "Nur als Buch" not in [a["title"] for a in shown["announced"]]


def test_the_watch_says_when_the_sources_that_know_announcements_are_off(watchers: World,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    token = watchers.built()
    watchers.watch(watchers.alice, token, kind="ebook")
    monkeypatch.setattr(watching.sources_config, "ready_sources", lambda _db, among=None: [])
    assert page(watchers, watchers.alice, token)["sources_few"] is True
    monkeypatch.setattr(watching.sources_config, "ready_sources", lambda _db, among=None: ["audible"])
    assert page(watchers, watchers.alice, token)["sources_few"] is False

