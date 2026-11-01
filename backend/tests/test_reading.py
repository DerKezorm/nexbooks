"""Reading in the browser, over the API: the bytes of a file with ``Range`` and ``ETag``, an edition for the
reader, where a person is in it (the newest report of all devices stays, with the clock put in by the test), and the
bookmarks. The rule under test, everywhere: **progress and bookmarks are the person's own**, and an edition of a library
the person is not a member of is one that does not exist."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import clock
from app.db import SessionLocal
from app.models import Account, BookFile, Bookmark, Edition, Library, LibraryMember, Progress
from app.services import apikeys
from app.services import progress as service

from .bookmaker import make_pdf
from .conftest import make_account, new_client
from .library_helpers import age, book, books_folder, make_library, run_jobs


class World:
    """Two libraries and four people: tester (operator, both), rita (family), sam (club), tom (none)."""

    def __init__(self) -> None:
        self.family = books_folder("family")
        self.club = books_folder("club")
        self.alpha = book(self.family, "alpha.epub", title="Alpha", authors=("Ada Feldkamp",), language="de",
                          chapters=("One", "Two", "Three", "Four"), text_bytes=40_000)
        pdf = make_pdf(self.family / "alpha.pdf", pages=4)
        age(pdf)
        book(self.club, "delta.epub", title="Delta", authors=("Ben Hollister",), language="en")
        self.family_id = make_library(self.family, "Family", scan=True)
        self.club_id = make_library(self.club, "Club", scan=True)
        self.rita = make_account("rita")
        self.sam = make_account("sam")
        self.tom = make_account("tom")
        with SessionLocal() as db:
            tester = db.scalar(select(Account).where(Account.name == "tester"))
            assert tester is not None
            self.tester_id = tester.id
            db.query(LibraryMember).delete()
            for user, library, right in ((tester.id, self.family_id, "manage"), (tester.id, self.club_id, "manage"),
                                         (self.rita.id, self.family_id, "read"), (self.sam.id, self.club_id, "read")):
                db.add(LibraryMember(library_id=library, user_id=user, right=right))
            db.commit()
        run_jobs()

    def edition(self, title: str) -> int:
        with SessionLocal() as db:
            return db.scalars(select(Edition.id).where(Edition.title == title)).one()

    def file(self, edition_id: int, fmt: str) -> int:
        with SessionLocal() as db:
            return db.scalars(select(BookFile.id).where(BookFile.edition_id == edition_id, BookFile.format == fmt)).one()


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def report(**over: object) -> dict[str, object]:
    return {"locator": "epubcfi(/6/4!/4/2)", "fraction": 0.25, "chapter": 2, "chapter_count": 4,
            "chapter_title": "Two", "device_key": "dev-a", "device": "Firefox on Linux", **over}


# ---- The bytes ------------------------------------------------------------------------------------------------------


def test_a_member_reads_the_bytes_of_a_file_whole_and_in_parts(world: World) -> None:
    edition = world.edition("Alpha")
    file_id = world.file(edition, "epub")
    with new_client(world.rita) as rita:
        whole = rita.get(f"/api/v1/files/{file_id}/content")
        assert whole.status_code == 200
        assert whole.content == world.alpha.read_bytes()
        assert whole.headers["accept-ranges"] == "bytes"
        assert whole.headers["content-type"] == "application/epub+zip"
        assert whole.headers["x-content-type-options"] == "nosniff"
        assert whole.headers["cache-control"] == "private, no-cache"
        # The bytes are data for the page's scripts: opened as a page by themselves they show nothing and run nothing.
        assert "sandbox" in whole.headers["content-security-policy"]
        assert "default-src 'none'" in whole.headers["content-security-policy"]
        part = rita.get(f"/api/v1/files/{file_id}/content", headers={"Range": "bytes=10-49"})
        assert part.status_code == 206 and part.content == world.alpha.read_bytes()[10:50]
        assert part.headers["content-range"] == f"bytes 10-49/{len(whole.content)}"
        tail = rita.get(f"/api/v1/files/{file_id}/content", headers={"Range": "bytes=-5"})
        assert tail.status_code == 206 and tail.content == world.alpha.read_bytes()[-5:]
        past = rita.get(f"/api/v1/files/{file_id}/content", headers={"Range": f"bytes={len(whole.content) + 5}-"})
        assert past.status_code == 416


def test_the_etag_spares_the_second_download_and_changes_with_the_file(world: World) -> None:
    file_id = world.file(world.edition("Alpha"), "epub")
    with new_client(world.rita) as rita:
        first = rita.get(f"/api/v1/files/{file_id}/content")
        tag = first.headers["etag"]
        again = rita.get(f"/api/v1/files/{file_id}/content", headers={"If-None-Match": tag})
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
        assert rita.get(f"/api/v1/files/{file_id}/content", headers={"If-None-Match": '"other", ' + tag}).status_code == 304
        assert rita.get(f"/api/v1/files/{file_id}/content", headers={"If-None-Match": '"other"'}).status_code == 200
        # The file changes on the disk: the tag changes, and the old one fetches the new bytes.
        world.alpha.write_bytes(world.alpha.read_bytes() + b"\0")
        changed = rita.get(f"/api/v1/files/{file_id}/content", headers={"If-None-Match": tag})
        assert changed.status_code == 200 and changed.headers["etag"] != tag


def test_a_file_of_a_library_one_is_not_in_is_a_file_that_does_not_exist(world: World) -> None:
    file_id = world.file(world.edition("Alpha"), "epub")
    club_file = world.file(world.edition("Delta"), "epub")
    unknown = file_id + club_file + 1000
    with new_client(world.sam) as sam, new_client(world.tom) as tom:
        nothing = sam.get(f"/api/v1/files/{unknown}/content")
        assert nothing.status_code == 404
        # Not a member, or no such file: the same answer, word for word.
        for client in (sam, tom):
            hidden = client.get(f"/api/v1/files/{file_id}/content")
            assert (hidden.status_code, hidden.json()) == (404, nothing.json())
        assert tom.get(f"/api/v1/files/{club_file}/content").status_code == 404
        assert sam.get(f"/api/v1/files/{club_file}/content").status_code == 200


def test_the_place_on_the_disk_never_comes_from_the_request(world: World) -> None:
    file_id = world.file(world.edition("Alpha"), "epub")
    with new_client(world.rita) as rita:
        for path in (f"/api/v1/files/{file_id}/content/../../etc/passwd", "/api/v1/files/..%2f..%2fsecret/content",
                     "/api/v1/files/0/content", "/api/v1/files/-1/content", f"/api/v1/files/{file_id}x/content"):
            assert rita.get(path).status_code in (404, 422), path
        # A file that is gone from the disk, or that was swapped for a link, is not served either.
        world.alpha.unlink()
        assert rita.get(f"/api/v1/files/{file_id}/content").status_code == 404


def test_a_device_and_a_key_read_the_bytes_and_a_page_of_another_site_does_not(world: World, client: TestClient) -> None:
    file_id = world.file(world.edition("Alpha"), "epub")
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    apikeys.forget()
    with new_client() as program:
        for secret in (token, key):
            got = program.get(f"/api/v1/files/{file_id}/content", headers=bearer(secret))
            assert got.status_code == 200 and got.content == world.alpha.read_bytes()
        # A key is for programs: a web page that holds one is refused (``Origin``).
        assert program.get(f"/api/v1/files/{file_id}/content",
                           headers={**bearer(key), "Origin": "https://example.com"}).status_code == 403
        assert program.get(f"/api/v1/files/{file_id}/content", headers=bearer("nbk_" + "z" * 43)).status_code == 401
        assert program.get(f"/api/v1/files/{file_id}/content").status_code == 401


# ---- An edition for the reader ----------------------------------------------------------------------------------------


def test_an_edition_for_the_reader_has_the_work_the_files_and_the_chapters(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        found = rita.get(f"/api/v1/editions/{edition}")
        assert found.status_code == 200
        body = found.json()
        assert body["work"]["title"] == "Alpha" and body["edition"]["id"] == edition
        assert [f["format"] for f in body["edition"]["files"]] == ["epub", "pdf"]
        assert [f["protected"] for f in body["edition"]["files"]] == [False, False]
        assert [c["title"] for c in body["edition"]["chapters"]] == ["One", "Two", "Three", "Four"]
        assert rita.get(f"/api/v1/editions/{world.edition('Delta')}").status_code == 404
        assert rita.get("/api/v1/editions/999999").json() == rita.get(f"/api/v1/editions/{world.edition('Delta')}").json()


# ---- Where the person is -----------------------------------------------------------------------------------------------


class Clock:
    """The server's clock, set by the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(clock, "now", lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def tick(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


