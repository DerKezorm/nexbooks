"""Watching an author against a catalogue built from records in the shapes of the real sources (Open Library, Hardcover,
Audible, the German library), by the real builder: what the records say of an edition beside the core of their title
(``Teil 1 von 2``, ``1/2``, ``Das Hörspiel``, ``Zwei Romane in einem Band``, ``Neuausgabe``, ``bearbeitet von``) reaches
the decision, and nothing like it is wished. The cases and what watching does with each are in
``fixtures/oeuvre/watch_build.py`` (invented author Ivar Quellbrandt; ``watch_cases.json`` is written by it)."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, CatalogPerson, CatalogWork, ForeignBook, ProcureWish, WatchItem, Wish
from app.services.oeuvre import watch as watching
from app.services.oeuvre import works

from .conftest import make_account
from .library_helpers import book, books_folder, join, make_library
from .oeuvre_fakes import Sources
from .procure_fakes import FakeNetwork
from .procure_search_helpers import INDEXER_HOST, FeedIndexer, add_indexer
from .watch_helpers import World

AUTHOR = "Ivar Quellbrandt"
KEY = "ivar quellbrandt"
CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "oeuvre" / "watch_cases.json").read_text(encoding="utf-8"))


class Shelf(World):
    """The catalogue of Ivar Quellbrandt, a library with two of his books (the German "Der alte Leuchtturm" and the
    English "The Blue Archive"), alice who may ask for books, bob who reads."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, net: FakeNetwork) -> None:
        self.sources = Sources(monkeypatch)
        folder = books_folder("watch-catalogue")
        book(folder, "leuchtturm.epub", title="Der alte Leuchtturm", authors=(AUTHOR,), language="de",
             isbn="9783100000446")
        book(folder, "archive.epub", title="The Blue Archive", authors=(AUTHOR,), language="en", isbn="9780100000457")
        self.library = make_library(folder, "First", scan=True)
        self.alice = make_account("alice")
        self.bob = make_account("bob")
        self.carol = make_account("carol")
        with SessionLocal() as db:
            for account in db.scalars(select(Account).where(Account.name.in_(("alice", "bob", "carol")))):
                account.language = "de"
            db.commit()
        join(self.library, self.alice.id, "acquire")
        join(self.library, self.bob.id, "read")
        fake = FeedIndexer(INDEXER_HOST, secrets.token_hex(16))
        net.add(fake)
        self.indexer = add_indexer(fake)
        self.net = net
        self.clients = []

    def person_id(self, name: str = AUTHOR) -> int:
        return super().person_id(name)

    def built(self, account: Account | None = None) -> str:
        client = self.client(account or self.alice)
        assert client.get(f"/api/v1/people/{self.person_id()}/catalog").status_code == 200
        self.sources.build()
        with SessionLocal() as db:
            token = db.scalar(select(CatalogPerson.token).where(CatalogPerson.key == KEY))
        assert token is not None
        return token


@pytest.fixture
def shelf(monkeypatch: pytest.MonkeyPatch, net: FakeNetwork) -> Iterator[Shelf]:
    world = Shelf(monkeypatch, net)
    yield world
    for client in world.clients:
        client.close()


def outcome(uid: int) -> dict[str, dict[str, Any]]:
    """For each case: the kinds wished, and the reason it went onto the wish list (None when it did not)."""
    found: dict[str, dict[str, Any]] = {item["id"]: {"wished": set(), "listed": None, "titles": None} for item in CASES}

    def case_of(titles: set[str]) -> str | None:
        return next((item["id"] for item in CASES if item["en"] in titles or item["de"] in titles), None)

    with SessionLocal() as db:
        works_by_key = {}
        for work in db.scalars(select(CatalogWork)):
            titles = {str(item.get("title")) for item in (work.titles or {}).values() if isinstance(item, dict)}
            ident = case_of(titles)
            if ident:
                works_by_key[work.key] = ident
                found[ident]["titles"] = work.titles
        for wish in db.scalars(select(ProcureWish).where(ProcureWish.user_id == uid)):
            foreign = db.get(ForeignBook, wish.foreign_id) if wish.foreign_id else None
            ident = case_of({foreign.title}) if foreign is not None else None
            if ident:
                found[ident]["wished"].add(wish.kind)
        for item in db.scalars(select(WatchItem).where(WatchItem.user_id == uid, WatchItem.via == "list")):
            ident = works_by_key.get(item.work_key)
            if ident:
                found[ident]["listed"] = item.why or "-"
    return found


