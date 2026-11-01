"""A world for wishes and following the downloads: the world of the search (one invented book, a person who may
ask for books, an indexer whose feed the test writes) plus a SABnzbd or a qBittorrent the releases go to. Only invented
names, example.com, keys made at run time."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download, ProcureWish
from app.services.procure import planner, tracking

from .procure_fakes import FakeNetwork, FakeQbittorrent, FakeSabnzbd
from .procure_search_helpers import TORRENT_HOST, FeedIndexer, Item, World, add_indexer, add_program, make_world

AUDIO = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2021-EXAMPLE"
BOOK = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"
BAD_AUDIO = "Rune Falkner-Die Laternenstrasse-Hoerspiel-DE-2021-EXAMPLE"
SECOND_AUDIO = "Rune Falkner-Die Laternenstrasse-MP3CD-DE-2022-OTHERGRP"


def sabnzbd(net: FakeNetwork) -> FakeSabnzbd:
    key = secrets.token_hex(16)
    fake = net.add(FakeSabnzbd("sab.example.com", key))
    add_program("sabnzbd", "sab.example.com", secret=key)
    return fake  # type: ignore[no-any-return]


def qbittorrent(net: FakeNetwork) -> FakeQbittorrent:
    user, password = "nexbooks", secrets.token_hex(8)
    fake = net.add(FakeQbittorrent("qbit.example.com", user, password))
    add_program("qbittorrent", "qbit.example.com", username=user, secret=password)
    return fake  # type: ignore[no-any-return]


def audio_world(net: FakeNetwork, *items: Item) -> World:
    """The world, and an indexer that offers these audiobooks (a good one when none is given)."""
    world = make_world(net)
    world.indexer.items = list(items) or [Item(AUDIO, size=400_000_000, categories=(3130,))]
    return world


def torrent_world(net: FakeNetwork, name: str = AUDIO, **extra: Any) -> tuple[World, FeedIndexer, str]:
    """The world with a torrent tracker that offers one audiobook; the info hash of its file."""
    from .test_procure_loading import torrent_file

    world = make_world(net)
    world.indexer.items = []
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    content, info_hash = torrent_file()
    tracker.items = [Item(name, size=400_000_000, categories=(3130,), seeders=20, **extra)]
    guid = hashlib.sha256(name.encode()).hexdigest()[:16]
    tracker.files[guid] = httpx.Response(200, content=content, headers={"content-type": "application/x-bittorrent"})
    return world, tracker, info_hash


def wish(world: World, *, kinds: tuple[str, ...] = ("audio",), language: str | None = "de", expect: int = 201,
         **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"items": [{"work_id": world.work_id}], "kinds": list(kinds), **body}
    if language:
        payload["language"] = language
    answer = world.client.post("/api/v1/procure/wishes", json=payload)
    assert answer.status_code == expect, answer.text
    return answer.json()  # type: ignore[no-any-return]


def wishes_of(world: World) -> list[dict[str, Any]]:
    answer = world.client.get("/api/v1/procure/wishes")
    assert answer.status_code == 200, answer.text
    return answer.json()["wishes"]  # type: ignore[no-any-return]


def rows(model: Any) -> list[Any]:
    with SessionLocal() as db:
        found = list(db.scalars(select(model).order_by(model.id)))
        for row in found:
            db.expunge(row)
        return found


def downloads() -> list[Download]:
    return rows(Download)  # type: ignore[no-any-return]


def wish_rows() -> list[ProcureWish]:
    return rows(ProcureWish)  # type: ignore[no-any-return]


def round_of_tracking() -> None:
    """One round of following the downloads, as the loop of the server does."""
    tracking.reset()
    tracking.run_round()


def planner_tick() -> dict[str, int] | None:
    """One tick of the planner, as the loop of the server does (the hourly housekeeping runs in it too)."""
    planner.reset()
    return planner.tick()


def download_folder(tmp: Path, name: str = "complete") -> Path:
    folder = tmp / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder
