"""The indexers of the operator: asking one (caps, a probe), keeping what it said about itself, and the rules for how
often it is asked.

* **Pacing** as the *arr apps do: at most one request every 2 seconds to one address, in this process.
* **A pause after a request limit** (429 or "request limit reached"): no request until the time the indexer names
  (``Retry-After``, at least ``MIN_PAUSE_SECONDS``), else one hour, at most a day; a limit that is hit again straight
  after the pause doubles the pause (``_streak``). The pause is kept in the database, so a restart does not forget it.
* **A pause after a fault of the indexer** (a server error, no answer): the planner and the reading of the newest
entries
  leave it alone for a time that doubles with every fault in a row (at most two hours); a person who searches is always
  served. **A refused key** (401, 403) stops every request but one a day until the key is changed or tested again.
* **The day's count and the operator's limit**: every request takes its place in the count of the day (the day of the
  instance's time zone, ``budget``) before it is made, in one statement, so that requests that run at the same moment
  never
  pass the limit; with a limit of ``n`` the indexer is not asked after ``n`` requests until midnight there (the
  background, ``background=True``, after 70 percent of ``n``). ``0`` means no limit of their own. The releases fetched
  are
  counted apart and held back only by what the indexer says about its own grabs.
* **``newznab:apilimits``**: what the indexer reports about its own limits is kept; at the limit the indexer rests until
  the time it names.
* **The key** is sealed in the database (``security.encrypt_secret``) and never read back by the API; an indexer that
  came from Prowlarr uses the key of the Prowlarr connection, so a new key there reaches every one of them.
* **Log lines** carry no key, no query values and no titles (the gate writes them).

This module connects the indexers, tests them and keeps their state; the search itself is in ``search``.
"""

from __future__ import annotations

import email.utils
import hashlib
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...errors import error
from ...models import INDEXER_KINDS, Indexer, ProwlarrConnection
from ...security import decrypt_secret, encrypt_secret
from . import budget, gate, newznab, rations
from .newznab import IndexerError

logger = logging.getLogger("nexbooks.procure")

PACE_SECONDS = 2.0
DEFAULT_PAUSE_SECONDS = 3600
MAX_PAUSE_SECONDS = 24 * 3600
#: A request limit is left alone at least this long, whatever ``Retry-After`` says.
MIN_PAUSE_SECONDS = 300
#: The pause after a fault of the indexer itself (a server error, no answer) starts here, doubles with each fault in a
#: row
#: and stops at ``MAX_FAULT_PAUSE``.
FAULT_PAUSE_SECONDS = 120
MAX_FAULT_PAUSE = 2 * 3600
#: An indexer that refused the key is asked once a day, to see whether the key was changed at its end.
KEY_REFUSED_PAUSE = 24 * 3600
NAME_MAX = 100
MAX_INDEXERS = 200
PRIORITY_MIN, PRIORITY_MAX, PRIORITY_DEFAULT = 1, 50, 25
DAILY_LIMIT_MAX = 100_000
KEY_MAX = 512
CAPS_CACHE_SECONDS = 15 * 60
CAPS_CACHE_MAX = 20
#: Why an indexer rests: ``request_limit`` (it said so), ``api_limit`` (its own ``apilimits`` are used up),
#: ``daily_limit`` (the operator's limit of the day is used up; worked out, not stored).
SECRET_CONTEXT = "indexer-key"

#: The moment on a clock that never goes back, and how the process waits; the tests set both.
ticks: Callable[[], float] = time.monotonic
sleep: Callable[[float], None] = time.sleep

_lock = threading.Lock()
_next_request: dict[str, float] = {}
_caps_cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}
#: How many faults of an indexer came in a row (the pause grows with it); a request that works clears it.
_streak: dict[int, int] = {}


def forget() -> None:
    """Forgets the pacing and the caps kept in memory (the tests)."""
    with _lock:
        _next_request.clear()
        _caps_cache.clear()
        _streak.clear()


