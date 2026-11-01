"""Request id, timing and security headers for every request."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
import weakref
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .errors import WRONG_CREDENTIAL
from .security import SESSION_COOKIE
from .services import logs, pictures

logger = logging.getLogger("nexbooks.api")


def _database_busy(exc: BaseException) -> bool:
    """SQLite's "database is locked" (or "busy"), however deep in the chain of causes."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, sqlite3.OperationalError) and ("locked" in str(seen) or "busy" in str(seen)):
            return True
        # SQLAlchemy keeps the driver's error as ``orig`` (and as the cause, when it raised it itself).
        seen = getattr(seen, "orig", None) or seen.__cause__ or seen.__context__
    return False


#: Paths whose calls explain nothing but fill the log.
QUIET_PATHS = ("/api/v1/health", "/api/v1/logs")
SLOW_MS = 3000
#: What takes long by nature: a backup of a big database takes a while.
SLOW_EXPECTED = ("/api/v1/backups",)

#: The interface sets inline styles, hence 'unsafe-inline' for styles only. Scripts stay strict.
#: ``blob:`` for images: a picture is shown before it is uploaded. The service worker (``/sw.js``, Web Push) and the
#: manifest come from the site itself, said out loud: a ``worker-src 'none'`` here once broke Web Push in Nexview.
CSP = (
    b"default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    b"connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    b"form-action 'self'; object-src 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)

#: The schemes a trusted proxy may hand on, per kind of connection.
_SCHEMES = {"http": ("http", "https"), "websocket": ("ws", "wss")}


def forwarded_proto(headers: list[tuple[bytes, bytes]], kind: str) -> str | None:
    """The scheme the last proxy wrote into ``X-Forwarded-Proto``: the rightmost value of the last such header (what
    stands left of it a sender may have written itself), without regard to case, and only one this kind of connection
    has (http, https; ws, wss for a WebSocket). None for anything else."""
    values = [value for name, value in headers if name.lower() == b"x-forwarded-proto"]
    if not values:
        return None
    proto = values[-1].decode("latin-1").split(",")[-1].strip().lower()
    return proto if proto in _SCHEMES.get(kind, ()) else None


class ForwardedProtoMiddleware:
    """The scheme a proxy that ends TLS reports. uvicorn runs with ``--no-proxy-headers`` (it would believe any sender
    that writes ``X-Forwarded-For``), so behind such a proxy every request looked like plain http: the redirect address
    of a sign-in provider, ``request.base_url`` and the ``Secure`` flag said ``http``. Only a peer in
    ``NEXBOOKS_TRUSTED_PROXIES`` is believed (none from the start), only its ``X-Forwarded-Proto`` and only the scheme:
    host and the sender's address stay as they were. Without trusted proxies nothing changes."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        from .deps import is_trusted_proxy

        kind = scope.get("type")
        if kind in _SCHEMES and scope.get("client") and is_trusted_proxy(str(scope["client"][0])):
            proto = forwarded_proto(scope.get("headers", []), kind)
            if proto:
                scope = {**scope, "scheme": proto}
        await self.app(scope, receive, send)


class RequestContextMiddleware:
    """Pure ASGI, so streaming responses and WebSockets are not buffered."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(3)
        scope.setdefault("state", {})["request_id"] = request_id
        token = logs.bind_request(request_id)
        start = time.perf_counter()
        status = 0
        path = scope.get("path", "?")

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                # A response that brings its own policy keeps it; every other gets the app's.
                present = {name.lower() for name, _value in headers}
                headers.extend(header for header in SECURITY_HEADERS if header[0] not in present)
                # What the API answers is private: kept by no browser and no proxy, unless a route says otherwise
                # (the profile pictures, under addresses that change with them).
                if path.startswith(("/api/", KOBO_PREFIX)) and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
            await send(message)

        method = scope.get("method", "WS")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration = (time.perf_counter() - start) * 1000
            logger.exception("Unhandled error on %s %s after %dms", method, path, duration)
            raise
        else:
            if scope["type"] != "http":
                return
            duration = (time.perf_counter() - start) * 1000
            if status >= 500:
                logger.error("%s %s -> %s in %dms", method, path, status, duration)
            elif duration >= SLOW_MS and not path.startswith(QUIET_PATHS + SLOW_EXPECTED):
                logger.warning("Slow request: %s %s -> %s in %dms", method, path, status, duration)
            elif not path.startswith(QUIET_PATHS):
                logger.debug("%s %s -> %s in %dms", method, path, status, duration)
        finally:
            logs.unbind_request(token)


#: Methods that change something. They must name their browser tab (see ``GuardMiddleware``).
CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CLIENT_HEADER = b"x-nexbooks-client"
CLIENT_PATTERN = re.compile(rb"^[A-Za-z0-9_-]{8,64}$")
#: Largest body an ordinary request may carry.
MAX_BODY = 16 * 1024 * 1024
#: Where another limit holds, checked while streaming: a backup brought in from elsewhere, and every route that takes
#: a picture, whose decoder says the same number (``pictures.MAX_BYTES``).
LARGE_BODIES = {
    "/api/v1/backups/upload": 64 * 1024**3,
    "/api/v1/auth/avatar": pictures.MAX_BYTES,
}


#: A file of a book uploaded from the browser: the route checks the limit of its kind and the space left
#: itself, before it reads and while it reads; this is the outer wall.
UPLOAD_FILE = re.compile(r"^/api/v1/uploads/\d{1,12}/files$")
UPLOAD_FILE_MAX = 4 * 1024**3


#: What a body may hold at the two addresses where the app fetches its first token (``APP_CLAIM``): a secret or a code
#: and a name, a few hundred bytes. Nobody has signed in there, so nobody may make the server read megabytes.
CLAIM_BODY_MAX = 4 * 1024


#: What a body may hold at the sync of KOReader (``/kosync``): a report is a few hundred bytes. Nobody has signed in
#: when the body arrives, so nobody may make the server read megabytes.
KOSYNC_PREFIX = "/kosync/"
KOSYNC_BODY_MAX = 16 * 1024
#: The same for the sync of a Kobo (``/kobo/<secret>/...``): the biggest body is the state of one book it reports. The
#: device reports it as the first thing a request holds, before anything says who it is.
KOBO_PREFIX = "/kobo/"
KOBO_BODY_MAX = 32 * 1024
#: The reports a device sends about itself come in batches, and nexbooks reads none of them.
KOBO_EVENTS = "/v1/analytics/"
KOBO_EVENTS_MAX = 1024 * 1024
#: Where a device speaks for a secret of its own and reads no cookie: no tab is named, none could be.
SELF_SIGNED = (KOSYNC_PREFIX, KOBO_PREFIX)


def body_limit(path: str) -> int:
    if path.startswith(KOSYNC_PREFIX):
        return KOSYNC_BODY_MAX
    if path.startswith(KOBO_PREFIX):
        return KOBO_EVENTS_MAX if KOBO_EVENTS in path else KOBO_BODY_MAX
    if UPLOAD_FILE.match(path):
        return UPLOAD_FILE_MAX
    if APP_CLAIM.match(path) or APP_CONFIRM.match(path):
        return CLAIM_BODY_MAX
    return LARGE_BODIES.get(path, MAX_BODY)


def _too_large(limit: int) -> tuple[dict, dict]:
    return _refuse(413, "too_large", "The request is too large.", max_mb=limit // (1024 * 1024))


class BodyTooLarge(Exception):
    pass


def _refuse(status: int, code: str, text: str, **values: object) -> tuple[dict, dict]:
    body = json.dumps({"detail": {"code": code, "message": text, **values}}).encode()
    start = {
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    }
    return start, {"type": "http.response.body", "body": body}


#: The start of a device token and of an API key (``services.devices.TOKEN_PREFIX``, ``services.apikeys.KEY_PREFIX``;
#: a test keeps the two in step).
TOKEN_STARTS = (b"nbd_", b"nbk_")


def _carries_token(value: bytes) -> bool:
    return value[:7].lower() == b"bearer " and value[7:].startswith(TOKEN_STARTS)


#: The addresses where the app gets its first token (``routers/app1.py``): two that fetch it with the secret or the code
#: of a pairing, and the ones where a device without a camera asks for a code of its own, asks how it stands and lets
#: it go. They have no token to show, the secret or the code is the proof. A request to them without a cookie of ours is
#: the app's, no tab. (Not a ``POST`` to ``APP_CONFIRM``: the app that confirms a device speaks for its device token,
#: like all the rest. A ``DELETE`` of a request whose id happens to read ``confirm`` is still the device's.)
APP_CLAIM = re.compile(r"^/api/app/1/pairing/(?:claim|request(?:/[^/]{1,200})?|[^/]{1,200}/claim)$")
#: Where the app confirms the request of a device without a camera, or looks at it first (``routers/app1.py``): the body
#: holds an id and a code. It is read before the token is judged, so it has the small limit too.
APP_CONFIRM = re.compile(r"^/api/app/1/pairing/request/(?:confirm|preview)$")


def is_app_claim(method: str, path: str) -> bool:
    """Whether a request goes to a way in of the app that needs no token (``APP_CLAIM``)."""
    return APP_CLAIM.match(path) is not None and not (method == "POST" and APP_CONFIRM.match(path) is not None)


def _is_browser(cookies: bytes) -> bool:
    """Whether the request carries a cookie of nexbooks (all start with ``nexbooks_``): the session, a sign-in step, the
    invitation or provider run. Such a request is a browser's, whatever else it says."""
    return b"nexbooks_" in cookies


class GuardMiddleware:
    """Two walls in front of every route, including routes still to come.

    * **A change must name its tab** in ``X-Nexbooks-Client``. A page on another site can make a browser
      send a form, but not a request with a header of its own (that needs CORS, which nexbooks does not allow): so
      the header is the wall against cross-site requests too. A program with ``Authorization: Bearer nbd_...`` (or
      ``nbk_...``) and no cookie of ours needs no tab: it speaks for a token, not for a cookie. So does the app that
      fetches its first token (``APP_CLAIM``): it speaks for a secret it shows in the body.
    * **A body has a size limit** before anything reads it: the declared length is checked first, and a body sent
      without one is counted while it arrives.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(("/api/", *SELF_SIGNED)):
            await self.app(scope, receive, send)
            return
        if path.startswith(KOBO_PREFIX) and "//" in path:
            # The address in the settings file of a Kobo ends in a slash: the device only adds its path to what it was
            # given, and it can be told no redirect.
            scope["path"] = path = re.sub(r"/{2,}", "/", path)
        headers = dict(scope.get("headers") or [])
        # A request with one of our tokens in ``Authorization`` and no cookie of ours is a program's, judged by the
        # token alone (``deps.require_account``). Any other ``Authorization`` header (Basic, a made-up token) does not
        # stand in for the tab header, and neither does a token next to a cookie: a made-up one would take the wall
        # away from a request the cookie answers (log out, sign-in steps, accepting an invitation).
        browser = _is_browser(headers.get(b"cookie", b""))
        exempt = not browser and (_carries_token(headers.get(b"authorization", b""))
                                  or is_app_claim(str(scope.get("method", "")), scope["path"]))
        # (The sync of KOReader needs no tab: it reads no cookie, only the two headers of its own, which a page on
        # another site cannot send. The sync of a Kobo reads no cookie either, only the secret in its address, which a
        # page on another site does not know.)
        if (scope.get("method") in CHANGING and not exempt and not path.startswith(SELF_SIGNED)
                and not CLIENT_PATTERN.match(headers.get(CLIENT_HEADER, b""))):
            for message in _refuse(400, "client_required", "Changes need the header X-Nexbooks-Client."):
                await send(message)
            return
        limit = body_limit(path)
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            for message in _too_large(limit):
                await send(message)
            return
        received = 0
        overflow = False

        async def counting_receive() -> dict:
            nonlocal received, overflow
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    overflow = True
                    raise BodyTooLarge
            return message

        started = False

        async def tracking_send(message: dict) -> None:
            nonlocal started
            # The framework turns the aborted read into an answer of its own (400); the caller gets the true one.
            if overflow:
                if message["type"] == "http.response.start" and not started:
                    started = True
                    for refusal in _too_large(limit):
                        await send(refusal)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                for message in _too_large(limit):
                    await send(message)


#: The bytes of a file: the route is the one thing that can keep a server busy for as long as a person likes.
#: The same bytes for the iPhone app (``/api/app/1/files/{id}/content``).
FILE_PATH = re.compile(r"^/api/(?:v1|app/1)/files/\d+/(?:content|download)$")


def _raw_credential(headers: dict[bytes, bytes]) -> bytes | None:
    """The secret a request brings as its credential, the token of ``Authorization: Bearer`` or else the session cookie,
    as the routes read them (a token wins over a cookie); None for a request that brings none."""
    token = headers.get(b"authorization", b"")
    if token[:7].lower() == b"bearer " and token[7:]:
        return token[7:]
    wanted = SESSION_COOKIE.encode("ascii") + b"="
    for crumb in headers.get(b"cookie", b"").split(b";"):
        crumb = crumb.strip()
        if crumb.startswith(wanted):
            return crumb[len(wanted):] or None
    return None


def _credential_of(headers: dict[bytes, bytes]) -> bytes | None:
    """A short name for the credential a request brings, the session cookie or a token (not the credential itself, which
    is a secret and is not kept), or None for a request that brings none. Nothing is believed here: a made-up credential
    gets a name like any other, and the route turns it away."""
    value = _raw_credential(headers)
    return hashlib.blake2b(value, digest_size=12).digest() if value else None


class FileGateMiddleware:
    """At most ``max_streams`` requests for the bytes of a file are being answered at the same moment for one credential
    (a session, a device token): the rest are turned away with ``429`` and ``Retry-After`` *before* anything is looked
    up. Measured: 300 requests at once for one person's file cost every other person's pages two seconds, because each
    one asked the database who it was; the ones turned away here cost the server nothing. The route counts again per
    person (``services.brakes.open_stream``), which also holds a person with several browsers to the same number.

    A player has one or two requests of this kind open at a time. A request without a credential is not counted: the
    route says 401 to it, as it does to every other.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._open: dict[bytes, int] = {}

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        asks_for_a_file = scope["type"] == "http" and scope.get("method") in ("GET", "HEAD")
        if not asks_for_a_file or not FILE_PATH.match(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        key = _credential_of(dict(scope.get("headers") or []))
        if key is None:
            await self.app(scope, receive, send)
            return
        with self._lock:
            count = self._open.get(key, 0)
            full = count >= max(1, get_settings().max_streams)
            if not full:
                self._open[key] = count + 1
        if full:
            start, body = _refuse(429, "too_many_streams", "Too many files at once. Try again soon.")
            start["headers"].append((b"retry-after", b"2"))
            await send(start)
            await send(body)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self._lock:
                left = self._open.get(key, 1) - 1
                if left > 0:
                    self._open[key] = left
                else:
                    self._open.pop(key, None)

    def open_requests(self) -> int:
        with self._lock:
            return sum(self._open.values())


async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or "-"
    if _database_busy(exc):
        # The database stayed busy past the wait (SQLite's busy timeout): a moment later it works. The browser gets
        # something to say and to retry instead of "went wrong".
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "busy", "message": "nexbooks is busy. Try again in a moment.",
                                "request_id": request_id}},
            headers={"X-Request-Id": request_id, "Retry-After": "2"},
        )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": f"Something went wrong on the server. Request id: {request_id}",
                "request_id": request_id,
            }
        },
        headers={"X-Request-Id": request_id},
    )


