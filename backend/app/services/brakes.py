"""Brakes on actions, per person and minute: a stolen session or a stuck script must not keep the server busy. Far above
what a person does by hand.

Kept in memory, per process: a restart forgets them, which is fine for a brake. Searches
and the like add their own kinds here.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from .. import clock
from ..config import get_settings
from ..errors import error

#: Per person and minute.
LIMITS = {
    #: Signing a device up for Web Push (its push service is looked up), a probe (it goes out to every device).
    "push": 20,
    "push_test": 10,
    #: "Look for others" in the sources (each asks up to a handful of requests of the sources, whose own limits are
    #: shared by everybody who looks after a library): one person cannot use them up.
    "review_search": 30,
    #: Looking in the library (typing with a pause between keystrokes stays far below it), and asking the catalogues
    #: of the sources for the typed words (each asks up to a handful of requests of the sources, whose own limits
    #: are shared by everybody).
    "search": 240,
    "catalog_search": 20,
    #: The covers of the hits a page shows (a quick search shows six of each search, the page of the search more).
    "catalog_cover": 150,
    #: Asking for the bytes of a file (``/files/{id}/content``): a player asks once or twice per part and a few times
    #: while somebody drags the bar, a reader once per book. Ten a second all minute long is far from a person and
    #: well short of what one script can ask.
    "stream": 600,
    #: Looking at Discover (the lists, the gaps, the page of a book: each reads what is kept, nothing asks a source),
    #: and
    #: changing the wish list.
    "discover": 300,
    "wish": 120,
    #: Mailing a book to a Kindle (``services/kindle.py``): every mail goes through the operator's mail server and into
    #: somebody's library at Amazon. A person sends a book now and then; a script or a stolen session sends thousands.
    #: The second count is per hour (``WINDOWS``), the third is the test mail, the fourth the test mails of an hour (the
    #: address can be changed at will, so a script would send to a new address every time).
    "kindle": 6,
    "kindle_hour": 30,
    "kindle_test": 3,
    "kindle_test_hour": 10,
    #: Looking at the catalogue for reading apps (``/opds``): a page, a cover, a search. An app that fills a screen of
    #: covers asks for dozens within a second or two.
    "opds": 600,
    #: The sync of KOReader (``/kosync``): a report when a page has been turned and the reader rests, a question when a
    #: book is opened. A device asks a few times a minute at the most.
    "kosync": 120,
    #: The sync of a Kobo (``/kobo``): a sync asks a few times, and then one request for every cover and every file of a
    #: batch of books, which a device sends within a second or two.
    "kobo": 600,
    #: Putting an edition on the list for the Kobo, or taking it off (``PUT`` and ``DELETE /editions/{id}/kobo``).
    "kobo_list": 120,
}
#: How long the minute of a kind is, in seconds, where it is not a minute.
WINDOWS = {"kindle_hour": 3600, "kindle_test_hour": 3600}
#: The code and the sentence of the answer.
CODES = {
    "push": ("push_too_often", "Too many at once. Wait a minute."),
    "push_test": ("push_too_often", "Too many at once. Wait a minute."),
    "review_search": ("review_search_too_often", "Too many searches at once. Wait a minute."),
    "search": ("search_too_often", "Too many searches at once. Wait a minute."),
    "catalog_search": ("search_too_often", "Too many searches at once. Wait a minute."),
    "catalog_cover": ("search_too_often", "Too many searches at once. Wait a minute."),
    "stream": ("stream_too_often", "Too many requests for files. Wait a moment."),
    "discover": ("discover_too_often", "Too many requests. Wait a minute."),
    "wish": ("discover_too_often", "Too many requests. Wait a minute."),
    "kindle": ("kindle_too_often", "Too many books sent at once. Wait a minute."),
    "kindle_hour": ("kindle_too_often", "Too many books sent within an hour. Try again later."),
    "kindle_test": ("kindle_too_often", "Too many test mails. Wait a minute."),
    "kindle_test_hour": ("kindle_too_often", "Too many test mails within an hour. Try again later."),
    "opds": ("opds_too_often", "Too many requests. Wait a minute."),
    "kosync": ("kosync_too_often", "Too many requests. Wait a minute."),
    "kobo": ("kobo_too_often", "Too many requests. Wait a minute."),
    "kobo_list": ("kobo_too_often", "Too many requests. Wait a minute."),
}

_lock = threading.Lock()
_seen: dict[tuple[str, int], deque[float]] = {}


def take(kind: str, account_id: int) -> None:
    """One more of ``kind`` for this person, or ``429`` with ``Retry-After`` when the minute (the hour, for a kind
    that is counted per hour) is full."""
    moment = clock.monotonic()
    window = WINDOWS.get(kind, 60)
    with _lock:
        seen = _seen.setdefault((kind, account_id), deque())
        while seen and moment - seen[0] > window:
            seen.popleft()
        if len(seen) >= LIMITS[kind]:
            code, text = CODES[kind]
            exc = error(code, text, 429)
            # Until the oldest of the window drops out.
            exc.headers = {"Retry-After": str(max(1, int(window - (moment - seen[0])) + 1))}
            raise exc
        seen.append(moment)


#: Files being sent at this moment, per person: a slow reader holds its place until the last byte has gone (or the
#: connection broke), so that one person cannot hold the server with a hundred downloads at once.
_open: dict[int, int] = {}


def open_stream(account_id: int) -> Callable[[], None]:
    """One more file being sent to this person, or ``429`` with ``Retry-After`` when they have as many open as the
    instance allows (``max_streams``; a player has one or two, several devices a few more). Returns the call that gives
    the place back: it must be called once the response is over, however it ended."""
    with _lock:
        count = _open.get(account_id, 0)
        if count >= max(1, get_settings().max_streams):
            exc = error("too_many_streams", "Too many files at once. Try again soon.", 429)
            exc.headers = {"Retry-After": "2"}
            raise exc
        _open[account_id] = count + 1
    done = threading.Event()

    def release() -> None:
        # Once only: a response that ends twice (a broken connection after the last byte) gives back one place.
        with _lock:
            if done.is_set():
                return
            done.set()
            left = _open.get(account_id, 1) - 1
            if left > 0:
                _open[account_id] = left
            else:
                _open.pop(account_id, None)

    return release


def streams_open(account_id: int) -> int:
    with _lock:
        return _open.get(account_id, 0)


def forget() -> None:
    """For the tests."""
    with _lock:
        _seen.clear()
        _open.clear()
