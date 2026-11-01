"""Which work a filed book goes into: the wished work; for a book of the sources a work of its own, one and
never two (an e-book and a recording of it filed one after the other); a torrent is linked and seeds on; two libraries
file the same book each for themselves; a job nobody asked for becomes a wish's download."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download, ExternalRef, ForeignBook, ForeignJob, Work

from .bookmaker import make_epub
from .filing_helpers import (
    download_roots,
    editions,
    files_in,
    finish,
    german_epub,
    library_path,
    place_files,
    recording,
    wishes,
    works,
    world_with,
)
from .follow_helpers import AUDIO, BOOK, qbittorrent, round_of_tracking, rows, sabnzbd, torrent_world, wish
from .library_helpers import can_link_files, run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, Item, allow_languages

TOKEN = "tok" + "f" * 13


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def other_book(folder: Path) -> None:
    make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch", authors=("Ida Brandt",), language="de")


def test_a_book_of_the_sources_gets_one_work_for_its_e_book_and_its_recording(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    sab = sabnzbd(net)
    world = world_with(net, other_book)
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,)),
                           Item(AUDIO, size=400_000_000, categories=(3130,))]
    with SessionLocal() as db:
        db.add(ForeignBook(token=TOKEN, ref="hardcover:77", title="Die Laternenstraße", authors=[AUTHOR],
                           language="de", series="Nebelchor", series_pos=2.0, released="2021"))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": ["ebook", "audio"],
                                                              "language": "de"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    loads = {row.kind: row for row in rows(Download)}
    assert set(loads) == {"ebook", "audio"}
    book_folder = roots / "complete" / "ebook"
    book_folder.mkdir(parents=True)
    german_epub(book_folder)
    finish(sab, loads["ebook"].client_ref, book_folder)
    audio_folder = roots / "complete" / "audio"
    recording(audio_folder, parts=2)
    finish(sab, loads["audio"].client_ref, audio_folder)
    done = {row.kind: row for row in rows(Download)}
    assert {row.state for row in done.values()} == {"imported"}, [(r.kind, r.state, r.failed_reason) for r in
                                                                   done.values()]
    titled = [work for work in works() if work.title.startswith("Die Laternen")]
    assert len(titled) == 1
    work_id = titled[0].id
    assert {edition.kind for edition in editions() if edition.work_id == work_id} == {"ebook", "audio"}
    assert done["ebook"].work_id == work_id and done["audio"].work_id == work_id
    # The EPUB is the goal of the ranking; the recording is an MP3 and the goal a FLAC: that wish goes on as an upgrade.
    assert {item.kind: item.state for item in wishes()} == {"ebook": "done", "audio": "upgrade"}
    assert {item.work_id for item in wishes()} == {work_id}
    with SessionLocal() as db:
        refs = list(db.scalars(select(ExternalRef).where(ExternalRef.source == "hardcover", ExternalRef.key == "77")))
        assert refs and all(ref.kind == "work" for ref in refs)
    # The year of the source names the folder.
    assert any(name.startswith("Rune Falkner/Nebelchor/02 - Die Laternenstraße (2021)")
               for name in files_in(library_path(world)))


def test_a_torrent_is_linked_into_the_library_and_seeds_on(operator: Any, net: FakeNetwork, roots: Path) -> None:
    qbit = qbittorrent(net)
    world, _tracker, info_hash = torrent_world(net)
    wish(world)
    run_jobs()
    folder = roots / "torrents" / "nexbooks" / "Some.Audiobook"
    files = recording(folder, parts=2)
    qbit.finish(info_hash, str(folder))
    round_of_tracking()
    run_jobs()
    [row] = rows(Download)
    assert row.state == "imported" and row.seed_state == "watching", (row.state, row.failed_reason)
    assert all(path.exists() for path in files)
    placed = [name for name in files_in(library_path(world)) if name.endswith(".mp3")]
    assert len(placed) == 2
    if can_link_files():
        target = library_path(world) / placed[0]
        assert target.stat().st_ino == files[0].stat().st_ino or target.samefile(files[0])
    assert [item.how for item in place_files()
            if item.role == "book"] in (["link", "link"], ["copy", "copy"])


def test_two_libraries_file_the_same_book_each_for_themselves(operator: Any, net: FakeNetwork, roots: Path) -> None:
    from .library_helpers import books_folder, join, make_library

    sab = sabnzbd(net)
    world = world_with(net, lambda folder: german_epub(folder / "falkner", name="laternen.epub", language="en"))
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,)),
                           Item(AUDIO.replace("2021", "2022"), size=400_000_000, categories=(3130,))]
    second_folder = books_folder("second")
    second = make_library(second_folder, "Second")
    join(second, world.person.id, "acquire")
    # Two libraries take recordings: each wish names its own.
    wish(world, kinds=("audio",), library_id=world.library_id)
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}], "kinds": ["audio"],
                                                              "language": "de", "library_id": second})
    assert reply.status_code == 201, reply.text
    run_jobs()
    loads = rows(Download)
    assert len(loads) == 2 and {row.library_id for row in loads} == {world.library_id, second}
    for number, row in enumerate(loads):
        folder = roots / "complete" / f"r{number}"
        recording(folder, parts=1)
        finish(sab, row.client_ref, folder)
    assert {row.state for row in rows(Download)} == {"imported"}
    assert [name for name in files_in(second_folder) if name.endswith(".mp3")]
    assert [name for name in files_in(library_path(world)) if name.endswith(".mp3")]
    with SessionLocal() as db:
        assert db.scalar(select(Work.id).where(Work.id != world.work_id)) is None


def test_a_job_nobody_asked_for_is_given_to_a_wish_by_the_operator_and_filed(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    from app.services.procure import automation

    from .conftest import new_client
    from .library_helpers import join, operator_account

    sab = sabnzbd(net)
    world = world_with(net, lambda folder: german_epub(folder / "falkner", name="laternen.epub", language="en"))
    world.indexer.items = []
    ops = operator_account()
    join(world.library_id, ops.id, "manage")
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "enabled": False})
    # German only: a recording whose files say no language is the book when its folder says German (with nothing that
    # says a language it would be a question, ``language_unknown``, whatever is allowed).
    allow_languages("de", original=False)
    made = wish(world, kinds=("audio",))
    wish_id = made["results"][0]["wish"]["id"]
    ref = sab.foreign_job("Something.Else")
    with SessionLocal() as db:
        from app.models import DownloadClient

        client_id = db.scalar(select(DownloadClient.id))
        db.add(ForeignJob(client_id=client_id, ref=ref, name="Something.Else", job_state="completed"))
        db.commit()
        job_id = db.scalar(select(ForeignJob.id))
    keeper = new_client(ops)
    refused = world.client.post(f"/api/v1/procure/foreign/{job_id}/assign", json={"wish_id": wish_id})
    assert refused.status_code == 403
    answer = keeper.post(f"/api/v1/procure/foreign/{job_id}/assign", json={"wish_id": wish_id})
    assert answer.status_code == 201, answer.text
    folder = roots / "complete" / "Something.Else"
    recording(folder / "Laternenstrasse - GERMAN", parts=1)
    sab.finish(ref, str(folder))
    round_of_tracking()
    run_jobs()
    [row] = rows(Download)
    assert row.state == "imported" and row.wish_id == wish_id, (row.state, row.problem_code, row.failed_reason)
    assert rows(ForeignJob) == []
