"""An e-book and its recording are one work, whatever the sources say about them (``catalog.leave_foreign_work``,
``catalog.join_twin_works``).

The put-together of generation 2 tore apart twins that were right: two editions with the same core title, found by
different sources under different ids, were taken for two books, and the one that left found no work to go to but a
new one with the same name. Only a proof that it is another book makes an edition leave (another title, another volume
of the same series); a leaving edition goes first to the work that holds its book, whatever that work is called now; and
a state that the earlier generation left (twins in two works, empty works) is made good by the next run, without
touching what a person decided."""

from __future__ import annotations

from sqlalchemy import delete, func, select

from app import clock
from app.db import SessionLocal
from app.models import Bookmark, Edition, ExternalRef, HistoryEntry, Progress, Work
from app.services import catalog, scanner, settings_service
from app.services.match import overlay
from app.services.readers.names import title_keys

from .conftest import make_account
from .library_helpers import book, books_folder, make_library, recording, run_jobs


def reassemble() -> None:
    """The next generation of the rules of putting books together (what the clock does once for every database)."""
    with SessionLocal() as db:
        settings_service.save(db, {"assemble_rev": scanner.ASSEMBLE_REV - 1})
        db.commit()
        assert scanner.reassemble_if_due(db) is True
    run_jobs()


def tell(edition_id: int, origin: str = "audible", **values: object) -> None:
    """A source says something about an edition (title, series, place in the series)."""
    with SessionLocal() as db:
        edition = db.get(Edition, edition_id)
        assert edition is not None
        edition.overlay = overlay.with_source(edition.overlay, dict(values), origins={name: origin for name in values})
        db.commit()


class Pair:
    """The e-book and the recording of one book, in one work (as the earlier rules put them there when their titles
    differed in a subtitle only)."""

    def __init__(self, title: str = "Salt Marsh", audio_title: str | None = None, name: str = "twins",
                 together: bool = True) -> None:
        folder = books_folder(name)
        audio_title = audio_title or title
        book(folder, "Ada Feldkamp/Ebook/b.epub", title=title, authors=("Ada Feldkamp",), language="en",
             text_bytes=3000)
        recording(folder, "Ada Feldkamp/Audio/b.m4b", 600.0, title=audio_title, album=audio_title,
                  artist="Ada Feldkamp", narrator="Rune Falkner")
        self.library_id = make_library(folder, "Twins", scan=True)
        with SessionLocal() as db:
            editions = {e.kind: e for e in db.scalars(select(Edition))}
            self.ebook, self.audio = editions["ebook"].id, editions["audio"].id
            if together and editions["ebook"].work_id != editions["audio"].work_id:
                editions["audio"].work_id = editions["ebook"].work_id
                db.flush()
                db.execute(delete(Work).where(Work.id.not_in(select(Edition.work_id))))
                db.commit()
            self.work = editions["ebook"].work_id

    def work_of(self, edition_id: int) -> int:
        return self.work_of_edition(edition_id)

    @staticmethod
    def work_of_edition(edition_id: int) -> int:
        with SessionLocal() as db:
            return db.get(Edition, edition_id).work_id  # type: ignore[union-attr]


def leaves(edition_id: int) -> tuple[bool, set[int]]:
    """Whether the edition leaves its work as it stands now (without the rest of the put-together), and the works it
    touches."""
    with SessionLocal() as db:
        edition = db.get(Edition, edition_id)
        assert edition is not None
        touched: set[int] = set()
        moved = catalog.leave_foreign_work(db, edition, touched)
        db.commit()
        return moved, touched


def works() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count(Work.id))) or 0)


def test_twins_whose_sources_give_other_subtitles_stay_one_work() -> None:
    pair = Pair("Salt Marsh: Die Rückkehr", "Salt Marsh - Eine Geschichte")
    assert leaves(pair.ebook)[0] is False and leaves(pair.audio)[0] is False
    reassemble()
    assert pair.work_of(pair.ebook) == pair.work_of(pair.audio) == pair.work
    assert works() == 1