def put(client: TestClient, edition: int, **over: object):  # type: ignore[no-untyped-def]
    return client.put(f"/api/v1/editions/{edition}/progress", json=report(**over))


def test_a_person_has_no_place_until_they_report_one(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        assert rita.get(f"/api/v1/editions/{edition}/progress").json() is None
        assert rita.get("/api/v1/progress").json() == []
        saved = put(rita, edition)
        assert saved.status_code == 200 and saved.json()["applied"] is True
        place = rita.get(f"/api/v1/editions/{edition}/progress").json()
        assert place["locator"] == "epubcfi(/6/4!/4/2)" and place["fraction"] == 0.25
        assert (place["chapter"], place["chapter_count"], place["chapter_title"]) == (2, 4, "Two")
        assert (place["device_key"], place["device"], place["unit"]) == ("dev-a", "Firefox on Linux", "chapter")
        assert place["work_id"] > 0 and place["edition_id"] == edition
        assert [p["edition_id"] for p in rita.get("/api/v1/progress").json()] == [edition]
        assert rita.get(f"/api/v1/progress?work={place['work_id']}").json()[0]["edition_id"] == edition
        assert rita.get("/api/v1/progress?work=999999").json() == []


def test_the_list_of_places_comes_a_page_at_a_time_and_says_how_many_there_are(operator: Account) -> None:
    folder = books_folder("paged")
    for n in range(5):
        book(folder, f"b{n}.epub", title=f"Paged {n}", authors=("Cy Dorn",), language="en")
    library = make_library(folder, "Paged", scan=True)
    rita = make_account("rita")
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library, user_id=rita.id, right="read"))
        db.commit()
    run_jobs()
    with SessionLocal() as db:
        editions = list(db.scalars(select(Edition.id).order_by(Edition.id)))
    assert len(editions) == 5
    with new_client(rita) as client:
        for edition in editions:
            assert put(client, edition).status_code == 200
        everything = client.get("/api/v1/progress")
        assert len(everything.json()) == 5 and everything.headers["x-total-count"] == "5"
        first = client.get("/api/v1/progress?limit=2")
        assert len(first.json()) == 2 and first.headers["x-total-count"] == "5"
        last = client.get("/api/v1/progress?limit=2&offset=4")
        assert len(last.json()) == 1 and last.headers["x-total-count"] == "5"
        seen = [p["edition_id"] for p in first.json()] + [p["edition_id"] for p in client.get("/api/v1/progress?limit=2&offset=2").json()]
        assert len(set(seen)) == 4
        assert client.get("/api/v1/progress?limit=0").status_code == 422
        assert client.get(f"/api/v1/progress?limit={service.LIST_LIMIT + 1}").status_code == 422
        assert client.get("/api/v1/progress?offset=-1").status_code == 422
        assert client.get("/api/v1/progress?work=999999").headers["x-total-count"] == "0"


