"""The progress sync of KOReader under ``/kosync``: the checksum a book is named by is the one KOReader makes (written out
again here from the rule of its plugin, and checked at every offset it reads at), a report arrives as a place the browser
opens at and comes back as a place KOReader can go to, a book the connection may not see is a book that is not there, a
wrong key is turned away and braked, and the way is no way for a browser.

KOReader sends the MD5 of the password as its key; ``keyed`` does the same with the secret of the connection."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import clock
from app.db import SessionLocal
from app.models import Account, BookFile, EreaderLink, LibraryMember, Progress, utcnow
from app.security import brake
from app.services import brakes, kosync, settings_service
from app.services.readers import epub

from .bookmaker import make_epub, make_pdf
from .conftest import PASSWORD, new_client
from .ereader_helpers import Reading, connect, opds_client
from .filing_helpers import book_files, download_roots, ebook_world, finish, german_epub, library_path, the_download
from .follow_helpers import BOOK
from .library_helpers import age, book, run_jobs, scan
from .procure_fakes import FakeNetwork
from .reading_helpers import Clock

PLUGIN = {"accept": "application/vnd.koreader.v1+json"}
#: The places KOReader names in a book it reflows (what it keeps as the last place).
POINTER = "/body/DocFragment[3]/body/div/p[12]/text().34"
OTHER_POINTER = "/body/DocFragment[2]/body/div/p[3]/text().0"
#: The id a device of KOReader makes up for itself: 32 hexadecimal digits (made fresh, nothing here is a real one).
DEVICE_ID = secrets.token_hex(16)


def md5(text_: str) -> str:
    return hashlib.md5(text_.encode()).hexdigest()


def keyed(name: str, secret: str) -> dict[str, str]:
    """What the plugin sends: the name, and the MD5 of the password as the key."""
    return {"x-auth-user": name, "x-auth-key": md5(secret), **PLUGIN}


def reference_digest(read_at: Callable[[int, int], bytes]) -> str:
    """``util.partialMD5`` of KOReader's frontend written out literally: for i from -1 to 10 it seeks to
    ``bit.lshift(1024, 2 * i)`` and reads 1024 bytes, stopping at the first read that gives nothing. ``bit.lshift`` keeps
    32 bits and takes the count modulo 32, which makes the offset of i = -1 zero."""
    digest = hashlib.md5()
    for i in range(-1, 11):
        offset = (1024 << ((2 * i) & 31)) & 0xFFFFFFFF
        sample = read_at(offset, 1024)
        if not sample:
            break
        digest.update(sample)
    return digest.hexdigest()


class Virtual:
    """A file of any size that is never on a disk: the byte at a place is a function of the place."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.at = 0

    @staticmethod
    def span(start: int, end: int) -> bytes:
        return bytes((place * 7 + 3) % 251 for place in range(start, end))

    def seek(self, offset: int, whence: int = 0) -> int:
        self.at = offset
        return offset

    def read(self, count: int) -> bytes:
        if self.at >= self.size:
            return b""
        return self.span(self.at, min(self.at + count, self.size))

    def read_at(self, offset: int, count: int) -> bytes:
        return self.span(offset, min(offset + count, self.size)) if offset < self.size else b""


def document_of(title: str) -> str:
    """The checksum the scan made for the EPUB of an e-book."""
    from app.models import Edition

    with SessionLocal() as db:
        found = db.scalar(select(BookFile.partial_md5).join(Edition, Edition.id == BookFile.edition_id)
                          .where(Edition.title == title, Edition.kind == "ebook", BookFile.format == "epub"))
    assert found, title
    return str(found)


def edition_of(title: str) -> int:
    from app.models import Edition

    with SessionLocal() as db:
        return int(db.scalars(select(Edition.id).where(Edition.title == title, Edition.kind == "ebook")).one())


def report(app: TestClient, auth: dict[str, str], document: str, *, percentage: Any = 0.42, pointer: str = POINTER,
           device: str = "Kobo Libra 2", device_id: str = DEVICE_ID) -> Any:
    return app.put("/kosync/syncs/progress", headers=auth, json={
        "document": document, "progress": pointer, "percentage": percentage, "device": device, "device_id": device_id})


@dataclass
class Sync:
    reading: Reading
    app: TestClient
    auth: dict[str, str]
    secret: str
    document: str


@pytest.fixture
def ada_sync(reading: Reading, ada_browser: TestClient) -> Sync:
    secret = connect(ada_browser, "My Kobo", kind="kosync")
    return Sync(reading, opds_client(), keyed("ada", secret), secret, document_of("The Quiet Orchard"))


@pytest.fixture
def ben_sync(reading: Reading) -> Sync:
    with new_client(reading.shelf.ben) as browser:
        secret = connect(browser, "My Kindle", kind="kosync")
    return Sync(reading, opds_client(), keyed("ben", secret), secret, document_of("The Quiet Orchard"))


@pytest.fixture
def long_tale(reading: Reading) -> str:
    """A book of four parts of the same size in the library ``Books``: the checksum of its EPUB."""
    book(reading.shelf.folder, "Rune Falkner/Long Tale.epub", title="Long Tale", authors=("Rune Falkner",), language="en",
         chapters=("A", "B", "C", "D"), text_bytes=40_000)
    scan(reading.shelf.library)
    run_jobs()
    return document_of("Long Tale")


def spine_of(title: str) -> list[int]:
    from app.models import Edition

    with SessionLocal() as db:
        path = db.scalar(select(BookFile.path).join(Edition, Edition.id == BookFile.edition_id)
                         .where(Edition.title == title, BookFile.format == "epub"))
    assert path is not None
    return [zipfile.ZipFile(Path(_folder()) / path).getinfo(f"OEBPS/text/c{n}.xhtml").file_size for n in range(4)]


def _folder() -> Path:
    from app.models import Library

    with SessionLocal() as db:
        return Path(db.scalars(select(Library.path).where(Library.name == "Books")).one())


