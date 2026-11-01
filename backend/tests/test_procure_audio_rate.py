"""The bit rate of a recording, and what it makes of it in the ranking: a reading of 80 to 108 kbit/s is no thin 64 of the
format the operator left out, and what the tags take up is no sound. Only invented books."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import EDITION_AUDIO
from app.services.procure import checking, formats, judge
from app.services.procure import release_match as rm
from app.services.procure.release_name import parse
from app.services.readers import audio

from .bookmaker import make_mp3

MINUTE = 60.0


@pytest.mark.parametrize(("rate", "place"), [
    (32, "mp3_64"), (64, "mp3_64"), (72, "mp3_64"), (73, "mp3_128"), (80, "mp3_128"), (89, "mp3_128"), (94, "mp3_128"),
    (98, "mp3_128"), (106, "mp3_128"), (108, "mp3_128"), (111, "mp3_128"), (128, "mp3_128"), (159, "mp3_128"),
    (160, "mp3_192"), (255, "mp3_192"), (256, "mp3_320"),
])
def test_the_place_of_an_mp3_in_the_ranking(rate: int, place: str) -> None:
    assert judge.audio_format("mp3", rate) == place


def part(minutes: float, size_rate: int, *, rate: int = 0, name: str = "01.mp3") -> SimpleNamespace:
    seconds = minutes * MINUTE
    probe = {"format": "mp3", "duration": seconds, "album": "Die Nebelbahn (ungekürzt)", "title": "Die Nebelbahn",
             "authors": ["Mara Lind"], "language": "de"}
    if rate:
        probe["bitrate"] = rate
    return SimpleNamespace(path=name, format="mp3", size=int(seconds * size_rate * 1000 / 8), origin="source", unit="",
                           probe=probe)


def verdict_on(*files: SimpleNamespace) -> checking.Verdict:
    wanted = checking.Wanted(kind=EDITION_AUDIO, language="de", accepted=["de"], named_language="de",
                             subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                      "authors": ["Mara Lind"], "series": "", "position": None},
                             config=formats.default_config())
    return checking.judge_units(checking.units_of(list(files)), wanted)


@pytest.mark.parametrize("rate", [80, 89, 94, 98, 106, 108])
def test_a_reading_of_a_usual_rate_goes_in(rate: int) -> None:
    verdict = verdict_on(part(300, rate))
    assert verdict.outcome == "take" and verdict.fmt == "mp3_128"


def test_a_true_64_is_still_left_out_where_the_operator_left_it_out() -> None:
    verdict = verdict_on(part(300, 64))
    assert (verdict.outcome, verdict.code) == ("reject", "format_not_wanted")
    assert verdict.values["found"] == ["mp3_64"]


def test_a_cover_in_every_part_does_not_lift_a_64() -> None:
    assert verdict_on(part(300, 95, rate=64)).code == "format_not_wanted"


def test_the_parts_of_a_recording_count_by_their_length() -> None:
    files = [part(600, 64, rate=64, name="01.mp3"), part(6, 320, rate=320, name="02.mp3")]
    assert checking.measured_rate(files) == round((600 * 64 + 6 * 320) / 606)


def test_a_part_read_before_the_rate_was_kept_falls_back_on_the_sizes() -> None:
    files = [part(60, 96, rate=96, name="01.mp3"), part(60, 96, name="02.mp3")]
    assert checking.measured_rate(files) == 96


def test_a_name_that_says_nothing_is_worked_out_from_its_size() -> None:
    name = "Mara Lind - Die Nebelbahn - MP3 - GERMAN"
    book = rm.Book(titles=(rm.Title("Die Nebelbahn", "de"),), authors=("Mara Lind",))
    wanted = judge.Wanted(book=book, language="de", duration=int(600 * MINUTE))
    size = int(600 * MINUTE * 94 * 1000 / 8)
    offer = judge.Offer(title=name, size=size, published_at=None, protocol="usenet", category_kind="audio")
    found = judge.judge(parse(name), offer, rm.match(parse(name), book), wanted, formats.default_config(),
                        datetime(2026, 10, 30, tzinfo=UTC))
    assert found.format == "mp3_128" and not [item for item in found.reasons if item["code"] == "format_not_wanted"]


def test_the_rate_of_an_mp3_leaves_its_tags_out(tmp_path: Path) -> None:
    path: Any = make_mp3(tmp_path / "01.mp3", seconds=30.0, cover=bytes(400_000), album="Die Nebelbahn")
    with path.open("rb") as handle:
        probe = audio.read(handle, "mp3")
    by_size = path.stat().st_size * 8 / probe["duration"] / 1000
    assert by_size > 200 and 120 <= probe["bitrate"] <= 135
