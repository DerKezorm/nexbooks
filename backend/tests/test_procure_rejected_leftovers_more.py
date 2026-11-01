"""The sweep of what a turned-down release left: what must stay also when a folder only looks like the job's own, and a
round that always gets to the end of the list. Only invented books."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import Download
from app.services.procure import arrival, leftovers

from .filing_helpers import download_roots, ebook_world, finish, german_epub, the_download
from .follow_helpers import round_of_tracking
from .procure_fakes import FakeNetwork


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def turned_down_release(net: FakeNetwork, roots: Path) -> tuple[Path, Any]:
    _world, sab, job = ebook_world(net)
    with SessionLocal() as db:
        title = db.query(Download).one().title
    folder = roots / "complete" / title
    german_epub(folder, authors=("Ida Brandt",), title="Ein ganz anderes Buch")
    finish(sab, job, folder)
    assert the_download().state == "rejected"
    round_of_tracking()
    assert the_download().removal_pending is False
    return folder, sab


def books_in(folder: Path) -> list[Path]:
    return [path for path in folder.rglob("*.epub")]


def another(first: Download, **values: Any) -> Download:
    names = ("user_id", "library_id", "kind", "language", "title", "protocol", "created_at", "updated_at", "client_id",
             "client_name", "state", "source_path")
    return Download(**{**{name: getattr(first, name) for name in names}, "client_ref": "another", **values})


def test_a_library_below_the_folder_stays(operator: Any, net: FakeNetwork, roots: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _sab = turned_down_release(net, roots)
    library = folder / "Regal"
    library.mkdir()
    (library / "buch.epub").write_bytes(b"x")
    monkeypatch.setattr(arrival, "library_folders", lambda: [library.resolve()])
    leftovers.turned_down()
    assert (library / "buch.epub").exists()
    assert any(path.parent == folder for path in books_in(folder)), "nothing of the folder went"
    assert the_download().source_path == "", "it is looked at once"


def test_the_folder_of_the_data_below_it_stays(operator: Any, net: FakeNetwork, roots: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _sab = turned_down_release(net, roots)
    data = folder / "daten"
    data.mkdir()
    (data / "buch.epub").write_bytes(b"x")
    monkeypatch.setattr(arrival, "data_folder", lambda: data.resolve())
    leftovers.turned_down()
    assert (data / "buch.epub").exists()


def test_a_library_beside_the_folder_changes_nothing(operator: Any, net: FakeNetwork, roots: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _sab = turned_down_release(net, roots)
    beside = folder.parent / "Regal"
    beside.mkdir()
    monkeypatch.setattr(arrival, "library_folders", lambda: [beside.resolve()])
    leftovers.turned_down()
    assert not folder.exists()


def test_the_folder_of_the_category_stays_whatever_its_name(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    leftovers.turned_down(categories={folder.resolve()})
    assert books_in(folder)
    assert the_download().source_path == ""


def test_a_folder_called_like_the_category_stays(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.query(Download).one().category = folder.name.upper()
        db.commit()
    leftovers.turned_down()
    assert books_in(folder)


def test_the_sweep_asks_the_program_for_the_folder_of_its_category(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, sab = turned_down_release(net, roots)
    sab.misc["complete_dir"] = str(folder.parent)
    sab.categories = [{"name": "nexbooks", "dir": folder.name}]
    leftovers.sweep()
    assert books_in(folder)


def test_a_program_that_does_not_answer_keeps_the_folder_for_the_next_round(operator: Any, net: FakeNetwork, roots: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure.clients import ClientError

    folder, _sab = turned_down_release(net, roots)

    def closed(_target: Any) -> Any:
        raise ClientError("unreachable")

    monkeypatch.setattr(leftovers, "open_client", closed)
    leftovers.sweep()
    assert books_in(folder)
    assert the_download().source_path != "", "it is looked at again"
    monkeypatch.undo()


def test_a_folder_another_release_lies_in_stays(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.add(another(db.query(Download).one(), title="Ida Brandt - Noch ein Buch", state="imported",
                       source_path=str(folder)))
        db.commit()
    leftovers.turned_down()
    assert books_in(folder)


def test_a_folder_with_another_release_in_a_folder_of_its_own_below_it_stays(operator: Any, net: FakeNetwork,
                                                                             roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.add(another(db.query(Download).one(), title="Ida Brandt - Noch ein Buch", state="imported",
                       source_path=str(folder / "Noch ein Buch")))
        db.commit()
    leftovers.turned_down()
    assert books_in(folder)


def test_the_same_release_turned_down_twice_is_no_other_release(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.add(another(db.query(Download).one(), state="rejected", source_path=str(folder)))
        db.commit()
    leftovers.turned_down()
    assert not folder.exists()


def test_rows_that_must_wait_do_not_hold_up_the_round(operator: Any, net: FakeNetwork, roots: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _sab = turned_down_release(net, roots)
    monkeypatch.setattr(leftovers, "TURNED_DOWN_PER_SWEEP", 2)
    with SessionLocal() as db:
        first = db.query(Download).one()
        last = first.id
        # Two rows with a lower number whose release is on its way again, and the row that may go last.
        first.id = last + 10
        db.flush()
        for number in (1, 2):
            db.add(another(first, id=number, title=f"Ida Brandt - Band {number}", state="rejected",
                           source_path=str(folder.parent / f"Band {number}")))
            db.add(another(first, id=number + 100, title=f"Ida Brandt - Band {number}", state="downloading",
                           source_path="", client_ref=f"job{number}"))
        db.commit()
    leftovers.turned_down()
    assert not folder.exists()


def test_after_the_filing_a_library_below_the_folder_of_the_job_stays(operator: Any, net: FakeNetwork, roots: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procure import importing

    folder = roots / "complete" / "Mara Lind - Die Nebelbahn - GERMAN"
    library = folder / "Regal"
    library.mkdir(parents=True)
    (library / "brief.nfo").write_text("x", encoding="utf-8")
    (folder / "release.nfo").write_text("x", encoding="utf-8")
    monkeypatch.setattr(arrival, "library_folders", lambda: [library.resolve()])
    row = Download(id=1, title=folder.name, source_path=str(folder), protocol="usenet")
    with SessionLocal() as db:
        importing._tidy_job_folder(db, row, None)
    assert (folder / "release.nfo").exists() and (library / "brief.nfo").exists()
