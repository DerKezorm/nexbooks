"""The way out to indexers, Prowlarr and download programs (``services/procure/gate.py``): what it lets through, what it
never reaches, what it follows, how much it reads, and what it writes to the log. Stand-ins only; nothing reaches a
real host."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.services import outbound
from app.services.procure import gate

from .procure_fakes import FakeIndexer, FakeNetwork


class Echo:
    """A host that answers what a test wants and keeps the requests."""

    def __init__(self, host: str, handler: Any = None) -> None:
        self.host = host
        self.handler = handler or (lambda request: httpx.Response(200, text="ok"))
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)  # type: ignore[no-any-return]


def ask(url: str, **kwargs: Any) -> httpx.Response:
    base = url.split("/", 3)
    address = "/".join(base[:3])
    with gate.Session(address, label="test") as session:
        return session.request(kwargs.pop("method", "GET"), url, **kwargs)


# --- The address the operator types ------------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "expected"), [
    ("https://Indexer.Example.com/api/", "https://indexer.example.com/api"),
    ("  http://10.0.0.5:9696  ", "http://10.0.0.5:9696"),
    ("http://prowlarr.example.com:9696/prowlarr/", "http://prowlarr.example.com:9696/prowlarr"),
    ("http://[fd00::5]:8080/x", "http://[fd00::5]:8080/x"),
])
def test_an_address_is_tidied(typed: str, expected: str) -> None:
    assert gate.normalize_address(typed) == expected


def test_an_address_without_a_path_gets_the_path_it_is_given() -> None:
    assert gate.normalize_address("https://indexer.example.com", default_path="/api") == "https://indexer.example.com/api"
    assert gate.normalize_address("https://indexer.example.com/", default_path="/api") == "https://indexer.example.com/api"
    assert gate.normalize_address("https://indexer.example.com/x", default_path="/api") == "https://indexer.example.com/x"


@pytest.mark.parametrize("typed", [
    "", "   ", "ftp://example.com/", "file:///etc/passwd", "javascript:alert(1)", "example.com/api",
    "http://user:pw@example.com/", "http://user@example.com/", "https://example.com/api?apikey=x",
    "https://example.com/api#frag", "https://example.com/?", "http://", "http://:80/", "https://exa mple.com/",
    "https://example.com:notaport/", "https://example.com:0/", "https://example.com/a\nb", "https://example.com/\x00",
    "http://" + "a" * 1100 + ".example.com/",
])
def test_an_address_that_cannot_be_used_is_refused(typed: str) -> None:
    with pytest.raises(gate.GateError) as caught:
        gate.normalize_address(typed)
    assert caught.value.code == "bad_address"


# --- Where it goes -----------------------------------------------------------------------------------------------------


def test_a_private_address_is_reached_on_purpose(net: FakeNetwork) -> None:
    """A homelab lives in private space: the name resolves to 10.99.0.5 here, and the request goes through."""
    host = net.add(Echo("sab.example.com"))
    assert ask("http://sab.example.com:8080/api").status_code == 200
    assert len(host.requests) == 1


@pytest.mark.parametrize("address", [
    "169.254.169.254", "169.254.170.2", "fe80::1", "ff02::1", "0.0.0.0", "fd00:ec2::254", "100.100.100.200",
    "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe",
])
def test_an_address_that_is_never_reached_is_refused(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    host = net.add(Echo("sab.example.com"))
    monkeypatch.setattr(gate, "resolver", lambda _host, _port: [address])
    with pytest.raises(gate.GateError) as caught:
        ask("http://sab.example.com:8080/api")
    assert caught.value.code == "refused"
    assert host.requests == []


def test_one_bad_address_among_good_ones_refuses_the_name(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    """A name that resolves to a good and a metadata address is refused as a whole: no address is picked."""
    host = net.add(Echo("sab.example.com"))
    monkeypatch.setattr(gate, "resolver", lambda _host, _port: ["10.0.0.5", "169.254.169.254"])
    with pytest.raises(gate.GateError) as caught:
        ask("http://sab.example.com/api")
    assert caught.value.code == "refused" and host.requests == []


def test_a_name_that_does_not_resolve_is_unreachable(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing(_host: str, _port: int) -> list[str]:
        raise outbound.Unreachable("sab.example.com")

    monkeypatch.setattr(gate, "resolver", nothing)
    with pytest.raises(gate.GateError) as caught:
        ask("http://sab.example.com/api")
    assert caught.value.code == "unreachable"


def test_a_session_asks_its_own_host_and_no_other(net: FakeNetwork) -> None:
    mine, other = net.add(Echo("indexer.example.com")), net.add(Echo("elsewhere.example.com"))
    with gate.Session("https://indexer.example.com/api", label="test") as session:
        assert session.request("GET", "https://indexer.example.com/getnzb/1").status_code == 200
        with pytest.raises(gate.GateError) as caught:
            session.request("GET", "https://elsewhere.example.com/getnzb/1")
        assert caught.value.code == "host_not_allowed"
        # Not even the same name with another user in front of it, or a look-alike.
        for url in ("https://indexer.example.com.elsewhere.example.com/x", "https://INDEXER.example.com@elsewhere.example.com/x"):
            with pytest.raises(gate.GateError):
                session.request("GET", url)
    assert len(mine.requests) == 1 and other.requests == []


# --- Redirects -------------------------------------------------------------------------------------------------------------


def test_a_redirect_to_the_same_host_is_followed(net: FakeNetwork) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/there"})
        return httpx.Response(200, text="arrived")

    net.add(Echo("indexer.example.com", handler))
    assert ask("http://indexer.example.com/start").text == "arrived"


def test_a_redirect_to_the_same_host_over_https_is_followed(net: FakeNetwork) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "http":
            return httpx.Response(301, headers={"location": "https://indexer.example.com/api"})
        return httpx.Response(200, text="secure")

    net.add(Echo("indexer.example.com", handler))
    assert ask("http://indexer.example.com/api").text == "secure"


def test_a_redirect_to_another_host_is_not_followed(net: FakeNetwork) -> None:
    other = net.add(Echo("evil.example.com"))
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": "http://evil.example.com/x"})))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/start")
    assert caught.value.code == "redirect"
    assert other.requests == []


@pytest.mark.parametrize(("origin", "target", "allowed"), [
    ("http://h.example.com/api", "http://h.example.com/other", True),
    ("http://h.example.com:80/api", "http://h.example.com/other", True),
    ("http://h.example.com:8080/api", "http://h.example.com:8080/x", True),
    ("http://h.example.com/api", "https://h.example.com/api", True),
    ("http://h.example.com:8080/api", "https://h.example.com:8080/api", True),
    ("http://h.example.com/api", "https://h.example.com:443/api", True),
    ("https://h.example.com:8443/api", "https://h.example.com:8443/x", True),
    ("http://h.example.com:8080/api", "http://h.example.com:8081/api", False),
    ("http://h.example.com/api", "http://h.example.com:8080/api", False),
    ("http://h.example.com:8080/api", "https://h.example.com/api", False),
    ("http://h.example.com:8080/api", "https://h.example.com:8443/api", False),
    ("http://h.example.com/api", "https://h.example.com:8443/api", False),
    ("https://h.example.com/api", "http://h.example.com/api", False),
    ("https://h.example.com:8443/api", "http://h.example.com:8443/api", False),
    ("http://h.example.com/api", "http://g.example.com/api", False),
    ("http://h.example.com/api", "ftp://h.example.com/api", False),
    ("http://h.example.com/api", "http://h.example.com:99999/api", False),
])
def test_a_request_stays_at_the_host_and_the_port(origin: str, target: str, allowed: bool) -> None:
    assert gate.same_place(origin, target) is allowed


def test_a_redirect_to_another_port_of_the_same_host_is_not_followed(net: FakeNetwork) -> None:
    other = net.add(Echo("indexer.example.com:9000"))
    mine = net.add(Echo("indexer.example.com:8080", lambda request: httpx.Response(
        302, headers={"location": "http://indexer.example.com:9000/landed"})))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com:8080/start")
    assert caught.value.code == "redirect"
    assert other.requests == [] and len(mine.requests) == 1


def test_a_session_asks_its_own_port_and_no_other(net: FakeNetwork) -> None:
    other = net.add(Echo("indexer.example.com:9000"))
    net.add(Echo("indexer.example.com:8080"))
    with (gate.Session("http://indexer.example.com:8080/api", label="test") as session,
          pytest.raises(gate.GateError) as caught):
        session.request("GET", "http://indexer.example.com:9000/getnzb/1")
    assert caught.value.code == "host_not_allowed" and other.requests == []


class _TwoPorts:
    """Two real servers on the loopback: A answers with a redirect to B, B counts who comes."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.hits: dict[str, list[str]] = {"a": [], "b": []}
        outer = self

        def handler(tag: str) -> type[BaseHTTPRequestHandler]:
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args: object) -> None:
                    return

                def do_GET(self) -> None:
                    outer.hits[tag].append(self.path)
                    if tag == "a" and self.path.startswith("/to-b"):
                        self.send_response(302)
                        self.send_header("Location", f"http://127.0.0.1:{outer.port_b}/landed")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    body = b"ok"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            return Handler

        self.server_a = ThreadingHTTPServer(("127.0.0.1", 0), handler("a"))
        self.server_b = ThreadingHTTPServer(("127.0.0.1", 0), handler("b"))
        self.port_a, self.port_b = self.server_a.server_address[1], self.server_b.server_address[1]
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True)
                        for server in (self.server_a, self.server_b)]
        for thread in self.threads:
            thread.start()

    def close(self) -> None:
        for server in (self.server_a, self.server_b):
            server.shutdown()
            server.server_close()


