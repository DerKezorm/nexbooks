"""The shelves made for one person: "because you read ...", "like your favourite books", "short", "for long
evenings". The rule above all: **they are made on this server from what the person read, and from what they may see, and
nothing of it is asked of a source.** Nobody's reading shows in anybody else's rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Edition, ForeignBook, HistoryEntry, LibraryMember
from app.services.browse import cache

from .browse_helpers import Browse, personal, titles

KEYS = ["because", "like", "short", "long"]


def finished(world: Browse, account: Any, title: str, day: str, days: int | None) -> None:
    """The person finished the book on ``day``, in ``days`` days from the first report (None: by hand)."""
    with SessionLocal() as db:
        edition = db.scalars(select(Edition).where(Edition.title == title)).first()
        assert edition is not None, title
        db.add(HistoryEntry(user_id=account.id, edition_id=edition.id, kind="read", finished_on=day, source="auto",
                            days=days))
        db.commit()


def rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in body["rows"]}


def test_a_person_who_read_nothing_has_the_short_books_and_no_more(browse: Browse) -> None:
    body = personal(browse.client(browse.carol), hide_owned="true")
    assert [row["key"] for row in body["rows"]] == ["short"]


def test_the_short_row_is_the_books_under_250_pages_the_most_wanted_first_and_never_a_children_s_book(browse: Browse) -> None:
    short = rows(personal(browse.client(browse.carol)))["short"]
    cards = short["items"]
    assert cards and all(card["pages"] is not None and card["pages"] < 250 for card in cards)
    assert len({c["token"] for c in cards}) == len(cards) <= 14, "a book once, at most fourteen"
    assert "Fremdes Bild" not in titles(short), "a book whose pages nobody said is not short"
    assert titles(short)[0] == "Hafenlichter", "the order of the sources: the most wanted of the short ones first"


def test_because_you_read_puts_more_by_the_same_people_first_and_then_the_shelves_of_the_genre(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 40)
    body = rows(personal(browse.client(browse.alice), hide_owned="true"))
    because = body["because"]
    assert [w["title"] for w in because["reads"]] == ["Die Salzwiese"]
    assert because["author"] == "I. Marwitz" and because["genre"] == "krimi"
    assert titles(because)[0] == "Altes Geld tötet", "Ilse Marwitz wrote it: the same person first"
    assert "Die Salzwiese" not in titles(because) and "Der Leuchtturmwaerter" not in titles(because), "what she has is hidden"
    assert len(titles(because)) > 3


def test_because_you_read_is_the_book_finished_last(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-01", 9)
    finished(browse, browse.alice, "Der Leuchtturmwaerter", "2026-10-10", 9)
    body = rows(personal(browse.client(browse.alice)))
    assert [w["title"] for w in body["because"]["reads"]] == ["Der Leuchtturmwaerter"]


def add_fantasy(world: Browse) -> None:
    """Open Library knows three novels of fantasy (one of them by the author of a book on alice's shelf), and the shelf is
    fetched again."""
    def doc(key: int, de: str, authors: list[str], year: int) -> dict[str, Any]:
        edition = {"key": f"/books/OL{key}M", "title": de, "language": ["ger"], "cover_i": 90000 + key, "publish_date": [str(year)]}
        return {"key": f"/works/OL{key}W", "title": de, "author_name": authors, "first_publish_year": year, "language": ["ger"],
                "cover_i": 80000 + key, "subject": ["Fantasy", "Epic fantasy"], "number_of_pages_median": 400, "edition_count": 9,
                "editions": {"numFound": 1, "start": 0, "docs": [edition]}}

    world.fake.ol_shelf = [*world.fake.ol_shelf, doc(2001, "Das Tor der Weberin", ["Iris Quist"], 2023),
                           doc(2003, "Die Aschenkrone", ["Dora Lindt"], 2022), doc(2005, "Der Mondsee", ["Per Holm"], 2024)]
    world.advance(25 * 3600)
    world.shelves("de")


def test_a_favourite_is_a_book_finished_within_five_days_and_the_row_is_more_like_it(browse: Browse) -> None:
    add_fantasy(browse)
    finished(browse, browse.alice, "Die Krone der Weberinnen", "2026-09-20", 5)
    finished(browse, browse.alice, "Der Leuchtturmwaerter", "2026-09-25", 6)
    finished(browse, browse.alice, "Nach dem Sturm", "2026-09-28", None)
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 40)
    body = rows(personal(browse.client(browse.alice), hide_owned="true"))
    like = body["like"]
    assert [w["title"] for w in like["reads"]] == ["Die Krone der Weberinnen"], "five days counts, six does not, by hand never"
    assert {"Die Aschenkrone", "Der Mondsee"} <= set(titles(like)), "the novels of the shelf of fantasy"
    assert "Das Tor der Weberin" not in titles(like), "not the author of the favourite: those come with 'because'"
    assert not set(titles(like)) & set(titles(body["because"])), "what the row before shows is not shown twice"


def test_without_a_favourite_there_is_no_row_of_them(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 40)
    assert "like" not in rows(personal(browse.client(browse.alice)))


def test_the_recordings_for_long_evenings_are_there_only_when_a_source_gave_a_running_time(browse: Browse) -> None:
    assert "long" not in rows(personal(browse.client(browse.carol))), "the open sources do not name a running time"
    with SessionLocal() as db:
        row = db.scalar(select(ForeignBook).where(ForeignBook.ref == "apple:1800000004"))
        assert row is not None
        data = dict(row.data)
        data["forms"] = [*data.get("forms", []), {"kind": "audio", "variant": "unabridged", "language": "de", "minutes": 1263,
                                                  "narrators": [], "pages": None, "source": "audible"}]
        row.data = data
        short = db.scalar(select(ForeignBook).where(ForeignBook.title == "Kurze Nacht"))
        assert short is not None
        short_data = dict(short.data)
        short_data["forms"] = [{"kind": "audio", "variant": "unabridged", "language": "de", "minutes": 600, "narrators": [],
                                "pages": None, "source": "audible"}]
        short.data = short_data
        db.commit()
    cache.forget()
    body = rows(personal(browse.client(browse.carol)))
    assert titles(body["long"]) == ["Orbit Null"], "twenty hours or more; ten hours is not long"
    assert body["long"]["items"][0]["minutes"] == 1263


def test_a_book_the_person_has_is_not_offered_when_they_hide_what_they_have(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 40)
    alice = browse.client(browse.alice)
    hidden = rows(personal(alice, hide_owned="true"))
    shown = rows(personal(alice, hide_owned="false"))
    owned = {"Der Leuchtturmwaerter", "Die Salzwiese"}
    for row in hidden.values():
        assert not owned & set(titles(row))
    assert owned & {t for row in shown.values() for t in titles(row)}


def test_nobody_s_reading_shows_in_anybody_else_s_rows(browse: Browse) -> None:
    carol, alice = browse.client(browse.carol), browse.client(browse.alice)
    before_carol, before_alice = personal(carol), personal(alice)
    finished(browse, browse.bob, "Orbit Null", "2026-10-11", 1)
    finished(browse, browse.bob, "Kaltes Licht", "2026-10-12", 2)
    assert personal(carol) == before_carol and personal(alice) == before_alice
    assert [row["key"] for row in personal(browse.client(browse.bob))["rows"]][:2] == ["because", "like"]


def test_a_book_in_a_library_the_person_left_counts_no_more(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 1)
    alice = browse.client(browse.alice)
    assert "because" in rows(personal(alice))
    with SessionLocal() as db:
        db.execute(delete(LibraryMember).where(LibraryMember.user_id == browse.alice.id))
        db.commit()
    body = rows(personal(alice))
    assert "because" not in body and "like" not in body


def test_the_rows_ask_no_source(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 1)
    asked = len(browse.fake.log)
    personal(browse.client(browse.alice))
    assert len(browse.fake.log) == asked, "made on this server and nowhere else"


def test_with_the_main_switch_off_the_rows_are_made_all_the_same_from_what_was_kept(browse: Browse) -> None:
    finished(browse, browse.alice, "Die Salzwiese", "2026-10-10", 1)
    browse.settings(sources_enabled=False)
    assert "because" in rows(personal(browse.client(browse.alice)))
