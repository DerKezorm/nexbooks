"""The covers and descriptions of the books of the sources are there to be shown: kept for a day at most, never in a
backup, and fetched again when a list or a page needs them. The facts of a book (title, authors, series, day) stay, and
the covers of the books on the shelf, which come from the files, are not touched."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, ForeignBook, Job, SourceCache, Wish
from app.services import backups, covers
from app.services.discover import enrich, foreign, lists, pics
from app.services.sources import cache
from app.services.sources.model import Candidate

from .conftest import make_account
from .discover_helpers import NOW, Disc
from .discover_shelves import Shelves


@pytest.fixture
def disc(monkeypatch: pytest.MonkeyPatch) -> Disc:
    return Disc(monkeypatch)


def later(monkeypatch: pytest.MonkeyPatch, hours: float) -> None:
    moment = NOW + timedelta(hours=hours)
    monkeypatch.setattr(clock, "now", lambda: moment)


def book_with_text_and_cover(disc: Disc, title: str = "Day Book") -> int:
    """A book of a source with a description, a fetched cover, a series and a day, and a person's wish for it."""
    disc.only("apple")
    person = make_account("reader")
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(
            source="apple", key="1700000555", title=title, authors=["A Person"], description="A made-up blurb.",
            series="Made-Up Cycle", series_pos=2.0, released="2024-03-14", languages=["de"],
            cover_url="https://is1-ssl.mzstatic.com/x/100x100bb.jpg"), "ebook")
        db.commit()
        assert foreign.fetch_cover(db, row) is True
        db.add(Wish(user_id=person.id, foreign_id=row.id, title=title, author="A Person", cover=row.cover,
                    added_at=NOW))
        db.commit()
        return row.id


def row_of(ident: int) -> ForeignBook:
    with SessionLocal() as db:
        row = db.get(ForeignBook, ident)
        assert row is not None
        db.expunge(row)
        return row


