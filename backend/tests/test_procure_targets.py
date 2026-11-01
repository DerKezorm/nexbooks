"""A library nexbooks may not write in is no place for a book (probe with a real indexer): the target is chosen among the
libraries it may write in, the dialog and the settings say which one is not, and nothing is searched or loaded for a
place that takes nothing; the wish waits with the reason."""

from __future__ import annotations

from typing import Any

import pytest

from app.db import SessionLocal
from app.models import Library
from app.services.organize import plan as planning
from app.services.procure import formats, search

from .follow_helpers import AUDIO, audio_world, downloads, sabnzbd, wish, wishes_of
from .library_helpers import books_folder, join, make_library
from .procure_fakes import FakeNetwork
from .procure_search_helpers import World

LOADING = "/api/v1/procure/searches"


def read_only(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """The libraries of these names are mounted read-only (the probe of "Ordnen" says so), the others are writable."""
    with SessionLocal() as db:
        paths = {library.path: library.name for library in db.query(Library)}
    real = planning.writable

    def probe(path: str) -> tuple[bool, str]:
        if paths.get(path) in names:
            return False, "read_only"
        return real(path)

    monkeypatch.setattr(planning, "writable", probe)


def with_annex(world: World) -> int:
    """A second library that sorts before the first one by name, which the person may ask for books in."""
    annex = make_library(books_folder("annex"), "Annex")
    join(annex, world.person.id, "acquire")
    return annex


def chosen(world: World) -> dict[str, str]:
    with SessionLocal() as db:
        return {kind: library.name for kind, library in search.targets(db, world.person.id).items()}


def test_the_place_is_the_first_library_nexbooks_may_write_in(operator: Any, net: FakeNetwork,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net)
    with_annex(world)
    assert chosen(world) == {"ebook": "Annex", "audio": "Annex"}
    read_only(monkeypatch, "Annex")
    assert chosen(world) == {"ebook": "Books", "audio": "Books"}


def test_without_a_writable_library_the_first_one_stays_the_place(operator: Any, net: FakeNetwork,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net)
    with_annex(world)
    read_only(monkeypatch, "Annex", "Books")
    assert chosen(world) == {"ebook": "Annex", "audio": "Annex"}


def test_a_place_the_operator_chose_stays_though_it_is_read_only(operator: Any, net: FakeNetwork,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """What the operator chose is not swapped behind their back; the settings and the dialog say what is wrong."""
    world = audio_world(net)
    annex = with_annex(world)
    with SessionLocal() as db:
        formats.save(db, {**formats.config(db), "library": {"ebook": annex, "audio": annex}})
    read_only(monkeypatch, "Annex")
    assert chosen(world) == {"ebook": "Annex", "audio": "Annex"}


def test_the_dialog_tells_which_libraries_are_read_only_and_defaults_to_one_that_is_not(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    world = audio_world(net)
    annex = with_annex(world)
    read_only(monkeypatch, "Annex")
    shown = world.client.get("/api/v1/procure/book-options", params={"work_id": world.work_id}).json()
    for entry in shown["kinds"]:
        by_id = {item["id"]: item for item in entry["libraries"]}
        assert by_id[annex]["writable"] is False and by_id[annex]["write_problem"] == "read_only"
        assert by_id[world.library_id]["writable"] is True and by_id[world.library_id]["write_problem"] == ""
        assert entry["default_library"] == world.library_id
    targets = world.client.get("/api/v1/procure/targets").json()["targets"]
    assert {item["name"]: (item["writable"], item["write_problem"]) for item in targets} == {
        "Annex": (False, "read_only"), "Books": (True, "")}


def test_the_settings_tell_which_library_is_read_only(operator: Any, client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    library = make_library(books_folder("ebooks"), "Ebooks", kind="ebook")
    other = make_library(books_folder("audio"), "Audio", kind="audio")
    read_only(monkeypatch, "Audio")
    answer = client.get("/api/v1/procure/formats").json()["libraries"]
    by_id = {item["id"]: item for item in answer}
    assert (by_id[library]["writable"], by_id[library]["write_problem"]) == (True, "")
    assert (by_id[other]["writable"], by_id[other]["write_problem"]) == (False, "read_only")


def test_a_release_is_not_loaded_into_a_library_nexbooks_may_not_write_in(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    from .procure_search_helpers import search as run_search

    found = run_search(world, language="de")
    read_only(monkeypatch, "Books")
    release = next(item["id"] for item in found["releases"] if item["title"] == AUDIO)
    answer = world.client.post(f"{LOADING}/{found['id']}/releases/{release}/load", json={})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "target_not_writable"
    assert answer.json()["detail"]["reason"] == "read_only" and answer.json()["detail"]["library_id"] == world.library_id
    assert sab.jobs == {} and downloads() == [] and world.indexer.fetched == []


def test_a_wish_for_a_library_that_takes_nothing_makes_no_search_and_waits_with_the_reason(
        operator: Any, net: FakeNetwork, monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    read_only(monkeypatch, "Books")
    made = wish(world)
    [result] = made["results"]
    assert result["status"] == "created"
    [shown] = wishes_of(world)
    assert shown["state"] == "not_found" and shown["detail"]["load_error"] == "target_not_writable"
    assert shown["next_try_at"] is not None
    assert world.indexer.functions() == []
    assert world.indexer.fetched == [] and sab.jobs == {} and downloads() == []


def test_a_wish_for_a_writable_library_is_searched_as_before(operator: Any, net: FakeNetwork,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    sab = sabnzbd(net)
    world = audio_world(net)
    with_annex(world)
    read_only(monkeypatch, "Annex")
    wish(world)
    from .library_helpers import run_jobs

    run_jobs()
    [shown] = wishes_of(world)
    assert shown["state"] == "loading" and shown["library"]["name"] == "Books" and len(sab.jobs) == 1
