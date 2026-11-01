"""Loading a release of a search: the file from the indexer, the job into nexbooks's category of a download
program, the row in ``downloads``; "Trotzdem laden" only when asked; a second click; a hand-over that got no answer."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models import Download, DownloadClient, Indexer
from app.services.procure import automation, loading
from app.services.procure import gate as loading_gate

from .procure_fakes import FakeDeluge, FakeNetwork, FakeNzbget, FakeQbittorrent, FakeSabnzbd, FakeTransmission
from .procure_search_helpers import TORRENT_HOST, FeedIndexer, Item, World, add_indexer, add_program, make_world, search

GOOD = "Rune.Falkner.-.Nebelchor.02.-.Die.Laternenstrasse.GERMAN.EPUB-EXAMPLE"
PDF = "Rune.Falkner.-.Die.Laternenstrasse.GERMAN.PDF-EXAMPLE"


def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b"e"


def torrent_file() -> tuple[bytes, str]:
    info = {"name": "Die.Laternenstrasse", "length": 1000, "piece length": 16384, "pieces": b"x" * 20}
    raw = bencode(info)
    return bencode({"announce": "https://tracker.example.com/announce", "info": info}), hashlib.sha1(
        raw, usedforsecurity=False).hexdigest()


def searched(net: FakeNetwork, items: list[Item] | None = None) -> tuple[World, dict[str, Any]]:
    world = make_world(net)
    world.indexer.items = items or [Item(GOOD), Item(PDF)]
    return world, search(world, language="de")


def release_of(found: dict[str, Any], title: str) -> int:
    return next(item["id"] for item in found["releases"] if item["title"] == title)


def load(world: World, found: dict[str, Any], title: str, **body: Any) -> httpx.Response:
    return world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release_of(found, title)}/load",
                             json=body)


def sabnzbd(net: FakeNetwork) -> FakeSabnzbd:
    key = secrets.token_hex(16)
    fake = net.add(FakeSabnzbd("sab.example.com", key))
    add_program("sabnzbd", "sab.example.com", secret=key)
    return fake  # type: ignore[no-any-return]


def downloads() -> list[Download]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(Download)))
        for row in rows:
            db.expunge(row)
        return rows


def test_a_release_goes_to_sabnzbd_into_nexbooks_category_and_is_written_down(operator: Any,
                                                                             net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert answer.status_code == 201, answer.text
    shown = answer.json()
    assert shown["state"] == "queued" and shown["forced"] == [] and shown["protocol"] == "usenet"
    assert len(sab.jobs) == 1
    category, file_name, body = next(iter(sab.jobs.values()))
    assert category == "nexbooks" and file_name.endswith(".nzb") and b"<nzb" in body
    add = next(request for request in sab.requests if request.url.params.get("mode") == "addfile")
    # A book that is missing goes to the front.
    assert add.url.params.get("priority") == "1"
    [row] = downloads()
    assert (row.state, row.kind, row.client_ref, row.category, row.title, row.language) == (
        "queued", "ebook", next(iter(sab.jobs)), "nexbooks", GOOD, "de")
    assert row.work_id == world.work_id and row.user_id == world.person.id and row.library_id == world.library_id
    with SessionLocal() as db:
        assert db.scalar(select(Indexer.grabs)) == 1
    # The link went only to the indexer, with its key, once.
    assert len(world.indexer.fetched) == 1


def test_a_release_that_was_turned_down_loads_only_when_asked(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    refused = load(world, found, PDF)
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"code": "release_refused", "message": "nexbooks turned this release down.",
                                        "reasons": ["format_not_wanted"]}
    assert sab.jobs == {} and world.indexer.fetched == [] and downloads() == []
    forced = load(world, found, PDF, force=True)
    assert forced.status_code == 201 and forced.json()["forced"] == ["format_not_wanted"]
    assert downloads()[0].forced == ["format_not_wanted"]


def test_a_second_click_on_the_same_release_is_refused_while_the_first_is_on_its_way(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    sabnzbd(net)
    world, found = searched(net)
    assert load(world, found, GOOD).status_code == 201
    again = load(world, found, GOOD)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_loading"
    assert len(downloads()) == 1


def test_two_clicks_at_once_hand_the_release_over_once(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    answers: list[int] = []
    gate = threading.Barrier(2)

    def click() -> None:
        gate.wait()
        answers.append(load(world, found, GOOD).status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [201, 409]
    assert len(sab.jobs) == 1 and len(downloads()) == 1


def test_a_hand_over_without_an_answer_is_unsure(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    sab.add_timeout = True
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert answer.status_code == 201 and answer.json()["state"] == "unsure"
    assert downloads()[0].client_ref == ""


def test_sabnzbd_that_puts_the_job_outside_the_category_has_it_removed_again(operator: Any,
                                                                            net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    sab.unknown_category = True
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_category_failed"
    assert sab.jobs == {} and downloads() == []


def test_a_failed_hand_over_leaves_nothing_behind_and_the_release_can_be_tried_again(operator: Any,
                                                                                    net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    sab.add_answer = httpx.Response(200, json={"status": False, "error": "refused"})
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "client_refused"
    assert downloads() == []
    sab.add_answer = None
    assert load(world, found, GOOD).status_code == 201


def test_the_next_program_is_asked_when_the_first_cannot_be_reached(operator: Any, net: FakeNetwork) -> None:
    add_program("sabnzbd", "gone.example.com", secret="k" * 32, priority=1)
    user, password = "nexbooks", secrets.token_hex(8)
    nzbget = net.add(FakeNzbget("nzbget.example.com", user, password))
    add_program("nzbget", "nzbget.example.com", username=user, secret=password, priority=2)
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert answer.status_code == 201, answer.text
    assert answer.json()["client"] == "nzbget-2"
    [params] = nzbget.appended
    assert params[0].endswith(".nzb") and params[2] == "nexbooks" and params[3] == 50
    assert base64.b64decode(params[1]).startswith(b"<?xml")
    assert downloads()[0].client_ref == "41"


def test_no_program_of_the_protocol(operator: Any, net: FakeNetwork) -> None:
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert answer.status_code == 409 and answer.json()["detail"] == {
        "code": "no_client", "message": "No download program for this kind of release is switched on.",
        "protocol": "usenet"}
    assert world.indexer.fetched == []


@pytest.mark.parametrize(("answer", "code"), [
    (httpx.Response(200, content=b"<html><body>not an nzb</body></html>"), "release_file_invalid"),
    (httpx.Response(200, content=b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY a "b">]><nzb>&a;</nzb>'),
     "release_file_invalid"),
    (httpx.Response(200, content=b'<?xml version="1.0"?><nzb></nzb>'), "release_file_invalid"),
    (httpx.Response(404, content=b"gone"), "release_fetch_failed"),
    (httpx.Response(429, headers={"retry-after": "600"}), "indexer_limit_reached"),
])
def test_a_file_that_is_no_nzb_or_an_indexer_that_refuses_hands_nothing_over(operator: Any, net: FakeNetwork,
                                                                             answer: httpx.Response,
                                                                             code: str) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    guid = hashlib.sha256(GOOD.encode()).hexdigest()[:16]
    world.indexer.files[guid] = answer
    shown = load(world, found, GOOD)
    assert shown.json()["detail"]["code"] == code
    assert sab.jobs == {} and downloads() == []


class FileHost:
    """A host that is not the indexer's, which hands out the file of a release."""

    def __init__(self, host: str, redirect_to: str = "") -> None:
        self.host = host
        self.redirect_to = redirect_to
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.redirect_to:
            return httpx.Response(302, headers={"location": self.redirect_to})
        return httpx.Response(200, content=nzb_file(), headers={"content-type": "application/x-nzb"})


