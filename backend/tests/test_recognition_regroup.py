"""A recording that an older version kept as one edition per disc is matched again as the one recording it is: the
answer given to the pieces is not kept, and the running time of the whole proves the version. All names are invented."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BookFile, Edition, MatchEntry

from .fake_sources import audible_product
from .library_helpers import books_folder, make_library, scan
from .match_helpers import World, member
from .test_recognition_folders import forget_editions, old_style, parts
from .test_scanner import state


def three_discs(name: str) -> tuple[object, int]:
    folder = books_folder(name)
    for disc in (1, 2, 3):
        parts(folder, f"Rune Falkner/Nachtglas (2019)/CD {disc}/{{number:02d}}.mp3", 1, seconds=200.0,
              album="Nachtglas MP3CD", artist="Rune Falkner")
    library = make_library(folder, "Audio")
    member(library)
    return folder, library


def test_pieces_matched_as_single_editions_are_matched_again_as_the_whole_recording(
        client: TestClient, operator: Account, world: World) -> None:
    world.only("audible")
    world.fake.audible = {"de": [audible_product("B0NIGHT001", "Nachtglas", ["Rune Falkner"], 10)]}
    _folder, library = three_discs("regroup")
    scan(library)
    with SessionLocal() as db:
        paths = sorted(db.scalars(select(BookFile.path).where(BookFile.role == "book")))
    assert len(paths) == 3
    forget_editions()
    # What an older version left: an edition for every disc, each matched on its own running time and left unproven.
    ids = old_style(library, [[path] for path in paths])
    with SessionLocal() as db:
        for path in paths:
            edition = db.get(Edition, ids[path])
            edition.title, edition.duration = "Nachtglas", 200.0
            db.add(MatchEntry(library_id=library, edition_id=edition.id, state="maybe", reason="runtime_unproven",
                              fingerprint="0" * 32, created_at=edition.created_at, updated_at=edition.created_at))
        db.commit()
    world.advance(600)
    scan(library)
    work = state()["works"]
    assert [w["title"] for w in work] == ["Nachtglas"]
    (edition,) = work[0]["editions"]
    assert len(edition["files"]) == 3 and 599 <= edition["duration"] <= 601
    with SessionLocal() as db:
        (entry,) = db.scalars(select(MatchEntry)).all()
        assert (entry.state, entry.reason) == ("sure", ""), (entry.state, entry.reason)
        assert db.scalar(select(Edition.asin)) == "B0NIGHT001"


def old_pieces(world: World, name: str) -> tuple[int, dict[str, int], list[str]]:
    """Three discs scanned once and then put back as an older version left them: an edition for every disc."""
    _folder, library = three_discs(name)
    scan(library)
    with SessionLocal() as db:
        paths = sorted(db.scalars(select(BookFile.path).where(BookFile.role == "book")))
    forget_editions()
    ids = old_style(library, [[path] for path in paths])
    with SessionLocal() as db:
        for path in paths:
            edition = db.get(Edition, ids[path])
            edition.title, edition.duration = "Nachtglas", 200.0
            db.add(MatchEntry(library_id=library, edition_id=edition.id, state="pending", reason="",
                              fingerprint="0" * 32, created_at=edition.created_at, updated_at=edition.created_at))
        db.commit()
    return library, ids, paths


def test_an_answer_found_for_a_piece_while_the_discs_are_put_together_is_not_written(
        client: TestClient, operator: Account, world: World, monkeypatch) -> None:
    from app.services.match import run as matching

    world.only("audible")
    world.fake.audible = {"de": [audible_product("B0NIGHT001", "Nachtglas", ["Rune Falkner"], 10)]}
    library, ids, paths = old_pieces(world, "regroup-race")
    real_lookup = matching.lookup
    seen = {"calls": 0}

    def lookup_and_scan(db, wanted, **kwargs):  # type: ignore[no-untyped-def]
        found = real_lookup(db, wanted, **kwargs)
        seen["calls"] += 1
        if seen["calls"] == 1:
            # The scan puts the discs together while the sources are being asked about the first piece alone.
            monkeypatch.setattr(matching, "lookup", real_lookup)
            # (The matching job of the scan would answer first; here it is the other worker that is late.)
            monkeypatch.setattr(matching, "enqueue_pending", lambda db: 0)
            world.advance(5)
            scan(library)
            monkeypatch.undo()
            monkeypatch.setattr(matching, "lookup", real_lookup)
        return found

    monkeypatch.setattr(matching, "lookup", lookup_and_scan)
    with SessionLocal() as db:
        entry = db.scalar(select(MatchEntry).where(MatchEntry.edition_id == ids[paths[0]]))
        matching.match_entry(db, entry)
    (edition,) = state()["works"][0]["editions"]
    assert 599 <= edition["duration"] <= 601
    with SessionLocal() as db:
        (entry,) = db.scalars(select(MatchEntry)).all()
        assert entry.state == "pending", (entry.state, entry.reason)
    matching.enqueue_pending  # noqa: B018
    from .library_helpers import run_jobs

    with SessionLocal() as db:
        matching.enqueue_pending(db)
    run_jobs()
    with SessionLocal() as db:
        (entry,) = db.scalars(select(MatchEntry)).all()
        assert (entry.state, entry.reason) == ("sure", ""), (entry.state, entry.reason)


# ---- The fingerprint of an edition ---------------------------------------------------------------------------------


def test_everything_that_matching_goes_by_changes_the_fingerprint_and_a_few_seconds_do_not() -> None:
    from app.services.catalog import Meta
    from app.services.match import run as matching

    def make(**changes: object) -> str:
        values: dict[str, object] = {"kind": "audio", "title": "Nachtglas", "authors": ["Rune Falkner"],
                                     "language": "de", "duration": 600, "series": "Quist", "series_pos": 2.0}
        values.update(changes)
        return matching.fingerprint(Meta(**values))  # type: ignore[arg-type]

    base = make()
    assert make(duration=612) == base, "a few seconds are the same recording"
    for changes in ({"duration": 700}, {"title": "Nachtglas 2"}, {"subtitle": "Roman"}, {"authors": ["Anselm Quist"]},
                    {"language": "en"}, {"isbn": "9780000000002"}, {"asin": "B0NIGHT001"}, {"series": "Other"},
                    {"series_pos": 3.0}, {"series_pos": None}, {"kind": "ebook"}):
        assert make(**changes) != base, changes
    placed = matching.in_place(base, 3, 7)
    assert matching.in_place(base, 3, 7) == placed
    assert matching.in_place(base, 1, 7) != placed, "the number of parts"
    assert matching.in_place(base, 3, 8) != placed, "the work"


def test_a_changed_edition_is_pending_again_and_the_decision_of_a_person_stays(
        client: TestClient, operator: Account, world: World) -> None:
    from app.services.match import run as matching

    world.only("audible")
    _folder, library = three_discs("noted")
    scan(library)
    with SessionLocal() as db:
        edition = db.scalars(select(Edition)).one()
        entry = db.scalars(select(MatchEntry)).one()
        entry.state, entry.reason = "maybe", "runtime_unproven"
        db.commit()
        matching.note(db, edition, entry.fingerprint)
        db.commit()
        assert db.get(MatchEntry, entry.id).state == "maybe", "the same fingerprint changes nothing"
        matching.note(db, edition, "1" * 32)
        db.commit()
        assert db.get(MatchEntry, entry.id).state == "pending"
        entry = db.get(MatchEntry, entry.id)
        entry.state, entry.decision = "open", "kept"
        db.commit()
        matching.note(db, edition, "2" * 32)
        db.commit()
        row = db.get(MatchEntry, entry.id)
        assert (row.state, row.decision) == ("open", "kept") and row.fingerprint == "2" * 32


def test_the_pieces_of_a_recording_that_is_being_read_are_not_matched_until_the_run_is_done(
        client: TestClient, operator: Account, world: World) -> None:
    from app.models import Library
    from app.services import scanner
    from app.services.match import run as matching

    world.only("audible")
    library, _ids, _paths = old_pieces(world, "being-read")
    with SessionLocal() as db:
        assert sorted(matching._pending(db, library)) == sorted(
            db.scalars(select(MatchEntry.id)).all()), "no run is going: all wait to be matched"
        db.get(Library, library).progress_total = 9
        db.commit()
        # The run is going, and the files of these editions are of the generation before: pieces of something to come.
        assert matching._pending(db, library) == []
        db.get(Library, library).rev = scanner.READ_REV
        db.query(BookFile).update({BookFile.rev: scanner.READ_REV})
        db.commit()
        assert len(matching._pending(db, library)) == 3, "read by the present rules: no piece"
        db.query(BookFile).filter(BookFile.id == db.scalars(select(BookFile.id)).first()).update(
            {BookFile.dirty: True})
        db.commit()
        assert len(matching._pending(db, library)) == 2, "a file that waits to be put together holds its edition back"


def test_the_fingerprint_the_scan_notes_is_the_one_of_what_the_files_say_the_parts_and_the_work(
        client: TestClient, operator: Account, world: World) -> None:
    from app.services import catalog, scanner
    from app.services.match import run as matching

    world.only("audible")
    _folder, library = three_discs("noted-by-scan")
    scan(library)
    with SessionLocal() as db:
        edition = db.scalars(select(Edition)).one()
        rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition.id)))
        meta = catalog.build_meta("audio", rows, scanner._sidecars_for(db, library, rows[0].unit))
        entry = db.scalars(select(MatchEntry)).one()
        assert len(rows) == 3
        assert entry.fingerprint == matching.in_place(matching.fingerprint(meta), 3, edition.work_id)
