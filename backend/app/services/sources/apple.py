"""Apple Books' chart of the best-selling e-books, one country at a time: a free, official file
(``rss.marketingtools.apple.com``, Apple's "RSS feed generator"), no account, no key.

It is a list, not a catalogue: it names the books that sell best in a country, with the day each came out, a picture
and the genres Apple files it under. There is no way to ask it for a book. nexbooks reads it for Discover, slowly (a
list is fetched once in twelve hours at most), and the operator can switch it off like every other source. What is
sent is the country, nothing else.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..readers.text import clean, html_to_text, unescape_deep, year_of
from . import http
from .model import Candidate, day_of

SOURCE = "apple"
HOST = "rss.marketingtools.apple.com"
#: Countries are two letters; the table of markets (``services/discover/markets.py``) decides which ones are asked.
COUNTRY = re.compile(r"^[a-z]{2}$")
#: The chart nexbooks reads: the books people pay for. (Apple has a free one too; it is full of out-of-copyright
#: texts.)
CHART = "top-paid"
SIZE = 50
_ID = re.compile(r"^\d{4,20}$")
#: The size of the picture in Apple's address is a part of the file name, and any size is answered.
_SMALL = re.compile(r"/(\d{1,4})x(\d{1,4})(bb)?\.(jpg|png|webp)$")


def url(country: str) -> str:
    """The address of the chart of a country. ``ValueError`` for anything that is no two letters (never built from a
    typed text: the countries come from a table)."""
    if not COUNTRY.match(country):
        raise ValueError("country")
    return f"https://{HOST}/api/v2/{country}/books/{CHART}/{SIZE}/books.json"


def bigger(picture: str) -> str:
    """The picture of a book in a size a card can show sharp (Apple's chart gives 100 pixels); the address is the same
    but for the size in the name."""
    return _SMALL.sub(r"/320x480\3.\4", picture)


def parse(data: Any, language: str = "") -> list[Candidate]:
    """The books of the chart, in order. Whatever is not a book with a title and a number is left out."""
    feed = data.get("feed") if isinstance(data, dict) else None
    results = feed.get("results") if isinstance(feed, dict) else None
    found: list[Candidate] = []
    for item in results[:100] if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        title = clean(item.get("name"), 300) if isinstance(item.get("name"), str) else ""
        if not _ID.match(ident) or not title:
            continue
        artist = clean(item.get("artistName"), 200) if isinstance(item.get("artistName"), str) else ""
        genres = item.get("genres")
        names = [g.get("name") for g in genres[:10] if isinstance(g, dict)] if isinstance(genres, list) else []
        subjects = [clean(name, 100) for name in names if isinstance(name, str)]
        picture = item.get("artworkUrl100")
        picture = picture if isinstance(picture, str) and http.host_allowed(SOURCE, picture) else ""
        released = day_of(item.get("releaseDate"))
        found.append(Candidate(
            source=SOURCE, key=ident, work_key=ident, title=title, authors=[artist] if artist else [],
            languages=[language] if language else [], year=year_of(released), released=released,
            subjects=[s for s in subjects if s], cover_url=picture))
    return found


def chart(db: Session, country: str, language: str = "") -> list[Candidate]:
    """The chart of a country (one request). ``http.SourceOff`` when Apple may not be asked, ``http.SourceError`` when
    it does not answer."""
    answer = http.request(db, SOURCE, url(country), limit=http.JSON_MAX)
    if answer.status_code == 404:
        return []
    return parse(http.json_of(SOURCE, answer), language)


# ---- The chart of one genre -------------------------------------------------------------------------

#: Apple's older feed is the one that takes a genre: the charts of a country, 100 books at most, with the day each came
#: out, the description and the genre it is filed under. The newer one (above) has no genre.
LEGACY = "https://itunes.apple.com"
GENRE_SIZE = 100
_GENRE = re.compile(r"^\d{4,6}$")


def genre_url(country: str, genre: str | int, size: int = GENRE_SIZE) -> str:
    """The address of the chart of the best-selling e-books of one genre (an Apple genre number) in a country.
    ``ValueError`` for anything that is no country or no number (the table of shelves decides which are asked)."""
    if not COUNTRY.match(country) or not _GENRE.match(str(genre)):
        raise ValueError("country or genre")
    return f"{LEGACY}/{country}/rss/toppaidebooks/genre={genre}/limit={min(max(size, 1), GENRE_SIZE)}/json"


def _label(value: Any) -> str:
    return value.get("label", "") if isinstance(value, dict) and isinstance(value.get("label"), str) else ""


def _height(attrs: Any) -> int:
    height = attrs.get("height") if isinstance(attrs, dict) else None
    return int(height) if isinstance(height, str) and height.isdigit() else 0


def parse_genre(data: Any, language: str = "") -> list[Candidate]:
    """The books of the chart of one genre, in order, each with the genre Apple files it under (``category``: a chart
    of one genre holds books of others). The picture is the largest the feed gives; the texts come with their HTML
    entities resolved ("Bonny &amp; Clyde" is "Bonny & Clyde"); whatever is not a book with a title and a number is
    left out."""
    feed = data.get("feed") if isinstance(data, dict) else None
    entries = feed.get("entry") if isinstance(feed, dict) else None
    if isinstance(entries, dict):
        entries = [entries]
    found: list[Candidate] = []
    for item in entries[:GENRE_SIZE] if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        ident_node = item.get("id")
        attrs = ident_node.get("attributes") if isinstance(ident_node, dict) else None
        ident = str(attrs.get("im:id") or "") if isinstance(attrs, dict) else ""
        title = clean(unescape_deep(_label(item.get("im:name"))), 300)
        if not _ID.match(ident) or not title:
            continue
        artist = clean(unescape_deep(_label(item.get("im:artist"))), 200)
        category = item.get("category")
        category_attrs = category.get("attributes") if isinstance(category, dict) else None
        subjects: list[str] = []
        genre = ""
        if isinstance(category_attrs, dict):
            subjects = [clean(unescape_deep(str(category_attrs.get(name) or "")), 100) for name in ("label", "term")]
            number = str(category_attrs.get("im:id") or "")
            genre = number if _GENRE.match(number) else ""
        images = item.get("im:image")
        pictures = [(node.get("attributes"), node.get("label")) for node in (images if isinstance(images, list) else [])
                    if isinstance(node, dict) and isinstance(node.get("label"), str)]
        pictures.sort(key=lambda pair: _height(pair[0]))
        picture = next((label for _attrs, label in reversed(pictures) if http.host_allowed(SOURCE, label)), "")
        released = day_of(_label(item.get("im:releaseDate")))
        if released[:4] < "1900":
            # Apple writes a year in the 1600s for a book whose day it does not know.
            released = ""
        found.append(Candidate(
            source=SOURCE, key=ident, work_key=ident, title=title,
            subtitle=clean(unescape_deep(_label(item.get("subtitle"))), 200),
            authors=[artist] if artist else [], languages=[language] if language else [], year=year_of(released),
            released=released, subjects=list(dict.fromkeys(s for s in subjects if s)), category=genre,
            description=html_to_text(unescape_deep(_label(item.get("summary"))), 4000), cover_url=picture))
    return found


def genre_chart(db: Session, country: str, genre: str | int, language: str = "") -> list[Candidate]:
    """The chart of one genre in a country (one request). ``http.SourceOff`` when Apple may not be asked,
    ``http.SourceError`` when it does not answer; a genre or country it does not know gives an empty list."""
    answer = http.request(db, SOURCE, genre_url(country, genre), limit=http.JSON_MAX)
    if answer.status_code == 404:
        return []
    return parse_genre(http.json_of(SOURCE, answer), language)