def test_a_redirect_to_another_port_is_not_followed_over_real_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two servers on two real ports of one host: the first sends the server to the second; nothing arrives there."""
    monkeypatch.setattr(gate, "transport", None)
    monkeypatch.setattr(gate, "resolver", lambda _host, _port: ["127.0.0.1"])
    servers = _TwoPorts()
    try:
        base = f"http://127.0.0.1:{servers.port_a}"
        with gate.Session(base, label="test") as session:
            assert session.request("GET", session.url("/plain")).text == "ok"
            with pytest.raises(gate.GateError) as caught:
                session.request("GET", session.url("/to-b"))
        assert caught.value.code == "redirect"
        assert servers.hits["a"] == ["/plain", "/to-b"] and servers.hits["b"] == []
    finally:
        servers.close()


@pytest.mark.parametrize("target", [
    "ftp://indexer.example.com/x", "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:9/", "//evil.example.com/x",
    "http://indexer.example.com.evil.example.com/x",
])
def test_a_redirect_to_something_that_is_not_the_host_is_refused(net: FakeNetwork, target: str) -> None:
    other = net.add(Echo("evil.example.com"))
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": target})))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/start")
    assert caught.value.code == "redirect" and other.requests == []


def test_a_chain_of_redirects_ends_after_two(net: FakeNetwork) -> None:
    host = net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": "/again"})))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/start")
    assert caught.value.code == "redirect"
    assert gate.MAX_REDIRECTS == 2 and len(host.requests) == 3


def test_a_redirect_with_307_keeps_the_method_and_the_body_and_302_makes_a_get(net: FakeNetwork) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/first":
            return httpx.Response(307, headers={"location": "/second"})
        if request.url.path == "/third":
            return httpx.Response(302, headers={"location": "/fourth"})
        return httpx.Response(200, text="done")

    host = net.add(Echo("indexer.example.com", handler))
    ask("http://indexer.example.com/first", method="POST", content=b"payload")
    assert [(request.method, request.content) for request in host.requests] == [("POST", b"payload"), ("POST", b"payload")]
    host.requests.clear()
    ask("http://indexer.example.com/third", method="POST", content=b"payload")
    assert [(request.method, request.content) for request in host.requests] == [("POST", b"payload"), ("GET", b"")]


# --- Size and time ---------------------------------------------------------------------------------------------------------


def test_an_answer_over_the_limit_is_refused(net: FakeNetwork) -> None:
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(200, content=b"x" * 2048)))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/api", limit=1024)
    assert caught.value.code == "too_large"
    assert len(ask("http://indexer.example.com/api", limit=2048).content) == 2048


def test_the_limits_are_ten_megabytes_for_an_answer_twenty_for_an_nzb_and_ten_for_a_torrent() -> None:
    assert gate.ANSWER_MAX == 10 * 1024 * 1024
    assert gate.limit_for("usenet") == 20 * 1024 * 1024
    assert gate.limit_for("torrent") == 10 * 1024 * 1024


def test_a_file_of_a_release_is_read_up_to_its_limit(net: FakeNetwork) -> None:
    """One byte over the limit of a torrent file is refused, the limit itself is read."""
    limit = gate.limit_for("torrent")
    net.add(Echo("tracker.example.com", lambda request: httpx.Response(
        200, content=b"t" * (limit + 1 if request.url.path == "/big" else limit))))
    assert len(ask("http://tracker.example.com/ok", limit=limit).content) == limit
    with pytest.raises(gate.GateError) as caught:
        ask("http://tracker.example.com/big", limit=limit)
    assert caught.value.code == "too_large"


def test_a_packed_answer_is_unpacked_in_bounds(net: FakeNetwork) -> None:
    """A bomb: a few kilobytes that unpack to more than the limit."""
    import gzip

    bomb = gzip.compress(b"0" * 5_000_000, 9)
    assert len(bomb) < 20_000
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(
        200, content=bomb, headers={"content-encoding": "gzip"})))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/api", limit=1_000_000)
    assert caught.value.code == "too_large"


def test_no_answer_in_time_is_a_timeout(net: FakeNetwork) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    net.add(Echo("indexer.example.com", slow))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/api")
    assert caught.value.code == "timeout"


def test_one_deadline_covers_the_whole_request(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deadline handed to the way out is the budget of the session counted from the start of the request, and the same
    for every hop of a redirect (the sockets keep it; ``test_outbound`` tests that part)."""
    seen: list[float] = []
    real = outbound.send

    def spy(session: Any, method: str, place: Any, headers: Any, *, deadline: float, ticks: Any, **rest: Any) -> Any:
        seen.append(deadline - ticks())
        return real(session, method, place, headers, deadline=deadline, ticks=ticks, **rest)

    monkeypatch.setattr(outbound, "send", spy)
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": "/b"})
                 if request.url.path == "/a" else httpx.Response(200)))
    with gate.Session("http://indexer.example.com", label="test", seconds=12.0) as session:
        session.request("GET", "http://indexer.example.com/a")
        session.request("GET", "http://indexer.example.com/b", seconds=3.0)
    assert len(seen) == 3 and all(abs(left - 12.0) < 1.0 for left in seen[:2]) and abs(seen[2] - 3.0) < 1.0
    assert gate.SLOW_SECONDS > gate.SECONDS


