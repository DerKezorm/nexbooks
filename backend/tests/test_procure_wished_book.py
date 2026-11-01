"""A book fetched for a wish goes in as the book wished.

The real case: an e-book and a recording of one book of the sources were wished at once. The recording went in first,
as a work of its own. The e-book's files called it only by its series and a number; a person took it
for the book anyway, it went into the work, and the next reading of the library put the recording out again ("it is not
the book of the others"): two entries for one book. After the person joined them, the work was called after the file,
and a wish for the e-book in another language looked on. Here the same shapes with an invented book: "Die
Laternenstraße", volume 2 of "Nebelchor", by Rune Falkner.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Download, Edition, ForeignBook, ProcureWish, Work
from app.services import catalog, title_shape

from .bookmaker import make_epub
from .filing_helpers import download_roots, editions, finish, german_epub, history, recording, world_with
from .follow_helpers import AUDIO, BOOK, rows, sabnzbd, wish_rows
from .library_helpers import run_jobs, scan
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE, Item

TOKEN = "tok" + "h" * 13


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return download_roots(tmp_path, monkeypatch)


def other_book(folder: Path) -> None:
    make_epub(folder / "brandt" / "anderes.epub", title="Ein anderes Buch", authors=("Ida Brandt",), language="de")


def wish_both(net: FakeNetwork) -> tuple[Any, Any, dict[str, Download]]:
    sab = sabnzbd(net)
    world = world_with(net, other_book)
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,)),
                           Item(AUDIO, size=400_000_000, categories=(3130,))]
    with SessionLocal() as db:
        db.add(ForeignBook(token=TOKEN, ref="hardcover:78", title=TITLE, authors=[AUTHOR], language="de",
                           series=SERIES, series_pos=2.0, released="2021"))
        db.commit()
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}],
                                                              "kinds": ["ebook", "audio"], "language": "de"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    loads = {row.kind: row for row in rows(Download)}
    assert set(loads) == {"ebook", "audio"}
    return world, sab, loads


def work_of(kind: str) -> int:
    [edition] = [item for item in editions() if item.kind == kind and item.title != "Ein anderes Buch"]
    return edition.work_id


def test_a_recording_and_an_e_book_of_one_wish_stay_one_book_whatever_the_file_calls_it(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    _world, sab, loads = wish_both(net)
    audio_folder = roots / "complete" / "audio"
    recording(audio_folder, parts=2)
    finish(sab, loads["audio"].client_ref, audio_folder)
    book_folder = roots / "complete" / "ebook"
    book_folder.mkdir(parents=True)
    german_epub(book_folder, title=f"{SERIES} 2")
    finish(sab, loads["ebook"].client_ref, book_folder)
    # The series and the number of the volume wished, by the author wished: that book, no question.
    assert {row.kind: row.state for row in rows(Download)} == {"ebook": "imported", "audio": "imported"}
    # Read the library again, as the next scan does: nothing parts the two.
    with SessionLocal() as db:
        library_id = db.scalar(select(Download.library_id).where(Download.kind == "audio"))
    assert library_id is not None
    scan(library_id)
    run_jobs()
    assert work_of("ebook") == work_of("audio")
    with SessionLocal() as db:
        work = db.get(Work, work_of("audio"))
        assert work is not None and work.title == TITLE
        book = db.scalar(select(Edition).where(Edition.kind == "ebook", Edition.work_id == work.id))
        assert book is not None and book.title == TITLE and book.grouped_by == "wish"
        audio = db.scalar(select(Edition).where(Edition.kind == "audio", Edition.work_id == work.id))
        assert audio is not None and audio.grouped_by == "wish"
    line = next(item for item in history() if item.kind == "ebook" and item.result == "done")
    assert line.note["file_title"] == f"{SERIES} 2" and line.note["filed_as"] == TITLE


def test_files_a_person_took_for_the_book_never_push_the_other_kind_out(operator: Any, net: FakeNetwork,
                                                                        roots: Path) -> None:
    """The files of the e-book carry another title altogether (no shape nexbooks can tell): the person took them for the
    book. The recording the wish filed before stays in the work all the same."""
    world, sab, loads = wish_both(net)
    audio_folder = roots / "complete" / "audio"
    recording(audio_folder, parts=2)
    finish(sab, loads["audio"].client_ref, audio_folder)
    book_folder = roots / "complete" / "ebook"
    book_folder.mkdir(parents=True)
    german_epub(book_folder, title="Ein Titel aus dem Verlag", series=None, series_pos=None)
    finish(sab, loads["ebook"].client_ref, book_folder)
    row = next(item for item in rows(Download) if item.kind == "ebook")
    assert row.problem_code == "files_unassigned"
    assert world.client.post(f"/api/v1/procure/downloads/{row.id}/decide", json={"action": "take"}).status_code == 200
    run_jobs()
    scan(row.library_id)
    run_jobs()
    assert work_of("ebook") == work_of("audio")


def test_a_later_download_for_the_book_of_the_sources_joins_the_work_of_the_first(operator: Any, net: FakeNetwork,
                                                                                    roots: Path) -> None:
    """The second kind wished later, for a book of the sources nexbooks knows by no name at a source: it goes where the
    first download for that book went, not into a work of its own."""
    sab = sabnzbd(net)
    world = world_with(net, other_book)
    world.indexer.items = [Item(AUDIO, size=400_000_000, categories=(3130,))]
    with SessionLocal() as db:
        db.add(ForeignBook(token=TOKEN, ref="hardcover:", title=TITLE, authors=[AUTHOR], language="de",
                           series=SERIES, series_pos=2.0))
        db.commit()

    def ask(kind: str) -> None:
        reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"book": TOKEN}], "kinds": [kind],
                                                                  "language": "de"})
        assert reply.status_code == 201, reply.text
        run_jobs()

    ask("audio")
    [audio] = rows(Download)
    audio_folder = roots / "complete" / "audio"
    recording(audio_folder, parts=2)
    finish(sab, audio.client_ref, audio_folder)
    world.indexer.items = [Item(BOOK, size=2_000_000, categories=(7120,))]
    ask("ebook")
    book = next(row for row in rows(Download) if row.kind == "ebook")
    assert book.work_id is None and book.foreign_id is not None
    book_folder = roots / "complete" / "ebook"
    book_folder.mkdir(parents=True)
    german_epub(book_folder, title=f"{SERIES} 2")
    finish(sab, book.client_ref, book_folder)
    assert {row.kind: row.state for row in rows(Download)} == {"ebook": "imported", "audio": "imported"}
    assert work_of("ebook") == work_of("audio")
    with SessionLocal() as db:
        edition = db.scalar(select(Edition).where(Edition.kind == "ebook", Edition.work_id == work_of("audio")))
        assert edition is not None and edition.title == TITLE


def test_an_e_book_whose_file_names_a_series_and_its_number_takes_the_title_wished(
        operator: Any, net: FakeNetwork, roots: Path) -> None:
    """The check finds the book by its series and volume: it goes in under the title wished, never as "Nebelchor 2"."""
    world, sab, loads = wish_both(net)
    book_folder = roots / "complete" / "ebook"
    book_folder.mkdir(parents=True)
    german_epub(book_folder, title=f"{TITLE} - {SERIES} Band 2 by uploader")
    finish(sab, loads["ebook"].client_ref, book_folder)
    row = next(item for item in rows(Download) if item.kind == "ebook")
    assert row.state == "imported", (row.state, row.problem_code, row.failed_reason)
    with SessionLocal() as db:
        book = db.scalar(select(Edition).where(Edition.kind == "ebook", Edition.title == TITLE))
        assert book is not None
    assert world is not None


def test_joined_by_a_person_the_work_is_called_after_the_title_not_after_the_shortest_file(
        operator: Any, net: FakeNetwork) -> None:
    from .library_helpers import recording as library_recording

    def fill(folder: Path) -> None:
        library_recording(folder, "falkner/laternen/teil.m4b", 600.0, title=TITLE, album=TITLE, artist=AUTHOR)
        make_epub(folder / "falkner" / "nebelchor2.epub", title=f"{SERIES} 2", authors=(AUTHOR,), language="de",
                  series=SERIES, series_pos=2.0)

    world = world_with(net, fill, right="manage")
    world.indexer.items = []
    audio_work, book_work = work_of("audio"), work_of("ebook")
    assert audio_work != book_work
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": audio_work}],
                                                              "kinds": ["ebook"]})
    assert reply.status_code == 201, reply.text
    run_jobs()
    [open_wish] = wish_rows()
    assert open_wish.state == "not_found" and open_wish.language == ""
    with SessionLocal() as db:
        book = db.scalar(select(Edition).where(Edition.kind == "ebook", Edition.work_id == book_work))
        assert book is not None
        book_id = book.id
    joined = world.client.post(f"/api/v1/editions/{book_id}/join", json={"work_id": audio_work})
    assert joined.status_code == 200, joined.text
    with SessionLocal() as db:
        work = db.get(Work, audio_work)
        assert work is not None and work.title == TITLE
        # The recording was put together again (its language was derived from the e-book it now stands with): it
        # stays with the book a person joined it to.
        kinds = {e.kind: e.grouped_by for e in db.scalars(select(Edition).where(Edition.work_id == audio_work))}
        assert kinds == {"audio": "hand", "ebook": "hand"}
    # The wish has the book it looked for now: it is done and searches no more.
    [row] = wish_rows()
    assert row.state == "done" and row.detail == {"reason": "met_elsewhere"}
    assert world.client.get("/api/v1/procure/wishes").json()["wishes"] == []


def test_a_wish_is_done_when_the_book_comes_in_by_a_reading_of_the_library(operator: Any, net: FakeNetwork) -> None:
    from .library_helpers import recording as library_recording

    folder_of: dict[str, Path] = {}

    def fill(folder: Path) -> None:
        folder_of["books"] = folder
        german_epub(folder / "falkner")

    world = world_with(net, fill)
    world.indexer.items = []
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                              "kinds": ["audio"]})
    assert reply.status_code == 201, reply.text
    run_jobs()
    assert wish_rows()[0].state == "not_found"
    # An AAC recording at about 140 kbit/s: a format the ranking takes, below the goal (FLAC from the start).
    path = library_recording(folder_of["books"], "falkner/hoerbuch/teil.m4b", 60.0, title=TITLE, album=TITLE,
                             artist=AUTHOR, language="de")
    padded(path, 1_050_000)
    scan(world.library_id)
    assert work_of("audio") == world.work_id
    [row] = wish_rows()
    assert row.state == "upgrade", (row.state, row.detail)
    assert row.detail is not None and row.detail["reason"] == "met_elsewhere"
    assert (row.detail["have"], row.detail["goal"]) == ("m4b_aac", "flac")


def test_a_recording_in_a_format_the_ranking_leaves_out_meets_no_wish(operator: Any, net: FakeNetwork) -> None:
    """A recording at a rate the operator does not take (64 kbit/s and below are off from the start): the wish looks
    on for one it takes."""
    from .library_helpers import recording as library_recording

    folder_of: dict[str, Path] = {}

    def fill(folder: Path) -> None:
        folder_of["books"] = folder
        german_epub(folder / "falkner")

    world = world_with(net, fill)
    world.indexer.items = []
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                              "kinds": ["audio"]})
    assert reply.status_code == 201, reply.text
    run_jobs()
    path = library_recording(folder_of["books"], "falkner/hoerbuch/teil.m4b", 60.0, title=TITLE, album=TITLE,
                             artist=AUTHOR, language="de")
    padded(path, 300_000)
    scan(world.library_id)
    assert work_of("audio") == world.work_id
    assert wish_rows()[0].state == "not_found"


def padded(path: Path, size: int) -> None:
    """Gives an MP4 file a ``free`` box of ``size`` bytes, so that its size over its running time comes to a real bit
    rate (the made files are only headers); the file keeps its age for the scan."""
    import struct

    stamp = path.stat().st_mtime
    with path.open("ab") as handle:
        handle.write(struct.pack(">I", size + 8) + b"free" + bytes(size))
    os.utime(path, (stamp, stamp))


def test_a_wish_in_a_language_the_book_does_not_have_looks_on(operator: Any, net: FakeNetwork) -> None:
    from .library_helpers import recording as library_recording

    folder_of: dict[str, Path] = {}

    def fill(folder: Path) -> None:
        folder_of["books"] = folder
        german_epub(folder / "falkner")

    world = world_with(net, fill)
    world.indexer.items = []
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                              "kinds": ["audio"], "language": "en"})
    assert reply.status_code == 201, reply.text
    run_jobs()
    library_recording(folder_of["books"], "falkner/hoerbuch/teil.m4b", 600.0, title=TITLE, album=TITLE, artist=AUTHOR,
                      language="de")
    scan(world.library_id)
    assert work_of("audio") == world.work_id
    assert wish_rows()[0].state == "not_found"


@pytest.mark.parametrize(("title", "series", "position", "wished", "expected"), [
    ("Nebelchor 2", ["Nebelchor"], 2.0, TITLE, True),
    ("Nebelchor Band 02", ["Nebelchor"], 2.0, TITLE, True),
    ("Nebelchor - Teil 2", ["Nebelchor"], None, TITLE, True),
    ("NC 2", ["Nebel Chor"], 2.0, TITLE, True),
    ("Die Laternen 2", [], None, "Die Laternen und das Meer", True),
    ("Nebelchor 3", ["Nebelchor"], 2.0, TITLE, False),
    ("Die Laternenstraße", ["Nebelchor"], 2.0, TITLE, False),
    ("Nebelchor", ["Nebelchor"], 2.0, TITLE, False),
    ("Ruß 2", [], 2.0, "Ruß", False),
    ("Kapitel 2", ["Nebelchor"], 2.0, TITLE, False),
])
def test_a_series_and_its_number_is_no_title(title: str, series: list[str], position: float | None, wished: str,
                                             expected: bool) -> None:
    assert title_shape.series_shorthand(title, series=series, position=position, wished=wished) is expected


@pytest.mark.parametrize(("title", "expected"), [
    (f"{TITLE} by uploader", True),
    (f"[EXAMPLE] {TITLE}", True),
    (f"{TITLE} - {AUTHOR}", True),
    (f"{TITLE} www example com", True),
    (f"{TITLE}: Ein Krimi", False),
    (TITLE, False),
    ("Wir wollen Ruß sehen", False),
])
def test_the_title_with_an_uploaders_name_is_the_title(title: str, expected: bool) -> None:
    assert title_shape.with_uploader(title, TITLE, [AUTHOR]) is expected


def test_the_work_goes_by_the_real_title_of_its_editions() -> None:
    def edition(ident: int, kind: str, title: str) -> Edition:
        return Edition(id=ident, kind=kind, title=title, series_name=SERIES, series_pos=2.0, published="",
                       description="", genre="", genre_guess=False, original_title="", original_language="",
                       pages=0, duration=0, cover="", color="", created_at=__import__("datetime").datetime(2026, 1, 1))

    shown = catalog.summarize([edition(1, "ebook", f"{SERIES} 2"), edition(2, "audio", TITLE)])
    assert shown is not None and shown.title == TITLE
    shown = catalog.summarize([edition(1, "ebook", "Ein anderer Titel"), edition(2, "audio", TITLE)])
    assert shown is not None and shown.title == "Ein anderer Titel"
    shown = catalog.summarize([edition(1, "ebook", f"{SERIES} 2")])
    assert shown is not None and shown.title == f"{SERIES} 2"


def test_an_open_wish_of_the_work_is_left_alone_until_the_book_has_the_kind(operator: Any, net: FakeNetwork) -> None:
    world = world_with(net, lambda folder: german_epub(folder / "falkner"))
    world.indexer.items = []
    reply = world.client.post("/api/v1/procure/wishes", json={"items": [{"work_id": world.work_id}],
                                                              "kinds": ["audio"]})
    assert reply.status_code == 201
    run_jobs()
    from app.services.procure import wishes

    with SessionLocal() as db:
        assert wishes.met_elsewhere(db) == 0
        assert db.scalar(select(ProcureWish.state)) == "not_found"
