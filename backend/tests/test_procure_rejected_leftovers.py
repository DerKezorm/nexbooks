"""What stays of a release the check turned down: a program takes the job out of its history, the files stay in its
folder of finished jobs. nexbooks takes what it knows out of the job's own folder and nothing else. Only invented
books."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import Download
from app.services.procure import arrival, leftovers

from .filing_helpers import download_roots, ebook_world, finish, german_epub, the_download
from .follow_helpers import round_of_tracking
from .library_helpers import can_link_files, make_link
from .procure_fakes import FakeNetwork


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def turned_down_release(net: FakeNetwork, roots: Path) -> tuple[Path, Any]:
    """A release of another author: the check turns it down and the job leaves the program. Returns its folder."""
    _world, sab, job = ebook_world(net)
    with SessionLocal() as db:
        title = db.query(Download).one().title
    folder = roots / "complete" / title
    german_epub(folder, authors=("Ida Brandt",), title="Ein ganz anderes Buch")
    (folder / "release.nfo").write_text("nfo", encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub" / "cover.jpg").write_bytes(b"x")
    finish(sab, job, folder)
    assert the_download().state == "rejected"
    round_of_tracking()
    assert the_download().removal_pending is False
    return folder, sab


def test_the_files_of_a_release_turned_down_go_with_its_folder(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    assert any(path.suffix == ".epub" for path in folder.iterdir()), "the program leaves them"
    assert leftovers.turned_down() >= 3
    assert not folder.exists()
    assert the_download().source_path == ""


def test_what_nexbooks_does_not_know_stays(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    (folder / "brief.txt").write_text("von Hand", encoding="utf-8")
    leftovers.turned_down()
    assert sorted(path.name for path in folder.iterdir()) == ["brief.txt"]
    assert the_download().source_path == "", "it is looked at once"


def test_a_folder_that_is_not_the_jobs_own_stays(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    other = folder.parent / "Etwas ganz anderes"
    other.mkdir()
    (other / "fremd.epub").write_bytes(b"x")
    with SessionLocal() as db:
        db.query(Download).one().source_path = str(other)
        db.commit()
    leftovers.turned_down()
    assert (other / "fremd.epub").exists()


def test_a_folder_outside_the_download_roots_stays(operator: Any, net: FakeNetwork, roots: Path, tmp_path: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    outside = tmp_path / "anderswo" / folder.name
    outside.mkdir(parents=True)
    (outside / "fremd.epub").write_bytes(b"x")
    with SessionLocal() as db:
        db.query(Download).one().source_path = str(outside)
        db.commit()
    leftovers.turned_down()
    assert (outside / "fremd.epub").exists()


def test_a_folder_in_a_library_stays(operator: Any, net: FakeNetwork, roots: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _sab = turned_down_release(net, roots)
    monkeypatch.setattr(arrival, "in_a_library", lambda _path: True)
    leftovers.turned_down()
    assert any(path.suffix == ".epub" for path in folder.iterdir())


def test_the_same_release_on_its_way_again_keeps_the_folder(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        first = db.query(Download).one()
        values = {name: getattr(first, name) for name in ("user_id", "library_id", "kind", "language", "title", "protocol",
                                                          "created_at", "updated_at", "client_id", "client_name")}
        db.add(Download(state="downloading", client_ref="another", wish_id=None, work_id=None, foreign_id=None, **values))
        db.commit()
    leftovers.turned_down()
    assert any(path.suffix == ".epub" for path in folder.iterdir())
    with SessionLocal() as db:
        assert db.query(Download).filter(Download.state == "rejected").one().source_path != ""


def test_a_job_still_in_its_program_is_left_alone(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.query(Download).one().removal_pending = True
        db.commit()
    leftovers.turned_down()
    assert any(path.suffix == ".epub" for path in folder.iterdir())


def test_a_torrent_keeps_its_files(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.query(Download).one().protocol = "torrent"
        db.commit()
    leftovers.turned_down()
    assert any(path.suffix == ".epub" for path in folder.iterdir())


def test_a_download_that_waits_for_a_person_keeps_its_files(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    with SessionLocal() as db:
        db.query(Download).one().state = "problem"
        db.commit()
    leftovers.turned_down()
    assert any(path.suffix == ".epub" for path in folder.iterdir())


@pytest.mark.skipif(not can_link_files(), reason="the system makes no links here")
def test_a_link_in_the_folder_is_never_followed(operator: Any, net: FakeNetwork, roots: Path, tmp_path: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    outside = tmp_path / "draussen"
    outside.mkdir()
    (outside / "fremd.epub").write_bytes(b"x")
    make_link(folder / "link", outside)
    leftovers.turned_down()
    assert (outside / "fremd.epub").exists() and os.path.lexists(folder / "link")


def test_the_sweep_does_it(operator: Any, net: FakeNetwork, roots: Path) -> None:
    folder, _sab = turned_down_release(net, roots)
    leftovers.sweep()
    assert not folder.exists()
