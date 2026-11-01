"""What a release carries beside its book: the address of a page in a ``.url`` is no program and turns nothing down. Only
invented books."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.procure import incoming

from .bookmaker import make_epub


def release(tmp_path: Path, extra: str) -> incoming.Walked:
    make_epub(tmp_path / "Die Nebelbahn.epub", title="Die Nebelbahn", authors=("Mara Lind",), language="de")
    (tmp_path / extra).write_bytes(b"[InternetShortcut]\r\nURL=https://example.com/\r\n")
    return incoming.walk(tmp_path)


def test_a_url_file_beside_the_book_is_no_danger(tmp_path: Path) -> None:
    found = release(tmp_path, "Besuch uns.url")
    assert found.dangerous == [] and [item.path for item in found.books] == ["Die Nebelbahn.epub"]


@pytest.mark.parametrize("name", ["setup.exe", "lies mich.lnk", "start.bat", "book.iso"])
def test_a_program_still_is(tmp_path: Path, name: str) -> None:
    assert release(tmp_path, name).dangerous == [name]
