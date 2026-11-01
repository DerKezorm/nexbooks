"""A recording is a play when its genre tag says so or many people read it; a publisher's line or the author's own
name in the field of the series is no series of the book. All names and titles are invented."""

from __future__ import annotations

from typing import Any

import pytest

from .library_helpers import book, books_folder, make_library, recording, scan
from .test_scanner import state


def one_edition() -> dict[str, Any]:
    [work] = state()["works"]
    [edition] = work["editions"]
    return {**edition, "series": work["series"], "pos": work["pos"]}


@pytest.mark.parametrize(("tags", "variant"), [
    ({"genre": "Hörspiel"}, "dramatized"),
    ({"narrator": "Ida Leser, Mara Brandt, Tom Kurz, Ole Lind"}, "dramatized"),
    ({"narrator": "Ida Leser"}, "unabridged"),
    ({"genre": "Krimi", "narrator": "Ida Leser, Mara Brandt"}, "unabridged"),
])
def test_a_genre_tag_or_a_cast_of_readers_makes_a_recording_a_play(tags: dict[str, str], variant: str) -> None:
    folder = books_folder(f"play-{abs(hash(str(tags)))}")
    recording(folder, "Rune Falkner/Nebelchor/nebelchor.m4b", 600.0, title="Nebelchor", album="Nebelchor",
              artist="Rune Falkner", **tags)
    scan(make_library(folder))
    assert one_edition()["variant"] == variant


@pytest.mark.parametrize(("series", "position", "kept"), [
    ("Fischer Taschenbuch", 1983.0, False),
    ("Rune Falkner", 2.0, False),
    ("Nebelchor", 2.0, True),
    # A series of plays counts past a hundred: the number alone keeps nothing out.
    ("Die vier Spürnasen", 210.0, True),
])
def test_a_publishers_line_or_the_authors_name_is_no_series_of_the_book(series: str, position: float,
                                                                        kept: bool) -> None:
    folder = books_folder(f"line-{abs(hash(series))}")
    book(folder, "falkner/laternen.epub", title="Die Laternenstraße", authors=("Rune Falkner",), language="de",
         series=series, series_pos=position)
    scan(make_library(folder))
    found = one_edition()
    assert (found["series"] == series) is kept, found["series"]
