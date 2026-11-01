"""The rank of what is said about a book: a person, then the file, then a source. Pure rule on the data an edition is put
together from (``catalog.Meta``)."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.catalog import Meta
from app.services.match import overlay


def meta(**kw: Any) -> Meta:
    kind = kw.pop("kind", "ebook")
    return Meta(kind=kind, title=kw.pop("title", "The Quiet Orchard"), **kw)


def source(**values: Any) -> dict[str, Any]:
    return overlay.with_source(None, values)


def hand(**values: Any) -> dict[str, Any]:
    return overlay.with_hand(None, values)


# ---- Text -------------------------------------------------------------------------------------------------------


def test_a_source_fills_what_the_file_leaves_empty_and_nothing_else() -> None:
    empty = meta()
    overlay.apply_to(empty, source(description="From a source.", genre="krimi", isbn="9783999900018"))
    assert (empty.description, empty.genre, empty.isbn) == ("From a source.", "krimi", "9783999900018")
    full = meta(description="From the file.", genre="roman", isbn="9783999900025")
    overlay.apply_to(full, source(description="From a source.", genre="krimi", isbn="9783999900018"))
    assert (full.description, full.genre, full.isbn) == ("From the file.", "roman", "9783999900025")


def test_a_source_that_writes_the_same_thing_differently_changes_nothing() -> None:
    named = meta(authors=["Lindqvist, Mara"], series="Orchard Tales", language="en")
    overlay.apply_to(named, source(authors=["Mara Lindqvist"], series="Orchard tales", language="eng"))
    assert (named.authors, named.series, named.language) == (["Lindqvist, Mara"], "Orchard Tales", "en")


def test_a_person_replaces_what_the_file_says_even_with_nothing() -> None:
    said = meta(description="From the file.", genre="roman", authors=["File Author"])
    overlay.apply_to(said, hand(description="By hand.", genre="", authors=["Hand Author", "Second One"]))
    assert (said.description, said.genre, said.authors) == ("By hand.", "", ["Hand Author", "Second One"])


def test_a_person_beats_a_source_on_the_same_field() -> None:
    both = overlay.with_source(hand(genre="krimi"), {"genre": "scifi", "description": "From a source."})
    assert both["genre"] == {"v": "krimi", "by": "hand"}, "a new match does not touch a correction"
    assert both["description"]["by"] == "source"
    said = meta()
    overlay.apply_to(said, both)
    assert (said.genre, said.description) == ("krimi", "From a source.")


def test_a_new_match_replaces_its_own_earlier_answers_and_keeps_the_persons() -> None:
    first = overlay.with_source(hand(title="Mine"), {"description": "Old", "genre": "roman"})
    second = overlay.with_source(first, {"description": "New"})
    assert second["description"]["v"] == "New" and "genre" not in second and second["title"]["by"] == "hand"
    assert overlay.has_source(second) and not overlay.has_source(overlay.without_source(second))
    assert list(overlay.without_source(second)) == ["title"]


def test_a_correction_is_taken_back_with_none() -> None:
    corrected = hand(title="Mine", genre="krimi")
    assert list(overlay.with_hand(corrected, {"genre": None})) == ["title"]
    assert overlay.with_hand(corrected, {"nonsense": "x", "genre": "krimi"}).keys() == corrected.keys()


# ---- Lists, numbers ---------------------------------------------------------------------------------------------


def test_the_year_follows_the_same_rank_and_sets_the_date_when_there_is_none() -> None:
    plain = meta()
    overlay.apply_to(plain, source(year=2019))
    assert (plain.year, plain.published) == (2019, "2019")
    dated = meta(year=2001, published="2001-05-05")
    overlay.apply_to(dated, source(year=2019))
    assert (dated.year, dated.published) == (2001, "2001-05-05")
    overlay.apply_to(dated, hand(year=1999))
    assert (dated.year, dated.published) == (1999, "1999")


def test_narrators_come_from_a_source_only_where_the_files_name_none() -> None:
    plain = meta(kind="audio")
    overlay.apply_to(plain, source(narrators=["Ben Hollister"]))
    assert plain.narrators == ["Ben Hollister"]
    told = meta(kind="audio", narrators=["Ruth Example"])
    overlay.apply_to(told, source(narrators=["Ben Hollister"]))
    assert told.narrators == ["Ruth Example"]


# ---- Series -----------------------------------------------------------------------------------------------------


def test_a_series_and_its_volume_belong_together() -> None:
    none = meta()
    overlay.apply_to(none, source(series="Orchard Tales", series_pos=2.0))
    assert (none.series, none.series_pos) == ("Orchard Tales", 2.0)


def test_a_source_fills_the_volume_of_the_series_the_file_names_but_never_of_another_series() -> None:
    same = meta(series="orchard tales")
    overlay.apply_to(same, source(series="Orchard Tales", series_pos=3.0))
    assert (same.series, same.series_pos) == ("orchard tales", 3.0)
    other = meta(series="Harbor Chronicles")
    overlay.apply_to(other, source(series="Orchard Tales", series_pos=3.0))
    assert (other.series, other.series_pos) == ("Harbor Chronicles", None), "the volume of another series is none of ours"
    placed = meta(series="Orchard Tales", series_pos=1.0)
    overlay.apply_to(placed, source(series="Orchard Tales", series_pos=3.0))
    assert placed.series_pos == 1.0


def test_a_person_sets_the_series_and_the_volume_or_clears_both() -> None:
    mine = meta(series="From the file", series_pos=4.0)
    overlay.apply_to(mine, hand(series="Mine", series_pos=7.0))
    assert (mine.series, mine.series_pos) == ("Mine", 7.0)
    cleared = meta(series="From the file", series_pos=4.0)
    overlay.apply_to(cleared, hand(series=""))
    assert (cleared.series, cleared.series_pos) == ("", None)
    volume_only = meta(series="From the file", series_pos=4.0)
    overlay.apply_to(volume_only, hand(series_pos=9.0))
    assert (volume_only.series, volume_only.series_pos) == ("From the file", 9.0)


# ---- Recordings and covers --------------------------------------------------------------------------------------


def test_a_source_decides_the_kind_of_recording_only_where_the_files_did_not() -> None:
    guessed = meta(kind="audio", variant="unabridged", uncertain=True)
    overlay.apply_to(guessed, source(variant="abridged"))
    assert (guessed.variant, guessed.uncertain) == ("abridged", False)
    said = meta(kind="audio", variant="dramatized", uncertain=False)
    overlay.apply_to(said, source(variant="abridged"))
    assert (said.variant, said.uncertain) == ("dramatized", False)
    overlay.apply_to(said, hand(variant="unabridged"))
    assert said.variant == "unabridged"
    book = meta()
    overlay.apply_to(book, source(variant="abridged"))
    assert book.variant == "", "an e-book has no kind of recording"


def test_a_cover_of_a_source_only_where_there_is_none_with_its_colour() -> None:
    bare = meta()
    overlay.apply_to(bare, overlay.with_source(None, {"cover": "abc.webp"}, cover={"color": "#112233"}))
    assert (bare.cover, bare.color) == ("abc.webp", "#112233")
    covered = meta(cover="own.webp", color="#445566")
    overlay.apply_to(covered, overlay.with_source(None, {"cover": "abc.webp"}, cover={"color": "#112233"}))
    assert (covered.cover, covered.color) == ("own.webp", "#445566")


# ---- Whatever is stored -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("stored", [None, "text", 3, [], {"genre": "krimi"}, {"genre": {"v": "krimi"}}, {"genre": {"v": "krimi", "by": "me"}},
                                    {"nonsense": {"v": 1, "by": "hand"}}, {"genre": {"by": "hand"}}])
def test_a_stored_overlay_that_is_damaged_changes_nothing(stored: Any) -> None:
    said = meta(genre="roman")
    overlay.apply_to(said, stored)
    assert said.genre == "roman" and said.title == "The Quiet Orchard"
    assert overlay.with_source(stored, {"description": "x"})["description"]["v"] == "x"
    assert not overlay.has_source(stored)


def test_empty_values_are_never_stored_as_a_sources_answer() -> None:
    assert overlay.with_source(None, {"description": "", "genre": None, "narrators": [], "year": 0, "series_pos": None}) == {}
