"""The search for a better format: a book below the goal of the ranking is looked for in the time the operator
chose, goes on after a step that does not reach the goal, ends when the goal is reached (or lowered), and never takes a
format that is not ticked."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ProcureWish
from app.services.procure import formats, rss, wishes

from .auto_helpers import Moving, configure, searching_requests, tick
from .bookmaker import make_mobi, make_pdf
from .filing_helpers import download_roots, editions, finish, german_epub, the_download, world_with
from .follow_helpers import BOOK, downloads, sabnzbd, wish, wishes_of
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, TITLE, Item

MOBI = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.MOBI-EXAMPLE"
PDF = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.PDF-EXAMPLE"
OTHER = "Somebody.Else.-.Another.Book.GERMAN.EPUB-EXAMPLE"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def pdf_library(net: FakeNetwork, *titles: str) -> tuple[Any, Any]:
    """The book as a PDF in the library, a wish for a better German e-book, and an indexer with these releases."""
    sab = sabnzbd(net)
    world = world_with(net, lambda folder: make_pdf(folder / "falkner" / "laternenstrasse.pdf", title=TITLE,
                                                    author=AUTHOR, pages=5))
    world.indexer.items = [Item(title, size=2_000_000, categories=(7120,)) for title in titles]
    made = wish(world, kinds=("ebook",), language="de")
    assert made["results"][0]["status"] == "created", made
    run_jobs()
    return world, sab


def the_wish() -> ProcureWish:
    with SessionLocal() as db:
        row = db.scalar(select(ProcureWish))
        assert row is not None
        db.expunge(row)
        return row


def test_a_wish_for_a_better_format_goes_on_in_the_time_the_operator_chose(operator: Any, net: FakeNetwork,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60, retry_hours=3)
    world, _sab = pdf_library(net, OTHER)
    [shown] = wishes_of(world)
    assert shown["state"] == "upgrade" and shown["detail"]["have"] == "pdf" and shown["detail"]["goal"] == "epub"
    assert shown["next_try_at"] is not None
    moving = Moving(monkeypatch)
    asked = len(searching_requests(world.indexer))
    # The time was 6 hours when the wish was searched; the choice of 3 is for the next try after it.
    moving.pass_by(hours=2)
    with SessionLocal() as db:
        assert wishes.run_due(db) == 0
    assert len(searching_requests(world.indexer)) == asked
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    moving.pass_by(hours=5)
    with SessionLocal() as db:
        assert wishes.run_due(db) == 1
    run_jobs()
    assert len(searching_requests(world.indexer)) > asked
    assert wishes_of(world)[0]["state"] == "loading" and len(downloads()) == 1


def test_a_step_that_does_not_reach_the_goal_goes_on_until_it_does(operator: Any, net: FakeNetwork, roots: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    configure(rss_minutes=60)
    world, sab = pdf_library(net, MOBI)
    [row] = downloads()
    folder = roots / "complete" / "mobi"
    folder.mkdir(parents=True)
    make_mobi(folder / "Rune Falkner - Die Laternenstrasse.mobi", title=TITLE, author=AUTHOR)
    finish(sab, row.client_ref, folder)
    done = the_download()
    assert done.state == "imported", (done.state, done.problem_code, done.failed_reason)
    assert done.replaced is not None and done.replaced["from"] == "pdf" and done.replaced["to"] == "mobi"
    [shown] = wishes_of(world)
    assert shown["state"] == "upgrade", "MOBI is better than the PDF, but the goal is the EPUB"
    assert shown["detail"] == {"have": "mobi", "goal": "epub"} and shown["next_try_at"] is not None
    moving = Moving(monkeypatch)
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    moving.pass_by(hours=7)
    tick()
    run_jobs()
    assert wishes_of(world)[0]["state"] == "loading"
    second = [item for item in downloads() if item.state == "queued"]
    assert len(second) == 1 and second[0].title == BOOK
    folder = roots / "complete" / "epub"
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, second[0].client_ref, folder)
    assert wishes_of(world) == [], "the goal is reached: the wish is done and gone from the list"
    assert the_wish().state == "done"
    assert [edition.id for edition in editions()]


def test_when_the_goal_is_lowered_the_search_for_a_better_format_ends_and_the_chip_goes(
        operator: Any, net: FakeNetwork) -> None:
    world, _sab = pdf_library(net, OTHER)
    assert wishes_of(world)[0]["state"] == "upgrade"
    options = world.client.get("/api/v1/procure/book-options", params={"work_id": world.work_id}).json()
    assert [kind["state"] for kind in options["kinds"] if kind["kind"] == "ebook"] in (["wished"], ["upgrade"])
    with SessionLocal() as db:
        config = formats.config(db)
        config["ebook"] = [{"f": entry["f"], "on": True if entry["f"] == "pdf" else entry["on"]}
                           for entry in config["ebook"]]
        config["target"]["ebook"] = "pdf"
        formats.save(db, config)
    tick()
    assert wishes_of(world) == [] and the_wish().state == "done"
    options = world.client.get("/api/v1/procure/book-options", params={"work_id": world.work_id}).json()
    assert [kind["state"] for kind in options["kinds"] if kind["kind"] == "ebook"] == ["have"]


def test_a_format_that_is_not_ticked_is_never_an_upgrade_neither_by_search_nor_by_the_newest_entries(
        operator: Any, net: FakeNetwork) -> None:
    sab_world, sab = pdf_library(net, OTHER)
    sab_world.indexer.items = [Item(PDF, size=2_000_000, categories=(7120,)), Item(OTHER, size=1, categories=(7120,))]
    wish_id = wishes_of(sab_world)[0]["id"]
    assert sab_world.client.post(f"/api/v1/procure/wishes/{wish_id}/search").status_code == 200
    run_jobs()
    assert wishes_of(sab_world)[0]["state"] == "upgrade" and downloads() == []
    assert rss.sync() >= 1
    assert wishes_of(sab_world)[0]["state"] == "upgrade" and downloads() == [] and sab.jobs == {}


def test_the_wish_that_looks_for_a_better_format_is_not_filled_by_a_release_that_is_not_better(
        operator: Any, net: FakeNetwork) -> None:
    world, sab = pdf_library(net, OTHER)
    # An EPUB is better than the PDF: taken. A second PDF is not better: left.
    world.indexer.items = [Item(PDF, size=2_000_000, categories=(7120,))]
    rss.sync()
    assert downloads() == [] and sab.jobs == {}
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    with SessionLocal() as db:
        from app.models import Indexer

        db.query(Indexer).update({Indexer.rss_checked_at: None})
        db.commit()
    rss.sync()
    assert len(downloads()) == 1 and downloads()[0].title == BOOK


def test_a_new_time_between_tries_moves_the_next_try_of_an_upgrade_too(operator: Any, net: FakeNetwork) -> None:
    world, _sab = pdf_library(net, OTHER)
    configure(retry_hours=24)
    with SessionLocal() as db:
        assert wishes.retime(db) == 1
    row = the_wish()
    assert row.next_try_at == row.searched_at + timedelta(hours=24) and world is not None


def test_lowering_the_goal_ends_an_upgrade_that_reached_it_at_once(operator: Any, client: Any,
                                                                   net: FakeNetwork) -> None:
    """The goal lowered to what the book has, the wish for a better format ends with the save, not
    with the housekeeping of the planner an hour later (its card said "has PDF, looks for EPUB" until then)."""
    world, _sab = pdf_library(net, OTHER)
    assert wishes_of(world)[0]["state"] == "upgrade"
    body = {key: value for key, value in client.get("/api/v1/procure/formats").json().items()
            if key not in ("libraries", "known_ebook", "known_audio", "known_languages", "samples")}
    body["ebook"] = [{**item, "on": True} for item in body["ebook"]]
    body["target"] = {**body["target"], "ebook": "pdf"}
    assert client.put("/api/v1/procure/formats", json=body).status_code == 200
    assert the_wish().state == "done" and wishes_of(world) == []
    # The goal raised again: no wish comes back by itself (an upgrade is looked for only where somebody wished one).
    body["target"] = {**body["target"], "ebook": "epub"}
    assert client.put("/api/v1/procure/formats", json=body).status_code == 200
    assert the_wish().state == "done" and wishes_of(world) == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count(ProcureWish.id))) == 1