def test_twins_known_by_two_sources_under_other_ids_and_other_series_stay_one_work() -> None:
    pair = Pair()
    with SessionLocal() as db:
        db.add(ExternalRef(edition_id=pair.ebook, source="dnb", kind="work", key="d-1"))
        db.add(ExternalRef(edition_id=pair.audio, source="audible", kind="work", key="B0-1"))
        db.commit()
    tell(pair.ebook, "dnb", series="Marsh Books", series_pos=3.0)
    tell(pair.audio, "audible", series="Fenland Tales", series_pos=1.0)
    reassemble()
    assert pair.work_of(pair.ebook) == pair.work_of(pair.audio) == pair.work
    assert works() == 1
    assert leaves(pair.ebook)[0] is False and leaves(pair.audio)[0] is False


def test_the_other_volume_of_the_same_series_is_another_book_and_leaves() -> None:
    pair = Pair()
    tell(pair.ebook, "dnb", series="Marsh Books", series_pos=1.0)
    tell(pair.audio, "audible", series="Marsh Books", series_pos=2.0)
    reassemble()
    assert pair.work_of(pair.ebook) != pair.work_of(pair.audio)
    assert works() == 2


def test_two_volumes_named_in_the_title_are_two_books_when_the_sources_name_no_series() -> None:
    pair = Pair("Salt Marsh: Teil 1", "Salt Marsh - Teil 2")
    assert leaves(pair.audio)[0] is True
    reassemble()
    assert pair.work_of(pair.ebook) != pair.work_of(pair.audio)
    assert works() == 2


def test_the_core_of_a_title_is_the_title_up_to_its_subtitle_and_not_another_volume() -> None:
    assert catalog.same_core("Salt Marsh: Die Rückkehr", "Salt Marsh - Eine Geschichte")
    assert catalog.same_core("Salt Marsh: Roman", "Salt Marsh - Thriller")
    assert catalog.same_core("Salt Marsh: Teil 1", "Salt Marsh: Teil 1")
    assert not catalog.same_core("Salt Marsh: Teil 1", "Salt Marsh: Teil 2")
    assert not catalog.same_core("Salt Marsh", "Harbour Lights")
    assert not catalog.same_core("", "Harbour Lights")
    # With ``strict`` a subtitle may only be there on one side.
    assert catalog.same_core("Salt Marsh", "Salt Marsh: Die Rückkehr", strict=True)
    assert not catalog.same_core("Salt Marsh: Die Rückkehr", "Salt Marsh: Eine Geschichte", strict=True)


def test_two_places_in_two_series_say_nothing_about_two_volumes() -> None:
    assert catalog.volumes_differ(1, 1.0, 1, 2.0) is True
    assert catalog.volumes_differ(None, 1.0, 1, 2.0) is True
    assert catalog.volumes_differ(1, 1.0, 2, 2.0) is False
    assert catalog.volumes_differ(1, 1.0, 1, 1.0) is False
    assert catalog.volumes_differ(1, None, 1, 2.0) is False


def test_the_e_book_and_the_recording_of_a_book_in_two_series_are_one_work_from_the_first_scan() -> None:
    folder = books_folder("two-series")
    book(folder, "Ada Feldkamp/Ebook/b.epub", title="Salt Marsh", authors=("Ada Feldkamp",), language="en",
         series="Marsh Books", series_pos=3.0, text_bytes=3000)
    recording(folder, "Ada Feldkamp/Audio/b.m4b", 600.0, title="Salt Marsh", album="Salt Marsh",
              artist="Ada Feldkamp", narrator="Rune Falkner", series="Fenland Tales", series_pos=1)
    make_library(folder, "Two series", scan=True)
    assert works() == 1


