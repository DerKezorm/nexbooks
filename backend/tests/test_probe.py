"""The helper process that opens files: its answers, its time limit, its crashes, and that it speaks JSON and nothing else."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.services import probe
from app.services.probe import ProbeFailed, Stopping, Worker

READER = "tests.probe_readers:read"


@pytest.fixture
def worker() -> Worker:  # type: ignore[misc]
    with Worker(reader=READER, timeout=20) as made:
        yield made


def test_the_helper_answers_in_another_process(worker: Worker) -> None:
    import os

    answer = worker.read("/root", "some/file.epub", "epub")
    assert answer["relative"] == "some/file.epub" and answer["format"] == "epub"
    assert answer["pid"] != os.getpid()
    # The same helper serves the next file.
    assert worker.read("/root", "other", "pdf")["pid"] == answer["pid"]


def test_bytes_come_back_as_bytes_through_json(worker: Worker) -> None:
    answer = worker.read("/", "bytes", "epub")
    assert answer["cover"] == [b"\x00\x01binary\xff", "#112233"]
    assert answer["nested"] == [{"b": b"xyz"}]


def test_a_text_that_cannot_be_written_as_utf8_never_comes_out_of_the_helper(worker: Worker) -> None:
    """A lone surrogate (what JSON can say with an escape, and what a Windows or Linux name can hold) in any place of the
    answer is taken out: the database write of the file would break on it, and the whole run with it."""
    answer = worker.read("/", "surrogate", "epub")
    assert answer["title"] == "xy" and answer["authors"] == ["xy", "ok"]
    assert answer["xy"] == 1 and answer["nested"] == {"k": ["xy", {"deep": "xy"}]}
    assert answer["cover"] == [b"\x00bin", "xy"]
    assert "surrogate" not in str(answer)
    for text in str(answer):
        assert not 0xD800 <= ord(text) <= 0xDFFF


def test_an_error_of_the_reader_is_a_code_and_the_helper_stays_up(worker: Worker) -> None:
    first = worker.read("/", "ok", "epub")["pid"]
    for name, code in (("boom", "unreadable"), ("oom", "too_large"), ("limit", "too_large"), ("object", "unreadable")):
        with pytest.raises(ProbeFailed) as caught:
            worker.read("/", name, "epub")
        assert caught.value.code == code, name
    # A code the server does not know is none: it goes into the database and onto the page.
    with pytest.raises(ProbeFailed) as caught:
        worker.read("/", "lying", "epub")
    assert caught.value.code == "unreadable"
    assert worker.read("/", "ok", "epub")["pid"] == first


def test_a_file_that_takes_too_long_costs_the_helper_and_nothing_else() -> None:
    with Worker(reader=READER, timeout=1.5) as slow:
        first = slow.read("/", "ok", "epub")["pid"]
        started = time.monotonic()
        with pytest.raises(ProbeFailed) as caught:
            slow.read("/", "hang", "epub")
        assert caught.value.code == "timeout"
        assert time.monotonic() - started < 15
        # A new helper takes the next file.
        assert slow.read("/", "ok", "epub")["pid"] != first


def test_a_helper_that_dies_is_replaced() -> None:
    with Worker(reader=READER, timeout=20) as fragile:
        first = fragile.read("/", "ok", "epub")["pid"]
        with pytest.raises(ProbeFailed) as caught:
            fragile.read("/", "crash", "epub")
        assert caught.value.code == "crashed"
        assert fragile.read("/", "ok", "epub")["pid"] != first


def test_an_answer_larger_than_the_limit_is_a_crash_not_a_memory_hole(worker: Worker) -> None:
    with pytest.raises(ProbeFailed):
        worker.read("/", "huge", "epub")
    assert worker.read("/", "ok", "epub")["relative"] == "ok"


def test_the_helper_ends_when_the_server_stops() -> None:
    stop = threading.Event()
    with Worker(reader=READER, timeout=60, stop=stop.is_set) as stopping:
        stopping.read("/", "ok", "epub")
        timer = threading.Timer(0.8, stop.set)
        timer.start()
        started = time.monotonic()
        with pytest.raises(Stopping):
            stopping.read("/", "hang", "epub")
        assert time.monotonic() - started < 15
        timer.join()


def test_closing_ends_the_process(worker: Worker) -> None:
    worker.read("/", "ok", "epub")
    process = worker._process
    assert process is not None and process.is_alive()
    worker.close()
    assert not process.is_alive()


def test_what_the_helper_sends_is_json_never_a_pickle() -> None:
    """The server reads the helper's answer with ``json.loads``; a pickle would not parse, and so could not run."""
    import pickle

    class Evil:
        def __reduce__(self) -> tuple[object, tuple[str]]:
            return (print, ("RAN",))

    with pytest.raises(ValueError):
        probe.json.loads(pickle.dumps(Evil()))
    source = Path(probe.__file__).read_text(encoding="utf-8")
    # Only the byte-wise calls of the pipe are used: ``recv()`` and ``send()`` are the ones that pickle.
    assert ".recv(" not in source and ".send(" not in source