def pace(key: str) -> None:
    """Waits until ``key`` (an address) may be asked again: one request every ``PACE_SECONDS``."""
    while True:
        with _lock:
            moment = ticks()
            ready = _next_request.get(key, 0.0)
            if moment >= ready:
                _next_request[key] = moment + PACE_SECONDS
                if len(_next_request) > 1000:
                    for stale in [name for name, due in _next_request.items() if due < moment]:
                        del _next_request[stale]
                return
            wait = ready - moment
        sleep(wait)


def retry_after_seconds(value: str | None) -> int:
    """Seconds from a Retry-After header (seconds or an HTTP date), else one hour; never below five minutes (an indexer
    that asks for two seconds would be asked thirty times an hour) and at most a day."""
    text = (value or "").strip()
    seconds: int | None = None
    if text.isdigit() and len(text) < 10:
        seconds = int(text)
    elif text:
        try:
            moment = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            moment = None
        if moment is not None:
            moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
            seconds = math.ceil((moment - clock.now()).total_seconds())
    if seconds is None or seconds <= 0:
        return DEFAULT_PAUSE_SECONDS
    return min(max(seconds, MIN_PAUSE_SECONDS), MAX_PAUSE_SECONDS)


# --- Asking an indexer ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    url: str
    kind: str
    #: ⚠️ The key in the clear, in memory only while the indexer is asked; no repr.
    api_key: str = ""
    #: Behind Prowlarr, which asks the site first: the longer deadline.
    slow: bool = False