# --- The door before the sign-in -------------------------------------------------------------------------------------
#
# Measured (30 books, one process): a person's shelf took 52 ms alone and 170 to 230 ms while eight programs asked
# for it without being signed in, with no cookie, with made-up cookies or with made-up tokens; each of those requests
# went through the framework, a thread of the pool and (with a cookie) a question to the database before it heard 401.
# So what cannot be anybody's is answered here, before all of that:
#
# * **No credential at all** (no session cookie, no ``Authorization: Bearer``) on a route that wants a sign-in: 401, the
#   answer the route gives, without asking anything. Which routes want one is read from the routes themselves (a
#   dependency on ``require_*`` of ``deps``), so a route added later is covered without being named.
# * **Credentials that keep failing from one sender**: after ``FAIL_LIMIT`` wrong guesses within ``FAIL_WINDOW`` seconds
#   from one sender (an answer 401 that the routes mark as a wrong credential: ``sign_in_required``, ``token_invalid``,
#   ``key_invalid``; not a key at a route for devices, not switched-off keys, not a web page's ``Origin``; the same
#   credential again within the window counts once), the next
#   request of that sender that brings a credential is looked up, and only a credential that no session, device or
#   API key of the database answers to is turned away with 429. **A credential that exists is never turned away**, not
#   after a restart and not after a night: whether it exists is read from the database (one question about three
#   indexed hashes), not remembered, so that there is nothing to lose and nothing to keep in step. Where the question
#   cannot be answered (the database is busy or gone), the request goes on to the route, which says what is true.
# * **The sender** is told apart as for the sign-in brake (``deps.address_keys``): behind a proxy that nexbooks was not
#   told to believe, the made-up ``X-Forwarded-For`` does not count and the proxy as a whole does.