def test_every_case_of_the_catalogue_is_wished_listed_or_left_as_it_should_be(shelf: Shelf) -> None:
    token = shelf.built()
    shelf.watch(shelf.alice, token, kind="both")
    got = outcome(shelf.alice.id)
    wrong = []
    for item in CASES:
        want, mine = item["want"], got[item["id"]]
        if want == "limit":
            continue
        if mine["titles"] is None:
            wrong.append(f"{item['id']}: not in the catalogue")
            continue
        if want == "wish":
            kinds = set(item.get("kinds") or [])
            ok = bool(mine["wished"]) and (not kinds or mine["wished"] == kinds) and mine["listed"] == item.get("listed")
        elif want == "nothing":
            ok = not mine["wished"] and mine["listed"] is None
        else:
            ok = not mine["wished"] and mine["listed"] == want.removeprefix("list:")
        if not ok:
            wrong.append(f"{item['id']}: want {want}, wished {sorted(mine['wished'])}, listed {mine['listed']}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("ident", ["c17_part_1_of_2", "c18_part_slash", "c20_two_novels", "c39_play",
                                   "c50_new_edition_subtitle", "c12_new_translation_old_known"])
def test_a_part_a_play_two_novels_a_new_edition_or_a_new_translation_from_the_built_catalogue_is_never_wished(
        shelf: Shelf, ident: str) -> None:
    token = shelf.built()
    shelf.watch(shelf.alice, token, kind="both")
    mine = outcome(shelf.alice.id)[ident]
    assert mine["titles"] is not None
    assert mine["wished"] == set(), mine["titles"]
    assert mine["listed"], mine["titles"]


@pytest.mark.parametrize("ident", ["c57_translated_two_years_after", "c10_paperback_alone", "c07_new_edition"])
def test_new_is_new_in_the_language_of_the_watch_however_long_ago_the_original_came_out(shelf: Shelf,
                                                                                         ident: str) -> None:
    """The first German edition the records know is new in German: two years, a year and a week, twenty-two years
    after the English original."""
    token = shelf.built()
    shelf.watch(shelf.alice, token, kind="both")
    mine = outcome(shelf.alice.id)[ident]
    assert mine["titles"] is not None
    assert mine["wished"], mine["titles"]
    assert mine["listed"] is None, mine["titles"]


def test_a_second_translation_goes_onto_the_wish_list_and_a_paperback_of_a_german_edition_is_left_alone(
        shelf: Shelf) -> None:
    token = shelf.built()
    made = shelf.watch(shelf.alice, token, kind="both")
    got = outcome(shelf.alice.id)
    second, paperback = got["c12_new_translation_old_known"], got["c56_paperback_of_german"]
    # The first German translation (2001) and the second (2027, other translators) are one edition in German that
    # keeps its first day, its latest one and the mark of two translations.
    assert second["titles"]["de"]["released"] == "2001-05-01"
    assert second["titles"]["de"]["latest"] == "2027-04-13"
    assert "text:translation" in second["titles"]["de"]["marks"]
    assert (second["wished"], second["listed"]) == (set(), "translation")
    line = next(item for item in made["announced"] if item["title"] == "Die Papierbruecke")
    assert (line["state"], line["why"], line["date"]) == ("listed", "translation", "2027-04-13")
    # The paperback of the German edition of 2024 (the same translator): no wish, not on the list, not shown.
    assert paperback["titles"]["de"]["year"] == 2024
    assert (paperback["wished"], paperback["listed"]) == (set(), None)
    assert "Das Moorlicht" not in [item["title"] for item in made["announced"]]
    # (So is the paperback of a German edition of this year whose two days the shop knows.)
    assert (got["c09_paperback_year"]["wished"], got["c09_paperback_year"]["listed"]) == (set(), None)


def test_what_the_core_of_a_title_cuts_off_stays_with_the_edition_as_its_marks(shelf: Shelf) -> None:
    shelf.built()
    by_case = {ident: value["titles"] for ident, value in outcome(shelf.alice.id).items()}
    assert "audio:part:1:2" in by_case["c17_part_1_of_2"]["de"]["marks"]
    assert "text:part:1:2" in by_case["c17_part_1_of_2"]["de"]["marks"]
    assert "audio:play" in by_case["c39_play"]["de"]["marks"]
    assert "text:box" in by_case["c20_two_novels"]["de"]["marks"]
    assert "text:reissue" in by_case["c50_new_edition_subtitle"]["de"]["marks"]
    assert "text:adaptation" in by_case["c51_adapted_by"]["de"]["marks"]
    # (A plain new book carries none, and its edition is written as before.)
    assert "marks" not in by_case["c01_new_both"]["de"]


def test_a_play_beside_a_new_novel_holds_the_audio_book_and_not_the_e_book(shelf: Shelf) -> None:
    token = shelf.built()
    shelf.watch(shelf.alice, token, kind="both")
    mine = outcome(shelf.alice.id)["c53_play_beside_novel"]
    assert mine["wished"] == {"ebook"}
    assert mine["listed"] == "play"


def test_watching_audio_books_wishes_only_what_has_a_recording(shelf: Shelf) -> None:
    token = shelf.built()
    shelf.watch(shelf.alice, token, kind="audio")
    got = outcome(shelf.alice.id)
    assert got["c47_audio_only_new"]["wished"] == {"audio"}
    assert got["c48_ebook_only"]["wished"] == set() and got["c48_ebook_only"]["listed"] is None
    assert got["c53_play_beside_novel"]["wished"] == set() and got["c53_play_beside_novel"]["listed"] == "play"


