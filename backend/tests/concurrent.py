"""A way to ask for several things at the same moment, and to hear of a thread that died."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


def at_once(calls: list[Callable[[], Any]]) -> list[Any]:
    """Runs the calls at the same moment (a gate holds every thread until all stand ready) and returns what each gave
    back. A call that raised makes this fail, naming the exception."""
    gate = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)
    died: list[BaseException] = []

    def run(index: int) -> None:
        try:
            gate.wait(timeout=30)
            results[index] = calls[index]()
        except BaseException as exc:  # noqa: BLE001 - a thread that dies must fail the test, not vanish
            died.append(exc)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not died, f"{len(died)} of {len(calls)} died: {[repr(exc) for exc in died[:2]]}"
    return results
