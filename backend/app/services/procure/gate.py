"""The way out to the indexers, Prowlarr and the download programs: every request to them goes through here, and only
through here. It is not the way to the sources (``services/sources/http.py``, a table of hosts of the internet): what
the operator connects lies in their own network as often as not.

What a request has to pass:

1. **The address.** http or https, a host name or an address, no user and no password in it, no query and no fragment
   (``normalize_address``). The operator types it; nobody else ever does (the routes are the operator's).
2. **The host and the port.** A session is made for one connection and talks to the host *and the port* of its address
   and to no other (``host_not_allowed``): what a release or an answer names is never followed to a host or to a port of
   the same host that the operator did not enter. **One exception: the link of a release** (``Session.request(...,
   anywhere=True)``), which lies on whatever host the indexer names, as the *arr programs have it. That request and its
   redirects go to any host over http or https, each hop checked anew after its name is resolved: behind an indexer on
   the internet no hop may reach a private, loopback, shared or reserved address; behind a private indexer private
   addresses are allowed. Such a host gets none of the session's headers, and nexbooks adds no key to the link.
3. **The way out** (``services/outbound.py``): the name resolved once, every address it gives checked, the connection to
   exactly those addresses. **Private and loopback addresses are allowed on purpose** (a homelab lives in them); what is
   never reached, whatever anybody types, stays so: link-local space and the metadata services of cloud machines.
4. **Redirects** are followed here, at most twice, and only to the same host and the same port; the scheme stays, or
   goes from http to https and then keeps the port number or goes from the port 80 to the port 443 (a program behind a
   proxy that sends http to https). Nothing else is followed, so no answer can lead the server to another place.
5. **Time and size.** One deadline for the whole request, the answer read included (30 s, 90 s behind Prowlarr), and a
   size: an answer 10 MB, an NZB file 20 MB, a torrent file 10 MB (``ANSWER_MAX``, ``NZB_MAX``, ``TORRENT_MAX``).
6. **The log.** One line per request with the host, the path and the *names* of the query (a value only of the few that
   say nothing: ``t=search``); never a key, a release name or a path of a file, never a body. A segment of the path that
   looks like a key is masked.

The tests put their own resolver and transport in (``resolver``, ``transport``); no test reaches a real host.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Self
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

import httpx

from .. import outbound

logger = logging.getLogger("nexbooks.procure")

#: One request: the whole of it, the answer included; and the connection itself.
SECONDS = 30.0
SLOW_SECONDS = 90.0
CONNECT_SECONDS = 10.0
#: What an answer may weigh. A file of a release is bigger: an NZB 20 MB, a torrent file 10 MB.
ANSWER_MAX = 10 * 1024 * 1024
NZB_MAX = 20 * 1024 * 1024
TORRENT_MAX = 10 * 1024 * 1024
MAX_REDIRECTS = 2
ADDRESS_MAX = 1024

#: Resolves a name to its addresses; the tests put their own in.
resolver: Callable[[str, int], list[str]] = outbound.resolve
#: The way out; the tests put a stand-in of the services here.
transport: httpx.BaseTransport | None = None
ticks: Callable[[], float] = time.monotonic

#: The values of a query that may stand in the log: they name a function, not a book or a key.
_LOGGABLE = frozenset({"t", "mode", "limit", "extended", "output", "section", "o", "cat"})
_KEYLIKE = re.compile(r"^[A-Za-z0-9_-]{24,}$")


class GateError(Exception):
    """A request that gave no answer. ``code`` is one of: ``bad_address`` (the address cannot be used), ``refused`` (an
    address that is never reached), ``host_not_allowed``, ``unreachable``, ``timeout``, ``too_large``, ``redirect``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_address(raw: str | None, *, default_path: str | None = None) -> str:
    """The address the operator typed, tidied: ``scheme://host[:port][/path]`` with the scheme and the host in lower
    case and no slash at the end. ``GateError("bad_address")`` for anything else: another scheme, a user or a password
    in it, a query or a fragment, a space or a control character, no host, a port that is no number, or a very long
    text. ``default_path`` is put after an address that has no path (an indexer at ``https://x.example.com`` is at
    ``/api``)."""
    text = (raw or "").strip()
    if not text or len(text) > ADDRESS_MAX or any(ord(char) <= 32 or ord(char) == 127 for char in text):
        raise GateError("bad_address")
    try:
        parts = urlsplit(text)
        port = parts.port
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise GateError("bad_address") from exc
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise GateError("bad_address")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment or "?" in text:
        raise GateError("bad_address")
    if port == 0:
        raise GateError("bad_address")
    shown = f"[{host}]" if ":" in host else host
    netloc = shown if port is None else f"{shown}:{port}"
    path = parts.path.rstrip("/")
    if not path and default_path:
        path = default_path
    return f"{parts.scheme.lower()}://{netloc}{path}"


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def port_of(url: str) -> int | None:
    """The port a request to ``url`` goes to: the one written, else the one of the scheme (None when it is unusable)."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        return parts.port or {"http": 80, "https": 443}.get(scheme)
    except ValueError:
        return None


def same_place(origin: str, target: str, *, upgrade: bool = True) -> bool:
    """Whether a request to ``target`` stays where ``origin`` is: the same host and the same port, the scheme the same
    or (``upgrade``) raised from http to https, then with the same port number or from the port 80 to the port 443.
    Nothing else: not another port of the same host, not https back to http."""
    try:
        before, after = urlsplit(origin).scheme.lower(), urlsplit(target).scheme.lower()
    except ValueError:
        return False
    if after not in ("http", "https") or before not in ("http", "https"):
        return False
    host = host_of(origin)
    if not host or host != host_of(target):
        return False
    port_before, port_after = port_of(origin), port_of(target)
    if port_before is None or port_after is None:
        return False
    if before == after:
        return port_before == port_after
    if not upgrade or (before, after) != ("http", "https"):
        return False
    return port_after == port_before or (port_before, port_after) == (80, 443)


def key_follows(saved: str, typed: str) -> bool:
    """Whether the key saved for the address ``saved`` may be sent to the address ``typed``: only while the host and the
    port stay (the scheme may be raised to https, never lowered). Another place needs the key typed again."""
    return same_place(saved, typed)


def basic_auth(user: str, secret: str) -> str:
    """The value of an ``Authorization`` header for HTTP Basic."""
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def with_query(url: str, params: dict[str, Any] | None) -> str:
    """``url`` with ``params`` added to its query (values as text)."""
    if not params:
        return url
    return str(httpx.URL(url).copy_merge_params({key: str(value) for key, value in params.items()}))


def safe_url(url: str) -> str:
    """Scheme, host, port, path and the query as names only (``?t=search&apikey=***``): what may stand in the log."""
    try:
        parts = urlsplit(url)
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "(address hidden)"
    path = "/".join("***" if _KEYLIKE.match(segment) else segment for segment in parts.path.split("/"))
    pairs = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        shown = quote(value, safe="") if name.lower() in _LOGGABLE and len(value) <= 40 else "***"
        pairs.append(f"{quote(name, safe='')}={shown}")
    return f"{parts.scheme}://{parts.hostname or ''}{port}{path}" + (f"?{'&'.join(pairs)}" if pairs else "")


def origin_only(url: str) -> str:
    """Scheme, host and port and nothing after it: for the link of a release, whose path and query hold a key."""
    try:
        parts = urlsplit(url)
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "(address hidden)"
    return f"{parts.scheme}://{parts.hostname or ''}{port}/..."


def _allow_all(_ip: outbound.IP) -> None:
    """Private, loopback and shared addresses are the operator's own network. (``outbound.pin`` refuses what is never
    reached before this is asked.)"""


def _public_only(ip: outbound.IP) -> None:
    """The rule for a host that is not the connection's own, behind a connection that lies on the internet: nothing
    private, nothing on the loopback, nothing shared or reserved."""
    if not outbound.public(str(ip)):
        raise outbound.Refused("private")


def is_web_link(url: str) -> bool:
    """Whether ``url`` is an http or https link with a host (the link of a release)."""
    try:
        parts = urlsplit(url)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname) and parts.port != 0
    except ValueError:
        return False


def _level(status: int) -> int:
    if status >= 500 or status in (401, 403, 429):
        return logging.WARNING
    return logging.INFO if status >= 400 else logging.DEBUG


class Session:
    """The requests to one connection: use as ``with Session(address, label="indexer") as session``. The address is the
    operator's; the session talks to its host and no other. Cookies are kept for the life of the session (a download
    program that logs in with one)."""

    def __init__(self, address: str, *, label: str, headers: dict[str, str] | None = None,
                 seconds: float = SECONDS) -> None:
        self.address = address.rstrip("/")
        self.label = label
        self.headers = dict(headers or {})
        self.seconds = seconds
        self.host = host_of(address)
        self._client: httpx.Client | None = None

    def __enter__(self) -> Self:
        self._client = outbound.client(transport)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def url(self, path: str) -> str:
        """The address of the connection with ``path`` after it (a path starts with ``/``)."""
        return self.address + path

    def _home_private(self) -> bool:
        """Whether the connection itself lies in a private network: every address its host has is private, loopback or
        shared. An indexer or a Prowlarr in the home network hands out its downloads from there."""
        try:
            addresses = resolver(self.host, outbound.port_of(self.address))
        except (outbound.Unreachable, ValueError):
            return False
        return bool(addresses) and all(not outbound.public(address) for address in addresses)

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None, limit: int = ANSWER_MAX, seconds: float | None = None,
                log_url: bool = True, magnet: bool = False, anywhere: bool = False,
                **sent: Any) -> httpx.Response:
        """One request. Returns the answer whatever its status (the caller reads it); raises ``GateError`` when there is
        none. ``log_url=False`` writes the origin only (the link of a release holds a key). ``magnet``: a redirect to a
        ``magnet:`` link is answered as it came (the release of a torrent indexer may be one), not followed.

        ``anywhere`` is for the link of a release, which lies on whatever host the indexer names (as with the *arr
        programs): the link and every redirect, up to ``MAX_REDIRECTS``, are followed to any host over http or https.
        The one limit is the network: behind a connection on the internet no hop may reach a private, loopback, shared
        or reserved address (checked for every hop, after the name is resolved); behind a private connection private
        addresses are allowed; the metadata services never. A host that is not the connection's own gets none of the
        session's headers, only those handed in with this call."""
        if self._client is None:
            raise RuntimeError("A session is used outside of 'with'")
        current = with_query(url, params)
        origin = self.address
        own_headers = {**self.headers, **(headers or {})}
        outside_rule: Callable[[outbound.IP], None] | None = None
        budget = self.seconds if seconds is None else seconds
        started = ticks()
        deadline = started + budget
        shown = safe_url if log_url else origin_only
        try:
            for hop in range(MAX_REDIRECTS + 1):
                rule: Callable[[outbound.IP], None] = _allow_all
                asked = own_headers
                if anywhere:
                    if not is_web_link(current):
                        raise GateError("bad_address")
                    if host_of(current) != self.host:
                        asked = dict(headers or {})
                        if outside_rule is None:
                            outside_rule = _allow_all if self._home_private() else _public_only
                        rule = outside_rule
                elif not same_place(origin, current):
                    logger.warning("A request to a host the operator did not enter was stopped label=%s", self.label)
                    raise GateError("host_not_allowed")
                place = outbound.pin(current, resolver, rule)
                answer = outbound.send(self._client, method, place, asked, deadline=deadline, ticks=ticks, limit=limit,
                                       connect_seconds=CONNECT_SECONDS, **sent)
                logger.log(_level(answer.status_code), "%s %s -> %s in %dms", method, shown(current),
                           answer.status_code, int((ticks() - started) * 1000))
                if answer.status_code not in (301, 302, 303, 307, 308):
                    return answer
                location = answer.headers.get("location", "").strip()
                if magnet and location[:7].lower() == "magnet:":
                    return answer
                try:
                    target = urljoin(current, location)
                except ValueError as exc:
                    raise GateError("redirect") from exc
                if hop >= MAX_REDIRECTS or not (anywhere or same_place(current, target)):
                    raise GateError("redirect")
                origin = target
                if answer.status_code in (301, 302, 303):
                    method, sent = "GET", {}
                current = target
            raise GateError("redirect")
        except GateError:
            raise
        except outbound.Refused as exc:
            logger.warning("%s %s was refused: %s", method, origin_only(current), exc.reason)
            raise GateError("refused") from exc
        except outbound.Unreachable as exc:
            logger.warning("%s %s failed: name not found", method, origin_only(current))
            raise GateError("unreachable") from exc
        except outbound.TooLarge as exc:
            logger.warning("%s %s failed: answer too large", method, origin_only(current))
            raise GateError("too_large") from exc
        except (outbound.Late, httpx.TimeoutException) as exc:
            logger.warning("%s %s failed: no answer in time", method, origin_only(current))
            raise GateError("timeout") from exc
        except httpx.InvalidURL as exc:
            # A redirect to something that is no address at all (httpx builds the next request before it is asked).
            logger.warning("%s %s answered with an address that cannot be used", method, origin_only(current))
            raise GateError("redirect") from exc
        except httpx.HTTPError as exc:
            logger.warning("%s %s failed: %s", method, origin_only(current), type(exc).__name__)
            raise GateError("unreachable") from exc


def limit_for(protocol: str) -> int:
    """What the file of a release may weigh: an NZB 20 MB, a torrent file 10 MB."""
    return NZB_MAX if protocol == "usenet" else TORRENT_MAX
