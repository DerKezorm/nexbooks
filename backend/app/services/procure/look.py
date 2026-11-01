"""Looking into the files of a download that waits for a person (a question of the check after the download): what the
files say about themselves, and the way to read them or to hear them before the person answers.

The files lie where the download program left them (or where nexbooks unpacked them), outside every library. Nothing a
request says names a place: it names the download and the number of one of its files, and the place comes from the
journal of the filing (``PlaceFile``), checked against the folder of the download again, file by file (no link on
the way, a plain file, inside the folder). Only the people who may ask for books in the library the book goes into see
them, and only while the download asks a question (afterwards the files are filed or gone).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import error
from ...models import ROLE_BOOK, Download, Library, PlaceFile
from .. import catalog
from ..match import filefacts
from ..readers import AUDIO_FORMATS
from . import downloads, importing, placing


@dataclass
class Look:
    download: Download
    kind: str
    facts: filefacts.Facts
    #: ``[{number, title, start}]`` of a recording, the start in seconds from the beginning of all its parts.
    chapters: list[dict[str, Any]]


def _question(db: Session, account: Any, download_id: int) -> tuple[Download, Library, list[PlaceFile]]:
    row = downloads.get(db, account, download_id)
    if row.state != "problem" or row.problem_code not in importing.QUESTIONS:
        raise error("not_a_question", "This download asks nothing.", 409)
    library = db.get(Library, row.library_id) if row.library_id is not None else None
    files = list(db.scalars(select(PlaceFile).where(
        PlaceFile.download_id == row.id, PlaceFile.role == ROLE_BOOK, PlaceFile.probe.is_not(None))
        .order_by(PlaceFile.origin, PlaceFile.id)))
    if library is None or not files:
        raise error("not_found", "Not found.", 404)
    return row, library, files


def _kind(files: list[PlaceFile]) -> str:
    return "audio" if files[0].format in AUDIO_FORMATS.values() else "ebook"


def _ordered(files: list[PlaceFile], kind: str) -> list[PlaceFile]:
    if kind != "audio":
        return sorted(files, key=lambda item: (item.origin, item.path.casefold(), item.id))
    return sorted(files, key=lambda item: (item.origin, catalog.audio_order(item)))  # type: ignore[arg-type]


def look_into(db: Session, account: Any, download_id: int) -> Look:
    """The files of the download and what they say. 404 for anybody who may not ask for the book, 409 when the download
    asks nothing."""
    row, _library, found = _question(db, account, download_id)
    kind = _kind(found)
    files = [item for item in _ordered(found, kind) if (item.format in AUDIO_FORMATS.values()) == (kind == "audio")]
    rows = []
    for number, item in enumerate(files, start=1):
        probe = item.probe if isinstance(item.probe, dict) else {}
        seconds = probe.get("duration")
        rows.append(SimpleNamespace(
            id=item.id, path=item.path, format=item.format, size=item.size, part=number if kind == "audio" else 1,
            duration_ms=round(float(seconds) * 1000) if isinstance(seconds, int | float) else 0, probe=probe))
    return Look(download=row, kind=kind, facts=filefacts.facts_from(rows, kind == "audio"),
                chapters=_chapters(rows) if kind == "audio" else [])


def _chapters(rows: list[Any]) -> list[dict[str, Any]]:
    """The chapters inside the files when some file has them, else one chapter to a file (named by its title tag when it
    is a title, else by its number)."""
    out: list[dict[str, Any]] = []
    offset = 0.0
    inside = any(isinstance(row.probe.get("chapters"), list) and row.probe["chapters"] for row in rows)
    for row in rows:
        if inside:
            for chapter in row.probe.get("chapters") or []:
                if isinstance(chapter, dict) and isinstance(chapter.get("start"), int | float):
                    out.append({"number": len(out) + 1, "title": str(chapter.get("title") or "")[:300],
                                "start": round(offset + float(chapter["start"]), 3)})
        elif len(rows) > 1:
            title = row.probe.get("title")
            out.append({"number": len(out) + 1, "title": title[:300] if isinstance(title, str) else "",
                        "start": round(offset, 3)})
        offset += row.duration_ms / 1000
    return out


def file_path(db: Session, account: Any, download_id: int, file_id: int) -> tuple[PlaceFile, Path]:
    """One file of the download with its place on the disk: from the journal of the filing, checked against the folder
    it was read from. 404 for anything else, alike for a file that does not exist and one of another download."""
    row, library, found = _question(db, account, download_id)
    item = next((each for each in found if each.id == file_id), None)
    if item is None:
        raise error("not_found", "Not found.", 404)
    if item.origin != "unpacked" and not row.source_path:
        raise error("not_found", "Not found.", 404)
    try:
        root = importing._root_of(row, library, item.origin)
        return item, placing.source_file(root, item.path)
    except (importing.Stop, placing.PlaceError, OSError, ValueError):
        raise error("not_found", "Not found.", 404) from None
