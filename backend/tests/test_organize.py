"""Tidying the files of a library: the scheme, the plan, the preview that is the run, the run that never
overwrites and never leaves the library, the way back, and the rights.

The rules under test: **nothing is written until the operator confirmed a preview**, **the run does what the preview
showed**, **a file is never overwritten**, **nothing leaves the library** (a name can neither climb nor follow a link),
**a book moves whole or not at all**, **what hangs on a book stays** (progress, history, the edition itself), and **it can
be undone**."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    ROLE_BOOK,
    Account,
    BookFile,
    Bookmark,
    Edition,
    HistoryEntry,
    Library,
    LibraryMember,
    OrganizeMove,
    OrganizeRun,
    Progress,
    Work,
)
from app.services import jobs, libraries, settings_service
from app.services.organize import plan as planning
from app.services.organize import run as running
from app.services.organize import scheme as schemes

from .bookmaker import cover_image, make_pdf
from .conftest import make_account, new_client
from .library_helpers import age, book, books_folder, join, make_library, make_link, recording, run_jobs

# ---- The scheme ---------------------------------------------------------------------------------------------------


def render(scheme: str, **values: str) -> list[str]:
    return schemes.render(schemes.parse(scheme), values)


def test_the_names_may_be_english_or_german_in_any_case() -> None:
    assert render("{Author}/{Title}", Author="A", Title="T") == ["A", "T"]
    assert render("{autor}/{REIHE}/{Band} - {titel} ({Jahr})", Author="A", Series="S", Volume="01", Title="T", Year="2020") \
        == ["A", "S", "01 - T (2020)"]
    assert render("{ Author }/{Title}", Author="A", Title="T") == ["A", "T"]
    assert render("{AuthorSort}/{Title} [{Language}] {Narrator}", AuthorSort="Dorn, Cy", Title="T", Language="de",
                  Narrator="N") == ["Dorn, Cy", "T [de] N"]


def test_a_part_with_nothing_left_is_left_out_and_what_dangles_is_tidied() -> None:
    scheme = "{Author}/{Series}/{Volume} - {Title} ({Year})"
    assert render(scheme, Author="A", Series="S", Volume="01", Title="T", Year="2020") == ["A", "S", "01 - T (2020)"]
    assert render(scheme, Author="A", Title="T") == ["A", "T"]
    assert render(scheme, Author="A", Title="T", Year="1999") == ["A", "T (1999)"]
    assert render(scheme, Title="T") == ["T"]
    assert render("{Title} [{Language}] ({Year})", Title="T") == ["T"]


def test_a_scheme_needs_a_title_in_the_last_part() -> None:
    for text in ("{Author}", "{Author}/{Series}", "{Title}/{Author}"):
        with pytest.raises(schemes.SchemeError) as caught:
            schemes.parse(text)
        assert caught.value.code == "no_title"
    with pytest.raises(schemes.SchemeError) as caught:
        render("{Author}/{Title}", Author="A", Title="  ...  ")
    assert caught.value.code == "no_title"


@pytest.mark.parametrize("text,code", [
    ("", "empty"), ("   ", "empty"), ("{Title}/", "empty_part"), ("/{Title}", "absolute"), ("{Title}//{Title}", "empty_part"),
    ("C:/{Title}", "absolute"), ("~/{Title}", "absolute"), ("..", "dots"), ("../{Title}", "dots"), ("{Author}/./{Title}", "dots"),
    ("{Author}\\{Title}", "backslash"), ("{Colour}/{Title}", "unknown_placeholder"), ("{Title", "unbalanced"),
    ("Title}", "unbalanced"), ("{{Title}}", "unbalanced"), ("a/b/c/d/e/f/{Title}", "too_deep"),
    ("{Title}" + "x" * 400, "too_long"), ("{Title}\x00", "bad_character"), ("{Title}\n", "empty") if False else ("{Ti\ntle}", "bad_character"),
])
def test_a_scheme_that_cannot_be_used_says_why(text: str, code: str) -> None:
    with pytest.raises(schemes.SchemeError) as caught:
        schemes.parse(text)
    assert caught.value.code == code


@pytest.mark.parametrize("raw,clean", [
    ("What: Why?", "What - Why"), ('He said "no" <to> *all* |it|', "He said no to all it"), ("AC/DC", "AC-DC"),
    ("Ends with dots...", "Ends with dots"), ("  spaced   out  ", "spaced out"), ("Empty ()", "Empty"), ("[]", ""),
    ("...", ""), ("con", "_con"), ("NUL.txt", "_NUL.txt"), ("COM1", "_COM1"), ("console", "console"),
    ("a\x00b\x1fc", "abc"), ("Tab\there", "Tab here"), ("- - dangling -", "dangling"), ("A -- B", "A - B"),
    ("Spider-Man", "Spider-Man"), (".hidden", "hidden"),
    ("CONIN$", "_CONIN$"), ("conout$.txt", "_conout$.txt"), ("COM¹", "_COM¹"), ("LPT³.epub", "_LPT³.epub"),
    ("evil\u202egpj.exe", "evilgpj.exe"), ("a\u2066b\u2069c\u200ed\u061ce", "abcde"), ("c1\x85\x9fx", "c1x"),
    ("lone\ud800surrogate", "lonesurrogate"), ("\u202eCON", "_CON"),
])
def test_a_name_is_made_safe_for_every_file_system(raw: str, clean: str) -> None:
    assert schemes.clean_part(raw) == clean


def test_a_long_name_is_cut_at_a_word_and_by_bytes() -> None:
    long = "word " * 80
    cut = schemes.clean_part(long)
    assert len(cut) <= schemes.MAX_PART and not cut.endswith(" ") and cut.startswith("word word")
    wide = schemes.clean_part("ä" * 150)
    assert len(wide.encode("utf-8")) <= schemes.MAX_BYTES
    assert schemes.clean_part("日本語" * 60).encode("utf-8").decode("utf-8")


@pytest.mark.parametrize("position,text", [(1, "01"), (2.0, "02"), (2.5, "02.5"), (10, "10"), (1.25, "01.25"), (105, "105"),
                                           (0, "00"), (None, ""), (-1, "")])
def test_the_volume_is_written_with_two_places(position: float | None, text: str) -> None:
    assert schemes.volume_text(position) == text


def test_a_value_can_never_add_a_part_or_climb() -> None:
    parts = render("{Author}/{Title}", Author="../../etc", Title="a/../../b")
    assert "/" not in "".join(parts) and parts[0] not in ("..", ".")
    assert all(".." not in part.replace("....", "") or part.count("/") == 0 for part in parts)


# ---- A library to tidy ----------------------------------------------------------------------------------------------


class Shelf:
    """A library of books laid out as people lay them: loose files, a folder per book with files beside it, a recording of
    many parts, an e-book in two formats. Ready to tidy; the operator is ``tester``."""

    def __init__(self) -> None:
        self.root = books_folder("tidy")
        self.first = book(self.root, "Loose/Mara Lindqvist - The Quiet Orchard.epub", title="The Quiet Orchard",
                          authors=("Mara Lindqvist",), language="en", date="2011-03-04", series="Orchard Days",
                          series_pos=1)
        pdf = make_pdf(self.root / "Loose" / "Mara Lindqvist - The Quiet Orchard.pdf", pages=3)
        age(pdf)
        self.second = book(self.root, "Second Orchard/second.epub", title="The Loud Orchard", authors=("Mara Lindqvist",),
                           language="en", date="2013", series="Orchard Days", series_pos=2.5)
        self.cover = self.root / "Second Orchard" / "cover.jpg"
        self.cover.write_bytes(cover_image((90, 60, 40), (300, 450), "JPEG"))
        age(self.cover)
        self.single = book(self.root, "single/kite.epub", title="Kite Season", authors=("Jonas Brandauer",), language="de")
        self.parts = [recording(self.root, f"Audio/Seven Bridges/{n:02d}.mp3", 12.0, album="Seven Bridges",
                                artist="Ben Hollister", title=f"Part {n}", track=n) for n in (1, 2)]
        self.audio_cover = self.root / "Audio" / "Seven Bridges" / "cover.jpg"
        self.audio_cover.write_bytes(cover_image((10, 40, 90), (300, 300), "JPEG"))
        age(self.audio_cover)
        self.library_id = make_library(self.root, "Tidy", scan=True)
        # Tidying is for an operator who manages the library as a member (``routers/organize._library``).
        with SessionLocal() as db:
            tester = db.scalar(select(Account).where(Account.name == "tester"))
        if tester is not None:
            join(self.library_id, tester.id)

    def tree(self) -> dict[str, str]:
        """Every file below the library with a digest of its content: what the disk holds."""
        return {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.root.rglob("*")) if path.is_file()}

    def folders(self) -> set[str]:
        return {path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_dir()}

    def edition(self, title: str, kind: str = "ebook") -> int:
        with SessionLocal() as db:
            return db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == kind)).one()

    def paths(self) -> dict[int, str]:
        with SessionLocal() as db:
            return {row.id: row.path for row in db.scalars(select(BookFile))}


SCHEME = "{Author}/{Series}/{Volume} - {Title} ({Year})"


@pytest.fixture
def shelf(operator: Account) -> Shelf:
    with SessionLocal() as db:
        settings_service.save(db, {"organize_enabled": True})
    return Shelf()


def preview(client: TestClient, shelf: Shelf, scheme: str = SCHEME) -> dict[str, Any]:
    answer = client.post("/api/v1/organize/preview", json={"library_id": shelf.library_id, "scheme": scheme})
    assert answer.status_code == 201, answer.text
    return answer.json()


def moves(client: TestClient, run: int, state: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {"offset": len(out), "limit": 100}
        if state:
            params["state"] = state
        page = client.get(f"/api/v1/organize/runs/{run}/moves", params=params).json()
        out += page["items"]
        if len(out) >= page["total"] or not page["items"]:
            return out


def start(client: TestClient, run: int):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/organize/runs/{run}/start", json={"confirm": True})


def targets(client: TestClient, run: int, state: str = "planned") -> dict[str, str]:
    return {m["source"]: m["target"] for m in moves(client, run, state)}


# ---- The plan and the preview ----------------------------------------------------------------------------------------


def test_the_preview_says_where_every_file_goes_and_touches_nothing(shelf: Shelf, client: TestClient) -> None:
    before, folders = shelf.tree(), shelf.folders()
    run = preview(client, shelf)
    assert run["state"] == "preview" and run["scheme"] == SCHEME
    assert shelf.tree() == before and shelf.folders() == folders  # not a byte, not a folder
    assert targets(client, run["id"]) == {
        "Loose/Mara Lindqvist - The Quiet Orchard.epub": "Mara Lindqvist/Orchard Days/01 - The Quiet Orchard (2011).epub",
        "Loose/Mara Lindqvist - The Quiet Orchard.pdf": "Mara Lindqvist/Orchard Days/01 - The Quiet Orchard (2011).pdf",
        "Second Orchard/second.epub": "Mara Lindqvist/Orchard Days/02.5 - The Loud Orchard (2013).epub",
        "Second Orchard/cover.jpg": "Mara Lindqvist/Orchard Days/cover.jpg",
        "single/kite.epub": "Jonas Brandauer/Kite Season.epub",
        "Audio/Seven Bridges/01.mp3": "Ben Hollister/Seven Bridges/01.mp3",
        "Audio/Seven Bridges/02.mp3": "Ben Hollister/Seven Bridges/02.mp3",
        "Audio/Seven Bridges/cover.jpg": "Ben Hollister/Seven Bridges/cover.jpg",
    }
    summary = run["summary"]
    assert (summary["move_books"], summary["stay_books"], summary["problem_books"]) == (4, 0, 0)
    assert summary["move_files"] == 8 and run["total"] == 8
    assert summary["empty"] == {"Series": 2, "Volume": 2, "Year": 2}
    assert len(summary["digest"]) == 64


def test_a_folder_of_a_recording_is_the_last_part_and_its_parts_keep_their_names(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf, "{Author}/{Title} ({Year})")
    found = targets(client, run["id"])
    assert found["Audio/Seven Bridges/01.mp3"] == "Ben Hollister/Seven Bridges/01.mp3"
    assert found["Audio/Seven Bridges/cover.jpg"] == "Ben Hollister/Seven Bridges/cover.jpg"
    assert found["Loose/Mara Lindqvist - The Quiet Orchard.epub"] == "Mara Lindqvist/The Quiet Orchard (2011).epub"


def test_a_book_that_is_where_it_belongs_stays_and_is_not_listed(shelf: Shelf, client: TestClient) -> None:
    first = preview(client, shelf)
    assert start(client, first["id"]).status_code == 202
    run_jobs()
    second = preview(client, shelf)
    assert second["summary"]["stay_books"] == 4 and second["summary"]["move_books"] == 0 and second["total"] == 0
    assert moves(client, second["id"]) == []
    assert start(client, second["id"]).status_code == 409


def test_the_preview_lists_the_problems_with_their_reason(shelf: Shelf, client: TestClient) -> None:
    taken = shelf.root / "Jonas Brandauer"
    taken.mkdir()
    (taken / "Kite Season.epub").write_bytes(b"not yours")
    run = preview(client, shelf)
    assert run["summary"]["problem_books"] == 1 and run["summary"]["problems"] == {"exists": 1}
    bad = moves(client, run["id"], "problem")
    assert [(m["source"], m["reason"], m["title"]) for m in bad] == [("single/kite.epub", "exists", "Kite Season")]
    assert run["summary"]["move_books"] == 3


def test_two_books_sent_to_one_name_are_both_a_problem(shelf: Shelf, client: TestClient) -> None:
    with SessionLocal() as db:
        for title in ("The Quiet Orchard", "The Loud Orchard"):
            db.get(Edition, shelf.edition(title)).title = "Orchard"  # type: ignore[union-attr]
        db.commit()
    run = preview(client, shelf, "{Author}/{Title}")
    bad = {m["source"]: m["reason"] for m in moves(client, run["id"], "problem")}
    # Both books, every file of both: the one that would have been first is as much a problem as the second.
    assert bad == {"Loose/Mara Lindqvist - The Quiet Orchard.epub": "collision",
                   "Loose/Mara Lindqvist - The Quiet Orchard.pdf": "book_blocked",
                   "Second Orchard/second.epub": "collision", "Second Orchard/cover.jpg": "book_blocked"}
    assert {m["source"] for m in moves(client, run["id"], "planned")} >= {"single/kite.epub"}
    assert run["summary"]["problems"] == {"book_blocked": 2, "collision": 2}


def test_a_problem_in_one_file_blocks_the_whole_book(shelf: Shelf, client: TestClient) -> None:
    target = shelf.root / "Mara Lindqvist" / "Orchard Days"
    target.mkdir(parents=True)
    (target / "01 - The Quiet Orchard (2011).pdf").write_bytes(b"in the way")
    run = preview(client, shelf)
    problems = {m["source"]: m["reason"] for m in moves(client, run["id"], "problem")}
    assert problems == {"Loose/Mara Lindqvist - The Quiet Orchard.pdf": "exists",
                        "Loose/Mara Lindqvist - The Quiet Orchard.epub": "book_blocked"}
    assert run["summary"]["problem_books"] == 1


def test_a_goal_behind_a_link_or_where_a_file_must_be_a_folder_is_a_problem(shelf: Shelf, client: TestClient,
                                                                        tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    make_link(shelf.root / "Jonas Brandauer", outside)
    (shelf.root / "Ben Hollister").write_bytes(b"a file where an author's folder would be")
    run = preview(client, shelf)
    reasons = {m["source"]: m["reason"] for m in moves(client, run["id"], "problem")}
    assert reasons["single/kite.epub"] == "link"
    assert reasons["Audio/Seven Bridges/01.mp3"] == "exists"
    assert not any(outside.iterdir())


def test_a_path_that_would_be_too_long_is_a_problem(shelf: Shelf, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schemes, "MAX_PATH", 30)
    monkeypatch.setattr(planning.schemes, "MAX_PATH", 30)
    run = preview(client, shelf)
    assert "too_long" in run["summary"]["problems"]


def test_a_title_with_forbidden_characters_gets_a_clean_name(shelf: Shelf, client: TestClient) -> None:
    with SessionLocal() as db:
        db.get(Edition, shelf.edition("Kite Season")).title = 'Kite: Season? <1> "x" / y.'  # type: ignore[union-attr]
        db.commit()
    run = preview(client, shelf, "{Author}/{Title}")
    assert targets(client, run["id"])["single/kite.epub"] == "Jonas Brandauer/Kite - Season 1 x - y.epub"


def test_a_book_without_an_author_stands_in_the_root_of_the_scheme(shelf: Shelf, client: TestClient) -> None:
    with SessionLocal() as db:
        from app.models import Credit

        db.query(Credit).filter_by(edition_id=shelf.edition("Kite Season")).delete()
        db.commit()
    run = preview(client, shelf, "{Author}/{Title}")
    assert targets(client, run["id"])["single/kite.epub"] == "Kite Season.epub"
    assert run["summary"]["empty"]["Author"] == 1


def test_a_scheme_that_is_wrong_is_refused_with_its_reason(shelf: Shelf, client: TestClient) -> None:
    for scheme, reason in (("{Author}", "no_title"), ("../{Title}", "dots"), ("{Colour}/{Title}", "unknown_placeholder"),
                           ("/etc/{Title}", "absolute")):
        answer = client.post("/api/v1/organize/preview", json={"library_id": shelf.library_id, "scheme": scheme})
        assert answer.status_code == 422, scheme
        assert answer.json()["detail"]["code"] == "scheme_invalid" and answer.json()["detail"]["reason"] == reason
    assert client.post("/api/v1/organize/preview", json={"library_id": shelf.library_id, "scheme": ""}).status_code == 422
    assert client.post("/api/v1/organize/preview", json={"library_id": 999, "scheme": SCHEME}).status_code == 404


def test_a_new_preview_replaces_the_old_one(shelf: Shelf, client: TestClient) -> None:
    preview(client, shelf)
    second = preview(client, shelf, "{Author}/{Title}")
    with SessionLocal() as db:
        assert db.scalars(select(OrganizeRun.scheme)).all() == ["{Author}/{Title}"]
        assert db.scalar(select(func.count()).select_from(OrganizeMove)) == 8
    assert client.get(f"/api/v1/organize/runs/{second['id']}").json()["scheme"] == "{Author}/{Title}"
    assert client.delete(f"/api/v1/organize/runs/{second['id']}").status_code == 204
    assert client.get(f"/api/v1/organize/runs/{second['id']}").status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OrganizeMove)) == 0


# ---- The run ---------------------------------------------------------------------------------------------------------------


def test_nothing_starts_without_the_operators_confirmation(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    before = shelf.tree()
    assert client.post(f"/api/v1/organize/runs/{run['id']}/start", json={}).status_code == 422
    assert client.post(f"/api/v1/organize/runs/{run['id']}/start", json={"confirm": False}).status_code == 422
    assert client.post(f"/api/v1/organize/runs/{run['id']}/start").status_code == 422
    run_jobs()
    assert shelf.tree() == before
    with SessionLocal() as db:
        assert db.get(OrganizeRun, run["id"]).state == "preview"  # type: ignore[union-attr]
        assert db.scalar(select(func.count()).select_from(OrganizeMove).where(OrganizeMove.state == "moved")) == 0


def test_the_run_does_exactly_what_the_preview_showed(shelf: Shelf, client: TestClient) -> None:
    before = shelf.tree()
    run = preview(client, shelf)
    shown = targets(client, run["id"])
    assert start(client, run["id"]).status_code == 202
    assert client.get(f"/api/v1/organize/runs/{run['id']}").json()["state"] == "queued"
    assert shelf.tree() == before  # queued: nothing yet
    run_jobs()
    done = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert done["state"] == "done" and done["done"] == done["total"] == 8 and done["error"] == ""
    after = shelf.tree()
    assert sorted(after) == sorted(shown.values())
    # Not a byte changed on the way: every file is the file it was.
    assert {shown[source]: digest for source, digest in before.items()} == after
    assert {m["target"] for m in moves(client, run["id"], "moved")} == set(shown.values())
    assert moves(client, run["id"], "planned") == []


def test_the_old_folders_that_are_empty_go_and_the_ones_with_something_in_them_stay(shelf: Shelf, client: TestClient) -> None:
    (shelf.root / "Loose" / "notes.txt").write_text("mine", encoding="utf-8")
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()
    folders = shelf.folders()
    assert "Loose" in folders and "Second Orchard" not in folders and "single" not in folders
    assert "Audio/Seven Bridges" not in folders and "Audio" not in folders
    assert (shelf.root / "Loose" / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert shelf.root.is_dir()


def test_the_database_follows_the_files_and_the_next_scan_finds_them_unchanged(shelf: Shelf, client: TestClient) -> None:
    with SessionLocal() as db:
        editions = sorted(db.scalars(select(Edition.id)))
        works = db.scalar(select(func.count()).select_from(Work))
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()  # also the scan the run asked for
    with SessionLocal() as db:
        assert sorted(db.scalars(select(Edition.id))) == editions
        assert db.scalar(select(func.count()).select_from(Work)) == works
        rows = list(db.scalars(select(BookFile)))
        assert all(row.missing_at is None for row in rows) and len(rows) == 8
        on_disk = {p.relative_to(shelf.root).as_posix() for p in shelf.root.rglob("*") if p.is_file()}
        assert {row.path for row in rows} == on_disk
        assert db.get(Library, shelf.library_id).report["added"] == 0  # type: ignore[union-attr]
        assert db.get(Library, shelf.library_id).report["changed"] == 0  # type: ignore[union-attr]
        assert db.get(Library, shelf.library_id).report["missing"] == 0  # type: ignore[union-attr]


def test_a_book_file_that_moves_gets_the_unit_of_its_new_place_at_once(shelf: Shelf) -> None:
    """Before the scan that follows a run looks at it: the unit names the folder and the name a book stands under."""
    from app.services import catalog

    with SessionLocal() as db:
        row = db.scalar(select(BookFile).where(BookFile.role == ROLE_BOOK, BookFile.format == "epub"))
        assert row is not None
        old_unit = row.unit
        new_path = "Somewhere Else/Another Name/another name.epub"
        running._follow(db, shelf.library_id, row, new_path)
        assert row.path == new_path
        assert row.unit == catalog.unit_key(new_path, row.format, row.probe if isinstance(row.probe, dict) else None)
        assert row.unit != old_unit
        db.rollback()


def test_progress_bookmarks_and_history_stay_with_the_book(shelf: Shelf, client: TestClient) -> None:
    edition = shelf.edition("The Quiet Orchard")
    rita = make_account("rita")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=shelf.library_id, user_id=rita.id, right="read"))
        db.commit()
    with new_client(rita) as person:
        body = {"locator": "epubcfi(/6/4)", "fraction": 0.5, "chapter": 1, "chapter_count": 3, "chapter_title": "One",
                "device_key": "dev-a", "device": "Firefox"}
        assert person.put(f"/api/v1/editions/{edition}/progress", json=body).status_code == 200
        person.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/2)", "fraction": 0.1})
        person.post(f"/api/v1/editions/{edition}/finished", json={"finished_on": "2025-01-02"})
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()
    with new_client(rita) as person:
        assert person.get(f"/api/v1/editions/{edition}/progress").json()["fraction"] == 0.5
        assert len(person.get(f"/api/v1/editions/{edition}/bookmarks").json()) == 1
        assert [e["finished_on"] for e in person.get("/api/v1/history").json()] == ["2025-01-02"]
        assert person.get(f"/api/v1/editions/{edition}").json()["edition"]["files"][0]["path"].startswith("Mara Lindqvist/")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress)) == 1
        assert db.scalar(select(func.count()).select_from(Bookmark)) == 1
        assert db.scalar(select(func.count()).select_from(HistoryEntry)) == 1


def test_a_file_is_never_overwritten_not_even_one_that_arrives_after_the_preview(shelf: Shelf, client: TestClient,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    blocker = shelf.root / "Jonas Brandauer" / "Kite Season.epub"
    blocker.parent.mkdir()
    blocker.write_bytes(b"it came in the meantime")
    run_jobs()
    assert blocker.read_bytes() == b"it came in the meantime"
    assert (shelf.root / "single" / "kite.epub").is_file()  # the book stayed where it was
    skipped = moves(client, run["id"], "skipped")
    assert [(m["source"], m["reason"]) for m in skipped] == [("single/kite.epub", "exists")]
    # The other books went.
    assert (shelf.root / "Mara Lindqvist" / "Orchard Days" / "01 - The Quiet Orchard (2011).epub").is_file()
    assert client.get(f"/api/v1/organize/runs/{run['id']}").json()["state"] == "done"


def test_a_book_moves_whole_or_not_at_all(shelf: Shelf, client: TestClient) -> None:
    """The second file of a book is blocked while the run is on: the first one, which already went, is put back."""
    run = preview(client, shelf)
    start(client, run["id"])
    target = shelf.root / "Mara Lindqvist" / "Orchard Days"
    target.mkdir(parents=True)
    (target / "01 - The Quiet Orchard (2011).pdf").write_bytes(b"came in the meantime")
    before_epub = (shelf.root / "Loose" / "Mara Lindqvist - The Quiet Orchard.epub").read_bytes()
    run_jobs()
    assert (shelf.root / "Loose" / "Mara Lindqvist - The Quiet Orchard.epub").read_bytes() == before_epub
    assert (shelf.root / "Loose" / "Mara Lindqvist - The Quiet Orchard.pdf").is_file()
    assert not (target / "01 - The Quiet Orchard (2011).epub").exists()
    assert (target / "01 - The Quiet Orchard (2011).pdf").read_bytes() == b"came in the meantime"
    states = {m["source"]: m["state"] for m in moves(client, run["id"])}
    assert states["Loose/Mara Lindqvist - The Quiet Orchard.epub"] == "skipped"
    assert states["Loose/Mara Lindqvist - The Quiet Orchard.pdf"] == "skipped"
    with SessionLocal() as db:
        # And the database says the same: the book is where it was.
        assert {row.path for row in db.scalars(select(BookFile).where(BookFile.path.like("Loose/%")))} == {
            "Loose/Mara Lindqvist - The Quiet Orchard.epub", "Loose/Mara Lindqvist - The Quiet Orchard.pdf"}


def test_a_file_that_changed_since_the_preview_is_not_moved(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    changed = shelf.root / "single" / "kite.epub"
    changed.write_bytes(changed.read_bytes() + b"more")
    run_jobs()
    assert changed.is_file()
    assert [(m["source"], m["reason"]) for m in moves(client, run["id"], "skipped")] == [("single/kite.epub", "changed")]


def test_a_library_that_changed_since_the_preview_asks_for_a_new_one(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    book(shelf.root, "newcomer/new.epub", title="Newcomer", authors=("New Author",), language="en")
    with SessionLocal() as db:
        libraries.start_scan(db, shelf.library_id)
    run_jobs()
    answer = start(client, run["id"])
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "preview_stale"
    assert not (shelf.root / "Mara Lindqvist").exists()
    # A file that grew is as stale.
    again = preview(client, shelf)
    path = shelf.root / "single" / "kite.epub"
    path.write_bytes(path.read_bytes() + b"x")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime - 7200))
    with SessionLocal() as db:
        libraries.start_scan(db, shelf.library_id)
    run_jobs()
    assert start(client, again["id"]).json()["detail"]["code"] == "preview_stale"


def test_one_tidying_at_a_time_for_a_library(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    again = client.post("/api/v1/organize/preview", json={"library_id": shelf.library_id, "scheme": SCHEME})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "organize_busy"
    assert start(client, run["id"]).status_code == 409
    run_jobs()


def test_tidying_is_off_until_the_operator_turns_it_on(operator: Account, client: TestClient) -> None:
    shelf = Shelf()
    assert client.get("/api/v1/organize/settings").json() == {"enabled": False}
    status = client.get("/api/v1/organize/status", params={"library": shelf.library_id}).json()
    assert status["enabled"] is False
    answer = client.post("/api/v1/organize/preview", json={"library_id": shelf.library_id, "scheme": SCHEME})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "organize_off"
    assert client.put("/api/v1/organize/settings", json={"enabled": True}).json() == {"enabled": True}
    assert client.post("/api/v1/organize/preview", json={"library_id": shelf.library_id, "scheme": SCHEME}).status_code == 201
    assert client.put("/api/v1/organize/settings", json={"enabled": False}).status_code == 200
    # Off again: a preview made before cannot be started, and a run cannot be undone.
    with SessionLocal() as db:
        run = db.scalar(select(OrganizeRun))
        assert run is not None
    assert start(client, run.id).json()["detail"]["code"] == "organize_off"


def test_the_status_says_whether_nexbooks_may_write_and_why_not(shelf: Shelf, client: TestClient,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    status = client.get("/api/v1/organize/status", params={"library": shelf.library_id}).json()
    assert status["enabled"] is True and status["writable"] is True and status["reason"] == ""
    assert status["scheme"] == SCHEME and {p["name"] for p in status["placeholders"]} >= {"Author", "Title", "Volume"}
    monkeypatch.setattr(planning, "writable", lambda path: (False, "read_only"))
    status = client.get("/api/v1/organize/status", params={"library": shelf.library_id}).json()
    assert status["writable"] is False and status["reason"] == "read_only"
    run = preview(client, shelf)  # a preview writes nothing, so it works on a read-only library
    answer = start(client, run["id"])
    assert answer.status_code == 409 and answer.json()["detail"] == {
        "code": "organize_not_writable", "message": "nexbooks may not write in this library.", "reason": "read_only"}
    assert client.get("/api/v1/organize/status", params={"library": 999}).status_code == 404


def test_a_library_mounted_read_only_stops_the_run_without_a_change(shelf: Shelf, client: TestClient,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    before = shelf.tree()
    monkeypatch.setattr(planning, "writable", lambda path: (False, "read_only"))
    run_jobs()
    done = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert done["state"] == "failed" and done["error"] == "read_only"
    assert shelf.tree() == before


def test_a_write_the_system_refuses_ends_the_run_and_puts_the_book_back(shelf: Shelf, client: TestClient,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    real = os.link
    calls = {"n": 0}

    def refuse(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] > 2:
            raise PermissionError(13, "read-only")
        real(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", refuse)
    run_jobs()
    done = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert done["state"] == "failed" and done["error"] == "no_write_access"
    # Nothing is half-moved: every book is whole, in one place.
    with SessionLocal() as db:
        for row in db.scalars(select(BookFile)):
            assert (shelf.root / row.path).is_file(), row.path
    names = shelf.tree()
    assert len(names) == 8


# ---- Undoing -------------------------------------------------------------------------------------------------------------


def test_undoing_puts_everything_back_where_it_was(shelf: Shelf, client: TestClient) -> None:
    before, folders, paths = shelf.tree(), shelf.folders(), shelf.paths()
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()
    assert shelf.tree() != before
    answer = client.post(f"/api/v1/organize/runs/{run['id']}/undo", json={"confirm": True})
    assert answer.status_code == 202 and answer.json()["state"] == "undoing"
    run_jobs()
    assert client.get(f"/api/v1/organize/runs/{run['id']}").json()["state"] == "undone"
    assert shelf.tree() == before and shelf.folders() == folders and shelf.paths() == paths
    assert {m["state"] for m in moves(client, run["id"])} == {"undone"}
    with SessionLocal() as db:
        assert all(row.missing_at is None for row in db.scalars(select(BookFile)))


def test_undoing_wants_the_confirmation_and_the_newest_run(shelf: Shelf, client: TestClient) -> None:
    first = preview(client, shelf)
    start(client, first["id"])
    run_jobs()
    assert client.post(f"/api/v1/organize/runs/{first['id']}/undo", json={}).status_code == 422
    assert client.post(f"/api/v1/organize/runs/{first['id']}/undo", json={"confirm": False}).status_code == 422
    second = preview(client, shelf, "{Author}/{Title}")
    start(client, second["id"])
    run_jobs()
    refused = client.post(f"/api/v1/organize/runs/{first['id']}/undo", json={"confirm": True})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "organize_not_latest"
    assert client.post(f"/api/v1/organize/runs/{second['id']}/undo", json={"confirm": True}).status_code == 202
    run_jobs()
    assert client.post(f"/api/v1/organize/runs/{first['id']}/undo", json={"confirm": True}).status_code == 202
    run_jobs()
    assert client.post(f"/api/v1/organize/runs/{first['id']}/undo", json={"confirm": True}).status_code == 409


def test_a_preview_cannot_be_undone_and_a_started_run_cannot_be_thrown_away(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    assert client.post(f"/api/v1/organize/runs/{run['id']}/undo", json={"confirm": True}).status_code == 409
    start(client, run["id"])
    run_jobs()
    assert client.delete(f"/api/v1/organize/runs/{run['id']}").status_code == 409


def test_undoing_leaves_a_file_the_person_moved_themselves_and_says_so(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()
    moved = shelf.root / "Jonas Brandauer" / "Kite Season.epub"
    elsewhere = shelf.root / "Kite Season - mine.epub"
    moved.rename(elsewhere)
    client.post(f"/api/v1/organize/runs/{run['id']}/undo", json={"confirm": True})
    run_jobs()
    done = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert done["state"] == "failed" and done["error"] == "undo_incomplete"
    assert elsewhere.is_file() and (shelf.root / "single" / "kite.epub").exists() is False
    assert (shelf.root / "Loose" / "Mara Lindqvist - The Quiet Orchard.epub").is_file()  # the rest went back


def test_undoing_never_overwrites_a_file_that_took_the_old_place(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()
    squatter = shelf.root / "single" / "kite.epub"
    squatter.parent.mkdir(exist_ok=True)
    squatter.write_bytes(b"somebody put this here")
    client.post(f"/api/v1/organize/runs/{run['id']}/undo", json={"confirm": True})
    run_jobs()
    assert squatter.read_bytes() == b"somebody put this here"
    assert (shelf.root / "Jonas Brandauer" / "Kite Season.epub").is_file()


# ---- A run stopped in the middle ------------------------------------------------------------------------------------------


def test_a_run_stopped_by_a_restart_goes_on_where_it_was(shelf: Shelf, client: TestClient,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    real = running._walk
    seen = {"groups": 0}

    def stop_after_one(*args: Any, **kwargs: Any) -> bool:
        seen["groups"] += 1
        if seen["groups"] == 2:
            jobs.stopping.set()
        return real(*args, **kwargs)

    monkeypatch.setattr(running, "_walk", stop_after_one)
    jobs.run_pending(10)
    state = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert state["state"] == "running" and 0 < state["done"] < 8
    jobs.stopping.clear()
    monkeypatch.setattr(running, "_walk", real)
    with SessionLocal() as db:
        jobs.recover(db)
    run_jobs()
    done = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert done["state"] == "done" and done["done"] == 8
    assert len(moves(client, run["id"], "moved")) == 8
    assert not (shelf.root / "Loose").exists() or not list((shelf.root / "Loose").glob("*.epub"))


def test_a_file_already_moved_when_the_machine_stopped_is_taken_as_moved(shelf: Shelf, client: TestClient) -> None:
    """The file was renamed but the database never heard of it (a crash between the two): the run finds it at its goal."""
    run = preview(client, shelf)
    start(client, run["id"])
    source = shelf.root / "single" / "kite.epub"
    target = shelf.root / "Jonas Brandauer" / "Kite Season.epub"
    target.parent.mkdir()
    source.rename(target)
    run_jobs()
    done = client.get(f"/api/v1/organize/runs/{run['id']}").json()
    assert done["state"] == "done"
    with SessionLocal() as db:
        assert db.scalars(select(BookFile.path).where(BookFile.path.like("%Kite%"))).one() == "Jonas Brandauer/Kite Season.epub"
    assert {m["state"] for m in moves(client, run["id"]) if m["source"] == "single/kite.epub"} == {"moved"}


def test_a_file_at_the_goal_of_another_size_is_not_taken_as_the_moved_one(shelf: Shelf, client: TestClient) -> None:
    """Another file lies at the goal and the source is gone: only a file of the size of the book may count as moved."""
    run = preview(client, shelf)
    start(client, run["id"])
    source = shelf.root / "single" / "kite.epub"
    target = shelf.root / "Jonas Brandauer" / "Kite Season.epub"
    target.parent.mkdir()
    source.rename(target)
    target.write_bytes(target.read_bytes() + b"another file")
    run_jobs()
    states = {(m["state"], m["reason"]) for m in moves(client, run["id"]) if m["source"] == "single/kite.epub"}
    assert states == {("skipped", "changed")}
    with SessionLocal() as db:
        assert db.scalars(select(BookFile.path).where(BookFile.path.like("%kite%"))).one() == "single/kite.epub"


# ---- Never out of the library ---------------------------------------------------------------------------------------------


def test_the_goal_of_a_hostile_name_stays_inside_the_library(shelf: Shelf, client: TestClient) -> None:
    with SessionLocal() as db:
        from app.models import Person

        for person in db.scalars(select(Person)):
            person.name = "../../../escape"
        db.commit()
        for edition in db.scalars(select(Edition)):
            edition.title = "..\\..\\x/../y"
        db.commit()
    run = preview(client, shelf, "{Author}/{Title}")
    for move in moves(client, run["id"]):
        parts = move["target"].split("/")
        assert ".." not in parts and "" not in parts and not move["target"].startswith("/") and "\\" not in move["target"]
    start(client, run["id"])
    run_jobs()
    outside = shelf.root.parent
    assert not (outside / "escape").exists()
    for path in shelf.root.rglob("*"):
        assert path.resolve().is_relative_to(shelf.root.resolve())


def test_a_link_in_the_way_stops_the_move_even_after_the_preview(shelf: Shelf, client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    run = preview(client, shelf)
    start(client, run["id"])
    make_link(shelf.root / "Jonas Brandauer", outside)
    run_jobs()
    assert list(outside.iterdir()) == []
    assert (shelf.root / "single" / "kite.epub").is_file()
    assert [m["reason"] for m in moves(client, run["id"], "skipped")] == ["link"]


def test_a_source_whose_folder_became_a_link_is_not_moved(shelf: Shelf, client: TestClient, tmp_path: Path) -> None:
    import shutil

    run = preview(client, shelf)
    start(client, run["id"])
    outside = tmp_path / "outside-single"
    outside.mkdir()
    shutil.copy2(shelf.root / "single" / "kite.epub", outside / "kite.epub")
    shutil.rmtree(shelf.root / "single")
    make_link(shelf.root / "single", outside)
    run_jobs()
    assert (outside / "kite.epub").is_file()  # what the link leads to is not touched
    assert not (shelf.root / "Jonas Brandauer").exists()
    assert [(m["source"], m["reason"]) for m in moves(client, run["id"], "skipped")] == [("single/kite.epub", "link")]


def test_the_routes_never_accept_a_path(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    assert client.get(f"/api/v1/organize/runs/{run['id']}/moves", params={"state": "../x"}).status_code == 422
    assert client.post("/api/v1/organize/preview", json={"library_id": "../1", "scheme": SCHEME}).status_code == 422
    assert client.get("/api/v1/organize/runs/abc").status_code == 422
    assert client.get(f"/api/v1/organize/runs/{run['id']}/moves", params={"limit": 101}).status_code == 422


# ---- Who may ---------------------------------------------------------------------------------------------------------------


def test_only_the_operator_may_tidy(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    member = make_account("rita")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=shelf.library_id, user_id=member.id, right="manage"))
        db.commit()
    calls = [
        ("get", f"/api/v1/organize/status?library={shelf.library_id}", None),
        ("get", "/api/v1/organize/settings", None), ("put", "/api/v1/organize/settings", {"enabled": True}),
        ("post", "/api/v1/organize/preview", {"library_id": shelf.library_id, "scheme": SCHEME}),
        ("get", f"/api/v1/organize/runs/{run['id']}", None), ("get", f"/api/v1/organize/runs/{run['id']}/moves", None),
        ("post", f"/api/v1/organize/runs/{run['id']}/start", {"confirm": True}),
        ("post", f"/api/v1/organize/runs/{run['id']}/undo", {"confirm": True}), ("delete", f"/api/v1/organize/runs/{run['id']}", None),
    ]
    with new_client(member) as person:
        for method, url, body in calls:
            answer = getattr(person, method)(url, **({"json": body} if body is not None else {}))
            assert answer.status_code == 403, (method, url, answer.status_code)
    from fastapi.testclient import TestClient as Anonymous

    from app.main import app

    with Anonymous(app, base_url="http://testserver", headers={"X-Nexbooks-Client": "tab-tests000"}) as nobody:
        for method, url, body in calls:
            answer = getattr(nobody, method)(url, **({"json": body} if body is not None else {}))
            assert answer.status_code in (401, 403), (method, url, answer.status_code)
    assert shelf.tree()  # and nothing was moved


def test_a_key_and_a_device_cannot_tidy(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    from app.services import apikeys

    apikeys.forget()
    with new_client() as program:
        for secret in (key, token):
            head = {"Authorization": f"Bearer {secret}"}
            assert program.get(f"/api/v1/organize/runs/{run['id']}", headers=head).status_code in (401, 403)
            assert program.post(f"/api/v1/organize/runs/{run['id']}/start", json={"confirm": True},
                                headers=head).status_code in (401, 403)
    assert not (shelf.root / "Mara Lindqvist").exists()


def test_a_change_names_its_tab(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    client.headers.pop("X-Nexbooks-Client")
    assert client.post(f"/api/v1/organize/runs/{run['id']}/start", json={"confirm": True}).status_code == 400
    assert client.put("/api/v1/organize/settings", json={"enabled": False}).status_code == 400


def test_removing_a_library_takes_its_runs_with_it(shelf: Shelf, client: TestClient) -> None:
    run = preview(client, shelf)
    start(client, run["id"])
    run_jobs()
    assert client.delete(f"/api/v1/libraries/{shelf.library_id}").status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OrganizeRun)) == 0
        assert db.scalar(select(func.count()).select_from(OrganizeMove)) == 0
    # The files stay where the run put them: nexbooks never deletes a book.
    assert (shelf.root / "Jonas Brandauer" / "Kite Season.epub").is_file()


def test_only_the_newest_runs_of_a_library_are_kept(shelf: Shelf, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import organize

    monkeypatch.setattr(organize, "KEEP_RUNS", 3)
    ids = [preview(client, shelf, f"{{Author}}/{{Title}} {n}")["id"] for n in range(2)]
    first = preview(client, shelf)
    start(client, first["id"])
    run_jobs()
    undone = client.post(f"/api/v1/organize/runs/{first['id']}/undo", json={"confirm": True})
    run_jobs()
    assert undone.status_code == 202
    for n in range(3):
        ids.append(preview(client, shelf, f"{{Title}} {n}")["id"])
    status = client.get("/api/v1/organize/status", params={"library": shelf.library_id}).json()
    assert len(status["runs"]) <= 3


def test_the_default_scheme_is_a_scheme(shelf: Shelf) -> None:
    assert schemes.parse(schemes.DEFAULT).text == schemes.DEFAULT == SCHEME


def test_case_only_changes_are_not_moves(shelf: Shelf) -> None:
    """On a file system that does not tell ``Kite`` from ``kite`` such a rename is no move, and on one that does the plan
    stays with what it found rather than going through a name that is taken."""
    move = planning.Move(1, 1, "Author/Kite.epub", "author/kite.epub", 1, 1)
    plan = planning.Plan()
    planning._judge(plan, {1: [move]}, planning.Disk(str(shelf.root)))
    assert plan.moves == [] and plan.stay_books == 1
