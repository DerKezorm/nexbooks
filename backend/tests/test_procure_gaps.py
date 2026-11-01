"""Guards of the wishes and the tracking: each of these fails when the guard it names is taken away."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.models import ForeignJob
from app.services.procure import leftovers

from .conftest import make_account, new_client
from .follow_helpers import AUDIO, audio_world, downloads, round_of_tracking, rows, sabnzbd, wish
from .library_helpers import join, make_link, run_jobs
from .procure_fakes import FakeNetwork
from .test_procure_tracking import loaded, seed_ready, seed_world


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The folder the download programs' jobs may be seen in."""
    base = tmp_path / "downloads"
    base.mkdir()
    monkeypatch.setattr(get_settings(), "download_roots", str(base))
    return base


def test_only_a_download_that_is_on_its_way_can_be_cancelled(operator: Any, net: FakeNetwork, roots: Path) -> None:
    qbit, info_hash, row, world = seed_world(net, roots)
    assert row.state == "imported"
    # Filed already: the book is on the shelf, the torrent is for "Seeden beenden".
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={"block": True})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "not_cancellable"
    assert info_hash in qbit.torrents and qbit.removed == []
    assert downloads()[0].state == "imported"


def test_a_failed_download_cannot_be_cancelled_a_second_time(operator: Any, net: FakeNetwork) -> None:
    world, sab, job = loaded(net)
    sab.fail(job)
    round_of_tracking()
    failed = [item for item in downloads() if item.state == "failed"]
    assert failed
    answer = world.client.post(f"/api/v1/procure/downloads/{failed[0].id}/cancel", json={"block": True})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "not_cancellable"


def test_a_torrent_whose_files_are_the_download_folder_itself_is_never_taken_away(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    # Taking it out with its files would take the folder every program of the server puts its jobs into.
    qbit, info_hash, _row = seed_ready(net, roots, roots)
    qbit.put(info_hash, ratio=2.0, seeding_time=999_999, state="stoppedUP", max_ratio=1.5)
    round_of_tracking()
    assert info_hash in qbit.torrents and qbit.removed == [] and roots.is_dir()


def test_a_job_the_hand_over_was_not_is_never_taken_for_it(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    stranger = sab.foreign_job("Completely.Different.Stuff.EPUB-EXAMPLE")
    world = audio_world(net)
    sab.take_then_timeout = True
    wish(world)
    run_jobs()
    [row] = downloads()
    assert row.state == "unsure" and row.client_ref == ""
    round_of_tracking()
    [row] = downloads()
    assert row.state == "queued" and row.client_ref != stranger and row.client_ref in sab.jobs
    [job] = rows(ForeignJob)
    assert job.ref == stranger


def leftover_world(net: FakeNetwork, roots: Path) -> tuple[Any, Path]:
    _world, sab, job = loaded(net)
    category = roots / "complete" / "nexbooks"
    category.mkdir(parents=True)
    sab.misc["complete_dir"] = str(roots / "complete")
    sab.categories.append({"name": "nexbooks", "dir": "nexbooks", "pp": "3"})
    sab.fail(job)
    return sab, category


def test_a_failed_folder_of_ours_that_holds_a_book_or_a_link_stays(operator: Any, net: FakeNetwork,
                                                                    roots: Path) -> None:
    _sab, category = leftover_world(net, roots)
    with_book = category / f"_FAILED_{AUDIO}"
    with_book.mkdir()
    (with_book / "book.m4b").write_bytes(b"x")
    precious = roots / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("keep", encoding="utf-8")
    with_link = category / f"_FAILED_{AUDIO[:30]}"
    with_link.mkdir()
    make_link(with_link / "lnk", precious)
    round_of_tracking()
    round_of_tracking()
    assert with_book.exists() and (with_book / "book.m4b").exists()
    assert with_link.exists() and (precious / "keep.txt").exists()


def test_a_failed_folder_is_ours_only_when_its_name_is_long_enough_to_say_which_release() -> None:
    names = [(7, "abcdefghijklmnopqrstuvwxyz")]
    assert leftovers.owner_of("_FAILED_abc", names) is None
    assert leftovers.owner_of("_FAILED_abcdefghijk", names) is None  # eleven letters
    assert leftovers.owner_of("_FAILED_abcdefghijkl", names) == 7  # twelve
    assert leftovers.owner_of("abcdefghijkl", names) is None


def test_the_person_who_looks_after_other_libraries_only_sees_no_history_of_this_one(
        operator: Any, net: FakeNetwork) -> None:
    from .follow_helpers import wish as ask
    from .library_helpers import books_folder, make_library

    sab = sabnzbd(net)
    world = audio_world(net)
    third = make_library(books_folder("third"), "Third")
    carl = make_account("carl")
    # Carl reads Books (so he sees the work) and may ask for books only in Third.
    join(world.library_id, carl.id, "read")
    join(third, carl.id, "acquire")
    ask(world)
    run_jobs()
    [row] = downloads()
    sab.fail(row.client_ref)
    round_of_tracking()
    assert world.client.get("/api/v1/procure/history").json()["entries"]
    with new_client(carl) as who:
        assert who.get("/api/v1/procure/history").json()["entries"] == []
        assert who.get("/api/v1/procure/blocklist").json()["entries"] == []
        assert who.get("/api/v1/procure/running").json()["downloads"] == []
