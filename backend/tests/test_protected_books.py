"""A copy-protected book: found when the library is read, said so on the list of what needs a person (even when the
sources know the book), and turned down by the check after a download. nexbooks does nothing to get around the protection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BookFile
from app.services import catalog
from app.services.match import review
from app.services.procure import checking, formats

from .fake_sources import ol_doc
from .library_helpers import book, books_folder, make_library, scan
from .match_helpers import World, entries, member

ISBN = "9780000000002"
KOBO_RIGHTS = {"rights.xml": b"<?xml version='1.0'?><kdrm><sign>AAAA</sign></kdrm>"}
SOURCE = ol_doc("OL1W", "The Quiet Orchard", ["Mara Lindqvist"], languages=("eng",), cover=77, year=2019, isbns=(ISBN,))


def test_a_protected_book_is_flagged_when_the_library_is_read(client: TestClient, operator: Account) -> None:
    folder = books_folder("locked")
    book(folder, "kobo.epub", title="Kobo Book", extra_entries=KOBO_RIGHTS, scrambled=(0, 1, 2))
    book(folder, "scrambled.epub", title="Scrambled Book", scrambled=(1,))
    book(folder, "free.epub", title="Free Book")
    library = make_library(folder, "Locked")
    member(library)
    scan(library)
    with SessionLocal() as db:
        flags = {row.path: bool((row.probe or {}).get("drm")) for row in db.scalars(select(BookFile))}
    assert flags == {"kobo.epub": True, "scrambled.epub": True, "free.epub": False}


def test_a_protected_book_the_sources_know_is_still_on_the_list_for_a_person(client: TestClient, operator: Account,
                                                                             world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [SOURCE]
    folder = books_folder("locked")
    book(folder, "kobo.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",), language="en", isbn=ISBN,
         extra_entries=KOBO_RIGHTS, scrambled=(0, 1, 2))
    library = make_library(folder, "Locked")
    member(library)
    scan(library)
    assert entries() == [("The Quiet Orchard", "maybe", "protected", "")]
    listed = client.get(f"/api/v1/libraries/{library}/review").json()
    [item] = listed["items"]
    assert item["reason"] == "protected" and "copy-protected" in item["reason_text"]
    # It is the book the source says: a person may take that, or leave the book as it is.
    assert [proposal["title"] for proposal in item["proposals"]] == ["The Quiet Orchard"]
    assert review.to_look_at(SessionLocal(), [library]) == {library: 1}


def test_a_book_that_is_not_protected_is_not_on_the_list_for_that(client: TestClient, operator: Account,
                                                                  world: World) -> None:
    world.only("openlibrary")
    world.fake.ol = [SOURCE]
    folder = books_folder("free")
    book(folder, "free.epub", title="The Quiet Orchard", authors=("Mara Lindqvist",), language="en", isbn=ISBN)
    library = make_library(folder, "Free")
    member(library)
    scan(library)
    assert entries() == [("The Quiet Orchard", "sure", "", "")]


# ---- The check after a download -----------------------------------------------------------------------------------------


def _unit(name: str, fmt: str, drm: bool, title: str = "The Quiet Orchard") -> checking.Unit:
    file = SimpleNamespace(path=f"{name}/{name}.{fmt}", format=fmt, probe={"drm": drm, "text_language": "en"}, size=1,
                           origin="source", unit=name)
    meta = catalog.Meta(kind="ebook", title=title, authors=["Mara Lindqvist"], language="en")
    return checking.Unit(key=f"source|{name}", kind="ebook", files=[file], meta=meta)


def _wanted(**over: Any) -> checking.Wanted:
    subject = {"title": "The Quiet Orchard", "authors": ["Mara Lindqvist"], "language": "en", "position": None}
    return checking.Wanted(kind="ebook", language="en", accepted=["en"], subject=subject,
                           config=formats.default_config(), named_author=True, **over)


def test_a_release_whose_book_is_copy_protected_is_turned_down_after_the_download() -> None:
    verdict = checking.judge_units([_unit("locked", "epub", True)], _wanted())
    assert (verdict.outcome, verdict.code) == ("reject", "protected")


def test_the_book_that_is_not_protected_is_taken_and_the_protected_one_beside_it_is_left() -> None:
    verdict = checking.judge_units([_unit("locked", "epub", True), _unit("free", "epub", False)], _wanted())
    assert verdict.outcome == "take"
    assert list(verdict.take) == ["source|free"] and verdict.drop == {"source:locked/locked.epub": "protected"}


def test_a_book_with_one_format_that_opens_is_not_protected() -> None:
    locked = _unit("both", "epub", True)
    locked.files.append(SimpleNamespace(path="both/both.pdf", format="pdf", probe={"drm": False}, size=1,
                                        origin="source", unit="both"))
    assert checking.judge_units([locked], _wanted()).outcome == "take"


def test_a_protected_file_is_a_reason_the_blocklist_knows() -> None:
    from app.services.procure import blocklist, importing

    assert "protected" in blocklist.REASONS and importing.BLOCK_REASONS["protected"] == "protected"
