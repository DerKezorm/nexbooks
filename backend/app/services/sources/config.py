"""Which sources there are, where each one lives, how fast it may be asked, and whether it may be asked now.

**Every address is built here from a fixed host** (``HOSTS``). What a person typed or a book's name says only ever
becomes a query value, never a host, a path or a scheme; and ``http.request`` checks the host of every request against
this table again before it connects. A redirect may go to another host only if the table of its source names it
(``REDIRECT_HOSTS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from ...models import SOURCES, Setting
from ...security import decrypt_secret
from .. import settings_service

#: Audible's markets: the key nexbooks uses (and the interface shows) and the end of the host, ``api.audible.<end>``.
AUDIBLE_MARKETS = {"de": "de", "us": "com", "uk": "co.uk", "fr": "fr", "ca": "ca", "au": "com.au", "it": "it",
                   "es": "es", "jp": "co.jp", "in": "in", "br": "com.br"}
#: The market whose catalogue is the one for a language of the book.
MARKET_OF_LANGUAGE = {"de": "de", "en": "us", "fr": "fr", "it": "it", "es": "es", "ja": "jp", "pt": "br"}
#: The most markets the operator may choose.
MAX_MARKETS = 6

HOSTS: dict[str, tuple[str, ...]] = {
    "hardcover": ("api.hardcover.app", "assets.hardcover.app"),
    "openlibrary": ("openlibrary.org", "covers.openlibrary.org"),
    # The pictures of Audible's products lie on Amazon's image host.
    "audible": tuple(f"api.audible.{end}" for end in AUDIBLE_MARKETS.values()) + ("m.media-amazon.com",),
    "dnb": ("services.dnb.de",),
    # The chart of Apple Books is a file at one host; its pictures lie on Apple's image hosts. The charts of one genre
    # are only given by Apple's older feed at ``itunes.apple.com``.
    "apple": ("rss.marketingtools.apple.com", "itunes.apple.com", *(f"is{n}-ssl.mzstatic.com" for n in range(1, 6))),
    # The photos and short biographies of authors: Wikidata
    # knows who a person is, Wikimedia Commons holds the
    # picture with its author and licence, and the pictures themselves lie on the upload host.
    "wikidata": ("www.wikidata.org", "commons.wikimedia.org", "upload.wikimedia.org"),
    # The national libraries of France, Sweden, the Netherlands and Norway: the catalogue of an author's
    # works in their language. Open data, no key.
    "bnf": ("catalogue.bnf.fr",),
    "libris": ("libris.kb.se",),
    "kb": ("jsru.kb.nl",),
    "nb": ("api.nb.no",),
}
#: The hosts that may be sent the token of a source: its API, and no host of its pictures. Every other host the
#: source's requests lead to (also after a redirect) gets none.
TOKEN_HOSTS: dict[str, tuple[str, ...]] = {
    "hardcover": ("api.hardcover.app",),
}
#: Where a redirect of a source may lead besides its own hosts: (exact hosts, domains below which any host is fine).
# Open
#: Library's cover address answers with a redirect to the Internet Archive, where the picture lies.
REDIRECT_HOSTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # The real chain goes to archive.org itself first, then to one of its storage hosts.
    "openlibrary": (("archive.org",), (".archive.org",)),
}


@dataclass(frozen=True)
class Limits:
    #: Requests per second the bucket refills with, and the most it holds (the burst).
    rate: float
    burst: int
    per_day: int


#: Hardcover says 60 a minute, a burst of 10 and 5,000 a day in the free plan; the others have no number, so they are
#: asked politely: about one a second.
LIMITS = {
    "hardcover": Limits(rate=1.0, burst=10, per_day=5000),
    "openlibrary": Limits(rate=1.0, burst=3, per_day=4000),
    "audible": Limits(rate=1.0, burst=3, per_day=3000),
    "dnb": Limits(rate=1.0, burst=2, per_day=3000),
    "apple": Limits(rate=1.0, burst=3, per_day=2000),
    "wikidata": Limits(rate=1.0, burst=3, per_day=3000),
    "bnf": Limits(rate=1.0, burst=2, per_day=2000),
    "libris": Limits(rate=1.0, burst=2, per_day=2000),
    "kb": Limits(rate=1.0, burst=2, per_day=2000),
    "nb": Limits(rate=1.0, burst=2, per_day=2000),
}

#: The sources that know a book by its title and author (the matching and the catalogue search ask these). Apple's chart
#: is a list, not a catalogue: it takes part in Discover only.
#: Wikidata knows people, not books for sale: it takes part in
#: the photos and biographies of authors only.
CATALOGUES = ("hardcover", "openlibrary", "audible", "dnb")
#: The national library that holds the books of a language best (measured): the
#: catalogue of an author's works asks it for the titles in that language. Besides the German one they take
#: part in nothing else.
NATIONAL_LIBRARIES = {"de": "dnb", "fr": "bnf", "sv": "libris", "nl": "kb", "no": "nb"}

#: The context the Hardcover token is sealed in.
TOKEN_CONTEXT = "operator:hardcover"


def market_host(market: str) -> str:
    """The host of Audible's catalogue for a market key; ``KeyError`` for one that is none (never from a typed text)."""
    return f"api.audible.{AUDIBLE_MARKETS[market]}"


