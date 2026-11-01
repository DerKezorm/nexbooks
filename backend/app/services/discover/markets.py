"""The markets Discover knows, the lists it shows and which source speaks for each list.

A market is where the charts are taken from: Apple's chart is one country's, Audible's catalogue is one market's. The
table is fixed here; a person's choice (``market`` in their profile) is only ever looked up in it, and anything that
is not in it is not asked for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    key: str
    #: Apple's country code, and the key of Audible's market (``sources.config.AUDIBLE_MARKETS``).
    apple: str
    audible: str
    #: The language most of the chart is written in.
    language: str


MARKETS: dict[str, Market] = {m.key: m for m in (
    Market("de", "de", "de", "de"),
    Market("us", "us", "us", "en"),
    Market("uk", "gb", "uk", "en"),
    Market("fr", "fr", "fr", "fr"),
    Market("ca", "ca", "ca", "en"),
    Market("au", "au", "au", "en"),
    Market("it", "it", "it", "it"),
    Market("es", "es", "es", "es"),
)}

#: The lists of the page, in the order of its tabs. ``foryou`` is computed for the person, the rest are fetched.
LISTS = ("best", "new", "soon", "trending")
#: What is fetched: a unit of work is one request (or two) to one source for one market; a unit may fill more than one
#: list. Audible's best-selling products (``best``) fill the best sellers and, of those, the ones that came out in the
#: last weeks (``new``); its newest first (``release``) fills what comes out (``soon``).
SOURCES_OF_LIST = {"best": ("apple", "audible"), "new": ("audible",), "soon": ("audible",), "trending": ("hardcover",)}
UNIT_OF_LIST = {"best": "best", "new": "best", "soon": "release", "trending": "trending"}
LISTS_OF_UNIT = {("apple", "best"): ("best",), ("audible", "best"): ("best", "new"), ("audible", "release"): ("soon",),
                 ("hardcover", "trending"): ("trending",)}
#: Whether a list is ranked (the numbers on the covers).
RANKED = frozenset({"best", "trending"})
#: What the sources of a list are a list of.
KIND_OF_SOURCE = {"apple": "ebook", "audible": "audio", "hardcover": "ebook"}
#: A list that no person looked at for this long is not fetched again.
WANTED_DAYS = 14
#: A list is fetched again after this long, and after an hour when the last try failed.
REFRESH_HOURS = 12
RETRY_HOURS = 1
#: "New" is what came out in this many weeks.
NEW_WEEKS = 8


def default_market(language: str) -> str:
    """The market that goes with the language of the page, for a person who chose none."""
    return {"de": "de", "fr": "fr", "it": "it", "es": "es"}.get((language or "")[:2].lower(), "us")


def market_of(chosen: str, language: str) -> str:
    """The market to show: the person's own choice when it is one there is, else the one for the language of the
    page."""
    return chosen if chosen in MARKETS else default_market(language)


#: Every unit there is: ``(source, unit)``.
UNITS = (("apple", "best"), ("audible", "best"), ("audible", "release"), ("hardcover", "trending"))


def unit_ref(source: str, market: str, unit: str) -> int:
    """A number for a job to wait on, the same for the same unit (the queue keeps one waiting job per number)."""
    keys = ["", *MARKETS]
    return (UNITS.index((source, unit)) + 1) * 100 + keys.index(market)
