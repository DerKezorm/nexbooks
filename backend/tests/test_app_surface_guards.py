"""The surface of the iPhone app, held fast.

* Numbers are never given twice (schema 20); and should one ever be, ``removed`` names the old work anyway, beside the
  new one in ``items``.
* ``removed`` comes once to the device that had the work, not again with every change somebody makes afterwards, and per
  device: the other devices of the person still hear of it.
* A person in no library learns nothing from ``latest``.
* ``sha256`` is never one of a file that changed since it was read.
* The name of a device is kept without control and direction characters.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import AppSeen, BookFile, Device, Progress
from app.routers.app1 import FEATURES
from app.services import app_changes, brakes

from .app_helpers import Shelf, app_of, device_token, make_shelf, sync, sync_all
from .conftest import make_account, new_client
from .library_helpers import join, scan


@pytest.fixture
def shelf() -> Shelf:
    return make_shelf()


def write(statement: str, **values: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys = ON"))
        connection.execute(text(statement), values)


def leave(shelf: Shelf, library: int, account_id: int) -> None:
    write("DELETE FROM library_members WHERE library_id = :library AND user_id = :user", library=library,
          user=account_id)


# --- Numbers, and what went ---------------------------------------------------------------------------------------------


def test_the_newest_work_goes_and_the_next_one_gets_a_new_number(shelf: Shelf) -> None:
    with SessionLocal() as db:
        top = max(db.scalars(select(BookFile.id)))
    newest = max(shelf.works.values())
    edition = max(shelf.editions.values())
    write("DELETE FROM works WHERE id = :work", work=newest)
    write("INSERT INTO works (title, added_at, updated_at) VALUES ('Later', 'x', 'x')")
    write("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
          "VALUES ((SELECT max(id) FROM works), :library, 'ebook', 'Later', 'x', 'x')", library=shelf.library)
    write("INSERT INTO files (library_id, edition_id, role, path, size, mtime_ns, format, seen_at, created_at) VALUES "
          "(:library, (SELECT max(id) FROM editions), 'book', 'later.epub', 1, 1, 'epub', 'x', 'x')",
          library=shelf.library)
    with engine.connect() as connection:
        work, made_edition, made_file = connection.execute(text(
            "SELECT (SELECT max(id) FROM works), (SELECT max(id) FROM editions), (SELECT max(id) FROM files)")).one()
    assert work > newest and made_edition > edition and made_file > top


def test_a_number_that_came_again_is_named_in_removed_beside_the_new_work(shelf: Shelf) -> None:
    """Should a number ever be given twice (by hand, a restore of another kind): the app hears that the old work went,
    and gets the new one; it applies ``removed`` first. Before, ``removed`` was cut by what ``items`` held, and the app
    took another book for a changed title."""
    _works, _removed, marker = sync_all(shelf.ada_app)
    kite = shelf.works["Kite Season"]
    write("DELETE FROM works WHERE id = :work", work=kite)
    write("INSERT INTO works (id, title, added_at, updated_at) VALUES (:work, 'Another Book', '2026-10-01 10:00:00.000000', '2026-10-01 10:00:00.000000')", work=kite)
    write("INSERT INTO editions (work_id, library_id, kind, title, created_at, updated_at) "
          "VALUES (:work, :library, 'ebook', 'Another Book', '2026-10-01 10:00:00.000000', '2026-10-01 10:00:00.000000')", work=kite, library=shelf.library)
    # On one page (both at once) and a page at a time.
    page = sync(shelf.ada_app, marker)
    assert kite in page["removed"] and [item["title"] for item in page["items"] if item["id"] == kite] == ["Another Book"]
    works, removed, _marker = sync_all(shelf.ada_app, marker, limit=1)
    assert kite in removed
    assert works[kite]["title"] == "Another Book"


# --- Removed: once, and per device ------------------------------------------------------------------------------------


def test_who_left_a_library_hears_of_its_works_once_and_not_of_every_change_after(shelf: Shelf) -> None:
    """Ben leaves ``Private``; he hears that its work is gone, once. Somebody changes it
    afterwards: Ben hears nothing of it (only a number and a moment, but of a library he is no longer in)."""
    _works, _removed, marker = sync_all(shelf.ben_app)
    secret = shelf.works["A Secret Shelf"]
    leave(shelf, shelf.private, shelf.ben.id)
    _works, removed, marker = sync_all(shelf.ben_app, marker)
    assert removed == [secret]
    # Asked again with the new marker: he has heard of it, and the server forgets what his device had.
    assert sync(shelf.ben_app, marker)["removed"] == []
    write("UPDATE works SET title = 'Changed by others' WHERE id = :work", work=secret)
    write("UPDATE editions SET title = 'Changed by others' WHERE work_id = :work", work=secret)
    _works, again, _marker = sync_all(shelf.ben_app, marker)
    assert again == []


def test_every_device_of_the_person_hears_of_a_removal_for_itself(shelf: Shelf) -> None:
    """``app_seen`` is per device: the phone hearing of it does not make the iPad forget to."""
    ipad = app_of(device_token(shelf.ben, "Ben's other iPad"))
    _works, _removed, phone_marker = sync_all(shelf.ben_app)
    _works, _removed, pad_marker = sync_all(ipad)
    secret = shelf.works["A Secret Shelf"]
    leave(shelf, shelf.private, shelf.ben.id)
    _works, removed, phone_marker = sync_all(shelf.ben_app, phone_marker)
    assert removed == [secret]
    sync(shelf.ben_app, phone_marker)
    _works, removed, _marker = sync_all(ipad, pad_marker)
    assert removed == [secret]


def test_a_removal_lost_on_the_way_comes_again_until_the_device_asks_past_it(shelf: Shelf) -> None:
    """The row goes only once the device asks with a marker past the removal: an answer that never arrived (the phone
    lost its connection) is asked for again with the old marker, and says the same."""
    _works, _removed, marker = sync_all(shelf.ben_app)
    leave(shelf, shelf.private, shelf.ben.id)
    first = sync(shelf.ben_app, marker)
    again = sync(shelf.ben_app, marker)
    assert first["removed"] == again["removed"] == [shelf.works["A Secret Shelf"]]


def test_from_nothing_removed_is_empty_even_for_what_the_device_had(shelf: Shelf) -> None:
    """Gap (a) of the review: starting from nothing, the app keeps what comes and forgets the rest; ``removed`` is
    empty, also when the device had works that went since."""
    sync_all(shelf.ben_app)
    leave(shelf, shelf.private, shelf.ben.id)
    write("DELETE FROM works WHERE id = :work", work=shelf.works["Kite Season"])
    with SessionLocal() as db:
        assert db.scalar(select(AppSeen.work_id).where(AppSeen.work_id == shelf.works["A Secret Shelf"])) is not None
    works, removed, _marker = sync_all(shelf.ben_app, 0, limit=1)
    assert removed == []
    # And the server forgot what the device had before: it holds what this run gave, nothing else.
    with SessionLocal() as db:
        held = set(db.scalars(select(AppSeen.work_id).where(AppSeen.device_id == _device_id(shelf.ben, "Ben's iPad"))))
    assert held == set(works)


def test_a_page_holds_exactly_limit_works_and_removals(shelf: Shelf) -> None:
    """Gap (b): never more than ``limit``, removals counted in."""
    for limit in (1, 2):
        page = sync(shelf.ben_app, 0, limit)
        assert len(page["items"]) + len(page["removed"]) == limit and page["marker"] < page["latest"]
    _works, _removed, marker = sync_all(shelf.ben_app)
    leave(shelf, shelf.private, shelf.ben.id)
    leave(shelf, shelf.library, shelf.ben.id)
    join(shelf.library, shelf.ben.id, "read")
    page = sync(shelf.ben_app, marker, 2)
    assert len(page["items"]) + len(page["removed"]) == 2


def _device_id(account: Any, name: str) -> int:
    with SessionLocal() as db:
        found = db.scalar(select(Device.id).where(Device.account_id == account.id, Device.name == name))
        assert found is not None
        return int(found)


def test_what_a_revoked_device_was_given_is_forgotten_by_the_hourly_round(shelf: Shelf) -> None:
    sync_all(shelf.ada_app)
    sync_all(shelf.ben_app)
    ada_device = _device_id(shelf.ada, "Ada's iPhone")
    write("UPDATE devices SET revoked_at = '2026-10-01 10:00:00.000000' WHERE id = :device", device=ada_device)
    with SessionLocal() as db:
        app_changes.prune(db)
    with SessionLocal() as db:
        assert db.scalar(select(AppSeen.work_id).where(AppSeen.device_id == ada_device)) is None
        assert db.scalar(select(AppSeen.work_id).where(AppSeen.device_id == _device_id(shelf.ben, "Ben's iPad")))


def test_a_device_that_is_deleted_takes_what_it_was_given_with_it(shelf: Shelf) -> None:
    sync_all(shelf.ada_app)
    with SessionLocal() as db:
        assert db.scalar(select(AppSeen.device_id)) is not None
    write("DELETE FROM devices")
    with SessionLocal() as db:
        assert db.scalar(select(AppSeen.device_id)) is None


# --- Nothing to see, nothing to learn -----------------------------------------------------------------------------------


def test_a_person_in_no_library_learns_nothing_from_the_marker(shelf: Shelf) -> None:
    cara = make_account("cara")
    cara_app = app_of(device_token(cara, "Cara's iPhone"))
    first = sync(cara_app)
    assert first == {"items": [], "removed": [], "marker": 0, "latest": 0}
    write("UPDATE works SET title = 'Written by others' WHERE id = :work", work=shelf.works["Kite Season"])
    assert sync(cara_app) == first
    # A marker of before (from a library she has left) means starting again, and again she learns nothing.
    refused = cara_app.get("/api/app/1/library", params={"after": 5})
    assert refused.status_code == 410
    assert (refused.json()["detail"]["code"], refused.json()["detail"]["latest"]) == ("marker_too_old", 0)
    # Once in a library, the works come from nothing.
    join(shelf.library, cara.id, "read")
    works, _removed, _marker = sync_all(cara_app)
    assert "Kite Season" in {item["title"] for item in works.values()}


# --- Places and the device ----------------------------------------------------------------------------------------------


def test_the_places_of_books_no_longer_seen_are_not_listed(shelf: Shelf) -> None:
    """Gap (c): ``GET /progress`` names only editions the person still sees."""
    edition = shelf.editions["A Secret Shelf/ebook"]
    assert shelf.ben_app.put(f"/api/app/1/editions/{edition}/progress",
                             json={"locator": "epubcfi(/6/4)", "percent": 10}).status_code == 200
    assert [item["edition_id"] for item in shelf.ben_app.get("/api/app/1/progress").json()["items"]] == [edition]
    leave(shelf, shelf.private, shelf.ben.id)
    assert shelf.ben_app.get("/api/app/1/progress").json()["items"] == []
    with SessionLocal() as db:
        assert db.get(Progress, (shelf.ben.id, edition)) is not None, "his own place stays for when he comes back"


@pytest.mark.parametrize(("sent", "kept"), [
    ("Evil\u202ename", "Evilname"),
    ("Ada\u200b's\u2066 iPad\u2069", "Ada's iPad"),
    ("Line\u2028break\x07", "Linebreak"),
])
def test_a_device_name_is_kept_without_control_and_direction_characters(shelf: Shelf, sent: str, kept: str) -> None:
    edition = shelf.editions["Kite Season/ebook"]
    answer = shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress",
                               json={"locator": "epubcfi(/6/2)", "percent": 5, "device": sent})
    assert answer.status_code == 200, answer.text
    assert answer.json()["current"]["device"] == kept
    with SessionLocal() as db:
        assert db.get(Progress, (shelf.ada.id, edition)).device == kept  # type: ignore[union-attr]


def test_a_device_name_with_nothing_left_is_the_name_the_device_was_paired_with(shelf: Shelf) -> None:
    """Gap (f): the name falls back to the pairing's, not to nothing."""
    edition = shelf.editions["Kite Season/ebook"]
    for sent in ("", "   ", "\u202e\u200b\u2066"):
        answer = shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress",
                                   json={"locator": "epubcfi(/6/2)", "percent": 5, "device": sent})
        assert answer.json()["current"]["device"] == "Ada's iPhone", repr(sent)