def test_a_person_who_may_not_ask_for_books_gets_the_new_books_on_the_wish_list_and_no_wish(shelf: Shelf) -> None:
    token = shelf.built()
    shelf.watch(shelf.bob, token, kind="both")
    with SessionLocal() as db:
        assert db.scalars(select(ProcureWish)).first() is None
        titles = set(db.scalars(select(Wish.title).where(Wish.user_id == shelf.bob.id)))
    assert "The Lantern Keeper" in titles


def test_the_translation_of_a_book_the_person_has_in_the_original_says_so_on_the_page(shelf: Shelf) -> None:
    token = shelf.built()
    shelf.watch(shelf.alice, token, kind="both")
    page = shelf.client(shelf.alice).get(f"/api/v1/catalog/authors/{token}").json()["watch"]
    line = next(item for item in page["announced"] if item["title"] == "Das blaue Archiv")
    assert (line["state"], line["why"]) == ("listed", "own:en")


def test_marks_of_reads_the_title_as_it_came_the_subtitle_the_part_and_the_credits() -> None:
    from app.services.sources.model import Entry

    def marks(**fields: Any) -> list[str]:
        return works.marks_of(Entry(source="dnb", key="1", **fields))

    assert marks(title="Der Hof", signals={"part": "1 von 2"}) == ["text:part:1:2"]
    assert marks(title="Der Hof", form="audio", signals={"full_title": "Der Hof (Teil 2 von 3)"}) == [
        "audio:part:2:3"]
    assert marks(title="Der Hof", form="audio", variant="dramatized") == ["audio:play"]
    assert marks(title="Der Hof", signals={"subtitle": "Drei Romane in einem Band"}) == ["text:box"]
    assert marks(title="Der Hof", signals={"subtitle": "Neuauflage"}) == ["text:reissue"]
    assert marks(title="Der Hof", signals={"credits": "Ivar Quellbrandt ; bearbeitet von Lena Beispiel"}) == [
        "text:adaptation"]
    assert marks(title="Der Hof", signals={"subtitle": "Neu übersetzt von Anna Beispiel"}) == ["text:translation"]
    assert marks(title="Der Hof", signals={"subtitle": "Roman"}) == []
    # (A volume of a series is no part: ``Band 4`` says nothing, ``Teil 4`` is told apart by its place in the series.)
    assert marks(title="Der Hof", signals={"part": "Band 4"}) == []


def test_doubt_of_reads_a_bare_part_and_a_new_edition_in_a_title_without_marks() -> None:
    """(A catalogue built before the marks were kept holds the titles alone.)"""
    for title, code in (("Der tiefe Hof 1/2", "part"), ("Der Rostgarten Neuausgabe", "reissue"),
                        ("Die Nachtbarke. Neu übersetzt", "translation"), ("Der Hof", "")):
        work = CatalogWork(key="k", titles={"de": {"title": title, "released": "2027-03-02"}})
        assert watching.doubt_of(work, work.titles["de"]) == code, title
    # (A new edition of the English original says nothing of the first German translation, nor does its year.)
    work = CatalogWork(key="k", year=2005, titles={"en": {"title": "The Fen House: Anniversary Edition",
                                                          "released": "2026-02-10", "year": 2005},
                                                   "de": {"title": "Das Fennhaus", "released": "2027-02-10"}})
    assert watching.doubt_of(work, work.titles["de"]) == ""
    assert watching.doubt_of(work, work.titles["en"]) == "reissue"


def test_a_year_of_the_language_before_its_first_day_is_an_earlier_edition_also_for_a_book_out_since_the_last_look(
        ) -> None:
    """(As the page of the author reads it: a day beside an earlier year is a later edition.)"""
    assert watching.earlier({"released": "2027-03-02", "year": 2026})
    assert not watching.earlier({"released": "2027-03-02", "year": 2027})
    assert not watching.earlier({"released": "", "year": 2019})
    # Out since the last look (a later look): the first edition in German is new, a later one is not.
    since = watching.local_today() - timedelta(days=3)
    out = (watching.local_today() - timedelta(days=1)).isoformat()
    assert watching.is_new({"released": out, "year": int(out[:4])}, mode="beat", since=since, known=False)
    assert not watching.is_new({"released": out, "year": int(out[:4]) - 1}, mode="beat", since=since, known=False)


def test_doubt_of_a_part_looks_at_the_place_in_the_series_and_at_the_kind() -> None:
    work = CatalogWork(key="k", titles={"de": {"title": "Der Hof", "released": "2027-03-02",
                                               "marks": ["audio:part:4:", "audio:play"]}}, series_pos=4.0)
    edition = work.titles["de"]
    # (``Teil 4`` of the fourth volume is the volume; the play is the recording's alone.)
    assert watching.doubt_of(work, edition, ["ebook"]) == ""
    assert watching.doubt_of(work, edition, ["audio"]) == "play"
    work.series_pos = 2.0
    assert watching.doubt_of(work, {**edition, "marks": ["audio:part:4:"]}, ["audio"]) == "part"
    assert watching.doubt_of(work, {**edition, "marks": ["text:part:1:2"]}, ["ebook"]) == "part"
