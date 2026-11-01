"""A world for the search of releases: a library with one invented book, a person who may ask for books, an indexer
whose feed the test writes, and the programs that take the releases. Only invented names, example.com, keys made at run
time."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from email.utils import format_datetime
from typing import Any
from xml.sax.saxutils import escape

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Account, DownloadClient, Indexer, Work
from app.security import encrypt_secret
from app.services.procure import formats, indexers, newznab, programs

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, join, make_library, run_jobs, scan
from .procure_fakes import FakeIndexer, FakeNetwork, fixture, xml_response

INDEXER_HOST = "indexer.example.com"
TORRENT_HOST = "tracker.example.com"

#: The invented book of the world: volume 2 of a series, German.
TITLE = "Die Laternenstraße"
AUTHOR = "Rune Falkner"
SERIES = "Nebelchor"


@dataclass
class Item:
    title: str
    size: int = 2_000_000
    categories: tuple[int, ...] = (7120,)
    days: int = 3
    seeders: int | None = None
    peers: int | None = None
    grabs: int | None = None
    guid: str = ""
    link: str = ""
    info_hash: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def feed(items: list[Item], host: str = INDEXER_HOST, kind: str = "newznab") -> bytes:
    """A feed with these items, written as the specs show it."""
    namespace = ("xmlns:torznab=\"http://torznab.com/schemas/2015/feed\"" if kind == "torznab"
                 else "xmlns:newznab=\"http://www.newznab.com/dtd/2010/feeds/attributes/\"")
    prefix = "torznab" if kind == "torznab" else "newznab"
    kind_type = "application/x-bittorrent" if kind == "torznab" else "application/x-nzb"
    # The same clock the app reads: a fixed date here made the age of a release grow by a day every day.
    now = clock.now()
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" {namespace}><channel><title>T</title>']
    for number, item in enumerate(items):
        guid = item.guid or hashlib.sha256(item.title.encode()).hexdigest()[:16]
        link = item.link or f"https://{host}/get/{guid}?apikey=KEYPLACE"
        attrs = [f'<{prefix}:attr name="category" value="{category}"/>' for category in item.categories]
        attrs.append(f'<{prefix}:attr name="size" value="{item.size}"/>')
        for name, value in (("seeders", item.seeders), ("peers", item.peers), ("grabs", item.grabs)):
            if value is not None:
                attrs.append(f'<{prefix}:attr name="{name}" value="{value}"/>')
        if item.info_hash:
            attrs.append(f'<{prefix}:attr name="infohash" value="{item.info_hash}"/>')
        for name, value in item.extra.items():
            attrs.append(f'<{prefix}:attr name="{name}" value="{escape(value)}"/>')
        published = format_datetime(now - timedelta(days=item.days, minutes=number))
        parts.append(
            f"<item><title>{escape(item.title)}</title><guid>{guid}</guid><link>{escape(link)}</link>"
            f"<pubDate>{published}</pubDate>"
            f'<enclosure url="{escape(link)}" length="{item.size}" type="{kind_type}"/>{"".join(attrs)}</item>')
    parts.append("</channel></rss>")
    return "".join(parts).encode()


def nzb() -> bytes:
    return (b'<?xml version="1.0" encoding="UTF-8"?>\n<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
            b'<file poster="a@example.com" date="1" subject="x"><groups><group>alt.example</group></groups>'
            b'<segments><segment bytes="10" number="1">a@example.com</segment></segments></file></nzb>')


class FeedIndexer(FakeIndexer):
    """A stand-in indexer whose answers to a search are the items the test gives, per query text, and whose links give
    an NZB (or what the test says)."""

    def __init__(self, host: str, key: str, kind: str = "newznab") -> None:
        super().__init__(host, key, kind=kind)
        self.items: list[Item] = []
        self.by_query: dict[str, list[Item]] = {}
        self.book_items: list[Item] | None = None
        self.files: dict[str, httpx.Response] = {}
        self.fetched: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/get/"):
            self.requests.append(request)
            guid = request.url.path.rsplit("/", 1)[-1]
            self.fetched.append(guid)
            if guid in self.files:
                return self.files[guid]
            return httpx.Response(200, content=nzb(), headers={"content-type": "application/x-nzb"})
        function = request.url.params.get("t", "")
        if function == "book" and self.book_items is not None and request.url.params.get("apikey") == self.api_key:
            self.requests.append(request)
            return xml_response(feed(self.book_items, self.host, self.kind))
        if function == "search" and request.url.params.get("apikey") == self.api_key:
            self.requests.append(request)
            query = request.url.params.get("q", "")
            items = self.by_query.get(query, self.items)
            offset = int(request.url.params.get("offset", "0") or 0)
            return xml_response(feed(items[offset:offset + 100], self.host, self.kind))
        return super().handle(request)


def add_indexer(fake: FakeIndexer, *, name: str = "Shelf", categories: list[int] | None = None,
                langs: dict[str, str] | None = None, priority: int = 25, caps: bytes | None = None) -> int:
    root, _ns = newznab.parse_xml(caps or fixture(f"{fake.kind}_caps.xml"))
    assert root is not None
    with SessionLocal() as db:
        moment = clock.now()
        row = Indexer(name=name, kind=fake.kind, url=fake.url, api_key_enc=indexers.seal(fake.api_key),
                      enabled=True, priority=priority, categories=categories or [7120, 7020, 3130, 3030],
                      category_langs=langs if langs is not None else {"7120": "de", "3130": "de"},
                      caps=newznab.parse_caps(root), created_at=moment, updated_at=moment)
        db.add(row)
        db.commit()
        return row.id


def add_program(kind: str, host: str, *, username: str = "", secret: str = "", priority: int = 1) -> int:
    with SessionLocal() as db:
        moment = clock.now()
        row = DownloadClient(name=f"{kind}-{priority}", kind=kind, url=f"http://{host}:8080", username=username,
                             secret_enc=encrypt_secret(secret, programs.SECRET_CONTEXT) if secret else "",
                             category="nexbooks", priority=priority, enabled=True, created_at=moment,
                             updated_at=moment)
        db.add(row)
        db.commit()
        return row.id


@dataclass
class World:
    net: FakeNetwork
    indexer: FeedIndexer
    indexer_id: int
    person: Account
    client: TestClient
    work_id: int
    library_id: int


def make_world(net: FakeNetwork, *, right: str = "acquire", series_pos: float = 2.0, language: str = "en",
               title: str = TITLE) -> World:
    """The library holds the book as an e-book in ``language`` (English: a German search finds no format to beat)."""
    folder = books_folder("procure")
    book(folder, "falkner/laternen.epub", title=title, authors=(AUTHOR,), language=language, series=SERIES,
         series_pos=series_pos)
    library = make_library(folder, "Books")
    scan(library)
    person = make_account("rita")
    join(library, person.id, right)
    fake = FeedIndexer(INDEXER_HOST, secrets.token_hex(16))
    net.add(fake)
    indexer_id = add_indexer(fake)
    with SessionLocal() as db:
        work_id = db.scalar(select(Work.id))
    assert work_id is not None
    return World(net=net, indexer=fake, indexer_id=indexer_id, person=person, client=new_client(person),
                 work_id=work_id, library_id=library)


def allow_languages(*codes: str, original: bool | None = None) -> None:
    """The languages the operator allows for fetching books (settings, "Sprachen")."""
    with SessionLocal() as db:
        config = formats.config(db)
        config["languages"] = list(codes)
        if original is not None:
            config["original"] = original
        formats.save(db, config)


def search(world: World, **payload: Any) -> dict[str, Any]:
    """Starts a search for the world's book, runs it, and reads it."""
    body = {"work_id": world.work_id, **payload}
    answer = world.client.post("/api/v1/procure/searches", json=body)
    assert answer.status_code == 202, answer.text
    run_jobs()
    shown = world.client.get(f"/api/v1/procure/searches/{answer.json()['id']}")
    assert shown.status_code == 200, shown.text
    return shown.json()  # type: ignore[no-any-return]


def by_title(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["title"]: item for item in found["releases"]}


def codes(release: dict[str, Any]) -> list[str]:
    return [item["code"] for item in release["reasons"]]