def nzb_file() -> bytes:
    from .procure_search_helpers import nzb

    return nzb()


def on_the_internet(monkeypatch: pytest.MonkeyPatch, private: dict[str, str] | None = None) -> None:
    """Every name resolves to a public address, except those the test names (to the address it gives)."""
    table = private or {}

    def resolve(host: str, _port: int) -> list[str]:
        bare = host.strip("[]")
        written = ":" in bare or bare[:1].isdigit()
        return [table.get(bare, bare if written else "93.184.216.34")]

    monkeypatch.setattr(loading_gate, "resolver", resolve)


@pytest.mark.parametrize("link", [
    "https://files.example.org/get/abc?apikey=k", "http://files.example.org/get/abc?apikey=k",
    "https://dl.indexer.example.com/get/abc?apikey=k",
])
def test_a_link_to_another_host_is_followed_over_http_or_https(operator: Any, net: FakeNetwork,
                                                               monkeypatch: pytest.MonkeyPatch, link: str) -> None:
    sab = sabnzbd(net)
    on_the_internet(monkeypatch)
    files = [net.add(FileHost("files.example.org")), net.add(FileHost("dl.indexer.example.com"))]
    world, found = searched(net, [Item(GOOD, link=link)])
    answer = load(world, found, GOOD)
    assert answer.status_code == 201, answer.text
    assert sum(len(host.requests) for host in files) == 1 and len(sab.jobs) == 1
    [request] = [request for host in files for request in host.requests]
    assert request.url.params.get("apikey") == "k" and "authorization" not in request.headers


