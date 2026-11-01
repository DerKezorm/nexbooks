"""The two libraries and the people the tests of Discover over the API share: what the caller has is worked out from
the libraries the caller may see, so the tests need people with libraries of their own. Every book is invented."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, LibraryMember, Work

from .bookmaker import cover_image
from .conftest import make_account, new_client
from .discover_helpers import Disc
from .library_helpers import book, books_folder, make_library
from .match_helpers import member


class Shelves(Disc):
    """The sources on and the lists of the German market fetched, and two libraries: alice has the first, bob the
    second, carol has none (and the operator ``tester`` is a member of neither)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(monkeypatch)
        self.settings(sources_enabled=False)
        self.first = books_folder("first")
        self.second = books_folder("second")
        book(self.first, "leuchtturm.epub", title="Der Leuchtturmwaerter", authors=("Mara Feldkamp",), language="de",
             subjects=("Roman",))
        book(self.first, "krone.epub", title="Die Krone der Weberinnen", authors=("Iris Quist",), language="de",
             series="Die Chroniken von Arvel", series_pos=1, subjects=("Fantasy",))
        book(self.first, "salz.epub", title="Die Salzwiese", authors=("I. Marwitz",), language="de",
             subjects=("Krimi",))
        # The same last name and another first name: not the author of "Nach dem Sturm" of the lists.
        book(self.first, "sturm.epub", title="Nach dem Sturm", authors=("Hans Feldkamp",), language="de")
        book(self.second, "orbit.epub", title="Orbit Null", authors=("Theo Valcourt",), language="de",
             subjects=("Science Fiction",), cover=cover_image((20, 60, 90)))
        book(self.second, "licht.epub", title="Kaltes Licht", authors=("Vera Brenner",), language="de",
             subjects=("Krimi",))
        self.first_id = make_library(self.first, "First", scan=True)
        self.second_id = make_library(self.second, "Second", scan=True)
        self.alice = make_account("alice")
        self.bob = make_account("bob")
        self.carol = make_account("carol")
        with SessionLocal() as db:
            db.query(LibraryMember).delete()
            db.commit()
        member(self.first_id, "alice", "read")
        member(self.second_id, "bob", "read")
        self.settings(sources_enabled=True)
        self.only("apple", "audible")
        self.fetch("de")
        self.clients: list[TestClient] = []

    def client(self, account: Account) -> TestClient:
        found = new_client(account)
        self.clients.append(found)
        return found

    def work_id(self, title: str) -> int:
        with SessionLocal() as db:
            return db.scalars(select(Work.id).where(Work.title == title)).one()


def read(client: TestClient, name: str = "best", **params: Any) -> dict[str, Any]:
    params = {"market": "de", "hide_owned": "false", **params}
    answer = client.get(f"/api/v1/discover/lists/{name}", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def by_title(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["title"]: item for item in body["items"]}


def token_of(client: TestClient, title: str, name: str = "best") -> str:
    return str(by_title(read(client, name))[title]["token"])
