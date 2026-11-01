"""The storage card: sizes of the database and the backups, where the data folder lies, the free room."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.models import Account
from app.services import backups, mounts, space

from .conftest import make_account, new_client


@pytest.fixture(autouse=True)
def fresh_counts() -> None:
    space.forget()


def test_the_card_counts_the_database_and_the_backups(client: TestClient, operator: Account) -> None:
    backups.create(kind=backups.MANUAL)
    card = client.get("/api/v1/storage").json()
    assert card["backups"]["bytes"] > 0 and card["backups"]["files"] == 1 and card["backups"]["complete"] is True
    assert card["database"]["bytes"] >= get_settings().database_path.stat().st_size
    assert card["free_bytes"] > 0 and card["total_bytes"] >= card["free_bytes"]
    assert set(card) == {"database", "backups", "free_bytes", "total_bytes", "data"}, "no book is counted"


def test_a_database_on_a_network_share_is_said_so(client: TestClient, operator: Account,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mounts, "locate", lambda path, **_kw: mounts.Where("network", "nfs4"))
    card = client.get("/api/v1/storage").json()
    assert card["database"]["where"] == {"kind": "network", "fstype": "nfs4", "network": True}
    assert card["data"]["where"]["network"] is True
    monkeypatch.setattr(mounts, "locate", lambda path, **_kw: mounts.Where("local", "ext4"))
    card = client.get("/api/v1/storage").json()
    assert card["database"]["where"]["network"] is False and card["data"]["where"]["network"] is False


def test_a_count_is_kept_for_a_while_and_a_slow_one_says_it_did_not_finish(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "one.bin").write_bytes(b"x" * 10)
    calls = []
    real = space.count_folder

    def counting(path: Path, **kwargs: Any) -> tuple[int, int, bool]:
        calls.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(space, "count_folder", counting)
    space.folder_size(tmp_path, now=1000.0)
    space.folder_size(tmp_path, now=1000.0 + 299)
    assert len(calls) == 1
    space.folder_size(tmp_path, now=1000.0 + 301)
    assert len(calls) == 2
    assert space.count_folder(tmp_path, limit_seconds=-1.0)[2] is False


def test_a_link_in_a_folder_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 5000)
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "small.bin").write_bytes(b"y" * 10)
    try:
        (inside / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without the right to make symbolic links: a junction does the same without it.
        import subprocess

        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(inside / "link"), str(outside)], capture_output=True,
                              check=False) if os.name == "nt" else None
        if made is None or made.returncode != 0:
            pytest.skip("neither symbolic links nor junctions are possible here")
    assert space.count_folder(inside) == (10, 1, True)


def test_the_card_is_for_the_operator_in_a_browser_only(client: TestClient, operator: Account) -> None:
    with new_client(make_account("tom")) as member:
        assert member.get("/api/v1/storage").status_code == 403
    with new_client() as stranger:
        assert stranger.get("/api/v1/storage").status_code == 401