# --- The checksum a book is named by ------------------------------------------------------------------------------


def test_the_checksum_is_the_one_koreader_makes_for_every_epub_the_scan_found(reading: Reading) -> None:
    with SessionLocal() as db:
        rows = db.execute(select(BookFile.path, BookFile.library_id, BookFile.partial_md5, BookFile.format)
                          .where(BookFile.role == "book")).all()
        from app.models import Library

        roots = {row.id: row.path for row in db.scalars(select(Library))}
    epubs = [row for row in rows if row.format == "epub"]
    assert len(epubs) >= 5
    for path, library_id, found, _fmt in epubs:
        data = (Path(roots[library_id]) / path).read_bytes()
        assert found == reference_digest(lambda offset, count, data=data: data[offset:offset + count]), path
    # Not a recording, and nothing else: no checksum.
    assert {row.partial_md5 for row in rows if row.format != "epub"} == {""}


def test_what_the_rule_reads_in_a_small_file_is_what_it_reads(tmp_path: Path) -> None:
    """A second witness that does not use the offsets at all: for small files the sample is a plain slice."""
    data = bytes((n * 31 + 7) % 256 for n in range(5000))
    cases = {
        0: b"",
        1: data[:1],
        1000: data[:1000],
        1024: data[:1024],
        1025: data[:1025],
        # (0, 1024) and (1024, 2048) and what is there of (4096, 5120); 16384 is past the end.
        5000: data[:2048] + data[4096:5000],
    }
    for size, expected in cases.items():
        path = tmp_path / f"f{size}.bin"
        path.write_bytes(data[:size])
        with open(path, "rb") as handle:
            assert kosync.partial_md5(handle) == hashlib.md5(expected).hexdigest(), size
    # The checksum of nothing is the checksum of nothing: the empty file is no special case.
    assert kosync.partial_md5_of(str(tmp_path), "f0.bin") == hashlib.md5(b"").hexdigest()


def test_every_offset_is_read_in_a_file_big_enough_for_all_of_them() -> None:
    assert kosync.OFFSETS == (0, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864, 268435456,
                              1073741824)
    for size in (1, 1024 * 1024 + 5, 4 * 1024 * 1024, 2**30, 2**30 + 1, 3 * 2**30):
        virtual = Virtual(size)
        assert kosync.partial_md5(virtual) == reference_digest(virtual.read_at), size
    # The last offsets count: a file one byte past 1 GiB differs from one that ends there.
    assert kosync.partial_md5(Virtual(2**30)) != kosync.partial_md5(Virtual(2**30 + 1))


def test_a_file_that_is_there_before_gets_its_checksum_in_portions(reading: Reading) -> None:
    with SessionLocal() as db:
        db.execute(text("UPDATE files SET partial_md5 = ''"))
        db.commit()
        wanted = db.scalar(select(text("count(*)")).select_from(BookFile).where(
            BookFile.format == "epub", BookFile.role == "book"))
    assert wanted and wanted >= 5
    with SessionLocal() as db:
        did, after = kosync.backfill(db, 0, limit=2)
        assert did == 2 and after > 0
        assert db.scalar(select(text("count(*)")).select_from(BookFile).where(BookFile.partial_md5 != "")) == 2
    seen = did
    while True:
        with SessionLocal() as db:
            did, after = kosync.backfill(db, after, limit=2)
        if not did:
            break
        seen += did
    assert seen == wanted and after == 0
    assert document_of("The Quiet Orchard") == reference_digest(
        lambda offset, count: _bytes_of("The Quiet Orchard")[offset:offset + count])
    # Nothing is left to do, from the start or from anywhere, and a second look reads nothing.
    with SessionLocal() as db:
        assert kosync.backfill(db) == (0, 0)