class Client:
    """Use as ``with Client(target) as indexer: ...``. One call of ``request`` is one request to the indexer, paced.

    ``reserve`` takes the place of a request in the count of the day before the request is made and raises
    ``IndexerError`` when the limit does not allow it (``run`` hands it in); ``reserve_grab`` does the same for the
    fetch
    of a release. Without them (a test of an address not saved yet) nothing is counted."""

    def __init__(self, target: Target, reserve: Callable[[], None] | None = None,
                 reserve_grab: Callable[[], None] | None = None) -> None:
        self.target = target
        self._reserve = reserve
        self._reserve_grab = reserve_grab
        self.requests = 0
        #: Releases fetched (counted apart from the requests).
        self.grabs = 0
        #: ``apilimits`` of the last answer that had them.
        self.limits: newznab.ApiLimits | None = None
        self._session: gate.Session | None = None

    def __enter__(self) -> Self:
        seconds = gate.SLOW_SECONDS if self.target.slow else gate.SECONDS
        self._session = gate.Session(self.target.url, label="indexer", seconds=seconds,
                                     headers={"Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8"})
        self._session.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._session is not None:
            self._session.__exit__(*exc)
            self._session = None

    def request(self, params: dict[str, str | int]) -> tuple[Any, set[str]]:
        if self._session is None:
            raise RuntimeError("An indexer client is used outside of 'with'")
        if self._reserve is not None:
            self._reserve()
        pace(self.target.url)
        query: dict[str, str | int] = dict(params)
        if self.target.api_key:
            query["apikey"] = self.target.api_key
        self.requests += 1
        try:
            answer = self._session.request("GET", self.target.url, params=query)
        except gate.GateError as exc:
            raise _gate_error(exc) from exc
        return self._interpret(answer)

    def _interpret(self, answer: httpx.Response) -> tuple[Any, set[str]]:
        status = answer.status_code
        content_type = answer.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        root, namespaces = newznab.parse_xml(answer.content, content_type)
        if root is not None and newznab.local_name(root.tag) == "error":
            raw = (root.get("code") or "").strip()
            code = newznab.int_text(raw)
            description = (root.get("description") or "").casefold()
            # The description is never logged or shown: some indexers repeat the key in it.
            logger.info("Indexer answered error %s with HTTP %d", code if code is not None else "?", status)
            if code is not None and 100 <= code <= 199:
                raise IndexerError("indexer_key_rejected")
            if newznab.LIMIT_TEXT in description or status == 429:
                raise self._limit(answer.headers)
            if status in (401, 403):
                raise IndexerError("indexer_key_rejected")
            if code == 410:
                raise IndexerError("indexer_switched_off")
            raise IndexerError("indexer_error", indexer_code=code if code is not None else 0)
        if status in (401, 403):
            raise IndexerError("indexer_key_rejected")
        if status == 410:
            raise IndexerError("indexer_switched_off")
        if status == 429:
            raise self._limit(answer.headers)
        if root is None:
            if 200 <= status < 300 or status == 404:
                raise IndexerError("indexer_not_newznab")
            raise IndexerError("indexer_http_error", status=status)
        if not 200 <= status < 300:
            raise IndexerError("indexer_http_error", status=status)
        return root, namespaces

    @staticmethod
    def _limit(headers: httpx.Headers) -> IndexerError:
        seconds = retry_after_seconds(headers.get("retry-after"))
        logger.warning("Indexer request limit reached, no requests for %d seconds", seconds)
        return IndexerError("indexer_limit_reached", 429, retry_after=seconds)

    def caps(self) -> dict[str, Any] | None:
        """The caps, or None when missing or broken. A key that is refused, a limit and a connection that fails are
        raised: an indexer that cannot be asked cannot be saved."""
        try:
            root, _namespaces = self.request({"t": "caps"})
        except IndexerError as exc:
            if exc.code in _FATAL_FOR_CAPS:
                raise
            logger.info("Indexer caps not usable (%s), the defaults apply", exc.code)
            return None
        caps = newznab.parse_caps(root)
        if caps is None:
            logger.info("Indexer caps not usable (no caps document), the defaults apply")
        return caps

    def probe(self, categories: list[int]) -> newznab.Feed:
        """One feed request without a query in the book categories: ``t=search`` with ``limit=1``. (Not ``t=book``,
        which some indexers answer with a hundred books of any author.)"""
        params: dict[str, str | int] = {"t": "search"}
        if categories:
            params["cat"] = ",".join(str(category) for category in categories)
        params.update({"extended": 1, "limit": 1})
        root, namespaces = self.request(params)
        feed = newznab.parse_feed(root, namespaces)
        if feed.limits is not None:
            self.limits = feed.limits
        if feed.kind is not None and feed.kind != self.target.kind:
            raise IndexerError("indexer_wrong_kind")
        return feed


    def fetch(self, link: str, protocol: str) -> tuple[bytes, str]:
        """The file of a release (an NZB, a torrent) behind its link, or the magnet link a torrent indexer sends
        instead: ``(content, magnet)``, one of them empty. The link lies on whatever host the indexer names (as with the
        *arr programs), also after a redirect; ``gate`` lets it reach no private address when the indexer itself lies on
        the internet, and never a metadata service (``release_link_private``). ⚠️ The link carries the key or a
        passkey: only its origin is logged, and nexbooks adds nothing of its own to it. One request, paced like every
        other."""
        if self._session is None:
            raise RuntimeError("An indexer client is used outside of 'with'")
        if link[:7].lower() == "magnet:":
            return b"", link
        if not gate.is_web_link(link):
            raise IndexerError("release_file_invalid")
        if self._reserve_grab is not None:
            self._reserve_grab()
        pace(self.target.url)
        self.grabs += 1
        accept = "application/x-nzb" if protocol == "usenet" else "application/x-bittorrent"
        try:
            answer = self._session.request("GET", link, headers={"Accept": accept}, limit=gate.limit_for(protocol),
                                           log_url=False, magnet=protocol == "torrent", anywhere=True)
        except gate.GateError as exc:
            if exc.code == "too_large":
                raise IndexerError("release_file_invalid") from exc
            if exc.code == "refused":
                raise IndexerError("release_link_private") from exc
            if exc.code in ("redirect", "bad_address"):
                raise IndexerError("release_fetch_failed") from exc
            raise _gate_error(exc) from exc
        status = answer.status_code
        if 300 <= status < 400:
            location = answer.headers.get("location", "").strip()
            if location[:7].lower() == "magnet:" and len(location) <= 4000:
                return b"", location
            raise IndexerError("release_fetch_failed", status=status)
        if status == 429:
            raise self._limit(answer.headers)
        if status in (401, 403):
            raise IndexerError("indexer_key_rejected")
        if not 200 <= status < 300:
            raise IndexerError("release_fetch_failed", status=status)
        return answer.content, ""


