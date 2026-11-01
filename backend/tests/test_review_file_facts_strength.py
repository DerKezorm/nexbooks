"""Only a strong sign makes "the file contradicts": a chapter or track title, a year a few years off and the ISBN of
another edition of the same book are weak or close, and the thresholds of the running time and the year are exact."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Edition
from app.services.match import filefacts
from app.services.sources.model import Candidate

from .library_helpers import books_folder, make_library, recording, scan


def says(**values: object) -> filefacts.Facts:
    return filefacts.Facts(folder="", files=[], says=filefacts.Says(**values))  # type: ignore[arg-type]


def states(facts: filefacts.Facts, candidate: Candidate, kind: str = "ebook") -> dict[str, str]:
    return {row.field: row.state for row in filefacts.compare(facts, candidate, kind)}


def source(**values: object) -> Candidate:
    base: dict[str, object] = {"source": "openlibrary", "key": "OL1W", "title": "The Quiet Orchard",
                               "authors": ["Mara Lindqvist"]}
    base.update(values)
    return Candidate(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("year,state", [(2019, "agrees"), (2020, "similar"), (2017, "similar"), (2016, "differs"),
                                        (2022, "differs")])
def test_a_year_up_to_two_apart_is_close_and_more_is_a_contradiction(year: int, state: str) -> None:
    found = states(says(title="The Quiet Orchard", year=year), source(year=2019))
    assert found["year"] == state


@pytest.mark.parametrize("minutes,state", [(600, "agrees"), (631, "agrees"), (632, "similar"), (705, "similar"),
                                           (706, "differs"), (900, "differs")])
def test_the_running_time_thresholds_are_five_and_fifteen_percent(minutes: int, state: str) -> None:
    facts = says(title="The Quiet Orchard", duration=minutes * 60.0)
    assert states(facts, source(runtime_min=600), "audio")["runtime"] == state


def test_the_isbn_of_another_edition_of_the_same_book_is_close_not_a_contradiction() -> None:
    facts = says(title="The Quiet Orchard", authors=["Mara Lindqvist"], isbn="9780000000002")
    assert states(facts, source(isbns=["9781111111111"]))["isbn"] == "similar"
    # The same ISBN still agrees; one that comes with another title is a reason against.
    assert states(facts, source(isbns=["9780000000002"]))["isbn"] == "agrees"
    other = says(title="A Different Book Entirely", authors=["Mara Lindqvist"], isbn="9780000000002")
    assert states(other, source(isbns=["9781111111111"]))["isbn"] == "differs"
    stranger = says(title="The Quiet Orchard", authors=["Someone Else"], isbn="9780000000002")
    assert states(stranger, source(isbns=["9781111111111"]))["isbn"] == "differs"


@pytest.mark.parametrize("track", ["Kapitel 1", "Track 03", "07", "Chapter 12: The Road", "Part 2 of 5", "3/12"])
def test_a_track_or_chapter_title_names_no_book(client: TestClient, operator: Account, track: str) -> None:
    """A recording whose tags carry no album, only the title of the track: the file says nothing about the title, so the
    right proposal is not marked as contradicted."""
    folder = books_folder("tracks")
    recording(folder, "Lindqvist/The Quiet Orchard/01.mp3", seconds=3.0, title=track, artist="Mara Lindqvist", track=1)
    scan(make_library(folder, "Tracks", kind="audio"))
    with SessionLocal() as db:
        row = db.scalar(select(Edition))
        assert row is not None
        facts = filefacts.facts_of(db, row)
    assert facts.says.title == "" and facts.says.authors == ["Mara Lindqvist"]
    found = states(facts, source(), "audio")
    assert found["title"] == "file_silent" and found["authors"] == "agrees"


@pytest.mark.parametrize("title", ["1984", "12 Years a Slave", "The Quiet Orchard"])
def test_a_real_title_that_holds_a_number_is_still_a_title(title: str) -> None:
    from app.services.match.filefacts import _TRACK_TITLE

    assert _TRACK_TITLE.match(title) is None
