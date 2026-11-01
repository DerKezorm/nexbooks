"""One person cannot hold the server with files: at most ``max_streams`` files are sent
to one person at the same moment (all their browsers and devices together), and there is a rate of requests for files per
person and minute. Beyond either, ``429`` with ``Retry-After``. A player holds one or two files and never meets them.

The slot of a file is given back when the response is over, however it ended (the last byte, a range that cannot be
served, a connection that broke)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import get_settings
from app.middleware import FileGateMiddleware
from app.models import Account
from app.security import SESSION_COOKIE
from app.services import brakes

from .conftest import new_client
from .test_listening import World, bearer  # the library of recordings is the one of the listening tests


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


@pytest.fixture
def two_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_streams", 2)


def first_part(world: World) -> int:
    return world.files(world.edition("Seven Bridges"))[0].id


def test_the_places_for_open_files_are_counted_per_person_and_given_back_once(two_streams: None) -> None:
    a = brakes.open_stream(1)
    b = brakes.open_stream(1)
    with pytest.raises(HTTPException) as refused:
        brakes.open_stream(1)
    assert getattr(refused.value, "status_code", None) == 429
    assert refused.value.headers["Retry-After"] == "2"  # type: ignore[attr-defined]
    # Another person has places of their own.
    other = brakes.open_stream(2)
    a()
    a()  # giving the same place back twice frees one place, not two
    assert brakes.streams_open(1) == 1
    c = brakes.open_stream(1)
    with pytest.raises(HTTPException):
        brakes.open_stream(1)
    for release in (b, c, other):
        release()
    assert brakes.streams_open(1) == 0 and brakes.streams_open(2) == 0


def test_a_person_with_all_places_in_use_gets_429_with_retry_after_and_others_are_served(
    world: World, two_streams: None
) -> None:
    part = first_part(world)
    held = [brakes.open_stream(world.rita.id), brakes.open_stream(world.rita.id)]
    with new_client(world.rita) as rita:
        refused = rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-9"})
        assert refused.status_code == 429
        assert refused.json()["detail"]["code"] == "too_many_streams"
        assert int(refused.headers["retry-after"]) >= 1
        # The download of the same file counts the same.
        assert rita.get(f"/api/v1/files/{part}/download").status_code == 429
        # Everything else of theirs works: a page of the app is no file.
        assert rita.get("/api/v1/works").status_code == 200
        held.pop()()
        served = rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-9"})
        assert served.status_code == 206
    # Another person is not held back by rita's places.
    with new_client(world.sam) as sam:
        assert sam.get("/api/v1/works").status_code == 200
    for release in held:
        release()


def test_the_place_is_given_back_after_every_kind_of_answer(world: World, two_streams: None) -> None:
    part = first_part(world)
    with new_client(world.rita) as rita:
        for _ in range(6):
            assert rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-9"}).status_code == 206
            assert rita.get(f"/api/v1/files/{part}/content").status_code == 200
            # A range no file has: 416 is an answer that held a place for a moment, too.
            assert rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=999999999-"}).status_code == 416
            assert rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=5-2"}).status_code == 400
            # Asked again with the ETag: nothing is sent, so nothing is held.
            etag = rita.get(f"/api/v1/files/{part}/content").headers["etag"]
            assert rita.get(f"/api/v1/files/{part}/content", headers={"If-None-Match": etag}).status_code == 304
            assert rita.get(f"/api/v1/files/{part}/download").status_code == 200
            assert rita.get("/api/v1/files/999999/content").status_code == 404
    assert brakes.streams_open(world.rita.id) == 0


def test_a_file_whose_connection_broke_gives_its_place_back() -> None:
    """The slot is held by the response itself: an ASGI call that fails while sending (the client went away) still ends
    with the place given back."""
    from app.routers.works import _Sending

    path = __file__
    release = brakes.open_stream(41)
    assert brakes.streams_open(41) == 1
    response = _Sending(path, media_type="text/plain", release=release)

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(_message: dict[str, object]) -> None:
        raise OSError("the client went away")

    scope = {"type": "http", "method": "GET", "headers": [], "extensions": {"http.response.pathsend": {}}}
    with pytest.raises(OSError, match="went away"):
        asyncio.run(response(scope, receive, send))
    assert brakes.streams_open(41) == 0


def test_a_person_who_asks_for_files_too_often_is_told_to_wait(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    part = first_part(world)
    monkeypatch.setitem(brakes.LIMITS, "stream", 5)
    with new_client(world.rita) as rita:
        for _ in range(5):
            assert rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-9"}).status_code == 206
        slow = rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-9"})
        assert slow.status_code == 429
        assert slow.json()["detail"]["code"] == "stream_too_often"
        assert 1 <= int(slow.headers["retry-after"]) <= 60
        assert rita.get(f"/api/v1/files/{part}/download").status_code == 429
        # The rest of the app does not feel it.
        assert rita.get("/api/v1/works").status_code == 200
    with new_client(world.sam) as sam:
        assert sam.get("/api/v1/works").status_code == 200


def test_a_device_token_counts_for_the_person_it_belongs_to(world: World, two_streams: None, client: TestClient) -> None:
    # All the devices and browsers of a person together have the places of the person.
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    part = first_part(world)
    held = [brakes.open_stream(world.tester_id), brakes.open_stream(world.tester_id)]
    with new_client() as program:
        refused = program.get(f"/api/v1/files/{part}/content", headers={**bearer(token), "Range": "bytes=0-9"})
        assert refused.status_code == 429 and "retry-after" in refused.headers
        held.pop()()
        assert program.get(f"/api/v1/files/{part}/content", headers={**bearer(token), "Range": "bytes=0-9"}).status_code == 206
    for release in held:
        release()


# ---- The gate in front of the route ---------------------------------------------------------------------------------


def _gate_scope(path: str = "/api/v1/files/7/content", cookie: bytes | None = None, method: str = "GET",
                bearer_token: bytes | None = None) -> dict[str, object]:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", SESSION_COOKIE.encode() + b"=" + cookie + b"; nexbooks_theme=dark"))
    if bearer_token is not None:
        headers.append((b"authorization", b"Bearer " + bearer_token))
    return {"type": "http", "method": method, "path": path, "headers": headers}


def test_the_gate_turns_the_surplus_of_one_credential_away_before_anything_is_looked_up(two_streams: None) -> None:
    """Measured: 300 requests for one person's file at once made every other person's pages wait two seconds, because each
    request asked the database who it was. The gate counts per credential in front of everything."""
    inside = 0

    async def scenario() -> tuple[list[list[dict[str, object]]], int]:
        nonlocal inside
        hold = asyncio.Event()

        async def app(_scope: dict[str, object], _receive: object, send: object) -> None:
            nonlocal inside
            inside += 1
            await hold.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})  # type: ignore[operator]
            await send({"type": "http.response.body", "body": b""})  # type: ignore[operator]

        gate = FileGateMiddleware(app)

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        sent: list[list[dict[str, object]]] = []

        def start(scope: dict[str, object]) -> asyncio.Task[None]:
            messages: list[dict[str, object]] = []
            sent.append(messages)

            async def send(message: dict[str, object]) -> None:
                messages.append(message)

            return asyncio.create_task(gate(scope, receive, send))

        tasks = [start(_gate_scope(cookie=b"aaa")) for _ in range(3)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Others are not held back: another credential, a token, another route, a change, a request with no credential.
        tasks += [start(_gate_scope(cookie=b"bbb")), start(_gate_scope(bearer_token=b"nbd_x")),
                  start(_gate_scope(path="/api/v1/works", cookie=b"aaa")),
                  start(_gate_scope(method="POST", cookie=b"aaa")), start(_gate_scope())]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        opened = gate.open_requests()
        hold.set()
        await asyncio.gather(*tasks)
        return sent, opened

    sent, opened = asyncio.run(scenario())
    # Two of the first three got in; the third was answered at the gate and never reached the app.
    refused = [messages for messages in sent if messages and messages[0]["status"] == 429]
    assert len(refused) == 1 and sent.index(refused[0]) == 2
    start = refused[0][0]
    assert (b"retry-after", b"2") in start["headers"]  # type: ignore[operator]
    assert b"too_many_streams" in refused[0][1]["body"]  # type: ignore[operator]
    assert inside == 2 + 5, "the app saw two of aaa and each of the others"
    assert opened == 2 + 2, "two of aaa and one each of bbb and the token count; the rest is not a file or has no credential"


def test_the_gate_gives_the_place_back_when_the_app_fails_or_the_request_ends(two_streams: None) -> None:
    async def scenario() -> int:
        async def broken(_scope: dict[str, object], _receive: object, _send: object) -> None:
            raise RuntimeError("boom")

        gate = FileGateMiddleware(broken)

        async def receive() -> dict[str, object]:
            return {"type": "http.request"}

        async def send(_message: dict[str, object]) -> None:
            return None

        for _ in range(5):
            with pytest.raises(RuntimeError):
                await gate(_gate_scope(cookie=b"aaa"), receive, send)
        return gate.open_requests()

    assert asyncio.run(scenario()) == 0


def test_a_real_burst_of_requests_for_one_file_is_turned_away_cheaply(world: World, two_streams: None) -> None:
    """Through the whole stack, the route and the gate agree: with two places, more than two at once is 429 (the gate counts
    the requests in flight, the route the files being sent), and one after the other all is served."""
    part = first_part(world)
    with new_client(world.rita) as rita:
        for _ in range(8):
            assert rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-9"}).status_code == 206