#: Failures that end a test: an indexer that cannot be asked, or that is not an indexer (a page of HTML, a 404), cannot
#: be saved. What is left is a caps document that is XML but unusable: the defaults apply then.
_FATAL_FOR_CAPS = frozenset({
    "indexer_key_rejected", "indexer_limit_reached", "indexer_unreachable", "indexer_timeout", "indexer_blocked",
    "indexer_daily_limit", "indexer_share_used", "indexer_switched_off", "indexer_not_newznab", "indexer_http_error",
    "indexer_answer_too_large",
})


def _gate_error(exc: gate.GateError) -> IndexerError:
    code = {"refused": "indexer_blocked", "host_not_allowed": "indexer_blocked", "bad_address": "indexer_blocked",
            "redirect": "indexer_blocked", "unreachable": "indexer_unreachable", "timeout": "indexer_timeout",
            "too_large": "indexer_answer_too_large"}.get(exc.code, "indexer_unreachable")
    return IndexerError(code, 504 if code == "indexer_timeout" else 502)


# --- The key and the connection --------------------------------------------------------------------------------------


def seal(key: str) -> str:
    return encrypt_secret(key, SECRET_CONTEXT)


def connection_of(db: Session) -> ProwlarrConnection | None:
    return db.scalar(select(ProwlarrConnection).order_by(ProwlarrConnection.id).limit(1))


def key_of(db: Session, row: Indexer) -> str:
    """The key an indexer is asked with: its own, or the one of the Prowlarr connection for an indexer from Prowlarr."""
    if row.prowlarr_indexer_id is not None:
        connection = connection_of(db)
        return decrypt_secret(connection.api_key_enc, SECRET_CONTEXT) if connection is not None else ""
    return decrypt_secret(row.api_key_enc, SECRET_CONTEXT)


def has_key(db: Session, row: Indexer) -> bool:
    if row.prowlarr_indexer_id is not None:
        connection = connection_of(db)
        return bool(connection and connection.api_key_enc)
    return bool(row.api_key_enc)


def today() -> str:
    return budget.day()


def used_today(row: Indexer) -> int:
    return budget.used_today(row)


def midnight() -> datetime:
    """The next midnight of the instance's time zone: when the count of the day starts again."""
    return budget.midnight()


def resting(row: Indexer, *, grab: bool = False, background: bool = False) -> tuple[datetime, str] | None:
    """Until when the indexer is left alone, and why; None when it may be asked. To fetch a release (``grab``) the
    operator's limit of requests and the indexer's own count of requests do not count, its count of grabs does. The
    background (the planner, the reading of the newest entries) also rests after a fault of the indexer and keeps to its
    part of the limit of the day."""
    moment = clock.now()
    until: datetime | None = None
    cause = ""
    if row.paused_until is not None and row.paused_until > moment and not (grab and row.pause_cause == "api_limit") \
            and not (row.pause_cause == "server_error" and not background):
        until, cause = row.paused_until, row.pause_cause or "request_limit"
    if grab:
        held = budget.grab_hold(row, moment)
        if held is not None and (until is None or held > until):
            until, cause = held, "grab_limit"
    elif budget.spent(row, moment, background=background):
        reset = budget.midnight(moment)
        if until is None or reset > until:
            until, cause = reset, "daily_limit"
    return (until, cause) if until is not None else None


def state_of(row: Indexer) -> str:
    """``off``, ``key_rejected``, ``resting`` or ``ready``."""
    if not row.enabled:
        return "off"
    rest = resting(row)
    if rest is not None and rest[1] != "key_rejected":
        return "resting"
    if row.last_error == "indexer_key_rejected":
        return "key_rejected"
    return "resting" if rest is not None else "ready"