def test_a_file_that_cannot_be_read_does_not_hold_up_the_others_and_is_tried_again_in_the_next_round(
        reading: Reading) -> None:
    with SessionLocal() as db:
        db.execute(text("UPDATE files SET partial_md5 = ''"))
        db.commit()
        first = db.scalar(select(BookFile.id).where(BookFile.format == "epub").order_by(BookFile.id))
        path = db.scalar(select(BookFile.path).where(BookFile.id == first))
    assert path is not None
    gone = _folder() / path
    kept, stamp = gone.read_bytes(), gone.stat()
    gone.unlink()
    try:
        after = 0
        with SessionLocal() as db:
            while True:
                did, after = kosync.backfill(db, after, limit=2)
                if not did:
                    break
            missing = db.scalars(select(BookFile.id).where(BookFile.partial_md5 == "", BookFile.format == "epub",
                                                           BookFile.role == "book")).all()
        assert missing == [first]
    finally:
        gone.write_bytes(kept)
        os.utime(gone, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    # Next round: the file is back (as it was), and gets its checksum.
    with SessionLocal() as db:
        after = 0
        while True:
            did, after = kosync.backfill(db, after)
            if not did:
                break
        assert db.scalars(select(BookFile.partial_md5).where(BookFile.id == first)).one() != ""


def _bytes_of(title: str) -> bytes:
    from app.models import Edition

    with SessionLocal() as db:
        path = db.scalar(select(BookFile.path).join(Edition, Edition.id == BookFile.edition_id)
                         .where(Edition.title == title, BookFile.format == "epub"))
    assert path is not None
    return (_folder() / path).read_bytes()


def test_the_checksum_moves_no_change_number_of_the_app(reading: Reading) -> None:
    from .app_helpers import sync

    before = sync(reading.shelf.ada_app)["marker"]
    with SessionLocal() as db:
        db.execute(text("UPDATE files SET partial_md5 = ''"))
        db.commit()
    marker_after_clearing = sync(reading.shelf.ada_app)["marker"]
    with SessionLocal() as db:
        after = 0
        while True:
            did, after = kosync.backfill(db, after)
            if not did:
                break
    assert sync(reading.shelf.ada_app)["marker"] == marker_after_clearing >= before


def test_a_file_that_changes_gets_a_new_checksum_and_a_pdf_gets_none(reading: Reading) -> None:
    from app.models import Edition

    old = document_of("Kite Season")
    with SessionLocal() as db:
        relative = db.scalar(select(BookFile.path).join(Edition, Edition.id == BookFile.edition_id)
                             .where(Edition.title == "Kite Season", BookFile.format == "epub"))
    assert relative is not None
    path = _folder() / relative
    make_epub(path, title="Kite Season", authors=("Jonas Brandauer",), language="en", text_bytes=9000)
    age(path)
    make_pdf(reading.shelf.folder / "Field Notes.pdf")
    age(reading.shelf.folder / "Field Notes.pdf")
    scan(reading.shelf.library)
    run_jobs()
    assert document_of("Kite Season") != old
    assert document_of("Kite Season") == reference_digest(lambda offset, count: path.read_bytes()[offset:offset + count])
    with SessionLocal() as db:
        assert db.scalar(select(BookFile.partial_md5).where(BookFile.format == "pdf")) == ""


def test_a_part_the_archive_does_not_hold_keeps_its_number_as_it_does_in_koreader(tmp_path: Path) -> None:
    # Measured with KOReader's own engine: a part the package names and the archive lacks is counted, a part the package
    # does not name is not. A book with a hole would otherwise be told a part too early from there on.
    path = make_epub(tmp_path / "b.epub", chapters=("A", "B", "C"), text_bytes=3000)
    holed = tmp_path / "holed.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(holed, "w") as target:
        for info in source.infolist():
            if info.filename != "OEBPS/text/c1.xhtml":
                target.writestr(info, source.read(info.filename))
    with open(holed, "rb") as handle:
        sizes = epub.spine_sizes(handle)
    assert len(sizes) == 3 and sizes[1] == 0 and sizes[0] > 0 and sizes[2] > 0
    assert kosync.fragment_at(sizes, 0.99) == 3


def test_the_sizes_of_the_parts_of_a_book_are_read_from_its_headers(tmp_path: Path) -> None:
    path = make_epub(tmp_path / "b.epub", chapters=("A", "B", "C"), text_bytes=3000)
    with open(path, "rb") as handle:
        sizes = epub.spine_sizes(handle)
    with zipfile.ZipFile(path) as archive:
        assert sizes == [archive.getinfo(f"OEBPS/text/c{n}.xhtml").file_size for n in range(3)]
    for hostile in (b"", b"PK", b"not a zip at all" * 50):
        broken = tmp_path / "broken.epub"
        broken.write_bytes(hostile)
        with open(broken, "rb") as handle:
            assert epub.spine_sizes(handle) == []


def test_the_part_of_a_book_a_share_falls_in() -> None:
    sizes = [100, 0, 300, 100, 500]
    assert [kosync.fragment_at(sizes, share) for share in (0, 0.05, 0.099, 0.1, 0.39, 0.4, 0.49, 0.5, 0.99, 1)] == [
        1, 1, 1, 3, 3, 4, 4, 5, 5, 5]
    assert kosync.fragment_at([], 0.5) is None and kosync.fragment_at([0, 0], 0.5) is None
    # Out of range is the start and the end.
    assert kosync.fragment_at(sizes, -3) == 1 and kosync.fragment_at(sizes, 7) == 5


# --- Signing in ---------------------------------------------------------------------------------------------------


def test_the_plugin_signs_in_with_the_name_and_the_md5_of_the_password(ada_sync: Sync) -> None:
    answer = ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth)
    assert answer.status_code == 200 and answer.json() == {"authorized": "OK"}
    assert answer.headers["content-type"].startswith("application/json")
    assert "set-cookie" not in answer.headers and answer.headers["cache-control"] == "no-store"
    # The name is the account's in any case, as for the catalogue.
    assert ada_sync.app.get("/kosync/users/auth", headers={**ada_sync.auth, "x-auth-user": "ADA"}).status_code == 200


def test_only_the_md5_of_the_secret_is_kept_and_it_is_not_enough_to_get_in(ada_sync: Sync) -> None:
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        stored = link.token_hash
        everything = " ".join(str(value) for value in (link.name, link.prefix, link.token_hash))
    assert stored == hashlib.sha256(md5(ada_sync.secret).encode()).hexdigest()
    assert ada_sync.secret not in everything and md5(ada_sync.secret) not in everything
    assert link.prefix == ada_sync.secret[:8] and ada_sync.secret.startswith("nbs_")
    # What is stored is neither the password nor the key: sending it as the key opens nothing.
    for wrong in (stored, ada_sync.secret, hashlib.sha256(ada_sync.secret.encode()).hexdigest()):
        assert ada_sync.app.get("/kosync/users/auth", headers={**ada_sync.auth, "x-auth-key": wrong}).status_code == 401


def test_a_wrong_key_or_name_or_nothing_is_a_401_in_the_shape_the_plugin_reads(ada_sync: Sync,
                                                                              ada_browser: TestClient,
                                                                              reading: Reading) -> None:
    opds_secret = connect(ada_browser, "app", kind="opds")
    tries = {
        "nothing": {},
        "only a name": {"x-auth-user": "ada"},
        "only a key": {"x-auth-key": md5(ada_sync.secret)},
        "the password of the account": keyed("ada", PASSWORD),
        "the md5 of the password of the account": {"x-auth-user": "ada", "x-auth-key": md5(PASSWORD)},
        "the secret itself, not its md5": {"x-auth-user": "ada", "x-auth-key": ada_sync.secret},
        "another name": keyed("ben", ada_sync.secret),
        "the md5 of an opds secret": keyed("ada", opds_secret),
        "upper case key": {"x-auth-user": "ada", "x-auth-key": md5(ada_sync.secret).upper()},
        "a key of the wrong length": {"x-auth-user": "ada", "x-auth-key": md5(ada_sync.secret)[:31]},
        "an empty name": {"x-auth-user": "", "x-auth-key": md5(ada_sync.secret)},
    }
    for name, headers in tries.items():
        # (Each of these is a guess that counts; the sender is let off each time, the brake has its own test.)
        brake.forget()
        answer = ada_sync.app.get("/kosync/users/auth", headers=headers)
        assert answer.status_code == 401, name
        assert answer.json() == {"code": 2001, "message": "Unauthorized"}, name
        assert "set-cookie" not in answer.headers, name
    # Every route asks, not only the one that checks the sign-in.
    brake.forget()
    for method, path in (("get", "/kosync/syncs/progress/" + ada_sync.document), ("put", "/kosync/syncs/progress")):
        answer = getattr(ada_sync.app, method)(path, headers={"x-auth-user": "ada", "x-auth-key": "0" * 32},
                                               **({"json": {"document": ada_sync.document}} if method == "put" else {}))
        assert answer.status_code == 401 and answer.json()["code"] == 2001, path


