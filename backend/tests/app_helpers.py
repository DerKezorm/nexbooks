"""What the tests of the surface of the iPhone app share: a device of an account, the app that speaks with its
token, and a small library of invented books in a folder whose name must never leave the server."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Account, Edition, Work
from app.services import devices

from .bookmaker import cover_image
from .conftest import make_account
from .library_helpers import book, books_folder, join, make_library, recording, run_jobs

#: A word in the names of the folders and files of the test books: no answer of the app may hold it.
HIDDEN = "hidden7f3a"


def device_token(account: Account, name: str = "Ada's iPhone") -> str:
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        _device, token = devices.make(db, row, name)
    return token


def app_of(token: str) -> TestClient:
    """The app: its token in every request, no cookie and no tab header."""
    return TestClient(app, base_url="http://testserver", headers={"Authorization": f"Bearer {token}"})


@dataclass
class Shelf:
    """A library ``Books`` (an e-book in a series with a cover, an audiobook of the same work with chapters, a second
    e-book) and a library ``Private`` (one e-book), each in a folder named with ``HIDDEN``. ``ada`` is a member of
    ``Books`` only, ``ben`` of both."""

    folder: Path
    private_folder: Path
    library: int
    private: int
    ada: Account
    ben: Account
    ada_app: TestClient
    ben_app: TestClient
    works: dict[str, int] = field(default_factory=dict)
    editions: dict[str, int] = field(default_factory=dict)

    def reload(self) -> None:
        with SessionLocal() as db:
            for edition in db.scalars(select(Edition)):
                key = f"{edition.title}/{edition.kind}"
                self.editions[key] = edition.id
                self.works[edition.title] = edition.work_id


def make_shelf() -> Shelf:
    folder = books_folder(f"books-{HIDDEN}")
    book(folder, f"Mara Lindqvist/{HIDDEN} Orchard.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",),
         language="en", series="Orchard Tales", series_pos=1.0, cover=cover_image((40, 90, 160)),
         description="A garden at the end of the world.")
    recording(folder, f"Mara Lindqvist/The Quiet Orchard {HIDDEN}/orchard.m4b", 600.0, title="The Quiet Orchard",
              album="The Quiet Orchard", artist="Mara Lindqvist", narrator="Iris Vale",
              chapters=[("Opening", 0.0), ("The Gate", 240.5)])
    book(folder, f"Jonas Brandauer/{HIDDEN} Kite.epub", title="Kite Season", authors=("Jonas Brandauer",),
         language="en")
    private_folder = books_folder(f"private-{HIDDEN}")
    book(private_folder, f"Ida Brandt/{HIDDEN} Secret.epub", title="A Secret Shelf", authors=("Ida Brandt",),
         language="en")
    library = make_library(folder, "Books")
    private = make_library(private_folder, "Private")
    run_jobs()
    from .library_helpers import scan

    scan(library)
    scan(private)
    ada, ben = make_account("ada"), make_account("ben")
    join(library, ada.id, "read")
    join(library, ben.id, "read")
    join(private, ben.id, "read")
    shelf = Shelf(folder=folder, private_folder=private_folder, library=library, private=private, ada=ada, ben=ben,
                  ada_app=app_of(device_token(ada)), ben_app=app_of(device_token(ben, "Ben's iPad")))
    shelf.reload()
    return shelf


def sync(client: TestClient, after: int = 0, limit: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"after": after}
    if limit is not None:
        params["limit"] = limit
    answer = client.get("/api/app/1/library", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()  # type: ignore[no-any-return]


def sync_all(client: TestClient, after: int = 0, limit: int | None = None) -> tuple[dict[int, dict[str, Any]], list[int],
                                                                                     int]:
    """Asks until the marker reaches the newest number: the works by id (the last word on each), what was removed, and
    the marker to ask with next."""
    works: dict[int, dict[str, Any]] = {}
    removed: list[int] = []
    for _round in range(10_000):
        page = sync(client, after, limit)
        for item in page["items"]:
            works[item["id"]] = item
        removed += page["removed"]
        after = page["marker"]
        if page["marker"] >= page["latest"]:
            return works, removed, after
    raise AssertionError("the marker never reached the newest number")


def titles(works: dict[int, dict[str, Any]]) -> list[str]:
    return sorted(item["title"] for item in works.values())


def work_ids() -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(Work.id).order_by(Work.id)))
