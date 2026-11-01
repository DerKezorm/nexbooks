"""A card about the running time that has no running time of a catalogue to show gives a rough one from the pages of the
book: 1.7 minutes a page. It decides nothing. Only invented books."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update

from app.db import SessionLocal
from app.models import EDITION_EBOOK, Edition
from app.services.procure import checking, estimate, importing

from .filing_helpers import audio_wish, download_roots, finish, recording
from .procure_fakes import FakeNetwork
from .procure_search_helpers import TITLE


def a_question(net: FakeNetwork, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtimes: dict[str, list[int]]) -> Any:
    """A recording nothing proves the version of, so that the check asks about its running time."""
    roots = download_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(importing, "_runtimes", lambda *_args: {"unabridged": [], "abridged": [], "dramatized": [],
                                                                checking.UNSAID: [], **runtimes})
    world, sab, job = audio_wish(net)
    folder = roots / "complete" / "release"
    folder.mkdir(parents=True)
    recording(folder, parts=2, seconds=4.0, album=TITLE)
    finish(sab, job, folder)
    return world


def pages_are(pages: int) -> None:
    with SessionLocal() as db:
        db.execute(update(Edition).where(Edition.kind == EDITION_EBOOK).values(pages=pages))
        db.commit()


def card(world: Any) -> dict[str, Any]:
    [found] = world.client.get("/api/v1/procure/problems").json()["problems"]
    assert found["code"] == "runtime"
    return found["values"]  # type: ignore[no-any-return]


def test_the_card_names_a_time_from_the_pages_of_the_e_book_of_the_work(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    world = a_question(net, tmp_path, monkeypatch, {})
    pages_are(400)
    values = card(world)
    assert values["reason"] == "unproven"
    assert values["pages"] == 400 and values["estimate"] == 400 * estimate.SECONDS_PER_PAGE == 40800
    assert "unabridged" not in values


def test_a_reader_who_may_ask_for_books_sees_it_too(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    world = a_question(net, tmp_path, monkeypatch, {})
    pages_are(250)
    assert world.person.role != "operator"
    assert card(world)["estimate"] == 250 * estimate.SECONDS_PER_PAGE


def test_a_time_a_catalogue_gives_leaves_no_estimate(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    world = a_question(net, tmp_path, monkeypatch, {"unabridged": [10]})
    pages_are(400)
    values = card(world)
    assert values["unabridged"] == 10 and values["reason"] == "shorter"
    assert "pages" not in values and "estimate" not in values


def test_without_pages_the_card_has_no_estimate(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    world = a_question(net, tmp_path, monkeypatch, {})
    pages_are(0)
    values = card(world)
    assert "pages" not in values and "estimate" not in values


def test_the_estimate_changes_nothing_in_the_check() -> None:
    from .test_procure_version_proof import recording as unit_of
    from .test_procure_version_proof import wanted

    verdict = checking.judge_units([unit_of(129)], wanted({}))
    assert (verdict.outcome, verdict.code, verdict.values["reason"]) == ("ask", "runtime", "unproven")
    assert "estimate" not in verdict.values and "pages" not in verdict.values


def test_a_book_of_the_sources_without_an_e_book_of_its_own_gives_the_pages_the_sources_name(operator: Any) -> None:
    from types import SimpleNamespace

    from app.services.discover import foreign
    from app.services.sources.model import Candidate

    with SessionLocal() as db:
        book = foreign.upsert(db, Candidate(source="apple", key="k1", title="Der Nebelweg", authors=["Mara Lind"],
                                            languages=["de"], pages=300), "ebook")
        db.commit()
        row = SimpleNamespace(work_id=None, foreign_id=book.id, kind="audio")
        assert estimate.pages_of(db, row, 1) == 300  # type: ignore[arg-type]
        assert estimate.for_card(db, row, 1, {"loaded": 3000}) == {"pages": 300, "estimate": 300 * 102}  # type: ignore[arg-type]
        assert estimate.for_card(db, row, 1, {"unabridged": 900}) == {}  # type: ignore[arg-type]
        row.kind = "ebook"
        assert estimate.for_card(db, row, 1, {}) == {}  # type: ignore[arg-type]


def test_the_pages_of_an_e_book_the_person_does_not_see_are_not_used(operator: Any, net: FakeNetwork, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from .conftest import make_account

    world = a_question(net, tmp_path, monkeypatch, {})
    pages_are(400)
    stranger = make_account("nora")
    row = SimpleNamespace(work_id=world.work_id, foreign_id=None, kind="audio")
    with SessionLocal() as db:
        assert estimate.pages_of(db, row, world.person.id) == 400  # type: ignore[arg-type]
        assert estimate.pages_of(db, row, stranger.id) == 0  # type: ignore[arg-type]