def check_available(row: Indexer, *, grab: bool = False, background: bool = False, test: bool = False) -> None:
    """``IndexerError`` while the indexer is switched off or rests. A test by hand (``test``) is served though the key
    was refused before: the operator may have changed it at the indexer's end."""
    if not row.enabled:
        raise IndexerError("indexer_off", 409)
    rest = resting(row, grab=grab, background=background)
    if rest is not None and rest[1] == "key_rejected":
        if test:
            return
        raise IndexerError("indexer_key_rejected")
    if rest is not None:
        left = max(1, math.ceil((rest[0] - clock.now()).total_seconds()))
        if rest[1] == "server_error":
            # The background leaves an indexer alone that failed: it says what failed, not that a limit is reached.
            raise IndexerError(row.last_error or "indexer_unreachable", 502, retry_after=left,
                               until=rest[0].isoformat())
        raise IndexerError("indexer_limit_reached" if rest[1] != "daily_limit" else "indexer_daily_limit", 429,
                           retry_after=left, until=rest[0].isoformat())


def target_of(db: Session, row: Indexer) -> Target:
    return Target(url=row.url, kind=row.kind, api_key=key_of(db, row), slow=row.prowlarr_indexer_id is not None)


def share_used_error() -> IndexerError:
    reset = budget.midnight()
    left = max(1, math.ceil((reset - clock.now()).total_seconds()))
    return IndexerError("indexer_share_used", 429, retry_after=left, until=reset.isoformat())


def _reserver(db: Session, indexer_id: int, person: int | None, background: bool) -> Callable[[], None]:
    """What a client calls before each request: the person's share of the day, then the place in the count of the day
    (one statement, so that requests that run at the same moment never pass the limit)."""

    def reserve() -> None:
        row = db.get(Indexer, indexer_id)
        if row is None:
            raise IndexerError("indexer_gone", 409)
        if budget.spent(row, background=background):
            raise IndexerError("indexer_daily_limit", 429)
        if not rations.take(row, person):
            raise IndexerError("indexer_share_used", 429)
        if not budget.reserve_request(db, indexer_id, background=background):
            rations.give_back(indexer_id, person)
            raise IndexerError("indexer_daily_limit", 429)

    return reserve


def _grab_reserver(db: Session, indexer_id: int) -> Callable[[], None]:
    """What a client calls before it fetches a release: a place in the count of releases of the day, which holds only
    while the indexer's own number of releases a day is not reached."""

    def reserve() -> None:
        if not budget.reserve_grab(db, indexer_id):
            reset = budget.midnight()
            left = max(1, math.ceil((reset - clock.now()).total_seconds()))
            raise IndexerError("indexer_limit_reached", 429, retry_after=left, until=reset.isoformat(), own=True)

    return reserve


def run[T](db: Session, row: Indexer, work: Callable[[Client], T], *, grab: bool = False,
           person: int | None = None, background: bool = False, test: bool = False) -> T:
    """``work(client)`` against a saved indexer, under its rules: not while it is off or rests, within the day's limit
    (the background within its part of it) and the person's share of it (``rations``), every request taking its place in
    the count before it is made, and what happens (limits reported, a limit reached, a refused key, a fault) is kept on
    the row. The fetch of a release (``grab``) is held back by the indexer's count of grabs, not by the limit of
    requests."""
    check_available(row, grab=grab, background=background, test=test)
    indexer_id = row.id
    client = Client(target_of(db, row), reserve=_reserver(db, indexer_id, None if grab else person, background),
                    reserve_grab=_grab_reserver(db, indexer_id))
    try:
        with client:
            result = work(client)
    except IndexerError as exc:
        _record(db, row, client, exc)
        if exc.code == "indexer_share_used":
            raise share_used_error() from exc
        raise
    _record(db, row, client, None)
    return result