def test_the_secret_of_the_catalogue_is_no_key_here_and_this_secret_is_no_key_to_the_catalogue(
        ada_sync: Sync, ada_browser: TestClient) -> None:
    opds_secret = connect(ada_browser, "catalogue", kind="opds")
    assert ada_sync.app.get("/kosync/users/auth", headers=keyed("ada", opds_secret)).status_code == 401
    from .ereader_helpers import basic

    assert ada_sync.app.get("/opds", headers=basic("ada", ada_sync.secret)).status_code == 401
    assert ada_sync.app.get("/opds", headers=basic("ada", md5(ada_sync.secret))).status_code == 401


def test_a_connection_of_another_kind_is_no_key_to_the_sync(ada_sync: Sync) -> None:
    with SessionLocal() as db:
        link = db.scalars(select(EreaderLink)).one()
        link.kind = "opds"
        db.commit()
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 401


def test_registering_is_turned_down_with_a_message_the_plugin_shows(ada_sync: Sync) -> None:
    with SessionLocal() as db:
        before = len(db.scalars(select(Account.id)).all())
    answer = ada_sync.app.post("/kosync/users/create", json={"username": "newcomer", "password": md5("x")})
    assert answer.status_code == 402
    body = answer.json()
    assert body["code"] == 2005 and "My account" in body["message"] and "nexbooks" in body["message"]
    # With nothing sent, with rubbish, with a name that exists: the same, and no account.
    for payload in (None, {"username": "ada", "password": "x"}, {"username": 5}):
        assert ada_sync.app.post("/kosync/users/create", json=payload).status_code == 402
    with SessionLocal() as db:
        assert len(db.scalars(select(Account.id)).all()) == before


def test_a_blocked_deleted_or_switched_off_connection_is_turned_away(
        client: TestClient, operator: Account, reading: Reading, ada_sync: Sync, ada_browser: TestClient) -> None:
    other = connect(ada_browser, "second", kind="kosync")
    links = ada_browser.get("/api/v1/ereaders").json()["links"]
    first, second = links[0]["id"], links[1]["id"]
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 200
    assert client.post(f"/api/v1/admin/ereaders/links/{first}/block").status_code == 200
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 401
    assert ada_sync.app.get("/kosync/users/auth", headers=keyed("ada", other)).status_code == 200
    assert ada_browser.delete(f"/api/v1/ereaders/links/{second}").status_code == 204
    assert ada_sync.app.get("/kosync/users/auth", headers=keyed("ada", other)).status_code == 401
    # The block stays until the operator deletes the connection; only then may the person make one of that kind.
    assert client.delete(f"/api/v1/admin/ereaders/links/{first}").status_code == 204
    fresh = connect(ada_browser, "third", kind="kosync")
    assert client.put("/api/v1/settings", json={"ereaders_allowed": False}).status_code == 200
    off = ada_sync.app.get("/kosync/users/auth", headers=keyed("ada", fresh))
    # 401 and not 403: it is the status the plugin shows a message for.
    assert off.status_code == 401 and "switched e-readers off" in off.json()["message"]
    assert client.put("/api/v1/settings", json={"ereaders_allowed": True}).status_code == 200
    assert ada_sync.app.get("/kosync/users/auth", headers=keyed("ada", fresh)).status_code == 200


def test_a_blocked_account_has_no_sync_but_a_lock_after_wrong_passwords_does_not_reach_it(reading: Reading, ada_sync: Sync) -> None:
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        ada.blocked_at = utcnow()
        db.commit()
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 401
    with SessionLocal() as db:
        ada = db.get(Account, reading.shelf.ada.id)
        assert ada is not None
        ada.blocked_at = None
        ada.locked_until = utcnow() + timedelta(minutes=5)
        db.commit()
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 200, "the lock is for the password"


def test_wrong_keys_are_braked_but_a_right_one_is_never_turned_away(reading: Reading, ada_sync: Sync) -> None:
    same = {"x-auth-user": "ada", "x-auth-key": md5("g")}
    # The same wrong key again and again (a plugin that was not told) counts once.
    for _ in range(12):
        assert ada_sync.app.get("/kosync/users/auth", headers=same).status_code == 401
    answers = [ada_sync.app.get("/kosync/users/auth", headers={"x-auth-user": "ada", "x-auth-key": md5(f"n{index}")})
               .status_code for index in range(8)]
    assert answers[:4] == [401] * 4 and set(answers[5:]) == {429}
    rested = ada_sync.app.get("/kosync/users/auth", headers={"x-auth-user": "ada", "x-auth-key": md5("h")})
    assert rested.status_code == 429 and int(rested.headers["retry-after"]) > 0
    # The connection that exists is looked up first: it still works from the same address.
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 200
    # The count is its own: the sign-in with the password still works.
    login = opds_client().post("/api/v1/auth/login", headers={"X-Nexbooks-Client": "tab-test0000"},
                               json={"name": "ada", "password": PASSWORD})
    assert login.status_code == 200, login.text
    brake.forget()
    assert ada_sync.app.get("/kosync/users/auth", headers={"x-auth-user": "ada", "x-auth-key": md5("h")}
                            ).status_code == 401


