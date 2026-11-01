"""The time zone of a person: what the browser reported, the server's own until then. Used where the server writes
a time for somebody (a notice of a new sign-in), never to decide anything else.

The time zone of the instance (``instance_zone``) is the machine's own: it says where a day begins and ends for the
limits of the indexers and for the day a book comes out."""

from __future__ import annotations

import os
from datetime import datetime, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from ..models import Account


@lru_cache(maxsize=1)
def time_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def valid_time_zone(name: Any) -> bool:
    return isinstance(name, str) and 0 < len(name) <= 64 and name in time_zones()


def zone_of(account: Account) -> tzinfo:
    """The person's time zone as the browser reported it; the server's own until then."""
    profile = account.profile if isinstance(account.profile, dict) else {}
    name = profile.get("timezone")
    if valid_time_zone(name):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    zone = datetime.now().astimezone().tzinfo
    assert zone is not None
    return zone


def _named_zone() -> tzinfo | None:
    """The zone the machine is set to, by its name (``TZ``, else ``/etc/timezone``, else the target of
    ``/etc/localtime``): a real zone follows the change of the clock, a fixed offset does not."""
    names: list[str] = [os.environ.get("TZ", "").lstrip(":")]
    for path in ("/etc/timezone",):
        try:
            names.append(Path(path).read_text(encoding="utf-8").strip())
        except OSError:
            pass
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            names.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in names:
        if valid_time_zone(name):
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return None


def detect_zone() -> tzinfo:
    """The machine's own time zone, by its name when it has one, else the offset it has now."""
    found = _named_zone()
    if found is not None:
        return found
    zone = datetime.now().astimezone().tzinfo
    assert zone is not None
    return zone


def instance_zone() -> tzinfo:
    """The time zone of the instance (the machine's own, ``TZ`` in a container). The tests replace this one function."""
    return detect_zone()