def test_progress_is_the_person_s_own_and_the_operator_sees_none_of_it(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        assert put(rita, edition, fraction=0.5).status_code == 200
    with SessionLocal() as db:
        tester = db.scalar(select(Account).where(Account.name == "tester"))
    # The operator is a member of the library and reads the same edition; the place of the other person is not theirs.
    with new_client(tester) as operator:
        assert operator.get(f"/api/v1/editions/{edition}").status_code == 200
        assert operator.get(f"/api/v1/editions/{edition}/progress").json() is None
        assert operator.get("/api/v1/progress").json() == []
        # Their own place does not touch the other person's, nor the other way round.
        assert put(operator, edition, fraction=0.9, device_key="dev-o").json()["progress"]["fraction"] == 0.9
    with new_client(world.rita) as rita:
        assert rita.get(f"/api/v1/editions/{edition}/progress").json()["fraction"] == 0.5
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress)) == 2


def test_an_edition_one_cannot_see_takes_no_place_and_gives_none(world: World) -> None:
    club = world.edition("Delta")
    with new_client(world.rita) as rita, new_client(world.tom) as tom:
        for client in (rita, tom):
            assert put(client, club).status_code == 404
            assert client.get(f"/api/v1/editions/{club}/progress").status_code == 404
            assert client.get(f"/api/v1/editions/{club}/bookmarks").status_code == 404
            assert client.post(f"/api/v1/editions/{club}/bookmarks", json={"locator": "x", "fraction": 0}).status_code == 404
        assert rita.get(f"/api/v1/editions/{club}/progress").json() == tom.get(f"/api/v1/editions/{club}/progress").json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress)) == 0