def _fault_pause(indexer_id: int, failure: IndexerError) -> tuple[int, str] | None:
    """How long the indexer is left alone after this failure, and why; None for a failure that is no fault of its own
    (a limit of the day, a person's share, the count of releases of the day). Counts the faults in a row."""
    code = failure.code
    if code == "indexer_key_rejected":
        return KEY_REFUSED_PAUSE, "key_rejected"
    if failure.values.get("own"):
        return None
    transient = code in ("indexer_limit_reached", "indexer_unreachable", "indexer_timeout", "indexer_not_newznab",
                         "indexer_answer_too_large") or (
        code == "indexer_http_error" and failure.status >= 500)
    if not transient:
        return None
    with _lock:
        count = _streak.get(indexer_id, 0) + 1
        _streak[indexer_id] = count
        if len(_streak) > 1000:
            for stale in list(_streak)[:500]:
                del _streak[stale]
    if code == "indexer_limit_reached":
        base = failure.retry_after or DEFAULT_PAUSE_SECONDS
        return min(MAX_PAUSE_SECONDS, base * 2 ** min(count - 1, 6)), "request_limit"
    return min(MAX_FAULT_PAUSE, FAULT_PAUSE_SECONDS * 2 ** min(count - 1, 6)), "server_error"


def _record(db: Session, row: Indexer, client: Client, failure: IndexerError | None) -> None:
    """Keeps what the requests showed on the row. The count of the day is not touched: every request took its place in
    it before it was made."""
    moment = clock.now()
    limits = client.limits
    if limits is not None:
        row.api_current, row.api_max = limits.api_current, limits.api_max
        row.grab_current, row.grab_max = limits.grab_current, limits.grab_max
        row.limits_seen_at = moment
        if limits.api_max is not None and limits.api_current is not None and limits.api_current >= limits.api_max:
            when = newznab_time(limits.api_next_at)
            row.paused_until = when if when is not None and when > moment else moment + timedelta(
                seconds=DEFAULT_PAUSE_SECONDS)
            row.pause_cause = "api_limit"
        if limits.grab_max is not None and limits.grab_current is not None and limits.grab_current >= limits.grab_max:
            when = newznab_time(limits.grab_next_at)
            row.grab_paused_until = when if when is not None and when > moment else moment + budget.UNKNOWN_RESET
        elif limits.grab_max is not None:
            row.grab_paused_until = None
    if failure is None:
        row.last_error = ""
        if client.requests:
            with _lock:
                _streak.pop(row.id, None)
            if row.pause_cause in ("server_error", "key_rejected"):
                row.paused_until, row.pause_cause = None, ""
    elif failure.code != "indexer_share_used":
        # A person's share of the day is theirs, not a state of the indexer.
        row.last_error = failure.code
        pause = _fault_pause(row.id, failure)
        if pause is not None:
            row.paused_until, row.pause_cause = moment + timedelta(seconds=pause[0]), pause[1]
    row.updated_at = moment
    db.commit()


