"""More of the filing: a recording never goes into a folder that is there already, only who may ask for books
in the library is told that a place was carried over, and a file of an upload may be larger than an ordinary request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Download, ProcureNotice, Progress

from .bookmaker import make_epub, make_pdf
from .conftest import make_account
from .filing_helpers import (
    audio_wish,
    download_roots,
    editions,
    files_in,
    finish,
    german_epub,
    library_path,
    recording,
    the_download,
    world_with,
)
from .follow_helpers import BOOK, rows, sabnzbd, wish
from .library_helpers import join, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE, Item

PLACED = "Rune Falkner/Nebelchor/02 - Die Laternenstraße"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_recording_never_goes_into_a_folder_that_is_there_already(operator: Any, net: FakeNetwork,
                                                                    roots: Path) -> None:
    world, sab, job = audio_wish(net)
    taken = library_path(world) / PLACED
    taken.mkdir(parents=True)
    (taken / "notes.txt").write_text("somebody's own", encoding="utf-8")
    folder = roots / "complete" / "rec"
    recording(folder, parts=1)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    names = files_in(library_path(world))
    assert f"{PLACED} (2)/01.mp3" in names and f"{PLACED}/notes.txt" in names
    assert not any(name.startswith(f"{PLACED}/") and name.endswith(".mp3") for name in names)


def test_only_who_may_ask_for_books_there_is_told_that_a_place_was_carried_over(operator: Any, net: FakeNetwork,
                                                                                roots: Path) -> None:
    sab = sabnzbd(net)
    world = world_with(net, lambda folder: make_pdf(folder / "falkner" / "laternenstrasse.pdf", title=TITLE,
                                                    author=AUTHOR, pages=5))
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    reader = make_account("rea")
    join(world.library_id, reader.id, "read")
    [pdf] = editions()
    with SessionLocal() as db:
        for person in (world.person.id, reader.id):
            db.add(Progress(user_id=person, edition_id=pdf.id, locator="page:2", fraction=0.4, unit="page",
                            moved_at=clock.now(), received_at=clock.now()))
        db.commit()
    wish(world, kinds=("ebook",), language="de")
    run_jobs()
    folder = roots / "complete" / BOOK
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, the_download().client_ref, folder)
    with SessionLocal() as db:
        assert [row.user_id for row in db.scalars(select(ProcureNotice))] == [world.person.id]
        # The reader's place is carried over all the same.
        place = db.get(Progress, (reader.id, pdf.id))
        assert place is not None and place.locator == "" and place.fraction == pytest.approx(0.4)


def test_a_file_of_an_upload_may_be_larger_than_an_ordinary_request(operator: Any, net: FakeNetwork) -> None:
    world = world_with(net, lambda folder: make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch",
                                                     authors=("Ida Brandt",), language="de"))
    upload = world.client.post("/api/v1/uploads", json={"library_id": world.library_id}).json()
    big = b"\0" * (17 * 1024 * 1024)
    answer = world.client.put(f"/api/v1/uploads/{upload['id']}/files", params={"path": "Teil 1.mp3"}, content=big,
                              headers={"content-type": "application/octet-stream"})
    assert answer.status_code == 201, answer.text
    # An ordinary request is still held to its limit.
    refused = world.client.post("/api/v1/procure/wishes", content=big, headers={"content-type": "application/json"})
    assert refused.status_code == 413
    assert rows(Download)[0].size == len(big)


def test_a_finished_download_is_filed_at_once_not_at_the_next_tidying(operator: Any, net: FakeNetwork,
                                                                     roots: Path) -> None:
    from app.services.procure import tracking

    from .filing_helpers import ebook_world

    _world, sab, job = ebook_world(net)
    folder = roots / "complete" / "r"
    folder.mkdir(parents=True)
    german_epub(folder)
    sab.finish(job, str(folder))
    tracking.reset()
    # The hourly tidying (which also asks for a lost filing) is not due in this round.
    tracking._last_tidy = tracking.clock()
    tracking.run_round()
    run_jobs()
    assert the_download().state == "imported"


def test_an_upload_too_large_by_its_length_is_refused_before_a_byte_is_read(operator: Any, net: FakeNetwork,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import uploads

    world = world_with(net, lambda folder: make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch",
                                                     authors=("Ida Brandt",), language="de"))
    upload = world.client.post("/api/v1/uploads", json={"library_id": world.library_id}).json()
    monkeypatch.setattr(uploads, "MAX_EBOOK_BYTES", 10)
    with SessionLocal() as db, pytest.raises(Exception) as refused:
        uploads.prepare(db, world.person, upload["id"], "big.epub", 11)
    assert getattr(refused.value, "status_code", None) == 413
    with SessionLocal() as db:
        target, allowed = uploads.prepare(db, world.person, upload["id"], "fits.epub", 10)
    assert allowed == 10 and not target.exists()


def test_only_the_operator_gives_a_job_nobody_asked_for_to_a_wish(operator: Any, net: FakeNetwork) -> None:
    from app.services.procure import downloads as download_service

    world = world_with(net, lambda folder: german_epub(folder / "falkner", name="laternen.epub", language="en"))
    with SessionLocal() as db, pytest.raises(Exception) as refused:
        download_service.assign_foreign(db, world.person, 1, 1)
    assert getattr(refused.value, "status_code", None) == 403


def test_a_filing_that_fails_for_good_is_a_problem_and_never_starts_over_by_itself(
        operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import Job
    from app.services.procure import importing

    from .filing_helpers import ebook_world

    world, sab, job = ebook_world(net)
    folder = roots / "complete" / "r"
    folder.mkdir(parents=True)
    german_epub(folder)
    calls: list[int] = []
    real = importing.check

    def broken(*_args: Any) -> None:
        calls.append(1)
        raise RuntimeError("a fault of nexbooks")

    monkeypatch.setattr(importing, "check", broken)
    finish(sab, job, folder)
    row = the_download()
    assert row.state == "problem" and row.problem_code == "place_failed"
    assert len(calls) == 5
    with SessionLocal() as db:
        assert db.scalar(select(Job.id).where(Job.kind == "pimport", Job.state == "queued")) is None
    # "Erneut prüfen" lets it go on, once it works again.
    monkeypatch.setattr(importing, "check", real)
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/recheck")
    assert answer.status_code == 200, answer.text
    run_jobs()
    assert the_download().state == "imported"