def test_a_place_in_a_library_one_left_is_hidden_and_comes_back_with_the_membership(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition)
        with SessionLocal() as db:
            db.query(LibraryMember).filter_by(user_id=world.rita.id).delete()
            db.commit()
        assert rita.get(f"/api/v1/editions/{edition}/progress").status_code == 404
        assert rita.get("/api/v1/progress").json() == []
        with SessionLocal() as db:
            db.add(LibraryMember(library_id=world.family_id, user_id=world.rita.id, right="read"))
            db.commit()
        assert rita.get(f"/api/v1/editions/{edition}/progress").json()["fraction"] == 0.25


def test_a_place_says_how_long_ago_it_was_reached_so_that_no_clock_is_compared(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as client:
        saved = put(client, edition, age=30)
        assert saved.json()["progress"]["age"] == 30
        tick.advance(100)
        assert client.get(f"/api/v1/editions/{edition}/progress").json()["age"] == 130
        assert [p["age"] for p in client.get("/api/v1/progress").json()] == [130]


def test_the_newest_report_of_all_devices_stays(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as a, new_client(world.rita) as b:
        assert put(a, edition, device_key="dev-a", fraction=0.1).json()["applied"] is True
        tick.advance(10)
        # B reports later: B stands.
        saved = put(b, edition, device_key="dev-b", device="Safari on iPad", fraction=0.4, chapter=4)
        assert saved.json()["applied"] is True and saved.json()["progress"]["device_key"] == "dev-b"
        tick.advance(20)
        # A reports a place it left 45 seconds ago (it was offline): that is older than B's. B stays, and A is told.
        late = put(a, edition, device_key="dev-a", fraction=0.2, age=45)
        assert late.json()["applied"] is False
        assert late.json()["progress"]["device_key"] == "dev-b" and late.json()["progress"]["fraction"] == 0.4
        # A reports a place it is at now: A stands again.
        assert put(a, edition, device_key="dev-a", fraction=0.5, age=0).json()["applied"] is True
        assert a.get(f"/api/v1/editions/{edition}/progress").json()["device_key"] == "dev-a"


def test_a_wrong_clock_on_a_device_keeps_nobody_out(world: World, tick: Clock) -> None:
    """A device does not say what time it is, only how long ago: a clock a year ahead or behind changes nothing."""
    edition = world.edition("Alpha")
    with new_client(world.rita) as a, new_client(world.rita) as b:
        assert put(a, edition, device_key="dev-a", fraction=0.1).json()["applied"] is True
        tick.advance(5)
        assert put(b, edition, device_key="dev-b", fraction=0.2).json()["applied"] is True
        tick.advance(5)
        assert put(a, edition, device_key="dev-a", fraction=0.3).json()["applied"] is True
        # The field a clock would be sent in does not exist: it is ignored, the server counts for itself.
        extra = a.put(f"/api/v1/editions/{edition}/progress", json={**report(fraction=0.35), "at": "2999-01-01T00:00:00Z"})
        assert extra.status_code == 200 and extra.json()["progress"]["moved_at"].startswith("2026-10-13T12:00:10")


def test_the_later_arrival_wins_a_tie_and_an_age_is_capped(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as a, new_client(world.rita) as b:
        assert put(a, edition, device_key="dev-a", fraction=0.1).json()["applied"] is True
        assert put(b, edition, device_key="dev-b", fraction=0.2).json()["applied"] is True
        assert a.get(f"/api/v1/editions/{edition}/progress").json()["device_key"] == "dev-b"
        # An age of a year counts as a week at most, and a negative one is refused.
        old = put(a, edition, device_key="dev-a", age=365 * 24 * 3600)
        assert old.status_code == 200 and old.json()["applied"] is False
        assert put(a, edition, age=-1).status_code == 422
        tick.advance(8 * 24 * 3600)
        week = put(a, edition, device_key="dev-a", fraction=0.7, age=365 * 24 * 3600)
        assert week.json()["progress"]["moved_at"].startswith("2026-10-14T12:00:00")  # a week before the clock now


def test_devices_that_report_at_once_leave_one_place_and_it_is_the_newest(world: World, tick: Clock) -> None:
    edition = world.edition("Alpha")
    reports = [service.Report(locator=f"epubcfi(/6/{n})", fraction=n / 100, unit="chapter", chapter=n, chapter_count=40,
                              chapter_title=f"C{n}", device_key=f"dev-{n}", device=f"Device {n}", age=float(n))
               for n in range(1, 25)]
    barrier = threading.Barrier(len(reports))

    def go(one: service.Report) -> bool:
        with SessionLocal() as db:
            barrier.wait()
            return service.save(db, world.rita.id, edition, one)[0]

    with ThreadPoolExecutor(len(reports)) as pool:
        results = list(pool.map(go, reports))
    assert results.count(True) >= 1
    with SessionLocal() as db:
        rows = db.scalars(select(Progress)).all()
        assert len(rows) == 1
        # Everyone arrived at the same instant, so the smallest age is the newest place: report 1.
        assert rows[0].device_key == "dev-1" and rows[0].chapter == 1


def test_a_report_is_checked(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        for bad in ({"fraction": 1.5}, {"fraction": -0.1}, {"locator": ""}, {"locator": "x" * 1001},
                    {"chapter_title": "x" * 301}, {"device": "x" * 81}, {"device_key": "a b"}, {"device_key": "x" * 41},
                    {"unit": "line"}, {"chapter": -1}, {"age": "soon"}):
            answer = put(rita, edition, **bad)
            assert answer.status_code == 422, bad
        assert put(rita, edition, unit="page", locator="page:3", chapter=3, chapter_count=4).status_code == 200
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Progress)) == 1


def test_a_key_reads_the_place_and_cannot_set_it(world: World, client: TestClient) -> None:
    edition = world.edition("Alpha")
    assert put(client, edition).status_code == 200
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    apikeys.forget()
    with new_client() as program:
        read = program.get(f"/api/v1/editions/{edition}/progress", headers=bearer(key))
        assert read.status_code == 200 and read.json()["fraction"] == 0.25
        assert program.get("/api/v1/progress", headers=bearer(key)).status_code == 200
        assert program.get(f"/api/v1/editions/{edition}/bookmarks", headers=bearer(key)).status_code == 200
        for answer in (program.put(f"/api/v1/editions/{edition}/progress", json=report(), headers=bearer(key)),
                       program.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "a", "fraction": 0},
                                    headers=bearer(key)),
                       program.delete("/api/v1/bookmarks/1", headers=bearer(key))):
            assert answer.status_code in (401, 403), answer.text