def test_a_flood_of_requests_is_braked_per_account(ada_sync: Sync, ben_sync: Sync,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(brakes.LIMITS, "kosync", 3)
    assert [ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code for _ in range(4)] == [
        200, 200, 200, 429]
    assert ben_sync.app.get("/kosync/users/auth", headers=ben_sync.auth).status_code == 200


def test_it_is_no_way_for_a_browser(ada_sync: Sync, ada_browser: TestClient) -> None:
    # The cookie of a signed-in browser opens nothing here, and nothing here sets or reads one.
    assert ada_browser.get("/kosync/users/auth").status_code == 401
    assert ada_browser.get(f"/kosync/syncs/progress/{ada_sync.document}").status_code == 401
    assert ada_browser.put("/kosync/syncs/progress", json={"document": ada_sync.document}).status_code == 401
    # No page on another site is let in: there is no CORS.
    answer = ada_sync.app.get("/kosync/users/auth", headers={**ada_sync.auth, "Origin": "https://example.com"})
    assert "access-control-allow-origin" not in answer.headers
    # Nothing else is there: other methods and other paths.
    assert ada_sync.app.delete("/kosync/users/me", headers=ada_sync.auth).status_code in (404, 405)
    assert ada_sync.app.put("/kosync/users/password", headers=ada_sync.auth, json={"password": "x"}).status_code in (
        404, 405)
    assert ada_sync.app.get("/kosync/nothing", headers=ada_sync.auth).status_code in (404, 405)


def test_the_secret_and_the_key_never_reach_the_log(ada_sync: Sync, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth)
    ada_sync.app.get("/kosync/users/auth", headers={"x-auth-user": "ada", "x-auth-key": md5("q")})
    report(ada_sync.app, ada_sync.auth, ada_sync.document)
    assert ada_sync.secret not in caplog.text and md5(ada_sync.secret) not in caplog.text and md5("q") not in caplog.text
    assert POINTER not in caplog.text


# --- A report -----------------------------------------------------------------------------------------------------


def test_a_report_becomes_a_place_the_browser_opens_at(reading: Reading, ada_sync: Sync,
                                                       ada_browser: TestClient) -> None:
    answer = report(ada_sync.app, ada_sync.auth, ada_sync.document)
    assert answer.status_code == 200
    body = answer.json()
    assert body["document"] == ada_sync.document and abs(body["timestamp"] - int(clock.now().timestamp())) <= 5
    edition = edition_of("The Quiet Orchard")
    place = ada_browser.get(f"/api/v1/editions/{edition}/progress").json()
    # No CFI (KOReader's place cannot be one), the share of the book, and the device by the name it gave.
    assert place["locator"] == "" and place["fraction"] == pytest.approx(0.42)
    assert place["device"] == "Kobo Libra 2" and place["device_key"] == "kr-" + DEVICE_ID
    assert place["chapter"] == 0 and "koreader" not in place
    with SessionLocal() as db:
        row = db.get(Progress, (reading.shelf.ada.id, edition))
        assert row is not None and row.koreader == POINTER
        # Nobody else has a place.
        assert len(db.scalars(select(Progress.user_id)).all()) == 1


def test_a_report_comes_back_as_it_was_for_another_device_of_koreader(ada_sync: Sync) -> None:
    assert report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.3123).status_code == 200
    answer = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth)
    assert answer.status_code == 200
    body = answer.json()
    assert body == {"document": ada_sync.document, "percentage": pytest.approx(0.3123), "progress": POINTER,
                    "device": "Kobo Libra 2", "device_id": DEVICE_ID,
                    "timestamp": body["timestamp"]}
    assert isinstance(body["timestamp"], int)
    assert abs(body["timestamp"] - int(clock.now().timestamp())) <= 5


def test_a_place_from_the_browser_comes_to_koreader_as_the_start_of_the_part_it_is_in(
        long_tale: str, ada_sync: Sync, ada_browser: TestClient) -> None:
    sizes = spine_of("Long Tale")
    assert len(set(sizes)) == 1
    edition = edition_of("Long Tale")
    # The browser says 62 % of the book: the third of four equal parts begins at 50 %.
    saved = ada_browser.put(f"/api/v1/editions/{edition}/progress", headers={"X-Nexbooks-Client": "tab-test0000"}, json={
        "locator": "epubcfi(/6/10!/4/2/8)", "fraction": 0.62, "chapter": 3, "chapter_count": 4,
        "chapter_title": "C", "device_key": "tab-abcdefgh", "device": "Firefox on Linux"})
    assert saved.status_code == 200 and saved.json()["applied"] is True
    answer = ada_sync.app.get(f"/kosync/syncs/progress/{long_tale}", headers=ada_sync.auth)
    assert answer.status_code == 200
    body = answer.json()
    assert body["progress"] == "/body/DocFragment[3]" and body["percentage"] == pytest.approx(0.62)
    assert body["device"] == "Firefox on Linux" and body["device_id"] == "tab-abcdefgh"
    # The ends of the book.
    for share, part in ((0.0, 1), (0.2499, 1), (0.2501, 2), (0.9999, 4), (1.0, 4)):
        ada_browser.put(f"/api/v1/editions/{edition}/progress", headers={"X-Nexbooks-Client": "tab-test0000"}, json={
            "locator": "epubcfi(/6/4)", "fraction": share, "device_key": "tab-abcdefgh", "device": "Firefox on Linux"})
        got = ada_sync.app.get(f"/kosync/syncs/progress/{long_tale}", headers=ada_sync.auth).json()
        assert got["progress"] == f"/body/DocFragment[{part}]", share


