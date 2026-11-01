"""A recording after the download when nothing proves which reading it is (Audible is off from the start, so there is
often no running time to hold it against): it is not filed as the unabridged one on a guess, a person decides. A word
in the names, a running time from the catalogue, a choice of the operator or the person's own answer settle it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import BlocklistEntry, Download
from app.services.match import overlay as match_overlay
from app.services.procure import formats, importing

from .filing_helpers import (
    audio_wish,
    download_roots,
    editions,
    files_in,
    finish,
    history,
    library_path,
    recording,
    the_download,
)
from .follow_helpers import rows
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def mp3s(world: Any) -> list[str]:
    return [name for name in files_in(library_path(world)) if name.endswith(".mp3")]


def test_a_recording_nothing_proves_waits_for_a_person_and_is_not_filed(operator: Any, net: FakeNetwork,
                                                                         roots: Path) -> None:
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=3, seconds=20.0, album=TITLE)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "runtime", (row.state, row.problem_code)
    assert row.problem_values["reason"] == "unproven" and 55 <= row.problem_values["loaded"] <= 65
    assert "unabridged" not in row.problem_values and "abridged" not in row.problem_values
    assert not mp3s(world) and not [edition for edition in editions() if edition.kind == "audio"]
    shown = world.client.get("/api/v1/procure/problems").json()["problems"]
    assert [item["code"] for item in shown] == ["runtime"] and shown[0]["values"]["reason"] == "unproven"


def test_the_person_keeps_it_as_the_unabridged_reading(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=3, seconds=20.0, album=TITLE)
    finish(sab, job, folder)
    row = the_download()
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "keep"})
    assert answer.status_code == 200, answer.text
    run_jobs()
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert (audio.variant, audio.variant_uncertain) == ("unabridged", False)
    assert history()[-1].note.get("variant") == "unabridged"


def test_the_person_blocks_it_and_the_search_goes_on(operator: Any, net: FakeNetwork, roots: Path) -> None:
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=3, seconds=20.0, album=TITLE)
    finish(sab, job, folder)
    row = the_download()
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "block"})
    assert answer.status_code == 200, answer.text
    assert rows(Download)[0].state == "removed" and not mp3s(world)
    [block] = rows(BlocklistEntry)
    assert block.reason == "abridged"


def test_a_word_in_the_names_settles_it_without_a_question(operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=3, seconds=20.0, album=f"{TITLE} (ungekürzt)")
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)
    [audio] = [edition for edition in editions() if edition.kind == "audio"]
    assert (audio.variant, audio.variant_uncertain) == ("unabridged", False)


def test_a_running_time_from_the_catalogue_settles_it_without_a_question(operator: Any, net: FakeNetwork,
                                                                         roots: Path, world: Any) -> None:
    from .fake_sources import audible_product

    world.fake.audible = {"de": [audible_product("B000000001", TITLE, [AUTHOR], 1)]}
    _people, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=3, seconds=20.0, album=TITLE)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)


def test_what_the_check_proved_goes_into_the_overlay() -> None:
    """The overlay is all that is left of it the next time the book is put together (``catalog.assign``)."""
    row = SimpleNamespace(decision={"_verdict": {"variant": "abridged", "proven": True}})
    edition = SimpleNamespace(kind="audio", overlay=None, variant="unabridged", variant_uncertain=True)
    importing._variant(None, row, edition)
    assert (edition.variant, edition.variant_uncertain) == ("abridged", False)
    assert match_overlay.is_guess(edition.overlay, "variant")


def test_what_the_check_proved_outweighs_what_the_files_say() -> None:
    meta = SimpleNamespace(kind="audio", variant="unabridged", uncertain=False)
    match_overlay.apply_to(meta, match_overlay.with_guess(None, "variant", "abridged"))
    assert (meta.variant, meta.uncertain) == ("abridged", False)


def test_an_operator_who_takes_abridged_recordings_is_not_asked(operator: Any, net: FakeNetwork, roots: Path) -> None:
    with SessionLocal() as db:
        config = formats.config(db)
        formats.save(db, {**config, "variant": "prefer", "abridged": True})
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "Release"
    recording(folder, parts=3, seconds=20.0, album=TITLE)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "imported", (row.state, row.problem_code, row.problem_values)
    assert mp3s(world)