def test_a_redirect_of_a_link_to_another_host_is_followed(operator: Any, net: FakeNetwork,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    on_the_internet(monkeypatch)
    far = net.add(FileHost("cdn.example.org"))
    net.add(FileHost("files.example.org", redirect_to="https://cdn.example.org/file.nzb"))
    world, found = searched(net, [Item(GOOD, link="https://files.example.org/get/abc?apikey=k")])
    assert load(world, found, GOOD).status_code == 201
    assert len(far.requests) == 1 and len(sab.jobs) == 1


@pytest.mark.parametrize("target", [
    "http://10.0.0.5/get/abc?apikey=k", "http://127.0.0.1:8080/x", "http://[::1]/x", "http://192.168.1.2/x",
    "http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/x", "http://inside.example.org/x",
])
def test_an_indexer_on_the_internet_has_no_link_into_a_private_network(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    sab = sabnzbd(net)
    on_the_internet(monkeypatch, {"inside.example.org": "10.0.0.7"})
    inside = net.add(FileHost("inside.example.org"))
    world, found = searched(net, [Item(GOOD, link=target)])
    answer = load(world, found, GOOD)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "release_link_private"
    target_host = urlsplit(target).hostname or ""
    assert not [request for request in net.requests if target_host in request.headers["host"]]
    assert inside.requests == [] and sab.jobs == {} and downloads() == []


def test_a_redirect_of_a_link_into_a_private_network_is_stopped(operator: Any, net: FakeNetwork,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    on_the_internet(monkeypatch, {"inside.example.org": "10.0.0.7"})
    inside = net.add(FileHost("inside.example.org"))
    net.add(FileHost("files.example.org", redirect_to="http://inside.example.org/steal"))
    world, found = searched(net, [Item(GOOD, link="https://files.example.org/get/abc?apikey=k")])
    answer = load(world, found, GOOD)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "release_link_private"
    assert inside.requests == [] and sab.jobs == {}


def test_a_private_indexer_may_link_into_its_own_network(operator: Any, net: FakeNetwork) -> None:
    """The tests resolve every name to a private address: the indexer is in the home network."""
    sab = sabnzbd(net)
    lan = net.add(FileHost("files.example.org"))
    world, found = searched(net, [Item(GOOD, link="http://files.example.org/get/abc?apikey=k")])
    assert load(world, found, GOOD).status_code == 201 and len(lan.requests) == 1 and len(sab.jobs) == 1


@pytest.mark.parametrize("target", ["http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/x"])
def test_a_private_indexer_never_has_a_link_to_a_metadata_service(operator: Any, net: FakeNetwork,
                                                                 monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    sab = sabnzbd(net)
    monkeypatch.setattr(loading_gate, "resolver", lambda host, _port: [host.strip("[]")] if host[:1] in "[1f" and (
        ":" in host or host[:1].isdigit()) else ["10.99.0.5"])
    world, found = searched(net, [Item(GOOD, link=target)])
    answer = load(world, found, GOOD)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "release_link_private"
    assert sab.jobs == {} and downloads() == []


def test_a_link_that_is_no_web_link_gives_no_file(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net, [Item(GOOD, link="ftp://files.example.org/get/abc")])
    answer = load(world, found, GOOD)
    assert answer.status_code == 502 and answer.json()["detail"]["code"] == "release_file_invalid"
    assert sab.jobs == {} and downloads() == []


def test_a_torrent_goes_to_qbittorrent_with_the_seed_goal_of_the_tracker(operator: Any, net: FakeNetwork) -> None:
    user, password = "nexbooks", secrets.token_hex(8)
    qbit = net.add(FakeQbittorrent("qbit.example.com", user, password))
    add_program("qbittorrent", "qbit.example.com", username=user, secret=password)
    world = make_world(net)
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    content, info_hash = torrent_file()
    tracker.items = [Item(GOOD, seeders=20, extra={"minimumratio": "1.5", "minimumseedtime": "172800"})]
    guid = hashlib.sha256(GOOD.encode()).hexdigest()[:16]
    tracker.files[guid] = httpx.Response(200, content=content, headers={"content-type": "application/x-bittorrent"})
    found = search(world, language="de")
    release = next(item["id"] for item in found["releases"] if item["protocol"] == "torrent")
    answer = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/load", json={})
    assert answer.status_code == 201, answer.text
    [added] = qbit.added
    assert added["category"] == "nexbooks" and added["has_file"] == "True" and added["stopped"] == "false"
    assert added["ratioLimit"] == "1.5" and added["seedingTimeLimit"] == "2880"
    assert downloads()[0].client_ref == info_hash and downloads()[0].info_hash == info_hash


def test_a_magnet_link_of_a_torrent_indexer_goes_to_transmission(operator: Any, net: FakeNetwork) -> None:
    user, password = "nexbooks", secrets.token_hex(8)
    transmission = net.add(FakeTransmission("tr.example.com", user, password))
    transmission.hash = "c" * 40
    add_program("transmission", "tr.example.com", username=user, secret=password)
    world = make_world(net)
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    tracker.items = [Item(GOOD, seeders=5)]
    guid = hashlib.sha256(GOOD.encode()).hexdigest()[:16]
    magnet = "magnet:?xt=urn:btih:" + "c" * 40 + "&dn=x"
    tracker.files[guid] = httpx.Response(302, headers={"location": magnet})
    found = search(world, language="de")
    release = next(item["id"] for item in found["releases"] if item["protocol"] == "torrent")
    answer = world.client.post(f"/api/v1/procure/searches/{found['id']}/releases/{release}/load", json={})
    assert answer.status_code == 201, answer.text
    [added] = transmission.added
    assert added["filename"] == magnet and added["labels"] == ["nexbooks"]
    assert added["download-dir"] == "/data/torrents/nexbooks"
    assert downloads()[0].client_ref == "c" * 40


def test_deluge_labels_the_torrent_or_takes_it_out_again(operator: Any, net: FakeNetwork) -> None:
    password = secrets.token_hex(8)
    deluge = net.add(FakeDeluge("deluge.example.com", password))
    add_program("deluge", "deluge.example.com", secret=password)
    world = make_world(net)
    tracker = net.add(FeedIndexer(TORRENT_HOST, "t" * 32, kind="torznab"))
    add_indexer(tracker, name="Tracker", priority=1)
    content, _info = torrent_file()
    tracker.items = [Item(GOOD, seeders=5)]
    guid = hashlib.sha256(GOOD.encode()).hexdigest()[:16]
    tracker.files[guid] = httpx.Response(200, content=content)
    found = search(world, language="de")
    release = next(item["id"] for item in found["releases"] if item["protocol"] == "torrent")
    url = f"/api/v1/procure/searches/{found['id']}/releases/{release}/load"
    deluge.label_fails = True
    failed = world.client.post(url, json={})
    assert failed.status_code == 502 and failed.json()["detail"]["code"] == "client_category_failed"
    assert deluge.removed == [deluge.hash] and downloads() == []
    deluge.label_fails = False
    assert world.client.post(url, json={}).status_code == 201
    assert deluge.torrent_labels == {deluge.hash: "nexbooks"}
    assert deluge.added[-1]["options"] == {"add_paused": False, "remove_at_ratio": False}


def test_the_operators_seed_choice_raises_the_goal(operator: Any, net: FakeNetwork) -> None:
    searched(net)
    with SessionLocal() as db:
        automation.save(db, {**automation.config(db), "seed": "7d"})
        from app.models import SearchRelease

        release = db.scalars(select(SearchRelease)).first()
        assert release is not None
        release.min_ratio, release.min_seed_seconds = 2.0, 3600
        assert loading.seed_goal(db, release) == loading.SeedGoal(ratio=2.0, seconds=7 * 24 * 3600)
        automation.save(db, {**automation.config(db), "seed": "ratio"})
        release.min_ratio = None
        assert loading.seed_goal(db, release).ratio == 1.0


def test_a_search_still_running_loads_nothing(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world, found = searched(net)
    from app.models import ReleaseSearch

    with SessionLocal() as db:
        db.execute(update(ReleaseSearch).values(state="running"))
        db.commit()
    answer = load(world, found, GOOD)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "search_not_done"


def test_a_person_who_lost_the_right_loads_nothing(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    from .library_helpers import join

    join(world.library_id, world.person.id, "read")
    answer = load(world, found, GOOD)
    assert answer.status_code == 403 and sab.jobs == {}


def test_a_program_that_is_switched_off_is_not_asked(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    with SessionLocal() as db:
        db.execute(update(DownloadClient).values(enabled=False))
        db.commit()
    assert load(world, found, GOOD).json()["detail"]["code"] == "no_client"
    assert sab.jobs == {}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Download)) == 0


def test_the_file_name_for_the_program_is_safe() -> None:
    assert loading.file_name("../../etc/passwd", "usenet") == "etc_passwd.nzb"
    assert loading.file_name("A/B\\C:D", "torrent") == "A_B_C_D.torrent"
    assert loading.file_name("", "usenet") == "release.nzb"
    assert len(loading.file_name("x" * 900, "usenet")) <= 190


def test_the_answer_of_a_load_names_no_link_and_no_key(operator: Any, net: FakeNetwork) -> None:
    sabnzbd(net)
    world, found = searched(net)
    answer = load(world, found, GOOD)
    assert world.indexer.api_key not in answer.text and "/get/" not in answer.text
    assert json.loads(answer.text)["title"] == GOOD


def test_a_release_a_stopped_server_left_on_its_way_can_be_loaded_again(operator: Any, net: FakeNetwork) -> None:
    sab = sabnzbd(net)
    world, found = searched(net)
    from datetime import timedelta

    from app import clock

    with SessionLocal() as db:
        moment = clock.now() - timedelta(minutes=30)
        db.add(Download(user_id=world.person.id, work_id=world.work_id, kind="ebook", title=GOOD, protocol="usenet",
                        state="sending", created_at=moment, updated_at=moment))
        db.commit()
    assert load(world, found, GOOD).status_code == 201
    assert len(sab.jobs) == 1 and [row.state for row in downloads()] == ["failed", "queued"]
    assert downloads()[0].failed_reason == "interrupted"


def test_a_fault_nobody_foresaw_while_fetching_is_a_failed_fetch_and_names_no_link(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A real indexer once answered with a header the rebuilt answer could not take, and the error fell through every
    layer as a traceback, again every 15 seconds. Whatever goes wrong while fetching is a failed fetch: the wish settles
    and nothing of the link (which carries the key) reaches the log."""
    from app.services.procure import indexers

    sab = sabnzbd(net)
    world, found = searched(net)

    def broken(self: Any, link: str, protocol: str) -> Any:
        raise UnicodeEncodeError("ascii", "secret-" + link, 7, 8, "made up")

    monkeypatch.setattr(indexers.Client, "fetch", broken)
    shown = load(world, found, GOOD)
    assert shown.status_code == 502, shown.text
    assert shown.json()["detail"]["code"] == "release_fetch_failed"
    assert sab.jobs == {} and downloads() == []
    assert "secret-" not in caplog.text and "apikey" not in caplog.text
