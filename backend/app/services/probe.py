"""The helper process that opens the files of a library.

A book is a file from outside, and opening it with a ZIP, XML, image or audio library is where such libraries have had
their faults: a crash, a loop, a memory hole. So the scan never opens a book itself. It asks a helper process to, one
file at a time, and waits for the answer for a limited time:

* **Time.** An answer that does not come within ``timeout`` seconds: the helper is killed and a new one started, the
file is
  listed as a problem (``timeout``), and the scan goes on with the next file.
* **Crash.** A helper that dies (a fault in a decoder) is the same: ``crashed``, a new helper, the next file.
* **Memory.** On Linux the helper may not take more address space than ``MEMORY_LIMIT`` (a bomb ends in an error there,
  not in the server being killed).
* **What comes back** is JSON (the cover as base64), never a pickle: whoever got hold of the helper through a hostile
  file could otherwise send the server an object that runs code when it is read.
* The helper changes nothing: it opens files read-only below the library's folder, never through a link
  (``readers/files.py``), and writes only to its pipe.

One helper per scan, started on the first file and ended with the scan.
"""

from __future__ import annotations

import base64
import importlib
import json
import logging
import multiprocessing
from collections.abc import Callable
from typing import Any, Self

from .. import clock
from .readers.limits import REASONS, ReadError
from .readers.text import scrub

logger = logging.getLogger("nexbooks.probe")

#: What the default helper runs: ``module:function``. A test names another.
READER = "app.services.readers:read_file"
#: How long one file may take.
TIMEOUT_SECONDS = 90.0
#: The most the helper may reserve in memory (Linux), and the largest answer the server takes from it.
MEMORY_LIMIT = 3 * 1024 * 1024 * 1024
MAX_REPLY = 16 * 1024 * 1024
#: The server looks for a stop request this often while it waits.
SLICE_SECONDS = 0.25


class ProbeFailed(Exception):
    """The file was not read: ``code`` is one of ``readers.limits.REASONS``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Stopping(Exception):
    """The server is stopping: the helper was ended, the scan puts itself back in the queue."""


def _encode(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(item) for item in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__bytes__"} and isinstance(value["__bytes__"], str):
            return base64.b64decode(value["__bytes__"], validate=True)
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def serve(connection: Any, reader: str, memory_limit: int) -> None:
    """The helper: reads requests ``(root, relative, format)`` from the pipe and answers each with JSON. Ends when the
    pipe closes. Runs in the new process."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    except (ImportError, ValueError, OSError):
        pass
    module_name, _, function_name = reader.partition(":")
    read = getattr(importlib.import_module(module_name), function_name)
    while True:
        try:
            message = connection.recv_bytes(65536)
        except (EOFError, OSError):
            return
        try:
            root, relative, fmt = json.loads(message)
            payload = json.dumps({"ok": _encode(read(root, relative, fmt))}).encode("utf-8")
        except ReadError as exc:
            payload = json.dumps({"error": exc.code}).encode("utf-8")
        except MemoryError:
            payload = b'{"error": "too_large"}'
        except Exception:  # noqa: BLE001 - whatever the file did to the reader, the helper stays up and says so
            payload = b'{"error": "unreadable"}'
        try:
            connection.send_bytes(payload)
        except (OSError, ValueError):
            return


class Worker:
    """Opens files through the helper; starts it when needed and again after it was ended. Not for several threads."""

    def __init__(self, reader: str = READER, timeout: float = TIMEOUT_SECONDS,
                 stop: Callable[[], bool] | None = None, memory_limit: int = MEMORY_LIMIT) -> None:
        self.reader = reader
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.stop = stop or (lambda: False)
        self._process: Any = None
        self._pipe: Any = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _start(self) -> None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=serve, args=(child, self.reader, self.memory_limit), daemon=True,
                                  name="nexbooks-probe")
        process.start()
        child.close()
        self._process, self._pipe = process, parent

    def close(self) -> None:
        """Ends the helper (politely, then not)."""
        process, pipe = self._process, self._pipe
        self._process = self._pipe = None
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
        if process is not None:
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=5.0)

    def read(self, root: str, relative: str, fmt: str) -> dict[str, Any]:
        """The probe of one file. ``ProbeFailed`` when it cannot be read, ``Stopping`` when the server is stopping."""
        if self._process is None or not self._process.is_alive():
            self.close()
            self._start()
        pipe = self._pipe
        try:
            pipe.send_bytes(json.dumps([root, relative, fmt]).encode("utf-8"))
        except (OSError, ValueError):
            self.close()
            raise ProbeFailed("crashed") from None
        deadline = clock.monotonic() + self.timeout
        while True:
            if self.stop():
                self.close()
                raise Stopping
            try:
                ready = pipe.poll(SLICE_SECONDS)
            except (OSError, EOFError):
                self.close()
                raise ProbeFailed("crashed") from None
            if ready:
                break
            if clock.monotonic() > deadline:
                logger.warning("A file took longer than %s seconds to read; the helper was ended", self.timeout)
                self.close()
                raise ProbeFailed("timeout")
            if not self._process.is_alive() and not pipe.poll(0):
                self.close()
                raise ProbeFailed("crashed")
        try:
            raw = pipe.recv_bytes(MAX_REPLY)
        except (EOFError, OSError):
            self.close()
            raise ProbeFailed("crashed") from None
        try:
            answer = json.loads(raw)
            if "error" in answer:
                code = answer["error"]
                # A word from the helper is a code of ours or none: it ends up in the database and the interface.
                raise ProbeFailed(code if code in REASONS else "unreadable")
            # Whatever the file said, no text that cannot be written as UTF-8 goes on (a lone surrogate would break the
            # database write and with it the run).
            return scrub(_decode(answer["ok"]))  # type: ignore[no-any-return]
        except ProbeFailed:
            raise
        except (ValueError, KeyError, TypeError):
            self.close()
            raise ProbeFailed("unreadable") from None