def test_a_device_token_sets_the_place_like_a_browser_does(world: World, client: TestClient) -> None:
    edition = world.edition("Alpha")
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    with new_client() as phone:
        saved = phone.put(f"/api/v1/editions/{edition}/progress", json=report(), headers=bearer(token))
        assert saved.status_code == 200 and saved.json()["applied"] is True
        assert phone.get(f"/api/v1/editions/{edition}/progress", headers=bearer(token)).json()["fraction"] == 0.25


def test_a_change_names_its_tab(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        rita.headers.pop("X-Nexbooks-Client")
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=report()).status_code == 400
        assert rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "a", "fraction": 0}).status_code == 400


# ---- Bookmarks -------------------------------------------------------------------------------------------------------------


def test_bookmarks_are_set_listed_in_book_order_and_removed(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        late = rita.post(f"/api/v1/editions/{edition}/bookmarks",
                         json={"locator": "epubcfi(/6/8)", "fraction": 0.8, "chapter_title": "Four"})
        early = rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/2)", "fraction": 0.1})
        assert (late.status_code, early.status_code) == (201, 201)
        listed = rita.get(f"/api/v1/editions/{edition}/bookmarks").json()
        assert [b["locator"] for b in listed] == ["epubcfi(/6/2)", "epubcfi(/6/8)"]
        assert listed[1]["chapter_title"] == "Four" and listed[1]["edition_id"] == edition
        # A double click, or a second device: the same place gives the one bookmark.
        twice = rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/8)", "fraction": 0.8})
        assert twice.status_code == 200 and twice.json()["id"] == late.json()["id"]
        assert len(rita.get(f"/api/v1/editions/{edition}/bookmarks").json()) == 2
        assert rita.delete(f"/api/v1/bookmarks/{late.json()['id']}").status_code == 204
        assert rita.delete(f"/api/v1/bookmarks/{late.json()['id']}").status_code == 404
        assert [b["locator"] for b in rita.get(f"/api/v1/editions/{edition}/bookmarks").json()] == ["epubcfi(/6/2)"]