#: Wrong guesses one sender may make within ``FAIL_WINDOW`` seconds before the door closes for credentials it cannot
#: find. A browser whose session ran out makes a handful before it shows the sign-in page.
FAIL_LIMIT = 30
FAIL_WINDOW = 60.0
#: How many senders are counted at once (the oldest go first): every new IPv6 network would otherwise stay.
MAX_SENDERS = 4096
#: How long the door waits for the database when it asks whether a credential exists (it asks inside the event loop).
LOOKUP_WAIT = 0.05

_DEPENDENCIES = frozenset({"require_account", "require_reader", "require_browser_account", "require_operator",
                           "require_device"})

_FIND = ("SELECT 1 FROM auth_sessions WHERE token_hash = ?1 UNION ALL SELECT 1 FROM devices WHERE token_hash = ?1 "
         "UNION ALL SELECT 1 FROM api_keys WHERE token_hash = ?1 LIMIT 1")


def _wants_sign_in(route: Any) -> bool:
    pending: list[Any] = [route.dependant]
    while pending:
        dependant = pending.pop()
        for sub in dependant.dependencies:
            call = sub.call
            if getattr(call, "__module__", "") == "app.deps" and getattr(call, "__name__", "") in _DEPENDENCIES:
                return True
            pending.append(sub)
    return False


