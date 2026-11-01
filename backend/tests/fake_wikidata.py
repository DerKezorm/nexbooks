"""A stand-in for Wikidata, Wikimedia Commons and the author endpoints of Open Library and Hardcover, as the transport of
``services/sources/http.py``. It answers in the formats of the real services (the fixtures in
``tests/fixtures/sources`` are samples of those), with invented people, counts every request, and can be told to fail.

Like the real services it does not know what the caller means: it answers a search with every item whose label holds the
name, and it is ``services/sources/wikidata.py`` that has to tell the author from a footballer of the same name."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from typing import Any

import httpx

from .bookmaker import cover_image
from .fake_sources import fixture, host_of, params_of

AUTHOR = "Q9000001"
NAMESAKE = "Q9000002"


class FakeAuthors(httpx.BaseTransport):
    def __init__(self) -> None:
        self.search: dict[str, Any] = fixture("wikidata_search.json")
        self.entities: dict[str, Any] = copy.deepcopy(fixture("wikidata_entities.json")["entities"])
        #: The works of an author by the item of the author: the ids ``list=search`` gives.
        self.works: dict[str, list[str]] = {AUTHOR: ["Q9100001", "Q9100002", "Q9100003"], NAMESAKE: []}
        self.commons: dict[str, Any] = fixture("commons_imageinfo.json")
        self.picture = cover_image((120, 90, 60), (480, 640), "JPEG")
        self.ol_authors: dict[str, Any] = fixture("openlibrary_authors.json")
        self.ol_author: dict[str, Any] = fixture("openlibrary_author.json")
        self.ol_works: dict[str, Any] = fixture("openlibrary_author_works.json")
        self.hc_authors: dict[str, Any] = fixture("hardcover_author_photo.json")
        self.status: dict[str, int] = {}
        self.log: list[httpx.Request] = []
        self.on_request: Callable[[httpx.Request], None] | None = None

    def count(self, host_part: str = "", path_part: str = "") -> int:
        return sum(1 for r in self.log if host_part in host_of(r) and path_part in r.url.path)

    # -- Answering ---------------------------------------------------------------------------------------------------

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.log.append(request)
        if self.on_request is not None:
            self.on_request(request)
        host, path, params = host_of(request), request.url.path, params_of(request)
        if host in self.status:
            return httpx.Response(self.status[host], content=b"{}", request=request)
        if host == "www.wikidata.org":
            action = params.get("action", [""])[0]
            if action == "wbsearchentities":
                name = params.get("search", [""])[0].casefold()
                # Like the real search: a hit by its label or by the alias it matched.
                hits = [hit for hit in self.search["search"]
                        if any(word.strip(".") in (hit["label"] + " " + hit.get("match", {}).get("text", "")).casefold()
                               for word in name.split())]
                return self._json(request, {**self.search, "search": hits})
            if action == "wbgetentities":
                wanted = params.get("ids", [""])[0].split("|")
                props = params.get("props", [""])[0].split("|")
                found = {}
                for ident in wanted:
                    raw = copy.deepcopy(self.entities.get(ident))
                    if raw is None:
                        found[ident] = {"id": ident, "missing": ""}
                        continue
                    for prop in ("labels", "aliases", "descriptions", "claims"):
                        if prop not in props:
                            raw.pop(prop, None)
                    found[ident] = raw
                return self._json(request, {"entities": found, "success": 1})
            if action == "query":
                query = params.get("srsearch", [""])[0]
                match = re.match(r'haswbstatement:P50=(Q\d+)(?: "(.*)")?$', query)
                if match:
                    ids = self.works.get(match.group(1), [])
                    if match.group(2):
                        phrase = match.group(2).casefold()
                        ids = [i for i in ids if any(phrase in text.casefold() for text in self._texts(i))]
                    return self._json(request, {"query": {"searchinfo": {"totalhits": len(ids)},
                                                          "search": [{"ns": 0, "title": i} for i in ids]}})
            return httpx.Response(404, request=request)
        if host == "commons.wikimedia.org":
            title = params.get("titles", [""])[0]
            data = copy.deepcopy(self.commons)
            page = next(iter(data["query"]["pages"].values()))
            if title.removeprefix("File:").replace("_", " ") != page["title"].removeprefix("File:"):
                return self._json(request, {"batchcomplete": "", "query": {"pages": {"-1": {
                    "ns": 6, "title": title, "missing": ""}}}})
            return self._json(request, data)
        if host == "upload.wikimedia.org":
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=self.picture, request=request)
        if host == "openlibrary.org" and path == "/search/authors.json":
            return self._json(request, self.ol_authors)
        if host == "openlibrary.org" and path.endswith("/works.json"):
            return self._json(request, self.ol_works)
        if host == "openlibrary.org" and re.match(r"^/authors/OL\d+A\.json$", path):
            return self._json(request, self.ol_author)
        if host == "covers.openlibrary.org":
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=self.picture, request=request)
        if host == "api.hardcover.app":
            return self._json(request, self.hc_authors if "authors(" in json.loads(request.content or b"{}").get(
                "query", "") else {"data": {}})
        if host == "assets.hardcover.app":
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=self.picture, request=request)
        return httpx.Response(404, request=request)

    def _texts(self, ident: str) -> list[str]:
        entity = self.entities.get(ident, {})
        texts = [label["value"] for label in entity.get("labels", {}).values()]
        for aliases in entity.get("aliases", {}).values():
            texts += [alias["value"] for alias in aliases]
        return texts

    def _json(self, request: httpx.Request, data: Any) -> httpx.Response:
        return httpx.Response(200, json=data, request=request)