def test_the_e_book_and_the_recording_with_other_subtitles_are_one_work_when_the_library_is_settled() -> None:
    pair = Pair("Salt Marsh: Die Rückkehr", "Salt Marsh - Eine Geschichte", together=False)
    assert works() == 1
    assert pair.work_of(pair.ebook) == pair.work_of(pair.audio)
    with SessionLocal() as db:
        work = db.get(Work, pair.work_of(pair.ebook))
        assert work is not None and work.title == "Salt Marsh: Die Rückkehr"
        # The e-book speaks first (it came into the work after the recording): the work shows its pages.
        assert work.pages == db.get(Edition, pair.ebook).pages != 0  # type: ignore[union-attr]


def test_two_e_books_whose_subtitles_differ_are_two_books() -> None:
    folder = books_folder("two-ebooks")
    book(folder, "Ada Feldkamp/One/b.epub", title="Salt Marsh: Die Rückkehr", authors=("Ada Feldkamp",),
         language="en", text_bytes=3000)
    book(folder, "Ada Feldkamp/Two/b.epub", title="Salt Marsh - Eine Geschichte", authors=("Ada Feldkamp",),
         language="en", text_bytes=3000)
    make_library(folder, "Two", scan=True)
    assert works() == 2


def test_a_book_in_another_series_is_still_found_in_its_work_but_not_the_other_volume() -> None:
    folder = books_folder("find")
    recording(folder, "Ada Feldkamp/Audio/b.m4b", 600.0, title="Salt Marsh", album="Salt Marsh",
              artist="Ada Feldkamp", narrator="Rune Falkner", series="Fenland Tales", series_pos=1)
    make_library(folder, "Find", scan=True)
    from types import SimpleNamespace

    with SessionLocal() as db:
        wanted = db.scalar(select(Work.id))

        def look(series: str, position: float) -> int | None:
            meta = SimpleNamespace(isbn="", title="Salt Marsh", series=series, series_pos=position,
                                   authors=["Ada Feldkamp"])
            found = catalog.find_work(db, meta)  # type: ignore[arg-type]
            return found.id if found else None

        assert look("Marsh Books", 3.0) == wanted
        assert look("Fenland Tales", 1.0) == wanted
        assert look("Fenland Tales", 2.0) is None


def test_an_edition_with_another_title_leaves_and_the_other_twin_stays() -> None:
    pair = Pair()
    with SessionLocal() as db:
        edition = db.get(Edition, pair.ebook)
        assert edition is not None
        edition.overlay = overlay.with_hand(edition.overlay, {"title": "Harbour Lights"})
        db.commit()
    reassemble()
    assert pair.work_of(pair.ebook) != pair.work_of(pair.audio)
    assert works() == 2


def _salt_and_harbour() -> tuple[int, int, int, int, int]:
    """Harbour Lights (e-book, recording) and Salt Marsh (e-book, recording), four editions in two works; the e-book of
    Salt Marsh is moved under the work of Harbour Lights, as a wrong match once did. Returns the ids: the editions of
    Harbour Lights, those of Salt Marsh, and the work of Salt Marsh."""
    folder = books_folder("salt-harbour")
    book(folder, "Ada Feldkamp/Harbour Lights/h.epub", title="Harbour Lights", authors=("Ada Feldkamp",),
         language="en", text_bytes=3000)
    book(folder, "Ada Feldkamp/Salt Marsh/s.epub", title="Salt Marsh", authors=("Ada Feldkamp",), language="en",
         text_bytes=3000)
    recording(folder, "Ada Feldkamp/Salt Marsh Audio/s.m4b", 600.0, title="Salt Marsh", album="Salt Marsh",
              artist="Ada Feldkamp", narrator="Rune Falkner")
    recording(folder, "Ada Feldkamp/Harbour Lights Audio/h.m4b", 600.0, title="Harbour Lights",
              album="Harbour Lights", artist="Ada Feldkamp", narrator="Rune Falkner")
    make_library(folder, "Salt", scan=True)
    with SessionLocal() as db:
        found = {(e.title, e.kind): e for e in db.scalars(select(Edition))}
        h_book, h_audio = found[("Harbour Lights", "ebook")], found[("Harbour Lights", "audio")]
        s_book, s_audio = found[("Salt Marsh", "ebook")], found[("Salt Marsh", "audio")]
        assert s_book.work_id == s_audio.work_id and h_book.work_id == h_audio.work_id
        right = s_audio.work_id
        s_book.work_id = h_book.work_id
        s_book.grouped_by = "auto"
        db.commit()
        return h_book.id, h_audio.id, s_book.id, s_audio.id, right


