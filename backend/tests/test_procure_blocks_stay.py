"""What was blocked for a book stays blocked when the book of the sources becomes a work, and a release of what the library
has already does not go in beside it as a copy. Only invented books."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BlocklistEntry, Download, ForeignBook, Work
from app.services.procure import blocklist, checking, formats

from .bookmaker import make_epub
from .filing_helpers import download_roots, finish, german_epub, the_download, world_with
from .follow_helpers import BOOK, rows, sabnzbd
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, Item

KEY = "mara lind|die nebelbahn"
NAME = "Mara Lind - Die Nebelbahn - GERMAN - MP3 - EXAMPLE"


def release(name: str = NAME) -> SimpleNamespace:
    return SimpleNamespace(title=name, protocol="usenet", kind="audio", info_hash="", indexer_name="Example")


def a_book(ref: str = "dnb:1", title: str = "Die Nebelbahn") -> int:
    from app import clock

    with SessionLocal() as db:
        moment = clock.now()
        row = ForeignBook(token=ref[-6:].rjust(18, "x"), ref=ref, title=title, title_key=title, authors=["Mara Lind"],
                          language="de", data={}, seen_at=moment, created_at=moment)
        db.add(row)
        db.commit()
        return int(row.id)


def a_work() -> int:
    from app import clock

    with SessionLocal() as db:
        moment = clock.now()
        row = Work(title="Die Nebelbahn", added_at=moment, updated_at=moment)
        db.add(row)
        db.commit()
        return int(row.id)


def block_it(foreign_id: int, key: str = KEY) -> None:
    with SessionLocal() as db:
        blocklist.block(db, None, release(), work_id=None, foreign_id=foreign_id, reason="format_not_wanted", book=key)
        db.commit()


def blocked(*, work_id: int | None = None, foreign_id: int | None = None, key: str = KEY) -> bool:
    with SessionLocal() as db:
        found = blocklist.blocked_for(db, work_id=work_id, foreign_id=foreign_id, key=key)
        return blocklist.matches(found, NAME, "usenet")


def test_a_block_of_a_book_of_the_sources_counts_for_the_work_of_the_same_book(operator: Any) -> None:
    foreign = a_book()
    block_it(foreign)
    assert blocked(work_id=77)
    assert blocked(foreign_id=foreign)


def test_a_block_of_another_book_does_not_count(operator: Any) -> None:
    block_it(a_book())
    assert not blocked(work_id=77, key="mara lind|der salzpfad")
    assert not blocked(work_id=77, key="")


def test_the_blocks_go_with_the_book_when_it_becomes_a_work(operator: Any) -> None:
    foreign, work = a_book(), a_work()
    block_it(foreign, "")
    with SessionLocal() as db:
        blocklist.to_work(db, foreign, work)
        db.commit()
    assert blocked(work_id=work, key="")
    [entry] = rows(BlocklistEntry)
    assert (entry.work_id, entry.foreign_id) == (work, None)


def test_a_block_the_work_has_already_is_not_made_twice(operator: Any) -> None:
    foreign, work = a_book(), a_work()
    block_it(foreign, "")
    with SessionLocal() as db:
        blocklist.block(db, None, release(), work_id=work, foreign_id=None, reason="by_hand")
        db.commit()
        blocklist.to_work(db, foreign, work)
        db.commit()
        assert len(list(db.scalars(select(BlocklistEntry)))) == 1


def have(fmt: str = "epub", **values: Any) -> checking.Have:
    return checking.Have(**{"fmt": fmt, "language": "de", "title": "Die Nebelbahn", "pages": 300, **values})


def verdict_on(fmt: str, *haves: checking.Have, **probe: Any) -> checking.Verdict:
    values = {"format": fmt, "title": "Die Nebelbahn", "authors": ["Mara Lind"], "language": "de", "pages": 300, **probe}
    item = SimpleNamespace(path=f"a.{fmt}", format=fmt, size=2_000_000, origin="source", unit="", probe=values)
    wanted = checking.Wanted(kind="ebook", language="de", accepted=["de"], named_language="de",
                             subject={"title": "Die Nebelbahn", "titles": [{"text": "Die Nebelbahn", "language": "de"}],
                                      "authors": ["Mara Lind"], "series": "", "position": None},
                             config=formats.default_config(), haves=list(haves))
    return checking.judge_units(checking.units_of([item]), wanted)


def test_the_same_book_in_the_same_format_goes_in_no_second_time() -> None:
    verdict = verdict_on("epub", have())
    assert (verdict.outcome, verdict.code, verdict.values["have"]) == ("reject", "not_better", "epub")


def test_a_worse_format_of_the_same_book_goes_in_no_second_time() -> None:
    assert verdict_on("mobi", have(pages=300, fmt="epub", publisher="Verlag"), publisher="Verlag").code == "not_better"


def test_a_better_format_of_it_replaces_it() -> None:
    verdict = verdict_on("epub", have(fmt="mobi", publisher="Verlag"), publisher="Verlag")
    assert verdict.outcome == "take" and verdict.replaces


def test_another_length_of_text_stands_beside_it() -> None:
    assert verdict_on("epub", have(pages=120)).outcome == "take"


def test_nothing_of_it_in_the_library_goes_in() -> None:
    assert verdict_on("epub").outcome == "take"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def test_a_block_goes_along_when_the_e_book_is_filed_and_the_recording_wishes_on(operator: Any, net: FakeNetwork,
                                                                                 roots: Path) -> None:
    sab = sabnzbd(net)
    world = world_with(net, lambda folder: make_epub(folder / "a" / "b.epub", title="Ein anderes Buch",
                                                     authors=("Ida Brandt",), language="de"))
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    with SessionLocal() as db:
        db.add(ForeignBook(token="tok" + "b" * 13, ref="hardcover:78", title="Die Laternenstraße", authors=[AUTHOR],
                           language="de", series="Nebelchor", series_pos=2.0))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": "tok" + "b" * 13}], "kinds": ["ebook"],
                                                              "language": "de"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    [load] = rows(Download)
    with SessionLocal() as db:
        foreign = db.scalar(select(ForeignBook.id).where(ForeignBook.ref == "hardcover:78"))
        blocklist.block(db, None, release(), work_id=None, foreign_id=foreign, reason="format_not_wanted", book="")
        db.commit()
    folder = roots / "complete" / "ebook"
    german_epub(folder)
    finish(sab, load.client_ref, folder)
    assert the_download().state == "imported"
    [entry] = rows(BlocklistEntry)
    assert entry.foreign_id is None and entry.work_id == the_download().work_id


BAND_ONE, BAND_TWO = "Die Nebelbahn - Band 1", "Die Nebelbahn - Band 2"


def key_of_book(title: str) -> str:
    return blocklist.book_key({"authors": ["Mara Lind"], "title": title})


def block_for_work(work: int, key: str) -> None:
    with SessionLocal() as db:
        blocklist.block(db, None, release(), work_id=work, foreign_id=None, reason="other_volume", book=key)
        db.commit()


@pytest.mark.parametrize(("one", "other"), [
    (BAND_ONE, BAND_TWO),
    ("Die Nebelbahn: Teil 1", "Die Nebelbahn: Teil 2"),
    ("Die Nebelbahn (Nebel-Saga 1)", "Die Nebelbahn (Nebel-Saga 2)"),
    ("Die Nebelbahn: Der Anfang", "Die Nebelbahn: Das Ende"),
    ("Die Nebelbahn, Band 1", "Die Nebelbahn, Band 2"),
])
def test_volumes_of_a_series_share_no_key(one: str, other: str) -> None:
    assert key_of_book(one) and key_of_book(one) != key_of_book(other)


@pytest.mark.parametrize("title", ["Die Nebelbahn: Roman", "Die Nebelbahn (ungekürzt)", "Die Nebelbahn - Ein Thriller",
                                   "Die Nebelbahn: Thriller", "Nebelbahn, Die", "Die Nebelbahn (2019)",
                                   "Die Nebelbahn (Reihe 1-3)"])
def test_what_a_shop_adds_to_a_title_makes_no_other_book(title: str) -> None:
    assert key_of_book(title) == key_of_book("Die Nebelbahn")


def test_a_block_for_one_volume_does_not_count_for_the_next(operator: Any) -> None:
    one, two = a_work(), a_work()
    block_for_work(one, key_of_book(BAND_ONE))
    assert blocked(work_id=one, key=key_of_book(BAND_ONE))
    assert not blocked(work_id=two, key=key_of_book(BAND_TWO))


def test_a_block_of_a_book_of_the_sources_does_not_count_for_the_next_volume(operator: Any) -> None:
    foreign = a_book(title=BAND_ONE)
    block_it(foreign, key_of_book(BAND_ONE))
    assert not blocked(work_id=a_work(), key=key_of_book(BAND_TWO))
    assert blocked(work_id=a_work(), key=key_of_book(BAND_ONE))


def test_a_block_of_a_work_stays_with_that_work_whatever_the_key(operator: Any) -> None:
    one, two = a_work(), a_work()
    block_for_work(one, KEY)
    assert blocked(work_id=one, key=KEY)
    assert not blocked(work_id=two, key=KEY)
    with SessionLocal() as db:
        assert blocklist.entries_for(db, work_id=two, foreign_id=None, key=KEY) == {}


def test_a_block_whose_work_is_gone_finds_the_book_again_by_its_key_only(operator: Any) -> None:
    one = a_work()
    block_for_work(one, key_of_book(BAND_ONE))
    with SessionLocal() as db:
        db.get(BlocklistEntry, rows(BlocklistEntry)[0].id).work_id = None
        db.commit()
    assert not blocked(work_id=a_work(), key=key_of_book(BAND_TWO))
    assert blocked(work_id=a_work(), key=key_of_book(BAND_ONE))
