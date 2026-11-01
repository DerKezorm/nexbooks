"""nexbooks takes away only what is in its own category.

A program removes a job by its name: a torrent by its info hash, whatever category it is in. The same torrent may be
the operator's own (he seeds it in a category of his own) or another tool's, and a Usenet job may have been moved to
another category by hand. Before every removal the job is looked for in nexbooks's category; what is not there is left
as it is, with its files. Only invented names, example.com, keys made at run time."""

from __future__ import annotations

import secrets
from typing import Any

import httpx
import pytest

from app.services.procure.clients import open_client, remove_own

from .follow_helpers import downloads, qbittorrent, torrent_world, wish
from .library_helpers import run_jobs
from .procure_fakes import FakeDeluge, FakeNetwork, FakeTransmission
from .procure_search_helpers import add_program
from .test_procure_follow_clients import KINDS, TORRENT, Kit, make

USER = "reader" + secrets.token_hex(2)
SECRET = secrets.token_hex(12)


def _moved(kit: Kit) -> None:
    """The job of ``kit.ref`` is in another category now (moved by hand, or another's torrent of the same hash)."""
    fake = kit.fake
    if kit.kind == "sabnzbd":
        fake.jobs[kit.ref] = ("movies", "Some.Movie", b"")
        fake.put(kit.ref, where="queue", filename="Some.Movie", cat="movies", status="Downloading")
    elif kit.kind == "nzbget":
        fake.put(7, NZBName="Some.Movie", Status="DOWNLOADING", Category="movies")
    elif kit.kind == "qbittorrent":
        fake.put(kit.ref, name="Some.Movie", category="mine", state="uploading", progress=1.0)
    elif kit.kind == "transmission":
        fake.put(kit.ref, name="Some.Movie", labels=["movies"], downloadDir="/data/torrents/movies")
    else:
        fake.put(kit.ref, name="Some.Movie", label="movies")


def _still_there(kit: Kit) -> bool:
    fake = kit.fake
    if kit.kind == "sabnzbd":
        return kit.ref in fake.jobs
    if kit.kind == "nzbget":
        return any(item["NZBID"] == 7 for item in fake.groups + fake.history)
    return kit.ref in fake.torrents


