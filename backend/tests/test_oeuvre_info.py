"""A photo and a short text for an author nobody of the libraries is: only when two
works of the catalogue are works of the Wikidata item, never on one title. Every person, title and answer is invented
(``tests/fake_wikidata.py``)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import CatalogPerson, CatalogWork, Job
from app.services import jobs, photos, settings_service
from app.services.oeuvre import info, store
from app.services.sources import http

from .conftest import make_account, new_client
from .fake_wikidata import AUTHOR, FakeAuthors


class Net:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fake = FakeAuthors()
        self.offset = 0.0
        start = clock.now()
        monkeypatch.setattr(clock, "now", lambda: start + timedelta(seconds=self.offset))
        monkeypatch.setattr(http, "sleep", self.advance)
        monkeypatch.setattr(http, "transport", self.fake)
        with SessionLocal() as db:
            settings_service.save(db, {"sources_enabled": True, "author_info": True, "source_wikidata": True})

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    return Net(monkeypatch)


def author(titles: list[tuple[str, str, list[str]]]) -> int:
    """An author of the catalogue (nobody of the libraries) with works ``(title, kind, sources)``, built."""
    with SessionLocal() as db:
        row = store.for_name(db, "Ada Feldkamp")
        assert row is not None
        row.built_at, row.state, row.orig_language = clock.now(), "ready", "en"
        db.flush()
        for index, (title, kind, sources) in enumerate(titles):
            db.add(CatalogWork(catalog_person_id=row.id, key=f"k{index}", kind=kind, titles={
                "en": {"title": title, "year": 2000 + index, "released": "", "ebook": True, "audio": False}},
                sources=sources, weight=10 - index))
        db.commit()
        return row.id


def looked_up(ident: int) -> dict[str, Any]:
    with SessionLocal() as db:
        info.look_up(db, ident)
    with SessionLocal() as db:
        row = db.get(CatalogPerson, ident)
        assert row is not None
        return row.info if isinstance(row.info, dict) else {}


TWO = ["openlibrary", "hardcover"]


def test_two_works_of_the_catalogue_among_the_items_works_give_the_photo_and_the_text(net: Net) -> None:
    ident = author([("The Quiet Orchard", "roman", TWO), ("Alpha", "roman", TWO), ("Gamma", "roman", TWO)])
    found = looked_up(ident)
    assert found["state"] == "sure" and found["qid"] == AUTHOR
    assert found["photo"] and photos.path_of(found["photo"]) is not None
    assert found["credit"]["license"] and found["bios"]["texts"]["de"]["text"] == "erfundene Schriftstellerin"
    client = new_client(make_account("nora"))
    with SessionLocal() as db:
        token = db.get(CatalogPerson, ident).token  # type: ignore[union-attr]
    page = client.get(f"/api/v1/catalog/authors/{token}").json()
    assert page["photo_url"] and page["credit"]["license"] and page["bio"]["texts"][0]["source"] == "wikidata"
    picture = client.get(page["photo_url"])
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"
    assert client.get(f"/api/v1/catalog/authors/{token}x/photo").status_code == 404
    client.close()


@pytest.mark.parametrize("titles", [
    # One title, however much it says by itself, is not enough for an author nobody of the libraries is.
    [("The Quiet Orchard", "roman", TWO), ("Gamma", "roman", TWO)],
    # An unsure work, or one only one source knows, is no proof.
    [("The Quiet Orchard", "roman", TWO), ("Alpha", "unsure", TWO)],
    [("The Quiet Orchard", "roman", TWO), ("Alpha", "roman", ["hardcover"])],
])
def test_less_than_two_works_give_no_photo(net: Net, titles: list[tuple[str, str, list[str]]]) -> None:
    found = looked_up(author(titles))
    assert found.get("state") in (None, "none") and "photo" not in found
    with SessionLocal() as db:
        token = db.scalar(select(CatalogPerson.token).where(CatalogPerson.key == "ada feldkamp"))
    client = new_client(make_account("nora"))
    page = client.get(f"/api/v1/catalog/authors/{token}").json()
    assert page["photo_url"] is None and page["bio"] is None
    client.close()


def test_a_look_up_is_queued_after_a_build_and_not_again_too_soon(net: Net) -> None:
    ident = author([("The Quiet Orchard", "roman", TWO), ("Alpha", "roman", TWO)])
    with SessionLocal() as db:
        row = db.get(CatalogPerson, ident)
        assert row is not None and info.enqueue(db, row)
        db.commit()
        assert db.scalar(select(Job.kind).where(Job.kind == "cinfo", Job.ref == ident)) == "cinfo"
    assert jobs.run_pending(10, lane="catalog") >= 1
    with SessionLocal() as db:
        row = db.get(CatalogPerson, ident)
        assert row is not None and row.info["state"] == "sure"
        assert not info.enqueue(db, row)
        # Switched off: nothing is asked.
        settings_service.save(db, {"author_info": False})
        row.info = None
        assert not info.enqueue(db, row)


def test_the_photo_of_the_catalogue_is_none_once_the_author_is_one_of_the_libraries(net: Net) -> None:
    ident = author([("The Quiet Orchard", "roman", TWO), ("Alpha", "roman", TWO)])
    found = looked_up(ident)
    assert found["photo"]
    with SessionLocal() as db:
        row = db.get(CatalogPerson, ident)
        assert row is not None
        token = row.token
        # A look-up that found nobody for sure keeps no photo to show, whatever a file says.
        row.info = {**found, "state": "none"}
        db.commit()
    client = new_client(make_account("nora"))
    assert client.get(f"/api/v1/catalog/authors/{token}/photo").status_code == 404
    assert client.get(f"/api/v1/catalog/authors/{token}").json()["photo_url"] is None
    client.close()