def test_bookmarks_are_the_person_s_own(world: World) -> None:
    edition = world.edition("Alpha")
    with SessionLocal() as db:
        tester = db.scalar(select(Account).where(Account.name == "tester"))
    with new_client(world.rita) as rita, new_client(tester) as operator:
        mine = rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/2)", "fraction": 0.1}).json()
        assert operator.get(f"/api/v1/editions/{edition}/bookmarks").json() == []
        # Another person's bookmark cannot be removed, and answers like one that is not there.
        assert operator.delete(f"/api/v1/bookmarks/{mine['id']}").status_code == 404
        assert operator.delete("/api/v1/bookmarks/99999").status_code == 404
        assert len(rita.get(f"/api/v1/editions/{edition}/bookmarks").json()) == 1
        # The same place set by both is two bookmarks, one each.
        other = operator.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/2)", "fraction": 0.1})
        assert other.status_code == 201 and other.json()["id"] != mine["id"]


def test_there_is_a_limit_to_the_bookmarks_of_a_book_and_it_says_so(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        with SessionLocal() as db:
            db.add_all(Bookmark(user_id=world.rita.id, edition_id=edition, locator=f"epubcfi(/6/{n})", fraction=0.0)
                       for n in range(service.MAX_BOOKMARKS))
            db.commit()
        over = rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/9/9)", "fraction": 0.1})
        assert (over.status_code, over.json()["detail"]["code"]) == (409, "bookmark_limit")
        # A place that is already marked is still answered.
        same = rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/7)", "fraction": 0.0})
        assert same.status_code == 200


def test_a_crowd_of_requests_at_the_limit_cannot_write_past_it(world: World) -> None:
    """The limit is kept by the statement that writes, not by a count made before it: twenty requests at the same moment
    with five places left end with exactly five more marks."""
    edition = world.edition("Alpha")
    room = 5
    with SessionLocal() as db:
        db.add_all(Bookmark(user_id=world.rita.id, edition_id=edition, locator=f"epubcfi(/6/{n})", fraction=0.0)
                   for n in range(service.MAX_BOOKMARKS - room))
        db.commit()
    start = threading.Barrier(20)

    def add(index: int) -> int:
        with new_client(world.rita) as client:
            start.wait()
            return client.post(f"/api/v1/editions/{edition}/bookmarks",
                               json={"locator": f"epubcfi(/9/{index})", "fraction": 0.9}).status_code

    with ThreadPoolExecutor(20) as pool:
        codes = list(pool.map(add, range(20)))
    with SessionLocal() as db:
        held = db.scalar(select(func.count()).select_from(Bookmark).where(Bookmark.user_id == world.rita.id))
    assert held == service.MAX_BOOKMARKS
    assert sorted(codes) == [201] * room + [409] * (20 - room)


def test_the_same_place_set_by_a_crowd_is_one_mark(world: World) -> None:
    edition = world.edition("Alpha")
    start = threading.Barrier(10)

    def add(_: int) -> int:
        with new_client(world.rita) as client:
            start.wait()
            return client.post(f"/api/v1/editions/{edition}/bookmarks",
                               json={"locator": "epubcfi(/6/2)", "fraction": 0.2}).status_code

    with ThreadPoolExecutor(10) as pool:
        codes = list(pool.map(add, range(10)))
    with SessionLocal() as db:
        held = db.scalar(select(func.count()).select_from(Bookmark).where(Bookmark.user_id == world.rita.id))
    assert held == 1
    assert codes.count(201) == 1 and codes.count(200) == 9


