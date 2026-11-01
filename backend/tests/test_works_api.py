"""The books over the API: lists, filters, details, covers, series and people, each as the person may see them.

The rule under test: **what a person may not see is not there**. A list does not hold it, a count does not include it, a
cover does not show it, and asking for it by number answers exactly like asking for a number that was never given."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, BookFile, Edition
from app.services import covers

from .bookmaker import cover_image
from .conftest import make_account, new_client
from .library_helpers import book, books_folder, make_library, recording, scan


class World:
    """Two libraries and four people: tester (operator, both), rita (family only), sam (club only), tom (none)."""

    def __init__(self) -> None:
        self.family = books_folder("family")
        self.club = books_folder("club")
        book(self.family, "alpha.epub", title="Alpha", authors=("Ada Feldkamp",), language="de", series="Saga",
             series_pos=1, cover=cover_image((190, 30, 40)), subjects=("Roman",), isbn="9783999900100")
        book(self.family, "beta.epub", title="Beta", authors=("Ben Hollister",), language="en", series="Saga",
             series_pos=2, subjects=("Fantasy",))
        book(self.family, "gamma.epub", title="Gamma", authors=("Ada Feldkamp",), language="de", subjects=("Krimi",))
        # In the club: the recording of Alpha (the same work), and a book of its own.
        recording(self.club, "alpha/alpha.m4b", 300.0, title="Alpha", album="Alpha", artist="Ada Feldkamp",
                  narrator="Ben Hollister")
        book(self.club, "delta.epub", title="Delta", authors=("Ada Feldkamp",), language="de", subjects=("Roman",))
        self.family_id = make_library(self.family, "Family", scan=True)
        self.club_id = make_library(self.club, "Club", scan=True)
        self.rita = make_account("rita")
        self.sam = make_account("sam")
        self.tom = make_account("tom")
        with SessionLocal() as db:
            from app.models import LibraryMember

            tester = db.scalar(select(Account).where(Account.name == "tester"))
            assert tester is not None
            db.query(LibraryMember).delete()
            for user, library, right in ((tester.id, self.family_id, "manage"), (tester.id, self.club_id, "manage"),
                                         (self.rita.id, self.family_id, "read"), (self.sam.id, self.club_id, "read")):
                db.add(LibraryMember(library_id=library, user_id=user, right=right))
            db.commit()

    def work_id(self, title: str) -> int:
        with SessionLocal() as db:
            from app.models import Work

            return db.scalars(select(Work.id).where(Work.title == title)).one()


@pytest.fixture
def world(client: TestClient, operator: Account) -> World:
    return World()


def titles(answer) -> list[str]:  # type: ignore[no-untyped-def]
    assert answer.status_code == 200, answer.text
    return [item["title"] for item in answer.json()["items"]]


# ---- lists -----------------------------------------------------------------------------------------------------------


def test_each_person_sees_the_books_of_their_libraries(client: TestClient, world: World) -> None:
    assert sorted(titles(client.get("/api/v1/works"))) == ["Alpha", "Beta", "Delta", "Gamma"]
    with new_client(world.rita) as rita, new_client(world.sam) as sam, new_client(world.tom) as tom:
        assert sorted(titles(rita.get("/api/v1/works"))) == ["Alpha", "Beta", "Gamma"]
        assert sorted(titles(sam.get("/api/v1/works"))) == ["Alpha", "Delta"]
        assert tom.get("/api/v1/works").json() == {"items": [], "total": 0, "page": 1, "per_page": 60}


def test_a_work_in_two_libraries_shows_the_editions_of_the_libraries_of_the_person(client: TestClient,
                                                                                 world: World) -> None:
    def kinds(session: TestClient) -> list[str]:
        item = next(i for i in session.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
        return sorted(e["kind"] for e in item["editions"])

    assert kinds(client) == ["audio", "ebook"]
    with new_client(world.rita) as rita, new_client(world.sam) as sam:
        assert kinds(rita) == ["ebook"]
        assert kinds(sam) == ["audio"]
        item = next(i for i in sam.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
        assert item["audio_only"] is True and item["editions"][0]["narrators"] == ["Ben Hollister"]


def test_the_counts_and_the_statistics_leave_out_what_the_person_may_not_see(client: TestClient, world: World) -> None:
    stats = client.get("/api/v1/works/stats").json()
    assert (stats["works"], stats["editions"], stats["works_with_audio"], stats["works_with_ebook"]) == (4, 5, 1, 4)
    with new_client(world.rita) as rita, new_client(world.sam) as sam, new_client(world.tom) as tom:
        assert rita.get("/api/v1/works").json()["total"] == 3
        mine = rita.get("/api/v1/works/stats").json()
        assert (mine["works"], mine["editions"], mine["works_with_audio"], mine["audio_editions"]) == (3, 3, 0, 0)
        assert mine["genres"] == {"roman": 1, "fantasy": 1, "krimi": 1}
        club = sam.get("/api/v1/works/stats").json()
        assert (club["works"], club["editions"], club["works_with_audio"], club["works_with_ebook"]) == (2, 2, 1, 1)
        assert tom.get("/api/v1/works/stats").json()["works"] == 0
        # The language of a book no one of mine has is not even named.
        assert "en" not in sam.get("/api/v1/works/stats").json()["languages"]


def test_the_filters_and_the_sorts(client: TestClient, world: World) -> None:
    assert titles(client.get("/api/v1/works", params={"genre": "roman"})) == ["Alpha", "Delta"]
    assert titles(client.get("/api/v1/works", params={"kind": "audio"})) == ["Alpha"]
    assert titles(client.get("/api/v1/works", params={"language": "EN"})) == ["Beta"]
    assert titles(client.get("/api/v1/works", params={"library": world.club_id})) == ["Alpha", "Delta"]
    assert titles(client.get("/api/v1/works", params={"genre": "none"})) == []
    assert titles(client.get("/api/v1/works", params={"sort": "title"})) == ["Alpha", "Beta", "Delta", "Gamma"]
    # The shelf: by genre (novels, crime, fantasy), then author, series, volume.
    assert titles(client.get("/api/v1/works", params={"sort": "shelf"})) == ["Alpha", "Delta", "Gamma", "Beta"]
    assert titles(client.get("/api/v1/works", params={"sort": "added"}))[0] in {"Delta", "Gamma", "Beta", "Alpha"}
    # A kind and a language narrow the same edition: German recordings do not exist here.
    assert titles(client.get("/api/v1/works", params={"kind": "audio", "language": "de"})) == []
    for bad in ({"genre": "poetry"}, {"kind": "paper"}, {"sort": "random"}, {"page": 0}, {"per_page": 1000},
                {"language": "x"}, {"library": 0}):
        assert client.get("/api/v1/works", params=bad).status_code == 422, bad


def test_a_library_of_somebody_else_cannot_be_asked_for(client: TestClient, world: World) -> None:
    with new_client(world.rita) as rita:
        mine = rita.get("/api/v1/works", params={"library": world.club_id})
        nothing = rita.get("/api/v1/works", params={"library": 987})
        assert (mine.status_code, mine.json()) == (404, nothing.json())
        assert rita.get("/api/v1/works/stats", params={"library": world.club_id}).status_code == 404


def test_the_pages(client: TestClient, world: World) -> None:
    first = client.get("/api/v1/works", params={"per_page": 3, "sort": "title"}).json()
    second = client.get("/api/v1/works", params={"per_page": 3, "page": 2, "sort": "title"}).json()
    assert (first["total"], second["total"]) == (4, 4)
    assert [i["title"] for i in first["items"]] == ["Alpha", "Beta", "Delta"]
    assert [i["title"] for i in second["items"]] == ["Gamma"]
    assert client.get("/api/v1/works", params={"page": 99}).json()["items"] == []


def test_the_shape_of_a_book_in_a_list(client: TestClient, world: World) -> None:
    alpha = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
    assert [a["name"] for a in alpha["authors"]] == ["Ada Feldkamp"]
    assert alpha["series"]["name"] == "Saga" and alpha["series"]["pos"] == 1.0
    assert alpha["genre"] == "roman" and alpha["color"].startswith("#") and len(alpha["color"]) == 7
    assert alpha["cover_url"].startswith(f"/api/v1/works/{alpha['id']}/cover?v=")
    assert alpha["pattern"] in range(5) and 0 <= alpha["tall"] < 1 and isinstance(alpha["gold"], bool)
    assert alpha["pages"] > 0 and alpha["audio_only"] is False
    ebook = next(e for e in alpha["editions"] if e["kind"] == "ebook")
    assert ebook["formats"] == ["epub"] and ebook["language"] == "de"
    audio = next(e for e in alpha["editions"] if e["kind"] == "audio")
    assert audio["duration"] == 300 and audio["variant"] == "unabridged" and audio["variant_uncertain"] is True
    beta = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Beta")
    assert beta["cover_url"] is None and beta["color"].startswith("#")  # the interface draws it in this colour


# ---- one book --------------------------------------------------------------------------------------------------------


def test_a_book_in_detail(client: TestClient, world: World) -> None:
    alpha = client.get(f"/api/v1/works/{world.work_id('Alpha')}").json()
    assert alpha["title"] == "Alpha" and "description" in alpha
    ebook = next(e for e in alpha["editions"] if e["kind"] == "ebook")
    assert ebook["library_name"] == "Family" and ebook["isbn"] == "9783999900100" and ebook["title"] == "Alpha"
    assert [f["format"] for f in ebook["files"]] == ["epub"] and ebook["files"][0]["path"] == "alpha.epub"
    assert len(ebook["files"][0]["sha256"]) == 64 and ebook["files"][0]["size"] > 0
    assert [c["title"] for c in ebook["chapters"]] == ["One", "Two", "Three"]
    assert ebook["chapters"][0]["target"] == "text/c0.xhtml"
    audio = next(e for e in alpha["editions"] if e["kind"] == "audio")
    assert audio["library_name"] == "Club" and audio["files"][0]["duration"] == 300.0
    assert audio["narrators"] == ["Ben Hollister"] and len(audio["chapters"]) == 1


def test_a_book_the_person_may_not_see_is_not_there(client: TestClient, world: World) -> None:
    gamma, delta = world.work_id("Gamma"), world.work_id("Delta")
    with new_client(world.rita) as rita, new_client(world.sam) as sam:
        assert rita.get(f"/api/v1/works/{gamma}").status_code == 200
        hidden = sam.get(f"/api/v1/works/{gamma}")
        absent = sam.get("/api/v1/works/987654")
        assert (hidden.status_code, hidden.json()) == (404, absent.json())
        assert rita.get(f"/api/v1/works/{delta}").status_code == 404
        # The editions of another library inside a book that is seen are left out, files and chapters with them.
        detail = rita.get(f"/api/v1/works/{world.work_id('Alpha')}").json()
        assert [e["kind"] for e in detail["editions"]] == ["ebook"]
        assert "Club" not in str(detail)


def test_a_book_that_lost_its_files_is_not_shown_but_is_not_forgotten(client: TestClient, world: World) -> None:
    (world.family / "gamma.epub").unlink()
    scan(world.family_id)
    assert "Gamma" not in titles(client.get("/api/v1/works"))
    assert client.get(f"/api/v1/works/{world.work_id('Gamma')}").status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(Edition).where(Edition.missing_at.is_not(None))).all()  # kept for what hangs on it
    stats = client.get("/api/v1/works/stats").json()
    assert stats["works"] == 3 and stats["editions"] == 4


# ---- covers -----------------------------------------------------------------------------------------------------------


def test_the_cover_comes_with_headers_that_let_it_be_kept_and_asked_about(client: TestClient, world: World) -> None:
    alpha = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
    answer = client.get(alpha["cover_url"])
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/webp"
    assert answer.content[:4] == b"RIFF" and answer.content[8:12] == b"WEBP"
    assert "immutable" in answer.headers["cache-control"] and "private" in answer.headers["cache-control"]
    assert answer.headers["x-content-type-options"] == "nosniff"
    etag = answer.headers["etag"]
    again = client.get(alpha["cover_url"], headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    # Without the version in the address the picture may change: it is asked about each time.
    plain = client.get(f"/api/v1/works/{alpha['id']}/cover")
    assert plain.status_code == 200 and "immutable" not in plain.headers["cache-control"]


def test_a_new_cover_is_a_new_address(client: TestClient, world: World) -> None:
    before = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")["cover_url"]
    book(world.family, "alpha.epub", title="Alpha", authors=("Ada Feldkamp",), language="de", series="Saga",
         series_pos=1, cover=cover_image((30, 60, 190)), subjects=("Roman",), isbn="9783999900100")
    scan(world.family_id)
    after = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")["cover_url"]
    assert after != before and after.split("?")[0] == before.split("?")[0]
    assert client.get(after).status_code == 200


def test_the_cover_of_a_book_is_for_the_eyes_of_its_libraries_only(client: TestClient, world: World) -> None:
    """Alpha has a cover in the family's library only: the club's recording has none, and the club is shown none."""
    alpha = world.work_id("Alpha")
    with new_client(world.rita) as rita, new_client(world.sam) as sam, new_client(world.tom) as tom:
        assert rita.get(f"/api/v1/works/{alpha}/cover").status_code == 200
        sam_cover = sam.get(f"/api/v1/works/{alpha}/cover")
        assert sam_cover.status_code == 404 and sam_cover.json()["detail"]["code"] == "not_found"
        item = next(i for i in sam.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
        assert item["cover_url"] is None
        # Nor the colour of that cover: sam sees a colour of the genre's palette.
        family_item = next(i for i in rita.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
        assert family_item["cover_url"] is not None
        assert tom.get(f"/api/v1/works/{alpha}/cover").status_code == 404
        assert tom.get(f"/api/v1/works/{world.work_id('Delta')}/cover").status_code == 404


def test_the_file_name_of_a_cover_cannot_be_made_to_reach_another_file(client: TestClient, world: World) -> None:
    """Whatever the database says, only a name of the shape the scan makes is ever opened."""
    secret = Path(covers.folder()).parent / "secret.key"
    secret.write_text("TOPSECRET", encoding="utf-8")
    alpha = world.work_id("Alpha")
    for name in ("../secret.key", "..\\secret.key", "secret.key", "x.webp", "0" * 20 + ".png", "/" + "a" * 20 + ".webp"):
        with SessionLocal() as db:
            db.query(Edition).filter(Edition.cover != "").update({Edition.cover: name})
            db.commit()
        answer = client.get(f"/api/v1/works/{alpha}/cover")
        assert answer.status_code == 404 and b"TOPSECRET" not in answer.content, name
    for odd in ("..%2Fsecret.key", "..", "x.webp"):
        assert client.get(f"/api/v1/works/{alpha}/cover", params={"v": odd}).status_code == 404


# ---- series and people ------------------------------------------------------------------------------------------------


def test_a_series_with_the_volumes_the_person_sees(client: TestClient, world: World) -> None:
    alpha = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
    series_id = alpha["series"]["id"]
    full = client.get(f"/api/v1/series/{series_id}").json()
    assert full["name"] == "Saga" and [w["title"] for w in full["works"]] == ["Alpha", "Beta"]
    with new_client(world.sam) as sam, new_client(world.tom) as tom:
        # Sam sees Alpha (its recording) but the recording is in no series: only the e-book in the family's library
        # says "Saga", so for Sam there is no such series (not a series with one volume, and not Beta either).
        assert sam.get(f"/api/v1/series/{series_id}").status_code == 404
        assert tom.get(f"/api/v1/series/{series_id}").status_code == 404
        assert tom.get("/api/v1/series/9999").status_code == 404


def test_a_person_with_the_books_the_caller_sees(client: TestClient, world: World) -> None:
    alpha = next(i for i in client.get("/api/v1/works").json()["items"] if i["title"] == "Alpha")
    ada, ben = alpha["authors"][0]["id"], None
    full = client.get(f"/api/v1/people/{ada}").json()
    assert full["name"] == "Ada Feldkamp" and sorted(w["title"] for w in full["wrote"]) == [
        "Alpha", "Delta", "Gamma"]
    with new_client(world.rita) as rita, new_client(world.sam) as sam, new_client(world.tom) as tom:
        assert sorted(w["title"] for w in rita.get(f"/api/v1/people/{ada}").json()["wrote"]) == ["Alpha", "Gamma"]
        assert sorted(w["title"] for w in sam.get(f"/api/v1/people/{ada}").json()["wrote"]) == ["Alpha", "Delta"]
        assert tom.get(f"/api/v1/people/{ada}").status_code == 404
        # A narrator: Ben Hollister wrote Beta (family) and narrated Alpha (club).
        beta = next(i for i in rita.get("/api/v1/works").json()["items"] if i["title"] == "Beta")
        ben = beta["authors"][0]["id"]
        mine, theirs = rita.get(f"/api/v1/people/{ben}").json(), sam.get(f"/api/v1/people/{ben}").json()
        assert [w["title"] for w in mine["wrote"]] == ["Beta"] and mine["narrated"] == []
        assert theirs["wrote"] == [] and [w["title"] for w in theirs["narrated"]] == ["Alpha"]
        assert tom.get(f"/api/v1/people/{ben}").status_code == 404


# ---- ways in ----------------------------------------------------------------------------------------------------------


def test_everything_asks_for_a_sign_in(client: TestClient, world: World) -> None:
    with new_client() as nobody:
        for url in ("/api/v1/works", "/api/v1/works/stats", "/api/v1/works/1", "/api/v1/works/1/cover",
                    "/api/v1/series/1", "/api/v1/people/1"):
            assert nobody.get(url).status_code == 401, url


def test_a_device_and_a_key_see_what_their_account_sees(client: TestClient, world: World) -> None:
    with new_client(world.rita) as rita:
        token = rita.post("/api/v1/devices", json={"name": "phone"}).json()["token"]
    assert client.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 200
    with new_client(world.sam) as sam:
        assert sam.put("/api/v1/settings", json={"api_keys_allowed": True}).status_code == 403
    # The operator's own key reads what the operator is a member of.
    key = client.post("/api/v1/api-keys", json={"name": "dash"}).json()["secret"]
    with TestClient(client.app, base_url="http://testserver") as program:
        device = program.get("/api/v1/works", headers={"Authorization": f"Bearer {token}"})
        assert sorted(titles(device)) == ["Alpha", "Beta", "Gamma"]
        keyed = program.get("/api/v1/works", headers={"Authorization": f"Bearer {key}"})
        assert sorted(titles(keyed)) == ["Alpha", "Beta", "Delta", "Gamma"]
        # A program does not come from a web page.
        assert program.get("/api/v1/works", headers={"Authorization": f"Bearer {key}",
                                                     "Origin": "https://example.com"}).status_code == 403
        cover = program.get(next(i for i in device.json()["items"] if i["title"] == "Alpha")["cover_url"],
                            headers={"Authorization": f"Bearer {token}"})
        assert cover.status_code == 200


def test_the_files_are_listed_but_not_served(client: TestClient, world: World) -> None:
    """The files are listed; they are served by their own routes."""
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.endswith(("/download", "/stream")) for path in paths)
    with SessionLocal() as db:
        assert db.scalars(select(BookFile).limit(1)).first() is not None
