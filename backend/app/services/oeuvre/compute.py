"""Putting the works of a prolific author together in a helper process, so that the server does not slow down.

The records of an author are many (a thousand and more for a prolific one with every edition), and grouping them is
work for the processor: in the server's own process it holds the interpreter's lock, and every request that runs at
the same time waits for it (measured: a request of 16 ms took 194 ms on average, up to half a second, while one
catalogue was built). So a catalogue of ``PROCESS_FROM`` records or more is built in a process of its own, as the scan
opens files (``services/probe.py``): the records go there as JSON, the works come back as JSON (never a pickle), and
the server waits for the answer without holding anything. A small catalogue is built in place: it takes milliseconds.

A helper that takes longer than ``TIMEOUT_SECONDS`` is ended and the build tried again later (``Failed``); the server
stopping ends it too.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
from collections.abc import Callable
from typing import Any

from ... import clock
from ..sources.model import Entry
from . import works

logger = logging.getLogger("nexbooks.oeuvre")

#: From this many records on, the works are put together in a helper process.
PROCESS_FROM = 300
TIMEOUT_SECONDS = 300.0
#: The largest question and answer (a prolific author's records with their signals are a few megabytes).
MAX_MESSAGE = 64 * 1024 * 1024
SLICE_SECONDS = 0.25


class Failed(Exception):
    """The helper did not answer in time, or ended without an answer."""


def _work_out(work: works.Work, index: dict[int, int]) -> dict[str, Any]:
    return {"key": work.key, "members": [index[id(entry)] for entry in work.entries],
            "titles": {language: edition.to_json() for language, edition in work.titles.items()}, "kind": work.kind,
            "why": work.why, "pen": work.pen, "series": work.series, "series_pos": work.series_pos, "year": work.year,
            "orig_language": work.orig_language, "sources": work.sources, "isbns": work.isbns, "refs": work.refs,
            "pages": work.pages, "minutes": work.minutes, "weight": work.weight}


def _work_in(data: dict[str, Any], entries: list[Entry]) -> works.Work:
    return works.Work(
        key=str(data["key"]), entries=[entries[int(i)] for i in data["members"]],
        titles={str(language): works.Edition(**edition) for language, edition in data["titles"].items()},
        kind=str(data["kind"]), why=str(data["why"]), pen=str(data["pen"]), series=str(data["series"]),
        series_pos=data["series_pos"], year=data["year"], orig_language=str(data["orig_language"]),
        sources=list(data["sources"]), isbns=list(data["isbns"]), refs=list(data["refs"]), pages=data["pages"],
        minutes=data["minutes"], weight=int(data["weight"]))


def compute(question: dict[str, Any]) -> dict[str, Any]:
    """The works of the records of a question (in the helper, or in place): the records as they came out (a series
    title splits a title) and the works with the numbers of their records."""
    entries = [Entry.from_json(item) for item in question["entries"]]
    built = works.build(entries, answered={key: set(value) for key, value in question["answered"].items()},
                        author_language=question["author_language"], pen_names=set(question["pen_names"]),
                        author=question["author"], namesakes=question["namesakes"])
    index = {id(entry): number for number, entry in enumerate(entries)}
    return {"entries": [entry.to_json() for entry in entries], "works": [_work_out(work, index) for work in built]}


def serve(connection: Any) -> None:
    """The helper: one question, one answer. Runs in the new process."""
    try:
        question = json.loads(connection.recv_bytes(MAX_MESSAGE))
        answer = {"ok": compute(question)}
    except Exception as exc:  # noqa: BLE001 - the server is told, the helper ends
        answer = {"error": type(exc).__name__}
    try:
        connection.send_bytes(json.dumps(answer).encode("utf-8"))
    except (OSError, ValueError):
        pass


def _answer_of(raw: bytes) -> tuple[list[Entry], list[works.Work]]:
    data = json.loads(raw)
    if "ok" not in data:
        raise Failed(str(data.get("error") or "no answer")[:60])
    entries = [Entry.from_json(item) for item in data["ok"]["entries"]]
    return entries, [_work_in(item, entries) for item in data["ok"]["works"]]


def build(entries: list[Entry], *, answered: dict[str, set[str]], author_language: str, pen_names: set[str],
          author: str, namesakes: int, stop: Callable[[], bool] | None = None,
          timeout: float = TIMEOUT_SECONDS) -> list[works.Work]:
    """``works.build``, in a helper process from ``PROCESS_FROM`` records on (see the top). ``Failed`` when the helper
    does not answer; ``stop`` says when the server is stopping (the helper is ended, ``Failed``)."""
    if len(entries) < PROCESS_FROM:
        return works.build(entries, answered=answered, author_language=author_language, pen_names=pen_names,
                           author=author, namesakes=namesakes)
    question = json.dumps({"entries": [entry.to_json() for entry in entries],
                           "answered": {key: sorted(value) for key, value in answered.items()},
                           "author_language": author_language, "pen_names": sorted(pen_names), "author": author,
                           "namesakes": namesakes}).encode("utf-8")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=serve, args=(child,), daemon=True, name="nexbooks-catalogue")
    process.start()
    child.close()
    try:
        parent.send_bytes(question)
        deadline = clock.monotonic() + timeout
        while True:
            if stop is not None and stop():
                raise Failed("stopping")
            if clock.monotonic() >= deadline:
                logger.warning("The works of an author took longer than %s seconds to put together", timeout)
                raise Failed("timeout")
            if parent.poll(SLICE_SECONDS):
                break
            if not process.is_alive() and not parent.poll(0):
                raise Failed("crashed")
        return _answer_of(parent.recv_bytes(MAX_MESSAGE))[1]
    except (EOFError, OSError, ValueError) as exc:
        raise Failed("crashed") from exc
    finally:
        parent.close()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)


__all__ = ["PROCESS_FROM", "Failed", "build", "compute"]
