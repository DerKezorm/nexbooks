"""The recognition of recordings in the folders real downloads come in: the parts in ``Audio``, ``CD 1``, ``Disc 2``
folders are one book, the number of a part is never a title, an album tag with a carrier or a form behind it is cleaned or
set aside for the folder of the book, a collection folder of a series is several books, the author is the top folder when
the tags do not say, the running time is the sum over every disc folder, and what was read before is put together anew
without anybody doing anything (the progress and the marks go with it). All names and tags are invented."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app import clock
from app.db import SessionLocal
from app.models import (
    BookFile,
    Bookmark,
    Edition,
    Library,
    MatchEntry,
    Progress,
    Series,
    Work,
)
from app.services import scanner
from app.services.match import run as matching
from app.services.readers import names

from .bookmaker import cover_image
from .conftest import make_account
from .library_helpers import age_all, book, books_folder, make_library, recording, scan
from .test_scanner import state, titles

# ---- The names: what a folder, a tag and a file name are ------------------------------------------------------------


@pytest.mark.parametrize("folder", ["Audio", "audio", "Hörbuch", "Hoerbuch", "MP3", "mp3", "CD 1", "CD1", "cd01", "CD 02",
                                    "Disc 2", "Disk 2", "disc2", "Teil 1", "Part 1", "Teil 1 von 6", "MP3 CD 2", "03"])
def test_folders_that_only_hold_the_parts_of_a_book_are_none(folder: str) -> None:
    assert names.is_cd_folder(folder)
    assert names.strip_part_folders(f"Autor/Titel (2019)/{folder}") == "Autor/Titel (2019)"


@pytest.mark.parametrize("folder", ["Titel (2019)", "Band 3", "eBook", "eBooks", "Cover", "Aschenfeld", "12 Monkeys",
                                    "Audioquelle"])
def test_folders_that_name_a_book_or_something_else_are_kept(folder: str) -> None:
    assert not names.is_cd_folder(folder)
    assert names.strip_part_folders(f"Autor/{folder}") == f"Autor/{folder}"


def test_the_first_folder_of_the_library_is_never_taken_off_and_several_in_a_row_all_are() -> None:
    assert names.strip_part_folders("Audio") == "Audio"
    assert names.strip_part_folders("Autor/Titel/Audio/CD 2") == "Autor/Titel"
    assert names.disc_in_path("Autor/Titel/CD 2/a.mp3") == 2
    assert names.disc_in_path("Autor/Titel/Audio/a.mp3") is None
    assert names.disc_in_path("Autor/Titel/Audio/Disc 3/a.mp3") == 3


@pytest.mark.parametrize("stem,title,part", [
    ("Nachtglas-1v6", "Nachtglas", 1), ("Nachtglas - 2v6", "Nachtglas", 2), ("Nachtglas Teil 3 von 6", "Nachtglas", 3),
    ("Nachtglas (4 of 6)", "Nachtglas", 4), ("001-Nachtglas", "Nachtglas", 1), ("01 Nachtglas", "Nachtglas", 1),
    ("CD1 - Nachtglas", "Nachtglas", 1), ("Track 01", "", 1), ("CD1", "", 1), ("Teil 2 von 6", "", 2), ("003", "", 3),
    ("Kapitel 12", "", 12), ("1984 Nachtglas", "1984 Nachtglas", None), ("12 Monkeys", "12 Monkeys", None),
    ("Nachtglas", "Nachtglas", None)])
def test_the_number_of_a_part_in_a_file_name_is_a_part_and_never_a_title(stem: str, title: str,
                                                                          part: int | None) -> None:
    assert names.strip_part_marks(stem) == title
    assert names.part_number_of(stem) == part


@pytest.mark.parametrize("tag,clean", [
    ("Nachtglas MP3CD", "Nachtglas"), ("Nachtglas MP3-CD", "Nachtglas"), ("Nachtglas (ungekürzt)", "Nachtglas"),
    ("Nachtglas - Hörbuch", "Nachtglas"), ("Nachtglas Audiobook", "Nachtglas"), ("Nachtglas (Unabridged)", "Nachtglas"),
    ("Nachtglas Unabridged", "Nachtglas"), ("Nachtglas CD 2", "Nachtglas"), ("Nachtglas - Teil 1 von 6", "Nachtglas"),
    ("Nachtglas 6 CDs", "Nachtglas"), ("Hörbuch: Nachtglas", "Nachtglas"), ("Nachtglas [MP3 CD]", "Nachtglas"),
    ("Nachtglas Teil 2", "Nachtglas Teil 2"), ("Nachtglas (Teil 2 von 3)", "Nachtglas"),
    ("Nachtglas (gekürzt)", "Nachtglas (gekürzt)"), ("Das Hörbuch", "Das Hörbuch"), ("Hörbuch", ""), ("Audiobook", ""),
    ("Unabridged", ""), ("Nachtglas (gekürzt, MP3-CD)", "Nachtglas (gekürzt)")])
def test_an_album_tag_loses_the_carrier_and_the_form(tag: str, clean: str) -> None:
    assert names.clean_album(tag) == clean


def test_an_album_that_is_a_text_of_a_series_or_a_shop_is_known() -> None:
    assert names.album_is_blurb("Ein Anselm-Quist-Thriller, Band 6")
    assert names.album_is_blurb("A Thriller about the Quist Saga")
    assert not names.album_is_blurb("Nachtglas")
    assert not names.album_is_blurb("Ein Mann namens Falk")
    assert names.album_is_generic("") and names.album_is_generic("Track 3") and names.album_is_generic("Unknown")
    assert not names.album_is_generic("Nachtglas")


def test_the_folder_of_a_book_is_read_for_title_series_volume_and_year() -> None:
    plain = names.folder_book("Nachtglas (2019)", depth=2)
    assert (plain.title, plain.series, plain.pos, plain.year, plain.structured) == ("Nachtglas", "", None, 2019, True)
    dash = names.folder_book("Nachtglas - Anselm Quist 6 (2019)", depth=2)
    assert (dash.title, dash.series, dash.pos, dash.year) == ("Nachtglas", "Anselm Quist", 6.0, 2019)
    band = names.folder_book("Nachtglas - Anselm Quist, Band 6 (2019)", depth=2)
    assert (band.title, band.series, band.pos, band.year) == ("Nachtglas", "Anselm Quist", 6.0, 2019)
    numbered = names.folder_book("1.-.Steinfeuer", depth=3, parent="Quist-Saga (2018)")
    assert (numbered.title, numbered.pos, numbered.series) == ("Steinfeuer", 1.0, "Quist-Saga")
    owner = names.folder_book("2.-.Die große Flut", depth=2, parent="Rune Falkner")
    assert (owner.title, owner.pos, owner.series) == ("Die große Flut", 2.0, "")
    flat = names.folder_book("Ada Feldkamp - Saga 03 - The Third Book", depth=1)
    assert (flat.author, flat.series, flat.pos, flat.title, flat.structured) == (
        "Ada Feldkamp", "Saga", 3.0, "The Third Book", False)
    assert names.folder_book("12 Monkeys", depth=2).title == "12 Monkeys"


def test_the_top_folder_is_the_author_only_when_it_looks_like_one_and_the_book_is_two_folders_deep() -> None:
    assert names.top_folder_author("Rune Falkner/Nachtglas (2019)/Audio/a.mp3") == "Rune Falkner"
    assert names.top_folder_author("Falkner, Rune/Nachtglas/CD 1/a.mp3") == "Rune Falkner"
    assert names.top_folder_author("Hörbücher/Nachtglas/a.mp3") == ""
    assert names.top_folder_author("Rune Falkner/a.mp3") == ""
    assert names.top_folder_author("Rune Falkner/Audio/a.mp3") == ""


# ---- The scan: the folders real downloads come in ----------------------------------------------------------------------


def parts(folder: Any, relative: str, count: int, *, seconds: float = 4.0, **tags: Any) -> None:
    for number in range(1, count + 1):
        recording(folder, relative.format(number=number), seconds=seconds, track=number, **tags)


def only_work() -> dict[str, Any]:
    (work,) = state()["works"]
    return work


def test_the_parts_in_an_audio_folder_are_the_book_of_the_folder_above() -> None:
    folder = books_folder("audio-folder")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 3, album="Nachtglas MP3CD",
          artist="Rune Falkner")
    scan(make_library(folder))
    work = only_work()
    assert work["title"] == "Nachtglas" and work["authors"] == ["Rune Falkner"]
    (edition,) = work["editions"]
    assert len(edition["files"]) == 3 and edition["kind"] == "audio"
    with SessionLocal() as db:
        assert db.scalar(select(Work.year)) == 2019


@pytest.mark.parametrize("sub", ["Audio", "Hörbuch", "MP3", "CD1", "cd01", "Disc 2", "Disk 2", "Teil 1", "Part 1"])
def test_every_general_folder_of_parts_leads_to_the_title_of_the_book_folder(sub: str) -> None:
    folder = books_folder("general-folder")
    parts(folder, f"Rune Falkner/Nachtglas (2019)/{sub}/{{number:02d}}.mp3", 2, artist="Rune Falkner")
    scan(make_library(folder))
    assert only_work()["title"] == "Nachtglas"


def test_several_disc_folders_under_a_book_are_one_edition_on_one_timeline() -> None:
    folder = books_folder("disc-folders")
    # The track numbers start over on every disc, and no tag says which disc it is: the folder says.
    for disc in (1, 2, 10):
        parts(folder, f"Rune Falkner/Nachtglas (2019)/CD {disc}/{{number:02d}}.mp3", 2, seconds=5.0,
              album=f"Nachtglas CD{disc}", artist="Rune Falkner", title=f"Disc {disc} part ")
    scan(make_library(folder))
    work = only_work()
    (edition,) = work["editions"]
    assert len(edition["files"]) == 6 and work["title"] == "Nachtglas"
    assert 29 <= edition["duration"] <= 31
    # The two files of a disc carry the same title: one chapter a disc (``chapter_groups``), starting in its first file.
    assert [name for name, _start in edition["chapters"]] == [f"Disc {disc} part" for disc in (1, 2, 10)]
    starts = [start for _name, start in edition["chapters"]]
    assert starts[0] == 0 and 9500 <= starts[1] <= 10500 and 19500 <= starts[2] <= 20500
    with SessionLocal() as db:
        paths = db.scalars(select(BookFile.path).where(BookFile.role == "book").order_by(BookFile.part)).all()
        assert [path.split("/")[-2] for path in paths] == ["CD 1", "CD 1", "CD 2", "CD 2", "CD 10", "CD 10"]


def test_the_ebook_and_cover_folders_beside_the_audio_are_no_parts_of_the_recording() -> None:
    folder = books_folder("beside")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas", artist="Rune Falkner")
    book(folder, "Rune Falkner/Nachtglas (2019)/eBook/nachtglas.epub", title="Nachtglas", authors=("Rune Falkner",))
    (folder / "Rune Falkner/Nachtglas (2019)/Cover").mkdir()
    (folder / "Rune Falkner/Nachtglas (2019)/Cover/cover.jpg").write_bytes(cover_image((20, 120, 60), kind="JPEG"))
    age_all(folder)
    scan(make_library(folder))
    work = only_work()
    kinds = {edition["kind"]: edition for edition in work["editions"]}
    assert set(kinds) == {"audio", "ebook"}
    assert all(path.endswith(".mp3") for path in kinds["audio"]["files"]) and len(kinds["audio"]["files"]) == 2
    assert kinds["ebook"]["files"] == ["Rune Falkner/Nachtglas (2019)/eBook/nachtglas.epub"]
    with SessionLocal() as db:
        # The picture in the cover folder speaks for the book above it.
        assert db.scalar(select(Edition.cover).where(Edition.kind == "audio")) != ""


def test_a_file_beside_the_parts_in_their_folder_still_speaks_for_the_recording() -> None:
    folder = books_folder("cover-in-audio")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas", artist="Rune Falkner")
    (folder / "Rune Falkner/Nachtglas (2019)/Audio/cover.jpg").write_bytes(cover_image((20, 120, 60), kind="JPEG"))
    age_all(folder)
    scan(make_library(folder))
    with SessionLocal() as db:
        assert db.scalar(select(Edition.cover)) != ""


def test_a_file_in_the_book_folder_speaks_for_the_recording_in_its_audio_folder_and_not_for_two_books() -> None:
    import json

    folder = books_folder("json-above")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas", artist="Rune Falkner")
    (folder / "Rune Falkner/Nachtglas (2019)/metadata.json").write_text(json.dumps({
        "narrators": ["Ben Hollister"], "genres": ["Thriller"], "language": "de"}), encoding="utf-8")
    # Two recordings below one folder: a file in it speaks for neither.
    parts(folder, "Rune Falkner/Zwei/Steinfeuer/Audio/{number:02d}.mp3", 1, album="Steinfeuer", artist="Rune Falkner")
    parts(folder, "Rune Falkner/Zwei/Flutwelle/Audio/{number:02d}.mp3", 1, album="Flutwelle", artist="Rune Falkner")
    (folder / "Rune Falkner/Zwei/metadata.json").write_text(json.dumps({"narrators": ["Nobody Reads"]}), encoding="utf-8")
    age_all(folder)
    scan(make_library(folder))
    found = {work["title"]: work for work in state()["works"]}
    assert found["Nachtglas"]["editions"][0]["narrators"] == ["Ben Hollister"]
    assert found["Steinfeuer"]["editions"][0]["narrators"] == [] and found["Flutwelle"]["editions"][0]["narrators"] == []


def test_file_names_with_the_number_of_a_part_give_no_title_and_the_parts_stay_in_order() -> None:
    folder = books_folder("part-names")
    for number in (1, 2, 10):
        recording(folder, f"Rune Falkner/Nachtglas (2019)/Nachtglas-{number}v10.mp3", seconds=3.0,
                  artist="Rune Falkner")
    for number in (1, 2, 3):
        recording(folder, f"Rune Falkner/Steinfeuer (2020)/{number:03d}-Steinfeuer.mp3", seconds=3.0,
                  artist="Rune Falkner")
    recording(folder, "Rune Falkner/Flutwelle (2021)/Teil 1 von 2.mp3", seconds=3.0, artist="Rune Falkner")
    recording(folder, "Rune Falkner/Flutwelle (2021)/Teil 2 von 2.mp3", seconds=3.0, artist="Rune Falkner")
    recording(folder, "Rune Falkner/Sturmnacht (2022)/CD1.mp3", seconds=3.0, artist="Rune Falkner")
    recording(folder, "Rune Falkner/Sturmnacht (2022)/CD2.mp3", seconds=3.0, artist="Rune Falkner")
    scan(make_library(folder))
    assert titles() == ["Flutwelle", "Nachtglas", "Steinfeuer", "Sturmnacht"]
    with SessionLocal() as db:
        order = db.scalars(select(BookFile.path).where(BookFile.path.like("%Nachtglas-%")).order_by(BookFile.part)).all()
        assert [path.rsplit("/", 1)[-1] for path in order] == ["Nachtglas-1v10.mp3", "Nachtglas-2v10.mp3",
                                                               "Nachtglas-10v10.mp3"]


def test_loose_parts_without_tags_or_folder_are_named_by_the_file_without_its_part_number() -> None:
    folder = books_folder("loose")
    for number in (1, 2, 3):
        recording(folder, f"Nachtglas-{number}v3.mp3", seconds=3.0)
    scan(make_library(folder))
    work = only_work()
    assert work["title"] == "Nachtglas" and len(work["editions"][0]["files"]) == 3


# ---- Album tags: clean, a text of a series, a different book than the folder's ------------------------------------------


def test_an_album_with_a_carrier_behind_it_is_cleaned_and_the_folder_gives_series_volume_and_year() -> None:
    folder = books_folder("album-clean")
    parts(folder, "Rune Falkner/Nachtglas - Anselm Quist 6 (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas MP3CD",
          artist="Rune Falkner")
    scan(make_library(folder))
    work = only_work()
    assert (work["title"], work["series"], work["pos"]) == ("Nachtglas", "Anselm Quist", 6.0)
    with SessionLocal() as db:
        assert db.scalar(select(Work.year)) == 2019


@pytest.mark.parametrize("album", ["Ein Anselm-Quist-Thriller, Band 6", "Hörbuch", "Audiobook", "Track 1", ""])
def test_an_album_that_is_a_text_of_a_series_or_general_leaves_the_title_to_the_book_folder(album: str) -> None:
    folder = books_folder("album-blurb")
    parts(folder, "Rune Falkner/Nachtglas - Anselm Quist, Band 6 (2019)/Audio/{number:02d}.mp3", 2, album=album,
          artist="Rune Falkner")
    scan(make_library(folder))
    work = only_work()
    assert (work["title"], work["series"], work["pos"]) == ("Nachtglas", "Anselm Quist", 6.0)


def test_a_clean_album_that_does_not_fit_a_careful_folder_name_loses_against_the_folder() -> None:
    folder = books_folder("album-other")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Anselm Quist Gesamtausgabe",
          artist="Rune Falkner")
    scan(make_library(folder))
    assert only_work()["title"] == "Nachtglas"


def test_a_clean_album_beats_a_folder_that_says_nothing_careful() -> None:
    folder = books_folder("album-wins")
    parts(folder, "Rune Falkner/neu gekauft/Audio/{number:02d}.mp3", 2, album="Nachtglas", artist="Rune Falkner")
    scan(make_library(folder))
    assert only_work()["title"] == "Nachtglas"


def test_albums_of_different_discs_that_differ_only_by_the_carrier_are_one_book() -> None:
    folder = books_folder("album-discs")
    recording(folder, "Rune Falkner/Nachtglas (2019)/CD 1/01.mp3", seconds=3.0, album="Nachtglas CD 1 (ungekürzt)",
              artist="Rune Falkner", track=1)
    recording(folder, "Rune Falkner/Nachtglas (2019)/CD 2/01.mp3", seconds=3.0, album="Nachtglas MP3CD",
              artist="Rune Falkner", track=1)
    scan(make_library(folder))
    work = only_work()
    assert len(work["editions"]) == 1 and len(work["editions"][0]["files"]) == 2


def test_two_different_albums_in_one_folder_stay_two_books() -> None:
    folder = books_folder("two-albums")
    recording(folder, "Rune Falkner/Sammlung/a1.mp3", seconds=3.0, album="Nachtglas MP3CD", artist="Rune Falkner")
    recording(folder, "Rune Falkner/Sammlung/b1.mp3", seconds=3.0, album="Steinfeuer", artist="Rune Falkner")
    scan(make_library(folder))
    assert titles() == ["Nachtglas", "Steinfeuer"]


# ---- A collection folder of a series ---------------------------------------------------------------------------------


def test_a_collection_folder_of_a_series_has_a_book_in_every_numbered_subfolder() -> None:
    folder = books_folder("collection")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/1.-.Steinfeuer/Audio/{number:02d}.mp3", 2, album="Quist-Saga",
          artist="Rune Falkner")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/2.-.Die.große.Flut/Audio/{number:02d}.mp3", 2, album="Quist-Saga",
          artist="Rune Falkner")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/10.-.Letzter Akt/CD 1/{number:02d}.mp3", 2, album="Quist-Saga",
          artist="Rune Falkner")
    scan(make_library(folder))
    found = {work["title"]: work for work in state()["works"]}
    assert set(found) == {"Steinfeuer", "Die große Flut", "Letzter Akt"}
    assert [(w["series"], w["pos"]) for w in (found["Steinfeuer"], found["Die große Flut"], found["Letzter Akt"])] == [
        ("Quist-Saga", 1.0), ("Quist-Saga", 2.0), ("Quist-Saga", 10.0)]
    assert all(len(w["editions"]) == 1 and len(w["editions"][0]["files"]) == 2 for w in found.values())
    with SessionLocal() as db:
        assert len(db.scalars(select(Series.id)).all()) == 1


# ---- The author --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag", [None, "Various", "Various Authors", "Diverse", "Unknown"])
def test_the_author_is_the_top_folder_when_the_tags_say_nothing_or_various(tag: str | None) -> None:
    folder = books_folder("author-folder")
    kwargs: dict[str, Any] = {"narrator": "Ben Hollister"}
    if tag is not None:
        kwargs["artist"] = tag
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas", **kwargs)
    scan(make_library(folder))
    work = only_work()
    assert work["authors"] == ["Rune Falkner"]
    assert work["editions"][0]["narrators"] == ["Ben Hollister"]


def test_an_author_in_the_tags_is_not_replaced_by_the_folder() -> None:
    folder = books_folder("author-tag")
    parts(folder, "Hanna Brandt/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas", artist="Rune Falkner")
    scan(make_library(folder))
    assert only_work()["authors"] == ["Rune Falkner"]


# ---- The running time of the whole book and the question to the sources ----------------------------------------------------


def test_the_running_time_is_the_sum_over_every_disc_folder_and_the_question_names_author_and_core_title() -> None:
    folder = books_folder("runtime")
    parts(folder, "Rune Falkner/Nachtglas - Anselm Quist 6 (2019)/CD 1/{number:02d}.mp3", 2, seconds=30.0,
          album="Nachtglas MP3CD", artist="Rune Falkner")
    parts(folder, "Rune Falkner/Nachtglas - Anselm Quist 6 (2019)/CD 2/{number:02d}.mp3", 3, seconds=30.0,
          album="Nachtglas MP3CD", artist="Rune Falkner")
    scan(make_library(folder))
    with SessionLocal() as db:
        edition = db.scalar(select(Edition))
        assert edition is not None
        wanted = matching.wanted_of(db, edition)
        assert 148 <= wanted.duration <= 152
        query = matching.query_of(wanted)
        assert (query.title, query.authors, query.kind) == ("Nachtglas", ["Rune Falkner"], "audio")
        assert (wanted.name_year, wanted.series_pos) == (2019, 6.0)


def test_the_volume_and_the_year_in_the_name_of_the_book_folder_reach_the_matching_through_the_audio_folder() -> None:
    folder = books_folder("name-volume")
    parts(folder, "Rune Falkner/Nachtglas Band 3 (2019)/Audio/{number:02d}.mp3", 2, seconds=30.0, artist="Rune Falkner")
    scan(make_library(folder))
    with SessionLocal() as db:
        edition = db.scalar(select(Edition))
        assert edition is not None
        wanted = matching.wanted_of(db, edition)
        assert (wanted.name_volume, wanted.name_year) == (3.0, 2019)


# ---- What was read before is put together anew, by itself --------------------------------------------------------------


def forget_editions() -> None:
    """Takes the books off the files, as if no run had ever put them together (the files and what was read stay)."""
    with SessionLocal() as db:
        db.execute(update(BookFile).values(edition_id=None))
        for table in (Progress, Bookmark, MatchEntry, Edition, Work):
            db.execute(delete(table))
        db.commit()


def old_style(library_id: int, groups: list[list[str]]) -> dict[str, int]:
    """Makes the state an older version left: every group of files its own edition and work, files read by the older rules
    (``rev`` 1). Returns the id of the edition of each group by the first path."""
    ids: dict[str, int] = {}
    now = clock.now()
    with SessionLocal() as db:
        for number, paths in enumerate(groups, start=1):
            work = Work(title=f"Audio {number}", added_at=now, updated_at=now)
            db.add(work)
            db.flush()
            edition = Edition(work_id=work.id, library_id=library_id, kind="audio", title=f"Audio {number}",
                              created_at=now, updated_at=now)
            db.add(edition)
            db.flush()
            db.execute(update(BookFile).where(BookFile.library_id == library_id, BookFile.path.in_(paths)).values(
                edition_id=edition.id, unit=f"a\x1f{paths[0].rsplit('/', 1)[0]}\x1fold{number}", rev=1))
            ids[paths[0]] = edition.id
        db.execute(update(Library).where(Library.id == library_id).values(rev=1))
        db.commit()
    return ids


def test_what_an_older_version_split_is_put_together_once_and_the_places_of_the_people_go_with_it() -> None:
    folder = books_folder("migration")
    parts(folder, "Rune Falkner/Nachtglas (2019)/CD 1/{number:02d}.mp3", 2, seconds=60.0, album="Nachtglas",
          artist="Rune Falkner")
    parts(folder, "Rune Falkner/Nachtglas (2019)/CD 2/{number:02d}.mp3", 2, seconds=60.0, album="Nachtglas",
          artist="Rune Falkner")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        paths = {disc: sorted(db.scalars(select(BookFile.path).where(BookFile.path.like(f"%CD {disc}/%")))) for disc in (1, 2)}
    forget_editions()
    # An older version made one edition for every disc; a person listened in the second one, and corrected it by hand.
    ids = old_style(library_id, [paths[1], paths[2]])
    person = make_account("reader", "member")
    with SessionLocal() as db:
        second = ids[paths[2][0]]
        db.add(Progress(user_id=person.id, edition_id=second, locator="time:30.000", fraction=0.2, unit="time",
                        chapter=1, chapter_count=2, chapter_title="x", moved_at=clock.now(), received_at=clock.now()))
        db.add(Bookmark(user_id=person.id, edition_id=second, locator="time:50.000", fraction=0.1, chapter_title="y",
                        note="here"))
        # The correction by hand lies in the other edition, the one the person did not listen in.
        edition = db.get(Edition, ids[paths[1][0]])
        edition.overlay = {"series": {"v": "Anselm Quist", "by": "hand"}}
        db.commit()
    # Nothing is asked of anybody: the next run reads the library again (the rules are newer) and puts it together.
    scan(library_id)
    work = only_work()
    (edition,) = work["editions"]
    assert work["title"] == "Nachtglas" and len(edition["files"]) == 4
    assert 239 <= edition["duration"] <= 241
    # The edition a person worked on is the one that stays.
    assert edition["id"] == ids[paths[2][0]]
    with SessionLocal() as db:
        # One edition is left, with the place moved by the running time of the first disc (two minutes).
        assert len(db.scalars(select(Edition.id)).all()) == 1
        (place,) = db.scalars(select(Progress)).all()
        assert place.edition_id == edition["id"]
        assert 149.0 <= float(place.locator.removeprefix("time:")) <= 151.0
        assert place.fraction == pytest.approx(150 / 240, abs=0.02) and place.chapter >= 1
        (mark,) = db.scalars(select(Bookmark)).all()
        assert mark.edition_id == edition["id"] and mark.note == "here"
        assert 169.0 <= float(mark.locator.removeprefix("time:")) <= 171.0
        # The correction by hand of the edition that went stayed.
        assert db.scalar(select(Edition.series_name)) == "Anselm Quist"
        assert db.scalar(select(Library.rev)) == scanner.READ_REV


def test_the_newer_place_of_a_person_stays_when_two_editions_become_one() -> None:
    folder = books_folder("migration-newer")
    parts(folder, "Rune Falkner/Nachtglas (2019)/CD 1/{number:02d}.mp3", 1, seconds=60.0, album="Nachtglas",
          artist="Rune Falkner")
    parts(folder, "Rune Falkner/Nachtglas (2019)/CD 2/{number:02d}.mp3", 1, seconds=60.0, album="Nachtglas",
          artist="Rune Falkner")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        paths = {disc: sorted(db.scalars(select(BookFile.path).where(BookFile.path.like(f"%CD {disc}/%")))) for disc in (1, 2)}
    forget_editions()
    ids = old_style(library_id, [paths[1], paths[2]])
    person = make_account("reader2", "member")
    earlier, later = clock.now().replace(microsecond=0), clock.now()
    with SessionLocal() as db:
        db.add(Progress(user_id=person.id, edition_id=ids[paths[1][0]], locator="time:20.000", fraction=0.1,
                        unit="time", moved_at=earlier, received_at=earlier))
        db.add(Progress(user_id=person.id, edition_id=ids[paths[2][0]], locator="time:10.000", fraction=0.1,
                        unit="time", moved_at=later, received_at=later))
        db.commit()
    scan(library_id)
    with SessionLocal() as db:
        (place,) = db.scalars(select(Progress)).all()
        # The report in the second disc was the newer one: ten seconds into the second disc is 70 seconds in the book.
        assert 69.0 <= float(place.locator.removeprefix("time:")) <= 71.0


def test_a_book_that_was_read_by_the_present_rules_is_not_touched_by_the_next_run() -> None:
    folder = books_folder("steady")
    parts(folder, "Rune Falkner/Nachtglas (2019)/Audio/{number:02d}.mp3", 2, album="Nachtglas", artist="Rune Falkner")
    library_id = make_library(folder)
    scan(library_id)
    first = only_work()["editions"][0]["id"]
    scan(library_id)
    assert only_work()["editions"][0]["id"] == first
    assert only_work()["editions"][0]["files"] == sorted(only_work()["editions"][0]["files"])


def test_a_unit_that_was_split_goes_to_the_larger_part_and_two_units_never_share_an_edition() -> None:
    folder = books_folder("split")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/1.-.Steinfeuer/Audio/{number:02d}.mp3", 3, album="Quist-Saga",
          artist="Rune Falkner")
    parts(folder, "Rune Falkner/Quist-Saga (2018)/2.-.Flutwelle/Audio/{number:02d}.mp3", 2, album="Quist-Saga",
          artist="Rune Falkner")
    library_id = make_library(folder)
    scan(library_id)
    with SessionLocal() as db:
        every = sorted(db.scalars(select(BookFile.path).where(BookFile.role == "book")))
    forget_editions()
    # An older version made one edition of the five files.
    old_style(library_id, [every])
    scan(library_id)
    found = {work["title"]: work for work in state()["works"]}
    assert set(found) == {"Steinfeuer", "Flutwelle"}
    with SessionLocal() as db:
        owners = {row.unit: set() for row in db.scalars(select(BookFile).where(BookFile.role == "book"))}
        for row in db.scalars(select(BookFile).where(BookFile.role == "book")):
            owners[row.unit].add(row.edition_id)
        assert all(len(ids) == 1 for ids in owners.values())
        assert len({next(iter(ids)) for ids in owners.values()}) == 2
