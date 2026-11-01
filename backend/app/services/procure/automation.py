"""The settings of the automatic work (the operator's): a main switch, how often the newest entries of the
indexers are read (RSS), how long a wish that found nothing waits before it is searched again, a replacement
after a failure, and how long a torrent is seeded.

The main switch
is on from the start: a wish is searched at once, the best release that fits is loaded and filed. A torrent is not
seeded from the start (``seed`` is ``no``); the choice of a ratio or seven days is the operator's.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from ...errors import error
from .. import settings_service

KEY = "procure_auto"
RSS_MINUTES = (5, 15, 30, 60)
RETRY_HOURS = (1, 3, 6, 12, 24)
SEED_GOALS = ("ratio", "7d", "no")
#: What the seed setting was before it became "no": a stored ``ratio`` without the mark below was never chosen.
LEGACY_SEED = "ratio"
#: Stored beside the settings (not part of them): the operator chose the seed goal themselves.
SEED_CHOSEN = "seed_set"
REPLACE_MAX = (1, 10)
#: Days a Usenet release may be old (0: the news servers of the programs say, else no limit).
RETENTION_MAX = 20000


def default_config() -> dict[str, Any]:
    return {"enabled": True, "rss_minutes": 15, "retry_hours": 6, "replace": True, "replace_max": 3, "seed": "no",
            "retention_days": 0}


def validate(value: dict[str, Any]) -> dict[str, Any]:
    """The settings made valid, or ``ValueError(field)`` for the first part that is not."""
    result = default_config()
    for name in ("enabled", "replace"):
        if not isinstance(value.get(name), bool):
            raise ValueError(name)  # noqa: TRY004 - one kind of failure for the caller
        result[name] = value[name]
    for name, allowed in (("rss_minutes", RSS_MINUTES), ("retry_hours", RETRY_HOURS), ("seed", SEED_GOALS)):
        if value.get(name) not in allowed:
            raise ValueError(name)
        result[name] = value[name]
    number = value.get("replace_max")
    if isinstance(number, bool) or not isinstance(number, int) or not REPLACE_MAX[0] <= number <= REPLACE_MAX[1]:
        raise ValueError("replace_max")
    result["replace_max"] = number
    days = value.get("retention_days", 0)
    if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= RETENTION_MAX:
        raise ValueError("retention_days")
    result["retention_days"] = days
    return result


def config(db: Session) -> dict[str, Any]:
    stored = settings_service.get(db, KEY)
    if not isinstance(stored, dict):
        return default_config()
    if not stored.get(SEED_CHOSEN) and stored.get("seed") == LEGACY_SEED:
        # Saved by an earlier version along with the other settings: nobody chose it, so it follows the default.
        stored = {**stored, "seed": default_config()["seed"]}
    try:
        return validate({**default_config(), **stored})
    except ValueError:
        return default_config()


def save(db: Session, value: dict[str, Any]) -> dict[str, Any]:
    try:
        checked = validate(value)
    except ValueError as exc:
        raise error("invalid_input", "The settings are not valid.", 422, field=str(exc)) from exc
    # Only a goal other than the default is a choice; the page sends the whole config with every change.
    kept = {**checked, **({SEED_CHOSEN: True} if checked["seed"] != default_config()["seed"] else {})}
    settings_service.save(db, {KEY: kept})
    return copy.deepcopy(checked)
