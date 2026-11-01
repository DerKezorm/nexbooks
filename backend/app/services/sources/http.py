"""Every request nexbooks makes to a source on the internet goes through here, and only through here.

What a request has to pass, in this order:

1. **The switches.** The operator's main switch, the switch of the source, the token (Hardcover), and the rest a source
   takes after errors (``config.availability``). A source that is off gets *no* request, not even a try.
2. **The address.** https, a host out of the table of the source (``config.HOSTS``), no credentials in it, no other
port.
   The host is never built from anything a person typed.
3. **The turn** (``throttle.take``): the bucket and the count of the day. A person's request waits a few seconds at most
   and then says it is busy; the background waits as long as it takes (and stops with the server).
4. **The way out** (``services/outbound.py``): the name resolved once, every address it gives in the public internet and
   none that is never reached, the connection to exactly those addresses, no redirect followed by the library (a
   redirect
   is followed *here*, at most twice, and only to a host the table of the source names), the answer read up to a size,
   and one deadline for the whole request.
5. **The answer.** 429 and 5xx make the source rest, 401 and 403 from Hardcover mean its token is no good (the source is
   not asked again until the operator connects anew), anything else that is not an answer is an error with a code.

The tests put their own resolver and transport in; no test reaches a real service.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy.orm import Session

from ... import __version__
from .. import jobs, outbound
from . import config, throttle

logger = logging.getLogger("nexbooks.sources")

USER_AGENT = f"nexbooks/{__version__} (+https://github.com/DerKezorm/nexbooks)"
#: One request: the whole of it, the answer included; and the connection itself.
SECONDS = 20.0
CONNECT_SECONDS = 8.0
#: What an answer may weigh: the pages of the catalogues are some hundred kilobytes at most.
JSON_MAX = 2 * 1024 * 1024
#: A person's request waits this long for its turn, at most.
INTERACTIVE_WAIT = 4.0
MAX_REDIRECTS = 2
#: The longest a source's own "wait" is believed, and the wait after a 429 that names none.
RETRY_AFTER_MAX = 3600.0
RETRY_AFTER_DEFAULT = 60.0

#: Resolves a name to its addresses; the tests put their own in.
resolver: Callable[[str, int], list[str]] = outbound.resolve
#: The way out; the tests put a stand-in of the services here.
transport: httpx.BaseTransport | None = None
ticks: Callable[[], float] = time.monotonic
#: How the background waits; the tests put a clock that jumps in its place.
sleep: Callable[[float], None] = time.sleep


class _CallsOff(threading.local):
    #: Whether the question this thread asks for was called off (the catalogue search sets it): looked at before a
    #: request waits for its turn and after, so that a question that is no longer wanted sends nothing.
    check: Callable[[], bool] | None = None


calls_off = _CallsOff()


def _called_off() -> bool:
    check = calls_off.check
    return bool(check is not None and check())


class SourceError(Exception):
    """A request that did not give an answer. ``code`` is one of: ``refused`` (an address that is never reached),
    ``unreachable``, ``timeout``, ``too_large``, ``redirect``, ``rate_limited``, ``token_invalid``, ``server``,
    ``http_error``, ``bad_answer``, ``daily_limit``, ``busy``, ``off`` (no request was made), ``called_off`` (the
    question was called off before its request went out: nothing was sent, and the source is none the worse)."""

    def __init__(self, code: str, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.values = values


class SourceOff(SourceError):
    """The source may not be asked now (``state`` says why); nothing was sent and nothing counted."""

    def __init__(self, state: str) -> None:
        super().__init__("off", state=state)
        self.state = state


def _public_only(ip: outbound.IP) -> None:
    if not outbound.public(str(ip)):
        raise outbound.Refused("not public")


def host_allowed(source: str, url: str, *, redirect: bool = False) -> bool:
    """Whether ``url`` may be asked for ``source``: https, no credentials, no port but the usual, and a host out of the
    source's table (after a redirect: or one the table names for redirects)."""
    try:
        parts = urlsplit(url)
        port = parts.port
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None or parts.password is not None or port not in (None, 443):
        return False
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return False
    if host in config.HOSTS.get(source, ()):
        return True
    if redirect:
        exact, below = config.REDIRECT_HOSTS.get(source, ((), ()))
        return host in exact or any(host.endswith(suffix) for suffix in below)
    return False


def pause(seconds: float) -> None:
    """Waits for the next token, in pieces so that the server can stop in the meantime."""
    left = seconds
    while left > 0:
        if jobs.stopping.is_set():
            raise jobs.Interrupted
        piece = min(0.5, left)
        sleep(piece)
        left -= piece


def _wait_for_turn(source: str, interactive: bool) -> None:
    for _attempt in range(120):
        try:
            wait = throttle.take(source, keep=0 if interactive else throttle.BACKGROUND_KEEPS)
        except throttle.DailyLimit as exc:
            # The same as an error that says when to come back: the source rests until the day is over.
            throttle.limit_reached(source)
            raise SourceError("daily_limit") from exc
        if wait <= 0:
            return
        if interactive and wait > INTERACTIVE_WAIT:
            raise SourceError("busy", retry_after=int(wait) + 1)
        pause(wait)
    raise SourceError("busy")


