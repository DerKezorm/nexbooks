"""The pictures and languages in a backup: a restore on a fresh machine shows the covers, the photos of the authors
and the operator's languages again, because nothing else would make them anew. Each picture is checked against its own
name, and an archive that holds a picture it should not, or not the ones its manifest counts, is damaged."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, Edition
from app.services import backups

from .bookmaker import cover_image
from .library_helpers import book, books_folder, join, make_library
from .test_backup_safety import agreeing, made_here, with_manifest, write_archive


@pytest.fixture(autouse=True)
def no_leftovers() -> Iterator[None]:
    shutil.rmtree(backups.pending_folder(), ignore_errors=True)
    shutil.rmtree(backups.failed_folder(), ignore_errors=True)
    yield
    locales = get_settings().locales_dir
    if locales is not None:
        (locales / "es.json").unlink(missing_ok=True)


def picture(colour: tuple[int, int, int]) -> tuple[str, bytes]:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 12), colour).save(buffer, "WEBP")
    data = buffer.getvalue()
    return hashlib.sha256(data).hexdigest()[:20] + ".webp", data


LANGUAGE = b'{"nav": {"library": "Biblioteca"}}'


def a_shelf(operator: Account) -> int:
    """A book with a cover, a photo of an author, a cover of a source and a language of the operator's."""
    folder = books_folder("pictures")
    book(folder, "a.epub", title="Painted Cover", authors=("Ada Feldkamp",), cover=cover_image((200, 30, 30)))
    library = make_library(folder, "Pictures", scan=True)
    join(library, operator.id)
    data = get_settings().data_dir
    for place, colour in (("people", (10, 200, 10)), ("foreign-covers", (10, 10, 200))):
        name, raw = picture(colour)
        (data / place).mkdir(parents=True, exist_ok=True)
        (data / place / name).write_bytes(raw)
    locales = get_settings().locales_dir
    assert locales is not None
    locales.mkdir(parents=True, exist_ok=True)
    (locales / "es.json").write_bytes(LANGUAGE)
    with SessionLocal() as db:
        return db.scalars(select(Edition.work_id)).one()


def test_a_restore_on_a_fresh_machine_brings_back_the_covers_photos_and_languages(
        client: TestClient, operator: Account) -> None:
    work = a_shelf(operator)
    cover = client.get(f"/api/v1/works/{work}/cover")
    assert cover.status_code == 200
    path = backups.create(kind=backups.MANUAL)
    with backups.zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        manifest = backups._manifest(archive)
        stored = {info.filename: info.compress_type for info in archive.infolist()}
    # The cover of a source (a day's guest, ``foreign-covers``) is in no backup.
    assert {name.split("/")[0] for name in names if "/" in name} == {"covers", "people", "locales", "database"}
    assert manifest.pictures == 2 and manifest.locales == 1
    assert all(kind == backups.zipfile.ZIP_STORED for name, kind in stored.items() if name.endswith(".webp"))
    # A fresh machine: no picture, no language.
    data = get_settings().data_dir
    for place in backups.PICTURE_FOLDERS:
        shutil.rmtree(data / place, ignore_errors=True)
    locales = get_settings().locales_dir
    assert locales is not None
    (locales / "es.json").unlink()
    assert client.get(f"/api/v1/works/{work}/cover").status_code == 404
    assert backups.check(path.name).usable
    backups.stage_restore(path.name)
    assert backups.apply_pending()
    assert (locales / "es.json").read_bytes() == LANGUAGE
    assert len(list((data / "people").iterdir())) == 1
    from .conftest import sign_in

    sign_in(client, operator)
    again = client.get(f"/api/v1/works/{work}/cover")
    assert again.status_code == 200 and again.content == cover.content


def test_a_picture_that_is_not_what_its_name_says_makes_the_archive_damaged(
        client: TestClient, operator: Account) -> None:
    a_shelf(operator)
    entries = made_here()
    name = next(entry for entry in entries if entry.startswith("people/"))
    entries[name] = entries[name][:-1] + bytes([entries[name][-1] ^ 1])
    archive = write_archive(entries)
    with pytest.raises(backups.BackupError) as caught:
        backups.check(archive)
    assert caught.value.code == "backup_damaged"
    answer = client.post(f"/api/v1/backups/{archive}/check")
    assert answer.status_code >= 400 and "people" not in answer.text
    assert not backups.pending_folder().exists()