def test_the_browser_keeps_its_device_name_clean_too(shelf: Shelf) -> None:
    edition = shelf.editions["Kite Season/ebook"]
    with new_client(shelf.ada) as browser:
        answer = browser.put(f"/api/v1/editions/{edition}/progress", json={
            "locator": "epubcfi(/6/2)", "fraction": 0.05, "device": "Firefox\u202e on Linux", "device_key": "tab"})
        assert answer.status_code == 200, answer.text
    with SessionLocal() as db:
        assert db.get(Progress, (shelf.ada.id, edition)).device == "Firefox on Linux"  # type: ignore[union-attr]


def test_a_place_that_does_not_fit_is_bad_place(shelf: Shelf) -> None:
    """Gap (g): the code the app branches on, not only the status."""
    for edition, locator in ((shelf.editions["Kite Season/ebook"], "time:12"),
                             (shelf.editions["The Quiet Orchard/audio"], "time:99999"),
                             (shelf.editions["The Quiet Orchard/audio"], "epubcfi(/6/2)")):
        answer = shelf.ada_app.put(f"/api/app/1/editions/{edition}/progress", json={"locator": locator, "percent": 1})
        assert (answer.status_code, answer.json()["detail"]["code"]) == (422, "bad_place"), locator


# --- What the server can do, covers, checksums --------------------------------------------------------------------------