def test_a_place_from_the_browser_that_cannot_be_put_in_words_is_not_told_at_all(
        long_tale: str, reading: Reading, ada_sync: Sync, ada_browser: TestClient) -> None:
    edition = edition_of("Long Tale")
    ada_browser.put(f"/api/v1/editions/{edition}/progress", headers={"X-Nexbooks-Client": "tab-test0000"}, json={
        "locator": "epubcfi(/6/4)", "fraction": 0.5, "device_key": "tab-abcdefgh", "device": "Firefox"})
    # The book's parts cannot be read (it is gone from the disk): no place is better than a wrong one.
    (_folder() / "Rune Falkner" / "Long Tale.epub").write_bytes(b"not a book any more")
    kosync._spines.clear()
    assert ada_sync.app.get(f"/kosync/syncs/progress/{long_tale}", headers=ada_sync.auth).json() == {}


def test_the_place_of_koreader_goes_when_anything_else_moves_the_place(long_tale: str, ada_sync: Sync,
                                                                       ada_browser: TestClient,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    report(ada_sync.app, ada_sync.auth, long_tale, percentage=0.9, pointer=POINTER)
    tick.advance(seconds=30)
    ada_browser.put(f"/api/v1/editions/{edition_of('Long Tale')}/progress", headers={"X-Nexbooks-Client": "tab-test0000"},
                    json={"locator": "epubcfi(/6/4)", "fraction": 0.1, "device_key": "tab-abcdefgh", "device": "Firefox"})
    with SessionLocal() as db:
        assert db.scalars(select(Progress.koreader)).one() == ""
    got = ada_sync.app.get(f"/kosync/syncs/progress/{long_tale}", headers=ada_sync.auth).json()
    assert got["progress"] == "/body/DocFragment[1]" and got["percentage"] == pytest.approx(0.1)


def test_an_older_report_does_not_take_the_place_of_a_newer_one(reading: Reading, ada_sync: Sync,
                                                                ada_browser: TestClient,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    tick = Clock(monkeypatch)
    edition = edition_of("The Quiet Orchard")
    # The browser reports 80 % at 12:00:30; KOReader's report then arrives, but its clock put it earlier: it has no
    # age to say, so it counts from its arrival; to arrive "earlier" the clock of the server goes back for it.
    tick.advance(seconds=30)
    ada_browser.put(f"/api/v1/editions/{edition}/progress", headers={"X-Nexbooks-Client": "tab-test0000"}, json={
        "locator": "epubcfi(/6/4)", "fraction": 0.8, "device_key": "tab-abcdefgh", "device": "Firefox"})
    tick.advance(seconds=-20)
    assert report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.3).status_code == 200
    place = ada_browser.get(f"/api/v1/editions/{edition}/progress").json()
    assert place["fraction"] == pytest.approx(0.8) and place["device"] == "Firefox"
    # A newer one does, and then an older one of the first device is the one dropped.
    tick.advance(seconds=60)
    report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.9)
    assert ada_browser.get(f"/api/v1/editions/{edition}/progress").json()["fraction"] == pytest.approx(0.9)
    tick.advance(seconds=-100)
    report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.5, device="Old Kindle", device_id="old")
    place = ada_browser.get(f"/api/v1/editions/{edition}/progress").json()
    assert place["fraction"] == pytest.approx(0.9) and place["device"] == "Kobo Libra 2"
    got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json()
    assert got["percentage"] == pytest.approx(0.9)


def test_the_place_of_one_person_is_not_the_place_of_another(reading: Reading, ada_sync: Sync, ben_sync: Sync) -> None:
    report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.2, device="Ada's Kobo", device_id="ada01")
    assert ben_sync.app.get(f"/kosync/syncs/progress/{ben_sync.document}", headers=ben_sync.auth).json() == {}
    report(ben_sync.app, ben_sync.auth, ben_sync.document, percentage=0.7, device="Ben's Kindle", device_id="ben01")
    ada_got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json()
    ben_got = ben_sync.app.get(f"/kosync/syncs/progress/{ben_sync.document}", headers=ben_sync.auth).json()
    assert (ada_got["percentage"], ada_got["device"]) == (0.2, "Ada's Kobo")
    assert (ben_got["percentage"], ben_got["device"]) == (0.7, "Ben's Kindle")


def test_a_book_the_connection_may_not_see_is_a_book_that_is_not_there(reading: Reading, ada_sync: Sync,
                                                                       ben_sync: Sync) -> None:
    secret_document = document_of("A Secret Shelf")
    # Ada is not in the library: a report is taken, as for any name, and nothing is kept; the question has no answer.
    sent = report(ada_sync.app, ada_sync.auth, secret_document, percentage=0.5)
    unknown = report(ada_sync.app, ada_sync.auth, "f" * 32, percentage=0.5)
    assert sent.status_code == unknown.status_code == 200
    assert set(sent.json()) == set(unknown.json()) == {"document", "timestamp"}
    with SessionLocal() as db:
        assert db.scalars(select(Progress.user_id)).all() == []
    assert ada_sync.app.get(f"/kosync/syncs/progress/{secret_document}", headers=ada_sync.auth).json() == {}
    assert ada_sync.app.get(f"/kosync/syncs/progress/{'f' * 32}", headers=ada_sync.auth).json() == {}
    # Ben is in it: for him the same book is there.
    assert report(ben_sync.app, ben_sync.auth, secret_document, percentage=0.5).status_code == 200
    got = ben_sync.app.get(f"/kosync/syncs/progress/{secret_document}", headers=ben_sync.auth).json()
    assert got["percentage"] == 0.5
    # And Ada still sees nothing of it.
    assert ada_sync.app.get(f"/kosync/syncs/progress/{secret_document}", headers=ada_sync.auth).json() == {}


def test_a_membership_taken_away_takes_the_book_away(reading: Reading, ada_sync: Sync) -> None:
    report(ada_sync.app, ada_sync.auth, ada_sync.document)
    assert ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json() != {}
    with SessionLocal() as db:
        db.query(LibraryMember).filter_by(user_id=reading.shelf.ada.id).delete()
        db.commit()
    from app.services.discover import owned

    owned.forget()
    assert ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json() == {}
    assert report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.99).status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(Progress.fraction)).all() == [pytest.approx(0.42)]


