"""The author in the tags of a file against the author wished: tags are careless. The name turned around, a particle left
out, a word behind it, one word of it: none of that is another person, and a release is not turned down for it. Another
person stays another person. Only invented names."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import EDITION_AUDIO
from app.services.procure import checking, formats
from app.services.readers import names


@pytest.mark.parametrize(("name", "other"), [
    ("Falkner Rune", "Rune Falkner"),
    ("Rune, Falkner", "Rune Falkner"),
    ("Falkner, Rune", "Rune Falkner"),
    ("Mara Falkner 2", "Mara Falkner"),
    ("Mara Falkner Deutsch", "Mara Falkner"),
    ("Odile Saint-Marceau", "Odile de Saint-Marceau"),
    ("Alexander Marceau", "Alexandre Marceau"),
    ("Franz van Dorn", "Franz Dorn"),
])
def test_one_person_however_the_tag_writes_it(name: str, other: str) -> None:
    assert names.same_person_as_written(name, other)


@pytest.mark.parametrize(("name", "other"), [
    ("Rune Falkner", "Ralf Falkner"),
    ("Rune Falkner", "Rune Lindqvist"),
    ("Hedda Thorne", "Silas Marlowe"),
    ("Falkner", "Rune Falkner"),
    ("Falkner", "Falkner"),
    ("Anna Falkner", "Hanna Falkner Lind"),
    ("Johannes Falkner", "Johanna Falkner"),
])
def test_two_people_stay_two(name: str, other: str) -> None:
    assert not names.same_person_as_written(name, other)


@pytest.mark.parametrize(("name", "part"), [("Dale", True), ("Falkner", True), ("Dale 2", True), ("Rune Falkner", False),
                                            ("Hedda", False)])
def test_one_word_of_a_name(name: str, part: bool) -> None:
    assert names.part_of_a_name(name, "Rune Dale Falkner") is part


@pytest.mark.parametrize("label", ["VORL 1", "cd2", "Track 12", "Teil 3"])
def test_the_label_of_a_part_is_no_person(label: str) -> None:
    assert names.is_label(label)


@pytest.mark.parametrize("name", ["Rune Falkner", "J. R. R. Lindqvist", "Ada X", "Ludo XIV"])
def test_a_person_stays_a_person(name: str) -> None:
    assert not names.is_label(name)


def verdict_on(authors: list[str], wished: str = "Rune Falkner") -> checking.Verdict:
    item = SimpleNamespace(path="01.mp3", format="mp3", size=600 * 12000, origin="source", unit="",
                           probe={"format": "mp3", "duration": 600.0, "album": "Die Nebelbahn (ungekürzt)",
                                  "title": "Die Nebelbahn", "authors": authors, "language": "de"})
    wanted = checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de",
                             subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                      "authors": [wished], "series": "", "position": None},
                             config=formats.default_config())
    return checking.judge_units(checking.units_of([item]), wanted)


@pytest.mark.parametrize("authors", [["Falkner Rune"], ["Rune"], ["Falkner"], ["VORL 1"], ["Rune Falkner 2"],
                                     ["Rune, Falkner"], ["Rune Falkner Deutsch"]])
def test_a_careless_tag_turns_no_release_down(authors: list[str]) -> None:
    verdict = verdict_on(authors)
    assert verdict.outcome == "take", (authors, verdict.code)


def test_another_author_with_the_title_wished_is_still_turned_down() -> None:
    verdict = verdict_on(["Hedda Thorne"])
    assert (verdict.outcome, verdict.code) == ("reject", "author_mismatch")


#: First names that look alike and are other people: a letter added or left out at an end, or one in between. All of them
#: pairs a careless check would take for one spelling.
NEAR = [("Julia", "Julian"), ("Daniel", "Daniela"), ("Andrea", "Andreas"), ("Anna", "Hanna"), ("Jan", "Jana"),
        ("Mara", "Marla"), ("Sara", "Sarah"), ("Michael", "Michaela"), ("Christian", "Christina"), ("Paul", "Paula"),
        ("Karl", "Karla"), ("Johann", "Johanna"), ("Leon", "Leona"), ("Simon", "Simone"), ("Dominik", "Dominika"),
        ("Lukas", "Luka"), ("Luca", "Lucas"), ("Marc", "Marco"), ("Mark", "Marek"), ("Frank", "Franka"),
        ("Rene", "Renee"), ("Lena", "Lenna")]


@pytest.mark.parametrize(("first", "other"), NEAR)
def test_a_first_name_that_looks_alike_is_no_second_spelling(first: str, other: str) -> None:
    assert not names.same_person_as_written(f"{first} Falkner", f"{other} Falkner")
    assert not names.same_person_as_written(f"{other} Falkner", f"{first} Falkner")
    assert not names.authors_compatible(f"{first} Falkner", f"{other} Falkner")


@pytest.mark.parametrize(("first", "other"), NEAR)
def test_it_may_be_a_slip_of_the_keys_so_it_is_near(first: str, other: str) -> None:
    assert names.first_name_near(f"{first} Falkner", f"{other} Falkner")
    assert names.first_name_near(f"Falkner, {other}", f"{first} Falkner")


@pytest.mark.parametrize(("name", "other"), [
    ("Mara Falkner", "Mara Lindqvist"), ("Julia Falkner", "Julian Lindqvist"), ("Elias Falkner", "Elisa Falkner"),
    ("Tom Falkner", "Toni Falkner"),
    ("Rune Falkner", "Ralf Falkner"), ("Julia", "Julian"), ("Mara Falkner", "Mara Falkner"),
])
def test_what_is_not_near(name: str, other: str) -> None:
    assert not names.first_name_near(name, other)


@pytest.mark.parametrize(("name", "other"), [
    ("Stephan Falkner", "Stefan Falkner"), ("Eric Falkner", "Erik Falkner"), ("Alexander Falkner", "Alexandre Falkner"),
    ("Marc Falkner", "Mark Falkner"), ("Philipp Falkner", "Filipp Falkner"), ("R. Falkner", "Rune Falkner"),
    ("Rune K. Falkner", "Rune Falkner"), ("Lew Falkner", "Leo Falkner"),
])
def test_one_name_in_two_spellings_is_one_person(name: str, other: str) -> None:
    assert names.authors_compatible(name, other) or names.same_person_as_written(name, other)
    assert verdict_on([name], other).outcome == "take"


@pytest.mark.parametrize(("first", "other"), NEAR)
def test_no_file_with_a_name_that_looks_alike_goes_in_unasked(first: str, other: str) -> None:
    verdict = verdict_on([f"{first} Falkner"], f"{other} Falkner")
    assert (verdict.outcome, verdict.code) == ("ask", "pen_name"), (first, other, verdict.code)
    assert verdict.values["found"] == [f"{first} Falkner"]
    assert verdict.values["wanted"] == [f"{other} Falkner"]


def test_a_name_that_looks_alike_on_another_title_is_turned_down_as_before() -> None:
    item = SimpleNamespace(path="01.mp3", format="mp3", size=600 * 12000, origin="source", unit="",
                           probe={"format": "mp3", "duration": 600.0, "album": "Ein ganz anderes Buch (ungekürzt)",
                                  "title": "Ein ganz anderes Buch", "authors": ["Julian Falkner"], "language": "de"})
    wanted = checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de",
                             subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                      "authors": ["Julia Falkner"], "series": "", "position": None},
                             config=formats.default_config())
    verdict = checking.judge_units(checking.units_of([item]), wanted)
    assert (verdict.outcome, verdict.code) == ("reject", "author_mismatch")


def test_two_recordings_with_a_name_that_looks_alike_are_a_question_too() -> None:
    items = [SimpleNamespace(path=path, format="mp3", size=600 * 12000, origin="source", unit="",
                             probe={"format": "mp3", "duration": 600.0, "album": album, "title": album,
                                    "authors": ["Julian Falkner"], "language": "de"})
             for path, album in (("a/01.mp3", "Die Nebelbahn (ungekürzt)"), ("b/01.mp3", "Die Nebelbahn (gekürzt)"))]
    for item in items:
        item.unit = item.path.split("/")[0]
    wanted = checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de",
                             subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                      "authors": ["Julia Falkner"], "series": "", "position": None},
                             config=formats.default_config())
    verdict = checking.judge_units(checking.units_of(items), wanted)
    assert verdict.outcome == "ask"
