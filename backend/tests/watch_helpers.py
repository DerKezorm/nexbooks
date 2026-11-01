"""A world for watching an author: the invented author Edmund Halloran and his catalogue (``oeuvre_fakes``),
one library with a book of his, three people (alice may ask for books, bob only reads, carol has no library), an indexer
that is only there to be switched on (no search is run), and a clock that jumps. Every name is invented."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import (
    Account,
    AuthorWatch,
    CatalogPerson,
    CatalogWork,
    ForeignBook,
    Person,
    ProcureWish,
    WatchItem,
    Wish,
)
from app.services.oeuvre import watch as watching

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, join, make_library
from .oeuvre_fakes import Sources
from .procure_fakes import FakeNetwork
from .procure_search_helpers import INDEXER_HOST, FeedIndexer, add_indexer

AUTHOR = "Edmund Halloran"
#: The one work of the catalogue that is still to come: English on 15 November 2026, German on 3 March 2027.
KEEPER = "Der Hüter der verlorenen Zimmer"
#: The book of the sources behind it is the book in the original language.
ORIGINAL = "The Keeper of Lost Rooms"


class World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, net: FakeNetwork) -> None:
        self.sources = Sources(monkeypatch)
        folder = books_folder("a2-first")
        book(folder, "ufer.epub", title="Das neunte Ufer", authors=(AUTHOR,), language="de", isbn="9783000000132")
        self.library = make_library(folder, "First", scan=True)
        self.alice = make_account("alice")
        self.bob = make_account("bob")
        self.carol = make_account("carol")
        with SessionLocal() as db:
            for account in db.scalars(select(Account).where(Account.name.in_(("alice", "bob", "carol")))):
                account.language = "de"
            db.commit()
        join(self.library, self.alice.id, "acquire")
        join(self.library, self.bob.id, "read")
        fake = FeedIndexer(INDEXER_HOST, secrets.token_hex(16))
        net.add(fake)
        self.indexer = add_indexer(fake)
        self.net = net
        self.sources.fake.log.clear()
        self.clients: list[TestClient] = []

    def client(self, account: Account) -> TestClient:
        found = new_client(account)
        self.clients.append(found)
        return found

    def person_id(self, name: str = AUTHOR) -> int:
        with SessionLocal() as db:
            ident = db.scalar(select(Person.id).where(Person.name == name))
            assert ident is not None
            return ident

    def built(self, account: Account | None = None) -> str:
        """Builds the catalogue of the author (as a person who looks at the page) and returns its token."""
        client = self.client(account or self.alice)
        assert client.get(f"/api/v1/people/{self.person_id()}/catalog").status_code == 200
        self.sources.build()
        with SessionLocal() as db:
            token = db.scalar(select(CatalogPerson.token).where(CatalogPerson.key == "edmund halloran"))
        assert token is not None
        return token

    def watch(self, account: Account, token: str, **body: Any) -> dict[str, Any]:
        answer = self.client(account).put(f"/api/v1/watching/authors/{token}", json=body)
        assert answer.status_code == 200, answer.text
        return answer.json()  # type: ignore[no-any-return]

    def beat(self) -> dict[str, int]:
        with SessionLocal() as db:
            return watching.tick(db)

    def days(self, days: float) -> None:
        self.sources.days(days)

    # ---- What is in the database ---------------------------------------------------------------------------------

    def wishes(self) -> list[ProcureWish]:
        with SessionLocal() as db:
            rows = list(db.scalars(select(ProcureWish).order_by(ProcureWish.id)))
            for row in rows:
                db.expunge(row)
            return rows

    def items(self) -> list[WatchItem]:
        with SessionLocal() as db:
            rows = list(db.scalars(select(WatchItem).order_by(WatchItem.id)))
            for row in rows:
                db.expunge(row)
            return rows

    def listed(self, account: Account) -> list[str]:
        with SessionLocal() as db:
            return [title for (title,) in db.execute(select(Wish.title).where(Wish.user_id == account.id))]

    def watch_row(self, account: Account) -> AuthorWatch:
        with SessionLocal() as db:
            row = db.scalar(select(AuthorWatch).where(AuthorWatch.user_id == account.id))
            assert row is not None
            db.expunge(row)
            return row

    def titles_of(self, wishes: list[ProcureWish]) -> list[str]:
        with SessionLocal() as db:
            return sorted(db.get(ForeignBook, row.foreign_id).title for row in wishes if row.foreign_id)  # type: ignore[union-attr]

    def edit_work(self, key_part: str, **changes: Any) -> None:
        """Changes a work of the catalogue straight in the database (as a build of another day would have)."""
        with SessionLocal() as db:
            for work in db.scalars(select(CatalogWork)):
                if key_part in str(work.titles):
                    for name, value in changes.items():
                        setattr(work, name, value)
            db.commit()

    def new_work(self, title: str, *, language: str, released: str, kind: str = "roman", ebook: bool = True,
                 audio: bool = False, key: str = "newbook") -> None:
        """A work the catalogue did not hold before (as a later build would bring), with a book of the sources behind it."""
        from app.models import CatalogWork

        with SessionLocal() as db:
            person = db.scalar(select(CatalogPerson).where(CatalogPerson.key == "edmund halloran"))
            assert person is not None
            foreign = ForeignBook(token=secrets.token_urlsafe(12)[:16], ref=f"test:{key}", title=title, language=language,
                                  released=released, authors=[AUTHOR], title_key=key, seen_at=clock.now(),
                                  created_at=clock.now())
            db.add(foreign)
            db.flush()
            db.add(CatalogWork(catalog_person_id=person.id, key=key, foreign_id=foreign.id, kind=kind, why="",
                               orig_language=language, year=int(released[:4]), sources=["openlibrary", "hardcover"],
                               isbns=[], refs=[], weight=3,
                               titles={language: {"title": title, "year": int(released[:4]), "released": released,
                                                  "ebook": ebook, "audio": audio, "sources": ["openlibrary"]}}))
            person.built_at = max(person.built_at or clock.now(), clock.now()) + timedelta(seconds=1)
            db.commit()


def close(world: World) -> None:
    for client in world.clients:
        client.close()