def _retry_after(answer: httpx.Response) -> float:
    """How long a source that answered 429 asks to be left alone: the seconds of ``Retry-After``, or the time to the
    moment it names (``Wed, 21 Oct 2026 07:28:00 GMT``); ``RETRY_AFTER_DEFAULT`` when it says neither."""
    text = answer.headers.get("retry-after", "").strip()
    try:
        return min(RETRY_AFTER_MAX, max(1.0, float(text)))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return RETRY_AFTER_DEFAULT
    if moment is None:
        return RETRY_AFTER_DEFAULT
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return min(RETRY_AFTER_MAX, max(1.0, (moment - throttle.now()).total_seconds()))


def _roundtrip(url: str, method: str, headers: dict[str, str], json_body: Any, limit: int) -> httpx.Response:
    """One request to a checked address: the name resolved once, every address checked, the connection to those."""
    place = outbound.pin(url, resolver, _public_only)
    started = ticks()
    sent: dict[str, Any] = {} if json_body is None else {"json": json_body}
    with outbound.client(transport) as session:
        return outbound.send(session, method, place, headers, deadline=started + SECONDS, ticks=ticks, limit=limit,
                             connect_seconds=CONNECT_SECONDS, **sent)


def request(db: Session, source: str, url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
            json_body: Any = None, limit: int = JSON_MAX, interactive: bool = False,
            picture: bool = False) -> httpx.Response:
    """One request to a source. Returns the answer when it is a 2xx, or a 404 (the source answered: there is nothing);
    raises ``SourceOff`` when the source may not be asked and ``SourceError`` for everything else. For a ``picture`` a
    400 and a 410 say the same: one picture that cannot be had says nothing of the source."""
    available = config.availability(db, source)
    if not available.ready:
        raise SourceOff(available.state)
    if not host_allowed(source, url):
        # A mistake in nexbooks, not in the source: nothing is sent.
        logger.error("A request to a host that is not the source's was stopped source=%s", source)
        raise SourceError("refused")
    if _called_off():
        raise SourceError("called_off")
    _wait_for_turn(source, interactive)
    if _called_off():
        raise SourceError("called_off")
    asked = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    current = url
    asked = _without_token(source, current, asked)
    try:
        for hop in range(MAX_REDIRECTS + 1):
            answer = _roundtrip(current, method, asked, json_body, limit)
            if answer.status_code in (301, 302, 303, 307, 308):
                target = urljoin(current, answer.headers.get("location", ""))
                if hop >= MAX_REDIRECTS or not host_allowed(source, target, redirect=True):
                    throttle.fail(source, "redirect")
                    raise SourceError("redirect")
                if answer.status_code == 303:
                    method, json_body = "GET", None
                # The token of a source goes to its API only, not to wherever a redirect leads (not even to the
                # source's own picture host).
                asked = _without_token(source, target, asked)
                current = target
                continue
            return _classify(source, answer, picture=picture)
        raise SourceError("redirect")
    except outbound.Refused as exc:
        throttle.fail(source, "refused")
        raise SourceError("refused") from exc
    except outbound.Unreachable as exc:
        throttle.fail(source, "unreachable")
        raise SourceError("unreachable") from exc
    except outbound.TooLarge as exc:
        throttle.fail(source, "too_large")
        raise SourceError("too_large") from exc
    except (outbound.Late, httpx.TimeoutException) as exc:
        throttle.fail(source, "timeout")
        raise SourceError("timeout") from exc
    except httpx.HTTPError as exc:
        throttle.fail(source, "unreachable")
        raise SourceError("unreachable") from exc


def _without_token(source: str, url: str, headers: dict[str, str]) -> dict[str, str]:
    """The headers without the credentials, unless ``url`` is a host that may be sent the token of ``source``."""
    if urlsplit(url).hostname in config.TOKEN_HOSTS.get(source, ()):
        return headers
    return {key: value for key, value in headers.items() if key.lower() != "authorization"}


def _classify(source: str, answer: httpx.Response, *, picture: bool = False) -> httpx.Response:
    status = answer.status_code
    if 200 <= status < 300 or status == 404 or (picture and status in (400, 410)):
        throttle.ok(source)
        return answer
    if status in (401, 403) and source == "hardcover":
        throttle.set_state(source, "token_invalid")
        throttle.fail(source, "token_invalid")
        raise SourceError("token_invalid")
    if status == 429:
        throttle.fail(source, "rate_limited", wait=_retry_after(answer))
        raise SourceError("rate_limited")
    code = "server" if status >= 500 else "http_error"
    throttle.fail(source, code)
    raise SourceError(code, status=status)


def json_of(source: str, answer: httpx.Response) -> Any:
    """The JSON of an answer, or ``SourceError("bad_answer")`` (counted as a failure of the source). An answer nested
    deeper than the parser can follow is none either (it raises ``RecursionError``, not ``ValueError``)."""
    try:
        return answer.json()
    except (ValueError, RecursionError) as exc:
        throttle.fail(source, "bad_answer")
        raise SourceError("bad_answer") from exc
