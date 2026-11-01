"""Readers for the tests of the helper process: they do what a hostile file makes a real reader do. Run in the helper."""

from __future__ import annotations

import os
import time
from typing import Any

from app.services.readers.limits import ReadError


def read(root: str, relative: str, fmt: str) -> dict[str, Any]:
    if relative == "hang":
        time.sleep(120)
    if relative == "crash":
        os._exit(3)
    if relative == "boom":
        raise ValueError("a reader that fails in its own way")
    if relative == "oom":
        raise MemoryError
    if relative == "limit":
        raise ReadError("too_large")
    if relative == "lying":
        raise ReadError("a code the server does not know")
    if relative == "object":
        return {"x": object()}
    if relative == "huge":
        return {"x": "a" * (20 * 1024 * 1024)}
    if relative == "surrogate":
        # A lone surrogate in every place a text can stand: value, key, list, nested, beside bytes. (As a number: the
        # character itself does not survive every tool.)
        bad = "x" + chr(0xD800) + "y"
        return {"title": bad, bad: 1, "authors": [bad, "ok"], "nested": {"k": [bad, {"deep": bad}]},
                "cover": (b"\x00bin", bad)}
    if relative == "bytes":
        return {"cover": (b"\x00\x01binary\xff", "#112233"), "nested": [{"b": b"xyz"}]}
    return {"root": root, "relative": relative, "format": fmt, "pid": os.getpid()}


def read_hostile(root: str, relative: str, fmt: str) -> dict[str, Any]:
    """The real reader, except for files whose names ask for a hang or a crash (a scan of a folder with such files)."""
    if "hang" in relative:
        time.sleep(120)
    if "crash" in relative:
        os._exit(7)
    from app.services.readers import read_file

    return read_file(root, relative, fmt)