def test_no_name_of_what_the_server_can_do_goes(shelf: Shelf) -> None:
    """Gap (d): a name in ``features`` is never taken away while ``app/1`` stands."""
    promised = {"pairing", "library", "works", "covers", "files", "progress", "bookmarks", "history", "later"}
    assert promised <= set(FEATURES)
    assert promised <= set(shelf.ada_app.get("/api/app/1/me").json()["features"])


def test_a_cover_is_kept_for_ever_only_while_v_is_its_tag(shelf: Shelf) -> None:
    """Gap (e): another ``v`` (an old one, a made-up one) must not let the app keep the picture for ever."""
    work = shelf.works["The Quiet Orchard"]
    tag = next(item for item in sync(shelf.ada_app)["items"] if item["id"] == work)["cover_etag"]
    assert tag
    url = f"/api/app/1/works/{work}/cover"
    right = shelf.ada_app.get(url, params={"v": tag})
    assert right.status_code == 200 and "immutable" in right.headers["cache-control"]
    assert right.headers["etag"] == f'"{tag}"'
    for other in (None, "0" * len(tag), tag[:-1] + ("0" if tag[-1] != "0" else "1"), tag[:4]):
        answer = shelf.ada_app.get(url, params={"v": other} if other is not None else {})
        assert answer.status_code == 200
        assert answer.headers["cache-control"] == "private, no-cache", other


