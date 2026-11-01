"""The helper that unpacks an archive, on its own: a name that climbs out, an absolute name or a drive is
refused before anything is written, whatever the folder would allow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.procure import unpack_worker


@pytest.mark.parametrize("name", ["../x.epub", "a/../../x.epub", "/etc/x.epub", "C:/x.epub", "a\\..\\..\\x.epub",
                                  "a/\x01.epub", ""])
def test_a_name_that_is_no_plain_name_below_the_folder_is_refused(name: str) -> None:
    with pytest.raises(unpack_worker.Refused) as refused:
        unpack_worker.safe_parts(name)
    assert refused.value.reason == "unsafe"


def test_a_plain_name_keeps_its_folders() -> None:
    assert unpack_worker.safe_parts("CD 1/01.mp3") == ["CD 1", "01.mp3"]
    assert unpack_worker.safe_parts("./a\\b.epub") == ["a", "b.epub"]


def test_the_limits_are_set_by_the_process_of_the_helper_and_not_by_whoever_calls_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    limits: list[int] = []
    monkeypatch.setattr(unpack_worker, "_limit", limits.append)
    arguments = ["zip", str(tmp_path / "nowhere.zip"), str(tmp_path), "1000", "10"]
    assert unpack_worker.run(arguments) == {"ok": False, "reason": "broken"}
    assert limits == []
    monkeypatch.setattr(sys, "argv", ["unpack_worker.py", *arguments])
    assert unpack_worker.main() == 0
    assert limits == [1000] and "broken" in capsys.readouterr().out