def test_an_edition_that_leaves_goes_to_its_book_even_when_that_work_is_called_something_else() -> None:
    h_book, h_audio, s_book, s_audio, right = _salt_and_harbour()
    with SessionLocal() as db:
        work = db.get(Work, right)
        assert work is not None
        # The work of the recording has not caught up with what its editions say (it is named after an edition that is
        # about to leave in this very run).
        work.title, work.key, work.main_key = "Old Name", "old name", "old name"
        db.commit()
    moved, touched = leaves(s_book)
    assert moved is True
    assert Pair.work_of_edition(s_book) == right and works() == 2
    assert right in touched
    reassemble()
    assert Pair.work_of_edition(s_book) == Pair.work_of_edition(s_audio)
    assert Pair.work_of_edition(h_book) == Pair.work_of_edition(h_audio)
    assert works() == 2


def test_an_edition_that_leaves_goes_to_its_book_across_the_places_of_two_series() -> None:
    _h_book, _h_audio, s_book, s_audio, right = _salt_and_harbour()
    with SessionLocal() as db:
        for ident, name, position in ((s_book, "Marsh Books", 5.0), (s_audio, "Fenland Tales", 2.0)):
            edition = db.get(Edition, ident)
            assert edition is not None
            edition.series_name = name
            edition.series_id = catalog.get_series(db, name).id  # type: ignore[union-attr]
            edition.series_pos = position
        db.flush()
        catalog.refresh_work(db, db.get(Work, right))  # type: ignore[arg-type]
        db.commit()
    moved, _touched = leaves(s_book)
    assert moved is True and Pair.work_of_edition(s_book) == right
    assert works() == 2


def test_of_two_editions_that_are_two_books_the_one_the_work_is_named_after_stays() -> None:
    """One work holds the e-book of Harbour Lights (the oldest edition) and the recording of Salt Marsh, called after the
    recording: the oldest edition does not throw it out."""
    folder = books_folder("tie")
    book(folder, "Ada Feldkamp/Harbour Lights/h.epub", title="Harbour Lights", authors=("Ada Feldkamp",),
         language="en", text_bytes=3000)
    recording(folder, "Ada Feldkamp/Salt Marsh Audio/s.m4b", 600.0, title="Salt Marsh", album="Salt Marsh",
              artist="Ada Feldkamp", narrator="Rune Falkner")
    make_library(folder, "Tie", scan=True)
    with SessionLocal() as db:
        found = {e.kind: e for e in db.scalars(select(Edition))}
        found["audio"].work_id = found["ebook"].work_id
        found["audio"].grouped_by = "auto"
        work = db.get(Work, found["ebook"].work_id)
        assert work is not None
        work.title, work.key, work.main_key = "Salt Marsh", "salt marsh", "salt marsh"
        ebook, audio = found["ebook"].id, found["audio"].id
        db.commit()
    assert leaves(ebook)[0] is True
    assert leaves(audio)[0] is False
    reassemble()
    with SessionLocal() as db:
        assert db.get(Edition, ebook).work_id != db.get(Edition, audio).work_id  # type: ignore[union-attr]
        assert db.get(Work, db.get(Edition, audio).work_id).title == "Salt Marsh"  # type: ignore[union-attr]


