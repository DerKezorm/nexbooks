"""Listening in the browser, over the API: a recording of several parts as one line of time, its chapters, the
bytes of each part with ``Range``, where a person is in it (a time, the newest report of all devices stays), bookmarks
with a note, and the player's choices in the profile.

The rules under test: **the place in a recording is the person's own** (the operator sees none of it), **the e-book and
the recording of a book keep a place each** (nothing moves the one when the other moves), and a
recording of a library the person is not in is one that does not exist."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BookFile, Edition, LibraryMember
from app.services import apikeys

from .conftest import make_account, new_client
from .library_helpers import book, books_folder, make_library, recording, run_jobs
from .test_reading import Clock


class World:
    """One library with a book that is an e-book and a recording, a recording of three parts and one with chapters inside;
    a second library of the club. tester (operator) is in both, rita in the family's, sam in the club's."""

    def __init__(self) -> None:
        self.family = books_folder("listen-family")
        self.club = books_folder("listen-club")
        # The same book twice: an EPUB and an MP3 of the same title and author.
        book(self.family, "Harbour/harbour.epub", title="The Quiet Harbour", authors=("Ada Feldkamp",), language="en",
             chapters=("One", "Two", "Three"))
        recording(self.family, "Harbour Audio/harbour.mp3", 30.0, album="The Quiet Harbour", artist="Ada Feldkamp",
                  title="The Quiet Harbour", narrator="Ben Hollister",
                  chapters=[("Arrival", 0.0), ("The Pier", 10.0), ("Night", 20.0)])
        # A recording of three parts in one folder: one edition, one line of time.
        self.parts = [recording(self.family, f"Seven Bridges/{n:02d}.mp3", 20.0, album="Seven Bridges",
                                artist="Clara Wendt", title=f"Part {n}", track=n) for n in (1, 2, 3)]
        recording(self.club, "Delta/delta.mp3", 12.0, album="Delta Tapes", artist="Jonas Brandauer", title="Delta")
        self.family_id = make_library(self.family, "Family", scan=True)
        self.club_id = make_library(self.club, "Club", scan=True)
        self.rita = make_account("rita")
        self.sam = make_account("sam")
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

    def edition(self, title: str, kind: str = "audio") -> int:
        with SessionLocal() as db:
            return db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == kind)).one()

    def files(self, edition_id: int) -> list[BookFile]:
        with SessionLocal() as db:
            rows = list(db.scalars(select(BookFile).where(BookFile.edition_id == edition_id).order_by(BookFile.part)))
            for row in rows:
                db.expunge(row)
            return rows


@pytest.fixture
def world(operator: Account) -> World:  # type: ignore[override]
    return World()


@pytest.fixture
def tick(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch)


def place(seconds: float, **over: object) -> dict[str, object]:
    return {"locator": f"time:{seconds:.3f}", "fraction": 0.5, "unit": "time", "chapter": 2, "chapter_count": 3,
            "chapter_title": "The Pier", "device_key": "dev-a", "device": "Firefox on Linux", **over}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- One line of time ---------------------------------------------------------------------------------------------------


def test_a_recording_of_three_parts_is_one_edition_whose_chapters_follow_the_parts(world: World) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        body = rita.get(f"/api/v1/editions/{edition}").json()["edition"]
    assert body["kind"] == "audio"
    files = body["files"]
    assert [f["part"] for f in files] == [1, 2, 3] and [f["format"] for f in files] == ["mp3"] * 3
    durations = [f["duration"] for f in files]
    assert all(19.5 < d < 20.5 for d in durations), durations
    # The length of the edition is the sum of its parts, and each part's chapter starts where the parts before it end:
    # the player puts the parts end to end on one line of time.
    assert abs(body["duration"] - sum(durations)) <= 1
    starts = [c["start"] for c in body["chapters"]]
    assert starts[0] == 0 and starts == pytest.approx([0, durations[0], durations[0] + durations[1]], abs=0.002)
    assert [c["title"] for c in body["chapters"]] == ["Part 1", "Part 2", "Part 3"]


def test_the_chapters_inside_one_file_are_read_with_their_starts(world: World) -> None:
    with new_client(world.rita) as rita:
        body = rita.get(f"/api/v1/editions/{world.edition('The Quiet Harbour')}").json()["edition"]
    assert [(c["title"], c["start"]) for c in body["chapters"]] == [("Arrival", 0), ("The Pier", 10), ("Night", 20)]
    assert body["narrators"] == ["Ben Hollister"]


def test_every_part_is_played_with_range_across_the_border_between_two_parts(world: World) -> None:
    """The player asks the end of one part and the start of the next (the next is loaded before the first ends)."""
    first, second, _ = world.files(world.edition("Seven Bridges"))
    with new_client(world.rita) as rita:
        size = first.size
        end = rita.get(f"/api/v1/files/{first.id}/content", headers={"Range": f"bytes={size - 1000}-"})
        assert end.status_code == 206 and end.content == world.parts[0].read_bytes()[-1000:]
        assert end.headers["content-range"] == f"bytes {size - 1000}-{size - 1}/{size}"
        assert end.headers["content-type"] == "audio/mpeg"
        start = rita.get(f"/api/v1/files/{second.id}/content", headers={"Range": "bytes=0-4095"})
        assert start.status_code == 206 and start.content == world.parts[1].read_bytes()[:4096]
        # A player that asks for the rest of a part from where it stands, and one that asks for a few pieces at once.
        rest = rita.get(f"/api/v1/files/{second.id}/content", headers={"Range": "bytes=100000-"})
        assert rest.status_code == 206 and rest.content == world.parts[1].read_bytes()[100000:]
        pieces = rita.get(f"/api/v1/files/{second.id}/content", headers={"Range": "bytes=0-9,100-109"})
        assert pieces.status_code == 206 and pieces.headers["content-type"].startswith("multipart/byteranges")
        # More pieces than a player ever asks for get the file once, as it is: no answer grows past the file.
        many = ",".join(f"{n * 10}-{n * 10 + 1}" for n in range(150))
        whole = rita.get(f"/api/v1/files/{second.id}/content", headers={"Range": f"bytes={many}"})
        assert whole.status_code == 200 and whole.content == world.parts[1].read_bytes()


def test_a_recording_of_another_library_does_not_exist_for_a_person_nor_its_parts(world: World) -> None:
    delta = world.edition("Delta Tapes")
    part = world.files(delta)[0].id
    with new_client(world.rita) as rita:
        for path in (f"/api/v1/editions/{delta}", f"/api/v1/files/{part}/content", f"/api/v1/editions/{delta}/progress",
                     f"/api/v1/editions/{delta}/bookmarks"):
            assert rita.get(path).status_code == 404, path
        assert rita.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-1"}).status_code == 404
        assert rita.put(f"/api/v1/editions/{delta}/progress", json=place(1)).status_code == 404
    with new_client(world.sam) as sam:
        assert sam.get(f"/api/v1/files/{part}/content", headers={"Range": "bytes=0-1"}).status_code == 206


def test_a_device_streams_a_recording_and_a_key_reads_it_but_sets_no_place(world: World, client: TestClient) -> None:
    edition = world.edition("Seven Bridges")
    part = world.files(edition)[0]
    token = client.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    key = client.post("/api/v1/api-keys", json={"name": "car"}).json()["secret"]
    apikeys.forget()
    with new_client() as program:
        for secret in (token, key):
            got = program.get(f"/api/v1/files/{part.id}/content", headers={**bearer(secret), "Range": "bytes=0-99"})
            assert got.status_code == 206 and got.content == world.parts[0].read_bytes()[:100]
        assert program.put(f"/api/v1/editions/{edition}/progress", json=place(5), headers=bearer(token)).status_code == 200
        assert program.put(f"/api/v1/editions/{edition}/progress", json=place(6), headers=bearer(key)).status_code == 401
        assert program.get(f"/api/v1/editions/{edition}/progress", headers=bearer(key)).json()["position"] == 5


# ---- The place --------------------------------------------------------------------------------------------------------


def test_the_place_in_a_recording_is_a_time(world: World) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        saved = rita.put(f"/api/v1/editions/{edition}/progress", json=place(25.5))
        assert saved.status_code == 200 and saved.json()["applied"] is True
        got = rita.get(f"/api/v1/editions/{edition}/progress").json()
        assert (got["locator"], got["position"], got["unit"], got["chapter"]) == ("time:25.500", 25.5, "time", 2)


@pytest.mark.parametrize("wrong", [
    {"locator": "epubcfi(/6/4!/4/2)"}, {"locator": "time:abc"}, {"locator": "time:-3"}, {"locator": "time:1e9"},
    {"locator": "time:12.12345"}, {"locator": "time: 12"}, {"unit": "chapter"}, {"unit": "page"},
    {"locator": "time:600"},
])
def test_a_place_that_is_no_time_inside_the_recording_is_refused(world: World, wrong: dict[str, object]) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        answer = rita.put(f"/api/v1/editions/{edition}/progress", json=place(10, **wrong))
        assert answer.status_code == 422, wrong
        assert rita.get(f"/api/v1/editions/{edition}/progress").json() is None


def test_a_time_is_no_place_in_an_e_book(world: World) -> None:
    ebook = world.edition("The Quiet Harbour", "ebook")
    with new_client(world.rita) as rita:
        for wrong in ({"locator": "time:10", "unit": "chapter"}, {"locator": "epubcfi(/6/2)", "unit": "time"}):
            body = {**place(10), **wrong}
            assert rita.put(f"/api/v1/editions/{ebook}/progress", json=body).status_code == 422, wrong
        assert rita.post(f"/api/v1/editions/{ebook}/bookmarks",
                         json={"locator": "time:3", "fraction": 0.1}).status_code == 422


def test_the_e_book_and_the_recording_of_a_book_keep_a_place_each(world: World) -> None:
    ebook = world.edition("The Quiet Harbour", "ebook")
    audio = world.edition("The Quiet Harbour")
    with new_client(world.rita) as rita:
        work = rita.get(f"/api/v1/editions/{audio}").json()["work"]
        # One book with two editions: the twins of the page of the book.
        assert sorted(e["kind"] for e in work["editions"]) == ["audio", "ebook"]
        assert rita.get(f"/api/v1/editions/{ebook}").json()["work"]["id"] == work["id"]
        assert rita.put(f"/api/v1/editions/{ebook}/progress", json={
            "locator": "epubcfi(/6/6!/4/2)", "fraction": 0.66, "chapter": 2, "chapter_count": 3,
            "device_key": "dev-a"}).status_code == 200
        assert rita.put(f"/api/v1/editions/{audio}/progress", json=place(4, fraction=0.13, chapter=1)).status_code == 200
        places = {p["edition_id"]: p for p in rita.get(f"/api/v1/progress?work={work['id']}").json()}
        assert places[ebook]["fraction"] == 0.66 and places[ebook]["position"] is None
        assert places[audio]["fraction"] == 0.13 and places[audio]["position"] == 4
        # Listening on does not move the e-book, reading on does not move the recording.
        assert rita.put(f"/api/v1/editions/{audio}/progress", json=place(25, fraction=0.83)).status_code == 200
        assert rita.get(f"/api/v1/editions/{ebook}/progress").json()["fraction"] == 0.66
        assert rita.put(f"/api/v1/editions/{ebook}/progress", json={
            "locator": "epubcfi(/6/8!/4/2)", "fraction": 0.9, "device_key": "dev-a"}).status_code == 200
        assert rita.get(f"/api/v1/editions/{audio}/progress").json()["position"] == 25


def test_the_place_in_a_recording_is_the_person_s_own_and_the_operator_sees_none_of_it(
        world: World, client: TestClient) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=place(30)).status_code == 200
        assert rita.post(f"/api/v1/editions/{edition}/bookmarks",
                         json={"locator": "time:31", "fraction": 0.5, "note": "the bell"}).status_code == 201
    # The operator is in the library too and sees the recording, but neither rita's place nor her marks.
    assert client.get(f"/api/v1/editions/{edition}").status_code == 200
    assert client.get(f"/api/v1/editions/{edition}/progress").json() is None
    assert client.get("/api/v1/progress").json() == []
    assert client.get(f"/api/v1/editions/{edition}/bookmarks").json() == []
    # The operator's own place is theirs and leaves rita's as it was.
    assert client.put(f"/api/v1/editions/{edition}/progress", json=place(2)).status_code == 200
    with new_client(world.rita) as rita:
        assert rita.get(f"/api/v1/editions/{edition}/progress").json()["position"] == 30


def test_the_newest_place_of_two_devices_stays_and_a_stale_one_is_told_the_newer(world: World, tick: Clock) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        assert rita.put(f"/api/v1/editions/{edition}/progress", json=place(40, device_key="phone")).json()["applied"]
        tick.advance(30)
        # The tablet was at 12 s twenty minutes ago (it was offline): it loses, and hears where the phone is.
        late = rita.put(f"/api/v1/editions/{edition}/progress",
                        json=place(12, device_key="tablet", device="Safari on iPad", age=1200)).json()
        assert late["applied"] is False and late["progress"]["position"] == 40
        assert late["progress"]["device_key"] == "phone" and late["progress"]["age"] == 30
        tick.advance(5)
        newer = rita.put(f"/api/v1/editions/{edition}/progress", json=place(41.5, device_key="tablet")).json()
        assert newer["applied"] is True and newer["progress"]["position"] == 41.5


# ---- Bookmarks with a note ------------------------------------------------------------------------------------------------


def test_a_bookmark_in_a_recording_keeps_a_time_and_a_note_that_can_be_changed(world: World, client: TestClient) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        made = rita.post(f"/api/v1/editions/{edition}/bookmarks",
                         json={"locator": "time:21.250", "fraction": 0.35, "chapter_title": "Part 2",
                               "note": "  where the bridge sings  "})
        assert made.status_code == 201
        mark = made.json()
        assert (mark["position"], mark["note"], mark["chapter_title"]) == (21.25, "where the bridge sings", "Part 2")
        # Twice at the same place: the one mark (a double click), and its note is not overwritten by the second call.
        again = rita.post(f"/api/v1/editions/{edition}/bookmarks",
                          json={"locator": "time:21.250", "fraction": 0.35, "note": "other"})
        assert again.status_code == 200 and again.json()["id"] == mark["id"] and again.json()["note"] == mark["note"]
        changed = rita.patch(f"/api/v1/bookmarks/{mark['id']}", json={"note": "the bell"})
        assert changed.status_code == 200 and changed.json()["note"] == "the bell"
        assert rita.get(f"/api/v1/editions/{edition}/bookmarks").json()[0]["note"] == "the bell"
        assert rita.patch(f"/api/v1/bookmarks/{mark['id']}", json={"note": "x" * 501}).status_code == 422
        assert rita.post(f"/api/v1/editions/{edition}/bookmarks",
                         json={"locator": "time:9999", "fraction": 0.1}).status_code == 422
        assert rita.post(f"/api/v1/editions/{edition}/bookmarks",
                         json={"locator": "time:3", "fraction": 0.1, "note": "x" * 501}).status_code == 422
    # Another person's mark is a mark that does not exist, to change as to remove: for the operator, who sees the book,
    # as for sam, who does not.
    assert client.patch(f"/api/v1/bookmarks/{mark['id']}", json={"note": "mine"}).status_code == 404
    assert client.delete(f"/api/v1/bookmarks/{mark['id']}").status_code == 404
    with new_client(world.sam) as sam:
        assert sam.patch(f"/api/v1/bookmarks/{mark['id']}", json={"note": "mine"}).status_code == 404
        assert sam.delete(f"/api/v1/bookmarks/{mark['id']}").status_code == 404
    with new_client(world.rita) as rita:
        assert rita.get(f"/api/v1/editions/{edition}/bookmarks").json()[0]["note"] == "the bell"


def test_a_note_cannot_be_changed_in_a_book_one_no_longer_sees(world: World) -> None:
    edition = world.edition("Seven Bridges")
    with new_client(world.rita) as rita:
        mark = rita.post(f"/api/v1/editions/{edition}/bookmarks", json={"locator": "time:5", "fraction": 0.1}).json()
    with SessionLocal() as db:
        db.query(LibraryMember).filter(LibraryMember.user_id == world.rita.id).delete()
        db.commit()
    with new_client(world.rita) as rita:
        assert rita.patch(f"/api/v1/bookmarks/{mark['id']}", json={"note": "x"}).status_code == 404


# ---- The player's choices ---------------------------------------------------------------------------------------------


def test_the_speed_the_jumps_and_the_step_back_are_kept_with_the_account(client: TestClient, account: Account) -> None:
    profile = client.get("/api/v1/auth/me").json()["profile"]
    assert (profile["player_speed"], profile["player_back"], profile["player_ahead"], profile["player_rewind"]) == (
        100, 15, 30, True)
    saved = client.put("/api/v1/me/preferences", json={"player_speed": 135, "player_back": 10, "player_ahead": 60,
                                                       "player_rewind": False})
    assert saved.status_code == 200
    assert (saved.json()["player_speed"], saved.json()["player_rewind"]) == (135, False)
    for speed in (50, 55, 300):
        assert client.put("/api/v1/me/preferences", json={"player_speed": speed}).status_code == 200, speed
    for wrong in ({"player_speed": 1.25}, {"player_speed": 47}, {"player_speed": 305}, {"player_speed": 45},
                  {"player_speed": True}, {"player_speed": "100"}, {"player_back": 7}, {"player_ahead": 0},
                  {"player_rewind": 1}):
        answer = client.put("/api/v1/me/preferences", json=wrong)
        assert answer.status_code == 422 and answer.json()["detail"]["code"] == "bad_preference", wrong
    assert client.get("/api/v1/auth/me").json()["profile"]["player_speed"] == 300


def test_the_player_s_choices_are_not_shown_to_the_operator(client: TestClient, account: Account) -> None:
    with new_client(make_account("mia")) as mia:
        assert mia.put("/api/v1/me/preferences", json={"player_speed": 175}).status_code == 200
    listed = client.get("/api/v1/accounts").json()
    assert listed and all("profile" not in row and "player_speed" not in row for row in listed)


def test_the_parts_of_a_recording_are_found_on_the_disk_by_the_database_only(world: World, tmp_path: Path) -> None:
    """A part whose file went away is not served, and the request cannot name another file."""
    first = world.files(world.edition("Seven Bridges"))[0]
    with new_client(world.rita) as rita:
        assert rita.get(f"/api/v1/files/{first.id}/content?path=../../etc/passwd").content == world.parts[0].read_bytes()
        world.parts[0].unlink()
        assert rita.get(f"/api/v1/files/{first.id}/content", headers={"Range": "bytes=0-9"}).status_code == 404
