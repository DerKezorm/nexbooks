"""The file of a release kept for a hand-over that comes again: where it is
kept, how much, how long, and that nothing a release says can lead out of the folder."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.services.procure import releasefiles


@pytest.fixture(autouse=True)
def clean_folder() -> None:
    for item in releasefiles.folder().iterdir():
        item.unlink()


def test_a_file_comes_back_as_it_was_put() -> None:
    key = releasefiles.key_of(3, "usenet", 1234, "Some Release Name")
    releasefiles.put(key, b"<nzb/>", "")
    assert releasefiles.get(key) == (b"<nzb/>", "")
    releasefiles.drop(key)
    assert releasefiles.get(key) is None
    assert list(releasefiles.folder().iterdir()) == []


def test_a_magnet_link_is_kept_as_a_magnet() -> None:
    key = releasefiles.key_of(4, "torrent", 99, "Another Release")
    releasefiles.put(key, b"", "magnet:?xt=urn:btih:" + "a" * 40)
    assert releasefiles.get(key) == (b"", "magnet:?xt=urn:btih:" + "a" * 40)


def test_the_name_of_the_file_is_a_hash_and_cannot_lead_out_of_the_folder() -> None:
    hostile = releasefiles.key_of(1, "usenet", 1, "../../../etc/passwd\x00\\..\\x")
    assert hostile.isalnum() and len(hostile) == 40
    releasefiles.put(hostile, b"x", "")
    names = {item.name for item in releasefiles.folder().iterdir()}
    assert names == {f"{hostile}.dat"}
    assert releasefiles.key_of(1, "usenet", 1, "A") != releasefiles.key_of(2, "usenet", 1, "A")
    assert releasefiles.key_of(1, "usenet", 1, "A") != releasefiles.key_of(1, "usenet", 2, "A")
    assert releasefiles.key_of(1, "usenet", 1, "A") != releasefiles.key_of(1, "torrent", 1, "A")


def test_a_file_that_is_too_big_is_not_kept() -> None:
    key = releasefiles.key_of(1, "usenet", 1, "Big")
    releasefiles.put(key, b"x" * (21 * 1024 * 1024), "")
    assert releasefiles.get(key) is None and list(releasefiles.folder().iterdir()) == []


def test_a_file_older_than_two_days_is_not_given_out_and_goes() -> None:
    key = releasefiles.key_of(1, "usenet", 1, "Old")
    releasefiles.put(key, b"<nzb/>", "")
    old = time.time() - releasefiles.MAX_AGE_SECONDS - 60
    os.utime(releasefiles.folder() / f"{key}.dat", (old, old))
    assert releasefiles.get(key) is None
    assert list(releasefiles.folder().iterdir()) == []


def test_prune_removes_the_old_and_then_the_oldest_until_the_folder_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releasefiles, "MAX_TOTAL", 3000)
    keys = [releasefiles.key_of(1, "usenet", number, "R") for number in range(5)]
    for number, key in enumerate(keys):
        releasefiles.put(key, b"x" * 1000, "")
        moment = time.time() - (5 - number) * 60
        os.utime(releasefiles.folder() / f"{key}.dat", (moment, moment))
    stale = releasefiles.key_of(1, "usenet", 99, "Stale")
    releasefiles.put(stale, b"x", "")
    os.utime(releasefiles.folder() / f"{stale}.dat", (1, 1))
    removed = releasefiles.prune()
    assert removed == 3, "the stale one and the two oldest"
    assert releasefiles.get(stale) is None
    assert [releasefiles.get(key) is not None for key in keys] == [False, False, True, True, True]


def test_prune_keeps_the_number_of_files_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releasefiles, "MAX_FILES", 3)
    for number in range(6):
        key = releasefiles.key_of(1, "usenet", number, "R")
        releasefiles.put(key, b"x", "")
        os.utime(releasefiles.folder() / f"{key}.dat", (time.time() - 600 + number, time.time() - 600 + number))
    assert releasefiles.prune() == 3
    assert len(list(releasefiles.folder().glob("*.dat"))) == 3


def test_a_disk_that_fails_costs_nothing_but_a_second_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Path, data: bytes) -> int:
        raise OSError("disk full")

    key = releasefiles.key_of(1, "usenet", 1, "R")
    with monkeypatch.context() as patched:
        patched.setattr(Path, "write_bytes", broken)
        releasefiles.put(key, b"x", "")
    assert releasefiles.get(key) is None


def test_the_hourly_housekeeping_of_the_planner_removes_old_release_files() -> None:
    from .auto_helpers import tick

    key = releasefiles.key_of(1, "usenet", 1, "Old")
    releasefiles.put(key, b"<nzb/>", "")
    old = time.time() - releasefiles.MAX_AGE_SECONDS - 60
    os.utime(releasefiles.folder() / f"{key}.dat", (old, old))
    fresh = releasefiles.key_of(1, "usenet", 2, "Fresh")
    releasefiles.put(fresh, b"<nzb/>", "")
    tick()
    assert sorted(item.name for item in releasefiles.folder().iterdir()) == [f"{fresh}.dat"]