def test_a_bookmark_is_checked(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        for bad in ({"locator": "", "fraction": 0}, {"locator": "a", "fraction": 2}, {"locator": "a" * 1001, "fraction": 0},
                    {"locator": "a", "fraction": 0, "chapter_title": "x" * 301}, {"fraction": 0}):
            assert rita.post(f"/api/v1/editions/{edition}/bookmarks", json=bad).status_code == 422, bad


# ---- What goes with what ----------------------------------------------------------------------------------------------------


def test_removing_a_library_takes_the_progress_and_the_bookmarks_of_its_books_with_it(world: World, client: TestClient) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition)
        rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/2)", "fraction": 0.1})
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Progress)) == 1
    assert client.delete(f"/api/v1/libraries/{world.family_id}").status_code == 204
    with SessionLocal() as db:
        assert db.get(Library, world.family_id) is None
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.scalar(select(func.count()).select_from(Bookmark)) == 0


def test_deleting_an_account_takes_its_places_and_marks(world: World) -> None:
    edition = world.edition("Alpha")
    with new_client(world.rita) as rita:
        put(rita, edition)
        rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "epubcfi(/6/2)", "fraction": 0.1})
    with SessionLocal() as db:
        db.delete(db.get(Account, world.rita.id))
        db.commit()
        assert db.scalar(select(func.count()).select_from(Progress)) == 0
        assert db.scalar(select(func.count()).select_from(Bookmark)) == 0


# ---- Copy-protected books ----------------------------------------------------------------------------------------------


ENCRYPTION = (
    '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
    'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>'
    '<enc:CipherData><enc:CipherReference URI="OEBPS/text/c0.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>'
)


@pytest.mark.parametrize(("entries", "protected"), [
    ({}, False),
    ({"META-INF/encryption.xml": ENCRYPTION.format(algorithm="http://www.idpf.org/2008/embedding").encode()}, False),
    ({"META-INF/encryption.xml": ENCRYPTION.format(algorithm="http://ns.adobe.com/pdf/enc#RC").encode()}, False),
    ({"META-INF/encryption.xml": ENCRYPTION.format(algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc").encode()}, True),
    ({"META-INF/rights.xml": b"<rights/>"}, True),
    ({"META-INF/encryption.xml": b"<broken"}, False),
    # The rights file of a Kobo book lies in the root of the archive and names no algorithm anywhere else.
    ({"rights.xml": b"<?xml version='1.0'?><kdrm><sign>AAAA</sign></kdrm>"}, True),
    ({"rights.xml": b"<?xml version='1.0'?><note>a licence note of a publisher</note>"}, False),
    ({"META-INF/license.lcpl": b"{}"}, True),
    ({"META-INF/sinf.xml": b"<sinf/>"}, True),
])
def test_a_copy_protected_epub_says_so(tmp_path: Path, entries: dict[str, bytes], protected: bool) -> None:
    from app.services.readers import read_file

    from .bookmaker import make_epub

    make_epub(tmp_path / "p.epub", extra_entries=entries)
    assert read_file(str(tmp_path), "p.epub", "epub")["drm"] is protected


@pytest.mark.parametrize(("scrambled", "protected"), [((), False), ((1,), True), ((0, 1, 2), True)])
def test_an_epub_whose_chapters_are_no_markup_is_protected_whatever_the_archive_says(
        tmp_path: Path, scrambled: tuple[int, ...], protected: bool) -> None:
    """A protection that names itself nowhere (no encryption file, no rights file) shows in the chapters: they are not
    markup. A reader that opened such a book would show noise."""
    from app.services.readers import read_file

    from .bookmaker import make_epub

    make_epub(tmp_path / "s.epub", scrambled=scrambled)
    assert read_file(str(tmp_path), "s.epub", "epub")["drm"] is protected


def test_the_files_of_a_protected_book_are_marked_and_can_still_be_downloaded(operator: Account, client: TestClient) -> None:
    from .bookmaker import make_mobi

    folder = books_folder("protected")
    make_mobi(folder / "locked.azw3", title="Locked", encryption=2)
    age(folder / "locked.azw3")
    book(folder, "free.epub", title="Free")
    library = make_library(folder, "P", scan=True)
    with SessionLocal() as db:
        db.add(LibraryMember(library_id=library, user_id=operator.id, right="read"))
        db.commit()
    found = {w["title"]: w for w in client.get("/api/v1/works").json()["items"]}
    locked = client.get(f"/api/v1/works/{found['Locked']['id']}").json()["editions"][0]["files"][0]
    free = client.get(f"/api/v1/works/{found['Free']['id']}").json()["editions"][0]["files"][0]
    assert (locked["protected"], free["protected"]) == (True, False)
    assert client.get(f"/api/v1/files/{locked['id']}/download").status_code == 200
