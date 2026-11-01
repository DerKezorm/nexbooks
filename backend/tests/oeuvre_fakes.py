"""A stand-in for the sources the catalogue of an author's works asks: it answers from the recorded and
invented answers in ``tests/fixtures/oeuvre`` (``build.py`` there writes them), by the name asked for, and counts every
request. An author it has no answer for gets an empty one, as from the real services."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Job
from app.security import encrypt_secret
from app.services import jobs, settings_service
from app.services.sources import config, http

from .bookmaker import cover_image

FIXTURES = Path(__file__).parent / "fixtures" / "oeuvre"
#: The day the tests live on: "Der Hüter der verlorenen Zimmer" comes out in English on 15 November 2026.
TODAY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def load(name: str) -> bytes:
    path = FIXTURES / name
    return path.read_bytes() if path.exists() else b""


def _slug(text: str) -> str:
    """The last name in the question (``Halloran``, ``"Halloran, Edmund"``, ``Halloran Edmund``)."""
    words = text.replace('"', " ").replace(",", " ").replace("(", " ").replace(")", " ").split()
    for word in words:
        lowered = word.lower()
        if lowered in ("halloran", "vane", "lindqvist", "aalders", "holte", "marchetti", "wren", "quellbrandt"):
            return lowered
    return ""


def host_of(request: httpx.Request) -> str:
    return request.headers.get("host", str(request.url.host)).split(":")[0].lower()


def params_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


class CatalogFake(httpx.BaseTransport):
    def __init__(self) -> None:
        self.log: list[httpx.Request] = []
        #: A status to answer with for a host.
        self.status: dict[str, int] = {}
        self.cover = cover_image((90, 40, 50))

    def count(self, host_part: str = "", path_part: str = "") -> int:
        return sum(1 for r in self.log if host_part in host_of(r) and path_part in r.url.path)

    def _json(self, request: httpx.Request, name: str, empty: Any) -> httpx.Response:
        body = load(name)
        return httpx.Response(200, content=body or json.dumps(empty).encode(), request=request,
                              headers={"Content-Type": "application/json"})

    def _xml(self, request: httpx.Request, name: str) -> httpx.Response:
        body = load(name) or (b'<?xml version="1.0"?><searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
                              b"<numberOfRecords>0</numberOfRecords></searchRetrieveResponse>")
        return httpx.Response(200, content=body, request=request, headers={"Content-Type": "application/xml"})

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.log.append(request)
        host, path, params = host_of(request), request.url.path, params_of(request)
        if host in self.status:
            return httpx.Response(self.status[host], content=b"{}", request=request)
        if host == "openlibrary.org" and path == "/search/authors.json":
            slug = _slug(params.get("q", [""])[0])
            return self._json(request, f"ol_authors_{slug}.json" if load(f"ol_authors_{slug}.json") else
                              "ol_authors.json", {"docs": []})
        if host == "openlibrary.org" and path == "/search.json":
            marc = params.get("lang", [""])[0]
            language = {"eng": "en", "ger": "de"}.get(marc, marc)
            return self._json(request, f"ol_{_slug(params.get('author', [''])[0])}_{language}.json", {"docs": []})
        if host == "api.hardcover.app":
            body = json.loads(request.content or b"{}")
            query, variables = body.get("query", ""), body.get("variables", {})
            if 'query_type:"Author"' in query:
                slug = _slug(str(variables.get("q", "")))
                return self._json(request, f"hc_author_search_{slug}.json" if load(f"hc_author_search_{slug}.json")
                                  else "hc_author_search.json", {"data": {"search": {"results": {"hits": []}}}})
            if "contributions(" in query:
                if variables.get("o"):
                    return httpx.Response(200, json={"data": {"contributions": []}}, request=request)
                return self._json(request, f"hc_contributions_{variables.get('id')}.json",
                                  {"data": {"contributions": []}})
            return httpx.Response(200, json={"data": {}}, request=request)
        if host.startswith("api.audible."):
            market = {"de": "de", "com": "us"}.get(host.removeprefix("api.audible."), "")
            if int(params.get("page", ["0"])[0]) > 0:
                return httpx.Response(200, json={"products": []}, request=request)
            return self._json(request, f"audible_{market}_{_slug(params.get('author', [''])[0])}.json",
                              {"products": []})
        if host == "services.dnb.de":
            return self._xml(request, f"dnb_{_slug(params.get('query', [''])[0])}.xml")
        if host == "catalogue.bnf.fr":
            return self._xml(request, f"bnf_{_slug(params.get('query', [''])[0])}.xml")
        if host == "jsru.kb.nl":
            return self._xml(request, f"kb_{_slug(params.get('query', [''])[0])}.xml")
        if host == "libris.kb.se":
            return self._json(request, f"libris_{_slug(params.get('query', [''])[0])}.json",
                              {"xsearch": {"records": 0, "list": []}})
        if host == "api.nb.no":
            if params.get("filter", ["", ""])[-1] != "languages:nob":
                return httpx.Response(200, json={"_embedded": {"items": []}}, request=request)
            return self._json(request, f"nb_{_slug(params.get('q', [''])[0])}.json", {"_embedded": {"items": []}})
        if host in ("m.media-amazon.com", "assets.hardcover.app", "covers.openlibrary.org"):
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=self.cover, request=request)
        return httpx.Response(404, request=request)


class Sources:
    """The sources switched on with the stand-in, and a clock that jumps (on ``TODAY``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, hardcover: bool = True, audible: bool = True) -> None:
        self.fake = CatalogFake()
        self.offset = 0.0
        monkeypatch.setattr(clock, "now", lambda: TODAY + timedelta(seconds=self.offset))
        monkeypatch.setattr(http, "sleep", self.advance)
        monkeypatch.setattr(http, "transport", self.fake)
        values: dict[str, Any] = {"sources_enabled": True, "source_audible": audible}
        if hardcover:
            import secrets

            values["hardcover_token_enc"] = encrypt_secret(secrets.token_urlsafe(24), config.TOKEN_CONTEXT)
        with SessionLocal() as db:
            settings_service.save(db, values)

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def days(self, days: float) -> None:
        self.offset += days * 86400

    def settings(self, **values: Any) -> None:
        with SessionLocal() as db:
            settings_service.save(db, values)

    def build(self) -> int:
        """Runs the catalogue jobs that wait."""
        return jobs.run_pending(100, lane="catalog")

    def waiting(self) -> int:
        with SessionLocal() as db:
            return len(list(db.scalars(select(Job.id).where(Job.kind == "catalog", Job.state == "queued"))))