def chosen_markets(db: Session) -> list[str]:
    """The markets the operator chose, only real ones, in their order (at most ``MAX_MARKETS``)."""
    value = settings_service.get(db, "source_markets")
    found = [m for m in value if isinstance(m, str) and m in AUDIBLE_MARKETS] if isinstance(value, list) else []
    unique = list(dict.fromkeys(found))[:MAX_MARKETS]
    return unique or ["de", "us"]


def markets_for(db: Session, language: str) -> list[str]:
    """The markets to ask for a book in ``language``: the market of that language when the operator has it, else every
    market they chose."""
    chosen = chosen_markets(db)
    own = MARKET_OF_LANGUAGE.get(language or "")
    if own and own in chosen:
        return [own]
    return chosen


def audible_decided(db: Session) -> bool:
    """Whether the operator has chosen about Audible, in either way: its switch has been saved. Audible is off from the
    start, and nexbooks asks once (the dialog of the settings); a switch saved by an earlier version, on or off, counts
    as the choice it was."""
    return db.get(Setting, "source_audible") is not None


def hardcover_token(db: Session) -> str:
    return decrypt_secret(str(settings_service.get(db, "hardcover_token_enc") or ""), TOKEN_CONTEXT)


@dataclass(frozen=True)
class Availability:
    """Whether a source may be asked now, and if not why (a code the interface words)."""

    source: str
    ready: bool
    #: ``ready``; ``main_off`` (the operator's main switch), ``off`` (this source), ``not_connected`` (Hardcover
    # without a
    #: token), ``token_invalid`` (Hardcover refused it), ``resting`` (after errors, for a while).
    state: str


def availability(db: Session, source: str) -> Availability:
    """Looks at the switches, the token and the rest after errors. No request is made and none counted."""
    from . import throttle

    if source not in SOURCES:
        return Availability(source, False, "off")
    if not settings_service.get(db, "sources_enabled"):
        return Availability(source, False, "main_off")
    if not settings_service.get(db, f"source_{source}"):
        return Availability(source, False, "off")
    state = throttle.state_of(source)
    if source == "hardcover":
        if state.state == "token_invalid":
            return Availability(source, False, "token_invalid")
        if not hardcover_token(db):
            return Availability(source, False, "not_connected")
    if state.blocked_until is not None and state.blocked_until > throttle.now():
        return Availability(source, False, "resting")
    return Availability(source, True, "ready")


def ready_sources(db: Session, among: tuple[str, ...] | None = None) -> list[str]:
    """The sources that may be asked now (of ``among``, when given)."""
    return [source for source in (among or SOURCES) if availability(db, source).ready]


def seconds_until_midnight() -> timedelta:
    from . import throttle

    now = throttle.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow - now