def test_a_connection_that_fails_is_unreachable(net: FakeNetwork) -> None:
    with pytest.raises(gate.GateError) as caught:
        ask("http://nobody.example.com/api")
    assert caught.value.code == "unreachable"


# --- The log ---------------------------------------------------------------------------------------------------------------


KEY = "k" * 32


def test_the_log_names_no_key_no_title_and_no_path_of_a_file(net: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    net.add(FakeIndexer("indexer.example.com", KEY))
    caplog.set_level(logging.DEBUG, logger="nexbooks.procure")
    ask("https://indexer.example.com/api", params={"t": "search", "apikey": KEY, "q": "Ada Example The Lantern Road"})
    text = caplog.text
    assert "indexer.example.com" in text and "t=search" in text
    assert KEY not in text and "Lantern" not in text and "Ada" not in text and "apikey=***" in text


def test_a_key_like_part_of_a_path_is_masked(caplog: pytest.LogCaptureFixture) -> None:
    shown = gate.safe_url(f"https://indexer.example.com/download/{KEY}/file.nzb?apikey={KEY}&id=7")
    assert KEY not in shown and shown == "https://indexer.example.com/download/***/file.nzb?apikey=***&id=***"
    assert gate.origin_only(f"https://indexer.example.com/download/{KEY}/f?x=1") == "https://indexer.example.com/..."
    assert gate.safe_url("http://[::1]:8080/api?t=caps") == "http://::1:8080/api?t=caps"


def test_the_link_of_a_release_is_logged_by_its_origin_only(net: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    net.add(Echo("indexer.example.com"))
    caplog.set_level(logging.DEBUG, logger="nexbooks.procure")
    with gate.Session("https://indexer.example.com/api", label="test") as session:
        session.request("GET", f"https://indexer.example.com/getnzb/{KEY}.nzb?r={KEY}", log_url=False)
    assert KEY not in caplog.text and "https://indexer.example.com/..." in caplog.text


def test_a_failure_is_logged_without_the_address_path(net: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nexbooks.procure")
    with pytest.raises(gate.GateError):
        ask(f"http://nobody.example.com/secret-path/{KEY}?apikey={KEY}")
    assert KEY not in caplog.text and "secret-path" not in caplog.text and "nobody.example.com" in caplog.text


def test_a_session_outside_of_with_is_a_mistake_of_the_code() -> None:
    session = gate.Session("http://indexer.example.com", label="test")
    with pytest.raises(RuntimeError):
        session.request("GET", "http://indexer.example.com/api")


# --- The link of a release: any host, the network is the limit ----------------------------------------------------------


def names(monkeypatch: pytest.MonkeyPatch, table: dict[str, list[str]]) -> None:
    """Each name resolves as the table says (what it does not list, to a public address; an address written out to
    itself)."""

    def resolve(host: str, _port: int) -> list[str]:
        bare = host.strip("[]")
        if bare in table:
            return table[bare]
        return [bare] if ":" in bare or bare[:1].isdigit() else ["93.184.216.34"]

    monkeypatch.setattr(gate, "resolver", resolve)


def fetch_link(address: str, link: str, **kwargs: Any) -> httpx.Response:
    with gate.Session(address, label="test", headers={"X-Session": "sealed-header"}) as session:
        return session.request("GET", link, log_url=False, anywhere=True, **kwargs)


@pytest.mark.parametrize("link", [
    "https://indexer.example.com/getnzb/1?apikey=k",
    "https://files.indexer.example.com/getnzb/1?apikey=k",
    "https://downloads.example.org/getnzb/1?apikey=k",
    "http://downloads.example.org/getnzb/1?apikey=k",
    "http://indexer.example.com:8080/getnzb/1",
])
def test_the_link_of_a_release_is_followed_to_any_host_over_http_or_https(
        net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, link: str) -> None:
    names(monkeypatch, {})
    for host in ("indexer.example.com", "files.indexer.example.com", "downloads.example.org", "indexer.example.com:8080"):
        net.add(Echo(host))
    assert fetch_link("https://indexer.example.com/api", link).status_code == 200


def test_an_ordinary_request_still_stays_at_its_host(net: FakeNetwork) -> None:
    other = net.add(Echo("downloads.example.org"))
    with gate.Session("https://indexer.example.com/api", label="test") as session, pytest.raises(gate.GateError) as caught:
        session.request("GET", "https://downloads.example.org/x")
    assert caught.value.code == "host_not_allowed" and other.requests == []


def test_a_host_that_is_not_the_connections_own_gets_none_of_its_headers(net: FakeNetwork,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    names(monkeypatch, {})
    mine, other = net.add(Echo("indexer.example.com")), net.add(Echo("downloads.example.org"))
    fetch_link("https://indexer.example.com/api", "https://indexer.example.com/g", headers={"Accept": "x/y"})
    fetch_link("https://indexer.example.com/api", "https://downloads.example.org/g", headers={"Accept": "x/y"})
    assert mine.requests[0].headers["x-session"] == "sealed-header"
    assert "x-session" not in other.requests[0].headers and other.requests[0].headers["accept"] == "x/y"
    assert other.requests[0].url.query == b""


def test_a_redirect_of_a_link_is_followed_to_another_domain(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    names(monkeypatch, {})
    far = net.add(Echo("cdn.example.org", lambda request: httpx.Response(200, text="file")))
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(
        302, headers={"location": "https://cdn.example.org/f"})))
    assert fetch_link("https://indexer.example.com/api", "https://indexer.example.com/g").text == "file"
    assert len(far.requests) == 1


def test_a_chain_of_redirects_of_a_link_ends_after_two(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    names(monkeypatch, {})
    host = net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": "/again"})))
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "https://indexer.example.com/g")
    assert caught.value.code == "redirect" and len(host.requests) == 3


@pytest.mark.parametrize("target", ["ftp://cdn.example.org/f", "file:///etc/passwd", "javascript:alert(1)"])
def test_a_redirect_of_a_link_to_another_scheme_is_refused(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch,
                                                           target: str) -> None:
    names(monkeypatch, {})
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": target})))
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "https://indexer.example.com/g")
    assert caught.value.code in ("bad_address", "redirect")


@pytest.mark.parametrize("target", ["javascript:alert(1)", "http://exa mple.com/"])
def test_a_redirect_to_something_that_is_no_address_is_an_ended_request_not_a_crash(
        net: FakeNetwork, target: str) -> None:
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(302, headers={"location": target})))
    with pytest.raises(gate.GateError) as caught:
        ask("http://indexer.example.com/start")
    assert caught.value.code == "redirect"