def test_a_recording_and_a_file_that_is_not_an_epub_are_not_documents(reading: Reading, ada_sync: Sync) -> None:
    make_pdf(reading.shelf.folder / "Field Notes.pdf")
    age(reading.shelf.folder / "Field Notes.pdf")
    scan(reading.shelf.library)
    run_jobs()
    with SessionLocal() as db:
        audio = db.scalar(select(BookFile).where(BookFile.format == "m4b"))
        pdf = db.scalar(select(BookFile).where(BookFile.format == "pdf"))
        assert audio is not None and pdf is not None
        audio.partial_md5, pdf.partial_md5 = "a" * 32, "b" * 32
        db.commit()
    for document in ("a" * 32, "b" * 32):
        assert report(ada_sync.app, ada_sync.auth, document).status_code == 200
        assert ada_sync.app.get(f"/kosync/syncs/progress/{document}", headers=ada_sync.auth).json() == {}
    with SessionLocal() as db:
        assert db.scalars(select(Progress.user_id)).all() == []


def test_a_book_that_is_gone_from_the_disk_is_not_a_document(reading: Reading, ada_sync: Sync) -> None:
    # (The edition is still there, as it is until the scan has settled it: only the file is marked.)
    report(ada_sync.app, ada_sync.auth, ada_sync.document)
    assert ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json() != {}
    with SessionLocal() as db:
        db.execute(text("UPDATE files SET missing_at = '2026-10-01 10:00:00' WHERE partial_md5 = :d"),
                   {"d": ada_sync.document})
        db.commit()
    assert ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json() == {}


def test_the_checksum_is_looked_up_in_any_case(ada_sync: Sync) -> None:
    assert report(ada_sync.app, ada_sync.auth, ada_sync.document.upper()).status_code == 200
    got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document.upper()}", headers=ada_sync.auth).json()
    assert got["percentage"] == pytest.approx(0.42)


# --- What a report may hold --------------------------------------------------------------------------------------


def test_a_report_that_is_not_one_is_turned_down_in_the_shape_of_the_official_server(ada_sync: Sync) -> None:
    good = {"document": ada_sync.document, "progress": POINTER, "percentage": 0.5, "device": "Kobo", "device_id": "k1"}
    bad = {
        "no body": None,
        "a list": [1, 2],
        "no document": {**good, "document": None},
        "an empty document": {**good, "document": ""},
        "a colon in the document": {**good, "document": "a:b"},
        "no percentage": {k: v for k, v in good.items() if k != "percentage"},
        "a percentage that is no number": {**good, "percentage": "far"},
        "a percentage that is not finite": {**good, "percentage": "nan"},
        "a percentage that is a flag": {**good, "percentage": True},
        "no progress": {k: v for k, v in good.items() if k != "progress"},
        "a progress that is a list": {**good, "progress": [1]},
        "no device": {k: v for k, v in good.items() if k != "device"},
    }
    for name, payload in bad.items():
        answer = ada_sync.app.put("/kosync/syncs/progress", headers=ada_sync.auth, json=payload)
        assert answer.status_code == 403, name
        assert set(answer.json()) == {"code", "message"}, name
    # A document with signs the official server cannot read back is turned down as it does.
    answer = ada_sync.app.put("/kosync/syncs/progress", headers=ada_sync.auth, json={**good, "document": "a/b"})
    assert answer.status_code == 403 and answer.json()["code"] == 2007
    # Text that is not JSON.
    for raw in (b"{", b"\xff\xfe", b"[" * 5000):
        answer = ada_sync.app.put("/kosync/syncs/progress", headers=ada_sync.auth, content=raw)
        assert answer.status_code == 403, raw[:5]
    with SessionLocal() as db:
        assert db.scalars(select(Progress.user_id)).all() == []


def test_a_percentage_outside_the_book_is_the_start_or_the_end_and_a_string_of_digits_is_a_number(
        ada_sync: Sync) -> None:
    for sent, kept in ((1.7, 1.0), (-0.2, 0.0), ("0.25", 0.25), (1, 1.0), (0, 0.0)):
        assert report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=sent).status_code == 200
        with SessionLocal() as db:
            assert db.scalars(select(Progress.fraction)).one() == pytest.approx(kept)


def test_a_place_that_cannot_be_kept_is_dropped_but_the_share_of_the_book_is_kept(ada_sync: Sync) -> None:
    for pointer in ("x" * 1001, "/body/DocFragment[1]\n/body", "/body\x00"):
        assert report(ada_sync.app, ada_sync.auth, ada_sync.document, pointer=pointer,
                      percentage=0.6).status_code == 200
        with SessionLocal() as db:
            row = db.scalars(select(Progress)).one()
            assert row.koreader == "" and row.fraction == pytest.approx(0.6)
    # Told back as a place made from the share (the start of the part), never as the rubbish that came.
    got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json()
    assert got["progress"].startswith("/body/DocFragment[")


def test_names_and_ids_of_devices_are_cleaned(ada_sync: Sync) -> None:
    name = "Ko" + chr(0x202E) + "bo" + chr(0x200B) + chr(10) + "  Libra"
    report(ada_sync.app, ada_sync.auth, ada_sync.document, device=name, device_id="a b/c" + chr(0) + "d" + "e" * 80)
    with SessionLocal() as db:
        row = db.scalars(select(Progress)).one()
        assert row.device == "Kobo Libra"
        assert row.device_key.startswith("kr-abcd") and len(row.device_key) <= 40
        assert set(row.device_key) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    # A device with no id of signs a key can hold still gets a key of its own.
    report(ada_sync.app, ada_sync.auth, ada_sync.document, device_id="///")
    with SessionLocal() as db:
        assert len(db.scalars(select(Progress.device_key)).one()) == len("kr-") + 16