def test_what_is_young_stays(disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = book_with_text_and_cover(disc)
    later(monkeypatch, 23)
    with SessionLocal() as db:
        assert foreign.expire_pictures(db) == 0
    row = row_of(ident)
    assert row.cover and pics.path_of(row.cover) is not None and row.data["description"] == "A made-up blurb."


def test_after_a_day_the_cover_and_the_description_are_gone_and_the_facts_stay(
        disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = book_with_text_and_cover(disc)
    before = row_of(ident)
    name = before.cover
    assert name and pics.path_of(name) is not None
    later(monkeypatch, 25)
    with SessionLocal() as db:
        assert foreign.expire_pictures(db) == 1
    row = row_of(ident)
    assert (row.cover, row.color) == ("", "")
    assert "description" not in row.data and "text_at" not in row.data and "cover_at" not in row.data
    assert pics.path_of(name) is None, "the file went with it"
    # The facts of the book stay.
    assert (row.title, row.authors, row.series, row.series_pos, row.released, row.language) == (
        "Day Book", ["A Person"], "Made-Up Cycle", 2.0, "2024-03-14", "de")
    assert row.data["forms"] and row.cover_url
    with SessionLocal() as db:
        assert db.scalars(select(Wish.cover)).one() == "", "the wish list shows no cover that is gone"


def test_a_cover_and_a_text_of_an_earlier_version_with_no_time_kept_are_old(disc: Disc) -> None:
    ident = book_with_text_and_cover(disc)
    with SessionLocal() as db:
        row = db.get(ForeignBook, ident)
        assert row is not None
        row.data = {key: value for key, value in row.data.items() if key not in ("cover_at", "text_at")}
        db.commit()
    with SessionLocal() as db:
        assert foreign.expire_pictures(db) == 1
    row = row_of(ident)
    assert row.cover == "" and "description" not in row.data


def test_the_cover_and_the_text_come_again_when_the_book_is_named_and_asked_for(
        disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = book_with_text_and_cover(disc)
    later(monkeypatch, 30)
    with SessionLocal() as db:
        foreign.expire_pictures(db)
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(
            source="apple", key="1700000555", title="Day Book", authors=["A Person"], description="A made-up blurb.",
            cover_url="https://is1-ssl.mzstatic.com/x/100x100bb.jpg"), "ebook")
        assert row.id == ident and row.data["description"] == "A made-up blurb."
        db.commit()  # the source is asked with sessions of its own: nothing may be held open here
        assert foreign.fetch_cover(db, row) is True
        db.commit()
    again = row_of(ident)
    assert again.cover and pics.path_of(again.cover) is not None
    with SessionLocal() as db:
        assert foreign.expire_pictures(db) == 0, "the new time starts at the new fetch"


def test_the_description_found_for_the_page_of_a_book_is_kept_for_a_day_as_well(
        disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    disc.only("apple")
    with SessionLocal() as db:
        row = foreign.upsert(db, Candidate(source="apple", key="1700000556", title="Bare Book",
                                           authors=["A Person"]), "ebook")
        db.commit()
        data = dict(row.data)
        data["description"], data["text_at"] = "Told by another source.", clock.now().isoformat()
        row.data = data
        db.commit()
        ident = row.id
    later(monkeypatch, 26)
    with SessionLocal() as db:
        assert foreign.expire_pictures(db) == 1
    assert "description" not in row_of(ident).data
    assert enrich.foreign is foreign  # the job that writes such a text stamps it (``text_at``)


def test_the_hourly_look_of_the_lists_lets_them_go(disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = book_with_text_and_cover(disc)
    later(monkeypatch, 25)
    lists._look(0)
    assert row_of(ident).cover == ""


def test_the_covers_of_books_on_a_shelf_are_not_touched(disc: Disc, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = book_with_text_and_cover(disc)
    shelf_cover = covers.folder() / ("a" * 20 + ".webp")
    shelf_cover.write_bytes(b"RIFF....WEBPa-cover-from-a-file")
    later(monkeypatch, 48)
    with SessionLocal() as db:
        foreign.expire_pictures(db)
    assert shelf_cover.is_file() and row_of(ident).cover == ""


def test_a_list_read_with_covers_gone_still_names_them_and_they_come_when_the_picture_is_asked_for(
        shelves: Shelves, monkeypatch: pytest.MonkeyPatch) -> None:
    carol = shelves.client(shelves.carol)
    first = carol.get("/api/v1/discover/lists/best", params={"market": "de"}).json()["items"]
    assert any(card["cover_url"] for card in first)
    later(monkeypatch, 30)
    with SessionLocal() as db:
        foreign.expire_pictures(db)
        assert not any(db.scalars(select(ForeignBook.cover)))
        for job in db.scalars(select(Job)):
            db.delete(job)
        db.commit()
    shown = carol.get("/api/v1/discover/lists/best", params={"market": "de"}).json()["items"]
    named = [card for card in shown if card["cover_url"]]
    assert named and all(card["cover_url"] == f"/api/v1/discover/books/{card['token']}/cover" for card in named)
    with SessionLocal() as db:
        assert not list(db.scalars(select(Job.ref).where(Job.kind == "fcover"))), "looking queues nothing"
    answer = carol.get(named[0]["cover_url"])
    assert answer.status_code == 200 and answer.content[:4] == b"RIFF"
    again = carol.get("/api/v1/discover/lists/best", params={"market": "de"}).json()["items"]
    assert any("?v=" in (card["cover_url"] or "") for card in again)


# ---- Audible and Apple: what they answered is kept for a day -------------------------------------------------------


def test_what_audible_and_apple_answered_is_kept_for_a_day_and_the_others_for_longer() -> None:
    for source in ("audible", "apple"):
        cache.put(source, "key", [{"title": "x"}], hit=True)
        cache.put(source, "key-long", [{"title": "x"}], hit=True, days=7)
    cache.put("openlibrary", "key", [{"title": "x"}], hit=True)
    with SessionLocal() as db:
        by = {(row.source, row.key): row.expires_at - row.fetched_at for row in db.scalars(select(SourceCache))}
    assert by[("audible", "key")] == timedelta(days=1) and by[("apple", "key")] == timedelta(days=1)
    assert by[("audible", "key-long")] == timedelta(days=1) and by[("apple", "key-long")] == timedelta(days=1)
    assert by[("openlibrary", "key")] == timedelta(days=30)


# ---- Never in a backup ---------------------------------------------------------------------------------------------


def test_a_backup_holds_no_cover_nor_description_of_a_source_and_nothing_audible_or_apple_said(
        disc: Disc, client: TestClient, operator: Account) -> None:
    ident = book_with_text_and_cover(disc)
    cache.put("audible", "a-search", [{"title": "A blurb", "description": "x"}], hit=True)
    cache.put("apple", "a-chart", [{"title": "A blurb"}], hit=True)
    cache.put("openlibrary", "a-search", [{"title": "Kept"}], hit=True)
    name = row_of(ident).cover
    assert pics.path_of(name) is not None
    path = backups.create(kind=backups.MANUAL)
    with zipfile.ZipFile(path) as archive:
        assert not any(entry.startswith("foreign-covers") or name in entry for entry in archive.namelist())
        database = Path(get_settings().data_dir) / "scratch-backup.db"
        database.write_bytes(archive.read(backups.DATABASE_ENTRY))
    try:
        with sqlite3.connect(database) as connection:
            cover, color, data = connection.execute("SELECT cover, color, data FROM foreign_books").fetchone()
            kept = json.loads(data)
            assert (cover, color) == ("", "") and "description" not in kept and "cover_at" not in kept
            assert kept["forms"] and connection.execute("SELECT title FROM foreign_books").fetchone() == ("Day Book",)
            assert connection.execute("SELECT cover FROM wishlist").fetchone() == ("",)
            sources = {row[0] for row in connection.execute("SELECT source FROM source_cache")}
        connection.close()
    finally:
        database.unlink(missing_ok=True)
    assert sources == {"openlibrary"}
    # The server's own data is as it was: the cover is still there and the cache still holds Audible's answer.
    assert row_of(ident).cover == name and pics.path_of(name) is not None
    assert cache.get("audible", "a-search") is not cache.MISSING