@pytest.mark.parametrize("address", [
    "10.0.0.5", "192.168.1.5", "172.16.0.9", "127.0.0.1", "::1", "fd12:3456::1", "100.64.0.7", "169.254.169.254",
    "fe80::1", "fd00:ec2::254", "::ffff:10.0.0.5", "64:ff9b::a9fe:a9fe",
])
def test_an_indexer_on_the_internet_has_no_link_to_a_name_that_resolves_privately(
        net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    names(monkeypatch, {"inside.example.org": [address]})
    inside = net.add(Echo("inside.example.org"))
    net.add(Echo("indexer.example.com"))
    for link in ("https://inside.example.org/g?apikey=k", "http://inside.example.org/g"):
        with pytest.raises(gate.GateError) as caught:
            fetch_link("https://indexer.example.com/api", link)
        assert caught.value.code == "refused"
    assert inside.requests == []


@pytest.mark.parametrize("link", [
    "http://10.0.0.5/g", "http://192.168.1.5:8080/g", "http://127.0.0.1/g", "http://[::1]/g",
    "http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/x", "http://[fd12:3456::1]/x",
])
def test_an_indexer_on_the_internet_has_no_link_to_a_private_address_written_out(
        net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, link: str) -> None:
    names(monkeypatch, {})
    net.add(Echo("indexer.example.com"))
    before = len(net.requests)
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", link)
    assert caught.value.code == "refused" and len(net.requests) == before


def test_a_name_with_one_private_address_among_public_ones_is_refused_behind_an_indexer_on_the_internet(
        net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    names(monkeypatch, {"mixed.example.org": ["93.184.216.34", "10.0.0.5"]})
    mixed = net.add(Echo("mixed.example.org"))
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "https://mixed.example.org/g")
    assert caught.value.code == "refused" and mixed.requests == []


def test_a_redirect_of_a_link_is_checked_anew_for_every_hop(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first host is public and sends the server to a name that resolves privately: stopped there, and the private
    host never sees a request."""
    names(monkeypatch, {"rebind.example.org": ["10.0.0.5"]})
    inside = net.add(Echo("rebind.example.org"))
    front = net.add(Echo("downloads.example.org", lambda request: httpx.Response(
        302, headers={"location": "http://rebind.example.org/steal"})))
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "https://downloads.example.org/g")
    assert caught.value.code == "refused" and len(front.requests) == 1 and inside.requests == []


def test_a_redirect_of_a_link_to_an_address_written_out_is_refused_behind_an_indexer_on_the_internet(
        net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    names(monkeypatch, {})
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(
        302, headers={"location": "http://127.0.0.1:9/steal"})))
    before = len(net.requests)
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "https://indexer.example.com/g")
    assert caught.value.code == "refused" and len(net.requests) == before + 1


def test_a_private_indexer_may_link_to_private_hosts_and_be_sent_there_by_redirect(
        net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prowlarr or an indexer in the home network hands out downloads from there."""
    names(monkeypatch, {"indexer.example.com": ["10.0.0.9"], "files.example.org": ["10.0.0.10"],
                        "other.example.org": ["192.168.1.7"]})
    net.add(Echo("files.example.org"))
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(
        302, headers={"location": "http://other.example.org/f"}) if request.url.path == "/hop" else httpx.Response(200)))
    landed = net.add(Echo("other.example.org"))
    assert fetch_link("http://indexer.example.com/api", "http://files.example.org/g").status_code == 200
    assert fetch_link("http://indexer.example.com/api", "http://indexer.example.com/hop").status_code == 200
    assert len(landed.requests) == 1
    net.add(Echo("lan.example.org"))
    names(monkeypatch, {"indexer.example.com": ["127.0.0.1"], "lan.example.org": ["10.1.2.3"]})
    assert fetch_link("http://indexer.example.com/api", "http://lan.example.org/g").status_code == 200