def test_a_report_is_no_bigger_than_a_report(ada_sync: Sync) -> None:
    payload = {"document": ada_sync.document, "progress": POINTER, "percentage": 0.5, "device": "Kobo", "device_id": "k1",
               "metadata": {"title": "x" * 20_000}}
    answer = ada_sync.app.put("/kosync/syncs/progress", headers=ada_sync.auth, content=json.dumps(payload))
    assert answer.status_code == 413
    # Metadata a plugin may send is accepted and not kept.
    small = {**payload, "metadata": {"filename": "orchard.epub", "title": "The Quiet Orchard", "authors": "Mara Lindqvist"}}
    assert ada_sync.app.put("/kosync/syncs/progress", headers=ada_sync.auth, json=small).status_code == 200
    with SessionLocal() as db:
        row = db.scalars(select(Progress)).one()
        assert "orchard.epub" not in " ".join(str(v) for v in (row.device, row.device_key, row.koreader, row.chapter_title))


def test_two_devices_of_koreader_follow_each_other(ada_sync: Sync) -> None:
    """A device pulls what another pushed and sees exactly the place, so the plugin goes there."""
    report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.31, pointer=POINTER, device="Phone",
           device_id="phone")
    got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json()
    assert got["progress"] == POINTER and got["device_id"] == "phone"
    report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.35, pointer=OTHER_POINTER, device="Kobo",
           device_id="kobo")
    got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth).json()
    assert got["progress"] == OTHER_POINTER and got["device"] == "Kobo" and got["device_id"] == "kobo"


def test_a_book_the_oldest_file_names_is_found_when_two_files_have_one_checksum(reading: Reading, ada_sync: Sync) -> None:
    with SessionLocal() as db:
        rows = list(db.scalars(select(BookFile).where(BookFile.format == "epub", BookFile.role == "book")
                               .order_by(BookFile.id)))
        for row in rows[:2]:
            row.partial_md5 = "c" * 32
        db.commit()
        found = kosync.locate(db, reading.shelf.ada.id, "c" * 32)
        assert found is not None and found.file.id == min(row.id for row in rows[:2]
                                                          if row.library_id == reading.shelf.library)


def test_the_setting_that_switches_e_readers_off_is_the_one_the_operator_has(reading: Reading, ada_sync: Sync) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"ereaders_allowed": False})
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 401
    assert report(ada_sync.app, ada_sync.auth, ada_sync.document).status_code == 401
    with SessionLocal() as db:
        assert db.scalars(select(Progress.user_id)).all() == []


def test_a_book_filed_after_a_download_has_its_checksum_at_once(operator: Account, net: FakeNetwork,
                                                                 tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    roots = download_roots(tmp_path, monkeypatch)
    world, sab, job = ebook_world(net)
    folder = roots / "complete" / BOOK
    folder.mkdir(parents=True)
    german_epub(folder)
    finish(sab, job, folder)
    assert the_download().state == "imported"
    [filed] = [item for item in book_files() if item.format == "epub" and "Nebelchor" in item.path]
    data = (library_path(world) / filed.path).read_bytes()
    assert filed.partial_md5 and filed.partial_md5 == reference_digest(lambda offset, count: data[offset:offset + count])


def test_a_report_with_a_lone_surrogate_in_its_text_does_not_break_the_sync(reading: Reading, ada_sync: Sync,
                                                                            ada_browser: TestClient) -> None:
    """Half of a character in the place, the name of the device or its id (JSON allows the escape) is no text that can
    be kept or sent: the place is turned down, the names are cleaned, and the sync goes on (the same check as for the
    location a Kobo reports)."""
    import json

    broken = chr(0xD800)
    good = report(ada_sync.app, ada_sync.auth, ada_sync.document, percentage=0.3)
    assert good.status_code == 200
    for field in ("progress", "device", "device_id"):
        data = {"document": ada_sync.document, "progress": POINTER, "percentage": 0.9, "device": "Kobo Libra 2",
                "device_id": DEVICE_ID, field: broken}
        # The escape in the text of the JSON (the library of the test would refuse to write the character itself).
        answer = ada_sync.app.put("/kosync/syncs/progress", headers=ada_sync.auth, content=json.dumps(data))
        assert answer.status_code < 500, (field, answer.text)
    got = ada_sync.app.get(f"/kosync/syncs/progress/{ada_sync.document}", headers=ada_sync.auth)
    assert got.status_code == 200
    assert ada_sync.app.get("/kosync/users/auth", headers=ada_sync.auth).status_code == 200


def test_many_requests_at_the_same_moment_for_more_books_than_the_memory_holds_do_not_fail(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The sizes of the parts of a book are kept in a small memory that forgets the oldest; requests that run at the same
    moment (the routes are plain functions, so they run in threads) must neither fail when another request has just
    made it forget the book they found, nor leave it larger than it may be."""
    import io
    import sys
    import threading
    from collections import OrderedDict
    from types import SimpleNamespace

    books = []
    for number in range(6):
        path = tmp_path / f"book-{number}.epub"
        path.write_bytes(b"x" * (10 + number))
        books.append(SimpleNamespace(library=SimpleNamespace(path=str(tmp_path)), file=SimpleNamespace(path=path.name)))
    monkeypatch.setattr(kosync, "_SPINES_KEPT", 2)
    monkeypatch.setattr(kosync, "_spines", OrderedDict())
    monkeypatch.setattr(epub, "spine_sizes", lambda _handle: [1, 2, 3])
    monkeypatch.setattr(kosync, "open_below", lambda _root, _relative: io.BytesIO(b""))
    monkeypatch.setattr(kosync.works, "book_path", lambda root, relative: Path(root) / relative)
    failures: list[BaseException] = []
    start = threading.Barrier(8)
    # Threads that hand over at once and again, so that the gap between a look and a change is hit.
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def hammer(offset: int) -> None:
        start.wait()
        try:
            for turn in range(3000):
                assert kosync._spine_sizes(books[(turn + offset) % len(books)]) == [1, 2, 3]  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=hammer, args=(index,)) for index in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(interval)
    assert failures == []
    assert len(kosync._spines) <= 2