def test_an_archive_holding_other_pictures_than_its_manifest_counts_is_damaged(
        client: TestClient, operator: Account) -> None:
    a_shelf(operator)
    entries = made_here()
    extra, raw = picture((1, 2, 3))
    with pytest.raises(backups.BackupError) as caught:
        backups.check(write_archive({**entries, f"covers/{extra}": raw}))
    assert caught.value.code == "backup_damaged"
    with pytest.raises(backups.BackupError):
        backups.check(write_archive(with_manifest(entries, locales=2)))


@pytest.mark.parametrize("entry", ["covers/../secret.key", "covers/evil.webp", "people/0123456789abcdef0123.png",
                                   "locales/../../x.json", "locales/EN.json", "elsewhere/0123456789abcdef0123.webp"])
def test_an_entry_that_is_no_picture_or_language_of_ours_makes_the_archive_unusable(
        client: TestClient, operator: Account, entry: str) -> None:
    entries = made_here()
    name = write_archive(agreeing({**entries, entry: b"x"}))
    assert backups.check(name).usable is False


@pytest.mark.parametrize("entry", ["covers/0123456789abcdef0123.webp\n", "locales/es.json\n",
                                   "people/0123456789abcdef0123.webp\n\n"])
def test_a_name_with_a_line_break_at_its_end_is_none_of_ours(
        client: TestClient, operator: Account, entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``$`` matches before a final line break; a name that ends in one would be a file with a break in its name on
    Linux and a failed restore on Windows. Neither the patterns nor the check of an archive let it by."""
    assert not backups.PICTURE.fullmatch(entry) and not backups.LOCALE.fullmatch(entry)
    name = write_archive(agreeing({**made_here(), entry: b"x"}))
    assert backups.check(name).usable is False
    assert not backups.NAME.fullmatch("nexbooks-2026-10-15-120000.zip\n")
    monkeypatch.setattr(backups.Path, "is_file", lambda _self: True)
    with pytest.raises(backups.BackupError):
        backups.path_of("nexbooks-2026-10-15-120000.zip\n")


def test_an_archive_with_more_entries_than_a_backup_can_hold_is_refused_before_any_is_read(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = made_here()
    monkeypatch.setattr(backups, "MAX_ENTRIES", len(entries) - 1)
    with pytest.raises(backups.BackupError) as caught:
        backups.check(write_archive(entries))
    assert caught.value.code == "backup_too_large"
    with pytest.raises(backups.BackupError) as staged:
        backups.stage_restore(write_archive(entries))
    assert staged.value.code in ("backup_too_large", "backup_damaged") and not backups.pending_folder().exists()
    monkeypatch.setattr(backups, "MAX_ENTRIES", len(entries))
    assert backups.check(write_archive(entries)).usable


def test_a_picture_larger_than_a_picture_can_be_is_refused(
        client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    a_shelf(operator)
    entries = made_here()
    monkeypatch.setattr(backups, "PICTURE_MAX", 20)
    with pytest.raises(backups.BackupError) as caught:
        backups.check(write_archive(entries))
    assert caught.value.code == "backup_too_large"


def test_a_restore_never_overwrites_a_picture_that_is_there(client: TestClient, operator: Account) -> None:
    a_shelf(operator)
    path = backups.create(kind=backups.MANUAL)
    people = get_settings().data_dir / "people"
    there = next(people.iterdir())
    there.write_bytes(b"kept as it is")
    backups.stage_restore(path.name)
    assert backups.apply_pending()
    assert there.read_bytes() == b"kept as it is"
    assert not any(item.name.endswith(".restoring") for item in people.iterdir())


def test_a_picture_damaged_on_the_disk_stays_out_and_the_backup_stays_usable(
        client: TestClient, operator: Account) -> None:
    a_shelf(operator)
    people = get_settings().data_dir / "people"
    damaged = next(people.iterdir())
    damaged.write_bytes(b"no longer the picture its name says")
    path = backups.create(kind=backups.MANUAL)
    with backups.zipfile.ZipFile(path) as archive:
        assert f"people/{damaged.name}" not in archive.namelist()
        assert backups._manifest(archive).pictures == 1
    assert backups.check(path.name).usable


def test_an_archive_of_an_earlier_version_with_covers_of_the_sources_is_usable_and_does_not_bring_them_back(
        client: TestClient, operator: Account) -> None:
    a_shelf(operator)
    name, raw = picture((90, 90, 200))
    entries = made_here()
    counted = json.loads(entries[backups.MANIFEST])["pictures"] + 1
    old = write_archive({**with_manifest(entries, pictures=counted), f"foreign-covers/{name}": raw})
    assert backups.check(old).usable
    shutil.rmtree(get_settings().data_dir / "foreign-covers", ignore_errors=True)
    backups.stage_restore(old)
    assert backups.apply_pending()
    assert not (get_settings().data_dir / "foreign-covers" / name).exists()