def test_a_private_indexer_never_reaches_a_metadata_service(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    names(monkeypatch, {"indexer.example.com": ["10.0.0.9"], "meta.example.org": ["169.254.169.254"]})
    meta = net.add(Echo("meta.example.org"))
    net.add(Echo("indexer.example.com", lambda request: httpx.Response(
        302, headers={"location": "http://[fd00:ec2::254]/x"})))
    for link in ("http://meta.example.org/g", "http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/x",
                 "http://indexer.example.com/hop"):
        with pytest.raises(gate.GateError) as caught:
            fetch_link("http://indexer.example.com/api", link)
        assert caught.value.code == "refused"
    assert meta.requests == []


def test_an_indexer_with_public_and_private_addresses_counts_as_public(net: FakeNetwork,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an indexer whose every address is private opens the private networks to its links."""
    names(monkeypatch, {"indexer.example.com": ["10.0.0.9", "93.184.216.34"], "lan.example.org": ["10.1.2.3"]})
    lan = net.add(Echo("lan.example.org"))
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "http://lan.example.org/g")
    assert caught.value.code == "refused" and lan.requests == []


def test_a_name_that_does_not_resolve_is_unreachable_for_a_link(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(host: str, _port: int) -> list[str]:
        if host == "gone.example.org":
            raise outbound.Unreachable(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(gate, "resolver", resolve)
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", "https://gone.example.org/g")
    assert caught.value.code == "unreachable"


@pytest.mark.parametrize("link", ["ftp://downloads.example.org/f", "file:///etc/passwd", "//downloads.example.org/f",
                                  "downloads.example.org/f", "https://", "http://user:pw@:80/"])
def test_a_link_that_is_no_web_link_is_not_asked(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch, link: str) -> None:
    names(monkeypatch, {})
    assert gate.is_web_link(link) is False
    before = len(net.requests)
    with pytest.raises(gate.GateError) as caught:
        fetch_link("https://indexer.example.com/api", link)
    assert caught.value.code == "bad_address" and len(net.requests) == before


def test_the_log_of_a_link_names_the_host_and_never_the_link(net: FakeNetwork, monkeypatch: pytest.MonkeyPatch,
                                                            caplog: pytest.LogCaptureFixture) -> None:
    names(monkeypatch, {})
    net.add(Echo("downloads.example.org"))
    key = "k" * 32
    with caplog.at_level(logging.DEBUG, logger="nexbooks.procure"):
        fetch_link("https://indexer.example.com/api", f"https://downloads.example.org/dl/{key}/file.nzb?apikey={key}")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "downloads.example.org" in text and key not in text and "file.nzb" not in text


def test_a_link_is_followed_to_another_port_over_real_sockets_behind_a_private_indexer(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The same two servers as above: for the link of a release the redirect to the other port is followed (the indexer
    lies on the loopback, a private network), and the file is read from there."""
    monkeypatch.setattr(gate, "transport", None)
    monkeypatch.setattr(gate, "resolver", lambda _host, _port: ["127.0.0.1"])
    servers = _TwoPorts()
    try:
        with gate.Session(f"http://127.0.0.1:{servers.port_a}", label="test") as session:
            answer = session.request("GET", session.url("/to-b"), log_url=False, anywhere=True)
        assert answer.text == "ok"
        assert servers.hits["a"] == ["/to-b"] and servers.hits["b"] == ["/landed"]
    finally:
        servers.close()