def newznab_time(value: str | None) -> datetime | None:
    """A time out of ``apilimits`` (an RFC 2822 date or ISO 8601) as UTC."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


# --- Testing ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TryResult:
    caps: dict[str, Any] | None
    categories: list[newznab.BookCategory]
    #: 0 or 1: whether a request in the suggested book categories delivered anything.
    items: int
    kind: str | None
    limits: newznab.ApiLimits | None


def _caps_key(url: str, key: str) -> tuple[str, str]:
    return url, hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def remember_caps(url: str, key: str, caps: dict[str, Any] | None) -> None:
    with _lock:
        if len(_caps_cache) >= CAPS_CACHE_MAX:
            oldest = min(_caps_cache, key=lambda name: _caps_cache[name][0])
            del _caps_cache[oldest]
        _caps_cache[_caps_key(url, key)] = (ticks(), caps)


def remembered_caps(url: str, key: str) -> tuple[bool, dict[str, Any] | None]:
    """``(found, caps)`` of a test of the last quarter of an hour: saving right after a test does not ask again."""
    with _lock:
        entry = _caps_cache.get(_caps_key(url, key))
    if entry is None or ticks() - entry[0] > CAPS_CACHE_SECONDS:
        return False, None
    return True, entry[1]


def try_target(client: Client, prefer: tuple[str, ...]) -> TryResult:
    """Caps, then one request in the suggested book categories (or Newznab's own two without caps)."""
    caps = client.caps()
    listed = newznab.book_categories(caps, prefer)
    chosen = [item.id for item in listed if item.suggested] or list(newznab.DEFAULT_CATEGORIES)
    feed = client.probe(chosen)
    remember_caps(client.target.url, client.target.api_key, caps)
    return TryResult(caps=caps, categories=listed, items=min(1, feed.items), kind=feed.kind, limits=client.limits)


def try_saved(db: Session, row: Indexer, prefer: tuple[str, ...]) -> TryResult:
    """The test of a saved indexer under its rules; the caps it found are kept."""
    holder: list[TryResult] = []

    def work(client: Client) -> TryResult:
        found = try_target(client, prefer)
        holder.append(found)
        return found

    result = run(db, row, work, test=True)
    row.caps = result.caps
    row.caps_checked_at = clock.now()
    # The caps read again: a language the operator saved apart from them stays, the others follow the caps.
    row.category_langs = newznab.only_deviations(row.caps, dict(row.category_langs or {}))
    db.commit()
    return result


def try_unsaved(target: Target, prefer: tuple[str, ...]) -> TryResult:
    """The test of an address and a key not saved yet: no state to keep, only the pacing."""
    with Client(target) as client:
        return try_target(client, prefer)


# --- Making, changing and removing ---------------------------------------------------------------------------------


def valid_name(db: Session, name: str, *, ignore_id: int | None = None) -> str:
    text = " ".join((name or "").split())
    if not text or len(text) > NAME_MAX:
        raise error("invalid_input", "The name is not valid.", 422, field="name")
    taken = db.scalar(select(Indexer.id).where(func.lower(Indexer.name) == text.lower(),
                                               Indexer.id != (ignore_id or 0)))
    if taken is not None:
        raise error("name_taken", "There is an indexer with this name.", 409, field="name")
    return text


def valid_address(url: str) -> str:
    try:
        return gate.normalize_address(url, default_path="/api")
    except gate.GateError as exc:
        raise error("address_invalid", "This address cannot be used.", 422, field="url") from exc


def valid_categories(categories: list[int] | None, langs: dict[str, str] | None) -> tuple[list[int], dict[str, str]]:
    chosen = list(dict.fromkeys(categories or []))
    if len(chosen) > 100 or any(not 1 <= number <= 99999 for number in chosen):
        raise error("invalid_input", "The categories are not valid.", 422, field="categories")
    given = {str(key): value for key, value in (langs or {}).items()}
    if any(value not in newznab.LANGUAGES for value in given.values()) or len(given) > 200:
        raise error("invalid_input", "The language of a category is not valid.", 422, field="category_langs")
    return chosen, {key: value for key, value in given.items() if key.isdigit()}


def valid_priority(value: int) -> int:
    if not PRIORITY_MIN <= value <= PRIORITY_MAX:
        raise error("invalid_input", "The priority is not valid.", 422, field="priority")
    return value


def valid_limit(value: int) -> int:
    if not 0 <= value <= DAILY_LIMIT_MAX:
        raise error("invalid_input", "The limit is not valid.", 422, field="daily_limit")
    return value


def valid_key(key: str) -> str:
    text = key.strip()
    # A header or a query cannot carry anything but plain characters; a key with a space or a control character is a
    # slip of the keyboard.
    if len(text) > KEY_MAX or any(ord(char) <= 32 or ord(char) == 127 for char in text):
        raise error("invalid_input", "The key is not valid.", 422, field="api_key")
    return text


def caps_for(db: Session, target: Target) -> dict[str, Any] | None:
    """The caps of an address: those of a test of the last quarter of an hour, else asked for now."""
    found, caps = remembered_caps(target.url, target.api_key)
    if found:
        return caps
    with Client(target) as client:
        caps = client.caps()
    remember_caps(target.url, target.api_key, caps)
    return caps


__all__ = [
    "INDEXER_KINDS", "Client", "IndexerError", "Target", "check_available", "forget", "key_of", "midnight", "pace",
    "resting", "run", "state_of", "try_saved", "try_unsaved", "used_today",
]
