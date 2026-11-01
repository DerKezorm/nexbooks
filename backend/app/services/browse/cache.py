"""The shelves of a market as read from the database, kept in memory: every look at the wall or at a shelf
reads the same public data, a few thousand books, and putting them together took a third of a second each time.

What is kept is public (``view.read`` holds nothing of a person); a person's own things are laid over a copy of it for
each answer. It is kept until the shelves of the market are fetched again (``forget``, by the job that wrote them) and
for ``SECONDS`` at most, so that a cover fetched in the meantime shows at the next look. One process serves the app
(the image starts one worker), so a counter in memory is all it takes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

#: How long a market's shelves are kept at most.
SECONDS = 60.0

_lock = threading.Lock()
_versions: dict[str, int] = {}
_kept: dict[str, tuple[int, float, Any]] = {}


def forget(market: str = "") -> None:
    """The shelves of ``market`` (all markets without one) changed: what was kept of them is not used again."""
    with _lock:
        for key in [market] if market else list({*_versions, *_kept}):
            _versions[key] = _versions.get(key, 0) + 1
            _kept.pop(key, None)


def get(market: str) -> Any | None:
    """What was kept for ``market``, when it is still good."""
    with _lock:
        found = _kept.get(market)
        if found is None:
            return None
        version, stamp, value = found
        if version != _versions.get(market, 0) or time.monotonic() - stamp > SECONDS:
            _kept.pop(market, None)
            return None
        return value


def version(market: str) -> int:
    with _lock:
        return _versions.get(market, 0)


def put(market: str, seen_version: int, value: Any) -> None:
    """Keeps ``value``, unless the shelves changed while it was read (``seen_version`` is the version before)."""
    with _lock:
        if _versions.get(market, 0) == seen_version:
            _kept[market] = (seen_version, time.monotonic(), value)