@pytest.mark.parametrize("kind", KINDS)
def test_a_job_of_the_category_is_taken_away(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    kit.working(30)
    with open_client(kit.target) as client:
        assert client.holds(kit.ref)
        assert remove_own(client, kit.ref, delete_files=True) is True
    assert not _still_there(kit)


@pytest.mark.parametrize("kind", KINDS)
def test_a_job_in_another_category_is_never_taken_away(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    _moved(kit)
    with open_client(kit.target) as client:
        assert not client.holds(kit.ref)
        assert remove_own(client, kit.ref, delete_files=True) is False
    assert _still_there(kit), "a job outside nexbooks's category went"


@pytest.mark.parametrize("kind", KINDS)
def test_a_job_that_is_gone_is_no_failure(kind: str, net: FakeNetwork) -> None:
    kit = make(kind, net)
    with open_client(kit.target) as client:
        assert remove_own(client, kit.ref, delete_files=True) is False


def test_qbittorrent_looks_at_the_category_of_the_torrent_itself(net: FakeNetwork) -> None:
    """A program that would not filter by category (an old version, a proxy in between) still keeps another's torrent:
    nexbooks reads the category of the torrent, not only the filter it asked for."""
    kit = make("qbittorrent", net)
    _moved(kit)
    original = kit.fake.handle

    def no_filter(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/torrents/info":
            params = dict(request.url.params)
            params.pop("category", None)
            request = httpx.Request("GET", request.url.copy_with(params=params), headers=request.headers)
        return original(request)

    kit.fake.handle = no_filter
    with open_client(kit.target) as client:
        assert remove_own(client, kit.ref, delete_files=True) is False
    assert _still_there(kit)


# --- The whole way: an unsure hand-over that a person cancels -------------------------------------------------------


def _program(kind: str, net: FakeNetwork) -> Any:
    host = f"{kind}.example.com"
    if kind == "qbittorrent":
        return qbittorrent(net)
    if kind == "transmission":
        fake = net.add(FakeTransmission(host, USER, SECRET))
        add_program("transmission", host, username=USER, secret=SECRET)
        return fake
    fake = net.add(FakeDeluge(host, SECRET))
    fake.plugins.append("Label")
    fake.labels.append("nexbooks")
    add_program("deluge", host, secret=SECRET)
    return fake


ADD_PATHS = {"qbittorrent": "/api/v2/torrents/add"}
ADD_METHODS = {"transmission": "torrent-add", "deluge": "core.add_torrent_file"}


def _silent_add(kind: str, fake: Any) -> None:
    """The program takes nothing and does not answer the hand-over in time: the download is unsure."""
    import json

    original = fake.handle

    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == ADD_PATHS.get(kind):
            raise httpx.ReadTimeout("no answer", request=request)
        if kind in ADD_METHODS and request.content and json.loads(request.content).get("method") == ADD_METHODS[kind]:
            raise httpx.ReadTimeout("no answer", request=request)
        return original(request)

    fake.handle = slow


def _foreign_copy(kind: str, fake: Any, info_hash: str) -> None:
    """The operator seeds the same torrent himself, in a category of his own."""
    if kind == "qbittorrent":
        fake.put(info_hash, category="mine", state="uploading", progress=1.0, content_path="/downloads/mine/x")
    elif kind == "transmission":
        fake.put(info_hash, labels=["mine"], downloadDir="/data/torrents/mine", status=6, leftUntilDone=0)
    else:
        fake.put(info_hash, label="mine", state="Seeding", progress=100.0, is_finished=True)


def _removed(kind: str, fake: Any) -> list[Any]:
    return list(fake.removed)


@pytest.mark.parametrize("kind", TORRENT)
def test_cancelling_an_unsure_torrent_keeps_the_same_torrent_of_another_category(kind: str, operator: Any,
                                                                                net: FakeNetwork) -> None:
    fake = _program(kind, net)
    world, _tracker, info_hash = torrent_world(net)
    _foreign_copy(kind, fake, info_hash)
    _silent_add(kind, fake)
    wish(world, kinds=("audio",), language="de")
    run_jobs()
    [row] = downloads()
    assert row.state == "unsure" and row.client_ref.lower() == info_hash.lower(), (row.state, row.client_ref)
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={})
    assert answer.status_code == 200, answer.text
    assert _removed(kind, fake) == [], "the operator's own torrent was taken away"
    assert info_hash in fake.torrents
    [after] = downloads()
    assert after.state == "removed" and not after.removal_pending


@pytest.mark.parametrize("kind", TORRENT)
def test_blocking_an_unsure_torrent_keeps_the_same_torrent_of_another_category(kind: str, operator: Any,
                                                                              net: FakeNetwork) -> None:
    fake = _program(kind, net)
    world, _tracker, info_hash = torrent_world(net)
    _foreign_copy(kind, fake, info_hash)
    _silent_add(kind, fake)
    wish(world, kinds=("audio",), language="de")
    run_jobs()
    [row] = downloads()
    answer = world.client.post(f"/api/v1/procure/downloads/{row.id}/cancel", json={"block": True})
    assert answer.status_code == 200, answer.text
    assert _removed(kind, fake) == []
    assert info_hash in fake.torrents


def test_ending_the_seeding_of_a_torrent_moved_to_another_category_leaves_it(operator: Any, net: FakeNetwork,
                                                                            tmp_path: Any,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """"Seeden beenden" for a filed torrent the operator has since put into a category of his own: nexbooks lets go of
    it, the program keeps it."""
    from .filing_helpers import download_roots
    from .test_procure_tracking import seed_world

    qbit, info_hash, row, world = seed_world(net, download_roots(tmp_path, monkeypatch))
    qbit.put(info_hash, category="mine")
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/stop-seeding").status_code == 200
    assert info_hash in qbit.torrents and qbit.removed == []
    assert downloads()[0].seed_state == "done"