Table = list[tuple[re.Pattern[str], frozenset[str], bool]]


def route_table(routes: list[Any]) -> Table:
    """Every route of the app: the pattern of its full address, its methods, and whether it wants a sign-in."""
    from fastapi.routing import APIRoute
    from starlette.routing import compile_path

    table: Table = []

    def walk(items: list[Any], prefix: str) -> None:
        for route in items:
            if isinstance(route, APIRoute):
                pattern, _format, _convertors = compile_path(prefix + route.path)
                table.append((pattern, frozenset(route.methods or ()), _wants_sign_in(route)))
                continue
            # A router the app included (FastAPI keeps it whole and names it ``original_router``; older versions
            # copied its routes in, which the first branch takes).
            inner = getattr(route, "original_router", None) or getattr(route, "router", None)
            if inner is not None and hasattr(inner, "routes"):
                context = getattr(route, "include_context", None)
                walk(inner.routes, prefix + (getattr(context, "prefix", None) or getattr(route, "prefix", "") or ""))

    walk(routes, "")
    return table


class CredentialLookup:
    """Whether the database knows a credential: a session, a device token or an API key whose hash it holds (whatever
    state it is in; the routes judge that). One connection of its own, read-only, kept open: opening one costs more than
    the question. It is asked inside the event loop, so it waits for the database only ``LOOKUP_WAIT`` seconds, and
    when it cannot answer it says yes (see above)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._path = ""

    def _open(self) -> sqlite3.Connection:
        from .db import engine

        path = str(engine.url.database or "")
        if self._connection is not None and path != self._path:
            self._close()
        if self._connection is None:
            connection = sqlite3.connect(path, timeout=LOOKUP_WAIT, check_same_thread=False, isolation_level=None)
            try:
                connection.execute("PRAGMA query_only = ON")
            except sqlite3.Error:
                connection.close()
                raise
            self._connection, self._path = connection, path
        return self._connection

    def _close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        with self._lock:
            self._close()

    def knows(self, credential: bytes) -> bool:
        digest = hashlib.sha256(credential.strip().strip(b'"').decode("latin-1").encode("utf-8")).hexdigest()
        with self._lock:
            try:
                return self._open().execute(_FIND, (digest,)).fetchone() is not None
            except (sqlite3.Error, OSError):
                self._close()
                return True


#: Every door there is (the app's, and the ones a test builds), so that the tests make all forget between two of them.
_doors: weakref.WeakSet[SignInGate] = weakref.WeakSet()


def forget_gate() -> None:
    for door in list(_doors):
        door.forget()


class SignInGate:
    """The door before the sign-in (see above). ``routes`` hands over the app's routes; they are read when the app
    starts (the lifespan), or at the first request if it never says, when every router is in."""

    def __init__(self, app: Any, routes: Callable[[], list[Any]]) -> None:
        self.app = app
        self._routes = routes
        self._table: Table | None = None
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        #: When a sender last brought a wrong credential, by sender and a digest of the credential: the same one again
        #: within ``FAIL_WINDOW`` is no new guess (an app with a revoked token asks for many things at once).
        self._sightings: dict[str, float] = {}
        self._lookup = CredentialLookup()
        _doors.add(self)

    def _routes_known(self) -> Table:
        if self._table is None:
            table = route_table(self._routes())
            if not any(guarded for _pattern, _methods, guarded in table):
                # Fails open (every route still asks for the sign-in itself), but then the door protects nothing: say
                # so where the operator reads it, once, as the app starts.
                logger.warning("The door before the sign-in found no route that wants a sign-in (%d routes): requests "
                               "without one reach the routes, which answer them, but the door is not doing its work.",
                               len(table))
            self._table = table
        return self._table

    def wants_sign_in(self, method: str, path: str) -> bool:
        for pattern, methods, guarded in self._routes_known():
            if method in methods and pattern.match(path):
                return guarded
        return False

    def forget(self) -> None:
        """For the tests."""
        with self._lock:
            self._fails.clear()
            self._sightings.clear()
        self._lookup.close()

    def _recent(self, sender: str, now: float) -> list[float]:
        return [stamp for stamp in self._fails.get(sender, ()) if now - stamp < FAIL_WINDOW]

    def closed_for(self, senders: list[str], now: float) -> int:
        """Seconds until the sender may bring a credential the door cannot find again; 0 while it may. The senders are
        the counts one request goes to (``deps.address_keys``); one of them being shut shuts the request out."""
        wait = 0
        with self._lock:
            for sender in senders:
                stamps = self._recent(sender, now)
                if len(stamps) >= FAIL_LIMIT:
                    wait = max(wait, max(1, int(stamps[-FAIL_LIMIT] + FAIL_WINDOW - now + 0.999)))
        return wait

    def _note_fail(self, sender: str, now: float, credential: str = "") -> None:
        with self._lock:
            if credential:
                key = sender + "|" + credential
                last = self._sightings.pop(key, None)
                self._sightings[key] = now
                while len(self._sightings) > MAX_SENDERS * 4:
                    self._sightings.pop(next(iter(self._sightings)))
                if last is not None and now - last < FAIL_WINDOW:
                    return
            stamps = self._recent(sender, now)
            stamps.append(now)
            # The newest sender goes to the end; the oldest leave first when there are too many.
            self._fails.pop(sender, None)
            self._fails[sender] = stamps[-FAIL_LIMIT:]
            while len(self._fails) > MAX_SENDERS:
                self._fails.pop(next(iter(self._fails)))

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            self._routes_known()
            try:
                await self.app(scope, receive, send)
            finally:
                self._lookup.close()
            return
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        from . import clock
        from .deps import address_keys

        headers = dict(scope.get("headers") or [])
        value = _raw_credential(headers)
        if value is None and headers.get(b"authorization", b"")[:7].lower().strip() == b"bearer":
            # "Bearer" with nothing after it: the routes take it for a wrong token (it counts against the sender).
            value = b"empty bearer"
        guarded = self.wants_sign_in(str(scope.get("method", "")), path)
        if value is None and guarded:
            for message in _refuse(401, "sign_in_required", "Sign in first."):
                await send(message)
            return
        senders: list[str] = []
        if value is not None and guarded:
            now = clock.monotonic()
            senders = [key for key, _free in address_keys(Request(scope))]
            wait = self.closed_for(senders, now)
            if wait and not self._lookup.knows(value):
                start, body = _refuse(429, "too_many_attempts", "Too many attempts. Try again later.",
                                      retry_after=wait)
                start["headers"].append((b"retry-after", str(wait).encode("ascii")))
                await send(start)
                await send(body)
                return

        async def watching(message: dict) -> None:
            wrong = (message["type"] == "http.response.start" and senders and int(message.get("status", 0)) == 401
                     and (scope.get("state") or {}).get(WRONG_CREDENTIAL))
            if wrong:
                now = clock.monotonic()
                assert value is not None
                credential = hashlib.sha256(value).hexdigest()[:32]
                for sender in senders:
                    self._note_fail(sender, now, credential)
            await send(message)

        await self.app(scope, receive, watching)
