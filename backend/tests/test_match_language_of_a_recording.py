"""The language of a recording against the catalogues: the same book in another market is read in another language, and
what a person wished is not taken away by it. Only invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.match import apply, overlay, score
from app.services.sources.model import Candidate

from .filing_helpers import audio_wish, download_roots, editions, finish, recording, the_download
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE


def audible(language: str = "es", minutes: int = 838, **values: Any) -> Candidate:
    return Candidate(source="audible", key="B0SPANISH", title="La Nebelbahn", authors=["Mara Lind"], asin="B0SPANISH",
                     languages=[language], runtime_min=minutes, narrators=["Pilar Lectora"],
                     variant="unabridged", **values)


def edition(language: str = "", guess: bool = False) -> SimpleNamespace:
    flags = overlay.with_guess(None, "language", language) if guess else None
    return SimpleNamespace(kind="audio", language=language, overlay=flags, files=[])


def values_of(found: SimpleNamespace, scored: score.Scored, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(apply, "_file_value", lambda _edition, _name: "")
    return apply.build_values(found, scored, [])[0]  # type: ignore[arg-type]


def test_a_recording_that_is_not_proven_says_no_language(monkeypatch: pytest.MonkeyPatch) -> None:
    found = values_of(edition(), score.Scored(candidate=audible("en", 1976)), monkeypatch)
    assert "language" not in found


def test_the_recording_the_running_time_proves_says_its_language(monkeypatch: pytest.MonkeyPatch) -> None:
    found = values_of(edition(), score.Scored(candidate=audible("en"), proof=True), monkeypatch)
    assert found["language"] == "en"


def test_a_language_a_file_or_a_wish_says_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    found = values_of(edition("de"), score.Scored(candidate=audible(), proof=True), monkeypatch)
    assert "language" not in found


def test_an_ebook_still_takes_the_language_of_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    book = SimpleNamespace(kind="ebook", language="", overlay=None, files=[])
    source = Candidate(source="dnb", key="1", title="Die Nebelbahn", authors=["Mara Lind"], languages=["de"])
    assert values_of(book, score.Scored(candidate=source), monkeypatch)["language"] == "de"


def wanted(**values: Any) -> score.Wanted:
    return score.Wanted(kind="audio", title="La Nebelbahn", authors=["Mara Lind"], duration=838 * 60, **values)


def test_a_recording_in_another_language_than_the_one_wished_is_not_the_book() -> None:
    assert score.score(wanted(language="de"), audible()).fatal == "language_differs"


def test_a_recording_in_another_language_than_the_one_worked_out_needs_a_person() -> None:
    found = score.score(wanted(language="de", language_guess=True), audible())
    assert found.capped and not found.fatal and "language_missing" in found.why


def test_an_ebook_in_another_language_than_the_one_worked_out_is_no_reason_against_it() -> None:
    book = score.Wanted(kind="ebook", title="La Nebelbahn", authors=["Mara Lind"], language="de", language_guess=True)
    source = Candidate(source="dnb", key="1", title="La Nebelbahn", authors=["Mara Lind"], languages=["es"])
    assert not score.score(book, source).capped


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_recording_without_a_language_keeps_the_one_wished(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=2, seconds=4.0, album=f"{TITLE} (ungekürzt)", artist=AUTHOR)
    finish(sab, job, folder)
    assert the_download().state == "imported", the_download().problem_code
    [audio] = [item for item in editions() if item.kind == "audio"]
    assert audio.language == "de"
    assert "language" in overlay.hand_fields(audio.overlay) and audio.overlay["language"]["src"] == "wish"


def test_a_book_that_names_its_language_keeps_it_as_its_own(operator: Any, net: FakeNetwork, roots: Path) -> None:
    from .filing_helpers import ebook_world, german_epub

    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / "Release"
    german_epub(folder)
    finish(sab, job, folder)
    assert the_download().state == "imported", the_download().problem_code
    book = [item for item in editions() if item.kind == "ebook"][-1]
    assert book.language == "de" and "language" not in overlay.hand_fields(book.overlay)
