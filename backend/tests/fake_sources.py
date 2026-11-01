"""A stand-in for the sources on the internet: Open Library, Audible, Hardcover and the German National Library, as the
transport of ``services/sources/http.py``. It answers in the formats of the real services (the fixtures in
``tests/fixtures/sources`` are samples of those), with invented books, counts every request, and can be told to fail.

Like the real services it does not look closely at the question: it answers with *all* the records it holds for the
service, and it is the matching that has to tell the book from the others.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from .bookmaker import cover_image

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
ARCHIVE = "ia800000.us.archive.org"


def fixture(name: str) -> Any:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text.encode("utf-8")


def ol_doc(key: str, title: str, authors: list[str], *, year: int | None = 2019, languages: tuple[str, ...] = ("eng",),
           isbns: tuple[str, ...] = (), cover: int | None = None, editions: int = 5, subjects: tuple[str, ...] = (),
           ) -> dict[str, Any]:
    doc: dict[str, Any] = {"key": f"/works/{key}", "title": title, "author_name": authors, "language": list(languages),
                           "edition_count": editions}
    if year:
        doc["first_publish_year"] = year
    if isbns:
        doc["isbn"] = list(isbns)
    if cover:
        doc["cover_i"] = cover
    if subjects:
        doc["subject"] = list(subjects)
    return doc


def audible_product(asin: str, title: str, authors: list[str], minutes: int, *, kind: str = "unabridged",
                    language: str = "german", narrators: tuple[str, ...] = ("Ben Hollister",), series: str = "",
                    sequence: str = "", picture: bool = False, summary: str = "", publisher: str = "",
                    released: str = "2018-05-14") -> dict[str, Any]:
    product: dict[str, Any] = {
        "asin": asin, "title": title, "authors": [{"name": a} for a in authors],
        "narrators": [{"name": n} for n in narrators], "runtime_length_min": minutes, "format_type": kind,
        "language": language, "release_date": released}
    if series:
        product["series"] = [{"title": series, "sequence": sequence}]
    if picture:
        product["product_images"] = {"500": f"https://m.media-amazon.com/images/I/{asin}._SL500_.jpg"}
    if summary:
        product["merchandising_summary"] = summary
    if publisher:
        product["publisher_name"] = publisher
    return product


def hc_doc(ident: int, title: str, authors: list[str], *, users: int = 100, year: int | None = None, series: str = "",
           position: float | None = None, description: str = "", picture: bool = False) -> dict[str, Any]:
    doc: dict[str, Any] = {"id": str(ident), "title": title, "author_names": authors, "users_count": users}
    if year:
        doc["release_year"] = year
    if series:
        doc["featured_series"] = {"series": {"name": series}, "position": position}
    if description:
        doc["description"] = description
    if picture:
        doc["image"] = {"url": f"https://assets.hardcover.app/books/{ident}/cover.jpg"}
    return doc


def hc_edition(title: str, language: str, *, isbn: str | None = None, asin: str | None = None) -> dict[str, Any]:
    return {"title": title, "isbn_13": isbn, "asin": asin, "pages": None, "edition_format": None,
            "language": {"language": language}, "reading_format": None}


def _key(subject: str) -> str:
    return re.sub(r"\s+", "_", subject.strip().casefold())


def shelf_docs(docs: list[dict[str, Any]], params: dict[str, list[str]]) -> list[dict[str, Any]]:
    """What Open Library answers to the question of a shelf: groups of ``subject_key:"a" OR subject_key:"b"`` (a whole
    subject, by its key) or ``subject:"a" OR ...`` (a phrase anywhere in a subject, as Open Library reads it) that are
    all to be met, ``-subject_key:"x"``, ``first_publish_year:[a TO b]``, ``language:ger``: the docs that fit, in the
    order they are held in (the sort is not looked at), at most ``limit``."""
    query = params.get("q", [""])[0]
    groups = [(re.findall(r'subject_key:"([^"]+)"', group), [w.casefold() for w in re.findall(r'(?<!_)subject:"([^"]+)"', group)])
              for group in re.findall(r"\(([^()]*)\)", query)]
    excluded_keys = re.findall(r'-subject_key:"([^"]+)"', query)
    excluded = [w.casefold() for w in re.findall(r'-subject:"([^"]+)"', query)]
    years = re.search(r"first_publish_year:\[(\d+) TO (\d+)\]", query)
    marc = re.search(r"language:(\w+)", query)
    found: list[dict[str, Any]] = []
    for doc in docs:
        subjects = [s.casefold() for s in doc.get("subject", [])]
        keys = {_key(s) for s in doc.get("subject", [])}
        if not all(any(k in keys for k in by_key) or any(w in text for text in subjects for w in by_phrase)
                   for by_key, by_phrase in groups):
            continue
        if any(k in keys for k in excluded_keys) or any(w in text for text in subjects for w in excluded):
            continue
        year = doc.get("first_publish_year", 0)
        if years and not (isinstance(year, int) and int(years.group(1)) <= year <= int(years.group(2))):
            continue
        if marc and marc.group(1) not in doc.get("language", []):
            continue
        found.append(doc)
    return found[:int(params.get("limit", ["100"])[0])]


class FakeSources(httpx.BaseTransport):
    """What the services hold (set the lists), what was asked (``log``), and how they behave (``status``, ``bodies``)."""

    def __init__(self) -> None:
        self.ol: list[dict[str, Any]] = []
        #: When set, Open Library answers a search with what this gives for its parameters (``{"q": ["isbn:..."]}``).
        self.ol_for: Callable[[dict[str, list[str]]], list[dict[str, Any]]] | None = None
        self.ol_work: dict[str, Any] = {"description": "A description from Open Library."}
        self.audible: dict[str, list[dict[str, Any]]] = {}
        self.hc: list[dict[str, Any]] = []
        self.hc_editions: dict[str, list[dict[str, Any]]] = {}
        #: What kind of book a book of Hardcover is (``literary_type_id``, ``cached_tags``), by its number.
        self.hc_kinds: dict[str, list[dict[str, Any]]] = {}
        self.dnb = fixture("dnb_sru.xml")
        #: The pages of an answer of the German National Library by the ``startRecord`` they are asked at (the answer is
        #: ``dnb`` for a start that is not in it): for the tests of paging.
        self.dnb_pages: dict[int, bytes] = {}
        #: When set, the National Library answers a search with what this gives for the parameters of the question (the
        #: ``query`` is ``tit="..." and per="..."``): for the tests that need an answer for each of many books.
        self.dnb_for: Callable[[dict[str, list[str]]], bytes] | None = None
        #: Apple's chart (the whole answer), Audible's lists by the sort they are asked in (``BestSellers``,
        #: ``-ReleaseDate``; the products), and the answers of Hardcover to the questions of Discover.
        self.apple: dict[str, Any] | None = fixture("apple_chart.json")
        #: Apple's older feed, the chart of one genre: the same answer for every genre unless told otherwise
        #: (``apple_genres``, by the number of the genre), and Open Library's books for the shelves.
        self.apple_genre: dict[str, Any] | None = fixture("apple_genre.json")
        self.apple_genres: dict[str, dict[str, Any] | None] = {}
        self.ol_shelf: list[dict[str, Any]] = fixture("openlibrary_shelf.json")["docs"]
        self.audible_chart: dict[str, list[dict[str, Any]]] = {}
        #: What Audible answers to a question by the fields ``title`` and ``author`` (not by free text), by market.
        self.audible_fields: dict[str, list[dict[str, Any]]] = {}
        #: The pages after the first of a list of Audible, by the sort and the number of the page (the first page is
        #: ``audible_chart``). A page that is not set is empty.
        self.audible_pages: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.hc_trending: dict[str, Any] = fixture("hardcover_trending.json")
        self.hc_books: dict[str, Any] = fixture("hardcover_books.json")
        self.hc_series_of: dict[str, Any] = fixture("hardcover_series_of.json")
        self.hc_series: dict[str, Any] = fixture("hardcover_series.json")
        self.hc_author: dict[str, Any] = fixture("hardcover_author.json")
        #: The other names of an author at Hardcover, by the name asked for (``alternate_names``).
        self.hc_alternates: dict[str, Any] = {}
        self.cover = cover_image((40, 90, 150))
        #: A status to answer with for a host (``{"api.hardcover.app": 401}``), and an extra header for it.
        self.status: dict[str, int] = {}
        self.headers: dict[str, dict[str, str]] = {}
        #: Replaces the body of the answer of a host (a hostile one).
        self.bodies: dict[str, bytes] = {}
        self.log: list[httpx.Request] = []
        self.on_request: Callable[[httpx.Request], None] | None = None

    # -- Counting ---------------------------------------------------------------------------------------------------

    def hosts(self) -> list[str]:
        return [host_of(request) for request in self.log]

    def count(self, host_part: str = "", path_part: str = "") -> int:
        return sum(1 for r in self.log if host_part in host_of(r) and path_part in r.url.path)

    def urls(self, host_part: str) -> list[str]:
        return [str(r.url) for r in self.log if host_part in host_of(r)]

    # -- Answering --------------------------------------------------------------------------------------------------

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.log.append(request)
        if self.on_request is not None:
            self.on_request(request)
        host, path = host_of(request), request.url.path
        if host in self.status:
            return httpx.Response(self.status[host], headers=self.headers.get(host, {}), content=b"{}", request=request)
        if host in self.bodies:
            return httpx.Response(200, content=self.bodies[host], request=request)
        if host == "openlibrary.org" and path == "/search.json" and 'subject' in params_of(request).get("q", [""])[0]:
            found = shelf_docs(self.ol_shelf, params_of(request))
            return self._json(request, {"numFound": len(found), "docs": found})
        if host == "openlibrary.org" and path == "/search.json":
            docs = self.ol_for(params_of(request)) if self.ol_for is not None else self.ol
            return self._json(request, {"numFound": len(docs), "docs": docs})
        if host == "openlibrary.org" and path.startswith("/works/"):
            return self._json(request, self.ol_work)
        if host == "covers.openlibrary.org":
            return httpx.Response(302, headers={"Location": f"https://{ARCHIVE}/cover.jpg"}, request=request)
        if host == "itunes.apple.com":
            genre = re.search(r"^/([a-z]{2})/rss/toppaidebooks/genre=(\d+)/", path)
            # By country and genre ("de/9032"), else by genre, else the one chart for all.
            chart = self.apple_genres.get(f"{genre.group(1)}/{genre.group(2)}", self.apple_genres.get(
                genre.group(2), self.apple_genre)) if genre else self.apple_genre
            if chart is None:
                return httpx.Response(404, request=request)
            return self._json(request, chart)
        if host == "rss.marketingtools.apple.com":
            if self.apple is None:
                return httpx.Response(404, request=request)
            return self._json(request, self.apple)
        if host in (ARCHIVE, "m.media-amazon.com", "assets.hardcover.app") or host.endswith("-ssl.mzstatic.com"):
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=self.cover, request=request)
        if host.startswith("api.audible."):
            market = host.removeprefix("api.audible.")
            sort = params_of(request).get("products_sort_by", [""])[0]
            page = int(params_of(request).get("page", ["1"])[0])
            if page > 1 and sort in self.audible_chart:
                return self._json(request, {"products": self.audible_pages.get((sort, page), [])})
            if sort in self.audible_chart:
                return self._json(request, {"products": self.audible_chart[sort]})
            query = params_of(request)
            if "keywords" not in query and ("title" in query or "author" in query):
                # A question by the fields ``title`` and ``author``: answered from its own list, which the test sets.
                return self._json(request, {"products": self.audible_fields.get(market, [])})
            return self._json(request, {"products": self.audible.get(market, [])})
        if host == "api.hardcover.app":
            return self._hardcover(request)
        if host == "services.dnb.de":
            start = int(params_of(request).get("startRecord", ["1"])[0])
            body = self.dnb_for(params_of(request)) if self.dnb_for is not None else self.dnb_pages.get(start, self.dnb)
            return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=body, request=request)
        return httpx.Response(404, request=request)

    def _json(self, request: httpx.Request, data: Any) -> httpx.Response:
        return httpx.Response(200, json=data, request=request)

    def _hardcover(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        query = body.get("query", "")
        if "alternate_names" in query:
            name = body.get("variables", {}).get("name", "")
            known = self.hc_alternates.get(name)
            # A list of lists: Hardcover knows several authors of that name (namesakes), each with its own names.
            if isinstance(known, list) and known and all(isinstance(item, list) for item in known):
                rows = [{"name": name, "alternate_names": item} for item in known]
            else:
                rows = [{"name": name, "alternate_names": known}] if name in self.hc_alternates else []
            return self._json(request, {"data": {"authors": rows}})
        if "me {" in query or "me{" in query:
            return self._json(request, {"data": {"me": [{"id": 1}]}})
        if "books_trending" in query:
            return self._json(request, self.hc_trending)
        if "_in:$ids" in query.replace(" ", ""):
            return self._json(request, self.hc_books)
        if "series{ id name }" in query:
            return self._json(request, self.hc_series_of)
        if "series_id:{_eq" in query.replace(" ", ""):
            return self._json(request, self.hc_series)
        if "contributions:{author" in query.replace(" ", ""):
            return self._json(request, self.hc_author)
        data: dict[str, Any] = {}
        variables = body.get("variables", {})
        for name in variables:
            if name.startswith("q"):
                data[f"s{name[1:]}"] = {"results": {"hits": [{"document": d} for d in self.hc]}}
            elif name.startswith("id"):
                data[f"e{name[2:]}"] = self.hc_editions.get(str(variables[name]), [])
                data[f"k{name[2:]}"] = self.hc_kinds.get(str(variables[name]), [])
        return self._json(request, {"data": data})


def host_of(request: httpx.Request) -> str:
    """The name the request was made to: the connection goes to the checked address, the name is in the Host header."""
    return request.headers.get("host", str(request.url.host)).split(":")[0].lower()


def params_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


def dnb_sru(records: list[tuple[str, str, str]]) -> bytes:
    """An answer of the National Library with these ``(number, title, author as "Last, First")`` records, each a German
    novel of 2021 (invented)."""
    body = "".join(
        f'<record><recordData><record xmlns="http://www.loc.gov/MARC21/slim"><leader>00000nam a2200000 c 4500</leader>'
        f'<controlfield tag="001">{ident}</controlfield><controlfield tag="007">tu</controlfield>'
        f'<datafield tag="041" ind1=" " ind2=" "><subfield code="a">ger</subfield></datafield>'
        f'<datafield tag="082" ind1="0" ind2="4"><subfield code="a">B</subfield></datafield>'
        f'<datafield tag="100" ind1="1" ind2=" "><subfield code="a">{author}</subfield><subfield code="4">aut</subfield>'
        f'</datafield><datafield tag="245" ind1="1" ind2="0"><subfield code="a">{title} :</subfield></datafield>'
        f'<datafield tag="264" ind1=" " ind2="1"><subfield code="c">2021</subfield></datafield></record></recordData>'
        f'<recordPosition>{index}</recordPosition></record>'
        for index, (ident, title, author) in enumerate(records, start=1))
    return ('<?xml version="1.0" encoding="UTF-8"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            f'<version>1.1</version><numberOfRecords>{len(records)}</numberOfRecords><records>{body}</records>'
            '</searchRetrieveResponse>').encode()