def test_a_checksum_is_never_one_of_a_file_that_changed_since_it_was_read(shelf: Shelf) -> None:
    work = shelf.works["Kite Season"]
    edition = shelf.editions["Kite Season/ebook"]

    def checksum() -> str:
        answer = shelf.ada_app.get(f"/api/app/1/works/{work}").json()
        return str(next(e for e in answer["editions"] if e["id"] == edition)["files"][0]["sha256"])

    with SessionLocal() as db:
        row = db.scalar(select(BookFile).where(BookFile.edition_id == edition, BookFile.role == "book"))
        assert row is not None
        stored, relative = row.sha256, row.path
    assert checksum() == stored and len(stored) == 64
    path = shelf.folder.joinpath(*relative.split("/"))
    # The same size, another time on the disk: changed for all nexbooks knows, so no checksum until the scan has read it.
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns - 10_000_000_000))
    assert checksum() == ""
    # Gone from the disk: nothing either.
    moved = path.with_suffix(".away")
    path.rename(moved)
    assert checksum() == ""
    moved.rename(path)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert checksum() == stored
    # Changed for real (bytes appended), then read by the scan: the new checksum.
    with path.open("ab") as handle:
        handle.write(b"\0" * 16)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns - 120_000_000_000))
    assert checksum() == ""
    scan(shelf.library)
    import hashlib

    assert checksum() == hashlib.sha256(path.read_bytes()).hexdigest()


# --- Headers, HEAD, and the codes of the files --------------------------------------------------------------------------


def test_the_headers_the_app_reads_are_there(shelf: Shelf) -> None:
    stored = shelf.editions["The Quiet Orchard/audio"]
    with SessionLocal() as db:
        file_id = db.scalar(select(BookFile.id).where(BookFile.edition_id == stored, BookFile.role == "book"))
    url = f"/api/app/1/files/{file_id}/content"
    whole = shelf.ada_app.get(url)
    for name in ("etag", "accept-ranges", "content-length", "content-disposition", "last-modified"):
        assert name in whole.headers, name
    part = shelf.ada_app.get(url, headers={"Range": "bytes=0-9", "If-Range": whole.headers["etag"]})
    assert part.status_code == 206 and part.headers["content-range"].startswith("bytes 0-9/")
    assert part.headers["etag"] == whole.headers["etag"]
    history = shelf.ada_app.get("/api/app/1/history")
    assert history.headers["x-total-count"] == "0"
    # HEAD is not answered (the app asks with a Range for the size); the contract says so.
    assert shelf.ada_app.head(url).status_code == 405


def test_too_many_requests_for_files_is_stream_too_often(shelf: Shelf, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(brakes.LIMITS, "stream", 3)
    with SessionLocal() as db:
        file_id = db.scalar(select(BookFile.id).where(BookFile.role == "book"))
    url = f"/api/app/1/files/{file_id}/content"
    codes = [shelf.ben_app.get(url).status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    over = shelf.ben_app.get(url)
    assert (over.status_code, over.json()["detail"]["code"]) == (429, "stream_too_often")
    assert int(over.headers["retry-after"]) > 0