def _scatter(pair: Pair) -> tuple[int, int]:
    """What the generation 2 left: the e-book alone in a work of its own with the same name, an empty work beside it,
    and a place, a mark and a finished pass hanging on the edition that moved."""
    reader = make_account("wanda")
    with SessionLocal() as db:
        now = clock.now()
        own = Work(title="Salt Marsh", key="salt marsh", main_key="salt marsh", added_at=now, updated_at=now)
        empty = Work(title="Salt Marsh", key="salt marsh", main_key="salt marsh", added_at=now, updated_at=now)
        db.add_all([own, empty])
        db.flush()
        edition = db.get(Edition, pair.ebook)
        assert edition is not None
        edition.work_id = own.id
        db.add(Progress(user_id=reader.id, edition_id=pair.ebook, locator="epubcfi(/6/4)", fraction=0.4,
                        moved_at=now, received_at=now))
        db.add(Bookmark(user_id=reader.id, edition_id=pair.ebook, locator="epubcfi(/6/8)", fraction=0.5))
        db.add(HistoryEntry(user_id=reader.id, edition_id=pair.ebook, kind="read", finished_on="2026-09-01",
                            source="hand", created_at=now))
        db.commit()
        return own.id, empty.id


def test_twins_that_the_earlier_generation_tore_apart_are_one_work_again() -> None:
    pair = Pair()
    _own, empty = _scatter(pair)
    assert works() == 3
    reassemble()
    assert pair.work_of(pair.ebook) == pair.work_of(pair.audio)
    assert works() == 1
    with SessionLocal() as db:
        assert db.get(Work, empty) is None
        progress = db.scalars(select(Progress)).one()
        assert progress.edition_id == pair.ebook and progress.fraction == 0.4
        assert db.scalar(select(func.count(Bookmark.id))) == 1
        entry = db.scalars(select(HistoryEntry)).one()
        assert entry.work_id == pair.work_of(pair.audio)
        work = db.get(Work, pair.work_of(pair.audio))
        assert work is not None and work.title == "Salt Marsh"
        # The work shows what its editions say now: the e-book speaks first and gives the pages.
        assert work.pages == db.get(Edition, pair.ebook).pages != 0  # type: ignore[union-attr]


def test_twins_a_person_split_stay_apart() -> None:
    pair = Pair()
    own, _empty = _scatter(pair)
    with SessionLocal() as db:
        db.get(Edition, pair.ebook).grouped_by = "hand"  # type: ignore[union-attr]
        db.commit()
    reassemble()
    assert pair.work_of(pair.ebook) == own != pair.work_of(pair.audio)


def test_the_same_title_by_somebody_else_is_another_book() -> None:
    folder = books_folder("namesakes")
    book(folder, "Ada Feldkamp/Salt Marsh/s.epub", title="Salt Marsh", authors=("Ada Feldkamp",), language="en",
         text_bytes=3000)
    book(folder, "Bruno Hallgren/Salt Marsh/s.epub", title="Salt Marsh", authors=("Bruno Hallgren",), language="en",
         text_bytes=3000)
    make_library(folder, "Namesakes", scan=True)
    assert works() == 2
    reassemble()
    assert works() == 2


def test_the_generation_of_putting_books_together_is_newer_than_the_one_that_tore_twins_apart() -> None:
    assert scanner.ASSEMBLE_REV >= 3


def test_a_library_with_many_twins_keeps_every_work_and_every_edition_where_it_was() -> None:
    """Nothing stood wrong, so nothing moves: the same number of works, and every edition in the work it was in."""
    folder = books_folder("many-twins")
    kinds = ["Roman", "Thriller", "Krimi"]
    for number in range(24):
        name = f"Fenland Tale {number:02d}" if number % 3 else f"Fenland Tale {number:02d}: {kinds[number % 3]}"
        author = f"Ada Feldkamp{number % 5}"
        where = f"{author}/Tale {number:02d}"
        book(folder, f"{where}/b.epub", title=name, authors=(author,), language="en", text_bytes=1500)
        if number % 2 == 0:
            audio_name = name.replace(":", " -") if number % 4 == 0 else name
            recording(folder, f"{where}/Audio/b.m4b", 600.0, title=audio_name, album=audio_name,
                      artist=author, narrator="Rune Falkner")
    make_library(folder, "Many", scan=True)
    with SessionLocal() as db:
        before = {e.id: e.work_id for e in db.scalars(select(Edition))}
        count = db.scalar(select(func.count(Work.id)))
    reassemble()
    with SessionLocal() as db:
        after = {e.id: e.work_id for e in db.scalars(select(Edition))}
        assert db.scalar(select(func.count(Work.id))) == count
    assert after == before


