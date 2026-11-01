"""A world for the check after the download and the filing: the world of the wishes (an invented book,
a person who may ask for books, an indexer, SABnzbd or qBittorrent) plus a download folder the test fills with
invented books, made here (EPUB by code, silent recordings). Only invented names, example.com."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import BookFile, Download, Edition, Library, PlaceFile, ProcureHistory, ProcureWish, Work

from .bookmaker import make_epub, make_mp3
from .follow_helpers import AUDIO, BOOK, audio_world, downloads, round_of_tracking, rows, sabnzbd, wish
from .library_helpers import run_jobs
from .procure_fakes import FakeNetwork
from .procure_search_helpers import AUTHOR, SERIES, TITLE, Item, World


def world_with(net: FakeNetwork, fill: Any, *, right: str = "acquire") -> World:
    """A world like ``make_world`` whose library holds what ``fill(folder)`` puts there (instead of the English EPUB)."""
    import secrets

    from app.models import Work

    from .conftest import make_account, new_client
    from .library_helpers import age_all, books_folder, join, make_library, scan
    from .procure_search_helpers import INDEXER_HOST, FeedIndexer, add_indexer

    folder = books_folder("procure")
    fill(folder)
    age_all(folder)
    library = make_library(folder, "Books")
    scan(library)
    person = make_account("rita")
    join(library, person.id, right)
    fake = FeedIndexer(INDEXER_HOST, secrets.token_hex(16))
    net.add(fake)
    indexer_id = add_indexer(fake)
    with SessionLocal() as db:
        work_id = db.scalar(select(Work.id))
    assert work_id is not None
    return World(net=net, indexer=fake, indexer_id=indexer_id, person=person, client=new_client(person),
                 work_id=work_id, library_id=library)


def download_roots(tmp_path: Path, monkeypatch: Any) -> Path:
    base = tmp_path / "dl"
    base.mkdir(exist_ok=True)
    monkeypatch.setattr(get_settings(), "download_roots", str(base))
    return base


def ebook_world(net: FakeNetwork, *items: Item, language: str = "de") -> tuple[World, Any, str]:
    """A wish for the German e-book of the world's book, loaded to SABnzbd. Returns the world, SABnzbd, the job."""
    sab = sabnzbd(net)
    world = audio_world(net, *(items or (Item(BOOK, size=2_000_000, categories=(7120,)),)))
    wish(world, kinds=("ebook",), language=language)
    run_jobs()
    [row] = downloads()
    return world, sab, row.client_ref


def audio_wish(net: FakeNetwork, *items: Item) -> tuple[World, Any, str]:
    sab = sabnzbd(net)
    world = audio_world(net, *items)
    wish(world, kinds=("audio",), language="de")
    run_jobs()
    [row] = downloads()
    return world, sab, row.client_ref


def german_epub(folder: Path, name: str = "Rune Falkner - Die Laternenstrasse.epub", **kwargs: Any) -> Path:
    values: dict[str, Any] = {"title": TITLE, "authors": (AUTHOR,), "language": "de", "series": SERIES,
                              "series_pos": 2.0}
    values.update(kwargs)
    return make_epub(folder / name, **values)


def recording(folder: Path, parts: int = 3, seconds: float = 4.0, **tags: Any) -> list[Path]:
    """A recording of the world's book in ``parts`` silent MP3 files. The album says it is the unabridged reading (as the
    names of a release usually do); a test about a recording nothing proves gives ``album=TITLE``."""
    values: dict[str, Any] = {"title": TITLE, "album": f"{TITLE} (ungekürzt)", "artist": AUTHOR, "series": SERIES,
                              "series_pos": "2"}
    values.update(tags)
    return [make_mp3(folder / f"{number:02d}.mp3", seconds=seconds, track=number, **values)
            for number in range(1, parts + 1)]


def finish(sab: Any, job: str, folder: Path) -> None:
    """SABnzbd says the job is done in ``folder``; tracking sees it, the filing runs."""
    sab.finish(job, str(folder))
    round_of_tracking()
    run_jobs()


def the_download() -> Download:
    [row] = downloads()
    return row


def files_in(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)).replace("\\", "/") for path in folder.rglob("*") if path.is_file()
                  and not any(part.startswith(".") for part in path.relative_to(folder).parts))


def hidden_in(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)).replace("\\", "/") for path in folder.rglob("*") if path.is_file()
                  and any(part.startswith(".") for part in path.relative_to(folder).parts))


def library_path(world: World) -> Path:
    with SessionLocal() as db:
        library = db.get(Library, world.library_id)
        assert library is not None
        return Path(library.path)


def editions() -> list[Edition]:
    return rows(Edition)  # type: ignore[no-any-return]


def book_files() -> list[BookFile]:
    return rows(BookFile)  # type: ignore[no-any-return]


def history() -> list[ProcureHistory]:
    return rows(ProcureHistory)  # type: ignore[no-any-return]


def wishes() -> list[ProcureWish]:
    return rows(ProcureWish)  # type: ignore[no-any-return]


def place_files() -> list[PlaceFile]:
    return rows(PlaceFile)  # type: ignore[no-any-return]


def works() -> list[Work]:
    with SessionLocal() as db:
        found = list(db.scalars(select(Work).order_by(Work.id)))
        for row in found:
            db.expunge(row)
        return found


def zip_of(path: Path, members: dict[str, bytes], *, encrypted: bool = False) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    if encrypted:
        raw = bytearray(path.read_bytes())
        # Set the "encrypted" flag of every entry (local header at 6, central header at 8 past the signature).
        index = raw.find(b"PK\x03\x04")
        while index != -1:
            raw[index + 6] |= 0x1
            index = raw.find(b"PK\x03\x04", index + 4)
        index = raw.find(b"PK\x01\x02")
        while index != -1:
            raw[index + 8] |= 0x1
            index = raw.find(b"PK\x01\x02", index + 4)
        path.write_bytes(bytes(raw))
    return path


def tar_of(path: Path, members: dict[str, bytes], *, link: str | None = None) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if link is not None:
            info = tarfile.TarInfo(link)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
    return path


__all__ = ["AUDIO", "AUTHOR", "BOOK", "SERIES", "TITLE"]
