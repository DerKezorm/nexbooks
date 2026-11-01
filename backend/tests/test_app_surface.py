"""The surface of the iPhone app: who asks, what each address answers, the rights at every address, and that
no answer names a place on the disk.

The surface is the same services as the browser's, so the rights are the browser's: a book in a library the person is
not a member of is one that does not exist (404), at every address. Only a device token opens it: a revoked one, an API
key, a browser's cookie or nothing at all is 401.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import __version__, middleware
from app.db import SessionLocal
from app.models import BookFile, Chapter, Device, Work
from app.routers.app1 import FEATURES, PROMISED
from app.security import brake
from app.services import apikeys, devices

from .app_helpers import HIDDEN, Shelf, app_of, device_token, make_shelf, sync
from .conftest import make_account, new_client


@pytest.fixture
def shelf() -> Shelf:
    return make_shelf()


def file_of(edition: int) -> BookFile:
    with SessionLocal() as db:
        row = db.scalar(select(BookFile).where(BookFile.edition_id == edition, BookFile.role == "book")
                        .order_by(BookFile.id))
        assert row is not None
        db.expunge(row)
        return row


# --- Who asks ---------------------------------------------------------------------------------------------------------


def test_me_names_the_account_its_libraries_the_server_and_what_it_can_do(shelf: Shelf) -> None:
    answer = shelf.ben_app.get("/api/app/1/me")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["account"] == {"id": shelf.ben.id, "name": "ben", "language": ""}
    assert [(item["name"], item["kind"], item["right"]) for item in body["libraries"]] == [
        ("Books", "both", "read"), ("Private", "both", "read")]
    assert body["server"] == {"version": __version__}
    assert body["features"] == list(FEATURES) and "library" in body["features"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,}", body["installation_id"])
    # The same name for ever, whoever asks.
    assert shelf.ada_app.get("/api/app/1/me").json()["installation_id"] == body["installation_id"]
    assert [item["name"] for item in shelf.ada_app.get("/api/app/1/me").json()["libraries"]] == ["Books"]


def _every_address(edition: int, work: int, file_id: int) -> list[tuple[str, str, Any]]:
    """Every address of the surface that wants a token."""
    return [
        ("GET", "/api/app/1/me", None),
        ("GET", "/api/app/1/library", None),
        ("GET", f"/api/app/1/works/{work}", None),
        ("GET", f"/api/app/1/works/{work}/cover", None),
        ("GET", f"/api/app/1/files/{file_id}/content", None),
        ("GET", "/api/app/1/progress", None),
        ("PUT", f"/api/app/1/editions/{edition}/progress", {"locator": "epubcfi(/6/2)", "percent": 1}),
        ("GET", f"/api/app/1/editions/{edition}/bookmarks", None),
        ("POST", f"/api/app/1/editions/{edition}/bookmarks", {"locator": "epubcfi(/6/4)", "fraction": 0.1}),
        ("PATCH", "/api/app/1/bookmarks/1", {"note": "x"}),
        ("DELETE", "/api/app/1/bookmarks/1", None),
        ("POST", f"/api/app/1/editions/{edition}/finished", {}),
        ("GET", "/api/app/1/history", None),
        ("DELETE", "/api/app/1/history/1", None),
    ]


def _addresses(shelf: Shelf) -> list[tuple[str, str, Any]]:
    edition = shelf.editions["Kite Season/ebook"]
    return _every_address(edition, shelf.works["The Quiet Orchard"], file_of(edition).id)


def test_every_address_but_the_two_that_give_a_token_is_listed_here() -> None:
    listed = {"/api/app/1" + re.sub(r"/\d+", "/{}", path.removeprefix("/api/app/1"))
              for _method, path, _body in _every_address(7, 7, 7)}
    promised = {re.sub(r"\{[^}]+\}", "{}", path) for path in PROMISED if "/pairing/" not in path}
    assert listed == promised


def test_without_a_device_token_every_address_is_401(shelf: Shelf, client: TestClient) -> None:
    revoked = device_token(shelf.ada, "Lost phone")
    with SessionLocal() as db:
        uid = db.scalar(select(Device.uid).where(Device.name == "Lost phone"))
        assert uid is not None and devices.revoke(db, shelf.ada.id, uid)
    browser = new_client(shelf.ada)
    key = make_key(shelf)
    for method, path, body in _addresses(shelf):
        for who, caller, code in (
                ("revoked", app_of(revoked), "token_invalid"),
                ("nobody", TestClient(client.app, base_url="http://testserver"), "sign_in_required"),
                ("a browser", browser, "device_token_required")):
            # A wrong token counts against the sender like a wrong password; here every address is asked once.
            brake.forget()
            middleware.forget_gate()
            answer = caller.request(method, path, json=body)
            if who == "nobody" and method != "GET":
                # Without a token a change is a browser's, and a browser's change must name its tab first.
                assert answer.status_code == 400 and answer.json()["detail"]["code"] == "client_required", path
                continue
            assert answer.status_code == 401, (who, method, path, answer.status_code)
            assert answer.json()["detail"]["code"] == code, (who, method, path, answer.json())
        answer = app_of(key).request(method, path, json=body)
        assert answer.status_code == 401 and answer.json()["detail"]["code"] == "token_wrong_kind", (method, path)


def make_key(shelf: Shelf) -> str:
    with SessionLocal() as db:
        from app.models import Account

        row = db.get(Account, shelf.ada.id)
        assert row is not None
        _key, token = apikeys.make(db, row, "script", "read")
    return token


def test_a_book_of_another_library_is_one_that_does_not_exist_at_every_address(shelf: Shelf) -> None:
    secret = shelf.editions["A Secret Shelf/ebook"]
    work = shelf.works["A Secret Shelf"]
    # Ben sees it; his bookmark and history entry are his alone.
    mark = shelf.ben_app.post(f"/api/app/1/editions/{secret}/bookmarks", json={"locator": "epubcfi(/6/2)",
                                                                               "fraction": 0.2})
    assert mark.status_code == 201, mark.text
    entry = shelf.ben_app.post(f"/api/app/1/editions/{secret}/finished", json={})
    assert entry.status_code == 201, entry.text
    for method, path, body in [
            ("GET", f"/api/app/1/works/{work}", None),
            ("GET", f"/api/app/1/works/{work}/cover", None),
            ("GET", f"/api/app/1/files/{file_of(secret).id}/content", None),
            ("PUT", f"/api/app/1/editions/{secret}/progress", {"locator": "epubcfi(/6/2)", "percent": 5}),
            ("GET", f"/api/app/1/editions/{secret}/bookmarks", None),
            ("POST", f"/api/app/1/editions/{secret}/bookmarks", {"locator": "epubcfi(/6/2)", "fraction": 0.1}),
            ("PATCH", f"/api/app/1/bookmarks/{mark.json()['id']}", {"note": "mine now"}),
            ("DELETE", f"/api/app/1/bookmarks/{mark.json()['id']}", None),
            ("POST", f"/api/app/1/editions/{secret}/finished", {}),
            ("DELETE", f"/api/app/1/history/{entry.json()['id']}", None),
            ("GET", "/api/app/1/works/999999", None),
            ("GET", "/api/app/1/files/999999/content", None)]:
        answer = shelf.ada_app.request(method, path, json=body)
        assert answer.status_code == 404, (method, path, answer.status_code, answer.text)
        assert answer.json()["detail"] == {"code": "not_found", "message": "Not found."}, (method, path)
    # Nothing of it reached Ada's lists, and Ben's things are as they were.
    assert all(item["title"] != "A Secret Shelf" for item in sync(shelf.ada_app)["items"])
    assert shelf.ben_app.get(f"/api/app/1/editions/{secret}/bookmarks").json()[0]["note"] == ""
    assert len(shelf.ben_app.get("/api/app/1/history").json()) == 1


# --- What the addresses answer --------------------------------------------------------------------------------------


def test_the_library_says_what_the_app_shows_on_the_shelf(shelf: Shelf) -> None:
    items = {item["title"]: item for item in sync(shelf.ada_app)["items"]}
    orchard = items["The Quiet Orchard"]
    assert set(orchard) == {"id", "title", "authors", "series", "genre", "year", "library_ids", "color", "cover_etag",
                            "editions"}
    assert [person["name"] for person in orchard["authors"]] == ["Mara Lindqvist"]
    assert orchard["series"]["name"] == "Orchard Tales" and orchard["series"]["pos"] == 1.0
    assert orchard["library_ids"] == [shelf.library] and re.fullmatch(r"#[0-9a-f]{6}", orchard["color"])
    ebook = next(e for e in orchard["editions"] if e["kind"] == "ebook")
    audio = next(e for e in orchard["editions"] if e["kind"] == "audio")
    assert ebook["formats"] == ["epub"] and ebook["size"] == file_of(ebook["id"]).size and ebook["pages"] > 0
    assert audio["formats"] == ["m4b"] and audio["duration"] == 600 and audio["narrators"] == ["Iris Vale"]
    assert set(ebook) == {"id", "library_id", "kind", "variant", "language", "duration", "pages", "formats", "size",
                          "narrators"}
    # The cover's number is the one its address answers with.
    cover = shelf.ada_app.get(f"/api/app/1/works/{orchard['id']}/cover")
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/webp"
    assert orchard["cover_etag"] and cover.headers["etag"] == f'"{orchard["cover_etag"]}"'
    again = shelf.ada_app.get(f"/api/app/1/works/{orchard['id']}/cover",
                              headers={"If-None-Match": cover.headers["etag"]})
    assert again.status_code == 304
    assert items["Kite Season"]["cover_etag"] is None or isinstance(items["Kite Season"]["cover_etag"], str)


def test_one_work_with_its_files_and_the_chapters_of_the_recording(shelf: Shelf) -> None:
    answer = shelf.ada_app.get(f"/api/app/1/works/{shelf.works['The Quiet Orchard']}")
    assert answer.status_code == 200, answer.text
    work = answer.json()
    assert work["description"] == "A garden at the end of the world."
    audio = next(e for e in work["editions"] if e["kind"] == "audio")
    assert [(c["title"], c["start"]) for c in audio["chapters"]] == [("Opening", 0.0), ("The Gate", 240.5)]
    stored = file_of(audio["id"])
    assert audio["files"] == [{"id": stored.id, "part": 1, "duration": 600.0, "size": stored.size,
                               "sha256": stored.sha256, "format": "m4b", "mime": "audio/mp4"}]
    assert len(stored.sha256) == 64
    ebook = next(e for e in work["editions"] if e["kind"] == "ebook")
    assert ebook["chapters"] == [] and ebook["files"][0]["mime"] == "application/epub+zip"
    with SessionLocal() as db:
        assert db.scalar(select(Chapter.id).where(Chapter.edition_id == ebook["id"])) is not None


def test_a_download_that_broke_off_goes_on_only_while_the_file_is_the_same(shelf: Shelf) -> None:
    stored = file_of(shelf.editions["The Quiet Orchard/audio"])
    whole = shelf.ada_app.get(f"/api/app/1/files/{stored.id}/content")
    assert whole.status_code == 200 and len(whole.content) == stored.size
    etag = whole.headers["etag"]
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["content-disposition"] == f'attachment; filename="{stored.id}.m4b"'
    rest = shelf.ada_app.get(f"/api/app/1/files/{stored.id}/content",
                             headers={"Range": "bytes=10-", "If-Range": etag})
    assert rest.status_code == 206 and rest.content == whole.content[10:]
    assert rest.headers["content-range"] == f"bytes 10-{stored.size - 1}/{stored.size}"
    # Another file stands there now (another tag): the whole file comes, not a part of a file that is no more.
    changed = shelf.ada_app.get(f"/api/app/1/files/{stored.id}/content",
                                headers={"Range": "bytes=10-", "If-Range": '"another"'})
    assert changed.status_code == 200 and changed.content == whole.content
    weak = shelf.ada_app.get(f"/api/app/1/files/{stored.id}/content",
                             headers={"Range": "bytes=10-", "If-Range": "W/" + etag})
    assert weak.status_code == 200
    assert shelf.ada_app.get(f"/api/app/1/files/{stored.id}/content",
                             headers={"If-None-Match": etag}).status_code == 304


def test_the_newest_place_stays_and_a_device_that_was_earlier_hears_where(shelf: Shelf) -> None:
    edition = shelf.editions["The Quiet Orchard/audio"]
    work = shelf.works["The Quiet Orchard"]
    ipad = app_of(device_token(shelf.ada, "Ada's iPad"))
    newer = ipad.put(f"/api/app/1/editions/{edition}/progress", json={"locator": "time:300.5", "percent": 50, "age": 0})
    assert newer.status_code == 200, newer.text
    assert newer.json()["kept"] is True and newer.json()["current"]["device"] == "Ada's iPad"
    # The phone was offline and reports a place from an hour ago: the iPad's place stays, and the phone hears it.
    older = shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress",
                              json={"locator": "time:120", "percent": 20, "age": 3600})
    assert older.status_code == 200, older.text
    body = older.json()
    assert body["kept"] is False
    current = body["current"]
    assert (current["locator"], current["position"], current["percent"], current["device"], current["this_device"],
            current["work_id"]) == ("time:300.5", 300.5, 50.0, "Ada's iPad", False, work)
    # The list says the same, with which device it was, and the name the phone was paired with when it gives none.
    listed = shelf.ada_app.get("/api/app/1/progress").json()
    assert [(item["edition_id"], item["device"], item["this_device"]) for item in listed["items"]] == [
        (edition, "Ada's iPad", False)]
    assert ipad.get("/api/app/1/progress").json()["items"][0]["this_device"] is True
    later = shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress", json={"locator": "time:310", "percent": 52})
    assert later.json()["kept"] is True and later.json()["current"]["device"] == "Ada's iPhone"
    # A place that does not fit the edition is refused.
    assert shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress",
                             json={"locator": "time:99999", "percent": 1}).status_code == 422
    # Only another person's own places: Ben sees none of Ada's.
    assert shelf.ben_app.get("/api/app/1/progress").json()["items"] == []


def test_progress_since_a_moment_brings_what_arrived_after_it(shelf: Shelf, monkeypatch: pytest.MonkeyPatch) -> None:
    from .pairing_helpers import Moving

    moving = Moving(monkeypatch)
    edition = shelf.editions["Kite Season/ebook"]
    other = shelf.editions["The Quiet Orchard/ebook"]
    assert shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress",
                             json={"locator": "epubcfi(/6/2)", "percent": 10}).status_code == 200
    first = shelf.ada_app.get("/api/app/1/progress").json()
    assert [item["edition_id"] for item in first["items"]] == [edition]
    # Asked again at once, the place comes again (a report may still be on its way when the answer is made).
    assert [item["edition_id"] for item in shelf.ada_app.get(
        "/api/app/1/progress", params={"since": first["until"]}).json()["items"]] == [edition]
    moving.pass_by(minutes=5)
    first = shelf.ada_app.get("/api/app/1/progress", params={"since": first["until"]}).json()
    moving.pass_by(minutes=5)
    assert shelf.ada_app.put(f"/api/app/1/editions/{other}/progress",
                             json={"locator": "epubcfi(/6/4)", "percent": 30}).status_code == 200
    since = shelf.ada_app.get("/api/app/1/progress", params={"since": first["until"]}).json()
    assert [item["edition_id"] for item in since["items"]] == [other]
    moving.pass_by(minutes=5)
    later = shelf.ada_app.get("/api/app/1/progress", params={"since": since["until"]}).json()
    assert [item["edition_id"] for item in later["items"]] == [other]
    moving.pass_by(minutes=5)
    assert shelf.ada_app.get("/api/app/1/progress", params={"since": later["until"]}).json()["items"] == []


def test_bookmarks_finished_and_taking_it_back_as_in_the_browser(shelf: Shelf) -> None:
    edition = shelf.editions["The Quiet Orchard/audio"]
    made = shelf.ada_app.post(f"/api/app/1/editions/{edition}/bookmarks",
                              json={"locator": "time:42.5", "fraction": 0.07, "note": "the gate"})
    assert made.status_code == 201, made.text
    again = shelf.ada_app.post(f"/api/app/1/editions/{edition}/bookmarks", json={"locator": "time:42.5", "fraction": 0.07})
    assert again.status_code == 200 and again.json()["id"] == made.json()["id"]
    changed = shelf.ada_app.patch(f"/api/app/1/bookmarks/{made.json()['id']}", json={"note": "  the old gate "})
    assert changed.status_code == 200 and changed.json()["note"] == "the old gate"
    browser = new_client(shelf.ada)
    assert [mark["note"] for mark in browser.get(f"/api/v1/editions/{edition}/bookmarks").json()] == ["the old gate"]
    assert shelf.ada_app.delete(f"/api/app/1/bookmarks/{made.json()['id']}").status_code == 204
    assert shelf.ada_app.get(f"/api/app/1/editions/{edition}/bookmarks").json() == []
    done = shelf.ada_app.post(f"/api/app/1/editions/{edition}/finished", json={})
    assert done.status_code == 201 and done.json()["kind"] == "listened"
    assert shelf.ada_app.post(f"/api/app/1/editions/{edition}/finished", json={}).status_code == 200
    listed = shelf.ada_app.get("/api/app/1/history")
    assert [entry["id"] for entry in listed.json()] == [done.json()["id"]] and listed.headers["x-total-count"] == "1"
    assert shelf.ada_app.delete(f"/api/app/1/history/{done.json()['id']}").status_code == 204
    assert browser.get("/api/v1/history").json() == []


# --- No place on the disk -------------------------------------------------------------------------------------------


def test_no_path_of_the_disk_leaves_through_the_app(shelf: Shelf) -> None:
    ben = shelf.ben_app
    with SessionLocal() as db:
        names = [row.path for row in db.scalars(select(BookFile))]
        work_ids = list(db.scalars(select(Work.id)))
    # Every folder and file name the scan stored that is not also what a book says of itself (the author's folder).
    words = {HIDDEN, "Orchard.epub", "orchard.m4b", "Kite.epub", "Secret.epub", shelf.folder.name,
             shelf.private_folder.name, str(shelf.folder.parent)}
    words |= {part for name in names for part in name.split("/") if HIDDEN in part}
    texts: list[str] = []
    answers = [ben.get("/api/app/1/me"), ben.get("/api/app/1/library"), ben.get("/api/app/1/progress"),
               ben.get("/api/app/1/history")]
    answers += [ben.get(f"/api/app/1/works/{ident}") for ident in work_ids]
    for edition in shelf.editions.values():
        answers.append(ben.put(f"/api/app/1/editions/{edition}/progress",
                               json={"locator": "time:5" if edition == shelf.editions["The Quiet Orchard/audio"]
                                     else "epubcfi(/6/2)", "percent": 1}))
        answers.append(ben.post(f"/api/app/1/editions/{edition}/bookmarks",
                                json={"locator": "time:6" if edition == shelf.editions["The Quiet Orchard/audio"]
                                      else "epubcfi(/6/4)", "fraction": 0.01}))
        answers.append(ben.get(f"/api/app/1/editions/{edition}/bookmarks"))
        answers.append(ben.post(f"/api/app/1/editions/{edition}/finished", json={}))
        stored = file_of(edition)
        content = ben.get(f"/api/app/1/files/{stored.id}/content", headers={"Range": "bytes=0-1"})
        assert content.status_code == 206
        texts.append(" ".join(f"{key}: {value}" for key, value in content.headers.items()))
    for answer in answers:
        assert answer.status_code in (200, 201), (answer.request.url, answer.status_code, answer.text)
        texts.append(answer.text)
        texts.append(" ".join(f"{key}: {value}" for key, value in answer.headers.items()))
    assert len(texts) > 20
    for body in texts:
        for word in words:
            assert word not in body, (word, body[:300])
    assert '"path"' not in "".join(texts)


def test_the_same_work_in_the_browser_and_in_the_app_says_the_same(shelf: Shelf) -> None:
    browser = new_client(shelf.ada)
    ident = shelf.works["The Quiet Orchard"]
    web = browser.get(f"/api/v1/works/{ident}").json()
    app = shelf.ada_app.get(f"/api/app/1/works/{ident}").json()
    assert (app["title"], app["description"], app["genre"], app["year"], app["color"]) == (
        web["title"], web["description"], web["genre"], web["year"], web["color"])
    assert sorted(e["id"] for e in app["editions"]) == sorted(e["id"] for e in web["editions"])
    assert [f["sha256"] for e in app["editions"] for f in e["files"]] == [
        f["sha256"] for e in web["editions"] for f in e["files"]]
    stranger = app_of(device_token(make_account("zoe")))
    assert sync(stranger)["items"] == []
    assert stranger.get(f"/api/app/1/works/{ident}").status_code == 404


def test_the_gate_in_front_of_the_files_counts_the_app_too() -> None:
    """The gate that turns the surplus of one credential away before anything is looked up (``test_streams``) stands in
    front of the app's address of the bytes as well."""
    assert middleware.FILE_PATH.match("/api/app/1/files/12/content")
    assert middleware.FILE_PATH.match("/api/v1/files/12/content")
    assert not middleware.FILE_PATH.match("/api/app/1/works/12/cover")