def _made_up_works(count: int) -> list[dict[str, object]]:
    """A list of works for ``tools/make_library.py``: e-book and recording together, an e-book alone, a recording alone,
    titles with subtitles, with the dash of a title page, with umlauts, books in series."""
    authors = ["Ada Feldkamp", "Bruno Hallgren", "Cora Lindqvist", "Dag Ostermann", "Elin Varga", "Finn Aldrich"]
    works: list[dict[str, object]] = []
    for number in range(count):
        shape = number % 10
        base = f"Fenland Tale {number:03d}"
        title = {0: base, 1: f"{base}: Die Rückkehr", 2: f"{base} \u2013 Eine Geschichte", 3: base + " Zwölf Nächte",
                 4: base, 5: f"{base} (Band 2)", 6: base, 7: base, 8: base, 9: base}[shape]
        editions: list[dict[str, object]] = []
        if shape not in (7, 8):
            editions.append({"kind": "ebook", "lang": "Englisch", "format": "EPUB", "pages": 20 + number % 7})
        if shape in (0, 1, 2, 3, 5, 7, 9):
            editions.append({"kind": "audio", "variant": "ungekürzt", "lang": "Englisch", "minutes": 300 + number,
                             "narrator": "Rune Falkner"})
        if shape == 8:
            editions.append({"kind": "audio", "variant": "Hörspiel", "lang": "Englisch", "minutes": 90 + number,
                             "narrator": "Rune Falkner"})
        works.append({"title": title, "author": authors[number % len(authors)], "genre": "krimi", "color": "#5e2027",
                      "pages": 120, "year": 2000 + number % 20, "series": "Fenland Cycle" if shape == 5 else None,
                      "seriesNo": 2 if shape == 5 else None, "blurb": "", "fassungen": editions})
    # Books of a series named in the title (``Series - Title``), without a place in the series: no file says which
    # volume it is, the title alone tells them apart. Some have a recording, one series has a recording called after
    # the series only.
    for index in range(max(2, count // 20)):
        series = f"Seeker Cycle {index}"
        for volume, name in enumerate(("The Track", "The Voice", "The Last Page")):
            editions = [{"kind": "ebook", "lang": "Englisch", "format": "EPUB", "pages": 20}]
            if volume != 1:
                editions.append({"kind": "audio", "variant": "ungekürzt", "lang": "Englisch", "minutes": 200 + volume,
                                 "narrator": "Rune Falkner"})
            works.append({"title": f"{series} - {name}", "author": authors[index % len(authors)], "genre": "krimi",
                          "color": "#2e4a6b", "pages": 120, "year": 2010 + volume, "series": None, "seriesNo": None,
                          "blurb": "", "fassungen": editions})
        works.append({"title": series, "author": authors[index % len(authors)], "genre": "krimi", "color": "#2e4a6b",
                      "pages": 120, "year": 2014, "series": None, "seriesNo": None, "blurb": "",
                      "fassungen": [{"kind": "audio", "variant": "ungekürzt", "lang": "Englisch", "minutes": 700,
                                     "narrator": "Rune Falkner"}]})
    return works


def test_a_made_up_library_with_twins_keeps_every_work_and_every_edition_where_it_was_when_put_together_again() -> None:
    """The check of the whole: a library made with ``tools/make_library.py``, read, then put together again by the newer
    rules: the same works, and no edition changes its work (nothing stood wrong). ``NEXBOOKS_TWIN_WORKS`` sets how many
    works are made (a few hundred for a look at a larger library)."""
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import make_library as maker

    count = int(os.environ.get("NEXBOOKS_TWIN_WORKS", "60"))
    folder = books_folder("made-up-twins")
    ebooks, recordings = maker.make_library(_made_up_works(count), folder)  # type: ignore[arg-type]
    assert ebooks and recordings
    make_library(folder, "Made up", scan=True)
    with SessionLocal() as db:
        before = {e.id: e.work_id for e in db.scalars(select(Edition))}
        works_before = db.scalar(select(func.count(Work.id)))
    assert works_before is not None and count <= works_before < len(before)
    reassemble()
    with SessionLocal() as db:
        after = {e.id: e.work_id for e in db.scalars(select(Edition))}
        works_after = db.scalar(select(func.count(Work.id)))
    assert works_after == works_before
    assert after == before
    # No work holds books with different titles (the books of a series told apart by what follows the name of the series
    # are many works, and so is the book called after the series alone).
    with SessionLocal() as db:
        for work_id in set(after.values()):
            keys = {title_keys(title)[0] for title in db.scalars(select(Edition.title).where(Edition.work_id == work_id))}
            assert len(keys) == 1, keys


def _seeker_books(*, with_bare_recording: bool = True) -> dict[str, int]:
    """Three books of the series ``Seeker``, each told apart by its title only (no file names a place in the series), an
    e-book each, and a recording of the third called by the name of the series alone. Returns the editions by name."""
    folder = books_folder("seeker")
    for name in ("The Track", "The Voice", "The Last Page"):
        book(folder, f"Ada Feldkamp/{name}/b.epub", title=f"Seeker - {name}", authors=("Ada Feldkamp",), language="en",
             text_bytes=3000)
    if with_bare_recording:
        recording(folder, "Ada Feldkamp/Audio/b.m4b", 600.0, title="Seeker", album="Seeker", artist="Ada Feldkamp",
                  narrator="Rune Falkner")
    make_library(folder, "Seeker", scan=True)
    with SessionLocal() as db:
        return {e.title: e.id for e in db.scalars(select(Edition))}


def test_books_of_a_series_told_apart_by_their_titles_are_many_works() -> None:
    found = _seeker_books()
    assert works() == 4
    before = {name: Pair.work_of_edition(ident) for name, ident in found.items()}
    assert len(set(before.values())) == 4
    reassemble()
    assert {name: Pair.work_of_edition(ident) for name, ident in found.items()} == before


def test_books_of_a_series_that_stand_in_one_work_are_told_apart_again() -> None:
    """The state a put-together that took the name of the series for the title left: the books of a series in the work
    of one of them. Each goes back to a work of its own, and the recording called after the series stays out of them."""
    found = _seeker_books()
    one, two, three = (found[f"Seeker - {name}"] for name in ("The Track", "The Voice", "The Last Page"))
    audio = found["Seeker"]
    with SessionLocal() as db:
        target = db.get(Edition, three).work_id  # type: ignore[union-attr]
        for ident in (one, two, audio):
            edition = db.get(Edition, ident)
            assert edition is not None
            edition.work_id = target
            edition.grouped_by = ""
        db.commit()
    reassemble()
    homes = {Pair.work_of_edition(ident) for ident in (one, two, three, audio)}
    assert len(homes) == 4
    assert works() == 4


def test_the_title_after_the_name_of_a_series_is_the_title_of_the_book() -> None:
    folder = books_folder("seeker-title")
    for name in ("The Track", "The Voice"):
        book(folder, f"Ada Feldkamp/{name}/b.epub", title=f"Seeker - {name}", authors=("Ada Feldkamp",),
             language="en", text_bytes=3000)
    make_library(folder, "Seeker", scan=True)
    with SessionLocal() as db:
        editions = list(db.scalars(select(Edition).order_by(Edition.id)))
        one, two = editions
        assert catalog.series_like(db, "seeker", [], [("ebook", "seeker the track"), ("ebook", "seeker the voice")])
        assert catalog.same_book_title(db, one, two) is False
        assert catalog.series_like(db, "seeker", ["Seeker"], []) is True
        assert catalog.series_like(db, "salt marsh", [], [("ebook", "salt marsh die rueckkehr")]) is False
        assert catalog.series_like(db, "", [], []) is False
