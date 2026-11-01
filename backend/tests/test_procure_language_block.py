"""A release turned down for its language is blocked for the wishes that do not take that language, and only for them:
a wish in that language looks at it again. The list shows the reason without the language it keeps. Only invented
books and names."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.db import SessionLocal
from app.models import BlocklistEntry
from app.services.procure import blocklist

from .procure_fakes import FakeNetwork
from .procure_search_helpers import Item, by_title, codes, make_world, search

ENGLISH = "Rune.Falkner-Die.Laternenstrasse.EPUB-EXAMPLE"


def blocked(world: Any, language: str, reason: str = "other_language") -> int:
    release = SimpleNamespace(title=ENGLISH, protocol="usenet", kind="ebook", info_hash="", indexer_name="Shelf")
    with SessionLocal() as db:
        entry = blocklist.block(db, None, release, work_id=world.work_id, foreign_id=None, reason=reason,
                                library_id=world.library_id,
                                language=language)
        db.commit()
        return entry.id


def test_a_release_blocked_for_its_language_is_blocked_only_for_the_wishes_that_do_not_take_it(
        operator: Any, net: FakeNetwork) -> None:
    world = make_world(net, language="de")
    blocked(world, "en")
    with SessionLocal() as db:
        for languages, held in ((["de"], True), (["en"], False), (["de", "en"], False), ([], True)):
            found = blocklist.blocked_for(db, work_id=world.work_id, foreign_id=None, languages=languages)
            assert blocklist.matches(found, ENGLISH, "usenet") is held, languages


def test_a_block_of_another_reason_or_without_a_language_holds_for_every_wish(operator: Any,
                                                                             net: FakeNetwork) -> None:
    world = make_world(net, language="de")
    blocked(world, "")
    with SessionLocal() as db:
        found = blocklist.blocked_for(db, work_id=world.work_id, foreign_id=None, languages=["en"])
        assert blocklist.matches(found, ENGLISH, "usenet")
        db.query(BlocklistEntry).delete()
        db.commit()
    blocked(world, "en", reason="sample")
    with SessionLocal() as db:
        assert db.query(BlocklistEntry).one().reason == "sample"
        found = blocklist.blocked_for(db, work_id=world.work_id, foreign_id=None, languages=["en"])
        assert blocklist.matches(found, ENGLISH, "usenet")


def test_a_search_in_the_language_of_the_block_takes_the_release_again(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net, language="de")
    world.indexer.items = [Item(ENGLISH)]
    blocked(world, "en")
    assert "blocklisted" in codes(by_title(search(world, language="de"))[ENGLISH])
    english = by_title(search(world, language="en"))[ENGLISH]
    assert "blocklisted" not in codes(english) and english["blocked_by"] is None


def test_the_list_shows_the_reason_without_the_language(operator: Any, net: FakeNetwork) -> None:
    world = make_world(net, language="de")
    blocked(world, "en")
    with SessionLocal() as db:
        assert db.query(BlocklistEntry).one().reason == "other_language:en"
    [entry] = world.client.get("/api/v1/procure/blocklist").json()["entries"]
    assert entry["reason"] == "other_language"
