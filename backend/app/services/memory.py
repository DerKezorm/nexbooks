"""Giving memory back after big work.

A library of 20,000 audio files stood at 790 MB after it had matched everything anew, against 90 MB before; on
Windows the same work left the process at about 150 MB, so what stayed was not held by nexbooks' own objects. Two things
on Linux keep memory after work that is done:

* **The C allocator.** Every thread that allocates gets an arena of its own, and memory freed in an arena goes back
  to the system only from its top. A scan and a match run in worker threads and allocate a great deal for a while.
  The image allows two arenas (``MALLOC_ARENA_MAX`` in the Dockerfile), and when the queue of jobs has run empty the
  server asks the allocator once to give back what it can (``trim``).
* **The page cache of the books.** A scan reads every new book to its end for the checksum. Those pages are counted to
  the container (``docker stats`` shows them) although nobody will read them again soon. Each file the scan opened is
  dropped from the cache when it is closed (``forget_pages``); the player's requests do not go this way.

Both do nothing where the system has no such call (Windows, macOS, a C library other than glibc).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import logging
import os
import sys
import threading
from functools import cache

from .. import clock

logger = logging.getLogger("nexbooks.memory")


@cache
def _malloc_trim() -> object | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        library = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        trim = library.malloc_trim
    except (OSError, AttributeError):
        return None
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    return trim


def trim() -> bool:
    """Collects the cycles Python still holds and asks the C allocator to give free memory back. Whether it could."""
    gc.collect()
    call = _malloc_trim()
    if call is None:
        return False
    try:
        call(0)  # type: ignore[operator]
    except OSError:
        return False
    return True


#: The least time between two trims. A source that drips one job after the other would otherwise trim after each of
#: them, and a trim walks every arena of the allocator.
TRIM_EVERY = 60.0
_last_trim: float | None = None
_trim_lock = threading.Lock()


def trim_if_due() -> bool:
    """``trim``, unless one ran within the last ``TRIM_EVERY`` seconds. Whether it did not have to wait: False means
    "not yet", and the caller asks again later so that the last work of a burst is not left untrimmed."""
    global _last_trim
    with _trim_lock:
        now = clock.monotonic()
        if _last_trim is not None and now - _last_trim < TRIM_EVERY:
            return False
        _last_trim = now
    trim()
    return True


def forget() -> None:
    """For the tests: no trim has run yet."""
    global _last_trim
    with _trim_lock:
        _last_trim = None


def forget_pages(descriptor: int) -> None:
    """Tells the system that the pages of an open file will not be wanted again soon (``POSIX_FADV_DONTNEED``)."""
    advise = getattr(os, "posix_fadvise", None)
    if advise is None:
        return
    try:
        advise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass
