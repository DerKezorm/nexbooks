"""Where a wished book goes when several libraries take its kind: the operator's choice (settings, "Ablage"), else the
person names one; nexbooks never picks one by its name (a novel in "Kinderbücher" because it comes first). Only
invented books."""

from __future__ import annotations

from typing import Any

from app.db import SessionLocal
from app.services.procure import formats

from .library_helpers import book, books_folder, join, make_library, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import make_world


def second_library(world: Any) -> int:
    folder = books_folder("kinder")
    book(folder, "a/anderes.epub", title="Ein anderes Buch", authors=("Ida Brandt",), language="de")
    library = make_library(folder, "Kinderbücher")
    scan(library)
    join(library, world.person.id, "acquire")
    return library


def wish(world: Any, **more: Any) -> Any:
    return world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                             "kinds": ["ebook"], "language": "de", **more})


def test_with_one_library_the_book_goes_there_without_a_question(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    [kind] = [item for item in world.client.get("/api/v1/procure/book-options",
                                                 params={"work_id": world.work_id}).json()["kinds"]
              if item["kind"] == "ebook"]
    assert kind["choose"] is False and kind["default_library"] == world.library_id
    assert wish(world).status_code == 201


def test_with_several_libraries_and_no_choice_of_the_operator_the_person_names_one(operator: Any,
                                                                                  net: FakeNetwork) -> None:
    world = make_world(net)
    other = second_library(world)
    [kind] = [item for item in world.client.get("/api/v1/procure/book-options",
                                                 params={"work_id": world.work_id}).json()["kinds"]
              if item["kind"] == "ebook"]
    assert kind["choose"] is True and kind["default_library"] is None
    refused = wish(world)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "library_required"
    made = wish(world, library_id=other)
    assert made.status_code == 201, made.text
    assert made.json()["results"][0]["wish"]["library"]["id"] == other


def test_the_operators_choice_ends_the_question(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net)
    second_library(world)
    with SessionLocal() as db:
        config = formats.config(db)
        config["library"] = {"ebook": world.library_id, "audio": None}
        formats.save(db, config)
    assert wish(world).status_code == 201
